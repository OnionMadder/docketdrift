"""Tests for the deterministic parsing layer.

These are SimpleTestCase (no DB) on purpose -- the extractors are pure
functions over text, and keeping them DB-free means ``manage.py test
opinions`` runs in under a second and can be run on prod without
touching the shared MariaDB.
"""
from django.test import SimpleTestCase

from opinions.parsing.rules import extract_rules, rule_set_label
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


class MinnesotaRuleExtractionTests(SimpleTestCase):
    """Court rules are a different record type from statutes.

    Every case below is drawn from text measured on prod (1,200 recent
    MN opinions, 4,148 full-form cites), not invented. The fixtures that
    matter most are the ones that were NOT in the planned vocabulary:
    Minn. R. Evid., the spelled-out long form, and administrative rules.
    """

    def refs(self, text):
        return extract_rules("MN", text)

    def slugs(self, text):
        return {r.reference_slug for r in self.refs(text)}

    def test_abbreviated_court_rule_forms(self):
        self.assertEqual(self.slugs("Minn. R. Civ. App. P. 109.02"),
                         {"minn.r.civ.app.p.109.02"})
        self.assertEqual(self.slugs("Minn. R. Civ. P. 12.02"),
                         {"minn.r.civ.p.12.02"})
        self.assertEqual(self.slugs("Minn. R. Crim. P. 27.03"),
                         {"minn.r.crim.p.27.03"})

    def test_evidence_rules_are_in_scope(self):
        # A TOP-4 rule set (557 measured cites) that was missing from the
        # vocabulary this module was planned from. Shipping without it
        # would have been a silent hole, not a visible gap.
        self.assertEqual(self.slugs("Minn. R. Evid. 404(b)(1)"),
                         {"minn.r.evid.404"})
        self.assertEqual(self.slugs("Minn. R. Evid. 801(c)"),
                         {"minn.r.evid.801"})

    def test_spelled_out_long_form_normalizes_onto_one_slug(self):
        # ~11% of real cites. The statute extractor excluded its own long
        # form on an unmeasured assumption and capped that graph; both
        # spellings must land on ONE slug or the graph fragments by
        # typography.
        self.assertEqual(self.slugs("Minnesota Rules of Civil Procedure 12.02"),
                         {"minn.r.civ.p.12.02"})
        self.assertEqual(
            self.slugs("Minnesota Rules of Civil Procedure 12.02 and later "
                       "Minn. R. Civ. P. 12.02 again"),
            {"minn.r.civ.p.12.02"},
        )
        self.assertEqual(self.slugs("Minnesota Rules of Evidence 404"),
                         {"minn.r.evid.404"})

    def test_longest_rule_set_match_wins(self):
        # "Civ. App. P." must not be shredded into "Civ. P." -- appellate
        # rule 103.03 and civil rule 103.03 are different rules.
        refs = self.refs("Minn. R. Civ. App. P. 103.03(b)")
        self.assertEqual([r.rule_set for r in refs], ["civ.app.p"])

    def test_subdivision_and_subsection_are_separated(self):
        (ref,) = self.refs("Minn. R. Civ. App. P. 136.01, subd. 1(c)")
        self.assertEqual(ref.rule_number, "136.01")
        self.assertEqual(ref.subdivision, "1")
        self.assertEqual(ref.subsection, "(c)")
        self.assertEqual(ref.reference_slug, "minn.r.civ.app.p.136.01.subd.1")
        self.assertEqual(ref.reference_display,
                         "Minn. R. Civ. App. P. 136.01, subd. 1(c)")

    def test_edition_year_is_not_a_subsection(self):
        # Found by reading real output: `Minn. R. 3310.2912 (2025)` was
        # storing 2025 as a subsection. It is the rule's edition year,
        # exactly as statutes carry `(2024)`.
        (ref,) = self.refs("Minn. R. 3310.2912 (2025)")
        self.assertEqual(ref.subsection, "")
        self.assertEqual(ref.reference_display, "Minn. R. 3310.2912")
        (ref,) = self.refs("Minn. R. Civ. P. 12.02 (2020)")
        self.assertEqual(ref.subsection, "")

    def test_full_subsection_chain_is_kept(self):
        # The court that wrote 103(a)(2) did not write 103(a); keeping
        # only the first group cites a broader provision than the one
        # relied on, which is a misstated citation.
        (ref,) = self.refs("Minn. R. Evid. 103(a)(2)")
        self.assertEqual(ref.subsection, "(a)(2)")
        self.assertEqual(ref.reference_display, "Minn. R. Evid. 103(a)(2)")

    def test_administrative_rules_are_not_court_rules(self):
        # Minn. R. 3310.2921 is a DEED unemployment-hearing rule; 8210.0600
        # governs absentee ballots. They share the "Minn. R." abbreviation
        # with the rules of civil procedure and nothing else.
        (ref,) = self.refs("Minn. R. 3310.2921 (2025)")
        self.assertEqual(ref.rule_set, "admin")
        self.assertEqual(ref.reference_slug, "minn.r.admin.3310.2921")
        (ref,) = self.refs("Minn. R. 8210.0600, subp. 1b")
        self.assertEqual(ref.rule_set, "admin")
        self.assertEqual(ref.subdivision, "1b")
        self.assertIn("subp.", ref.reference_display)

    def test_boilerplate_disclaimer_is_flagged_not_dropped(self):
        # 31% of all rule cites are this one string at the top of every
        # unpublished opinion. Counted plainly it would make 136.01 the
        # most-cited rule in Minnesota; dropped, we would be discarding
        # the court's own text.
        disclaimer = ("This opinion is nonprecedential except as provided by "
                      "Minn. R. Civ. App. P. 136.01, subd. 1(c).")
        (ref,) = self.refs(disclaimer)
        self.assertTrue(ref.is_boilerplate)

    def test_the_same_rule_cited_substantively_is_NOT_flagged(self):
        # 123 measured occurrences of 136.01 sit outside the disclaimer.
        # Excluding by rule number would delete every one of them.
        body = ("The court considered whether Minn. R. Civ. App. P. 136.01, "
                "subd. 1(c) permits citation of the earlier order.")
        (ref,) = self.refs(body)
        self.assertFalse(ref.is_boilerplate)

    def test_does_not_fire_on_prose_or_on_statutes(self):
        # Both of these matched a loose probe and are why the anchor
        # requires "R." or the whole word "Rules".
        self.assertEqual(self.refs("the Minnesota Real Estate 101 course"), [])
        self.assertEqual(self.refs("the Minnesota River in townships 12"), [])
        # A statute is not a rule. 609.185 is Minn. Stat., not Minn. R.
        self.assertEqual(self.refs("Minn. Stat. § 609.185, subd. 1"), [])

    def test_subdivision_RANGES_match_the_rule_only(self):
        # We cannot store a range and must not invent a cite to its first
        # member -- same rule as the statute extractor.
        (ref,) = self.refs("Minn. R. Civ. P. 12.02, subds. 2-3")
        self.assertEqual(ref.subdivision, "")
        self.assertEqual(ref.reference_slug, "minn.r.civ.p.12.02")

    def test_dispatcher_is_per_state_and_refuses_to_guess(self):
        self.assertEqual(extract_rules("ZZ", "Minn. R. Civ. P. 12.02"), [])
        self.assertEqual(extract_rules("MN", ""), [])
        # No label beats an invented one (the A.R.S. "section 13.1103" bug).
        self.assertEqual(rule_set_label("MN", "civ.p"), "Minn. R. Civ. P.")
        self.assertEqual(rule_set_label("MN", "nonsense.set"), "")
        self.assertEqual(rule_set_label("ZZ", "civ.p"), "")


