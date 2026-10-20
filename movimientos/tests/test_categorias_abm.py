from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from movimientos.models import Categoria, Detalle, Regla
from movimientos.services.classify import CAT_SIN_DETALLE


def det(desc, monto="-10", categoria=None, manual=False):
    return Detalle.objects.create(codigo="", fecha=datetime(2026, 9, 1), cuenta="c", descripcion=desc,
                                  monto=Decimal(monto), tipo="gasto", categoria=categoria,
                                  categoria_manual=manual)


class AbmBase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        self.servicios = Categoria.objects.create(name="Servicios")
        self.luz = Categoria.objects.create(name="Luz", parent=self.servicios)
        self.auto = Categoria.objects.create(name="Auto")
        self.reservada = Categoria.objects.create(name=CAT_SIN_DETALLE)

    def post(self, name, data=None, *args):
        return self.client.post(reverse(name, args=args), data or {}, follow=True)


class ListaTests(AbmBase):
    def test_lists_tree_rules_and_counts(self):
        Regla.objects.create(patron="edesur", categoria=self.luz, fuente="cuenta", moneda="ARS", prioridad=7)
        det("EDESUR", categoria=self.luz)
        det("EDESUR 2", categoria=self.luz)
        r = self.client.get(reverse("categorias_gestionar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Servicios")
        self.assertContains(r, "edesur")
        items = {i["cat"].name: i for i in r.context["items"]}
        self.assertEqual(items["Luz"]["movimientos"], 2)
        self.assertEqual(items["Auto"]["movimientos"], 0)
        self.assertEqual([i["cat"].name for i in r.context["items"]],
                         ["Auto", "Servicios", "Luz", CAT_SIN_DETALLE])
        self.assertTrue(items[CAT_SIN_DETALLE]["reservada"])

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("categorias_gestionar")).status_code, 302)


class CategoriaTests(AbmBase):
    def test_create_with_parent(self):
        r = self.post("categoria_nueva", {"name": "Agua", "parent": self.servicios.pk})
        self.assertRedirects(r, reverse("categorias_gestionar"))
        self.assertContains(r, "Reclasificados 0 movimientos")
        self.assertEqual(Categoria.objects.get(name="Agua").parent, self.servicios)

    def test_create_root(self):
        self.post("categoria_nueva", {"name": "Salud", "parent": ""})
        self.assertIsNone(Categoria.objects.get(name="Salud").parent)

    def test_duplicate_name_shows_error(self):
        r = self.post("categoria_nueva", {"name": "Auto", "parent": ""})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ya existe una categoría con ese nombre")
        self.assertEqual(Categoria.objects.filter(name="Auto").count(), 1)

    def test_rename_and_move(self):
        r = self.post("categoria_editar", {"name": "Electricidad", "parent": self.auto.pk}, self.luz.pk)
        self.assertContains(r, "Reclasificados")
        self.luz.refresh_from_db()
        self.assertEqual((self.luz.name, self.luz.parent), ("Electricidad", self.auto))

    def test_refuse_cycle(self):
        for parent in (self.luz.pk, self.servicios.pk):
            r = self.post("categoria_editar", {"name": "Servicios", "parent": parent}, self.servicios.pk)
            self.assertContains(r, "No se puede mover una categoría dentro de sí misma")
        self.servicios.refresh_from_db()
        self.assertIsNone(self.servicios.parent)

    def test_refuse_rename_reserved(self):
        r = self.post("categoria_editar", {"name": "Otra", "parent": ""}, self.reservada.pk)
        self.assertContains(r, "es una categoría del sistema")
        self.reservada.refresh_from_db()
        self.assertEqual(self.reservada.name, CAT_SIN_DETALLE)

    def test_delete_leaf_cleans_rules_and_movements(self):
        Regla.objects.create(patron="nafta", categoria=self.auto)
        manual = det("YPF", categoria=self.auto, manual=True)
        r = self.post("categoria_borrar", None, self.auto.pk)
        self.assertRedirects(r, reverse("categorias_gestionar"))
        self.assertContains(r, "Reclasificados")
        self.assertFalse(Categoria.objects.filter(pk=self.auto.pk).exists())
        self.assertFalse(Regla.objects.exists())
        manual.refresh_from_db()
        self.assertEqual((manual.categoria, manual.categoria_manual), (None, False))

    def test_refuse_delete_with_children(self):
        r = self.post("categoria_borrar", None, self.servicios.pk)
        self.assertContains(r, "tiene subcategorías")
        self.assertTrue(Categoria.objects.filter(pk=self.servicios.pk).exists())

    def test_refuse_delete_reserved(self):
        r = self.post("categoria_borrar", None, self.reservada.pk)
        self.assertContains(r, "es una categoría del sistema")
        self.assertTrue(Categoria.objects.filter(pk=self.reservada.pk).exists())

    def test_delete_requires_post(self):
        self.assertEqual(self.client.get(reverse("categoria_borrar", args=[self.auto.pk])).status_code, 405)


