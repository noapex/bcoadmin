# Generated by Django 2.1.7 on 2019-04-12 23:34

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('movimientos', '0005_categoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='categoria',
            name='regex',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
