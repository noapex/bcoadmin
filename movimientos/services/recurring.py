"""Recurring-payment detection and per-item status."""
import calendar
import statistics
from collections import defaultdict
from datetime import date, timedelta

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from ..models import Detalle, Recurrente, Resumen

TOLERANCIA_DIAS = 7
MIN_MESES = 3
VENTANA_MESES = 13
FRECUENCIAS = (1, 2, 3, 6, 12)


def _day(dt):
    if hasattr(dt, "hour") and settings.USE_TZ and timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.date() if hasattr(dt, "date") else dt


def _idx(d):
    return d.year * 12 + d.month - 1


def _add_months(year, month, n, day):
    """(year, month) + n months, with `day` clamped to the month length."""
    i = year * 12 + month - 1 + n
    y, m = divmod(i, 12)
    m += 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def fecha_referencia(fuente):
    """Latest imported date of a source: statement end, else latest movement."""
    hasta = Resumen.objects.filter(fuente=fuente).aggregate(m=Max("hasta"))["m"]
    if hasta:
        return hasta
    ultima = Detalle.objects.filter(fuente=fuente).aggregate(m=Max("fecha"))["m"]
    return _day(ultima) if ultima else None


def _snap(gap):
    return min(FRECUENCIAS, key=lambda f: (abs(f - gap), f))


def _analizar(filas):
    """filas: [(date, abs_amount)] sorted by date -> dict or None."""
    meses = sorted({_idx(d) for d, _ in filas})
    if len(meses) < MIN_MESES:
        return None
    gaps = [b - a for a, b in zip(meses, meses[1:])]
    ultima, monto = filas[-1]
    return dict(
        frecuencia_meses=_snap(statistics.median(gaps)),
        dia_habitual=int(statistics.median_low(d.day for d, _ in filas)),
        monto_ref=monto, ultima_fecha=ultima)


def detectar_recurrentes(hoy=None):
    """Upsert Recurrente rows from expense history; returns the candidate count.

    When one (clave, moneda) shows up under several sources, the group with the
    most recent ultima_fecha wins (ties: lowest fuente name).
    """
    grupos = defaultdict(list)
    qs = (Detalle.objects.filter(tipo="gasto", activo=True, monto__isnull=False)
          .exclude(comercio="").values_list("comercio", "moneda", "fuente", "fecha", "monto")
          .order_by("fecha", "pk"))
    for comercio, moneda, fuente, fecha, monto in qs:
        grupos[(comercio, moneda, fuente)].append((_day(fecha), abs(monto)))

    refs = {}
    elegidos = {}
    for (comercio, moneda, fuente), filas in grupos.items():
        if fuente not in refs:
            refs[fuente] = fecha_referencia(fuente) or hoy
        ref = refs[fuente]
        if ref is None:
            continue
        minimo = _idx(ref) - (VENTANA_MESES - 1)
        filas = [f for f in filas if minimo <= _idx(f[0]) <= _idx(ref)]
        calc = _analizar(filas) if filas else None
        if calc is None:
            continue
        calc["fuente"] = fuente
        clave = (comercio, moneda)
        prev = elegidos.get(clave)
        if (prev is None or calc["ultima_fecha"] > prev["ultima_fecha"]
                or (calc["ultima_fecha"] == prev["ultima_fecha"] and fuente < prev["fuente"])):
            elegidos[clave] = calc

    for (comercio, moneda), calc in elegidos.items():
        rec = Recurrente.objects.filter(clave=comercio, moneda=moneda).first()
        if rec is None:
            Recurrente.objects.create(nombre=comercio.title(), clave=comercio, moneda=moneda, **calc)
            continue
        if rec.manual:
            continue
        for k, v in calc.items():
            setattr(rec, k, v)
        if not rec.nombre_editado:
            rec.nombre = comercio.title()
        rec.save()
    return len(elegidos)


def movimientos_de(rec):
    qs = Detalle.objects.filter(activo=True, moneda=rec.moneda)
    if rec.manual or not rec.clave:
        return qs.filter(descripcion__icontains=rec.patron) if rec.patron else qs.none()
    return qs.filter(comercio=rec.clave, fuente=rec.fuente, tipo="gasto")


def _fue_atrasado(rec):
    """True if the last occurrence landed after (previous expected date + tolerance)."""
    ultima = rec.ultima_fecha
    previas = [_day(f) for f in movimientos_de(rec).order_by("-fecha").values_list("fecha", flat=True)]
    previas = [d for d in previas if _idx(d) < _idx(ultima)]
    if not previas:
        return False
    p = previas[0]
    esperado = _add_months(p.year, p.month, rec.frecuencia_meses, rec.dia_habitual)
    return ultima > esperado + timedelta(days=TOLERANCIA_DIAS)


def estado(rec, ref=None):
    """pagado | pendiente | falta | atrasado | silenciado, judged against the source's last import."""
    if rec.estado_admin == "silenciado":
        return "silenciado"
    ref = ref or fecha_referencia(rec.fuente)
    if rec.ultima_fecha is None or ref is None:
        return "pendiente"
    u = rec.ultima_fecha
    esperado = _add_months(u.year, u.month, rec.frecuencia_meses, rec.dia_habitual)
    limite = esperado + timedelta(days=TOLERANCIA_DIAS)
    if ref < esperado:
        return "atrasado" if _fue_atrasado(rec) else "pagado"
    return "pendiente" if ref < limite else "falta"
