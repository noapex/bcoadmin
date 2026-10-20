from datetime import date, datetime, time
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from movimientos.models import Categoria, Detalle, Regla, Resumen, Tarjeta
from movimientos.services.classify import (
    CAT_INTERNOS, CAT_PAGO_TARJETA, CAT_SIN_DETALLE, clasificar_todo, payee_key, resolver_pagos_tarjeta)

SEED_SETTINGS = dict(
    CATEGORIAS_GASTOS={
        "Servicios": {"Luz": ["edesur"], "Streaming": {"Video": ["videoflix"]}},
        "Sueldo": ["sueldo ejemplo"],
        "Tarjetas": {"Visa": ["x"]},
    },
    TARJETAS={"Visa": ["1111", "2222"], "American": ["3333"]},
    IGNORAR=["Licitacion Ejemplo"],
)


def mk(descripcion, monto, fuente="cuenta", fecha=date(2026, 10, 13), **kw):
    dt = datetime.combine(fecha, time.min)
    if timezone.is_naive(dt) and timezone.now().tzinfo:
        dt = timezone.make_aware(dt)
    return Detalle.objects.create(
        codigo="1", fecha=dt, cuenta="c", descripcion=descripcion, monto=Decimal(str(monto)),
        fuente=fuente, **kw)


class PayeeKeyTests(TestCase):
    def test_payee_key_groups_and_separates(self):
        same = [
            ("Debito automatico Federacion patro-0600123436000000111026", "Debito automatico Federacion patro-0600123436000000110926"),
            ("Pago de servicios Edesur: 0001928991", "PAGO DE SERVICIOS EDESUR: 0001928991"),
            ("Dlo*duvet home", "Dlo/duvet home"),
            ("Anthropic* claud in1u", "Anthropic/ claud in1u6bzsbusd"),
            ("Compra con tarjeta de debito Merpago*mercadolibre - tarj nro. 0698", "Compra con tarjeta de debito Merpago*mercadolibre - tarj nro. 4659"),
            ("Debito debin Id debin r7zg0qndk7m003yv9exypo cuit 30703 088534", "Debito debin Id debin jmqkyz9qgeqwy5v8nv50p3 cuit 30703 088534"),
        ]
        for a, b in same:
            self.assertEqual(payee_key(a), payee_key(b), (a, b))
        self.assertNotEqual(payee_key("Debito automatico Federacion patro-0600123436000000111026"),
                            payee_key("Debito automatico Federacion patro-0600123439000000111026"))

    def test_strips_accents_dates_amounts_usd(self):
        self.assertEqual(payee_key("Compra Cafetería 13/10/2026 $1.234,56 USD"), "compra cafeteria")
        self.assertEqual(payee_key("Impuesto 0,6% sobre $ 100,00"), "impuesto")


