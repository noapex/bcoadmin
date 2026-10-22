from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase

from movimientos.models import Detalle, Recurrente, Resumen
from movimientos.services import recurring
from movimientos.services.recurring import detectar_recurrentes, estado, fecha_referencia


def gasto(comercio, y, m, d, monto="-1000", fuente="cuenta", moneda="ARS"):
    return Detalle.objects.create(
        codigo="", fecha=datetime(y, m, d), cuenta="c", descripcion=comercio.upper(),
        monto=Decimal(monto), fuente=fuente, moneda=moneda, tipo="gasto", comercio=comercio)


def monthly(comercio, months, day, **kw):
    for y, m in months:
        gasto(comercio, y, m, day, **kw)


MAY_SEP = [(2026, m) for m in range(5, 10)]


class RecurringTests(TestCase):
    def test_monthly_detected(self):
        monthly("edesur", MAY_SEP, 17)
        self.assertEqual(detectar_recurrentes(), 1)
        rec = Recurrente.objects.get()
        self.assertEqual((rec.clave, rec.frecuencia_meses, rec.dia_habitual), ("edesur", 1, 17))
        self.assertEqual(rec.nombre, "Edesur")
        self.assertEqual(rec.ultima_fecha, date(2026, 9, 17))
        self.assertEqual(rec.monto_ref, Decimal("1000"))

    def test_bimonthly_detected(self):
        monthly("aysa", [(2026, m) for m in (1, 3, 5, 7, 9)], 5)
        detectar_recurrentes()
        self.assertEqual(Recurrente.objects.get().frecuencia_meses, 2)

    def test_two_contracts_two_items(self):
        monthly("fed pat 111", MAY_SEP, 3)
        monthly("fed pat 222", MAY_SEP, 3)
        detectar_recurrentes()
        self.assertEqual(Recurrente.objects.count(), 2)

    def test_fewer_than_three_months_ignored(self):
        monthly("x", [(2026, 8), (2026, 9)], 3)
        self.assertEqual(detectar_recurrentes(), 0)
        self.assertEqual(Recurrente.objects.count(), 0)

    def test_ignores_non_gasto_and_inactive(self):
        monthly("y", MAY_SEP, 3)
        Detalle.objects.update(tipo="interno")
        self.assertEqual(detectar_recurrentes(), 0)
        Detalle.objects.update(tipo="gasto", activo=False)
        self.assertEqual(detectar_recurrentes(), 0)

    def test_window_is_13_months(self):
        # Three months, but two are older than 13 months before the reference.
        gasto("z", 2024, 1, 5)
        gasto("z", 2024, 2, 5)
        monthly("z", [(2026, 9)], 5)
        self.assertEqual(detectar_recurrentes(), 0)

    def test_missing_month_is_falta(self):
        monthly("m", [(2026, 6), (2026, 7), (2026, 8)], 10)
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        self.assertEqual(estado(rec, date(2026, 9, 24)), "falta")
        self.assertEqual(estado(rec, date(2026, 9, 12)), "pendiente")
        self.assertEqual(estado(rec, date(2026, 9, 5)), "pagado")

    def test_reference_is_latest_import_not_calendar(self):
        monthly("m", [(2026, 6), (2026, 7), (2026, 8)], 10)
        Resumen.objects.create(fuente="cuenta", hasta=date(2026, 9, 5))
        self.assertEqual(fecha_referencia("cuenta"), date(2026, 9, 5))
        detectar_recurrentes()
        self.assertEqual(estado(Recurrente.objects.get()), "pagado")

    def test_reference_falls_back_to_latest_detalle(self):
        monthly("m", [(2026, 6), (2026, 7)], 10)
        self.assertEqual(fecha_referencia("cuenta"), date(2026, 7, 10))
        self.assertIsNone(fecha_referencia("visa"))

    def test_late_payment_is_atrasado(self):
        gasto("l", 2026, 6, 10)
        gasto("l", 2026, 7, 10)
        gasto("l", 2026, 8, 25)
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        self.assertEqual(estado(rec, date(2026, 9, 1)), "atrasado")

    def test_silenced_status(self):
        monthly("m", [(2026, 6), (2026, 7), (2026, 8)], 10)
        detectar_recurrentes()
        Recurrente.objects.update(estado_admin="silenciado")
        self.assertEqual(estado(Recurrente.objects.get(), date(2026, 12, 1)), "silenciado")

    def test_detection_keeps_owner_edits(self):
        monthly("edesur", MAY_SEP, 17)
        detectar_recurrentes()
        Recurrente.objects.update(nombre="Luz", nombre_editado=True, estado_admin="silenciado")
        gasto("edesur", 2026, 10, 17, monto="-2000")
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        self.assertEqual((rec.nombre, rec.estado_admin), ("Luz", "silenciado"))
        self.assertEqual(rec.monto_ref, Decimal("2000"))
        self.assertEqual(rec.ultima_fecha, date(2026, 10, 17))

    def test_manual_items_not_recomputed(self):
        monthly("edesur", MAY_SEP, 17)
        Recurrente.objects.create(nombre="E", clave="edesur", moneda="ARS", fuente="cuenta",
                                  manual=True, frecuencia_meses=3, dia_habitual=2)
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        self.assertEqual((rec.frecuencia_meses, rec.dia_habitual), (3, 2))

    def test_same_clave_two_fuentes_most_recent_wins(self):
        monthly("netflix", [(2026, 6), (2026, 7), (2026, 8)], 4)
        monthly("netflix", [(2026, 7), (2026, 8), (2026, 9)], 4, fuente="visa")
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        self.assertEqual(rec.fuente, "visa")

    def test_currencies_separate(self):
        monthly("aws", MAY_SEP, 3)
        monthly("aws", MAY_SEP, 3, moneda="USD")
        detectar_recurrentes()
        self.assertEqual(Recurrente.objects.count(), 2)

    def test_movimientos_de(self):
        monthly("edesur", MAY_SEP, 17)
        gasto("otro", 2026, 9, 1)
        detectar_recurrentes()
        self.assertEqual(recurring.movimientos_de(Recurrente.objects.get()).count(), 5)
        man = Recurrente.objects.create(nombre="O", patron="OTR", moneda="ARS", fuente="cuenta", manual=True)
        self.assertEqual(recurring.movimientos_de(man).count(), 1)


