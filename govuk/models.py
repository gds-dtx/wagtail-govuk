import base64
import hashlib
import re
from datetime import timedelta
from urllib.parse import urlparse
from uuid import uuid4

import jwt
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
from django.core.paginator import Paginator
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import Truncator
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from taggit.models import TagBase, TaggedItemBase
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Orderable, Page
from wagtail.snippets.blocks import SnippetChooserBlock


HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    message="Enter a valid hex color, for example #1d70b8 or #fff.",
)
HTTP_METHOD_PATTERN = re.compile(r"^[A-Za-z]{3,20}$")
DEFAULT_JWT_LIFETIME = timedelta(minutes=5)
SIGNING_ALGORITHM_EDDSA = "EdDSA"
SIGNING_ALGORITHM_ES256 = "ES256"
SIGNING_ALGORITHM_CHOICES = (
    (SIGNING_ALGORITHM_EDDSA, "EdDSA (Ed25519)"),
    (SIGNING_ALGORITHM_ES256, "ES256 (P-256)"),
)
SIGNING_ALGORITHM_VALUES = {value for value, _ in SIGNING_ALGORITHM_CHOICES}

SigningPublicKey = Ed25519PublicKey | ec.EllipticCurvePublicKey
SigningPrivateKey = Ed25519PrivateKey | ec.EllipticCurvePrivateKey


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


@register_setting(icon="warning")
class PhaseBannerSettings(BaseSiteSetting):
    enabled = models.BooleanField(
        default=False,
        verbose_name="Show phase banner across the site",
    )
    phase_label = models.CharField(
        max_length=20,
        default="Alpha",
        help_text="Label shown in the phase tag, for example Alpha or Beta.",
    )
    feedback_url = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Feedback link URL, for example /feedback or https://example.gov.uk/feedback.",
    )

    panels = [
        FieldPanel("enabled"),
        FieldPanel("phase_label"),
        FieldPanel("feedback_url"),
    ]


@register_setting(icon="link")
class FooterSettings(BaseSiteSetting):
    footer_links = StreamField(
        [
            (
                "link",
                blocks.StructBlock(
                    [
                        (
                            "title",
                            blocks.CharBlock(
                                required=True,
                                max_length=120,
                            ),
                        ),
                        (
                            "url",
                            blocks.CharBlock(
                                required=True,
                                max_length=500,
                                help_text="Use a relative URL like /cookies or an absolute URL like https://www.gov.uk/help.",
                            ),
                        ),
                    ],
                    icon="link",
                    label="Footer link",
                ),
            )
        ],
        blank=True,
        help_text="Links shown in the footer support links list.",
    )

    panels = [
        FieldPanel("footer_links"),
    ]


@register_setting(icon="cog")
class CustomiseSettings(BaseSiteSetting):
    hero_background_color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        validators=[HEX_COLOR_VALIDATOR],
        help_text="Optional hero background color in hex, for example #b5cd1e.",
    )
    hero_text_color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        validators=[HEX_COLOR_VALIDATOR],
        help_text="Optional hero text color in hex, for example #ffffff.",
    )
    extra_css = models.TextField(
        blank=True,
        default="",
        help_text="Optional additional CSS appended after hero overrides.",
    )
    extra_js = models.TextField(
        blank=True,
        default="",
        help_text="Optional additional JavaScript.",
    )

    panels = [
        FieldPanel("hero_background_color"),
        FieldPanel("hero_text_color"),
        FieldPanel("extra_css"),
        FieldPanel("extra_js"),
    ]

    class Meta:
        verbose_name = "Customise"
        verbose_name_plural = "Customise"

    def render_custom_css(self) -> str:
        sections: list[str] = []

        hero_background_color = (self.hero_background_color or "").strip()
        hero_text_color = (self.hero_text_color or "").strip()

        if hero_background_color:
            sections.append(f".masthead {{ background: {hero_background_color}; }}")
        if hero_text_color:
            sections.append(f".masthead {{ color: {hero_text_color}; }}")
            sections.append(f".hero__description {{ color: {hero_text_color}; }}")

        extra_css = (self.extra_css or "").strip()
        if extra_css:
            sections.append(extra_css)

        return "\n".join(sections).strip()

    def render_custom_js(self) -> str:
        return (self.extra_js or "").strip()

    @property
    def has_custom_css(self) -> bool:
        return bool(self.render_custom_css())

    @property
    def has_custom_js(self) -> bool:
        return bool(self.render_custom_js())


