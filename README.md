# bcoadmin

pip install -r requirements.txt

./manage migrate

./manage runserver

http://localhost:8000

## opcional

./manage createsuperuser

como user root

./manage runserver 127.0.0.1:80

http://localhost

## Primer uso

```
./manage.py migrate
./manage.py importar <archivos>
```

Las categorías y sus reglas de clasificación se administran desde la app, en
"Categorías → Gestionar categorías" (`/categorias/gestionar/`): la base de datos es la
fuente de verdad y cada cambio reclasifica los movimientos (salvo los asignados a mano).

`./manage.py seed_reglas` es opcional: importa una única vez la configuración vieja
(`CATEGORIAS_GASTOS`, `TARJETAS`, `IGNORAR`, `INTERNOS` de los settings). Después de eso
los settings ya no se usan para clasificar.

Los patrones de pago de tarjeta (`Tarjeta.patron_pago`) se cargan una vez con `seed_reglas` o desde el admin.

`importar` acepta los PDF mensuales de la cuenta, `movimientos.xlsx` y los
"Último resumen" de las tarjetas; se pueden repetir sin duplicar movimientos.

Los pagos recurrentes se detectan solos. Los que no interesan se silencian desde
el admin (acción "Silenciar"); no los borres: un recurrente borrado vuelve a
aparecer en la próxima importación.
