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
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.paginator import Paginator
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import Truncator, slugify
from modelcluster.contrib.taggit import ClusterTaggableManager
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from taggit.models import TagBase, TaggedItemBase
from wagtail import blocks
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.blocks import StructValue
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.contrib.table_block.blocks import TableBlock
from wagtail.fields import RichTextField, StreamField
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Orderable, Page, Site
from wagtail.snippets.blocks import SnippetChooserBlock

from govuk.utils import row_id_from_text

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
RELATED_ROLES_COUNT = 5
JOB_GRADE_CHOICES = (
    ("ao", "AO (Administrative Officer)"),
    ("eo", "EO (Executive Officer)"),
    ("heo", "HEO (Higher Executive Officer)"),
    ("seo", "SEO (Senior Executive Officer)"),
    ("g7", "G7 (Grade 7)"),
    ("g6", "G6 (Grade 6)"),
    ("scs1", "SCS 1 (Senior Civil Service 1)"),
    ("scs2", "SCS 2 (Senior Civil Service 2)"),
    ("scs3", "SCS 3 (Senior Civil Service 3)"),
)
JOB_GRADE_LABELS = dict(JOB_GRADE_CHOICES)
# Ascending seniority, used to order grades however they were authored.
JOB_GRADE_ORDER = {value: index for index, (value, _) in enumerate(JOB_GRADE_CHOICES)}
SCS_GRADE_CHOICES = tuple(
    (value, label) for value, label in JOB_GRADE_CHOICES if value.startswith("scs")
)
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


def page_settings_panels() -> list:
    """``Page.settings_panels``, minus scheduling while nothing can schedule.

    Wagtail's go-live and expiry dates do nothing by themselves: they need the
    ``publish_scheduled`` command run on a timer, and nothing in this service's
    deployment runs it -- no cron, no worker, no scheduled task in any of the
    three repos. An editor who sets a go-live date therefore gets a page that
    quietly never publishes, with no error to tell them so. Not offering the
    field is the honest version of that, and it leaves publishing as a thing
    someone does deliberately rather than a thing that was supposed to happen.

    ``SCHEDULED_PUBLISHING=true`` puts the panel back, and with it the "Edit
    schedule" toggle in the status side panel -- Wagtail shows that only when
    the form has a ``go_live_at`` field, which is this panel's doing.

    Read once at import, as panel definitions are. Changing the env var means a
    new container either way.
    """
    if getattr(settings, "SCHEDULED_PUBLISHING", False):
        return list(Page.settings_panels)
    return [
        panel
        for panel in Page.settings_panels
        if getattr(panel, "path", None) != "wagtail.admin.panels.PublishingPanel"
    ]


def content_page_content_panels() -> list:
    """``ContentPage``'s editing panels, framework content only on a framework.

    ContentPage is the page type every instance on this codebase builds with,
    so anything offered here is offered to every site. The welcome content is
    the Capability Framework's own -- role families, skill level definitions --
    and an editor on a site without the framework has nothing to put in it and
    no way to render it.

    The field stays on the model either way. This decides what the form shows,
    not what the database holds, so switching the flag needs no migration.
    """
    panels = list(Page.content_panels) + [
        FieldPanel("hero_title"),
        FieldPanel("hero_intro"),
        FieldPanel("author"),
        FieldPanel("body"),
        FieldPanel("body_blocks"),
    ]
    if settings.FEATURE_FLAGS.get("SKILLS"):
        panels.append(FieldPanel("framework_welcome_body"))
    return panels


def content_page_settings_panels() -> list:
    """``ContentPage``'s settings panels, framework switches only on a framework.

    The three framework switches each turn on a block of Capability Framework
    furniture -- the role side navigation, the site-wide changelog, the welcome
    layout. On a site without the framework they have nothing to show, and the
    wording that labels them comes from settings the admin does not register
    without the flag, so an editor there would be reading captions nobody on
    that site can change.

    Read once at import, as ``page_settings_panels`` is.
    """
    panels = page_settings_panels() + [
        FieldPanel("enable_hero_styling"),
        FieldPanel("enable_combined_service_navigation_and_hero_styling"),
        FieldPanel("show_last_updated_date"),
        FieldPanel("show_page_content_metadata"),
        FieldPanel("enable_free_text_heading_navigation"),
    ]
    if settings.FEATURE_FLAGS.get("SKILLS"):
        panels += [
            FieldPanel("show_role_navigation"),
            FieldPanel("show_framework_updates"),
            FieldPanel("show_framework_welcome"),
        ]
    panels.append(InlinePanel("tagged_items", heading="Tags", label="Tag"))
    return panels


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
    error_contact_link_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Who the error pages offer to put a reader in touch with, for "
            "example a team's name. Leave blank for no contact sentence."
        ),
    )
    error_contact_email = models.EmailField(
        blank=True,
        default="",
        help_text="Where the error pages' contact link points.",
    )
    error_contact_about = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "What the reader would be speaking to someone about, closing the "
            "contact sentence: 'if you need to speak to someone about the …'."
        ),
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
        FieldPanel("error_contact_link_text"),
        FieldPanel("error_contact_email"),
        FieldPanel("error_contact_about"),
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


