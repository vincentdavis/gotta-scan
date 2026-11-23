"""
Django settings for the GOTTA.SCAN web app.

Local-first: DEBUG on, SQLite, media on disk. Env overrides for anything that
changes when this deploys (Railway later): GOTTASCAN_SECRET_KEY, GOTTASCAN_DEBUG,
GOTTASCAN_WORKER_TOKEN, GOTTASCAN_UPLOAD_TOKEN.
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# All mutable state (DB, uploaded scans) lives under data/ — one thing to back up.
# On Railway this points at a mounted volume (GOTTASCAN_DATA_DIR) so it survives deploys.
DATA_DIR = Path(os.environ.get('GOTTASCAN_DATA_DIR', BASE_DIR / 'data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.environ.get(
    'GOTTASCAN_SECRET_KEY',
    'django-insecure-68*t@%fbxt^vt@)_67t9=-p=x#b&#-kn-jwrw6ofxzqt9f*7r_',
)

DEBUG = os.environ.get('GOTTASCAN_DEBUG', '1') == '1'

# The iPhone uploads over LAN using this Mac's IP, so accept any host locally.
ALLOWED_HOSTS = ['*']

# Behind Railway's TLS-terminating proxy; trust its forwarded-proto + the public
# domain for CSRF (the browser photo-upload form POSTs).
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
_PUBLIC_DOMAIN = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
CSRF_TRUSTED_ORIGINS = [f'https://{_PUBLIC_DOMAIN}'] if _PUBLIC_DOMAIN else []

# Shared secret the Mac worker must send as X-Worker-Token.
WORKER_TOKEN = os.environ.get('GOTTASCAN_WORKER_TOKEN', 'dev-worker-token')

# Optional shared secret for scan uploads (X-Upload-Token). Empty = open
# (fine on local Wi-Fi; set it before deploying anywhere public).
UPLOAD_TOKEN = os.environ.get('GOTTASCAN_UPLOAD_TOKEN', '')

# Hard cap for an uploaded scan bundle (compressed).
SCAN_MAX_UPLOAD_BYTES = 3 * 1024 ** 3

# Hard cap for the bundle's decompressed size (zip-bomb guard).
SCAN_MAX_EXTRACTED_BYTES = 6 * 1024 ** 3

# Scans stuck in 'processing' longer than this get requeued automatically
# (covers a worker that died mid-job). Must exceed the worker's --timeout.
SCAN_STALE_CLAIM_SECONDS = 2 * 3600

# How many extracted images get browser-viewable JPEG thumbnails.
SCAN_THUMBNAIL_COUNT = 8

# Photogrammetry sets run to hundreds of photos; Django's default cap is 100.
DATA_UPLOAD_MAX_NUMBER_FILES = 2000


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'scans',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DATA_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

MEDIA_URL = '/media/'
MEDIA_ROOT = DATA_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
