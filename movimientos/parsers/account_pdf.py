"""Parser for the Santander AR monthly account statement PDF ("Mi resumen de cuenta").

Plain text extraction is ambiguous, so every section is parsed from pdfplumber word
coordinates: header words give the column positions on each page.
"""
import re
from decimal import Decimal

import pdfplumber

from .base import ParsedMovement, ParsedStatement, ParseResult, norm_cuota, parse_amount, parse_date

FORMATO = "account_pdf"
_DATE = re.compile(r"^\d{2}/\d{2}/\d{2}(?:\d{2})?$")
_NUM = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$")
_PREFIX = re.compile(r"^-?(?:\$|U\$S)$")
_LAST4 = re.compile(r"Tarjeta terminada en (\d{4})")
_TITULAR = re.compile(r"^Total consumos de (.+?) \| Tarjeta terminada en")
_END_ARS = re.compile(r"^Saldo total -?\$ ")      # the total may be negative: "Saldo total -$ 41.293,19"
_END_USD = re.compile(r"^Saldo total -?U\$S ")
_CENT = Decimal("0.01")
_LINE_TOLERANCE = 2      # pt: words whose tops differ by at most this share a line
_SPAN_TOLERANCE = 1      # pt: slack when matching a date/receipt to a movement's vertical span


# ---------------------------------------------------------------- helpers

def _group_lines(words):
    lines = []
    for wd in sorted(words, key=lambda x: (x["page"], x["top"], x["x0"])):
        if lines and lines[-1][0]["page"] == wd["page"] and wd["top"] - lines[-1][0]["top"] <= _LINE_TOLERANCE:
            lines[-1].append(wd)
        else:
            lines.append([wd])
    return [sorted(line, key=lambda x: x["x0"]) for line in lines]


def _text(words):
    return " ".join(wd["text"] for wd in words)


def _center(wd):
    return (wd["page"], (wd["top"] + wd["bottom"]) / 2)


def _amounts(words):
    """[(amount, x1)] for each number, signed by an optional preceding "$"/"-$"/"U$S"/"-U$S" word."""
    out, prefix = [], ""
    for wd in words:
        t = wd["text"]
        if _PREFIX.match(t):
            prefix = t
            continue
        if _NUM.match(t):
            out.append((parse_amount(prefix + t), wd["x1"]))
        prefix = ""
    return out


def _assign(items, rows, field):
    """Give each (position, value) to the first row whose vertical span contains it."""
    for pos, value in items:
        for row in rows:
            if field not in row and row["start"] <= pos <= row["end"]:
                row[field] = value
                break


def _span(line):
    page = line[0]["page"]
    return (page, min(wd["top"] for wd in line) - _SPAN_TOLERANCE), (page, max(wd["bottom"] for wd in line) + _SPAN_TOLERANCE)


# ---------------------------------------------------------------- account sections

def _account_cols(line):
    texts = {wd["text"] for wd in line}
    if not {"Fecha", "Comprobante", "Movimiento", "Caja", "Saldo"} <= texts:
        return None
    cols = {}
    for wd in line:
        t = wd["text"]
        if t in ("Fecha", "Comprobante", "Movimiento", "Caja", "Saldo") and t not in cols:
            cols[t] = wd["x0"]
        elif t == "Cuenta" and "Caja" in cols and "Cuenta" not in cols:
            cols["Cuenta"] = wd["x0"]
    return cols