class LetteredChapterTests(SimpleTestCase):
    """Minnesota chapters carry letter suffixes, and they are identities.

    Chapter 518 is Marriage Dissolution; 518B is the Domestic Abuse Act.
    Until 2026-09-20 the extractor stopped the chapter at the digits and
    stored 518B.01 as `minn.stat.518` -- filing every Order for
    Protection case under dissolution, and leaving the Domestic Abuse
    Act with no page. A wrong answer, not a missing one.
    """

    def test_the_bug_itself(self):
        self.assertEqual(slugs("Minn. Stat. § 518B.01, subd. 2"),
                         {"minn.stat.518b.01.subd.2"})
        (cite,) = extract("Minn. Stat. § 518B.01, subd. 2")
        self.assertEqual(cite.chapter, "518B")
        self.assertEqual(cite.section, "01")
        self.assertEqual(cite.reference_display, "Minn. Stat. § 518B.01, subd. 2")

    def test_every_affected_chapter_family(self):
        # Measured as really present in the corpus; all core
        # self-represented subject matter.
        for text, want in [
            ("Minn. Stat. § 260C.301, subd. 1(b)", "minn.stat.260c.301.subd.1"),
            ("Minn. Stat. § 253B.18", "minn.stat.253b.18"),
            ("Minn. Stat. § 169A.20", "minn.stat.169a.20"),
            ("Minn. Stat. § 504B.285", "minn.stat.504b.285"),
            ("Minn. Stat. § 518A.39", "minn.stat.518a.39"),
            ("Minn. Stat. § 609A.02", "minn.stat.609a.02"),
            ("Minn. Stat. § 245C.15", "minn.stat.245c.15"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(slugs(text), {want})

    def test_lettered_chapter_in_the_long_form_too(self):
        # The long form is ~11% of cites; it had the identical defect.
        self.assertEqual(
            slugs("Minnesota Statutes section 518B.01, subdivision 2"),
            {"minn.stat.518b.01.subd.2"})
        self.assertEqual(
            slugs("Minnesota Statutes section 518B.01, subdivision 2 and later "
                  "Minn. Stat. § 518B.01, subd. 2"),
            {"minn.stat.518b.01.subd.2"})

    def test_chapter_only_lettered(self):
        # Chapter-only keeps the "ch." marker -- minn.stat.ch.518B is a
        # DIFFERENT scope from minn.stat.518b.01, and the statute page's
        # roll-up is gated on that distinction (rolling every section up
        # under its chapter is a different feature with a different
        # meaning). My first expectation here was wrong; the code was
        # right.
        self.assertEqual(slugs("Minn. Stat. ch. 518B"), {"minn.stat.ch.518b"})
        self.assertEqual(slugs("Minn. Stat. chapter 260C"), {"minn.stat.ch.260c"})

    def test_slug_is_lowercase_display_is_uppercase(self):
        # URLs stay case-stable; the display matches how the legislature
        # and the courts write it.
        (cite,) = extract("Minn. Stat. § 518b.01")
        self.assertEqual(cite.reference_slug, "minn.stat.518b.01")
        self.assertEqual(cite.reference_display, "Minn. Stat. § 518B.01")

    def test_unlettered_chapters_are_UNCHANGED(self):
        # The regression that matters: this fix must not disturb the
        # 4,987 sections already extracted correctly.
        self.assertEqual(slugs("Minn. Stat. § 609.185"), {"minn.stat.609.185"})
        self.assertEqual(slugs("Minn. Stat. § 609.185, subd. 1"),
                         {"minn.stat.609.185.subd.1"})
        self.assertEqual(slugs("Minn. Stat. § 645.16"), {"minn.stat.645.16"})
        self.assertEqual(slugs("Minn. Stat. ch. 169"), {"minn.stat.ch.169"})
        self.assertEqual(slugs("Minn. Stat. § 563.01 , subd. 3(a) (2018)"),
                         {"minn.stat.563.01.subd.3"})

    def test_a_lettered_chapter_routes_from_a_bare_query(self):
        self.assertEqual(bare_slug_candidates("518B.01"), ["minn.stat.518b.01"])
        self.assertEqual(bare_slug_candidates("518B.01, subd. 2"),
                         ["minn.stat.518b.01.subd.2", "minn.stat.518b.01"])


class StatuteBoilerplateTests(SimpleTestCase):
    """The nonprecedential notice is flagged per occurrence, never by number.

    Every unpublished Minnesota opinion opens by reciting Minn. Stat.
    480A.08, subd. 3 -- the statute restricting citation of it. Counted
    plainly that was 7,436 occurrences and made a publication-status
    footer the most-cited statute in Minnesota, 3.2x section 645.16.

    Measured on prod 2026-09-21: 96.6% of its occurrences sit in the
    first 400 characters (next MN statute: 73.8%), the cue fires on
    95.5% of a 400-occurrence random sample, and NONE of the 18 misses
    sit in the head -- every one read by hand was a court genuinely
    citing the statute. Those 18 are what these tests protect.
    """

    def one(self, text):
        found = [c for c in extract(text)
                 if c.reference_slug.startswith("minn.stat.480a.08")]
        self.assertEqual(len(found), 1, f"expected one 480A.08 cite in {text!r}")
        return found[0]

    def test_the_disclaimer_is_flagged(self):
        # The real sentence, as it appears at the top of the document.
        disclaimer = ("This opinion will be unpublished and may not be cited "
                      "except as provided by Minn. Stat. § 480A.08, subd. 3 (2024).")
        self.assertTrue(self.one(disclaimer).is_boilerplate)

    def test_the_nonprecedential_spelling_is_flagged(self):
        disclaimer = ("This opinion is nonprecedential except as provided by "
                      "Minn. Stat. § 480A.08, subd. 3 (2022).")
        self.assertTrue(self.one(disclaimer).is_boilerplate)

    def test_a_GENUINE_cite_to_the_same_statute_is_NOT_flagged(self):
        # Verbatim shape of the real misses: the court relying on
        # 480A.08 for the proposition that unpublished opinions carry no
        # precedential weight. 18 of 400 sampled occurrences look like
        # this, and excluding by statute number would delete every one.
        body = ("Appellant relies on two unpublished decisions of this court. "
                "Unpublished opinions of this court are not precedential. "
                "Minn. Stat. § 480A.08, subd. 3 (2020).")
        self.assertFalse(self.one(body).is_boilerplate)

    def test_the_cue_must_stay_NARROW(self):
        # The trap this test exists to prevent: widening the cue to
        # "unpublished" or "not precedential" would flag the genuine
        # citations above. Neither phrase may fire on its own.
        for phrase in ("Unpublished opinions of this court are not precedential.",
                       "The parties cited several unpublished decisions."):
            text = phrase + " Minn. Stat. § 480A.08, subd. 3 (2019)."
            self.assertFalse(
                self.one(text).is_boilerplate,
                f"cue wrongly fired after {phrase!r}")

    def test_the_flag_is_scoped_to_ONE_statute(self):
        # A disclaimer-shaped sentence must not bleed onto a neighbouring
        # statute. Only 480A.08, subd. 3 is the citation-restriction
        # statute; 480A.08 bare and 480A.06 are different provisions.
        text = ("This opinion will be unpublished and may not be cited except "
                "as provided by Minn. Stat. § 480A.06, subd. 3 (2024).")
        (cite,) = [c for c in extract(text)
                   if c.reference_slug.startswith("minn.stat.480a.06")]
        self.assertFalse(cite.is_boilerplate)

    def test_ordinary_statutes_are_never_flagged(self):
        # The default must hold for the other 636K rows, including when
        # the opinion's own header disclaimer sits right beside them.
        text = ("This opinion will be unpublished and may not be cited except "
                "as provided by Minn. Stat. § 480A.08, subd. 3 (2024). "
                "Appellant was convicted under Minn. Stat. § 609.185.")
        by_slug = {c.reference_slug: c for c in extract(text)}
        self.assertTrue(by_slug["minn.stat.480a.08.subd.3"].is_boilerplate)
        self.assertFalse(by_slug["minn.stat.609.185"].is_boilerplate)

    def test_other_states_carry_the_field_as_False(self):
        # AZ/NH/LA have no equivalent notice -- measured, not assumed:
        # their most head-concentrated statute is 55.8% against MN's
        # 96.6%, and those are offense statutes named in the opening
        # sentence. The shared dataclass default must still be present.
        for state, text in (("AZ", "A.R.S. § 13-1105"),
                            ("NH", "RSA 632-A:2"),
                            ("LA", "La. R.S. 14:30.1")):
            cites = extract_statutes(state, text)
            self.assertTrue(cites, f"no cite extracted for {state}")
            for c in cites:
                self.assertFalse(c.is_boilerplate)


class IndexNowKeyViewTests(SimpleTestCase):
    """The key file is the ownership proof. A malformed or missing key must
    404 -- serving a bad one makes every ping fail verification."""

    def _get(self, key):
        from django.test import RequestFactory, override_settings
        from opinions.views import indexnow_key
        with override_settings(INDEXNOW_KEY=key):
            return indexnow_key(RequestFactory().get("/indexnow-key.txt"))

    def test_serves_valid_key_verbatim(self):
        resp = self._get("0123456789abcdef0123456789abcdef")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), "0123456789abcdef0123456789abcdef")

    def test_missing_or_malformed_key_404s(self):
        from django.http import Http404
        for bad in ("", "short", "has space in it", "<script>alert(1)</script>"):
            with self.assertRaises(Http404):
                self._get(bad)


