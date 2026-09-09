"""An export written before the framework page types changed still imports.

The framework used to be one ContentPage type with switches on it, a page per
role, and a skills index called SkillsAZPage. Every export taken from an
instance running that code -- the development instance's, the rehearsal's, the
one the cutover was verified against -- names those types. Imported as it
stands on this code, such a file produced a site that looked complete and was
not: 247 pages created, 53 skipped as unknown models, no framework main page,
no redirects, every role URL 404. These tests are that the file is read as
what it describes.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Page, Site

from govuk.models import (
    ContentPage,
    FrameworkContentPage,
    FrameworkMainPage,
    FrameworkSkillsPage,
    GovukRole,
    GovukSkill,
)
from govuk.page_import_export import (
    PAGE_EXPORT_FORMAT,
    _resolve_model_class,
    import_pages_from_payload,
)
from govuk.tests.framework_helpers import role_url


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _node(model, slug, *, fields=None, children=None, title=None):
    return {
        "model": model,
        "settings": {"slug": slug, "title": title or slug.replace("-", " ").title()},
        "fields": fields or {},
        "tags": [],
        "privacy": [],
        "children": children or [],
    }


def _legacy_notes(result) -> list[str]:
    return [note for note in result.notes if "before the framework page types" in note]


@override_settings(FEATURE_FLAGS=_feature_flags())
class LegacyExportImportTests(TestCase):
    """The 27 August export's shape, in miniature."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.user = get_user_model().objects.create_superuser(
            username="importer",
            email="importer@example.gov.uk",
            password="unused-password",
        )
        self.role = GovukRole.objects.create(title="Data analyst", family="Data")
        self.skill = GovukSkill.objects.create(title="Prototyping")

    def _legacy_payload(self):
        home_slug = self.site.root_page.slug
        return {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                _node(
                    "govuk.ContentPage",
                    home_slug,
                    title="Capability Framework",
                    fields={
                        "body": "<p>Welcome.</p>",
                        "show_role_navigation": True,
                        "show_framework_updates": True,
                        "show_framework_welcome": True,
                        "framework_welcome_body": [
                            {"type": "section", "value": {"body": "<p>Find your role.</p>"}}
                        ],
                    },
                    children=[
                        _node("govuk.SkillsAZPage", "skills", title="Skills A to Z"),
                        _node(
                            "govuk.ContentPage",
                            "job-grades",
                            fields={"body": "<p>Grades.</p>", "show_role_navigation": True},
                        ),
                        _node(
                            "govuk.ContentPage",
                            "download",
                            fields={"body": "<p>Files.</p>", "show_in_role_navigation": True},
                        ),
                        _node("govuk.ContentPage", "privacy", fields={"body": "<p>Privacy.</p>"}),
                        _node(
                            "govuk.RolePage",
                            "data-analyst",
                            fields={"body": "<p>Was a page.</p>"},
                        ),
                        _node("govuk.RolePage", "data-engineer"),
                    ],
                )
            ],
        }

    def _import(self, payload):
        return import_pages_from_payload(payload=payload, site=self.site, user=self.user)

    def test_the_home_page_becomes_the_framework_main_page(self):
        result = self._import(self._legacy_payload())

        self.assertEqual(result.errors, [])
        self.site.refresh_from_db()
        root = self.site.root_page.specific
        self.assertIsInstance(root, FrameworkMainPage)
        self.assertEqual(root.title, "Capability Framework")
        self.assertTrue(root.show_framework_welcome)
        self.assertIn("Find your role.", str(root.framework_welcome_body))

    def test_pages_with_a_switch_or_a_side_menu_tick_become_framework_content_pages(self):
        self._import(self._legacy_payload())

        self.assertEqual(
            sorted(FrameworkContentPage.objects.values_list("slug", flat=True)),
            ["download", "job-grades"],
        )
        # Plain pages stay plain: the privacy notice does not join the side menu.
        self.assertTrue(ContentPage.objects.filter(slug="privacy").exists())
        self.assertFalse(FrameworkContentPage.objects.filter(slug="privacy").exists())

    def test_the_skills_index_is_read_under_its_old_name(self):
        self._import(self._legacy_payload())

        skills_page = FrameworkSkillsPage.objects.get(slug="skills")
        self.assertEqual(skills_page.title, "Skills A to Z")

    def test_role_pages_are_not_imported_and_are_not_counted_as_failures(self):
        result = self._import(self._legacy_payload())

        self.assertFalse(Page.objects.filter(slug="data-analyst").exists())
        self.assertFalse(Page.objects.filter(slug="data-engineer").exists())
        self.assertEqual(result.errors, [])
        self.assertEqual(result.skipped, 0)
        # Nothing about an unknown model: the file is understood, not tolerated.
        self.assertFalse(any("unknown model" in error for error in result.errors))

    def test_the_report_says_how_the_file_was_read(self):
        result = self._import(self._legacy_payload())

        notes = _legacy_notes(result)
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertIn(f"'{self.site.root_page.slug}' as the framework main page", note)
        self.assertIn("2 pages beside the roles as framework content pages", note)
        self.assertIn("the skills index as a framework skills page", note)
        self.assertIn("2 role pages were not imported as pages", note)

    def test_the_live_services_redirects_are_seeded_because_the_targets_now_exist(self):
        result = self._import(self._legacy_payload())

        skills_page = FrameworkSkillsPage.objects.get(slug="skills")

        skill_redirect = Redirect.objects.get(old_path=f"/skill/{self.skill.slug}")
        self.assertEqual(skill_redirect.link, f"{skills_page.url}#{self.skill.slug}")
        self.assertTrue(any("Redirected 1" in note for note in result.notes), result.notes)
        # The home page became the framework main page, so a role is served at
        # the live service's own URL and needs no redirect.
        self.assertFalse(Redirect.objects.filter(old_path__startswith="/role/").exists())

    def test_a_live_role_url_is_answered_by_the_route_itself(self):
        self._import(self._legacy_payload())

        self.site.refresh_from_db()
        main_page = self.site.root_page.specific
        self.assertEqual(role_url(main_page, self.role), "/role/data-analyst/")
        self.assertEqual(self.client.get("/role/data-analyst/").status_code, 200)
        bare = self.client.get("/role/data-analyst")
        self.assertEqual((bare.status_code, bare["Location"]), (301, "/role/data-analyst/"))

    def test_every_role_in_the_file_is_served_on_the_main_page(self):
        self._import(self._legacy_payload())

        self.site.refresh_from_db()
        main_page = self.site.root_page.specific
        response = self.client.get(role_url(main_page, self.role))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data analyst")

    def test_importing_the_same_file_twice_updates_in_place(self):
        self._import(self._legacy_payload())
        result = self._import(self._legacy_payload())

        self.assertEqual(result.errors, [])
        self.assertEqual(result.created, 0)
        self.assertEqual(FrameworkMainPage.objects.count(), 1)
        self.assertEqual(FrameworkSkillsPage.objects.count(), 1)
        self.assertEqual(FrameworkContentPage.objects.count(), 2)
        self.assertEqual(ContentPage.objects.filter(slug="privacy").count(), 1)

    def test_pages_filed_under_a_role_page_are_kept_one_level_up(self):
        payload = self._legacy_payload()
        payload["pages"][0]["children"][4]["children"] = [
            _node("govuk.ContentPage", "analyst-reading-list")
        ]

        result = self._import(payload)

        self.assertEqual(result.errors, [])
        page = Page.objects.get(slug="analyst-reading-list")
        self.assertEqual(page.get_parent().pk, self.site.root_page.pk)

    def test_the_old_labels_resolve_whatever_their_case(self):
        self.assertIs(_resolve_model_class("govuk.SkillsAZPage"), FrameworkSkillsPage)
        self.assertIs(_resolve_model_class("govuk.skillsazpage"), FrameworkSkillsPage)
        self.assertIs(_resolve_model_class("govuk.FrameworkSkillsPage"), FrameworkSkillsPage)
        self.assertIsNone(_resolve_model_class("govuk.RolePage"))


