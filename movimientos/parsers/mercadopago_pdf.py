"""Parser for the Mercado Pago credit-card statement PDF ("Este es tu resumen de ... Tarjeta de crédito").

pdfplumber's plain text has one line per movement ("d/mmm <description> [<n> de <m> <operation>]
[$ X] [US$ Y]"). Dates carry no year: the closing year comes from a YYYY-MM in the file name
(else today's year), moved back a year if that closing date would be in the future.
Amounts are printed from the card's side (purchases positive, payments/refunds "-$"), so
monto is the printed amount negated, as in card_xlsx.
"""
import os
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

import pdfplumber

from .base import ParsedMovement, ParsedStatement, ParseResult, norm_cuota, parse_amount

FORMATO = "mercadopago_pdf"
FUENTE = "mpago"
CUENTA = "Mercado Pago"
_MONTHS = {m: i for i, m in enumerate(
    ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"), 1)}
_MONTHS["set"] = 9
_NUM = r"\d{1,3}(?:\.\d{3})*,\d{2}"
_AMOUNTS = rf"(?: (-?\$ {_NUM}))?(?: (-?US\$ {_NUM}))?$"
_DATE = r"^(\d{1,2})/([A-Za-z]{3}) "
_MOVEMENT = re.compile(rf"{_DATE}(.+?){_AMOUNTS}")
_PURCHASE = re.compile(rf"{_DATE}(.+?)(?: (\d+ de \d+))?(?: (\d{{4,}}))?{_AMOUNTS}")
_SUBTOTAL = re.compile(rf"^Subtotal (-?\$ {_NUM}) (-?US\$ {_NUM})$")
_TOTAL = re.compile(rf"^Total a pagar (-?\$ {_NUM}) (-?US\$ {_NUM})$")
_DATES = re.compile(r"Cierre actual (\d{1,2}) de (\w+) Vencimiento actual (\d{1,2}) de (\w+)")
_FILE_YEAR = re.compile(r"(20\d{2})-(?:0[1-9]|1[0-2])")
_SECTIONS = (("Composición del saldo", "pago"), ("Consumos", "consumo"), ("Impuestos e intereses", "impuesto"),
             ("Pagos anticipados", "pago"), ("Ajustes y reembolsos", "pago"))
_SKIP = "Total a pagar del periodo anterior"
_CENT = Decimal("0.01")


def looks_like(text: str) -> bool:
    return ("Tarjeta de crédito" in text and "DETALLE DE MOVIMIENTOS" in text
            and ("MercadoLibre S.R.L." in text or "Mercado Pago" in text) and "Cierre actual" in text)


def _month(name):
    return _MONTHS.get(name[:3].lower())


def _dates(lines, filename, today):
    """(cierre, vencimiento) from the "Cierre actual ... Vencimiento actual ..." line, or None."""
    m = next((m for m in map(_DATES.search, lines) if m), None)
    if m is None or None in (_month(m.group(2)), _month(m.group(4))):
        return None
    y = _FILE_YEAR.search(os.path.basename(str(filename or "")))
    year = int(y.group(1)) if y else today.year
    cierre = date(year, _month(m.group(2)), int(m.group(1)))
    if cierre > today:
        cierre = cierre.replace(year=year - 1)
    due_month = _month(m.group(4))
    vencimiento = date(cierre.year + (due_month < cierre.month), due_month, int(m.group(3)))
    return cierre, vencimiento


def parse_lines(lines, filename="", today=None) -> ParseResult:
    """Parse the statement's plain-text lines (all pages, in order)."""
    result = ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    lines = [re.sub(r"\s+", " ", t).strip() for t in lines]
    dates = _dates(lines, filename, today or date.today())
    if dates is None:
        result.errores.append("Fechas de cierre / vencimiento no encontradas")
        return result
    cierre, vencimiento = dates

    totales, in_detail, section, title = {}, False, None, ""
    sums = defaultdict(Decimal)
    for text in lines:
        if text == "DETALLE DE MOVIMIENTOS":
            in_detail = True
            continue
        if not in_detail:
            continue
        t = _TOTAL.match(text)
        if t:
            totales = {"ARS": parse_amount(t.group(1)), "USD": parse_amount(t.group(2))}
            break
        heading = next((s for h, s in _SECTIONS if text.startswith(h)), None)
        if heading:
            section, title, sums = heading, text.rstrip("0123456789"), defaultdict(Decimal)
            continue
        s = _SUBTOTAL.match(text)
        if s:
            for moneda, raw in (("ARS", s.group(1)), ("USD", s.group(2))):
                if sums[moneda].quantize(_CENT) != parse_amount(raw).quantize(_CENT):
                    result.errores.append(f"Subtotal de {title!r} no cuadra en {moneda}: {sums[moneda]} vs {raw}")
            sums = defaultdict(Decimal)
            continue
        if not re.match(_DATE, text):
            continue
        m = (_PURCHASE if section == "consumo" else _MOVEMENT).match(text)
        month = _month(m.group(2)) if m else None
        amounts = [(c, parse_amount(g)) for c, g in (("ARS", m.group(m.re.groups - 1)),
                                                     ("USD", m.group(m.re.groups))) if g] if m else []
        if section is None or not month or not amounts:
            result.errores.append(f"Movimiento no reconocido: {text!r}")
            continue
        for moneda, amt in amounts:
            sums[moneda] += amt
        desc = m.group(3)
        if desc == _SKIP:
            result.omitidas += 1
            continue
        compra = date(cierre.year - (month > cierre.month), month, int(m.group(1)))
        cuota, codigo = (norm_cuota(m.group(4) or ""), m.group(5) or "") if section == "consumo" else ("", "")
        for moneda, amt in amounts:
            result.movimientos.append(ParsedMovement(
                fecha=cierre if cuota else compra, descripcion=desc, monto=-amt, moneda=moneda, fuente=FUENTE,
                codigo=codigo, cuota=cuota, fecha_compra=compra, cuenta=CUENTA, seccion=section))

    if not totales:
        result.errores.append("Línea 'Total a pagar' no encontrada")
    result.resumenes.append(ParsedStatement(
        fuente=FUENTE, desde=None, hasta=cierre, vencimiento=vencimiento, totales=totales))
    return result


def _page_texts(path):
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def matches(path) -> bool:
    try:
        return looks_like("\n".join(_page_texts(path)))
    except Exception:
        return False


def parse(path) -> ParseResult:
    return parse_lines([line for page in _page_texts(path) for line in page.split("\n")], path)
