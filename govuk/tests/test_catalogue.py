"""The content catalogue's pages: where they go, what they serve, how search finds them, and the admin around them."""

import re
from datetime import datetime, timezone
from io import StringIO
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, Site

from govuk.ai_search.views import _browse_url
from govuk.catalogue.colours import (
    colour_class,
    contrast,
    star_colour,
    style_sheet,
    tag_colours,
)
from govuk.models import (
    CatalogueScope,
    CatalogueType,
    ContentEntryExternalPage,
    ContentEntryGenericPage,
    ContentEntryGuidePage,
    ContentEntryRequirementsPage,
    ContentGroupPage,
    ContentGuidePage,
    ContentMainPage,
    ContentPage,
    ContentRequirementPage,
    HarvestedSource,
    catalogue_page_models,
    catalogue_type_labels,
    without_catalogue_pages,
)
from govuk.search_backend import search_backend
from govuk.tests.catalogue_helpers import (
    catalogue_flags,
    import_fixture,
    make_content_main_page,
)


class CatalogueTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific
        self.page = make_content_main_page(self.root)
        with override_settings(FEATURE_FLAGS=catalogue_flags()):
            self.result = import_fixture(self.page)
        self.base = self.page.relative_url(self.site)

    def get(self, path="", **params):
        return self.client.get(self.base + path, params)


@override_settings(FEATURE_FLAGS=catalogue_flags())
class PlacementTests(TestCase):
    def test_parent_and_subpage_types_agree(self):
        for model in catalogue_page_models():
            for parent in model.allowed_parent_page_models():
                with self.subTest(model=model.__name__, parent=parent.__name__):
                    self.assertIn(model, parent.allowed_subpage_models())
            for child in model.allowed_subpage_models():
                with self.subTest(model=model.__name__, child=child.__name__):
                    self.assertIn(model, child.allowed_parent_page_models())

    def test_each_level_goes_only_where_it_belongs(self):
        self.assertEqual(ContentGroupPage.allowed_parent_page_models(), [ContentMainPage])
        self.assertEqual(ContentEntryGuidePage.allowed_parent_page_models(), [ContentGroupPage])
        self.assertEqual(
            ContentRequirementPage.allowed_parent_page_models(), [ContentEntryRequirementsPage]
        )
        self.assertIn(ContentGuidePage, ContentGuidePage.allowed_subpage_models())
        self.assertIn(ContentMainPage, apps.get_model("govuk.ContentPage").allowed_subpage_models())

    def test_an_entry_that_breaks_its_layouts_rules_is_invalid(self):
        scope = CatalogueScope.objects.create(key="public-sector", label="Public sector")
        policy = CatalogueType.objects.create(key="policy", label="Policy", layout="policy")
        for label, model in (
            ("a type of another layout", ContentEntryRequirementsPage),
            ("an external entry without its address", ContentEntryExternalPage),
        ):
            with self.subTest(label):
                page = model(
                    title="x", entry_type=policy, scope=scope, short_description="x",
                    long_description="x", owner_organisation="x",
                )
                with self.assertRaises(ValidationError):
                    page.clean()


