from django.conf import settings
from django.urls import include, path
from django.contrib import admin

from wagtail.admin import urls as wagtailadmin_urls
from wagtail import urls as wagtail_urls
from wagtail.documents import urls as wagtaildocs_urls


from allauth.account.decorators import secure_admin_login
from govuk.api import (
    ExternalContentItemsAPIView,
    ExternalContentSourcesAPIView,
    api_externalcontent_root_view,
    api_health_view,
    api_root_view,
    api_router,
)
from govuk.views import (
    account_logout_redirect,
    assets_alias_view,
    custom_css_view,
    feedback_view,
    framework_csv_view,
    jwks_view,
    oidc_callback,
    oidc_login,
    oidc_login_redirect,
    profile_view,
    search_view,
    wagtail_logout_redirect,
)
from govuk.view_robots import robots_txt_view
from govuk.view_securitytxt import security_txt_view

admin.autodiscover()
admin.site.login = secure_admin_login(admin.site.login)

# Branded 500s: Django's default handler renders without the request, which
# would strip the header and contact details off the one page that most needs
# to look like the service.
handler500 = "govuk.views.server_error"

urlpatterns = [
    path("login/", oidc_login_redirect, name="account_login"),
    path("accounts/login/", oidc_login_redirect),
    path("accounts/oidc/<str:provider_id>/login/", oidc_login),
    path(
        "accounts/oidc/<str:provider_id>/login/callback/",
        oidc_callback,
        name="oidc_callback",
    ),
    path("_util/login/", oidc_login_redirect, name="wagtailcore_login"),
    path("admin/logout/", wagtail_logout_redirect, name="wagtailadmin_logout"),
    path("django-admin/", admin.site.urls),
    path("admin/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("api/", api_root_view, name="api_root"),
    path("api/health/", api_health_view, name="api_health"),
    path(
        "api/externalcontent/",
        api_externalcontent_root_view,
        name="api_externalcontent_root",
    ),
    path(
        "api/externalcontent/sources/",
        ExternalContentSourcesAPIView.as_view(),
        name="api_externalcontent_sources",
    ),
    path(
        "api/externalcontent/items/",
        ExternalContentItemsAPIView.as_view(),
        name="api_externalcontent_items",
    ),
    path("api/", api_router.urls),
    path("gen/custom.css", custom_css_view, name="govuk_custom_css"),
    path(".well-known/jwks.json", jwks_view, name="govuk_jwks"),
    path(".well-known/security.txt", security_txt_view, name="govuk_security_txt"),
    path("assets/<path:path>", assets_alias_view, name="assets_alias"),
    path("accounts/profile/", profile_view, name="account_profile"),
    path("accounts/logout/", account_logout_redirect, name="account_logout"),
    path("accounts/", include("allauth.urls")),
    path("search/", search_view, name="search"),
    # Before the Wagtail catch-all: /download/ itself is a page, and these are
    # the files it links to.
    path(
        "download/<slug:name>.csv",
        framework_csv_view,
        name="govuk_framework_csv",
    ),
    path("robots.txt", robots_txt_view, name="govuk_robots_txt"),
]

if settings.FEATURE_FLAGS.get("FEEDBACK"):
    urlpatterns += [
        path("feedback", feedback_view),
        path("feedback/", feedback_view, name="feedback"),
    ]


from django.conf.urls.static import static

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

urlpatterns = urlpatterns + [
    # For anything not caught by a more specific rule above, hand over to
    # Wagtail's page serving mechanism. This should be the last pattern in
    # the list:
    path("", include(wagtail_urls)),
    # Alternatively, if you want Wagtail pages to be served from a subpath
    # of your site, rather than the site root:
    #    path("pages/", include(wagtail_urls)),
]
