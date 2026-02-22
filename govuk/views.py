from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_http_methods
from wagtail.models import Site

from govuk.forms import FeedbackForm
from govuk.models import CustomiseSettings, EdDSAKeySettings, Feedback
from govuk.oidc import (
    ADMIN_OIDC_NEXT_URL_KEY,
    OIDC_ID_TOKEN_SESSION_KEY,
    build_oidc_login_url,
    build_oidc_logout_url,
    oidc_callback as allauth_oidc_callback,
)
from govuk.search_backend import search_backend


@login_required
def profile_view(request):
    return render(
        request,
        "accounts/profile.html",
        {"auth_id_token": request.session.get(OIDC_ID_TOKEN_SESSION_KEY)},
    )


def assets_alias_view(request, path):
    return staticfiles_serve(request, f"assets/{path}", insecure=True)


def _customise_settings_for_request(request) -> CustomiseSettings:
    site = Site.find_for_request(request)
    if site is None:
        raise Http404
    return CustomiseSettings.for_site(site)


def _eddsa_key_settings_for_request(request) -> EdDSAKeySettings:
    site = Site.find_for_request(request)
    if site is None:
        raise Http404
    return EdDSAKeySettings.for_site(site)


@require_http_methods(["GET"])
def custom_css_view(request):
    custom_css = _customise_settings_for_request(request).render_custom_css()
    if not custom_css:
        raise Http404

    return HttpResponse(custom_css, content_type="text/css; charset=utf-8")


@require_http_methods(["GET"])
def custom_js_view(request):
    custom_js = _customise_settings_for_request(request).render_custom_js()
    if not custom_js:
        raise Http404

    return HttpResponse(custom_js, content_type="application/javascript; charset=utf-8")


@require_http_methods(["GET"])
def jwks_view(request):
    jwks_keys = _eddsa_key_settings_for_request(request).build_jwks_keys()
    if not jwks_keys:
        raise Http404

    return JsonResponse({"keys": jwks_keys})


@require_http_methods(["GET"])
def search_view(request):
    query = (request.GET.get("query") or "").strip()
    page_number = request.GET.get("page", 1)
    site = Site.find_for_request(request)

    results = search_backend.search(
        query=query,
        filters={
            "request": request,
            "site": site,
            "live": True,
            "public": True,
        },
        page=page_number,
    )
    return render(
        request,
        "search/results.html",
        {
            "query": query,
            "results": results,
        },
    )


def _normalised_referrer(value: str | None) -> str:
    return (value or "").strip()[:500]


def _normalised_feedback_type(value: str | None) -> str | None:
    feedback_type = (value or "").strip()
    if not feedback_type:
        return None

    valid_feedback_types = {choice for choice, _ in Feedback.FeedbackType.choices}
    if feedback_type in valid_feedback_types:
        return feedback_type
    return None


def _user_display_name(user) -> str:
    full_name = user.get_full_name().strip()
    if full_name:
        return full_name
    username = getattr(user, "get_username", lambda: "")()
    return (username or "").strip()


def _is_mobile_user_agent(user_agent: str) -> bool:
    lowered = user_agent.lower()
    return any(
        token in lowered
        for token in (
            "android",
            "blackberry",
            "iphone",
            "ipad",
            "ipod",
            "mobile",
            "phone",
            "tablet",
            "windows phone",
        )
    )


def _browser_from_user_agent(user_agent: str) -> str:
    lowered = user_agent.lower()
    if "edg/" in lowered:
        return "Microsoft Edge"
    if "opr/" in lowered or "opera/" in lowered:
        return "Opera"
    if "chrome/" in lowered and "chromium/" not in lowered:
        return "Chrome"
    if "firefox/" in lowered:
        return "Firefox"
    if "safari/" in lowered and "chrome/" not in lowered:
        return "Safari"
    if "msie" in lowered or "trident/" in lowered:
        return "Internet Explorer"
    return "Unknown"


def _feedback_sign_in_url(
    request, referrer: str, feedback_type: str | None = None
) -> str:
    feedback_url = request.path
    feedback_query = {}
    if referrer:
        feedback_query["referrer"] = referrer
    if feedback_type:
        feedback_query["feedback_type"] = feedback_type
    if feedback_query:
        feedback_url = f"{feedback_url}?{urlencode(feedback_query)}"
    return f"{settings.LOGIN_URL}?{urlencode({'next': feedback_url})}"


@require_http_methods(["GET", "POST"])
def feedback_view(request):
    if not settings.FEATURE_FLAGS.get("FEEDBACK"):
        raise Http404

    inferred_referrer = _normalised_referrer(
        request.GET.get("referrer") or request.META.get("HTTP_REFERER")
    )
    inferred_feedback_type = _normalised_feedback_type(request.GET.get("feedback_type"))

    if not request.user.is_authenticated:
        return render(
            request,
            "feedback/form.html",
            {
                "form": None,
                "submitted": False,
                "sign_in_url": _feedback_sign_in_url(
                    request, inferred_referrer, inferred_feedback_type
                ),
            },
        )

    if request.method == "POST":
        form = FeedbackForm(request.POST)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.user = request.user
            feedback.name = _user_display_name(request.user)
            feedback.email = (request.user.email or "").strip()
            if not feedback.referrer:
                feedback.referrer = inferred_referrer
            user_agent = request.META.get("HTTP_USER_AGENT", "").strip()
            feedback.browser = _browser_from_user_agent(user_agent)
            feedback.is_mobile = _is_mobile_user_agent(user_agent)
            feedback.save()
            return redirect(f"{request.path}?submitted=1")
    else:
        initial = {"referrer": inferred_referrer}
        if inferred_feedback_type:
            initial["feedback_type"] = inferred_feedback_type
        form = FeedbackForm(initial=initial)

    return render(
        request,
        "feedback/form.html",
        {
            "form": form,
            "submitted": request.GET.get("submitted") == "1",
            "feedback_name": _user_display_name(request.user),
            "feedback_email": (request.user.email or "").strip(),
        },
    )


def oidc_login_redirect(request):
    next_url = request.GET.get("next") or request.session.get(ADMIN_OIDC_NEXT_URL_KEY)
    return redirect(build_oidc_login_url(next_url))


def oidc_callback(request, provider_id):
    return allauth_oidc_callback(request, provider_id)


@require_http_methods(["GET", "POST"])
def account_logout_redirect(request):
    django_logout(request)
    return redirect(build_oidc_logout_url())


@require_POST
def wagtail_logout_redirect(request):
    django_logout(request)
    return redirect("/")
