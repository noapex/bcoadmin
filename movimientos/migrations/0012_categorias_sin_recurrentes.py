from django.db import migrations

# Categories whose movements are never recurring obligations (taxes, transfers, POS purchases...).
# Descendants are covered by the ancestor rule, so only these rows are updated.
NOMBRES = ("impuestos", "compras con débito", "extracciones", "transf", "movimientos internos",
           "pagos de tarjeta", "tarjeta sin detalle")


def desmarcar_categorias(apps, schema_editor):
    # Explicit alias: the demo router would otherwise send these queries to the default database.
    Categoria = apps.get_model("movimientos", "Categoria").objects.using(schema_editor.connection.alias)
    ids = [pk for pk, name in Categoria.values_list("pk", "name") if name.strip().lower() in NOMBRES]
    Categoria.filter(pk__in=ids).update(sugiere_recurrentes=False)


class Migration(migrations.Migration):

    dependencies = [
        ('movimientos', '0011_recurrentes_puntaje_sugiere'),
    ]

    operations = [
        migrations.RunPython(desmarcar_categorias, migrations.RunPython.noop),
    ]
