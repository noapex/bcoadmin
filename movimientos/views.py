# -*- coding: utf-8 -*-
import os
import re
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import FileFieldForm
from .get_movs import download_movs
from .models import Categoria, Detalle, Recurrente
from .services import recurring, stats
from .services.classify import CAT_SIN_DETALLE, CARD_SOURCES, patrones_tarjeta
from .services.importer import import_files

MONEDAS = ("ARS", "USD")
TITULO_MONEDA = {"ARS": "Pesos", "USD": "Dólares"}
FUENTES = ("cuenta",) + tuple(sorted(CARD_SOURCES))
MES_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
ANIO_MIN, ANIO_MAX = 2000, 2100
MAX_MESES = 60
ESTADO_ORDEN = {"falta": 0, "pendiente": 1, "atrasado": 2, "pagado": 3, "inactivo": 4}
ESTADO_ICONO = {"falta": "❌", "pendiente": "⏳", "atrasado": "⚠", "pagado": "✅", "inactivo": "💤"}
ESTADO_ETIQUETA = {"falta": "Falta", "pendiente": "Pendiente", "atrasado": "Pagado con atraso",
                   "pagado": "Pagado", "inactivo": "Inactivo"}


def _mes(value):
    """YYYY-MM within ANIO_MIN..ANIO_MAX, else None."""
    if value and MES_RE.match(value) and ANIO_MIN <= int(value[:4]) <= ANIO_MAX:
        return value
    return None


def _moneda(request):
    value = request.GET.get("moneda", "ARS")
    return value if value in MONEDAS else "ARS"


def _shift(mes, n):
    y, m = int(mes[:4]), int(mes[5:])
    i = y * 12 + m - 1 + n
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def _periodo(request, ultimo, meses=12):
    """(desde, hasta) from GET; default: the `meses` months ending at `ultimo`.

    The span is capped at MAX_MESES by moving `desde` forward.
    """
    hasta = _mes(request.GET.get("hasta")) or ultimo
    desde = _mes(request.GET.get("desde")) or _shift(hasta, -(meses - 1))
    return max(min(desde, hasta), _shift(hasta, -(MAX_MESES - 1))), hasta


def _con_celdas(tabla):
    """Pair each row's values with its month so templates can build per-cell links."""
    meses = tabla["meses"]
    for fila in tabla["filas"] + [tabla["total"]]:
        fila["celdas"] = list(zip(meses, fila["valores"]))
    return tabla


def _f(value):
    return float(value) if value is not None else None


FRECUENCIA_ETIQUETA = {1: "mensual", 2: "bimestral", 3: "trimestral", 6: "semestral", 12: "anual"}
# accion -> (new estado_admin, success message)
ACCIONES_RECURRENTE = {
    "monitorear": ("confirmado", "“{}” ahora se monitorea."),
    "ignorar": ("silenciado", "“{}” se ignora."),
    "dejar": ("silenciado", "“{}” dejó de monitorearse."),
    "restaurar": ("sugerido", "“{}” volvió a sugerencias."),
}


def _frecuencia(rec):
    return FRECUENCIA_ETIQUETA.get(rec.frecuencia_meses, f"cada {rec.frecuencia_meses} meses")


def _recurrentes(moneda, refs=None):
    """Monitored (confirmado) items of a currency with their status, sorted by urgency.

    `refs` caches each source's reference date across calls (one lookup per source, not per item).
    """
    refs = {} if refs is None else refs
    items = []
    for rec in Recurrente.objects.filter(moneda=moneda, estado_admin="confirmado"):
        if rec.fuente not in refs:
            refs[rec.fuente] = recurring.fecha_referencia(rec.fuente)
        est = recurring.estado(rec, ref=refs[rec.fuente])
        items.append({"rec": rec, "estado": est, "icono": ESTADO_ICONO.get(est, ""),
                      "etiqueta": ESTADO_ETIQUETA.get(est, est), "frecuencia": _frecuencia(rec)})
    items.sort(key=lambda i: (ESTADO_ORDEN.get(i["estado"], 9), i["rec"].nombre))
    return items


