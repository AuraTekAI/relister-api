"""Test-only settings.

Runs the suite against an in-memory SQLite database so tests can never touch a
real (dev or production) Postgres, and swaps Redis/Celery/S3-adjacent services
for local no-op backends. Everything else is inherited unchanged from
`settings.py`, so app config, DRF settings and model behaviour under test stay
identical to production.

Usage:
    python src/manage.py test VehicleListing --settings=relister.settings_test
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'relister-tests',
    }
}

# Never hand a task to a real broker during tests; every test that cares about
# enqueueing patches the task object directly.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'memory://'

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# Deterministic image-hosting config so URL assertions don't depend on the
# deployment's real bucket/CDN.
AWS_VEHICLE_IMAGE_BUCKET = 'test-vehicle-images'
AWS_VEHICLE_IMAGE_REGION = 'ap-southeast-2'
AWS_S3_VEHICLE_IMAGE_PREFIX = 'vehicle-images/'
AWS_CLOUDFRONT_DOMAIN = 'images.test.invalid'

# Throttle counters live in the cache, and the locmem cache above persists
# across tests in one process — with the production rates (login: 5/minute,
# keyed by IP) the 6th login POST anywhere in a suite run starts drawing 429s
# and unrelated tests fail. Effectively disable rate limits under test; the
# throttle classes themselves still execute, so misconfigured scopes would
# still blow up loudly.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_THROTTLE_RATES': {
        'anon': '10000/minute',
        'user': '10000/minute',
        'login': '10000/minute',
        'register': '10000/minute',
        'password_reset': '10000/minute',
    },
}