class ClassifyTests(TestCase):
    def test_default_kinds(self):
        a = mk("algo", 100)
        b = mk("algo", -100)
        c = mk("algo", 31041.94, fuente="visa")
        clasificar_todo()
        for r in (a, b, c):
            r.refresh_from_db()
        self.assertEqual((a.tipo, b.tipo, c.tipo), ("ingreso", "gasto", "gasto"))
        self.assertEqual(a.comercio, "algo")

    def test_card_refund_is_negative_gasto(self):
        r = mk("Dlo*duvet home", 31041.94, fuente="visa", tipo="ingreso")
        clasificar_todo()
        r.refresh_from_db()
        self.assertEqual(r.tipo, "gasto")

    def test_rule_priority(self):
        cat_a = Categoria.objects.create(name="A")
        cat_b = Categoria.objects.create(name="B")
        Regla.objects.create(patron="netflix", prioridad=100, categoria=cat_a)
        Regla.objects.create(patron="net", prioridad=5, categoria=cat_b, tipo="interno")
        r = mk("NETFLIX mensual", -10)
        clasificar_todo()
        r.refresh_from_db()
        self.assertEqual(r.categoria, cat_b)
        self.assertEqual(r.tipo, "interno")

    def test_regex_fuente_moneda_filters(self):
        cat = Categoria.objects.create(name="C")
        Regla.objects.create(patron=r"^pago \d+$", es_regex=True, fuente="tarjeta", moneda="USD", categoria=cat)
        ok = mk("pago 12", -1, fuente="visa", moneda="USD")
        wrong_cur = mk("pago 12", -1, fuente="visa", moneda="ARS")
        wrong_src = mk("pago 12", -1, fuente="cuenta", moneda="USD")
        clasificar_todo()
        for r in (ok, wrong_cur, wrong_src):
            r.refresh_from_db()
        self.assertEqual((ok.categoria, wrong_cur.categoria, wrong_src.categoria), (cat, None, None))

    def test_manual_category_survives(self):
        cat = Categoria.objects.create(name="M")
        Regla.objects.create(patron="algo", categoria=Categoria.objects.create(name="R"), tipo="interno")
        r = mk("algo", -5, categoria=cat, tipo="gasto", categoria_manual=True)
        clasificar_todo()
        r.refresh_from_db()
        self.assertEqual((r.categoria, r.tipo), (cat, "gasto"))
        self.assertEqual(r.comercio, "algo")

    def test_returns_changed_count_and_is_stable(self):
        mk("algo", 100)
        mk("otro", -100)
        self.assertGreater(clasificar_todo(), 0)
        self.assertEqual(clasificar_todo(), 0)

    def test_card_payment_is_interno_only_when_card_detail_exists(self):
        Tarjeta.objects.create(nombre="American", codigo="0003", fuente="amex", patron_pago="amex")
        r = mk("Pago tarjeta de credito amex Deb. automatico 13/10/2026 part 00012312312",
               -165101.84, fecha=date(2026, 10, 13))
        clasificar_todo()
        r.refresh_from_db()
        self.assertEqual((r.tipo, r.categoria.name), ("gasto", CAT_SIN_DETALLE))
        Resumen.objects.create(fuente="amex", hasta=date(2026, 10, 1), vencimiento=date(2026, 10, 13))
        clasificar_todo()
        r.refresh_from_db()
        self.assertEqual((r.tipo, r.categoria.name), ("interno", CAT_PAGO_TARJETA))

    def test_standalone_resolver(self):
        Tarjeta.objects.create(nombre="V", codigo="1", fuente="visa", patron_pago="pago visa")
        r = mk("pago visa", -10)
        self.assertGreater(resolver_pagos_tarjeta(), 0)
        r.refresh_from_db()
        self.assertEqual((r.tipo, r.categoria.name), ("gasto", CAT_SIN_DETALLE))

    def test_card_payment_outside_window_stays_gasto(self):
        Tarjeta.objects.create(nombre="V", codigo="1", fuente="visa", patron_pago="pago visa")
        Resumen.objects.create(fuente="visa", hasta=date(2026, 9, 1), vencimiento=date(2026, 10, 1))
        far = mk("pago visa", -10, fecha=date(2026, 10, 12))
        near = mk("pago visa", -10, fecha=date(2026, 10, 11))
        clasificar_todo()
        far.refresh_from_db()
        near.refresh_from_db()
        self.assertEqual((near.tipo, far.tipo), ("interno", "gasto"))

    def test_card_payment_resolver_beats_rules_but_not_manual(self):
        Tarjeta.objects.create(nombre="V", codigo="1", fuente="visa", patron_pago="pago visa")
        Regla.objects.create(patron="pago visa", categoria=Categoria.objects.create(name="X"), tipo="ingreso")
        manual = mk("pago visa", -10, categoria_manual=True, tipo="ingreso")
        auto = mk("pago visa", -10)
        clasificar_todo()
        manual.refresh_from_db()
        auto.refresh_from_db()
        self.assertEqual(manual.tipo, "ingreso")
        self.assertEqual(auto.categoria.name, CAT_SIN_DETALLE)


