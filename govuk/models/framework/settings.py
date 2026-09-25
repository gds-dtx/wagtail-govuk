
from django.core.exceptions import ValidationError
from django.db import models
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting
from wagtail.models import Orderable


class CapabilityFrameworkWordingSettings(BaseSiteSetting):
    """The words a role or skill page prints that no editor authored.

    Registered as a setting in ``wagtail_hooks``, alongside the skills and roles
    snippets and under the same feature flag: the only pages that read this
    wording are the ones that flag governs, so a service without them has no use
    for a form of 38 fields nothing on it prints.

    Headings, column headings, sentence lead-ins and the two empty states were
    written into the templates, which put them out of reach of the people who
    own the framework's language: changing "Roles that share x skills" meant a
    release. Every default here is the wording the templates already carried,
    so an instance that has never opened this form reads exactly as before.

    ``{role}`` stands for the role's name, lowercased for mid-sentence use the
    way the framework writes it, and is filled in per role.
    """

    ROLE_PLACEHOLDER = "{role}"
    LEVEL_PLACEHOLDER = "{level}"
    ORDINAL_PLACEHOLDER = "{ordinal}"
    ARTICLE_PLACEHOLDER = "{article}"
    FAMILY_PLACEHOLDER = "{family}"
    COUNT_PLACEHOLDER = "{count}"
    FIRST_PLACEHOLDER = "{first}"
    LAST_PLACEHOLDER = "{last}"
    LEVELS_RANGE_PLACEHOLDER = "{levels_range}"

    contents_heading = models.CharField(
        max_length=255,
        default="Contents",
        help_text="Heading over the in-page contents links.",
    )
    last_updated_prefix = models.CharField(
        max_length=255,
        default="Last updated",
        help_text="Shown before the date the role was last changed.",
    )
    see_all_updates_link_text = models.CharField(
        max_length=255,
        default="See all updates",
        help_text="Wording of the link down to the updates section.",
    )

    scs_context_text = models.CharField(
        max_length=255,
        default="A specific {role} job can vary depending on the",
        help_text=(
            "Senior Civil Service roles only. Shown before the link to the "
            "context and challenges page. Use {role} for the role name."
        ),
    )
    scs_context_link_text = models.CharField(
        max_length=255,
        default="context and challenges in your organisation",
        help_text=(
            "Wording of that link. A full stop follows it. Shown as plain "
            "text where the page it points at is missing."
        ),
    )
    scs_skills_heading = models.CharField(
        max_length=255,
        default="Skills for {role}",
        help_text="Heading over a Senior Civil Service role's skills.",
    )
    scs_skills_intro = models.CharField(
        max_length=255,
        default="The {role} role will need to use digital and data skills to:",
        help_text="Sentence introducing the two points below it.",
    )
    scs_skills_leadership_point = models.CharField(
        max_length=255,
        default="be an effective digital and data leader",
        help_text="First of those points.",
    )
    scs_skills_context_point_text = models.CharField(
        max_length=255,
        default="operate in different contexts, depending",
        help_text="Second point, up to the link.",
    )
    scs_skills_context_point_link_text = models.CharField(
        max_length=255,
        default="on the context and challenges in your organisation",
        help_text="Rest of that point, linked to the context and challenges page.",
    )
    scs_skills_table_skill_heading = models.CharField(
        max_length=255,
        default="Skill",
        help_text="First column of the Senior Civil Service skills table.",
    )
    scs_skills_table_description_heading = models.CharField(
        max_length=255,
        default="Description, including examples of leadership",
        help_text="Second column of that table.",
    )
    scs_leadership_examples_heading = models.CharField(
        max_length=255,
        default="Examples of leadership using this skill:",
        help_text="Shown in that table above a skill's leadership points.",
    )

    role_grades_text = models.CharField(
        max_length=255,
        default="This role is often performed at the",
        help_text=(
            "Shown before the link to the job grades page, above the grades a "
            "Senior Civil Service role is done at."
        ),
    )
    level_grades_text = models.CharField(
        max_length=255,
        default="This role level is most often performed at the",
        help_text="The same sentence for a role level rather than a whole role.",
    )
    job_grades_link_text = models.CharField(
        max_length=255,
        default="Civil Service job grade",
        help_text=(
            "Wording of that link. Shown as plain text where the job grades "
            "page is missing."
        ),
    )
    grades_text_after = models.CharField(
        max_length=255,
        default="of:",
        help_text="Wording after that link, ending the sentence.",
    )

    role_levels_heading = models.CharField(
        max_length=255,
        default="{role} role levels",
        help_text=(
            "Heading over a role's levels. Here {role} keeps the role title's "
            "capitals, this being the start of a heading."
        ),
    )
    level_skills_table_skill_heading = models.CharField(
        max_length=255,
        default="Skill",
        help_text="First column of a role level's skills table.",
    )
    level_skills_table_description_heading = models.CharField(
        max_length=255,
        default="Description",
        help_text="Second column of that table.",
    )
    skill_level_prefix = models.CharField(
        max_length=255,
        default="Level:",
        help_text="Shown before the level a role level needs a skill at.",
    )
    skill_level_scale_text = models.CharField(
        max_length=255,
        default="{level} is the {ordinal} of four ascending skill levels",
        help_text=(
            "Read aloud in place of the progress bar, which is decorative. "
            "Use {level} for the level and {ordinal} for first to fourth."
        ),
    )
    skill_points_intro = models.CharField(
        max_length=255,
        default="You can:",
        help_text="Sentence the points under a skill read on from.",
    )

    related_roles_heading = models.CharField(
        max_length=255,
        default="Roles that share {role} skills",
        help_text="Heading over the roles sharing skills with this one.",
    )
    related_roles_table_role_heading = models.CharField(
        max_length=255,
        default="Role",
        help_text="First column of that table.",
    )
    related_roles_table_skills_heading = models.CharField(
        max_length=255,
        default="Shared skills",
        help_text="Second column of that table.",
    )
    progression_scs_roles_heading = models.CharField(
        max_length=255,
        default="Senior Civil Service roles that {role} could lead to",
        help_text="Heading over the senior roles this role leads on to.",
    )
    progression_roles_heading = models.CharField(
        max_length=255,
        default="Roles that could lead to {role}",
        help_text="Heading over the roles that lead into this one.",
    )

    updates_heading = models.CharField(
        max_length=255,
        default="Updates",
        help_text="Heading over a role's change history.",
    )
    published_prefix = models.CharField(
        max_length=255,
        default="Published",
        help_text="Shown before the date the role was first published.",
    )
    no_roles_message = models.CharField(
        max_length=255,
        default="No roles selected.",
        help_text="Shown on a role page with no role chosen and nothing written.",
    )

    scs_skill_label = models.CharField(
        max_length=255,
        default="Senior Civil Service",
        help_text="Shown beside a skill that only Senior Civil Service roles use.",
    )
    skill_leadership_examples_heading = models.CharField(
        max_length=255,
        default="Examples of leadership using this skill",
        help_text="Heading over a skill's leadership points on the skills page.",
    )
    skill_levels_table_level_heading = models.CharField(
        max_length=255,
        default="Skill level",
        help_text="First column of a skill's levels table.",
    )
    skill_levels_table_description_heading = models.CharField(
        max_length=255,
        default="Description",
        help_text="Second column of that table.",
    )
    skill_level_no_description_message = models.CharField(
        max_length=255,
        default="No description provided.",
        help_text="Shown where a skill level has had no points written for it.",
    )
    skill_roles_heading = models.CharField(
        max_length=255,
        default="Roles that require this skill",
        help_text="Heading over the roles that need a skill.",
    )
    no_skills_message = models.CharField(
        max_length=255,
        default="No skills found.",
        help_text="Shown on the skills page while no skills exist.",
    )

    overview_heading_text = models.CharField(
        max_length=255,
        default="What {article} {role} does",
        help_text="Heading over a role's description. {article} is a or an.",
    )
    role_lead_text = models.CharField(
        max_length=500,
        default=(
            "Find out what {article} {role} in government does and the skills "
            "you need to do the role at each level."
        ),
        help_text="The sentence under a role's heading.",
    )
    scs_role_lead_text = models.CharField(
        max_length=500,
        default=(
            "Find out what {article} {role} in the Senior Civil Service does "
            "and the skills you need to do the role."
        ),
        help_text="The sentence under a Senior Civil Service role's heading.",
    )
    role_levels_opening_one = models.CharField(
        max_length=255,
        default="There is one {role} role level.",
        help_text="Opens the levels section for a role with a single level.",
    )
    role_levels_opening_many = models.CharField(
        max_length=255,
        default="There are {count} {role} role levels{levels_range}.",
        help_text=(
            "Opens the levels section. {levels_range} is the from-to clause "
            "below, or nothing where the levels are unnamed."
        ),
    )
    role_levels_range_text = models.CharField(
        max_length=255,
        default=", from {first} to {last}",
        help_text="The from-to clause naming the first and last role levels.",
    )
    role_levels_described_one = models.CharField(
        max_length=255,
        default=(
            "The typical responsibilities and skills for this role level are "
            "described below."
        ),
        help_text="Follows the opening for a role with a single level.",
    )
    role_levels_described_many = models.CharField(
        max_length=255,
        default=(
            "The typical responsibilities and skills for each role level are "
            "described in the sections below."
        ),
        help_text="Follows the opening for a role with several levels.",
    )
    role_levels_purpose_text = models.CharField(
        max_length=500,
        default=(
            "You can use this to identify the skills you need to progress in "
            "your career, or simply to learn more about each role in the "
            "Government Digital and Data profession."
        ),
        help_text="Closes the sentence under the role levels heading.",
    )
    show_all_updates_link_text = models.CharField(
        max_length=100,
        default="+ show all updates",
        help_text="The link that opens the home page's collapsed update history.",
    )
    hide_all_updates_link_text = models.CharField(
        max_length=100,
        default="- hide all updates",
        help_text="The link that closes the home page's update history.",
    )
    further_resources_heading = models.CharField(
        max_length=255,
        default="Further resources",
        help_text="Heading over the navigation's non-role pages.",
    )
    role_family_group_title = models.CharField(
        max_length=255,
        default="{family} roles",
        help_text=(
            "Title of each family's navigation group and home page section. "
            "{family} is the family's name from the role records."
        ),
    )
    breadcrumb_home_label = models.CharField(
        max_length=100,
        default="Home",
        help_text="The first entry of the narrow-screen breadcrumb.",
    )

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("contents_heading"),
                FieldPanel("last_updated_prefix"),
                FieldPanel("see_all_updates_link_text"),
                FieldPanel("overview_heading_text"),
                FieldPanel("role_lead_text"),
                FieldPanel("scs_role_lead_text"),
            ],
            heading="Above a role",
        ),
        MultiFieldPanel(
            [
                FieldPanel("scs_context_text"),
                FieldPanel("scs_context_link_text"),
                FieldPanel("scs_skills_heading"),
                FieldPanel("scs_skills_intro"),
                FieldPanel("scs_skills_leadership_point"),
                FieldPanel("scs_skills_context_point_text"),
                FieldPanel("scs_skills_context_point_link_text"),
                FieldPanel("scs_skills_table_skill_heading"),
                FieldPanel("scs_skills_table_description_heading"),
                FieldPanel("scs_leadership_examples_heading"),
            ],
            heading="Senior Civil Service roles",
        ),
        MultiFieldPanel(
            [
                FieldPanel("role_grades_text"),
                FieldPanel("level_grades_text"),
                FieldPanel("job_grades_link_text"),
                FieldPanel("grades_text_after"),
            ],
            heading="Job grades",
        ),
        MultiFieldPanel(
            [
                FieldPanel("role_levels_heading"),
                FieldPanel("level_skills_table_skill_heading"),
                FieldPanel("level_skills_table_description_heading"),
                FieldPanel("skill_level_prefix"),
                FieldPanel("skill_level_scale_text"),
                FieldPanel("skill_points_intro"),
            ],
            heading="Role levels",
        ),
        MultiFieldPanel(
            [
                FieldPanel("role_levels_opening_one"),
                FieldPanel("role_levels_opening_many"),
                FieldPanel("role_levels_range_text"),
                FieldPanel("role_levels_described_one"),
                FieldPanel("role_levels_described_many"),
                FieldPanel("role_levels_purpose_text"),
            ],
            heading="Role levels introduction",
        ),
        MultiFieldPanel(
            [
                FieldPanel("related_roles_heading"),
                FieldPanel("related_roles_table_role_heading"),
                FieldPanel("related_roles_table_skills_heading"),
                FieldPanel("progression_scs_roles_heading"),
                FieldPanel("progression_roles_heading"),
            ],
            heading="Related roles and career paths",
        ),
        MultiFieldPanel(
            [
                FieldPanel("updates_heading"),
                FieldPanel("published_prefix"),
                FieldPanel("show_all_updates_link_text"),
                FieldPanel("hide_all_updates_link_text"),
                FieldPanel("no_roles_message"),
            ],
            heading="Updates and empty states",
        ),
        MultiFieldPanel(
            [
                FieldPanel("further_resources_heading"),
                FieldPanel("role_family_group_title"),
                FieldPanel("breadcrumb_home_label"),
            ],
            heading="Navigation",
        ),
        MultiFieldPanel(
            [
                FieldPanel("scs_skill_label"),
                FieldPanel("skill_leadership_examples_heading"),
                FieldPanel("skill_levels_table_level_heading"),
                FieldPanel("skill_levels_table_description_heading"),
                FieldPanel("skill_level_no_description_message"),
                FieldPanel("skill_roles_heading"),
                FieldPanel("no_skills_message"),
            ],
            heading="Skills page",
        ),
    ]

    class Meta:
        verbose_name = "Capability framework wording"
        verbose_name_plural = "Capability framework wording"

    # Filled in per role, so a page holding several does not have to choose one.
    ROLE_FIELDS = (
        "scs_context_text",
        "scs_skills_heading",
        "scs_skills_intro",
        "related_roles_heading",
        "progression_scs_roles_heading",
        "progression_roles_heading",
    )

    def for_role(self, *, display_role_name: str, role_title: str) -> dict[str, str]:
        """This role's wording, with its name already in place.

        The headings come back here rather than being substituted in the
        template because the in-page contents links repeat them, and the two
        have to say the same thing for the link to make sense.
        """
        wording = {
            name: getattr(self, name).replace(
                self.ROLE_PLACEHOLDER, display_role_name
            )
            for name in self.ROLE_FIELDS
        }
        # A heading opens with the role, so it keeps the capitals in a title
        # like "Development operations (DevOps) engineer" rather than the
        # lowercased form the mid-sentence wording uses.
        wording["role_levels_heading"] = self.role_levels_heading.replace(
            self.ROLE_PLACEHOLDER, role_title
        )
        return wording

    def family_group_title(self, family: str) -> str:
        """A family's navigation group title, used as the home page section
        heading and the narrow-screen breadcrumb entry alike, so the anchor
        the breadcrumb points at always matches the heading it lands on."""
        return self.role_family_group_title.replace(self.FAMILY_PLACEHOLDER, family)

    def skill_level_scale(self, *, label: str, ordinal: str) -> str:
        """What a screen reader is given in place of the progress bar."""
        return self.skill_level_scale_text.replace(
            self.LEVEL_PLACEHOLDER, label
        ).replace(self.ORDINAL_PLACEHOLDER, ordinal)