class ReglaTests(AbmBase):
    def regla_data(self, **kw):
        data = {"patron": "coto", "es_regex": "", "fuente": "", "moneda": "", "prioridad": 100, "tipo": ""}
        data.update(kw)
        return data

    def test_add_rule_reclassifies_end_to_end(self):
        coto = det("COTO SUC 12")
        otro = det("OTRO")
        r = self.post("regla_nueva", self.regla_data(), self.auto.pk)
        self.assertRedirects(r, reverse("categorias_gestionar"))
        self.assertContains(r, "Reclasificados 2 movimientos")  # comercio filled on both
        coto.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual((coto.categoria, otro.categoria), (self.auto, None))
        regla = Regla.objects.get()
        self.assertEqual((regla.categoria, regla.prioridad, regla.es_regex), (self.auto, 100, False))

    def test_rules_allowed_on_reserved(self):
        self.post("regla_nueva", self.regla_data(patron="pago x"), self.reservada.pk)
        self.assertEqual(Regla.objects.get().categoria, self.reservada)

    def test_invalid_regex_rejected(self):
        r = self.post("regla_nueva", self.regla_data(patron="(abc", es_regex="on"), self.auto.pk)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Expresión regular inválida")
        self.assertFalse(Regla.objects.exists())

    def test_blank_pattern_and_bad_choices_rejected(self):
        self.post("regla_nueva", self.regla_data(patron="   "), self.auto.pk)
        self.post("regla_nueva", self.regla_data(fuente="banco"), self.auto.pk)
        self.post("regla_nueva", self.regla_data(moneda="EUR"), self.auto.pk)
        self.post("regla_nueva", self.regla_data(tipo="otro"), self.auto.pk)
        self.assertFalse(Regla.objects.exists())

    def test_edit_rule(self):
        regla = Regla.objects.create(patron="coto", categoria=self.auto)
        det("JUMBO")
        r = self.post("regla_editar", self.regla_data(patron="^jum", es_regex="on", fuente="cuenta",
                                                      moneda="ARS", prioridad=5, tipo="gasto"), regla.pk)
        self.assertContains(r, "Reclasificados")
        regla.refresh_from_db()
        self.assertEqual((regla.patron, regla.es_regex, regla.fuente, regla.moneda, regla.prioridad, regla.tipo),
                         ("^jum", True, "cuenta", "ARS", 5, "gasto"))
        self.assertEqual(Detalle.objects.get(descripcion="JUMBO").categoria, self.auto)

    def test_delete_rule(self):
        regla = Regla.objects.create(patron="coto", categoria=self.auto)
        d = det("COTO", categoria=self.auto)
        r = self.post("regla_borrar", None, regla.pk)
        self.assertContains(r, "Reclasificados")
        self.assertFalse(Regla.objects.exists())
        d.refresh_from_db()
        self.assertIsNone(d.categoria)
