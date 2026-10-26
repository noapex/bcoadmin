"""Manual category assignment from the movements list and the simplified edit page."""
from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from movimientos.models import Categoria, Detalle, Regla


def mov(desc="COTO SUPER", monto="-100", categoria=None, manual=False, tipo="gasto", activo=True, db="default"):
    return Detalle.objects.using(db).create(
        codigo="", fecha=datetime(2026, 9, 10), cuenta="c", descripcion=desc, monto=Decimal(monto),
        tipo=tipo, categoria_id=categoria.pk if categoria else None, categoria_manual=manual, activo=activo)


class Base(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("u", password="p"))
        self.super = Categoria.objects.create(name="Supermercado")
        self.otra = Categoria.objects.create(name="Ocio")
        Regla.objects.create(patron="coto", categoria=self.super)
        self.d = mov(categoria=self.super)


class QuickAssignTests(Base):
    def url(self, pk=None):
        return reverse("movimiento_categoria", args=[pk or self.d.pk])

    def test_sets_category_and_manual(self):
        r = self.client.post(self.url(), {"categoria": self.otra.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "categoria": "Ocio", "manual": True, "tipo": "gasto"})
        self.d.refresh_from_db()
        self.assertEqual(self.d.categoria, self.otra)
        self.assertTrue(self.d.categoria_manual)
        self.assertEqual(self.d.tipo, "gasto")

    def test_auto_resets_manual_and_reclassifies(self):
        Detalle.objects.filter(pk=self.d.pk).update(categoria=self.otra, categoria_manual=True)
        r = self.client.post(self.url(), {"categoria": "auto"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual((data["categoria"], data["manual"]), ("Supermercado", False))
        self.d.refresh_from_db()
        self.assertEqual(self.d.categoria, self.super)
        self.assertFalse(self.d.categoria_manual)

    def test_auto_without_matching_rule(self):
        d = mov(desc="OTRA COSA", categoria=self.otra, manual=True)
        r = self.client.post(self.url(d.pk), {"categoria": "auto"})
        self.assertEqual(r.json()["categoria"], "Sin categoría")

    def test_none_pins_no_category(self):
        r = self.client.post(self.url(), {"categoria": "none"})
        self.assertEqual(r.json(), {"ok": True, "categoria": "Sin categoría", "manual": True, "tipo": "gasto"})
        self.d.refresh_from_db()
        self.assertEqual((self.d.categoria, self.d.categoria_manual), (None, True))

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    def test_unknown_pk_or_category_is_404(self):
        r = self.client.post(self.url(999999), {"categoria": self.otra.pk})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(self.client.post(self.url(), {"categoria": 999999}).status_code, 404)

    def test_invalid_input_is_400(self):
        for value in ("", "abc", "-1"):
            r = self.client.post(self.url(), {"categoria": value})
            self.assertEqual(r.status_code, 400, value)
            self.assertFalse(r.json()["ok"])
        self.d.refresh_from_db()
        self.assertFalse(self.d.categoria_manual)


class QuickAssignDemoTests(TestCase):
    databases = {"default", "demo"}

    def test_demo_writes_only_to_demo(self):
        self.client.force_login(get_user_model().objects.create_superuser("u", password="p"))
        s = self.client.session
        s["demo"] = True
        s.save()
        real = Categoria.objects.using("default").create(name="Real")
        mov(desc="REAL", categoria=real, db="default")
        cat = Categoria.objects.using("demo").create(name="DemoCat")
        d = mov(desc="DEMO", db="demo")
        r = self.client.post(reverse("movimiento_categoria", args=[d.pk]), {"categoria": cat.pk})
        self.assertEqual(r.status_code, 200)
        d = Detalle.objects.using("demo").get(pk=d.pk)
        self.assertEqual((d.categoria_id, d.categoria_manual), (cat.pk, True))
        self.assertFalse(Detalle.objects.using("default").filter(categoria_manual=True).exists())


class EditPageTests(Base):
    def url(self, nxt=None):
        u = reverse("movimiento_editar", args=[self.d.pk])
        return u + (f"?next={nxt}" if nxt else "")

    def post(self, data, nxt=None):
        base = {"categoria": self.d.categoria_id or "auto", "tipo": self.d.tipo}
        base.update(data)
        return self.client.post(self.url(nxt), base)

    def test_get_shows_fields_and_admin_link(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        for text in ("COTO SUPER", "-100,00", 'name="categoria"', 'name="tipo"', 'name="excluir"',
                     "— Automático (reglas) —", "Excluir de los totales", "Editor avanzado",
                     reverse("admin:movimientos_detalle_change", args=[self.d.pk])):
            self.assertContains(r, text)

    def test_change_category_marks_manual(self):
        r = self.post({"categoria": self.otra.pk})
        self.assertEqual(r.status_code, 302)
        self.d.refresh_from_db()
        self.assertEqual((self.d.categoria, self.d.categoria_manual), (self.otra, True))

    def test_change_tipo_marks_manual(self):
        self.post({"tipo": "interno"})
        self.d.refresh_from_db()
        self.assertEqual((self.d.tipo, self.d.categoria_manual), ("interno", True))

    def test_unchanged_save_keeps_rules(self):
        self.post({})
        self.d.refresh_from_db()
        self.assertFalse(self.d.categoria_manual)

    def test_manual_without_category_preselects_none_and_survives_save(self):
        Detalle.objects.filter(pk=self.d.pk).update(categoria=None, categoria_manual=True)
        self.d.refresh_from_db()
        r = self.client.get(self.url())
        self.assertContains(r, '<option value="none" selected>— Sin categoría (fijado a mano) —</option>', html=True)
        self.post({"categoria": "none"})
        self.d.refresh_from_db()
        self.assertEqual((self.d.categoria, self.d.categoria_manual), (None, True))

    def test_none_pins_rule_matched_row(self):
        self.post({"categoria": "none"})
        self.d.refresh_from_db()
        self.assertEqual((self.d.categoria, self.d.categoria_manual), (None, True))
        from movimientos.services.classify import clasificar_todo
        clasificar_todo()
        self.d.refresh_from_db()
        self.assertIsNone(self.d.categoria)

    def test_auto_resets_and_applies_rule(self):
        Detalle.objects.filter(pk=self.d.pk).update(categoria=self.otra, tipo="interno", categoria_manual=True)
        self.d.refresh_from_db()
        self.post({"categoria": "auto", "tipo": "ingreso"})
        self.d.refresh_from_db()
        self.assertEqual((self.d.categoria, self.d.tipo, self.d.categoria_manual), (self.super, "gasto", False))

    def test_excluir_sets_inactive(self):
        self.post({"excluir": "on"})
        self.d.refresh_from_db()
        self.assertFalse(self.d.activo)
        self.post({})
        self.d.refresh_from_db()
        self.assertTrue(self.d.activo)

    def test_redirects_to_safe_next_with_message(self):
        r = self.post({}, nxt="/movimientos/%3Fmes%3D2026-09%26tipo%3Dgasto")
        self.assertRedirects(r, "/movimientos/?mes=2026-09&tipo=gasto", fetch_redirect_response=False)
        r = self.client.get(r["Location"])
        self.assertContains(r, "Movimiento actualizado.")

    def test_unsafe_next_ignored(self):
        r = self.post({}, nxt="https://evil.example.com/")
        self.assertRedirects(r, "/movimientos/?moneda=ARS&mes=2026-09", fetch_redirect_response=False)

    def test_invalid_post_rerenders(self):
        r = self.post({"categoria": "999999"})
        self.assertEqual(r.status_code, 200)
        self.d.refresh_from_db()
        self.assertEqual(self.d.categoria, self.super)

    def test_unknown_pk_404(self):
        self.assertEqual(self.client.get(reverse("movimiento_editar", args=[999999])).status_code, 404)


class ListTests(Base):
    def test_pencil_links_to_edit_page_with_next(self):
        r = self.client.get("/movimientos/?mes=2026-09")
        self.assertContains(r, reverse("movimiento_editar", args=[self.d.pk]) + "?next=/movimientos/%3Fmes%3D2026-09")
        self.assertNotContains(r, reverse("admin:movimientos_detalle_change", args=[self.d.pk]))

    def test_single_template_select(self):
        mov(desc="OTRO")
        r = self.client.get("/movimientos/?mes=2026-09")
        self.assertContains(r, 'id="cat-picker"', count=1)
        self.assertContains(r, "— Automático (reglas) —", count=1)

    def test_manual_marker(self):
        r = self.client.get("/movimientos/?mes=2026-09")
        self.assertNotContains(r, 'class="manual-mark"')
        Detalle.objects.filter(pk=self.d.pk).update(categoria_manual=True)
        r = self.client.get("/movimientos/?mes=2026-09")
        self.assertContains(r, 'class="manual-mark"', count=1)

    def test_header_count_without_excluded(self):
        r = self.client.get("/movimientos/?mes=2026-09")
        self.assertContains(r, "1 movimiento ·")
        self.assertNotContains(r, " excluido")

    def test_inactive_rows_shown_but_not_totalled(self):
        mov(desc="EXCLUIDO", monto="-50", activo=False)
        mov(desc="EXCLUIDO 2", monto="-60", activo=False)
        r = self.client.get("/movimientos/?mes=2026-09")
        self.assertContains(r, "1 movimiento · 2 excluidos ·")
        self.assertContains(r, "EXCLUIDO")
        self.assertContains(r, ">excluido<")
        self.assertEqual(r.context["total"], Decimal("-100"))
