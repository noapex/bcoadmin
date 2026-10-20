"""Synthetic xlsx builders that reproduce the real bank layouts (made-up data only)."""
from pathlib import Path

import openpyxl

CARD_HEADER = ["Fecha", "Descripción", "Cuotas", "Comprobante", "Monto en pesos", "Monto en dólares"]

# Section rows for a synthetic Visa statement. ARS consumption nets to 20000.00 + 31041.94 - 31041.94.
DEFAULT_CARD_ROWS = [
    ["Pago de tarjeta y devoluciones"], CARD_HEADER,
    ["25/08/2026", "Su pago en pesos", "", "-", "$-10.000,00", None],
    [None, None, None, None, None, None],
    [None, None, None, None, None, None],
    ["Tarjeta de Juan Perez - Visa Crédito terminada en 5544"], CARD_HEADER,
    ["24/07/2025", "Tienda ejemplo uno ", "14 de 18", "111111*", "$31.041,94", None],
    ["20/08/2026", "Servicio ejemplo usd ", "", "222222K", None, "U$S20,00"],
    [None, "Servicio ejemplo dos ", "", "555555K", None, "U$S3,71"],
    [None, "Tienda ejemplo uno ", "", "333333K", "$-31.041,94", None],
    ["22/08/2026", "Comercio ejemplo ", "", "444444F", "$20.000,00", None],
    ["Total de Visa Crédito terminada en 5544", None, None, None, "$20.000,00", "U$S23,71"],
    [None, None, None, None, None, None],
    [None, None, None, None, None, None],
    ["Otros conceptos"], ["Descripción", "Monto en pesos"],
    ["Impuesto de sellos $", "$1.234,56"],
    [None, None],
    ["Aviso importante: archivo sin validez legal."],
]


def make_card_xlsx(tmpdir, rows_spec=None, card="Visa Crédito", last4="5544",
                   cierre="24/09/2026", vencimiento="05/10/2026") -> Path:
    rows = [
        [None], ["Movimientos del resumen"], [f"Tarjeta {card} terminada en {last4}"], [None],
        ["Fecha de cierre", "Fecha de vencimiento"], [cierre, vencimiento],
        ["Total a pagar"], ["$1,00", "U$S1,00"], [None], [None],
        ["Cierres y vencimientos"], [f"Cierre: {cierre}"], [None], [None],
    ]
    rows += DEFAULT_CARD_ROWS if rows_spec is None else rows_spec
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Resumen"
    for r in rows:
        ws.append(r)
    path = Path(tmpdir) / "resumen.xlsx"
    wb.save(path)
    return path


ACCOUNT_HEADER = [None, "Fecha", "Sucursal origen", "Descripción", "Referencia",
                  "Caja de Ahorro", "Cuenta Corriente", "Saldo"]

DEFAULT_ACCOUNT_ROWS = [
    ["19/10/2026", "000 - CASA CENTRAL", "Pago de servicios \t Edesur: 0001928991", "45645645", "-82212.63", "", ""],
    ["07/10/2026", "009 - SUCURSAL X", "Impuesto ley 25.413 debito 0,6% \t ", "000011824", "", "-8.92", "2754646.73"],
    ["13/10/2026", "000 - CASA CENTRAL", "Debito automatico \t Seguro ejemplo-0600001", "00406031", "-15337.00", "", ""],
    ["13/10/2026", "000 - CASA CENTRAL", "Debito automatico \t Seguro ejemplo-0600002", "00406030", "-15337.00", "", ""],
    ["13/10/2026", "000 - CASA CENTRAL", "Debito automatico \t Seguro ejemplo-0600003", "00406029", "-15337.00", "", ""],
    ["14/10/2026", "000 - CASA CENTRAL", "Transferencia recibida \t De ejemplo", "00003872", "2110004.00", "", "3611748.88"],
]


def make_account_xlsx(tmpdir, rows=None, moneda="Pesos") -> Path:
    data = DEFAULT_ACCOUNT_ROWS if rows is None else rows
    sheet = [
        [","], [None], [None, None, None, None, None, None, None, "lunes, 19 de octubre de 2026 - 19:7"],
        [None], [None], [None],
        [None, "Cuenta", "Cuenta única 000-000000/0"],
        [None, "Moneda", moneda],
        [None, "Fecha", "20/08/2026 - 19/10/2026"],
        [None], [None], [None, "Últimos movimientos"], [None], ACCOUNT_HEADER,
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "UltimosMovimientos"
    for r in sheet:
        ws.append(r)
    for r in data:
        ws.append([None] + list(r))
    for _ in range(3):
        ws.append([None])
    path = Path(tmpdir) / "movimientos.xlsx"
    wb.save(path)
    return path
