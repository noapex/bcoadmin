"""Parser for the Banco Galicia account movements xlsx ("Extracto_<n>.xlsx", sheet "Cuentas").

Rows come newest first; "Saldo Parcial" is the balance after each movement, used as
`codigo` exactly like the PDF parser does (see galicia_pdf).
"""
import re
from decimal import Decimal

import openpyxl

from .base import ParsedMovement, ParsedStatement, ParseResult, parse_amount, parse_date
from .galicia_pdf import CUENTA, FUENTE, codigo_saldo

FORMATO = "galicia_xlsx"
_MONEDAS = {"pesos": "ARS", "dólares": "USD", "dolares": "USD"}
_HEADER = ["Fecha", "Movimiento", "Débito", "Crédito", "Saldo Parcial"]
_CENT = Decimal("0.01")


def _load(path, limit=None):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            rows.append(row)
            if limit and len(rows) >= limit:
                break
        return rows
    finally:
        wb.close()


def _text(row, i):
    v = row[i] if i < len(row) else None
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def matches(path) -> bool:
    try:
        rows = _load(path, 1)
        return bool(rows) and _text(rows[0], 0).startswith("Banco Galicia")
    except Exception:
        return False


def parse(path) -> ParseResult:
    rows = _load(path)
    result = ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    titulo = _text(rows[0], 0) if rows else ""
    moneda = next((v for k, v in _MONEDAS.items() if k in titulo.lower()), None)
    if moneda is None:
        # Never guess the currency: ARS and USD must not be mixed.
        result.errores.append(f"Moneda no reconocida: {titulo!r}")
        return result
    desde = hasta = header_idx = None
    for i, row in enumerate(rows):
        m = re.search(r"del (\d{2}/\d{2}/\d{4}) al (\d{2}/\d{2}/\d{4})", _text(row, 0))
        if m:
            desde, hasta = parse_date(m.group(1)), parse_date(m.group(2))
        if [_text(row, j) for j in range(len(_HEADER))] == _HEADER:
            header_idx = i
            break
    if header_idx is None or hasta is None:
        result.errores.append("Encabezado (intervalo / títulos de columna) no encontrado")
        return result

    saldo = None
    for row in reversed(rows[header_idx + 1:]):  # oldest first
        if not _text(row, 0):
            if any(_text(row, j) for j in range(1, 5)):
                result.errores.append(f"Fila sin fecha: {_text(row, 1)!r} "
                                      + " ".join(_text(row, j) for j in range(2, 5)))
            continue
        try:
            debito, credito, nuevo = (parse_amount(row[j]) for j in (2, 3, 4))
            if (debito != 0) == (credito != 0):
                result.errores.append(f"{_text(row, 0)} {_text(row, 1)!r}: débito/crédito ambiguo")
                continue
            monto = debito if debito != 0 else credito
            if saldo is not None and (saldo + monto).quantize(_CENT) != nuevo.quantize(_CENT):
                result.errores.append(
                    f"{_text(row, 0)} {_text(row, 1)!r}: saldo no cuadra ({saldo} + {monto} != {nuevo})")
            saldo = nuevo
            result.movimientos.append(ParsedMovement(
                fecha=parse_date(_text(row, 0)), descripcion=_text(row, 1), monto=monto, moneda=moneda,
                fuente=FUENTE, codigo=codigo_saldo(nuevo), cuenta=CUENTA))
        except Exception as exc:
            result.errores.append(f"{_text(row, 0)!r}: {exc}")

    result.resumenes.append(ParsedStatement(
        fuente=FUENTE, desde=desde, hasta=hasta, vencimiento=None,
        totales={moneda: saldo} if saldo is not None else {}))
    return result