class SidebarSettings(ClusterableModel, BaseSiteSetting):
    """Editor control of the framework sidebar's "Further resources" listing.

    Each row picks a framework content page (or the skills index), says whether
    it is visible, and -- through its position in the list -- sets the order.
    Listing any page makes this list the menu: framework children left off it
    are left out. Leaving the list empty shows every framework child in tree
    order, which is what a site gets before anyone configures it.
    ``further_resources_group`` reads all of this.
    """

    panels = [
        InlinePanel(
            "items",
            heading="Sidebar pages",
            label="Page",
            help_text=(
                "Choose the pages shown in the framework sidebar's further "
                "resources, drag to reorder, and untick any to hide it without "
                "losing its place. These are the only pages shown: a framework "
                "page left off this list does not appear. Leave the list empty "
                "to show every framework page in page-tree order."
            ),
        ),
    ]

    class Meta:
        verbose_name = "Sidebar settings"
        verbose_name_plural = "Sidebar settings"


class SidebarNavigationItem(Orderable):
    setting = ParentalKey(
        SidebarSettings, on_delete=models.CASCADE, related_name="items"
    )
    page = models.ForeignKey(
        "wagtailcore.Page", on_delete=models.CASCADE, related_name="+"
    )
    visible = models.BooleanField(
        default=True,
        help_text="Untick to hide this page from the sidebar without removing it.",
    )

    panels = [
        FieldPanel("page"),
        FieldPanel("visible"),
    ]

    ALLOWED_PAGE_TYPES = {"FrameworkContentPage", "FrameworkSkillsPage"}

    def clean(self):
        super().clean()
        # Only framework content pages and the skills index belong in the
        # sidebar list; anything else is ignored by ``further_resources_group``
        # anyway, so guide the editor here rather than let it silently do
        # nothing.
        if self.page_id:
            specific_class = self.page.specific_class
            if specific_class is None or specific_class.__name__ not in self.ALLOWED_PAGE_TYPES:
                raise ValidationError(
                    {"page": "Choose a framework content page or the skills index."}
                )




