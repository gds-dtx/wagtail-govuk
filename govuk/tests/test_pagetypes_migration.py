"""Migration 0071 on a database shaped like the development instance's.

Production starts empty and takes its content from the export, so 0071 does
nothing there. The development instance already holds the framework in the
shape the code had before the page types were reshaped -- one ContentPage type
with switches on it, five of them ticked for the role side menu by 0069, a
SkillsAZPage, and a page per role -- and 0071 converts it in place at
container start. These tests build that shape at 0070, run the migration
forward, and check what comes out: which pages changed type, what was deleted
with the role pages, and that nothing the editors chose was lost.

They are also the reason the reshaping is two migrations rather than one: run
on Postgres they showed that a table whose rows 0071 deletes cannot be altered
in the same transaction ("pending trigger events"), which SQLite never reports.

They drive the migration executor rather than calling the RunPython functions,
because the functions need the tables as they stand mid-migration (RolePage
still there, the new types just created), and only the executor puts the
database in that state.
"""

from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Page, ReferenceIndex, Revision, Site

from govuk.live_service_links import (
    unanswerable_live_service_urls,
    unseeded_live_service_redirects,
)
from govuk.models import (
    ContentPage,
    FrameworkContentPage,
    FrameworkMainPage,
    FrameworkSkillsPage,
)

BEFORE = [("govuk", "0070_page_feedback_field_labels")]

# Treebeard's materialised path: four characters per level, base 36.
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _step(number: int) -> str:
    digits = ""
    while number:
        digits = _ALPHABET[number % 36] + digits
        number //= 36
    return digits.rjust(4, "0")


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _leaf():
    return MigrationExecutor(connection).loader.graph.leaf_nodes("govuk")


