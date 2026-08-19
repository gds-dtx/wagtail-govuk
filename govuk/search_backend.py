from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.paginator import Page as PaginatorPage
from django.core.paginator import Paginator
from django.db import connections
from django.db.models import Q, QuerySet, TextField, prefetch_related_objects
from django.db.models.functions import Cast
from django.utils.html import strip_tags
from django.utils.text import slugify
from django.utils import timezone
from wagtail.models import Page, PageViewRestriction, Site

from govuk.models import ContentPage, ExternalContentItem, SectionPage
from govuk.utils import normalised_text

DEFAULT_PAGE_SIZE = 15
SEARCH_CONFIG = "english"
SEARCH_WEIGHTS = [0.1, 0.2, 0.4, 1.0]
PAGE_TAG_TEXT_WEIGHT = 0.75
CARD_TAG_TEXT_WEIGHT = 1.0
TAG_RESULT_WEIGHT = 1.2
EXTERNAL_SOURCE_TEXT_WEIGHT = 0.4
EXTERNAL_TAG_TEXT_WEIGHT = 0.6
THIS_SITE_SOURCE_FILTER = "__this_site__"
INTERNAL_RESULT_BOOST = 4.0
RECENCY_BOOST_BUCKETS: tuple[tuple[int, float], ...] = (
    (7, 4.0),
    (30, 2.5),
    (90, 1.5),
    (180, 0.75),
    (365, 0.3),
)


