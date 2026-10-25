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


class BareRuleRoutingTests(SimpleTestCase):
    """A bare number can be a court rule; the parser only reads notation.

    Which rule SET (and whether it is a statute instead) is decided
    against the corpus by views._cite_destinations, and a collision is
    shown to the reader -- these tests hold the notation half.
    """

    def test_parses_court_and_admin_numbers(self):
        from opinions.parsing.rules import bare_rule_number
        self.assertEqual(bare_rule_number("MN", "109.02"), ("109.02", "109.02"))
        self.assertEqual(bare_rule_number("MN", "Rule 109.02"), ("109.02", "109.02"))
        self.assertEqual(bare_rule_number("MN", "136.01, subd. 1"),
                         ("136.01", "136.01, subd. 1"))
        self.assertEqual(bare_rule_number("MN", "3310.2921"), ("3310.2921", "3310.2921"))

    def test_undotted_and_mixed_queries_do_not_route(self):
        from opinions.parsing.rules import bare_rule_number
        # "404" is also a page number; same rule as bare statute chapters.
        self.assertIsNone(bare_rule_number("MN", "404"))
        self.assertIsNone(bare_rule_number("MN", "negligence 109.02"))
        self.assertIsNone(bare_rule_number("MN", "§ 52.01"))   # a statute marker
        self.assertIsNone(bare_rule_number("ZZ", "109.02"))    # no extractor

    def test_slug_candidates_per_set(self):
        from opinions.parsing.rules import bare_rule_slugs
        self.assertEqual(bare_rule_slugs("MN", "civ.app.p", "109.02"),
                         ["minn.r.civ.app.p.109.02"])
        self.assertEqual(bare_rule_slugs("MN", "civ.app.p", "136.01, subd. 1"),
                         ["minn.r.civ.app.p.136.01.subd.1", "minn.r.civ.app.p.136.01"])
        self.assertEqual(bare_rule_slugs("MN", "admin", "3310.2921"),
                         ["minn.r.admin.3310.2921"])

    def test_admin_number_never_truncates_into_a_court_rule(self):
        # "Civ. P. 3310.2921" partially matches as 331 -- a rule nobody cited.
        from opinions.parsing.rules import bare_rule_slugs
        self.assertEqual(bare_rule_slugs("MN", "civ.p", "3310.2921"), [])
        self.assertEqual(bare_rule_slugs("MN", "admin", "52.01"), [])


