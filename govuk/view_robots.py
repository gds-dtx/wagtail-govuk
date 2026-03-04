from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET", "HEAD"])
def robots_txt_view(request):
    if getattr(settings, "NOINDEX", False):
        lines = [
            "User-agent: *",
            "User-agent: Googlebot",
            "User-agent: AdsBot-Google",
            "Disallow: /",
        ]
    else:
        lines = [
            "User-agent: *",
            "User-agent: Googlebot",
            "User-agent: AdsBot-Google",
            "Disallow:",
            "\n",
            "User-agent: *",
            "User-agent: Googlebot",
            "User-agent: AdsBot-Google",
            "Allow:",
        ]
    robots_txt = "\n".join(lines) + "\n"
    return HttpResponse(robots_txt, content_type="text/plain; charset=utf-8")