@override_settings(FEATURE_FLAGS=catalogue_flags())
class MainPageTests(CatalogueTestCase):
    def test_the_main_page_is_a_content_page(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "govuk/content_page.html")

    def test_its_own_content_pages_are_served(self):
        child = self.page.add_child(instance=ContentPage(title="About", slug="about"))
        child.save_revision().publish()
        self.assertEqual(self.get("about/").status_code, 200)

    def test_the_all_page_lists_every_entry_primary_first(self):
        response = self.get("all/")
        self.assertEqual(response.context["result_count"], 7)
        self.assertTrue(response.context["result_cards"][0]["primary"])
        self.assertEqual(self.get("all/", tag="security").context["result_count"], 2)
        self.assertEqual(
            [c["title"] for c in response.context["breadcrumbs"]][-2:], ["Content", "All content"]
        )

    def test_the_filters_are_dropdowns_that_keep_the_chosen_value(self):
        response = self.get("all/", profession="architecture")
        for name in ("tag", "profession", "type"):
            self.assertContains(
                response, f'<select class="govuk-select" id="catalogue-{name}" name="{name}">', html=False
            )
        self.assertNotContains(response, 'id="catalogue-scope"')
        self.assertContains(response, '<label class="govuk-label govuk-label--s" for="catalogue-q">Keyword search</label>')
        self.assertContains(response, '<label class="govuk-label govuk-label--s" for="catalogue-type">Content type</label>')
        self.assertContains(
            response, '<option value="architecture" selected>Architecture (1)</option>', html=False
        )

    def test_the_counts_are_of_what_the_search_would_show(self):
        response = self.get("all/", q="another")
        options = response.context["filter_form"]["fields"]
        profession = next(field for field in options if field["name"] == "profession")
        self.assertEqual(
            [(o["value"], o["count"]) for o in profession["options"]], [("architecture", 1)]
        )

    def test_counts_for_the_form_as_it_stands_come_as_json(self):
        cases = [
            ("all/", {}, 7, ("type", "policy", 2)),
            ("all/", {"profession": "architecture"}, 1, ("type", "policy", 0)),
            ("all/", {"q": "another"}, 1, ("profession", "engineering", 0)),
            ("standards-and-policies/", {"type": "standard"}, 2, ("type", "policy", 2)),
        ]
        for path, params, count, (name, value, option_count) in cases:
            with self.subTest(path=path, params=params):
                response = self.get(path, counts="1", **params)
                self.assertEqual(response["Content-Type"], "application/json")
                data = response.json()
                self.assertEqual(data["count"], count)
                option = next(o for o in data["fields"][name] if o["value"] == value)
                self.assertEqual(option["count"], option_count)

    def test_the_form_has_somewhere_to_say_what_apply_would_give(self):
        response = self.get("all/")
        self.assertContains(response, "data-catalogue-filters>")
        self.assertContains(response, 'role="status" data-catalogue-filters-status')
        self.assertContains(response, "catalogue.js")


@override_settings(FEATURE_FLAGS=catalogue_flags())
class AllContentOnMainPageTests(CatalogueTestCase):
    """The main page set to list every entry itself, rather than on "all"."""

    def setUp(self):
        super().setUp()
        self.page.all_content_location = ContentMainPage.AllContentLocation.THIS_PAGE
        self.page.save()

    def test_by_default_the_main_page_lists_nothing_and_all_does(self):
        self.page.all_content_location = ContentMainPage.AllContentLocation.ALL_PAGE
        self.page.save()
        self.assertNotIn("result_cards", self.get().context)
        self.assertEqual(self.get("all/").context["result_count"], 7)

    def test_the_main_page_lists_every_entry_below_its_own_content(self):
        response = self.get()
        self.assertTemplateUsed(response, "govuk/content_page.html")
        self.assertEqual(response.context["result_count"], 7)
        html = response.content.decode()
        self.assertIn('<div class="govuk-width-container govuk-!-margin-bottom-6 app-catalogue" id="all-content">', html)
        # No heading of its own, so the cards are the level below the page's h1.
        self.assertNotIn(">All content</h2>", html)
        self.assertIn('<h2 class="govuk-heading-s app-catalogue-card__title">', html)
        self.assertIn('<form method="get" action="#all-content" class="app-catalogue-filters"', html)
        self.assertIn("catalogue.js", html)
        self.assertEqual(self.get(tag="security").context["result_count"], 2)

    def test_pages_and_apply_come_back_to_the_listing(self):
        with mock.patch("govuk.catalogue.presentation.ALL_PAGE_SIZE", 1):
            html = self.get(tag="security").content.decode()
        self.assertIn('href="?tag=security&amp;page=2#all-content"', html)

    def test_it_answers_the_counts_itself(self):
        response = self.get(counts="1", profession="architecture")
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["count"], 1)

    def test_all_comes_to_the_main_page_with_its_search_and_filters(self):
        response = self.get("all/", tag="security")
        self.assertEqual(response.status_code, 302)  # not 301: the setting can be changed back
        self.assertEqual(response["Location"], f"{self.base}?tag=security#all-content")

    def test_links_to_all_content_go_to_the_main_page(self):
        self.assertContains(self.get("standards-and-policies/"), f'href="{self.base}#all-content"')
        self.assertEqual(_browse_url(), f"{self.base}#all-content")


