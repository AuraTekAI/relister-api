from pathlib import Path

import os
import json

import environ
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

env_file_path = BASE_DIR / '.env'
if not env_file_path.exists():
    raise ValueError(f"\n\n----> .env file does not exists. Please create .env file in this directory ({BASE_DIR}).\n\n")

environ.Env.read_env(os.path.join(BASE_DIR, '.env'), overwrite=True)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")
if DEBUG == 'True':
    DEBUG = True
else:
    DEBUG = False



# Application definition
INSTALLED_APPS = [
    # Daphne must come first so `runserver` uses the ASGI/Channels server
    # (enables websockets in development). Production runs daphne/uvicorn directly.
    "daphne",

    # Pre installed apps
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # User defined apps
    'accounts',
    'VehicleListing',
    'payments',
    'zip_manager',
    'extension_logs',
    'team_alerts',
    'blog',

    # Third party apps
    "django_celery_beat",
    "rest_framework",
    "corsheaders",
    "drf_yasg",
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_filters',
    "channels",

]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "relister.urls"
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=env.int('ACCESS_TOKEN_LIFETIME_DAYS', default=1)),
    "REFRESH_TOKEN_LIFETIME": timedelta(weeks=env.int('REFRESH_TOKEN_LIFETIME_WEEKS', default=2)),
    "ROTATE_REFRESH_TOKENS": True,
}
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_PAGINATION_CLASS": "utils.custom_pagination.CustomPageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/minute",
        "user": "200/minute",
        "login": "5/minute",
        "register": "10/hour",
        "password_reset": "5/hour",
    },
}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / 'templates'],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "relister.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

# USE_SQLITE = env('USE_SQLITE')
# if USE_SQLITE == 'True':
#     DATABASES = {
#         'default' : {
#             'ENGINE': 'django.db.backends.sqlite3',
#             'NAME': BASE_DIR / 'db.sqlite3',
#         }
#     }
# else:
DATABASES = {
    'default' : {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('POSTGRES_DB'),
        'USER': env('POSTGRES_USER'),
        'PASSWORD': env('POSTGRES_PASSWORD'),
        'HOST': env('DB_HOST'),
        'PORT': env('DB_PORT')
    }
}

CACHES = {
    'default': {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL") + env("REDIS_DB"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient"
        }
    }
}




# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",},
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = "en-us"

USE_I18N = True
TIME_ZONE = 'Australia/Perth'
USE_TZ = True

ZENROWS_API_KEY = env('ZENROWS_API_KEY')

STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = env('STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET')
STRIPE_SUCCESS_URL = env('STRIPE_SUCCESS_URL')
STRIPE_CANCEL_URL = env('STRIPE_CANCEL_URL')
FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:3000')


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


PARENT_DIR = os.path.dirname(BASE_DIR)


SWAGGER_SETTINGS = {
'SECURITY_DEFINITIONS': {
 'Bearer':{
    'type':'apiKey',
    'name':'Authorization',
    'in':'header'
  }
 }
}
AUTH_USER_MODEL = "accounts.User"

ALLOWED_HOSTS = env('ALLOWED_HOSTS').split(',')
CSRF_TRUSTED_ORIGINS = env('CSRF_TRUSTED_ORIGINS').split(',')

SECRET_KEY = env("SECRET_KEY")

ENVIRONMENT = env("ENVIRONMENT")



CELERY_ENABLED = env('CELERY_ENABLED')
if CELERY_ENABLED == 'True' or CELERY_ENABLED == 'true':
    CELERY_ENABLED = True
else:
    CELERY_ENABLED = False

CELERY_BROKER_URL = env('CELERY_BROKER_URL')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND')

CELERY_TIMEZONE = env('CELERY_TIMEZONE')

REDIS_HOST = env('REDIS_HOST')
REDIS_PORT = env('REDIS_PORT')
REDIS_DB = env('REDIS_DB')
REDIS_PASSWORD = env('REDIS_PASSWORD')
REDIS_URL = env('REDIS_URL')

