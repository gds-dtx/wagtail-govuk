from datetime import timedelta
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Orderable

from .blocks import LinkBlock
from .constants import (
    DEFAULT_JWT_LIFETIME,
    SIGNING_ALGORITHM_EDDSA,
    SIGNING_ALGORITHM_ES256,
)
from .signing import (
    JWTGenerationError,
    SecretTextarea,
    _base64url_without_padding,
    _load_signing_private_key,
    _load_signing_public_key,
    _normalised_htm,
    _normalised_htu,
    _normalised_signing_algorithm,
    _normalised_wagtail_admin_issuer,
    _private_key_required_error,
    _private_public_key_mismatch_error,
    _signing_public_key_fingerprint,
)


@register_setting(icon="warning", order=3)
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

    class Meta:
        verbose_name = "Phase banner"


@register_setting(icon="link", order=2)
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


@register_setting(icon="cog", order=1)
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
    service_name_location = models.CharField(
        max_length=20,
        choices=[
            ("header", "Header bar"),
            ("navigation", "Service navigation"),
        ],
        default="header",
        help_text="Where the service name appears.",
    )
    service_name_link = models.CharField(
        max_length=500,
        blank=True,
        default="/",
        verbose_name="Service name link",
        help_text=(
            "Where the service name links to. In the header bar the logo and "
            "service name form a single link to this address. In the service "
            "navigation the logo always links to GOV.UK and only the service "
            "name links here. Defaults to this site's home page (/)."
        ),
    )
    sign_in_location = models.CharField(
        max_length=20,
        choices=[
            ("header", "Header bar"),
            ("navigation", "Service navigation"),
            ("hidden", "Hidden"),
        ],
        default="navigation",
        help_text=(
            "Where the sign in and sign out links appear. Choose Hidden for "
            "sites where visitors never sign in -- the sign in link is hidden, "
            "but a signed-in user can still sign out."
        ),
    )
    search_location = models.CharField(
        max_length=20,
        choices=[
            ("header", "Header bar"),
            ("navigation", "Service navigation"),
            ("hidden", "Hidden"),
        ],
        default="header",
        help_text="Where the search box appears.",
    )
    show_site_name_in_search_box = models.BooleanField(
        default=False,
        help_text="Include the site name in the header search label and placeholder.",
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
    enable_search_results_page = models.BooleanField(
        default=True,
        verbose_name="Allow free-text search results",
        help_text=(
            "Let visitors search for any phrase and see a results page. Turn "
            "this off to keep only the suggestions that jump straight to a "
            "live page or snippet as the reader types -- pressing Enter or the "
            "search button on its own then does nothing, and the results page "
            "is not served."
        ),
    )
    content_max_width = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(320), MaxValueValidator(2560)],
        verbose_name="Maximum content width (pixels)",
        help_text=(
            "How wide the page content grows on large screens, in pixels. "
            "Leave blank for the default of 950."
        ),
    )
    extra_css = models.TextField(
        blank=True,
        default="",
        help_text="Optional additional CSS appended after other overrides.",
    )
    show_page_feedback_prompt = models.BooleanField(
        default=False,
        verbose_name="Ask \"Is this page useful?\" on every page",
        help_text=(
            "Ask \"Is this page useful?\" at the foot of every page, as GOV.UK "
            "publications do. Answers are recorded in the server logs."
        ),
    )
    page_feedback_more_url = models.CharField(
        max_length=500,
        blank=True,
        default="/feedback",
        verbose_name="Feedback follow-up link",
        help_text=(
            "Where \"Give more feedback\" points after someone answers, for "
            "example a survey. Defaults to /feedback."
        ),
    )
    page_feedback_more_intro = models.CharField(
        max_length=255,
        blank=True,
        default="To help improve the framework, we’d like to know more about your visit today.",
        verbose_name="Feedback follow-up sentence",
        help_text="The sentence shown after someone answers, above the link.",
    )
    page_feedback_more_link_text = models.CharField(
        max_length=255,
        blank=True,
        default="Give more feedback on using the framework website",
        verbose_name="Feedback follow-up link text",
        help_text="The wording of the link shown after someone answers.",
    )
    show_back_to_top = models.BooleanField(
        default=False,
        verbose_name="Show a “Back to top” link",
        help_text=(
            "Offer a “Back to top” link at the bottom-left of the page once the reader has "
            "scrolled past the first screen of a long page."
        ),
    )

    panels = [
        FieldPanel("header_logo"),
        MultiFieldPanel(
            [
                FieldPanel("service_name_location"),
                FieldPanel("service_name_link"),
            ],
            heading="Service name",
        ),
        FieldPanel("sign_in_location"),
        MultiFieldPanel(
            [
                FieldPanel("search_location"),
                FieldPanel("enable_search_results_page"),
                FieldPanel("show_site_name_in_search_box"),
                FieldPanel("search_placeholder"),
            ],
            heading="Search box",
        ),
        MultiFieldPanel(
            [
                FieldPanel("show_page_feedback_prompt"),
                # No heading overrides: the fields' own labels ("Feedback
                # follow-up sentence" and so on) are what the revision-compare
                # view shows, and the form should read the same.
                FieldPanel("page_feedback_more_intro"),
                FieldPanel("page_feedback_more_link_text"),
                FieldPanel("page_feedback_more_url"),
            ],
            heading="Page feedback",
        ),
        FieldPanel("show_back_to_top"),
        FieldPanel("content_max_width"),
        FieldPanel("extra_css", heading="Extra CSS"),
    ]

    class Meta:
        verbose_name = "Customise"
        verbose_name_plural = "Customise"

    def render_custom_css(self) -> str:
        sections: list[str] = []

        if self.content_max_width:
            width = self.content_max_width
            # main.css reads the content width from this variable (default 950)
            # and centres the container at a hard-wired 1030px (= 950 + 80).
            # Media queries cannot read the variable, so move that breakpoint to
            # width + 80: keep the fixed side margins up to it, centre above it.
            breakpoint_px = width + 80
            sections.append(f":root {{ --govuk-content-width: {width}px; }}")
            if breakpoint_px > 1030:
                # Undo main.css's 1030px centring in the gap up to the new,
                # wider breakpoint.
                sections.append(
                    f"@media (min-width: 1030px) and (max-width: {breakpoint_px - 1}px) {{\n"
                    "    .govuk-width-container, .hero { margin-left: 30px !important; margin-right: 30px !important; }\n"
                    "}"
                )
            sections.append(
                f"@media (min-width: {breakpoint_px}px) {{\n"
                "    .govuk-width-container, .hero { margin-left: auto !important; margin-right: auto !important; }\n"
                "}"
            )

        extra_css = (self.extra_css or "").strip()
        if extra_css:
            sections.append(extra_css)

        return "\n".join(sections).strip()

    @property
    def has_custom_css(self) -> bool:
        return bool(self.render_custom_css())


