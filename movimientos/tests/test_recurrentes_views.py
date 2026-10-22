from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from movimientos.models import Detalle, Recurrente


def rec(nombre, estado="sugerido", moneda="ARS", **kw):
    kw.setdefault("ultima_fecha", date(2026, 8, 1))
    kw.setdefault("fuente", "cuenta")
    return Recurrente.objects.create(nombre=nombre, clave=nombre.lower(), moneda=moneda,
                                     dia_habitual=1, monto_ref=Decimal("1234.5"), estado_admin=estado, **kw)


class Base(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))
        Detalle.objects.create(codigo="", fecha=datetime(2026, 9, 20), cuenta="c", descripcion="X",
                               monto=Decimal("-10"), tipo="gasto")


class DashboardOnlyConfirmedTests(Base):
    def test_panel_and_kpi_use_only_confirmed(self):
        rec("Netflix", "confirmado")
        rec("Carniceria", "sugerido")
        rec("Sellos", "silenciado")
        r = self.client.get("/")
        self.assertEqual([i["rec"].nombre for i in r.context["recurrentes"]], ["Netflix"])
        self.assertEqual(r.context["kpis"]["faltan"], 1)
        self.assertNotContains(r, "Carniceria")
        self.assertContains(r, "1 sugerencia")
        self.assertContains(r, reverse("recurrentes"))

    def test_empty_state_cta_with_suggestions(self):
        rec("A", "sugerido")
        rec("B", "sugerido", moneda="USD")
        r = self.client.get("/")
        self.assertEqual(r.context["recurrentes"], [])
        self.assertEqual(r.context["kpis"]["faltan"], 0)
        self.assertContains(r, "Revisá 2 sugerencias")

    def test_nav_link(self):
        r = self.client.get("/")
        self.assertContains(r, f'href="{reverse("recurrentes")}"')


class RecurrentesPageTests(Base):
    def test_lists_three_sections(self):
        s1 = rec("Sug baja", "sugerido", puntaje=10)
        s2 = rec("Sug alta", "sugerido", puntaje=80, frecuencia_meses=2)
        rec("Monitoreado uno", "confirmado")
        rec("Ignorado uno", "silenciado")
        r = self.client.get(reverse("recurrentes"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i["rec"] for i in r.context["sugeridos"]], [s2, s1])
        self.assertEqual([i["rec"].nombre for i in r.context["monitoreados"]], ["Monitoreado uno"])
        self.assertEqual([x.nombre for x in r.context["ignorados"]], ["Ignorado uno"])
        self.assertContains(r, "bimestral")
        self.assertContains(r, "1.234,50")
        self.assertContains(r, f"/movimientos/?moneda=ARS&amp;recurrente={s2.pk}")
        self.assertContains(r, "<details")
        self.assertContains(r, "Falta")  # current estado pill for the monitored item

    def test_empty_states(self):
        r = self.client.get(reverse("recurrentes"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No hay sugerencias")

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("recurrentes")).status_code, 302)


class EstadoActionsTests(Base):
    CASOS = [("monitorear", "sugerido", "confirmado"), ("ignorar", "sugerido", "silenciado"),
             ("dejar", "confirmado", "silenciado"), ("restaurar", "silenciado", "sugerido")]

    def test_actions_change_only_estado_admin(self):
        for accion, desde, hasta in self.CASOS:
            r0 = rec(f"Item {accion}", desde, frecuencia_meses=3, nombre_editado=True)
            antes = Recurrente.objects.filter(pk=r0.pk).values().get()
            url = reverse("recurrente_estado", args=[r0.pk, accion])
            resp = self.client.post(url, follow=True)
            self.assertRedirects(resp, reverse("recurrentes"))
            despues = Recurrente.objects.filter(pk=r0.pk).values().get()
            self.assertEqual(despues.pop("estado_admin"), hasta, accion)
            antes.pop("estado_admin")
            antes.pop("modified"), despues.pop("modified")
            self.assertEqual(antes, despues, accion)
            self.assertEqual(len(list(resp.context["messages"])), 1)

    def test_get_not_allowed(self):
        r0 = rec("X")
        self.assertEqual(self.client.get(reverse("recurrente_estado", args=[r0.pk, "monitorear"])).status_code, 405)
        self.assertEqual(self.client.get(reverse("recurrentes_ignorar_todas")).status_code, 405)

    def test_csrf_required(self):
        r0 = rec("X")
        c = Client(enforce_csrf_checks=True)
        c.force_login(get_user_model().objects.get())
        self.assertEqual(c.post(reverse("recurrente_estado", args=[r0.pk, "monitorear"])).status_code, 403)
        self.assertEqual(c.post(reverse("recurrentes_ignorar_todas")).status_code, 403)
        r0.refresh_from_db()
        self.assertEqual(r0.estado_admin, "sugerido")

    def test_unknown_action_404(self):
        r0 = rec("X")
        self.assertEqual(self.client.post(f"/recurrentes/{r0.pk}/borrar/").status_code, 404)

    def test_ignorar_todas(self):
        a, b = rec("A"), rec("B", moneda="USD")
        c = rec("C", "confirmado")
        r = self.client.post(reverse("recurrentes_ignorar_todas"), follow=True)
        self.assertRedirects(r, reverse("recurrentes"))
        self.assertContains(r, "2 sugerencias ignoradas")
        self.assertEqual(dict(Recurrente.objects.values_list("nombre", "estado_admin")),
                         {"A": "silenciado", "B": "silenciado", "C": "confirmado"})


class AdminManualConfirmedTests(TestCase):
    def test_admin_created_item_is_confirmed(self):
        self.client.force_login(get_user_model().objects.create_superuser("adm", "a@x.com", "pw"))
        data = {"nombre": "Gym", "clave": "", "patron": "GIMNASIO", "moneda": "ARS", "fuente": "cuenta",
                "frecuencia_meses": 1, "dia_habitual": 5, "estado_admin": "sugerido"}
        self.assertEqual(self.client.post(reverse("admin:movimientos_recurrente_add"), data).status_code, 302)
        r0 = Recurrente.objects.get()
        self.assertEqual((r0.manual, r0.estado_admin), (True, "confirmado"))


class QueryCountTests(Base):
    """Status needs the source's reference date: computed once per source, not per item."""

    def _queries(self, url, n):
        Recurrente.objects.all().delete()
        for i in range(n):
            # falta: past the tolerance window, so estado() needs no movement history
            rec(f"Mon {i}", "confirmado", fuente="visa" if i % 2 else "cuenta")
            rec(f"Ign {i}", "silenciado")
            rec(f"Sug {i}", "sugerido")
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_recurrentes_page_queries_constant(self):
        self.assertEqual(self._queries(reverse("recurrentes"), 2), self._queries(reverse("recurrentes"), 12))

    def test_dashboard_queries_constant(self):
        self.assertEqual(self._queries("/", 2), self._queries("/", 12))
