import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, PageViewRestriction, Site

from govuk.models import ContentPage, GovukRole, GovukSkill, GovukTag, SectionPage
from govuk.page_import_export import PAGE_EXPORT_FORMAT, import_pages_from_payload


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
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
        self.assertContains(response, "Select one or more skills to export.")
        self.assertContains(response, "Select one or more roles to export.")

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_import_export_index_hides_skills_and_roles_sections_when_disabled(self):
        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Select one or more skills to export.")
        self.assertNotContains(response, "Select one or more roles to export.")

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

    def test_export_includes_selected_skills_and_roles(self):
        forensics = GovukSkill.objects.create(
            title="Forensics",
            body="<p>Collect and analyse evidence.</p>",
            working_points=[
                {
                    "type": "point",
                    "value": "Analyses evidence from digital investigations.",
                }
            ],
        )
        security_testing = GovukSkill.objects.create(
            title="Security testing",
            body="<p>Run assurance testing for services.</p>",
        )
        incident_responder = GovukRole.objects.create(
            title="Incident responder",
            body="<p>Responds to cyber incidents.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate incident responder",
                        "description": "<p>Supports investigations.</p>",
                        "skills": [
                            {"skill": forensics.pk, "level": "working"},
                            {"skill": security_testing.pk, "level": "awareness"},
                        ],
                    },
                }
            ],
        )

        response = self.client.post(
            self.export_url,
            data={
                "site_id": self.site.pk,
                "skill_ids": [forensics.pk],
                "role_ids": [incident_responder.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["pages"], [])
        self.assertEqual(len(payload["skills"]), 1)
        self.assertEqual(payload["skills"][0]["slug"], forensics.slug)
        self.assertEqual(len(payload["roles"]), 1)
        self.assertEqual(payload["roles"][0]["slug"], incident_responder.slug)
        self.assertEqual(
            payload["roles"][0]["levels"][0]["skills"],
            [
                {"skill_slug": forensics.slug, "level": "working"},
                {"skill_slug": security_testing.slug, "level": "awareness"},
            ],
        )

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

    def test_import_creates_and_updates_skills_and_roles(self):
        existing_skill = GovukSkill.objects.create(
            title="Forensics",
            body="<p>Old forensics summary.</p>",
        )
        existing_role = GovukRole.objects.create(
            title="Incident responder",
            body="<p>Old role summary.</p>",
            levels=[],
        )

        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [],
            "skills": [
                {
                    "slug": existing_skill.slug,
                    "title": "Forensics updated",
                    "body": "<p>Updated forensics summary.</p>",
                    "awareness_points": [
                        {
                            "type": "point",
                            "value": "Understands evidence handling.",
                        }
                    ],
                    "working_points": [],
                    "practitioner_points": [],
                    "expert_points": [],
                },
                {
                    "slug": "security-testing",
                    "title": "Security testing",
                    "body": "<p>Run assurance testing for services.</p>",
                    "awareness_points": [],
                    "working_points": [
                        {
                            "type": "point",
                            "value": "Performs repeatable security tests.",
                        }
                    ],
                    "practitioner_points": [],
                    "expert_points": [],
                },
            ],
            "roles": [
                {
                    "slug": existing_role.slug,
                    "title": "Incident responder",
                    "body": "<p>Updated role summary.</p>",
                    "levels": [
                        {
                            "title": "Associate incident responder",
                            "description": "<p>Supports investigations.</p>",
                            "skills": [
                                {
                                    "skill_slug": existing_skill.slug,
                                    "level": "working",
                                },
                                {
                                    "skill_slug": "security-testing",
                                    "level": "awareness",
                                },
                            ],
                        }
                    ],
                }
            ],
        }

        upload = SimpleUploadedFile(
            "skills-roles.json",
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

        existing_skill.refresh_from_db()
        existing_role.refresh_from_db()
        imported_security_testing = GovukSkill.objects.get(slug="security-testing")

        def _point_values(points):
            values = []
            for point in points:
                raw = getattr(point, "value", point)
                if isinstance(raw, dict):
                    values.append(raw.get("value", ""))
                else:
                    values.append(raw)
            return values

        self.assertEqual(existing_skill.title, "Forensics updated")
        self.assertEqual(existing_skill.body, "<p>Updated forensics summary.</p>")
        self.assertEqual(
            _point_values(existing_skill.awareness_points),
            ["Understands evidence handling."],
        )
        self.assertEqual(
            _point_values(imported_security_testing.working_points),
            ["Performs repeatable security tests."],
        )

        role_levels = existing_role.get_levels_with_skills()
        self.assertEqual(existing_role.body, "<p>Updated role summary.</p>")
        self.assertEqual(len(role_levels), 1)
        self.assertEqual(role_levels[0]["title"], "Associate incident responder")
        self.assertEqual(
            [skill_row["skill"].slug for skill_row in role_levels[0]["skills"]],
            ["forensics", "security-testing"],
        )

    def test_import_order_is_tags_then_skills_roles_then_pages(self):
        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "tags": [{"slug": "alpha", "name": "Alpha"}],
            "skills": [{"slug": "skill-one", "title": "Skill one"}],
            "roles": [{"slug": "role-one", "title": "Role one", "levels": []}],
            "pages": [{"model": "govuk.SectionPage", "settings": {"slug": "sequence-page"}}],
        }

        call_order: list[str] = []

        def _record_tags(*, raw_tags, raw_pages):
            call_order.append("tags")
            return {"by_slug": {}, "by_name": {}}

        def _record_skills(raw_skills, *, result):
            call_order.append("skills")

        def _record_roles(raw_roles, *, result):
            call_order.append("roles")

        def _record_pages(*args, **kwargs):
            call_order.append("pages")

        with (
            patch("govuk.page_import_export._import_tags_from_payload", side_effect=_record_tags),
            patch("govuk.page_import_export._import_skills", side_effect=_record_skills),
            patch("govuk.page_import_export._import_roles", side_effect=_record_roles),
            patch("govuk.page_import_export._import_page_node", side_effect=_record_pages),
        ):
            import_pages_from_payload(
                payload=payload,
                site=self.site,
                user=self.admin_user,
            )

        self.assertEqual(call_order, ["tags", "skills", "roles", "pages"])

    def test_import_creates_only_new_tags_and_sets_page_and_card_tags(self):
        existing_tag = GovukTag.objects.create(slug="existing-tag", name="Existing tag")

        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "tags": [
                {"slug": "existing-tag", "name": "Renamed existing tag"},
                {"slug": "new-tag", "name": "New tag"},
            ],
            "pages": [
                {
                    "model": "govuk.SectionPage",
                    "settings": {
                        "title": "Tagged section",
                        "draft_title": "Tagged section",
                        "slug": "tagged-section",
                        "seo_title": "",
                        "search_description": "",
                        "show_in_menus": False,
                        "go_live_at": None,
                        "expire_at": None,
                    },
                    "fields": {
                        "enable_hero_styling": False,
                        "enable_combined_service_navigation_and_hero_styling": False,
                        "hero_title": "Tagged section",
                        "hero_intro": "<p>Intro</p>",
                        "rows": [
                            {
                                "id": "7371d28b-34be-49b0-a7d2-3b28ef9077d8",
                                "type": "row",
                                "value": {
                                    "heading": "Row heading",
                                    "cards": [
                                        {
                                            "id": "fdcc9257-a872-48f5-b292-a6f023bc5062",
                                            "type": "item",
                                            "value": {
                                                "title": "Tagged card",
                                                "image": None,
                                                "image_fit": "cover",
                                                "text": "<p>Card text</p>",
                                                "link": {
                                                    "title": "Learn more",
                                                    "page": None,
                                                    "external_url": "https://example.gov.uk",
                                                },
                                                "tags": [
                                                    {
                                                        "id": "5a0b48b8-c809-455b-a50d-cf7462411f2d",
                                                        "type": "item",
                                                        "value": "New tag",
                                                    }
                                                ],
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                        "free_text": "",
                        "enable_free_text_heading_navigation": False,
                    },
                    "tags": [
                        {"slug": "existing-tag", "name": "Renamed existing tag"},
                        {"slug": "new-tag", "name": "New tag"},
                    ],
                    "privacy": [],
                    "children": [],
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

        existing_tag.refresh_from_db()
        self.assertEqual(existing_tag.name, "Existing tag")
        self.assertTrue(GovukTag.objects.filter(slug="new-tag", name="New tag").exists())

        imported_page = SectionPage.objects.get(slug="tagged-section")
        self.assertEqual(
            sorted(imported_page.tags.values_list("slug", flat=True)),
            ["existing-tag", "new-tag"],
        )

        raw_card_tag_value = imported_page.rows.raw_data[0]["value"]["cards"][0]["value"]["tags"][0][
            "value"
        ]
        self.assertIsInstance(raw_card_tag_value, int)
        self.assertEqual(GovukTag.objects.get(pk=raw_card_tag_value).slug, "new-tag")

    def _import(self, payload):
        upload = SimpleUploadedFile(
            "pages.json",
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )
        return self.client.post(
            self.import_url,
            data={"site_id": self.site.pk, "json_file": upload},
            follow=True,
        )

    def test_a_file_holding_nothing_to_import_is_not_reported_as_complete(self):
        """A green "Import complete" over a file that was never an export.

        Cutover is a sequence of exports and imports checked by reading the
        banner, so a success message for a file that moved nothing is the one
        message that must not appear. Every one of these left the site as it
        was and still said the import had finished.
        """
        for label, payload in (
            ("an empty object", {}),
            ("nothing but a format", {"format": PAGE_EXPORT_FORMAT}),
            ("an empty pages list", {"format": PAGE_EXPORT_FORMAT, "pages": []}),
            ("pages that are not a list", {"pages": "not a list"}),
            ("somebody else's file", {"users": [{"name": "someone"}]}),
        ):
            with self.subTest(label=label):
                pages_before = Page.objects.count()
                response = self._import(payload)
                messages = [str(message) for message in response.context["messages"]]

                self.assertEqual(Page.objects.count(), pages_before)
                self.assertTrue(messages)
                self.assertNotIn(
                    "Import complete",
                    " ".join(messages),
                    msg=f"{label} was reported as a completed import",
                )
                self.assertIn("Nothing was imported.", messages[0])

    def test_a_file_holding_one_page_is_still_reported_as_complete(self):
        response = self._import(
            {
                "format": PAGE_EXPORT_FORMAT,
                "pages": [
                    {
                        "model": "govuk.ContentPage",
                        "settings": {"title": "Apply", "slug": "apply"},
                        "fields": {"body": "<p>New body</p>"},
                    }
                ],
            }
        )
        messages = [str(message) for message in response.context["messages"]]

        self.assertIn("Import complete", " ".join(messages))
        self.content_page.refresh_from_db()
        self.assertEqual(self.content_page.body, "<p>New body</p>")

    def test_the_home_page_can_be_exported_and_comes_back_in_place(self):
        """The front page is content, and export used to leave it behind.

        A cutover that exported everything offered and imported it into a new
        instance still opened on "Welcome to your new Wagtail site!", because
        the site root was the one page the export list never showed. The
        import side has always known what to do with a home page node.
        """
        home_page = self.site.root_page.specific
        home_page.title = "Capability Framework"
        home_page.hero_title = "Find your role"
        home_page.save_revision().publish()

        index = self.client.get(self.index_url)
        self.assertContains(index, "Capability Framework")

        response = self.client.post(
            self.export_url,
            data={"site_id": self.site.pk, "page_ids": [home_page.pk]},
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(payload["pages"][0]["settings"]["slug"], home_page.slug)
        self.assertEqual(payload["pages"][0]["fields"]["hero_title"], "Find your role")
        exported_children = [
            child["settings"]["slug"] for child in payload["pages"][0]["children"]
        ]
        self.assertIn("benefits", exported_children)

        home_page.title = "Overwritten by hand"
        home_page.hero_title = ""
        home_page.save_revision().publish()
        pages_before = Page.objects.count()

        self._import(payload)

        home_page.refresh_from_db()
        self.assertEqual(Page.objects.count(), pages_before)
        self.assertEqual(self.site.root_page_id, home_page.pk)
        self.assertEqual(home_page.specific.title, "Capability Framework")
        self.assertEqual(home_page.specific.hero_title, "Find your role")