@override_settings(FEATURE_FLAGS=catalogue_flags())
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
        self.assertContains(response, "app-catalogue-card--primary")

    def test_cards_link_to_their_page_or_straight_to_the_source(self):
        response = self.get("standards-and-policies/")
        self.assertContains(response, 'href="https://www.gov.uk/guidance/external-policy"')
        self.assertContains(response, f'href="{self.base}standards-and-policies/hosted-standard/"')
        self.assertContains(response, "Source: GOV.UK")
        hosted = next(
            card
            for section in response.context["type_sections"]
            for card in section["cards"]
            if card["title"] == "Hosted standard"
        )
        self.assertEqual(hosted["source_name"], "")

    def test_each_card_has_its_types_tag_and_colour(self):
        response = self.get("standards-and-policies/")
        html = response.content.decode()
        self.assertIn('<strong class="govuk-tag app-catalogue-card__tag">Policy</strong>', html)
        self.assertIn('<li class="app-catalogue-card app-colour-8f338e app-catalogue-card--primary">', html)
        self.assertIn('<li class="app-catalogue-card app-colour-1d70b8 app-catalogue-card--primary">', html)
        # The CSP blocks style attributes, so the colours are a nonced style sheet.
        nonce = re.search(r'<style nonce="([^"]+)">li\.app-colour-', html).group(1)
        self.assertIn(f"'nonce-{nonce}'", response["Content-Security-Policy"])
        self.assertIn(
            "li.app-colour-8f338e{--app-card-colour:#8f338e;--app-card-star:#ffffff;"
            "--app-tag-text:#481a47;--app-tag-background:#e9d6e8}",
            html,
        )
        # The starred corner is drawn by CSS, so its meaning is also in the heading.
        self.assertIn('>Hosted standard</a><span class="govuk-visually-hidden">, featured</span>', html)
        self.assertNotIn('>Another standard</a><span class="govuk-visually-hidden">', html)

    def test_a_hosted_entrys_card_says_when_it_was_published_or_updated(self):
        entry = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        Page.objects.filter(pk=entry.pk).update(
            first_published_at=datetime(2026, 3, 2, 9, tzinfo=timezone.utc),
            last_published_at=datetime(2026, 3, 2, 17, tzinfo=timezone.utc),
        )
        self.assertContains(
            self.get("standards-and-policies/"), 'Published <time datetime="2026-03-02">2 March 2026</time>'
        )
        Page.objects.filter(pk=entry.pk).update(last_published_at=datetime(2026, 4, 5, tzinfo=timezone.utc))
        self.assertContains(
            self.get("standards-and-policies/"), 'Last updated <time datetime="2026-04-05">5 April 2026</time>'
        )

    def test_an_external_entrys_card_has_its_sources_date_or_none(self):
        response = self.get("standards-and-policies/")
        external = next(
            card for s in response.context["type_sections"] for card in s["cards"] if card["title"] == "External policy"
        )
        # Not the date it was listed here, which says nothing of the source.
        self.assertIsNone(external["updated"])
        HarvestedSource.objects.create(
            url="https://www.gov.uk/guidance/external-policy",
            source_updated_at=datetime(2025, 11, 20, 12, tzinfo=timezone.utc),
        )
        html = self.get("standards-and-policies/").content.decode()
        self.assertIn(
            '<p class="govuk-body-s">Last updated <time datetime="2025-11-20">20 November 2025</time></p>\n'
            '            <p class="govuk-body-s">Source: GOV.UK</p>',
            html,
        )

    def test_an_unpublished_entry_leaves_its_group(self):
        ContentEntryRequirementsPage.objects.get(slug="hosted-standard").unpublish()
        titles = [
            c["title"]
            for s in self.get("standards-and-policies/").context["type_sections"]
            for c in s["cards"]
        ]
        self.assertNotIn("Hosted standard", titles)

    def test_filters_and_search_narrow_the_cards(self):
        response = self.get("standards-and-policies/", profession="architecture")
        titles = [c["title"] for s in response.context["type_sections"] for c in s["cards"]]
        self.assertEqual(titles, ["Another standard"])
        response = self.get("standards-and-policies/", q="another")
        self.assertEqual([c["title"] for c in response.context["result_cards"]], ["Another standard"])

    def test_its_own_blocks_show_above_the_entries(self):
        group = ContentGroupPage.objects.get(slug="guidance")
        group.body = [{"type": "text", "value": "<p>Read these first.</p>"}]
        group.save_revision().publish()
        self.assertContains(self.get("guidance/"), "Read these first.")