class OpinionAbsoluteUrlTests(SimpleTestCase):
    """get_absolute_url feeds the MCP `url` field an agent will follow; a
    raw space or comma from the docket must be percent-encoded."""

    def test_docket_with_space_is_encoded(self):
        from opinions.models import Court, Opinion, State
        op = Opinion(case_number="No. 35908", court=Court(state=State(code="LA", slug="la")))
        self.assertEqual(op.get_absolute_url(),
                         "https://la.docketdrift.com/opinion/No.%2035908/")

    def test_plain_docket_unchanged(self):
        from opinions.models import Court, Opinion, State
        op = Opinion(case_number="A24-1561", court=Court(state=State(code="MN", slug="mn")))
        self.assertEqual(op.get_absolute_url(),
                         "https://mn.docketdrift.com/opinion/A24-1561/")



class LouisianaThirdCircuitPanelTests(SimpleTestCase):
    """The Third Circuit writes its panel as full names with no 'Judge'
    prefix and no parens; rehearing notices use 'BEFORE JUDGES:' + 'Hon.'
    lines. Neither matched before, so the circuit had zero panel votes."""

    def _panel(self, text):
        from opinions.parsing.la import LouisianaParser
        return LouisianaParser().parse(text).panel

    def test_bare_court_composed_of(self):
        text = ("STATE OF LOUISIANA\nCOURT OF APPEAL, THIRD CIRCUIT\n25-629\n"
                "STATE OF LOUISIANA VERSUS SAMMY CHEATHAM\n**********\n"
                "APPEAL FROM THE SIXTEENTH JUDICIAL DISTRICT COURT\n**********\n"
                "Court composed of Elizabeth A. Pickett, Jonathan W. Perry, and Wilbur L. Stiles,\n"
                "Judges.\nPICKETT, Judge.\nThe defendant, Sammy Cheatham, appeals his conviction.\n")
        self.assertEqual(self._panel(text),
                         ["Elizabeth A. Pickett", "Jonathan W. Perry", "Wilbur L. Stiles"])

    def test_before_judges_hon_lines(self):
        text = ("STATE OF LOUISIANA\nCOURT OF APPEAL, THIRD CIRCUIT\nP.O. Box 16577 Lake Charles LA 70616\n"
                "NOT DESIGNATED FOR PUBLICATION\nCA-0025-0298\n"
                "BEFORE JUDGES:\nHon. Shannon J. Gremillion\nHon. Gary J. Ortego\nHon. Wilbur L. Stiles\n"
                "As counsel of record in the captioned case, you are hereby notified that the "
                "application for rehearing filed by James Cureton has this day been DENIED.\n")
        self.assertEqual(self._panel(text),
                         ["Shannon J. Gremillion", "Gary J. Ortego", "Wilbur L. Stiles"])

    def test_fourth_circuit_parenthesized_form_still_wins(self):
        text = ("COURT OF APPEAL\nFOURTH CIRCUIT\nSTATE OF LOUISIANA\n2026-C-0149\n"
                "(Court composed of Judge Daniel L. Dysart, Judge Rosemary Ledet, Judge\nNakisha Ervin-Knott)\n"
                "DYSART, J.\nRelator seeks review of the trial court's ruling.\n")
        self.assertEqual(self._panel(text),
                         ["Daniel L. Dysart", "Rosemary Ledet", "Nakisha Ervin-Knott"])

    def test_bare_form_does_not_swallow_body_prose(self):
        text = ("COURT OF APPEAL, THIRD CIRCUIT\n24-101\n"
                "Court composed of Van H. Kyzar and Candyce G. Perret, Judges. Defendant, Robert Bolton, "
                "appeals the judgment. Bolton argues the trial court erred.\n")
        self.assertEqual(self._panel(text), ["Van H. Kyzar", "Candyce G. Perret"])