class ImporterWiringTests(TestCase):
    def test_import_files_runs_detection_and_reports_failure(self):
        from unittest import mock
        from movimientos.services import importer
        with mock.patch.object(importer, "detectar_recurrentes") as det:
            importer.import_files([])
            det.assert_called_once_with()
        with mock.patch.object(importer, "detectar_recurrentes", side_effect=RuntimeError("boom")):
            reports = importer.import_files([])
        self.assertIn("recurrentes fallida", reports[-1].errores[0])


class OwnerEditsAndManualRefreshTests(TestCase):
    def test_edited_parameters_survive_detection(self):
        monthly("edesur", MAY_SEP, 17)
        detectar_recurrentes()
        Recurrente.objects.update(frecuencia_meses=2, dia_habitual=3, parametros_editados=True)
        gasto("edesur", 2026, 10, 17, monto="-2000")
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        self.assertEqual((rec.frecuencia_meses, rec.dia_habitual, rec.fuente), (2, 3, "cuenta"))
        self.assertEqual((rec.ultima_fecha, rec.monto_ref), (date(2026, 10, 17), Decimal("2000")))

    def test_edited_fuente_survives_detection(self):
        monthly("edesur", MAY_SEP, 17)
        monthly("edesur", MAY_SEP, 20, fuente="visa")
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        otra = "cuenta" if rec.fuente == "visa" else "visa"
        Recurrente.objects.update(fuente=otra, parametros_editados=True)
        detectar_recurrentes()
        self.assertEqual(Recurrente.objects.get().fuente, otra)

    def test_manual_item_refreshes_last_date_and_amount(self):
        rec = Recurrente.objects.create(nombre="Gym", patron="GIMNASIO", moneda="ARS", fuente="cuenta",
                                        manual=True, frecuencia_meses=3, dia_habitual=2)
        gasto("gimnasio", 2026, 6, 2, monto="-500")
        gasto("gimnasio", 2026, 9, 4, monto="-800")
        g = gasto("gimnasio", 2026, 9, 30, monto="-900")
        g.tipo = "interno"
        g.save()
        gasto("gimnasio", 2026, 9, 25, moneda="USD", monto="-5")
        detectar_recurrentes()
        rec.refresh_from_db()
        self.assertEqual((rec.ultima_fecha, rec.monto_ref), (date(2026, 9, 4), Decimal("800")))
        self.assertEqual((rec.frecuencia_meses, rec.dia_habitual), (3, 2))

    def test_admin_edit_marks_parametros_editados(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        self.client.force_login(get_user_model().objects.create_superuser("adm", "a@x.com", "pw"))
        rec = Recurrente.objects.create(nombre="Edesur", clave="edesur", moneda="ARS", fuente="cuenta",
                                        frecuencia_meses=1, dia_habitual=17)
        url = reverse("admin:movimientos_recurrente_change", args=[rec.pk])
        data = {"nombre": "Edesur", "clave": "edesur", "patron": "", "moneda": "ARS", "fuente": "cuenta",
                "frecuencia_meses": 1, "dia_habitual": 17, "estado_admin": "confirmado"}
        self.client.post(url, data)
        rec.refresh_from_db()
        self.assertFalse(rec.parametros_editados)
        data["dia_habitual"] = 20
        self.assertEqual(self.client.post(url, data).status_code, 302)
        rec.refresh_from_db()
        self.assertTrue(rec.parametros_editados)


class InactivoAndReferenceTests(TestCase):
    def test_missed_two_periods_is_inactivo(self):
        monthly("m", [(2026, 6), (2026, 7), (2026, 8)], 10)
        detectar_recurrentes()
        rec = Recurrente.objects.get()
        # expected 2026-09-10; second expected 2026-10-10; tolerance 7 days
        self.assertEqual(estado(rec, date(2026, 10, 16)), "falta")
        self.assertEqual(estado(rec, date(2026, 10, 17)), "inactivo")
        Recurrente.objects.update(frecuencia_meses=3)
        rec.refresh_from_db()
        # expected 2026-11-10; second expected 2027-02-10
        self.assertEqual(estado(rec, date(2027, 2, 16)), "falta")
        self.assertEqual(estado(rec, date(2027, 2, 17)), "inactivo")

    def test_reference_is_max_of_statement_and_movements(self):
        monthly("m", [(2026, 6), (2026, 7)], 10)
        Resumen.objects.create(fuente="cuenta", hasta=date(2026, 7, 1))
        self.assertEqual(fecha_referencia("cuenta"), date(2026, 7, 10))
        Resumen.objects.create(fuente="cuenta", hasta=date(2026, 8, 1))
        self.assertEqual(fecha_referencia("cuenta"), date(2026, 8, 1))
        Resumen.objects.create(fuente="visa", hasta=date(2026, 5, 1))
        self.assertEqual(fecha_referencia("visa"), date(2026, 5, 1))


def mensual(comercio, months, day, montos=None, categoria=None, **kw):
    for i, (y, m) in enumerate(months):
        d = gasto(comercio, y, m, day, monto=montos[i] if montos else "-1000", **kw)
        if categoria is not None:
            Detalle.objects.filter(pk=d.pk).update(categoria=categoria)


class SuggestionFilterTests(TestCase):
    def test_multiple_times_per_month_excluded(self):
        for y, m in MAY_SEP:
            gasto("carniceria el toro", y, m, 3)
            gasto("carniceria el toro", y, m, 18)
        self.assertEqual(detectar_recurrentes(), 0)
        self.assertFalse(Recurrente.objects.exists())

    def test_occasional_double_month_still_detected(self):
        monthly("edesur", MAY_SEP, 17)
        gasto("edesur", 2026, 7, 28)  # one extra charge: median per month stays 1
        self.assertEqual(detectar_recurrentes(), 1)

    def test_card_installments_excluded(self):
        for n, (y, m) in enumerate(MAY_SEP, start=1):
            d = gasto("grimoldi", y, m, 10, fuente="visa")
            Detalle.objects.filter(pk=d.pk).update(cuota=f"{n}/6")
        self.assertEqual(detectar_recurrentes(), 0)

    def test_non_suggesting_category_excluded_including_descendants(self):
        from movimientos.models import Categoria
        impuestos = Categoria.objects.create(name="Impuestos", sugiere_recurrentes=False)
        sellos = Categoria.objects.create(name="Sellos", parent=impuestos)
        servicios = Categoria.objects.create(name="Servicios")
        mensual("impuesto de sellos", MAY_SEP, 5, categoria=sellos)
        mensual("sircreb", MAY_SEP, 6, categoria=impuestos)
        mensual("edesur", MAY_SEP, 17, categoria=servicios)
        mensual("sin categoria", MAY_SEP, 9)
        self.assertEqual(detectar_recurrentes(), 2)
        self.assertEqual(set(Recurrente.objects.values_list("clave", flat=True)), {"edesur", "sin categoria"})


class PuntajeTests(TestCase):
    def test_automatic_debit_scores_above_pos_purchase(self):
        mensual("debito automatico seguro auto", MAY_SEP, 13)
        mensual("compra con tarjeta de debito coto", MAY_SEP, 13)
        detectar_recurrentes()
        p = dict(Recurrente.objects.values_list("clave", "puntaje"))
        # +40 debit +20 stable amount +15 stable day
        self.assertEqual(p["debito automatico seguro auto"], 75)
        # -30 POS +20 +15
        self.assertEqual(p["compra con tarjeta de debito coto"], 5)

    def test_card_subscription_not_penalised_as_pos(self):
        mensual("merpago spotify", MAY_SEP, 4, fuente="visa")
        detectar_recurrentes()
        self.assertEqual(Recurrente.objects.get().puntaje, 60)  # +25 card monthly +20 +15

    def test_unstable_amount_and_day_score_low(self):
        days = [1, 28, 10, 20, 5]
        montos = ["-100", "-900", "-300", "-2000", "-50"]
        for (y, m), d, monto in zip(MAY_SEP, days, montos):
            gasto("kiosco", y, m, d, monto=monto)
        detectar_recurrentes()
        self.assertEqual(Recurrente.objects.get().puntaje, 0)

    def test_score_clamped_to_0_100(self):
        mensual("pago de servicios debito automatico netflix", MAY_SEP, 4, fuente="visa")
        for (y, m), d, monto in zip(MAY_SEP, [1, 28, 10, 20, 5], ["-100", "-900", "-300", "-2000", "-50"]):
            gasto("compra con tarjeta de debito kiosco", y, m, d, monto=monto)
        detectar_recurrentes()
        self.assertEqual(dict(Recurrente.objects.values_list("clave", "puntaje")),
                         {"pago de servicios debito automatico netflix": 100,
                          "compra con tarjeta de debito kiosco": 0})


class StaleSuggestionTests(TestCase):
    def test_stale_sugerido_deleted_others_kept(self):
        from movimientos.models import Categoria
        cat = Categoria.objects.create(name="Gastos varios")
        for clave in ("borrar", "confirmado", "silenciado", "editado", "params"):
            mensual(clave, MAY_SEP, 5, categoria=cat)
        manual = Recurrente.objects.create(nombre="M", clave="manual", moneda="ARS", fuente="cuenta",
                                           manual=True)
        detectar_recurrentes()
        Recurrente.objects.filter(clave="confirmado").update(estado_admin="confirmado")
        Recurrente.objects.filter(clave="silenciado").update(estado_admin="silenciado")
        Recurrente.objects.filter(clave="editado").update(nombre="Ed", nombre_editado=True)
        Recurrente.objects.filter(clave="params").update(parametros_editados=True)
        Categoria.objects.filter(pk=cat.pk).update(sugiere_recurrentes=False)
        self.assertEqual(detectar_recurrentes(), 0)
        self.assertEqual(set(Recurrente.objects.values_list("clave", flat=True)),
                         {"confirmado", "silenciado", "editado", "params", "manual"})
        self.assertTrue(Recurrente.objects.filter(pk=manual.pk).exists())

    def test_detection_never_changes_estado_admin(self):
        mensual("a", MAY_SEP, 5)
        mensual("b", MAY_SEP, 6)
        detectar_recurrentes()
        Recurrente.objects.filter(clave="a").update(estado_admin="confirmado")
        Recurrente.objects.filter(clave="b").update(estado_admin="silenciado")
        gasto("a", 2026, 10, 5)
        gasto("b", 2026, 10, 6)
        detectar_recurrentes()
        self.assertEqual(dict(Recurrente.objects.values_list("clave", "estado_admin")),
                         {"a": "confirmado", "b": "silenciado"})
