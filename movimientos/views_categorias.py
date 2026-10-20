"""In-app management (ABM) of categories and their classification rules."""
from collections import defaultdict

from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import CategoriaForm, ReglaForm
from .models import Categoria, Detalle, Regla
from .services.classify import CATEGORIAS_RESERVADAS, clasificar_todo


def _reclasificar(request, hecho):
    n = clasificar_todo()
    messages.success(request, f"{hecho}. Reclasificados {n} movimientos.")
    return redirect("categorias_gestionar")


def _rechazar(request, mensaje):
    messages.error(request, mensaje)
    return redirect("categorias_gestionar")


def categorias_gestionar(request):
    reglas = defaultdict(list)
    for regla in Regla.objects.order_by("prioridad", "id"):
        reglas[regla.categoria_id].append(regla)
    cuenta = dict(Detalle.objects.filter(categoria__isnull=False)
                  .values_list("categoria").annotate(n=Count("id")).order_by())
    items = [{"cat": c, "reglas": reglas[c.pk], "movimientos": cuenta.get(c.pk, 0),
              "reservada": c.name in CATEGORIAS_RESERVADAS}
             for c in Categoria.objects.order_by("tree_id", "lft")]
    return render(request, "movimientos/categorias_gestionar.html",
                  {"items": items, "reglas_sin_categoria": reglas[None]})


def _categoria_form(request, cat):
    initial = {} if cat else {"parent": request.GET.get("parent")}
    form = CategoriaForm(request.POST or None, instance=cat, initial=initial)
    if request.method == "POST" and form.is_valid():
        form.save()
        return _reclasificar(request, f"Categoría “{form.instance.name}” guardada")
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
    with transaction.atomic():
        Regla.objects.filter(categoria=cat).delete()
        # Manual assignments to a deleted category go back to the rules.
        Detalle.objects.filter(categoria=cat).update(categoria=None, categoria_manual=False)
        cat.delete()
    return _reclasificar(request, f"Categoría “{cat.name}” borrada")


def _regla_form(request, regla, cat):
    form = ReglaForm(request.POST or None, instance=regla)
    if request.method == "POST" and form.is_valid():
        nueva = form.save(commit=False)
        if regla is None:
            nueva.categoria = cat
        nueva.save()
        return _reclasificar(request, f"Regla “{nueva.patron}” guardada")
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
    regla.delete()
    return _reclasificar(request, f"Regla “{regla.patron}” borrada")
