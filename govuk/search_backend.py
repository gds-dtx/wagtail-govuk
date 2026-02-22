from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.paginator import Page as PaginatorPage
from django.core.paginator import Paginator
from django.db import connections
from django.db.models import Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.utils.html import strip_tags
from django.utils import timezone
from wagtail.models import Page, Site

from govuk.models import ContentPage, ExternalContentItem, SectionPage

DEFAULT_PAGE_SIZE = 15
SEARCH_CONFIG = "english"
SEARCH_WEIGHTS = [0.1, 0.2, 0.4, 1.0]
PAGE_TAG_TEXT_WEIGHT = 0.75
CARD_TAG_TEXT_WEIGHT = 1.0
TAG_RESULT_WEIGHT = 1.2
EXTERNAL_SOURCE_TEXT_WEIGHT = 0.4
EXTERNAL_TAG_TEXT_WEIGHT = 0.6
EXTERNAL_RECENCY_BOOST_BUCKETS: tuple[tuple[int, float], ...] = (
    (7, 8.0),
    (30, 5.0),
    (90, 3.0),
    (180, 1.5),
    (365, 0.75),
)


@dataclass(slots=True)
class SearchResultItem:
    title: str
    search_description: str
    url: str
    score: float = 0.0
    breadcrumbs: list[dict[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_name: str = ""
    last_updated: datetime | None = None


class SearchBackend:
    def search(
        self, query: str, filters: dict[str, Any] | None = None, page: int | str = 1
    ) -> PaginatorPage:
        filters = filters or {}
        clean_query = (query or "").strip()

        if not clean_query:
            paginator = Paginator([], self._page_size(filters))
            return paginator.get_page(page)

        page_results = self._build_page_results(clean_query, filters)
        hero_results = self._build_hero_results(clean_query, filters)
        card_results = self._build_card_results(clean_query, filters)
        tag_results = self._build_tag_results(clean_query, filters)
        external_content_results = self._build_external_content_results(
            clean_query,
            filters,
        )
        combined_results = self._merge_results(
            page_results
            + hero_results
            + card_results
            + tag_results
            + external_content_results
        )

        paginator = Paginator(combined_results, self._page_size(filters))
        return paginator.get_page(page)

    def _build_page_results(
        self, query: str, filters: dict[str, Any]
    ) -> list[SearchResultItem]:
        queryset = self._apply_filters(Page.objects.all(), filters)
        if self._is_postgres(queryset.db):
            queryset = self._search_pages_postgres(queryset, query)
        else:
            queryset = self._search_pages_sqlite(queryset, query)

        request = filters.get("request")
        site_root = self._site_root_page(filters)
        results: list[SearchResultItem] = []
        for page in queryset:
            specific_page = page.specific
            title = page.title
            description = page.search_description or ""
            tag_labels = self._page_tag_labels(specific_page)
            tags_text = self._clean_text(" ".join(tag_labels))
            page_rank = float(getattr(page, "rank", 0.0) or 0.0)
            score = page_rank + self._text_relevance(
                query,
                (
                    (title, 3.0),
                    (page.seo_title, 2.0),
                    (description, 1.0),
                    (tags_text, PAGE_TAG_TEXT_WEIGHT),
                ),
            )
            if score <= 0:
                continue
            results.append(
                SearchResultItem(
                    title=title,
                    search_description=description,
                    url=self._page_url(page, request),
                    score=score,
                    breadcrumbs=self._page_breadcrumbs(
                        page,
                        request=request,
                        site_root=site_root,
                        include_page=False,
                    ),
                    tags=tag_labels,
                    last_updated=self._page_last_updated(page),
                )
            )
        return results

    def _build_card_results(
        self, query: str, filters: dict[str, Any]
    ) -> list[SearchResultItem]:
        section_pages = self._apply_filters(SectionPage.objects.all(), filters)
        if self._is_postgres(section_pages.db):
            section_pages = self._search_sections_postgres(section_pages, query)
        else:
            section_pages = self._search_sections_sqlite(section_pages, query)

        request = filters.get("request")
        site_root = self._site_root_page(filters)
        query_lower = query.lower()
        results: list[SearchResultItem] = []

        for section_page in section_pages:
            section_url = self._page_url(section_page, request)
            section_rank = float(getattr(section_page, "card_rank", 0.0) or 0.0)

            for card in self._section_cards(section_page):
                title = self._clean_text(card.get("title"))
                text = self._clean_text(card.get("text"))
                link_text = self._clean_text(card.get("link_text"))
                link_url = (card.get("link_url") or "").strip()
                card_tag_text: list[str] = []
                card_tag_labels: list[str] = []
                for tag in card.get("tags", []):
                    tag_text = self._tag_text(tag)
                    if tag_text:
                        card_tag_text.append(tag_text)
                    tag_label = self._tag_label(tag)
                    if tag_label:
                        card_tag_labels.append(tag_label)
                tags_text = self._clean_text(" ".join(card_tag_text))
                result_tags = self._unique_values(
                    card_tag_labels + self._page_tag_labels(section_page)
                )

                searchable_text = " ".join(
                    value
                    for value in (title, text, link_text, link_url, tags_text)
                    if value
                ).lower()
                if query_lower not in searchable_text:
                    continue

                score = section_rank + self._text_relevance(
                    query,
                    (
                        (title, 3.5),
                        (text, 2.0),
                        (link_text, 1.5),
                        (link_url, 1.0),
                        (tags_text, CARD_TAG_TEXT_WEIGHT),
                    ),
                )
                results.append(
                    SearchResultItem(
                        title=title or section_page.title,
                        search_description=(
                            text
                            or section_page.search_description
                            or f"Card in {section_page.title}"
                        ),
                        url=link_url or section_url,
                        score=score,
                        breadcrumbs=self._page_breadcrumbs(
                            section_page,
                            request=request,
                            site_root=site_root,
                            include_page=True,
                        ),
                        tags=result_tags,
                        last_updated=self._page_last_updated(section_page),
                    )
                )

        return results

    def _build_tag_results(
        self, query: str, filters: dict[str, Any]
    ) -> list[SearchResultItem]:
        request = filters.get("request")
        site_root = self._site_root_page(filters)
        results: list[SearchResultItem] = []

        for model in (ContentPage, SectionPage):
            queryset = self._apply_filters(
                model.objects.filter(
                    Q(tags__slug__icontains=query) | Q(tags__name__icontains=query)
                )
                .prefetch_related("tags")
                .distinct(),
                filters,
            )

            for page in queryset:
                tag_labels = self._page_tag_labels(page)
                tags_text = self._clean_text(" ".join(tag_labels))
                score = self._text_relevance(query, ((tags_text, TAG_RESULT_WEIGHT),))
                if score <= 0:
                    continue

                description = page.search_description or self._tag_result_description(
                    page
                )
                results.append(
                    SearchResultItem(
                        title=page.title,
                        search_description=description,
                        url=self._page_url(page, request),
                        score=score,
                        breadcrumbs=self._page_breadcrumbs(
                            page,
                            request=request,
                            site_root=site_root,
                            include_page=False,
                        ),
                        tags=tag_labels,
                        last_updated=self._page_last_updated(page),
                    )
                )

        return results

    def _build_hero_results(
        self, query: str, filters: dict[str, Any]
    ) -> list[SearchResultItem]:
        request = filters.get("request")
        site_root = self._site_root_page(filters)
        results: list[SearchResultItem] = []

        for model in (ContentPage, SectionPage):
            queryset = self._apply_filters(model.objects.all(), filters)
            if self._is_postgres(queryset.db):
                queryset = self._search_hero_postgres(queryset, query)
            else:
                queryset = self._search_hero_sqlite(queryset, query)

            for page in queryset:
                hero_title = self._clean_text(getattr(page, "hero_title", ""))
                hero_intro = self._clean_text(getattr(page, "hero_intro", ""))
                tag_labels = self._page_tag_labels(page)
                score = float(
                    getattr(page, "hero_rank", None)
                    or self._text_relevance(
                        query,
                        (
                            (hero_title, 3.0),
                            (hero_intro, 2.0),
                        ),
                    )
                )
                results.append(
                    SearchResultItem(
                        title=page.title,
                        search_description=hero_intro or page.search_description or "",
                        url=self._page_url(page, request),
                        score=score,
                        breadcrumbs=self._page_breadcrumbs(
                            page,
                            request=request,
                            site_root=site_root,
                            include_page=False,
                        ),
                        tags=tag_labels,
                        last_updated=self._page_last_updated(page),
                    )
                )

        return results

    def _build_external_content_results(
        self, query: str, filters: dict[str, Any]
    ) -> list[SearchResultItem]:
        queryset = self._external_content_queryset(filters)
        if self._is_postgres(queryset.db):
            queryset = self._search_external_content_postgres(queryset, query)
        else:
            queryset = self._search_external_content_sqlite(queryset, query)

        results: list[SearchResultItem] = []
        for item in queryset:
            tag_labels = self._page_tag_labels(item)
            tags_text = self._clean_text(" ".join(tag_labels))
            source_name = self._clean_text(getattr(item.source, "name", ""))
            item_rank = float(getattr(item, "external_rank", 0.0) or 0.0)
            recency_boost = self._external_recency_boost(item)
            score = (
                item_rank
                + recency_boost
                + self._text_relevance(
                    query,
                    (
                        (item.title, 3.0),
                        (item.summary, 2.0),
                        (item.url, 1.0),
                        (source_name, EXTERNAL_SOURCE_TEXT_WEIGHT),
                        (tags_text, EXTERNAL_TAG_TEXT_WEIGHT),
                    ),
                )
            )
            if score <= 0:
                continue

            description = self._clean_text(item.summary)
            if not description and source_name:
                description = f"Source: {source_name}"
            if not description:
                description = self._tag_result_description(item)

            results.append(
                SearchResultItem(
                    title=item.title or item.url,
                    search_description=description,
                    url=item.url,
                    score=score,
                    tags=tag_labels,
                    source_name=source_name,
                    last_updated=self._external_content_last_updated(item),
                )
            )

        return results

    def _search_pages_sqlite(self, queryset: QuerySet, query: str) -> QuerySet:
        return queryset.filter(
            Q(title__icontains=query)
            | Q(seo_title__icontains=query)
            | Q(search_description__icontains=query)
        ).order_by("-first_published_at", "-latest_revision_created_at", "title")

    def _search_pages_postgres(self, queryset: QuerySet, query: str) -> QuerySet:
        search_vector = (
            SearchVector("title", weight="A", config=SEARCH_CONFIG)
            + SearchVector("seo_title", weight="B", config=SEARCH_CONFIG)
            + SearchVector("search_description", weight="C", config=SEARCH_CONFIG)
        )
        search_query = SearchQuery(query, search_type="websearch", config=SEARCH_CONFIG)
        return (
            queryset.annotate(
                rank=SearchRank(search_vector, search_query, weights=SEARCH_WEIGHTS),
            )
            .filter(rank__gt=0)
            .order_by("-rank", "-first_published_at", "title")
        )

    def _search_sections_sqlite(self, queryset: QuerySet, query: str) -> QuerySet:
        return queryset.filter(rows__icontains=query).order_by(
            "-first_published_at", "-latest_revision_created_at", "title"
        )

    def _search_sections_postgres(self, queryset: QuerySet, query: str) -> QuerySet:
        rows_vector = SearchVector(
            Cast("rows", TextField()),
            weight="D",
            config=SEARCH_CONFIG,
        )
        search_query = SearchQuery(query, search_type="websearch", config=SEARCH_CONFIG)
        return (
            queryset.annotate(
                card_rank=SearchRank(rows_vector, search_query, weights=SEARCH_WEIGHTS),
            )
            .filter(card_rank__gt=0)
            .order_by("-card_rank", "-first_published_at", "title")
        )

    def _search_hero_sqlite(self, queryset: QuerySet, query: str) -> QuerySet:
        return queryset.filter(
            Q(hero_title__icontains=query) | Q(hero_intro__icontains=query)
        ).order_by("-first_published_at", "-latest_revision_created_at", "title")

    def _search_hero_postgres(self, queryset: QuerySet, query: str) -> QuerySet:
        hero_vector = SearchVector(
            "hero_title", weight="A", config=SEARCH_CONFIG
        ) + SearchVector(
            Cast("hero_intro", TextField()), weight="B", config=SEARCH_CONFIG
        )
        search_query = SearchQuery(query, search_type="websearch", config=SEARCH_CONFIG)
        return (
            queryset.annotate(
                hero_rank=SearchRank(hero_vector, search_query, weights=SEARCH_WEIGHTS),
            )
            .filter(hero_rank__gt=0)
            .order_by("-hero_rank", "-first_published_at", "title")
        )

    def _search_external_content_sqlite(
        self, queryset: QuerySet, query: str
    ) -> QuerySet:
        return (
            queryset.filter(
                Q(title__icontains=query)
                | Q(summary__icontains=query)
                | Q(url__icontains=query)
                | Q(source__name__icontains=query)
                | Q(tags__slug__icontains=query)
                | Q(tags__name__icontains=query)
            )
            .distinct()
            .order_by(
                "-updated_at", "-created_at", "-published_at", "-last_seen_at", "title"
            )
        )

    def _search_external_content_postgres(
        self, queryset: QuerySet, query: str
    ) -> QuerySet:
        search_vector = (
            SearchVector("title", weight="A", config=SEARCH_CONFIG)
            + SearchVector("summary", weight="B", config=SEARCH_CONFIG)
            + SearchVector("url", weight="C", config=SEARCH_CONFIG)
            + SearchVector("source__name", weight="D", config=SEARCH_CONFIG)
            + SearchVector("tags__slug", weight="D", config=SEARCH_CONFIG)
            + SearchVector("tags__name", weight="D", config=SEARCH_CONFIG)
        )
        search_query = SearchQuery(query, search_type="websearch", config=SEARCH_CONFIG)
        return (
            queryset.annotate(
                external_rank=SearchRank(
                    search_vector, search_query, weights=SEARCH_WEIGHTS
                ),
            )
            .filter(external_rank__gt=0)
            .order_by(
                "-external_rank",
                "-updated_at",
                "-created_at",
                "-published_at",
                "-last_seen_at",
                "title",
            )
            .distinct()
        )

    def _apply_filters(self, queryset: QuerySet, filters: dict[str, Any]) -> QuerySet:
        if filters.get("live", True):
            queryset = queryset.live()
        if filters.get("public", True):
            queryset = queryset.public()

        site_or_root = filters.get("site")
        if site_or_root:
            root_page = (
                site_or_root.root_page
                if isinstance(site_or_root, Site)
                else site_or_root
            )
            queryset = queryset.descendant_of(
                root_page, inclusive=bool(filters.get("include_root", False))
            )

        exclude_ids = filters.get("exclude_ids")
        if exclude_ids:
            queryset = queryset.exclude(pk__in=exclude_ids)

        return queryset

    def _external_content_queryset(self, filters: dict[str, Any]) -> QuerySet:
        queryset = ExternalContentItem.objects.filter(hidden=False).select_related(
            "source", "source__settings__site"
        )
        site = filters.get("site")
        if isinstance(site, Site):
            queryset = queryset.filter(
                Q(source__settings__site=site) | Q(source__isnull=True)
            )
        return queryset.prefetch_related("tags")

    def _is_postgres(self, db_alias: str) -> bool:
        return connections[db_alias].vendor == "postgresql"

    def _section_cards(self, section_page: SectionPage) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for block in section_page.rows:
            if block.block_type != "row":
                continue
            for card in block.value.get("cards", []):
                cards.append(
                    {
                        "title": card.get("title"),
                        "text": card.get("text"),
                        "link_text": card.get("link_text"),
                        "link_url": card.get("link_url"),
                        "tags": card.get("tags", []),
                    }
                )
        return cards

    def _site_root_page(self, filters: dict[str, Any]) -> Page | None:
        site_or_root = filters.get("site")
        if isinstance(site_or_root, Site):
            return site_or_root.root_page
        if isinstance(site_or_root, Page):
            return site_or_root
        return None

    def _page_breadcrumbs(
        self,
        page: Page,
        *,
        request,
        site_root: Page | None = None,
        include_page: bool = False,
    ) -> list[dict[str, str]]:
        breadcrumbs: list[dict[str, str]] = []
        for ancestor in page.get_ancestors(inclusive=include_page).specific():
            if site_root and not ancestor.path.startswith(site_root.path):
                continue
            if not include_page and ancestor.pk == page.pk:
                continue

            url = ancestor.get_url(request=request) or ancestor.url or "#"
            breadcrumbs.append(
                {
                    "title": ancestor.title,
                    "url": url,
                }
            )
        return breadcrumbs

    def _page_url(self, page: Page, request) -> str:
        url = page.get_url(request=request)
        if url:
            return url
        return page.url or "#"

    def _coalesce_datetime(self, *values: Any) -> datetime | None:
        for value in values:
            if isinstance(value, datetime):
                return value
        return None

    def _page_last_updated(self, page: Page) -> datetime | None:
        return self._coalesce_datetime(
            getattr(page, "latest_revision_created_at", None),
            getattr(page, "last_published_at", None),
            getattr(page, "first_published_at", None),
        )

    def _external_content_last_updated(
        self, item: ExternalContentItem
    ) -> datetime | None:
        return self._coalesce_datetime(
            getattr(item, "updated_at", None),
            getattr(item, "created_at", None),
            getattr(item, "published_at", None),
            getattr(item, "last_seen_at", None),
        )

    def _external_recency_boost(self, item: ExternalContentItem) -> float:
        last_updated = self._external_content_last_updated(item)
        if not isinstance(last_updated, datetime):
            return 0.0

        now = timezone.now()
        if timezone.is_naive(last_updated):
            now = now.replace(tzinfo=None)

        age_days = max((now - last_updated).days, 0)
        for max_age_days, boost in EXTERNAL_RECENCY_BOOST_BUCKETS:
            if age_days <= max_age_days:
                return boost
        return 0.0

    def _clean_text(self, value: Any) -> str:
        if not value:
            return ""
        return " ".join(strip_tags(str(value)).split())

    def _tag_text(self, tag: Any) -> str:
        if not tag:
            return ""

        key = self._clean_text(getattr(tag, "slug", "") or getattr(tag, "key", ""))
        value = self._clean_text(getattr(tag, "name", "") or getattr(tag, "value", ""))
        if key or value:
            return " ".join(part for part in (key, value) if part)

        return self._clean_text(tag)

    def _tag_label(self, tag: Any) -> str:
        if not tag:
            return ""

        value = self._clean_text(getattr(tag, "name", "") or getattr(tag, "value", ""))
        if value:
            return value

        key = self._clean_text(getattr(tag, "slug", "") or getattr(tag, "key", ""))
        if key:
            return key

        return self._clean_text(tag)

    def _unique_values(self, values: list[str]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean_value = self._clean_text(value)
            if not clean_value:
                continue
            normalised = clean_value.lower()
            if normalised in seen:
                continue
            seen.add(normalised)
            unique_values.append(clean_value)
        return unique_values

    def _page_tag_labels(self, page: Any) -> list[str]:
        tags_manager = getattr(page, "tags", None)
        if not tags_manager:
            return []

        labels: list[str] = []
        for tag in tags_manager.all():
            label = self._tag_label(tag)
            if label:
                labels.append(label)
        return self._unique_values(labels)

    def _tag_result_description(self, page: Any) -> str:
        tags_manager = getattr(page, "tags", None)
        if not tags_manager:
            return ""

        values: list[str] = []
        for tag in tags_manager.all():
            value = self._clean_text(getattr(tag, "name", ""))
            if value:
                values.append(value)
        if not values:
            return ""

        return f"Tagged: {', '.join(values)}"

    def _page_size(self, filters: dict[str, Any]) -> int:
        page_size = filters.get("page_size", DEFAULT_PAGE_SIZE)
        try:
            parsed_page_size = int(page_size)
        except (TypeError, ValueError):
            return DEFAULT_PAGE_SIZE
        return parsed_page_size if parsed_page_size > 0 else DEFAULT_PAGE_SIZE

    def _merge_results(self, results: list[SearchResultItem]) -> list[SearchResultItem]:
        unique_results: list[SearchResultItem] = []
        seen: set[tuple[str, str]] = set()

        for item in sorted(results, key=lambda item: (-item.score, item.title.lower())):
            key = (item.title.lower(), item.url)
            if key in seen:
                continue
            seen.add(key)
            unique_results.append(item)

        return unique_results

    def _text_relevance(
        self, query: str, weighted_values: tuple[tuple[Any, float], ...]
    ) -> float:
        query_lower = query.lower()
        terms = [term for term in query_lower.split() if term]
        score = 0.0

        for value, weight in weighted_values:
            text = self._clean_text(value).lower()
            if not text:
                continue
            if query_lower in text:
                score += 2.0 * weight
            for term in terms:
                if term in text:
                    score += 0.5 * weight
        return score


search_backend = SearchBackend()
