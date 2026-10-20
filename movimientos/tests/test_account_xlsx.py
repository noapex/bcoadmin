import tempfile
from datetime import date
from decimal import Decimal
from unittest import TestCase

from movimientos.parsers import account_xlsx, card_xlsx
from movimientos.tests import factories


class AccountXlsxTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = factories.make_account_xlsx(self.tmp.name)

    def test_account_xlsx(self):
        res = account_xlsx.parse(self.path)
        self.assertEqual(res.formato, "account_xlsx")
        self.assertEqual(res.resumenes[0].hasta, date(2026, 10, 19))
        self.assertEqual(res.resumenes[0].desde, date(2026, 8, 20))
        m = res.movimientos[0]
        self.assertEqual((m.fuente, m.moneda, m.descripcion, m.monto, m.codigo),
                         ("cuenta", "ARS", "Pago de servicios Edesur: 0001928991", Decimal("-82212.63"), "45645645"))
        self.assertEqual(m.cuenta, "000 - CASA CENTRAL")
        self.assertEqual(m.fecha, date(2026, 10, 19))
        cc = [m for m in res.movimientos if m.monto == Decimal("-8.92")]
        self.assertEqual(len(cc), 1)
        self.assertEqual(cc[0].descripcion, "Impuesto ley 25.413 debito 0,6%")
        self.assertEqual(len([m for m in res.movimientos if m.monto == Decimal("-15337.00")]), 3)
        self.assertEqual(len(res.movimientos), 6)

    def test_matches_and_dollars(self):
        self.assertTrue(account_xlsx.matches(self.path))
        self.assertFalse(card_xlsx.matches(self.path))
        path = factories.make_account_xlsx(self.tmp.name, moneda="Dólares")
        self.assertEqual({m.moneda for m in account_xlsx.parse(path).movimientos}, {"USD"})