def _parse_section(words, moneda):
    rows, dates, comps = [], [], []
    cols = None
    dropped = 0  # lines carrying an amount dropped for a bad date / receipt column
    for line in _group_lines(words):
        new_cols = _account_cols(line)
        if new_cols:
            cols = new_cols
            continue
        if cols is None:
            continue
        b_fecha = (cols["Fecha"] + cols["Comprobante"]) / 2
        b_comp = (cols["Comprobante"] + cols["Movimiento"]) / 2
        line_dates, line_comps, text, right, valid = [], [], [], [], True
        for wd in line:
            if wd["x1"] > cols["Caja"]:
                right.append(wd)
            elif wd["x0"] >= b_comp:
                text.append(wd)
            elif wd["x0"] < b_fecha and _DATE.match(wd["text"]):
                line_dates.append((_center(wd), wd["text"]))
            elif wd["x0"] >= b_fecha and wd["text"].isdigit():
                line_comps.append((_center(wd), wd["text"]))
            else:
                valid = False  # legal text, footers, titles
        if not valid:
            if _amounts(right):
                dropped += 1
            continue
        dates += line_dates
        comps += line_comps
        saldos, importes = [], []
        for amount, x1 in _amounts(right):
            if x1 >= cols["Saldo"]:
                saldos.append(amount)
            else:
                cuenta = "Cuenta Corriente" if "Cuenta" in cols and x1 >= cols["Cuenta"] else "Caja de Ahorro"
                importes.append((amount, cuenta))
        if saldos and importes:
            start, end = _span(line)
            rows.append({"start": start, "end": end, "text": [_text(text)], "importes": importes,
                         "saldo": saldos[-1]})
        elif text and not right and rows:
            rows[-1]["text"].append(_text(text))
            rows[-1]["end"] = _span(line)[1]

    _assign(dates, rows, "fecha")
    _assign(comps, rows, "codigo")

    movs, errs, omitidas = [], [], dropped
    prev_fecha = saldo_prev = None
    for row in rows:
        fecha = parse_date(row["fecha"]) if "fecha" in row else prev_fecha
        prev_fecha = fecha
        desc = re.sub(r"\s+", " ", " ".join(row["text"])).strip()
        if desc.startswith("Saldo Inicial"):
            saldo_prev = row["saldo"]
            omitidas += 1
            continue
        if fecha is None or len(row["importes"]) != 1:
            errs.append(f"movimiento ilegible: {desc!r}")
            saldo_prev = row["saldo"]
            continue
        monto, cuenta = row["importes"][0]
        if saldo_prev is not None and (saldo_prev + monto).quantize(_CENT) != row["saldo"].quantize(_CENT):
            errs.append(f"saldo inconsistente en {fecha.isoformat()} {desc}")
        saldo_prev = row["saldo"]
        movs.append(ParsedMovement(fecha=fecha, descripcion=desc, monto=monto, moneda=moneda, fuente="cuenta",
                                   codigo=row.get("codigo", ""), cuenta=cuenta))
    return movs, errs, omitidas


def parse_account_lines(words, moneda):
    """Parse the words of one "Movimientos en pesos/dólares" section (header line included)."""
    movs, errs, _ = _parse_section(words, moneda)
    return movs, errs


# ---------------------------------------------------------------- embedded credit card

def _card_fuente(text):
    if "American Express" in text:
        return "amex"
    if "Master" in text:
        return "master"
    return "visa" if "Visa" in text else ""


def _card_cols(line):
    if not line or line[0]["text"] != "Fecha":
        return None
    cols = {wd["text"]: wd["x0"] for wd in line}
    return cols if {"Descripción", "Pesos", "Dólares"} <= cols.keys() else None


