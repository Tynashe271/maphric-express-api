"""Settings used when running the automated test suite.

Tests must not depend on Neon PostgreSQL, Redis or SMTP credentials, so the
external services are replaced with local in-memory equivalents.
"""

import os

os.environ.setdefault('DATABASE_URL', 'sqlite://:memory:')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('DEBUG', 'False')

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR  # noqa: F401

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'NAME': None},
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'maphric-tests',
    }
}

# The test client speaks plain HTTP, and the docs routes are exercised directly.
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
API_DOCS_PUBLIC = True

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
DEFAULT_FROM_EMAIL = 'noreply@example.com'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

CELERY_TASK_ALWAYS_EAGER = True

# The assistant tests mock the HTTP call, so the key only has to be non-empty.
OPENAI_API_KEY = 'test-openai-key'

REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_THROTTLE_CLASSES': [],
    # Scopes must stay registered for the per-action throttles to resolve; a
    # rate of None means unlimited.
    'DEFAULT_THROTTLE_RATES': {
        scope: None
        for scope in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']  # noqa: F405
    },
}
