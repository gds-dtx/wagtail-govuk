from django.utils.html import strip_tags


def normalised_text(value) -> str:
    return " ".join(strip_tags(str(value or "")).split())