class ArizonaRuleExtractionTests(SimpleTestCase):
    """Shapes measured in real AZ text 2026-10-15, not written from memory."""

    def slugs(self, text):
        return [(r.reference_slug, r.reference_display)
                for r in extract_rules("AZ", text)]

    def test_spacing_variants_are_one_rule_set(self):
        for t in ("Ariz. R. Crim. P. 27.4.", "Ariz. R.Crim. P. 27.4.",
                  "Ariz.R.Crim.P. 27.4.",
                  "Arizona Rule of Criminal Procedure 27.4, which"):
            self.assertEqual(self.slugs(t)[0][0], "ariz.r.crim.p.27.4", t)

    def test_arcap_acronym_is_the_civil_appellate_rules(self):
        # 147 of 1,214 opinions cite ARCAP with no "Ariz. R." anchor at
        # all. One body of law, three spellings, one slug.
        for t in ("upon compliance with ARCAP 21(b), we award",
                  "See Ariz. R. Civ. App. P. 21(b).",
                  "Arizona Rules of Civil Appellate Procedure 21, but"):
            self.assertEqual(self.slugs(t)[0][0], "ariz.r.civ.app.p.21", t)
        self.assertEqual(self.slugs("Arcap 5 words"), [])

    def test_volume_number_is_never_a_rule(self):
        # "16 A.R.S." is the statute VOLUME the rules are printed in.
        # The cite is to Rule 50 (number-first form); never to "16".
        self.assertEqual(self.slugs("Rule 50, Ariz. R. Civ. P., 16 A.R.S., Pt. 1"),
                         [("ariz.r.civ.p.50", "Ariz. R. Civ. P. 50")])
        self.assertEqual(self.slugs("Ariz. R. Civ. P., 16 A.R.S., Pt. 1"), [])

    def test_whole_parenthetical_chain_kept_year_refused(self):
        self.assertEqual(self.slugs("See Ariz. R. Crim. P. 31.10(a)(7)(A) (x)"),
                         [("ariz.r.crim.p.31.10", "Ariz. R. Crim. P. 31.10(a)(7)(A)")])
        self.assertEqual(self.slugs("Ariz. R. Civ. P. 26.1 (2019) requires"),
                         [("ariz.r.civ.p.26.1", "Ariz. R. Civ. P. 26.1")])
        self.assertEqual(self.slugs("Arizona Rule of Evidence 404 (b) to"),
                         [("ariz.r.evid.404", "Ariz. R. Evid. 404(b)")])

    def test_procedure_rule_sets_with_extra_p(self):
        self.assertEqual(self.slugs("See Ariz. R.P. Juv. Ct. 64(D)(2).")[0][0],
                         "ariz.r.juv.ct.64")
        self.assertEqual(self.slugs("Ariz. R.P. Spec. Act. 1(a).")[0][0],
                         "ariz.r.spec.act.1")

    def test_number_before_the_named_set(self):
        # The commonest miss in real output, found by listing anchors the
        # first version did NOT extract.
        cases = {
            "under Rule 18.4(b) of the Arizona Rules of Criminal Procedure, a":
                ("ariz.r.crim.p.18.4", "Ariz. R. Crim. P. 18.4(b)"),
            "pursuant to Rule 24, Ariz. R. Civ. P., 16 A.R.S., Pt. 1":
                ("ariz.r.civ.p.24", "Ariz. R. Civ. P. 24"),
            "Rule 609, Arizona Rules of Evidence, 17A A.R.S.":
                ("ariz.r.evid.609", "Ariz. R. Evid. 609"),
            "with Rule 5(f), Arizona Rules of the Supreme Court, 17 A.R.S.":
                ("ariz.r.sup.ct.5", "Ariz. R. Sup. Ct. 5(f)"),
            "her compliance with Rule 21, ARCAP. CONCLUSION":
                ("ariz.r.civ.app.p.21", "Ariz. R. Civ. App. P. 21"),
        }
        for text, want in cases.items():
            self.assertEqual(self.slugs(text), [want], text)

    def test_defined_term_alias_before_the_number(self):
        self.assertEqual(
            self.slugs("filed pursuant to Arizona Rule of Criminal Procedure (“Rule”) 33.1."),
            [("ariz.r.crim.p.33.1", "Ariz. R. Crim. P. 33.1")])
        self.assertEqual(
            self.slugs("Arizona Rule of Civil Appellate Procedure (“ARCAP”) 21."),
            [("ariz.r.civ.app.p.21", "Ariz. R. Civ. App. P. 21")])

    def test_protective_order_rules(self):
        self.assertEqual(self.slugs("see also Ariz. R. Protective Order P. 23(e)(1).")[0][0],
                         "ariz.r.prot.order.p.23")
        self.assertEqual(self.slugs("See Ariz. R. Protective Ord. P. 25(b) (x)")[0][0],
                         "ariz.r.prot.order.p.25")

    def test_admin_code_is_its_own_set(self):
        self.assertEqual(self.slugs("A.A.C. R9-10-101(B)")[0][0], "ariz.r.admin.r9-10-101")
        self.assertEqual(self.slugs("Ariz. Admin. Code R2-19-119")[0][0],
                         "ariz.r.admin.r2-19-119")

    def test_arcap_21_recurrence_is_counted(self):
        # ARCAP 21 at the tail is the court applying the rule (costs on
        # compliance) -- a recurrence, counted. Measured, not assumed.
        refs = extract_rules("AZ", "we award costs upon compliance with ARCAP 21.")
        self.assertFalse(refs[0].is_boilerplate)

    def test_memorandum_decision_notice_is_flagged(self):
        for head in (
            "NOTICE: NOT FOR OFFICIAL PUBLICATION. UNDER ARIZONA RULE OF THE "
            "SUPREME COURT 111(c), THIS DECISION IS NOT PRECEDENTIAL AND MAY "
            "BE CITED ONLY AS AUTHORIZED BY RULE.",
            "NOTICE: NOT FOR PUBLICATION. UNDER ARIZ. R. SUP. CT. 111(c), THIS "
            "DECISION DOES NOT CREATE LEGAL PRECEDENT",
        ):
            refs = extract_rules("AZ", head)
            self.assertEqual(refs[0].reference_slug, "ariz.r.sup.ct.111", head)
            self.assertTrue(refs[0].is_boilerplate, head)

    def test_genuine_111_cites_are_not_flagged(self):
        # The cue fires on these too; they are courts DISCUSSING the rule,
        # outside the head -- why the gate is cue AND position here.
        body = "x" * 5000 + (" Grabinski is a memorandum decision; it may not be "
                             "cited for persuasive value. See Ariz. R. Sup. Ct. "
                             "111(c)(1)(C).")
        self.assertFalse(extract_rules("AZ", body)[0].is_boilerplate)
        # Head 111(g) without the cue: the court ordering depublication.
        refs = extract_rules("AZ", "the Court of Appeals' opinion shall not be "
                             "published, pursuant to Rule 111(g), Arizona Rules "
                             "of the Supreme Court.")
        self.assertEqual(refs[0].reference_slug, "ariz.r.sup.ct.111")
        self.assertFalse(refs[0].is_boilerplate)

    def test_bare_queries(self):
        from opinions.parsing.rules import bare_rule_number, bare_rule_slugs
        self.assertEqual(bare_rule_number("AZ", "32.1"), ("32.1", "32.1"))
        self.assertEqual(bare_rule_number("AZ", "Rule 60(b)"), ("60", "60(b)"))
        self.assertIsNone(bare_rule_number("AZ", "60"))   # a page number
        self.assertEqual(bare_rule_slugs("AZ", "evid", "404(b)"), ["ariz.r.evid.404"])
        self.assertEqual(bare_rule_slugs("AZ", "admin", "32.1"), [])


