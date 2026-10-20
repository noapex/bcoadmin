# -*- coding: utf-8 -*-
from django.db import models
from mptt.models import MPTTModel, TreeForeignKey


class TimeStampedModel(models.Model):
    """
    An abstract base class model that provides self-
    updating "created" and "modified" fields.
    """

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Detalle(TimeStampedModel):
    TIPOS = [("ingreso", "Ingreso"), ("gasto", "Gasto"), ("interno", "Interno")]

    codigo = models.CharField('Código', max_length=20)
    fecha = models.DateTimeField()
    cuenta = models.CharField('Cuenta', max_length=50)
    descripcion = models.CharField('Descripción', max_length=200)
    monto = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    activo = models.BooleanField(default=True)
    fuente = models.CharField(max_length=10, default="cuenta", db_index=True)
    moneda = models.CharField(max_length=3, default="ARS", db_index=True)
    tipo = models.CharField(max_length=8, choices=TIPOS, default="gasto", db_index=True)
    categoria = models.ForeignKey('Categoria', null=True, blank=True, on_delete=models.SET_NULL)
    categoria_manual = models.BooleanField(default=False)
    cuota = models.CharField(max_length=10, blank=True)
    fecha_compra = models.DateField(null=True, blank=True)
    titular = models.CharField(max_length=100, blank=True)
    ref = models.CharField(max_length=30, blank=True, db_index=True)  # norm_ref(codigo)
    comercio = models.CharField(max_length=120, blank=True, db_index=True)  # payee_key
    archivo = models.ForeignKey('DataFile', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=['fuente', 'moneda', 'monto'])]

    def __str__(self):
        return str(self.descripcion)


class Tarjeta(TimeStampedModel):
    nombre = models.CharField('Descripción', max_length=200)
    codigo = models.CharField('Código', max_length=200)
    fuente = models.CharField(max_length=10, unique=True)  # "visa" | "amex" | "master"
    patron_pago = models.CharField(max_length=200)  # regex (re.I) matched against account descripcion

    def __str__(self):
        return self.nombre


class Resumen(TimeStampedModel):
    fuente = models.CharField(max_length=10)
    desde = models.DateField(null=True, blank=True)
    hasta = models.DateField()
    vencimiento = models.DateField(null=True, blank=True)
    totales = models.JSONField(default=dict)
    archivo = models.ForeignKey('DataFile', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        unique_together = [('fuente', 'hasta')]


class Regla(TimeStampedModel):
    patron = models.CharField(max_length=200)
    es_regex = models.BooleanField(default=False)  # False: case-insensitive "contains"
    fuente = models.CharField(max_length=10, blank=True)  # "" = any; "tarjeta" = any card
    moneda = models.CharField(max_length=3, blank=True)
    prioridad = models.IntegerField(default=100)  # lower runs first
    categoria = models.ForeignKey('Categoria', null=True, blank=True, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=8, blank=True)  # "" = keep default kind


class Recurrente(TimeStampedModel):
    ESTADOS = [("sugerido", "Sugerido"), ("confirmado", "Confirmado"), ("silenciado", "Silenciado")]

    nombre = models.CharField(max_length=120)
    clave = models.CharField(max_length=120, blank=True)
    patron = models.CharField(max_length=200, blank=True)  # manual items: contains on descripcion
    moneda = models.CharField(max_length=3)
    fuente = models.CharField(max_length=10)
    frecuencia_meses = models.PositiveSmallIntegerField(default=1)
    dia_habitual = models.PositiveSmallIntegerField(default=1)
    monto_ref = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ultima_fecha = models.DateField(null=True, blank=True)
    estado_admin = models.CharField(max_length=10, choices=ESTADOS, default="sugerido")
    manual = models.BooleanField(default=False)
    nombre_editado = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['clave', 'moneda'], condition=~models.Q(clave=''), name='recurrente_clave_moneda_uniq')]


class DataFile(models.Model):
    data = models.FileField()

    # def save(self, *args, **kwargs):
    #     super(DataFile, self).save(*args, **kwargs)
    #     filename = self.data.path
    #     # get_movimientos(filename)

    def __str__(self):
        return str(self.data.url)


class Categoria(MPTTModel):
    name = models.CharField(max_length=50, unique=True)
    search_str = models.CharField(max_length=100, null=True, blank=True)
    parent = TreeForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')

    class MPTTMeta:
        order_insertion_by = ['name']

    def __str__(self):
        return str(self.name)
