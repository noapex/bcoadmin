# -*- coding: utf-8 -*-
# Generated by Django 1.10.1 on 2017-09-30 03:52
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('movimientos', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='detalle',
            name='codigo',
            field=models.CharField(max_length=20, verbose_name=b'C\xc3\xb3digo'),
        ),
        migrations.AlterField(
            model_name='detalle',
            name='descripcion',
            field=models.CharField(max_length=200, verbose_name=b'Descripci\xc3\xb3n'),
        ),
    ]
