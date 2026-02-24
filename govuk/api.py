from urllib.parse import urljoin

from django.conf import settings
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

AUTH_QUERY_PARAMETERS = frozenset({"bearer"})


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


class WagtailPages(AuthenticatedAPIViewSetMixin, PagesAPIViewSet):
    permission_classes = [AllowAny]
    meta_fields = PagesAPIViewSet.meta_fields + [
        APIField("privacy", serializer=PagePrivacyField()),
    ]
    listing_default_fields = PagesAPIViewSet.listing_default_fields + ["privacy"]
    known_query_parameters = PagesAPIViewSet.known_query_parameters.union(
        AUTH_QUERY_PARAMETERS
    )

    def get_queryset(self):
        return super().get_queryset().prefetch_related("view_restrictions__groups")


class WagtailImages(AuthenticatedAPIViewSetMixin, ImagesAPIViewSet):
    known_query_parameters = ImagesAPIViewSet.known_query_parameters.union(
        AUTH_QUERY_PARAMETERS
    )


class WagtailDocuments(AuthenticatedAPIViewSetMixin, DocumentsAPIViewSet):
    known_query_parameters = DocumentsAPIViewSet.known_query_parameters.union(
        AUTH_QUERY_PARAMETERS
    )


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


class ExternalContentSourcesAPIView(AuthenticatedAPIViewSetMixin, generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = ExternalContentSourceSerializer
    pagination_class = ExternalContentPagination

    def get_queryset(self):
        queryset = ContentDiscoverySource.objects.all()

        raw_tag_filter = (self.request.query_params.get("tag") or "").strip().lower()
        if raw_tag_filter:
            queryset = queryset.filter(external_content_items__hidden=False)
            if raw_tag_filter.isdigit():
                queryset = queryset.filter(
                    external_content_items__tags__id=int(raw_tag_filter)
                )
            else:
                queryset = queryset.filter(
                    external_content_items__tags__slug__iexact=raw_tag_filter
                )

        raw_source_filter = (self.request.query_params.get("source") or "").strip()
        if raw_source_filter:
            if raw_source_filter.isdigit():
                queryset = queryset.filter(id=int(raw_source_filter))
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

        raw_tag_filter = (self.request.query_params.get("tag") or "").strip().lower()
        if raw_tag_filter:
            if raw_tag_filter.isdigit():
                queryset = queryset.filter(tags__id=int(raw_tag_filter))
            else:
                queryset = queryset.filter(tags__slug__iexact=raw_tag_filter)

        raw_source_filter = (self.request.query_params.get("source") or "").strip()
        if raw_source_filter:
            if raw_source_filter.isdigit():
                queryset = queryset.filter(source_id=int(raw_source_filter))
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
        {
            "endpoints": {
                **_build_wagtail_endpoint_links(request),
                "externalcontent": _build_external_content_endpoint_links(request),
            },
        }
    )


def api_externalcontent_root_view(request):
    return JsonResponse({"endpoints": _build_external_content_endpoint_links(request)})
