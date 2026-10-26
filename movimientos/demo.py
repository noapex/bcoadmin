"""Demo mode: the movimientos app reads and writes a separate, synthetic database for one request.

The active database lives in a ContextVar set by DemoMiddleware from the session and always
reset at the end of the request, so it never leaks between requests or threads.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.db import connections
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

DEMO_DB = "demo"
DEFAULT_DB = "default"
APP_LABEL = "movimientos"
SESSION_KEY = "demo"

_db = ContextVar("movimientos_db", default=DEFAULT_DB)


def db_actual() -> str:
    """Database alias the movimientos app uses right now."""
    return _db.get()


def demo_activo() -> bool:
    return _db.get() == DEMO_DB


@contextmanager
def usar_demo():
    """Route the movimientos app to the demo database inside the block (commands, tests)."""
    token = _db.set(DEMO_DB)
    try:
        yield
    finally:
        _db.reset(token)


def demo_disponible() -> bool:
    """True once `generar_demo` has created the tables and some movements in the demo database."""
    from .models import Detalle

    if Detalle._meta.db_table not in connections[DEMO_DB].introspection.table_names():
        return False
    return Detalle.objects.using(DEMO_DB).exists()


def en_demo(request) -> bool:
    """Demo mode flag of the request's session (authenticated users only)."""
    user = getattr(request, "user", None)
    session = getattr(request, "session", None)
    return bool(user is not None and user.is_authenticated and session is not None and session.get(SESSION_KEY))


class DemoMiddleware:
    """Must run after SessionMiddleware (and AuthenticationMiddleware)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        activo = en_demo(request)
        token = _db.set(DEMO_DB if activo else DEFAULT_DB)
        try:
            if not activo:
                return self.get_response(request)
            # Uploaded statements in MEDIA_ROOT are real data.
            if settings.MEDIA_URL and request.path.startswith(settings.MEDIA_URL):
                raise Http404
            exento = request.path == reverse("demo_toggle") or request.path.startswith(settings.STATIC_URL)
            if not exento and not demo_disponible():
                return render(request, "movimientos/demo_vacio.html")
            return self.get_response(request)
        finally:
            _db.reset(token)


def contexto(request):
    """Template context processor: `demo` is True while the request runs in demo mode."""
    return {"demo": demo_activo()}
