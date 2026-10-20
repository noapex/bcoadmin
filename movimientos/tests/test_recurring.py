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