class CapabilityFrameworkWordingSettings(BaseSiteSetting):
    """The words a role or skill page prints that no editor authored.

    Registered as a setting in ``wagtail_hooks``, alongside the skills and roles
    snippets and under the same feature flag: the only pages that read this
    wording are the ones that flag governs, so a service without them has no use
    for a form of 38 fields nothing on it prints.

    Headings, column headings, sentence lead-ins and the two empty states were
    written into the templates, which put them out of reach of the people who
    own the framework's language: changing "Roles that share x skills" meant a
    release. Every default here is the wording the templates already carried,
    so an instance that has never opened this form reads exactly as before.

    ``{role}`` stands for the role's name, lowercased for mid-sentence use the
    way the framework writes it, and is filled in per role.
    """

    ROLE_PLACEHOLDER = "{role}"
    LEVEL_PLACEHOLDER = "{level}"
    ORDINAL_PLACEHOLDER = "{ordinal}"
    ARTICLE_PLACEHOLDER = "{article}"
    FAMILY_PLACEHOLDER = "{family}"
    COUNT_PLACEHOLDER = "{count}"
    FIRST_PLACEHOLDER = "{first}"
    LAST_PLACEHOLDER = "{last}"
    LEVELS_RANGE_PLACEHOLDER = "{levels_range}"

    contents_heading = models.CharField(
        max_length=255,
        default="Contents",
        help_text="Heading over the in-page contents links.",
    )
    last_updated_prefix = models.CharField(
        max_length=255,
        default="Last updated",
        help_text="Shown before the date the role was last changed.",
    )
    see_all_updates_link_text = models.CharField(
        max_length=255,
        default="See all updates",
        help_text="Wording of the link down to the updates section.",
    )

    scs_context_text = models.CharField(
        max_length=255,
        default="A specific {role} job can vary depending on the",
        help_text=(
            "Senior Civil Service roles only. Shown before the link to the "
            "context and challenges page. Use {role} for the role name."
        ),
    )
    scs_context_link_text = models.CharField(
        max_length=255,
        default="context and challenges in your organisation",
        help_text=(
            "Wording of that link. A full stop follows it. Shown as plain "
            "text where the page it points at is missing."
        ),
    )
    scs_skills_heading = models.CharField(
        max_length=255,
        default="Skills for {role}",
        help_text="Heading over a Senior Civil Service role's skills.",
    )
    scs_skills_intro = models.CharField(
        max_length=255,
        default="The {role} role will need to use digital and data skills to:",
        help_text="Sentence introducing the two points below it.",
    )
    scs_skills_leadership_point = models.CharField(
        max_length=255,
        default="be an effective digital and data leader",
        help_text="First of those points.",
    )
    scs_skills_context_point_text = models.CharField(
        max_length=255,
        default="operate in different contexts, depending",
        help_text="Second point, up to the link.",
    )
    scs_skills_context_point_link_text = models.CharField(
        max_length=255,
        default="on the context and challenges in your organisation",
        help_text="Rest of that point, linked to the context and challenges page.",
    )
    scs_skills_table_skill_heading = models.CharField(
        max_length=255,
        default="Skill",
        help_text="First column of the Senior Civil Service skills table.",
    )
    scs_skills_table_description_heading = models.CharField(
        max_length=255,
        default="Description, including examples of leadership",
        help_text="Second column of that table.",
    )
    scs_leadership_examples_heading = models.CharField(
        max_length=255,
        default="Examples of leadership using this skill:",
        help_text="Shown in that table above a skill's leadership points.",
    )

    role_grades_text = models.CharField(
        max_length=255,
        default="This role is often performed at the",
        help_text=(
            "Shown before the link to the job grades page, above the grades a "
            "Senior Civil Service role is done at."
        ),
    )
    level_grades_text = models.CharField(
        max_length=255,
        default="This role level is most often performed at the",
        help_text="The same sentence for a role level rather than a whole role.",
    )
    job_grades_link_text = models.CharField(
        max_length=255,
        default="Civil Service job grade",
        help_text=(
            "Wording of that link. Shown as plain text where the job grades "
            "page is missing."
        ),
    )
    grades_text_after = models.CharField(
        max_length=255,
        default="of:",
        help_text="Wording after that link, ending the sentence.",
    )

    role_levels_heading = models.CharField(
        max_length=255,
        default="{role} role levels",
        help_text=(
            "Heading over a role's levels. Here {role} keeps the role title's "
            "capitals, this being the start of a heading."
        ),
    )
    level_skills_table_skill_heading = models.CharField(
        max_length=255,
        default="Skill",
        help_text="First column of a role level's skills table.",
    )
    level_skills_table_description_heading = models.CharField(
        max_length=255,
        default="Description",
        help_text="Second column of that table.",
    )
    skill_level_prefix = models.CharField(
        max_length=255,
        default="Level:",
        help_text="Shown before the level a role level needs a skill at.",
    )
    skill_level_scale_text = models.CharField(
        max_length=255,
        default="{level} is the {ordinal} of four ascending skill levels",
        help_text=(
            "Read aloud in place of the progress bar, which is decorative. "
            "Use {level} for the level and {ordinal} for first to fourth."
        ),
    )
    skill_points_intro = models.CharField(
        max_length=255,
        default="You can:",
        help_text="Sentence the points under a skill read on from.",
    )

    related_roles_heading = models.CharField(
        max_length=255,
        default="Roles that share {role} skills",
        help_text="Heading over the roles sharing skills with this one.",
    )
    related_roles_table_role_heading = models.CharField(
        max_length=255,
        default="Role",
        help_text="First column of that table.",
    )
    related_roles_table_skills_heading = models.CharField(
        max_length=255,
        default="Shared skills",
        help_text="Second column of that table.",
    )
    progression_scs_roles_heading = models.CharField(
        max_length=255,
        default="Senior Civil Service roles that {role} could lead to",
        help_text="Heading over the senior roles this role leads on to.",
    )
    progression_roles_heading = models.CharField(
        max_length=255,
        default="Roles that could lead to {role}",
        help_text="Heading over the roles that lead into this one.",
    )

    updates_heading = models.CharField(
        max_length=255,
        default="Updates",
        help_text="Heading over a role's change history.",
    )
    published_prefix = models.CharField(
        max_length=255,
        default="Published",
        help_text="Shown before the date the role was first published.",
    )
    no_roles_message = models.CharField(
        max_length=255,
        default="No roles selected.",
        help_text="Shown on a role page with no role chosen and nothing written.",
    )

    scs_skill_label = models.CharField(
        max_length=255,
        default="Senior Civil Service",
        help_text="Shown beside a skill that only Senior Civil Service roles use.",
    )
    skill_leadership_examples_heading = models.CharField(
        max_length=255,
        default="Examples of leadership using this skill",
        help_text="Heading over a skill's leadership points on the skills page.",
    )
    skill_levels_table_level_heading = models.CharField(
        max_length=255,
        default="Skill level",
        help_text="First column of a skill's levels table.",
    )
    skill_levels_table_description_heading = models.CharField(
        max_length=255,
        default="Description",
        help_text="Second column of that table.",
    )
    skill_level_no_description_message = models.CharField(
        max_length=255,
        default="No description provided.",
        help_text="Shown where a skill level has had no points written for it.",
    )
    skill_roles_heading = models.CharField(
        max_length=255,
        default="Roles that require this skill",
        help_text="Heading over the roles that need a skill.",
    )
    no_skills_message = models.CharField(
        max_length=255,
        default="No skills found.",
        help_text="Shown on the skills page while no skills exist.",
    )

    overview_heading_text = models.CharField(
        max_length=255,
        default="What {article} {role} does",
        help_text="Heading over a role's description. {article} is a or an.",
    )
    role_lead_text = models.CharField(
        max_length=500,
        default=(
            "Find out what {article} {role} in government does and the skills "
            "you need to do the role at each level."
        ),
        help_text="The sentence under a role's heading.",
    )
    scs_role_lead_text = models.CharField(
        max_length=500,
        default=(
            "Find out what {article} {role} in the Senior Civil Service does "
            "and the skills you need to do the role."
        ),
        help_text="The sentence under a Senior Civil Service role's heading.",
    )
    role_levels_opening_one = models.CharField(
        max_length=255,
        default="There is one {role} role level.",
        help_text="Opens the levels section for a role with a single level.",
    )
    role_levels_opening_many = models.CharField(
        max_length=255,
        default="There are {count} {role} role levels{levels_range}.",
        help_text=(
            "Opens the levels section. {levels_range} is the from-to clause "
            "below, or nothing where the levels are unnamed."
        ),
    )
    role_levels_range_text = models.CharField(
        max_length=255,
        default=", from {first} to {last}",
        help_text="The from-to clause naming the first and last role levels.",
    )
    role_levels_described_one = models.CharField(
        max_length=255,
        default=(
            "The typical responsibilities and skills for this role level are "
            "described below."
        ),
        help_text="Follows the opening for a role with a single level.",
    )
    role_levels_described_many = models.CharField(
        max_length=255,
        default=(
            "The typical responsibilities and skills for each role level are "
            "described in the sections below."
        ),
        help_text="Follows the opening for a role with several levels.",
    )
    role_levels_purpose_text = models.CharField(
        max_length=500,
        default=(
            "You can use this to identify the skills you need to progress in "
            "your career, or simply to learn more about each role in the "
            "Government Digital and Data profession."
        ),
        help_text="Closes the sentence under the role levels heading.",
    )
    show_all_updates_link_text = models.CharField(
        max_length=100,
        default="+ show all updates",
        help_text="The link that opens the home page's collapsed update history.",
    )
    hide_all_updates_link_text = models.CharField(
        max_length=100,
        default="- hide all updates",
        help_text="The link that closes the home page's update history.",
    )
    further_resources_heading = models.CharField(
        max_length=255,
        default="Further resources",
        help_text="Heading over the navigation's non-role pages.",
    )
    role_family_group_title = models.CharField(
        max_length=255,
        default="{family} roles",
        help_text=(
            "Title of each family's navigation group and home page section. "
            "{family} is the family's name from the role records."
        ),
    )
    breadcrumb_home_label = models.CharField(
        max_length=100,
        default="Home",
        help_text="The first entry of the narrow-screen breadcrumb.",
    )

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("contents_heading"),
                FieldPanel("last_updated_prefix"),
                FieldPanel("see_all_updates_link_text"),
                FieldPanel("overview_heading_text"),
                FieldPanel("role_lead_text"),
                FieldPanel("scs_role_lead_text"),
            ],
            heading="Above a role",
        ),
        MultiFieldPanel(
            [
                FieldPanel("scs_context_text"),
                FieldPanel("scs_context_link_text"),
                FieldPanel("scs_skills_heading"),
                FieldPanel("scs_skills_intro"),
                FieldPanel("scs_skills_leadership_point"),
                FieldPanel("scs_skills_context_point_text"),
                FieldPanel("scs_skills_context_point_link_text"),
                FieldPanel("scs_skills_table_skill_heading"),
                FieldPanel("scs_skills_table_description_heading"),
                FieldPanel("scs_leadership_examples_heading"),
            ],
            heading="Senior Civil Service roles",
        ),
        MultiFieldPanel(
            [
                FieldPanel("role_grades_text"),
                FieldPanel("level_grades_text"),
                FieldPanel("job_grades_link_text"),
                FieldPanel("grades_text_after"),
            ],
            heading="Job grades",
        ),
        MultiFieldPanel(
            [
                FieldPanel("role_levels_heading"),
                FieldPanel("level_skills_table_skill_heading"),
                FieldPanel("level_skills_table_description_heading"),
                FieldPanel("skill_level_prefix"),
                FieldPanel("skill_level_scale_text"),
                FieldPanel("skill_points_intro"),
            ],
            heading="Role levels",
        ),
        MultiFieldPanel(
            [
                FieldPanel("role_levels_opening_one"),
                FieldPanel("role_levels_opening_many"),
                FieldPanel("role_levels_range_text"),
                FieldPanel("role_levels_described_one"),
                FieldPanel("role_levels_described_many"),
                FieldPanel("role_levels_purpose_text"),
            ],
            heading="Role levels introduction",
        ),
        MultiFieldPanel(
            [
                FieldPanel("related_roles_heading"),
                FieldPanel("related_roles_table_role_heading"),
                FieldPanel("related_roles_table_skills_heading"),
                FieldPanel("progression_scs_roles_heading"),
                FieldPanel("progression_roles_heading"),
            ],
            heading="Related roles and career paths",
        ),
        MultiFieldPanel(
            [
                FieldPanel("updates_heading"),
                FieldPanel("published_prefix"),
                FieldPanel("show_all_updates_link_text"),
                FieldPanel("hide_all_updates_link_text"),
                FieldPanel("no_roles_message"),
            ],
            heading="Updates and empty states",
        ),
        MultiFieldPanel(
            [
                FieldPanel("further_resources_heading"),
                FieldPanel("role_family_group_title"),
                FieldPanel("breadcrumb_home_label"),
            ],
            heading="Navigation",
        ),
        MultiFieldPanel(
            [
                FieldPanel("scs_skill_label"),
                FieldPanel("skill_leadership_examples_heading"),
                FieldPanel("skill_levels_table_level_heading"),
                FieldPanel("skill_levels_table_description_heading"),
                FieldPanel("skill_level_no_description_message"),
                FieldPanel("skill_roles_heading"),
                FieldPanel("no_skills_message"),
            ],
            heading="Skills page",
        ),
    ]

    class Meta:
        verbose_name = "Capability framework wording"
        verbose_name_plural = "Capability framework wording"

    # Filled in per role, so a page holding several does not have to choose one.
    ROLE_FIELDS = (
        "scs_context_text",
        "scs_skills_heading",
        "scs_skills_intro",
        "related_roles_heading",
        "progression_scs_roles_heading",
        "progression_roles_heading",
    )

    def for_role(self, *, display_role_name: str, role_title: str) -> dict[str, str]:
        """This role's wording, with its name already in place.

        The headings come back here rather than being substituted in the
        template because the in-page contents links repeat them, and the two
        have to say the same thing for the link to make sense.
        """
        wording = {
            name: getattr(self, name).replace(
                self.ROLE_PLACEHOLDER, display_role_name
            )
            for name in self.ROLE_FIELDS
        }
        # A heading opens with the role, so it keeps the capitals in a title
        # like "Development operations (DevOps) engineer" rather than the
        # lowercased form the mid-sentence wording uses.
        wording["role_levels_heading"] = self.role_levels_heading.replace(
            self.ROLE_PLACEHOLDER, role_title
        )
        return wording

    def family_group_title(self, family: str) -> str:
        """A family's navigation group title, used as the home page section
        heading and the narrow-screen breadcrumb entry alike, so the anchor
        the breadcrumb points at always matches the heading it lands on."""
        return self.role_family_group_title.replace(self.FAMILY_PLACEHOLDER, family)

    def skill_level_scale(self, *, label: str, ordinal: str) -> str:
        """What a screen reader is given in place of the progress bar."""
        return self.skill_level_scale_text.replace(
            self.LEVEL_PLACEHOLDER, label
        ).replace(self.ORDINAL_PLACEHOLDER, ordinal)


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
    is_senior_civil_service = models.BooleanField(
        default=False,
        verbose_name="Senior Civil Service skill",
        help_text=(
            "Senior Civil Service skills describe leadership rather than "
            "ascending proficiency levels."
        ),
    )
    leadership_points = StreamField(
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
        verbose_name="Examples of leadership",
        help_text="Examples of leadership using this skill, for Senior Civil Service skills.",
    )

    panels = [
        FieldPanel("slug"),
        FieldPanel("title"),
        FieldPanel("body"),
        FieldPanel("is_senior_civil_service"),
        FieldPanel("awareness_points"),
        FieldPanel("working_points"),
        FieldPanel("practitioner_points"),
        FieldPanel("expert_points"),
        FieldPanel("leadership_points"),
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

    def get_changelog(self) -> dict:
        """Published entries for this skill, newest first, with key dates.

        Filtered in Python rather than with .filter(), which would go back to
        the database and waste the prefetch the skills A to Z does for all 185
        skills at once.
        """
        entries = [entry for entry in self.changelog_entries.all() if entry.live]
        return {"entries": entries, **_changelog_dates(entries)}

    def get_roles_requiring_skill(self) -> list["GovukRole"]:
        """Roles that require this skill at any level, sorted by title."""
        return GovukRole.roles_by_skill_id().get(self.pk, [])

    def get_leadership_points(self) -> list[str]:
        """Examples of leadership, for Senior Civil Service skills."""
        return [
            entry["value"]
            for entry in self._normalised_stream_points(self.leadership_points)
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
    family = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Role family used to group roles, for example Data.",
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
                            "grades",
                            blocks.ListBlock(
                                blocks.ChoiceBlock(
                                    required=False,
                                    choices=JOB_GRADE_CHOICES,
                                ),
                                required=False,
                                label="Indicative Civil Service job grades",
                                help_text=(
                                    "Grades this role level is most often "
                                    "performed at. Leave empty to hide the "
                                    "sentence, as management-track levels do."
                                ),
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
    is_senior_civil_service = models.BooleanField(
        default=False,
        verbose_name="Senior Civil Service role",
        help_text=(
            "Senior Civil Service roles have no role levels. Their skills are "
            "listed flat, with examples of leadership."
        ),
    )
    scs_grades = StreamField(
        [("grade", blocks.ChoiceBlock(required=False, choices=SCS_GRADE_CHOICES))],
        blank=True,
        use_json_field=True,
        verbose_name="Indicative Senior Civil Service grades",
        help_text="Grades this role is most often performed at.",
    )
    scs_skills = StreamField(
        [
            (
                "skill",
                SnippetChooserBlock("govuk.GovukSkill", required=False),
            )
        ],
        blank=True,
        use_json_field=True,
        verbose_name="Senior Civil Service skills",
        help_text="Skills required by this role, with no proficiency level.",
    )
    roles_that_could_lead_here = StreamField(
        [("role", SnippetChooserBlock("govuk.GovukRole", required=False))],
        blank=True,
        use_json_field=True,
        verbose_name="Roles that could lead to this role",
        help_text=(
            "Roles someone might do before this one. Curated rather than "
            "derived from shared skills, and listed in the order given."
        ),
    )

    panels = [
        FieldPanel("slug"),
        FieldPanel("title"),
        FieldPanel("family"),
        FieldPanel("body"),
        FieldPanel("levels"),
        FieldPanel("is_senior_civil_service"),
        FieldPanel("scs_grades"),
        FieldPanel("scs_skills"),
        FieldPanel("roles_that_could_lead_here"),
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

    @staticmethod
    def _extract_skill_id(value) -> int | None:
        if type(value) is int and value > 0:
            return value
        if isinstance(value, str):
            parsed = row_id_from_text(value)
            if parsed is not None and parsed > 0:
                return parsed
        skill_pk = getattr(value, "pk", None)
        if type(skill_pk) is int and skill_pk > 0:
            return skill_pk
        return None

    def get_scs_skills(self) -> list[dict]:
        """Skills for a Senior Civil Service role, with leadership examples."""
        skills: list[dict] = []
        seen: set[int] = set()
        for block in self.scs_skills:
            skill = getattr(block, "value", None)
            if skill is None or getattr(skill, "pk", None) in seen:
                continue
            seen.add(skill.pk)
            skills.append(
                {"skill": skill, "leadership_points": skill.get_leadership_points()}
            )
        return skills

    def get_scs_grade_labels(self) -> list[str]:
        return self._grade_labels(self.scs_grades)

    def get_roles_that_could_lead_here(self) -> list["GovukRole"]:
        """The curated career path into this role, in the order given.

        Unlike ``get_related_roles`` this is not inferred from shared skills:
        the framework lists particular roles a person might come from, which
        is a judgement the content team makes.
        """
        roles: list[GovukRole] = []
        seen: set[int] = set()
        for block in self.roles_that_could_lead_here:
            role = getattr(block, "value", None)
            role_pk = getattr(role, "pk", None)
            if role is None or role_pk in seen or role_pk == self.pk:
                continue
            seen.add(role_pk)
            roles.append(role)
        return roles

    @classmethod
    def senior_roles_by_source_role_id(cls) -> dict[int, list["GovukRole"]]:
        """Map each role id to the Senior Civil Service roles it could lead to.

        The content team authors this the other way round, as the roles that
        could lead to a senior one, so the only way to the framework's
        "Senior Civil Service roles that X could lead to" is to turn the
        mapping around. Built in one pass, as ``roles_by_skill_id`` is.

        Read from the raw stream rather than the resolved snippets, the way
        ``get_skill_ids`` reads levels, because every role page builds this and
        resolving each senior role's chooser blocks costs a query apiece.

        The framework's own ordering is not recoverable from the reverse
        mapping, so the roles are listed by title.
        """
        index: dict[int, list[GovukRole]] = {}
        for senior_role in cls.objects.filter(is_senior_civil_service=True):
            seen: set[int] = set()
            raw_blocks = (
                getattr(senior_role.roles_that_could_lead_here, "raw_data", None) or []
            )
            for raw_block in raw_blocks:
                if not isinstance(raw_block, dict) or raw_block.get("type") != "role":
                    continue
                source_id = RolePage._extract_role_id(raw_block.get("value"))
                if not source_id or source_id in seen or source_id == senior_role.pk:
                    continue
                seen.add(source_id)
                index.setdefault(source_id, []).append(senior_role)
        for roles in index.values():
            roles.sort(key=lambda role: (role.title or "").strip().lower())
        return index

    def get_skill_ids(self) -> set[int]:
        """Distinct ids of skills required at any level of this role."""
        skill_ids: set[int] = set()
        for block in self.scs_skills:
            skill = getattr(block, "value", None)
            skill_id = self._extract_skill_id(skill)
            if skill_id:
                skill_ids.add(skill_id)

        raw_levels = getattr(self.levels, "raw_data", None)
        if raw_levels:
            for raw_level in raw_levels:
                if not isinstance(raw_level, dict) or raw_level.get("type") != "level":
                    continue
                raw_skills = (raw_level.get("value") or {}).get("skills") or []
                for raw_entry in raw_skills:
                    if not isinstance(raw_entry, dict):
                        continue
                    # ListBlock items appear either as plain struct dicts or
                    # wrapped as {"type": "item", "value": {...}}.
                    entry_value = raw_entry.get("value") if "skill" not in raw_entry else raw_entry
                    if not isinstance(entry_value, dict):
                        continue
                    skill_id = self._extract_skill_id(entry_value.get("skill"))
                    if skill_id:
                        skill_ids.add(skill_id)
            return skill_ids

        for level_block in self.levels:
            if level_block.block_type != "level":
                continue
            for skill_requirement in level_block.value.get("skills") or []:
                skill_id = self._extract_skill_id(skill_requirement.get("skill"))
                if skill_id:
                    skill_ids.add(skill_id)
        return skill_ids

    def get_related_roles(self, count: int = RELATED_ROLES_COUNT) -> list[dict]:
        """Other roles sharing skills with this one, most shared skills first.

        Mirrors the DDaT Capability Framework behaviour: ordered by number of
        shared skills descending then title, capped at ``count`` (the Strapi
        site used a ``relatedRolesCount`` global setting defaulting to 5).
        """
        own_skill_ids = self.get_skill_ids()
        if not own_skill_ids:
            return []

        matches: list[tuple[GovukRole, set[int]]] = []
        for role in type(self).objects.exclude(pk=self.pk):
            shared_ids = own_skill_ids & role.get_skill_ids()
            if shared_ids:
                matches.append((role, shared_ids))

        matches.sort(
            key=lambda match: (-len(match[1]), (match[0].title or "").strip().lower())
        )
        matches = matches[:count]

        skills_by_id = GovukSkill.objects.in_bulk(
            {skill_id for _, shared_ids in matches for skill_id in shared_ids}
        )
        return [
            {
                "role": role,
                "shared_skills": sorted(
                    (
                        skills_by_id[skill_id]
                        for skill_id in shared_ids
                        if skill_id in skills_by_id
                    ),
                    key=lambda skill: (skill.title or "").strip().lower(),
                ),
            }
            for role, shared_ids in matches
        ]

    def get_changelog(self) -> dict:
        """Published entries for this role, newest first, with key dates."""
        entries = list(self.changelog_entries.filter(live=True))
        return {"entries": entries, **_changelog_dates(entries)}

    @classmethod
    def roles_by_skill_id(cls) -> dict[int, list["GovukRole"]]:
        """Map each skill id to the roles requiring it, sorted by role title.

        Built in a single pass so pages listing many skills do not inspect
        every role's StreamField repeatedly.
        """
        index: dict[int, list[GovukRole]] = {}
        for role in cls.objects.all():
            for skill_id in role.get_skill_ids():
                index.setdefault(skill_id, []).append(role)
        for roles in index.values():
            roles.sort(key=lambda role: (role.title or "").strip().lower())
        return index

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
                    "grades": self._grade_labels(level_value.get("grades")),
                    "skills": skill_rows,
                }
            )

        return role_levels

    @staticmethod
    def _grade_labels(raw_grades) -> list[str]:
        """Labels for a level's indicative grades, ordered by seniority."""
        values = {
            str(getattr(raw_grade, "value", raw_grade) or "").strip()
            for raw_grade in (raw_grades or [])
        }
        return [
            JOB_GRADE_LABELS[value]
            for value in sorted(
                values & JOB_GRADE_LABELS.keys(), key=JOB_GRADE_ORDER.get
            )
        ]

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


class GovukChangelogEntry(models.Model):
    """A dated note describing a change to the framework.

    Entries with no role or skill are site-wide and appear on the framework
    home page; entries attached to a role or skill appear in the "Updates"
    section of the relevant page.
    """

    date = models.DateField(
        db_index=True,
        help_text="Date the change was published.",
    )
    role = models.ForeignKey(
        "govuk.GovukRole",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="changelog_entries",
        help_text="Leave blank for a site-wide update.",
    )
    skill = models.ForeignKey(
        "govuk.GovukSkill",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="changelog_entries",
        help_text="Leave blank for a site-wide update.",
    )
    change_type = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Optional label, for example New role or Skills updated.",
    )
    note = RichTextField(
        features=["bold", "italic", "link", "ul", "ol"],
        help_text="What changed.",
    )
    live = models.BooleanField(
        default=True,
        verbose_name="Published",
        help_text="Unpublish to hide this entry without deleting it.",
    )

    panels = [
        FieldPanel("date"),
        FieldPanel("role"),
        FieldPanel("skill"),
        FieldPanel("change_type"),
        FieldPanel("note"),
        FieldPanel("live"),
    ]

    class Meta:
        verbose_name = "Changelog entry"
        verbose_name_plural = "Changelog entries"
        ordering = ["-date", "pk"]

    def clean(self):
        super().clean()
        if self.role_id and self.skill_id:
            raise ValidationError(
                "Choose either a role or a skill for this entry, not both."
            )

    def __str__(self) -> str:
        subject = self.role or self.skill or "Framework"
        return f"{subject} - {self.date}"


def site_wide_changelog() -> dict:
    """Changelog entries about the framework rather than one role or skill."""
    entries = list(
        GovukChangelogEntry.objects.filter(
            role__isnull=True, skill__isnull=True, live=True
        )
    )
    return {"entries": entries, **_changelog_dates(entries)}


def _changelog_dates(entries) -> dict[str, object]:
    """First and last publication dates for a set of changelog entries."""
    dates = sorted(entry.date for entry in entries if entry.date)
    if not dates:
        return {"published_date": None, "last_updated_date": None}
    return {"published_date": dates[0], "last_updated_date": dates[-1]}


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
            parsed = row_id_from_text(value)
            if parsed is not None and parsed > 0:
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


FRAMEWORK_WELCOME_RICH_TEXT_FEATURES = ["bold", "italic", "link", "ul", "ol"]


class FrameworkWelcomeSectionValue(StructValue):
    def anchor_id(self) -> str:
        """The section's link target, so the contents list and the heading agree."""
        return slugify(self.get("anchor") or self.get("heading") or "")


class FrameworkWelcomeSectionBlock(blocks.StructBlock):
    """A heading and its prose."""

    heading = blocks.CharBlock(
        max_length=255,
        help_text="Section heading, for example How to use this framework.",
    )
    level = blocks.ChoiceBlock(
        choices=[
            ("h2", "Main section, listed in the contents"),
            ("h3", "Sub-section, not listed in the contents"),
        ],
        default="h2",
        help_text="Main sections appear in the contents list at the top of the page.",
    )
    anchor = blocks.CharBlock(
        required=False,
        max_length=255,
        help_text=(
            "Optional link target, for example capability-assessments. Defaults to "
            "the heading. Set one to keep existing links working if the heading is "
            "reworded."
        ),
    )
    body = blocks.RichTextBlock(
        features=FRAMEWORK_WELCOME_RICH_TEXT_FEATURES,
        help_text="The section's prose.",
    )

    class Meta:
        icon = "doc-full"
        label = "Section"
        value_class = FrameworkWelcomeSectionValue
        template = "blocks/framework_welcome_section.html"


class SkillLevelBlock(blocks.StructBlock):
    name = blocks.CharBlock(
        max_length=100,
        help_text="Level name, for example Awareness.",
    )
    filled_segments = blocks.IntegerBlock(
        min_value=1,
        max_value=4,
        default=1,
        help_text=(
            "How many of the 4 progress bar segments are filled, so awareness is 1 "
            "and expert is 4."
        ),
    )
    description = blocks.RichTextBlock(
        features=FRAMEWORK_WELCOME_RICH_TEXT_FEATURES,
        help_text="What someone at this level can do.",
    )

    class Meta:
        icon = "list-ul"
        label = "Skill level"


class SkillLevelDefinitionsBlock(blocks.StructBlock):
    """The skill level table, with the progress bars a rich text field would strip."""

    caption = blocks.CharBlock(
        max_length=255,
        default="Skill level definitions",
        help_text="Table caption, read out by screen readers.",
    )
    level_column_heading = blocks.CharBlock(
        max_length=255,
        default="Skill level definitions",
    )
    meaning_column_heading = blocks.CharBlock(
        max_length=255,
        default="What the level means",
    )
    levels = blocks.ListBlock(
        SkillLevelBlock(),
        help_text="One row per level, in ascending order.",
    )

    class Meta:
        icon = "table"
        label = "Skill level definitions"
        template = "blocks/framework_welcome_skill_levels.html"


class SectionBreakBlock(blocks.StaticBlock):
    class Meta:
        icon = "horizontalrule"
        label = "Section break"
        admin_text = "A horizontal rule across the page."
        template = "blocks/framework_welcome_section_break.html"


class InsetTextBlock(blocks.RichTextBlock):
    class Meta:
        icon = "warning"
        label = "Inset text"
        template = "blocks/framework_welcome_inset_text.html"


class GovukTableBlock(TableBlock):
    """A table an editor builds in a grid, rendered as a GOV.UK table.

    Wagtail's rich text has no table feature, so before this the only way to
    put one on a content page was to hand-write the HTML into a raw HTML
    embed. That is not a formatting option a content designer has, which is
    what left "tables can be added to the page" as the last unticked box on
    CS32-3527 while everything around it was done.

    Cells hold text, not HTML: the renderer stays on Wagtail's default, so
    whatever is typed is escaped. A table is a place a paste from a document
    would otherwise carry markup straight onto a public page.
    """

    def __init__(self, *args, table_options=None, **kwargs):
        # Wagtail hands handsontable whatever LANGUAGE_CODE is, and ours is
        # en-gb. The vendored handsontable 6.2.2 ships one locale, en-US, so
        # it logs a console error and falls back to it anyway. The strings
        # this picks are the grid's own context menu ("Insert row above"),
        # where the two spellings do not differ. Naming the locale it has
        # keeps the admin console clean.
        table_options = {"language": "en-US", **(table_options or {})}
        super().__init__(*args, table_options=table_options, **kwargs)

    class Meta:
        icon = "table"
        label = "Table"
        template = "blocks/govuk_table.html"
        help_text = (
            "Right-click a cell to add or remove rows and columns. Give the "
            "table a caption: it is how somebody using a screen reader knows "
            "what the table is before reading it."
        )


class ContentBodyBlock(blocks.StreamBlock):
    """Prose and tables, in whatever order the page needs them.

    ``ContentPage.body`` is a rich text field and stays one -- 67 pages of
    live content are stored in it, and the verified export was taken with it
    that shape. This renders after it, so an editor adding a table to an
    existing page changes nothing about the page's existing text.

    A page that needs a table part-way through moves its body text into a Text
    block here, which offers the same formatting the body field does.
    """

    # No features argument, so this offers exactly what the body field above
    # offers -- both take the default set, which the rich text hooks extend
    # with the GOV.UK button, start button, inset text and raw HTML.
    text = blocks.RichTextBlock(label="Text", icon="pilcrow")
    table = GovukTableBlock()

    class Meta:
        required = False


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
    body_blocks = StreamField(
        ContentBodyBlock(),
        blank=True,
        use_json_field=True,
        verbose_name="Tables and further content",
        help_text=(
            "Shown after the body above. To put a table part-way through a "
            "page, move the body text into a Text block here and add the "
            "table between the blocks."
        ),
    )
    enable_free_text_heading_navigation = models.BooleanField(
        default=False,
        verbose_name="Enable sidebar heading navigation",
        help_text="Show free text in a two-thirds and one-third layout with an automatic clickable heading list.",
    )
    show_role_navigation = models.BooleanField(
        default=False,
        verbose_name="Show role navigation",
        help_text="Show the list of roles grouped by family alongside the page, as the role pages do.",
    )
    show_framework_updates = models.BooleanField(
        default=False,
        verbose_name="Show framework updates",
        help_text=(
            "Show the changelog entries that are not tied to a single role or "
            "skill, with a last updated date above the page content."
        ),
    )
    show_framework_welcome = models.BooleanField(
        default=False,
        verbose_name="Show framework welcome content",
        help_text=(
            "Show the framework's welcome text, with a contents list and the "
            "roles grouped by family for narrow screens."
        ),
    )
    framework_welcome_body = StreamField(
        [
            ("section", FrameworkWelcomeSectionBlock()),
            ("skill_level_definitions", SkillLevelDefinitionsBlock()),
            ("section_break", SectionBreakBlock()),
            (
                "inset_text",
                InsetTextBlock(features=FRAMEWORK_WELCOME_RICH_TEXT_FEATURES),
            ),
        ],
        blank=True,
        use_json_field=True,
        verbose_name="Framework welcome content",
        help_text=(
            "The welcome page's editorial content, shown when Show framework "
            "welcome content is switched on. The contents list is built from the "
            "main section headings."
        ),
    )
    tags = ClusterTaggableManager(through="govuk.ContentPageTag", blank=True)

    content_panels = content_page_content_panels()

    settings_panels = content_page_settings_panels()

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        # Nothing framework-shaped until an editor has asked for it. The
        # lookup below reads as a read and is not one: Wagtail's
        # BaseSiteSetting.for_site does a get_or_create, so calling it
        # unconditionally wrote a Capability Framework settings row for every
        # site that rendered any page, including sites with the feature off
        # and its admin panel unregistered.
        wants_framework = (
            self.show_role_navigation
            or self.show_framework_welcome
            or self.show_framework_updates
        )
        if wants_framework:
            framework_wording = CapabilityFrameworkWordingSettings.for_request(request)
            if self.show_role_navigation or self.show_framework_welcome:
                groups = role_navigation_groups(
                    current_page_id=self.pk, wording=framework_wording
                )
                if self.show_role_navigation:
                    context["role_navigation"] = groups
                if self.show_framework_welcome:
                    context["framework_sections"] = groups
                    context["framework_contents"] = self.framework_welcome_contents(
                        groups
                    )
            if self.show_framework_updates:
                context["framework_changelog"] = site_wide_changelog()
            # The framework wording names the updates block and the navigation.
            context["framework_wording"] = framework_wording
        # Only one side column fits, so the role navigation wins where an
        # editor has asked for both.
        context["heading_navigation"] = (
            self.enable_free_text_heading_navigation and not self.show_role_navigation
        )
        # The generic ancestor trail names its first crumb after the site,
        # which on the framework is a sentence long and reads as a heading
        # rather than a way back, and it shows at every width where the live
        # service has no breadcrumb at all. Follow the role pages instead:
        # home, then this page, and only where the navigation is hidden.
        #
        # On a framework only. This is the page type every instance builds
        # with, and a site whose pages nest more than one deep wants the trail
        # the context processor assembles. The home label below comes from
        # wording settings the admin does not register without this flag, so
        # a site without it would carry a crumb no editor there can change.
        if settings.FEATURE_FLAGS.get("SKILLS"):
            site = Site.find_for_request(request)
            if site and self.pk != site.root_page_id:
                context["breadcrumbs"] = framework_breadcrumbs(request, self)
                context["breadcrumbs_mobile_only"] = True
            else:
                context["breadcrumbs"] = []
        return context

    def framework_welcome_contents(self, role_groups: list[dict]) -> list[dict]:
        """The contents list at the top of the welcome page.

        Built from the main section headings, so an editor adding a section to
        the page adds it here too. The role groups sit directly under the
        opening section, where the live service puts them.
        """
        sections = [
            {"title": block.value["heading"], "anchor": block.value.anchor_id()}
            for block in self.framework_welcome_body
            if block.block_type == "section" and block.value.get("level") == "h2"
        ]
        groups = [
            {"title": group["title"], "anchor": slugify(group["title"])}
            for group in role_groups or []
        ]
        return sections[:1] + groups + sections[1:]


def _default_site_wording() -> "CapabilityFrameworkWordingSettings":
    """The framework wording for the default site, or the model's defaults.

    The navigation helpers have no request to hang a lookup on, and an unsaved
    instance carries every default, so a site that has never edited the
    wording reads the same either way.
    """
    site = Site.objects.filter(is_default_site=True).first()
    saved = (
        CapabilityFrameworkWordingSettings.objects.filter(site=site).first()
        if site
        else None
    )
    return saved or CapabilityFrameworkWordingSettings()


def role_page_urls_by_role_id(*, exclude_page_id: int | None = None) -> dict[int, str]:
    """Map each role id to the URL of a live page that renders it."""
    urls: dict[int, str] = {}
    pages = RolePage.objects.live()
    if exclude_page_id is not None:
        pages = pages.exclude(pk=exclude_page_id)
    for page in pages:
        page_url = page.url
        if not page_url:
            continue
        for role_id in page.get_selected_role_ids():
            urls.setdefault(role_id, page_url)
    return urls


def further_resources_group(*, current_page_id: int | None = None, wording=None) -> dict | None:
    """The pages about the framework itself, for the side navigation.

    The live service closes its navigation with these: the skills index and
    the handful of pages that are about the framework rather than one role.
    Anything the editors add beside the roles turns up here on its own.
    """
    site = Site.objects.filter(is_default_site=True).first()
    if site is None:
        return None

    # Comparing content types rather than reading each page's specific record
    # keeps this to one query, and it runs on every page that has the
    # navigation.
    role_page_type = ContentType.objects.get_for_model(RolePage)

    items = []
    for page in site.root_page.get_children().live().order_by("path"):
        if page.content_type_id == role_page_type.pk or not page.url:
            continue
        items.append(
            {
                "title": page.title,
                "url": page.url,
                "is_current": page.pk == current_page_id,
            }
        )

    if not items:
        return None
    if wording is None:
        wording = _default_site_wording()
    return {"title": wording.further_resources_heading, "items": items}


def role_navigation_groups(*, current_page_id: int | None = None, wording=None) -> list[dict]:
    """The side navigation: live roles grouped by family, then the rest.

    Mirrors the DDaT Capability Framework, which lists every role grouped
    under its family heading on each role page, and closes with the pages
    about the framework itself.
    """
    urls_by_role_id: dict[int, str] = {}
    page_ids_by_role_id: dict[int, int] = {}
    for page in RolePage.objects.live():
        page_url = page.url
        if not page_url:
            continue
        for role_id in page.get_selected_role_ids():
            urls_by_role_id.setdefault(role_id, page_url)
            page_ids_by_role_id.setdefault(role_id, page.pk)

    groups: dict[str, list[dict]] = {}
    for role in GovukRole.objects.filter(pk__in=urls_by_role_id):
        family = (role.family or "").strip()
        if not family:
            continue
        groups.setdefault(family, []).append(
            {
                "title": role.title,
                "url": urls_by_role_id[role.pk],
                "is_current": page_ids_by_role_id.get(role.pk) == current_page_id,
            }
        )

    if wording is None:
        wording = _default_site_wording()
    navigation = [
        {
            "title": wording.family_group_title(family),
            "items": sorted(items, key=lambda item: (item["title"] or "").lower()),
        }
        for family, items in sorted(groups.items())
    ]

    resources = further_resources_group(
        current_page_id=current_page_id, wording=wording
    )
    if resources:
        navigation.append(resources)
    return navigation


def framework_breadcrumbs(request, page, *, family: str = "") -> list[dict]:
    """Home, an optional role family, then the page itself.

    Stands in for the side navigation on a narrow screen, which hides it, so
    every page carrying that navigation carries the same trail. The family
    entry points at its heading on the home page, which is where the role
    lists appear once the navigation is hidden.
    """
    site = Site.find_for_request(request)
    home_url = site.root_page.get_url(request) if site else None
    if not home_url:
        return []

    wording = CapabilityFrameworkWordingSettings.for_request(request)
    trail = [
        {"title": wording.breadcrumb_home_label, "url": home_url, "is_current": False}
    ]

    if family:
        group_title = wording.family_group_title(family)
        trail.append(
            {
                "title": group_title,
                "url": f"{home_url}#{slugify(group_title)}",
                "is_current": False,
            }
        )

    trail.append({"title": page.title, "url": None, "is_current": True})
    return trail


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

    settings_panels = page_settings_panels() + [
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
            parsed = row_id_from_text(value)
            if parsed is not None and parsed > 0:
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
        """The ids of the roles chosen here, without fetching the roles.

        Read from the stored JSON, which already holds them. Reading the blocks
        instead resolves the chooser, which is a query for records the caller
        may not want: the side navigation asks all 52 role pages for their ids
        and then fetches every role it was told about in one, so it was paying
        a query a page to learn what the JSON says. A page that has never been
        saved has no JSON to read, so the blocks remain the fallback.
        """
        role_ids: list[int] = []
        seen: set[int] = set()

        for raw_block in getattr(self.selected_roles, "raw_data", []) or []:
            role_id = self._extract_role_id(raw_block)
            if role_id and role_id not in seen:
                role_ids.append(role_id)
                seen.add(role_id)

        if not role_ids:
            for block in self.selected_roles:
                role_id = self._extract_role_id(getattr(block, "value", None))
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
                "is_scs": role.is_senior_civil_service,
                "scs_skills": role.get_scs_skills(),
                "scs_grades": role.get_scs_grade_labels(),
            }
            for role in self.get_selected_roles()
        ]

    @staticmethod
    def _display_role_name(title: str) -> str:
        """Lowercase a role title for mid-sentence use, keeping acronyms."""
        return " ".join(
            word if word.isupper() else word.lower()
            for word in (title or "").split()
        )

    # A "u" sounded as "you" takes "a" rather than "an", which is how the
    # framework writes "a user researcher".
    _CONSONANT_SOUNDED_VOWELS = ("eu", "ubi", "uni", "use", "usu", "uti")

    @classmethod
    def _lead_role_name(cls, title: str) -> str:
        """A role title as the framework writes it in the sentence below the
        heading, where only the opening word is lowered so that the capitals
        inside "Development operations (DevOps) engineer" survive.

        An acronym opening the title is left as it is, the same exception the
        heading above makes: "an IT service manager", not "an it service
        manager" a line under "What an IT service manager does".
        """
        words = (title or "").split()
        if not words:
            return ""
        first = words[0] if words[0].isupper() else words[0].lower()
        return " ".join([first, *words[1:]])

    @classmethod
    def _role_article(cls, name: str) -> str:
        """"a" or "an" for a role name, chosen by how the name is said rather
        than how it is spelt: "a user researcher" but "an IT service manager".
        """
        lowered = (name or "").lstrip().lower()
        if not lowered:
            return "a"
        vowel_sounded = lowered[0] in "aeiou" and not lowered.startswith(
            cls._CONSONANT_SOUNDED_VOWELS
        )
        return "an" if vowel_sounded else "a"

    @classmethod
    def _overview_heading(cls, display_role_name: str, wording) -> str:
        """The heading over a role's description, and the contents entry that
        points at it, which have to read the same."""
        article = cls._role_article(display_role_name)
        return wording.overview_heading_text.replace(
            wording.ARTICLE_PLACEHOLDER, article
        ).replace(wording.ROLE_PLACEHOLDER, display_role_name)

    @classmethod
    def _role_lead(cls, section: dict, wording) -> str:
        """The one sentence the framework prints under a role's heading.

        For example "Find out what a business architect in government does and
        the skills you need to do the role at each level." Senior Civil Service
        roles have no levels, so theirs stops at the role.
        """
        name = cls._lead_role_name(section["role"].title)
        if not name:
            return ""

        article = cls._role_article(name)
        text = wording.scs_role_lead_text if section["is_scs"] else wording.role_lead_text
        return text.replace(wording.ARTICLE_PLACEHOLDER, article).replace(
            wording.ROLE_PLACEHOLDER, name
        )

    @classmethod
    def _role_levels_intro(cls, section: dict, wording) -> list[str]:
        """The two sentences the framework prints under the role levels heading.

        For example "There are 4 business architect role levels, from trainee
        business architect to lead business architect."
        """
        levels = section["levels"]
        if not levels:
            return []

        display_role_name = section["display_role_name"]
        titles = [
            cls._display_role_name(level["title"])
            for level in levels
            if level["title"]
        ]

        if len(levels) == 1:
            opening = wording.role_levels_opening_one.replace(
                wording.ROLE_PLACEHOLDER, display_role_name
            )
            described = wording.role_levels_described_one
        else:
            levels_range = ""
            if len(titles) > 1:
                levels_range = wording.role_levels_range_text.replace(
                    wording.FIRST_PLACEHOLDER, titles[0]
                ).replace(wording.LAST_PLACEHOLDER, titles[-1])
            opening = (
                wording.role_levels_opening_many.replace(
                    wording.COUNT_PLACEHOLDER, str(len(levels))
                )
                .replace(wording.ROLE_PLACEHOLDER, display_role_name)
                .replace(wording.LEVELS_RANGE_PLACEHOLDER, levels_range)
            )
            described = wording.role_levels_described_many

        return [opening, f"{described} {wording.role_levels_purpose_text}"]

    @staticmethod
    def _section_anchors(
        *, is_scs: bool, has_levels: bool, display_role_name: str, suffix: str
    ) -> dict[str, str]:
        """The framework's own ids, so a link written against the live service
        still lands on the section it named.

        The overview keeps "what-a-" whatever article the heading takes:
        "what-a-it-service-manager-does" under "What an IT service manager
        does". A Senior Civil Service role's page reuses three of the others
        differently from the rest of the framework: its skills heading carries
        "role-levels", the shared-skills heading "roles-that-shares", and
        "related-roles" means the roles leading into the job rather than the
        ones beside it.

        That first one is only free because senior roles have no levels, which
        is how the framework is written rather than anything the model holds:
        one tick of the senior box on a role with levels and both sections
        would answer to "role-levels", leaving the page with a repeated id and
        two contents links onto the same heading. Where a senior role does have
        levels its skills take the ordinary "skills" instead.
        """
        return {
            "overview": f"what-a-{slugify(display_role_name)}-does{suffix}",
            "skills": ("role-levels" if is_scs and not has_levels else "skills")
            + suffix,
            "levels": f"role-levels{suffix}",
            "related_roles": ("roles-that-shares" if is_scs else "related-roles")
            + suffix,
            "progression_scs_roles": f"related-scs-roles{suffix}",
            "progression_roles": (
                "related-roles" if is_scs else "roles-that-could-lead-here"
            )
            + suffix,
            "updates": f"update-history{suffix}",
        }

    @staticmethod
    def _contents_entries(section: dict) -> list[dict]:
        """In-page contents links, in the order the sections are rendered.

        Each one repeats the heading it points at, so both read the wording
        the section was given rather than each writing out its own.
        """
        anchors = section["anchors"]
        wording = section["wording"]
        entries: list[dict] = []

        if section["role"].body:
            entries.append(
                {
                    "anchor": anchors["overview"],
                    "text": section["overview_heading"],
                    "children": [],
                }
            )
        if section["scs_skills"]:
            entries.append(
                {
                    "anchor": anchors["skills"],
                    "text": wording["scs_skills_heading"],
                    "children": [],
                }
            )
        if section["levels"]:
            entries.append(
                {
                    "anchor": anchors["levels"],
                    "text": wording["role_levels_heading"],
                    "children": [
                        {
                            "anchor": level["anchor"],
                            "text": f"{level['number']}. {level['title']}",
                            "children": [],
                        }
                        for level in section["levels"]
                        if level["anchor"]
                    ],
                }
            )
        if section["related_roles"]:
            entries.append(
                {
                    "anchor": anchors["related_roles"],
                    "text": wording["related_roles_heading"],
                    "children": [],
                }
            )
        if section["progression_scs_roles"]:
            entries.append(
                {
                    "anchor": anchors["progression_scs_roles"],
                    "text": wording["progression_scs_roles_heading"],
                    "children": [],
                }
            )
        if section["progression_roles"]:
            entries.append(
                {
                    "anchor": anchors["progression_roles"],
                    "text": wording["progression_roles_heading"],
                    "children": [],
                }
            )
        return entries

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        role_sections = self.get_role_sections()

        role_page_urls = role_page_urls_by_role_id(exclude_page_id=self.pk)
        # Built on the first section that has a use for it. A page holding only
        # senior roles never asks, theirs being the side of the mapping the
        # content team authors.
        senior_roles_by_source: dict[int, list[GovukRole]] | None = None
        site_root = self._site_root(request)
        skills_page = self._first_live_in_site(SkillsAZPage.objects.all(), site_root)
        context["skills_index_url"] = skills_page.url if skills_page else ""
        context["scs_context_url"] = self._scs_context_url(site_root)
        context["job_grades_url"] = self._job_grades_url(site_root)
        framework_wording = CapabilityFrameworkWordingSettings.for_request(request)
        context["framework_wording"] = framework_wording

        # A page normally renders one role, so its anchors can match the
        # framework's. Extra roles are suffixed to keep every id unique.
        needs_unique_anchors = len(role_sections) > 1

        for section in role_sections:
            role = section["role"]
            display_role_name = self._display_role_name(role.title)
            section["display_role_name"] = display_role_name
            section["overview_heading"] = self._overview_heading(
                display_role_name, framework_wording
            )
            section["wording"] = framework_wording.for_role(
                display_role_name=display_role_name, role_title=role.title
            )
            suffix = f"-{role.slug}" if needs_unique_anchors else ""
            section["anchors"] = self._section_anchors(
                is_scs=section["is_scs"],
                has_levels=bool(section["levels"]),
                display_role_name=display_role_name,
                suffix=suffix,
            )
            for number, level in enumerate(section["levels"], start=1):
                level["number"] = number
                level["anchor"] = (
                    f"{slugify(level['title'])}{suffix}" if level["title"] else ""
                )
                for skill_row in level["skills"]:
                    skill_row["level_scale_text"] = framework_wording.skill_level_scale(
                        label=skill_row["required_level_label"],
                        ordinal=SKILL_LEVEL_ORDINALS.get(
                            skill_row["required_level"], ""
                        ),
                    )
            section["related_roles"] = [
                {**entry, "url": role_page_urls.get(entry["role"].pk, "")}
                for entry in role.get_related_roles()
            ]
            section["progression_roles"] = [
                {"role": entry, "url": role_page_urls.get(entry.pk, "")}
                for entry in role.get_roles_that_could_lead_here()
            ]
            # Only on the way up. A senior role lists what leads into it, and
            # the framework leaves it there rather than also pointing on to the
            # senior roles above that one.
            if section["is_scs"]:
                section["progression_scs_roles"] = []
            else:
                if senior_roles_by_source is None:
                    senior_roles_by_source = GovukRole.senior_roles_by_source_role_id()
                section["progression_scs_roles"] = [
                    {"role": entry, "url": role_page_urls.get(entry.pk, "")}
                    for entry in senior_roles_by_source.get(role.pk, [])
                ]
            section["changelog"] = role.get_changelog()
            section["lead"] = self._role_lead(section, framework_wording)
            section["levels_intro"] = self._role_levels_intro(
                section, framework_wording
            )
            section["contents"] = self._contents_entries(section)

        context["role_sections"] = role_sections
        context["role_navigation"] = role_navigation_groups(
            current_page_id=self.pk, wording=framework_wording
        )
        # The side navigation is the way around the framework, but it is hidden
        # on a narrow screen, so a breadcrumb stands in for it there.
        context["breadcrumbs"] = self._role_breadcrumbs(request, role_sections)
        context["breadcrumbs_mobile_only"] = True
        return context

    # The framework explains, on every Senior Civil Service role, that the job
    # varies with the organisation, and links to the page that sets that out.
    SCS_CONTEXT_SLUG = (
        "context-and-challenges-for-senior-civil-service-roles-in-digital-and-data"
    )
    # Both the role and its levels name the grade the job is usually done at,
    # and link to the page explaining what those grades are.
    JOB_GRADES_SLUG = "job-grades"

    @classmethod
    def _scs_context_url(cls, site_root: Page | None) -> str:
        return cls._page_url_by_slug(cls.SCS_CONTEXT_SLUG, site_root)

    @classmethod
    def _job_grades_url(cls, site_root: Page | None) -> str:
        return cls._page_url_by_slug(cls.JOB_GRADES_SLUG, site_root)

    @classmethod
    def _page_url_by_slug(cls, slug: str, site_root: Page | None) -> str:
        """Where that page sits on this site, or nothing if it has not got one.

        Written out by hand the link is a 404 wherever the page is missing or
        has been moved, so the template asks for a URL and falls back to plain
        text without one. It also spares the reader a redirect: Wagtail gives
        back the path with its trailing slash, which a hand-written one lacks.
        """
        page = cls._first_live_in_site(Page.objects.filter(slug=slug), site_root)
        return page.url if page else ""

    @staticmethod
    def _site_root(request) -> Page | None:
        """The root of the site this request is being served from.

        One database can serve several sites, and a bare ``Page.objects``
        lookup crosses between them, so without this a role page could send a
        reader to another site's copy of a page, or to one this site does not
        publish at all.
        """
        site = Site.find_for_request(request)
        return site.root_page if site else None

    @staticmethod
    def _first_live_in_site(queryset, site_root: Page | None) -> Page | None:
        """The first live page in ``queryset`` belonging to that site.

        Falls back to searching every site when the root is unknown, which is
        no worse than the lookup this replaced.
        """
        queryset = queryset.live()
        if site_root is not None:
            queryset = queryset.descendant_of(site_root, inclusive=False)
        return queryset.first()

    def _role_breadcrumbs(self, request, role_sections: list[dict]) -> list[dict]:
        """Home, the role's family, then the role itself."""
        families = []
        for section in role_sections:
            family = (section["role"].family or "").strip()
            if family and family not in families:
                families.append(family)

        # A page holding roles from more than one family has no single place in
        # the navigation to point back to.
        return framework_breadcrumbs(
            request, self, family=families[0] if len(families) == 1 else ""
        )


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

    settings_panels = page_settings_panels() + [
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
        # One query for every skill's entries, not one per skill.
        skills = list(GovukSkill.objects.prefetch_related("changelog_entries"))
        skills.sort(
            key=lambda skill: (
                (skill.title or "").strip().lower(),
                (skill.slug or "").strip().lower(),
                skill.pk or 0,
            )
        )
        roles_by_skill = GovukRole.roles_by_skill_id()
        role_urls = role_page_urls_by_role_id()
        return [
            {
                "skill": skill,
                "level_rows": [] if skill.is_senior_civil_service else skill.get_level_rows(),
                "leadership_points": skill.get_leadership_points(),
                "is_scs": skill.is_senior_civil_service,
                "roles": [
                    {"role": role, "url": role_urls.get(role.pk, "")}
                    for role in roles_by_skill.get(skill.pk, [])
                ],
                # The same updates a role page shows, so a skill's history is
                # readable where the skill is, not only in search dates.
                "changelog": skill.get_changelog(),
            }
            for skill in skills
        ]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        skill_sections = self.get_skill_sections()
        framework_wording = CapabilityFrameworkWordingSettings.for_request(request)
        for section in skill_sections:
            for level_row in section["level_rows"]:
                level_row["scale_text"] = framework_wording.skill_level_scale(
                    label=level_row["label"], ordinal=level_row["ordinal"]
                )
        context["skill_sections"] = skill_sections
        context["framework_wording"] = framework_wording
        # The skills index sits alongside the roles in the framework, so it
        # carries the same side navigation, and the same narrow-screen
        # breadcrumb standing in for it.
        context["role_navigation"] = role_navigation_groups(
            current_page_id=self.pk, wording=framework_wording
        )
        context["breadcrumbs"] = framework_breadcrumbs(request, self)
        context["breadcrumbs_mobile_only"] = True
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

    settings_panels = page_settings_panels() + [
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
                selected_source_key = row_id_from_text(selected_source_key)

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

    settings_panels = page_settings_panels() + [
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
