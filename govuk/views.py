import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from wagtail.models import Site

from govuk import capability_framework_csv
from govuk.forms import FeedbackForm
from govuk.models import CustomiseSettings, EdDSAKeySettings, Feedback
from govuk.oidc import (
    ADMIN_OIDC_NEXT_URL_KEY,
    OIDC_ID_TOKEN_SESSION_KEY,
    build_oidc_login_url,
    build_oidc_logout_url,
    oidc_callback as allauth_oidc_callback,
    oidc_login as allauth_oidc_login,
    safe_oidc_next_url,
)
from govuk.search_backend import search_backend

# Every "Is this page useful?" answer is one line on this logger, which the
# JSON formatter carries to CloudWatch alongside every other request. That is
# the server-side record CS32-3543 asks for: nothing is stored in the database
# and nothing identifies the visitor, so there is no personal data to look
# after -- only a count of yes and no against a path.
page_feedback_logger = logging.getLogger("govuk.page_feedback")

PAGE_FEEDBACK_ANSWERS = ("yes", "no")


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


@require_http_methods(["GET", "HEAD"])
def custom_css_view(request):
    custom_css = _customise_settings_for_request(request).render_custom_css()
    if not custom_css:
        raise Http404

    return HttpResponse(custom_css, content_type="text/css; charset=utf-8")


@require_http_methods(["GET", "HEAD"])
def jwks_view(request):
    jwks_keys = _eddsa_key_settings_for_request(request).build_jwks_keys()
    if not jwks_keys:
        raise Http404

    return JsonResponse(
        {"keys": jwks_keys}, json_dumps_params={"indent": 2, "sort_keys": True}
    )


def _page_numbers(results) -> list[dict]:
    """The numbers the pagination offers, eliding a long run of pages.

    The Design System's pagination shows the first and last page, the current
    one and its neighbours, and an ellipsis for the rest.
    """
    paginator = getattr(results, "paginator", None)
    if paginator is None or paginator.num_pages < 2:
        return []

    numbers = []
    for entry in paginator.get_elided_page_range(
        results.number, on_each_side=1, on_ends=1
    ):
        if isinstance(entry, int):
            numbers.append(
                {
                    "number": entry,
                    "is_current": entry == results.number,
                    "is_ellipsis": False,
                }
            )
        else:
            numbers.append({"number": "", "is_current": False, "is_ellipsis": True})
    return numbers


def _pagination_query(query: str, tag: str, source: str) -> str:
    """The query string a page link carries, so that paging keeps the search
    and any filters the reader has chosen.

    The filters the search settled on rather than the ones asked for: a tag
    that matches nothing is dropped before the results are built, and carrying
    it on into the page links would show a filtered URL over unfiltered
    results.
    """
    return urlencode(
        {
            name: value
            for name, value in (("query", query), ("tag", tag), ("source", source))
            if value
        }
    )


@require_http_methods(["GET", "HEAD"])
def search_view(request):
    query = (request.GET.get("query") or "").strip()
    page_number = request.GET.get("page", 1)
    selected_tag = (request.GET.get("tag") or "").strip().lower()
    selected_source = (request.GET.get("source") or "").strip()
    site = Site.find_for_request(request)

    results = search_backend.search(
        query=query,
        filters={
            "request": request,
            "site": site,
            "live": True,
            "public": not request.user.is_authenticated,
            "tag": selected_tag,
            "source": selected_source,
        },
        page=page_number,
    )
    return render(
        request,
        "search/results.html",
        {
            "query": query,
            "results": results,
            "page_numbers": _page_numbers(results),
            "pagination_query": _pagination_query(
                query,
                (getattr(results, "selected_tag", None) or {}).get("key", ""),
                getattr(results, "selected_source_id", ""),
            ),
            "available_tags": getattr(results, "available_tags", []),
            "available_sources": getattr(results, "available_sources", []),
            "selected_tag": getattr(results, "selected_tag", None),
            "selected_source_id": getattr(results, "selected_source_id", ""),
            "selected_source_label": getattr(results, "selected_source_label", ""),
        },
    )


SUGGESTION_MIN_LENGTH = 2
SUGGESTION_LIMIT = 10
# How far down the ranked results to look before regrouping by kind. Roles are
# few and rank high; a generous pool keeps a role from being cut off behind a
# run of skills that happened to score above it.
SUGGESTION_POOL = 50


