from urllib.parse import urljoin

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.urls import reverse
from rest_framework import generics, serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from wagtail.api.conf import APIField
from wagtail.api.v2.router import WagtailAPIRouter
from wagtail.api.v2.views import PagesAPIViewSet
from wagtail.documents.api.v2.views import DocumentsAPIViewSet
from wagtail.images.api.v2.views import ImagesAPIViewSet

from govuk.authentication import InternalAccessJWTAuthentication
from govuk.models import ContentDiscoverySource, ExternalContentItem, GovukTag
from govuk.utils import normalised_text, row_id_from_text

DEFAULT_API_REPOSITORY_URL = "https://github.com/govuk-digital-backbone/wagtail-govuk"


def _get_api_version():
    return getattr(settings, "VERSION", "unknown")


def _get_common_api_meta():
    return {
        "repository_url": getattr(
            settings, "API_REPOSITORY_URL", DEFAULT_API_REPOSITORY_URL
        ),
        "version": _get_api_version(),
    }


def _add_common_meta(response_data):
    if not isinstance(response_data, dict):
        return response_data

    existing_meta = response_data.get("meta")
    if isinstance(existing_meta, dict):
        response_data["meta"] = {**existing_meta, **_get_common_api_meta()}
    else:
        response_data["meta"] = _get_common_api_meta()
    return response_data


def _check_database_health():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return False
    return True


class PagePrivacyField(serializers.Field):
    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, page):
        restrictions = []
        for restriction in page.view_restrictions.all():
            restriction_data = {
                "id": restriction.id,
                "type": restriction.restriction_type,
            }
            if restriction.restriction_type == restriction.GROUPS:
                restriction_data["groups"] = [
                    {"id": group.id, "name": group.name}
                    for group in restriction.groups.all()
                ]
            restrictions.append(restriction_data)

        return {
            "restricted": bool(restrictions),
            "restrictions": restrictions,
        }


class AuthenticatedAPIViewSetMixin:
    authentication_classes = [InternalAccessJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if hasattr(response, "data"):
            _add_common_meta(response.data)
        return response


class PageTitleField(serializers.Field):
    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, page):
        page_specific = getattr(page, "specific", page)
        for candidate in (page_specific, page):
            if hero_title := str(getattr(candidate, "hero_title", "") or ""):
                if norm := normalised_text(hero_title):
                    return norm
        for candidate in (page_specific, page):
            if title := str(getattr(candidate, "title", "") or ""):
                if norm := normalised_text(title):
                    return norm
        return ""


class PageDescriptionField(serializers.Field):
    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, page):
        page_specific = getattr(page, "specific", page)
        for candidate in (page_specific, page):
            if hero_intro := str(getattr(candidate, "hero_intro", "") or ""):
                if norm := normalised_text(hero_intro):
                    return norm
        for candidate in (page_specific, page):
            if search_desc := str(getattr(candidate, "search_description", "") or ""):
                if norm := normalised_text(search_desc):
                    return norm

        return None


class PageTagSlugsField(serializers.Field):
    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, page):
        merged_slugs: set[str] = set()
        page_specific = getattr(page, "specific", page)

        for candidate in (page, page_specific):
            tags_manager = getattr(candidate, "tags", None)
            if tags_manager:
                merged_slugs.update(
                    slug
                    for slug in tags_manager.all().values_list("slug", flat=True)
                    if slug
                )

            tagged_items_manager = getattr(candidate, "tagged_items", None)
            if tagged_items_manager:
                merged_slugs.update(
                    slug
                    for slug in tagged_items_manager.all().values_list(
                        "tag__slug", flat=True
                    )
                    if slug
                )

        return sorted(merged_slugs)


class WagtailPages(AuthenticatedAPIViewSetMixin, PagesAPIViewSet):
    permission_classes = [AllowAny]
    body_fields = PagesAPIViewSet.body_fields + [
        APIField("title", serializer=PageTitleField()),
        APIField("description", serializer=PageDescriptionField()),
        APIField("tags", serializer=PageTagSlugsField()),
    ]
    meta_fields = PagesAPIViewSet.meta_fields + [
        APIField("privacy", serializer=PagePrivacyField()),
    ]
    listing_default_fields = PagesAPIViewSet.listing_default_fields + [
        "privacy",
        "description",
        "tags",
    ]

    def get_queryset(self):
        return super().get_queryset().prefetch_related("view_restrictions__groups")


class WagtailImages(AuthenticatedAPIViewSetMixin, ImagesAPIViewSet):
    pass


class WagtailDocuments(AuthenticatedAPIViewSetMixin, DocumentsAPIViewSet):
    pass


class GovukTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = GovukTag
        fields = ["id", "slug", "name"]


class ExternalContentSourceSummarySerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    class Meta:
        model = ContentDiscoverySource
        fields = ["id", "name", "url", "label"]

    def get_label(self, obj):
        return (obj.name or obj.url or "").strip()


class ExternalContentSourceSerializer(ExternalContentSourceSummarySerializer):
    item_count = serializers.IntegerField(read_only=True)

    class Meta(ExternalContentSourceSummarySerializer.Meta):
        fields = ExternalContentSourceSummarySerializer.Meta.fields + ["item_count"]


class ExternalContentItemSerializer(serializers.ModelSerializer):
    source = ExternalContentSourceSummarySerializer(read_only=True)
    tags = GovukTagSerializer(many=True, read_only=True)

    class Meta:
        model = ExternalContentItem
        fields = [
            "id",
            "key",
            "source",
            "url",
            "title",
            "summary",
            "published_at",
            "created_at",
            "updated_at",
            "first_seen_at",
            "last_seen_at",
            "tags",
            "metadata",
        ]


