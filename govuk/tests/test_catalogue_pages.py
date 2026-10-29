"""Serving the content catalogue: the main page, groups, entries, admin and command."""

from io import StringIO
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.catalogue.sync import sync_catalogue_page
from govuk.models import ContentMainPage, ContentPage
from govuk.tests.catalogue_helpers import FakeGitHub, guidance_flags
from govuk.tests.test_catalogue_sync import make_content_main_page


class CatalogueTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific
        self.page = make_content_main_page(self.root)
        sync_catalogue_page(self.page, fetch=FakeGitHub())
        self.base = self.page.relative_url(self.site)

    def get(self, path="", **params):
        return self.client.get(self.base + path, params)


@override_settings(FEATURE_FLAGS=guidance_flags())
class ContentMainPageTests(CatalogueTestCase):
    def test_the_page_itself_is_a_content_page(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "govuk/content_page.html")

    def test_its_own_child_pages_are_still_served(self):
        child = self.page.add_child(instance=ContentPage(title="About", slug="about"))
        child.save_revision().publish()
        self.assertEqual(self.get("about/").status_code, 200)

    def test_parent_and_subpage_types_agree(self):
        for parent_name in ContentMainPage.parent_page_types:
            with self.subTest(parent=parent_name):
                self.assertIn(ContentMainPage, apps.get_model(parent_name).allowed_subpage_models())
        for child in ContentMainPage.subpage_types:
            with self.subTest(child=child):
                self.assertIn(ContentMainPage, apps.get_model(child).allowed_parent_page_models())

    def test_the_repository_is_validated(self):
        page = ContentMainPage(title="x", catalogue_repository="not a repo")
        with self.assertRaises(ValidationError):
            page.clean()


@override_settings(FEATURE_FLAGS=guidance_flags())
class GroupPageTests(CatalogueTestCase):
    def test_a_section_per_type_with_its_primary_entries_first(self):
        response = self.get("standards-and-policies/")
        self.assertEqual(response.status_code, 200)
        sections = [
            (s["title"], [(c["title"], c["primary"]) for c in s["cards"]])
            for s in response.context["type_sections"]
        ]
        self.assertEqual(
            sections,
            [
                ("Standards", [("Hosted standard", True), ("Another standard", False)]),
                ("Policies", [("External policy", True), ("Hosted policy", False)]),
            ],
        )
        # No section of its own for the primary entries: their cards stand out.
        self.assertNotContains(response, "Key standards and policies")
        self.assertContains(response, "app-catalogue-card app-catalogue-card--primary")

    def test_cards_say_where_they_come_from_and_nothing_more(self):
        response = self.get("standards-and-policies/")
        self.assertContains(response, "Source: GOV.UK")
        self.assertNotContains(response, "govuk-tag")
        # Content held in the catalogue is this site's own: no source.
        hosted = next(
            card
            for section in response.context["type_sections"]
            for card in section["cards"]
            if card["title"] == "Hosted standard"
        )
        self.assertEqual(hosted["source_name"], "")
        # Primary entries are not badged anywhere.
        for path in ("standards-and-policies/", "all/"):
            self.assertNotContains(self.get(path), ">Key</strong>")

    def test_external_cards_link_straight_to_the_source(self):
        response = self.get("standards-and-policies/")
        self.assertContains(response, 'href="https://www.gov.uk/guidance/external-policy"')
        self.assertContains(response, f'href="{self.base}standards-and-policies/hosted-standard/"')

    def test_filters_and_search_narrow_the_cards(self):
        response = self.get("standards-and-policies/", profession="architecture")
        titles = [c["title"] for s in response.context["type_sections"] for c in s["cards"]]
        self.assertEqual(titles, ["Another standard"])
        response = self.get("standards-and-policies/", q="another")
        self.assertEqual([c["title"] for c in response.context["result_cards"]], ["Another standard"])
        self.assertContains(response, "1 result")

    def test_the_filters_are_dropdowns_that_keep_the_chosen_value(self):
        response = self.get("all/", profession="architecture")
        for name in ("tag", "profession", "type", "scope"):
            self.assertContains(response, f'<select class="govuk-select" id="catalogue-{name}" name="{name}">', html=False)
        self.assertContains(response, '<option value="architecture" selected>Architecture (1)</option>', html=False)
        self.assertContains(response, "Clear search and filters")

    def test_the_all_page_lists_every_group(self):
        response = self.get("all/")
        self.assertEqual(response.context["result_count"], 7)
        self.assertEqual(response.context["result_cards"][0]["title"], "External policy")
        response = self.get("all/", tag="security")
        self.assertEqual(response.context["result_count"], 2)

    def test_unknown_groups_and_blogs_404(self):
        self.assertEqual(self.get("blogs/").status_code, 404)
        self.assertEqual(self.get("nope/").status_code, 404)

    def test_breadcrumbs(self):
        response = self.get("guidance/")
        self.assertEqual(
            [c["title"] for c in response.context["breadcrumbs"]][-2:], ["Content", "Guidance"]
        )


