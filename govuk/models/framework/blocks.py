
from django.utils.text import slugify
from wagtail import blocks
from wagtail.blocks import StructValue

from ..constants import FRAMEWORK_WELCOME_RICH_TEXT_FEATURES


class FrameworkWelcomeSectionValue(StructValue):
    def anchor_id(self) -> str:
        """The section's link target, so the contents list and the heading agree."""
        return slugify(self.get("anchor") or self.get("heading") or "")


class FrameworkWelcomeSectionBlock(blocks.StructBlock):
    """A heading and its prose."""

    heading = blocks.CharBlock(
        max_length=255,
        help_text="Section heading, for example How to use this framework.",
    )
    level = blocks.ChoiceBlock(
        choices=[
            ("h2", "Main section, listed in the contents"),
            ("h3", "Sub-section, not listed in the contents"),
        ],
        default="h2",
        help_text="Main sections appear in the contents list at the top of the page.",
    )
    anchor = blocks.CharBlock(
        required=False,
        max_length=255,
        help_text=(
            "Optional link target, for example capability-assessments. Defaults to "
            "the heading. Set one to keep existing links working if the heading is "
            "reworded."
        ),
    )
    body = blocks.RichTextBlock(
        features=FRAMEWORK_WELCOME_RICH_TEXT_FEATURES,
        help_text="The section's prose.",
    )

    class Meta:
        icon = "doc-full"
        label = "Section"
        value_class = FrameworkWelcomeSectionValue
        template = "blocks/framework_welcome_section.html"


class SkillLevelBlock(blocks.StructBlock):
    name = blocks.CharBlock(
        max_length=100,
        help_text="Level name, for example Awareness.",
    )
    filled_segments = blocks.IntegerBlock(
        min_value=1,
        max_value=4,
        default=1,
        help_text=(
            "How many of the 4 progress bar segments are filled, so awareness is 1 "
            "and expert is 4."
        ),
    )
    description = blocks.RichTextBlock(
        features=FRAMEWORK_WELCOME_RICH_TEXT_FEATURES,
        help_text="What someone at this level can do.",
    )

    class Meta:
        icon = "list-ul"
        label = "Skill level"


class SkillLevelDefinitionsBlock(blocks.StructBlock):
    """The skill level table, with the progress bars a rich text field would strip."""

    caption = blocks.CharBlock(
        max_length=255,
        default="Skill level definitions",
        help_text="Table caption, read out by screen readers.",
    )
    level_column_heading = blocks.CharBlock(
        max_length=255,
        default="Skill level definitions",
    )
    meaning_column_heading = blocks.CharBlock(
        max_length=255,
        default="What the level means",
    )
    levels = blocks.ListBlock(
        SkillLevelBlock(),
        help_text="One row per level, in ascending order.",
    )

    class Meta:
        icon = "table"
        label = "Skill level definitions"
        template = "blocks/framework_welcome_skill_levels.html"


class SectionBreakBlock(blocks.StaticBlock):
    class Meta:
        icon = "horizontalrule"
        label = "Section break"
        admin_text = "A horizontal rule across the page."
        template = "blocks/framework_welcome_section_break.html"




