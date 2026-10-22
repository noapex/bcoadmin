import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from movimientos.models import Categoria, DataFile, Detalle, Recurrente, Tarjeta
from movimientos.services.classify import CAT_SIN_DETALLE
from movimientos.tests.factories import make_account_xlsx


class LoginRequiredTests(TestCase):
    def test_dashboard_redirects_anonymous_to_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)


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
            ultima_fecha=date(2026, 8, 1), dia_habitual=1, estado_admin="confirmado")

    def test_dashboard_ars(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        k = r.context["kpis"]
        self.assertEqual((k["ingresos"], k["gastos"], k["balance"]), (Decimal("1000"), Decimal("350"), Decimal("650")))
        self.assertEqual(k["faltan"], 1)
        self.assertContains(r, 'id="dash-data"')
        self.assertContains(r, "❌")
        self.assertContains(r, "Netflix")

    def test_inactivo_not_counted_and_listed_last(self):
        Recurrente.objects.create(
            nombre="Aaa viejo", clave="viejo", moneda="ARS", fuente="cuenta",
            ultima_fecha=date(2026, 6, 1), dia_habitual=1)
        Recurrente.objects.create(
            nombre="Zzz al dia", clave="aldia", moneda="ARS", fuente="cuenta",
            ultima_fecha=date(2026, 9, 1), dia_habitual=1)
        r = self.client.get("/")
        self.assertEqual(r.context["kpis"]["faltan"], 1)
        self.assertEqual([(i["rec"].nombre, i["estado"]) for i in r.context["recurrentes"]],
                         [("Netflix", "falta"), ("Zzz al dia", "pagado"), ("Aaa viejo", "inactivo")])
        self.assertContains(r, "💤")
        self.assertContains(r, "· inactivo")

    def test_category_slice_link_filters_gastos(self):
        mov(2026, 9, 13, "25", categoria=self.cat, desc="REINTEGRO")
        r = self.client.get("/")
        self.assertContains(r, '"&tipo=gasto&categoria="')
        lista = self.client.get(f"/movimientos/?mes=2026-09&tipo=gasto&categoria={self.cat.pk}")
        slice_total = next(c["total"] for c in r.context["data"]["categorias"] if c["id"] == self.cat.pk)
        self.assertEqual(abs(lista.context["total"]), Decimal(str(slice_total)))

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

    def test_header_shows_names_not_ids(self):
        rec = Recurrente.objects.create(
            nombre="Luz Edesur", patron="EDESUR", manual=True, moneda="ARS", fuente="cuenta")
        r = self.client.get(f"/movimientos/?mes=2026-09&categoria={self.child.pk}&recurrente={rec.pk}")
        self.assertContains(r, "categoría: Luz")
        self.assertContains(r, "recurrente Luz Edesur")
        self.assertNotContains(r, f"#{self.child.pk}")

    def test_tipo_and_moneda(self):
        r = self.client.get("/movimientos/?mes=2026-09&moneda=USD&tipo=gasto")
        self.assertEqual([d.descripcion for d in r.context["movimientos"]], ["SIN CAT USD"])

    def test_bad_params_do_not_crash(self):
        self.assertEqual(self.client.get("/movimientos/?mes=zz&categoria=abc&recurrente=q").status_code, 200)


class LegacyRedirectTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))

    def test_redirects(self):
        r = self.client.get("/balance/")
        self.assertEqual((r.status_code, r["Location"]), (302, "/"))
        for page in ("/tarjetas/", "/categorias/"):
            self.assertEqual(self.client.get(page).status_code, 200, page)
        for old in ("/2026/09/", "/detalle/2026/09/"):
            r = self.client.get(old)
            self.assertEqual((r.status_code, r["Location"]), (302, "/movimientos/?mes=2026-09"), old)


