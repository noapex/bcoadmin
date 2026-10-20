from datetime import datetime
from decimal import Decimal

from django.test import TestCase

from movimientos.models import Categoria, Detalle
from movimientos.services.stats import (
    category_evolution, month_range, monthly_totals, spending_by_category, ultimo_mes_con_datos)


def mov(y, m, d, monto, tipo="gasto", moneda="ARS", categoria=None, activo=True):
    return Detalle.objects.create(
        codigo="", fecha=datetime(y, m, d), cuenta="c", descripcion="x", monto=Decimal(monto),
        tipo=tipo, moneda=moneda, categoria=categoria, activo=activo)


class MonthlyTotalsTests(TestCase):
    def test_month_range_crosses_years(self):
        self.assertEqual(month_range("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_totals_sign_interno_refund_and_currency(self):
        mov(2026, 3, 1, "1000", tipo="ingreso")
        mov(2026, 3, 2, "-300")
        mov(2026, 3, 3, "-200")
        mov(2026, 3, 4, "50")  # refund nets out
        mov(2026, 3, 5, "-500", tipo="interno")
        mov(2026, 3, 6, "-99", activo=False)
        mov(2026, 3, 7, "-70", moneda="USD")
        [row] = monthly_totals("ARS", "2026-03", "2026-03")
        self.assertEqual(row, {"mes": "2026-03", "ingresos": Decimal("1000.00"),
                               "gastos": Decimal("450.00"), "balance": Decimal("550.00")})
        [usd] = monthly_totals("USD", "2026-03", "2026-03")
        self.assertEqual((usd["ingresos"], usd["gastos"]), (Decimal("0.00"), Decimal("70.00")))

    def test_empty_middle_month_and_years_not_merged(self):
        mov(2025, 9, 10, "-10")
        mov(2026, 9, 10, "-20")
        rows = monthly_totals("ARS", "2025-09", "2026-09")
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows[0]["gastos"], Decimal("10.00"))
        self.assertEqual(rows[-1]["gastos"], Decimal("20.00"))
        self.assertEqual(rows[5], {"mes": "2026-02", "ingresos": Decimal("0.00"),
                                   "gastos": Decimal("0.00"), "balance": Decimal("0.00")})

    def test_range_bounds_inclusive_of_last_day(self):
        mov(2026, 3, 31, "-5")
        mov(2026, 4, 1, "-7")
        [row] = monthly_totals("ARS", "2026-03", "2026-03")
        self.assertEqual(row["gastos"], Decimal("5.00"))


class CategoryTests(TestCase):
    def setUp(self):
        self.comida = Categoria.objects.create(name="Comida")
        self.super = Categoria.objects.create(name="Super", parent=self.comida)
        self.auto = Categoria.objects.create(name="Auto")

    def test_rolls_up_to_root_and_sin_categoria(self):
        mov(2026, 3, 1, "-100", categoria=self.comida)
        mov(2026, 3, 2, "-50", categoria=self.super)
        mov(2026, 3, 3, "-30", categoria=self.auto)
        mov(2026, 3, 4, "-5")
        mov(2026, 3, 5, "-999", tipo="interno", categoria=self.auto)
        mov(2026, 4, 1, "-999", categoria=self.auto)
        self.assertEqual(spending_by_category("ARS", "2026-03"), [
            ("Comida", self.comida.id, Decimal("150.00")),
            ("Auto", self.auto.id, Decimal("30.00")),
            ("Sin categoría", None, Decimal("5.00")),
        ])

    def test_evolution_top_n_and_otros(self):
        mov(2026, 3, 1, "-100", categoria=self.comida)
        mov(2026, 4, 1, "-40", categoria=self.super)
        mov(2026, 3, 2, "-30", categoria=self.auto)
        mov(2026, 4, 2, "-5")
        res = category_evolution("ARS", "2026-03", "2026-04", top_n=1)
        self.assertEqual(res["meses"], ["2026-03", "2026-04"])
        self.assertEqual(res["series"], [
            {"nombre": "Comida", "id": self.comida.id, "valores": [Decimal("100.00"), Decimal("40.00")]},
            {"nombre": "Otros", "id": None, "valores": [Decimal("30.00"), Decimal("5.00")]},
        ])

    def test_evolution_no_otros_when_all_fit(self):
        mov(2026, 3, 1, "-100", categoria=self.comida)
        res = category_evolution("ARS", "2026-03", "2026-03")
        self.assertEqual([s["nombre"] for s in res["series"]], ["Comida"])


class UltimoMesTests(TestCase):
    def test_ultimo_mes(self):
        self.assertIsNone(ultimo_mes_con_datos("ARS"))
        mov(2026, 2, 3, "-1")
        mov(2026, 5, 3, "-1", tipo="interno")
        mov(2026, 6, 3, "-1", moneda="USD")
        self.assertEqual(ultimo_mes_con_datos("ARS"), "2026-02")