def _toggle_internos(request):
    """Query strings for the "Sin internos | Con internos" switch, keeping every other GET param."""
    sin, con = request.GET.copy(), request.GET.copy()
    sin.pop("internos", None)
    con["internos"] = "1"
    return {"internos_off": sin.urlencode(), "internos_on": con.urlencode()}


def _navegar_meses(request, desde, hasta, ultimo):
    """Query strings that shift the [desde, hasta] range one month back/forward, keeping its length and
    every other GET param. Forward is None once the range reaches the last month with data."""
    if not desde:
        return {"mes_anterior": None, "mes_siguiente": None}

    def qs(n):
        q = request.GET.copy()
        q.pop("mes", None)
        q["desde"], q["hasta"] = _shift(desde, n), _shift(hasta, n)
        return q.urlencode()

    return {"mes_anterior": qs(-1), "mes_siguiente": qs(1) if not ultimo or hasta < ultimo else None}


def _total_mensual(items):
    """Monthly-equivalent cost of monitored items per currency (monto_ref / frecuencia), skipping inactive ones."""
    total = {m: Decimal(0) for m in MONEDAS}
    for i in items:
        rec = i["rec"]
        if i["estado"] != "inactivo" and rec.monto_ref and rec.moneda in total:
            total[rec.moneda] += abs(rec.monto_ref) / (rec.frecuencia_meses or 1)
    return {m: v.quantize(Decimal("0.01")) for m, v in total.items()}


def dashboard(request):
    moneda = _moneda(request)
    ultimo = stats.ultimo_mes_con_datos(moneda)
    ctx = {"moneda": moneda, "vacio": ultimo is None}
    if ultimo is None:
        return render(request, "movimientos/dashboard.html", ctx)

    desde, hasta = _periodo(request, ultimo)
    mes = _mes(request.GET.get("mes")) or hasta
    if not desde <= mes <= hasta:
        mes = hasta

    internos = request.GET.get("internos") == "1"
    totales = stats.monthly_totals(moneda, desde, hasta, incluir_internos=internos)
    actual = next(t for t in totales if t["mes"] == mes)
    # Recurring payments are a "did I pay everything?" check: show both currencies regardless of the toggle.
    refs, recs = {}, []
    for m in MONEDAS:
        recs += _recurrentes(m, refs)
    recs.sort(key=lambda i: (ESTADO_ORDEN.get(i["estado"], 9), i["rec"].nombre))
    categorias = stats.spending_by_category(moneda, mes)
    evol = stats.category_evolution(moneda, desde, hasta)
    data = {
        "moneda": moneda, "mes": mes,
        "meses": [t["mes"] for t in totales],
        "ingresos": [_f(t["ingresos"]) for t in totales],
        "gastos": [_f(t["gastos"]) for t in totales],
        "balance": [_f(t["balance"]) for t in totales],
        "categorias": [{"nombre": n, "id": i, "total": _f(v)} for n, i, v in categorias],
        "evolucion": {
            "meses": evol["meses"],
            "series": [{"nombre": s["nombre"], "id": s["id"], "valores": [_f(v) for v in s["valores"]]}
                       for s in evol["series"]],
        },
    }
    ctx.update({
        "desde": desde, "hasta": hasta, "mes": mes,
        "internos": internos, **_toggle_internos(request),
        "kpis": {"ingresos": actual["ingresos"], "gastos": actual["gastos"],
                 "balance": actual["balance"],
                 "faltan": sum(1 for r in recs if r["estado"] == "falta"),
                 "pendientes": sum(1 for r in recs if r["estado"] == "pendiente")},
        "recurrentes": recs,
        "total_recurrentes": _total_mensual(recs),
        "sugerencias": Recurrente.objects.filter(estado_admin="sugerido").count(),
        "data": data,
    })
    return render(request, "movimientos/dashboard.html", ctx)


