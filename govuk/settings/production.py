import os

from .base import *
from .runtime import deployment_allowed_hosts

# Development settings - these are used in development and test environments

DEBUG = os.getenv("DEBUG", "False") == "True"
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

# Host allow-list, no wildcard (Django rejects unexpected Host headers when
# DEBUG is off). Set ALLOWED_HOSTS to a comma-separated list per environment;
# it must include every host the app is reached on. Falls back to DOMAIN when
# the var is unset, and the task's own address is added for the load balancer
# health check, which arrives by IP -- see deployment_allowed_hosts.
ALLOWED_HOSTS = deployment_allowed_hosts()
CSRF_TRUSTED_ORIGINS = [BASE_URL]
CSRF_ALLOWED_ORIGINS = [BASE_URL]
CORS_ORIGINS_WHITELIST = [BASE_URL]
SECURE_PROXY_SSL_HEADER = ("HTTP_CLOUDFRONT_FORWARDED_PROTO", "https")
USE_X_FORWARDED_PORT = True
DEFAULT_SITE_PORT = 443

WAGTAILADMIN_BASE_URL = BASE_URL
WAGTAILAPI_BASE_URL = BASE_URL + "/"
