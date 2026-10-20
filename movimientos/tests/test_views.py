from django.test import TestCase


class LoginRequiredTests(TestCase):
    def test_dashboard_redirects_anonymous_to_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)


import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from movimientos.models import DataFile, Detalle
from movimientos.tests.factories import make_account_xlsx


class UploadViewTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))

    def _xlsx_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            return SimpleUploadedFile("cuenta.xlsx", make_account_xlsx(tmp).read_bytes())

    def test_get_shows_form(self):
        r = self.client.get(reverse("upload"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'type="file"')

    def test_post_valid_and_unrecognised(self):
        good = self._xlsx_upload()
        bad = SimpleUploadedFile("notes.txt", b"nothing")
        r = self.client.post(reverse("upload"), {"file_field": [good, bad]})
        self.assertEqual(r.status_code, 200)
        reports = r.context["reports"]
        self.assertEqual([x.archivo for x in reports], ["cuenta.xlsx", "notes.txt"])
        self.assertGreater(reports[0].nuevos, 0)
        self.assertContains(r, "Formato no reconocido")
        self.assertContains(r, "cuenta.xlsx")
        self.assertEqual(Detalle.objects.count(), reports[0].nuevos)
        self.assertEqual(DataFile.objects.count(), 1)

    def test_post_empty_shows_message(self):
        r = self.client.post(reverse("upload"), {})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Seleccioná al menos un archivo")
        self.assertEqual(DataFile.objects.count(), 0)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("upload")).status_code, 302)


class SyncViewTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(get_user_model().objects.create_user("u", password="p"))

    def test_sync_imports_downloaded_file(self):
        def fake_download(dest):
            make_account_xlsx(dest)
        with mock.patch("movimientos.views.download_movs", side_effect=fake_download):
            r = self.client.get(reverse("sync"))
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.context["reports"][0].nuevos, 0)
        self.assertGreater(Detalle.objects.count(), 0)

    def test_sync_without_download_reports_error(self):
        with mock.patch("movimientos.views.download_movs"):
            r = self.client.get(reverse("sync"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No se descargó el archivo")
