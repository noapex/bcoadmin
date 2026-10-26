"""Fill the demo database with realistic, fully synthetic data: ./manage.py generar_demo [--seed N] [--meses 18].

Only invented names, people, CUITs and account numbers. Never reads the real database.
"""
import calendar
import random
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections, router, transaction
from django.db.migrations.executor import MigrationExecutor

from ...demo import DEMO_DB, db_actual, usar_demo
from ...models import Categoria, DataFile, Detalle, Recurrente, Regla, Resumen, Tarjeta
from ...parsers.base import norm_ref
from ...services.classify import CAT_INTERNOS, CAT_PAGO_TARJETA, CAT_SIN_DETALLE, clasificar_todo, payee_key
from ...services.recurring import detectar_recurrentes, estado

CENT = Decimal("0.01")
INFLACION = 1.03  # monthly

ARBOL = [
    ("Servicios", ["Luz", "Gas", "Agua", "Internet", "Celular"]),
    ("Supermercado", []),
    ("Restaurantes y delivery", []),
    ("Transporte", ["Combustible", "Peajes", "Transporte público"]),
    ("Suscripciones", []), ("Salud", []), ("Educación", []), ("Hogar", []), ("Ropa", []),
    ("Entretenimiento", []), ("Impuestos", []), ("Seguros", []), ("Sueldo", []), ("Otros ingresos", []),
    (CAT_INTERNOS, []), (CAT_PAGO_TARJETA, []), (CAT_SIN_DETALLE, []),
]
# Same flags migration 0012 sets on the real system categories.
SIN_RECURRENTES = ("Impuestos", CAT_INTERNOS, CAT_PAGO_TARJETA, CAT_SIN_DETALLE)

# (patron, categoria, tipo, prioridad, fuente): case-insensitive "contains" rules.
REGLAS = [
    ("transferencia propia", CAT_INTERNOS, "interno", 10, ""),
    ("compra de dólares", CAT_INTERNOS, "interno", 10, ""),
    ("su pago en", CAT_PAGO_TARJETA, "interno", 10, "tarjeta"),
    ("impuesto", "Impuestos", "", 50, ""),
    ("percepción", "Impuestos", "", 50, ""),
    ("haberes", "Sueldo", "ingreso", 100, ""),
    ("transferencia recibida", "Otros ingresos", "", 100, ""),
    ("intereses", "Otros ingresos", "", 100, ""),
    ("luz del sur", "Luz", "", 100, ""),
    ("gasnorte", "Gas", "", 100, ""),
    ("aguaclara", "Agua", "", 100, ""),
    ("fibranet", "Internet", "", 100, ""),
    ("móviltel", "Celular", "", 100, ""),
    ("super ahorro", "Supermercado", "", 100, ""),
    ("mercado don tito", "Supermercado", "", 100, ""),
    ("la parrilla de pepe", "Restaurantes y delivery", "", 100, ""),
    ("pizzería napoli", "Restaurantes y delivery", "", 100, ""),
    ("deliveryya", "Restaurantes y delivery", "", 100, ""),
    ("restaurante el puerto", "Restaurantes y delivery", "", 100, ""),
    ("combustibles ruta 2", "Combustible", "", 100, ""),
    ("autopistas del plata", "Peajes", "", 100, ""),
    ("tarjeta viajar", "Transporte público", "", 100, ""),
    ("streamflix", "Suscripciones", "", 100, ""),
    ("cloudbox", "Suscripciones", "", 100, ""),
    ("musicaplus", "Suscripciones", "", 100, ""),
    ("videoclub plus", "Suscripciones", "", 100, ""),
    ("farmacia central", "Salud", "", 100, ""),
    ("prepaga salud total", "Salud", "", 100, ""),
    ("gimnasio fuerza", "Salud", "", 100, ""),
    ("colegio san martín", "Educación", "", 100, ""),
    ("ferretería el tornillo", "Hogar", "", 100, ""),
    ("muebles casa linda", "Hogar", "", 100, ""),
    ("electro hogar total", "Hogar", "", 100, ""),
    ("moda urbana", "Ropa", "", 100, ""),
    ("cine estrella", "Entretenimiento", "", 100, ""),
    ("seguros la confianza", "Seguros", "", 100, ""),
]