# CORS Settings
CORS_ALLOW_ALL_ORIGINS = DEBUG  # Only allow all origins in development
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[
    "http://localhost:3000",
    "http://127.0.0.1:3000",
])

# Optional: If you need to allow specific HTTP methods
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]

# ── Channels (websocket real-time extension control) ──────────────────────────
# The ASGI app (relister.asgi.application) wraps Django's HTTP handler with a
# websocket router. The channel layer uses the same Redis that Celery/cache use,
# on a dedicated DB index so control messages never collide with other keys.
ASGI_APPLICATION = "relister.asgi.application"
# Connect the channel layer EXACTLY like the cache does — via REDIS_URL, a plain
# redis:// URL with NO auth. The local Redis has no password (cache/Celery use
# REDIS_URL, not REDIS_PASSWORD); passing REDIS_PASSWORD here made every WS
# connect fail with "AUTH called without any password configured". A dedicated DB
# index (default 3) keeps control-channel keys isolated from cache/Celery.
CHANNELS_REDIS_URL = REDIS_URL.rstrip("/") + "/" + str(env.int("CHANNELS_REDIS_DB", default=3))
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [CHANNELS_REDIS_URL],
        },
    },
}

# Optional: If you need to allow specific headers
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]


IMAGES_DIR = os.path.join(os.path.dirname(__file__), '..', 'static', 'images')
# Logging configuration
LOG_DIR = os.path.join(PARENT_DIR, 'logs')
# Ensure the log directory exists
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)  # Create the directory if it doesn't exist

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        }
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        },
        'gumtree_file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(LOG_DIR, 'gumtree_listing.log'),
            'formatter': 'verbose'
        },
        'facebook_file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(LOG_DIR, 'facebook_listing.log'),
            'formatter': 'verbose'
        },
        'facebook_listing_cronjob_file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(LOG_DIR, 'facebook_listing_cronjob.log'),
            'formatter': 'verbose'
        },
        'relister_views_file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(LOG_DIR, 'relister_views.log'),
            'formatter': 'verbose'
        },
        'custom_domain_file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': os.path.join(LOG_DIR, 'custom_domain_listing.log'),
            'formatter': 'verbose'
        },
    },
    'loggers': {
        'gumtree': {
            'handlers': ['console', 'gumtree_file'],
            'level': 'DEBUG',
            'propagate': False
        },
        'facebook': {
            'handlers': ['console', 'facebook_file'],
            'level': 'DEBUG',
            'propagate': False
        },
        'facebook_listing_cronjob': {
            'handlers': ['console', 'facebook_listing_cronjob_file'],
            'level': 'DEBUG',
            'propagate': False
        },
        'relister_views': {
            'handlers': ['console', 'relister_views_file'],
            'level': 'DEBUG',
            'propagate': False
        },
        'custom_domain': {
            'handlers': ['console', 'custom_domain_file'],
            'level': 'DEBUG',
            'propagate': False
        },
    }
}


# ── AWS S3 (zip_manager) ────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY', default='')
AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='ap-southeast-2')
AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='')
AWS_S3_ZIP_PREFIX = env('AWS_S3_ZIP_PREFIX', default='zip-files/')
AWS_S3_PRESIGNED_URL_EXPIRY = env.int('AWS_S3_PRESIGNED_URL_EXPIRY', default=3600)
# ─────────────────────────────────────────────────────────────────────────────

