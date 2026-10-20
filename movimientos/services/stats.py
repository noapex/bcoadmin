"""Per-currency aggregations for the dashboard. Internal transfers and inactive rows never count."""
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.db.models import Max, Sum
from django.db.models.functions import TruncMonth

from movimientos.models import Categoria, Detalle, Tarjeta
from movimientos.services.classify import CAT_SIN_DETALLE, CARD_SOURCES, patrones_tarjeta, tarjeta_de_pago

CENT = Decimal("0.01")
SIN_CATEGORIA = "Sin categoría"
ORDEN_TARJETAS = ("visa", "amex", "master")


def _q(value):
    # Legacy rows may come back as float from SQLite REAL.
    return Decimal(str(value or 0)).quantize(CENT)


def _parse(mes):
    y, m = mes.split("-")
    return int(y), int(m)


def month_range(desde, hasta):
    y, m = _parse(desde)
    y2, m2 = _parse(hasta)
    out = []
    while (y, m) <= (y2, m2):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _base(moneda, desde, hasta):
    y, m = _parse(desde)
    y2, m2 = _parse(hasta)
    end = datetime(y2 + 1, 1, 1) if m2 == 12 else datetime(y2, m2 + 1, 1)
    return (Detalle.objects.filter(activo=True, moneda=moneda, fecha__gte=datetime(y, m, 1), fecha__lt=end)
            .exclude(tipo="interno"))


def monthly_totals(moneda, desde, hasta):
    rows = (_base(moneda, desde, hasta).annotate(m=TruncMonth("fecha"))
            .values("m", "tipo").annotate(s=Sum("monto")))
    acc = defaultdict(lambda: {"ingreso": Decimal(0), "gasto": Decimal(0)})
    for r in rows:
        acc[r["m"].strftime("%Y-%m")][r["tipo"]] += _q(r["s"])
    out = []
    for mes in month_range(desde, hasta):
        ing = acc[mes]["ingreso"].quantize(CENT)
        gas = (-acc[mes]["gasto"]).quantize(CENT)
        out.append({"mes": mes, "ingresos": ing, "gastos": gas, "balance": ing - gas})
    return out


def _roots():
    """category id -> (root id, root name), from one query."""
    cats = list(Categoria.objects.values_list("id", "tree_id", "level", "name"))
    root_by_tree = {t: (i, n) for i, t, lvl, n in cats if lvl == 0}
    return {i: root_by_tree[t] for i, t, _lvl, _n in cats}


def _gasto_by_root(moneda, desde, hasta):
    """{(mes, root_id|None, root_name): positive spend}"""
    roots = _roots()
    rows = (_base(moneda, desde, hasta).filter(tipo="gasto").annotate(m=TruncMonth("fecha"))
            .values("m", "categoria_id").annotate(s=Sum("monto")))
    acc = defaultdict(Decimal)
    for r in rows:
        rid, name = roots.get(r["categoria_id"], (None, SIN_CATEGORIA))
        acc[(r["m"].strftime("%Y-%m"), rid, name)] -= _q(r["s"])
    return acc


def spending_by_category(moneda, mes):
    totals = defaultdict(Decimal)
    for (_m, rid, name), v in _gasto_by_root(moneda, mes, mes).items():
        totals[(name, rid)] += v
    return sorted(((n, i, v.quantize(CENT)) for (n, i), v in totals.items()), key=lambda t: t[2], reverse=True)


def category_evolution(moneda, desde, hasta, top_n=6):
    meses = month_range(desde, hasta)
    idx = {m: i for i, m in enumerate(meses)}
    series = defaultdict(lambda: [Decimal(0)] * len(meses))
    for (mes, rid, name), v in _gasto_by_root(moneda, desde, hasta).items():
        vals = series[(name, rid)] = list(series[(name, rid)])
        vals[idx[mes]] += v
    ranked = sorted(series.items(), key=lambda kv: sum(kv[1]), reverse=True)
    out = [{"nombre": n, "id": i, "valores": [x.quantize(CENT) for x in vals]}
           for (n, i), vals in ranked[:top_n]]
    rest = ranked[top_n:]
    if rest:
        otros = [sum((vals[k] for _key, vals in rest), Decimal(0)).quantize(CENT) for k in range(len(meses))]
        out.append({"nombre": "Otros", "id": None, "valores": otros})
    return {"meses": meses, "series": out}


def ultimo_mes_con_datos(moneda):
    last = (Detalle.objects.filter(activo=True, moneda=moneda).exclude(tipo="interno")
            .aggregate(f=Max("fecha"))["f"])
    return last.strftime("%Y-%m") if last else None


def _fila_total(filas, n):
    valores = [sum((f["valores"][k] for f in filas), Decimal(0)).quantize(CENT) for k in range(n)]
    return {"valores": valores, "total": sum(valores, Decimal(0)).quantize(CENT)}


def _por_mes(meses):
    return defaultdict(lambda: [Decimal(0)] * len(meses))