CUENTA_ARS = "CA $ 001-123456/7"
CUENTA_USD = "CA U$S 001-765432/1"
CUENTA_GALICIA = "CA $ 9999-0012345-6"
TITULAR = "Juan Pérez"
TARJETAS = {
    "visa": dict(nombre="Visa Crédito ****4321", codigo="0000004321", patron_pago="pago tarjeta visa",
                 cuenta="Visa ****4321", cierre=24),
    "amex": dict(nombre="Amex ****9876", codigo="0000009876", patron_pago="pago tarjeta amex",
                 cuenta="Amex ****9876", cierre=17),
}

LUZ = "Pago de servicios Luz del Sur SA cliente 4455667"
GAS = "Débito automático GasNorte cliente 5566778"
AGUA = "Pago de servicios AguaClara cuenta 778899"
INTERNET = "Débito automático FibraNet abonado 112233"
CELULAR = "Débito automático MóvilTel línea 998877"
SEGURO_HOGAR = "Débito automático Seguros La Confianza hogar 3344"
SEGURO_AUTO = "Débito automático Seguros La Confianza auto 3345"
PREPAGA = "Débito automático Prepaga Salud Total plan 210"
COLEGIO = "Débito automático Colegio San Martín cuota"
STREAMFLIX = "StreamFlix.com"
CLOUDBOX = "CloudBox storage"
MUSICAPLUS = "MusicaPlus"
VIDEOCLUB = "VideoClub Plus"
GIMNASIO = "Gimnasio Fuerza"

# (descripcion, moneda, display name): monitored after detection.
CONFIRMAR = [
    (LUZ, "ARS", "Luz (Luz del Sur)"), (GAS, "ARS", "Gas (GasNorte)"), (AGUA, "ARS", "Agua (AguaClara)"),
    (INTERNET, "ARS", "Internet (FibraNet)"), (CELULAR, "ARS", "Celular (MóvilTel)"),
    (SEGURO_HOGAR, "ARS", "Seguro del hogar"), (STREAMFLIX, "USD", "StreamFlix"), (CLOUDBOX, "USD", "CloudBox"),
]
IGNORAR = [(GIMNASIO, "ARS"), (MUSICAPLUS, "USD")]
CUOTAS = [("Muebles Casa Linda", 6, 450_000, 900_000), ("Electro Hogar Total", 12, 600_000, 1_200_000),
          ("Tienda Moda Urbana", 3, 60_000, 150_000), ("Ferretería El Tornillo", 3, 40_000, 90_000)]


def _q(x):
    return Decimal(str(x)).quantize(CENT)


