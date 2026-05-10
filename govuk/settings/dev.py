import os
from .base import *

# Development settings - these are used in development and test environments

DEBUG = os.getenv("DEBUG", "True") == "True"
SECRET_KEY = os.getenv("SECRET_KEY")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DATABASE_NAME"),
        "USER": os.getenv("DATABASE_USER"),
        "PASSWORD": os.getenv("DATABASE_PASSWORD"),
        "HOST": os.getenv("DATABASE_HOST"),
        "PORT": os.getenv("DATABASE_PORT", "5432"),
    }
}

MEDIA_ROOT = "/app/data/media"

BASE_URL = os.getenv("BASE_URL").strip().rstrip("/")

ALLOWED_HOSTS = [os.getenv("DOMAIN"), "*"]
CSRF_TRUSTED_ORIGINS = [BASE_URL]
CSRF_ALLOWED_ORIGINS = [BASE_URL]
CORS_ORIGINS_WHITELIST = [BASE_URL]
SECURE_PROXY_SSL_HEADER = ("HTTP_CLOUDFRONT_FORWARDED_PROTO", "https")
USE_X_FORWARDED_PORT = True
DEFAULT_SITE_PORT = 443

WAGTAILADMIN_BASE_URL = BASE_URL
WAGTAILAPI_BASE_URL = BASE_URL + "/"
