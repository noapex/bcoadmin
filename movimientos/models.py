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

    def __unicode__(self):
        return str(self.descripcion)

class DataFile(models.Model):
    data = models.FileField()

    # def save(self, *args, **kwargs):
    #     super(DataFile, self).save(*args, **kwargs)
    #     filename = self.data.path
    #     # get_movimientos(filename)

    def __unicode__(self):
        return str(self.data.url)
