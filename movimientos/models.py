# -*- coding: utf-8 -*-
from django.db import models
from .xls_parser import get_movimientos


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
    codigo = models.CharField('Código', max_length=20)
    fecha = models.DateTimeField()
    cuenta = models.CharField('Cuenta', max_length=50)
    descripcion = models.CharField('Descripción', max_length=200)
    monto = models.FloatField(null=True, blank=True)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return str(self.descripcion)


class MovimientoIgnorado(TimeStampedModel):
    descripcion = models.CharField('Descripción', max_length=200)
    string = models.CharField('String', max_length=200)
    regex = models.BooleanField(default=True)

    def __str__(self):
        return str(self.descripcion)


class Tarjeta(TimeStampedModel):
    nombre = models.CharField('Descripción', max_length=200)
    codigo = models.CharField('Código', max_length=200)

    def __str__(self):
        return str(self.descripcion)




class DataFile(models.Model):
    data = models.FileField()

    # def save(self, *args, **kwargs):
    #     super(DataFile, self).save(*args, **kwargs)
    #     filename = self.data.path
    #     # get_movimientos(filename)

    def __str__(self):
        return str(self.data.url)

from mptt.models import MPTTModel, TreeForeignKey

class Categoria(MPTTModel):
    name = models.CharField(max_length=50, unique=True)
    search_str = models.CharField(max_length=100, null=True, blank=True)
    parent = TreeForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')

    class MPTTMeta:
        order_insertion_by = ['name']

    def __str__(self):
        return str(self.name)
