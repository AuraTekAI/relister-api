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

    # Third party apps
    "django_celery_beat",
    "rest_framework",
    "corsheaders",
    "drf_yasg",
    'rest_framework_simplejwt',
    'django_filters',
    
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
    "ACCESS_TOKEN_LIFETIME": timedelta(weeks=2),
    "REFRESH_TOKEN_LIFETIME": timedelta(weeks=2),
    "ROTATE_REFRESH_TOKENS": True,
}
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
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
TIME_ZONE = 'America/Chicago'
USE_TZ = True

ZENROWS_API_KEY = env('ZENROWS_API_KEY')


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
CORS_ALLOW_ALL_ORIGINS = True  # Only use this for development
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",  # React default port
    "http://127.0.0.1:3000",
]

# Optional: If you need to allow specific HTTP methods
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]

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
    }
}


EMAIL_BACKEND = env('EMAIL_BACKEND')
EMAIL_HOST = env('EMAIL_HOST')
EMAIL_PORT = env('EMAIL_PORT')
EMAIL_USE_TLS = env('EMAIL_USE_TLS')
EMAIL_HOST_USER = env('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')
MAX_RETRIES_ATTEMPTS = int(env('MAX_RETRIES_ATTEMPTS')) 