@register_setting(icon="error", order=4)
class ErrorPagesSettings(BaseSiteSetting):
    not_found_heading = models.CharField(
        max_length=255,
        blank=True,
        default="Page not found",
        verbose_name="Page not found: heading",
        help_text=(
            "The heading on the page a reader reaches when a web address does "
            "not exist. Leave blank for the Design System's “Page not "
            "found”."
        ),
    )
    not_found_body = RichTextField(
        blank=True,
        default=(
            "<p>If you typed the web address, check it is correct.</p>"
            "<p>If you pasted the web address, check you copied the entire "
            "address.</p>"
        ),
        verbose_name="Page not found: body",
        help_text=(
            "What the page not found page says above the contact sentence. "
            "Clear it to say nothing. The contact sentence below is added "
            "separately."
        ),
    )
    problem_heading = models.CharField(
        max_length=255,
        blank=True,
        default="Sorry, there is a problem with the service",
        verbose_name="Problem with the service: heading",
        help_text=(
            "The heading on the page a reader reaches when the service hits an "
            "error. Leave blank for the Design System's “Sorry, there is a "
            "problem with the service”."
        ),
    )
    problem_body = RichTextField(
        blank=True,
        default="<p>Try again later.</p>",
        verbose_name="Problem with the service: body",
        help_text=(
            "What the problem page says above the contact sentence. Clear it "
            "to say nothing. The contact sentence below is added separately."
        ),
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

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("not_found_heading"),
                FieldPanel("not_found_body"),
            ],
            heading="404: Page not found",
        ),
        MultiFieldPanel(
            [
                FieldPanel("problem_heading"),
                FieldPanel("problem_body"),
            ],
            heading="500: Problem with the service",
        ),
        MultiFieldPanel(
            [
                FieldPanel("error_contact_link_text"),
                FieldPanel("error_contact_email"),
                FieldPanel("error_contact_about"),
            ],
            heading="Error contact",
        ),
    ]

    class Meta:
        verbose_name = "Error pages"
        verbose_name_plural = "Error pages"


@register_setting(icon="cog", order=5)
class MaintenanceModeSettings(BaseSiteSetting):
    """The admin switch that closes the service behind the 503 page.

    ``enabled`` is planned maintenance: the public site answers with the
    service-unavailable page, but signed-in staff are let through so they can
    keep working. The ``MAINTENANCE_MODE`` environment variable remains as an
    emergency override that closes the site to everyone but the exempt paths;
    ``MaintenanceModeMiddleware`` reads both. The heading and body here are the
    503 page's wording, moved out of the Error pages setting so every
    maintenance control lives in one menu.
    """

    enabled = models.BooleanField(
        default=False,
        verbose_name="Maintenance mode",
        help_text=(
            "Close the site behind the service-unavailable page. Signed-in "
            "users are still let through; the health check and admin stay open."
        ),
    )
    heading = models.CharField(
        max_length=255,
        blank=True,
        default="Sorry, the service is unavailable",
        verbose_name="Service unavailable: heading",
        help_text=(
            "The heading on the page a reader reaches while the service is "
            "closed for maintenance. Leave blank for the Design System's "
            "“Sorry, the service is unavailable”."
        ),
    )
    body = RichTextField(
        blank=True,
        default="<p>You will be able to use the service later.</p>",
        verbose_name="Service unavailable: body",
        help_text=(
            "What the unavailable page says above the contact sentence when no "
            "maintenance return time is set. Clear it to say nothing. The "
            "contact sentence below is added separately."
        ),
    )

    panels = [
        FieldPanel("enabled"),
        FieldPanel("heading"),
        FieldPanel("body"),
    ]

    class Meta:
        verbose_name = "Maintenance mode"
        verbose_name_plural = "Maintenance mode"


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




