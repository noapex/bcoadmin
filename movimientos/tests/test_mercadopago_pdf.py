from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import TestCase, mock

from django.core.management import call_command
from django.test import TestCase as DjangoTestCase, override_settings

from movimientos.models import Detalle, Tarjeta
from movimientos.parsers import mercadopago_pdf
from movimientos.parsers.base import ParsedMovement, ParseResult
from movimientos.parsers.mercadopago_pdf import looks_like, parse_lines
from movimientos.services import importer
from movimientos.services.classify import CAT_PAGO_TARJETA, clasificar_todo

D = Decimal
TODAY = date(2026, 10, 29)
HEADER = ["Este es tu resumen de octubre Tarjeta de crédito", "Total a pagar Fecha de cierre",
          "Fecha de vencimiento", "Total a pagar $ 244.465,14 US$ 0,00"]
DETAIL = [
    "DETALLE DE MOVIMIENTOS",
    "Composición del saldo del periodo anterior",
    "Fecha Descripción Pesos Dólares",
    "5/sep Total a pagar del periodo anterior $ 244.465,12",
    "11/sep Débito automático de tarjeta -$ 4.165,43",
    "11/sep Débito automático de tarjeta -$ 2,15",
    "11/sep Débito automático de tarjeta -$ 100.000,00",
    "11/sep Débito automático de tarjeta -$ 100.000,00",
    "11/sep Débito automático de tarjeta -$ 40.297,54",
    "Subtotal $ 0,00 US$ 0,00",
    "Consumos",
    "Con tarjeta virtual",
    "Fecha Descripción Cuota Operación Pesos Dólares",
    "28/ago MERPAGO*ADCOMUNIC 2 de 3 109415 $ 91.666,67",
    "1/sep MERPAGO*EXCLUSIVETECH 2 de 3 877479 $ 149.899,67",
    "Subtotal $ 241.566,34 US$ 0,00",
    "Impuestos e intereses1",
    "Fecha Descripción Pesos Dólares",
    "5/oct Impuesto al sello Buenos Aires $ 2.898,80",
    "¹ Los impuestos por consumos en moneda extranjera incluyen a todos los consumos.",
    "Subtotal $ 2.898,80 US$ 0,00",
    "Pagos anticipados",
    "No realizaste pagos anticipados",
    "DETALLE DE MOVIMIENTOS",
    "Ajustes y reembolsos",
    "No tenés ajustes ni reembolsos",
    "Total a pagar $ 244.465,14 US$ 0,00",
]
DATES = ["Cierre anterior 5 de septiembre Vencimiento anterior 10 de septiembre",
         "Cierre actual 5 de octubre Vencimiento actual 13 de octubre",
         "Cierre próximo 5 de noviembre Vencimiento próximo 10 de noviembre"]
FOOTER = ["Mercado Pago.", "Fue emitido por MercadoLibre S.R.L."]


def sample(detail=DETAIL, dates=DATES):
    return HEADER + detail + dates + FOOTER