def gastos_por_categoria(moneda, desde, hasta):
    """Monthly spend (positive) per category over the whole Categoria tree.

    Returns {"meses": [YYYY-MM...],
             "filas": [{"id", "nombre", "nivel", "raiz_id", "tiene_hijos", "valores", "total"}...],
             "total": {"valores", "total"}}.
    `filas` follows tree order and ends with the "Sin categoría" row (id None). Each row's
    `valores` (aligned with `meses`) includes the category's descendants. Only activo gastos.
    """
    meses = month_range(desde, hasta)
    idx = {m: i for i, m in enumerate(meses)}
    cats = list(Categoria.objects.order_by("tree_id", "lft")
                .values("id", "name", "parent_id", "level", "tree_id", "lft", "rght"))
    parent = {c["id"]: c["parent_id"] for c in cats}
    acc = _por_mes(meses)
    rows = (_base(moneda, desde, hasta).filter(tipo="gasto").annotate(m=TruncMonth("fecha"))
            .values("m", "categoria_id").annotate(s=Sum("monto")))
    for r in rows:
        k, value = idx[r["m"].strftime("%Y-%m")], -_q(r["s"])
        cid = r["categoria_id"]
        if cid is None:
            acc[None][k] += value
        while cid is not None:  # credit the category and every ancestor
            acc[cid][k] += value
            cid = parent.get(cid)
    root_by_tree = {c["tree_id"]: c["id"] for c in cats if c["level"] == 0}
    filas = []
    for c in cats:
        vals = [v.quantize(CENT) for v in acc[c["id"]]]
        filas.append({"id": c["id"], "nombre": c["name"], "nivel": c["level"],
                      "raiz_id": root_by_tree.get(c["tree_id"]), "tiene_hijos": c["rght"] - c["lft"] > 1,
                      "valores": vals, "total": sum(vals, Decimal(0)).quantize(CENT)})
    sin = [v.quantize(CENT) for v in acc[None]]
    filas.append({"id": None, "nombre": SIN_CATEGORIA, "nivel": 0, "raiz_id": None, "tiene_hijos": False,
                  "valores": sin, "total": sum(sin, Decimal(0)).quantize(CENT)})
    # Grand total = roots + Sin categoría (children are already inside their roots).
    return {"meses": meses, "filas": filas,
            "total": _fila_total([f for f in filas if f["nivel"] == 0], len(meses))}


_ULTIMOS4 = re.compile(r"(\d{4})\s*$")
OTRAS_TARJETAS = "Otras tarjetas"


def gastos_por_tarjeta(moneda, desde, hasta):
    """Monthly card spend (positive) per card source, plus account-side payments without detail.

    Returns {"meses": [YYYY-MM...],
             "filas": [{"fuente", "nombre", "sin_detalle", "categoria_id", "valores", "total"}...],
             "total": {"valores", "total"}}.
    Per card (visa, amex, master order): its row (gastos with that fuente: purchases + taxes - refunds)
    followed by its "pagos sin detalle" row (account payments categorised CAT_SIN_DETALLE matched to
    the card via tarjeta_de_pago). Unmatched sin-detalle payments go last (fuente None).
    `categoria_id` is set on sin-detalle rows only.
    """
    meses = month_range(desde, hasta)
    idx = {m: i for i, m in enumerate(meses)}
    nombres = dict(Tarjeta.objects.values_list("fuente", "nombre"))
    base = _base(moneda, desde, hasta).filter(tipo="gasto").annotate(m=TruncMonth("fecha"))

    consumos, ultimos4 = _por_mes(meses), defaultdict(set)
    for r in (base.filter(fuente__in=CARD_SOURCES).values("m", "fuente", "cuenta").annotate(s=Sum("monto"))):
        consumos[r["fuente"]][idx[r["m"].strftime("%Y-%m")]] -= _q(r["s"])
        found = _ULTIMOS4.search(r["cuenta"] or "")
        if found:
            ultimos4[r["fuente"]].add(found.group(1))

    patrones = patrones_tarjeta()
    sin_detalle, cat_sin = _por_mes(meses), None
    for r in (base.filter(fuente="cuenta", categoria__name=CAT_SIN_DETALLE)
              .values("m", "descripcion", "categoria_id").annotate(s=Sum("monto"))):
        cat_sin = r["categoria_id"]
        sin_detalle[tarjeta_de_pago(r["descripcion"], patrones)][idx[r["m"].strftime("%Y-%m")]] -= _q(r["s"])

    def fila(fuente, nombre, vals, es_sin):
        vals = [v.quantize(CENT) for v in vals]
        return {"fuente": fuente, "nombre": nombre, "sin_detalle": es_sin,
                "categoria_id": cat_sin if es_sin else None,
                "valores": vals, "total": sum(vals, Decimal(0)).quantize(CENT)}

    filas = []
    for fuente in ORDEN_TARJETAS:
        nombre = nombres.get(fuente) or fuente.capitalize()
        if fuente in consumos:
            sufijo = "/".join(sorted(ultimos4[fuente]))
            filas.append(fila(fuente, f"{nombre} · {sufijo}" if sufijo else nombre, consumos[fuente], False))
        if fuente in sin_detalle:
            filas.append(fila(fuente, f"{nombre} · pagos sin detalle", sin_detalle[fuente], True))
    if None in sin_detalle:
        filas.append(fila(None, f"{OTRAS_TARJETAS} · pagos sin detalle", sin_detalle[None], True))
    return {"meses": meses, "filas": filas, "total": _fila_total(filas, len(meses))}
