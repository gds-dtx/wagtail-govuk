import os

from .base import *
from .base import _bool_env
from .runtime import deployment_allowed_hosts

# Production settings - these are used in the staging and production environments

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

# User-uploaded media (images, documents) must not live on the container
# filesystem. ECS task storage is ephemeral -- wiped on every deploy, restart
# and scale-in -- and not shared between tasks, so a file written by the task
# that handled the upload is invisible to every other task and gone by the next
# deploy, while the image/document row in RDS (shared, durable) still points at
# it. The upload reports success and then 404s. Store media in S3 instead, so
# every task reads and writes one durable bucket.
#
# Set MEDIA_S3_BUCKET to switch media storage to S3; leave it unset (local dev,
# CI) to keep the FileSystemStorage default inherited from base.py. Credentials
# come from the ECS task role via boto3's default provider chain -- do not set
# access keys here. The infrastructure side (bucket, IAM policy, CloudFront)
# lives in the wagtail-iac repo.
MEDIA_S3_BUCKET = os.getenv("MEDIA_S3_BUCKET")
if MEDIA_S3_BUCKET:
    _media_s3_options = {
        "bucket_name": MEDIA_S3_BUCKET,
        # Never silently overwrite an existing key: Wagtail expects a distinct
        # object per upload, and overwrite-on-collision would let one upload
        # clobber another's file.
        "file_overwrite": False,
        # Keep uploads under a prefix so one bucket can also hold, say, static
        # files or backups without collision.
        "location": os.getenv("MEDIA_S3_LOCATION", "media"),
        # Public website media served through CloudFront: unsigned URLs, so the
        # CDN can cache them and links do not expire. Set MEDIA_S3_QUERYSTRING_AUTH
        # truthy to fall back to signed URLs if the bucket is kept fully private.
        "querystring_auth": _bool_env("MEDIA_S3_QUERYSTRING_AUTH", default=False),
    }
    # Region is normally provided to the task as AWS_REGION by ECS; pass it
    # explicitly when present so boto3 does not have to guess.
    _media_s3_region = os.getenv("MEDIA_S3_REGION") or os.getenv("AWS_REGION")
    if _media_s3_region:
        _media_s3_options["region_name"] = _media_s3_region
    # Serve media through CloudFront (or a bucket-fronting domain) when one is
    # configured, so URLs point at the CDN rather than the raw S3 endpoint.
    _media_s3_domain = os.getenv("MEDIA_S3_CUSTOM_DOMAIN")
    if _media_s3_domain:
        _media_s3_options["custom_domain"] = _media_s3_domain.strip().rstrip("/")

    STORAGES = {
        **STORAGES,
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": _media_s3_options,
        },
    }

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
