"""Changelog notes link to this site, not to the one they were imported from.

The framework's published change notes are written the live service's way --
``[Data engineer](/role/data-engineer)`` -- and the CSV import stored them as
it found them, so 58 of the home page's links point at a URL scheme this site
does not serve. They resolved only for as long as ``seed_live_service_redirects``
had been run, which made a runbook step load-bearing for the content itself:
import the admin export into a fresh instance without it and the home page
publishes with every changelog link a 404.

Rewriting at render time removes the dependency. The redirects stay, because
bookmarks and inbound links still need them.
"""

import json

from django.template import Context, Template
from django.test import RequestFactory, TestCase, override_settings
from wagtail.models import Site

from govuk.live_service_links import (
    live_service_link_map,
    rewrite_live_service_links,
)
from govuk.models import (
    ContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
    SkillsAZPage,
)


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class LiveServiceLinkMapTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Data engineer")
        self.role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data engineer",
                slug="data-engineer",
                selected_roles=json.dumps([{"type": "role", "value": self.role.pk}]),
            )
        )
        self.role_page.save_revision().publish()

        self.skill = GovukSkill.objects.create(title="Prototyping")
        self.skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A to Z", slug="skills")
        )
        self.skills_page.save_revision().publish()

    def test_a_role_maps_to_the_page_that_renders_it(self):
        link_map = live_service_link_map(self.site)

        self.assertEqual(link_map["/role/data-engineer"], self.role_page.url)

    def test_a_skill_maps_into_its_section_of_the_a_to_z(self):
        link_map = live_service_link_map(self.site)

        self.assertEqual(
            link_map["/skill/prototyping"], f"{self.skills_page.url}#prototyping"
        )

    def test_a_role_no_live_page_renders_is_not_in_the_map(self):
        """A redirect is a better answer for it than a guess at a URL."""
        GovukRole.objects.create(title="Unrendered role")

        self.assertNotIn("/role/unrendered-role", live_service_link_map(self.site))

    def test_the_map_agrees_with_the_redirects_the_command_seeds(self):
        """The two read the same rule, so they cannot drift apart."""
        from io import StringIO

        from django.core.management import call_command
        from wagtail.contrib.redirects.models import Redirect

        call_command("seed_live_service_redirects", stdout=StringIO())
        link_map = live_service_link_map(self.site)

        for redirect in Redirect.objects.filter(site=self.site):
            target = redirect.redirect_link or (
                redirect.redirect_page.url if redirect.redirect_page else ""
            )
            self.assertEqual(link_map[redirect.old_path], target, redirect.old_path)


class RewriteLiveServiceLinksTests(TestCase):
    """The rewrite itself, away from the database."""

    LINK_MAP = {
        "/role/data-engineer": "/data-engineer/",
        "/skill/prototyping": "/skills/#prototyping",
    }

    def _rewrite(self, html: str) -> str:
        return rewrite_live_service_links(html, self.LINK_MAP)

    def test_a_role_link_is_pointed_at_the_page(self):
        self.assertEqual(
            self._rewrite('<p><a href="/role/data-engineer">Data engineer</a></p>'),
            '<p><a href="/data-engineer/">Data engineer</a></p>',
        )

    def test_a_skill_link_keeps_its_fragment(self):
        self.assertEqual(
            self._rewrite('<a href="/skill/prototyping">Prototyping</a>'),
            '<a href="/skills/#prototyping">Prototyping</a>',
        )

    def test_a_trailing_slash_is_the_same_link(self):
        self.assertEqual(
            self._rewrite('<a href="/role/data-engineer/">Data engineer</a>'),
            '<a href="/data-engineer/">Data engineer</a>',
        )

    def test_every_link_in_a_note_is_rewritten(self):
        rewritten = self._rewrite(
            '<p><a href="/role/data-engineer">One</a> and '
            '<a href="/skill/prototyping">two</a>.</p>'
        )

        self.assertNotIn("/role/", rewritten)
        self.assertNotIn("/skill/", rewritten)

    def test_a_path_the_map_does_not_know_is_left_alone(self):
        html = '<a href="/role/nobody-has-this">Nobody</a>'

        self.assertEqual(self._rewrite(html), html)

    def test_a_link_carrying_a_fragment_is_left_for_the_redirect(self):
        """Appending a second fragment would break the link this is fixing."""
        html = '<a href="/role/data-engineer#levels">Levels</a>'

        self.assertEqual(self._rewrite(html), html)

    def test_links_to_the_rest_of_the_site_are_untouched(self):
        html = '<p><a href="/download/">Download</a> the framework.</p>'

        self.assertEqual(self._rewrite(html), html)

    def test_the_text_of_a_note_is_not_touched(self):
        """Only hrefs. A note may talk about a path without linking to it."""
        html = "<p>The old service published this at /role/data-engineer.</p>"

        self.assertEqual(self._rewrite(html), html)

    def test_an_empty_note_is_no_trouble(self):
        self.assertEqual(rewrite_live_service_links("", self.LINK_MAP), "")
        self.assertEqual(rewrite_live_service_links(None, self.LINK_MAP), "")

    def test_an_empty_map_leaves_the_note_as_it_was(self):
        html = '<a href="/role/data-engineer">Data engineer</a>'

        self.assertEqual(rewrite_live_service_links(html, {}), html)


