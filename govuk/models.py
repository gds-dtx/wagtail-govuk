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
from django.utils.text import slugify
from django.utils.text import Truncator
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from taggit.models import TagBase, TaggedItemBase
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, InlinePanel
from wagtail.blocks import StructValue
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Orderable, Page, Site
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
    (SIGNING_ALGORITHM_ES256, "ES256 (P-256)"),
    (SIGNING_ALGORITHM_EDDSA, "EdDSA (Ed25519)"),
)
SIGNING_ALGORITHM_VALUES = {value for value, _ in SIGNING_ALGORITHM_CHOICES}
SKILL_LEVEL_CHOICES = (
    ("awareness", "Awareness"),
    ("working", "Working"),
    ("practitioner", "Practitioner"),
    ("expert", "Expert"),
)
SKILL_LEVEL_LABELS = dict(SKILL_LEVEL_CHOICES)
SKILL_LEVEL_VALUES = {value for value, _ in SKILL_LEVEL_CHOICES}
SKILL_LEVEL_LABEL_TO_VALUE = {
    label.lower(): value for value, label in SKILL_LEVEL_CHOICES
}
SKILL_LEVEL_ORDINALS = {
    "awareness": "first",
    "working": "second",
    "practitioner": "third",
    "expert": "fourth",
}
THIS_SITE_SOURCE_FILTER = "__this_site__"
SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES = [
    "h2",
    "h3",
    "h4",
    "bold",
    "italic",
    "link",
    "ul",
    "ol",
    "inset-text",
]

SigningPublicKey = Ed25519PublicKey | ec.EllipticCurvePublicKey
SigningPrivateKey = Ed25519PrivateKey | ec.EllipticCurvePrivateKey


class JWTGenerationError(ValueError):
    """Raised when JWT generation input is invalid."""


class SecretTextarea(forms.Textarea):
    """Never render the stored value back into the admin form."""

    def format_value(self, value):
        return ""


class LinkStructValue(StructValue):
    @property
    def url(self):
        external_url = (self.get("external_url") or "").strip()
        if external_url:
            return external_url
        page = self.get("page")
        return (page.url or "") if page else ""


class LinkBlock(blocks.StructBlock):
    title = blocks.CharBlock(
        required=True,
        max_length=120,
    )
    page = blocks.PageChooserBlock(
        required=False,
    )
    external_url = blocks.URLBlock(
        required=False,
        help_text="Use an absolute external URL like https://www.gov.uk/help.",
    )

    class Meta:
        icon = "link"
        label = "Link"
        value_class = LinkStructValue


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


def _normalised_skill_level(value: str | None) -> str:
    raw_level = (value or "").strip().lower()
    if raw_level in SKILL_LEVEL_VALUES:
        return raw_level

    mapped_level = SKILL_LEVEL_LABEL_TO_VALUE.get(raw_level)
    if mapped_level:
        return mapped_level
    return ""


def _next_unique_slug(
    *,
    model_class,
    candidate: str,
    instance_id: int | None = None,
    fallback: str,
) -> str:
    base_slug = (candidate or "").strip("-")
    if not base_slug:
        base_slug = fallback
    base_slug = base_slug[:120]

    slug_value = base_slug
    suffix = 2
    while model_class.objects.filter(slug=slug_value).exclude(pk=instance_id).exists():
        suffix_text = f"-{suffix}"
        max_base_length = 120 - len(suffix_text)
        slug_value = f"{base_slug[:max_base_length]}{suffix_text}"
        suffix += 1
    return slug_value


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
    phase_text = models.CharField(
        max_length=500,
        default="This is a new service - ",
        help_text="Phase banners are used to show users your service is still being worked on.",
    )
    feedback_link_text = models.CharField(
        max_length=100,
        default="your feedback",
        help_text="Wording of the feedback link, shown between the two sentences.",
    )
    phase_text_after = models.CharField(
        max_length=500,
        default="will help us to improve it.",
        help_text="Wording shown after the feedback link.",
    )

    panels = [
        FieldPanel("enabled"),
        FieldPanel("phase_label"),
        FieldPanel("feedback_url"),
        FieldPanel("phase_text"),
        FieldPanel("feedback_link_text"),
        FieldPanel("phase_text_after"),
    ]