@override_settings(FEATURE_FLAGS=catalogue_flags())
class EntryPageTests(CatalogueTestCase):
    def test_an_external_entry_redirects_to_its_source(self):
        response = self.get("standards-and-policies/external-policy/")
        self.assertRedirects(
            response, "https://www.gov.uk/guidance/external-policy", fetch_redirect_response=False
        )

    def test_requirements_front_page_lists_the_numbered_points(self):
        response = self.get("standards-and-policies/hosted-standard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(p["number"], p["title"], p["summary"]) for p in response.context["sequence"]],
            [(1, "The first point", "Do the first thing."), (2, "The second point", "Then the second.")],
        )
        self.assertContains(response, "The introduction.")
        # The standard's link to a point became a page link, served as its address.
        self.assertContains(
            response, f'href="{self.base}standards-and-policies/hosted-standard/first-point/"'
        )
        self.assertEqual(
            [c["title"] for c in response.context["entry"]["related"]], ["External policy"]
        )

    def test_points_are_numbered_by_their_order_in_the_tree(self):
        response = self.get("standards-and-policies/hosted-standard/second-point/")
        self.assertEqual(response.context["caption"], "Hosted standard")
        self.assertEqual(response.context["sequence_previous"]["title"], "The first point")
        self.assertIsNone(response.context["sequence_next"])
        second = ContentRequirementPage.objects.get(slug="second-point")
        second.move(ContentRequirementPage.objects.get(slug="first-point"), pos="left")
        response = self.get("standards-and-policies/hosted-standard/second-point/")
        self.assertIsNone(response.context["sequence_previous"])
        self.assertEqual(response.context["sequence_number"], 1)

    def test_headings_get_anchors_and_a_contents_list(self):
        response = self.get("guidance/repo-guide/alerting/")
        self.assertContains(response, '<h2 id="sending-alerts">Sending alerts</h2>', html=False)
        self.assertEqual(
            [item["anchor"] for item in response.context["contents"]], ["sending-alerts"]
        )

    def test_images_widen_the_content_security_policy_for_that_page_only(self):
        response = self.get("standards-and-policies/hosted-standard/first-point/")
        self.assertIn("https://assets.example.gov.uk", response.headers["Content-Security-Policy"])
        response = self.get("standards-and-policies/hosted-standard/")
        self.assertNotIn("assets.example.gov.uk", response.headers["Content-Security-Policy"])

    def test_a_policy_has_its_metadata_and_numbered_accordion_sections(self):
        response = self.get("standards-and-policies/hosted-policy/")
        self.assertContains(response, 'class="govuk-accordion app-catalogue-policy"')
        self.assertEqual(response.context["caption"], "Policy")
        self.assertEqual(
            [fact["label"] for fact in response.context["masthead_facts"]],
            ["Version", "Effective from", "Last reviewed", "Next review"],
        )
        self.assertContains(response, '<span id="policy-statement">1. Policy statement</span>', html=False)
        self.assertContains(response, '<span id="scope">2. Scope</span>', html=False)
        self.assertContains(response, "1 April 2026")
        self.assertContains(response, "1 January 2027")

    def test_a_guide_has_its_nested_side_navigation(self):
        response = self.get("guidance/repo-guide/alerting/escalation/")
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

    def test_a_methods_front_page_starts_at_its_first_step(self):
        response = self.get("guidance/hosted-method/")
        self.assertContains(response, "govuk-button--start")
        self.assertContains(
            response, f'href="{self.base}guidance/hosted-method/prepare/" role="button"', html=False
        )

    def test_every_entry_page_ends_with_its_details(self):
        response = self.get("guidance/hosted-method/")
        self.assertContains(response, "About this method")
        self.assertContains(
            response, '<strong class="app-catalogue-badge">Engineering</strong>', html=False
        )
        self.assertContains(
            response, '<strong class="app-catalogue-badge app-catalogue-badge--grey">Cloud</strong>', html=False
        )
        self.assertNotContains(response, "github.com/example/catalogue")

    def test_a_tool_page_comes_from_the_entry(self):
        response = self.get("resources-and-tools/linked-tool/")
        self.assertContains(response, "v3")
        self.assertContains(response, 'href="https://tool.example.gov.uk/">Use it</a>', html=False)
        self.assertNotContains(response, "Not https")

    def test_an_entrys_search_description_is_its_card_sentence(self):
        page = ContentEntryRequirementsPage.objects.get(slug="hosted-standard")
        self.assertEqual(page.search_description, "Short description of hosted-standard")


