"""Serving synced guidance: the page, its routes, the API and the admin."""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.guidance.adapters import build_bundle
from govuk.guidance.export import export_snapshot
from govuk.guidance.sync import sync_guidance_page
from govuk.models import ContentPage, GuidanceCollectionPage
from govuk.models.guidance import _find_route
from govuk.tests.guidance_helpers import (
    COMMIT,
    fake_fetch,
    guidance_flags,
    manifest_files,
    tech_docs_files,
)
from govuk.tests.test_guidance_sync import make_guidance_page


class GuidanceTestCase(TestCase):
    files = staticmethod(tech_docs_files)

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific
        self.page = make_guidance_page(self.root)
        sync_guidance_page(self.page, fetch=fake_fetch(self.files()))
        self.base = self.page.relative_url(self.site)

    def get(self, path=""):
        return self.client.get(self.base + path)


@override_settings(FEATURE_FLAGS=guidance_flags())
class GuidancePageTests(GuidanceTestCase):
    def test_the_index_is_the_collection_page(self):
        response = self.get()
        self.assertContains(response, "<h1 class=\"govuk-heading-xl\">The Example Way</h1>", html=False)
        self.assertContains(response, "Standards for example teams")
        # The guidance's own name heads the navigation, not "Home".
        self.assertContains(
            response, f'<a class="app-guidance-nav__link" href="{self.base}" aria-current="page">The Example Way</a>'
        )
        self.assertNotContains(response, ">Home</a>")
        # The source's banner, once, below the heading.
        html = response.content.decode()
        self.assertIn("For internal use.", html)
        self.assertLess(html.index("The Example Way</h1>"), html.index("app-guidance__banner"))
        # The index's headings are in its contents list, not the sidebar.
        self.assertContains(response, 'href="#about" class="contents-list-links govuk-link">About')

    def test_a_document_has_the_sidebar_contents_and_furniture(self):
        response = self.get("standards/alerting/")
        self.assertContains(response, "<title>How to manage alerts")
        self.assertContains(response, 'class="app-guidance-nav__title">Operating a service<')
        self.assertContains(
            response,
            f'<a class="app-guidance-nav__link" href="{self.base}standards/alerting/" aria-current="page">Manage alerts</a>',
        )
        self.assertNotContains(response, "role-nav")
        self.assertContains(response, 'href="#sending-alerts" class="contents-list-links govuk-link">Sending alerts')
        self.assertContains(response, f'href="{self.base}standards/monitoring/"')
        self.assertNotContains(response, "guidance:/")
        # The banner is the front page's, not every page's.
        self.assertNotContains(response, "For internal use.")
        self.assertContains(response, 'href="https://example.slack.com/messages/alerts">#alerts</a>')
        self.assertContains(
            response,
            f"https://github.com/example/example-way/blob/{COMMIT}/source/standards/alerting.html.md.erb",
        )

    def test_breadcrumbs_lead_back_through_the_collection(self):
        response = self.get("standards/alerting/")
        crumbs = [c["title"] for c in response.context["breadcrumbs"]]
        self.assertEqual(crumbs[-2:], ["The Example Way", "How to manage alerts"])
        self.assertNotIn("breadcrumbs_mobile_only", response.context)
        self.assertNotContains(response, "app-breadcrumbs--mobile-only")

    def test_breadcrumbs_follow_the_navigation_tree(self):
        response = self.get("standards/source-code/use-github/")
        trail = response.context["breadcrumbs"]
        self.assertEqual(
            [c["title"] for c in trail[-3:]], ["The Example Way", "Store source code", "Use GitHub"]
        )
        self.assertEqual(trail[-2]["url"], f"{self.base}standards/source-code/")

    def test_overdue_pages_say_so(self):
        response = self.get("standards/alerting/")
        self.assertContains(response, "This page was due for review on 28 February 2026")

    def test_a_branch_opens_only_while_reading_it(self):
        child = f'href="{self.base}standards/source-code/use-github/"'
        self.assertNotContains(self.get("standards/alerting/"), child)
        for path in ("standards/source-code/", "standards/source-code/use-github/"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertContains(response, "app-guidance-nav__list--nested")
                self.assertContains(response, child)
                # One sidebar shape throughout: no group added or taken away.
                titles = [g["title"] for g in response.context["guidance_navigation"]]
                self.assertEqual(titles, ["Operating a service"])
        response = self.get("standards/source-code/use-github/")
        self.assertContains(response, "app-guidance-nav__item--open")

    def test_withdrawn_pages_say_so(self):
        response = self.get("standards/operating-systems/")
        self.assertContains(response, "This guidance has been withdrawn.")
        self.assertNotContains(response, "Old advice")

    def test_other_addresses_for_a_page_redirect_to_it(self):
        cases = {
            "standards/source-code/index/": "standards/source-code/",
            "standards/old-alerting/": "standards/alerting/#sending-alerts",
        }
        for path, target in cases.items():
            with self.subTest(path=path):
                self.assertRedirects(
                    self.get(path), self.base + target, status_code=301, fetch_redirect_response=False
                )

    def test_the_source_sites_html_addresses_redirect(self):
        cases = {
            "standards/alerting.html": "standards/alerting/",
            "standards/source-code/index.html": "standards/source-code/",
            "standards/old-alerting.html": "standards/alerting/#sending-alerts",
            "index.html": "",
        }
        for path, target in cases.items():
            with self.subTest(path=path):
                self.assertRedirects(
                    self.get(path), self.base + target, status_code=301, fetch_redirect_response=False
                )
        self.assertEqual(self.get("nope.html").status_code, 404)
        self.assertEqual(self.client.get("/elsewhere/page.html").status_code, 404)

    def test_unknown_paths_404(self):
        self.assertEqual(self.get("standards/nope/").status_code, 404)

    def test_an_unsynced_page_says_so(self):
        page = make_guidance_page(self.root, slug="unsynced", title="Unsynced")
        self.assertContains(self.client.get(page.relative_url(self.site)), "This guidance has not been synced yet.")
        self.assertEqual(self.client.get(page.relative_url(self.site) + "anything/").status_code, 404)

    def test_pages_with_images_allow_githubs_raw_host(self):
        manifest = make_guidance_page(self.root, slug="manifest", title="Manifest")
        sync_guidance_page(manifest, fetch=fake_fetch(manifest_files()))
        response = self.client.get(manifest.relative_url(self.site) + "standards/alerting/")
        self.assertIn("https://raw.githubusercontent.com", response.headers["Content-Security-Policy"])
        response = self.client.get(manifest.relative_url(self.site))
        self.assertNotIn("raw.githubusercontent.com", response.headers["Content-Security-Policy"])

    def test_a_section_outside_the_navigation_shows_on_its_pages(self):
        manifest = make_guidance_page(self.root, slug="manifest", title="Manifest")
        sync_guidance_page(manifest, fetch=fake_fetch(manifest_files()))
        base = manifest.relative_url(self.site)
        response = self.client.get(base + "standards/orphan/")
        titles = [g["title"] for g in response.context["guidance_navigation"]]
        self.assertEqual(titles, ["Orphaned section", "Operating a service"])
        response = self.client.get(base + "standards/alerting/escalation/")
        self.assertEqual(
            [g["title"] for g in response.context["guidance_navigation"]], ["Operating a service"]
        )

    def test_a_page_listed_twice_is_current_at_its_shallowest(self):
        groups = [
            {"items": [{"path": "a", "children": [{"path": "b", "children": []}]}]},
            {"items": [{"path": "b", "children": []}]},
        ]
        self.assertEqual(_find_route(groups, "b"), (1, 0))
        self.assertEqual(_find_route(groups, "a"), (0, 0))
        self.assertIsNone(_find_route(groups, "c"))

    def test_it_can_have_no_child_pages(self):
        self.assertFalse(ContentPage.can_create_at(self.page))

    def test_parent_and_subpage_types_agree(self):
        for parent_name in GuidanceCollectionPage.parent_page_types:
            parent = apps.get_model(parent_name)
            with self.subTest(parent=parent_name):
                self.assertIn(GuidanceCollectionPage, parent.allowed_subpage_models())

    def test_the_repository_is_validated(self):
        page = GuidanceCollectionPage(title="x", repository_url="https://gitlab.com/a/b", branch="main")
        with self.assertRaises(ValidationError) as caught:
            page.clean()
        self.assertIn("repository_url", caught.exception.message_dict)


@override_settings(FEATURE_FLAGS=guidance_flags(guidance_enabled=False))
class GuidanceFlagOffTests(GuidanceTestCase):
    def test_nothing_is_served(self):
        for path in ("", "standards/alerting/", "standards/alerting.html"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)
        self.assertEqual(self.client.get("/api/guidance/").status_code, 404)

    def test_the_page_type_cannot_be_created(self):
        self.assertFalse(GuidanceCollectionPage.can_create_at(self.root))

    def test_it_is_left_out_of_the_pages_api(self):
        response = self.client.get("/api/pages/?type=govuk.GuidanceCollectionPage")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["meta"]["total_count"], 0)


