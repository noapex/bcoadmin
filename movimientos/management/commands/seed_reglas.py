import re

from django.conf import settings
from django.core.management.base import BaseCommand

from ...models import Categoria, Regla, Tarjeta
from ...services.classify import CAT_INTERNOS, CAT_PAGO_TARJETA

FUENTE_POR_NOMBRE = {"american": "amex", "amex": "amex", "visa": "visa", "master": "master"}
PAGO_TARJETA_REGEX = r"su pago en|tu pago en"
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
        for nombre, codigos in tarjetas.items():
            fuente = FUENTE_POR_NOMBRE.get(nombre.split()[0].lower())
            if fuente is None:
                self.stderr.write(f"Tarjeta desconocida omitida: {nombre}")
                continue
            partes = [re.escape(c) for c in codigos]
            if fuente == "visa":
                partes += ["pago de tarjeta de credito$", "^pago de tarjeta de credito"]
            elif fuente == "amex":
                partes.append("amex")
            Tarjeta.objects.update_or_create(
                fuente=fuente, defaults={"nombre": nombre, "codigo": ", ".join(codigos),
                                         "patron_pago": "|".join(partes)})
