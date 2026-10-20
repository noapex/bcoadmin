"""Per-currency aggregations for the dashboard. Internal transfers and inactive rows never count."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.db.models import Max, Sum
from django.db.models.functions import TruncMonth

from movimientos.models import Categoria, Detalle

CENT = Decimal("0.01")
SIN_CATEGORIA = "Sin categoría"


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