@override_settings(FEATURE_FLAGS=catalogue_flags())
class StyleSheetTests(CatalogueTestCase):
    def test_the_catalogue_style_sheet_is_loaded_with_the_catalogue_on(self):
        self.assertContains(self.get("all/"), "/static/catalogue.css")

    @override_settings(FEATURE_FLAGS=catalogue_flags(enabled=False))
    def test_and_not_without_it(self):
        self.assertNotContains(self.client.get("/"), "catalogue.css")


@override_settings(FEATURE_FLAGS=catalogue_flags(enabled=False))
class FlagOffTests(CatalogueTestCase):
    def test_nothing_is_served(self):
        for path in (
            "", "all/", "guidance/", "standards-and-policies/hosted-standard/",
            "standards-and-policies/hosted-standard/first-point/",
            "standards-and-policies/external-policy/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)
        for path in ("all/", "guidance/"):
            with self.subTest(path=path, counts=True):
                self.assertEqual(self.get(path, counts="1").status_code, 404)

    def test_no_catalogue_page_can_be_created(self):
        group = ContentGroupPage.objects.first()
        self.assertFalse(ContentMainPage.can_create_at(self.root))
        self.assertFalse(ContentGroupPage.can_create_at(self.page))
        self.assertFalse(ContentEntryGuidePage.can_create_at(group))

    def test_its_pages_are_left_out_of_public_listings(self):
        self.assertFalse(
            without_catalogue_pages(Page.objects.descendant_of(self.root, inclusive=True))
            .type(*catalogue_page_models())
            .exists()
        )
        response = self.client.get("/api/pages/?type=govuk.ContentMainPage")
        self.assertEqual(response.json()["meta"]["total_count"], 0)

    def test_the_commands_refuse(self):
        for command, args in (
            ("import_content_catalogue", [str(self.page.pk), "example/catalogue"]),
            ("export_content_catalogue", [str(self.page.pk), "/tmp/never-written"]),
        ):
            with self.subTest(command=command), self.assertRaises(CommandError):
                call_command(command, *args, stdout=StringIO())

    def test_the_admin_views_404(self):
        admin = get_user_model().objects.create_superuser("admin", "a@example.gov.uk", "x")
        self.client.force_login(admin)
        for name in ("govuk_catalogue_import", "govuk_catalogue_export"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=[self.page.pk]))
                self.assertEqual(response.status_code, 404)


