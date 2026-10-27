import re

from django import forms
from django.db.models import Q

from .models import Categoria, Detalle, Regla
from .services.classify import CATEGORIAS_RESERVADAS


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(d, initial) for d in data]
        return [single_file_clean(data, initial)]


class FileFieldForm(forms.Form):
    file_field = MultipleFileField()



class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["name", "parent", "tipo", "sugiere_recurrentes"]
        labels = {"name": "Nombre", "parent": "Categoría padre"}
        help_texts = {"sugiere_recurrentes": "Desmarcalo para impuestos, transferencias o compras sueltas: "
                                             "sus movimientos (y los de sus subcategorías) no se sugieren "
                                             "como pagos recurrentes."}
        error_messages = {"name": {"unique": "Ya existe una categoría con ese nombre."}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].required = False
        self.fields["parent"].empty_label = "(ninguna: categoría principal)"
        self.fields["tipo"].required = False

    def clean_tipo(self):
        return self.cleaned_data["tipo"] or "egreso"

    def clean_name(self):
        name = self.cleaned_data["name"]
        actual = self.instance.name if self.instance.pk else None
        if actual in CATEGORIAS_RESERVADAS and name != actual:
            raise forms.ValidationError(f"“{actual}” es una categoría del sistema: no se puede renombrar.")
        return name

    def clean_parent(self):
        parent = self.cleaned_data["parent"]
        if parent and self.instance.pk and parent.is_descendant_of(self.instance, include_self=True):
            raise forms.ValidationError(
                "No se puede mover una categoría dentro de sí misma ni de una de sus subcategorías.")
        return parent


class ReglaForm(forms.ModelForm):
    FUENTES = [("", "Cualquiera"), ("cuenta", "Cuenta"), ("galicia", "Galicia"),
               ("tarjeta", "Cualquier tarjeta"),
               ("visa", "Visa"), ("amex", "Amex"), ("master", "Master")]
    MONEDAS = [("", "Cualquiera"), ("ARS", "ARS"), ("USD", "USD")]
    TIPOS = [("", "Sin cambio"), ("ingreso", "Ingreso"), ("gasto", "Gasto"), ("interno", "Interno")]

    fuente = forms.ChoiceField(choices=FUENTES, required=False, label="Fuente")
    moneda = forms.ChoiceField(choices=MONEDAS, required=False, label="Moneda")
    tipo = forms.ChoiceField(choices=TIPOS, required=False, label="Tipo")

    class Meta:
        model = Regla
        fields = ["patron", "es_regex", "fuente", "moneda", "prioridad", "tipo"]
        labels = {"patron": "Patrón", "es_regex": "Es expresión regular",
                  "prioridad": "Prioridad (menor se evalúa primero)"}
        help_texts = {"patron": "Sin regex: texto contenido en la descripción (sin distinguir mayúsculas)."}

    def clean(self):
        data = super().clean()
        patron = data.get("patron")
        if data.get("es_regex") and patron:
            try:
                re.compile(patron, re.I)
            except re.error as exc:
                self.add_error("patron", f"Expresión regular inválida: {exc}")
        return data


AUTO = "auto"
AUTO_LABEL = "— Automático (reglas) —"
NONE = "none"  # no category, pinned by hand (rules won't touch it)
NONE_LABEL = "— Sin categoría (fijado a mano) —"


def _arbol(para=None, incluir=None):
    qs = Categoria.objects.order_by("tree_id", "lft")
    if para:
        qs = qs.filter(Q(tipo__in=(para, "ambas")) | Q(pk=incluir))
    return [(c, "\u00a0" * 3 * c.level + ("\u2514 " if c.level else "") + c.name) for c in qs]


def opciones_categoria(para=None, incluir=None):
    """[(pk, label)] in tree order, for category selects; children are indented with non-breaking
    spaces (plain ones collapse inside <option>) and a "└ " marker. `para` ("ingreso"/"egreso")
    keeps only categories for that sign, plus the `incluir` pk (the current one)."""
    return [(str(c.pk), label) for c, label in _arbol(para, incluir)]


def opciones_categoria_con_tipo():
    """[(pk, label, tipo)] for the list picker, which filters by each row's sign client-side."""
    return [(str(c.pk), label, c.tipo) for c, label in _arbol()]


class MovimientoForm(forms.Form):
    categoria = forms.ChoiceField(label="Categoría")
    tipo = forms.ChoiceField(choices=Detalle.TIPOS, required=False, label="Tipo",
                             help_text="Con “Automático”, la categoría y el tipo vuelven a definirlos las reglas.")
    excluir = forms.BooleanField(required=False, label="Excluir de los totales",
                                 help_text="El movimiento sigue en la lista, pero no suma en totales ni en el tablero.")

    def __init__(self, *args, para=None, incluir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].choices = ([(AUTO, AUTO_LABEL), (NONE, NONE_LABEL)]
                                            + opciones_categoria(para, incluir))

    def clean(self):
        data = super().clean()
        if data.get("categoria") != AUTO and not data.get("tipo"):
            self.add_error("tipo", "Elegí un tipo.")
        return data
