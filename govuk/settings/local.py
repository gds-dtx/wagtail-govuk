import os
from .base import *

# Local settings - these are used for local development and testing

DEBUG = True
WHITENOISE_USE_FINDERS = True
INCOMING_REQUEST_INFO_LOGGING = True
CONTENT_DISCOVERY_REQUEST_INFO_LOGGING = True

SECRET_KEY = "abc123"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

WAGTAILADMIN_BASE_URL = "http://localhost:8000"

ADDITIONAL_CSS = ["/static/cyber.css"]

FEATURE_FLAGS = {
    "SKILLS": True,
    "ORGANISATIONS": True,
    "PEOPLE_FINDER": True,
    "FEEDBACK": True,
}