class SeedTests(TestCase):
    @override_settings(**SEED_SETTINGS)
    def test_seed_is_idempotent(self):
        call_command("seed_reglas", stdout=StringIO())
        counts = (Categoria.objects.count(), Regla.objects.count(), Tarjeta.objects.count())
        call_command("seed_reglas", stdout=StringIO())
        self.assertEqual(counts, (Categoria.objects.count(), Regla.objects.count(), Tarjeta.objects.count()))
        self.assertEqual(Tarjeta.objects.count(), 2)
        self.assertFalse(Categoria.objects.filter(name="Tarjetas").exists())
        self.assertEqual(Categoria.objects.get(name="Video").parent.name, "Streaming")
        self.assertTrue(Regla.objects.filter(patron="edesur", categoria__name="Luz", tipo="").exists())
        visa = Tarjeta.objects.get(fuente="visa")
        self.assertIn("1111", visa.patron_pago)
        self.assertIn("pago de tarjeta de credito$", visa.patron_pago)
        self.assertIn("amex", Tarjeta.objects.get(fuente="amex").patron_pago)

    @override_settings(**SEED_SETTINGS)
    def test_seed_keeps_admin_edits_to_tarjeta(self):
        call_command("seed_reglas", stdout=StringIO())
        Tarjeta.objects.filter(fuente="visa").update(patron_pago="mio", nombre="Editada")
        call_command("seed_reglas", stdout=StringIO())
        t = Tarjeta.objects.get(fuente="visa")
        self.assertEqual((t.patron_pago, t.nombre), ("mio", "Editada"))

    @override_settings(CATEGORIAS_GASTOS={}, IGNORAR=[], TARJETAS={"Visa": "1234", "Visa Gold": ["5678"]})
    def test_seed_string_codes_and_merged_fuente(self):
        call_command("seed_reglas", stdout=StringIO())
        t = Tarjeta.objects.get(fuente="visa")
        self.assertEqual(Tarjeta.objects.count(), 1)
        self.assertTrue(t.patron_pago.startswith("1234|5678|"))

    @override_settings(**SEED_SETTINGS)
    def test_seed_reuses_existing_category(self):
        existing = Categoria.objects.create(name="Luz")
        call_command("seed_reglas", stdout=StringIO())
        self.assertEqual(Categoria.objects.filter(name="Luz").count(), 1)
        self.assertEqual(Regla.objects.get(patron="edesur").categoria, existing)

    @override_settings(**SEED_SETTINGS, INTERNOS=["De fulano ejemplo"])
    def test_dollar_purchase_and_internos_are_interno(self):
        call_command("seed_reglas", stdout=StringIO())
        a = mk("Debito por compra de dolares Compraste u$s 1.563,52 a $ 1.534,99", -2400000)
        b = mk("Licitacion Ejemplo 123", -5)
        c = mk("Transferencia recibida De fulano ejemplo", 50)
        d = mk("Su pago en pesos", 10000, fuente="visa")
        e = mk("Impuesto de sellos", -3)
        clasificar_todo()
        for r in (a, b, c, d, e):
            r.refresh_from_db()
        self.assertEqual([r.tipo for r in (a, b, c, d)], ["interno"] * 4)
        self.assertEqual(a.categoria.name, CAT_INTERNOS)
        self.assertEqual(d.categoria.name, CAT_PAGO_TARJETA)
        self.assertEqual((e.tipo, e.categoria.name), ("gasto", "Impuestos"))

    @override_settings(**SEED_SETTINGS)
    def test_card_payment_rows_resolved_after_seed(self):
        call_command("seed_reglas", stdout=StringIO())
        r = mk("Pago de tarjeta de credito", -500, fecha=date(2026, 10, 13))
        clasificar_todo()
        r.refresh_from_db()
        self.assertEqual((r.tipo, r.categoria.name), ("gasto", CAT_SIN_DETALLE))
