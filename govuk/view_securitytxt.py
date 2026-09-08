from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponsePermanentRedirect
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET", "HEAD"])
def security_txt_view(request):
    location = getattr(
        settings,
        "SECURITYTXT_LOCATION",
        None,
    )
    if location is None:
        return HttpResponseNotFound()
    return HttpResponsePermanentRedirect(location)