@register_setting(icon="search")
class ContentDiscoverySettings(ClusterableModel, BaseSiteSetting):
    panels = [
        InlinePanel(
            "sources",
            heading="Content discovery sources",
            label="Source",
            help_text="Add one or more remote URLs for sitemaps, APIs, JSON feeds, RSS or Atom feeds.",
        ),
    ]

    class Meta:
        verbose_name = "Content discovery"
        verbose_name_plural = "Content discovery"


@register_setting(icon="redirect")
class AuthenticatedRedirectSettings(ClusterableModel, BaseSiteSetting):
    panels = [
        InlinePanel(
            "redirect_rules",
            heading="Authenticated user redirects",
            label="Redirect",
            help_text=(
                "Add one or more temporary redirects. "
                "When an authenticated user requests the source path, "
                "they are redirected to the destination path."
            ),
        ),
    ]

    class Meta:
        verbose_name = "Authenticated user redirects"
        verbose_name_plural = "Authenticated user redirects"


@register_setting(icon="key")
class EdDSAKeySettings(ClusterableModel, BaseSiteSetting):
    panels = [
        InlinePanel(
            "key_pairs",
            heading="Signing key pairs",
            label="Key pair",
            help_text=(
                "Add one or more Ed25519 or P-256 private/public key pairs. "
                "Private keys are hidden after save."
            ),
        ),
    ]

    class Meta:
        verbose_name = "Signing keys"
        verbose_name_plural = "Signing keys"

    @property
    def ordered_key_pairs(self):
        return self.key_pairs.order_by("-is_primary", "sort_order", "id")

    def get_primary_key_pair(self):
        return (
            self.key_pairs.filter(is_primary=True).order_by("sort_order", "id").first()
        )

    def build_jwks_keys(self) -> list[dict[str, str]]:
        return [key_pair.as_jwk() for key_pair in self.ordered_key_pairs]

    def generate_jwt(
        self,
        *,
        htu: str | None = None,
        htm: str | None = None,
        lifetime: timedelta = DEFAULT_JWT_LIFETIME,
        extra_claims: dict | None = None,
        add_jti: bool = False,
    ) -> str:
        if lifetime <= timedelta(seconds=0):
            raise JWTGenerationError("JWT lifetime must be greater than 0 seconds.")

        primary_key_pair = self.get_primary_key_pair()
        if primary_key_pair is None:
            raise JWTGenerationError(
                "Cannot generate JWT because no primary signing key is configured."
            )

        include_http_claims = bool((htu or "").strip() or (htm or "").strip())
        normalised_htu = None
        normalised_htm = None
        if include_http_claims:
            if not (htu and htm):
                raise JWTGenerationError(
                    "Provide both 'htu' and 'htm' claims together, or omit both."
                )
            normalised_htu = _normalised_htu(htu)
            normalised_htm = _normalised_htm(htm)

        now = timezone.now()
        expiration = now + lifetime
        payload: dict[str, object] = {
            "iss": _normalised_wagtail_admin_issuer(),
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(expiration.timestamp()),
        }
        if normalised_htu is not None and normalised_htm is not None:
            payload["htu"] = normalised_htu
            payload["aud"] = normalised_htu
            payload["htm"] = normalised_htm
        if add_jti:
            payload["jti"] = str(uuid4())

        if extra_claims:
            reserved_claims = set(payload)
            overlapping_claims = reserved_claims.intersection(extra_claims)
            if overlapping_claims:
                overlapping_list = ", ".join(sorted(overlapping_claims))
                raise JWTGenerationError(
                    f"extra_claims must not override reserved claim(s): {overlapping_list}."
                )
            payload.update(extra_claims)

        return primary_key_pair.sign_jwt(payload)


