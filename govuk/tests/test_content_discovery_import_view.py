from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from wagtail.models import Site

from govuk.models import ContentDiscoverySettings, ContentDiscoverySource


class ContentDiscoveryImportViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        ContentDiscoverySettings.for_site(self.site)
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )
        self.url = reverse("govuk_content_discovery_import_site", args=[self.site.pk])

    def test_imports_csv_for_current_site(self):
        self.client.force_login(self.admin_user)
        upload = SimpleUploadedFile(
            "sources.csv",
            (
                "url,name,disable_tls_verification,send_signed_bearer_jwt\n"
                "https://example.com/feed.xml,Example feed,false,true\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            self.url,
            data={"csv_file": upload, "next": "/admin/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")
        settings = ContentDiscoverySettings.for_site(self.site)
        source = ContentDiscoverySource.objects.get(
            settings=settings, url="https://example.com/feed.xml"
        )
        self.assertEqual(source.name, "Example feed")
        self.assertFalse(source.disable_tls_verification)
        self.assertTrue(source.send_signed_bearer_jwt)

    def test_rejects_rows_targeting_other_sites(self):
        other_site = Site.objects.create(
            hostname="other.example.gov.uk",
            port=8080,
            root_page=self.site.root_page,
            is_default_site=False,
        )
        self.client.force_login(self.admin_user)
        upload = SimpleUploadedFile(
            "sources.csv",
            (
                "site_id,url\n"
                f"{other_site.pk},https://example.com/feed.xml\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(self.url, data={"csv_file": upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContentDiscoverySource.objects.count(), 0)
        self.assertContains(response, "is not allowed for this import")