def _fin_de_mes(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def _dia(y, m, d):
    return date(y, m, max(1, min(d, calendar.monthrange(y, m)[1])))


def _habil_antes(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _habil_despues(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _meses(fin, n):
    i = fin.year * 12 + fin.month - 1
    return [divmod(j, 12) for j in range(i - n + 1, i + 1)]


class Generador:
    def __init__(self, rng, fin, meses):
        self.rng, self.fin = rng, fin
        self.meses = [(y, m + 1) for y, m in _meses(fin, meses)]
        self.filas, self.resumenes = [], []

    # -- helpers --
    def _codigo(self):
        return str(self.rng.randint(10_000_000, 99_999_999))

    def add(self, fuente, moneda, dia, descripcion, monto, cuenta, codigo=None, **extra):
        if dia > self.fin:
            return None
        codigo = self._codigo() if codigo is None else codigo
        fila = Detalle(codigo=codigo, ref=norm_ref(codigo), fecha=datetime.combine(dia, datetime.min.time()),
                       cuenta=cuenta, descripcion=descripcion, monto=_q(monto), fuente=fuente, moneda=moneda,
                       tipo="gasto" if fuente in TARJETAS or monto < 0 else "ingreso", **extra)
        self.filas.append(fila)
        return fila

    def _jitter(self, y, m, d, k=1):
        return _dia(y, m, d + self.rng.randint(-k, k))

    def _random_en(self, desde, hasta):
        return desde + timedelta(days=self.rng.randint(0, max(0, (hasta - desde).days)))

    # -- accounts --
    def cuentas(self):
        rng, ult = self.rng, len(self.meses) - 1
        sueldo = 2_450_000 + rng.uniform(0, 150_000)
        # Forced statuses relative to the reference date (the last cuenta movement, `fin`):
        # internet stops two months ago (falta), the phone bill of this month is due (pendiente).
        dia_falta, dia_pendiente = min(28, self.fin.day + 5), max(1, self.fin.day - 3)
        for i, (y, m) in enumerate(self.meses):
            f = INFLACION ** i
            cta = dict(fuente="cuenta", moneda="ARS", cuenta=CUENTA_ARS)
            self.add(dia=_habil_despues(date(y, m, 1)), descripcion="Acreditación de haberes Empresa Ejemplo SA",
                     monto=sueldo * f, **cta)
            if m in (6, 12):
                self.add(dia=_dia(y, m, 18), descripcion="Acreditación de haberes SAC Empresa Ejemplo SA",
                         monto=sueldo * f / 2, **cta)
            verano = 1.3 if m in (12, 1, 2) else 1
            invierno = 2.6 if m in (5, 6, 7, 8, 9) else 1
            self.add(dia=self._jitter(y, m, 12), descripcion=LUZ, monto=-32_000 * f * verano * rng.uniform(.9, 1.1), **cta)
            self.add(dia=self._jitter(y, m, 15), descripcion=GAS, monto=-9_000 * f * invierno * rng.uniform(.9, 1.1), **cta)
            if m % 2 == 0:
                self.add(dia=self._jitter(y, m, 20), descripcion=AGUA, monto=-21_000 * f * rng.uniform(.95, 1.05), **cta)
            if i <= ult - 2:
                self.add(dia=_dia(y, m, dia_falta), descripcion=INTERNET, monto=-24_000 * f, **cta)
            if i <= ult - 1:
                self.add(dia=_dia(y, m, dia_pendiente), descripcion=CELULAR, monto=-16_000 * f, **cta)
            self.add(dia=self._jitter(y, m, 8), descripcion=SEGURO_HOGAR, monto=-38_000 * f, **cta)
            self.add(dia=self._jitter(y, m, 8), descripcion=SEGURO_AUTO, monto=-72_000 * f, **cta)
            self.add(dia=self._jitter(y, m, 5), descripcion=PREPAGA, monto=-165_000 * f, **cta)
            if m >= 3:
                self.add(dia=self._jitter(y, m, 10), descripcion=COLEGIO, monto=-210_000 * f, **cta)
            self.add(dia=_dia(y, m, 1), descripcion="Comisión mantenimiento de cuenta", monto=-4_500 * f, **cta)
            for _ in range(rng.randint(3, 5)):
                self.add(dia=_dia(y, m, rng.randint(1, 28)), descripcion="Impuesto débitos y créditos",
                         monto=-rng.uniform(500, 5_000) * f, **cta)
            # Own transfer to the second bank and dollar purchases: interno.
            transf = 350_000 * f * rng.uniform(.8, 1.2)
            d = _habil_despues(date(y, m, 4))
            self.add(dia=d, descripcion="Transferencia propia a caja de ahorro Galicia", monto=-transf, **cta)
            self.add("galicia", "ARS", d, "Transferencia propia desde cuenta principal", transf, CUENTA_GALICIA)
            if i % 2 == 0:
                usd = rng.randint(150, 400)
                d = _dia(y, m, 6)
                self.add(dia=d, descripcion="Compra de dólares", monto=-usd * 1_000 * 1.02 ** i, **cta)
                self.add("cuenta", "USD", d, "Compra de dólares", usd, CUENTA_USD)
            if i % 3 == 1:
                self.add(dia=_dia(y, m, rng.randint(10, 25)), descripcion="Transferencia recibida de María Gómez",
                         monto=rng.uniform(80_000, 200_000) * f, **cta)
            self.add("cuenta", "USD", _fin_de_mes(y, m), "Intereses caja de ahorro dólares",
                     rng.uniform(.5, 2), CUENTA_USD)
            self._galicia(y, m, f)
            self.resumenes.append(Resumen(fuente="cuenta", desde=date(y, m, 1), hasta=min(_fin_de_mes(y, m), self.fin)))
            self.resumenes.append(Resumen(fuente="galicia", desde=date(y, m, 1),
                                          hasta=min(_fin_de_mes(y, m), self.fin)))
        # The reference date of the account is `fin`.
        self.add("cuenta", "ARS", self.fin, "Impuesto débitos y créditos", -rng.uniform(500, 3_000), CUENTA_ARS)

    def _galicia(self, y, m, f):
        rng = self.rng
        gal = dict(fuente="galicia", moneda="ARS", cuenta=CUENTA_GALICIA)
        for _ in range(rng.randint(2, 3)):
            self.add(dia=_dia(y, m, rng.randint(1, 28)), descripcion="Compra con tarjeta de débito Super Ahorro",
                     monto=-rng.uniform(18_000, 60_000) * f, **gal)
        if rng.random() < .6:
            self.add(dia=_dia(y, m, rng.randint(1, 28)), descripcion="Compra con tarjeta de débito Farmacia Central",
                     monto=-rng.uniform(8_000, 30_000) * f, **gal)
        for _ in range(rng.randint(2, 4)):
            self.add(dia=_dia(y, m, rng.randint(1, 28)), descripcion="Recarga Tarjeta Viajar",
                     monto=-rng.uniform(2_500, 6_000) * f, **gal)
        self.add(dia=_dia(y, m, rng.randint(1, 28)), descripcion="Extracción cajero automático",
                 monto=-40_000 * f, **gal)

    # -- cards --
    def tarjetas(self):
        for fuente, cfg in TARJETAS.items():
            self._tarjeta(fuente, cfg)

    def _ciclos(self, dia_cierre):
        y, m = self.meses[0]
        py, pm = divmod(y * 12 + m - 2, 12)
        previo = _habil_antes(_dia(py, pm + 1, dia_cierre))
        ciclos = []
        for y, m in self.meses:
            cierre = _habil_antes(_dia(y, m, dia_cierre))
            if cierre > self.fin:
                break
            ciclos.append((previo + timedelta(days=1), cierre, _habil_despues(cierre + timedelta(days=10))))
            previo = cierre
        return ciclos

    def _compras_visa(self, desde, hasta, f):
        rng = self.rng
        out = []
        for desc, n, lo, hi in [("Super Ahorro", (3, 5), 25_000, 90_000),
                                ("La Parrilla de Pepe", (0, 2), 35_000, 70_000),
                                ("Pizzería Napoli", (1, 2), 15_000, 30_000),
                                ("DeliveryYa *Sushi Zen", (1, 2), 12_000, 28_000),
                                ("DeliveryYa *Burger Bros", (1, 2), 10_000, 22_000),
                                ("Combustibles Ruta 2", (2, 3), 30_000, 55_000),
                                ("Cine Estrella", (0, 1), 12_000, 20_000)]:
            for _ in range(rng.randint(*n)):
                out.append((self._random_en(desde, hasta), desc, "ARS", -rng.uniform(lo, hi) * f))
        for dia, desc, usd in [(5, STREAMFLIX, 10.99), (12, CLOUDBOX, 2.99), (20, MUSICAPLUS, 5.99)]:
            out.append((self._dia_en_ciclo(desde, hasta, dia), desc, "USD", -usd))
        return out

    def _compras_amex(self, desde, hasta, f):
        rng = self.rng
        out = []
        for desc, n, lo, hi in [("Mercado Don Tito", (2, 3), 20_000, 60_000),
                                ("Autopistas del Plata peaje", (3, 6), 1_500, 4_000),
                                ("Restaurante El Puerto", (0, 1), 40_000, 80_000),
                                ("Farmacia Central", (0, 1), 8_000, 25_000)]:
            for _ in range(rng.randint(*n)):
                out.append((self._random_en(desde, hasta), desc, "ARS", -rng.uniform(lo, hi) * f))
        out.append((self._dia_en_ciclo(desde, hasta, 3), VIDEOCLUB, "ARS", -6_500 * f))
        out.append((self._dia_en_ciclo(desde, hasta, 2), GIMNASIO, "ARS", -28_000 * f))
        return out

    @staticmethod
    def _dia_en_ciclo(desde, hasta, dia):
        for y, m in ((desde.year, desde.month), (hasta.year, hasta.month)):
            d = _dia(y, m, dia)
            if desde <= d <= hasta:
                return d
        return hasta

    def _tarjeta(self, fuente, cfg):
        rng = self.rng
        ciclos = self._ciclos(cfg["cierre"])
        # Statements never imported: their account-side payment shows up as "Tarjeta sin detalle".
        sin_resumen = {len(ciclos) - (6 if fuente == "visa" else 10)} if len(ciclos) >= 12 else set()
        cuotas = {}  # cycle index -> [(descripcion, cuota, fecha_compra, monto, codigo)]
        pago_previo = None
        tarj = dict(fuente=fuente, cuenta=cfg["cuenta"])
        for k, (desde, cierre, venc) in enumerate(ciclos):
            f = INFLACION ** k
            if rng.random() < .3:
                desc, n, lo, hi = rng.choice(CUOTAS)
                compra, total, codigo = self._random_en(desde, cierre), rng.uniform(lo, hi) * f, self._codigo()
                for j in range(n):
                    cuotas.setdefault(k + j, []).append((desc, f"{j + 1}/{n}", compra, -total / n, codigo))
            compras = self._compras_visa(desde, cierre, f) if fuente == "visa" else self._compras_amex(desde, cierre, f)
            if rng.random() < .12:
                compras.append((self._random_en(desde, cierre), "Devolución Tienda Moda Urbana", "ARS",
                                rng.uniform(10_000, 40_000)))
            compras.append((cierre, "Percepción IIBB", "ARS", -rng.uniform(1_500, 4_000) * f))
            totales = {"ARS": Decimal(0), "USD": Decimal(0)}
            filas = []
            for dia, desc, moneda, monto in compras:
                filas.append(dict(dia=dia, descripcion=desc, moneda=moneda, monto=monto, fecha_compra=dia))
            for desc, cuota, compra, monto, codigo in cuotas.get(k, []):
                # Installments are dated at the statement's closing date, like the real parsers.
                filas.append(dict(dia=cierre, descripcion=desc, moneda="ARS", monto=monto, cuota=cuota,
                                  fecha_compra=compra, codigo=codigo))
            if pago_previo:
                for moneda, label in (("ARS", "pesos"), ("USD", "dólares")):
                    if pago_previo[1][moneda] > 0:
                        filas.append(dict(dia=pago_previo[0], descripcion=f"Su pago en {label}", moneda=moneda,
                                          monto=pago_previo[1][moneda], fecha_compra=pago_previo[0]))
            for fila in filas:
                if not fila["descripcion"].startswith("Su pago"):
                    totales[fila["moneda"]] -= _q(fila["monto"])
                if k not in sin_resumen:
                    self.add(titular=TITULAR, **tarj, **fila)
            if k not in sin_resumen:
                self.resumenes.append(Resumen(fuente=fuente, desde=desde, hasta=cierre, vencimiento=venc,
                                              totales={m: str(v) for m, v in totales.items()}))
            # Paid in full from the account on the due date.
            nombre = "Visa" if fuente == "visa" else "Amex"
            self.add("cuenta", "ARS", venc, f"Pago tarjeta {nombre}", -totales["ARS"], CUENTA_ARS)
            if totales["USD"] > 0:
                self.add("cuenta", "USD", venc, f"Pago tarjeta {nombre}", -totales["USD"], CUENTA_USD)
            pago_previo = (venc, totales)


class Command(BaseCommand):
    help = "Wipe the demo database and fill it with synthetic data (never touches the real database)."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--meses", type=int, default=18)

    def handle(self, *args, **opts):
        if DEMO_DB not in settings.DATABASES:
            raise CommandError("settings.DATABASES has no 'demo' database.")
        nombres = [Path(str(connections[a].settings_dict["NAME"])).expanduser().resolve()
                   for a in (DEMO_DB, DEFAULT_DB_ALIAS)]
        if nombres[0] == nombres[1]:
            raise CommandError("The demo database must not be the real database.")
        if opts["meses"] < 3:
            raise CommandError("--meses must be at least 3.")
        with usar_demo():
            if db_actual() != DEMO_DB or router.db_for_write(Detalle) != DEMO_DB:
                raise CommandError("The movimientos app is not routed to the demo database.")
            self._migrar()
            with transaction.atomic(using=DEMO_DB):
                self._generar(opts["seed"], opts["meses"])
                self._resumen()

    def _migrar(self):
        executor = MigrationExecutor(connections[DEMO_DB])
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            call_command("migrate", database=DEMO_DB, interactive=False, verbosity=0)

    def _generar(self, seed, meses):
        for model in (Recurrente, Detalle, Resumen, Regla, Tarjeta, Categoria, DataFile):
            model.objects.using(DEMO_DB).all().delete()
        cats = {}
        for nombre, hijos in ARBOL:
            cats[nombre] = padre = Categoria.objects.using(DEMO_DB).create(
                name=nombre, sugiere_recurrentes=nombre not in SIN_RECURRENTES)
            for hijo in hijos:
                cats[hijo] = Categoria.objects.using(DEMO_DB).create(name=hijo, parent=padre)
        Regla.objects.using(DEMO_DB).bulk_create(
            Regla(patron=p, categoria_id=cats[c].pk, tipo=t, prioridad=pr, fuente=fu) for p, c, t, pr, fu in REGLAS)
        Tarjeta.objects.using(DEMO_DB).bulk_create(
            Tarjeta(fuente=fu, nombre=c["nombre"], codigo=c["codigo"], patron_pago=c["patron_pago"])
            for fu, c in TARJETAS.items())

        gen = Generador(random.Random(seed), date.today(), meses)
        gen.cuentas()
        gen.tarjetas()
        Detalle.objects.using(DEMO_DB).bulk_create(gen.filas, batch_size=500)
        Resumen.objects.using(DEMO_DB).bulk_create(gen.resumenes)

        clasificar_todo()
        detectar_recurrentes()
        for desc, moneda, nombre in CONFIRMAR:
            n = Recurrente.objects.using(DEMO_DB).filter(clave=payee_key(desc), moneda=moneda).update(
                estado_admin="confirmado", nombre=nombre, nombre_editado=True)
            if not n:
                self.stderr.write(f"Not detected as recurring: {desc}")
        for desc, moneda in IGNORAR:
            Recurrente.objects.using(DEMO_DB).filter(clave=payee_key(desc), moneda=moneda).update(
                estado_admin="silenciado")

    def _resumen(self):
        filas = Counter(Detalle.objects.using(DEMO_DB).values_list("fuente", "moneda", "tipo"))
        self.stdout.write("Demo data (database 'demo'):")
        for (fuente, moneda, tipo), n in sorted(filas.items()):
            self.stdout.write(f"  {fuente:8} {moneda} {tipo:8} {n:5}")
        self.stdout.write(f"  total movimientos: {sum(filas.values())}, resúmenes: "
                          f"{Resumen.objects.using(DEMO_DB).count()}, categorías: "
                          f"{Categoria.objects.using(DEMO_DB).count()}, reglas: {Regla.objects.using(DEMO_DB).count()}")
        recs = Counter(Recurrente.objects.using(DEMO_DB).values_list("estado_admin", flat=True))
        estados = Counter(estado(r) for r in Recurrente.objects.using(DEMO_DB).filter(estado_admin="confirmado"))
        self.stdout.write(f"  recurrentes: {dict(sorted(recs.items()))}; monitoreados: {dict(sorted(estados.items()))}")
