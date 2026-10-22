from datetime import datetime
from decimal import Decimal

from django.test import TestCase

from movimientos.models import Categoria, Detalle, Tarjeta
from movimientos.services.classify import CAT_SIN_DETALLE
from movimientos.services.stats import (
    category_evolution, gastos_por_categoria, gastos_por_tarjeta, ingresos_por_categoria, month_range,
    monthly_totals,
    spending_by_category, ultimo_mes_con_datos)


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


def D(*values):
    return [Decimal(v) for v in values]


class GastosPorCategoriaTests(TestCase):
    def setUp(self):
        self.comida = Categoria.objects.create(name="Comida")
        self.super = Categoria.objects.create(name="Super", parent=self.comida)
        self.mayorista = Categoria.objects.create(name="Mayorista", parent=self.super)
        self.auto = Categoria.objects.create(name="Auto")

    def _filas(self, res):
        return {f["nombre"]: f for f in res["filas"]}

    def test_rolls_descendants_up_and_sin_categoria(self):
        mov(2026, 3, 1, "-100", categoria=self.comida)
        mov(2026, 3, 2, "-50", categoria=self.super)
        mov(2026, 3, 3, "10", categoria=self.super)  # refund nets out
        mov(2026, 3, 4, "-20", categoria=self.mayorista)
        mov(2026, 4, 1, "-30", categoria=self.auto)
        mov(2026, 3, 5, "-5")
        mov(2026, 3, 6, "-999", tipo="interno", categoria=self.auto)
        mov(2026, 3, 7, "-999", categoria=self.auto, activo=False)
        mov(2026, 3, 8, "500", tipo="ingreso", categoria=self.comida)
        res = gastos_por_categoria("ARS", "2026-03", "2026-04")
        self.assertEqual(res["meses"], ["2026-03", "2026-04"])
        self.assertEqual([(f["nombre"], f["nivel"]) for f in res["filas"]], [
            ("Auto", 0), ("Comida", 0), ("Super", 1), ("Mayorista", 2), ("Sin categoría", 0)])
        f = self._filas(res)
        self.assertEqual(f["Comida"]["valores"], D("160", "0"))
        self.assertEqual(f["Comida"]["total"], Decimal("160"))
        self.assertEqual(f["Super"]["valores"], D("60", "0"))
        self.assertEqual(f["Mayorista"]["valores"], D("20", "0"))
        self.assertEqual(f["Auto"]["valores"], D("0", "30"))
        self.assertEqual(f["Sin categoría"]["valores"], D("5", "0"))
        self.assertIsNone(f["Sin categoría"]["id"])
        self.assertEqual(f["Super"]["raiz_id"], self.comida.id)
        self.assertTrue(f["Comida"]["tiene_hijos"])
        self.assertFalse(f["Auto"]["tiene_hijos"])
        self.assertEqual(res["total"], {"valores": D("165", "30"), "total": Decimal("195")})

    def test_currencies_separated(self):
        mov(2026, 3, 1, "-100", categoria=self.comida)
        mov(2026, 3, 2, "-7", moneda="USD", categoria=self.super)
        usd = self._filas(gastos_por_categoria("USD", "2026-03", "2026-03"))
        self.assertEqual(usd["Comida"]["valores"], D("7"))
        ars = self._filas(gastos_por_categoria("ARS", "2026-03", "2026-03"))
        self.assertEqual(ars["Comida"]["valores"], D("100"))
        self.assertEqual(ars["Super"]["valores"], D("0"))

    def test_constant_queries(self):
        for i in range(5):
            mov(2026, 3, 1 + i, "-1", categoria=Categoria.objects.create(name=f"C{i}", parent=self.auto))
        with self.assertNumQueries(2):
            gastos_por_categoria("ARS", "2026-01", "2026-12")

    def test_empty(self):
        res = gastos_por_categoria("ARS", "2026-03", "2026-03")
        self.assertEqual(res["total"], {"valores": D("0"), "total": Decimal("0")})
        self.assertEqual(res["filas"][-1]["nombre"], "Sin categoría")


def card(y, m, d, monto, fuente, cuenta="", moneda="ARS", tipo="gasto", desc="x", categoria=None):
    return Detalle.objects.create(
        codigo="", fecha=datetime(y, m, d), cuenta=cuenta, descripcion=desc, monto=Decimal(monto),
        tipo=tipo, moneda=moneda, fuente=fuente, categoria=categoria)


