import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase, skipUnless

from movimientos.parsers import account_pdf
from movimientos.parsers.account_pdf import parse_account_lines, parse_card_lines


def w(text, x0, top, page=0):
    return {"text": text, "x0": x0, "x1": x0 + 5 * len(text), "top": top, "bottom": top + 8, "page": page}


def words_of(line, x0, top, page=0):
    out, x = [], x0
    for t in line.split():
        out.append(w(t, x, top, page))
        x += 5 * len(t) + 5
    return out


def header(top=80, page=0, moneda="ARS"):
    hdr = [w("Fecha", 40, top, page), w("Comprobante", 80, top, page), w("Movimiento", 140, top, page)]
    if moneda == "ARS":
        hdr += words_of("Caja de Ahorro en pesos", 330, top, page) + words_of("Cuenta Corriente en pesos", 420, top, page)
    else:
        hdr += words_of("Caja de Ahorro en dólares", 330, top, page)
    return hdr + words_of("Saldo en cuenta", 500, top, page)


def base_lines(saldo_tarjeta="2.859.599,69"):
    return header() + [
        w("21/08/26", 40, 100), w("Saldo", 140, 100), w("Inicial", 170, 100), w("$", 330, 100), w("2.939.505,16", 340, 100), w("$", 500, 100), w("2.939.505,16", 510, 100),
        w("27344390", 80, 120), w("Pago", 140, 120), w("de", 165, 120), w("servicios", 180, 120), w("-$", 330, 120), w("69.226,31", 340, 120), w("$", 500, 120), w("2.870.278,85", 510, 120),
        w("24/08/26", 40, 127),                                   # date between the two lines
        w("Edesur:", 140, 134), w("0001928991", 180, 134),
        w("25/08/26", 40, 150), w("Pago", 140, 150), w("de", 165, 150), w("tarjeta", 180, 150), w("de", 215, 150), w("credito", 230, 150), w("-$", 330, 150), w("10.679,16", 340, 150), w("$", 500, 150), w(saldo_tarjeta, 510, 150),
    ]


class AccountLinesTests(TestCase):
    def test_date_on_detail_line_and_missing_comprobante(self):
        movs, errs = parse_account_lines(base_lines(), "ARS")
        self.assertEqual(errs, [])
        self.assertEqual([(m.fecha, m.descripcion, m.monto, m.codigo) for m in movs], [
            (date(2026, 8, 24), "Pago de servicios Edesur: 0001928991", Decimal("-69226.31"), "27344390"),
            (date(2026, 8, 25), "Pago de tarjeta de credito", Decimal("-10679.16"), ""),
        ])
        self.assertTrue(all((m.fuente, m.moneda, m.cuenta, m.seccion) == ("cuenta", "ARS", "Caja de Ahorro", "")
                            for m in movs))

    def test_running_balance_mismatch_is_reported(self):
        words_bad = base_lines(saldo_tarjeta="2.859.598,69")
        movs, errs = parse_account_lines(words_bad, "ARS")
        self.assertEqual(len(movs), 2)
        self.assertEqual(len(errs), 1)
        self.assertIn("saldo inconsistente", errs[0])

    def test_cuenta_corriente_column_and_carried_date_across_pages(self):
        words = base_lines() + header(top=60, page=1) + [
            w("Cobro", 140, 90, 1), w("de", 170, 90, 1), w("interes", 185, 90, 1), w("-$", 420, 90, 1), w("1.341,61", 430, 90, 1), w("$", 500, 90, 1), w("2.858.258,08", 510, 90, 1),
            w("Del", 140, 102, 1), w("01/08/26", 160, 102, 1), w("al", 205, 102, 1), w("31/08/26", 220, 102, 1),
            w("2", 520, 300, 1), w("-", 530, 300, 1), w("14", 540, 300, 1),       # page number
        ]
        movs, errs = parse_account_lines(words, "ARS")
        self.assertEqual(errs, [])
        last = movs[-1]
        self.assertEqual((last.fecha, last.descripcion, last.monto, last.cuenta),
                         (date(2026, 8, 25), "Cobro de interes Del 01/08/26 al 31/08/26", Decimal("-1341.61"),
                          "Cuenta Corriente"))

    def test_usd_section(self):
        words = header(moneda="USD") + [
            w("21/08/26", 40, 100), w("Saldo", 140, 100), w("Inicial", 170, 100), w("U$S", 330, 100), w("4.299,66", 350, 100), w("U$S", 500, 100), w("4.299,66", 520, 100),
            w("25/08/26", 40, 120), w("Pago", 140, 120), w("de", 165, 120), w("tarjeta", 180, 120), w("-U$S", 330, 120), w("77,89", 360, 120), w("U$S", 500, 120), w("4.221,77", 520, 120),
        ]
        movs, errs = parse_account_lines(words, "USD")
        self.assertEqual(errs, [])
        self.assertEqual([(m.moneda, m.monto, m.cuenta) for m in movs], [("USD", Decimal("-77.89"), "Caja de Ahorro")])


