from django.test import TestCase


class LoginRequiredTests(TestCase):
    def test_dashboard_redirects_anonymous_to_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)


import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from movimientos.models import DataFile, Detalle
from movimientos.tests.factories import make_account_xlsx


class UploadViewTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))

    def _xlsx_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            return SimpleUploadedFile("cuenta.xlsx", make_account_xlsx(tmp).read_bytes())

    def test_get_shows_form(self):
        r = self.client.get(reverse("upload"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'type="file"')

    def test_post_valid_and_unrecognised(self):
        good = self._xlsx_upload()
        bad = SimpleUploadedFile("notes.txt", b"nothing")
        r = self.client.post(reverse("upload"), {"file_field": [good, bad]})
        self.assertEqual(r.status_code, 200)
        reports = r.context["reports"]
        self.assertEqual([x.archivo for x in reports], ["cuenta.xlsx", "notes.txt"])
        self.assertGreater(reports[0].nuevos, 0)
        self.assertContains(r, "Formato no reconocido")
        self.assertContains(r, "cuenta.xlsx")
        self.assertEqual(Detalle.objects.count(), reports[0].nuevos)
        self.assertEqual(DataFile.objects.count(), 1)

    def test_post_empty_shows_message(self):
        r = self.client.post(reverse("upload"), {})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Seleccioná al menos un archivo")
        self.assertEqual(DataFile.objects.count(), 0)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("upload")).status_code, 302)


class SyncViewTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))

    def test_sync_imports_downloaded_file(self):
        def fake_download(dest):
            make_account_xlsx(dest)
        with mock.patch("movimientos.views.download_movs", side_effect=fake_download):
            r = self.client.get(reverse("sync"))
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.context["reports"][0].nuevos, 0)
        self.assertGreater(Detalle.objects.count(), 0)

    def test_sync_without_download_reports_error(self):
        with mock.patch("movimientos.views.download_movs"):
            r = self.client.get(reverse("sync"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No se descargó el archivo")


class AdminTests(TestCase):
    def setUp(self):
        from datetime import datetime
        from movimientos.models import Categoria
        user = get_user_model().objects.create_superuser("adm", "a@x.com", "pw")
        self.client.force_login(user)
        self.cat = Categoria.objects.create(name="Super")
        self.det = Detalle.objects.create(
            codigo="1", fecha=datetime(2025, 1, 5), cuenta="c",
            descripcion="COTO", monto=-100, tipo="gasto")

    def test_change_form_sets_manual(self):
        url = reverse("admin:movimientos_detalle_change", args=[self.det.pk])
        r = self.client.post(url, {
            "codigo": "1", "fecha_0": "2025-01-05", "fecha_1": "00:00:00", "cuenta": "c",
            "descripcion": "COTO", "monto": "-100", "activo": "on", "fuente": "cuenta",
            "moneda": "ARS", "tipo": "gasto", "categoria": self.cat.pk, "cuota": "",
            "ref": "", "comercio": "",
        })
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["adminform"].errors)
        self.det.refresh_from_db()
        self.assertEqual(self.det.categoria_id, self.cat.pk)
        self.assertTrue(self.det.categoria_manual)

    def test_changelist_edit_sets_manual(self):
        url = reverse("admin:movimientos_detalle_changelist")
        r = self.client.post(url, {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-id": self.det.pk, "form-0-categoria": self.cat.pk,
            "form-0-tipo": "gasto", "_save": "Guardar",
        })
        self.assertEqual(r.status_code, 302)
        self.det.refresh_from_db()
        self.assertTrue(self.det.categoria_manual)

    def test_changelist_unchanged_not_manual(self):
        url = reverse("admin:movimientos_detalle_changelist")
        self.client.post(url, {
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-id": self.det.pk, "form-0-categoria": "",
            "form-0-tipo": "gasto", "_save": "Guardar",
        })
        self.det.refresh_from_db()
        self.assertFalse(self.det.categoria_manual)

    def test_volver_a_reglas_action(self):
        Detalle.objects.filter(pk=self.det.pk).update(categoria=self.cat, categoria_manual=True)
        url = reverse("admin:movimientos_detalle_changelist")
        with mock.patch("movimientos.admin.clasificar_todo") as m:
            self.client.post(url, {"action": "volver_a_reglas", "_selected_action": [self.det.pk]})
        self.det.refresh_from_db()
        self.assertFalse(self.det.categoria_manual)
        m.assert_called_once()

    def test_recurrente_nombre_editado_and_manual(self):
        from movimientos.models import Recurrente
        add = reverse("admin:movimientos_recurrente_add")
        data = {"nombre": "Netflix", "clave": "", "patron": "NETFLIX", "moneda": "ARS",
                "fuente": "cuenta", "frecuencia_meses": 1, "dia_habitual": 5,
                "estado_admin": "sugerido"}
        r = self.client.post(add, data)
        self.assertEqual(r.status_code, 302)
        rec = Recurrente.objects.get()
        self.assertTrue(rec.manual)
        self.assertFalse(rec.nombre_editado)
        data["nombre"] = "Netflix HD"
        self.client.post(reverse("admin:movimientos_recurrente_change", args=[rec.pk]), data)
        rec.refresh_from_db()
        self.assertTrue(rec.nombre_editado)

    def test_recurrente_actions(self):
        from movimientos.models import Recurrente
        rec = Recurrente.objects.create(nombre="x", moneda="ARS", fuente="cuenta")
        url = reverse("admin:movimientos_recurrente_changelist")
        self.client.post(url, {"action": "confirmar", "_selected_action": [rec.pk]})
        rec.refresh_from_db()
        self.assertEqual(rec.estado_admin, "confirmado")
        self.client.post(url, {"action": "silenciar", "_selected_action": [rec.pk]})
        rec.refresh_from_db()
        self.assertEqual(rec.estado_admin, "silenciado")

    def test_regla_save_reclassifies(self):
        from movimientos.models import Regla
        with mock.patch("movimientos.admin.clasificar_todo") as m:
            self.client.post(reverse("admin:movimientos_regla_add"), {
                "patron": "COTO", "prioridad": 100, "categoria": self.cat.pk, "tipo": ""})
        self.assertEqual(Regla.objects.count(), 1)
        m.assert_called()

    def test_all_changelists_render(self):
        from django.contrib import admin
        from django.apps import apps
        for model in apps.get_app_config("movimientos").get_models():
            if model in admin.site._registry:
                r = self.client.get(reverse(f"admin:movimientos_{model._meta.model_name}_changelist"))
                self.assertEqual(r.status_code, 200, model)


from datetime import date, datetime
from decimal import Decimal

from movimientos.models import Categoria, Recurrente


def mov(y, m, d, monto, moneda="ARS", tipo=None, categoria=None, desc="X", fuente="cuenta"):
    monto = Decimal(monto)
    return Detalle.objects.create(
        codigo="", fecha=datetime(y, m, d), cuenta="c", descripcion=desc, monto=monto,
        moneda=moneda, fuente=fuente, categoria=categoria,
        tipo=tipo or ("ingreso" if monto > 0 else "gasto"))


class DashboardTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        self.cat = Categoria.objects.create(name="Servicios")
        mov(2026, 9, 5, "1000")
        mov(2026, 9, 10, "-300", categoria=self.cat, desc="EDESUR")
        mov(2026, 9, 11, "-50", desc="SIN CAT")
        mov(2026, 8, 11, "-70", desc="AGOSTO")
        mov(2026, 9, 12, "-9", tipo="interno")
        mov(2026, 9, 6, "40", moneda="USD")
        mov(2026, 9, 7, "-15", moneda="USD")
        Recurrente.objects.create(
            nombre="Netflix", clave="netflix", moneda="ARS", fuente="cuenta", monto_ref=Decimal("19.99"),
            ultima_fecha=date(2026, 6, 17), dia_habitual=17, estado_admin="confirmado")

    def test_dashboard_ars(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        k = r.context["kpis"]
        self.assertEqual((k["ingresos"], k["gastos"], k["balance"]), (Decimal("1000"), Decimal("350"), Decimal("650")))
        self.assertEqual(k["faltan"], 1)
        self.assertContains(r, 'id="dash-data"')
        self.assertContains(r, "❌")
        self.assertContains(r, "Netflix")

    def test_dashboard_usd_only_usd(self):
        r = self.client.get("/?moneda=USD")
        k = r.context["kpis"]
        self.assertEqual((k["ingresos"], k["gastos"], k["balance"]), (Decimal("40"), Decimal("15"), Decimal("25")))
        self.assertEqual(k["faltan"], 0)

    def test_dashboard_empty_db(self):
        Detalle.objects.all().delete()
        Recurrente.objects.all().delete()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Todavía no hay movimientos")

    def test_dashboard_bad_params_fall_back(self):
        r = self.client.get("/?moneda=EUR&mes=basura&desde=x&hasta=y")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["moneda"], "ARS")

    def test_mes_param_selects_month(self):
        r = self.client.get("/?mes=2026-08")
        self.assertEqual(r.context["kpis"]["gastos"], Decimal("70"))


class MovimientosListTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        self.root = Categoria.objects.create(name="Servicios")
        self.child = Categoria.objects.create(name="Luz", parent=self.root)
        mov(2026, 9, 10, "-300", categoria=self.child, desc="EDESUR")
        mov(2026, 9, 11, "-50", desc="SIN CAT SEP")
        mov(2026, 8, 11, "-70", desc="SIN CAT AGO")
        mov(2026, 9, 12, "-9", moneda="USD", desc="SIN CAT USD")

    def test_sin_categoria_in_month(self):
        r = self.client.get("/movimientos/?mes=2026-09&categoria=none")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([d.descripcion for d in r.context["movimientos"]], ["SIN CAT SEP"])
        self.assertEqual(r.context["total"], Decimal("-50"))

    def test_root_category_includes_descendants(self):
        r = self.client.get(f"/movimientos/?mes=2026-09&categoria={self.root.pk}")
        self.assertEqual([d.descripcion for d in r.context["movimientos"]], ["EDESUR"])

    def test_recurrente_filter(self):
        rec = Recurrente.objects.create(
            nombre="Edesur", patron="EDESUR", manual=True, moneda="ARS", fuente="cuenta")
        r = self.client.get(f"/movimientos/?recurrente={rec.pk}")
        self.assertEqual([d.descripcion for d in r.context["movimientos"]], ["EDESUR"])

    def test_tipo_and_moneda(self):
        r = self.client.get("/movimientos/?mes=2026-09&moneda=USD&tipo=gasto")
        self.assertEqual([d.descripcion for d in r.context["movimientos"]], ["SIN CAT USD"])

    def test_bad_params_do_not_crash(self):
        self.assertEqual(self.client.get("/movimientos/?mes=zz&categoria=abc&recurrente=q").status_code, 200)


class LegacyRedirectTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))

    def test_redirects(self):
        for old in ("/balance/", "/tarjetas/", "/categorias/"):
            r = self.client.get(old)
            self.assertEqual((r.status_code, r["Location"]), (302, "/"), old)
        for old in ("/2026/09/", "/detalle/2026/09/"):
            r = self.client.get(old)
            self.assertEqual((r.status_code, r["Location"]), (302, "/movimientos/?mes=2026-09"), old)