class ArizonaBylineNameTests(SimpleTestCase):
    """Two ways a sitting AZ judge silently lost every vote (2026-09-26):
    a typographic apostrophe in the opinion text, and a generational suffix
    read as the surname."""

    def test_curly_apostrophe_author_and_panel(self):
        from opinions.management.commands.resolve_judges import _extract_generic_byline
        text = ("IN THE\nARIZONA COURT OF APPEALS\nDIVISION TWO\n\nOPINION\n"
                "Judge O’Neil authored the opinion of the Court, in which "
                "Presiding Judge Gard and Judge Eckerstrom concurred.\n")
        g = _extract_generic_byline(text)
        self.assertEqual(g.author_last, "o'neil")
        self.assertIn("gard", g.panel_last)

    def test_curly_apostrophe_in_az_parser_author(self):
        from opinions.parsing.az import ArizonaParser
        text = ("IN THE\nARIZONA COURT OF APPEALS\nDIVISION TWO\n\nOPINION\n"
                "Judge O’Neil authored the opinion of the Court, in which "
                "Presiding Judge Gard and Judge Eckerstrom concurred.\n")
        self.assertIn("O'Neil", ArizonaParser().parse(text).author or "")

    def test_generational_suffix_is_not_the_surname(self):
        from opinions.management.commands.resolve_judges import (
            _extract_generic_byline, _last_name)
        self.assertEqual(_last_name("James B. Morse Jr."), "Morse")
        self.assertEqual(_last_name("Judge James B. Morse, Jr."), "Morse")
        text = ("IN THE\nARIZONA COURT OF APPEALS\nDIVISION ONE\n\nMEMORANDUM DECISION\n"
                "Presiding Judge Andrew J. Becke delivered the decision of the Court, in which "
                "Judge James B. Morse Jr. and Chief Judge Randall M. Howe joined.\n")
        g = _extract_generic_byline(text)
        self.assertEqual(g.author_last, "becke")
        self.assertIn("morse", g.panel_last)
        self.assertIn("howe", g.panel_last)