class EdDSAKeyPair(Orderable):
    class Algorithm(models.TextChoices):
        EDDSA = SIGNING_ALGORITHM_EDDSA, "EdDSA (Ed25519)"
        ES256 = SIGNING_ALGORITHM_ES256, "ES256 (P-256)"

    settings = ParentalKey(
        "govuk.EdDSAKeySettings",
        on_delete=models.CASCADE,
        related_name="key_pairs",
    )
    key_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional key ID (kid). If blank, it is generated from the public key.",
    )
    algorithm = models.CharField(
        max_length=16,
        choices=Algorithm.choices,
        default=Algorithm.EDDSA,
        help_text="Signing algorithm for this key pair.",
    )
    public_key = models.TextField(
        help_text="Public key in PEM format matching the selected algorithm.",
    )
    private_key = models.TextField(
        blank=True,
        help_text=(
            "Unencrypted private key in PEM format matching the selected algorithm. "
            "Stored securely and hidden after save."
        ),
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Primary key pair used for signing new tokens.",
    )

    panels = [
        FieldPanel("key_id"),
        FieldPanel("algorithm"),
        FieldPanel("public_key"),
        FieldPanel("private_key", widget=SecretTextarea(attrs={"rows": 2})),
    ]

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["settings", "key_id"],
                name="govuk_eddsa_key_id_unique_per_site",
            ),
            models.UniqueConstraint(
                fields=["settings"],
                condition=Q(is_primary=True),
                name="govuk_single_primary_eddsa_key_per_site",
            ),
        ]

    @classmethod
    def _next_available_key_id(
        cls, *, settings_id: int, candidate: str, algorithm: str
    ) -> str:
        normalised_candidate = (candidate or "").strip()
        normalised_algorithm = _normalised_signing_algorithm(algorithm)
        if not normalised_candidate:
            normalised_candidate = (
                "es256-key"
                if normalised_algorithm == SIGNING_ALGORITHM_ES256
                else "eddsa-key"
            )

        base_candidate = normalised_candidate[:64]
        candidate_value = base_candidate
        suffix = 2
        while cls.objects.filter(
            settings_id=settings_id,
            key_id=candidate_value,
        ).exists():
            suffix_text = f"-{suffix}"
            max_base_length = 64 - len(suffix_text)
            candidate_value = f"{base_candidate[:max_base_length]}{suffix_text}"
            suffix += 1
        return candidate_value

    @classmethod
    def generate_for_settings(
        cls,
        *,
        settings_obj: EdDSAKeySettings,
        algorithm: str = SIGNING_ALGORITHM_EDDSA,
    ) -> "EdDSAKeyPair":
        normalised_algorithm = _normalised_signing_algorithm(algorithm)
        if normalised_algorithm == SIGNING_ALGORITHM_ES256:
            private_key = ec.generate_private_key(ec.SECP256R1())
        else:
            private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        generated_key_id = cls._next_available_key_id(
            settings_id=settings_obj.pk,
            candidate=_signing_public_key_fingerprint(
                public_key,
                algorithm=normalised_algorithm,
            ),
            algorithm=normalised_algorithm,
        )
        return cls.objects.create(
            settings=settings_obj,
            key_id=generated_key_id,
            algorithm=normalised_algorithm,
            public_key=public_key_pem,
            private_key=private_key_pem,
        )

    def mark_as_primary(self):
        type(self).objects.filter(
            settings_id=self.settings_id,
            is_primary=True,
        ).exclude(pk=self.pk).update(is_primary=False)
        if not self.is_primary:
            type(self).objects.filter(pk=self.pk).update(is_primary=True)
            self.is_primary = True

    def clean(self):
        super().clean()

        self.algorithm = _normalised_signing_algorithm(self.algorithm)
        self.key_id = (self.key_id or "").strip()
        self.public_key = (self.public_key or "").strip()
        self.private_key = (self.private_key or "").strip()

        public_key = _load_signing_public_key(
            self.public_key,
            algorithm=self.algorithm,
        )

        existing_private_key = ""
        existing_algorithm = self.algorithm
        if self.pk and not self.private_key:
            existing_key_data = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("private_key", "algorithm")
                .first()
            )
            if existing_key_data:
                existing_private_key = (existing_key_data["private_key"] or "").strip()
                existing_algorithm = _normalised_signing_algorithm(
                    existing_key_data["algorithm"]
                )

        private_key_value = self.private_key or existing_private_key

        if self._state.adding and not private_key_value:
            raise ValidationError(
                {"private_key": _private_key_required_error(self.algorithm)}
            )

        if existing_private_key and existing_algorithm != self.algorithm:
            raise ValidationError(
                {"private_key": "Provide a private key when changing the algorithm."}
            )

        if private_key_value:
            private_key = _load_signing_private_key(
                private_key_value,
                algorithm=self.algorithm,
            )
            private_public_key = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            provided_public_key = public_key.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            if private_public_key != provided_public_key:
                raise ValidationError(
                    {"private_key": _private_public_key_mismatch_error(self.algorithm)}
                )

        if not self.key_id:
            self.key_id = _signing_public_key_fingerprint(
                public_key,
                algorithm=self.algorithm,
            )

    def save(self, *args, **kwargs):
        self.algorithm = _normalised_signing_algorithm(self.algorithm)
        self.key_id = (self.key_id or "").strip()
        self.public_key = (self.public_key or "").strip()
        self.private_key = (self.private_key or "").strip()

        if self.pk and not self.private_key:
            existing_key_data = (
                type(self)
                .objects.filter(pk=self.pk)
                .values("private_key", "algorithm")
                .first()
            )
            if existing_key_data and (existing_key_data["private_key"] or "").strip():
                existing_algorithm = _normalised_signing_algorithm(
                    existing_key_data["algorithm"]
                )
                if existing_algorithm != self.algorithm:
                    raise ValidationError(
                        {
                            "private_key": (
                                "Provide a private key when changing the algorithm."
                            )
                        }
                    )
                self.private_key = (existing_key_data["private_key"] or "").strip()

        with transaction.atomic():
            if self.is_primary and self.settings_id:
                type(self).objects.filter(
                    settings_id=self.settings_id,
                    is_primary=True,
                ).exclude(pk=self.pk).update(is_primary=False)

            super().save(*args, **kwargs)

            if (
                not type(self)
                .objects.filter(
                    settings_id=self.settings_id,
                    is_primary=True,
                )
                .exists()
            ):
                type(self).objects.filter(pk=self.pk).update(is_primary=True)
                self.is_primary = True

    def delete(self, *args, **kwargs):
        current_settings_id = self.settings_id
        super().delete(*args, **kwargs)

        if not current_settings_id:
            return

        if (
            type(self)
            .objects.filter(
                settings_id=current_settings_id,
                is_primary=True,
            )
            .exists()
        ):
            return

        next_primary = (
            type(self)
            .objects.filter(settings_id=current_settings_id)
            .order_by("sort_order", "id")
            .first()
        )
        if next_primary:
            type(self).objects.filter(pk=next_primary.pk).update(is_primary=True)

    def as_jwk(self) -> dict[str, str]:
        public_key = _load_signing_public_key(
            self.public_key,
            algorithm=self.algorithm,
        )
        if self.algorithm == SIGNING_ALGORITHM_EDDSA:
            raw_public_key = public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            return {
                "kty": "OKP",
                "use": "sig",
                "alg": SIGNING_ALGORITHM_EDDSA,
                "crv": "Ed25519",
                "kid": self.key_id,
                "x": _base64url_without_padding(raw_public_key),
            }

        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValidationError("Public key must be a P-256 key.")
        public_numbers = public_key.public_numbers()
        x_coordinate = public_numbers.x.to_bytes(32, "big")
        y_coordinate = public_numbers.y.to_bytes(32, "big")
        return {
            "kty": "EC",
            "use": "sig",
            "alg": SIGNING_ALGORITHM_ES256,
            "crv": "P-256",
            "kid": self.key_id,
            "x": _base64url_without_padding(x_coordinate),
            "y": _base64url_without_padding(y_coordinate),
        }

    def sign_jwt(self, payload: dict[str, object]) -> str:
        private_key = _load_signing_private_key(
            self.private_key,
            algorithm=self.algorithm,
        )
        return jwt.encode(
            payload,
            key=private_key,
            algorithm=self.algorithm,
            headers={"kid": self.key_id, "typ": "JWT"},
        )

    def __str__(self) -> str:
        return self.key_id or f"{self.algorithm} key {self.pk}"


