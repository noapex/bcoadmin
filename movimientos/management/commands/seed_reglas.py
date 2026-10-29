import re

from django.conf import settings
from django.core.management.base import BaseCommand

from ...models import Categoria, Regla, Tarjeta
from ...services.classify import CAT_INTERNOS, CAT_PAGO_TARJETA

FUENTE_POR_NOMBRE = {"american": "amex", "amex": "amex", "visa": "visa", "master": "master",
                     "mercado": "mpago", "mpago": "mpago"}
PAGO_TARJETA_REGEX = r"su pago en|tu pago en"
PAGO_MPAGO_REGEX = r"d[eé]bito autom[aá]tico de tarjeta"  # card-side payment line of the mpago statement
PAGO_MPAGO_ANTERIOR_REGEX = r"^pago de tarjeta$"  # same, in statements up to April 2026
INTERNOS_BASE = ["compra de dolares", "venta de dolares", "op cambio", "transf online banking propia"]
IMPUESTOS = ["impuesto", "iva ", "iibb", "percep", "rg 5617", "sellos", "sircreb"]


class Command(BaseCommand):
    help = "Create (idempotently) categories, rules and cards from settings."

    def handle(self, *args, **options):
        self.nuevas = 0
        gastos = getattr(settings, "CATEGORIAS_GASTOS", {})
        self._arbol(gastos, None)
        self._tarjetas(getattr(settings, "TARJETAS", {}))

        interno = self._cat(CAT_INTERNOS, None)
        pagos = self._cat(CAT_PAGO_TARJETA, None)
        extra = list(getattr(settings, "IGNORAR", [])) + list(getattr(settings, "INTERNOS", []))
        for patron in INTERNOS_BASE + extra:
            self._regla(patron, interno, tipo="interno", prioridad=10)
        self._regla(PAGO_TARJETA_REGEX, pagos, tipo="interno", prioridad=10, es_regex=True, fuente="tarjeta")
        self._regla(PAGO_MPAGO_REGEX, pagos, tipo="interno", prioridad=10, es_regex=True, fuente="mpago")
        self._regla(PAGO_MPAGO_ANTERIOR_REGEX, pagos, tipo="interno", prioridad=10, es_regex=True, fuente="mpago")
        impuestos = self._cat("Impuestos", None)
        for patron in IMPUESTOS:
            self._regla(patron, impuestos, prioridad=50)
        self.stdout.write(f"Reglas nuevas: {self.nuevas}")

    def _cat(self, name, parent):
        return Categoria.objects.get_or_create(name=name, defaults={"parent": parent})[0]

    def _regla(self, patron, categoria, tipo="", prioridad=100, es_regex=False, fuente=""):
        _, created = Regla.objects.get_or_create(
            patron=patron, es_regex=es_regex, fuente=fuente,
            defaults={"categoria": categoria, "tipo": tipo, "prioridad": prioridad})
        self.nuevas += created

    def _arbol(self, nodos, parent):
        for name, value in nodos.items():
            if name == "Tarjetas":
                continue
            cat = self._cat(name, parent)
            if isinstance(value, dict):
                self._arbol(value, cat)
            else:
                for patron in value:
                    self._regla(patron, cat)

    def _tarjetas(self, tarjetas):
        # Merge entries sharing a source; create-only so admin edits survive re-seeding.
        por_fuente = {}
        for nombre, codigos in tarjetas.items():
            fuente = FUENTE_POR_NOMBRE.get(nombre.split()[0].lower())
            if fuente is None:
                self.stderr.write(f"Tarjeta desconocida omitida: {nombre}")
                continue
            if isinstance(codigos, str):
                codigos = [codigos]
            entry = por_fuente.setdefault(fuente, {"nombre": nombre, "codigos": []})
            entry["codigos"].extend(codigos)
        for fuente, entry in por_fuente.items():
            codigos = entry["codigos"]
            partes = [re.escape(c) for c in codigos]
            if fuente == "visa":
                partes += ["pago de tarjeta de credito$", "^pago de tarjeta de credito"]
            elif fuente == "amex":
                partes.append("amex")
            Tarjeta.objects.get_or_create(
                fuente=fuente, defaults={"nombre": entry["nombre"], "codigo": ", ".join(codigos),
                                         "patron_pago": "|".join(partes)})