@override_settings(FEATURE_FLAGS=guidance_flags())
class GuidanceAPITests(GuidanceTestCase):
    def test_collections(self):
        [item] = self.client.get("/api/guidance/").json()["items"]
        self.assertEqual(item["id"], self.page.pk)
        self.assertEqual(item["commit"], COMMIT)
        self.assertTrue(item["url"].endswith(self.base))

    def test_a_collection_lists_its_navigation_and_documents(self):
        data = self.client.get(f"/api/guidance/{self.page.pk}/").json()
        self.assertEqual(data["site"]["format"], "tech-docs")
        self.assertEqual(len(data["documents"]), 7)
        source_code = data["navigation"][0]["items"][1]
        self.assertEqual(source_code["path"], "standards/source-code")
        # The whole tree, not only the branch a reader has open.
        self.assertEqual(source_code["children"][0]["path"], "standards/source-code/use-github")

    def test_a_document_carries_html_and_markdown(self):
        data = self.client.get(f"/api/guidance/{self.page.pk}/docs/standards/alerting").json()
        self.assertEqual(data["title"], "How to manage alerts")
        self.assertIn("http://testserver" + self.base + "standards/monitoring/", data["html"])
        self.assertIn("Read about [monitoring][] first.", data["markdown"])
        self.assertEqual(data["metadata"]["owner"], "#alerts")
        self.assertEqual(data["headings"][0]["anchor"], "sending-alerts")
        index = self.client.get(f"/api/guidance/{self.page.pk}/docs/").json()
        self.assertEqual(index["path"], "")

    def test_unknown_or_unpublished_collections_404(self):
        self.assertEqual(self.client.get(f"/api/guidance/{self.page.pk}/docs/nope").status_code, 404)
        self.page.unpublish()
        self.assertEqual(self.client.get(f"/api/guidance/{self.page.pk}/").status_code, 404)

    def test_the_api_root_lists_it(self):
        self.assertIn("guidance", self.client.get("/api/").json()["endpoints"])


