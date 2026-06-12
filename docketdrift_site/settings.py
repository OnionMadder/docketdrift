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

# Fail-loud guard: refuse to boot a production process with the dev
# placeholder secret. If DJANGO_DEBUG=False is set, DJANGO_SECRET_KEY must
# be a real value. Better to crash at startup than to silently sign
# session cookies + password-reset tokens with a public string that lives
# in the repo. The guard is intentionally string-prefix-based so any
# accidental copy of the placeholder also trips it.
if not DEBUG and SECRET_KEY.startswith("dev-only-"):
    raise RuntimeError(
        "DJANGO_SECRET_KEY is unset (or still the dev placeholder) but "
        "DJANGO_DEBUG=False. Set a real secret in /home/private/.env."
    )

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

# --- Security headers (emitted by django.middleware.security.SecurityMiddleware)
# Nosniff + Referrer-Policy are safe in dev too. HSTS is gated on
# DEBUG=False so local browsers don't get pinned to HTTPS for
# 127.0.0.1.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
if not DEBUG:
    # 1 year, all subdomains (mn., nh., ...), preload-list eligible.
    # Once a browser sees this header, it refuses HTTP for the next
    # year — safe to enable now since the apex + every state subdomain
    # is already HTTPS via the NFSN-managed certs.
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

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
    # gzip dynamic responses (HTML, XML sitemaps, JSON). WhiteNoise already
    # gzips static assets via its compressed-manifest store; GZipMiddleware
    # covers everything else. Sits before SessionMiddleware so it sees the
    # full final body. BREACH-style attacks aren't a concern here -- we
    # don't echo unmasked CSRF tokens or session secrets in response bodies.
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # CrawlerBlockMiddleware MUST sit BEFORE StateRouterMiddleware so a
    # 429 for a noisy SEO crawler doesn't incur the per-request State
    # lookup. See opinions/middleware.py for the blocklist + rationale.
    "opinions.middleware.CrawlerBlockMiddleware",
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
            # Persistent connections: each gunicorn worker holds an open
            # MariaDB connection for up to 60s instead of three-way
            # handshaking per request. Saves ~5-15ms of warm-request
            # latency; the per-worker memory cost is trivial. Safe with
            # gunicorn's preforking model because each worker has its own
            # connection pool, not a shared one.
            "CONN_MAX_AGE": 60,
            # CONN_HEALTH_CHECKS makes Django do a cheap SELECT 1 against
            # the pooled connection before reusing it. NFSN's shared
            # MariaDB occasionally drops SSL sockets mid-pool (especially
            # under contention from concurrent management commands like
            # ingest_court running in parallel) -- without this, the next
            # request fails with OperationalError (2013, "Lost connection
            # to MySQL server during query"). Tiny per-request cost,
            # eliminates the whole class of mid-query disconnect 500s.
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "charset": "utf8mb4",
                # Per-connection MAX_STATEMENT_TIME ceiling. A single
                # runaway query (slow FULLTEXT MATCH(), full-corpus
                # SUBSTRING, accidental cartesian join, VEC_DISTANCE
                # without a date cutoff) used to pin a gunicorn worker
                # for 30+ seconds, queuing every subsequent request
                # behind it. Cap each statement at 25s -- enough for
                # the legitimately slow public-search COUNT on a 120K-
                # row corpus but tight enough that a runaway query
                # surfaces as a 500 on ONE request instead of timing
                # out FIVE before someone notices.
                # `init_command` runs on every new pooled connection,
                # so the limit applies regardless of which worker /
                # which connection picks up the request.
                "init_command": "SET SESSION max_statement_time = 25",
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


# --- Tag suggestion thresholds --------------------------------------------
# Cosine similarity bands for the suggest_tags command. Calibrated to the
# actual voyage-law-2 distribution we observe between long-form opinion
# documents and short tag descriptions -- they cluster much lower than
# opinion-vs-opinion or query-vs-opinion scores (top matches land around
# 0.30-0.45, not the 0.7+ you'd see in the brief's example). The signal
# is in the relative ordering, not the absolute value.
#
# Auto-apply only when a match is unambiguously dominant (e.g. an
# explicit "ineffective assistance of counsel" opinion correctly
# scoring 0.42 against the ineffective-assistance tag). Review band
# surfaces the next tier where a human eye is the cheapest disambiguator.
# Below review: not worth a review-queue slot.
#
# Recalibrate after the first few hundred review decisions are in --
# we'll have empirical data on where the precision/recall knee actually
# sits for this corpus.
TAG_SUGGESTION_AUTO_APPLY_THRESHOLD = 0.40  # above this -> auto-tag
TAG_SUGGESTION_REVIEW_THRESHOLD = 0.25       # below this -> drop, don't even surface
TAG_SUGGESTION_TOP_N = 5                     # max candidates per opinion