class GastosPorTarjetaTests(TestCase):
    def setUp(self):
        Tarjeta.objects.create(nombre="Visa Gold", codigo="1", fuente="visa", patron_pago="pago visa")
        Tarjeta.objects.create(nombre="American", codigo="3", fuente="amex", patron_pago="amex")
        self.sin = Categoria.objects.create(name=CAT_SIN_DETALLE)
        card(2026, 3, 1, "-100", "visa", "Visa 5544")
        card(2026, 3, 2, "-10", "visa", "Visa 5544", desc="Impuesto de sellos")
        card(2026, 3, 3, "30", "visa", "Visa 5544")  # refund nets out
        card(2026, 4, 1, "-50", "visa", "Visa 5544")
        card(2026, 3, 4, "-999", "visa", "Visa 5544", tipo="interno", desc="Su pago en pesos")
        card(2026, 4, 2, "-200", "amex", "Amex 0003")
        card(2026, 3, 5, "-7", "master")
        card(2026, 3, 6, "-5", "visa", "Visa 5544", moneda="USD")
        card(2026, 3, 10, "-300", "cuenta", desc="Pago tarjeta de credito amex 13/03", categoria=self.sin)
        card(2026, 4, 10, "-40", "cuenta", desc="PAGO VISA", categoria=self.sin)
        card(2026, 4, 11, "-1", "cuenta", desc="Pago tarjeta desconocida", categoria=self.sin)
        card(2026, 3, 12, "-1000", "cuenta", desc="Alquiler")

    def test_rows_per_card_and_sin_detalle(self):
        res = gastos_por_tarjeta("ARS", "2026-03", "2026-04")
        self.assertEqual(res["meses"], ["2026-03", "2026-04"])
        self.assertEqual([(f["nombre"], f["valores"]) for f in res["filas"]], [
            ("Visa Gold · 5544", D("80", "50")),
            ("Visa Gold · pagos sin detalle", D("0", "40")),
            ("American · 0003", D("0", "200")),
            ("American · pagos sin detalle", D("300", "0")),
            ("Master", D("7", "0")),
            ("Otras tarjetas · pagos sin detalle", D("0", "1")),
        ])
        sd = res["filas"][1]
        self.assertEqual((sd["fuente"], sd["sin_detalle"], sd["categoria_id"]), ("visa", True, self.sin.id))
        self.assertEqual((res["filas"][0]["fuente"], res["filas"][0]["sin_detalle"]), ("visa", False))
        self.assertEqual(res["filas"][0]["total"], Decimal("130"))
        self.assertEqual(res["total"], {"valores": D("387", "291"), "total": Decimal("678")})

    def test_unknown_card_source_folds_into_otras(self):
        Tarjeta.objects.create(nombre="Naranja", codigo="9", fuente="naranja", patron_pago="naranja")
        card(2026, 4, 12, "-9", "cuenta", desc="Pago naranja", categoria=self.sin)
        res = gastos_por_tarjeta("ARS", "2026-03", "2026-04")
        self.assertEqual(res["filas"][-1]["nombre"], "Otras tarjetas · pagos sin detalle")
        self.assertEqual(res["filas"][-1]["valores"], D("0", "10"))
        self.assertEqual(res["total"]["total"], Decimal("687"))

    def test_usd_isolated(self):
        res = gastos_por_tarjeta("USD", "2026-03", "2026-04")
        self.assertEqual([(f["nombre"], f["valores"]) for f in res["filas"]], [("Visa Gold · 5544", D("5", "0"))])

    def test_constant_queries(self):
        for i in range(5):
            card(2026, 3, 1 + i, "-1", "master", f"Master 00{i}0")
            card(2026, 3, 1 + i, "-1", "cuenta", desc=f"pago visa {i}", categoria=self.sin)
        with self.assertNumQueries(4):
            gastos_por_tarjeta("ARS", "2026-01", "2026-12")

    def test_empty(self):
        Detalle.objects.all().delete()
        res = gastos_por_tarjeta("ARS", "2026-03", "2026-03")
        self.assertEqual(res["filas"], [])
        self.assertEqual(res["total"], {"valores": D("0"), "total": Decimal("0")})


class IngresosPorCategoriaTests(TestCase):
    def test_positive_rollup_only_ingresos(self):
        root = Categoria.objects.create(name="Cinconueves")
        ate = Categoria.objects.create(name="ATE", parent=root)
        mov(2026, 9, 14, "2110004", tipo="ingreso", categoria=ate)
        mov(2026, 9, 15, "-500", categoria=ate)            # gasto: not an income
        mov(2026, 9, 16, "100", tipo="ingreso")            # no category
        mov(2026, 9, 17, "999", tipo="interno", categoria=ate)
        res = ingresos_por_categoria("ARS", "2026-09", "2026-09")
        filas = {f["nombre"]: f for f in res["filas"]}
        self.assertEqual(filas["ATE"]["total"], Decimal("2110004.00"))
        self.assertEqual(filas["Cinconueves"]["total"], Decimal("2110004.00"))
        self.assertEqual(filas["Sin categoría"]["total"], Decimal("100.00"))
        self.assertEqual(res["total"]["total"], Decimal("2110104.00"))
