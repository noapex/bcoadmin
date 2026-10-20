"""Integration checks on the owner's real statements (never committed).

Skipped unless BCOADMIN_SAMPLES_DIR points at the folder holding the downloads.
Per-PDF parsing consistency lives in test_account_pdf.RealAccountPdfTests.
"""
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from django.test import TestCase, override_settings

from movimientos.models import Detalle, Resumen
from movimientos.parsers import account_pdf, card_xlsx
from movimientos.services.importer import import_files

SAMPLES = os.environ.get("BCOADMIN_SAMPLES_DIR")
PDF_GLOB = os.environ.get("BCOADMIN_SAMPLES_GLOB", "*.pdf")  # account PDFs, filtered by matches()
VISA_XLSX = "Último resumen - Visa 5544.xlsx"


def _samples(*patterns):
    base = Path(SAMPLES)
    return sorted({p for pat in patterns for p in base.glob(pat)})


def _account_pdfs():
    return [p for p in _samples(PDF_GLOB) if account_pdf.matches(p)]


def _visa_pdf(cierre):
    """The account PDF issued on the card's closing day (files are named YYYY-MM-DD_<account>.pdf)."""
    return next(p for p in _account_pdfs() if p.name.startswith(cierre.isoformat()))


def _all_files():
    return sorted(set(_account_pdfs()) | set(_samples("movimientos*.xlsx", "Último resumen*.xlsx")))


@skipUnless(SAMPLES, "BCOADMIN_SAMPLES_DIR not set")
class RealCardXlsxTests(TestCase):
    def test_consumos_match_printed_totals(self):
        paths = _samples("Último resumen*.xlsx")
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(card_xlsx.matches(path))
                res = card_xlsx.parse(path)
                self.assertEqual(res.errores, [])
                (st,) = res.resumenes
                for moneda, total in st.totales.items():
                    consumos = -sum((m.monto for m in res.movimientos
                                     if m.seccion == "consumo" and m.moneda == moneda), Decimal("0"))
                    self.assertEqual(consumos, total, moneda)


@skipUnless(SAMPLES, "BCOADMIN_SAMPLES_DIR not set")
class RealImportTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)

    def test_reimport_adds_nothing(self):
        paths = _all_files()
        self.assertTrue(paths)
        first = import_files(paths)
        self.assertEqual([r.archivo for r in first if r.rechazado], [])
        total = Detalle.objects.count()
        second = import_files(paths)
        for r in second:
            with self.subTest(archivo=r.archivo):
                self.assertFalse(r.rechazado, r.errores)
                self.assertEqual(r.nuevos, 0)
        self.assertEqual(Detalle.objects.count(), total)

    def test_account_xlsx_adds_nothing_inside_pdf_periods(self):
        import_files(_account_pdfs())
        periods = list(Resumen.objects.filter(fuente="cuenta").values_list("desde", "hasta"))
        known = set(Detalle.objects.values_list("pk", flat=True))
        import_files(_samples("movimientos*.xlsx"))
        added = [(r.fecha.date(), r.monto, r.ref, r.descripcion[:40])
                 for r in Detalle.objects.exclude(pk__in=known).order_by("fecha")
                 if any(desde <= r.fecha.date() <= hasta for desde, hasta in periods)]
        self.assertEqual(added, [])

    def _assert_visa_cycle_not_duplicated(self, pdf_first):
        xlsx_path = Path(SAMPLES) / VISA_XLSX
        xlsx = card_xlsx.parse(xlsx_path)
        cierre = xlsx.resumenes[0].hasta
        consumos = [m for m in xlsx.movimientos if m.seccion == "consumo"]
        desde = min(m.fecha_compra for m in consumos)
        order = [_visa_pdf(cierre), xlsx_path]
        reports = import_files(order if pdf_first else order[::-1])
        self.assertEqual([r.archivo for r in reports if r.rechazado], [])
        # Consumption rows are the ones bought before the closing day (fees are dated on it).
        rows = Detalle.objects.filter(fuente="visa", fecha_compra__gte=desde, fecha_compra__lt=cierre)
        in_cycle = [m for m in xlsx.movimientos if desde <= m.fecha_compra < cierre]
        self.assertEqual(rows.count(), len(in_cycle))
        self.assertEqual(rows.filter(monto__lt=0).count(), len([m for m in consumos if m.monto < 0]))
        self.assertEqual(Detalle.objects.filter(fuente="visa").count(), len(xlsx.movimientos))

    def test_visa_cycle_xlsx_then_pdf(self):
        self._assert_visa_cycle_not_duplicated(pdf_first=False)

    def test_visa_cycle_pdf_then_xlsx(self):
        self._assert_visa_cycle_not_duplicated(pdf_first=True)
