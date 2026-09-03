"""Scheduling is not offered while nothing can act on a schedule.

Wagtail's go-live and expiry dates need ``publish_scheduled`` run on a timer.
Nothing in this service's deployment runs it -- the command appears nowhere in
this repo or in the two Terraform repos, and no task backend is configured --
so a date set in the admin is a page that quietly never publishes. An editor
gets no error and no page.

Hiding the panel is the honest version of that until the scheduled task exists.
The dead-button trap matters as much as the fields: ``6f517f1`` fixed a control
that was visible and did nothing, and swapping a silent failure for an "Edit
schedule" toggle that opens onto an empty panel would be the same bug again.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, Site
from wagtail.test.utils.form_data import (
    nested_form_data,
    querydict_from_html,
    rich_text,
    streamfield,
)

from govuk.models import (
    ContentPage,
    RolePage,
    SectionPage,
    SkillsAZPage,
    TagListingsPage,
    page_settings_panels,
)

PUBLISHING_PANEL = "wagtail.admin.panels.PublishingPanel"

PAGE_MODELS = (ContentPage, RolePage, SkillsAZPage, TagListingsPage, SectionPage)


def _panel_paths(panels) -> list[str]:
    return [getattr(panel, "path", None) for panel in panels]


class PageSettingsPanelsTests(TestCase):
    def test_the_publishing_panel_is_dropped_by_default(self):
        self.assertNotIn(PUBLISHING_PANEL, _panel_paths(page_settings_panels()))

    def test_the_other_settings_panels_are_kept(self):
        """Comments are the rest of Wagtail's settings tab, and still wanted."""
        kept = page_settings_panels()

        self.assertEqual(len(kept), len(Page.settings_panels) - 1)
        self.assertIn("wagtail.admin.panels.CommentPanel", _panel_paths(kept))

    @override_settings(SCHEDULED_PUBLISHING=True)
    def test_one_setting_gives_scheduling_back(self):
        """For the day the scheduled task exists. No code change, no migration."""
        self.assertEqual(
            _panel_paths(page_settings_panels()),
            _panel_paths(Page.settings_panels),
        )

    def test_wagtails_own_list_is_left_alone(self):
        """Filtering a class attribute in place would strip it from the admin."""
        page_settings_panels()

        self.assertIn(PUBLISHING_PANEL, _panel_paths(Page.settings_panels))


class EveryPageModelTests(TestCase):
    """All five of them. A model that kept the panel would still take dates."""

    def test_no_page_model_offers_scheduling(self):
        for model in PAGE_MODELS:
            with self.subTest(model=model.__name__):
                self.assertNotIn(PUBLISHING_PANEL, _panel_paths(model.settings_panels))

    def test_these_are_every_page_type_in_the_app(self):
        """A sixth page model added later has to be handled too."""
        subclasses = {
            model
            for model in Page.__subclasses__()
            if model._meta.app_label == "govuk"
        }

        self.assertEqual(subclasses, set(PAGE_MODELS))

    def test_the_edit_form_has_no_go_live_or_expiry_field(self):
        for model in PAGE_MODELS:
            with self.subTest(model=model.__name__):
                fields = model.get_edit_handler().get_form_class().base_fields

                self.assertNotIn("go_live_at", fields)
                self.assertNotIn("expire_at", fields)


class SchedulingControlsInTheAdminTests(TestCase):
    """The rendered page, not the panel list: no fields and no dead toggle."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.page = self.root_page.add_child(
            instance=ContentPage(title="A page", slug="a-page", body="<p>Words.</p>")
        )
        self.page.save_revision().publish()

        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-user",
            email="admin@example.gov.uk",
            password="unused-password",
        )
        self.client.force_login(self.admin_user)

    def _edit_page_html(self) -> str:
        response = self.client.get(
            reverse("wagtailadmin_pages:edit", args=[self.page.pk])
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_editor_is_not_shown_a_go_live_date(self):
        html = self._edit_page_html()

        self.assertNotIn('name="go_live_at"', html)
        self.assertNotIn('name="expire_at"', html)

    def test_the_edit_schedule_toggle_goes_with_the_fields(self):
        """Wagtail gates the toggle on the form having go_live_at, so it does.

        Asserted rather than assumed: a toggle opening onto an empty panel
        would be the dead control 6f517f1 removed, reintroduced.
        """
        self.assertNotIn("Edit schedule", self._edit_page_html())

    def test_publishing_still_works(self):
        """Hiding the schedule must not touch publishing itself.

        Submitted as the form the admin actually served, so a missing field
        would fail here rather than pass on a hand-written payload.
        """
        data = querydict_from_html(self._edit_page_html(), form_id="page-edit-form")
        # A StreamField's inputs are built by its JavaScript, so they are not
        # in the served HTML for the scrape above to find.
        data.update(
            nested_form_data(
                {
                    "framework_welcome_body": streamfield([]),
                    "body_blocks": streamfield([]),
                }
            )
        )
        data["title"] = "A page, edited"
        data["body"] = rich_text("<p>New words.</p>")
        data["action-publish"] = "action-publish"

        response = self.client.post(
            reverse("wagtailadmin_pages:edit", args=[self.page.pk]), data=data
        )

        self.assertEqual(response.status_code, 302)
        self.page.refresh_from_db()
        self.assertEqual(self.page.title, "A page, edited")
        self.assertTrue(self.page.live)
