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

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
DEFAULT_FROM_EMAIL = 'noreply@example.com'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# The assistant only calls the provider when a key is configured, so the suite
# needs a placeholder to exercise the request path with a mocked transport.
OPENAI_API_KEY = 'test-openai-key'

CELERY_TASK_ALWAYS_EAGER = True

REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_THROTTLE_CLASSES': [],
    'DEFAULT_THROTTLE_RATES': {},
}
