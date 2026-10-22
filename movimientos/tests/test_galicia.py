"""Banco Galicia account parsers (PDF text core and xlsx) and their cross-format dedup."""
import tempfile
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from movimientos import parsers
from movimientos.models import Detalle
from movimientos.parsers import account_pdf, account_xlsx, card_xlsx, galicia_pdf, galicia_xlsx
from movimientos.services import importer
from movimientos.services.importer import import_file
from movimientos.tests import factories

D = Decimal
LINES = factories.GALICIA_PDF_LINES


def _replace(lines, old, new):
    return [new if line == old else line for line in lines]


class GaliciaPdfLinesTests(SimpleTestCase):
    def test_movements_statement_and_no_errors(self):
        res = galicia_pdf.parse_lines(LINES)
        self.assertEqual(res.formato, "galicia_pdf")
        self.assertEqual(res.errores, [])
        self.assertEqual([(m.fecha, m.monto) for m in res.movimientos], [
            (date(2026, 8, 31), D("-6000.00")), (date(2026, 8, 31), D("500000.50")),
            (date(2026, 9, 11), D("-50000.00")), (date(2026, 9, 11), D("-50000.00")),
            (date(2026, 9, 16), D("1000.00"))])
        m = res.movimientos[0]
        self.assertEqual((m.fuente, m.moneda, m.cuenta), ("galicia", "ARS", "Galicia CA"))
        (st,) = res.resumenes
        self.assertEqual((st.fuente, st.desde, st.hasta, st.vencimiento),
                         ("galicia", date(2026, 8, 28), date(2026, 9, 25), None))
        self.assertEqual(st.totales, {"ARS": D("1395000.50")})

    def test_detail_lines_joined_and_footers_skipped(self):
        descs = [m.descripcion for m in galicia_pdf.parse_lines(LINES).movimientos]
        self.assertEqual(descs, [
            "TRANSFERENCIA A TERCEROS Persona Ejemplo 20000000001 VARIOS",
            # The detail continues after the page break; page footer/header lines are dropped.
            "ACREDITAMIENTO DE HABERES EMPRESA EJEMPLO ACRED.HABERES",
            "TRANSF. CTAS PROPIAS JUAN PEREZ",
            "TRANSF. CTAS PROPIAS JUAN PEREZ",
            "INTERES CAPITALIZADO Septiembre 2026",
        ])

    def test_codigo_is_balance_after_movement(self):
        codes = [m.codigo for m in galicia_pdf.parse_lines(LINES).movimientos]
        self.assertEqual(codes, ["994000.00", "1494000.50", "1444000.50", "1394000.50", "1395000.50"])

    def test_running_balance_mismatch_is_reported_not_fatal(self):
        lines = _replace(LINES, "11/09/26 TRANSF. CTAS PROPIAS -50.000,00 1.444.000,50",
                         "11/09/26 TRANSF. CTAS PROPIAS -50.000,00 1.444.000,40")
        res = galicia_pdf.parse_lines(lines)
        self.assertEqual(len(res.movimientos), 5)
        self.assertTrue(any("saldo" in e.lower() and "11/09/26" in e for e in res.errores), res.errores)

    def test_total_line_mismatch_is_reported(self):
        lines = _replace(LINES, "Total $ 501.000,50 -$ 106.000,00 $ 1.395.000,50",
                         "Total $ 501.000,50 -$ 106.100,00 $ 1.395.000,50")
        res = galicia_pdf.parse_lines(lines)
        self.assertTrue(any("débitos" in e for e in res.errores), res.errores)

    def test_missing_header_is_an_error(self):
        res = galicia_pdf.parse_lines([line for line in LINES if not line.startswith("CBU ")])
        self.assertEqual(res.resumenes, [])
        self.assertTrue(res.errores)

    def test_looks_like(self):
        self.assertTrue(galicia_pdf.looks_like("\n".join(LINES[:12]), "\n".join(LINES[-5:])))
        self.assertFalse(galicia_pdf.looks_like("Mi resumen de cuenta\nSantander", "Santander"))
        # A Galicia statement without the account block / movements table (e.g. a card) is not this format.
        self.assertFalse(galicia_pdf.looks_like("Resumen de Cuenta Visa\nBanco de Galicia", "Banco de Galicia"))

    def test_orphan_detail_text_is_reported(self):
        i = LINES.index("31/08/26 TRANSFERENCIA A TERCEROS -6.000,00 994.000,00")
        res = galicia_pdf.parse_lines(LINES[:i] + ["TEXTO HUERFANO"] + LINES[i:])
        self.assertEqual(len(res.movimientos), 5)
        self.assertTrue(any("TEXTO HUERFANO" in e for e in res.errores), res.errores)

    def test_movement_outside_table_is_reported(self):
        i = LINES.index("Resumen de Cuenta Sueldo Página 1 / 2")
        res = galicia_pdf.parse_lines(LINES[:i + 1] + ["05/09/26 PERDIDO -1,00 1.494.000,49"] + LINES[i + 1:])
        self.assertEqual(len(res.movimientos), 5)
        self.assertTrue(any("05/09/26 PERDIDO" in e for e in res.errores), res.errores)


class GaliciaXlsxTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = factories.make_galicia_xlsx(self.tmp.name)

    def test_parse(self):
        res = galicia_xlsx.parse(self.path)
        self.assertEqual(res.formato, "galicia_xlsx")
        self.assertEqual(res.errores, [])
        # Oldest first, as the bank applied them.
        self.assertEqual([(m.fecha, m.monto, m.codigo) for m in res.movimientos], [
            (date(2026, 8, 31), D("-6000.00"), "994000.00"),
            (date(2026, 8, 31), D("500000.50"), "1494000.50"),
            (date(2026, 9, 11), D("-50000.00"), "1444000.50"),
            (date(2026, 9, 11), D("-50000.00"), "1394000.50"),
            (date(2026, 9, 16), D("1000.00"), "1395000.50")])
        m = res.movimientos[2]
        self.assertEqual((m.fuente, m.moneda, m.cuenta, m.descripcion),
                         ("galicia", "ARS", "Galicia CA", "TRANSF. CTAS PROPIAS JUAN PEREZ"))
        (st,) = res.resumenes
        self.assertEqual((st.fuente, st.desde, st.hasta, st.vencimiento),
                         ("galicia", date(2026, 8, 22), date(2026, 10, 23), None))
        self.assertEqual(st.totales, {"ARS": D("1395000.50")})

    def test_running_balance_mismatch_is_reported(self):
        rows = [list(r) for r in factories.GALICIA_XLSX_ROWS]
        rows[1][4] = "1394000,40"
        res = galicia_xlsx.parse(factories.make_galicia_xlsx(self.tmp.name, rows=rows))
        self.assertEqual(len(res.movimientos), 5)
        self.assertTrue(any("saldo" in e.lower() for e in res.errores), res.errores)

    def test_row_without_date_is_reported_blank_row_skipped(self):
        rows = [list(r) for r in factories.GALICIA_XLSX_ROWS]
        rows.insert(1, [None] * 6)
        rows.insert(1, [None, " PERDIDO\n", "-1,00", "0,00", "1395000,50", None])
        res = galicia_xlsx.parse(factories.make_galicia_xlsx(self.tmp.name, rows=rows))
        self.assertEqual(len(res.movimientos), 5)
        self.assertEqual([e for e in res.errores if e.startswith("Fila sin fecha")],
                         ["Fila sin fecha: 'PERDIDO' -1,00 0,00 1395000,50"])

    def test_dollars_and_unknown_currency(self):
        res = galicia_xlsx.parse(factories.make_galicia_xlsx(self.tmp.name, moneda="Dólares"))
        self.assertEqual({m.moneda for m in res.movimientos}, {"USD"})
        res = galicia_xlsx.parse(factories.make_galicia_xlsx(self.tmp.name, moneda="Euros"))
        self.assertEqual((res.movimientos, res.resumenes), ([], []))
        self.assertTrue(res.errores)

    def test_detect(self):
        self.assertIs(parsers.detect(self.path), galicia_xlsx)
        self.assertFalse(account_xlsx.matches(self.path))
        self.assertFalse(card_xlsx.matches(self.path))
        self.assertFalse(galicia_xlsx.matches(factories.make_account_xlsx(self.tmp.name)))
        self.assertFalse(account_pdf.matches(self.path))


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class GaliciaDedupTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.xlsx = factories.make_galicia_xlsx(self.tmp.name)

    def _import_pdf(self):
        with mock.patch.object(importer.parsers, "parse", return_value=galicia_pdf.parse_lines(LINES)):
            return import_file("resumen.pdf")

    def test_pdf_then_xlsx_adds_nothing(self):
        first = self._import_pdf()
        self.assertEqual((first.nuevos, first.duplicados), (5, 0))
        second = import_file(str(self.xlsx))
        self.assertEqual((second.nuevos, second.duplicados), (0, 5))

    def test_xlsx_then_pdf_adds_nothing(self):
        self.assertEqual(import_file(str(self.xlsx)).nuevos, 5)
        r = self._import_pdf()
        self.assertEqual((r.nuevos, r.duplicados), (0, 5))

    def test_same_day_identical_amounts_both_kept(self):
        self._import_pdf()
        rows = Detalle.objects.filter(fuente="galicia", monto=D("-50000.00"))
        self.assertEqual(sorted(rows.values_list("ref", flat=True)), ["139400050", "144400050"])
        self.assertEqual(set(Detalle.objects.values_list("tipo", flat=True)), {"gasto", "ingreso"})


class GaliciaRuleFormTests(SimpleTestCase):
    def test_rule_can_target_galicia(self):
        from movimientos.forms import ReglaForm
        self.assertIn(("galicia", "Galicia"), ReglaForm.FUENTES)
        self.assertEqual(ReglaForm.base_fields["fuente"].clean("galicia"), "galicia")