@register_setting(icon="link")
class FooterSettings(BaseSiteSetting):
    footer_links = StreamField(
        [
            (
                "link",
                LinkBlock(),
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
    header_logo = models.CharField(
        max_length=20,
        choices=[
            ("govuk", "GOV.UK"),
            ("uk-government", "UK Government"),
        ],
        default="govuk",
        help_text="Select the header logo to display.",
    )
    show_site_name_in_search_box = models.BooleanField(
        default=False,
        help_text="Include the site name in the header search label and placeholder.",
    )
    show_service_name_in_navigation = models.BooleanField(
        default=False,
        help_text=(
            "Show the site name and search in the service navigation bar rather "
            "than in the GOV.UK header, as GOV.UK services usually do."
        ),
    )
    hide_sign_in_link = models.BooleanField(
        default=False,
        help_text="Hide the sign in link, for sites where visitors never sign in.",
    )
    search_placeholder = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Wording shown in the header search box, for example "
            "Search for roles or skills. Defaults to Search."
        ),
    )
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

    panels = [
        FieldPanel("header_logo"),
        FieldPanel("show_site_name_in_search_box"),
        FieldPanel("show_service_name_in_navigation"),
        FieldPanel("hide_sign_in_link"),
        FieldPanel("search_placeholder"),
        FieldPanel("hero_background_color"),
        FieldPanel("hero_text_color"),
        FieldPanel("extra_css"),
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

    @property
    def has_custom_css(self) -> bool:
        return bool(self.render_custom_css())


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


class GovukSkill(models.Model):
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        help_text="Optional key used for links, for example forensics.",
    )
    title = models.CharField(
        max_length=255,
        help_text="Skill name, for example Forensics.",
    )
    body = RichTextField(
        blank=True,
        features=SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES,
        help_text="Short description of the skill.",
    )
    awareness_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional awareness-level points.",
    )
    working_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional working-level points.",
    )
    practitioner_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional practitioner-level points.",
    )
    expert_points = StreamField(
        [
            (
                "point",
                blocks.TextBlock(
                    required=False,
                    max_length=500,
                    rows=3,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Optional expert-level points.",
    )

    panels = [
        FieldPanel("slug"),
        FieldPanel("title"),
        FieldPanel("body"),
        FieldPanel("awareness_points"),
        FieldPanel("working_points"),
        FieldPanel("practitioner_points"),
        FieldPanel("expert_points"),
    ]

    class Meta:
        verbose_name = "Skill"
        verbose_name_plural = "Skills"
        ordering = ["title", "slug"]

    @staticmethod
    def _normalised_stream_points(stream_value) -> list[dict]:
        if not stream_value:
            return []

        cleaned_entries: list[dict] = []
        for block in stream_value:
            block_type = getattr(block, "block_type", None)
            block_value = getattr(block, "value", None)

            if isinstance(block, dict):
                block_type = block.get("type")
                block_value = block.get("value")
            elif isinstance(block, str):
                block_type = "point"
                block_value = block

            if block_type != "point":
                continue
            point = (block_value or "").strip()
            if point:
                cleaned_entries.append({"type": "point", "value": point})
        return cleaned_entries

    def clean(self):
        super().clean()
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="skill",
        )
        self.awareness_points = self._normalised_stream_points(self.awareness_points)
        self.working_points = self._normalised_stream_points(self.working_points)
        self.practitioner_points = self._normalised_stream_points(
            self.practitioner_points
        )
        self.expert_points = self._normalised_stream_points(self.expert_points)

    def points_for_level(self, level: str | None) -> list[str]:
        level_key = _normalised_skill_level(level)
        if not level_key:
            return []

        points_field_map = {
            "awareness": self.awareness_points,
            "working": self.working_points,
            "practitioner": self.practitioner_points,
            "expert": self.expert_points,
        }
        stream_value = points_field_map.get(level_key)
        if stream_value is None:
            return []
        return [
            entry["value"] for entry in self._normalised_stream_points(stream_value)
        ]

    def get_level_rows(self) -> list[dict]:
        level_rows: list[dict] = []
        for level_key, level_label in SKILL_LEVEL_CHOICES:
            level_rows.append(
                {
                    "key": level_key,
                    "label": level_label,
                    "ordinal": SKILL_LEVEL_ORDINALS.get(level_key, ""),
                    "points": self.points_for_level(level_key),
                }
            )
        return level_rows

    def __str__(self) -> str:
        return self.title or self.slug

    def save(self, *args, **kwargs):
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="skill",
        )
        self.awareness_points = self._normalised_stream_points(self.awareness_points)
        self.working_points = self._normalised_stream_points(self.working_points)
        self.practitioner_points = self._normalised_stream_points(
            self.practitioner_points
        )
        self.expert_points = self._normalised_stream_points(self.expert_points)
        super().save(*args, **kwargs)


class GovukRole(models.Model):
    slug = models.SlugField(
        max_length=120,
        unique=True,
        blank=True,
        help_text="Optional key used for links, for example digital-forensics-analyst.",
    )
    title = models.CharField(
        max_length=255,
        help_text="Role name, for example Digital forensics analyst.",
    )
    body = RichTextField(
        blank=True,
        features=SKILLS_AND_ROLES_BODY_RICH_TEXT_FEATURES,
        help_text="Optional summary for this role.",
    )
    levels = StreamField(
        [
            (
                "level",
                blocks.StructBlock(
                    [
                        (
                            "title",
                            blocks.CharBlock(
                                required=True,
                                max_length=255,
                                help_text=(
                                    "Role level name, for example "
                                    "Associate digital forensics analyst."
                                ),
                            ),
                        ),
                        (
                            "description",
                            blocks.RichTextBlock(
                                required=False,
                                features=["bold", "italic", "link", "ul", "ol"],
                            ),
                        ),
                        (
                            "skills",
                            blocks.ListBlock(
                                blocks.StructBlock(
                                    [
                                        (
                                            "skill",
                                            SnippetChooserBlock(
                                                "govuk.GovukSkill",
                                                required=True,
                                            ),
                                        ),
                                        (
                                            "level",
                                            blocks.ChoiceBlock(
                                                required=True,
                                                choices=SKILL_LEVEL_CHOICES,
                                            ),
                                        ),
                                    ],
                                    icon="pick",
                                    label="Skill requirement",
                                ),
                                required=False,
                                help_text=(
                                    "Add one or more skill requirements for this role level."
                                ),
                            ),
                        ),
                    ],
                    icon="user",
                    label="Role level",
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Role levels and their associated skills.",
    )

    panels = [
        FieldPanel("slug"),
        FieldPanel("title"),
        FieldPanel("body"),
        FieldPanel("levels"),
    ]

    class Meta:
        verbose_name = "Role"
        verbose_name_plural = "Roles"
        ordering = ["title", "slug"]

    def clean(self):
        super().clean()
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="role",
        )

    @staticmethod
    def _skill_level_label(level: str | None) -> str:
        level_key = _normalised_skill_level(level)
        if not level_key:
            return ""
        return SKILL_LEVEL_LABELS.get(level_key, level_key.title())

    def get_levels_with_skills(self) -> list[dict]:
        role_levels: list[dict] = []
        for level_block in self.levels:
            if level_block.block_type != "level":
                continue

            level_value = level_block.value
            role_level_title = (level_value.get("title") or "").strip()
            role_level_description = level_value.get("description") or ""
            skill_rows: list[dict] = []

            for skill_requirement in level_value.get("skills") or []:
                skill = skill_requirement.get("skill")
                required_level = _normalised_skill_level(skill_requirement.get("level"))
                if skill is None or not required_level:
                    continue

                skill_rows.append(
                    {
                        "skill": skill,
                        "required_level": required_level,
                        "required_level_label": self._skill_level_label(required_level),
                        "points": skill.points_for_level(required_level),
                    }
                )

            skill_rows.sort(
                key=lambda row: (
                    (row["skill"].title or "").strip().lower(),
                    row["skill"].pk or 0,
                )
            )

            role_levels.append(
                {
                    "title": role_level_title,
                    "description": role_level_description,
                    "skills": skill_rows,
                }
            )

        return role_levels

    def __str__(self) -> str:
        return self.title or self.slug

    def save(self, *args, **kwargs):
        self.title = (self.title or "").strip()
        slug_candidate = slugify((self.slug or self.title or "").strip())[:120]
        self.slug = _next_unique_slug(
            model_class=type(self),
            candidate=slug_candidate,
            instance_id=self.pk,
            fallback="role",
        )
        super().save(*args, **kwargs)


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
    sync_source = models.BooleanField(
        default=False,
        verbose_name="Sync from remote source and hide any missing",
        help_text=(
            "When enabled, this remote source will be synced and any "
            "missing items previously discovered will be hidden. Disabled "
            "(default) will just keep adding discovered content."
        ),
    )
    consume_tags = models.BooleanField(
        default=False,
        verbose_name="Consume tags from remote source",
        help_text=(
            "When enabled, if the remote source content has tags they will be "
            "applied to the discovered content."
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
        FieldPanel("sync_source"),
        FieldPanel("consume_tags"),
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
    private = models.BooleanField(
        default=False,
        help_text="Set this item private, accessible to any logged-in users.",
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
        FieldPanel("private"),
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
    def upsert_from_url(cls, *, url: str, source=None, tags=None, **defaults):
        normalised_url = url.strip()
        item, _ = cls.objects.update_or_create(
            url=normalised_url,
            defaults={"source": source, **defaults},
        )
        tags_to_apply = []
        if source:
            tags_to_apply.extend(source.get_default_tags())
        if tags:
            tags_to_apply.extend(tags)
        if tags_to_apply:
            existing_tag_ids = set(item.tagged_items.values_list("tag_id", flat=True))
            pending_tag_ids: set[int] = set()
            rows_to_add = []
            for tag in tags_to_apply:
                tag_id = getattr(tag, "pk", None)
                if (
                    not tag_id
                    or tag_id in existing_tag_ids
                    or tag_id in pending_tag_ids
                ):
                    continue
                rows_to_add.append(ExternalContentItemTag(content_object=item, tag=tag))
                pending_tag_ids.add(tag_id)

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


class RolePageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.RolePage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="role_page_tagged_items",
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
        "govuk.RolePage",
        "govuk.SkillsAZPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
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
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
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
        FieldPanel("author"),
        FieldPanel("body"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_free_text_heading_navigation"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]


class RolePage(Page):
    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
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
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
    )
    body = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )
    tags = ClusterTaggableManager(through="govuk.RolePageTag", blank=True)
    selected_roles = StreamField(
        [
            (
                "role",
                SnippetChooserBlock(
                    "govuk.GovukRole",
                    required=False,
                ),
            )
        ],
        blank=True,
        use_json_field=True,
        help_text="Select one or more roles to render on this page.",
    )

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        FieldPanel("body"),
        FieldPanel("selected_roles"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_free_text_heading_navigation"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]

    @classmethod
    def can_create_at(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_exist_under(parent)

    @staticmethod
    def _extract_role_id(value) -> int | None:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                parsed = int(stripped)
                if parsed > 0:
                    return parsed
        role_pk = getattr(value, "pk", None)
        if type(role_pk) is int and role_pk > 0:
            return role_pk
        if isinstance(value, dict):
            for key in ("value", "id", "pk"):
                extracted = RolePage._extract_role_id(value.get(key))
                if extracted:
                    return extracted
        return None

    def get_selected_role_ids(self) -> list[int]:
        role_ids: list[int] = []
        seen: set[int] = set()

        for block in self.selected_roles:
            role_id = self._extract_role_id(getattr(block, "value", None))
            if role_id and role_id not in seen:
                role_ids.append(role_id)
                seen.add(role_id)

        if not role_ids:
            for raw_block in getattr(self.selected_roles, "raw_data", []) or []:
                role_id = self._extract_role_id(raw_block)
                if role_id and role_id not in seen:
                    role_ids.append(role_id)
                    seen.add(role_id)
        return role_ids

    def get_selected_roles(self) -> list[GovukRole]:
        role_ids = self.get_selected_role_ids()
        if not role_ids:
            return []

        roles_by_id = {
            role.pk: role for role in GovukRole.objects.filter(pk__in=role_ids)
        }
        return [roles_by_id[role_id] for role_id in role_ids if role_id in roles_by_id]

    def get_role_sections(self) -> list[dict]:
        return [
            {
                "role": role,
                "levels": role.get_levels_with_skills(),
            }
            for role in self.get_selected_roles()
        ]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["role_sections"] = self.get_role_sections()
        return context


class SkillsAZPage(Page):
    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
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
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
    )
    body = RichTextField(blank=True)
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        FieldPanel("body"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_free_text_heading_navigation"),
    ]

    @classmethod
    def can_create_at(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_create_at(parent)

    @classmethod
    def can_exist_under(cls, parent):
        if not settings.FEATURE_FLAGS.get("SKILLS"):
            return False
        return super().can_exist_under(parent)

    def get_skill_sections(self) -> list[dict]:
        skills = list(GovukSkill.objects.all())
        skills.sort(
            key=lambda skill: (
                (skill.title or "").strip().lower(),
                (skill.slug or "").strip().lower(),
                skill.pk or 0,
            )
        )
        return [
            {
                "skill": skill,
                "level_rows": skill.get_level_rows(),
            }
            for skill in skills
        ]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["skill_sections"] = self.get_skill_sections()
        return context


class TagListingsPage(Page):
    class SortOrder(models.TextChoices):
        NEWEST_FIRST = "newest_first", "Newest first"
        ALPHABETICAL = "alphabetical_az", "Alphabetical (A-Z)"

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
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
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
    enable_source_display = models.BooleanField(
        default=False,
        verbose_name="Enable source display",
        help_text="Show source labels on listing cards.",
    )
    enable_tag_display = models.BooleanField(
        default=False,
        verbose_name="Enable tag display",
        help_text="Show tag labels on listing cards.",
    )
    show_private_cards_to_non_authenticated_users = models.BooleanField(
        default=False,
        verbose_name="Show private cards to non-authenticated users",
        help_text=(
            "Show cards for private pages to users who are not signed in. "
            "Opening those pages still requires signing in."
        ),
    )
    hide_last_updated = models.BooleanField(
        default=False,
        verbose_name="Hide last updated",
        help_text="Hide the last updated date below each listing.",
    )
    sort_order = models.CharField(
        max_length=20,
        choices=SortOrder.choices,
        default=SortOrder.NEWEST_FIRST,
        verbose_name="Sort order",
        help_text="Choose how listing cards are ordered.",
    )
    tags = ClusterTaggableManager(
        through="govuk.TagListingsPageTag",
        blank=True,
    )

    parent_page_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
    ]

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        InlinePanel("tagged_items", heading="Tags to list", label="Tag", min_num=1),
        FieldPanel("free_text"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_source_filter"),
        FieldPanel("enable_source_display"),
        FieldPanel("enable_tag_filter"),
        FieldPanel("enable_tag_display"),
        FieldPanel("show_private_cards_to_non_authenticated_users"),
        FieldPanel("hide_last_updated"),
        FieldPanel("sort_order"),
        FieldPanel("enable_free_text_heading_navigation"),
    ]

    def _configured_tag_ids(self) -> list[int]:
        return list(self.tags.values_list("id", flat=True))

    def _this_site_source_labels(self, *, request=None) -> tuple[str, str]:
        site_name = ""
        if request is not None:
            site = Site.find_for_request(request)
            if isinstance(site, Site):
                site_name = (site.site_name or "").strip()

        display_label = site_name or "This site"
        filter_label = f"{site_name} (this site)" if site_name else "This site"
        return display_label, filter_label

    def _external_listing_queryset(self, *, tag_ids: list[int], request=None):
        if not tag_ids:
            return ExternalContentItem.objects.none()
        queryset = ExternalContentItem.objects.filter(
            hidden=False, tags__id__in=tag_ids
        )
        if request is None or not request.user.is_authenticated:
            queryset = queryset.filter(private=False)
        return queryset.distinct().select_related("source").prefetch_related("tags")

    def _page_listing_querysets(self, *, tag_ids: list[int], request=None):
        if not tag_ids:
            return []

        page_sort_updated = Coalesce(
            "last_published_at",
            "latest_revision_created_at",
            "first_published_at",
        )
        page_querysets = [
            ContentPage.objects.live()
            .filter(tags__id__in=tag_ids)
            .annotate(sort_updated=page_sort_updated)
            .prefetch_related("tags", "view_restrictions")
            .distinct(),
            SectionPage.objects.live()
            .filter(tags__id__in=tag_ids)
            .annotate(sort_updated=page_sort_updated)
            .prefetch_related("tags", "view_restrictions")
            .distinct(),
            RolePage.objects.live()
            .filter(tags__id__in=tag_ids)
            .annotate(sort_updated=page_sort_updated)
            .prefetch_related("tags", "view_restrictions")
            .distinct(),
        ]
        is_authenticated = bool(request and request.user.is_authenticated)
        if (
            not is_authenticated
            and not self.show_private_cards_to_non_authenticated_users
        ):
            page_querysets = [queryset.public() for queryset in page_querysets]
        return page_querysets

    def _page_listing_items(
        self,
        *,
        tag_ids: list[int],
        selected_tag_id: int | None = None,
        request=None,
        this_site_source_label: str = "",
    ) -> list[dict]:
        page_querysets = self._page_listing_querysets(tag_ids=tag_ids, request=request)
        if not page_querysets:
            return []

        page_items: list[dict] = []
        for queryset in page_querysets:
            if selected_tag_id is not None:
                queryset = queryset.filter(tags__id=selected_tag_id)
            for page in queryset:
                page_items.append(
                    {
                        "id": page.id,
                        "url": page.url or page.url_path,
                        "title": page.hero_title or page.title,
                        "summary": page.hero_intro or page.search_description or "",
                        "source": (
                            {"name": this_site_source_label}
                            if this_site_source_label
                            else None
                        ),
                        "tags": [tag.name for tag in page.tags.all()],
                        "private": bool(page.view_restrictions.all()),
                        "metadata": {},
                        "updated_at": page.last_published_at or page.sort_updated,
                        "created_at": page.first_published_at,
                        "published_at": page.first_published_at,
                        "last_seen_at": page.last_published_at,
                        "sort_updated": page.sort_updated,
                    }
                )
        return page_items

    def _available_filter_tags(
        self, *, tag_ids: list[int], request=None
    ) -> list["GovukTag"]:
        if not tag_ids:
            return []

        available_tag_ids: set[int] = set(tag_ids)

        external_item_ids = list(
            self._external_listing_queryset(
                tag_ids=tag_ids, request=request
            ).values_list("id", flat=True)
        )
        if external_item_ids:
            available_tag_ids.update(
                ExternalContentItemTag.objects.filter(
                    content_object_id__in=external_item_ids
                ).values_list("tag_id", flat=True)
            )

        page_querysets = self._page_listing_querysets(tag_ids=tag_ids, request=request)
        for page_queryset, through_model in (
            (page_querysets[0], ContentPageTag),
            (page_querysets[1], SectionPageTag),
            (page_querysets[2], RolePageTag),
        ):
            page_ids = list(page_queryset.values_list("id", flat=True))
            if page_ids:
                available_tag_ids.update(
                    through_model.objects.filter(
                        content_object_id__in=page_ids
                    ).values_list("tag_id", flat=True)
                )

        return list(
            GovukTag.objects.filter(id__in=available_tag_ids).order_by("name", "slug")
        )

    def get_listing_queryset(
        self,
        *,
        selected_tag_id: int | None = None,
        selected_source_id: int | str | None = None,
        request=None,
    ) -> list[dict]:
        # Keep this as the single data-source entry point so external content and
        # tagged Wagtail pages remain filtered and sorted consistently.
        configured_tag_ids = self._configured_tag_ids()
        if not configured_tag_ids:
            return []

        selected_source_key = selected_source_id
        if isinstance(selected_source_key, str):
            selected_source_key = selected_source_key.strip()
            if not selected_source_key:
                selected_source_key = None
            elif selected_source_key != THIS_SITE_SOURCE_FILTER:
                if selected_source_key.isdigit():
                    selected_source_key = int(selected_source_key)
                else:
                    selected_source_key = None

        external_queryset = self._external_listing_queryset(
            tag_ids=configured_tag_ids,
            request=request,
        )
        if selected_tag_id is not None:
            external_queryset = external_queryset.filter(tags__id=selected_tag_id)
        external_queryset = external_queryset.annotate(
            sort_updated=Coalesce(
                "updated_at",
                "created_at",
                "published_at",
                "last_seen_at",
                "first_seen_at",
            )
        )
        if selected_source_key == THIS_SITE_SOURCE_FILTER:
            external_queryset = external_queryset.none()
        elif isinstance(selected_source_key, int):
            external_queryset = external_queryset.filter(source_id=selected_source_key)

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
                    "tags": [tag.name for tag in item.tags.all()],
                    "private": item.private,
                    "metadata": item.metadata or {},
                    "updated_at": item.updated_at,
                    "created_at": item.created_at,
                    "published_at": item.published_at,
                    "last_seen_at": item.last_seen_at,
                    "sort_updated": item.sort_updated,
                }
            )

        this_site_source_label, _ = self._this_site_source_labels(request=request)
        if selected_source_key in {None, THIS_SITE_SOURCE_FILTER}:
            listing_items.extend(
                self._page_listing_items(
                    tag_ids=configured_tag_ids,
                    selected_tag_id=selected_tag_id,
                    request=request,
                    this_site_source_label=this_site_source_label,
                )
            )

        if self.sort_order == self.SortOrder.ALPHABETICAL:
            listing_items.sort(
                key=lambda item: (
                    (item.get("title") or item.get("url") or "").strip().lower(),
                    item["id"],
                ),
            )
        else:
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

        configured_tag_ids = self._configured_tag_ids()
        available_tags = self._available_filter_tags(
            tag_ids=configured_tag_ids,
            request=request,
        )
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
        _, this_site_filter_label = self._this_site_source_labels(request=request)
        available_sources.append(
            {
                "id": THIS_SITE_SOURCE_FILTER,
                "label": this_site_filter_label,
            }
        )
        source_queryset = self._external_listing_queryset(
            tag_ids=configured_tag_ids,
            request=request,
        )
        if selected_tag_id is not None:
            source_queryset = source_queryset.filter(tags__id=selected_tag_id)
        source_rows = (
            source_queryset.exclude(source__isnull=True)
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

        selected_source_key: int | str | None = None
        if selected_source_id == THIS_SITE_SOURCE_FILTER:
            selected_source_key = THIS_SITE_SOURCE_FILTER
        elif selected_source_id:
            selected_source_key = int(selected_source_id)
        listing_items = self.get_listing_queryset(
            selected_tag_id=selected_tag_id,
            selected_source_id=selected_source_key,
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
    author = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional author name displayed above the main content.",
    )
    show_last_updated_date = models.BooleanField(
        default=False,
        verbose_name="Show last updated date",
        help_text="Show the page last updated date above the main content.",
    )
    show_page_content_metadata = models.BooleanField(
        default=False,
        verbose_name="Show page content metadata",
        help_text="Show page metadata above the main content.",
    )
    enable_tag_filter = models.BooleanField(
        default=False,
        verbose_name="Enable tag filter",
        help_text="Show a tag filter control above the cards.",
    )
    enable_tag_display = models.BooleanField(
        default=False,
        verbose_name="Enable tag display",
        help_text="Show tag labels on cards.",
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
                                            "image",
                                            ImageChooserBlock(
                                                required=False,
                                                help_text="Optional header image for this card.",
                                            ),
                                        ),
                                        (
                                            "image_fit",
                                            blocks.ChoiceBlock(
                                                choices=[
                                                    ("cover", "Cover"),
                                                    ("contain", "Contain"),
                                                ],
                                                default="cover",
                                                required=True,
                                                help_text=(
                                                    "How the header image should fit "
                                                    "inside the card."
                                                ),
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
                                            "link",
                                            LinkBlock(
                                                required=False,
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
                                max_num=60,
                                help_text="Add between 1 and 60 cards in this row.",
                            ),
                        ),
                    ],
                    icon="placeholder",
                    label="Row section",
                ),
            ),
        ],
        blank=True,
        help_text="Add one or more row sections. Each row can contain up to 60 cards.",
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
        "govuk.RolePage",
        "govuk.SkillsAZPage",
    ]
    subpage_types = [
        "govuk.ContentPage",
        "govuk.SectionPage",
        "govuk.TagListingsPage",
        "govuk.RolePage",
        "govuk.SkillsAZPage",
    ]

    content_panels = Page.content_panels + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        FieldPanel("rows"),
        FieldPanel("free_text"),
    ]

    settings_panels = Page.settings_panels + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_tag_filter"),
        FieldPanel("enable_tag_display"),
        FieldPanel("enable_free_text_heading_navigation"),
        InlinePanel("tagged_items", heading="Tags", label="Tag"),
    ]

    @staticmethod
    def _card_tag_items(card) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for tag in card.get("tags", []):
            if not tag:
                continue

            key = (getattr(tag, "slug", "") or getattr(tag, "key", "")).strip().lower()
            value = (getattr(tag, "name", "") or getattr(tag, "value", "")).strip()

            if not key and isinstance(value, str):
                key = slugify(value).strip().lower()
            if not value and key:
                value = key
            if not key or not value or key in seen:
                continue

            seen.add(key)
            items.append({"key": key, "value": value})
        return items

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)

        prepared_rows: list[dict] = []
        available_tags_by_key: dict[str, str] = {}
        for block in self.rows:
            if block.block_type != "row":
                continue

            prepared_cards: list[dict] = []
            for card in block.value.get("cards", []):
                tag_items = self._card_tag_items(card)
                tag_keys = {item["key"] for item in tag_items}
                for item in tag_items:
                    available_tags_by_key[item["key"]] = item["value"]
                prepared_cards.append(
                    {
                        "card": card,
                        "tag_keys": tag_keys,
                    }
                )

            prepared_rows.append(
                {
                    "heading": block.value.get("heading"),
                    "cards": prepared_cards,
                }
            )

        selected_tag = None
        selected_tag_key = ""
        if self.enable_tag_filter:
            selected_tag_key = (request.GET.get("tag") or "").strip().lower()
            if selected_tag_key not in available_tags_by_key:
                selected_tag_key = ""

            if selected_tag_key:
                selected_tag = {
                    "key": selected_tag_key,
                    "value": available_tags_by_key[selected_tag_key],
                }

        row_sections: list[dict] = []
        for row in prepared_rows:
            cards = [
                card_entry["card"]
                for card_entry in row["cards"]
                if not selected_tag_key or selected_tag_key in card_entry["tag_keys"]
            ]
            if cards:
                row_sections.append(
                    {
                        "heading": row["heading"],
                        "cards": cards,
                    }
                )

        context["available_tags"] = [
            {"key": key, "value": value}
            for key, value in sorted(
                available_tags_by_key.items(),
                key=lambda row: row[1].lower(),
            )
        ]
        context["selected_tag"] = selected_tag
        context["row_sections"] = row_sections
        return context


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
    "GovukSkill",
    "GovukRole",
    "PhaseBannerSettings",
    "RolePage",
    "SkillsAZPage",
    "SectionPage",
    "SectionPageTag",
    "TagListingsPage",
    "TagListingsPageTag",
]