@dataclass(slots=True)
class SearchResultItem:
    title: str
    search_description: str
    url: str
    score: float = 0.0
    breadcrumbs: list[dict[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    tag_keys: list[str] = field(default_factory=list)
    is_external: bool = False
    source_name: str = ""
    source_id: int | None = None
    last_updated: datetime | None = None


class SearchBackend:
    def search(
        self, query: str, filters: dict[str, Any] | None = None, page: int | str = 1
    ) -> PaginatorPage:
        filters = filters or {}
        clean_query = (query or "").strip()
        selected_tag_key = self._normalised_tag_filter(filters.get("tag"))
        selected_source_key = self._normalised_source_filter(filters.get("source"))

        if not clean_query:
            paginator = Paginator([], self._page_size(filters))
            page_obj = paginator.get_page(page)
            self._attach_filter_context(
                page_obj,
                available_tags=[],
                available_sources=[],
                selected_tag=None,
                selected_source_id="",
                selected_source_label="",
            )
            return page_obj

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

        available_tags = self._available_tags(combined_results)
        selected_tag = next(
            (tag for tag in available_tags if tag["key"] == selected_tag_key),
            None,
        )
        if selected_tag is None:
            selected_tag_key = ""

        available_sources = self._available_sources(
            combined_results,
            selected_tag_key=selected_tag_key,
            this_site_source={
                "id": THIS_SITE_SOURCE_FILTER,
                "label": self._this_site_source_label(filters),
            },
        )
        selected_source = None
        if selected_source_key:
            selected_source = next(
                (
                    source
                    for source in available_sources
                    if source["id"] == selected_source_key
                ),
                None,
            )
            if selected_source is None:
                selected_source_key = ""

        filtered_results = self._filter_results(
            combined_results,
            selected_tag_key=selected_tag_key,
            selected_source_key=selected_source_key,
        )
        paginator = Paginator(filtered_results, self._page_size(filters))
        page_obj = paginator.get_page(page)
        self._attach_filter_context(
            page_obj,
            available_tags=available_tags,
            available_sources=available_sources,
            selected_tag=selected_tag,
            selected_source_id=selected_source_key,
            selected_source_label=selected_source["label"] if selected_source else "",
        )
        return page_obj

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
            title = normalised_text(page.title)
            display_title = self._page_result_title(specific_page)
            description = self._page_search_description(specific_page)
            tag_items = self._page_tag_items(specific_page)
            tag_labels = [tag["value"] for tag in tag_items]
            tag_keys = [tag["key"] for tag in tag_items]
            tags_text = normalised_text(" ".join(tag_labels))
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
                    title=display_title or title,
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
                    tag_keys=tag_keys,
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
            section_title = self._page_result_title(section_page)

            for card in self._section_cards(section_page):
                title = normalised_text(card.get("title"))
                text = normalised_text(card.get("text"))
                link_text = normalised_text(card.get("link_text"))
                link_url = (card.get("link_url") or "").strip()
                card_tag_text: list[str] = []
                card_tag_items: list[dict[str, str]] = []
                for tag in card.get("tags", []):
                    tag_text = self._tag_text(tag)
                    if tag_text:
                        card_tag_text.append(tag_text)
                    tag_item = self._tag_item(tag)
                    if tag_item:
                        card_tag_items.append(tag_item)
                tags_text = normalised_text(" ".join(card_tag_text))
                result_tag_items = self._unique_tag_items(
                    card_tag_items + self._page_tag_items(section_page)
                )
                result_tags = [tag["value"] for tag in result_tag_items]
                result_tag_keys = [tag["key"] for tag in result_tag_items]

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
                        title=title or section_title,
                        search_description=(
                            text
                            or self._page_search_description(section_page)
                            or f"Card in {section_title}"
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
                        tag_keys=result_tag_keys,
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
                tag_items = self._page_tag_items(page)
                tag_labels = [tag["value"] for tag in tag_items]
                tag_keys = [tag["key"] for tag in tag_items]
                tags_text = normalised_text(" ".join(tag_labels))
                score = self._text_relevance(query, ((tags_text, TAG_RESULT_WEIGHT),))
                if score <= 0:
                    continue

                description = self._page_search_description(
                    page
                ) or self._tag_result_description(page)
                results.append(
                    SearchResultItem(
                        title=self._page_result_title(page),
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
                        tag_keys=tag_keys,
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
                hero_title = normalised_text(getattr(page, "hero_title", ""))
                hero_intro = normalised_text(getattr(page, "hero_intro", ""))
                tag_items = self._page_tag_items(page)
                tag_labels = [tag["value"] for tag in tag_items]
                tag_keys = [tag["key"] for tag in tag_items]
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
                        title=self._page_result_title(page),
                        search_description=self._page_search_description(page),
                        url=self._page_url(page, request),
                        score=score,
                        breadcrumbs=self._page_breadcrumbs(
                            page,
                            request=request,
                            site_root=site_root,
                            include_page=False,
                        ),
                        tags=tag_labels,
                        tag_keys=tag_keys,
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
            tag_items = self._page_tag_items(item)
            tag_labels = [tag["value"] for tag in tag_items]
            tag_keys = [tag["key"] for tag in tag_items]
            tags_text = normalised_text(" ".join(tag_labels))
            source_name = normalised_text(getattr(item.source, "name", ""))
            item_rank = float(getattr(item, "external_rank", 0.0) or 0.0)
            score = (
                item_rank
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

            description = normalised_text(item.summary)
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
                    tag_keys=tag_keys,
                    is_external=True,
                    source_name=source_name,
                    source_id=item.source_id,
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
        queryset = self._viewable_by_reader(queryset, filters)

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

    def _viewable_by_reader(
        self, queryset: QuerySet, filters: dict[str, Any]
    ) -> QuerySet:
        """The pages this reader would actually be served.

        ``public()`` answers the whole question only while nobody is signed in:
        it drops every restricted page, whoever is asking. Signed in, the
        search was asking nothing at all, so a page held back for one group, or
        kept behind a shared password, was listed by title and description to
        anybody holding an account -- while the page itself went on answering
        that same reader with a redirect or the password form. A restriction is
        there to keep something quiet before it is announced, and a search
        result names it and summarises it.

        So each restriction is put the question Wagtail puts at serve time, and
        only those it refuses are excluded. What the search lists is then what
        the site would serve: a group's own members still find their page, a
        password already entered this session no longer hides the page it was
        entered for, and a superuser meets the password form here exactly as
        they would meet it on the page.
        """
        if filters.get("public", True):
            return queryset.public()

        request = filters.get("request")
        if request is None:
            # A restriction is a question about a reader, and without a request
            # there is no reader to ask it about. Nothing extra is let through.
            return queryset.public()

        refused = self._refused_restrictions_q(request)
        return queryset.exclude(refused) if refused else queryset

    def _refused_restrictions_q(self, request) -> Q:
        """Pages under a restriction this request fails, as one Q.

        Held on the request, as the ancestors above are: a single search runs
        several querysets and the same restrictions decide all of them, while
        asking a group restriction costs a query every time it is asked.
        """
        cached = getattr(request, "_govuk_search_refused_restrictions", None)
        if cached is not None:
            return cached

        restrictions = list(
            PageViewRestriction.objects.select_related("page").prefetch_related("groups")
        )
        self._prime_reader_groups(request, restrictions)

        refused = Q()
        for restriction in restrictions:
            if restriction.accept_request(request):
                continue
            # A restriction covers the page it is set on and everything below
            # it, which is how Wagtail decides what it applies to.
            page = restriction.page
            refused |= Q(path__startswith=page.path, depth__gte=page.depth)

        request._govuk_search_refused_restrictions = refused
        return refused

    @staticmethod
    def _prime_reader_groups(request, restrictions: list[PageViewRestriction]) -> None:
        """Fetch the reader's groups once, before they are asked for repeatedly.

        ``accept_request`` reads ``request.user.groups.all()`` itself, and a
        related manager builds a fresh queryset every call, so a site with a
        dozen group restrictions asked the same question a dozen times. Filling
        the cache Django keeps on the user answers all of them from the first.
        """
        user = getattr(request, "user", None)
        if getattr(user, "pk", None) is None or user.is_superuser:
            # A superuser passes a group restriction without being asked, and
            # an anonymous or absent user has no groups to fetch.
            return
        if not any(
            restriction.restriction_type == PageViewRestriction.GROUPS
            for restriction in restrictions
        ):
            return

        prefetch_related_objects([user], "groups")

    def _external_content_queryset(self, filters: dict[str, Any]) -> QuerySet:
        queryset = ExternalContentItem.objects.filter(hidden=False).select_related(
            "source", "source__settings__site"
        )
        if filters.get("public", True):
            queryset = queryset.filter(private=False)
        site = filters.get("site")
        if isinstance(site, Site):
            queryset = queryset.filter(
                Q(source__settings__site=site) | Q(source__isnull=True)
            )
        return queryset.prefetch_related("tags")

    def _is_postgres(self, db_alias: str) -> bool:
        return connections[db_alias].vendor == "postgresql"

    def _section_card_link_title(self, link_value: Any) -> str:
        if not link_value:
            return ""
        if hasattr(link_value, "get"):
            return normalised_text(link_value.get("title"))
        return normalised_text(getattr(link_value, "title", ""))

    def _section_card_link_url(self, link_value: Any) -> str:
        if not link_value:
            return ""

        url = getattr(link_value, "url", None)
        if isinstance(url, str):
            return url.strip()

        if not hasattr(link_value, "get"):
            return ""

        external_url = (link_value.get("external_url") or "").strip()
        if external_url:
            return external_url

        page = link_value.get("page")
        if page is None:
            return ""

        return (getattr(page, "url", "") or "").strip()

    def _section_cards(self, section_page: SectionPage) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for block in section_page.rows:
            if block.block_type != "row":
                continue
            for card in block.value.get("cards", []):
                link_value = card.get("link")
                link_text = self._section_card_link_title(link_value)
                link_url = self._section_card_link_url(link_value)
                cards.append(
                    {
                        "title": card.get("title"),
                        "text": card.get("text"),
                        "link_text": link_text or card.get("link_text"),
                        "link_url": link_url or card.get("link_url"),
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

    def _page_search_description(self, page: Any) -> str:
        hero_intro = normalised_text(getattr(page, "hero_intro", ""))
        if hero_intro:
            return hero_intro
        return normalised_text(getattr(page, "search_description", ""))

    def _page_result_title(self, page: Any) -> str:
        hero_title = normalised_text(getattr(page, "hero_title", ""))
        if hero_title:
            return hero_title
        return normalised_text(getattr(page, "title", ""))

    def _external_content_last_updated(
        self, item: ExternalContentItem
    ) -> datetime | None:
        return self._coalesce_datetime(
            getattr(item, "updated_at", None),
            getattr(item, "created_at", None),
            getattr(item, "published_at", None),
            getattr(item, "last_seen_at", None),
        )

    def _recency_boost(self, last_updated: datetime | None) -> float:
        if not isinstance(last_updated, datetime):
            return 0.0

        now = timezone.now()
        if timezone.is_naive(last_updated):
            now = now.replace(tzinfo=None)

        age_days = max((now - last_updated).days, 0)
        for max_age_days, boost in RECENCY_BOOST_BUCKETS:
            if age_days <= max_age_days:
                return boost
        return 0.0

    def _ranking_score(self, item: SearchResultItem) -> float:
        score = float(item.score or 0.0) + self._recency_boost(item.last_updated)
        if not item.is_external:
            score += INTERNAL_RESULT_BOOST
        return score

    def _clean_text(self, value: Any) -> str:
        if not value:
            return ""
        return " ".join(strip_tags(str(value)).split())

    def _tag_text(self, tag: Any) -> str:
        if not tag:
            return ""

        key = normalised_text(getattr(tag, "slug", "") or getattr(tag, "key", ""))
        value = normalised_text(getattr(tag, "name", "") or getattr(tag, "value", ""))
        if key or value:
            return " ".join(part for part in (key, value) if part)

        return normalised_text(tag)

    def _tag_label(self, tag: Any) -> str:
        if not tag:
            return ""

        value = normalised_text(getattr(tag, "name", "") or getattr(tag, "value", ""))
        if value:
            return value

        key = normalised_text(getattr(tag, "slug", "") or getattr(tag, "key", ""))
        if key:
            return key

        return normalised_text(tag)

    def _tag_key(self, tag: Any) -> str:
        if not tag:
            return ""

        key = normalised_text(getattr(tag, "slug", "") or getattr(tag, "key", ""))
        if key:
            return key.lower()

        value = normalised_text(getattr(tag, "name", "") or getattr(tag, "value", ""))
        if value:
            slugified_value = slugify(value)
            if slugified_value:
                return slugified_value.lower()
            return value.lower()

        text_value = normalised_text(tag)
        slugified_text = slugify(text_value)
        if slugified_text:
            return slugified_text.lower()
        return text_value.lower()

    def _tag_item(self, tag: Any) -> dict[str, str] | None:
        tag_key = self._tag_key(tag)
        tag_label = self._tag_label(tag)
        if not tag_label and tag_key:
            tag_label = tag_key
        if not tag_key and tag_label:
            slugified_label = slugify(tag_label)
            if slugified_label:
                tag_key = slugified_label.lower()
            else:
                tag_key = tag_label.lower()
        if not tag_key or not tag_label:
            return None
        return {"key": tag_key, "value": tag_label}

    def _unique_values(self, values: list[str]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean_value = normalised_text(value)
            if not clean_value:
                continue
            normalised = clean_value.lower()
            if normalised in seen:
                continue
            seen.add(normalised)
            unique_values.append(clean_value)
        return unique_values

    def _unique_tag_items(self, items: list[dict[str, str]]) -> list[dict[str, str]]:
        unique_items: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in items:
            key = normalised_text(item.get("key", "")).lower()
            value = normalised_text(item.get("value", ""))
            if not key or not value:
                continue
            if key in seen:
                continue
            seen.add(key)
            unique_items.append({"key": key, "value": value})
        return unique_items

    def _page_tag_items(self, page: Any) -> list[dict[str, str]]:
        tags_manager = getattr(page, "tags", None)
        if not tags_manager:
            return []

        items: list[dict[str, str]] = []
        for tag in tags_manager.all():
            item = self._tag_item(tag)
            if item:
                items.append(item)
        return self._unique_tag_items(items)

    def _page_tag_labels(self, page: Any) -> list[str]:
        return [tag["value"] for tag in self._page_tag_items(page)]

    def _tag_result_description(self, page: Any) -> str:
        tags_manager = getattr(page, "tags", None)
        if not tags_manager:
            return ""

        values: list[str] = []
        for tag in tags_manager.all():
            value = normalised_text(getattr(tag, "name", ""))
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

        for item in results:
            item.score = self._ranking_score(item)

        for item in sorted(results, key=lambda item: (-item.score, item.title.lower())):
            key = (item.title.lower(), item.url)
            if key in seen:
                continue
            seen.add(key)
            unique_results.append(item)

        return unique_results

    def _normalised_tag_filter(self, value: Any) -> str:
        return normalised_text(value).lower()

    def _normalised_source_filter(self, value: Any) -> str:
        source_value = normalised_text(value)
        if not source_value:
            return ""
        if source_value == THIS_SITE_SOURCE_FILTER:
            return THIS_SITE_SOURCE_FILTER
        if not source_value.isdigit():
            return ""

        parsed_source_id = int(source_value)
        if parsed_source_id <= 0:
            return ""
        return str(parsed_source_id)

    def _this_site_source_label(self, filters: dict[str, Any]) -> str:
        site = filters.get("site")
        if isinstance(site, Site):
            site_name = normalised_text(site.site_name)
            if site_name:
                return f"{site_name} (this site)"
        return "This site"

    def _available_tags(self, results: list[SearchResultItem]) -> list[dict[str, str]]:
        tag_items: list[dict[str, str]] = []
        for result in results:
            for key, value in zip(result.tag_keys, result.tags):
                tag_items.append({"key": key, "value": value})

        unique_items = self._unique_tag_items(tag_items)
        return sorted(unique_items, key=lambda item: item["value"].lower())

    def _available_sources(
        self,
        results: list[SearchResultItem],
        *,
        selected_tag_key: str = "",
        this_site_source: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        source_results = results
        if selected_tag_key:
            source_results = [
                result for result in results if selected_tag_key in result.tag_keys
            ]

        sources_by_id: dict[int, str] = {}
        for result in source_results:
            if result.source_id is None:
                continue
            source_label = normalised_text(result.source_name)
            if not source_label:
                continue
            sources_by_id[result.source_id] = source_label

        available_sources = [
            {"id": str(source_id), "label": source_label}
            for source_id, source_label in sources_by_id.items()
        ]
        available_sources.sort(key=lambda source: source["label"].lower())
        if this_site_source:
            available_sources = [this_site_source] + available_sources
        return available_sources

    def _filter_results(
        self,
        results: list[SearchResultItem],
        *,
        selected_tag_key: str = "",
        selected_source_key: str = "",
    ) -> list[SearchResultItem]:
        filtered_results = results
        if selected_tag_key:
            filtered_results = [
                result
                for result in filtered_results
                if selected_tag_key in result.tag_keys
            ]
        if selected_source_key == THIS_SITE_SOURCE_FILTER:
            filtered_results = [
                result for result in filtered_results if not result.is_external
            ]
        elif selected_source_key:
            selected_source_id = int(selected_source_key)
            filtered_results = [
                result
                for result in filtered_results
                if result.is_external and result.source_id == selected_source_id
            ]
        return filtered_results

    def _attach_filter_context(
        self,
        page_obj: PaginatorPage,
        *,
        available_tags: list[dict[str, str]],
        available_sources: list[dict[str, str]],
        selected_tag: dict[str, str] | None,
        selected_source_id: str,
        selected_source_label: str,
    ) -> None:
        page_obj.available_tags = available_tags
        page_obj.available_sources = available_sources
        page_obj.selected_tag = selected_tag
        page_obj.selected_source_id = selected_source_id
        page_obj.selected_source_label = selected_source_label

    def _text_relevance(
        self, query: str, weighted_values: tuple[tuple[Any, float], ...]
    ) -> float:
        query_lower = query.lower()
        terms = [term for term in query_lower.split() if term]
        score = 0.0

        for value, weight in weighted_values:
            text = normalised_text(value).lower()
            if not text:
                continue
            if query_lower in text:
                score += 2.0 * weight
            for term in terms:
                if term in text:
                    score += 0.5 * weight
        return score


search_backend = SearchBackend()