# ── Vehicle image hosting pipeline (VehicleListing.image_pipeline) ─────────
# Its own bucket/credentials, each defaulting to the shared AWS settings above
# so nothing changes for a deployment that doesn't set them.
#
# These exist because the pipeline originally read AWS_STORAGE_BUCKET_NAME
# directly — the same variable zip_manager serves the browser-extension zips
# from. Pointing that at an image bucket would have made every extension
# download 404, so the two subsystems get independent config instead of being
# separated only by an S3 prefix.
AWS_VEHICLE_IMAGE_BUCKET = env('AWS_VEHICLE_IMAGE_BUCKET', default=AWS_STORAGE_BUCKET_NAME)
AWS_VEHICLE_IMAGE_ACCESS_KEY_ID = env('AWS_VEHICLE_IMAGE_ACCESS_KEY_ID', default=AWS_ACCESS_KEY_ID)
AWS_VEHICLE_IMAGE_SECRET_ACCESS_KEY = env('AWS_VEHICLE_IMAGE_SECRET_ACCESS_KEY', default=AWS_SECRET_ACCESS_KEY)
AWS_VEHICLE_IMAGE_REGION = env('AWS_VEHICLE_IMAGE_REGION', default=AWS_S3_REGION_NAME)
AWS_S3_VEHICLE_IMAGE_PREFIX = env('AWS_S3_VEHICLE_IMAGE_PREFIX', default='vehicle-images/')
# CloudFront distribution domain fronting the bucket (e.g. d123abc.cloudfront.net
# or a custom domain like images.autorelister.com.au). Left blank in dev to fall
# back to a direct virtual-hosted S3 URL; set in production.
AWS_CLOUDFRONT_DOMAIN = env('AWS_CLOUDFRONT_DOMAIN', default='')
# Max width in px for each generated WebP variant. Keys must stay exactly
# 'thumbnail'/'medium'/'large' — image_pipeline.get_or_create_ready_hosted_image
# indexes the render result by these names.
VEHICLE_IMAGE_SIZES = {'thumbnail': 320, 'medium': 800, 'large': 1600}
VEHICLE_IMAGE_WEBP_QUALITY = env.int('VEHICLE_IMAGE_WEBP_QUALITY', default=82)
# Quality for the FB-safe JPEG upload variant the extension re-uploads to
# Facebook Marketplace (WebP is rejected there). Slightly higher than the WebP
# quality since JPEG is less efficient at the same perceptual quality.
VEHICLE_IMAGE_UPLOAD_JPEG_QUALITY = env.int('VEHICLE_IMAGE_UPLOAD_JPEG_QUALITY', default=85)
# mode=auto (see image_pipeline.download_image_bytes) can escalate a blocked
# request to premium/residential proxies or JS rendering server-side before
# answering, which takes longer than a plain proxied fetch — 20s was tuned for
# the latter and was at risk of timing out an escalated request.
VEHICLE_IMAGE_DOWNLOAD_TIMEOUT = env.int('VEHICLE_IMAGE_DOWNLOAD_TIMEOUT', default=35)
# Celery per-task rate limit for process_vehicle_listing_image_task — throttles
# how fast we hit Gumtree/dealer sites for image downloads regardless of how
# many listings get scraped at once.
VEHICLE_IMAGE_DOWNLOAD_RATE_LIMIT = env('VEHICLE_IMAGE_DOWNLOAD_RATE_LIMIT', default='60/m')
# When True, the Chrome extension's publish payload serves our own
# S3/CloudFront-hosted copy of each processed photo instead of proxying the
# full-size dealer original through custom_domain_image_proxy — this keeps the
# worker-pinning proxy off the publish hot path and fixes PARTIAL_IMAGE_UPLOAD
# drops for custom-domain dealers.
#
# The pipeline now stores an FB-safe JPEG upload variant (HostedImage.upload_image)
# that _resolve_extension_images serves; images without it yet fall back to the
# proxy automatically. DEFAULT FALSE for a controlled rollout: apply the
# migration and run `manage.py backfill_upload_variants` first, then set
# EXTENSION_USE_HOSTED_IMAGES=True in the env (no code deploy needed) to turn the
# fix on. Flip back to False to instantly revert to the old proxy-everything path.
EXTENSION_USE_HOSTED_IMAGES = env.bool('EXTENSION_USE_HOSTED_IMAGES', default=False)
# When True, the EasyVehicles adapter checks each gallery photo while parsing
# and, for any full-size URL that isn't serving, stores the slide's displayed
# (640x480) rendition instead.
#
# On by default because the dealer's full-size bucket is unreliable in a way
# that stops listings publishing at all: measured 2026-07-29, 5 of 23 full-size
# photos serving on one listing, 7 of 20 and 8 of 17 on others — while the
# displayed rendition was 20 of 20 and 17 of 17 on those same pages. Below 15
# fetchable photos the extension aborts the publish outright, so a lower-
# resolution photo is strictly better than no listing.
#
# The cost is one request per photo at parse time (~1.4s each against this
# dealer's CDN). Set False to skip the check and store whatever the page lists,
# accepting that some listings will not publish.
EASYVEHICLES_VERIFY_IMAGE_URLS = env.bool('EASYVEHICLES_VERIFY_IMAGE_URLS', default=True)
# ─────────────────────────────────────────────────────────────────────────────

