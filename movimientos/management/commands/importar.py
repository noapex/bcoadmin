from django.core.management.base import BaseCommand

from movimientos.services.importer import import_files


class Command(BaseCommand):
    help = "Import bank/card statements (xlsx, pdf, legacy xls) skipping duplicates."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+")

    def handle(self, *args, **options):
        for r in import_files(options["paths"]):
            self.stdout.write(
                f"{r.archivo}: {r.formato} nuevos={r.nuevos} duplicados={r.duplicados} "
                f"omitidas={r.omitidas} errores={len(r.errores)}")
