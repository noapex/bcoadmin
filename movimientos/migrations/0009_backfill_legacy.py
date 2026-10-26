from django.db import migrations

from movimientos.parsers.base import norm_ref


def backfill(apps, schema_editor):
    Detalle = apps.get_model("movimientos", "Detalle")
    # Explicit alias: the demo router would otherwise send these queries to the default database.
    for d in Detalle.objects.using(schema_editor.connection.alias).all().iterator():
        d.fuente = "cuenta"
        d.moneda = "ARS"
        d.ref = norm_ref(d.codigo)
        d.tipo = "ingreso" if (d.monto or 0) > 0 else "gasto"
        d.save(update_fields=["fuente", "moneda", "ref", "tipo"])


class Migration(migrations.Migration):

    dependencies = [
        ("movimientos", "0008_dashboard_fields"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
