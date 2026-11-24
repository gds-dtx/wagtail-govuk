"""Who may use Claude, and the token that proves it to the ASGI service.

Only Django can mint the token, while serving /search/ to a reader allowed to
use AI search, so the service's Claude routes answer nothing else. It is bound
to the question, expires in a minute and is good once (``claims``).
"""

import hashlib
import secrets

from django.contrib.auth import get_user_model
from django.core import signing

from .claims import account_for
from .claude import get_claude

ACCESS_SALT = "govuk.ai_search.access"
ACCESS_MAX_AGE = 60


def claude_offered(search_settings) -> bool:
    """Whether AI search has anything for Claude to do on this site."""
    return bool(
        search_settings is not None
        and search_settings.ai_search_on
        and (search_settings.use_ai_ranking or search_settings.show_ai_summary)
        and get_claude() is not None
    )


def may_use_claude(search_settings, user) -> bool:
    if not claude_offered(search_settings):
        return False
    signed_in = bool(user is not None and user.is_authenticated)
    return signed_in or not search_settings.ai_search_requires_sign_in


def _question_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def make_access(user, question: str) -> str:
    signed_in = user is not None and user.is_authenticated
    return signing.dumps(
        {"u": user.pk if signed_in else None, "q": _question_hash(question), "j": secrets.token_urlsafe(16)},
        salt=ACCESS_SALT,
    )


def read_access(token: str, question: str, search_settings) -> dict | None:
    """``{"signed_in", "id", "account"}`` if ``token`` was minted for this question,
    recently, for a reader still allowed Claude; otherwise None. Does not claim it."""
    if not token or not claude_offered(search_settings):
        return None
    try:
        data = signing.loads(token, salt=ACCESS_SALT, max_age=ACCESS_MAX_AGE)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or data.get("q") != _question_hash(question) or not data.get("j"):
        return None
    user_id = data.get("u")
    if user_id is None:
        if search_settings.ai_search_requires_sign_in:
            return None
        return {"signed_in": False, "id": data["j"], "account": ""}
    if not get_user_model().objects.filter(pk=user_id, is_active=True).exists():
        return None
    return {"signed_in": True, "id": data["j"], "account": account_for(user_id)}
