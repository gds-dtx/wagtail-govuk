
from django.conf import settings
from django.db import models
from django.http import Http404
from django.utils.text import slugify
from modelcluster.contrib.taggit import ClusterTaggableManager
from wagtail.admin.panels import FieldPanel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Page, Site

from govuk.utils import row_id_from_text

from ..blocks import InsetTextBlock
from ..constants import FRAMEWORK_WELCOME_RICH_TEXT_FEATURES, SKILL_LEVEL_ORDINALS
from ..pages import BaseContentPage
from ..panels import (
    base_content_panels,
    base_settings_panels,
    framework_content_panels,
    framework_content_settings_panels,
    framework_main_settings_panels,
)
from .blocks import (
    FrameworkWelcomeSectionBlock,
    SectionBreakBlock,
    SkillLevelDefinitionsBlock,
)
from .navigation import (
    framework_breadcrumbs,
    framework_home_link,
    role_navigation_groups,
    role_page_urls_by_role_id,
)
from .settings import CapabilityFrameworkWordingSettings
from .snippets import GovukRole, GovukSkill, site_wide_changelog


class FrameworkFieldsMixin(models.Model):
    """The framework switches and welcome content, on the framework page types.

    Only ``FrameworkMainPage`` and ``FrameworkContentPage`` carry the Capability
    Framework furniture -- the role side navigation, the site-wide changelog and
    the welcome layout. Plain ``ContentPage`` no longer offers any of it. The
    ``get_context`` here layers the framework context on top of the base page
    context; both framework types inherit it.
    """

    # Default on: a new framework main page starts with all three switched on
    # (the framework home is the welcome page, with the role navigation and the
    # updates). A framework content page ignores the stored values -- it forces
    # role navigation on and the rest off in its own get_context.
    show_role_navigation = models.BooleanField(
        default=True,
        verbose_name="Show role navigation",
        help_text="Show the list of roles grouped by family alongside the page, as the role pages do.",
    )
    show_framework_updates = models.BooleanField(
        default=True,
        verbose_name="Show framework updates",
        help_text=(
            "Show the changelog entries that are not tied to a single role or "
            "skill, with a last updated date above the page content."
        ),
    )
    show_framework_welcome = models.BooleanField(
        default=True,
        verbose_name="Show framework welcome content",
        help_text=(
            "Show the framework's welcome text, with a contents list and the "
            "roles grouped by family for narrow screens."
        ),
    )
    framework_welcome_body = StreamField(
        [
            ("section", FrameworkWelcomeSectionBlock()),
            ("skill_level_definitions", SkillLevelDefinitionsBlock()),
            ("section_break", SectionBreakBlock()),
            (
                "inset_text",
                InsetTextBlock(features=FRAMEWORK_WELCOME_RICH_TEXT_FEATURES),
            ),
        ],
        blank=True,
        use_json_field=True,
        verbose_name="Framework welcome content",
        help_text=(
            "The welcome page's editorial content, shown when Show framework "
            "welcome content is switched on. The contents list is built from the "
            "main section headings."
        ),
    )

    class Meta:
        abstract = True

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        # Nothing framework-shaped until an editor has asked for it, and
        # nothing at all on a site without the framework. The lookup below
        # reads as a read and is not one: Wagtail's BaseSiteSetting.for_site
        # does a get_or_create, so calling it unconditionally wrote a
        # framework settings row for every site that rendered any
        # page, including sites with the feature off and its admin panel
        # unregistered.
        #
        # The flag is checked as well as the switches because the switches are
        # columns the framework page types share, and a stored True outlives
        # the site it was set on: the page export carries all four fields, so
        # importing a framework export elsewhere sets them. Their panels are
        # hidden without the flag, which would leave an editor looking at a role
        # navigation they cannot find the switch for.
        wants_framework = settings.FEATURE_FLAGS.get("SKILLS") and (
            self.show_role_navigation
            or self.show_framework_welcome
            or self.show_framework_updates
        )
        if wants_framework:
            framework_wording = CapabilityFrameworkWordingSettings.for_request(request)
            if self.show_role_navigation or self.show_framework_welcome:
                # current_page_id marks this page current in the "Further
                # resources" group when it is one of them -- a framework content
                # page highlights itself there, the way a role does on its route.
                groups = role_navigation_groups(
                    current_page_id=self.pk, wording=framework_wording, request=request
                )
                if self.show_role_navigation:
                    context["role_navigation"] = groups
                    # The navigation's top link is the framework main page,
                    # current when that page is the one being rendered.
                    context["framework_home"] = framework_home_link(
                        is_current=isinstance(self, FrameworkMainPage)
                    )
                if self.show_framework_welcome:
                    context["framework_sections"] = groups
                    context["framework_contents"] = self.framework_welcome_contents(
                        groups
                    )
            if self.show_framework_updates:
                context["framework_changelog"] = site_wide_changelog()
            # The framework wording names the updates block and the navigation.
            context["framework_wording"] = framework_wording
        # Only one side column fits, so the role navigation wins where an
        # editor has asked for both.
        context["heading_navigation"] = (
            self.enable_free_text_heading_navigation and not self.show_role_navigation
        )
        # The generic ancestor trail names its first crumb after the site,
        # which on the framework is a sentence long and reads as a heading
        # rather than a way back, and it shows at every width where the live
        # service has no breadcrumb at all. Follow the role pages instead:
        # home, then this page, and only where the navigation is hidden.
        #
        # On a framework only. The home label below comes from wording settings
        # the admin does not register without this flag, so a site without it
        # would carry a crumb no editor there can change.
        if settings.FEATURE_FLAGS.get("SKILLS"):
            site = Site.find_for_request(request)
            if site and self.pk != site.root_page_id:
                context["breadcrumbs"] = framework_breadcrumbs(request, self)
                context["breadcrumbs_mobile_only"] = True
            else:
                context["breadcrumbs"] = []
        return context

    def framework_welcome_contents(self, role_groups: list[dict]) -> list[dict]:
        """The contents list at the top of the welcome page.

        Built from the main section headings, so an editor adding a section to
        the page adds it here too. The role groups sit directly under the
        opening section, where the live service puts them.
        """
        sections = [
            {"title": block.value["heading"], "anchor": block.value.anchor_id()}
            for block in self.framework_welcome_body
            if block.block_type == "section" and block.value.get("level") == "h2"
        ]
        groups = [
            {"title": group["title"], "anchor": slugify(group["title"])}
            for group in role_groups or []
        ]
        return sections[:1] + groups + sections[1:]




