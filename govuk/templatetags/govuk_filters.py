from django import template

register = template.Library()


ORDINAL_WORDS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
}


@register.filter
def ordinal_word(value):
    """Spell a small position out, for screen reader wording like "the first of 4"."""
    try:
        return ORDINAL_WORDS[int(value)]
    except (KeyError, TypeError, ValueError):
        return value


@register.filter
def comma_number(value):
    if value in (None, ""):
        return "0"

    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value
