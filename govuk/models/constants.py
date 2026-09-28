import re
from datetime import timedelta

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from django.core.validators import RegexValidator

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
    "line-break",
]

SigningPublicKey = Ed25519PublicKey | ec.EllipticCurvePublicKey
SigningPrivateKey = Ed25519PrivateKey | ec.EllipticCurvePrivateKey




FRAMEWORK_WELCOME_RICH_TEXT_FEATURES = [
    "bold",
    "italic",
    "link",
    "ul",
    "ol",
    # InsetTextBlock takes this list, and a quote that runs to two lines needs
    # a line break rather than a second block: a new block is a second border.
    "line-break",
]




FRAMEWORK_HOME_LABEL = "Home"