class MinnesotaSupremeBylineTests(SimpleTestCase):
    """Supreme captions put the author top-right on the same line as the
    court below; Justice Gaïtas also carries a non-ASCII letter."""

    def _author(self, text):
        from opinions.parsing.mn import MinnesotaParser
        return MinnesotaParser().parse(text).author or ""

    def test_author_after_court_of_appeals_prefix_with_diaeresis(self):
        text = ("STATE OF MINNESOTA\n\n            IN SUPREME COURT\n\n            A23-1948\n\n\n"
                "Court of Appeals                                   Gaïtas, J.\n"
                "                         Concurring in part, dissenting in part,\n"
                "                                       Hudson, C.J., Thissen, J.\n"
                "Jeremy McNitt,            Concurring in part, dissenting in part, Thissen, J.\n"
                "                                       Took no part, Procaccini, J.\n"
                "        Respondent/Cross-Appellant,\n\nvs.                       Filed: September 2, 2026\n")
        self.assertTrue(self._author(text).startswith("Gaïtas"), self._author(text))

    def test_author_after_county_prefix(self):
        text = ("STATE OF MINNESOTA\n\n            IN SUPREME COURT\n\n            A24-0100\n\n\n"
                "Hennepin County                                    Hudson, C.J.\n"
                "                                        Dissenting, Thissen, J.\n"
                "State of Minnesota,\n\n            Respondent,\n\nvs.       Filed: March 4, 2026\n")
        self.assertTrue(self._author(text).startswith("Hudson"), self._author(text))

    def test_sentence_internal_judge_list_is_not_a_byline(self):
        text = ("STATE OF MINNESOTA\nIN COURT OF APPEALS\nA25-0001\n\nState of Minnesota,\nRespondent,\nvs.\n"
                "John Doe,\nAppellant.\n\nFiled March 2, 2026\nAffirmed\nSMITH, Judge\n\n"
                "Considered and decided by Smith, Presiding Judge; Jones, Judge; and Brown, Judge.\n")
        self.assertTrue(self._author(text).lower().startswith("smith"), self._author(text))