class AuthenticatedRedirectRule(Orderable):
    settings = ParentalKey(
        "govuk.AuthenticatedRedirectSettings",
        on_delete=models.CASCADE,
        related_name="redirect_rules",
    )
    source_path = models.CharField(
        max_length=255,
        help_text="Path to match, for example /.",
    )
    destination_path = models.CharField(
        max_length=500,
        help_text="Path to redirect to, for example /dashboard.",
    )

    panels = [
        FieldPanel("source_path"),
        FieldPanel("destination_path"),
    ]

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["settings", "source_path"],
                name="home_auth_redirect_source_path_unique_per_site",
            )
        ]

    def clean(self):
        super().clean()
        self.source_path = self._normalize_path(self.source_path)
        self.destination_path = self._normalize_path(self.destination_path)

        if not self.source_path.startswith("/"):
            raise ValidationError({"source_path": "Source path must start with '/'."})
        if "?" in self.source_path or "#" in self.source_path:
            raise ValidationError(
                {
                    "source_path": (
                        "Source path must not include a query string or fragment."
                    )
                }
            )
        if not self.destination_path.startswith("/"):
            raise ValidationError(
                {"destination_path": "Destination path must start with '/'."}
            )

        if self.source_path == self.destination_path:
            raise ValidationError(
                {
                    "destination_path": (
                        "Destination path must be different from source path."
                    )
                }
            )

    @staticmethod
    def _normalize_path(path: str) -> str:
        return (path or "").strip()

    def __str__(self) -> str:
        return f"{self.source_path} -> {self.destination_path}"


