"""
Django settings for docketdrift_site.

Configuration is environment-driven via python-dotenv. See .env.example for
the full list of variables. Local dev defaults to SQLite; production on NFSN
flips to MariaDB by setting DOCKETDRIFT_DB=mariadb in /home/private/.env.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load `.env` from the project root for local dev. On NFSN the deploy will
# point this at /home/private/.env (outside the web root).
load_dotenv(BASE_DIR / ".env")

# Django's `mysql` backend imports MySQLdb. PyMySQL is a pure-Python drop-in;
# this shim teaches Django to find it without requiring `mysqlclient` (which
# needs a C compiler on Windows).
import pymysql  # noqa: E402

pymysql.install_as_MySQLdb()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Core ------------------------------------------------------------------
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-secret-do-not-use-in-production",
)
DEBUG = _env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = _env_list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "docketdrift.com", ".docketdrift.com"],
)

# Behind NFSN's Apache+Proxy, Django sits behind a TLS-terminating reverse
# proxy: gunicorn only sees plain HTTP from Apache, but the public URL is
# HTTPS. Trust the X-Forwarded-Proto header so request.is_secure() returns
# True and admin login / CSRF / secure cookies all behave.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Django 4.0+ requires explicit Origin trust for state-changing requests
# (including the admin login POST). Cover the apex + every state subdomain.
CSRF_TRUSTED_ORIGINS = _env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["https://docketdrift.com", "https://*.docketdrift.com"],
)

# Only send session + CSRF cookies over HTTPS in production.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "opinions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files in production -- must sit right after
    # SecurityMiddleware so static asset requests short-circuit out of the
    # middleware stack before anything heavier runs.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "opinions.middleware.StateRouterMiddleware",
]

ROOT_URLCONF = "docketdrift_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "opinions.context_processors.site_extras",
            ],
        },
    },
]

WSGI_APPLICATION = "docketdrift_site.wsgi.application"


# --- Database --------------------------------------------------------------
# Local dev defaults to SQLite. Production on NFSN flips to MariaDB by
# setting DOCKETDRIFT_DB=mariadb in /home/private/.env -- which points the
# ENGINE at the MariaDB process at `madmaster.db`.
_db_backend = os.environ.get("DOCKETDRIFT_DB", "sqlite").strip().lower()

if _db_backend == "mariadb":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "HOST": os.environ.get("DOCKETDRIFT_DB_HOST", "madmaster.db"),
            "NAME": os.environ.get("DOCKETDRIFT_DB_NAME", "docketdrift"),
            "USER": os.environ.get("DOCKETDRIFT_DB_USER", ""),
            "PASSWORD": os.environ.get("DOCKETDRIFT_DB_PASSWORD", ""),
            "OPTIONS": {
                "charset": "utf8mb4",
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# --- Auth ------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- i18n / tz -------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Chicago"  # MN is Central; revisit per-state at expansion.
USE_I18N = True
USE_TZ = True


# --- Static + Media --------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# User uploads (manual opinion PDFs via Django admin) land here. On NFSN that
# resolves to /home/private/docketdrift/media/ -- writable by the gunicorn
# daemon user ('me') and NOT served by Apache. The MEDIA_URL is wired up in
# docketdrift_site/urls.py to a Django view in DEBUG; in production we'll
# decide per-file whether to expose downloads.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Logging ---------------------------------------------------------------
# Route everything through a console handler so it lands in stderr -- and
# therefore in NFSN's daemon log via gunicorn. The default Django LOGGING
# config silently drops request errors when ADMINS is unset; this overrides
# that so unhandled exceptions always show up in /home/logs/daemon_<tag>.log.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "opinions": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


# --- App-specific ----------------------------------------------------------
COURTLISTENER_TOKEN = os.environ.get("COURTLISTENER_TOKEN", "")

# Donation link surfaced on /support/. Leave empty to hide the donate
# button (the page still renders the "where the money goes" explainer
# and a tell-a-friend fallback). Set to a GitHub Sponsors / Ko-fi /
# Stripe Payment Link URL when ready.
DONATE_URL = os.environ.get("DONATE_URL", "")
