import jwt
from cryptography.hazmat.primitives import serialization
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.models import EdDSAKeyPair, EdDSAKeySettings


class EdDSAKeyAdminViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.key_settings = EdDSAKeySettings.for_site(self.site)
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.admin_user)

    def test_generate_view_creates_key_pair(self):
        response = self.client.post(
            reverse("govuk_eddsa_generate_site_key", args=[self.site.pk]),
            data={"next": "/admin/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")
        self.assertEqual(
            EdDSAKeyPair.objects.filter(settings=self.key_settings).count(),
            1,
        )

    def test_set_primary_view_updates_primary_key_pair(self):
        first_key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)
        second_key_pair = EdDSAKeyPair.generate_for_settings(
            settings_obj=self.key_settings
        )

        response = self.client.post(
            reverse("govuk_eddsa_set_primary_key", args=[second_key_pair.pk]),
            data={"next": "/admin/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")

        first_key_pair.refresh_from_db()
        second_key_pair.refresh_from_db()
        self.assertFalse(first_key_pair.is_primary)
        self.assertTrue(second_key_pair.is_primary)

    def test_delete_view_removes_key_pair(self):
        key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)

        response = self.client.post(
            reverse("govuk_eddsa_delete_key", args=[key_pair.pk]),
            data={"next": "/admin/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")
        self.assertFalse(EdDSAKeyPair.objects.filter(pk=key_pair.pk).exists())

    def test_private_key_value_is_not_shown_on_settings_page(self):
        key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)
        response = self.client.get(
            reverse(
                "wagtailsettings:edit",
                args=(
                    EdDSAKeySettings._meta.app_label,
                    "eddsakeysettings",
                    self.site.pk,
                ),
            )
        )

        self.assertEqual(response.status_code, 200)
        sensitive_private_key_line = key_pair.private_key.splitlines()[1]
        self.assertNotContains(response, sensitive_private_key_line)

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_generate_jwt_view_returns_bearer_token_json(self):
        key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)

        response = self.client.post(
            reverse("govuk_eddsa_generate_site_jwt", args=[self.site.pk]),
            data={
                "htu": "https://api.example.gov.uk/search",
                "htm": "get",
                "lifetime_seconds": "120",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["expires_in"], 120)
        self.assertEqual(body["issuer"], "https://admin.example.gov.uk")
        self.assertEqual(body["kid"], key_pair.key_id)
        self.assertEqual(body["htm"], "GET")
        self.assertEqual(body["htu"], "https://api.example.gov.uk/search")
        self.assertTrue(body["access_token"])

        public_key = serialization.load_pem_public_key(key_pair.public_key.encode("utf-8"))
        payload = jwt.decode(
            body["access_token"],
            key=public_key,
            algorithms=["EdDSA"],
            issuer="https://admin.example.gov.uk",
            options={"require": ["iss", "iat", "nbf", "exp", "jti"]},
        )
        self.assertEqual(payload["htu"], "https://api.example.gov.uk/search")
        self.assertEqual(payload["htm"], "GET")

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_generate_jwt_view_rejects_invalid_lifetime(self):
        EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)

        response = self.client.post(
            reverse("govuk_eddsa_generate_site_jwt", args=[self.site.pk]),
            data={"lifetime_seconds": "0"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "lifetime_seconds must be between 1 and 86400.",
            response.content.decode("utf-8"),
        )