class GovukTag(TagBase):
    """Tag dictionary entry where slug is the key and name is the display value."""

    def clean(self):
        super().clean()
        if self.slug:
            self.slug = self.slug.strip().lower()

    @property
    def key(self) -> str:
        return self.slug

    @property
    def value(self) -> str:
        return self.name

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["slug"]


class ContentDiscoverySource(Orderable):
    settings = ParentalKey(
        "govuk.ContentDiscoverySettings",
        on_delete=models.CASCADE,
        related_name="sources",
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional display name for this source, for example Technology in government blog.",
    )
    url = models.URLField(
        max_length=500,
        help_text="Remote URL to discover content from, for example a sitemap, feed or API endpoint.",
    )
    disable_tls_verification = models.BooleanField(
        default=False,
        verbose_name="Disable TLS verification",
        help_text="When enabled, certificate verification is skipped for this source.",
    )
    send_signed_bearer_jwt = models.BooleanField(
        default=False,
        verbose_name="Send signed bearer JWT",
        help_text=(
            "When enabled, sends an Authorization bearer token signed using this site's "
            "primary key from Settings > Signing keys."
        ),
    )
    default_tags = StreamField(
        [
            (
                "tag",
                SnippetChooserBlock(
                    "govuk.GovukTag",
                    required=False,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional tags to apply to discovered content from this source.",
    )

    panels = [
        FieldPanel("name"),
        FieldPanel("url"),
        FieldPanel("disable_tls_verification"),
        FieldPanel("send_signed_bearer_jwt"),
        FieldPanel("default_tags"),
    ]

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.name or self.url

    @staticmethod
    def _extract_tag_id(value) -> int | None:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                parsed = int(stripped)
                if parsed > 0:
                    return parsed
        tag_pk = getattr(value, "pk", None)
        if type(tag_pk) is int and tag_pk > 0:
            return tag_pk
        if isinstance(value, dict):
            for key in ("value", "id", "pk"):
                extracted = ContentDiscoverySource._extract_tag_id(value.get(key))
                if extracted:
                    return extracted
        return None

    def get_default_tag_ids(self) -> list[int]:
        tag_ids: list[int] = []
        seen: set[int] = set()

        for block in self.default_tags:
            tag_id = self._extract_tag_id(getattr(block, "value", None))
            if tag_id and tag_id not in seen:
                tag_ids.append(tag_id)
                seen.add(tag_id)

        # Some environments return chooser values as raw IDs in JSON;
        # fall back to raw stream data if resolved block values yielded none.
        if not tag_ids:
            for raw_block in getattr(self.default_tags, "raw_data", []) or []:
                tag_id = self._extract_tag_id(raw_block)
                if tag_id and tag_id not in seen:
                    tag_ids.append(tag_id)
                    seen.add(tag_id)
        return tag_ids

    def get_default_tags(self) -> list["GovukTag"]:
        tag_ids = self.get_default_tag_ids()
        if not tag_ids:
            return []

        tags_by_id = {tag.pk: tag for tag in GovukTag.objects.filter(pk__in=tag_ids)}
        return [tags_by_id[tag_id] for tag_id in tag_ids if tag_id in tags_by_id]


class ExternalContentItemTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.ExternalContentItem",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="external_content_item_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class ExternalContentItem(ClusterableModel):
    key = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        db_index=True,
        help_text="SHA256 hash of the URL.",
    )
    source = models.ForeignKey(
        "govuk.ContentDiscoverySource",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="external_content_items",
    )
    url = models.URLField(
        max_length=500,
        unique=True,
        help_text="Remote URL for the discovered content entry.",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional title for this external content item.",
    )
    summary = models.TextField(
        blank=True,
        help_text="Optional summary or excerpt.",
    )
    published_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Optional publication date from the source.",
    )
    created_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Optional created date from the source.",
    )
    updated_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Optional updated date from the source.",
    )
    tags = ClusterTaggableManager(through="govuk.ExternalContentItemTag", blank=True)
    hidden = models.BooleanField(
        default=False,
        help_text="Hide this item from external content listings.",
    )
    metadata = models.JSONField(
        blank=True,
        default=dict,
        help_text="Optional source-specific metadata.",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    panels = [
        FieldPanel("source"),
        FieldPanel("url"),
        FieldPanel("title"),
        FieldPanel("summary"),
        FieldPanel("published_at"),
        FieldPanel("created_at"),
        FieldPanel("updated_at"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
        FieldPanel("hidden"),
        FieldPanel("metadata"),
    ]

    class Meta:
        ordering = ["-last_seen_at", "title", "url"]

    @staticmethod
    def build_key(url: str) -> str:
        return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        self.url = self.url.strip()
        self.key = self.build_key(self.url)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title or self.url

    @classmethod
    def upsert_from_url(cls, *, url: str, source=None, **defaults):
        normalised_url = url.strip()
        item, _ = cls.objects.update_or_create(
            url=normalised_url,
            defaults={"source": source, **defaults},
        )
        if source:
            source_tags = source.get_default_tags()
            if source_tags:
                existing_tag_ids = set(
                    item.tagged_items.values_list("tag_id", flat=True)
                )
                rows_to_add = [
                    ExternalContentItemTag(content_object=item, tag=source_tag)
                    for source_tag in source_tags
                    if source_tag.pk not in existing_tag_ids
                ]
                if rows_to_add:
                    ExternalContentItemTag.objects.bulk_create(
                        rows_to_add,
                        ignore_conflicts=True,
                    )
        return item


class ContentPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.ContentPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="content_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class SectionPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.SectionPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="section_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class TagListingsPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.TagListingsPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="tag_listings_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class ContentPage(Page):
    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]
    enable_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable hero styling",
        help_text="When enabled, this page uses hero styling.",
    )
    enable_combined_service_navigation_and_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable combined service navigation and hero styling",
        help_text="When enabled, this page uses a combined service navigation and hero styling.",
    )
    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    body = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )
    tags = ClusterTaggableManager(through="govuk.ContentPageTag", blank=True)

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("body"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("enable_free_text_heading_navigation"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]


class TagListingsPage(Page):
    enable_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable hero styling",
        help_text="When enabled, this page uses hero styling.",
    )
    enable_combined_service_navigation_and_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable combined service navigation and hero styling",
        help_text="When enabled, this page uses a combined service navigation and hero styling.",
    )
    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    free_text = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )
    enable_tag_filter = models.BooleanField(
        default=False,
        verbose_name="Enable tag filter",
        help_text="Show a tag filter control above the listings.",
    )
    enable_source_filter = models.BooleanField(
        default=False,
        verbose_name="Enable source filter",
        help_text="Show a source filter control above the listings.",
    )
    tags = ClusterTaggableManager(
        through="govuk.TagListingsPageTag",
        blank=True,
    )

    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        InlinePanel("tagged_items", heading="Tags to list", label="Tag", min_num=1),
        FieldPanel("free_text"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("enable_tag_filter"),
        FieldPanel("enable_source_filter"),
        FieldPanel("enable_free_text_heading_navigation"),
    ]

    def _configured_tag_ids(self) -> list[int]:
        return list(self.tags.values_list("id", flat=True))

    def _effective_tag_ids(self, *, selected_tag_id: int | None = None) -> list[int]:
        configured_tag_ids = self._configured_tag_ids()
        if not configured_tag_ids:
            return []
        if selected_tag_id is not None and selected_tag_id in configured_tag_ids:
            return [selected_tag_id]
        return configured_tag_ids

    def _external_listing_queryset(self, *, tag_ids: list[int]):
        if not tag_ids:
            return ExternalContentItem.objects.none()
        return (
            ExternalContentItem.objects.filter(hidden=False, tags__id__in=tag_ids)
            .distinct()
            .select_related("source")
        )

    def _page_listing_items(self, *, tag_ids: list[int], request=None) -> list[dict]:
        if not tag_ids:
            return []

        page_sort_updated = Coalesce(
            "last_published_at",
            "latest_revision_created_at",
            "first_published_at",
        )
        content_pages = (
            ContentPage.objects.live()
            .filter(tags__id__in=tag_ids)
            .annotate(sort_updated=page_sort_updated)
            .distinct()
        )
        section_pages = (
            SectionPage.objects.live()
            .filter(tags__id__in=tag_ids)
            .annotate(sort_updated=page_sort_updated)
            .distinct()
        )
        if request is None or not request.user.is_authenticated:
            content_pages = content_pages.public()
            section_pages = section_pages.public()

        page_items: list[dict] = []
        for page in list(content_pages) + list(section_pages):
            page_items.append(
                {
                    "id": page.id,
                    "url": page.url or page.url_path,
                    "title": page.hero_title or page.title,
                    "summary": page.hero_intro or page.search_description or "",
                    "source": None,
                    "metadata": {},
                    "updated_at": page.last_published_at or page.sort_updated,
                    "created_at": page.first_published_at,
                    "published_at": page.first_published_at,
                    "last_seen_at": page.last_published_at,
                    "sort_updated": page.sort_updated,
                }
            )
        return page_items

    def get_listing_queryset(
        self,
        *,
        selected_tag_id: int | None = None,
        selected_source_id: int | None = None,
        request=None,
    ) -> list[dict]:
        # Keep this as the single data-source entry point so external content and
        # tagged Wagtail pages remain filtered and sorted consistently.
        tag_ids = self._effective_tag_ids(selected_tag_id=selected_tag_id)
        if not tag_ids:
            return []

        external_queryset = self._external_listing_queryset(tag_ids=tag_ids).annotate(
            sort_updated=Coalesce(
                "updated_at",
                "created_at",
                "published_at",
                "last_seen_at",
                "first_seen_at",
            )
        )
        if selected_source_id is not None:
            external_queryset = external_queryset.filter(source_id=selected_source_id)

        listing_items: list[dict] = []
        for item in external_queryset:
            source_label = ""
            if item.source is not None:
                source_label = (item.source.name or item.source.url or "").strip()
            listing_items.append(
                {
                    "id": item.id,
                    "url": item.url,
                    "title": item.title,
                    "summary": item.summary,
                    "source": {"name": source_label} if source_label else None,
                    "metadata": item.metadata or {},
                    "updated_at": item.updated_at,
                    "created_at": item.created_at,
                    "published_at": item.published_at,
                    "last_seen_at": item.last_seen_at,
                    "sort_updated": item.sort_updated,
                }
            )

        if selected_source_id is None:
            listing_items.extend(
                self._page_listing_items(tag_ids=tag_ids, request=request)
            )

        listing_items.sort(
            key=lambda item: (
                item["sort_updated"].timestamp() if item["sort_updated"] else 0.0,
                item["id"],
            ),
            reverse=True,
        )
        return listing_items

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)

        available_tags = list(self.tags.all())
        selected_tag = None
        selected_tag_slug = ""
        if self.enable_tag_filter:
            selected_tag_slug = (request.GET.get("tag") or "").strip().lower()
            if selected_tag_slug:
                selected_tag = next(
                    (tag for tag in available_tags if tag.slug == selected_tag_slug),
                    None,
                )

        available_sources = []
        selected_source_id = ""
        selected_source_label = ""
        selected_tag_id = selected_tag.id if selected_tag is not None else None
        source_tag_ids = self._effective_tag_ids(selected_tag_id=selected_tag_id)
        source_rows = (
            self._external_listing_queryset(tag_ids=source_tag_ids)
            .exclude(source__isnull=True)
            .values("source_id", "source__name", "source__url")
            .distinct()
            .order_by("source__name", "source__url")
        )
        for source_row in source_rows:
            source_id = source_row["source_id"]
            if source_id is None:
                continue
            source_label = (
                source_row["source__name"] or source_row["source__url"] or ""
            ).strip()
            if not source_label:
                continue
            available_sources.append(
                {
                    "id": str(source_id),
                    "label": source_label,
                }
            )

        if self.enable_source_filter:
            selected_source_id = (request.GET.get("source") or "").strip()
            selected_source = next(
                (
                    source
                    for source in available_sources
                    if source["id"] == selected_source_id
                ),
                None,
            )
            if selected_source is not None:
                selected_source_label = selected_source["label"]
            else:
                selected_source_id = ""

        selected_source_pk = int(selected_source_id) if selected_source_id else None
        listing_items = self.get_listing_queryset(
            selected_tag_id=selected_tag.id if selected_tag is not None else None,
            selected_source_id=selected_source_pk,
            request=request,
        )

        paginator = Paginator(listing_items, 15)
        context["listing_items"] = paginator.get_page(request.GET.get("page"))
        context["available_tags"] = available_tags
        context["available_sources"] = available_sources
        context["selected_tag"] = selected_tag
        context["selected_source_id"] = selected_source_id
        context["selected_source_label"] = selected_source_label
        return context


