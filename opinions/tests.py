"""Tests for the deterministic parsing layer.

These are SimpleTestCase (no DB) on purpose -- the extractors are pure
functions over text, and keeping them DB-free means ``manage.py test
opinions`` runs in under a second and can be run on prod without
touching the shared MariaDB.
"""
from django.test import SimpleTestCase

from opinions.parsing.statutes import bare_cite_slugs, extract_statutes
from opinions.parsing.statutes_mn import bare_slug_candidates, extract


def slugs(text):
    return {c.reference_slug for c in extract(text)}


class MinnesotaStatuteExtractionTests(SimpleTestCase):
    """The abbreviated and long forms must normalize onto ONE slug.

    Both house styles appear in the same corpus and often in the same
    opinion ("Minnesota Statutes section 563.01" on first reference,
    "Minn. Stat. § 563.01" thereafter). If they produced different slugs
    the citation graph would fragment by typography.
    """

    def test_abbreviated_forms(self):
        self.assertEqual(slugs("Minn. Stat. § 609.185"), {"minn.stat.609.185"})
        self.assertEqual(slugs("Minn.Stat. 609.185"), {"minn.stat.609.185"})
        self.assertEqual(slugs("Minn. Stat. ch. 169"), {"minn.stat.ch.169"})

    def test_long_form_is_captured(self):
        # Regression: this was out of scope on an unmeasured "rare in
        # appellate prose" assumption. It appears in 458 of 1,200 sampled
        # MN opinions, 47 of which use it EXCLUSIVELY.
        self.assertEqual(
            slugs("Minnesota Statutes section 563.01"), {"minn.stat.563.01"})
        self.assertEqual(
            slugs("Minnesota Statutes, section 606.01"), {"minn.stat.606.01"})

    def test_long_and_short_agree_on_one_slug(self):
        self.assertEqual(
            slugs("Minnesota Statutes section 563.01, and later Minn. Stat. "
                  "§ 563.01 again"),
            {"minn.stat.563.01"},
        )

    def test_subdivision_spellings(self):
        for text in (
            "Minn. Stat. § 609.185, subd. 1",
            "Minn. Stat. § 609.185, subdivision 1",
            "Minnesota Statutes section 609.185, subdivision 1 (2024)",
        ):
            with self.subTest(text=text):
                self.assertEqual(slugs(text), {"minn.stat.609.185.subd.1"})

    def test_space_before_comma_still_finds_subdivision(self):
        # pypdf artifact, common enough to matter. The old ",\s*subd\."
        # missed these and stored a subdivision cite at section
        # granularity -- a wrong answer, not a missing one.
        self.assertEqual(
            slugs("Minn. Stat. § 563.01 , subd. 3(a) (2018)"),
            {"minn.stat.563.01.subd.3"},
        )

    def test_subdivision_RANGES_match_section_only(self):
        # We cannot store a range, so we must not invent a cite to its
        # first member. Section-level capture is the honest outcome.
        for text in (
            "Minn. Stat. § 563.01, subds. 2-3(a), 7 (2020)",
            "Minn. Stat. § 563.01, subdivisions 2-3",
        ):
            with self.subTest(text=text):
                self.assertEqual(slugs(text), {"minn.stat.563.01"})

    def test_does_not_fire_on_prose_or_on_rules(self):
        self.assertEqual(slugs("the Minnesota Statutes are codified annually"), set())
        # A court RULE is not a statute. 109.02 is Minn. R. Civ. App. P.
        self.assertEqual(slugs("Minn. R. Civ. App. P. 109.02"), set())
        self.assertEqual(slugs("Minn. R. Gen. Prac. 109.01 and 109.02"), set())

    def test_dispatcher_routes_by_state(self):
        self.assertEqual(
            {c.reference_slug for c in extract_statutes("MN", "Minn. Stat. § 609.185")},
            {"minn.stat.609.185"},
        )
        self.assertEqual(extract_statutes("ZZ", "Minn. Stat. § 609.185"), [])


class BareCiteRoutingTests(SimpleTestCase):
    """A search for a bare section number should route, not text-search.

    InnoDB splits "563.01" at the period and drops "01" (under
    innodb_ft_min_token_size = 3), so the text index physically cannot
    answer this query. Routing to the structured statute page is the
    only correct answer.
    """

    def test_routes_plain_and_marked_numbers(self):
        self.assertEqual(bare_slug_candidates("563.01"), ["minn.stat.563.01"])
        self.assertEqual(bare_slug_candidates("§ 563.01"), ["minn.stat.563.01"])
        self.assertEqual(bare_slug_candidates("section 609.185"), ["minn.stat.609.185"])

    def test_subdivision_falls_back_to_the_section(self):
        self.assertEqual(
            bare_slug_candidates("563.01, subd. 3"),
            ["minn.stat.563.01.subd.3", "minn.stat.563.01"],
        )

    def test_bare_chapter_number_does_NOT_route(self):
        # "169" is also a page number, a year fragment and a dollar
        # figure. A wrong redirect is worse than a search.
        self.assertEqual(bare_slug_candidates("169"), [])
        self.assertEqual(bare_slug_candidates("2024"), [])

    def test_only_a_WHOLE_query_routes(self):
        self.assertEqual(bare_slug_candidates("negligence 563.01 standard"), [])
        self.assertEqual(bare_slug_candidates("in forma pauperis"), [])

    def test_dispatcher_is_optional_per_state(self):
        # States without the hook keep the old behavior rather than error.
        self.assertEqual(bare_cite_slugs("MN", "563.01"), ["minn.stat.563.01"])
        self.assertEqual(bare_cite_slugs("ZZ", "563.01"), [])
        self.assertEqual(bare_cite_slugs("MN", ""), [])
        self.assertEqual(bare_cite_slugs(None, "563.01"), [])


class RemovalRequestIsAdminOnlyTests(SimpleTestCase):
    """RemovalRequest must never reach a public surface.

    The model records that a named person -- usually the subject of the
    opinion -- asked us to take a page down. That is internal operational
    context; publishing it would broadcast the very association the
    requester objected to, which is the worst possible failure mode for
    this particular table.

    The property currently holds because ``admin.py`` is the only module
    that queries the model. This test makes that structural rather than
    remembered: add a read anywhere public and the suite fails.
    """

    PUBLIC_MODULES = [
        "opinions/views.py",
        "opinions/mcp.py",
        "opinions/sitemaps.py",
        "opinions/context_processors.py",
        "opinions/admin_views.py",
    ]
    NEEDLES = ("RemovalRequest", "removal_request")

    def _sources(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in self.PUBLIC_MODULES:
            path = os.path.join(root, rel)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    yield rel, fh.read()
        tpl = os.path.join(root, "opinions", "templates")
        for dirpath, _dirs, files in os.walk(tpl):
            for name in files:
                if not name.endswith(".html"):
                    continue
                path = os.path.join(dirpath, name)
                # The admin templates live under templates/admin/ and are
                # allowed to reference it.
                if os.sep + "admin" + os.sep in path:
                    continue
                with open(path, encoding="utf-8") as fh:
                    yield os.path.relpath(path, root), fh.read()

    def test_no_public_module_or_template_references_it(self):
        for rel, src in self._sources():
            for needle in self.NEEDLES:
                self.assertNotIn(
                    needle, src,
                    msg=("%s references %r. Removal requests are admin-only: "
                         "publishing that a person asked for takedown "
                         "broadcasts the association they objected to."
                         % (rel, needle)),
                )
