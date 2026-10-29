"""Rule engine: kind (tipo) and category for every Detalle, plus payee key."""
import re
import unicodedata
from datetime import timedelta

from django.utils import timezone

from ..models import Categoria, Detalle, Regla, Resumen, Tarjeta

CAT_SIN_DETALLE = "Tarjeta sin detalle"
CAT_PAGO_TARJETA = "Pagos de tarjeta"
CAT_INTERNOS = "Movimientos internos"
# Code depends on these names: they can't be renamed or deleted from the UI.
CATEGORIAS_RESERVADAS = (CAT_INTERNOS, CAT_PAGO_TARJETA, CAT_SIN_DETALLE)

CARD_SOURCES = {"visa", "amex", "master", "mpago"}
PAYMENT_WINDOW_DAYS = 10
BATCH = 500
COMERCIO_MAX = 120

_TARJ_NRO = re.compile(r"tarj\.?\s*nro\.?\s*\d+")
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
_AMOUNT = re.compile(r"(?:sobre\s+)?(?:u\$s|\$)\s*-?[\d.,]+|-?[\d.,]*\d\s*%")
_TOKEN = re.compile(r"[a-z0-9]+")
_SPLIT_DIGITS = re.compile(r"(?<=\d)[ \t]+(?=\d)")  # "cuit 30703 088534" -> "cuit 30703088534"


def payee_key(descripcion: str) -> str:
    """Description stripped of its variable parts, so recurring payees group together."""
    text = unicodedata.normalize("NFKD", descripcion or "").lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _TARJ_NRO.sub(" ", text)
    text = _DATE.sub(" ", text)
    text = text.replace("*", " ").replace("/", " ")
    text = _AMOUNT.sub(" ", text)
    text = _SPLIT_DIGITS.sub("", text)
    out = []
    for tok in _TOKEN.findall(text):
        has_digit = any(c.isdigit() for c in tok)
        if has_digit and not tok.isdigit():
            if len(tok) >= 4:  # mixed letters+digits: ids, hashes
                continue
        elif tok.isdigit() and len(tok) >= 10:
            tok = tok[:10]  # drop trailing glued dates / varying suffixes
        out.append(tok)
    if out and out[-1] == "usd":
        out.pop()
    return " ".join(out)[:COMERCIO_MAX]


def _default_tipo(fuente, monto):
    if fuente in CARD_SOURCES:
        return "gasto"
    return "ingreso" if monto is not None and monto > 0 else "gasto"


class _Rule:
    def __init__(self, regla):
        self.fuente = regla.fuente
        self.moneda = regla.moneda
        self.categoria_id = regla.categoria_id
        self.tipo = regla.tipo
        self.patron = regla.patron
        self.regex = re.compile(regla.patron, re.I) if regla.es_regex else None
        self.needle = regla.patron.lower()

    def matches(self, fuente, moneda, descripcion):
        if self.fuente:
            if self.fuente == "tarjeta":
                if fuente not in CARD_SOURCES:
                    return False
            elif self.fuente != fuente:
                return False
        if self.moneda and self.moneda != moneda:
            return False
        if self.regex is not None:
            return bool(self.regex.search(descripcion))
        return self.needle in descripcion.lower()


def _load_rules():
    rules = []
    for regla in Regla.objects.order_by("prioridad", "id"):
        try:
            rules.append(_Rule(regla))
        except re.error:
            continue  # an invalid regex typed in the admin must not break classification
    return rules


def _local_date(dt):
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.date()


def _get_cat(name):
    return Categoria.objects.get_or_create(name=name)[0]


def _flush(rows, fields):
    if rows:
        Detalle.objects.bulk_update(rows, fields, batch_size=BATCH)


def _apply_rules(rules, card_payment=None):
    """Rules pass (card-payment resolver takes precedence); returns the set of pks changed."""
    changed, batch = set(), []
    qs = Detalle.objects.only(
        "id", "descripcion", "fuente", "moneda", "monto", "fecha", "tipo", "categoria",
        "categoria_manual", "comercio")
    for row in qs.iterator(chunk_size=2000):
        comercio = payee_key(row.descripcion)
        dirty = comercio != row.comercio
        row.comercio = comercio
        if not row.categoria_manual:
            tipo, cat_id = _default_tipo(row.fuente, row.monto), None
            # Only account outflows can be card payments; inflows fall through to rules/default.
            es_pago = (card_payment and row.fuente == "cuenta"
                       and row.monto is not None and row.monto < 0)
            resolved = card_payment(row) if es_pago else None
            if resolved:
                tipo, cat_id = resolved
            else:
                for rule in rules:
                    if rule.matches(row.fuente, row.moneda, row.descripcion):
                        cat_id = rule.categoria_id
                        if rule.tipo:
                            tipo = rule.tipo
                        break
            if tipo != row.tipo or cat_id != row.categoria_id:
                dirty = True
            row.tipo, row.categoria_id = tipo, cat_id
        if dirty:
            changed.add(row.pk)
            batch.append(row)
            if len(batch) >= BATCH:
                _flush(batch, ["comercio", "tipo", "categoria"])
                batch = []
    _flush(batch, ["comercio", "tipo", "categoria"])
    return changed


def patrones_tarjeta():
    """[(fuente, compiled patron_pago)] for every Tarjeta; invalid regexes are skipped."""
    out = []
    for t in Tarjeta.objects.order_by("id"):
        try:
            out.append((t.fuente, re.compile(t.patron_pago, re.I)))
        except re.error:
            continue
    return out


def tarjeta_de_pago(descripcion: str, patrones=None) -> str | None:
    """Card fuente whose patron_pago matches an account description, else None.

    Pass `patrones` (from patrones_tarjeta()) to avoid a query per call.
    """
    if patrones is None:
        patrones = patrones_tarjeta()
    return next((f for f, rx in patrones if rx.search(descripcion or "")), None)


def _card_payment_resolver():
    """Return fn(row) -> (tipo, categoria_id) | None for account-side card payments.

    A payment is interno only if that card's statement (Resumen) is imported for its cycle.
    """
    tarjetas = patrones_tarjeta()
    if not tarjetas:
        return None
    venc = {}
    for fuente, v in Resumen.objects.exclude(vencimiento=None).values_list("fuente", "vencimiento"):
        venc.setdefault(fuente, []).append(v)
    cat_interno, cat_sin = _get_cat(CAT_PAGO_TARJETA).pk, _get_cat(CAT_SIN_DETALLE).pk
    window = timedelta(days=PAYMENT_WINDOW_DAYS)

    def resolve(row):
        fuente = tarjeta_de_pago(row.descripcion, tarjetas)
        if fuente is None:
            return None
        day = _local_date(row.fecha)
        if any(abs(v - day) <= window for v in venc.get(fuente, ())):
            return "interno", cat_interno
        return "gasto", cat_sin

    return resolve


def resolver_pagos_tarjeta() -> int:
    """Card-payment resolution runs inside the classification pass; single code path."""
    return clasificar_todo()


def clasificar_todo() -> int:
    """Re-classify every non-manual row, fill `comercio`; return rows changed."""
    return len(_apply_rules(_load_rules(), _card_payment_resolver()))
