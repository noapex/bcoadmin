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
        return request

    def test_sets_and_resets_context(self):
        seen = []

        def view(request):
            seen.append(demo.db_actual())
            return "ok"

        mw = demo.DemoMiddleware(view)
        self.assertEqual(mw(self._request({"demo": True})), "ok")
        self.assertEqual(mw(self._request({})), "ok")
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
                             {"name": "Renombrada", "parent": "", "sugiere_recurrentes": "on",
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