class ExternalContentPagination(PageNumberPagination):
    page_size = 15
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        page_size = self.get_page_size(self.request) or self.page.paginator.per_page
        return Response(
            {
                "meta": {
                    "total_count": self.page.paginator.count,
                    "page": self.page.number,
                    "per_page": page_size,
                    "total_pages": self.page.paginator.num_pages,
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
                "items": data,
            }
        )


def _filter_parameter(request, name: str) -> str:
    """A filter from the query string, in the form the database can be asked for.

    A NUL is dropped: PostgreSQL refuses a string literal carrying one, so
    "?tag=%00" was a 500 rather than a filter matching nothing. SQLite takes
    it, which is why the tests and CI were quiet about it.
    """
    return (request.query_params.get(name) or "").replace("\x00", "").strip()


class ExternalContentSourcesAPIView(AuthenticatedAPIViewSetMixin, generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = ExternalContentSourceSerializer
    pagination_class = ExternalContentPagination

    def get_queryset(self):
        queryset = ContentDiscoverySource.objects.all()

        raw_tag_filter = _filter_parameter(self.request, "tag").lower()
        if raw_tag_filter:
            queryset = queryset.filter(external_content_items__hidden=False)
            tag_id = row_id_from_text(raw_tag_filter)
            if tag_id is not None:
                queryset = queryset.filter(external_content_items__tags__id=tag_id)
            else:
                queryset = queryset.filter(
                    external_content_items__tags__slug__iexact=raw_tag_filter
                )

        raw_source_filter = _filter_parameter(self.request, "source")
        if raw_source_filter:
            source_id = row_id_from_text(raw_source_filter)
            if source_id is not None:
                queryset = queryset.filter(id=source_id)
            else:
                queryset = queryset.filter(
                    Q(name__iexact=raw_source_filter) | Q(url__iexact=raw_source_filter)
                )

        return (
            queryset.annotate(
                item_count=Count(
                    "external_content_items",
                    filter=Q(external_content_items__hidden=False),
                    distinct=True,
                )
            )
            .distinct()
            .order_by("sort_order", "id")
        )


class ExternalContentItemsAPIView(AuthenticatedAPIViewSetMixin, generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = ExternalContentItemSerializer
    pagination_class = ExternalContentPagination

    def get_queryset(self):
        queryset = ExternalContentItem.objects.filter(hidden=False)

        raw_tag_filter = _filter_parameter(self.request, "tag").lower()
        if raw_tag_filter:
            tag_id = row_id_from_text(raw_tag_filter)
            if tag_id is not None:
                queryset = queryset.filter(tags__id=tag_id)
            else:
                queryset = queryset.filter(tags__slug__iexact=raw_tag_filter)

        raw_source_filter = _filter_parameter(self.request, "source")
        if raw_source_filter:
            source_id = row_id_from_text(raw_source_filter)
            if source_id is not None:
                queryset = queryset.filter(source_id=source_id)
            else:
                queryset = queryset.filter(
                    Q(source__name__iexact=raw_source_filter)
                    | Q(source__url__iexact=raw_source_filter)
                )

        return (
            queryset.select_related("source")
            .prefetch_related("tags")
            .annotate(
                sort_updated=Coalesce(
                    "updated_at",
                    "created_at",
                    "published_at",
                    "last_seen_at",
                    "first_seen_at",
                )
            )
            .order_by("-sort_updated", "-id")
            .distinct()
        )


api_router = WagtailAPIRouter("wagtailapi")
api_router.register_endpoint("pages", WagtailPages)
api_router.register_endpoint("images", WagtailImages)
api_router.register_endpoint("documents", WagtailDocuments)


def _build_wagtail_endpoint_links(request):
    links = {}
    for endpoint_name in api_router._endpoints:
        listing_path = reverse(f"{api_router.url_namespace}:{endpoint_name}:listing")
        listing_url = _build_api_absolute_url(request, listing_path)
        links[endpoint_name] = {
            "listing": listing_url,
            "detail": f"{listing_url}{{id}}/",
        }
    return links


def _build_external_content_endpoint_links(request):
    return {
        "sources": _build_api_absolute_url(
            request, reverse("api_externalcontent_sources")
        ),
        "items": _build_api_absolute_url(request, reverse("api_externalcontent_items")),
    }


def _build_api_absolute_url(request, path):
    base_url = getattr(settings, "WAGTAILADMIN_BASE_URL", "")
    if base_url:
        return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
    return request.build_absolute_uri(path)


def api_root_view(request):
    return JsonResponse(
        _add_common_meta(
            {
                "endpoints": {
                    **_build_wagtail_endpoint_links(request),
                    "externalcontent": _build_external_content_endpoint_links(request),
                    "health": _build_api_absolute_url(request, reverse("api_health")),
                },
            }
        ),
        json_dumps_params={"indent": 2, "sort_keys": True},
    )


def api_externalcontent_root_view(request):
    return JsonResponse(
        _add_common_meta(
            {"endpoints": _build_external_content_endpoint_links(request)}
        ),
        json_dumps_params={"indent": 2, "sort_keys": True},
    )


def api_health_view(request):
    database_ok = _check_database_health()
    status = 200 if database_ok else 503
    return JsonResponse(
        _add_common_meta(
            {
                "health": {
                    "database": "ok" if database_ok else "error",
                }
            }
        ),
        status=status,
        json_dumps_params={"indent": 2, "sort_keys": True},
    )