@override_settings(FEATURE_FLAGS=guidance_flags())
class EntryPageTests(CatalogueTestCase):
    def test_requirements_front_page_lists_the_numbered_points(self):
        response = self.get("standards-and-policies/hosted-standard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(p["number"], p["title"], p["summary"]) for p in response.context["sequence"]],
            [(1, "The first point", "Do the first thing."), (2, "The second point", "Then the second.")],
        )
        self.assertContains(response, "The introduction.")
        # The standard's own link to a point is pointed at its page here.
        self.assertContains(response, f'href="{self.base}standards-and-policies/hosted-standard/first-point/"')
        self.assertNotContains(response, "guidance:/")
        # Related entries are listed as cards.
        self.assertEqual([c["title"] for c in response.context["entry"]["related"]], ["External policy"])

    def test_a_requirement_page_has_its_place_and_previous_and_next(self):
        response = self.get("standards-and-policies/hosted-standard/second-point/")
        # Every point's caption is the standard it belongs to; its place is in
        # the previous and next links.
        self.assertEqual(response.context["caption"], "Hosted standard")
        self.assertEqual(response.context["sequence_previous"]["title"], "The first point")
        self.assertIsNone(response.context["sequence_next"])
        self.assertEqual(
            [c["title"] for c in response.context["breadcrumbs"]][-3:],
            ["Standards and policies", "Hosted standard", "The second point"],
        )

    def test_images_widen_the_content_security_policy_for_that_page_only(self):
        response = self.get("standards-and-policies/hosted-standard/first-point/")
        self.assertIn("https://assets.example.gov.uk", response.headers["Content-Security-Policy"])
        response = self.get("standards-and-policies/hosted-standard/")
        self.assertNotIn("assets.example.gov.uk", response.headers["Content-Security-Policy"])

    def test_a_policy_has_its_metadata_and_numbered_accordion_sections(self):
        response = self.get("standards-and-policies/hosted-policy/")
        self.assertContains(response, 'class="govuk-accordion app-catalogue-policy"')
        self.assertContains(response, "app-catalogue-masthead")
        self.assertEqual(response.context["caption"], "Policy")
        self.assertEqual(
            [fact["label"] for fact in response.context["masthead_facts"]],
            ["Version", "Effective from", "Last reviewed", "Next review"],
        )
        self.assertContains(response, '<span id="policy-statement">1. Policy statement</span>', html=False)
        self.assertContains(response, '<span id="scope">2. Scope</span>', html=False)
        self.assertContains(response, "2.1")
        self.assertContains(response, "1 April 2026")
        self.assertContains(response, "1 January 2027")

    def test_a_guide_has_its_nested_side_navigation(self):
        response = self.get("guidance/repo-guide/topics/alerting/escalation/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "app-guidance-nav__list--nested")
        self.assertEqual(
            [c["title"] for c in response.context["breadcrumbs"]][-3:],
            ["Repo guide", "Alerting", "Escalation"],
        )

    def test_steps_are_numbered_with_reference_pages_beside_them(self):
        response = self.get("guidance/hosted-method/run/")
        self.assertEqual(response.context["caption"], "Step 2 of 2")
        self.assertEqual(response.context["sequence_previous"]["title"], "Prepare")
        self.assertEqual(
            [group["title"] for group in response.context["entry_navigation"]], ["Reference"]
        )
        self.assertContains(response, "1. Prepare")

    def test_every_entry_page_ends_with_its_details(self):
        response = self.get("guidance/hosted-method/")
        self.assertContains(response, "About this method")
        self.assertContains(response, '<strong class="app-catalogue-badge">Engineering</strong>', html=False)
        self.assertContains(response, '<strong class="app-catalogue-badge app-catalogue-badge--grey">Cloud</strong>', html=False)
        self.assertContains(response, "https://github.com/example/catalogue/issues/new?title=Feedback+on")
        self.assertContains(
            response, "https://github.com/example/catalogue/blob/main/entries/hosted-method.yml"
        )
        # Related entries are links in the details, not a section of cards.
        response = self.get("standards-and-policies/hosted-standard/")
        self.assertContains(response, 'href="https://www.gov.uk/guidance/external-policy">External policy</a> (GOV.UK)', html=False)

    def test_a_methods_front_page_starts_at_its_first_step(self):
        response = self.get("guidance/hosted-method/")
        self.assertContains(response, "govuk-button--start")
        self.assertContains(response, f'href="{self.base}guidance/hosted-method/prepare/" role="button"', html=False)

    def test_a_tool_page_comes_from_the_entry(self):
        response = self.get("resources-and-tools/linked-tool/")
        self.assertContains(response, "v3")
        self.assertContains(response, 'href="https://tool.example.gov.uk/">Use it</a>', html=False)
        self.assertNotContains(response, "Not https")
        self.assertEqual(self.get("resources-and-tools/linked-tool/more/").status_code, 404)

    def test_an_entry_in_the_wrong_group_redirects(self):
        response = self.get("guidance/hosted-standard/first-point/")
        self.assertRedirects(
            response,
            f"{self.base}standards-and-policies/hosted-standard/first-point/",
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_external_and_unknown_entries_404(self):
        self.assertEqual(self.get("standards-and-policies/external-policy/").status_code, 404)
        self.assertEqual(self.get("standards-and-policies/no-such-entry/").status_code, 404)
        self.assertEqual(self.get("standards-and-policies/hosted-standard/nope/").status_code, 404)


@override_settings(FEATURE_FLAGS=guidance_flags(guidance_enabled=False))
class FlagOffTests(CatalogueTestCase):
    def test_nothing_is_served(self):
        for path in ("", "all/", "guidance/", "standards-and-policies/hosted-standard/"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)

    def test_the_page_type_cannot_be_created(self):
        self.assertFalse(ContentMainPage.can_create_at(self.root))

    def test_it_is_left_out_of_the_pages_api(self):
        response = self.client.get("/api/pages/?type=govuk.ContentMainPage")
        self.assertEqual(response.json()["meta"]["total_count"], 0)

    def test_the_sync_command_refuses(self):
        with self.assertRaises(CommandError):
            call_command("sync_content_catalogue", stdout=StringIO())

    def test_the_sync_view_404s(self):
        admin = get_user_model().objects.create_superuser("admin", "a@example.gov.uk", "x")
        self.client.force_login(admin)
        response = self.client.get(reverse("govuk_catalogue_sync", args=[self.page.pk]))
        self.assertEqual(response.status_code, 404)


@override_settings(FEATURE_FLAGS=guidance_flags())
class AdminTests(CatalogueTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("admin", "a@example.gov.uk", "x")
        self.url = reverse("govuk_catalogue_sync", args=[self.page.pk])

    def test_the_edit_view_shows_the_sync_status(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("wagtailadmin_pages:edit", args=[self.page.pk]))
        self.assertContains(response, "7 entries synced")
        self.assertContains(response, "Guidance 2")
        self.assertContains(response, self.url)

    def test_the_add_view_renders(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("wagtailadmin_pages:add", args=["govuk", "contentmainpage", self.root.pk])
        )
        self.assertContains(response, "The catalogue is synced when this page is first published.")

    def test_sync_now(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), "Sync now")
        with mock.patch("govuk.wagtail_hooks.catalogue.sync_catalogue_page") as sync:
            sync.return_value.summary.return_value = "Synced."
            response = self.client.post(self.url, {"force": "1"})
        self.assertRedirects(
            response, reverse("wagtailadmin_pages:edit", args=[self.page.pk]), fetch_redirect_response=False
        )
        self.assertTrue(sync.call_args.kwargs["force"])

    def test_sync_now_needs_publish_permission(self):
        user = get_user_model().objects.create_user(username="nobody", password="x")
        user.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="wagtailadmin", codename="access_admin")
        )
        self.client.force_login(user)
        with mock.patch("govuk.wagtail_hooks.catalogue.sync_catalogue_page") as sync:
            self.client.post(self.url)
        sync.assert_not_called()

    def test_publishing_a_new_page_syncs_it(self):
        self.client.force_login(self.admin)
        page = self.root.add_child(
            instance=ContentMainPage(title="New", slug="new", catalogue_repository="example/other")
        )
        with mock.patch("govuk.wagtail_hooks.catalogue.sync_catalogue_page") as sync:
            sync.return_value.summary.return_value = "Synced."
            self.client.post(
                reverse("wagtailadmin_pages:edit", args=[page.pk]),
                {
                    "title": "New",
                    "slug": "new",
                    "catalogue_repository": "example/other",
                    "catalogue_branch": "main",
                    "body-count": "0",
                    "tagged_items-TOTAL_FORMS": "0",
                    "tagged_items-INITIAL_FORMS": "0",
                    "tagged_items-MIN_NUM_FORMS": "0",
                    "tagged_items-MAX_NUM_FORMS": "1000",
                    "hero_style": page.hero_style,
                    "action-publish": "publish",
                },
            )
        sync.assert_called_once()

    def test_the_sync_command(self):
        with mock.patch("govuk.management.commands.sync_content_catalogue.sync_catalogue_page") as sync:
            sync.return_value.summary.return_value = "Synced."
            sync.return_value.warnings = []
            out = StringIO()
            call_command("sync_content_catalogue", stdout=out)
        self.assertIn("Synced 1 of 1 pages.", out.getvalue())
