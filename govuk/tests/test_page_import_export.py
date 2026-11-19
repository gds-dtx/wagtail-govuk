import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, PageViewRestriction, Site

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    ContentPage,
    FrameworkMainPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
)
from govuk.page_import_export import (
    FRAMEWORK_WORDING_FIELD_NAMES,
    PAGE_EXPORT_FORMAT,
    build_page_export_payload,
    import_pages_from_payload,
)
from govuk.tests.helpers import body_html, feature_flags, publish_page, text_body


def framework_home_node(children=()) -> dict:
    """A file's home page node rooted at the framework main page, as the framework's export is."""
    return {
        "model": "govuk.FrameworkMainPage",
        "settings": {"title": "Capability Framework", "slug": "home"},
        "fields": {"body": text_body("<p>The framework.</p>")},
        "tags": [],
        "privacy": [],
        "children": list(children),
    }


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class ImportExportTestCase(TestCase):
    """The default site, the import/export URLs and ways to run an import or export."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.index_url = reverse("govuk_pages_import_export")
        self.export_url = reverse("govuk_pages_export")
        self.import_url = reverse("govuk_pages_import")

    @staticmethod
    def _superuser(username: str = "admin-user"):
        return get_user_model().objects.create_superuser(
            username=username,
            email=f"{username}@example.gov.uk",
            password="unused-password",
        )

    def _add_benefits_pages(self, apply_body: str = "<p>Body</p>") -> None:
        self.benefits_page = self.root_page.add_child(
            instance=ContentPage(title="Benefits", slug="benefits")
        )
        self.content_page = self.benefits_page.add_child(
            instance=ContentPage(title="Apply", slug="apply", body=text_body(apply_body))
        )

    def _export(self, pages=()) -> dict:
        """An export of these pages and every skill and role."""
        return build_page_export_payload(
            site=self.site,
            pages=list(pages),
            skills=list(GovukSkill.objects.all()),
            roles=list(GovukRole.objects.all()),
        )

    def _import(self, payload: dict, *, user=None):
        return import_pages_from_payload(payload=payload, site=self.site, user=user or self.user)

    def _post_import(self, payload: dict | bytes, *, follow: bool = True):
        """Upload a file through the admin import view."""
        content = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return self.client.post(
            self.import_url,
            data={
                "site_id": self.site.pk,
                "json_file": SimpleUploadedFile(
                    "export.json", content, content_type="application/json"
                ),
            },
            follow=follow,
        )

    @staticmethod
    def _messages(response) -> list[str]:
        return [str(message) for message in response.context["messages"]]

    def _message_text(self, response) -> str:
        return " ".join(self._messages(response))


class PageImportExportAdminViewTests(ImportExportTestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = self._superuser()
        self.client.force_login(self.admin_user)
        self._add_benefits_pages("<p>Old body</p>")

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

    def test_each_export_list_has_its_own_select_all(self):
        GovukSkill.objects.create(title="Forensics", body="<p>Evidence.</p>")
        GovukRole.objects.create(title="Incident responder", body="<p>Incidents.</p>")

        response = self.client.get(self.index_url)

        self.assertContains(response, 'data-controller="w-bulk"', count=1)
        for group, label in [
            ("pages", "Select all pages"),
            ("skills", "Select all skills"),
            ("roles", "Select all roles"),
        ]:
            with self.subTest(group=group):
                self.assertContains(response, label, count=1)
                # The select-all and each row share the group, so it ticks only its list.
                self.assertContains(response, f'data-w-bulk-group-param="{group}"')
        self.assertContains(response, 'data-w-bulk-target="all"', count=3)
        # Benefits and Apply, plus the site's home page.
        self.assertContains(response, 'data-w-bulk-group-param="pages"', count=4)

    def test_an_empty_list_offers_no_select_all(self):
        response = self.client.get(self.index_url)

        self.assertContains(response, "Select all pages")
        self.assertNotContains(response, "Select all skills")
        self.assertNotContains(response, "Select all roles")

    @override_settings(FEATURE_FLAGS=feature_flags())
    def test_import_export_index_hides_skills_and_roles_sections_when_disabled(self):
        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Select one or more skills to export.")
        self.assertNotContains(response, "Select one or more roles to export.")

    def test_export_includes_children_privacy_and_page_settings(self):
        self.content_page.show_in_menus = True
        self.content_page.seo_title = "Apply for support"
        self.content_page.search_description = "How to apply"
        self.content_page.hero_style = "combined"
        self.content_page.save()

        group = Group.objects.create(name="Import editors")
        restriction = PageViewRestriction.objects.create(
            page=self.content_page,
            restriction_type=PageViewRestriction.GROUPS,
        )
        restriction.groups.set([group])

        response = self.client.post(
            self.export_url,
            data={"site_id": self.site.pk, "page_ids": [self.benefits_page.pk]},
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
                {"type": "point", "value": "Analyses evidence from digital investigations."}
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
                    "model": "govuk.ContentPage",
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
                        "body": text_body("<p>Updated section content</p>"),
                        "hero_style": "styled",
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
                                "body": text_body("<p>Updated content body</p>"),
                                "hero_style": "combined",
                                "enable_free_text_heading_navigation": True,
                            },
                            "tags": [{"slug": "housing-benefit", "name": "Housing benefit"}],
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

        response = self._post_import(payload, follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{self.index_url}?site_id={self.site.pk}")

        self.benefits_page.refresh_from_db()
        self.content_page.refresh_from_db()

        self.assertEqual(self.benefits_page.title, "Benefits updated")
        self.assertEqual(self.benefits_page.seo_title, "Benefits SEO")
        self.assertTrue(self.benefits_page.show_in_menus)
        self.assertEqual(body_html(self.benefits_page), "<p>Updated section content</p>")

        self.assertEqual(
            Page.objects.descendant_of(self.root_page, inclusive=False)
            .filter(slug="apply")
            .count(),
            1,
        )
        self.assertEqual(self.content_page.title, "Apply now")
        self.assertEqual(body_html(self.content_page), "<p>Updated content body</p>")
        self.assertEqual(self.content_page.seo_title, "Apply SEO")
        self.assertTrue(self.content_page.show_in_menus)
        self.assertEqual(self.content_page.hero_style, "combined")
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
                        {"type": "point", "value": "Understands evidence handling."}
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
                        {"type": "point", "value": "Performs repeatable security tests."}
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
                                {"skill_slug": existing_skill.slug, "level": "working"},
                                {"skill_slug": "security-testing", "level": "awareness"},
                            ],
                        }
                    ],
                }
            ],
        }

        response = self._post_import(payload, follow=False)

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
            "pages": [{"model": "govuk.ContentPage", "settings": {"slug": "sequence-page"}}],
        }

        call_order: list[str] = []

        def _record_tags(*, raw_tags, raw_pages, user, result):
            call_order.append("tags")
            return {"by_slug": {}, "by_name": {}}

        def _record_skills(raw_skills, *, user, result):
            call_order.append("skills")

        def _record_roles(raw_roles, *, user, result):
            call_order.append("roles")

        def _record_pages(*args, **kwargs):
            call_order.append("pages")

        with (
            patch("govuk.page_import_export._import_tags_from_payload", side_effect=_record_tags),
            patch("govuk.page_import_export._import_skills", side_effect=_record_skills),
            patch("govuk.page_import_export._import_roles", side_effect=_record_roles),
            patch("govuk.page_import_export._import_page_node", side_effect=_record_pages),
        ):
            self._import(payload, user=self.admin_user)

        self.assertEqual(call_order, ["tags", "skills", "roles", "pages"])

    def test_a_home_page_of_another_type_is_refused_not_rebuilt_around(self):
        """Falling through used to build a second home page and move every slug-matched page under it."""
        payload = {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.FrameworkMainPage",
                    "settings": {"title": "The framework", "slug": self.root_page.slug},
                    "fields": {"body": text_body("<p>Welcome</p>")},
                    "children": [
                        {
                            "model": "govuk.ContentPage",
                            "settings": {"title": "Benefits changed", "slug": "benefits"},
                            "fields": {},
                        },
                        {
                            "model": "govuk.ContentPage",
                            "settings": {"title": "Brand new", "slug": "brand-new"},
                            "fields": {"body": text_body("<p>New</p>")},
                        },
                    ],
                }
            ],
        }

        result = self._import(payload, user=self.admin_user)

        # The site keeps its shape: one home page, and nothing moved under a second one.
        self.assertEqual(Page.objects.filter(slug=self.root_page.slug).count(), 1)
        self.benefits_page.refresh_from_db()
        self.assertEqual(self.benefits_page.get_parent().pk, self.root_page.pk)
        # What the file holds inside its home page still arrives, in place.
        self.assertEqual(Page.objects.get(pk=self.benefits_page.pk).title, "Benefits changed")
        brand_new = Page.objects.get(slug="brand-new")
        self.assertEqual(brand_new.get_parent().pk, self.root_page.pk)
        self.assertTrue(
            any("home page was left as it is" in error for error in result.errors),
            result.errors,
        )

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
                    "model": "govuk.ContentPage",
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
                        "hero_style": "none",
                        "hero_title": "Tagged section",
                        "hero_intro": "<p>Intro</p>",
                        "body": [
                            {
                                "id": "7371d28b-34be-49b0-a7d2-3b28ef9077d8",
                                "type": "cards",
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

        response = self._post_import(payload, follow=False)

        self.assertEqual(response.status_code, 302)

        existing_tag.refresh_from_db()
        self.assertEqual(existing_tag.name, "Existing tag")
        self.assertTrue(GovukTag.objects.filter(slug="new-tag", name="New tag").exists())

        imported_page = ContentPage.objects.get(slug="tagged-section")
        self.assertEqual(
            sorted(imported_page.tags.values_list("slug", flat=True)),
            ["existing-tag", "new-tag"],
        )

        raw_card_tag_value = imported_page.body.raw_data[0]["value"]["cards"][0]["value"][
            "tags"
        ][0]["value"]
        self.assertIsInstance(raw_card_tag_value, int)
        self.assertEqual(GovukTag.objects.get(pk=raw_card_tag_value).slug, "new-tag")

    def test_a_file_holding_nothing_to_import_is_not_reported_as_complete(self):
        """Cutover is checked by reading the banner, so a file that moved nothing must not say it finished."""
        for label, payload in (
            ("an empty object", {}),
            ("nothing but a format", {"format": PAGE_EXPORT_FORMAT}),
            ("an empty pages list", {"format": PAGE_EXPORT_FORMAT, "pages": []}),
            ("pages that are not a list", {"pages": "not a list"}),
            ("somebody else's file", {"users": [{"name": "someone"}]}),
        ):
            with self.subTest(label=label):
                pages_before = Page.objects.count()
                messages = self._messages(self._post_import(payload))

                self.assertEqual(Page.objects.count(), pages_before)
                self.assertTrue(messages)
                self.assertNotIn(
                    "Import complete",
                    " ".join(messages),
                    msg=f"{label} was reported as a completed import",
                )
                self.assertIn("Nothing was imported.", messages[0])

    def test_every_reason_nothing_was_imported_is_given_not_just_the_first(self):
        """Showing only the first reason turns them into a queue of re-uploads."""
        response = self._post_import(
            {
                "format": PAGE_EXPORT_FORMAT,
                "changelog": [
                    {"note": "An entry with no date"},
                    {"date": "2026-08-19", "note": ""},
                ],
                "tags": [{"slug": "carried-tag", "name": "Carried tag"}],
            }
        )
        messages = self._messages(response)

        self.assertIn("Nothing was imported.", messages[0])
        self.assertEqual(
            messages[0].count("Skipped a changelog entry for the framework"),
            2,
            messages[0],
        )

    def test_a_file_holding_one_page_is_still_reported_as_complete(self):
        response = self._post_import(
            {
                "format": PAGE_EXPORT_FORMAT,
                "pages": [
                    {
                        "model": "govuk.ContentPage",
                        "settings": {"title": "Apply", "slug": "apply"},
                        "fields": {"body": text_body("<p>New body</p>")},
                    }
                ],
            }
        )

        self.assertIn("Import complete", self._message_text(response))
        self.content_page.refresh_from_db()
        self.assertEqual(body_html(self.content_page), "<p>New body</p>")

    def test_the_home_page_can_be_exported_and_comes_back_in_place(self):
        """The site root was the one page the export list never showed."""
        home_page = self.root_page
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
        exported_children = [child["settings"]["slug"] for child in payload["pages"][0]["children"]]
        self.assertIn("benefits", exported_children)

        home_page.title = "Overwritten by hand"
        home_page.hero_title = ""
        home_page.save_revision().publish()
        pages_before = Page.objects.count()

        self._post_import(payload)

        home_page.refresh_from_db()
        self.assertEqual(Page.objects.count(), pages_before)
        self.assertEqual(self.site.root_page_id, home_page.pk)
        self.assertEqual(home_page.specific.title, "Capability Framework")
        self.assertEqual(home_page.specific.hero_title, "Find your role")


class RoleExportImportFieldTests(ImportExportTestCase):
    """The export has to carry every field, or a transfer silently loses content."""

    def setUp(self):
        super().setUp()
        self.user = self._superuser("importer")

        self.skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        self.leadership_skill = GovukSkill.objects.create(
            title="Capability building",
            is_senior_civil_service=True,
            leadership_points=[{"type": "point", "value": "prioritising needs"}],
        )

        self.role = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate data analyst",
                        "description": "",
                        "grades": ["eo", "heo"],
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                }
            ],
        )
        self.scs_role = GovukRole.objects.create(
            title="Chief technology officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            scs_grades=[{"type": "grade", "value": "scs1"}],
            scs_skills=[{"type": "skill", "value": self.leadership_skill.pk}],
        )

        GovukChangelogEntry.objects.create(
            date="2026-04-01",
            role=self.role,
            change_type="Skills updated",
            note="<p>Added data modelling.</p>",
        )
        GovukChangelogEntry.objects.create(
            date="2026-03-01",
            note="<p>Framework wide update.</p>",
        )

    @staticmethod
    def _by_slug(rows: list[dict]) -> dict[str, dict]:
        return {row["slug"]: row for row in rows}

    def _set_progression(self, *roles: GovukRole) -> None:
        """Name the roles that could lead to the chief technology officer."""
        self.scs_role.roles_that_could_lead_here = [
            {"type": "role", "value": role.pk} for role in roles
        ]
        self.scs_role.save()

    @staticmethod
    def _progression() -> list[str]:
        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        return [role.slug for role in scs_role.get_roles_that_could_lead_here()]

    @staticmethod
    def _placeholder_notes(result) -> list[str]:
        """The notes about the home page, not the ones about redirects."""
        return [note for note in result.notes if "placeholder home page" in note]

    def test_export_carries_every_role_field(self):
        exported = self._by_slug(self._export()["roles"])

        self.assertEqual(exported["data-analyst"]["family"], "Data")
        self.assertEqual(exported["data-analyst"]["levels"][0]["grades"], ["eo", "heo"])
        self.assertTrue(exported["chief-technology-officer"]["is_senior_civil_service"])
        self.assertEqual(exported["chief-technology-officer"]["scs_grades"], ["scs1"])
        self.assertEqual(
            exported["chief-technology-officer"]["scs_skills"], ["capability-building"]
        )

    def test_export_carries_the_roles_that_could_lead_to_a_role_as_slugs(self):
        self._set_progression(self.role)

        exported = self._by_slug(self._export()["roles"])

        self.assertEqual(
            exported["chief-technology-officer"]["roles_that_could_lead_here"],
            ["data-analyst"],
        )

    def test_import_restores_a_wiped_progression_mapping(self):
        self._set_progression(self.role)
        payload = self._export()
        self._set_progression()

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._progression(), ["data-analyst"])

    def test_a_role_can_name_one_that_is_imported_after_it(self):
        """Roles arrive in one pass, so the references are resolved in a second."""
        payload = self._export()
        exported = self._by_slug(payload["roles"])
        exported["chief-technology-officer"]["roles_that_could_lead_here"] = ["data-analyst"]
        # Chief technology officer is imported first, before data analyst exists.
        payload["roles"] = [exported["chief-technology-officer"], exported["data-analyst"]]
        GovukRole.objects.all().delete()

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._progression(), ["data-analyst"])

    def test_a_rejected_role_keeps_the_progression_mapping_it_already_had(self):
        """The second pass must not carry over a payload the first refused to save."""
        other_role = GovukRole.objects.create(title="Data engineer", family="Data")
        self._set_progression(other_role)

        payload = self._export()
        row = self._by_slug(payload["roles"])["chief-technology-officer"]
        # Longer than the field allows, so full_clean rejects it.
        row["title"] = "C" * 300
        row["roles_that_could_lead_here"] = ["data-analyst"]

        result = self._import(payload)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(self._progression(), [other_role.slug])

    def test_a_role_with_no_progression_mapping_is_not_written_a_second_time(self):
        """Almost every role names none, and the second pass would rewrite each unchanged field."""
        payload = self._export()
        for row in payload["roles"]:
            self.assertEqual(row["roles_that_could_lead_here"], [])

        # The second pass is the only thing that saves that field on its own.
        saved = []
        original_save = GovukRole.save

        def record(role, *args, update_fields=None, **kwargs):
            if update_fields == ["roles_that_could_lead_here"]:
                saved.append(role.slug)
            return original_save(role, *args, update_fields=update_fields, **kwargs)

        with patch.object(GovukRole, "save", record):
            result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(saved, [])

    def test_a_file_written_before_the_field_existed_leaves_the_mapping_alone(self):
        """Reading an older export's silence as "empty it" would clear every senior role's path."""
        self._set_progression(self.role)
        payload = self._export()
        for row in payload["roles"]:
            row.pop("roles_that_could_lead_here", None)

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._progression(), ["data-analyst"])

    def test_an_empty_list_still_empties_the_mapping(self):
        """Said outright, which is what an export of a role an editor has cleared carries."""
        self._set_progression(self.role)
        payload = self._export()
        self._by_slug(payload["roles"])["chief-technology-officer"][
            "roles_that_could_lead_here"
        ] = []

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._progression(), [])

    def test_a_mapping_that_is_not_an_array_is_reported_rather_than_obeyed(self):
        self._set_progression(self.role)
        payload = self._export()
        self._by_slug(payload["roles"])["chief-technology-officer"][
            "roles_that_could_lead_here"
        ] = "data-analyst"

        result = self._import(payload)

        self.assertEqual(
            result.errors,
            [
                "Role 'chief-technology-officer' kept its existing progression "
                "roles because 'roles_that_could_lead_here' is not an array."
            ],
        )
        self.assertEqual(self._progression(), ["data-analyst"])

    def test_a_progression_role_missing_from_the_destination_is_reported(self):
        self._set_progression(self.role)
        payload = self._export()
        payload["roles"] = [row for row in payload["roles"] if row["slug"] != "data-analyst"]
        self.role.delete()

        result = self._import(payload)

        self.assertEqual(
            result.errors,
            [
                "Role 'chief-technology-officer' skipped a role that could lead "
                "to it for missing role 'data-analyst'."
            ],
        )

    def test_export_carries_every_skill_field(self):
        exported = self._by_slug(self._export()["skills"])

        self.assertTrue(exported["capability-building"]["is_senior_civil_service"])
        self.assertEqual(
            [point["value"] for point in exported["capability-building"]["leadership_points"]],
            ["prioritising needs"],
        )
        self.assertFalse(exported["data-modelling"]["is_senior_civil_service"])

    def test_import_restores_a_wiped_senior_civil_service_skill(self):
        payload = self._export()

        self.leadership_skill.is_senior_civil_service = False
        self.leadership_skill.leadership_points = []
        self.leadership_skill.save()

        result = self._import(payload)
        self.assertEqual(result.errors, [])

        skill = GovukSkill.objects.get(slug="capability-building")
        self.assertTrue(skill.is_senior_civil_service)
        self.assertEqual(skill.get_leadership_points(), ["prioritising needs"])

    def test_export_carries_the_site_name(self):
        self.site.site_name = "Capability Framework"
        self.site.save(update_fields=["site_name"])

        self.assertEqual(self._export()["site"]["site_name"], "Capability Framework")

    def test_import_restores_the_site_name_but_leaves_the_hostname_alone(self):
        self.site.site_name = "Capability Framework"
        self.site.save(update_fields=["site_name"])
        payload = self._export()

        self.site.site_name = ""
        self.site.hostname = "somewhere-else.example.com"
        self.site.save(update_fields=["site_name", "hostname"])

        self._import(payload)

        self.site.refresh_from_db()
        self.assertEqual(self.site.site_name, "Capability Framework")
        self.assertEqual(self.site.hostname, "somewhere-else.example.com")

    def test_importing_a_whole_site_twice_does_not_nest_a_second_copy(self):
        """Re-importing is how dev content gets refreshed."""
        publish_page("Data analyst", "<p>Body.</p>", parent=self.root_page)
        home = self.root_page
        payload = self._export([home])

        for _ in range(2):
            result = self._import(payload)
            self.assertEqual(result.errors, [])

        self.site.refresh_from_db()
        self.assertEqual(self.site.root_page.pk, home.pk)
        self.assertEqual([child.slug for child in home.get_children()], ["data-analyst"])

    def test_a_placeholder_home_page_is_replaced_rather_than_nested_under(self):
        """A new instance ships an empty home page of the wrong type."""
        placeholder = self.root_page
        # The starter site's home page, a content page since 0089.
        self.assertIsInstance(placeholder, ContentPage)
        payload = self._export()
        payload["pages"] = [
            framework_home_node(
                children=[
                    {
                        "model": "govuk.ContentPage",
                        "settings": {"title": "Data analyst", "slug": "data-analyst"},
                        "fields": {"body": text_body("<p>Body.</p>")},
                        "tags": [],
                        "privacy": [],
                        "children": [],
                    }
                ]
            )
        ]

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertTrue(self._placeholder_notes(result))
        self.site.refresh_from_db()
        new_root = self.site.root_page.specific
        self.assertIsInstance(new_root, FrameworkMainPage)
        self.assertEqual(new_root.slug, "home")
        self.assertEqual(new_root.depth, 2)
        # The framework sits at the top level, not under /home/.
        self.assertEqual([child.slug for child in new_root.get_children()], ["data-analyst"])
        self.assertEqual(ContentPage.objects.get(slug="data-analyst").url, "/data-analyst/")
        self.assertFalse(Page.objects.filter(pk=placeholder.pk).exists())

    def test_a_home_page_with_children_is_left_alone(self):
        """Only an empty placeholder is safe to throw away."""
        publish_page("Data analyst", "<p>Body.</p>", parent=self.root_page)
        payload = self._export()
        payload["pages"] = [framework_home_node()]

        result = self._import(payload)

        self.site.refresh_from_db()
        self.assertEqual(self.site.root_page.pk, self.root_page.pk)
        self.assertEqual(self._placeholder_notes(result), [])

    def test_export_carries_changelog_entries(self):
        payload = self._export()
        exported = self._by_slug(payload["roles"])

        self.assertEqual(
            [entry["change_type"] for entry in exported["data-analyst"]["changelog"]],
            ["Skills updated"],
        )
        self.assertEqual(len(payload["changelog"]), 1)
        self.assertIn("Framework wide", payload["changelog"][0]["note"])

    def test_import_restores_fields_that_were_wiped(self):
        payload = self._export()

        self.role.family = ""
        self.role.levels = []
        self.role.save()
        self.scs_role.is_senior_civil_service = False
        self.scs_role.scs_grades = []
        self.scs_role.scs_skills = []
        self.scs_role.save()
        GovukChangelogEntry.objects.all().delete()

        result = self._import(payload)
        self.assertEqual(result.errors, [])

        role = GovukRole.objects.get(slug="data-analyst")
        self.assertEqual(role.family, "Data")
        self.assertEqual(
            role.get_levels_with_skills()[0]["grades"],
            ["EO (Executive Officer)", "HEO (Higher Executive Officer)"],
        )

        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertTrue(scs_role.is_senior_civil_service)
        self.assertEqual(scs_role.get_scs_grade_labels(), ["SCS 1 (Senior Civil Service 1)"])
        self.assertEqual(len(scs_role.get_scs_skills()), 1)

        self.assertEqual(GovukChangelogEntry.objects.filter(role=role).count(), 1)
        self.assertEqual(GovukChangelogEntry.objects.filter(role=None, skill=None).count(), 1)

    def test_importing_twice_does_not_duplicate_changelog_entries(self):
        payload = self._export()

        for _ in range(2):
            self._import(payload)

        self.assertEqual(GovukChangelogEntry.objects.count(), 2)

    def test_hyphen_paragraph_bullets_are_repaired_on_the_way_in(self):
        """Migration 0058 fixed existing rows; an older file would put the dashes straight back."""
        payload = self._export()
        payload["changelog"] = [
            {
                "date": "2026-05-29",
                "note": "<p>8 skills updated:</p><p>- data innovation</p><p>- data ethics</p>",
                "change_type": "",
                "live": True,
            }
        ]

        self._import(payload)

        entry = GovukChangelogEntry.objects.get(role=None, skill=None)
        self.assertIn("<ul><li>data innovation</li><li>data ethics</li></ul>", entry.note)
        self.assertNotIn("<p>- ", entry.note)

    def test_unknown_grade_keys_are_dropped_rather_than_failing_the_import(self):
        payload = self._export()
        exported = self._by_slug(payload["roles"])
        exported["data-analyst"]["levels"][0]["grades"] = ["eo", "not-a-grade"]
        exported["chief-technology-officer"]["scs_grades"] = ["scs1", "heo"]

        result = self._import(payload)
        self.assertEqual(result.errors, [])

        role = GovukRole.objects.get(slug="data-analyst")
        self.assertEqual(role.get_levels_with_skills()[0]["grades"], ["EO (Executive Officer)"])
        # HEO is a real grade, but not one a Senior Civil Service role can hold.
        scs_role = GovukRole.objects.get(slug="chief-technology-officer")
        self.assertEqual(scs_role.get_scs_grade_labels(), ["SCS 1 (Senior Civil Service 1)"])


class ImportIntoASiteWithSkillsSwitchedOffTests(ImportExportTestCase):
    """FEATURE_SKILLS is set by the Terraform that builds an instance, so a site can come up without it."""

    def setUp(self):
        super().setUp()
        self.user = self._superuser("importer")
        GovukRole.objects.create(title="Data analyst", family="Data")
        GovukSkill.objects.create(title="Data modelling")
        GovukChangelogEntry.objects.create(date="2026-03-01", note="<p>Framework wide update.</p>")
        page = publish_page("Data analyst", "<p>Body.</p>")
        self.payload = self._export([page])

    def _import_with_skills_off(self):
        GovukRole.objects.all().delete()
        GovukSkill.objects.all().delete()
        GovukChangelogEntry.objects.all().delete()
        with override_settings(FEATURE_FLAGS=feature_flags()):
            return self._import(self.payload)

    def test_the_flag_is_named_rather_than_the_file_being_blamed(self):
        result = self._import_with_skills_off()

        self.assertEqual(len(result.errors), 1)
        self.assertIn("FEATURE_SKILLS", result.errors[0])
        self.assertIn("skills, roles, changelog and wording", result.errors[0])

    def test_the_pages_still_arrive(self):
        """Reporting the problem must not turn into refusing the pages, which transfer fine."""
        result = self._import_with_skills_off()

        self.assertEqual(result.skipped, 0)
        self.assertTrue(ContentPage.objects.filter(slug="data-analyst").exists())

    def test_the_run_is_explained_by_the_one_flag_message_not_page_symptoms(self):
        """The flag message must not be drowned in per-page missing-role reports."""
        result = self._import_with_skills_off()

        self.assertEqual([error for error in result.errors if "dropped role" in error], [])

    def test_a_file_carrying_no_framework_content_is_not_warned_about(self):
        """A site running without the framework imports its own pages all the time."""
        for key in ("skills", "roles", "changelog", "wording"):
            self.payload.pop(key, None)

        result = self._import_with_skills_off()

        self.assertEqual(result.errors, [])


class FrameworkWordingExportImportTests(ImportExportTestCase):
    """The wording is a site setting, so without this an edit would be retyped in each environment."""

    def setUp(self):
        super().setUp()
        self.user = self._superuser("wording-importer")
        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)

    def _reloaded(self) -> CapabilityFrameworkWordingSettings:
        return CapabilityFrameworkWordingSettings.objects.get(pk=self.wording.pk)

    def test_every_field_on_the_form_is_a_field_that_travels(self):
        """A field of a type the list's rule passes over would be quietly left out of every export."""
        on_the_form = [
            child.field_name
            for panel in CapabilityFrameworkWordingSettings.panels
            for child in getattr(panel, "children", [panel])
            if hasattr(child, "field_name")
        ]

        self.assertEqual(sorted(on_the_form), sorted(FRAMEWORK_WORDING_FIELD_NAMES))

    def test_the_export_carries_every_wording_field(self):
        exported = self._export()["wording"]

        self.assertEqual(sorted(exported), sorted(FRAMEWORK_WORDING_FIELD_NAMES))
        self.assertEqual(exported["contents_heading"], "Contents")
        self.assertEqual(len(exported), 52)

    def test_an_edit_survives_the_journey_to_another_environment(self):
        self.wording.related_roles_heading = "Roles sharing skills with {role}"
        self.wording.no_skills_message = "There are no skills yet."
        self.wording.save()
        payload = self._export()

        # The environment being imported into, still on the defaults.
        self.wording.related_roles_heading = "Roles that share {role} skills"
        self.wording.no_skills_message = "No skills found."
        self.wording.save()

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._reloaded().related_roles_heading, "Roles sharing skills with {role}")
        self.assertEqual(self._reloaded().no_skills_message, "There are no skills yet.")

    def test_a_file_from_before_the_wording_moved_leaves_it_alone(self):
        """Older exports say nothing about the wording, which is not a request to revert it."""
        self.wording.updates_heading = "Changes"
        self.wording.save()
        page = publish_page("Somewhere", "")

        payload = self._export([page])
        payload.pop("wording")
        result = self._import(payload)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._reloaded().updates_heading, "Changes")

    def test_wording_alone_imports_and_leaves_fields_it_does_not_name(self):
        """Pushing a wording change is a reason to import on its own."""
        self.wording.published_prefix = "First published"
        self.wording.save()

        result = self._import({"wording": {"updates_heading": "Changes"}})

        self.assertEqual(result.errors, [])
        self.assertEqual(result.updated, 1)
        self.assertEqual(self._reloaded().updates_heading, "Changes")
        self.assertEqual(self._reloaded().published_prefix, "First published")

    def test_a_heading_too_long_for_the_column_is_reported_and_the_rest_land(self):
        result = self._import(
            {"wording": {"updates_heading": "Changes", "contents_heading": "C" * 256}}
        )

        self.assertEqual(len(result.errors), 1)
        self.assertIn("contents_heading", result.errors[0])
        self.assertEqual(self._reloaded().contents_heading, "Contents")
        self.assertEqual(self._reloaded().updates_heading, "Changes")

    def test_wording_given_as_something_other_than_text_is_reported(self):
        """Null would otherwise be stored as "None", and a list as its repr."""
        result = self._import(
            {"wording": {"contents_heading": None, "updates_heading": ["Changes"]}}
        )

        self.assertEqual(len(result.errors), 2)
        self.assertEqual(self._reloaded().contents_heading, "Contents")
        self.assertEqual(self._reloaded().updates_heading, "Updates")

    def test_wording_that_is_not_an_object_is_reported(self):
        result = self._import({"wording": "Contents"})

        self.assertEqual(result.skipped, 1)
        self.assertIn("Payload 'wording' value must be an object when provided.", result.errors)

    def test_an_empty_payload_says_wording_is_one_of_the_things_it_could_hold(self):
        result = self._import({})

        self.assertEqual(
            result.errors,
            [
                "Payload must contain at least one entry in 'tags', 'pages', "
                "'skills', 'roles' or 'wording'."
            ],
        )

    def test_a_round_trip_leaves_the_wording_exactly_as_it_was(self):
        for index, field_name in enumerate(FRAMEWORK_WORDING_FIELD_NAMES):
            setattr(self.wording, field_name, f"Wording {index}")
        self.wording.save()
        payload = self._export()

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        reloaded = self._reloaded()
        self.assertEqual(
            {name: getattr(reloaded, name) for name in FRAMEWORK_WORDING_FIELD_NAMES},
            payload["wording"],
        )


