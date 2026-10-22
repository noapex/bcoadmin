"""Recurring-payment detection and per-item status."""
import calendar
import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Max, Q
from django.utils import timezone

from ..models import Categoria, Detalle, Recurrente, Resumen
from .classify import CARD_SOURCES

TOLERANCIA_DIAS = 7
MIN_MESES = 3
VENTANA_MESES = 13
FRECUENCIAS = (1, 2, 3, 6, 12)
# Matched against `comercio` (payee_key: lowercase, no accents, "*" -> space).
PATRONES_DEBITO = ("debito automatico", "debito directo", "pago de servicios",
                   "pago electronico de servicios", "snp debito")
PATRONES_COMPRA = ("compra con tarjeta de debito", "compra en el exterior", "merpago")
ULTIMOS_MONTOS = 6


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
    """Latest imported date of a source: max(latest statement end, latest movement)."""
    hasta = Resumen.objects.filter(fuente=fuente).aggregate(m=Max("hasta"))["m"]
    ultima = Detalle.objects.filter(fuente=fuente).aggregate(m=Max("fecha"))["m"]
    fechas = [d for d in (hasta, _day(ultima) if ultima else None) if d]
    return max(fechas) if fechas else None


def _snap(gap):
    return min(FRECUENCIAS, key=lambda f: (abs(f - gap), f))


def _analizar(filas):
    """filas: [(date, abs_amount)] sorted by date -> dict or None.

    None when it shows up in fewer than MIN_MESES months, or typically more than
    once per month (median occurrences per month > 1: habits, not obligations).
    """
    por_mes = defaultdict(int)
    for d, _ in filas:
        por_mes[_idx(d)] += 1
    meses = sorted(por_mes)
    if len(meses) < MIN_MESES or statistics.median(por_mes.values()) > 1:
        return None
    gaps = [b - a for a, b in zip(meses, meses[1:])]
    ultima, monto = filas[-1]
    return dict(
        frecuencia_meses=_snap(statistics.median(gaps)),
        dia_habitual=int(statistics.median_low(d.day for d, _ in filas)),
        monto_ref=monto, ultima_fecha=ultima)


def _puntaje(comercio, fuente, frecuencia, filas):
    """Suggestion score 0-100; it only orders the suggestions inbox.

    +40 automatic debit / bill payment wording (PATRONES_DEBITO)
    +25 card source with monthly frequency (subscriptions)
    +20 stable amount: max/min of the last ULTIMOS_MONTOS amounts <= 1.5. A plain ratio
        over ~6 months tolerates the usual inflation drift; erratic spending fails it.
    +15 stable day of month: population stdev of the day <= 3
    -30 point-of-sale wording (PATRONES_COMPRA), unless the card-subscription bonus applies
    """
    puntos = 0
    if any(p in comercio for p in PATRONES_DEBITO):
        puntos += 40
    suscripcion = fuente in CARD_SOURCES and frecuencia == 1
    if suscripcion:
        puntos += 25
    montos = [m for _, m in filas[-ULTIMOS_MONTOS:]]
    if min(montos) > 0 and max(montos) / min(montos) <= Decimal("1.5"):
        puntos += 20
    if statistics.pstdev(d.day for d, _ in filas) <= 3:
        puntos += 15
    if not suscripcion and any(p in comercio for p in PATRONES_COMPRA):
        puntos -= 30
    return max(0, min(100, puntos))


def categorias_sin_recurrentes():
    """Ids of categories that never suggest: flagged off themselves or under a flagged-off ancestor."""
    ids = set()
    for cat in Categoria.objects.filter(sugiere_recurrentes=False):
        ids.update(cat.get_descendants(include_self=True).values_list("pk", flat=True))
    return ids


def detectar_recurrentes(hoy=None):
    """Upsert Recurrente rows from expense history; returns the candidate count.

    Movements in categories that don't suggest recurring items are ignored, and so
    are card installments (cuota "3/6"): finite purchases, not obligations.
    When one (clave, moneda) shows up under several sources, the group with the
    most recent ultima_fecha wins (ties: lowest fuente name).
    Untouched auto suggestions (sugerido, not manual, not edited) that are no
    longer candidates are deleted; detection never changes estado_admin.
    """
    grupos = defaultdict(list)
    qs = (Detalle.objects.filter(tipo="gasto", activo=True, monto__isnull=False)
          .exclude(comercio="").exclude(categoria__in=categorias_sin_recurrentes()).filter(cuota="")
          .values_list("comercio", "moneda", "fuente", "fecha", "monto")
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
        calc["puntaje"] = _puntaje(comercio, fuente, calc["frecuencia_meses"], filas)
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
        if rec.manual or rec.parametros_editados:
            # Refreshed below, keeping the owner's frequency/day/source.
            if rec.puntaje != calc["puntaje"]:
                rec.puntaje = calc["puntaje"]
                rec.save(update_fields=["puntaje", "modified"])
            continue
        for k, v in calc.items():
            setattr(rec, k, v)
        if not rec.nombre_editado:
            rec.nombre = comercio.title()
        rec.save()

    sugeridos = Recurrente.objects.filter(estado_admin="sugerido", manual=False, nombre_editado=False,
                                          parametros_editados=False).values_list("pk", "clave", "moneda")
    obsoletos = [pk for pk, clave, moneda in sugeridos if (clave, moneda) not in elegidos]
    Recurrente.objects.filter(pk__in=obsoletos).delete()

    owner_items = Recurrente.objects.filter(
        Q(manual=True) & ~Q(patron="") | Q(manual=False, parametros_editados=True))
    for rec in owner_items:
        _refrescar(rec)
    return len(elegidos)


def _refrescar(rec):
    """Update ultima_fecha/monto_ref from the item's own movements; nothing else."""
    ultimo = (movimientos_de(rec).filter(tipo="gasto", monto__isnull=False)
              .order_by("-fecha", "-pk").values_list("fecha", "monto").first())
    if ultimo is None:
        return
    fecha, monto = _day(ultimo[0]), abs(ultimo[1])
    if (rec.ultima_fecha, rec.monto_ref) != (fecha, monto):
        rec.ultima_fecha, rec.monto_ref = fecha, monto
        rec.save(update_fields=["ultima_fecha", "monto_ref", "modified"])


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
    """pagado | pendiente | falta | inactivo | atrasado | silenciado, judged against the source's last import.

    inactivo: missed for two or more expected periods (likely cancelled).
    """
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
    if ref < limite:
        return "pendiente"
    segundo = _add_months(u.year, u.month, 2 * rec.frecuencia_meses, rec.dia_habitual)
    return "falta" if ref < segundo + timedelta(days=TOLERANCIA_DIAS) else "inactivo"
