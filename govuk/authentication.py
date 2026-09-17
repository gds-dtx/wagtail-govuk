from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed


@lru_cache(maxsize=None)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    """One JWKS client per URL, shared across requests.

    DRF builds a fresh authenticator for every request, so caching the client
    here rather than on the instance is what lets PyJWKClient keep the fetched
    signing keys between requests instead of calling the JWKS endpoint each
    time.
    """
    return jwt.PyJWKClient(jwks_url)


class TokenUser:
    """A stateless user built from verified OIDC ID-token claims.

    No database row is read or created: authenticating here only proves the
    caller holds a token the trusted issuer signed. It implements the slice of
    Django's user contract that DRF permissions and request handling touch.
    """

    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, claims: dict, *, id_claim: str = "sub"):
        self.claims = claims
        self.id = claims.get(id_claim)

    @property
    def pk(self):
        return self.id

    @property
    def username(self) -> str:
        return "" if self.id is None else str(self.id)

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_username(self) -> str:
        return self.username

    def __str__(self) -> str:
        return f"TokenUser {self.username}"

    def __eq__(self, other) -> bool:
        return isinstance(other, TokenUser) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)


class InternalAccessJWTAuthentication(BaseAuthentication):
    """Validate bearer tokens issued by Internal Access OIDC.

    Verification is configured through the OIDC_TOKEN_AUTH settings in
    govuk/settings/base.py (algorithm, JWKS URL, issuer, and allowed
    audiences). Tokens are verified against the issuer's published JWKS using
    PyJWT; no token is ever minted here.
    """

    @property
    def _config(self) -> dict:
        return settings.OIDC_TOKEN_AUTH

    @property
    def max_id_token_age(self) -> timedelta:
        return timedelta(
            seconds=self._config.get("MAX_ID_TOKEN_AGE_SECONDS", 12 * 3600)
        )

    def authenticate(self, request):
        raw_token = self._raw_token(request)
        if raw_token is None:
            # No bearer credentials: let the permission layer decide, and let
            # any other authenticator have its turn.
            return None
        claims = self._validated_claims(raw_token)
        user = TokenUser(claims, id_claim=self._config.get("USER_ID_CLAIM", "sub"))
        return user, claims

    def authenticate_header(self, request):
        # Providing this is what makes DRF answer 401 (not 403) when a
        # protected endpoint is called without credentials.
        return f'{self._config.get("AUTH_HEADER_TYPE", "Bearer")} realm="api"'

    def _raw_token(self, request):
        header = get_authorization_header(request)
        parts = header.split()
        if not parts:
            return None
        expected = self._config.get("AUTH_HEADER_TYPE", "Bearer").encode()
        if parts[0].lower() != expected.lower():
            # A different auth scheme; not ours to handle. There is deliberately
            # no query-string fallback -- tokens do not belong in URLs.
            return None
        if len(parts) != 2:
            raise AuthenticationFailed(
                "Authorization header must contain two space-delimited values"
            )
        return parts[1]

    def _validated_claims(self, raw_token) -> dict:
        audience = self._config.get("AUDIENCE")
        issuer = self._config.get("ISSUER")
        try:
            signing_key = _jwks_client(
                self._config["JWKS_URL"]
            ).get_signing_key_from_jwt(raw_token)
            claims = jwt.decode(
                raw_token,
                key=signing_key.key,
                algorithms=[self._config.get("ALGORITHM", "RS256")],
                audience=audience,
                issuer=issuer,
                leeway=self._config.get("LEEWAY_SECONDS", 0),
                options={
                    # Verify the audience only when one is configured, and
                    # require the claims we then go on to trust.
                    "verify_aud": audience is not None,
                    "verify_iss": issuer is not None,
                    "require": ["exp", "iat"],
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationFailed(f"Invalid token: {exc}") from exc
        self._validate_id_token_age(claims)
        return claims

    def _validate_id_token_age(self, claims: dict) -> None:
        # PyJWT's "require"/"iat" validation above guarantees a numeric iat.
        issued_at = datetime.fromtimestamp(float(claims["iat"]), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if issued_at > now:
            raise AuthenticationFailed("Token 'iat' claim is in the future")
        if now - issued_at >= self.max_id_token_age:
            raise AuthenticationFailed(
                "Token is older than the maximum allowed 12 hours"
            )
