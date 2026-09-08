"""Every sentence the framework prints is the editors' to change.

The wording settings were built so the framework team owns the language
without releases (see the settings model's docstring). These tests hold the
last holdouts to that: the sentences that were still assembled from string
literals in code and templates -- the role page introductions, the navigation
headings, the breadcrumb, and the home page's updates block.
"""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    FrameworkContentPage,
    GovukChangelogEntry,
    GovukRole,
)
from govuk.tests.framework_helpers import make_framework_main_page, role_url


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class FrameworkLanguageEditableTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.main_page = make_framework_main_page(self.root_page)

        self.role = GovukRole.objects.create(
            title="Business architect",
            family="Architecture",
            body="<p>Develops the view.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Trainee business architect",
                        "description": "<p>Learns.</p>",
                        "skills": [],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Lead business architect",
                        "description": "<p>Leads.</p>",
                        "skills": [],
                    },
                },
            ],
        )
        self.role_url = role_url(self.main_page, self.role)

        # A non-role page beside the roles, so the navigation has a further
        # resources group to head. The framework's further resources are the
        # main page's own children.
        self.privacy_page = self.main_page.add_child(
            instance=FrameworkContentPage(title="Privacy", slug="privacy", body="")
        )
        self.privacy_page.save_revision().publish()

        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)

    def _set(self, **fields):
        for name, value in fields.items():
            setattr(self.wording, name, value)
        self.wording.save()

    def test_the_defaults_read_exactly_as_the_framework_writes_them(self):
        response = self.client.get(self.role_url)

        self.assertContains(response, "What a business architect does")
        self.assertContains(
            response,
            "Find out what a business architect in government does and the "
            "skills you need to do the role at each level.",
        )
        self.assertContains(
            response,
            "There are 2 business architect role levels, from trainee business "
            "architect to lead business architect.",
        )
        self.assertContains(response, "Architecture roles")
        self.assertContains(response, "Further resources")

    def test_the_overview_heading_is_the_editors_to_change(self):
        self._set(overview_heading_text="The {role} job, in short")

        response = self.client.get(self.role_url)

        self.assertContains(response, "The business architect job, in short")
        self.assertNotContains(response, "What a business architect does")

    def test_the_lead_sentence_is_the_editors_to_change(self):
        self._set(role_lead_text="Meet {article} {role} and their skills.")

        response = self.client.get(self.role_url)

        self.assertContains(response, "Meet a business architect and their skills.")

    def test_the_levels_introduction_is_the_editors_to_change(self):
        self._set(
            role_levels_opening_many="{role} has {count} steps{levels_range}.",
            role_levels_range_text=" ({first} up to {last})",
            role_levels_described_many="Each is set out below.",
            role_levels_purpose_text="Use them to plan.",
        )

        response = self.client.get(self.role_url)

        self.assertContains(
            response,
            "business architect has 2 steps (trainee business architect up to "
            "lead business architect).",
        )
        self.assertContains(response, "Each is set out below. Use them to plan.")

    def test_the_navigation_headings_are_the_editors_to_change(self):
        self._set(
            role_family_group_title="The {family} family",
            further_resources_heading="Everything else",
        )

        response = self.client.get(self.role_url)

        self.assertContains(response, "The Architecture family")
        # Kept in step: the narrow-screen breadcrumb links the family group to
        # its section on the home page, so both must read from the same field.
        self.assertContains(response, 'href="/#the-architecture-family"')

    def test_the_breadcrumb_home_label_is_the_editors_to_change(self):
        self._set(breadcrumb_home_label="Start")

        response = self.client.get(self.role_url)

        self.assertContains(
            response,
            '<a class="govuk-breadcrumbs__link" href="/">Start</a>',
            html=True,
        )

    def test_the_home_updates_block_reads_the_same_wording_as_a_role_page(self):
        # The updates block belongs to the framework main page, which shows it
        # by default. A framework content page never carries it.
        GovukChangelogEntry.objects.create(
            date="2026-08-01", note="<p>Framework refreshed</p>"
        )
        self._set(
            updates_heading="What changed",
            published_prefix="First seen",
            last_updated_prefix="Freshest",
            see_all_updates_link_text="Everything that moved",
            show_all_updates_link_text="open the history",
            hide_all_updates_link_text="close the history",
        )

        response = self.client.get(self.main_page.url)

        self.assertContains(response, ">What changed</h2>", html=False)
        self.assertContains(response, "First seen")
        self.assertContains(response, "Freshest")
        self.assertContains(response, "Everything that moved")
        self.assertContains(response, 'data-show-text="open the history"')
        self.assertContains(response, 'data-hide-text="close the history"')
