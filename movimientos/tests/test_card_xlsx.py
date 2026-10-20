import tempfile
from datetime import date
from decimal import Decimal
from unittest import TestCase

from movimientos.parsers import account_xlsx, card_xlsx
from movimientos.tests import factories


class CardXlsxTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = factories.make_card_xlsx(self.tmp.name)

    def test_card_xlsx_parses_sections_and_signs(self):
        res = card_xlsx.parse(self.path)
        self.assertEqual(res.formato, "card_xlsx")
        st = res.resumenes[0]
        self.assertEqual((st.fuente, st.hasta, st.vencimiento), ("visa", date(2026, 9, 24), date(2026, 10, 5)))
        inst = [m for m in res.movimientos if m.cuota]
        self.assertEqual((inst[0].cuota, inst[0].fecha, inst[0].fecha_compra, inst[0].monto),
                         ("14/18", date(2026, 9, 24), date(2025, 7, 24), Decimal("-31041.94")))
        usd = [m for m in res.movimientos if m.moneda == "USD" and m.seccion == "consumo"]
        self.assertEqual(usd[1].fecha, usd[0].fecha)  # carried-forward date
        refund = [m for m in res.movimientos if m.monto == Decimal("31041.94")]
        self.assertEqual(refund[0].seccion, "consumo")
        pago = [m for m in res.movimientos if m.seccion == "pago"]
        self.assertTrue(pago and all(m.monto > 0 for m in pago))
        imp = [m for m in res.movimientos if m.seccion == "impuesto"]
        self.assertEqual(imp[0].fecha, date(2026, 9, 24))
        consumos_ars = -sum(m.monto for m in res.movimientos if m.seccion == "consumo" and m.moneda == "ARS")
        self.assertEqual(consumos_ars, st.totales["ARS"])
        self.assertTrue(card_xlsx.matches(self.path))
        self.assertFalse(account_xlsx.matches(self.path))

    def test_card_metadata_and_fields(self):
        res = card_xlsx.parse(self.path)
        m = next(m for m in res.movimientos if m.cuota)
        self.assertEqual((m.fuente, m.moneda, m.codigo, m.titular, m.cuenta, m.descripcion),
                         ("visa", "ARS", "111111*", "Juan Perez", "Visa 5544", "Tienda ejemplo uno"))
        self.assertEqual(res.resumenes[0].totales["USD"], Decimal("23.71"))
        self.assertEqual(len(res.movimientos), 7)

    def test_amex_source(self):
        path = factories.make_card_xlsx(self.tmp.name, card="American Express Crédito", last4="4623")
        res = card_xlsx.parse(path)
        self.assertEqual(res.resumenes[0].fuente, "amex")
        self.assertEqual(res.movimientos[0].cuenta, "Amex 4623")
