import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from wagtail.models import Page, PageViewRestriction, Site

from govuk.models import ContentPage, GovukTag, SectionPage
from govuk.page_import_export import PAGE_EXPORT_FORMAT


class PageImportExportAdminViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.admin_user)

        self.section_page = self.root_page.add_child(
            instance=SectionPage(title="Benefits", slug="benefits")
        )
        self.content_page = self.section_page.add_child(
            instance=ContentPage(title="Apply", slug="apply", body="<p>Old body</p>")
        )

        self.index_url = reverse("govuk_pages_import_export")
        self.export_url = reverse("govuk_pages_export")
        self.import_url = reverse("govuk_pages_import")

    def test_import_export_index_lists_pages_and_slugs(self):
        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Import / Export")
        self.assertContains(response, "Benefits")
        self.assertContains(response, "benefits")
        self.assertContains(response, "Apply")
        self.assertContains(response, "apply")

    def test_export_includes_children_privacy_and_page_settings(self):
        self.content_page.show_in_menus = True
        self.content_page.seo_title = "Apply for support"
        self.content_page.search_description = "How to apply"
        self.content_page.enable_combined_service_navigation_and_hero_styling = True
        self.content_page.save()

        group = Group.objects.create(name="Import editors")
        restriction = PageViewRestriction.objects.create(
            page=self.content_page,
            restriction_type=PageViewRestriction.GROUPS,
        )
        restriction.groups.set([group])

        response = self.client.post(
            self.export_url,
            data={
                "site_id": self.site.pk,
                "page_ids": [self.section_page.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

        payload = json.loads(response.content)
        self.assertEqual(payload["format"], PAGE_EXPORT_FORMAT)

        exported_section = payload["pages"][0]
        self.assertEqual(exported_section["settings"]["slug"], "benefits")
        self.assertEqual(len(exported_section["children"]), 1)

        exported_content = exported_section["children"][0]
        self.assertEqual(exported_content["settings"]["slug"], "apply")
        self.assertTrue(exported_content["settings"]["show_in_menus"])
        self.assertEqual(exported_content["settings"]["seo_title"], "Apply for support")
        self.assertEqual(exported_content["privacy"][0]["type"], PageViewRestriction.GROUPS)
        self.assertEqual(exported_content["privacy"][0]["groups"], ["Import editors"])

    def test_import_overrides_existing_page_by_slug(self):
        GovukTag.objects.create(slug="housing-benefit", name="Housing benefit")

        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.SectionPage",
                    "settings": {
                        "title": "Benefits updated",
                        "draft_title": "Benefits updated",
                        "slug": "benefits",
                        "seo_title": "Benefits SEO",
                        "search_description": "Benefits search text",
                        "show_in_menus": True,
                        "go_live_at": None,
                        "expire_at": None,
                    },
                    "fields": {
                        "hero_title": "Support options",
                        "hero_intro": "<p>Updated intro</p>",
                        "rows": [],
                        "free_text": "<p>Updated section content</p>",
                        "enable_hero_styling": True,
                        "enable_combined_service_navigation_and_hero_styling": False,
                        "enable_free_text_heading_navigation": False,
                    },
                    "tags": [],
                    "privacy": [{"type": PageViewRestriction.LOGIN}],
                    "children": [
                        {
                            "model": "govuk.ContentPage",
                            "settings": {
                                "title": "Apply now",
                                "draft_title": "Apply now",
                                "slug": "apply",
                                "seo_title": "Apply SEO",
                                "search_description": "Apply search text",
                                "show_in_menus": True,
                                "go_live_at": None,
                                "expire_at": None,
                            },
                            "fields": {
                                "hero_title": "Apply for support",
                                "hero_intro": "<p>Step by step</p>",
                                "body": "<p>Updated content body</p>",
                                "enable_hero_styling": False,
                                "enable_combined_service_navigation_and_hero_styling": True,
                                "enable_free_text_heading_navigation": True,
                            },
                            "tags": [
                                {
                                    "slug": "housing-benefit",
                                    "name": "Housing benefit",
                                }
                            ],
                            "privacy": [
                                {
                                    "type": PageViewRestriction.GROUPS,
                                    "groups": ["Import group"],
                                }
                            ],
                            "children": [],
                        }
                    ],
                }
            ],
        }

        upload = SimpleUploadedFile(
            "pages.json",
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )
        response = self.client.post(
            self.import_url,
            data={
                "site_id": self.site.pk,
                "json_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{self.index_url}?site_id={self.site.pk}")

        self.section_page.refresh_from_db()
        self.content_page.refresh_from_db()

        self.assertEqual(self.section_page.title, "Benefits updated")
        self.assertEqual(self.section_page.seo_title, "Benefits SEO")
        self.assertTrue(self.section_page.show_in_menus)
        self.assertEqual(self.section_page.free_text, "<p>Updated section content</p>")

        self.assertEqual(
            Page.objects.descendant_of(self.root_page, inclusive=False)
            .filter(slug="apply")
            .count(),
            1,
        )
        self.assertEqual(self.content_page.title, "Apply now")
        self.assertEqual(self.content_page.body, "<p>Updated content body</p>")
        self.assertEqual(self.content_page.seo_title, "Apply SEO")
        self.assertTrue(self.content_page.show_in_menus)
        self.assertTrue(
            self.content_page.enable_combined_service_navigation_and_hero_styling
        )
        self.assertEqual(
            list(self.content_page.tags.values_list("slug", flat=True)),
            ["housing-benefit"],
        )

        restriction = self.content_page.view_restrictions.get()
        self.assertEqual(restriction.restriction_type, PageViewRestriction.GROUPS)
        self.assertEqual(
            list(restriction.groups.values_list("name", flat=True)),
            ["Import group"],
        )