def recurrentes(request):
    sugeridos = Recurrente.objects.filter(estado_admin="sugerido").order_by("-puntaje", "nombre", "pk")
    monitoreados, refs = [], {}
    for moneda in MONEDAS:
        monitoreados += _recurrentes(moneda, refs)
    return render(request, "movimientos/recurrentes.html", {
        # Suggestions show no status: no estado() needed.
        "sugeridos": [{"rec": rec, "frecuencia": _frecuencia(rec)} for rec in sugeridos],
        "monitoreados": monitoreados,
        "total_recurrentes": _total_mensual(monitoreados),
        "ignorados": Recurrente.objects.filter(estado_admin="silenciado").order_by("nombre", "pk"),
    })


@require_POST
def recurrente_estado(request, pk, accion):
    if accion not in ACCIONES_RECURRENTE:
        raise Http404
    rec = get_object_or_404(Recurrente, pk=pk)
    nuevo, mensaje = ACCIONES_RECURRENTE[accion]
    # Only estado_admin changes: never save() the whole row.
    Recurrente.objects.filter(pk=rec.pk).update(estado_admin=nuevo)
    messages.success(request, mensaje.format(rec.nombre))
    return redirect("recurrentes")


@require_POST
def recurrentes_ignorar_todas(request):
    n = Recurrente.objects.filter(estado_admin="sugerido").update(estado_admin="silenciado")
    messages.success(request, f"{n} sugerencia{'s' if n != 1 else ''} ignorada{'s' if n != 1 else ''}.")
    return redirect("recurrentes")


def categorias(request):
    moneda = _moneda(request)
    ultimo = stats.ultimo_mes_con_datos(moneda)
    ctx = {"moneda": moneda, "vacio": ultimo is None}
    if ultimo is not None:
        desde, hasta = _periodo(request, ultimo, meses=6)
        ingresos = stats.ingresos_por_categoria(moneda, desde, hasta)
        # Income touches few categories: list only those with income in the period.
        ingresos["filas"] = [f for f in ingresos["filas"] if f["total"]]
        ctx.update({"desde": desde, "hasta": hasta,
                    "tabla": _con_celdas(stats.gastos_por_categoria(moneda, desde, hasta)),
                    "tabla_ingresos": _con_celdas(ingresos)})
    return render(request, "movimientos/categorias.html", ctx)


def tarjetas(request):
    # Both currencies share one period, ending at the latest month with data in either.
    ultimos = [u for u in (stats.ultimo_mes_con_datos(m) for m in MONEDAS) if u]
    ctx = {"vacio": not ultimos}
    if ultimos:
        desde, hasta = _periodo(request, max(ultimos))
        secciones = []
        for m in MONEDAS:
            tabla = _con_celdas(stats.gastos_por_tarjeta(m, desde, hasta))
            secciones.append({"moneda": m, "titulo": TITULO_MONEDA[m], "tabla": tabla,
                              "hay_sin_detalle": any(f["sin_detalle"] for f in tabla["filas"])})
        ctx.update({"desde": desde, "hasta": hasta, "secciones": secciones})
    return render(request, "movimientos/tarjetas.html", ctx)


