# -*- coding: utf-8 -*-
import os
import re
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.db.models import Sum
from django.shortcuts import render

from .forms import FileFieldForm
from .get_movs import download_movs
from .models import Categoria, Detalle, Recurrente
from .services import recurring, stats
from .services.importer import import_files

MONEDAS = ("ARS", "USD")
MES_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
ESTADO_ORDEN = {"falta": 0, "pendiente": 1, "atrasado": 2, "pagado": 3, "inactivo": 4}
ESTADO_ICONO = {"falta": "❌", "pendiente": "⏳", "atrasado": "⚠", "pagado": "✅", "inactivo": "💤"}
ESTADO_ETIQUETA = {"falta": "Falta", "pendiente": "Pendiente", "atrasado": "Pagado con atraso",
                   "pagado": "Pagado", "inactivo": "Inactivo"}


def _mes(value):
    return value if value and MES_RE.match(value) else None


def _moneda(request):
    value = request.GET.get("moneda", "ARS")
    return value if value in MONEDAS else "ARS"


def _shift(mes, n):
    y, m = int(mes[:4]), int(mes[5:])
    i = y * 12 + m - 1 + n
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def _f(value):
    return float(value) if value is not None else None


def _recurrentes(moneda):
    items = []
    for rec in Recurrente.objects.filter(moneda=moneda).exclude(estado_admin="silenciado"):
        est = recurring.estado(rec)
        items.append({"rec": rec, "estado": est, "icono": ESTADO_ICONO.get(est, ""),
                      "etiqueta": ESTADO_ETIQUETA.get(est, est)})
    items.sort(key=lambda i: (ESTADO_ORDEN.get(i["estado"], 9), i["rec"].nombre))
    return items


def dashboard(request):
    moneda = _moneda(request)
    ultimo = stats.ultimo_mes_con_datos(moneda)
    ctx = {"moneda": moneda, "vacio": ultimo is None}
    if ultimo is None:
        return render(request, "movimientos/dashboard.html", ctx)

    hasta = _mes(request.GET.get("hasta")) or ultimo
    desde = _mes(request.GET.get("desde")) or _shift(hasta, -11)
    if desde > hasta:
        desde = hasta
    mes = _mes(request.GET.get("mes")) or hasta
    if not desde <= mes <= hasta:
        mes = hasta

    totales = stats.monthly_totals(moneda, desde, hasta)
    actual = next(t for t in totales if t["mes"] == mes)
    recs = _recurrentes(moneda)
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
        "kpis": {"ingresos": actual["ingresos"], "gastos": actual["gastos"],
                 "balance": actual["balance"],
                 "faltan": sum(1 for r in recs if r["estado"] == "falta")},
        "recurrentes": recs,
        "data": data,
    })
    return render(request, "movimientos/dashboard.html", ctx)


def movimientos_list(request):
    moneda = _moneda(request)
    qs = Detalle.objects.filter(activo=True, moneda=moneda)
    mes = _mes(request.GET.get("mes"))
    recurrente = request.GET.get("recurrente", "")
    if mes is None and not recurrente:
        mes = stats.ultimo_mes_con_datos(moneda)
    if mes:
        y, m = int(mes[:4]), int(mes[5:])
        nxt = _shift(mes, 1)
        qs = qs.filter(fecha__gte=date(y, m, 1), fecha__lt=date(int(nxt[:4]), int(nxt[5:]), 1))
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
    qs = qs.select_related("categoria").order_by("-fecha", "-pk")
    total = qs.aggregate(t=Sum("monto"))["t"] or Decimal(0)
    return render(request, "movimientos/movimientos_list.html", {
        "movimientos": qs, "total": total, "moneda": moneda, "mes": mes,
        "categoria": categoria, "tipo": tipo, "recurrente": recurrente,
        "categoria_obj": cat, "recurrente_obj": rec,
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