class NewHampshireRuleExtractionTests(SimpleTestCase):
    """Shapes measured in real NH text 2026-10-15."""

    def slugs(self, text):
        return [(r.reference_slug, r.reference_display)
                for r in extract_rules("NH", text)]

    def test_court_rules_carry_no_state_anchor(self):
        self.assertEqual(self.slugs("See Sup. Ct. R. 20(2). The"),
                         [("nh.r.sup.ct.20", "Sup. Ct. R. 20(2)")])
        self.assertEqual(self.slugs("argument. Supreme Court Rule 16(3)(b). That")[0][0],
                         "nh.r.sup.ct.16")
        self.assertEqual(self.slugs("see SUP. CT. R. 13(2)")[0][0], "nh.r.sup.ct.13")

    def test_lettered_numbers_are_distinct_rules(self):
        self.assertEqual(self.slugs("See Sup.Ct. R. 16-A (plain error rule)")[0][0],
                         "nh.r.sup.ct.16-a")
        self.assertEqual(self.slugs("Family Division Rule 1.25-A")[0][0],
                         "nh.r.fam.div.1.25-a")

    def test_superior_court_old_and_civil_rules_are_separate_sets(self):
        self.assertEqual(self.slugs("See Super. Ct. R. 59-A. One")[0][0], "nh.r.super.ct.59-a")
        self.assertEqual(self.slugs("Super. Ct. Civ. R. 51(c)")[0][0], "nh.r.super.ct.civ.51")

    def test_ranges_never_bind_their_first_member(self):
        self.assertEqual(self.slugs("Because Superior Court Rule 27-28 apply"), [])
        self.assertEqual(extract_rules("AZ", "Ariz. R. Crim. P. 26.1-26.3 govern"), [])

    def test_evidence_and_conduct_forms(self):
        for t in ("N.H. R. Ev. 613(c). If", "New Hampshire Rule of Evidence 613(c) x",
                  "N.H. R. EV. 613(c)."):
            self.assertEqual(self.slugs(t)[0][0], "nh.r.evid.613", t)
        self.assertEqual(self.slugs("New Hampshire Rule of Evidence (Rule) 702. *725")[0][0],
                         "nh.r.evid.702")
        self.assertEqual(self.slugs("N.H. R. PROF. CONDUCT 1.15. The")[0][0],
                         "nh.r.prof.conduct.1.15")

    def test_admin_rules_keep_the_agency_prefix(self):
        self.assertEqual(self.slugs("N.H. Admin. Rules, Env-Ws 386.58(g) (regs"),
                         [("nh.r.admin.env-ws.386.58", "N.H. Admin. R., Env-Ws 386.58(g)")])
        self.assertEqual(self.slugs("N.H. CODE ADMIN. R., Rab 202.06 (2007).")[0][0],
                         "nh.r.admin.rab.202.06")

    def test_false_friends(self):
        for t in ("See N.H.H.R. JOUR. 649 (1999).", "U.S. Sup. Ct. R. 10",
                  "United States Supreme Court Rule 10", "See UNIF. R. Crim. P. 473",
                  "Fed. R. CRIM. P. 8(a)"):
            self.assertEqual(self.slugs(t), [], t)

    def test_four_digit_rules_and_number_first(self):
        self.assertEqual(self.slugs("standards of New Hampshire Rules of Evidence 1002 and"),
                         [("nh.r.evid.1002", "N.H. R. Ev. 1002")])
        self.assertEqual(self.slugs("violation of Rule 1.5 of the New Hampshire Rules of "
                                    "Professional Conduct. Rule 1.5 states")[0][0],
                         "nh.r.prof.conduct.1.5")

    def test_minnesota_four_digit_evidence_rules_not_truncated(self):
        # Live bug until 2026-10-16: 1101 was stored as Rule 110.
        refs = extract_rules("MN", "Minn. R. Evid. 1101(a). It provides")
        self.assertEqual(refs[0].reference_slug, "minn.r.evid.1101")
        refs = extract_rules("MN", "Minnesota Rule of Evidence 1002. Monson")
        self.assertEqual(refs[0].reference_slug, "minn.r.evid.1002")
        self.assertEqual(extract_rules("MN", "Minn. R. Civ. P. 60.02-.03 apply"), [])

    def test_rehearing_notice_yields_no_row(self):
        self.assertEqual(self.slugs(
            "NOTICE: This opinion is subject to motions for rehearing under "
            "Rule 22 as well as formal revision before publication"), [])


