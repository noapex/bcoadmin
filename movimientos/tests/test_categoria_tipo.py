"""Categories declare whether they apply to income, expenses or both; pickers follow the movement's sign."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from movimientos.forms import opciones_categoria
from movimientos.models import Categoria

from .test_editar import mov


class CategoriaTipoTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("u", password="p"))
        self.super = Categoria.objects.create(name="Supermercado")
        self.sueldo = Categoria.objects.create(name="Sueldo", tipo="ingreso")
        self.transf = Categoria.objects.create(name="Transf", tipo="ambas")

    def nombres(self, opciones):
        return [label.strip(" └ ") for _, label in opciones]

    def test_default_is_egreso(self):
        self.assertEqual(self.super.tipo, "egreso")

    def test_abm_create_defaults_to_egreso_and_saves_choice(self):
        self.client.post(reverse("categoria_nueva"), {"name": "Agua", "parent": ""})
        self.assertEqual(Categoria.objects.get(name="Agua").tipo, "egreso")
        self.client.post(reverse("categoria_nueva"), {"name": "Alquiler cobrado", "parent": "", "tipo": "ingreso"})
        self.assertEqual(Categoria.objects.get(name="Alquiler cobrado").tipo, "ingreso")

    def test_options_filtered_by_sign(self):
        self.assertEqual(self.nombres(opciones_categoria("egreso")), ["Supermercado", "Transf"])
        self.assertEqual(self.nombres(opciones_categoria("ingreso")), ["Sueldo", "Transf"])
        self.assertEqual(len(opciones_categoria()), 3)

    def test_options_keep_current_category(self):
        self.assertIn("Sueldo", self.nombres(opciones_categoria("egreso", incluir=self.sueldo.pk)))

    def test_edit_page_offers_categories_for_the_movement_sign(self):
        r = self.client.get(reverse("movimiento_editar", args=[mov(monto="-50").pk]))
        choices = dict(r.context["form"].fields["categoria"].choices)
        self.assertIn(str(self.super.pk), choices)
        self.assertNotIn(str(self.sueldo.pk), choices)
        r = self.client.get(reverse("movimiento_editar", args=[mov(monto="900").pk]))
        choices = dict(r.context["form"].fields["categoria"].choices)
        self.assertIn(str(self.sueldo.pk), choices)
        self.assertNotIn(str(self.super.pk), choices)

    def test_list_picker_options_carry_tipo(self):
        mov()
        r = self.client.get(reverse("movimientos"))
        self.assertContains(r, f'<option value="{self.sueldo.pk}" data-tipo="ingreso">')