EMAIL_BACKEND = 'relister.email_backend.FlashpostEmailBackend'
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='hello@autorelister.com.au')
FLASHPOST_API_URL = env('FLASHPOST_API_URL')
FLASHPOST_API_KEY = env('FLASHPOST_API_KEY')
# Compatibility alias — accounts/views.py, payments/tasks.py, VehicleListing/utils.py
# all import EMAIL_HOST_USER by name; this alias keeps them working without any changes.
EMAIL_HOST_USER = DEFAULT_FROM_EMAIL
MAX_RETRIES_ATTEMPTS = int(env('MAX_RETRIES_ATTEMPTS'))
ADMIN_EMAIL = env('ADMIN_EMAIL')
TECH_SUPPORT_EMAIL = env('TECH_SUPPORT_EMAIL')
MAX_DAILY_LISTINGS_COUNT = int(env('MAX_DAILY_LISTINGS_COUNT'))

DELAY_START_TIME_BEFORE_ACCESS_BROWSER = int(env('DELAY_START_TIME_BEFORE_ACCESS_BROWSER'))
DELAY_END_TIME_BEFORE_ACCESS_BROWSER = int(env('DELAY_END_TIME_BEFORE_ACCESS_BROWSER'))
LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION = int(env('LONG_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION'))
LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION = int(env('LONG_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION'))
SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION = int(env('SHORT_DELAY_START_TIME_BETWEEN_ELEMENTS_SELECTION'))
SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION = int(env('SHORT_DELAY_END_TIME_BETWEEN_ELEMENTS_SELECTION'))
SIMPLE_DELAY_START_TIME = int(env('SIMPLE_DELAY_START_TIME'))
SIMPLE_DELAY_END_TIME = int(env('SIMPLE_DELAY_END_TIME'))
DELAY_START_TIME_FOR_LOADING_PAGE = int(env('DELAY_START_TIME_FOR_LOADING_PAGE'))
DELAY_END_TIME_FOR_LOADING_PAGE = int(env('DELAY_END_TIME_FOR_LOADING_PAGE'))

# ── Overage billing ─────────────────────────────────────────────────────────
# When True (default), the daily overage task only LOGS what it would charge and
# makes NO Stripe calls. Set OVERAGE_BILLING_DRYRUN=False in .env to go live.
OVERAGE_BILLING_DRYRUN = env.bool('OVERAGE_BILLING_DRYRUN', default=True)

# ── Web Push (VAPID) ────────────────────────────────────────────────────────
# Private key must never be committed — set both in .env on the server.
VAPID_PUBLIC_KEY = env('VAPID_PUBLIC_KEY', default='')
VAPID_PRIVATE_KEY = env('VAPID_PRIVATE_KEY', default='')
VAPID_SUBJECT = env('VAPID_SUBJECT', default='mailto:support@autorelister.com.au')