class LouisianaRuleExtractionTests(SimpleTestCase):
    """Shapes measured in real LA text 2026-10-15."""

    def refs(self, text):
        return [(r.reference_slug, r.is_boilerplate) for r in extract_rules("LA", text)]

    def test_uniform_rules_every_separator(self):
        for sep in ("—", "-", " – ", "\x97", ""):
            t = "See Uniform Rules" + sep + "Courts of Appeal, Rule 2-12.4. FACTS"
            self.assertEqual(self.refs(t), [("la.r.urca.2-12.4", False)], repr(sep))
        self.assertEqual(self.refs("in violation of URCA, Rule 2-12.4(B)(1)."),
                         [("la.r.urca.2-12.4", False)])

    def test_not_designated_notice_is_flagged(self):
        self.assertEqual(self.refs(
            "This opinion is NOT DESIGNATED FOR PUBLICATION. Uniform Rules "
            "—Courts of Appeal, Rule 2-16.3. "),
            [("la.r.urca.2-16.3", True)])

    def test_more_uniform_rule_spellings_and_number_first(self):
        for t, want in (
            ("NOT DESIGNATED FOR PUBLICATION. Rule 2-16.3, Uniform Rules, Courts of Appeal. ",
             [("la.r.urca.2-16.3", True)]),
            ("See Rule 2-12.4, Uniform Rules — Courts of Appeal. . These",
             [("la.r.urca.2-12.4", False)]),
            ("Uniform Rules of Louisiana Courts of Appeal, Rule 1-3 bars",
             [("la.r.urca.1-3", False)]),
            ("pursuant to Uniform Rules\x97Courts of Appeal 2-12.4, no further",
             [("la.r.urca.2-12.4", False)]),
            ("NOT DESIGNATED FOR PUBLICATION. Uniform Rules―Courts of Appeal, Rule 2-16.3. ",
             [("la.r.urca.2-16.3", True)]),
        ):
            self.assertEqual(self.refs(t), want, repr(t))

    def test_clerk_delivery_certificate_is_flagged(self):
        t = ("HAS BEEN DELIVERED IN ACCORDANCE WITH UNIFORM RULES - COURT OF "
             "APPEAL, RULE 2-16.4 AND 2-16.5 THIS DAY FEBRUARY 2, 2020")
        self.assertEqual(self.refs(t)[0], ("la.r.urca.2-16.4", True))
        # The same rule cited by a court in an argument is counted.
        self.assertEqual(self.refs("the timing rules of Uniform Rules—Courts of "
                                   "Appeal, Rule 4-6 govern the transmission of"),
                         [("la.r.urca.4-6", False)])

    def test_rule_iv_panel_footnote_is_flagged(self):
        self.assertEqual(self.refs(
            "NOTES [*] Pursuant to Rule IV, Part 2, § 3, Watson, J. was not on panel."),
            [("la.r.sup.ct.iv.part2.3", True)])
        self.assertEqual(self.refs("J., not on panel. Rule IV, Part II, § 3. CALOGERO"),
                         [("la.r.sup.ct.iv.part2.3", True)])

    def test_supreme_court_roman_rules_keep_their_section(self):
        self.assertEqual(self.refs("pursuant to Supreme Court Rule XIX, § 19, pending"),
                         [("la.r.sup.ct.xix.19", False)])
        self.assertEqual(self.refs("Untimely filed pursuant to La.S.Ct. Rule X § 5. R"),
                         [("la.r.sup.ct.x.5", False)])

    def test_professional_conduct_both_orders(self):
        self.assertEqual(self.refs("respondent violated Rule 8.4(a) of the Rules of "
                                   "Professional Conduct."),
                         [("la.r.prof.conduct.8.4", False)])
        self.assertEqual(self.refs("the following Rules of Professional Conduct: Rules 1.3 (n)"),
                         [("la.r.prof.conduct.1.3", False)])

    def test_statutes_and_bare_rules_are_not_rules(self):
        for t in ("a violation of La. R.S. 14:43. The", "Rule X of the Civil Code",
                  "Rule 2-12.4 bare"):
            self.assertEqual(self.refs(t), [], t)


