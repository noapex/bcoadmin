"""Demo mode: request-scoped routing of the movimientos app to a separate database."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import Sum
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from movimientos import demo
from movimientos.db_router import DemoRouter
from movimientos.models import Categoria, DataFile, Detalle, Recurrente, Regla, Resumen, Tarjeta
from movimientos.services import recurring

APP_MODELS = (Categoria, DataFile, Detalle, Recurrente, Regla, Resumen, Tarjeta)


def det(db, desc, monto="-100", categoria=None, fecha=datetime(2026, 9, 10)):
    # categoria_id: the router is strict (the active context decides, never an instance's database).
    return Detalle.objects.using(db).create(codigo="", fecha=fecha, cuenta="c", descripcion=desc,
                                            monto=Decimal(monto), tipo="gasto",
                                            categoria_id=categoria.pk if categoria else None)


class RouterTests(SimpleTestCase):
    def setUp(self):
        self.router = DemoRouter()

    def test_app_models_go_to_default_without_demo(self):
        for model in APP_MODELS:
            self.assertEqual(self.router.db_for_read(model), "default")
            self.assertEqual(self.router.db_for_write(model), "default")

    def test_app_models_go_to_demo_with_demo(self):
        with demo.usar_demo():
            self.assertEqual(demo.db_actual(), "demo")
            for model in APP_MODELS:
                self.assertEqual(self.router.db_for_read(model), "demo")
                self.assertEqual(self.router.db_for_write(model), "demo")
        self.assertEqual(demo.db_actual(), "default")

    def test_auth_and_sessions_always_default(self):
        with demo.usar_demo():
            for model in (User, Session):
                self.assertEqual(self.router.db_for_read(model), "default")
                self.assertEqual(self.router.db_for_write(model), "default")

    def test_allow_migrate(self):
        self.assertTrue(self.router.allow_migrate("demo", "movimientos"))
        self.assertTrue(self.router.allow_migrate("default", "movimientos"))
        for app in ("auth", "sessions", "admin", "contenttypes"):
            self.assertFalse(self.router.allow_migrate("demo", app))
            self.assertTrue(self.router.allow_migrate("default", app))

    def test_allow_relation_only_within_same_db(self):
        a, b, c = Categoria(), Categoria(), Categoria()
        a._state.db, b._state.db, c._state.db = "demo", "demo", "default"
        self.assertTrue(self.router.allow_relation(a, b))
        self.assertFalse(self.router.allow_relation(a, c))


class MiddlewareTests(TestCase):
    databases = {"default", "demo"}

    def setUp(self):
        det("demo", "Algo ficticio")

    def _request(self, session):
        request = RequestFactory().get("/")
        request.session = session
        request.user = mock.Mock(is_authenticated=True)
        request.user.groups.filter.return_value.exists.return_value = False  # not a solo-demo user
        return request

    def test_sets_and_resets_context(self):
        seen = []

        def view(request):
            seen.append(demo.db_actual())
            return HttpResponse("ok")

        mw = demo.DemoMiddleware(view)
        self.assertEqual(mw(self._request({"demo": True})).content, b"ok")
        self.assertEqual(mw(self._request({})).content, b"ok")
        self.assertEqual(seen, ["demo", "default"])
        self.assertEqual(demo.db_actual(), "default")

    def test_resets_after_exception(self):
        def view(request):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            demo.DemoMiddleware(view)(self._request({"demo": True}))
        self.assertEqual(demo.db_actual(), "default")


class DemoClientBase(TestCase):
    databases = {"default", "demo"}

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("u", password="p")
        self.client.force_login(self.user)

    def set_demo(self, on=True):
        s = self.client.session
        s["demo"] = on
        s.save()


class IsolationTests(DemoClientBase):
    def setUp(self):
        super().setUp()
        cat_real = Categoria.objects.using("default").create(name="CategoriaRealSecreta")
        cat_demo = Categoria.objects.using("demo").create(name="CategoriaDemoFicticia")
        det("default", "MOVIMIENTO REAL SECRETO", categoria=cat_real)
        det("demo", "MOVIMIENTO DEMO FICTICIO", categoria=cat_demo)

    def test_demo_on_shows_only_demo_rows(self):
        self.set_demo(True)
        for url in ("/movimientos/", "/", "/categorias/", "/categorias/gestionar/", "/admin/movimientos/detalle/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotContains(r, "REAL SECRETO")
            self.assertNotContains(r, "CategoriaRealSecreta")
        self.assertContains(self.client.get("/movimientos/"), "MOVIMIENTO DEMO FICTICIO")
        self.assertContains(self.client.get("/"), "CategoriaDemoFicticia")
        self.assertContains(self.client.get("/admin/movimientos/detalle/"), "MOVIMIENTO DEMO FICTICIO")

    def test_demo_off_shows_only_real_rows(self):
        for url in ("/movimientos/", "/admin/movimientos/detalle/"):
            r = self.client.get(url)
            self.assertContains(r, "MOVIMIENTO REAL SECRETO")
            self.assertNotContains(r, "DEMO FICTICIO")
        self.assertNotContains(self.client.get("/"), "CategoriaDemoFicticia")

    def test_admin_history_hidden_in_demo(self):
        self.set_demo(True)
        pk = Detalle.objects.using("demo").get().pk
        r = self.client.get(f"/admin/movimientos/detalle/{pk}/history/", follow=True)
        self.assertNotContains(r, "REAL SECRETO")
        self.assertContains(r, "modo demo")

    def test_admin_change_in_demo_writes_no_log_entry(self):
        from django.contrib.admin.models import LogEntry
        self.set_demo(True)
        cat = Categoria.objects.using("demo").get()
        r = self.client.post(f"/admin/movimientos/categoria/{cat.pk}/change/",
                             {"name": "Renombrada", "parent": "", "tipo": "egreso", "sugiere_recurrentes": "on",
                              "_save": "Guardar"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Categoria.objects.using("demo").get().name, "Renombrada")
        self.assertEqual(Categoria.objects.using("default").get().name, "CategoriaRealSecreta")
        self.assertFalse(LogEntry.objects.exists())

    def test_media_is_not_served_in_demo(self):
        self.set_demo(True)
        self.assertEqual(self.client.get("/media/cualquier.pdf").status_code, 404)


class ToggleTests(DemoClientBase):
    def test_post_flips_and_redirects(self):
        r = self.client.post(reverse("demo_toggle"), {"next": "/movimientos/"})
        self.assertRedirects(r, "/movimientos/", fetch_redirect_response=False)
        self.assertTrue(self.client.session["demo"])
        self.client.post(reverse("demo_toggle"))
        self.assertFalse(self.client.session["demo"])

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("demo_toggle")).status_code, 405)

    def test_unsafe_next_ignored(self):
        r = self.client.post(reverse("demo_toggle"), {"next": "https://evil.example.com/"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)

    def test_banner_and_title_only_when_on(self):
        det("demo", "Algo ficticio")
        det("default", "Algo real")
        r = self.client.get("/movimientos/")
        self.assertNotContains(r, "MODO DEMO")
        self.assertNotContains(r, "[Demo]")
        self.assertContains(r, "Modo demo")  # the switch
        self.set_demo(True)
        r = self.client.get("/movimientos/")
        self.assertContains(r, "MODO DEMO · datos ficticios")
        self.assertContains(r, "<title>[Demo] ")

    def test_empty_demo_db_shows_hint(self):
        self.set_demo(True)
        r = self.client.get("/")
        self.assertContains(r, "generar_demo")
        self.assertContains(r, "MODO DEMO")


class ImportDisabledTests(DemoClientBase):
    def setUp(self):
        super().setUp()
        det("demo", "Algo ficticio")
        self.set_demo(True)

    def test_upload_disabled(self):
        f = SimpleUploadedFile("cuenta.xlsx", b"x")
        with mock.patch("movimientos.views.import_files") as imp:
            r = self.client.post(reverse("upload"), {"file_field": [f]})
        imp.assert_not_called()
        self.assertContains(r, "En modo demo no se importan archivos.")
        self.assertFalse(DataFile.objects.using("default").exists())
        self.assertFalse(DataFile.objects.using("demo").exists())

    def test_sync_disabled(self):
        with mock.patch("movimientos.views.download_movs") as dl, \
                mock.patch("movimientos.views.import_files") as imp:
            r = self.client.get(reverse("sync"))
        dl.assert_not_called()
        imp.assert_not_called()
        self.assertContains(r, "En modo demo no se importan archivos.")
        self.assertFalse(DataFile.objects.using("default").exists())
        self.assertFalse(DataFile.objects.using("demo").exists())


class WritePathTests(DemoClientBase):
    def setUp(self):
        super().setUp()
        det("demo", "Algo ficticio")
        Categoria.objects.using("default").create(name="Real")
        self.set_demo(True)

    def test_abm_create_commits_to_demo_only(self):
        r = self.client.post(reverse("categoria_nueva"), {"name": "Nueva demo", "parent": "",
                                                          "sugiere_recurrentes": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Categoria.objects.using("demo").filter(name="Nueva demo").exists())
        self.assertEqual(list(Categoria.objects.using("default").values_list("name", flat=True)), ["Real"])

    def test_abm_failure_rolls_back_demo(self):
        # Rollback only works if the transaction is opened on the demo database.
        with mock.patch("movimientos.views_categorias.clasificar_todo", side_effect=RuntimeError):
            self.client.post(reverse("categoria_nueva"), {"name": "Nueva demo", "parent": "",
                                                          "sugiere_recurrentes": "on"})
        self.assertFalse(Categoria.objects.using("demo").filter(name="Nueva demo").exists())
        self.assertFalse(Categoria.objects.using("default").filter(name="Nueva demo").exists())


def _snapshot(db):
    return {m.__name__: m.objects.using(db).count() for m in APP_MODELS} | {
        "users": User.objects.using("default").count() if db == "default" else 0}


def _resumen_demo():
    rows = Counter(Detalle.objects.using("demo").values_list("fuente", "moneda", "tipo"))
    recs = Counter(Recurrente.objects.using("demo").values_list("estado_admin", flat=True))
    return rows, recs, _snapshot("demo")


class GenerarDemoTests(TestCase):
    databases = {"default", "demo"}

    def setUp(self):
        # Real-side rows a buggy generator could wipe or touch.
        cat = Categoria.objects.using("default").create(name="Real")
        det("default", "Real 1", categoria=cat)
        Regla.objects.using("default").create(patron="real", categoria_id=cat.pk)

    def _run(self, seed=42):
        call_command("generar_demo", seed=seed, stdout=StringIO())

    def test_escala_halves_every_amount(self):
        def montos():
            return list(Detalle.objects.using("demo").order_by("fecha", "descripcion", "fuente", "moneda", "pk")
                        .values_list("monto", flat=True))

        def totales():
            return list(Resumen.objects.using("demo").order_by("fuente", "hasta").values_list("totales", flat=True))

        call_command("generar_demo", seed=7, escala=1, stdout=StringIO())
        completos, tot_completos = montos(), totales()
        call_command("generar_demo", seed=7, escala=0.5, stdout=StringIO())
        mitad, tot_mitad = montos(), totales()
        self.assertEqual(len(mitad), len(completos))
        for full, half in zip(completos, mitad):
            self.assertLessEqual(abs(Decimal(full) / 2 - Decimal(half)), Decimal("0.01"))
        for full, half in zip(tot_completos, tot_mitad):
            for moneda, v in full.items():
                self.assertLessEqual(abs(Decimal(v) / 2 - Decimal(half[moneda])), Decimal("0.01") * 400)

    def test_default_escala_is_half(self):
        self._run()
        call_command("generar_demo", seed=42, escala=1, stdout=StringIO())
        completo = Detalle.objects.using("demo").aggregate(s=Sum("monto"))["s"]
        self._run()
        mitad = Detalle.objects.using("demo").aggregate(s=Sum("monto"))["s"]
        self.assertAlmostEqual(float(mitad), float(completo) / 2, delta=abs(float(completo)) * 0.001 + 50)

    def test_generates_rich_isolated_deterministic_data(self):
        antes = _snapshot("default")
        self._run()
        primero = _resumen_demo()
        self._run()
        self.assertEqual(_resumen_demo(), primero)
        self.assertEqual(_snapshot("default"), antes)
        self.assertEqual(demo.db_actual(), "default")

        rows = primero[0]
        fuentes = {f for f, _, _ in rows}
        self.assertTrue({"cuenta", "visa", "amex", "galicia"} <= fuentes)
        self.assertTrue(any(m == "USD" and f == "visa" for f, m, _ in rows))
        self.assertTrue(any(m == "USD" and f == "cuenta" for f, m, _ in rows))
        self.assertTrue({"ingreso", "gasto", "interno"} <= {t for _, _, t in rows})
        self.assertTrue(Detalle.objects.using("demo").exclude(cuota="").exists())
        self.assertTrue(Detalle.objects.using("demo").filter(categoria__name="Tarjeta sin detalle").exists())
        self.assertFalse(Detalle.objects.using("demo").filter(comercio="").exists())

        with demo.usar_demo():
            estados = Counter(recurring.estado(r) for r in Recurrente.objects.filter(estado_admin="confirmado"))
        self.assertGreaterEqual(estados["falta"], 1)
        self.assertGreaterEqual(estados["pendiente"], 1)
        self.assertGreaterEqual(primero[1]["confirmado"], 8)
        self.assertGreaterEqual(primero[1]["silenciado"], 2)
        self.assertGreaterEqual(primero[1]["sugerido"], 1)

    def test_other_seed_changes_data(self):
        self._run(1)
        uno = _resumen_demo()
        self._run(2)
        self.assertNotEqual(_resumen_demo()[0], uno[0])


class DebugToolbarTests(DemoClientBase):
    def setUp(self):
        super().setUp()
        det("demo", "Algo ficticio")

    def _request(self, on):
        request = RequestFactory().get("/__debug__/render_panel/", REMOTE_ADDR="127.0.0.1")
        request.user = self.user
        request.session = {"demo": on}
        return request

    def test_callback_wins_over_local_settings(self):
        from django.conf import settings
        self.assertEqual(settings.DEBUG_TOOLBAR_CONFIG["SHOW_TOOLBAR_CALLBACK"], "movimientos.demo.mostrar_toolbar")

    def test_callback_hides_toolbar_only_in_demo(self):
        from django.conf import settings
        if "debug_toolbar" not in settings.INSTALLED_APPS:
            self.skipTest("debug_toolbar not installed")
        with self.settings(DEBUG=True, INTERNAL_IPS=["127.0.0.1"]):
            self.assertFalse(demo.mostrar_toolbar(self._request(True)))
            self.assertTrue(demo.mostrar_toolbar(self._request(False)))

    def test_no_markup_and_no_panels_in_demo(self):
        from django.conf import settings
        from django.http import Http404
        if "debug_toolbar" not in settings.INSTALLED_APPS:
            self.skipTest("debug_toolbar not installed")
        from debug_toolbar import views as djdt_views
        self.set_demo(True)
        with self.settings(DEBUG=True, INTERNAL_IPS=["127.0.0.1"]):
            r = self.client.get("/movimientos/")
            self.assertNotContains(r, "djDebug")
            with self.assertRaises(Http404):
                djdt_views.render_panel(self._request(True))


class CategoriaMoveTests(DemoClientBase):
    def test_move_node_in_demo_writes_no_log_entry(self):
        from django.contrib.admin.models import LogEntry
        with demo.usar_demo():
            a = Categoria.objects.create(name="A")
            b = Categoria.objects.create(name="B")
        det("demo", "Algo ficticio")
        self.set_demo(True)
        r = self.client.post("/admin/movimientos/categoria/", {
            "cmd": "move_node", "cut_item": b.pk, "pasted_on": a.pk, "position": "last-child"})
        self.assertContains(r, "OK, moved.")
        self.assertEqual(Categoria.objects.using("demo").get(name="B").parent_id, a.pk)
        self.assertFalse(LogEntry.objects.using("default").exists())


class BackfillMigrationTests(TestCase):
    databases = {"default", "demo"}

    def test_backfill_saves_on_the_migrated_alias(self):
        import importlib
        from django.apps import apps
        from django.db import connections
        mig = importlib.import_module("movimientos.migrations.0009_backfill_legacy")
        real = det("default", "Real", monto="-5")
        Detalle.objects.using("default").filter(pk=real.pk).update(fuente="visa", moneda="USD")
        fila = det("demo", "Demo", monto="-5")
        Detalle.objects.using("demo").filter(pk=fila.pk).update(fuente="visa", moneda="USD")
        mig.backfill(apps, mock.Mock(connection=connections["demo"]))
        self.assertEqual(Detalle.objects.using("demo").values_list("fuente", "moneda").get(), ("cuenta", "ARS"))
        self.assertEqual(Detalle.objects.using("default").values_list("fuente", "moneda").get(), ("visa", "USD"))


class HardeningTests(DemoClientBase):
    def test_html_is_not_cached_in_both_modes(self):
        det("demo", "Algo ficticio")
        self.assertIn("no-store", self.client.get("/movimientos/")["Cache-Control"])
        self.set_demo(True)
        self.assertIn("no-store", self.client.get("/movimientos/")["Cache-Control"])

    def test_toggle_drops_pending_messages(self):
        det("demo", "Algo ficticio")
        self.client.post(reverse("recurrentes_ignorar_todas"))  # leaves a pending real-mode message
        r = self.client.post(reverse("demo_toggle"), {"next": "/movimientos/"}, follow=True)
        self.assertNotContains(r, "ignorada")
        self.assertContains(r, "Modo demo activado")

    def test_empty_demo_db_lets_logout_and_toggle_through(self):
        self.set_demo(True)
        r = self.client.post(reverse("demo_toggle"))
        self.assertEqual(r.status_code, 302)
        self.set_demo(True)
        r = self.client.post("/accounts/logout/")
        self.assertNotContains(r, "generar_demo")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_generar_demo_refuses_same_file_by_resolved_path(self):
        import os
        import tempfile
        from django.core.management.base import CommandError
        from django.db import connections
        det("demo", "Debe sobrevivir")
        tmp = tempfile.mkdtemp()
        real = os.path.join(tmp, "db.sqlite3")
        alias = os.path.join(tmp, "sub", "..", "db.sqlite3")
        with mock.patch.dict(connections["default"].settings_dict, {"NAME": real}), \
                mock.patch.dict(connections["demo"].settings_dict, {"NAME": alias}):
            with self.assertRaises(CommandError):
                call_command("generar_demo", stdout=StringIO())
        self.assertTrue(Detalle.objects.using("demo").filter(descripcion="Debe sobrevivir").exists())


class SoloDemoTests(DemoClientBase):
    """Users in the `demo` group always run in demo mode and can never switch to real data."""

    def setUp(self):
        super().setUp()
        call_command("crear_usuario_demo", "visita", password="clave-de-prueba", stdout=StringIO())
        self.visita = User.objects.get(username="visita")
        self.client.force_login(self.visita)
        cat_real = Categoria.objects.using("default").create(name="CategoriaRealSecreta")
        det("default", "MOVIMIENTO REAL SECRETO", categoria=cat_real)
        det("demo", "MOVIMIENTO DEMO FICTICIO")

    def test_command_creates_plain_user_in_demo_group(self):
        self.assertTrue(self.visita.check_password("clave-de-prueba"))
        self.assertFalse(self.visita.is_staff or self.visita.is_superuser)
        self.assertTrue(demo.solo_demo(self.visita))

    def test_sees_only_demo_rows_without_session_flag(self):
        r = self.client.get("/movimientos/")
        self.assertContains(r, "MOVIMIENTO DEMO FICTICIO")
        self.assertNotContains(r, "REAL SECRETO")
        self.assertContains(r, "MODO DEMO · datos ficticios")

    def test_cannot_turn_demo_off(self):
        self.set_demo(False)
        r = self.client.post(reverse("demo_toggle"), {"next": "/movimientos/"})
        self.assertEqual(r.status_code, 403)
        self.assertNotContains(self.client.get("/movimientos/"), "REAL SECRETO")

    def test_switch_hidden(self):
        self.assertNotContains(self.client.get("/movimientos/"), reverse("demo_toggle"))

    def test_no_admin_access(self):
        r = self.client.get("/admin/auth/user/")
        self.assertNotEqual(r.status_code, 200)

    def test_regular_user_still_toggles(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/movimientos/"), reverse("demo_toggle"))
        self.assertFalse(demo.solo_demo(self.user))
