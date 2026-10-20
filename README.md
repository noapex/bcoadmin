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
./manage.py seed_reglas
./manage.py importar <archivos>
```

`importar` acepta los PDF mensuales de la cuenta, `movimientos.xlsx` y los
"Último resumen" de las tarjetas; se pueden repetir sin duplicar movimientos.

Los pagos recurrentes se detectan solos. Los que no interesan se silencian desde
el admin (acción "Silenciar"); no los borres: un recurrente borrado vuelve a
aparecer en la próxima importación.