@override_settings(FEATURE_FLAGS=_feature_flags())
class PageTypesMigrationTests(TransactionTestCase):
    # The flush after each test empties every table, including the root page
    # and the default site the other tests rely on; this puts them back.
    serialized_rollback = True

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        self.old_apps = executor.loader.project_state(BEFORE).apps
        try:
            self._build_the_development_instance()
        except Exception:
            # tearDown is not run when setUp fails; the schema must not be
            # left at 0070 for the flush and the next test.
            MigrationExecutor(connection).migrate(_leaf())
            raise

        MigrationExecutor(connection).migrate(_leaf())
        Site.clear_site_root_paths_cache()
        self.site = Site.objects.get(is_default_site=True)

    def tearDown(self):
        # Whatever a failing assertion leaves behind, the schema the other
        # tests run against is the current one.
        MigrationExecutor(connection).migrate(_leaf())
        Site.clear_site_root_paths_cache()

    # -- the development instance, at 0070 ------------------------------------

    def _content_type(self, model):
        ContentType = self.old_apps.get_model("contenttypes", "ContentType")
        content_type, _ = ContentType.objects.get_or_create(
            app_label=model._meta.app_label, model=model._meta.model_name
        )
        return content_type

    def _add_page(self, model, parent, *, slug, title=None, **fields):
        parent.refresh_from_db()
        parent.numchild += 1
        parent.save(update_fields=["numchild"])
        title = title or slug.replace("-", " ").capitalize()
        return model.objects.create(
            title=title,
            draft_title=title,
            slug=slug,
            path=parent.path + _step(parent.numchild),
            depth=parent.depth + 1,
            numchild=0,
            url_path=f"{parent.url_path}{slug}/",
            content_type=self._content_type(model),
            locale=self.locale,
            live=True,
            **fields,
        )

    def _build_the_development_instance(self):
        apps = self.old_apps
        OldPage = apps.get_model("wagtailcore", "Page")
        OldSite = apps.get_model("wagtailcore", "Site")
        Locale = apps.get_model("wagtailcore", "Locale")
        OldContentPage = apps.get_model("govuk", "ContentPage")
        SkillsAZPage = apps.get_model("govuk", "SkillsAZPage")
        RolePage = apps.get_model("govuk", "RolePage")
        GovukRole = apps.get_model("govuk", "GovukRole")
        OldRevision = apps.get_model("wagtailcore", "Revision")
        OldReferenceIndex = apps.get_model("wagtailcore", "ReferenceIndex")

        self.locale = Locale.objects.first()
        tree_root = OldPage.objects.get(depth=1)

        # The home page: the welcome page, all three switches on.
        home = self._add_page(
            OldContentPage,
            tree_root,
            slug="cf",
            title="Capability Framework",
            show_role_navigation=True,
            show_framework_updates=True,
            show_framework_welcome=True,
        )
        site = OldSite.objects.get(is_default_site=True)
        OldSite.objects.filter(pk=site.pk).update(root_page_id=home.pk)

        # The header as the development instance has it: the live service's
        # layout, set through the two tick boxes this migration replaces.
        OldCustomiseSettings = apps.get_model("govuk", "CustomiseSettings")
        OldCustomiseSettings.objects.create(
            site_id=site.pk,
            show_service_name_in_navigation=True,
            hide_sign_in_link=True,
            hero_background_color="#112233",
            hero_text_color="#fefefe",
            extra_css=".existing { margin: 0; }",
        )
        self.home_id = home.pk
        home_as_page = OldPage.objects.get(pk=home.pk)

        skills = self._add_page(SkillsAZPage, home_as_page, slug="skills", title="Skills A to Z")
        self.skills_id = skills.pk

        # Live's five side-menu pages as they are on dev: two put there by the
        # switch, three by 0069's tick alone.
        for slug in ("job-grades", "roadmap"):
            self._add_page(OldContentPage, home_as_page, slug=slug, show_role_navigation=True)
        for slug in (
            "download",
            "propose-a-change",
            "context-and-challenges-for-senior-civil-service-roles-in-digital-and-data",
        ):
            self._add_page(OldContentPage, home_as_page, slug=slug, show_in_role_navigation=True)
        # Footer pages: neither switch nor tick.
        for slug in ("privacy", "cookie-statement"):
            self._add_page(OldContentPage, home_as_page, slug=slug)

        # Two roles, each with a page, one of which has history and a redirect.
        analyst = GovukRole.objects.create(title="Data analyst", slug="data-analyst")
        GovukRole.objects.create(title="Data engineer", slug="data-engineer")
        analyst_page = self._add_page(
            RolePage,
            home_as_page,
            slug="data-analyst",
            title="Data analyst",
            body="<p>Written on the page, not the role.</p>",
        )
        engineer_page = self._add_page(RolePage, home_as_page, slug="data-engineer", title="Data engineer")
        self.role_page_ids = [analyst_page.pk, engineer_page.pk]
        self.analyst = analyst

        page_ct = self._content_type(OldPage)
        rolepage_ct = self._content_type(RolePage)
        OldRevision.objects.create(
            content_type=rolepage_ct,
            base_content_type=page_ct,
            object_id=str(analyst_page.pk),
            object_str="Data analyst",
            content={"title": "Data analyst"},
            created_at=timezone.now(),
        )
        OldReferenceIndex.objects.create(
            content_type=self._content_type(OldContentPage),
            base_content_type=page_ct,
            object_id=str(home.pk),
            to_content_type=page_ct,
            to_object_id=str(analyst_page.pk),
            model_path="body",
            content_path="body",
            content_path_hash=uuid4(),
        )
        # The redirects app is not in govuk's migration graph, so it has no
        # historical model here; its table is the same at 0070 and now.
        Redirect.objects.create(
            old_path="/role/data-analyst",
            site_id=site.pk,
            redirect_page_id=analyst_page.pk,
            is_permanent=True,
        )

    # -- what comes out ---------------------------------------------------------

    def test_the_home_page_is_the_framework_main_page(self):
        main_page = FrameworkMainPage.objects.get()

        self.assertEqual(main_page.pk, self.home_id)
        self.assertEqual(main_page.title, "Capability Framework")
        self.assertTrue(main_page.show_framework_welcome)
        self.assertEqual(self.site.root_page_id, main_page.pk)
        self.assertEqual(main_page.url, "/")

    def test_all_five_side_menu_pages_become_framework_content_pages(self):
        """Three of them were in the menu by the tick alone. Reading only the
        switches, as the migration first did, dropped them from the menu."""
        self.assertEqual(
            sorted(FrameworkContentPage.objects.values_list("slug", flat=True)),
            [
                "context-and-challenges-for-senior-civil-service-roles-in-digital-and-data",
                "download",
                "job-grades",
                "propose-a-change",
                "roadmap",
            ],
        )
        for page in FrameworkContentPage.objects.all():
            self.assertEqual(page.get_parent().pk, self.home_id)

    def test_the_footer_pages_stay_plain(self):
        self.assertEqual(
            sorted(ContentPage.objects.values_list("slug", flat=True)),
            ["cookie-statement", "privacy"],
        )

    def test_the_skills_index_keeps_its_page_under_the_new_name(self):
        skills_page = FrameworkSkillsPage.objects.get()

        self.assertEqual(skills_page.pk, self.skills_id)
        self.assertEqual(skills_page.url, "/skills/")

    def test_the_role_pages_go_and_take_their_history_with_them(self):
        self.assertFalse(Page.objects.filter(pk__in=self.role_page_ids).exists())
        self.assertFalse(Page.objects.filter(slug__in=["data-analyst", "data-engineer"]).exists())

        object_ids = [str(pk) for pk in self.role_page_ids]
        self.assertFalse(Revision.objects.filter(object_id__in=object_ids).exists())
        self.assertFalse(ReferenceIndex.objects.filter(object_id__in=object_ids).exists())
        self.assertFalse(ReferenceIndex.objects.filter(to_object_id__in=object_ids).exists())
        # The redirect pointed at a page that no longer exists.
        self.assertFalse(Redirect.objects.filter(old_path="/role/data-analyst").exists())

    def test_the_tree_still_adds_up(self):
        main_page = Page.objects.get(pk=self.home_id)

        self.assertEqual(main_page.numchild, main_page.get_children().count())
        self.assertEqual(main_page.numchild, 8)

    def test_the_header_layout_survives_the_change_from_tick_boxes_to_dropdowns(self):
        """Two booleans become three choices. Dropping the booleans and adding
        the choices with their defaults would give the development site the
        header-bar layout with a Sign in link, whatever it showed the day
        before; the values are carried across instead."""
        from govuk.models import CustomiseSettings

        settings_row = CustomiseSettings.objects.get(site=self.site)

        self.assertEqual(settings_row.service_name_location, "navigation")
        self.assertEqual(settings_row.search_location, "navigation")
        self.assertEqual(settings_row.sign_in_location, "hidden")

    def test_the_hero_colours_survive_as_the_css_they_always_produced(self):
        """The two colour fields go, but they were not inoperable: the custom
        CSS view wrote them out. A site that set them keeps the same CSS."""
        from govuk.models import CustomiseSettings

        settings_row = CustomiseSettings.objects.get(site=self.site)

        self.assertEqual(
            settings_row.extra_css,
            ".masthead { background: #112233; }\n"
            ".masthead { color: #fefefe; }\n"
            ".hero__description { color: #fefefe; }\n"
            ".existing { margin: 0; }",
        )
        self.assertIn(".masthead { background: #112233; }", settings_row.render_custom_css())

    def test_every_role_is_still_answered_at_its_live_url(self):
        """No redirect survives for the roles, and none is needed: the main
        page is the home page, so the route serves each role at /role/<slug>/."""
        self.assertEqual(unseeded_live_service_redirects(self.site), [])
        self.assertEqual(unanswerable_live_service_urls(self.site), [])

        response = self.client.get("/role/data-analyst/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data analyst")
