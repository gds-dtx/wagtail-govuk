"""Scheduling is hidden while nothing runs ``publish_scheduled``, so a set date would never publish."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Page, get_page_models
from wagtail.test.utils.form_data import (
    nested_form_data,
    querydict_from_html,
    rich_text,
    streamfield,
)

from govuk.models import (
    ContentEntryExternalPage,
    ContentEntryGenericPage,
    ContentEntryGuidePage,
    ContentEntryPolicyPage,
    ContentEntryRequirementsPage,
    ContentEntryStepsPage,
    ContentEntryToolPage,
    ContentGroupPage,
    ContentGuidePage,
    ContentMainPage,
    ContentPage,
    ContentRequirementPage,
    ContentStepPage,
    FrameworkContentPage,
    FrameworkMainPage,
    FrameworkSkillsPage,
    TagListingsPage,
    page_settings_panels,
)
from govuk.tests.helpers import publish_page

PUBLISHING_PANEL = "wagtail.admin.panels.PublishingPanel"

PAGE_MODELS = (
    ContentPage,
    FrameworkMainPage,
    FrameworkContentPage,
    FrameworkSkillsPage,
    TagListingsPage,
    ContentMainPage,
    ContentGroupPage,
    ContentEntryRequirementsPage,
    ContentEntryPolicyPage,
    ContentEntryGuidePage,
    ContentEntryStepsPage,
    ContentEntryGenericPage,
    ContentEntryToolPage,
    ContentEntryExternalPage,
    ContentRequirementPage,
    ContentStepPage,
    ContentGuidePage,
)


def _panel_paths(panels) -> list[str]:
    return [getattr(panel, "path", None) for panel in panels]


class PageSettingsPanelsTests(TestCase):
    def test_the_other_panels_are_kept_and_wagtails_own_list_is_left_alone(self):
        kept = page_settings_panels()

        self.assertNotIn(PUBLISHING_PANEL, _panel_paths(kept))
        self.assertEqual(len(kept), len(Page.settings_panels) - 1)
        self.assertIn("wagtail.admin.panels.CommentPanel", _panel_paths(kept))
        self.assertIn(PUBLISHING_PANEL, _panel_paths(Page.settings_panels))

    @override_settings(SCHEDULED_PUBLISHING=True)
    def test_one_setting_gives_scheduling_back(self):
        self.assertEqual(_panel_paths(page_settings_panels()), _panel_paths(Page.settings_panels))


class SchedulingHiddenTests(TestCase):
    def setUp(self):
        self.page = publish_page("A page", "<p>Words.</p>")
        self.client.force_login(
            get_user_model().objects.create_superuser(
                username="admin-user", email="admin@example.gov.uk", password="unused-password"
            )
        )

    def _edit_page_html(self) -> str:
        response = self.client.get(reverse("wagtailadmin_pages:edit", args=[self.page.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_no_page_type_offers_scheduling(self):
        # From Wagtail's registry, so a page model added later is covered too.
        self.assertEqual(
            {model for model in get_page_models() if model._meta.app_label == "govuk"},
            set(PAGE_MODELS),
        )
        for model in PAGE_MODELS:
            with self.subTest(model=model.__name__):
                self.assertNotIn(PUBLISHING_PANEL, _panel_paths(model.settings_panels))
                fields = model.get_edit_handler().get_form_class().base_fields
                self.assertNotIn("go_live_at", fields)
                self.assertNotIn("expire_at", fields)

        html = self._edit_page_html()
        self.assertNotIn('name="go_live_at"', html)
        self.assertNotIn('name="expire_at"', html)
        # A toggle opening onto an empty panel would be a dead control.
        self.assertNotIn("Edit schedule", html)

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
                {"body": streamfield([("text", rich_text("<p>New words.</p>"))])}
            )
        )
        data["title"] = "A page, edited"
        data["action-publish"] = "action-publish"

        response = self.client.post(
            reverse("wagtailadmin_pages:edit", args=[self.page.pk]), data=data
        )

        self.assertEqual(response.status_code, 302)
        self.page.refresh_from_db()
        self.assertEqual(self.page.title, "A page, edited")
        self.assertTrue(self.page.live)
