"""Parser for the Banco Galicia monthly account statement PDF ("Resumen de Cuenta Sueldo").

pdfplumber's plain text keeps the table order: one line per movement
("dd/mm/yy <type> <amount> <balance>", debits negative) followed by its detail lines.
Neither the PDF nor the xlsx carries receipt numbers, so `codigo` is the balance after
the movement: equal across both formats and distinct for same-day identical amounts.
"""
import re
from decimal import Decimal

import pdfplumber

from .base import ParsedMovement, ParsedStatement, ParseResult, parse_amount, parse_date

FORMATO = "galicia_pdf"
FUENTE = "galicia"
CUENTA = "Galicia CA"
_NUM = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
_MOVEMENT = re.compile(rf"^(\d{{2}}/\d{{2}}/\d{{2}}) (.+?) ({_NUM}) ({_NUM})$")
_TABLE_HEADER_TEXT = "Fecha Descripción Origen Crédito Débito Saldo"
_TABLE_HEADER = re.compile(rf"^{_TABLE_HEADER_TEXT}$")
_PAGE_FOOTER = re.compile(r"^Resumen de Cuenta .*Página \d+ / \d+$")
_BALANCE = re.compile(rf"^(-?)(U\$S|\$) ?({_NUM})$")
_PERIOD = re.compile(r"(\d{2}/\d{2}/\d{4}) (\d{2}/\d{2}/\d{4})$")
_TOTAL = re.compile(rf"^Total (?:U\$S|\$) ({_NUM}) -?(?:U\$S|\$) ({_NUM}) -?(?:U\$S|\$) ({_NUM})$")
_CENT = Decimal("0.01")


def codigo_saldo(saldo: Decimal) -> str:
    """Balance as the movement's reference: 2 decimals, no separators ("1483130.12")."""
    return f"{saldo.quantize(_CENT):f}"


def looks_like(first_page: str, last_page: str) -> bool:
    return "Resumen de Cuenta" in first_page and (
        "Datos de la cuenta" in first_page or _TABLE_HEADER_TEXT in first_page) and (
        "Galicia" in last_page or re.search(r"^007\d{19}$", first_page, re.M) is not None)


def _header(lines):
    """(saldo inicial, saldo final, moneda, desde, hasta) from the first page's account block."""
    try:
        start = next(i for i, t in enumerate(lines) if t.startswith("Datos de la cuenta"))
    except StopIteration:
        return None
    saldos, moneda, period = [], None, None
    for text in lines[start + 1:]:
        if _TABLE_HEADER.match(text):
            break
        m = _BALANCE.match(text)
        if m and len(saldos) < 2:
            saldos.append(parse_amount(m.group(1) + m.group(3)))
            moneda = moneda or ("USD" if m.group(2) == "U$S" else "ARS")
        p = _PERIOD.search(text)
        if p and period is None:
            period = (parse_date(p.group(1)), parse_date(p.group(2)))
    if len(saldos) < 2 or period is None:
        return None
    return saldos[0], saldos[1], moneda, period[0], period[1]


def parse_lines(lines) -> ParseResult:
    """Parse the statement's plain-text lines (all pages, in order)."""
    result = ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    lines = [re.sub(r"\s+", " ", t).strip() for t in lines]
    header = _header(lines)
    if header is None:
        result.errores.append("Encabezado de cuenta (saldos / período) no encontrado")
        return result
    inicial, final, moneda, desde, hasta = header

    saldo, current, details, total = inicial, None, [], None

    def flush():
        if current is not None:
            current.descripcion = " ".join([current.descripcion] + details)

    in_table = False
    for text in lines:
        if _TABLE_HEADER.match(text):
            in_table = True
            continue
        if _PAGE_FOOTER.match(text):
            in_table = False
            continue
        if not text:
            continue
        if not in_table:
            # Page headers, footers and legal text live outside the table; a movement there would be lost.
            if re.match(r"^\d{2}/\d{2}/\d{2} ", text):
                result.errores.append(f"Movimiento fuera de la tabla: {text!r}")
            continue
        t = _TOTAL.match(text)
        if t:
            total = [parse_amount(g) for g in t.groups()]
            break
        m = _MOVEMENT.match(text)
        if m:
            flush()
            details = []
            monto, nuevo = parse_amount(m.group(3)), parse_amount(m.group(4))
            if (saldo + monto).quantize(_CENT) != nuevo.quantize(_CENT):
                result.errores.append(
                    f"{m.group(1)} {m.group(2)}: saldo no cuadra ({saldo} + {monto} != {nuevo})")
            saldo = nuevo
            current = ParsedMovement(
                fecha=parse_date(m.group(1)), descripcion=m.group(2), monto=monto, moneda=moneda,
                fuente=FUENTE, codigo=codigo_saldo(nuevo), cuenta=CUENTA)
            result.movimientos.append(current)
        elif re.match(r"^\d{2}/\d{2}/\d{2} ", text):
            result.errores.append(f"Movimiento no reconocido: {text!r}")
        elif current is not None:
            details.append(text)
        else:
            result.errores.append(f"Texto sin movimiento: {text!r}")
    flush()

    if saldo.quantize(_CENT) != final.quantize(_CENT):
        result.errores.append(f"Saldo final no cuadra: {saldo} vs {final}")
    if total is None:
        if result.movimientos:
            result.errores.append("Línea 'Total' no encontrada")
    else:
        creditos = sum((m.monto for m in result.movimientos if m.monto > 0), Decimal(0))
        debitos = -sum((m.monto for m in result.movimientos if m.monto < 0), Decimal(0))
        for nombre, got, printed in (("créditos", creditos, total[0]), ("débitos", debitos, abs(total[1])),
                                     ("saldo", saldo, total[2])):
            if got.quantize(_CENT) != printed.quantize(_CENT):
                result.errores.append(f"Total de {nombre} no cuadra: {got} vs {printed}")

    result.resumenes.append(ParsedStatement(
        fuente=FUENTE, desde=desde, hasta=hasta, vencimiento=None, totales={moneda: final}))
    return result


def _page_texts(path):
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def matches(path) -> bool:
    try:
        with pdfplumber.open(path) as pdf:
            first = pdf.pages[0].extract_text() or ""
            if "Resumen de Cuenta" not in first:
                return False
            return looks_like(first, pdf.pages[-1].extract_text() or "")
    except Exception:
        return False


def parse(path) -> ParseResult:
    return parse_lines([line for page in _page_texts(path) for line in page.split("\n")])
