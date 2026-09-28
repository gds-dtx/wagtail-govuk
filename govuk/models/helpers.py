



def _next_unique_slug(
    *,
    model_class,
    candidate: str,
    instance_id: int | None = None,
    fallback: str,
) -> str:
    base_slug = (candidate or "").strip("-")
    if not base_slug:
        base_slug = fallback
    base_slug = base_slug[:120]

    slug_value = base_slug
    suffix = 2
    while model_class.objects.filter(slug=slug_value).exclude(pk=instance_id).exists():
        suffix_text = f"-{suffix}"
        max_base_length = 120 - len(suffix_text)
        slug_value = f"{base_slug[:max_base_length]}{suffix_text}"
        suffix += 1
    return slug_value



