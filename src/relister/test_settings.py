"""Local test settings — run the test suite without Postgres/Redis.

Usage:
    python manage.py test --settings=relister.test_settings

Uses an in-memory sqlite database and builds tables straight from the current
model state (migrations disabled), so tests run on a dev machine with no
services up. Cache/channel layers are swapped for in-memory backends.
"""
import os

# Server-only secrets that base settings require but tests never use — give
# them dummies so the suite runs on a dev machine with no .env additions.
os.environ.setdefault('REDIS_PASSWORD', 'test-only')

from .settings import *  # noqa: E402,F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}


class _DisableMigrations(dict):
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _DisableMigrations()

CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
}
CHANNEL_LAYERS = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
}
CELERY_TASK_ALWAYS_EAGER = True

# The login endpoint is rate-throttled (5/minute); tests that log in
# repeatedly would receive 429s. Throttling is not under test — raise the
# rates far beyond anything a test run reaches.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_THROTTLE_RATES': {
        **REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'],  # noqa: F405
        'anon': '10000/minute',
        'user': '10000/minute',
        'login': '10000/minute',
    },
}
