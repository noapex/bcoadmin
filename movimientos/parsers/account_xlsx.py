"""Parser for the Santander AR account movements xlsx ("movimientos.xlsx")."""
import re
from decimal import Decimal

import openpyxl

from .base import ParsedMovement, ParsedStatement, ParseResult, parse_amount, parse_date

FORMATO = "account_xlsx"
_MONEDAS = {"pesos": "ARS", "dólares": "USD", "dolares": "USD"}


def _load(path, limit=None):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            rows.append(row)
            if limit and len(rows) >= limit:
                break
        return wb.worksheets[0].title, rows
    finally:
        wb.close()


def _text(row, i):
    v = row[i] if i < len(row) else None
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def matches(path) -> bool:
    try:
        _, rows = _load(path, 20)
        return any(_text(r, 1) == "Fecha" and _text(r, 2) == "Sucursal origen" for r in rows)
    except Exception:
        return False


def parse(path) -> ParseResult:
    _, rows = _load(path)
    result = ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    moneda, desde, hasta, header_idx = "ARS", None, None, None
    for i, row in enumerate(rows):
        label = _text(row, 1)
        if label == "Moneda":
            moneda = _MONEDAS.get(_text(row, 2).lower())
            if moneda is None:
                # Never guess the currency: ARS and USD must not be mixed.
                result.errores.append(f"Moneda no reconocida: {_text(row, 2)!r}")
                return result
        elif label == "Fecha" and _text(row, 2) == "Sucursal origen":
            header_idx = i
            break
        elif label == "Fecha":
            parts = [p.strip() for p in _text(row, 2).split(" - ")]
            if len(parts) == 2:
                desde, hasta = parse_date(parts[0]), parse_date(parts[1])
    if header_idx is None or hasta is None:
        result.errores.append("Account header (period / column titles) not found")
        return result
    cols = {_text(rows[header_idx], j): j for j in range(len(rows[header_idx])) if _text(rows[header_idx], j)}

    def col(row, name):
        j = cols.get(name)
        return row[j] if j is not None and j < len(row) else None

    for row in rows[header_idx + 1:]:
        if not _text(row, 1):
            break
        try:
            amt = None
            for name in ("Caja de Ahorro", "Cuenta Corriente"):
                amt = parse_amount(col(row, name))
                if amt is not None:
                    break
            if amt is None:
                result.omitidas += 1
                continue
            ref = col(row, "Referencia")
            result.movimientos.append(ParsedMovement(
                fecha=parse_date(col(row, "Fecha")),
                descripcion=re.sub(r"\s+", " ", str(col(row, "Descripción") or "")).strip(),
                monto=amt, moneda=moneda, fuente="cuenta",
                codigo=str(ref).strip() if ref is not None else "",
                cuenta=re.sub(r"\s+", " ", str(col(row, "Sucursal origen") or "")).strip()))
        except Exception as exc:
            result.errores.append(f"{_text(row, 1)!r}: {exc}")

    result.resumenes.append(ParsedStatement(
        fuente="cuenta", desde=desde, hasta=hasta, vencimiento=None, totales={}))
    return result