class SectionSliceTests(TestCase):
    def test_negative_saldo_total_closes_the_section(self):
        words = (words_of("Movimientos en pesos", 23, 10) + base_lines()
                 + words_of("Saldo total -$ 41.293,19", 387, 200) + words_of("Movimientos en dólares", 23, 220))
        lines = account_pdf._group_lines(words)
        texts = [account_pdf._text(line) for line in lines]
        section, end_line = account_pdf._section(lines, texts, "Movimientos en pesos", account_pdf._END_ARS)
        self.assertEqual(account_pdf._amounts(end_line)[-1][0], Decimal("-41293.19"))
        self.assertFalse(any(wd["text"] == "dólares" for wd in section))


def card_header(top, page=0, comprobante=True):
    hdr = [w("Fecha", 28, top, page)]
    if comprobante:
        hdr += [w("Comprobante", 83, top, page), w("Descripción", 145, top, page), w("Cuota", 351, top, page)]
    else:
        hdr += [w("Descripción", 83, top, page)]
    return hdr + [w("Pesos", 447, top, page), w("Dólares", 513, top, page)]


class CardLinesTests(TestCase):
    def setUp(self):
        self.words = (
            words_of("Tarjeta Santander", 23, 70) + words_of("Visa crédito", 23, 90)
            + words_of("Total en pesos", 37, 160) + [w("Cierre", 425, 160), w("Vencimiento", 496, 160)]
            + [w("$810.64177", 37, 175), w("24/09/26", 425, 175), w("05/10/26", 496, 175)]
            + words_of("Tarjeta terminada en 5544", 39, 200)
            + words_of("Pago anterior y devoluciones", 23, 230) + card_header(250, comprobante=False)
            + [w("01/09/26", 25, 270), *words_of("Saldo anterior", 80, 270), w("$", 423, 270), w("10.679,16", 430, 270), w("U$S", 501, 270), w("77,89", 518, 270)]
            + [w("25/08/26", 25, 290), *words_of("Tu pago en pesos", 80, 290), w("-$", 420, 290), w("10.679,16", 430, 290)]
            + [*words_of("Tu pago en dólares", 80, 310), w("-U$S", 498, 310), w("77,89", 518, 310), w("25/08/26", 25, 312.5)]
            + [w("01/09/26", 25, 330), *words_of("Cr.rg 5617 30% m", 80, 330), w("-$", 420, 330), w("34.980,40", 430, 330)]
            + [w("Total", 330, 350), w("-$", 421, 350), w("34.980,40", 431, 350), w("U$S", 505, 350), w("0,00", 522, 350)]
            + words_of("Consumos del mes", 23, 380)
            + words_of("Total consumos de Juan Perez | Tarjeta terminada en 5544", 23, 400)
            + card_header(420)
            + [w("24/07/25", 25, 440), w("760275", 80, 440), w("Dlo/duvet", 142, 440), w("home", 190, 440), *words_of("14 de 18", 348, 440), w("$", 423, 440), w("31.041,94", 430, 440)]
            + [w("20/08/26", 25, 460), w("367178", 80, 460), *words_of("Anthropic/ claud in1u6bzsbusd 20,00", 142, 460), w("U$S", 501, 460), w("20,00", 518, 460)]
            + [w("940272", 80, 480), w("Dlo/duvet", 142, 480), w("home", 190, 480), w("-$", 420, 480), w("31.041,94", 430, 480), w("20/08/26", 25, 482.5)]
            + [w("7", 537, 700), w("-", 544, 700), w("14", 551, 700)]
            + card_header(60, page=1)
            + [w("22/08/26", 25, 80, 1), w("160922", 80, 80, 1), *words_of("Spotify usd 3,71", 142, 80, 1), w("U$S", 506, 80, 1), w("3,71", 522, 80, 1)]
            + [w("Consumos", 330, 100, 1), w("totales", 366, 100, 1), w("$", 420, 100, 1), w("31.041,94", 427, 100, 1), w("U$S", 496, 100, 1), w("23,71", 513, 100, 1)]
            + words_of("Impuestos", 23, 120, 1) + card_header(140, page=1, comprobante=False)
            + [w("24/09/26", 25, 160, 1), *words_of("Impuesto de sellos $", 80, 160, 1), w("$", 428, 160, 1), w("9.954,88", 434, 160, 1)]
            + [w("Total", 330, 180, 1), w("$", 420, 180, 1), w("9.954,88", 427, 180, 1), w("U$S", 505, 180, 1), w("0,00", 522, 180, 1)]
            + words_of("Total a pagar $ 810.641,77", 317, 200, 1)
        )

    def test_card_rows_semantics(self):
        movs, statement, omitidas, errs = parse_card_lines(self.words)
        self.assertEqual(errs, [])
        self.assertEqual(omitidas, 1)  # Saldo anterior
        self.assertEqual((statement.fuente, statement.hasta, statement.vencimiento, statement.totales),
                         ("visa", date(2026, 9, 24), date(2026, 10, 5),
                          {"ARS": Decimal("31041.94"), "USD": Decimal("23.71")}))
        got = [(m.seccion, m.fecha, m.fecha_compra, m.monto, m.moneda, m.codigo, m.cuota, m.descripcion) for m in movs]
        self.assertEqual(got, [
            ("pago", date(2026, 8, 25), date(2026, 8, 25), Decimal("10679.16"), "ARS", "", "", "Tu pago en pesos"),
            ("pago", date(2026, 8, 25), date(2026, 8, 25), Decimal("77.89"), "USD", "", "", "Tu pago en dólares"),
            ("impuesto", date(2026, 9, 1), date(2026, 9, 1), Decimal("34980.40"), "ARS", "", "", "Cr.rg 5617 30% m"),
            ("consumo", date(2026, 9, 24), date(2025, 7, 24), Decimal("-31041.94"), "ARS", "760275", "14/18", "Dlo/duvet home"),
            ("consumo", date(2026, 8, 20), date(2026, 8, 20), Decimal("-20.00"), "USD", "367178", "",
             "Anthropic/ claud in1u6bzsbusd 20,00"),
            ("consumo", date(2026, 8, 20), date(2026, 8, 20), Decimal("31041.94"), "ARS", "940272", "", "Dlo/duvet home"),
            ("consumo", date(2026, 8, 22), date(2026, 8, 22), Decimal("-3.71"), "USD", "160922", "", "Spotify usd 3,71"),
            ("impuesto", date(2026, 9, 24), date(2026, 9, 24), Decimal("-9954.88"), "ARS", "", "", "Impuesto de sellos $"),
        ])
        self.assertTrue(all((m.fuente, m.cuenta) == ("visa", "Visa 5544") for m in movs))
        self.assertEqual({m.titular for m in movs if m.seccion == "consumo"}, {"Juan Perez"})

    def test_no_card_section(self):
        movs, statement, omitidas, errs = parse_card_lines(words_of("Tarjeta de débito", 23, 70))
        self.assertEqual((movs, statement, omitidas, errs), ([], None, 0, []))


SAMPLES = os.environ.get("BCOADMIN_SAMPLES_DIR")


@skipUnless(SAMPLES, "BCOADMIN_SAMPLES_DIR not set")
class RealAccountPdfTests(TestCase):
    def test_real_pdfs_are_consistent(self):
        paths = sorted(Path(SAMPLES).glob("*_00720009007001455304*.pdf"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(account_pdf.matches(path))
                res = account_pdf.parse(path)
                self.assertEqual(res.errores, [])
                card = [s for s in res.resumenes if s.fuente != "cuenta"]
                for st in card:
                    for moneda, total in st.totales.items():
                        consumos = -sum(m.monto for m in res.movimientos
                                        if m.seccion == "consumo" and m.moneda == moneda)
                        self.assertEqual(consumos, total)