class RoleRenderingMixin:
    """The rendering of a single role, shared by the framework main page's route.

    A role has no page of its own any more: the framework main page serves one
    at ``/<main-page>/role/<role-slug>/`` for every ``GovukRole`` snippet (see
    ``FrameworkMainPage.serve_role``). This mixin holds the logic that used to
    live on the old ``RolePage`` -- the section it builds for a role, the
    headings and sentences the framework prints around it, the in-page anchors,
    and the surrounding context (skills index, related roles, breadcrumbs).

    Because a route renders exactly one role, the anchors match the framework's
    own ids without the per-role suffix the old multi-role page needed.
    """

    @staticmethod
    def _extract_role_id(value) -> int | None:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str):
            parsed = row_id_from_text(value)
            if parsed is not None and parsed > 0:
                return parsed
        role_pk = getattr(value, "pk", None)
        if type(role_pk) is int and role_pk > 0:
            return role_pk
        if isinstance(value, dict):
            for key in ("value", "id", "pk"):
                extracted = RoleRenderingMixin._extract_role_id(value.get(key))
                if extracted:
                    return extracted
        return None

    @staticmethod
    def _role_section(role: "GovukRole") -> dict:
        return {
            "role": role,
            "levels": role.get_levels_with_skills(),
            "is_scs": role.is_senior_civil_service,
            "scs_skills": role.get_scs_skills(),
            "scs_grades": role.get_scs_grade_labels(),
        }

    @staticmethod
    def _display_role_name(title: str) -> str:
        """Lowercase a role title for mid-sentence use, keeping acronyms."""
        return " ".join(
            word if word.isupper() else word.lower()
            for word in (title or "").split()
        )

    # A "u" sounded as "you" takes "a" rather than "an", which is how the
    # framework writes "a user researcher".
    _CONSONANT_SOUNDED_VOWELS = ("eu", "ubi", "uni", "use", "usu", "uti")

    @classmethod
    def _lead_role_name(cls, title: str) -> str:
        """A role title as the framework writes it in the sentence below the
        heading, where only the opening word is lowered so that the capitals
        inside "Development operations (DevOps) engineer" survive.

        An acronym opening the title is left as it is, the same exception the
        heading above makes: "an IT service manager", not "an it service
        manager" a line under "What an IT service manager does".
        """
        words = (title or "").split()
        if not words:
            return ""
        first = words[0] if words[0].isupper() else words[0].lower()
        return " ".join([first, *words[1:]])

    @classmethod
    def _role_article(cls, name: str) -> str:
        """"a" or "an" for a role name, chosen by how the name is said rather
        than how it is spelt: "a user researcher" but "an IT service manager".
        """
        lowered = (name or "").lstrip().lower()
        if not lowered:
            return "a"
        vowel_sounded = lowered[0] in "aeiou" and not lowered.startswith(
            cls._CONSONANT_SOUNDED_VOWELS
        )
        return "an" if vowel_sounded else "a"

    @classmethod
    def _overview_heading(cls, display_role_name: str, wording) -> str:
        """The heading over a role's description, and the contents entry that
        points at it, which have to read the same."""
        article = cls._role_article(display_role_name)
        return wording.overview_heading_text.replace(
            wording.ARTICLE_PLACEHOLDER, article
        ).replace(wording.ROLE_PLACEHOLDER, display_role_name)

    @classmethod
    def _role_lead(cls, section: dict, wording) -> str:
        """The one sentence the framework prints under a role's heading.

        For example "Find out what a business architect in government does and
        the skills you need to do the role at each level." Senior Civil Service
        roles have no levels, so theirs stops at the role.
        """
        name = cls._lead_role_name(section["role"].title)
        if not name:
            return ""

        article = cls._role_article(name)
        text = wording.scs_role_lead_text if section["is_scs"] else wording.role_lead_text
        return text.replace(wording.ARTICLE_PLACEHOLDER, article).replace(
            wording.ROLE_PLACEHOLDER, name
        )

    @classmethod
    def _role_levels_intro(cls, section: dict, wording) -> list[str]:
        """The two sentences the framework prints under the role levels heading.

        For example "There are 4 business architect role levels, from trainee
        business architect to lead business architect."
        """
        levels = section["levels"]
        if not levels:
            return []

        display_role_name = section["display_role_name"]
        titles = [
            cls._display_role_name(level["title"])
            for level in levels
            if level["title"]
        ]

        if len(levels) == 1:
            opening = wording.role_levels_opening_one.replace(
                wording.ROLE_PLACEHOLDER, display_role_name
            )
            described = wording.role_levels_described_one
        else:
            levels_range = ""
            if len(titles) > 1:
                levels_range = wording.role_levels_range_text.replace(
                    wording.FIRST_PLACEHOLDER, titles[0]
                ).replace(wording.LAST_PLACEHOLDER, titles[-1])
            opening = (
                wording.role_levels_opening_many.replace(
                    wording.COUNT_PLACEHOLDER, str(len(levels))
                )
                .replace(wording.ROLE_PLACEHOLDER, display_role_name)
                .replace(wording.LEVELS_RANGE_PLACEHOLDER, levels_range)
            )
            described = wording.role_levels_described_many

        return [opening, f"{described} {wording.role_levels_purpose_text}"]

    @staticmethod
    def _section_anchors(
        *, is_scs: bool, has_levels: bool, display_role_name: str
    ) -> dict[str, str]:
        """The framework's own ids, so a link written against the live service
        still lands on the section it named.

        The overview keeps "what-a-" whatever article the heading takes:
        "what-a-it-service-manager-does" under "What an IT service manager
        does". A Senior Civil Service role's page reuses three of the others
        differently from the rest of the framework: its skills heading carries
        "role-levels", the shared-skills heading "roles-that-shares", and
        "related-roles" means the roles leading into the job rather than the
        ones beside it.

        That first one is only free because senior roles have no levels, which
        is how the framework is written rather than anything the model holds:
        one tick of the senior box on a role with levels and both sections
        would answer to "role-levels", leaving the page with a repeated id and
        two contents links onto the same heading. Where a senior role does have
        levels its skills take the ordinary "skills" instead.

        Each route renders one role, so these are the framework's bare ids with
        no per-role suffix -- the ids a link written against the live service
        expects.
        """
        return {
            "overview": f"what-a-{slugify(display_role_name)}-does",
            "skills": "role-levels" if is_scs and not has_levels else "skills",
            "levels": "role-levels",
            "related_roles": "roles-that-shares" if is_scs else "related-roles",
            "progression_scs_roles": "related-scs-roles",
            "progression_roles": (
                "related-roles" if is_scs else "roles-that-could-lead-here"
            ),
            "updates": "update-history",
        }

    @staticmethod
    def _contents_entries(section: dict) -> list[dict]:
        """In-page contents links, in the order the sections are rendered.

        Each one repeats the heading it points at, so both read the wording
        the section was given rather than each writing out its own.
        """
        anchors = section["anchors"]
        wording = section["wording"]
        entries: list[dict] = []

        if section["role"].body:
            entries.append(
                {
                    "anchor": anchors["overview"],
                    "text": section["overview_heading"],
                    "children": [],
                }
            )
        if section["scs_skills"]:
            entries.append(
                {
                    "anchor": anchors["skills"],
                    "text": wording["scs_skills_heading"],
                    "children": [],
                }
            )
        if section["levels"]:
            entries.append(
                {
                    "anchor": anchors["levels"],
                    "text": wording["role_levels_heading"],
                    "children": [
                        {
                            "anchor": level["anchor"],
                            "text": f"{level['number']}. {level['title']}",
                            "children": [],
                        }
                        for level in section["levels"]
                        if level["anchor"]
                    ],
                }
            )
        if section["related_roles"]:
            entries.append(
                {
                    "anchor": anchors["related_roles"],
                    "text": wording["related_roles_heading"],
                    "children": [],
                }
            )
        if section["progression_scs_roles"]:
            entries.append(
                {
                    "anchor": anchors["progression_scs_roles"],
                    "text": wording["progression_scs_roles_heading"],
                    "children": [],
                }
            )
        if section["progression_roles"]:
            entries.append(
                {
                    "anchor": anchors["progression_roles"],
                    "text": wording["progression_roles_heading"],
                    "children": [],
                }
            )
        return entries

    def _enrich_section(
        self, section: dict, framework_wording, role_page_urls: dict[int, str]
    ) -> None:
        """Fill a role's section with the headings, anchors, links and prose the
        template renders around it."""
        role = section["role"]
        display_role_name = self._display_role_name(role.title)
        section["display_role_name"] = display_role_name
        section["overview_heading"] = self._overview_heading(
            display_role_name, framework_wording
        )
        section["wording"] = framework_wording.for_role(
            display_role_name=display_role_name, role_title=role.title
        )
        section["anchors"] = self._section_anchors(
            is_scs=section["is_scs"],
            has_levels=bool(section["levels"]),
            display_role_name=display_role_name,
        )
        for number, level in enumerate(section["levels"], start=1):
            level["number"] = number
            level["anchor"] = slugify(level["title"]) if level["title"] else ""
            for skill_row in level["skills"]:
                skill_row["level_scale_text"] = framework_wording.skill_level_scale(
                    label=skill_row["required_level_label"],
                    ordinal=SKILL_LEVEL_ORDINALS.get(skill_row["required_level"], ""),
                )
        section["related_roles"] = [
            {**entry, "url": role_page_urls.get(entry["role"].pk, "")}
            for entry in role.get_related_roles()
        ]
        section["progression_roles"] = [
            {"role": entry, "url": role_page_urls.get(entry.pk, "")}
            for entry in role.get_roles_that_could_lead_here()
        ]
        # Only on the way up. A senior role lists what leads into it, and the
        # framework leaves it there rather than also pointing on to the senior
        # roles above that one.
        if section["is_scs"]:
            section["progression_scs_roles"] = []
        else:
            senior_roles_by_source = GovukRole.senior_roles_by_source_role_id()
            section["progression_scs_roles"] = [
                {"role": entry, "url": role_page_urls.get(entry.pk, "")}
                for entry in senior_roles_by_source.get(role.pk, [])
            ]
        section["changelog"] = role.get_changelog()
        section["lead"] = self._role_lead(section, framework_wording)
        section["levels_intro"] = self._role_levels_intro(section, framework_wording)
        section["contents"] = self._contents_entries(section)

    def build_role_context(self, request, role: "GovukRole") -> dict:
        """The template context for one role, served under the framework main page.

        A route renders exactly one role, so ``role_sections`` is a single-item
        list and the anchors match the framework's own ids. ``page_heading``
        carries the role's title, which the template shows in place of the main
        page's own.
        """
        section = self._role_section(role)
        role_page_urls = role_page_urls_by_role_id()
        site_root = self._site_root(request)
        skills_page = self._first_live_in_site(FrameworkSkillsPage.objects.all(), site_root)
        framework_wording = CapabilityFrameworkWordingSettings.for_request(request)
        self._enrich_section(section, framework_wording, role_page_urls)

        family = (role.family or "").strip()
        return {
            "page_heading": role.title,
            "role_sections": [section],
            "skills_index_url": skills_page.url if skills_page else "",
            "scs_context_url": self._scs_context_url(site_root),
            "job_grades_url": self._job_grades_url(site_root),
            "framework_wording": framework_wording,
            "role_navigation": role_navigation_groups(
                current_role_slug=role.slug, wording=framework_wording, request=request
            ),
            # The role, not the framework home, is the current thing here.
            "framework_home": framework_home_link(is_current=False),
            # The side navigation is the way around the framework, but it is
            # hidden on a narrow screen, so a breadcrumb stands in for it there.
            "breadcrumbs": framework_breadcrumbs(
                request, self, family=family, title=role.title
            ),
            "breadcrumbs_mobile_only": True,
        }

    # The framework explains, on every Senior Civil Service role, that the job
    # varies with the organisation, and links to the page that sets that out.
    SCS_CONTEXT_SLUG = (
        "context-and-challenges-for-senior-civil-service-roles-in-digital-and-data"
    )
    # Both the role and its levels name the grade the job is usually done at,
    # and link to the page explaining what those grades are.
    JOB_GRADES_SLUG = "job-grades"

    @classmethod
    def _scs_context_url(cls, site_root: Page | None) -> str:
        return cls._page_url_by_slug(cls.SCS_CONTEXT_SLUG, site_root)

    @classmethod
    def _job_grades_url(cls, site_root: Page | None) -> str:
        return cls._page_url_by_slug(cls.JOB_GRADES_SLUG, site_root)

    @classmethod
    def _page_url_by_slug(cls, slug: str, site_root: Page | None) -> str:
        """Where that page sits on this site, or nothing if it has not got one.

        Written out by hand the link is a 404 wherever the page is missing or
        has been moved, so the template asks for a URL and falls back to plain
        text without one. It also spares the reader a redirect: Wagtail gives
        back the path with its trailing slash, which a hand-written one lacks.
        """
        page = cls._first_live_in_site(Page.objects.filter(slug=slug), site_root)
        return page.url if page else ""

    @staticmethod
    def _site_root(request) -> Page | None:
        """The root of the site this request is being served from.

        One database can serve several sites, and a bare ``Page.objects``
        lookup crosses between them, so without this a role page could send a
        reader to another site's copy of a page, or to one this site does not
        publish at all.
        """
        site = Site.find_for_request(request)
        return site.root_page if site else None

    @staticmethod
    def _first_live_in_site(queryset, site_root: Page | None) -> Page | None:
        """The first live page in ``queryset`` belonging to that site.

        Falls back to searching every site when the root is unknown, which is
        no worse than the lookup this replaced.
        """
        queryset = queryset.live()
        if site_root is not None:
            queryset = queryset.descendant_of(site_root, inclusive=False)
        return queryset.first()


