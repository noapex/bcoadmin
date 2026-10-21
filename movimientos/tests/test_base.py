from datetime import date
from decimal import Decimal
from unittest import TestCase

from movimientos.parsers.base import norm_cuota, norm_ref, parse_amount, parse_date


class BaseHelpersTests(TestCase):
    def test_parse_amount_formats(self):
        cases = {
            "$159.654,11": Decimal("159654.11"), "$-31.041,94": Decimal("-31041.94"),
            "U$S20,00": Decimal("20.00"), "U$S-77,89": Decimal("-77.89"),
            "-$ 67.000,00": Decimal("-67000.00"), "-U$S 77,89": Decimal("-77.89"),
            "$ 2.939.505,16": Decimal("2939505.16"), "-82212.63": Decimal("-82212.63"),
            "2110004.00": Decimal("2110004.00"), 79999.93: Decimal("79999.93"),
        }
        for raw, expected in cases.items():
            self.assertEqual(parse_amount(raw), expected, raw)
        for empty in ("", None, "-", "   "):
            self.assertIsNone(parse_amount(empty))

    def test_parse_amount_rejects_text_with_digits(self):
        # A file name or reference in an amount column must not become a number.
        for raw in ("2026-02-19_00720009007001455304.pdf", "Regimen 0,70%", "12/10/2026", "abc123"):
            self.assertIsNone(parse_amount(raw), raw)

    def test_parse_date(self):
        self.assertEqual(parse_date("24/08/26"), date(2026, 8, 24))
        self.assertEqual(parse_date("17/04/2026"), date(2026, 4, 17))
        self.assertEqual(parse_date("07/10/2026 10:48"), date(2026, 10, 7))

    def test_norm_ref_and_cuota(self):
        self.assertEqual(norm_ref("000011839"), "11839")
        self.assertEqual(norm_ref("367178K"), "367178")
        self.assertEqual(norm_ref("024309*"), "24309")
        self.assertEqual(norm_ref("-"), "")
        self.assertEqual(norm_ref("000000000"), "")
        self.assertEqual(norm_cuota("07 de 09"), "7/9")
        self.assertEqual(norm_cuota("14 de 18"), "14/18")
        self.assertEqual(norm_cuota(""), "")
