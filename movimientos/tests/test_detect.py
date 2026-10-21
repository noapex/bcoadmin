import os
import tempfile
from decimal import Decimal
from pathlib import Path

import openpyxl
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from movimientos import parsers
from movimientos.models import Detalle
from movimientos.parsers import account_xlsx, card_xlsx, legacy_xls
from movimientos.tests.factories import make_account_xlsx, make_card_xlsx


class DetectTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_detects_card_and_account_xlsx(self):
        self.assertIs(parsers.detect(make_card_xlsx(self.tmp)), card_xlsx)
        self.assertIs(parsers.detect(make_account_xlsx(self.tmp)), account_xlsx)

    def test_parse_dispatches(self):
        result = parsers.parse(make_account_xlsx(self.tmp))
        self.assertEqual(result.formato, "account_xlsx")

    def test_unrecognised_files_return_none(self):
        empty = self.tmp / "empty.xlsx"
        openpyxl.Workbook().save(empty)
        txt = self.tmp / "a.txt"
        txt.write_text("hola")
        blob = self.tmp / "b.bin"
        blob.write_bytes(os.urandom(256))
        for p in (empty, txt, blob):
            self.assertIsNone(parsers.detect(p), p.name)
            self.assertIsNone(parsers.parse(p), p.name)


class LegacyRowsTests(SimpleTestCase):
    HEADER = ["", "Fecha", "Sucursal origen", "Descripción", "Referencia",
              "Importe caja de ahorro pesos", "Importe cuenta corriente pesos", "Saldo pesos"]

    def test_parses_rows_and_skips_noise(self):
        rows = [
            ["", "Cuenta", "Cuenta única"],
            self.HEADER,
            ["", "10/03/2019 19:09", "0000 CASA CENTRAL", "PAGO   ELECTRONICO  X", "072533539", -215.42, "", ""],
            ["", "09/03/2019 10:00", "0009 ", "Deposito", "000123", "", "1.500,50", ""],
            ["", "09/03/2019 10:00", "0009 ", "Solo dolares", "000124", "", "", ""],
            ["", "Total", "", "", "", "", "", ""],
        ]
        r = legacy_xls.parse_rows(rows)
        self.assertEqual(len(r.movimientos), 2)
        self.assertEqual(r.omitidas, 1)
        m = r.movimientos[0]
        self.assertEqual((m.monto, m.moneda, m.fuente, m.codigo), (Decimal("-215.42"), "ARS", "cuenta", "072533539"))
        self.assertEqual(m.descripcion, "PAGO ELECTRONICO X")
        self.assertEqual(r.movimientos[1].monto, Decimal("1500.50"))

    def test_swapped_columns_and_duplicate_sheet_rows(self):
        header = ["", "Fecha", "Sucursal Origen", "Referencia", "Descripcion", "Importe C. Ahorro Pesos"]
        row = ["", 43195.9, "0000 Casa Central", "027698142", "Pago", -1668.87]
        seen = set()
        r = legacy_xls.parse_rows([header, row], 0, None, seen)
        legacy_xls.parse_rows([header, row], 0, r, seen)
        self.assertEqual(len(r.movimientos), 1)
        self.assertEqual(r.movimientos[0].descripcion, "Pago")

    def test_matches_rejects_missing_file(self):
        self.assertFalse(legacy_xls.matches("/nonexistent/file.xlsx"))

    def test_binary_xls_detected_by_content_not_extension(self):
        # The old sync renamed binary .xls downloads to .xlsx.
        with tempfile.TemporaryDirectory() as tmp:
            renamed = Path(tmp) / "UltimosMovimientos_1.xlsx"
            renamed.write_bytes(legacy_xls.CFB_MAGIC + b"\0" * 64)
            zipped = Path(tmp) / "real.xlsx"
            zipped.write_bytes(b"PK\x03\x04" + b"\0" * 64)
            self.assertTrue(legacy_xls.is_binary_xls(renamed))
            self.assertFalse(legacy_xls.is_binary_xls(zipped))


class DetalleDecimalTests(TestCase):
    def test_monto_round_trips_exactly(self):
        d = Detalle.objects.create(codigo="1", fecha=timezone.now(), cuenta="c", descripcion="d",
                                   monto=Decimal("-15337.10"))
        d.refresh_from_db()
        self.assertEqual(d.monto, Decimal("-15337.10"))