class ParseLinesTests(TestCase):
    def test_sample(self):
        r = parse_lines(sample(), "resumen-tarjeta-mercado-pago-2026-10.pdf", TODAY)
        self.assertEqual(r.errores, [])
        self.assertEqual(r.formato, "mercadopago_pdf")
        self.assertEqual(r.omitidas, 1)
        self.assertEqual([m.seccion for m in r.movimientos], ["pago"] * 5 + ["consumo"] * 2 + ["impuesto"])
        self.assertTrue(all((m.fuente, m.moneda, m.cuenta) == ("mpago", "ARS", "Mercado Pago") for m in r.movimientos))
        pagos = r.movimientos[:5]
        self.assertEqual([m.monto for m in pagos], [D("4165.43"), D("2.15"), D("100000.00"), D("100000.00"), D("40297.54")])
        self.assertTrue(all(m.fecha == date(2026, 9, 11) and m.cuota == "" for m in pagos))
        c1, c2, imp = r.movimientos[5:]
        self.assertEqual((c1.fecha, c1.fecha_compra, c1.descripcion, c1.cuota, c1.codigo, c1.monto),
                         (date(2026, 10, 5), date(2026, 8, 28), "MERPAGO*ADCOMUNIC", "2/3", "109415", D("-91666.67")))
        self.assertEqual((c2.fecha_compra, c2.codigo, c2.monto), (date(2026, 9, 1), "877479", D("-149899.67")))
        self.assertEqual((imp.fecha, imp.descripcion, imp.monto), (date(2026, 10, 5), "Impuesto al sello Buenos Aires", D("-2898.80")))
        s, = r.resumenes
        self.assertEqual((s.fuente, s.desde, s.hasta, s.vencimiento), ("mpago", None, date(2026, 10, 5), date(2026, 10, 13)))
        self.assertEqual(s.totales, {"ARS": D("244465.14"), "USD": D("0.00")})

    def test_year_from_today_when_filename_has_none(self):
        r = parse_lines(sample(), "resumen.pdf", date(2027, 3, 1))
        self.assertEqual(r.resumenes[0].hasta, date(2026, 10, 5))
        r = parse_lines(sample(), "resumen.pdf", date(2026, 11, 2))
        self.assertEqual(r.resumenes[0].hasta, date(2026, 10, 5))

    def test_future_closing_uses_previous_year(self):
        r = parse_lines(sample(), "resumen-2026-10.pdf", date(2026, 10, 1))
        self.assertEqual(r.resumenes[0].hasta, date(2025, 10, 5))

    def test_december_to_january_rollover(self):
        detail = ["DETALLE DE MOVIMIENTOS", "Consumos", "Con tarjeta física",
                  "Fecha Descripción Cuota Operación Pesos Dólares",
                  "20/dic TIENDA UNO 123456 $ 1.000,00",
                  "2/ene TIENDA DOS 1 de 6 654321 $ 500,00",
                  "Subtotal $ 1.500,00 US$ 0,00",
                  "Total a pagar $ 1.500,00 US$ 0,00"]
        dates = ["Cierre actual 5 de enero Vencimiento actual 13 de enero"]
        r = parse_lines(sample(detail, dates), "resumen-tarjeta-mercado-pago-2027-01.pdf", date(2027, 1, 20))
        self.assertEqual(r.errores, [])
        a, b = r.movimientos
        self.assertEqual((a.fecha, a.fecha_compra, a.cuota, a.codigo), (date(2026, 12, 20), date(2026, 12, 20), "", "123456"))
        self.assertEqual((b.fecha, b.fecha_compra, b.cuota), (date(2027, 1, 5), date(2027, 1, 2), "1/6"))
        self.assertEqual((r.resumenes[0].hasta, r.resumenes[0].vencimiento), (date(2027, 1, 5), date(2027, 1, 13)))

    def test_due_date_in_next_year(self):
        dates = ["Cierre actual 28 de diciembre Vencimiento actual 8 de enero"]
        r = parse_lines(sample(DETAIL, dates), "x-2026-12.pdf", TODAY.replace(year=2027))
        self.assertEqual((r.resumenes[0].hasta, r.resumenes[0].vencimiento), (date(2026, 12, 28), date(2027, 1, 8)))

    def test_usd_line_and_set_month(self):
        detail = ["DETALLE DE MOVIMIENTOS", "Consumos", "Con tarjeta virtual",
                  "Fecha Descripción Cuota Operación Pesos Dólares",
                  "3/set NETFLIX.COM 998877 US$ 12,99",
                  "Subtotal $ 0,00 US$ 12,99",
                  "Ajustes y reembolsos",
                  "Fecha Descripción Pesos Dólares",
                  "4/oct Devolución TIENDA -$ 300,00",
                  "Subtotal -$ 300,00 US$ 0,00",
                  "Total a pagar -$ 300,00 US$ 12,99"]
        r = parse_lines(sample(detail), "x-2026-10.pdf", TODAY)
        self.assertEqual(r.errores, [])
        usd, refund = r.movimientos
        self.assertEqual((usd.moneda, usd.monto, usd.fecha, usd.seccion), ("USD", D("-12.99"), date(2026, 9, 3), "consumo"))
        self.assertEqual((refund.moneda, refund.monto, refund.seccion), ("ARS", D("300.00"), "pago"))
        self.assertEqual(r.resumenes[0].totales, {"ARS": D("-300.00"), "USD": D("12.99")})

    def test_subtotal_mismatch_reports_error(self):
        detail = [t.replace("Subtotal $ 241.566,34", "Subtotal $ 241.566,35") for t in DETAIL]
        r = parse_lines(sample(detail), "x-2026-10.pdf", TODAY)
        self.assertEqual(len(r.errores), 1)
        self.assertIn("Consumos", r.errores[0])
        self.assertEqual(len(r.movimientos), 8)

    def test_unrecognised_movement_line_reports_error(self):
        detail = DETAIL[:14] + ["28/ago ALGO RARO sin importe"] + DETAIL[14:]
        r = parse_lines(sample(detail), "x-2026-10.pdf", TODAY)
        self.assertTrue(any("ALGO RARO" in e for e in r.errores))

    def test_missing_closing_date(self):
        r = parse_lines(HEADER + DETAIL, "x-2026-10.pdf", TODAY)
        self.assertEqual(r.movimientos, [])
        self.assertTrue(r.errores)


class LooksLikeTests(TestCase):
    def test_positive(self):
        self.assertTrue(looks_like("\n".join(sample())))

    def test_negative_other_formats(self):
        self.assertFalse(looks_like("Mi resumen de cuenta\nDETALLE DE MOVIMIENTOS"))
        self.assertFalse(looks_like("Resumen de Cuenta Sueldo\nDatos de la cuenta\nGalicia"))
        self.assertFalse(looks_like("Pagaste con Mercado Pago\nDETALLE DE MOVIMIENTOS"))

    def test_matches_rejects_non_pdf(self):
        self.assertFalse(mercadopago_pdf.matches(__file__))


