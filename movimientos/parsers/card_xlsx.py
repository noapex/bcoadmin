"""Parser for the Santander AR credit-card statement xlsx ("Último resumen")."""
import re
from datetime import date
from decimal import Decimal

import openpyxl

from .base import ParsedMovement, ParsedStatement, ParseResult, norm_cuota, parse_amount, parse_date

FORMATO = "card_xlsx"
_CARD_SECTION = re.compile(r"^Tarjeta de (.+?) - ")
_LAST4 = re.compile(r"terminada en (\d{4})")


def _rows(path, limit=None):
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


def _cell(row, i):
    return row[i] if i < len(row) else None


def _text(row, i):
    v = _cell(row, i)
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def matches(path) -> bool:
    try:
        return any(_text(r, 0) == "Movimientos del resumen" for r in _rows(path, 15))
    except Exception:
        return False


def _fuente(card_name: str) -> str:
    if "American Express" in card_name:
        return "amex"
    if "Master" in card_name:
        return "master"
    return "visa" if "Visa" in card_name else ""


def parse(path) -> ParseResult:
    rows = _rows(path)
    result = ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    fuente, last4, cierre, vencimiento = "", "", None, None
    totales: dict[str, Decimal] = {}

    for i, row in enumerate(rows[:15]):
        title = _text(row, 0)
        if title.startswith("Tarjeta ") and "terminada en" in title:
            fuente = _fuente(title)
            m = _LAST4.search(title)
            last4 = m.group(1) if m else ""
        elif title == "Fecha de cierre" and i + 1 < len(rows):
            nxt = rows[i + 1]
            cierre, vencimiento = parse_date(_cell(nxt, 0)), parse_date(_cell(nxt, 1))
    if not fuente or cierre is None:
        result.errores.append("Card statement header (card name / closing date) not found")
        return result
    cuenta = f"{ {'visa': 'Visa', 'amex': 'Amex', 'master': 'Master'}[fuente] } {last4}"

    section = None  # None | "pago" | "consumo" | "impuesto"
    titular = ""
    last_date: date | None = None
    expect_header = False
    for row in rows:
        first = _text(row, 0)
        if expect_header:
            expect_header = False
            continue
        if first == "Pago de tarjeta y devoluciones":
            section, titular, last_date, expect_header = "pago", "", None, True
            continue
        m = _CARD_SECTION.match(first)
        if m:
            section, titular, last_date, expect_header = "consumo", m.group(1), None, True
            continue
        if first == "Otros conceptos":
            section, titular, last_date, expect_header = "impuesto", "", None, True
            continue
        if section is None:
            continue
        if first.startswith("Total de "):
            for idx, cur in ((4, "ARS"), (5, "USD")):
                amt = parse_amount(_cell(row, idx))
                if amt is not None:
                    totales[cur] = totales.get(cur, Decimal("0")) + amt
            section = None
            continue
        if not any(_text(row, j) for j in range(len(row))):
            section = None
            continue

        try:
            if section == "impuesto":
                amounts = [("ARS", _cell(row, 1))]
                desc, cuota, codigo, fecha_compra = first, "", "", cierre
                fecha = cierre
            else:
                raw_date = _cell(row, 0)
                if raw_date is not None and str(raw_date).strip():
                    last_date = parse_date(raw_date)
                if last_date is None:
                    raise ValueError("row without date and nothing to carry forward")
                desc, codigo = _text(row, 1), _text(row, 3)
                codigo = "" if codigo == "-" else codigo
                cuota = norm_cuota(_text(row, 2))
                fecha_compra = last_date
                fecha = cierre if cuota else last_date
                amounts = [("ARS", _cell(row, 4)), ("USD", _cell(row, 5))]
            found = False
            for moneda, raw in amounts:
                amt = parse_amount(raw)
                if amt is None:
                    continue
                found = True
                result.movimientos.append(ParsedMovement(
                    fecha=fecha, descripcion=desc, monto=-amt, moneda=moneda, fuente=fuente,
                    codigo=codigo, cuota=cuota, fecha_compra=fecha_compra, titular=titular,
                    cuenta=cuenta, seccion=section))
            if not found:
                result.omitidas += 1
        except Exception as exc:
            result.errores.append(f"{first or _text(row, 1)!r}: {exc}")

    result.resumenes.append(ParsedStatement(
        fuente=fuente, desde=None, hasta=cierre, vencimiento=vencimiento, totales=totales))
    return result
