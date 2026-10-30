"""The content catalogue's pages: where they go, what they serve, and the admin around them."""

from io import StringIO

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, Site

from govuk.models import (
    CatalogueScope,
    CatalogueType,
    ContentEntryExternalPage,
    ContentEntryGuidePage,
    ContentEntryRequirementsPage,
    ContentGroupPage,
    ContentGuidePage,
    ContentMainPage,
    ContentPage,
    ContentRequirementPage,
    catalogue_page_models,
    without_guidance_pages,
)
from govuk.tests.catalogue_helpers import (
    guidance_flags,
    import_fixture,
    make_content_main_page,
)


class CatalogueTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific
        self.page = make_content_main_page(self.root)
        with override_settings(FEATURE_FLAGS=guidance_flags()):
            self.result = import_fixture(self.page)
        self.base = self.page.relative_url(self.site)

    def get(self, path="", **params):
        return self.client.get(self.base + path, params)


@override_settings(FEATURE_FLAGS=guidance_flags())
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

    def test_an_entrys_type_must_use_its_layout(self):
        scope = CatalogueScope.objects.create(key="public-sector", label="Public sector")
        policy = CatalogueType.objects.create(key="policy", label="Policy", layout="policy")
        page = ContentEntryRequirementsPage(
            title="x", entry_type=policy, scope=scope, short_description="x",
            long_description="x", owner_organisation="x",
        )
        with self.assertRaises(ValidationError):
            page.clean()

    def test_an_external_entry_needs_its_address(self):
        scope = CatalogueScope.objects.create(key="public-sector", label="Public sector")
        policy = CatalogueType.objects.create(key="policy", label="Policy", layout="policy")
        page = ContentEntryExternalPage(
            title="x", entry_type=policy, scope=scope, short_description="x",
            long_description="x", owner_organisation="x",
        )
        with self.assertRaises(ValidationError):
            page.clean()


@override_settings(FEATURE_FLAGS=guidance_flags())
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
        for name in ("tag", "profession", "type", "scope"):
            self.assertContains(
                response, f'<select class="govuk-select" id="catalogue-{name}" name="{name}">', html=False
            )
        self.assertContains(
            response, '<option value="architecture" selected>Architecture (1)</option>', html=False
        )


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
        self.assertContains(response, "app-catalogue-card app-catalogue-card--primary")

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


@override_settings(FEATURE_FLAGS=guidance_flags())
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


@override_settings(FEATURE_FLAGS=guidance_flags(guidance_enabled=False))
class FlagOffTests(CatalogueTestCase):
    def test_nothing_is_served(self):
        for path in (
            "", "all/", "guidance/", "standards-and-policies/hosted-standard/",
            "standards-and-policies/hosted-standard/first-point/",
            "standards-and-policies/external-policy/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)

    def test_no_catalogue_page_can_be_created(self):
        group = ContentGroupPage.objects.first()
        self.assertFalse(ContentMainPage.can_create_at(self.root))
        self.assertFalse(ContentGroupPage.can_create_at(self.page))
        self.assertFalse(ContentEntryGuidePage.can_create_at(group))

    def test_its_pages_are_left_out_of_public_listings(self):
        self.assertFalse(
            without_guidance_pages(Page.objects.descendant_of(self.root, inclusive=True))
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


@override_settings(FEATURE_FLAGS=guidance_flags())
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