@override_settings(FEATURE_FLAGS=guidance_flags())
class GuidanceAdminTests(GuidanceTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.gov.uk", password="unused"
        )
        self.url = reverse("govuk_guidance_sync", args=[self.page.pk])

    def test_the_edit_view_shows_the_sync_status(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("wagtailadmin_pages:edit", args=[self.page.pk]))
        self.assertContains(response, "7 pages synced")
        self.assertContains(response, self.url)

    def test_the_add_view_renders_before_there_is_anything_to_sync(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse(
                "wagtailadmin_pages:add",
                args=["govuk", "guidancecollectionpage", self.root.pk],
            )
        )
        self.assertContains(response, "The guidance is synced when this page is first published.")

    def test_sync_now(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.url), "Sync now")
        with mock.patch("govuk.wagtail_hooks.guidance.sync_guidance_page") as sync:
            sync.return_value.summary.return_value = "Synced."
            response = self.client.post(self.url, {"force": "1"})
        self.assertRedirects(
            response, reverse("wagtailadmin_pages:edit", args=[self.page.pk]), fetch_redirect_response=False
        )
        sync.assert_called_once()
        self.assertTrue(sync.call_args.kwargs["force"])

    def test_sync_now_needs_publish_permission(self):
        editor = get_user_model().objects.create_user(username="nobody", password="unused")
        editor.user_permissions.add(*_admin_access_permissions())
        self.client.force_login(editor)
        with mock.patch("govuk.wagtail_hooks.guidance.sync_guidance_page") as sync:
            self.client.post(self.url)
        sync.assert_not_called()

    def test_publishing_a_new_page_syncs_it(self):
        self.client.force_login(self.admin)
        page = self.root.add_child(
            instance=GuidanceCollectionPage(
                title="New", slug="new", repository_url="https://github.com/example/new"
            )
        )
        with mock.patch("govuk.wagtail_hooks.guidance.sync_guidance_page") as sync:
            sync.return_value.summary.return_value = "Synced."
            self.client.post(
                reverse("wagtailadmin_pages:edit", args=[page.pk]),
                {
                    "title": "New",
                    "slug": "new",
                    "repository_url": "https://github.com/example/new",
                    "branch": "main",
                    "subdirectory": "",
                    "action-publish": "publish",
                },
            )
        sync.assert_called_once()