def movimientos_list(request):
    moneda = _moneda(request)
    qs = Detalle.objects.filter(activo=True, moneda=moneda)
    # Period: `mes` is shorthand for a one-month range; otherwise `desde`/`hasta`.
    # A recurring drill-down with no period shows all its history.
    mes = _mes(request.GET.get("mes"))
    desde = mes or _mes(request.GET.get("desde"))
    hasta = mes or _mes(request.GET.get("hasta"))
    recurrente = request.GET.get("recurrente", "")
    if not (desde or hasta) and not recurrente:
        desde = hasta = stats.ultimo_mes_con_datos(moneda)
    if desde or hasta:
        desde, hasta = desde or hasta, hasta or desde
        desde, hasta = min(desde, hasta), max(desde, hasta)
        desde = max(desde, _shift(hasta, -(MAX_MESES - 1)))
        nxt = _shift(hasta, 1)
        qs = qs.filter(fecha__gte=date(int(desde[:4]), int(desde[5:]), 1),
                       fecha__lt=date(int(nxt[:4]), int(nxt[5:]), 1))
    categoria = request.GET.get("categoria", "")
    cat = rec = None
    if categoria == "none":
        qs = qs.filter(categoria__isnull=True)
    elif categoria.isdigit():
        cat = Categoria.objects.filter(pk=int(categoria)).first()
        qs = qs.filter(categoria__in=cat.get_descendants(include_self=True)) if cat else qs.none()
    if recurrente.isdigit():
        rec = Recurrente.objects.filter(pk=int(recurrente)).first()
        qs = qs.filter(pk__in=recurring.movimientos_de(rec).values("pk")) if rec else qs.none()
    tipo = request.GET.get("tipo", "")
    if tipo in ("ingreso", "gasto", "interno"):
        qs = qs.filter(tipo=tipo)
    # Internal movements (own transfers, card payments…) are hidden unless asked for.
    internos = request.GET.get("internos") == "1"
    if not internos and tipo != "interno":
        qs = qs.exclude(tipo="interno")
    fuente = request.GET.get("fuente", "")
    if fuente in FUENTES:
        qs = qs.filter(fuente=fuente)
    else:
        fuente = ""
    tarjeta = request.GET.get("tarjeta", "")
    if tarjeta in stats.ORDEN_TARJETAS or tarjeta == "otras":
        # Account-side card payments without detail, of one card ("otras": unmatched/unknown cards).
        objetivo = None if tarjeta == "otras" else tarjeta
        patrones = patrones_tarjeta()
        pagos = qs.filter(fuente="cuenta", categoria__name=CAT_SIN_DETALLE).values_list("pk", "descripcion")
        qs = qs.filter(pk__in=[pk for pk, desc in pagos if stats.tarjeta_de_fila(desc, patrones) == objetivo])
    else:
        tarjeta = ""
    qs = qs.select_related("categoria").order_by("-fecha", "-pk")
    total = qs.aggregate(t=Sum("monto"))["t"] or Decimal(0)
    # Non-period filters, carried by the period form and the currency toggle.
    filtros = [(k, v) for k, v in [("categoria", categoria), ("tipo", tipo), ("fuente", fuente),
                                   ("tarjeta", tarjeta), ("recurrente", recurrente),
                                   ("internos", "1" if internos else "")] if v]
    return render(request, "movimientos/movimientos_list.html", {
        "movimientos": qs, "total": total, "moneda": moneda, "desde": desde, "hasta": hasta,
        "categoria": categoria, "tipo": tipo, "recurrente": recurrente, "fuente": fuente,
        "tarjeta": tarjeta,
        "categoria_obj": cat, "recurrente_obj": rec,
        "filtros": filtros, "filtros_qs": urlencode(filtros),
        "internos": internos, **_toggle_internos(request),
        **_navegar_meses(request, desde, hasta, stats.ultimo_mes_con_datos(moneda)),
    })


def sync(request):
    # Download into a fresh temp dir so any file found there is the new one.
    with tempfile.TemporaryDirectory() as tmp:
        download_movs(tmp)
        downloaded = sorted(Path(tmp).iterdir())
        if not downloaded:
            return render(request, 'movimientos/upload.html', {'error': "No se descargó el archivo"})
        reports = import_files([str(downloaded[0])])
    return render(request, 'movimientos/upload.html', {'reports': reports})


def upload(request):
    form = FileFieldForm()
    if request.method == 'POST':
        files = request.FILES.getlist('file_field')
        if not files:
            return render(request, 'movimientos/upload.html',
                          {'form': form, 'error': "Seleccioná al menos un archivo"})
        # Each upload gets its own directory so same-named files cannot collide and
        # the original filename/extension (needed for format detection) is kept.
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for n, f in enumerate(files):
                folder = Path(tmp) / str(n)
                folder.mkdir()
                path = folder / (os.path.basename(f.name) or 'archivo')
                with open(path, 'wb') as out:
                    for chunk in f.chunks():
                        out.write(chunk)
                paths.append(str(path))
            reports = import_files(paths)
        return render(request, 'movimientos/upload.html', {'form': form, 'reports': reports})
    return render(request, 'movimientos/upload.html', {'form': form})