class AmountFormatTests(TestCase):
    def test_thousands_use_es_ar_format(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        mov(2026, 9, 5, "1234567.5")
        self.assertContains(self.client.get("/"), "1.234.567,50")
        self.assertContains(self.client.get("/movimientos/?mes=2026-09"), "1.234.567,50")

    def test_currency_toggle_keeps_period(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        mov(2026, 9, 5, "10")
        self.assertContains(self.client.get("/"), "?moneda=USD&amp;desde=2025-10&amp;hasta=2026-09&amp;mes=2026-09".replace("&amp;", "&"))


class CategoriasPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        self.root = Categoria.objects.create(name="Servicios")
        self.child = Categoria.objects.create(name="Luz", parent=self.root)
        mov(2026, 9, 10, "-300", categoria=self.child, desc="EDESUR")
        mov(2026, 8, 11, "-1234.5", desc="SIN CAT AGO")
        mov(2026, 9, 12, "-77", moneda="USD", categoria=self.root, desc="USD")

    def test_renders_tree_with_links(self):
        r = self.client.get(reverse("categorias"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.context["desde"], r.context["hasta"]), ("2025-10", "2026-09"))
        self.assertContains(r, "Servicios")
        self.assertContains(r, "Luz")
        self.assertContains(r, "1.234,50")
        self.assertContains(r, f"/movimientos/?moneda=ARS&mes=2026-09&tipo=gasto&categoria={self.root.pk}")
        self.assertContains(r, f"/movimientos/?moneda=ARS&mes=2026-09&tipo=gasto&categoria={self.child.pk}")
        self.assertContains(r, "/movimientos/?moneda=ARS&mes=2026-08&tipo=gasto&categoria=none")
        self.assertNotContains(r, "mes=2026-07&tipo=gasto")  # zero cells are not links
        self.assertContains(r, reverse("categorias_gestionar"))

    def test_usd_isolated(self):
        r = self.client.get(reverse("categorias") + "?moneda=USD")
        filas = {f["nombre"]: f for f in r.context["tabla"]["filas"]}
        self.assertEqual(filas["Servicios"]["total"], Decimal("77"))
        self.assertEqual(r.context["tabla"]["total"]["total"], Decimal("77"))
        self.assertNotContains(r, "1.234,50")

    def test_period_params(self):
        r = self.client.get(reverse("categorias") + "?desde=2026-09&hasta=2026-09")
        self.assertEqual(r.context["tabla"]["meses"], ["2026-09"])
        self.assertNotContains(r, "1.234,50")

    def test_empty_db(self):
        Detalle.objects.all().delete()
        r = self.client.get(reverse("categorias"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Todavía no hay movimientos")


class TarjetasPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        Tarjeta.objects.create(nombre="Visa Gold", codigo="1", fuente="visa", patron_pago="pago visa")
        self.sin = Categoria.objects.create(name=CAT_SIN_DETALLE)
        Detalle.objects.create(codigo="", fecha=datetime(2026, 9, 3), cuenta="Visa 5544", descripcion="Compra",
                               monto=Decimal("-1500"), fuente="visa", tipo="gasto")
        Detalle.objects.create(codigo="", fecha=datetime(2026, 9, 4), cuenta="Visa 5544", descripcion="Compra usd",
                               monto=Decimal("-12"), fuente="visa", tipo="gasto", moneda="USD")
        mov(2026, 8, 10, "-400", categoria=self.sin, desc="PAGO VISA")

    def test_sections_rows_and_links(self):
        r = self.client.get(reverse("tarjetas"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Pesos")
        self.assertContains(r, "Dólares")
        self.assertContains(r, "Visa Gold · 5544")
        self.assertContains(r, "Visa Gold · pagos sin detalle")
        self.assertContains(r, "1.500,00")
        self.assertContains(r, "/movimientos/?moneda=ARS&mes=2026-09&tipo=gasto&fuente=visa")
        self.assertContains(r, "/movimientos/?moneda=USD&mes=2026-09&tipo=gasto&fuente=visa")
        self.assertContains(r, f"/movimientos/?moneda=ARS&mes=2026-08&tipo=gasto&fuente=cuenta&categoria={self.sin.pk}")
        self.assertContains(r, "Mes sin resumen importado")

    def test_currencies_not_mixed(self):
        r = self.client.get(reverse("tarjetas"))
        ars, usd = r.context["secciones"]
        self.assertEqual((ars["moneda"], usd["moneda"]), ("ARS", "USD"))
        self.assertEqual(ars["tabla"]["total"]["total"], Decimal("1900"))
        self.assertEqual(usd["tabla"]["total"]["total"], Decimal("12"))

    def test_empty_db(self):
        Detalle.objects.all().delete()
        r = self.client.get(reverse("tarjetas"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Todavía no hay movimientos")


class FuenteFilterTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        mov(2026, 9, 10, "-300", desc="CUENTA")
        mov(2026, 9, 11, "-50", desc="VISA", fuente="visa")

    def test_filters_and_shows_header(self):
        r = self.client.get("/movimientos/?mes=2026-09&fuente=visa")
        self.assertEqual([d.descripcion for d in r.context["movimientos"]], ["VISA"])
        self.assertContains(r, "fuente: visa")

    def test_unknown_fuente_ignored(self):
        r = self.client.get("/movimientos/?mes=2026-09&fuente=zzz")
        self.assertEqual(len(r.context["movimientos"]), 2)


class PeriodoClampTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        mov(2026, 9, 10, "-300", desc="X")

    def test_huge_span_is_capped_to_60_months(self):
        for url in ("/", "/categorias/", "/tarjetas/"):
            r = self.client.get(url + "?desde=2000-01&hasta=2026-09")
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual((r.context["desde"], r.context["hasta"]), ("2021-10", "2026-09"), url)

    def test_out_of_range_years_fall_back_to_defaults(self):
        for url in ("/", "/categorias/", "/tarjetas/", "/movimientos/"):
            for q in ("?desde=0001-01&hasta=9999-12", "?desde=0000-01&hasta=0000-02&mes=0000-01",
                      "?desde=1999-12&hasta=2101-01"):
                r = self.client.get(url + q)
                self.assertEqual(r.status_code, 200, url + q)
                if url != "/movimientos/":
                    self.assertEqual((r.context["desde"], r.context["hasta"]), ("2025-10", "2026-09"), url + q)


class TarjetaFilterTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        Tarjeta.objects.create(nombre="Visa", codigo="1", fuente="visa", patron_pago="pago visa")
        Tarjeta.objects.create(nombre="American", codigo="3", fuente="amex", patron_pago="amex")
        Tarjeta.objects.create(nombre="Naranja", codigo="9", fuente="naranja", patron_pago="naranja")
        self.sin = Categoria.objects.create(name=CAT_SIN_DETALLE)
        mov(2026, 9, 10, "-400", categoria=self.sin, desc="PAGO VISA")
        mov(2026, 9, 11, "-100", categoria=self.sin, desc="Pago tarjeta amex")
        mov(2026, 9, 12, "-7", categoria=self.sin, desc="Pago tarjeta desconocida")
        mov(2026, 9, 13, "-3", categoria=self.sin, desc="Pago naranja")
        mov(2026, 9, 14, "-50", desc="PAGO VISA sin categoria")

    def _list(self, tarjeta):
        return self.client.get(f"/movimientos/?moneda=ARS&mes=2026-09&tipo=gasto&fuente=cuenta"
                               f"&categoria={self.sin.pk}&tarjeta={tarjeta}")

    def test_cell_links_carry_card_and_totals_match(self):
        r = self.client.get(reverse("tarjetas"))
        ars = r.context["secciones"][0]["tabla"]
        for fila in ars["filas"]:
            clave = fila["fuente"] or "otras"
            self.assertContains(r, f"&categoria={self.sin.pk}&tarjeta={clave}")
            lista = self._list(clave)
            self.assertEqual(-lista.context["total"], fila["total"], clave)

    def test_filters_by_card(self):
        r = self._list("visa")
        self.assertEqual([d.descripcion for d in r.context["movimientos"]], ["PAGO VISA"])
        self.assertContains(r, "pagos sin detalle de visa")
        r = self._list("otras")
        self.assertEqual(sorted(d.descripcion for d in r.context["movimientos"]),
                         ["Pago naranja", "Pago tarjeta desconocida"])

    def test_unknown_value_ignored(self):
        r = self.client.get("/movimientos/?mes=2026-09&tarjeta=zzz")
        self.assertEqual(len(r.context["movimientos"]), 5)


class MovimientosPeriodoTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        for d, monto, moneda in [(date(2026, 7, 5), "-10.00", "ARS"), (date(2026, 8, 5), "-20.00", "ARS"),
                                 (date(2026, 9, 5), "-30.00", "ARS"), (date(2026, 8, 6), "-5.00", "USD")]:
            Detalle.objects.create(codigo="1", fecha=datetime(d.year, d.month, d.day), cuenta="c",
                                   descripcion=f"mov {d}", monto=Decimal(monto), moneda=moneda, tipo="gasto")

    def _descs(self, resp):
        return sorted(m.descripcion for m in resp.context["movimientos"])

    def test_default_is_last_month_with_data(self):
        r = self.client.get("/movimientos/")
        self.assertEqual(self._descs(r), ["mov 2026-09-05"])
        self.assertContains(r, 'name="desde"')
        self.assertContains(r, 'name="hasta"')

    def test_range_filter(self):
        r = self.client.get("/movimientos/?desde=2026-07&hasta=2026-08")
        self.assertEqual(self._descs(r), ["mov 2026-07-05", "mov 2026-08-05"])
        self.assertEqual(r.context["total"], Decimal("-30.00"))

    def test_mes_is_shorthand_for_single_month(self):
        r = self.client.get("/movimientos/?mes=2026-08")
        self.assertEqual(self._descs(r), ["mov 2026-08-05"])
        self.assertEqual((r.context["desde"], r.context["hasta"]), ("2026-08", "2026-08"))

    def test_reversed_range_is_swapped(self):
        r = self.client.get("/movimientos/?desde=2026-09&hasta=2026-07")
        self.assertEqual(len(self._descs(r)), 3)

    def test_form_keeps_other_filters(self):
        r = self.client.get("/movimientos/?mes=2026-08&tipo=gasto&categoria=none&fuente=cuenta")
        for name, value in [("tipo", "gasto"), ("categoria", "none"), ("fuente", "cuenta"), ("moneda", "ARS")]:
            self.assertContains(r, f'type="hidden" name="{name}" value="{value}"')

    def test_currency_toggle_keeps_range(self):
        r = self.client.get("/movimientos/?desde=2026-07&hasta=2026-08&moneda=USD")
        self.assertEqual(self._descs(r), ["mov 2026-08-06"])
        self.assertContains(r, "?moneda=ARS&desde=2026-07&hasta=2026-08")


class MovimientosParcialTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        Detalle.objects.create(codigo="1", fecha=datetime(2026, 9, 5), cuenta="c", descripcion="a",
                               monto=Decimal("-1234.50"), moneda="ARS", tipo="gasto")

    def test_rows_carry_raw_amount_and_partial_placeholders(self):
        r = self.client.get("/movimientos/?mes=2026-09")
        self.assertContains(r, 'data-monto="-1234.50"')
        self.assertContains(r, 'id="parcial"')
        self.assertContains(r, 'id="parcial-fila"')