@override_settings(FEATURE_FLAGS=guidance_flags())
class GuidanceCommandTests(GuidanceTestCase):
    def test_sync_guidance(self):
        with mock.patch(
            "govuk.management.commands.sync_guidance.sync_guidance_page"
        ) as sync:
            sync.return_value.summary.return_value = "Synced."
            sync.return_value.warnings = []
            out = StringIO()
            call_command("sync_guidance", stdout=out)
        self.assertIn("Synced 1 of 1 pages.", out.getvalue())

    @override_settings(FEATURE_FLAGS=guidance_flags(guidance_enabled=False))
    def test_sync_guidance_needs_the_flag(self):
        with self.assertRaises(CommandError):
            call_command("sync_guidance", stdout=StringIO())

    def test_export_round_trips_through_the_manifest_adapter(self):
        """The export is a manifest repo that renders the same guidance."""
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            call_command("export_guidance", self.page.pk, str(out), stdout=StringIO())
            files = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}

        exported = make_guidance_page(self.root, slug="exported", title="Exported")
        sync_guidance_page(exported, fetch=fake_fetch(files))

        original = {d.path: d for d in self.page.snapshot.documents.all()}
        again = {d.path: d for d in exported.snapshot.documents.all()}
        self.assertEqual(set(original), set(again))
        self.assertEqual(build_bundle(files).format, "manifest")
        for key in ("navigation", "sections", "redirects", "banner_html"):
            with self.subTest(key=key):
                self.assertEqual(exported.snapshot.site[key], self.page.snapshot.site[key])
        differing = [p for p in original if original[p].html != again[p].html]
        # Only the link to the non-page file differs: the export has pages only.
        self.assertEqual(differing, ["standards/monitoring"])

    def test_export_refuses_a_non_empty_directory(self):
        with TemporaryDirectory() as tmp:
            Path(tmp, "something").write_text("x")
            with self.assertRaises(CommandError):
                call_command("export_guidance", self.page.pk, tmp, stdout=StringIO())

    def test_export_writes_withdrawn_pages_and_alerts(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            export_snapshot(self.page.snapshot, out)
            withdrawn = (out / "standards/operating-systems.md").read_text()
            alerting = (out / "standards/alerting.md").read_text()
            source_code = (out / "standards/source-code/index.md").exists()
        self.assertIn("status: withdrawn", withdrawn)
        self.assertIn("> [!WARNING]", alerting)
        self.assertTrue(source_code)


def _admin_access_permissions():
    from django.contrib.auth.models import Permission

    return Permission.objects.filter(content_type__app_label="wagtailadmin", codename="access_admin")
