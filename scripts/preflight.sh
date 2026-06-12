#!/bin/sh
# Pre-push preflight check. Run before `git push origin main` or
# equivalent. Catches the two outage classes that bit hardest in the
# 2026-06-09 -> 2026-06-12 session:
#
#   1. Multi-line {# ... #} template comments (opinions.E001) -- caught
#      by Django's system check, blocks any manage.py command.
#   2. Decorator-orphan SyntaxError -- caught by trying to import the
#      views module. If @cache_control got attached to a variable or
#      helper instead of the intended view, the import crashes.
#
# Both have produced full-site 500s when they slipped through.
#
# Usage (from the repo root):
#   ./scripts/preflight.sh
#
# Exit codes:
#   0  -- all checks passed, safe to push
#   1  -- a check failed; do NOT push
#
# Recommended workflow: run this before every push. Light enough to
# be a habit (~2s); heavy enough to catch a class of subtle bugs
# template authors and decorator-shufflers tend to ship.

set -e

PYTHON=".venv/Scripts/python.exe"
if [ ! -x "$PYTHON" ]; then
    PYTHON="./.venv/bin/python"
fi

# The bare `python -c "import opinions.views"` route needs Django
# already configured; we do it through django.setup() so the import-side
# decorator-orphan SyntaxError + URL-conf import errors surface here
# rather than at gunicorn boot in production.
export DJANGO_SETTINGS_MODULE="docketdrift_site.settings"

echo "[preflight] manage.py check (includes opinions.E001 multi-line {# #} guard)"
"$PYTHON" manage.py check

echo "[preflight] importing opinions.views (catches decorator-orphan SyntaxError)"
"$PYTHON" -c "import django; django.setup(); import opinions.views; print('opinions.views imported cleanly')"

echo "[preflight] importing opinions.urls (catches URL-conf syntax errors)"
"$PYTHON" -c "import django; django.setup(); import opinions.urls; print('opinions.urls imported cleanly')"

echo "[preflight] importing every parser (catches parser regex / import bugs)"
"$PYTHON" -c "import django; django.setup(); from opinions.parsing import REGISTRY; print('parsers:', list(REGISTRY))"

echo "[preflight] all checks passed. safe to push."
