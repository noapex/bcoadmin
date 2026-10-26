"""Routes the movimientos app to the demo database while demo mode is active (see demo.py).

Everything else (auth, sessions, admin log, contenttypes) always stays on the default database,
so login and sessions keep working with the owner's real user.
"""
from .demo import APP_LABEL, DEFAULT_DB, DEMO_DB, db_actual


class DemoRouter:
    def _db(self, model):
        return db_actual() if model._meta.app_label == APP_LABEL else DEFAULT_DB

    def db_for_read(self, model, **hints):
        return self._db(model)

    def db_for_write(self, model, **hints):
        return self._db(model)

    def allow_relation(self, obj1, obj2, **hints):
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == DEMO_DB:
            return app_label == APP_LABEL
        return True
