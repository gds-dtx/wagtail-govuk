from django.test import TestCase
from django.urls import reverse
from wagtail.models import Site

from govuk.models import EdDSAKeyPair, EdDSAKeySettings


class JwksViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.site.hostname = "testserver"
        self.site.port = 80
        self.site.save(update_fields=["hostname", "port"])
        self.key_settings = EdDSAKeySettings.for_site(self.site)

    def test_returns_404_when_no_key_pairs_are_configured(self):
        response = self.client.get(reverse("govuk_jwks"))

        self.assertEqual(response.status_code, 404)

    def test_returns_all_public_keys_with_primary_key_first(self):
        first_key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)
        second_key_pair = EdDSAKeyPair.generate_for_settings(
            settings_obj=self.key_settings
        )
        second_key_pair.mark_as_primary()

        first_key_pair.refresh_from_db()
        second_key_pair.refresh_from_db()
        self.assertFalse(first_key_pair.is_primary)
        self.assertTrue(second_key_pair.is_primary)

        response = self.client.get(reverse("govuk_jwks"))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("keys", data)
        self.assertEqual(len(data["keys"]), 2)
        self.assertEqual(data["keys"][0]["kid"], second_key_pair.key_id)
        self.assertEqual(data["keys"][1]["kid"], first_key_pair.key_id)

        first_jwk = data["keys"][0]
        self.assertEqual(first_jwk["kty"], "OKP")
        self.assertEqual(first_jwk["alg"], "EdDSA")
        self.assertEqual(first_jwk["crv"], "Ed25519")
        self.assertEqual(first_jwk["use"], "sig")
        self.assertNotIn("private_key", first_jwk)


class EdDSAKeyPairModelTests(TestCase):
    def setUp(self):
        site = Site.objects.get(is_default_site=True)
        self.key_settings = EdDSAKeySettings.for_site(site)

    def test_first_key_pair_becomes_primary_automatically(self):
        first_key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)
        second_key_pair = EdDSAKeyPair.generate_for_settings(
            settings_obj=self.key_settings
        )

        first_key_pair.refresh_from_db()
        second_key_pair.refresh_from_db()

        self.assertTrue(first_key_pair.is_primary)
        self.assertFalse(second_key_pair.is_primary)

    def test_deleting_primary_key_promotes_another_key(self):
        first_key_pair = EdDSAKeyPair.generate_for_settings(settings_obj=self.key_settings)
        second_key_pair = EdDSAKeyPair.generate_for_settings(
            settings_obj=self.key_settings
        )

        first_key_pair.delete()
        second_key_pair.refresh_from_db()

        self.assertTrue(second_key_pair.is_primary)
