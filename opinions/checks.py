"""Django system checks for the opinions app.

System checks run on every ``manage.py check``, ``runserver``, and
``migrate`` (and via the gunicorn boot path on NFSN), making this the
right place to wire deploy-blocking guardrails that catch a known class
of mistake before it ships to production.

Current checks:

- ``E001_multiline_django_comment``: catches the recurring
  multi-line ``{# ... #}`` bleed where the comment text renders as
  visible page content because Django only honors ``{#`` ``#}`` on
  a SINGLE line. Multi-line blocks must use
  ``{% comment %} ... {% endcomment %}``. This bug has shipped THREE
  times now -- two times this session alone -- and a docs note didn't
  stop it. A check that returns an Error (E-level, deploy-blocking)
  does.
"""
from __future__ import annotations

from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, register, Tags


# Roots to scan. We deliberately don't scan ``.venv/`` or admin third-
# party packages -- a third-party template with a multi-line ``{# #}``
# isn't our bug to fix.
def _template_roots() -> list[Path]:
    roots: list[Path] = []
    # 1. Per-app templates directories.
    for app_config in apps.get_app_configs():
        # Only scan apps inside our project root (skip site-packages).
        app_path = Path(app_config.path)
        if not str(app_path).startswith(str(settings.BASE_DIR)):
            continue
        templates_dir = app_path / "templates"
        if templates_dir.is_dir():
            roots.append(templates_dir)
    # 2. Project-level TEMPLATES["DIRS"] entries.
    for tpl_cfg in getattr(settings, "TEMPLATES", []):
        for d in tpl_cfg.get("DIRS", []) or []:
            d_path = Path(d)
            if d_path.is_dir():
                roots.append(d_path)
    return roots


def _find_multiline_comment_lines(text: str) -> list[int]:
    """Return 1-based line numbers where a ``{#`` opens but doesn't
    close on the same line.

    Conservative: doesn't try to parse template syntax. Just scans
    line-by-line. False positives are unlikely because ``{#`` appearing
    inside template text but NOT closed on the same line is exactly the
    bug we want to flag.
    """
    bad: list[int] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Walk every occurrence on this line. A line may legitimately
        # have several short ``{# .. #}`` comments; we only flag when
        # an opener has no matching closer on the same line.
        col = 0
        while True:
            open_idx = line.find("{#", col)
            if open_idx == -1:
                break
            close_idx = line.find("#}", open_idx + 2)
            if close_idx == -1:
                bad.append(lineno)
                break
            col = close_idx + 2
    return bad


@register(Tags.templates)
def check_no_multiline_django_comments(app_configs, **kwargs):
    """Refuse to boot if any template has a multi-line ``{# #}`` block.

    Django's ``{# #}`` short-form comment is SINGLE-LINE only -- the
    parser ignores the ``{#`` token entirely when the matching ``#}``
    isn't on the same line, which makes the rest of the would-be
    comment render as visible page text. Use ``{% comment %} ...
    {% endcomment %}`` for multi-line blocks.

    Returns Error (deploy-blocking). ``manage.py check`` exits non-zero;
    the NFSN gunicorn boot script aborts; runserver refuses to start.
    """
    errors = []
    for root in _template_roots():
        for path in root.rglob("*.html"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            bad = _find_multiline_comment_lines(text)
            if not bad:
                continue
            preview = ", ".join(str(n) for n in bad[:5])
            if len(bad) > 5:
                preview += f", ... ({len(bad)} total)"
            errors.append(Error(
                "Multi-line Django template comment in "
                f"{path.relative_to(settings.BASE_DIR)} (line(s) {preview}).",
                hint=(
                    "Django only parses {# ... #} comments when both delimiters "
                    "are on the same line. Multi-line {# ... #} bleeds into "
                    "rendered page output. Replace with "
                    "{% comment %} ... {% endcomment %}."
                ),
                obj=str(path),
                id="opinions.E001",
            ))
    return errors
