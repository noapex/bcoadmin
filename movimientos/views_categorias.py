"""In-app management (ABM) of categories and their classification rules."""
import logging
from collections import defaultdict

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import CategoriaForm, ReglaForm
from .models import Categoria, Detalle, Regla
from .services.classify import CATEGORIAS_RESERVADAS, clasificar_todo


logger = logging.getLogger(__name__)


def _aplicar(request, cambio, hecho):
    """Run `cambio()` and the reclassification in one transaction, flash the outcome, back to the list.

    `cambio` may return an extra sentence (or None) for the success message. Any failure rolls both back.
    """
    try:
        with transaction.atomic():
            extra = cambio()
            n = clasificar_todo()
    except Exception:
        logger.exception("Category/rule change rolled back")
        messages.error(request, "Falló la reclasificación de movimientos. No se aplicó ningún cambio.")
        return redirect("categorias_gestionar")
    messages.success(request, " ".join(filter(None, [f"{hecho}.", extra, f"{n} movimientos actualizados."])))
    return redirect("categorias_gestionar")


def _aviso_manuales(n):
    return f"{n} movimientos asignados a mano vuelven a las reglas." if n else ""


def _rechazar(request, mensaje):
    messages.error(request, mensaje)
    return redirect("categorias_gestionar")


def categorias_gestionar(request):
    reglas = defaultdict(list)
    for regla in Regla.objects.order_by("prioridad", "id"):
        reglas[regla.categoria_id].append(regla)
    cuenta, manuales = {}, {}
    for cid, n, m in (Detalle.objects.filter(categoria__isnull=False).values_list("categoria")
                      .annotate(n=Count("id"), m=Count("id", filter=Q(categoria_manual=True))).order_by()):
        cuenta[cid], manuales[cid] = n, m
    items = [{"cat": c, "reglas": reglas[c.pk], "movimientos": cuenta.get(c.pk, 0),
              "aviso_manuales": _aviso_manuales(manuales.get(c.pk, 0)),
              "reservada": c.name in CATEGORIAS_RESERVADAS}
             for c in Categoria.objects.order_by("tree_id", "lft")]
    return render(request, "movimientos/categorias_gestionar.html",
                  {"items": items, "reglas_sin_categoria": reglas[None]})


def _categoria_form(request, cat):
    initial = {} if cat else {"parent": request.GET.get("parent")}
    form = CategoriaForm(request.POST or None, instance=cat, initial=initial)
    if request.method == "POST" and form.is_valid():
        def guardar():
            form.save()

        return _aplicar(request, guardar, f"Categoría “{form.cleaned_data['name']}” guardada")
    titulo = f"Editar categoría “{cat.name}”" if cat else "Nueva categoría"
    return render(request, "movimientos/abm_form.html", {"form": form, "titulo": titulo})


def categoria_nueva(request):
    return _categoria_form(request, None)


def categoria_editar(request, pk):
    return _categoria_form(request, get_object_or_404(Categoria, pk=pk))


@require_POST
def categoria_borrar(request, pk):
    cat = get_object_or_404(Categoria, pk=pk)
    if cat.name in CATEGORIAS_RESERVADAS:
        return _rechazar(request, f"“{cat.name}” es una categoría del sistema: no se puede borrar.")
    if cat.get_children().exists():
        return _rechazar(request, f"“{cat.name}” tiene subcategorías: movelas o borralas primero.")
    def borrar():
        Regla.objects.filter(categoria=cat).delete()
        # Manual assignments to a deleted category go back to the rules; the owner is told how many.
        manuales = Detalle.objects.filter(categoria=cat, categoria_manual=True).count()
        Detalle.objects.filter(categoria=cat).update(categoria=None, categoria_manual=False)
        cat.delete()
        return _aviso_manuales(manuales)

    return _aplicar(request, borrar, f"Categoría “{cat.name}” borrada")


def _regla_form(request, regla, cat):
    form = ReglaForm(request.POST or None, instance=regla)
    if request.method == "POST" and form.is_valid():
        nueva = form.save(commit=False)
        if regla is None:
            nueva.categoria = cat

        def guardar():
            nueva.save()

        return _aplicar(request, guardar, f"Regla “{nueva.patron}” guardada")
    titulo = (f"Editar regla de “{cat.name}”" if regla else f"Nueva regla para “{cat.name}”") if cat \
        else "Editar regla"
    return render(request, "movimientos/abm_form.html", {"form": form, "titulo": titulo})


def regla_nueva(request, pk):
    return _regla_form(request, None, get_object_or_404(Categoria, pk=pk))


def regla_editar(request, pk):
    regla = get_object_or_404(Regla.objects.select_related("categoria"), pk=pk)
    return _regla_form(request, regla, regla.categoria)


@require_POST
def regla_borrar(request, pk):
    regla = get_object_or_404(Regla, pk=pk)

    def borrar():
        regla.delete()

    return _aplicar(request, borrar, f"Regla “{regla.patron}” borrada")
