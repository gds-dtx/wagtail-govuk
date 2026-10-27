# The schema half of 0087: with every body moved into the stream, drop the rich
# text field and rename the stream to ``body``. Written by hand because
# makemigrations would read the pair as one field removed and another changed.

import wagtail.fields
from django.db import migrations

MODEL_NAMES = ["contentpage", "frameworkmainpage", "frameworkcontentpage"]


class Migration(migrations.Migration):
    dependencies = [
        ("govuk", "0087_fold_body_into_blocks"),
    ]

    operations = [
        *(migrations.RemoveField(model_name=name, name="body") for name in MODEL_NAMES),
        *(
            migrations.RenameField(
                model_name=name, old_name="body_blocks", new_name="body"
            )
            for name in MODEL_NAMES
        ),
        migrations.AlterField(
            model_name="contentpage",
            name="body",
            field=wagtail.fields.StreamField(
                [
                    ("text", 0),
                    ("table", 1),
                    ("cards", 14),
                    ("banner", 18),
                    ("inset_text", 19),
                ],
                blank=True,
                block_lookup={
                    0: ("govuk.models.blocks.BodyTextBlock", (), {}),
                    1: ("govuk.models.GovukTableBlock", (), {}),
                    2: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {
                            "help_text": "Optional heading for this row section.",
                            "required": False,
                        },
                    ),
                    3: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {"max_length": 120, "required": True},
                    ),
                    4: (
                        "wagtail.images.blocks.ImageChooserBlock",
                        (),
                        {
                            "help_text": "Optional header image for this card.",
                            "required": False,
                        },
                    ),
                    5: (
                        "wagtail.blocks.ChoiceBlock",
                        [],
                        {
                            "choices": [("cover", "Cover"), ("contain", "Contain")],
                            "help_text": "How the header image should fit inside the card.",
                        },
                    ),
                    6: (
                        "wagtail.blocks.RichTextBlock",
                        (),
                        {
                            "features": ["bold", "italic", "link", "ul", "ol"],
                            "required": False,
                        },
                    ),
                    7: ("wagtail.blocks.PageChooserBlock", (), {"required": False}),
                    8: (
                        "wagtail.blocks.URLBlock",
                        (),
                        {
                            "help_text": "Use an absolute external URL like https://www.gov.uk/help.",
                            "required": False,
                        },
                    ),
                    9: (
                        "wagtail.blocks.StructBlock",
                        [[("title", 3), ("page", 7), ("external_url", 8)]],
                        {"required": False},
                    ),
                    10: (
                        "wagtail.snippets.blocks.SnippetChooserBlock",
                        ("govuk.GovukTag",),
                        {"required": False},
                    ),
                    11: (
                        "wagtail.blocks.ListBlock",
                        (10,),
                        {
                            "help_text": "Optional tags for this card.",
                            "required": False,
                        },
                    ),
                    12: (
                        "wagtail.blocks.StructBlock",
                        [
                            [
                                ("title", 3),
                                ("image", 4),
                                ("image_fit", 5),
                                ("text", 6),
                                ("link", 9),
                                ("tags", 11),
                            ]
                        ],
                        {},
                    ),
                    13: (
                        "wagtail.blocks.ListBlock",
                        (12,),
                        {
                            "help_text": "Add between 1 and 60 cards in this row.",
                            "max_num": 60,
                            "min_num": 1,
                        },
                    ),
                    14: (
                        "wagtail.blocks.StructBlock",
                        [[("heading", 2), ("cards", 13)]],
                        {"label": "Cards"},
                    ),
                    15: (
                        "wagtail.blocks.RichTextBlock",
                        (),
                        {"features": ["bold", "italic", "link"], "required": False},
                    ),
                    16: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {
                            "help_text": "Optional. Leave blank for a banner without a button.",
                            "max_length": 60,
                            "required": False,
                        },
                    ),
                    17: (
                        "wagtail.blocks.ChoiceBlock",
                        [],
                        {"choices": [("brand", "Brand colour"), ("light", "Light")]},
                    ),
                    18: (
                        "wagtail.blocks.StructBlock",
                        [
                            [
                                ("heading", 3),
                                ("text", 15),
                                ("button_text", 16),
                                ("button_page", 7),
                                ("button_external_url", 8),
                                ("style", 17),
                            ]
                        ],
                        {},
                    ),
                    19: (
                        "govuk.models.InsetTextBlock",
                        (),
                        {
                            "features": [
                                "bold",
                                "italic",
                                "link",
                                "ul",
                                "ol",
                                "line-break",
                            ]
                        },
                    ),
                },
                help_text="Build the page from blocks - text, tables, cards, banners and inset text - in the order you want them to appear.",
                verbose_name="Content blocks",
            ),
        ),
        migrations.AlterField(
            model_name="frameworkcontentpage",
            name="body",
            field=wagtail.fields.StreamField(
                [
                    ("text", 0),
                    ("table", 1),
                    ("cards", 14),
                    ("banner", 18),
                    ("inset_text", 19),
                ],
                blank=True,
                block_lookup={
                    0: ("govuk.models.blocks.BodyTextBlock", (), {}),
                    1: ("govuk.models.GovukTableBlock", (), {}),
                    2: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {
                            "help_text": "Optional heading for this row section.",
                            "required": False,
                        },
                    ),
                    3: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {"max_length": 120, "required": True},
                    ),
                    4: (
                        "wagtail.images.blocks.ImageChooserBlock",
                        (),
                        {
                            "help_text": "Optional header image for this card.",
                            "required": False,
                        },
                    ),
                    5: (
                        "wagtail.blocks.ChoiceBlock",
                        [],
                        {
                            "choices": [("cover", "Cover"), ("contain", "Contain")],
                            "help_text": "How the header image should fit inside the card.",
                        },
                    ),
                    6: (
                        "wagtail.blocks.RichTextBlock",
                        (),
                        {
                            "features": ["bold", "italic", "link", "ul", "ol"],
                            "required": False,
                        },
                    ),
                    7: ("wagtail.blocks.PageChooserBlock", (), {"required": False}),
                    8: (
                        "wagtail.blocks.URLBlock",
                        (),
                        {
                            "help_text": "Use an absolute external URL like https://www.gov.uk/help.",
                            "required": False,
                        },
                    ),
                    9: (
                        "wagtail.blocks.StructBlock",
                        [[("title", 3), ("page", 7), ("external_url", 8)]],
                        {"required": False},
                    ),
                    10: (
                        "wagtail.snippets.blocks.SnippetChooserBlock",
                        ("govuk.GovukTag",),
                        {"required": False},
                    ),
                    11: (
                        "wagtail.blocks.ListBlock",
                        (10,),
                        {
                            "help_text": "Optional tags for this card.",
                            "required": False,
                        },
                    ),
                    12: (
                        "wagtail.blocks.StructBlock",
                        [
                            [
                                ("title", 3),
                                ("image", 4),
                                ("image_fit", 5),
                                ("text", 6),
                                ("link", 9),
                                ("tags", 11),
                            ]
                        ],
                        {},
                    ),
                    13: (
                        "wagtail.blocks.ListBlock",
                        (12,),
                        {
                            "help_text": "Add between 1 and 60 cards in this row.",
                            "max_num": 60,
                            "min_num": 1,
                        },
                    ),
                    14: (
                        "wagtail.blocks.StructBlock",
                        [[("heading", 2), ("cards", 13)]],
                        {"label": "Cards"},
                    ),
                    15: (
                        "wagtail.blocks.RichTextBlock",
                        (),
                        {"features": ["bold", "italic", "link"], "required": False},
                    ),
                    16: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {
                            "help_text": "Optional. Leave blank for a banner without a button.",
                            "max_length": 60,
                            "required": False,
                        },
                    ),
                    17: (
                        "wagtail.blocks.ChoiceBlock",
                        [],
                        {"choices": [("brand", "Brand colour"), ("light", "Light")]},
                    ),
                    18: (
                        "wagtail.blocks.StructBlock",
                        [
                            [
                                ("heading", 3),
                                ("text", 15),
                                ("button_text", 16),
                                ("button_page", 7),
                                ("button_external_url", 8),
                                ("style", 17),
                            ]
                        ],
                        {},
                    ),
                    19: (
                        "govuk.models.InsetTextBlock",
                        (),
                        {
                            "features": [
                                "bold",
                                "italic",
                                "link",
                                "ul",
                                "ol",
                                "line-break",
                            ]
                        },
                    ),
                },
                help_text="Build the page from blocks - text, tables, cards, banners and inset text - in the order you want them to appear.",
                verbose_name="Content blocks",
            ),
        ),
        migrations.AlterField(
            model_name="frameworkmainpage",
            name="body",
            field=wagtail.fields.StreamField(
                [
                    ("text", 0),
                    ("table", 1),
                    ("cards", 14),
                    ("banner", 18),
                    ("inset_text", 19),
                ],
                blank=True,
                block_lookup={
                    0: ("govuk.models.blocks.BodyTextBlock", (), {}),
                    1: ("govuk.models.GovukTableBlock", (), {}),
                    2: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {
                            "help_text": "Optional heading for this row section.",
                            "required": False,
                        },
                    ),
                    3: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {"max_length": 120, "required": True},
                    ),
                    4: (
                        "wagtail.images.blocks.ImageChooserBlock",
                        (),
                        {
                            "help_text": "Optional header image for this card.",
                            "required": False,
                        },
                    ),
                    5: (
                        "wagtail.blocks.ChoiceBlock",
                        [],
                        {
                            "choices": [("cover", "Cover"), ("contain", "Contain")],
                            "help_text": "How the header image should fit inside the card.",
                        },
                    ),
                    6: (
                        "wagtail.blocks.RichTextBlock",
                        (),
                        {
                            "features": ["bold", "italic", "link", "ul", "ol"],
                            "required": False,
                        },
                    ),
                    7: ("wagtail.blocks.PageChooserBlock", (), {"required": False}),
                    8: (
                        "wagtail.blocks.URLBlock",
                        (),
                        {
                            "help_text": "Use an absolute external URL like https://www.gov.uk/help.",
                            "required": False,
                        },
                    ),
                    9: (
                        "wagtail.blocks.StructBlock",
                        [[("title", 3), ("page", 7), ("external_url", 8)]],
                        {"required": False},
                    ),
                    10: (
                        "wagtail.snippets.blocks.SnippetChooserBlock",
                        ("govuk.GovukTag",),
                        {"required": False},
                    ),
                    11: (
                        "wagtail.blocks.ListBlock",
                        (10,),
                        {
                            "help_text": "Optional tags for this card.",
                            "required": False,
                        },
                    ),
                    12: (
                        "wagtail.blocks.StructBlock",
                        [
                            [
                                ("title", 3),
                                ("image", 4),
                                ("image_fit", 5),
                                ("text", 6),
                                ("link", 9),
                                ("tags", 11),
                            ]
                        ],
                        {},
                    ),
                    13: (
                        "wagtail.blocks.ListBlock",
                        (12,),
                        {
                            "help_text": "Add between 1 and 60 cards in this row.",
                            "max_num": 60,
                            "min_num": 1,
                        },
                    ),
                    14: (
                        "wagtail.blocks.StructBlock",
                        [[("heading", 2), ("cards", 13)]],
                        {"label": "Cards"},
                    ),
                    15: (
                        "wagtail.blocks.RichTextBlock",
                        (),
                        {"features": ["bold", "italic", "link"], "required": False},
                    ),
                    16: (
                        "wagtail.blocks.CharBlock",
                        (),
                        {
                            "help_text": "Optional. Leave blank for a banner without a button.",
                            "max_length": 60,
                            "required": False,
                        },
                    ),
                    17: (
                        "wagtail.blocks.ChoiceBlock",
                        [],
                        {"choices": [("brand", "Brand colour"), ("light", "Light")]},
                    ),
                    18: (
                        "wagtail.blocks.StructBlock",
                        [
                            [
                                ("heading", 3),
                                ("text", 15),
                                ("button_text", 16),
                                ("button_page", 7),
                                ("button_external_url", 8),
                                ("style", 17),
                            ]
                        ],
                        {},
                    ),
                    19: (
                        "govuk.models.InsetTextBlock",
                        (),
                        {
                            "features": [
                                "bold",
                                "italic",
                                "link",
                                "ul",
                                "ol",
                                "line-break",
                            ]
                        },
                    ),
                },
                help_text="Build the page from blocks - text, tables, cards, banners and inset text - in the order you want them to appear.",
                verbose_name="Content blocks",
            ),
        ),
    ]