class FrameworkMainPage(
    RoutablePageMixin, RoleRenderingMixin, FrameworkFieldsMixin, BaseContentPage
):
    """The single Capability Framework page for the site.

    It carries the framework welcome content and the framework switches, and it
    serves a page for every role snippet at ``role/<role-slug>/``. The roles
    have no page of their own, so the navigation and the role URLs are built
    from the ``GovukRole`` snippets rather than from hand-made pages.

    It can sit anywhere in the tree (no ``parent_page_types`` restriction), and
    ``max_count`` keeps the framework to a single instance per site.
    """

    max_count = 1
    subpage_types = [
        # A framework content page sits beside the roles in the side menu; a
        # plain content page under here does not. That is how an editor
        # chooses which of the two a page is: the live service lists five
        # pages beside the roles and keeps its privacy notice, cookie
        # statement and accessibility statement out of the menu.
        "govuk.FrameworkContentPage",
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkSkillsPage",
    ]
    # The framework page types render the same page as the old framework
    # content page did; only the role route swaps in the role template.
    template = "govuk/content_page.html"
    tags = ClusterTaggableManager(through="govuk.FrameworkMainPageTag", blank=True)

    content_panels = framework_content_panels()

    settings_panels = framework_main_settings_panels()

    @classmethod
    def can_create_at(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_exist_under(parent)

    def serve(self, request, *args, **kwargs):
        """Not a page on a site without the framework.

        ``can_create_at`` and ``can_exist_under`` stop an editor making one,
        but the page import is not the admin and creates any page whose model
        it can resolve, so a framework export landed on another service leaves
        this page in its tree. It 404s there, as the skills index does, rather
        than serve the framework's welcome page on a site that has no framework.
        """
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            raise Http404
        return super().serve(request, *args, **kwargs)

    @path("role/<slug:role_slug>/")
    def serve_role(self, request, role_slug):
        """Serve a role's page from its snippet, or 404.

        A route per role stands in for the pages the framework used to carry.
        The routes sit under ``role/`` so they do not shadow the framework
        main page's own child pages: ``RoutablePageMixin`` resolves its routes
        before it falls through to ordinary child-page routing, so a bare
        ``<slug>/`` pattern would swallow every child page's first path segment
        and 404 it as a missing role. (A child page whose slug is ``role`` is
        the one page this does shadow.)

        ``role``, singular, because that is the live service's URL: it
        publishes every role at ``/role/<slug>``. With the framework main page
        as the site's home page a role is served at its live address and every
        bookmark, search result and link in the migrated content reaches it
        directly, with no redirect to seed and none to go missing.

        Without the framework flag the route 404s, as the old role pages did:
        a route cannot be guarded by ``can_exist_under``, and an import can
        leave a framework main page on a site without the flag.
        """
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            raise Http404
        role = GovukRole.objects.filter(slug=role_slug, live=True).first()
        if role is None:
            raise Http404
        return self.render(
            request,
            template="govuk/role_page.html",
            context_overrides=self.build_role_context(request, role),
        )


class FrameworkContentPage(FrameworkFieldsMixin, BaseContentPage):
    """A framework content page: a page about the framework beside the roles.

    Only ever a child of the single ``FrameworkMainPage``. It always carries the
    role navigation and nothing else framework-shaped: no updates block, no
    welcome layout, no welcome content to author, and no sidebar heading
    navigation. Those are fixed rather than offered -- see ``get_context`` -- so
    the page reads consistently as one of the framework's own.
    """

    parent_page_types = ["govuk.FrameworkMainPage"]
    subpage_types = [
        # A plain page may sit under a framework page, as under the main page:
        # the development instance holds project pages that way.
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkSkillsPage",
    ]
    template = "govuk/content_page.html"
    tags = ClusterTaggableManager(through="govuk.FrameworkContentPageTag", blank=True)

    def serve(self, request, *args, **kwargs):
        """Not a page on a site without the framework -- see FrameworkMainPage."""
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            raise Http404
        return super().serve(request, *args, **kwargs)

    # No framework welcome content in the editor; the welcome layout is the
    # main page's.
    content_panels = base_content_panels()

    settings_panels = framework_content_settings_panels()

    @classmethod
    def can_create_at(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_exist_under(parent)

    def get_context(self, request, *args, **kwargs):
        """Always the role navigation, never the rest.

        A framework content page offers no switches, so its behaviour cannot be
        read off stored fields (an import could carry anything). It is fixed
        here: role navigation on, updates and welcome off, and the sidebar
        heading navigation off so the role navigation keeps that column.
        """
        self.show_role_navigation = True
        self.show_framework_updates = False
        self.show_framework_welcome = False
        self.enable_free_text_heading_navigation = False
        return super().get_context(request, *args, **kwargs)


class FrameworkSkillsPage(Page):
    # One skills index per site, like the framework main page. The skill search
    # results, the role pages and the live-service redirects all resolve "the"
    # skills page, so a second one would make which page they point at arbitrary.
    max_count = 1
    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.FrameworkMainPage",
        "govuk.FrameworkContentPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]
    enable_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable hero styling",
        help_text="When enabled, this page uses hero styling.",
    )
    enable_combined_service_navigation_and_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable combined service navigation and hero styling",
        help_text="When enabled, this page uses a combined service navigation and hero styling.",
    )
    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
    )
    body = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        FieldPanel("body"),
    ]

    # No hero-styling toggles and no sidebar heading navigation, like the other
    # framework pages: the skills index carries the role navigation in that
    # column and sets its own header treatment. Whether it is listed in the
    # sidebar is managed centrally in Sidebar settings.
    settings_panels = base_settings_panels()

    @classmethod
    def can_create_at(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_exist_under(parent)

    def serve(self, request, *args, **kwargs):
        """Not a page on a site without the framework: the A to Z is the
        framework's, not a page type anyone else can serve."""
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            raise Http404
        return super().serve(request, *args, **kwargs)

    def get_skill_sections(self) -> list[dict]:
        # One query for every skill's entries, not one per skill.
        skills = list(
            GovukSkill.objects.filter(live=True).prefetch_related("changelog_entries")
        )
        skills.sort(
            key=lambda skill: (
                (skill.title or "").strip().lower(),
                (skill.slug or "").strip().lower(),
                skill.pk or 0,
            )
        )
        roles_by_skill = GovukRole.roles_by_skill_id()
        role_urls = role_page_urls_by_role_id()
        return [
            {
                "skill": skill,
                "level_rows": [] if skill.is_senior_civil_service else skill.get_level_rows(),
                "leadership_points": skill.get_leadership_points(),
                "is_scs": skill.is_senior_civil_service,
                "roles": [
                    {"role": role, "url": role_urls.get(role.pk, "")}
                    for role in roles_by_skill.get(skill.pk, [])
                ],
                # The same updates a role page shows, so a skill's history is
                # readable where the skill is, not only in search dates.
                "changelog": skill.get_changelog(),
            }
            for skill in skills
        ]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        skill_sections = self.get_skill_sections()
        framework_wording = CapabilityFrameworkWordingSettings.for_request(request)
        for section in skill_sections:
            for level_row in section["level_rows"]:
                level_row["scale_text"] = framework_wording.skill_level_scale(
                    label=level_row["label"], ordinal=level_row["ordinal"]
                )
        context["skill_sections"] = skill_sections
        context["framework_wording"] = framework_wording
        # The skills index sits alongside the roles in the framework, so it
        # carries the same side navigation, and the same narrow-screen
        # breadcrumb standing in for it.
        context["role_navigation"] = role_navigation_groups(
            current_page_id=self.pk, wording=framework_wording, request=request
        )
        # The framework home is the navigation's top link; the skills index is
        # not it, so it is not marked current there.
        context["framework_home"] = framework_home_link(is_current=False)
        context["breadcrumbs"] = framework_breadcrumbs(request, self)
        context["breadcrumbs_mobile_only"] = True
        return context