class ArizonaStatuteV2GrammarTests(SimpleTestCase):
    """Forms measured 2026-10-16: v1 missed a cite in 46% of AZ opinions."""

    def slugs(self, text):
        return [s.reference_slug for s in extract_statutes("AZ", text)]

    def test_measured_forms(self):
        cases = {
            "attorney fees. A.R.S. § 42- 18206; see": ["ars.42-18206"],
            "pursuant to A.R.S. section 12-2101(B) (1994). We": ["ars.12-2101.b"],
            "See AR.S. § 12-1801; Burton": ["ars.12-1801"],
            "Arizona Revised Statutes (“A.R.S.”) section 12- 2101(A)(1) x": ["ars.12-2101.a"],
            "application of A.R.S. 17-309(A)(1) and": ["ars.17-309.a"],
            "within the purview of A.R.S. Sec. 38-1004(A); F": ["ars.38-1004.a"],
            "A. R.S. § 44 — 1401 prior to": ["ars.44-1401"],
            "see A.R.S § 12-1501. A": ["ars.12-1501"],
            "[A.R.S.] § 13-404(A) is": ["ars.13-404.a"],
        }
        for text, want in cases.items():
            self.assertEqual(self.slugs(text), want, text)

    def test_lists_inherit_the_title(self):
        self.assertEqual(self.slugs("fees under A.R.S. §§ 12-341 and 42-18206. ¶6"),
                         ["ars.12-341", "ars.42-18206"])
        self.assertEqual(self.slugs("Arizona Revised Statutes (“A.R.S.”) sections "
                                    "13-4031 and -4033(A)(1). "),
                         ["ars.13-4031", "ars.13-4033.a"])
        self.assertEqual(self.slugs("Arizona Revised Statutes (A.R.S.) §§ 12-120.21(A)(1) "
                                    "(2016) and -2101 (2016). 5"),
                         ["ars.12-120.21.a", "ars.12-2101"])

    def test_ranges_and_prose_numbers(self):
        self.assertEqual(self.slugs("A.R.S. §§ 13-1401 to -1410 define"), [])
        self.assertEqual(self.slugs("A.R.S. §§ 13-1401 through 13-1410"), [])
        self.assertEqual(self.slugs("sentenced under A.R.S. § 13-702 to 5 years"),
                         ["ars.13-702"])

    def test_volume_and_dockets_are_not_statutes(self):
        for t in ("pursuant to 16 A.R.S., Rules of Civil Procedure, Rule 60(c)4.",
                  "Procedure, 17A A.R.S. (1985 Supp.), and",
                  "docket 1 CA-CV 25-0606 filed"):
            self.assertEqual(self.slugs(t), [], t)

    def test_dotted_tails_never_truncate_the_section(self):
        # "(?![\d.]\d)" let backtracking stop at "40" in "25-408.1.4" --
        # a cite to a statute nobody named. The guard is (?!\d|\.\d).
        self.assertEqual(self.slugs("See A.R.S. § 25-408.1.4. ¶ 24"), [])
        self.assertNotIn("ars.25-40", self.slugs("A.R.S. § 25-408.1.4"))
        self.assertEqual(extract_rules("LA", "Uniform Rules—Courts of Appeal, Rule 1-345 x"), [])
        self.assertEqual(extract_rules("LA", "La. Dist. Ct. R. 9.14.2 then"), [])

    def test_v1_shapes_unchanged(self):
        self.assertEqual(self.slugs("A.R.S. § 13-1105(A)(2)"), ["ars.13-1105.a"])
        self.assertEqual(self.slugs("A.R.S. § 12-341.01(A)."), ["ars.12-341.01.a"])


class ArizonaBareStatuteRoutingTests(SimpleTestCase):
    def test_routes_plain_and_marked_numbers(self):
        self.assertEqual(bare_cite_slugs("AZ", "13-1105"), ["ars.13-1105"])
        self.assertEqual(bare_cite_slugs("AZ", "§ 13-1105(A)"),
                         ["ars.13-1105.a", "ars.13-1105"])
        self.assertEqual(bare_cite_slugs("AZ", "A.R.S. 28-1381"), ["ars.28-1381"])

    def test_docket_fragments_and_rules_do_not_route(self):
        self.assertEqual(bare_cite_slugs("AZ", "25-0606"), [])   # leading zero
        self.assertEqual(bare_cite_slugs("AZ", "1 CA-CV 25-0606"), [])
        self.assertEqual(bare_cite_slugs("AZ", "32.1"), [])
        self.assertEqual(bare_cite_slugs("AZ", "negligence 13-1105"), [])