@override_settings(FEATURE_FLAGS=catalogue_flags())
class AdminTests(CatalogueTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser("admin", "a@example.gov.uk", "x")
        self.import_url = reverse("govuk_catalogue_import", args=[self.page.pk])
        self.export_url = reverse("govuk_catalogue_export", args=[self.page.pk])

    def test_the_main_pages_edit_view_links_to_import_and_export(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("wagtailadmin_pages:edit", args=[self.page.pk]))
        self.assertContains(response, self.import_url)
        self.assertContains(response, self.export_url)

    def test_every_catalogue_page_types_edit_view_renders(self):
        self.client.force_login(self.admin)
        for model in catalogue_page_models():
            page = model.objects.first()
            if page is None:
                continue
            with self.subTest(model=model.__name__):
                response = self.client.get(reverse("wagtailadmin_pages:edit", args=[page.pk]))
                self.assertEqual(response.status_code, 200)

    def test_the_vocabulary_snippets_are_listed(self):
        self.client.force_login(self.admin)
        for model in ("cataloguetype", "cataloguescope", "catalogueprofession"):
            with self.subTest(model=model):
                response = self.client.get(f"/admin/snippets/govuk/{model}/")
                self.assertEqual(response.status_code, 200)

    def test_the_export_is_a_zip(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.export_url)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn('filename="content-catalogue.zip"', response["Content-Disposition"])

    def test_an_import_needs_a_zip_or_a_repository(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.import_url, {})
        self.assertContains(response, "Choose a zip file or enter a repository, not both.")

    def test_a_zip_imports(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.admin)
        archive = self.client.get(self.export_url).content
        other = make_content_main_page(self.root, title="Other", slug="other")
        response = self.client.post(
            reverse("govuk_catalogue_import", args=[other.pk]),
            {"zip_file": SimpleUploadedFile("c.zip", archive, "application/zip")},
        )
        self.assertContains(response, "pages created")
        self.assertTrue(ContentGroupPage.objects.child_of(other).filter(slug="guidance").exists())

    def test_import_and_export_need_publish_permission(self):
        user = get_user_model().objects.create_user(username="nobody", password="x")
        user.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="wagtailadmin", codename="access_admin")
        )
        self.client.force_login(user)
        for url in (self.import_url, self.export_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotEqual(response.status_code, 200)


BODY = [
    {"type": "text", "value": "<h2>Why it exists</h2><p>Opening text.</p>"},
    {"type": "banner", "value": {"heading": "Join the community", "style": "light", "text": "<p>Every month.</p>"}},
    {"type": "warning_text", "value": "<p>Mind the gap.</p>"},
    {"type": "raw_html", "value": '<p><img src="https://assets.example.gov.uk/a.png" alt=""></p>'},
]


@override_settings(FEATURE_FLAGS=catalogue_flags())
class GenericEntryTests(CatalogueTestCase):
    """The generic entry layout: an entry of any type, around a body laid out as a content page's is."""

    def setUp(self):
        super().setUp()
        self.group = ContentGroupPage.objects.get(slug="resources-and-tools")
        # A product: its type's own layout is the tool layout.
        self.type = CatalogueType.objects.get(key="product")
        self.entry = self.group.add_child(
            instance=ContentEntryGenericPage(
                title="Local architecture model",
                slug="architecture-model",
                entry_type=self.type,
                scope=CatalogueScope.objects.get(key="public-sector"),
                short_description="The technology layers local services are built on.",
                long_description="A guide to the public technology layers that deliver local services.",
                owner_organisation="Government Digital Service",
                body=BODY,
            )
        )
        self.entry.save_revision().publish()

    def get_page(self, page=None):
        return self.client.get((page or self.entry).url)

    def test_any_type_fits_it(self):
        for key in ("product", "guidance", "standard", "policy"):
            with self.subTest(type=key):
                self.entry.entry_type = CatalogueType.objects.get(key=key)
                self.entry.clean()
        self.assertEqual(ContentEntryGenericPage.allowed_subpage_models(), [])
        self.assertIn(ContentEntryGenericPage, ContentGroupPage.allowed_subpage_models())

    def test_the_add_view_offers_every_type(self):
        admin = get_user_model().objects.create_superuser("admin", "a@example.gov.uk", "x")
        self.client.force_login(admin)
        response = self.client.get(
            reverse("wagtailadmin_pages:add", args=["govuk", "contententrygenericpage", self.group.pk])
        )
        for entry_type in CatalogueType.objects.all():
            self.assertContains(response, f'<option value="{entry_type.pk}">{entry_type.label}</option>', html=False)
        self.assertContains(response, "Any type: this page lays it out freely")

    def test_it_has_the_entrys_heading_and_details(self):
        response = self.get_page()
        self.assertTemplateUsed(response, "catalogue/layouts/generic.html")
        self.assertContains(response, '<h1 class="govuk-heading-xl">Local architecture model</h1>', html=False)
        self.assertContains(response, "A guide to the public technology layers that deliver local services.")
        self.assertContains(response, "About this product")
        # Free-form: no masthead or contents list, whatever its type's layout.
        self.assertNotContains(response, "app-catalogue-masthead")
        self.assertIsNone(response.context.get("contents"))

    def test_its_body_is_laid_out_as_a_content_pages(self):
        response = self.get_page()
        self.assertContains(response, "body-banner--light body-band", html=False)
        self.assertContains(response, '<div class="govuk-width-container body-segment">', html=False)
        self.assertContains(response, "Opening text.")
        self.assertContains(response, 'class="govuk-warning-text"')
        # The same blocks in a guide stay in its column.
        guide = ContentEntryGuidePage.objects.get(slug="repo-guide")
        guide.body = BODY
        guide.save_revision().publish()
        self.assertContains(self.get_page(guide), "body-panel")

    def test_its_images_widen_its_content_security_policy(self):
        self.assertIn("https://assets.example.gov.uk", self.get_page().headers["Content-Security-Policy"])

    def test_it_is_listed_and_searched_as_its_type(self):
        sections = self.client.get(self.group.url).context["type_sections"]
        products = next(s for s in sections if s["title"] == "Products")
        self.assertIn("Local architecture model", [c["title"] for c in products["cards"]])
        items = self.client.get("/search/suggest/", {"q": "architecture model"}).json()
        self.assertEqual(
            items[0], {"text": "Local architecture model", "link": self.entry.url, "type": "Product"}
        )


@override_settings(FEATURE_FLAGS=catalogue_flags())
class CatalogueSearchTests(CatalogueTestCase):
    """Its type as a badge, after the site's own pages."""

    def setUp(self):
        super().setUp()
        # One of the site's own pages, matching "standard" less well than the
        # catalogue's entries do: only in its summary, not its title.
        self.own_page = self.root.add_child(
            instance=ContentPage(
                title="Writing for the site",
                slug="writing",
                search_description="How to meet our standard for plain English.",
            )
        )
        self.own_page.save_revision().publish()

    def _suggest(self, q):
        response = self.client.get("/search/suggest/", {"q": q})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_entries_and_their_pages_are_labelled_with_their_type(self):
        point = ContentRequirementPage.objects.get(slug="first-point")
        labels = catalogue_type_labels([point.get_parent().specific, point, self.own_page])
        self.assertEqual(labels, {point.get_parent().pk: "Standard", point.pk: "Standard"})

        items = self._suggest("standard")
        self.assertIn({"text": "Another standard", "link": "/content/standards-and-policies/external-standard/", "type": "Standard"}, items)
        items = self._suggest("first point")
        self.assertEqual(items[0]["text"], "The first point")
        self.assertEqual(items[0]["type"], "Standard")

    def test_the_sites_own_pages_come_before_the_catalogues(self):
        request = RequestFactory().get("/search/")
        ranked = [
            item.title
            for item in search_backend.search(
                "standard", {"request": request, "site": self.site, "live": True, "public": True}
            ).paginator.object_list
        ]
        # On relevance alone an entry outranks the page...
        self.assertLess(ranked.index("Another standard"), ranked.index("Writing for the site"))

        items = self._suggest("standard")
        kinds = [item["type"] for item in items]
        # ...but the box lists the site's own pages first, the group page
        # among them, then the catalogue's entries in their relevance order.
        self.assertEqual(kinds[: kinds.count("Page")], ["Page"] * kinds.count("Page"))
        texts = [item["text"] for item in items]
        self.assertIn("Writing for the site", texts[: kinds.count("Page")])
        self.assertIn("Standards and policies", texts[: kinds.count("Page")])
        entries = [title for title in ranked if title in texts[kinds.count("Page") :]]
        self.assertEqual(texts[kinds.count("Page") :], entries)

    def test_the_results_page_badges_entries_with_their_type(self):
        response = self.client.get("/search/", {"query": "another standard"})
        self.assertContains(
            response, '<strong class="govuk-tag govuk-tag--purple">Standard</strong>', html=False
        )


class CardColourTests(TestCase):
    def test_a_tag_is_drawn_as_govuk_frontend_draws_its_own(self):
        # GOV.UK Frontend 6's .govuk-tag, --green and --red.
        for colour, expected in (
            ("#1d70b8", ("#0f385c", "#d2e2f1")),
            ("#0f7a52", ("#083d29", "#cfe4dc")),
            ("#ca3535", ("#651b1b", "#f4d7d7")),
        ):
            with self.subTest(colour):
                self.assertEqual(tag_colours(colour), expected)

    def test_a_light_colours_tag_text_is_darkened_to_be_readable(self):
        text, background = tag_colours("#ffee00")
        self.assertGreaterEqual(contrast(text, background), 4.5)

    def test_the_star_is_white_unless_the_corner_is_too_light(self):
        self.assertEqual(star_colour("#54319f"), "#ffffff")
        self.assertEqual(star_colour("#f47738"), "#0b0c0c")

    def test_one_rule_per_colour_and_an_unreadable_one_is_blue(self):
        sheet = style_sheet(["#1D70B8", "#1d70b8", "red;}body{display:none"])
        self.assertEqual(sheet.count("li.app-colour-1d70b8{"), 1)
        self.assertNotIn("display", sheet)
        self.assertEqual(colour_class("not a colour"), "app-colour-1d70b8")