@require_http_methods(["GET", "HEAD"])
def search_suggest_view(request):
    """Suggestions for the header search box as the reader types.

    A JSON list of ``{"text", "link", "type"}``, roles first, then skills, then
    supporting pages, at most ten -- the shape the live service's autocomplete
    consumed, so the box can behave as it does there (CS32-3458). Each entry
    is the same result the results page would show for the same query, from
    the same backend and the same ranking, only regrouped by kind: a role
    links to its page, a skill to its section of the A to Z, a page to itself.
    External content is left out; the spec names roles, skills and the site's
    own pages.

    Two characters or fewer suggest nothing rather than everything. ``query``
    is accepted as well as ``q`` so the same URL answers the results page's
    parameter name.
    """
    query = (request.GET.get("q") or request.GET.get("query") or "").strip()
    if len(query) < SUGGESTION_MIN_LENGTH:
        response = JsonResponse([], safe=False)
    else:
        site = Site.find_for_request(request)
        results = search_backend.search(
            query=query,
            filters={
                "request": request,
                "site": site,
                "live": True,
                "public": not request.user.is_authenticated,
                "page_size": SUGGESTION_POOL,
            },
            page=1,
        )
        grouped: dict[str, list[dict[str, str]]] = {"Role": [], "Skill": [], "Page": []}
        for item in results.object_list:
            if item.is_external or not item.url:
                continue
            kind = item.result_type if item.result_type in ("Role", "Skill") else "Page"
            grouped[kind].append({"text": item.title, "link": item.url, "type": kind})
        suggestions = (grouped["Role"] + grouped["Skill"] + grouped["Page"])[:SUGGESTION_LIMIT]
        response = JsonResponse(suggestions, safe=False)
    # What a signed-in editor sees can differ from what the public sees, and
    # content changes as editors work, so nothing on the path keeps a copy.
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET", "HEAD"])
def framework_csv_view(request, name):
    """One of the framework's published CSVs, built from the content asked for.

    Generated at request time rather than kept as a file, so the download is
    the published content by construction -- the live service's copies were
    regenerated on a schedule and drifted, and its links now answer
    AccessDenied. The label and columns match the published downloads.
    """
    if not settings.FEATURE_FLAGS.get("SKILLS"):
        raise Http404
    try:
        label, write = capability_framework_csv.FRAMEWORK_CSV_DOWNLOADS[name]
    except KeyError:
        raise Http404

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.now().date().isoformat()
    response["Content-Disposition"] = (
        f'attachment; filename="{label} - {stamp}.csv"'
    )
    write(response)
    return response


BARE_SERVER_ERROR_HTML = (
    "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Sorry, there is a problem with the service</title></head>"
    "<body><h1>Sorry, there is a problem with the service</h1>"
    "<p>Try again later.</p></body></html>"
)


def server_error(request):
    """The GOV.UK problem-with-the-service page, as branded as the moment allows.

    Django's own 500 handler renders the template without the request, so the
    page could never carry the site's header or contact details. Rendering
    with the request restores those -- and when the problem is deep enough
    that even this template cannot render (the database gone, say), the bare
    page below still answers rather than a blank error.
    """
    try:
        return render(request, "500.html", status=500)
    except Exception:
        return HttpResponseServerError(BARE_SERVER_ERROR_HTML)


def _page_feedback_return_path(request, value: str | None) -> str:
    """The page the answer came from, or the site root if it cannot be trusted.

    The form posts the page's own path so the answer can be counted against
    it and the reader sent back there without JavaScript. It is visitor input,
    so only a local absolute path is accepted -- a scheme, a host, a
    protocol-relative ``//`` or a control character all fall back to ``/``
    rather than becoming an open redirect.
    """
    path = (value or "").strip()[:500]
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    if any(character in path for character in "\r\n\x00 \\"):
        return "/"
    if not url_has_allowed_host_and_scheme(
        path, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return "/"
    return path.split("?", 1)[0].split("#", 1)[0]


@require_POST
def page_feedback_view(request):
    """Record a yes or no to "Is this page useful?" and send the reader back.

    Answers the component in ``includes/page_feedback.html``. With JavaScript
    the form is posted in the background and the page shows its thank-you
    without moving; without it the browser posts here and is redirected back
    to the same page with ``?page_feedback=sent`` so the template can show the
    same thank-you. Only sites that have switched the prompt on answer at all.
    """
    site = Site.find_for_request(request)
    customise_settings = (
        CustomiseSettings.objects.filter(site=site).first() if site else None
    )
    if customise_settings is None or not customise_settings.show_page_feedback_prompt:
        raise Http404

    answer = (request.POST.get("answer") or "").strip().lower()
    if answer not in PAGE_FEEDBACK_ANSWERS:
        return HttpResponseBadRequest("answer must be yes or no")

    path = _page_feedback_return_path(request, request.POST.get("page"))
    page_feedback_logger.info(
        "Page feedback",
        extra={
            "page_feedback_answer": answer,
            "page_feedback_path": path,
            "site_hostname": site.hostname,
        },
    )

    if request.headers.get("X-Requested-With") == "fetch":
        return HttpResponse(status=204)
    response = HttpResponseRedirect(f"{path}?page_feedback=sent#page-feedback")
    response.status_code = 303
    return response


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
            return redirect(f"{reverse('feedback')}?submitted=1")
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
    next_url = safe_oidc_next_url(request, request.GET.get("next"))
    if next_url is None:
        next_url = safe_oidc_next_url(
            request, request.session.get(ADMIN_OIDC_NEXT_URL_KEY)
        )

    if next_url:
        request.session[ADMIN_OIDC_NEXT_URL_KEY] = next_url
    else:
        request.session.pop(ADMIN_OIDC_NEXT_URL_KEY, None)

    return redirect(build_oidc_login_url(next_url))


def oidc_callback(request, provider_id):
    return allauth_oidc_callback(request, provider_id)


def oidc_login(request, provider_id):
    return allauth_oidc_login(request, provider_id)


@require_http_methods(["GET", "POST"])
def account_logout_redirect(request):
    django_logout(request)
    return redirect(build_oidc_logout_url())


@require_POST
def wagtail_logout_redirect(request):
    django_logout(request)
    return redirect("/")
