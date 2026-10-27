"""Dedup-aware import of parsed statements into Detalle/Resumen."""
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .. import parsers
from ..demo import db_actual
from ..models import DataFile, Detalle, Resumen
from ..parsers.base import norm_ref
from .classify import clasificar_todo
from .recurring import detectar_recurrentes

# The bank dates the same movement differently in the PDF and the xlsx (seen: 3-5 days).
SAME_REF_TOLERANCE_DAYS = 7      # both receipts present and equal
MISSING_REF_TOLERANCE_DAYS = 5   # either side has no receipt
AMOUNT_EPS = Decimal("0.005")
CARD_SOURCES = {"visa", "amex", "master"}


@dataclass
class ImportReport:
    archivo: str
    formato: str | None
    nuevos: int = 0
    duplicados: int = 0
    omitidas: int = 0
    errores: list[str] = field(default_factory=list)
    rechazado: bool = False
    # (estado, Detalle pk) per parsed movement, in file order; estado is "nuevo" or "duplicado"
    # (the pk is then the row it matched).
    filas: list[tuple[str, int]] = field(default_factory=list)


def _aware(day):
    """Midnight of `day`, matching the project's USE_TZ setting."""
    dt = datetime.combine(day, time.min)
    return timezone.make_aware(dt) if settings.USE_TZ else dt


def _local_date(dt):
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    return dt.date()


def _default_tipo(p):
    if p.fuente in CARD_SOURCES:
        return "gasto"
    return "ingreso" if p.monto > 0 else "gasto"


def _find_duplicate(p, ref, consumed):
    window = max(SAME_REF_TOLERANCE_DAYS, MISSING_REF_TOLERANCE_DAYS)
    lo = _aware(p.fecha - timedelta(days=window))
    hi = _aware(p.fecha + timedelta(days=window + 1))
    # Legacy amounts are SQLite REALs: filter by range, then compare exactly in Python.
    rows = Detalle.objects.filter(
        fuente=p.fuente, moneda=p.moneda, cuota=p.cuota,
        fecha__gte=lo, fecha__lt=hi,
        monto__gte=p.monto - AMOUNT_EPS, monto__lte=p.monto + AMOUNT_EPS,
    ).exclude(pk__in=consumed)
    best, best_key = None, None
    for row in rows:
        if row.monto is None or Decimal(str(row.monto)).quantize(Decimal("0.01")) != p.monto.quantize(Decimal("0.01")):
            continue
        if row.ref and ref:
            if row.ref != ref:
                continue
            tolerance = SAME_REF_TOLERANCE_DAYS
        else:
            tolerance = MISSING_REF_TOLERANCE_DAYS
        days = abs((_local_date(row.fecha) - p.fecha).days)
        if days > tolerance:
            continue
        key = (days, row.ref != ref, row.pk)
        if best_key is None or key < best_key:
            best, best_key = row, key
    return best


def import_file(path, datafile=None):
    result = None
    report = ImportReport(archivo=os.path.basename(str(path)), formato=None)
    try:
        result = parsers.parse(path)
    except Exception as exc:
        report.errores.append(f"{type(exc).__name__}: {exc}")
        report.rechazado = True
        return report
    if result is None:
        report.errores.append("Formato no reconocido")
        report.rechazado = True
        return report
    report.formato = result.formato
    report.omitidas = result.omitidas
    report.errores.extend(result.errores)
    try:
        with transaction.atomic(using=db_actual()):
            consumed = set()
            for p in result.movimientos:
                ref = norm_ref(p.codigo)
                dup = _find_duplicate(p, ref, consumed)
                if dup:
                    consumed.add(dup.pk)
                    report.duplicados += 1
                    report.filas.append(("duplicado", dup.pk))
                    continue
                row = Detalle.objects.create(
                    codigo=p.codigo, fecha=_aware(p.fecha), cuenta=p.cuenta,
                    descripcion=p.descripcion, monto=p.monto, fuente=p.fuente,
                    moneda=p.moneda, tipo=_default_tipo(p), cuota=p.cuota,
                    fecha_compra=p.fecha_compra, titular=p.titular, ref=ref,
                    archivo=datafile)
                consumed.add(row.pk)
                report.nuevos += 1
                report.filas.append(("nuevo", row.pk))
            for s in result.resumenes:
                Resumen.objects.update_or_create(
                    fuente=s.fuente, hasta=s.hasta,
                    defaults=dict(desde=s.desde, vencimiento=s.vencimiento, archivo=datafile,
                                  totales={k: str(v) for k, v in s.totales.items()}))
    except Exception as exc:
        report.nuevos = report.duplicados = 0
        report.filas = []
        report.errores.append(f"{type(exc).__name__}: {exc}")
        report.rechazado = True
    return report


def import_files(paths):
    reports = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                datafile = DataFile()
                datafile.data.save(os.path.basename(str(path)), File(fh), save=True)
            report = import_file(path, datafile)
            if report.rechazado:
                # A rejected file must not leave a DataFile row or media copy behind.
                datafile.data.delete(save=False)
                datafile.delete()
            reports.append(report)
        except Exception as exc:
            reports.append(ImportReport(archivo=os.path.basename(str(path)), formato=None,
                                        errores=[f"{type(exc).__name__}: {exc}"], rechazado=True))
    for step, label in ((clasificar_todo, "Clasificación"), (detectar_recurrentes, "Detección de recurrentes")):
        try:
            step()
        except Exception as exc:
            # Imports are already committed; report the failure without losing the reports.
            msg = f"{label} fallida: {type(exc).__name__}: {exc}"
            if reports:
                reports[-1].errores.append(msg)
            else:
                reports.append(ImportReport(archivo="", formato=None, errores=[msg], rechazado=True))
    return reports
