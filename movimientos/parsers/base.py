"""Common types and number/date helpers shared by all statement parsers."""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class ParsedMovement:
    fecha: date                 # account: movement date; card: purchase date, or closing date for installments
    descripcion: str            # "<type> <detail>", whitespace collapsed, original case
    monto: Decimal              # effect on the owner's money (outflow negative)
    moneda: str                 # "ARS" | "USD"
    fuente: str                 # "cuenta" | "visa" | "amex" | "master" | "mpago"
    codigo: str = ""            # raw receipt/reference as printed
    cuota: str = ""             # normalized "7/9" or ""
    fecha_compra: date | None = None
    titular: str = ""
    cuenta: str = ""            # account: branch or account type; card: "Visa 5544"
    seccion: str = ""           # card: "consumo" | "pago" | "impuesto"; account: ""


@dataclass
class ParsedStatement:
    fuente: str
    desde: date | None
    hasta: date                 # account: period end; card: closing date
    vencimiento: date | None    # card due date
    totales: dict[str, Decimal]  # per currency, as printed by the bank


@dataclass
class ParseResult:
    formato: str                # "card_xlsx" | "account_xlsx" | "account_pdf" | "legacy_xls"
    movimientos: list[ParsedMovement]
    resumenes: list[ParsedStatement]
    omitidas: int = 0           # rows seen but intentionally skipped
    errores: list[str] = field(default_factory=list)


def parse_amount(value: str | float | int | None) -> Decimal | None:
    """Parse AR-formatted ("$-1.234,56", "U$S20,00") or dot-decimal amounts."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    negative = False
    m = re.search(r"\d", text)
    if not m:
        return None
    if "-" in text[:m.start()]:
        negative = True
    num = text[m.start():].replace(" ", "")
    if not re.fullmatch(r"\d[\d.,]*", num) or not re.fullmatch(r"[-$US\s]*", text[:m.start()]):
        return None
    if "," in num:
        num = num.replace(".", "").replace(",", ".")
    try:
        result = Decimal(num)
    except Exception:
        return None
    return -result if negative else result


def parse_date(value: str | date | datetime) -> date:
    """Parse dd/mm/yy, dd/mm/yyyy, "dd/mm/yyyy HH:MM", date or datetime."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    token = str(value).strip().split()[0]
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date: {value!r}")


def norm_ref(codigo: str) -> str:
    """Digits only, leading zeros stripped; "" if none."""
    return re.sub(r"\D", "", codigo or "").lstrip("0")


def norm_cuota(cuota: str) -> str:
    """"07 de 09" -> "7/9"; "" -> ""."""
    m = re.match(r"\s*(\d+)\s*(?:de|/)\s*(\d+)\s*$", cuota or "")
    return f"{int(m.group(1))}/{int(m.group(2))}" if m else ""
