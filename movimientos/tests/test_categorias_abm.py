from datetime import datetime
from decimal import Decimal

from unittest import mock

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
        self.assertContains(r, "0 movimientos actualizados")
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
        self.assertContains(r, "movimientos actualizados")
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
        det("YPF 2", categoria=self.auto, manual=True)
        det("YPF 3", categoria=self.auto)
        lista = self.client.get(reverse("categorias_gestionar"))
        self.assertContains(lista, "2 movimientos asignados a mano vuelven a las reglas")
        r = self.post("categoria_borrar", None, self.auto.pk)
        self.assertContains(r, "2 movimientos asignados a mano vuelven a las reglas")
        self.assertRedirects(r, reverse("categorias_gestionar"))
        self.assertContains(r, "movimientos actualizados")
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
        self.assertContains(r, "movimientos actualizados")
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
        self.assertContains(r, "movimientos actualizados")
        regla.refresh_from_db()
        self.assertEqual((regla.patron, regla.es_regex, regla.fuente, regla.moneda, regla.prioridad, regla.tipo),
                         ("^jum", True, "cuenta", "ARS", 5, "gasto"))
        self.assertEqual(Detalle.objects.get(descripcion="JUMBO").categoria, self.auto)

    def test_delete_rule(self):
        regla = Regla.objects.create(patron="coto", categoria=self.auto)
        d = det("COTO", categoria=self.auto)
        r = self.post("regla_borrar", None, regla.pk)
        self.assertContains(r, "movimientos actualizados")
        self.assertFalse(Regla.objects.exists())
        d.refresh_from_db()
        self.assertIsNone(d.categoria)


class ReclasificacionFallidaTests(AbmBase):
    """A failing reclassification rolls the whole change back and shows an error, not a 500."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch("movimientos.views_categorias.clasificar_todo", side_effect=RuntimeError("boom"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, *args):
        with self.assertLogs("movimientos.views_categorias", "ERROR"):
            return super().post(*args)

    def assert_error(self, r):
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No se aplicó ningún cambio")

    def test_create_category_rolled_back(self):
        self.assert_error(self.post("categoria_nueva", {"name": "Salud", "parent": ""}))
        self.assertFalse(Categoria.objects.filter(name="Salud").exists())

    def test_rename_rolled_back(self):
        self.assert_error(self.post("categoria_editar", {"name": "Coche", "parent": ""}, self.auto.pk))
        self.auto.refresh_from_db()
        self.assertEqual(self.auto.name, "Auto")

    def test_delete_category_rolled_back(self):
        Regla.objects.create(patron="nafta", categoria=self.auto)
        d = det("YPF", categoria=self.auto, manual=True)
        self.assert_error(self.post("categoria_borrar", None, self.auto.pk))
        self.assertTrue(Categoria.objects.filter(pk=self.auto.pk).exists())
        self.assertEqual(Regla.objects.count(), 1)
        d.refresh_from_db()
        self.assertEqual((d.categoria, d.categoria_manual), (self.auto, True))

    def test_rule_changes_rolled_back(self):
        data = {"patron": "coto", "es_regex": "", "fuente": "", "moneda": "", "prioridad": 100, "tipo": ""}
        self.assert_error(self.post("regla_nueva", data, self.auto.pk))
        self.assertFalse(Regla.objects.exists())
        regla = Regla.objects.create(patron="coto", categoria=self.auto)
        self.assert_error(self.post("regla_editar", dict(data, patron="jumbo"), regla.pk))
        regla.refresh_from_db()
        self.assertEqual(regla.patron, "coto")
        self.assert_error(self.post("regla_borrar", None, regla.pk))
        self.assertTrue(Regla.objects.filter(pk=regla.pk).exists())


class SugiereRecurrentesTests(AbmBase):
    def test_checkbox_saves_flag_and_list_shows_it(self):
        r = self.post("categoria_editar", {"name": "Auto", "parent": ""}, self.auto.pk)
        self.assertRedirects(r, reverse("categorias_gestionar"))
        self.auto.refresh_from_db()
        self.assertFalse(self.auto.sugiere_recurrentes)  # unticked checkbox
        self.assertContains(r, "sin recurrentes", count=1)
        self.post("categoria_editar", {"name": "Auto", "parent": "", "sugiere_recurrentes": "on"}, self.auto.pk)
        self.auto.refresh_from_db()
        self.assertTrue(self.auto.sugiere_recurrentes)
        form = self.client.get(reverse("categoria_editar", args=[self.auto.pk]))
        self.assertContains(form, "Puede tener recurrentes")

    def test_unticking_removes_suggestion(self):
        from movimientos.models import Recurrente
        from movimientos.services.recurring import detectar_recurrentes
        Regla.objects.create(patron="edesur", categoria=self.luz)
        for m in (5, 6, 7, 8, 9):
            Detalle.objects.create(codigo="", fecha=datetime(2026, m, 17), cuenta="c", descripcion="EDESUR",
                                   monto=Decimal("-100"), tipo="gasto")
        self.post("categoria_editar", {"name": "Luz", "parent": self.servicios.pk,
                                       "sugiere_recurrentes": "on"}, self.luz.pk)
        self.assertEqual(Recurrente.objects.count(), 1)
        # Unticking the parent removes the suggestion of a movement in a child category.
        self.post("categoria_editar", {"name": "Servicios", "parent": ""}, self.servicios.pk)
        self.assertEqual(Recurrente.objects.count(), 0)
        self.assertEqual(detectar_recurrentes(), 0)


class MigracionCategoriasTests(TestCase):
    def test_data_migration_unflags_names_case_insensitive(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module("movimientos.migrations.0012_categorias_sin_recurrentes")
        nombres = ["IMPUESTOS", "Compras con débito", "extracciones", "Transf", "Movimientos internos",
                   "Pagos de tarjeta", "Tarjeta sin detalle", "Servicios", "Transferencias"]
        for n in nombres:
            Categoria.objects.create(name=n)
        mig.desmarcar_categorias(apps, None)
        self.assertEqual(sorted(Categoria.objects.filter(sugiere_recurrentes=True).values_list("name", flat=True)),
                         ["Servicios", "Transferencias"])
        self.assertEqual(Categoria.objects.count(), len(nombres))
