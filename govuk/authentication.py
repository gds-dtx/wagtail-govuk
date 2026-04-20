from datetime import timedelta

from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.authentication import JWTStatelessUserAuthentication
from rest_framework_simplejwt.utils import aware_utcnow, datetime_from_epoch


class InternalAccessJWTAuthentication(JWTStatelessUserAuthentication):
    """
    Validate bearer tokens issued by Internal Access OIDC.

    Verification is configured through the SIMPLE_JWT settings in
    govuk/settings/base.py (JWKS URL, issuer, and allowed audiences).
    """

    max_id_token_age = timedelta(hours=12)

    def get_validated_token(self, raw_token):
        validated_token = super().get_validated_token(raw_token)
        self._validate_id_token_age(validated_token)
        return validated_token

    def _validate_id_token_age(self, validated_token):
        issued_at_epoch = validated_token.get("iat")
        if issued_at_epoch is None:
            raise InvalidToken("Token has no 'iat' claim")

        try:
            issued_at = datetime_from_epoch(float(issued_at_epoch))
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise InvalidToken("Token has invalid 'iat' claim") from exc

        now = aware_utcnow()
        if issued_at > now:
            raise InvalidToken("Token 'iat' claim is in the future")
        if now - issued_at >= self.max_id_token_age:
            raise InvalidToken("Token is older than the maximum allowed 12 hours")