def parse_card_lines(words):
    """Parse the embedded "Tarjeta Santander" statement.

    Returns (movements, statement or None when there is no card section, omitidas, errores).
    """
    lines = _group_lines(words)
    texts = [_text(line) for line in lines]
    try:
        first = texts.index("Tarjeta Santander")
    except ValueError:
        return [], None, 0, []
    fuente = next((_card_fuente(t) for t in texts[first + 1:first + 3] if _card_fuente(t)), "")

    cierre = vencimiento = None
    pending_dates = None   # x positions of the "Cierre"/"Vencimiento" header
    last4 = titular = ""
    section, cols = None, None
    totales: dict[str, Decimal] = {}
    rows, dates = [], []
    dropped = 0  # lines carrying an amount dropped for a non-date in the date column
    for line, text in zip(lines[first + 1:], texts[first + 1:]):
        if text.startswith("Total a pagar") or text == "Tarjeta de débito":
            break
        if pending_dates:
            by_x = {round(wd["x0"]): wd["text"] for wd in line if _DATE.match(wd["text"])}
            cierre_txt = next((t for x, t in by_x.items() if abs(x - pending_dates[0]) <= 10), None)
            venc_txt = next((t for x, t in by_x.items() if abs(x - pending_dates[1]) <= 10), None)
            cierre = parse_date(cierre_txt) if cierre_txt else None
            vencimiento = parse_date(venc_txt) if venc_txt else None
            pending_dates = None
            continue
        line_texts = [wd["text"] for wd in line]
        if cierre is None and line_texts[-2:] == ["Cierre", "Vencimiento"]:
            pending_dates = (line[-2]["x0"], line[-1]["x0"])
            continue
        m = _LAST4.search(text)
        if m and not last4:
            last4 = m.group(1)
        m = _TITULAR.match(text)
        if m:
            titular = m.group(1)
            continue
        if text in ("Pago anterior y devoluciones", "Consumos del mes", "Impuestos"):
            section = {"Pago anterior y devoluciones": "pago", "Consumos del mes": "consumo"}.get(text, "impuesto")
            cols = None
            continue
        if section is None:
            continue
        new_cols = _card_cols(line)
        if new_cols:
            cols = new_cols
            continue
        if text.startswith("Consumos totales"):
            for amount, x1 in _amounts(line):
                totales["USD" if cols and x1 >= cols["Dólares"] else "ARS"] = amount
            section = None
            continue
        if text.startswith("Total "):
            section = None
            continue
        if cols is None:
            continue

        next_col = cols.get("Comprobante", cols["Descripción"])
        b_fecha = (cols["Fecha"] + next_col) / 2
        b_comp = (cols["Comprobante"] + cols["Descripción"]) / 2 if "Comprobante" in cols else b_fecha
        amt_start = cols["Pesos"] - 40
        cuota_start = cols["Cuota"] - 10 if "Cuota" in cols else amt_start
        line_dates, desc, comp, cuota, right, valid = [], [], [], [], [], True
        for wd in line:
            if wd["x0"] >= amt_start:
                right.append(wd)
            elif wd["x0"] >= cuota_start:
                cuota.append(wd)
            elif wd["x0"] < b_fecha:
                if _DATE.match(wd["text"]):
                    line_dates.append((_center(wd), wd["text"]))
                else:
                    valid = False
            elif wd["x0"] < b_comp:
                comp.append(wd)
            else:
                desc.append(wd)
        if not valid:
            if _amounts(right):
                dropped += 1
            continue
        dates += line_dates
        amounts = [("USD" if x1 >= cols["Dólares"] else "ARS", amount) for amount, x1 in _amounts(right)]
        if amounts:
            start, end = _span(line)
            rows.append({"start": start, "end": end, "section": section, "titular": titular,
                         "desc": _text(desc), "codigo": _text(comp), "cuota": norm_cuota(_text(cuota)),
                         "amounts": amounts})

    if cierre is None:
        return [], None, 0, ["Card statement header (closing date) not found"]
    _assign(dates, rows, "fecha")

    cuenta = f"{ {'visa': 'Visa', 'amex': 'Amex', 'master': 'Master'}.get(fuente, '') } {last4}".strip()
    movs, errs, omitidas = [], [], dropped
    prev_section = prev_fecha = None
    for row in rows:
        if row["section"] != prev_section:
            prev_section, prev_fecha = row["section"], None
        fecha_row = parse_date(row["fecha"]) if "fecha" in row else prev_fecha
        prev_fecha = fecha_row
        desc, seccion = row["desc"], row["section"]
        if seccion == "pago" and desc.startswith("Saldo anterior"):
            omitidas += 1
            continue
        if seccion == "pago" and not desc.startswith("Tu pago"):
            seccion = "impuesto"
        if row["section"] == "impuesto":
            fecha = fecha_compra = cierre
        else:
            fecha_compra = fecha_row
            fecha = cierre if row["cuota"] else fecha_row
        if fecha is None:
            errs.append(f"consumo sin fecha: {desc!r}")
            continue
        for moneda, amount in row["amounts"]:
            movs.append(ParsedMovement(
                fecha=fecha, descripcion=desc, monto=-amount, moneda=moneda, fuente=fuente,
                codigo=row["codigo"], cuota=row["cuota"], fecha_compra=fecha_compra,
                titular=row["titular"] if row["section"] == "consumo" else "", cuenta=cuenta, seccion=seccion))
    statement = ParsedStatement(fuente=fuente, desde=None, hasta=cierre, vencimiento=vencimiento, totales=totales)
    return movs, statement, omitidas, errs


