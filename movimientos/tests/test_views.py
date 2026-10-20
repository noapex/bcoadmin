from django.test import TestCase


class LoginRequiredTests(TestCase):
    def test_dashboard_redirects_anonymous_to_login(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)