class WordingOnlyExportViewTests(ImportExportTestCase):
    """The wording is not a page, skill or role, so ticking nothing is what exports it alone."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self._superuser("wording-exporter"))
        self.page = publish_page("Somewhere", "")
        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)

    def test_selecting_nothing_exports_the_wording_by_itself(self):
        self.wording.updates_heading = "Changes"
        self.wording.save()

        response = self.client.post(self.export_url, data={"site_id": self.site.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        payload = json.loads(response.content)
        self.assertEqual(payload["wording"]["updates_heading"], "Changes")
        self.assertEqual(payload["pages"], [])
        self.assertEqual(payload["skills"], [])
        self.assertEqual(payload["roles"], [])

    def test_the_file_is_named_for_what_is_in_it(self):
        wording_only = self.client.post(self.export_url, data={"site_id": self.site.pk})
        with_a_page = self.client.post(
            self.export_url,
            data={"site_id": self.site.pk, "page_ids": [self.page.pk]},
        )

        self.assertIn(
            f'filename="wording-export-site-{self.site.pk}-',
            wording_only["Content-Disposition"],
        )
        self.assertIn(
            f'filename="pages-export-site-{self.site.pk}-',
            with_a_page["Content-Disposition"],
        )

    def test_the_exported_file_applies_on_the_way_back_in(self):
        self.wording.related_roles_heading = "Roles sharing skills with {role}"
        self.wording.save()
        exported = self.client.post(self.export_url, data={"site_id": self.site.pk})

        self.wording.related_roles_heading = "Roles that share {role} skills"
        self.wording.save()

        response = self._post_import(exported.content)

        self.assertContains(response, "Import complete.")
        self.assertEqual(
            CapabilityFrameworkWordingSettings.for_site(self.site).related_roles_heading,
            "Roles sharing skills with {role}",
        )

    def test_a_selection_that_matches_nothing_is_still_reported(self):
        """Ticking a page since deleted is a mistake to hear about, not a wording export."""
        response = self.client.post(
            self.export_url,
            data={"site_id": self.site.pk, "page_ids": [self.page.pk + 5000]},
            follow=True,
        )

        self.assertContains(response, "No matching pages, skills or roles were found for export.")


@override_settings(FEATURE_FLAGS=feature_flags())
class FrameworkWordingWithTheFrameworkOffTests(ImportExportTestCase):
    """The wording belongs to role and skill pages a site without the framework does not have."""

    def setUp(self):
        super().setUp()
        self.user = self._superuser("wording-importer-off")
        self.client.force_login(self.user)

    def test_selecting_nothing_is_still_a_mistake_where_there_is_no_wording(self):
        response = self.client.post(self.export_url, data={"site_id": self.site.pk}, follow=True)

        self.assertContains(response, "Select at least one page to export.")

    def test_the_export_carries_no_wording(self):
        self.assertNotIn("wording", self._export())

    def test_an_imported_file_holding_wording_is_told_the_framework_is_off(self):
        publish_page("Somewhere", "")

        result = self._import(
            {
                "pages": [],
                "tags": [{"slug": "alpha", "name": "Alpha"}],
                "wording": {"updates_heading": "Changes"},
            }
        )

        self.assertEqual(len(result.errors), 1)
        self.assertIn("wording", result.errors[0])
        self.assertIn("FEATURE_SKILLS", result.errors[0])
        self.assertEqual(
            CapabilityFrameworkWordingSettings.for_site(self.site).updates_heading,
            "Updates",
        )