@override_settings(FEATURE_FLAGS=_feature_flags())
class ChangelogNoteTagTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Data engineer")
        self.role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data engineer",
                slug="data-engineer",
                selected_roles=json.dumps([{"type": "role", "value": self.role.pk}]),
            )
        )
        self.role_page.save_revision().publish()

    def _render(self, note: str) -> str:
        entry = GovukChangelogEntry(date="2026-08-01", note=note)
        template = Template("{% load govuk_filters %}{% changelog_note entry %}")
        request = RequestFactory().get("/")
        return template.render(Context({"entry": entry, "request": request}))

    def test_the_tag_rewrites_and_renders_the_note(self):
        rendered = self._render(
            '<p><a href="/role/data-engineer">Data engineer</a> has changed.</p>'
        )

        self.assertIn(f'href="{self.role_page.url}"', rendered)
        self.assertNotIn("/role/data-engineer", rendered)

    def test_the_markup_of_a_note_survives(self):
        rendered = self._render("<p>Something <b>changed</b>.</p>")

        self.assertIn("<b>changed</b>", rendered)

    def test_the_map_is_built_once_however_many_notes_are_rendered(self):
        """The A to Z asks 185 skills for their changelog in one render.

        Four queries build the map -- the site, the role slugs, the role pages,
        the skills A to Z -- and 20 notes add none. Twenty maps would be 80.
        """
        entries = [
            GovukChangelogEntry(
                date="2026-08-01",
                note=f'<p><a href="/role/data-engineer">Note {index}</a></p>',
            )
            for index in range(20)
        ]
        template = Template(
            "{% load govuk_filters %}"
            "{% for entry in entries %}{% changelog_note entry %}{% endfor %}"
        )
        request = RequestFactory().get("/")

        with self.assertNumQueries(4):
            rendered = template.render(
                Context({"entries": entries, "request": request})
            )

        self.assertEqual(rendered.count(self.role_page.url), 20)


@override_settings(FEATURE_FLAGS=_feature_flags())
class ChangelogLinksOnAPageTests(TestCase):
    """End to end, with no redirects seeded at all."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Data engineer")
        self.role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data engineer",
                slug="data-engineer",
                selected_roles=json.dumps([{"type": "role", "value": self.role.pk}]),
            )
        )
        self.role_page.save_revision().publish()

    def test_a_site_wide_note_on_a_content_page_links_into_this_site(self):
        GovukChangelogEntry.objects.create(
            date="2026-08-01",
            note='<p><a href="/role/data-engineer">Data engineer</a> was added.</p>',
        )
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Home",
                slug="framework-home",
                body="<p>The framework.</p>",
                show_framework_updates=True,
            )
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()

        self.assertIn(f'href="{self.role_page.url}"', html)
        self.assertNotIn('href="/role/data-engineer"', html)

    def test_a_note_on_a_role_page_links_into_this_site(self):
        GovukChangelogEntry.objects.create(
            date="2026-08-01",
            role=self.role,
            note='<p>See <a href="/role/data-engineer">Data engineer</a>.</p>',
        )

        html = self.client.get(self.role_page.url).content.decode()

        self.assertNotIn('href="/role/data-engineer"', html)