class SectionPage(Page):
    enable_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable hero styling",
        help_text="When enabled, this page uses hero styling.",
    )
    enable_combined_service_navigation_and_hero_styling = models.BooleanField(
        default=False,
        verbose_name="Enable combined service navigation and hero styling",
        help_text="When enabled, this page uses a combined service navigation and hero styling.",
    )
    hero_title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional hero heading. If blank, the page title is used.",
    )
    hero_intro = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
    )
    tags = ClusterTaggableManager(through="govuk.SectionPageTag", blank=True)
    rows = StreamField(
        [
            (
                "row",
                blocks.StructBlock(
                    [
                        (
                            "heading",
                            blocks.CharBlock(
                                required=False,
                                help_text="Optional heading for this row section.",
                            ),
                        ),
                        (
                            "cards",
                            blocks.ListBlock(
                                blocks.StructBlock(
                                    [
                                        (
                                            "title",
                                            blocks.CharBlock(
                                                required=True,
                                                max_length=120,
                                            ),
                                        ),
                                        (
                                            "text",
                                            blocks.RichTextBlock(
                                                required=False,
                                                features=[
                                                    "bold",
                                                    "italic",
                                                    "link",
                                                    "ul",
                                                    "ol",
                                                ],
                                            ),
                                        ),
                                        (
                                            "link_text",
                                            blocks.CharBlock(
                                                required=False,
                                                max_length=80,
                                                help_text="Optional button text.",
                                            ),
                                        ),
                                        (
                                            "link_url",
                                            blocks.CharBlock(
                                                required=False,
                                                max_length=500,
                                                help_text="Optional URL for the button, for example /apply or https://example.gov.uk/apply.",
                                            ),
                                        ),
                                        (
                                            "tags",
                                            blocks.ListBlock(
                                                SnippetChooserBlock(
                                                    "govuk.GovukTag",
                                                    required=False,
                                                ),
                                                required=False,
                                                help_text="Optional tags for this card.",
                                            ),
                                        ),
                                    ],
                                    icon="doc-full",
                                    label="Card",
                                ),
                                min_num=1,
                                max_num=15,
                                help_text="Add between 1 and 15 cards in this row.",
                            ),
                        ),
                    ],
                    icon="placeholder",
                    label="Row section",
                ),
            ),
        ],
        blank=True,
        help_text="Add one or more row sections. Each row can contain up to 15 cards.",
    )
    free_text = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )

    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
    ]

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("rows"),
        FieldPanel("free_text"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("enable_free_text_heading_navigation"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]


class Feedback(models.Model):
    class FeedbackType(models.TextChoices):
        CORRECTION = "correction", "Correction"
        FEATURE_SUGGESTION = "feature_suggestion", "Feature suggestion"
        BUG_REPORT = "bug_report", "Bug report"
        CONTENT_REQUEST = "content_request", "Content request"
        GENERAL = "general", "General feedback"
        OTHER = "other", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_entries",
    )
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    feedback_type = models.CharField(
        max_length=40,
        choices=FeedbackType.choices,
        default=FeedbackType.GENERAL,
    )
    comments = models.TextField()
    referrer = models.CharField(max_length=500, blank=True)
    browser = models.CharField(max_length=255, blank=True)
    is_mobile = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        label = self.name or "Unknown user"
        return f"{label} - {self.get_feedback_type_display()}"

    def feedback_type_label(self) -> str:
        return self.get_feedback_type_display()

    feedback_type_label.short_description = "Type"

    def comments_preview(self) -> str:
        return Truncator(self.comments).chars(50)

    comments_preview.short_description = "Feedback"


__all__ = [
    "AuthenticatedRedirectRule",
    "AuthenticatedRedirectSettings",
    "ContentDiscoverySettings",
    "ContentDiscoverySource",
    "CustomiseSettings",
    "EdDSAKeyPair",
    "EdDSAKeySettings",
    "JWTGenerationError",
    "ContentPage",
    "ContentPageTag",
    "ExternalContentItem",
    "ExternalContentItemTag",
    "Feedback",
    "FooterSettings",
    "GovukTag",
    "PhaseBannerSettings",
    "SectionPage",
    "SectionPageTag",
    "TagListingsPage",
    "TagListingsPageTag",
]
