import base64
import hashlib
from urllib.parse import urlparse

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from django import forms
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

from .constants import (
    HTTP_METHOD_PATTERN,
    SIGNING_ALGORITHM_EDDSA,
    SIGNING_ALGORITHM_ES256,
    SIGNING_ALGORITHM_VALUES,
    SigningPrivateKey,
    SigningPublicKey,
)


class JWTGenerationError(ValueError):
    """Raised when JWT generation input is invalid."""



class SecretTextarea(forms.Textarea):
    """Never render the stored value back into the admin form."""

    def format_value(self, value):
        return ""



def _base64url_without_padding(raw_bytes: bytes) -> str:
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")


def _normalised_signing_algorithm(value: str | None) -> str:
    algorithm = (value or "").strip() or SIGNING_ALGORITHM_EDDSA
    if algorithm not in SIGNING_ALGORITHM_VALUES:
        raise ValidationError("Select a valid signing algorithm.")
    return algorithm


def _private_key_required_error(algorithm: str) -> str:
    if algorithm == SIGNING_ALGORITHM_ES256:
        return "Enter a P-256 private key or use Generate key pair."
    return "Enter an Ed25519 private key or use Generate key pair."


def _private_public_key_mismatch_error(algorithm: str) -> str:
    if algorithm == SIGNING_ALGORITHM_ES256:
        return "Private and public keys do not match the same P-256 key pair."
    return "Private and public keys do not match the same Ed25519 key pair."


def _load_signing_public_key(public_key: str, *, algorithm: str) -> SigningPublicKey:
    normalised_algorithm = _normalised_signing_algorithm(algorithm)
    normalised_public_key = (public_key or "").strip()
    try:
        parsed_public_key = serialization.load_pem_public_key(
            normalised_public_key.encode("utf-8")
        )
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        if normalised_algorithm == SIGNING_ALGORITHM_ES256:
            raise ValidationError(
                "Enter a valid P-256 public key in PEM format."
            ) from exc
        raise ValidationError(
            "Enter a valid Ed25519 public key in PEM format."
        ) from exc

    if normalised_algorithm == SIGNING_ALGORITHM_EDDSA:
        if not isinstance(parsed_public_key, Ed25519PublicKey):
            raise ValidationError("Public key must be an Ed25519 key.")
        return parsed_public_key

    if not isinstance(parsed_public_key, ec.EllipticCurvePublicKey) or not isinstance(
        parsed_public_key.curve,
        ec.SECP256R1,
    ):
        raise ValidationError("Public key must be a P-256 key.")
    return parsed_public_key


def _load_signing_private_key(private_key: str, *, algorithm: str) -> SigningPrivateKey:
    normalised_algorithm = _normalised_signing_algorithm(algorithm)
    normalised_private_key = (private_key or "").strip()
    try:
        parsed_private_key = serialization.load_pem_private_key(
            normalised_private_key.encode("utf-8"),
            password=None,
        )
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        if normalised_algorithm == SIGNING_ALGORITHM_ES256:
            raise ValidationError(
                "Enter a valid unencrypted P-256 private key in PEM format."
            ) from exc
        raise ValidationError(
            "Enter a valid unencrypted Ed25519 private key in PEM format."
        ) from exc

    if normalised_algorithm == SIGNING_ALGORITHM_EDDSA:
        if not isinstance(parsed_private_key, Ed25519PrivateKey):
            raise ValidationError("Private key must be an Ed25519 key.")
        return parsed_private_key

    if not isinstance(parsed_private_key, ec.EllipticCurvePrivateKey) or not isinstance(
        parsed_private_key.curve,
        ec.SECP256R1,
    ):
        raise ValidationError("Private key must be a P-256 key.")
    return parsed_private_key


def _signing_public_key_fingerprint(
    public_key: SigningPublicKey, *, algorithm: str
) -> str:
    normalised_algorithm = _normalised_signing_algorithm(algorithm)
    if normalised_algorithm == SIGNING_ALGORITHM_ES256:
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValidationError("Public key must be a P-256 key.")
        public_numbers = public_key.public_numbers()
        raw_public_key = public_numbers.x.to_bytes(
            32, "big"
        ) + public_numbers.y.to_bytes(
            32,
            "big",
        )
    else:
        raw_public_key = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    return hashlib.sha256(raw_public_key).hexdigest()[:32]


def _normalised_wagtail_admin_issuer() -> str:
    issuer = (getattr(settings, "WAGTAILADMIN_BASE_URL", "") or "").strip()
    if not issuer:
        raise ImproperlyConfigured(
            "WAGTAILADMIN_BASE_URL must be set before generating JWTs."
        )

    parsed_issuer = urlparse(issuer)
    if parsed_issuer.scheme not in {"http", "https"} or not parsed_issuer.netloc:
        raise ImproperlyConfigured(
            "WAGTAILADMIN_BASE_URL must be an absolute http(s) URL."
        )
    return issuer.rstrip("/")


def _normalised_htu(value: str | None) -> str:
    htu = (value or "").strip()
    if not htu:
        raise JWTGenerationError("Claim 'htu' must not be empty when provided.")

    parsed_htu = urlparse(htu)
    if parsed_htu.scheme not in {"http", "https"} or not parsed_htu.netloc:
        raise JWTGenerationError("Claim 'htu' must be an absolute http(s) URL.")
    return htu


def _normalised_htm(value: str | None) -> str:
    htm = (value or "").strip().upper()
    if not htm:
        raise JWTGenerationError("Claim 'htm' must not be empty when provided.")
    if not HTTP_METHOD_PATTERN.match(htm):
        raise JWTGenerationError(
            "Claim 'htm' must be an HTTP method like GET, POST, PUT or DELETE."
        )
    return htm