@override_settings(FEATURE_FLAGS=_feature_flags())
class FilesThatNeedNoReadingTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.user = get_user_model().objects.create_superuser(
            username="importer",
            email="importer@example.gov.uk",
            password="unused-password",
        )

    def _import(self, pages):
        return import_pages_from_payload(
            payload={"format": PAGE_EXPORT_FORMAT, "pages": pages},
            site=self.site,
            user=self.user,
        )

    def test_a_file_naming_the_present_types_passes_through_untouched(self):
        result = self._import(
            [
                _node(
                    "govuk.FrameworkMainPage",
                    self.site.root_page.slug,
                    fields={"show_framework_welcome": True},
                    children=[_node("govuk.FrameworkContentPage", "roadmap")],
                )
            ]
        )

        self.assertEqual(result.errors, [])
        self.assertEqual(_legacy_notes(result), [])
        self.assertTrue(FrameworkMainPage.objects.filter(slug=self.site.root_page.slug).exists())
        self.assertTrue(FrameworkContentPage.objects.filter(slug="roadmap").exists())

    def test_plain_content_pages_with_no_switches_stay_plain(self):
        result = self._import(
            [
                _node(
                    "govuk.ContentPage",
                    self.site.root_page.slug,
                    fields={"body": "<p>A site without the framework.</p>"},
                    children=[_node("govuk.ContentPage", "about")],
                )
            ]
        )

        self.assertEqual(result.errors, [])
        self.assertEqual(_legacy_notes(result), [])
        self.assertFalse(FrameworkMainPage.objects.exists())
        self.assertFalse(FrameworkContentPage.objects.exists())
        self.assertTrue(ContentPage.objects.filter(slug="about").exists())
