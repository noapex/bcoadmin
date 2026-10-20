"""Adapter for the legacy Santander AR .xls account exports (2017-2020 layouts)."""
import datetime as dt
import re
import unicodedata
from decimal import Decimal

import xlrd

from .base import ParsedMovement, ParseResult, parse_amount, parse_date

FORMATO = "legacy_xls"
_REQUIRED = {"fecha", "sucursal origen", "referencia", "descripcion"}


def _fold(value) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


def _header_cols(row):
    """Map folded header name -> column index, or None if this is not the header row."""
    cols = {}
    for i, v in enumerate(row):
        if isinstance(v, str) and v.strip():
            cols.setdefault(_fold(v), i)
    return cols if _REQUIRED <= set(cols) else None


def _read_sheets(path):
    book = xlrd.open_workbook(path)
    return [(s.name, [[c.value for c in s.row(r)] for r in range(s.nrows)], book.datemode)
            for s in book.sheets()]


def matches(path) -> bool:
    try:
        if not str(path).lower().endswith(".xls"):
            return False
        return any(_header_cols(r) for _, rows, _ in _read_sheets(path) for r in rows[:30])
    except Exception:
        return False


def _to_date(value, datemode):
    if isinstance(value, (int, float)):
        return xlrd.xldate_as_datetime(value, datemode).date()
    return parse_date(value)


def _code(value) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def parse_rows(rows, datemode=0, result=None, seen=None) -> ParseResult:
    """Parse the rows of one sheet; `seen` de-duplicates across sheets of the same file."""
    result = result or ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    seen = seen if seen is not None else set()
    cols = None
    for row in rows:
        header = _header_cols(row)
        if header:
            cols = header
            continue
        if cols is None:
            continue
        raw_date = row[cols["fecha"]] if cols["fecha"] < len(row) else ""
        if raw_date in ("", None):
            continue
        try:
            fecha = _to_date(raw_date, datemode)
        except Exception:
            continue  # titles, totals and other non-movement rows
        try:
            amt = None
            for name, j in cols.items():
                if name.startswith("importe") and "pesos" in name and j < len(row):
                    amt = parse_amount(row[j])
                    if amt is not None:
                        break
            if amt is None:
                result.omitidas += 1  # e.g. USD-only rows
                continue
            codigo = _code(row[cols["referencia"]])
            desc = re.sub(r"\s+", " ", str(row[cols["descripcion"]])).strip()
            key = (fecha, codigo, desc, amt) if codigo else None
            if key and key in seen:
                continue  # same movement repeated in "Movimientos del Dia"
            if key:
                seen.add(key)
            result.movimientos.append(ParsedMovement(
                fecha=fecha, descripcion=desc, monto=amt, moneda="ARS", fuente="cuenta",
                codigo=codigo,
                cuenta=re.sub(r"\s+", " ", str(row[cols["sucursal origen"]])).strip()))
        except Exception as exc:
            result.errores.append(f"{raw_date!r}: {exc}")
    return result


def parse(path) -> ParseResult:
    result = ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    seen = set()
    for _, rows, datemode in _read_sheets(path):
        parse_rows(rows, datemode, result, seen)
    return result
