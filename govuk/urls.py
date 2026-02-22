from django.conf import settings
from django.urls import include, path
from django.contrib import admin

from wagtail.admin import urls as wagtailadmin_urls
from wagtail import urls as wagtail_urls
from wagtail.documents import urls as wagtaildocs_urls

from allauth.account.decorators import secure_admin_login
from govuk.api import api_root_view, api_router, api_v2_root_view
from govuk.views import (
    account_logout_redirect,
    assets_alias_view,
    custom_css_view,
    custom_js_view,
    feedback_view,
    jwks_view,
    oidc_callback,
    oidc_login_redirect,
    profile_view,
    search_view,
    wagtail_logout_redirect,
)

admin.autodiscover()
admin.site.login = secure_admin_login(admin.site.login)

urlpatterns = [
    path("login/", oidc_login_redirect, name="account_login"),
    path("accounts/login/", oidc_login_redirect),
    path(
        f"accounts/{settings.SOCIALACCOUNT_OPENID_CONNECT_URL_PREFIX}/<str:provider_id>/login/callback/",
        oidc_callback,
        name="oidc_callback",
    ),
    path("_util/login/", oidc_login_redirect, name="wagtailcore_login"),
    path("admin/logout/", wagtail_logout_redirect, name="wagtailadmin_logout"),
    path("django-admin/", admin.site.urls),
    path("admin/", include(wagtailadmin_urls)),
    path("documents/", include(wagtaildocs_urls)),
    path("api/", api_root_view, name="api_root"),
    path("api/v2/", api_v2_root_view, name="api_v2_root"),
    path("api/v2/", api_router.urls),
    path("gen/custom.css", custom_css_view, name="govuk_custom_css"),
    path("gen/custom.js", custom_js_view, name="govuk_custom_js"),
    path(".well-known/jwks.json", jwks_view, name="govuk_jwks"),
    path("assets/<path:path>", assets_alias_view, name="assets_alias"),
    path("accounts/profile/", profile_view, name="account_profile"),
    path("accounts/logout/", account_logout_redirect, name="account_logout"),
    path("accounts/", include("allauth.urls")),
    path("search/", search_view, name="search"),
]

if settings.FEATURE_FLAGS.get("FEEDBACK"):
    urlpatterns += [
        path("feedback", feedback_view),
        path("feedback/", feedback_view, name="feedback"),
    ]


if settings.DEBUG:
    from django.conf.urls.static import static
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    # Serve static and media files from development server
    urlpatterns += staticfiles_urlpatterns()
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