class RuleSurfaceWiringTests(SimpleTestCase):
    """The rule surfaces exist and are labeled as rules, not statutes."""

    def test_get_rule_is_registered_and_annotated(self):
        from opinions.mcp import _TOOL_BY_NAME
        tool = _TOOL_BY_NAME["get_rule"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertNotIn("statute", tool["title"].lower())

    def test_rules_sitemap_route(self):
        from django.urls import reverse
        self.assertEqual(reverse("opinions:sitemap_rules"), "/sitemap-rules.xml")


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


class ResolverCourtTiebreakTests(SimpleTestCase):
    """Two seated judges sharing a surname on different courts: the
    opinion's court decides; same court or no court stays a skip."""

    def _judges(self):
        from opinions.models import Judge
        return (Judge(pk=1, full_name="Rachael D. Johnson", court_id=11, is_currently_seated=True),
                Judge(pk=2, full_name="Marc E. Johnson", court_id=12, is_currently_seated=True))

    def test_picks_the_judge_on_the_opinions_court(self):
        from opinions.management.commands.resolve_judges import _pick_by_court
        a, b = self._judges()
        self.assertIs(_pick_by_court([a, b], 11), a)
        self.assertIs(_pick_by_court([a, b], 12), b)

    def test_no_court_or_other_court_or_same_court_is_a_skip(self):
        from opinions.management.commands.resolve_judges import _pick_by_court
        from opinions.models import Judge
        a, b = self._judges()
        self.assertIsNone(_pick_by_court([a, b], None))
        self.assertIsNone(_pick_by_court([a, b], 7))
        c = Judge(pk=3, full_name="Other Johnson", court_id=11, is_currently_seated=True)
        self.assertIsNone(_pick_by_court([a, c], 11))
        self.assertIsNone(_pick_by_court([a], 11))


class BluebookCiteTests(SimpleTestCase):
    """The "Cite this case" string, for every reporter shape held on prod
    (measured 2026-10-05). Unsaved model instances -- no DB."""

    def _op(self, state, level, title, cite="", docket="X", division="", when=(2018, 9, 4)):
        import datetime
        from opinions.models import Court, Opinion
        court = Court(state_id=state, level=level, division=division, name="n")
        op = Opinion(title=title, reporter_cite=cite, case_number=docket,
                     release_date=datetime.date(*when))
        op.court = court
        return op

    def cite(self, *a, **k):
        from opinions.templatetags.citations import bluebook_cite_for
        return bluebook_cite_for(self._op(*a, **k))

    def test_regional_reporter_names_the_court(self):
        self.assertEqual(self.cite("MN", "APPEALS", "Doe v. Roe", "900 N.W.2d 100"),
                         "Doe v. Roe, 900 N.W.2d 100 (Minn. Ct. App. 2018)")
        self.assertEqual(self.cite("MN", "SUPREME", "Doe v. Roe", "190 N.W. 1", when=(1922, 1, 6)),
                         "Doe v. Roe, 190 N.W. 1 (Minn. 1922)")
        self.assertEqual(self.cite("NH", "SUPREME", "Doe v. Roe", "650 A.2d 1"),
                         "Doe v. Roe, 650 A.2d 1 (N.H. 2018)")

    def test_official_reporter_is_year_only(self):
        self.assertEqual(self.cite("MN", "SUPREME", "Doe v. Roe", "300 Minn. 100"),
                         "Doe v. Roe, 300 Minn. 100 (2018)")
        self.assertEqual(self.cite("LA", "SUPREME", "Doe v. Roe", "45 La. Ann. 10"),
                         "Doe v. Roe, 45 La. Ann. 10 (2018)")

    def test_nh_neutral_cite_keeps_courtesy_date(self):
        self.assertEqual(self.cite("NH", "SUPREME", "State v. Smith", "2026 N.H. 7", when=(2026, 6, 11)),
                         "State v. Smith, 2026 N.H. 7 (June 11, 2026)")

    def test_arizona_drops_division_louisiana_keeps_circuit(self):
        self.assertEqual(self.cite("AZ", "APPEALS", "Doe v. Roe", "400 P.3d 1", division="2"),
                         "Doe v. Roe, 400 P.3d 1 (Ariz. Ct. App. 2018)")
        self.assertEqual(self.cite("LA", "APPEALS", "Doe v. Roe", "100 So. 3d 1", division="1"),
                         "Doe v. Roe, 100 So. 3d 1 (La. Ct. App. 1st Cir. 2018)")

    def test_no_reporter_cite_uses_docket_and_full_date(self):
        self.assertEqual(self.cite("MN", "APPEALS", "Doe v. Roe", docket="A26-0649"),
                         "Doe v. Roe, No. A26-0649 (Minn. Ct. App. Sept. 4, 2018)")

    def test_stored_docket_prefix_is_not_doubled(self):
        self.assertEqual(self.cite("LA", "SUPREME", "Doe v. Roe", docket="NO. 2017-C-0345"),
                         "Doe v. Roe, No. 2017-C-0345 (La. Sept. 4, 2018)")
        self.assertEqual(self.cite("MN", "SUPREME", "Doe v. Roe", docket="Nos. 18,829-(87)"),
                         "Doe v. Roe, Nos. 18,829-(87) (Minn. Sept. 4, 2018)")

    def test_empty_caption_and_title_truncation(self):
        self.assertEqual(self.cite("MN", "APPEALS", "", "900 N.W.2d 100"),
                         "900 N.W.2d 100 (Minn. Ct. App. 2018)")
        from opinions.templatetags.citations import bluebook_cite_for
        long = self._op("MN", "APPEALS", "In re the Welfare of the Children of: " + "A" * 80, "900 N.W.2d 100")
        out = bluebook_cite_for(long, name_limit=70)
        self.assertTrue(out.endswith("…, 900 N.W.2d 100 (Minn. Ct. App. 2018)"), out)


class InTextCiteLinkTests(SimpleTestCase):
    """Matching of resolved cites inside opinion text (opinion_text.py)."""

    def pat(self, *cites):
        from opinions.templatetags.opinion_text import _build_cite_pattern
        return _build_cite_pattern(list(cites))

    def test_spacing_after_periods_is_optional(self):
        p = self.pat("639 So. 2d 730")
        for text in ("639 So.2d 730", "639 So. 2d 730", "639 So.\n2d 730", "639  So. 2d\n730"):
            self.assertIsNotNone(p.search(text), text)
        self.assertIsNotNone(self.pat("131 N.W.2d 855").search("131 N. W. 2d 855"))

    def test_digit_boundaries_prevent_substring_links(self):
        p = self.pat("39 So. 2d 73")
        self.assertIsNone(p.search("639 So. 2d 730"))
        self.assertIsNone(p.search("39 So. 2d 730"))
        self.assertIsNotNone(p.search("see 39 So. 2d 73, 75"))

    def test_longest_first(self):
        m = self.pat("12 N.H. 34", "12 N.H. 345").search("12 N.H. 345")
        self.assertEqual(m.group(0), "12 N.H. 345")

    def test_keys_collapse_spelling_variants(self):
        from opinions.templatetags.opinion_text import _normalize_cite
        self.assertEqual(_normalize_cite("639 So.2d 730"), _normalize_cite("639 So. 2d\n730"))
        self.assertNotEqual(_normalize_cite("39 So. 2d 730"), _normalize_cite("39 So. 2d 73"))


class MinnesotaUnpublishedHeaderTests(SimpleTestCase):
    """Pre-2020 unpublished MN opinions say "will be unpublished and may not
    be cited", not "is nonprecedential". Missing that wording defaulted 4,072
    archive-backfill opinions to precedential=True (2026-10-24)."""

    OLD = (
        " \n \nThis opinion will be unpublished and \nmay not be cited except as "
        "provided by \nMinn. Stat. § 480A.08, subd. 3 (2016). \n \nSTATE OF "
        "MINNESOTA \nIN COURT OF APPEALS \nA16-2084 \n \nPakou Lee-Barrios and "
        "o/b/o Minor Child, petitioner, \nRespondent, \n \nvs. \n \nEric Joseph "
        "Vacko, \nRespondent Below, \n \nBrittany Ann Vacko, \nAppellant. \n \n"
        "Filed October 30, 2017  \nAffirmed \nKirk, Judge \n"
    )

    def parse(self, text):
        from opinions.parsing.mn import MinnesotaParser
        return MinnesotaParser().parse(text)

    def test_old_unpublished_header_is_nonprecedential(self):
        self.assertFalse(self.parse(self.OLD).is_precedential)

    def test_new_footer_still_nonprecedential(self):
        text = self.OLD.replace(
            "This opinion will be unpublished and \nmay not be cited",
            "This opinion is nonprecedential")
        self.assertFalse(self.parse(text).is_precedential)

    def test_published_stays_precedential(self):
        text = self.OLD.replace(
            "This opinion will be unpublished and \nmay not be cited except as "
            "provided by \nMinn. Stat. § 480A.08, subd. 3 (2016). \n", "")
        self.assertTrue(self.parse(text).is_precedential)

    def test_respondent_below_is_one_role(self):
        self.assertEqual(
            self.parse(self.OLD).case_name,
            "Pakou Lee-Barrios and o/b/o Minor Child v. "
            "Eric Joseph Vacko, Brittany Ann Vacko")

    def test_role_only_block_with_below(self):
        from opinions.parsing.mn import _looks_like_role_label
        self.assertTrue(_looks_like_role_label("Respondents Below,"))
        self.assertFalse(_looks_like_role_label("Below Construction, Inc.,"))

    def test_et_al_keeps_its_period(self):
        from opinions.parsing.mn import _order_caption_name
        self.assertEqual(
            _order_caption_name("Town of White Bear, Respondent, vs. Scott "
                                "Stoddard, et al., Respondents Below,"),
            "Town of White Bear v. Scott Stoddard, et al.")


class TextSaysNonprecedentialTests(SimpleTestCase):
    """The court's own notice overrides CourtListener's precedential_status,
    which reports MN unpublished opinions as "Published" (2026-10-24)."""

    def says(self, state, text):
        from opinions.parsing import text_says_nonprecedential
        return text_says_nonprecedential(state, text)

    MN_HEAD = ("STATE OF MINNESOTA\nIN COURT OF APPEALS\nA25-0112\n\n"
               "Jane Doe, Appellant,\n\nvs.\n\nState of Minnesota, Respondent.\n\n"
               "Filed June 2, 2025\nAffirmed\nSmith, Judge\n")

    def test_mn_modern_notice(self):
        self.assertTrue(self.says(
            "MN", "This opinion is nonprecedential except as provided by\n"
                  "Minn. R. Civ. App. P. 136.01, subd. 1(c).\n\n" + self.MN_HEAD))

    def test_mn_old_notice(self):
        self.assertTrue(self.says(
            "MN", "This opinion will be unpublished and\nmay not be cited except "
                  "as provided by\nMinn. Stat. § 480A.08, subd. 3 (2016).\n\n"
                  + self.MN_HEAD))

    def test_mn_published_is_not_flipped(self):
        self.assertFalse(self.says("MN", self.MN_HEAD + "We affirm.\n"))

    def test_notice_quoted_deep_in_body_is_ignored(self):
        body = self.MN_HEAD + ("x " * 2000) + (
            "\nThe order stated: This opinion is nonprecedential.\n")
        self.assertFalse(self.says("MN", body))

    def test_empty_and_unknown_state(self):
        self.assertFalse(self.says("MN", ""))
        self.assertFalse(self.says("ZZ", "This opinion is nonprecedential."))

    AZ_COA = ("IN THE\nARIZONA COURT OF APPEALS\nDIVISION ONE\n\nSTATE OF ARIZONA, "
              "Appellee,\n\nv.\n\nJOHN DOE, Appellant.\n\nNo. 1 CA-CR 19-0527\n"
              "FILED 6-2-2020\n\n")

    def test_az_capital_notice(self):
        self.assertTrue(self.says(
            "AZ", "NOTICE: NOT FOR OFFICIAL PUBLICATION.\n" + self.AZ_COA
                  + "MEMORANDUM DECISION\n\nJudge Kent E. Cattani delivered the decision.\n"))

    def test_az_published_mentioning_memorandum_decision(self):
        self.assertFalse(self.says(
            "AZ", self.AZ_COA + "OPINION\n\nJudge Smith delivered the opinion.\n"
                  "A separate memorandum decision filed simultaneously with this "
                  "opinion addresses his other claims.\n"))

    def test_la_header_forms_and_prose(self):
        from opinions.parsing.la import NONPRECEDENTIAL_RE as rx
        self.assertTrue(rx.search("NOT DESIGNATED FOR PUBLICATION"))
        self.assertTrue(rx.search(
            "Court of Appeal of Louisiana, Third Circuit. March 5, 2008. "
            "Not Designated for Publication JOHN L. OLIVIER"))
        self.assertTrue(rx.search(
            "No. 2007 KA 1447. Court of Appeal of Louisiana, First Circuit.\n"
            "Not Designated for Publication."))
        for prose in (
            "State v. Alo, 05-1001 (La.App. 5 Cir. 01/13/06) (not designated for publication)",
            "This court, in an opinion not designated for publication, affirmed",
            "Other issues are discussed in an appendix not designated for publication.",
            "The facts are attached as Appendix A and are Not Designated for Publication.",
        ):
            self.assertIsNone(rx.search(prose), prose)


class MinnesotaSupremeCaptionTests(SimpleTestCase):
    """The Supreme Court prints a TWO-COLUMN caption: author, separate
    writings, "Filed:" and "Office of Appellate Courts" interleave with the
    parties. ~330 Supreme PDFs were titled "State of Minnesota," or nothing
    (2026-10-24). Fixtures are the real headers of the listed opinions."""

    EXPECTED = {
        # credit wraps onto the party line: "State of Minnesota, Chutich, and Thissen, JJ."
        "A20-0366": "State of Minnesota v. Edgar Galvan-Contreras",
        "A19-0889": "Enterprise Leasing Co. of Minnesota v. County of Hennepin",
        # role with no comma on the party line
        "A20-1637": "City of Circle Pines v. County of Anoka",
        # no rule line: counsel must not join the caption
        "A21-1549": "In re the Marriage of: Allison Catherine Buckner v. "
                    "Bernard Joseph Robichaud, Jr.",
        # justice credit at the START of a line, missing period ("Anderson, J,")
        "A21-0004": "Barbara Henry/Cross-Appellant v. Independent School District "
                    "#625, a/k/a Saint Paul Public Schools/Cross-Respondent",
    }

    def test_real_headers(self):
        import json, pathlib
        from opinions.parsing.mn import MinnesotaParser
        heads = json.loads((pathlib.Path(__file__).parent
                            / "test_data_mn_supreme_captions.json").read_text("utf-8"))
        for docket, want in self.EXPECTED.items():
            self.assertEqual(MinnesotaParser().parse(heads[docket]).case_name, want, docket)

    def test_party_named_like_a_justice_is_kept(self):
        from opinions.parsing.mn import _supreme_caption
        text = ("STATE OF MINNESOTA\nIN SUPREME COURT\nA18-1491\n\n"
                "Court of Appeals   Hudson, J.\n\nState of Minnesota,\n\n Respondent,\n\n"
                "vs.   Filed:  June 3, 2020\n Office of Appellate Courts\n"
                "Steven Jeffrey Anderson,\n\n Appellant.\n________________________\n")
        self.assertEqual(_supreme_caption(text, "A18-1491"),
                         "State of Minnesota v. Steven Jeffrey Anderson")

    def test_leftover_right_column_returns_nothing(self):
        from opinions.parsing.mn import _supreme_caption
        text = ("A20-0001\n\nCourt of Appeals   Smith, J.\nDissenting, Jones,\n"
                "Party One, Unknownname, JJ.\n\n Appellant,\n\nvs.\n\nParty Two,\n\n"
                " Respondent.\n________\n")
        # "Unknownname, JJ." is not a justice on the roster -> residue -> "".
        self.assertEqual(_supreme_caption(text, "A20-0001"), "")

    def test_consolidated_docket_run_is_not_the_title(self):
        from opinions.parsing.mn import _order_caption_name
        self.assertEqual(
            _order_caption_name("A16-1900 A16-1901\nA16-1903, A16-1904\nCity of Fairmont, "
                                "Respondent,\nvs.\nThomas D. Unruh, Appellant."),
            "City of Fairmont v. Thomas D. Unruh")
        # a parenthesized docket INSIDE the caption is the court's text and stays
        self.assertEqual(
            _order_caption_name("Sarah N. Lewis (A14-0106), Appellant, vs. State, Respondent."),
            "Sarah N. Lewis (A14-0106) v. State")
