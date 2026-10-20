import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from movimientos.models import DataFile, Detalle, Resumen
from movimientos.parsers.base import ParsedMovement, ParseResult
from movimientos.services import importer
from movimientos.services.importer import import_file, import_files
from movimientos.tests.factories import make_account_xlsx, make_card_xlsx

D = Decimal


def mov(fecha, monto, codigo="", fuente="cuenta", moneda="ARS", cuota="", desc="x"):
    return ParsedMovement(fecha=fecha, descripcion=desc, monto=D(monto), moneda=moneda,
                          fuente=fuente, codigo=codigo, cuota=cuota)


def run(*movs):
    res = ParseResult(formato="account_xlsx", movimientos=list(movs), resumenes=[])
    with mock.patch.object(importer.parsers, "parse", return_value=res):
        return import_file("whatever.xlsx")


class DedupTests(TestCase):
    def test_reimport_same_file_adds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_account_xlsx(tmp)
            first = import_file(str(path))
            self.assertGreater(first.nuevos, 0)
            second = import_file(str(path))
        self.assertEqual((second.nuevos, second.duplicados), (0, first.nuevos))
        self.assertEqual(Detalle.objects.count(), first.nuevos)

    def test_zero_padding_cross_format(self):
        run(mov(date(2026, 10, 7), "-8.92", "11839"))
        r = run(mov(date(2026, 10, 8), "-8.92", "000011839"))
        self.assertEqual((r.nuevos, r.duplicados), (0, 1))

    def test_same_day_same_amount_different_ref_kept(self):
        movs = [mov(date(2026, 10, 13), "-15337.00", c) for c in ("406029", "406030", "406031")]
        self.assertEqual(run(*movs).nuevos, 3)
        r = run(*movs)
        self.assertEqual((r.nuevos, r.duplicados), (0, 3))

    def test_same_day_same_amount_no_ref_kept_then_deduped(self):
        movs = [mov(date(2026, 10, 13), "-24.50"), mov(date(2026, 10, 13), "-24.50")]
        self.assertEqual(run(*movs).nuevos, 2)
        r = run(*movs)
        self.assertEqual((r.nuevos, r.duplicados), (0, 2))

    def test_installment_next_cuota_is_new(self):
        run(mov(date(2026, 8, 24), "-100", "760275", "visa", cuota="14/18"))
        r = run(mov(date(2026, 9, 24), "-100", "760275", "visa", cuota="15/18"))
        self.assertEqual(r.nuevos, 1)
        self.assertEqual(Detalle.objects.count(), 2)

    def test_currency_separates(self):
        run(mov(date(2026, 8, 24), "-20", "111", "visa", "ARS"))
        r = run(mov(date(2026, 8, 24), "-20", "111", "visa", "USD"))
        self.assertEqual(r.nuevos, 1)

    def test_matches_legacy_row(self):
        Detalle.objects.create(codigo="45645645", fecha=datetime(2026, 10, 19, 10, 48), cuenta="c",
                               descripcion="legacy", monto=D("-82212.63"), ref="45645645")
        r = run(mov(date(2026, 10, 19), "-82212.63", "000045645645"))
        self.assertEqual((r.nuevos, r.duplicados), (0, 1))

    def test_matches_legacy_float_amount(self):
        Detalle.objects.bulk_create([Detalle(
            codigo="1", fecha=datetime(2026, 10, 19, 10, 48), cuenta="c", descripcion="legacy",
            monto=79999.93, ref="1")])
        r = run(mov(date(2026, 10, 19), "79999.93", "1"))
        self.assertEqual((r.nuevos, r.duplicados), (0, 1))

    def test_empty_ref_on_either_side_is_duplicate(self):
        run(mov(date(2026, 9, 23), "-5.00", "", "visa", "USD"))
        r = run(mov(date(2026, 9, 23), "-5.00", "302190", "visa", "USD"))
        self.assertEqual((r.nuevos, r.duplicados), (0, 1))
        run(mov(date(2026, 9, 23), "-7.00", "302190", "visa", "USD"))
        r = run(mov(date(2026, 9, 23), "-7.00", "", "visa", "USD"))
        self.assertEqual((r.nuevos, r.duplicados), (0, 1))

    def test_kind_and_resumen_upsert(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = make_card_xlsx(tmp)
            import_file(str(card))
            import_file(str(card))
        self.assertTrue(Detalle.objects.filter(fuente="visa").exists())
        self.assertFalse(Detalle.objects.filter(fuente="visa").exclude(tipo="gasto").exists())
        self.assertEqual(Resumen.objects.filter(fuente="visa").count(), 1)
        run(mov(date(2026, 10, 1), "100"), mov(date(2026, 10, 2), "-5"))
        self.assertEqual(Detalle.objects.get(monto=100).tipo, "ingreso")
        self.assertEqual(Detalle.objects.get(monto=-5).tipo, "gasto")

    def test_exception_rolls_back_file(self):
        res = ParseResult(formato="account_xlsx", resumenes=[],
                          movimientos=[mov(date(2026, 10, 1), "1"), mov(date(2026, 10, 2), "2")])
        real = Detalle.objects.create
        calls = []

        def flaky(**kw):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(**kw)
        with mock.patch.object(importer.parsers, "parse", return_value=res), \
                mock.patch.object(Detalle.objects, "create", side_effect=flaky):
            r = import_file("x.xlsx")
        self.assertTrue(r.rechazado)
        self.assertEqual(len(r.errores), 1)
        self.assertEqual(Detalle.objects.count(), 0)


class ImportFilesTests(TestCase):
    def test_unrecognised_file_rejected_others_imported(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as media:
            bad = Path(tmp) / "bad.txt"
            bad.write_text("nothing")
            good = make_account_xlsx(tmp)
            with override_settings(MEDIA_ROOT=media):
                reports = import_files([str(bad), str(good)])
            self.assertTrue(reports[0].rechazado)
            self.assertGreater(reports[1].nuevos, 0)
            self.assertEqual(DataFile.objects.count(), 2)
            self.assertTrue(bad.exists() and good.exists())
            self.assertEqual(Detalle.objects.first().archivo_id, DataFile.objects.last().pk)