# ---------------------------------------------------------------- file level

def _words(pdf):
    out = []
    for i, page in enumerate(pdf.pages):
        for wd in page.extract_words(keep_blank_chars=False, use_text_flow=False):
            out.append({"text": wd["text"], "x0": wd["x0"], "x1": wd["x1"], "top": wd["top"],
                        "bottom": wd["bottom"], "page": i})
    return out


def matches(path) -> bool:
    try:
        with pdfplumber.open(path) as pdf:
            return "Mi resumen de cuenta" in (pdf.pages[0].extract_text() or "")
    except Exception:
        return False


def _labelled_date(lines, label):
    for line in lines:
        for a, b in zip(line, line[1:]):
            if a["text"] == label and _DATE.match(b["text"]):
                return parse_date(b["text"])
    return None


def _section(lines, texts, title, end_re):
    """(words from the title line up to the end line, end line) or (None, None)."""
    try:
        i = texts.index(title)
    except ValueError:
        return None, None
    j = next((k for k in range(i + 1, len(texts)) if end_re.match(texts[k])), len(texts))
    return [wd for line in lines[i:j] for wd in line], (lines[j] if j < len(lines) else None)


def parse(path) -> ParseResult:
    result = ParseResult(formato=FORMATO, movimientos=[], resumenes=[])
    with pdfplumber.open(path) as pdf:
        words = _words(pdf)
    lines = _group_lines(words)
    texts = [_text(line) for line in lines]

    totales: dict[str, Decimal] = {}
    for moneda, title, end in (("ARS", "Movimientos en pesos", _END_ARS), ("USD", "Movimientos en dólares", _END_USD)):
        section_words, end_line = _section(lines, texts, title, end)
        if section_words is None:
            continue
        movs, errs, omitidas = _parse_section(section_words, moneda)
        result.movimientos += movs
        result.errores += errs
        result.omitidas += omitidas
        amounts = _amounts(end_line) if end_line else []
        if amounts:
            totales[moneda] = amounts[-1][0]
        else:
            result.errores.append(f"{title}: 'Saldo total' line not found")

    hasta = _labelled_date(lines, "Hasta:")
    if hasta is None:
        result.errores.append("Statement period (Hasta:) not found")
    else:
        result.resumenes.append(ParsedStatement(fuente="cuenta", desde=_labelled_date(lines, "Desde:"),
                                                hasta=hasta, vencimiento=None, totales=totales))

    card_movs, card_statement, omitidas, errs = parse_card_lines(words)
    result.movimientos += card_movs
    result.omitidas += omitidas
    result.errores += errs
    if card_statement:
        result.resumenes.append(card_statement)
        result.errores += reconcile_card(card_movs, card_statement)
    return result


def reconcile_card(movs, statement):
    """Errors for each currency whose parsed consumption differs from the printed total."""
    parsed: dict[str, Decimal] = {}
    for m in movs:
        if m.seccion == "consumo":
            parsed[m.moneda] = parsed.get(m.moneda, Decimal(0)) - m.monto
    errs = []
    for moneda in sorted(set(parsed) | set(statement.totales)):
        got, total = parsed.get(moneda, Decimal(0)), statement.totales.get(moneda, Decimal(0))
        if got.quantize(_CENT) != total.quantize(_CENT):
            errs.append(f"consumos de tarjeta no cuadran: {moneda} {got} vs {total}")
    return errs