class ImporterCardTests(DjangoTestCase):
    def test_mpago_rows_default_to_gasto(self):
        movs = [ParsedMovement(fecha=date(2026, 9, 11), descripcion="Débito automático de tarjeta",
                               monto=D("100"), moneda="ARS", fuente="mpago", seccion="pago")]
        res = ParseResult(formato="mercadopago_pdf", movimientos=movs, resumenes=[])
        with mock.patch.object(importer.parsers, "parse", return_value=res):
            importer.import_file("x.pdf")
        self.assertEqual(Detalle.objects.get(fuente="mpago").tipo, "gasto")

    @override_settings(CATEGORIAS_GASTOS={}, IGNORAR=[], TARJETAS={"Mercado Pago": "MERPAGO"})
    def test_seed_makes_statement_payments_interno(self):
        call_command("seed_reglas", stdout=StringIO())
        self.assertEqual(Tarjeta.objects.get(fuente="mpago").nombre, "Mercado Pago")
        r = parse_lines(sample(), "x-2026-10.pdf", TODAY)
        with mock.patch.object(importer.parsers, "parse", return_value=r):
            importer.import_file("x.pdf")
        clasificar_todo()
        pagos = Detalle.objects.filter(fuente="mpago", monto__gt=0)
        self.assertEqual(pagos.count(), 5)
        self.assertTrue(all(d.tipo == "interno" and d.categoria.name == CAT_PAGO_TARJETA for d in pagos))
        self.assertFalse(Detalle.objects.filter(fuente="mpago", monto__lt=0).exclude(tipo="gasto").exists())


# Statements up to April 2026: no "Tarjeta de crédito" header, previous balance under
# "Resumen de <mes>" and payments under "Pagos realizados".
OLD_HEADER = ["Este es tu resumen de enero", "Total a pagar Fecha de cierre", "Fecha de vencimiento"]
OLD_DETAIL = [
    "DETALLE DE MOVIMIENTOS",
    "Resumen de diciembre",
    "Fecha Descripción Pesos Dólares",
    "5/dic Resumen de diciembre $ 474.114,42",
    "Subtotal $ 474.114,42 US$ 0,00",
    "Pagos realizados",
    "Fecha Descripción Pesos Dólares",
    "6/dic Pago de tarjeta -$ 474.114,42",
    "Subtotal -$ 474.114,42 US$ 0,00",
    "Consumos",
    "Con tarjeta virtual",
    "Fecha Descripción Cuota Operación Pesos Dólares",
    "14/oct MERPAGO*ADRIANCHICHIK 3 de 3 540363 $ 66.666,33",
    "21/dic MERPAGO*EMIBOGLIO 1 de 3 480569 $ 109.966,66",
    "Subtotal $ 176.632,99 US$ 0,00",
    "Ajustes y reembolsos",
    "No tenés ajustes ni reembolsos",
    "Total a pagar $ 176.632,99 US$ 0,00",
]
OLD_DATES = ["Cierre actual 5 de enero Vencimiento actual 12 de enero"]


class OlderFormatTests(TestCase):
    def test_detected_without_tarjeta_de_credito_header(self):
        self.assertTrue(looks_like("\n".join(OLD_HEADER + OLD_DETAIL + OLD_DATES + FOOTER)))

    def test_previous_balance_skipped_and_payment_parsed(self):
        r = parse_lines(OLD_HEADER + OLD_DETAIL + OLD_DATES + FOOTER, "x-2026-01.pdf", TODAY)
        self.assertEqual(r.errores, [])
        self.assertEqual(r.omitidas, 1)
        self.assertEqual([(m.seccion, m.descripcion, m.monto) for m in r.movimientos], [
            ("pago", "Pago de tarjeta", D("474114.42")),
            ("consumo", "MERPAGO*ADRIANCHICHIK", D("-66666.33")),
            ("consumo", "MERPAGO*EMIBOGLIO", D("-109966.66"))])
        self.assertEqual(r.movimientos[0].fecha, date(2025, 12, 6))
        self.assertEqual(r.resumenes[0].hasta, date(2026, 1, 5))

    def test_previous_period_line_without_amount_is_skipped(self):
        detail = [t.replace("5/sep Total a pagar del periodo anterior $ 244.465,12",
                            "5/sep Total a pagar del periodo anterior") for t in DETAIL]
        detail = [t for t in detail if not t.startswith("11/sep")]
        r = parse_lines(sample(detail), "x-2026-10.pdf", TODAY)
        self.assertEqual(r.errores, [])
        self.assertEqual(r.omitidas, 1)


class SeedOlderPaymentTests(DjangoTestCase):
    @override_settings(CATEGORIAS_GASTOS={}, IGNORAR=[], TARJETAS={})
    def test_pago_de_tarjeta_is_interno(self):
        call_command("seed_reglas", stdout=StringIO())
        r = parse_lines(OLD_HEADER + OLD_DETAIL + OLD_DATES + FOOTER, "x-2026-01.pdf", TODAY)
        with mock.patch.object(importer.parsers, "parse", return_value=r):
            importer.import_file("x.pdf")
        clasificar_todo()
        self.assertEqual(Detalle.objects.get(fuente="mpago", monto__gt=0).tipo, "interno")
