"""Engine tests. Run with `python -m unittest discover -s tests`."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fex.build import BuildChart, weight_for_bmi
from fex.carriers import load_carriers, load_rulepacks
from fex.catalog import Catalog, normalize
from fex.engine import Engine
from fex.models import Applicant, ConditionEntry
from fex.quoting import illustrative_rate, load_rate_tables, quote
from fex.rules import Rule, RuleError
from fex.tiers import Tier, worst


class TierTests(unittest.TestCase):
    def test_ordering_is_best_to_worst(self):
        ranks = [t.rank for t in [Tier.PREFERRED, Tier.LEVEL, Tier.GRADED,
                                  Tier.MODIFIED, Tier.GI, Tier.DECLINE]]
        self.assertEqual(ranks, sorted(ranks))

    def test_worst_wins(self):
        self.assertEqual(worst(Tier.LEVEL, Tier.GRADED, Tier.PREFERRED), Tier.GRADED)

    def test_aliases(self):
        self.assertEqual(Tier.parse("guaranteed issue"), Tier.GI)
        self.assertEqual(Tier.parse("IMMEDIATE"), Tier.LEVEL)
        with self.assertRaises(ValueError):
            Tier.parse("gold")


class RuleTests(unittest.TestCase):
    def make(self, **over):
        base = {
            "id": "r", "outcome": "graded", "label": "test rule",
            "conditions": ["cancer_internal"],
            "when": {"months_since_treatment": {"lt": 24}},
        }
        base.update(over)
        return Rule.from_dict(base)

    def test_fires_when_inside_window(self):
        app = Applicant(age=70, conditions=[
            ConditionEntry("cancer_internal", {"months_since_treatment": 6})])
        findings = self.make().evaluate(app)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].outcome, Tier.GRADED)
        self.assertFalse(findings[0].pending)

    def test_silent_when_outside_window(self):
        app = Applicant(age=70, conditions=[
            ConditionEntry("cancer_internal", {"months_since_treatment": 60})])
        self.assertEqual(self.make().evaluate(app), [])

    def test_unknown_detail_becomes_a_question(self):
        app = Applicant(age=70, conditions=[ConditionEntry("cancer_internal", {})])
        finding = self.make().evaluate(app)[0]
        self.assertTrue(finding.pending)
        self.assertIn("months since treatment", finding.question)

    def test_on_missing_skip(self):
        app = Applicant(age=70, conditions=[ConditionEntry("cancer_internal", {})])
        self.assertEqual(self.make(on_missing="skip").evaluate(app), [])

    def test_on_missing_fire_is_definite(self):
        app = Applicant(age=70, conditions=[ConditionEntry("cancer_internal", {})])
        finding = self.make(on_missing="fire").evaluate(app)[0]
        self.assertFalse(finding.pending)

    def test_applicant_predicates(self):
        rule = self.make(conditions=[], when={}, applicant={"age": {"gte": 80}})
        self.assertEqual(rule.evaluate(Applicant(age=70)), [])
        self.assertEqual(len(rule.evaluate(Applicant(age=81))), 1)

    def test_conditions_all_requires_every_id(self):
        rule = self.make(conditions=[], when={},
                         conditions_all=["diabetes_type2", "chf"])
        one = Applicant(age=70, conditions=[ConditionEntry("diabetes_type2")])
        both = Applicant(age=70, conditions=[ConditionEntry("diabetes_type2"),
                                             ConditionEntry("chf")])
        self.assertEqual(rule.evaluate(one), [])
        self.assertEqual(len(rule.evaluate(both)), 1)

    def test_bad_rule_rejected(self):
        with self.assertRaises(RuleError):
            Rule.from_dict({"id": "x"})
        with self.assertRaises(RuleError):
            Rule.from_dict({"id": "x", "outcome": "graded", "typo": 1})


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.chart = BuildChart.from_dict(
            {"bmi": {"min": 17, "preferred_max": 32, "level_max": 40, "graded_max": 46}})

    def app(self, weight):
        return Applicant(age=65, height_in=66, weight_lb=weight)

    def test_in_range_is_clean(self):
        self.assertEqual(self.chart.evaluate(self.app(150)), [])

    def test_over_level_limit_is_graded(self):
        self.assertEqual(self.chart.evaluate(self.app(260))[0].outcome, Tier.GRADED)

    def test_over_top_limit_declines(self):
        self.assertEqual(self.chart.evaluate(self.app(320))[0].outcome, Tier.DECLINE)

    def test_underweight_flagged(self):
        finding = self.chart.evaluate(self.app(90))[0]
        self.assertEqual(finding.outcome, Tier.GRADED)
        self.assertIn("minimum", finding.reason)

    def test_missing_build_is_a_question(self):
        finding = self.chart.evaluate(Applicant(age=65))[0]
        self.assertTrue(finding.pending)

    def test_explicit_chart_beats_bmi(self):
        chart = BuildChart.from_dict(
            {"chart": {66: {"min": 100, "level_max": 200, "graded_max": 240}}})
        self.assertEqual(chart.evaluate(self.app(190)), [])
        self.assertEqual(chart.evaluate(self.app(210))[0].outcome, Tier.GRADED)

    def test_past_the_highest_column_is_off_the_chart(self):
        # The last column is the maximum insurable weight, not just the last
        # tier boundary, so exceeding it declines rather than dropping a tier.
        chart = BuildChart.from_dict({"chart": {66: {"min": 100, "level_max": 200}}})
        self.assertEqual(chart.evaluate(self.app(210))[0].outcome, Tier.DECLINE)

    def test_over_max_is_overridable(self):
        chart = BuildChart.from_dict(
            {"chart": {66: {"min": 100, "level_max": 200}}, "over_max": "graded"})
        self.assertEqual(chart.evaluate(self.app(210))[0].outcome, Tier.GRADED)

    def test_bmi_to_weight_roundtrip(self):
        w = weight_for_bmi(66, 30)
        self.assertAlmostEqual(703 * w / 66**2, 30, delta=0.2)


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.load()

    def test_normalize_strips_dosing(self):
        self.assertEqual(normalize("Eliquis 5mg twice a day"), "eliquis")

    def test_brand_and_generic_both_match(self):
        self.assertEqual(self.catalog.match_drug("Eliquis").ingredient, "apixaban")
        self.assertEqual(self.catalog.match_drug("apixaban").ingredient, "apixaban")

    def test_lay_terms_match_conditions(self):
        self.assertEqual(self.catalog.match_condition("sugar"), "diabetes_type2")
        self.assertEqual(self.catalog.match_condition("mini stroke"), "tia")
        self.assertEqual(self.catalog.match_condition("water pill"), None)

    def test_fuzzy_spelling(self):
        self.assertEqual(self.catalog.match_drug("metformim").ingredient, "metformin")

    def test_unmatched_reported(self):
        _, unmatched = self.catalog.parse_medications(["zzzzqqq"])
        self.assertEqual(unmatched, ["zzzzqqq"])

    def test_condition_attrs_preserved(self):
        parsed, _ = self.catalog.parse_conditions(
            [{"name": "diabetes", "insulin": True}])
        self.assertEqual(parsed[0].id, "diabetes_type2")
        self.assertTrue(parsed[0].attrs["insulin"])

    def test_duplicate_conditions_merge(self):
        parsed, _ = self.catalog.parse_conditions([
            {"name": "diabetes", "insulin": True},
            {"name": "type 2 diabetes", "a1c": 7.5},
        ])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].attrs, {"insulin": True, "a1c": 7.5})

    def test_every_alias_resolves_to_its_own_condition(self):
        for cond in self.catalog.conditions.values():
            for alias in cond.aliases:
                self.assertIsNotNone(
                    self.catalog.match_condition(alias),
                    f"alias {alias!r} on {cond.id} matches nothing",
                )


class DataIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packs = load_rulepacks()
        cls.carriers = load_carriers(packs=cls.packs)
        cls.catalog = Catalog.load()

    def test_carriers_load(self):
        self.assertGreaterEqual(len(self.carriers), 25)

    def test_every_rule_condition_exists(self):
        known = set(self.catalog.conditions) | {"chronic_opioid", "tobacco_cessation"}
        for carrier in self.carriers:
            for rule in carrier.rules:
                for cid in rule.conditions + rule.conditions_all:
                    self.assertIn(cid, known, f"{carrier.id}/{rule.id} -> {cid}")

    def test_every_drug_implies_a_real_condition(self):
        for drug in self.catalog.drugs:
            for cid in drug.implies:
                self.assertIn(cid, self.catalog.conditions, drug.ingredient)
            for cid in drug.set_attrs:
                self.assertIn(cid, self.catalog.conditions, drug.ingredient)

    def test_carrier_ids_unique(self):
        ids = [c.id for c in self.carriers]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_carrier_has_a_product(self):
        for carrier in self.carriers:
            self.assertTrue(carrier.products, carrier.id)

    def test_product_face_ranges_sane(self):
        for carrier in self.carriers:
            for p in carrier.products:
                self.assertLess(p.face_min, p.face_max, f"{carrier.id}/{p.id}")
                self.assertLess(p.issue_age_min, p.issue_age_max, f"{carrier.id}/{p.id}")

    def test_seeded_defaults_are_never_marked_verified(self):
        # A file still carrying the seed language has not been checked against
        # a real document, whatever its flag says.
        for carrier in self.carriers:
            if "Seeded from general market knowledge" in carrier.source_note:
                self.assertFalse(
                    carrier.verified,
                    f"{carrier.id} claims verified but still carries the seeded "
                    f"source_note - replace it with the guide you transcribed from",
                )

    def test_verified_carriers_carry_provenance(self):
        # verified:true is a claim that a person read a document. This makes the
        # claim say which document, so it cannot be set on a hunch. It passes
        # vacuously today and starts biting the moment a carrier is transcribed.
        for carrier in self.carriers:
            if not carrier.verified:
                continue
            self.assertTrue(
                carrier.as_of.strip(),
                f"{carrier.id} is verified but has no as_of edition or date",
            )
            self.assertTrue(
                carrier.source_note.strip(),
                f"{carrier.id} is verified but does not cite a source document",
            )
            self.assertNotEqual(
                carrier.build.source, "bmi-approximation",
                f"{carrier.id} is verified but its build is still the BMI "
                f"approximation - transcribe the printed chart, or set an "
                f"explicit build source saying the guide publishes BMI limits",
            )

    def test_rule_pack_override_order(self):
        # Americo pulls in the liberal pack after the standard one, so its
        # heart attack lookback must be the shorter of the two.
        americo = next(c for c in self.carriers if c.id == "americo")
        rule = next(r for r in americo.rules if r.id == "lv_heart_attack")
        self.assertEqual(rule.when["months_since_event"]["lt"], 12)


class MutualOfOmahaTests(unittest.TestCase):
    """Values pinned directly to the Living Promise guide (form 128042).

    These exist so a later edit that corrupts a transcribed number fails loudly
    rather than quietly mispricing a case.
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = Engine.load()
        cls.carrier = next(c for c in cls.engine.carriers if c.id == "mutual_of_omaha")

    def run_case(self, **kw):
        kw.setdefault("height_in", 66)
        kw.setdefault("weight_lb", 160)
        report = self.engine.run(**kw)
        return next(
            (r for r in report.results if r.carrier_id == "mutual_of_omaha"), None
        ), report

    # -- build chart ---------------------------------------------------
    def test_build_chart_matches_the_printed_table(self):
        for height, minimum, level_max, graded_max in [
            (56, 74, 204, 221),    # 4'8", the first row
            (66, 103, 268, 285),   # 5'6"
            (70, 115, 300, 316),   # 5'10"
            (82, 158, 407, 427),   # 6'10", the last row
        ]:
            row = self.carrier.build.limits_for(height)
            self.assertEqual(row["min"], minimum, f"min at {height}in")
            self.assertEqual(row["level_max"], level_max, f"level max at {height}in")
            self.assertEqual(row["graded_max"], graded_max, f"graded max at {height}in")

    def test_build_moves_level_to_graded_at_the_printed_line(self):
        # 5'6": level to 268 lb, graded to 285 lb.
        at_limit, _ = self.run_case(age=60, height_in=66, weight_lb=268)
        over, _ = self.run_case(age=60, height_in=66, weight_lb=269)
        self.assertEqual(at_limit.tier, Tier.LEVEL)
        self.assertEqual(over.tier, Tier.GRADED)

    def test_build_past_the_graded_column_declines(self):
        over, _ = self.run_case(age=60, height_in=66, weight_lb=286)
        self.assertEqual(over.tier, Tier.GI)   # only the GI plan survives
        self.assertNotEqual(over.product_id, "living_promise_graded")

    # -- rates ---------------------------------------------------------
    def test_rate_table_is_real_not_illustrative(self):
        table = self.engine.rate_tables["mutual_of_omaha"]
        self.assertFalse(table.illustrative)
        self.assertEqual(table.policy_fee_annual, 36.0)
        self.assertEqual(table.monthly_factor, 0.089)

    def test_premium_matches_the_printed_rate(self):
        # Age 68 male non-tobacco level is $71.15 per $1,000 per year.
        result, _ = self.run_case(age=68, gender="male", face_amount=10000)
        expected_annual = 71.15 * 10 + 36.0
        self.assertEqual(result.tier, Tier.LEVEL)
        self.assertAlmostEqual(result.quote.annual, expected_annual, places=6)
        self.assertAlmostEqual(result.quote.monthly, expected_annual * 0.089, places=6)
        self.assertFalse(result.quote.illustrative)

    def test_graded_plan_has_no_tobacco_distinction(self):
        table = self.engine.rate_tables["mutual_of_omaha"]
        for gender in ("male", "female"):
            clean = table.lookup(Applicant(age=70, gender=gender), Tier.GRADED)
            smoker = table.lookup(
                Applicant(age=70, gender=gender, tobacco=True), Tier.GRADED
            )
            self.assertEqual(clean, smoker, gender)

    def test_level_plan_does_have_a_tobacco_distinction(self):
        table = self.engine.rate_tables["mutual_of_omaha"]
        clean = table.lookup(Applicant(age=70), Tier.LEVEL)
        smoker = table.lookup(Applicant(age=70, tobacco=True), Tier.LEVEL)
        self.assertGreater(smoker, clean)

    # -- medication list ------------------------------------------------
    def test_uninsurable_drug_declines_the_underwritten_plans(self):
        result, _ = self.run_case(age=70, medications=["Aricept"])
        self.assertEqual(result.tier, Tier.GI)
        self.assertNotEqual(result.product_id, "living_promise_level")

    def test_asterisked_drug_lands_on_graded_not_decline(self):
        # Spiriva carries an asterisk: "may qualify for the Graded benefit".
        result, _ = self.run_case(age=70, medications=["Spiriva"])
        self.assertEqual(result.tier, Tier.GRADED)
        self.assertTrue(
            any("medication list" in r for r in result.blocking_reasons),
            result.blocking_reasons,
        )

    def test_drug_list_matches_the_generic_too(self):
        brand, _ = self.run_case(age=70, medications=["Aricept"])
        generic, _ = self.run_case(age=70, medications=["donepezil 10mg"])
        self.assertEqual(brand.tier, generic.tier)

    def test_additional_information_drug_asks_rather_than_rates(self):
        result, _ = self.run_case(age=70, medications=["Eliquis"])
        self.assertEqual(result.tier, Tier.LEVEL)      # not rated
        self.assertTrue(
            any("reason for this medication" in q for q in result.open_questions),
            result.open_questions,
        )

    def test_drug_not_on_any_list_is_left_alone(self):
        result, _ = self.run_case(age=70, medications=["atorvastatin"])
        self.assertEqual(result.tier, Tier.LEVEL)

    # -- products and states --------------------------------------------
    def test_living_promise_products_are_marked_verified(self):
        by_id = {p.id: p for p in self.carrier.products}
        self.assertTrue(by_id["living_promise_level"].verified)
        self.assertTrue(by_id["living_promise_graded"].verified)
        # The guide does not cover the guaranteed issue plan.
        self.assertFalse(by_id["gwl"].verified)

    def test_carrier_stays_unverified_until_the_application_arrives(self):
        # Build, rates and the drug list are transcribed, but the Part One and
        # Part Two health questions are not in this guide.
        self.assertFalse(self.carrier.verified)

    def test_not_sold_in_new_york(self):
        _, report = self.run_case(age=65, state="NY")
        self.assertNotIn("mutual_of_omaha", {r.carrier_id for r in report.results})

    def test_graded_plan_is_unavailable_in_three_states(self):
        for state in ("AR", "MT", "NC"):
            result, _ = self.run_case(
                age=60, state=state, height_in=66, weight_lb=280   # over the level line
            )
            self.assertNotEqual(
                result.product_id, "living_promise_graded", f"graded offered in {state}"
            )

    def test_face_limits_match_the_guide(self):
        by_id = {p.id: p for p in self.carrier.products}
        self.assertEqual(by_id["living_promise_level"].face_max, 40000)
        self.assertEqual(by_id["living_promise_level"].face_min, 2000)
        self.assertEqual(by_id["living_promise_graded"].face_max, 20000)
        self.assertEqual((by_id["living_promise_level"].issue_age_min,
                          by_id["living_promise_level"].issue_age_max), (45, 85))
        self.assertEqual((by_id["living_promise_graded"].issue_age_min,
                          by_id["living_promise_graded"].issue_age_max), (45, 80))


class FaceBandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine.load()
        cls.aetna = next(c for c in cls.engine.carriers if c.id == "aetna")

    def product(self, pid):
        return next(p for p in self.aetna.products if p.id == pid)

    def test_face_maximum_shrinks_with_issue_age(self):
        level = self.product("level")
        self.assertEqual(level.face_max_for(60), 35000)
        self.assertEqual(level.face_max_for(70), 25000)
        self.assertEqual(level.face_max_for(82), 15000)
        self.assertEqual(level.face_max_for(88), 10000)

    def test_quote_is_capped_at_the_band(self):
        report = self.engine.run(
            age=82, gender="male", height_in=68, weight_lb=170, face_amount=35000
        )
        result = next(r for r in report.results if r.carrier_id == "aetna")
        self.assertEqual(result.quote.face_amount, 15000)
        self.assertTrue(
            any("issue age 82" in n for n in result.notes), result.notes
        )

    def test_gap_in_the_bands_falls_back_conservatively(self):
        from fex.carriers import Product
        product = Product.from_dict({
            "id": "x", "name": "X", "tier": "level",
            "face": {"min": 1000, "max": 50000,
                     "bands": [{"ages": [45, 65], "max": 40000},
                               {"ages": [66, 80], "max": 20000}]},
        })
        self.assertEqual(product.face_max_for(70), 20000)
        self.assertEqual(product.face_max_for(90), 20000)   # not the 50000 headline

    def test_no_bands_means_the_headline_maximum(self):
        from fex.carriers import Product
        product = Product.from_dict({
            "id": "x", "name": "X", "tier": "level", "face": {"min": 1000, "max": 30000},
        })
        self.assertEqual(product.face_max_for(70), 30000)


class QuotingTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine.load()
        self.carrier = next(c for c in self.engine.carriers if c.id == "mutual_of_omaha")
        self.product = self.carrier.products[0]

    def test_premium_rises_with_age(self):
        young = illustrative_rate(Applicant(age=55), Tier.LEVEL)
        old = illustrative_rate(Applicant(age=80), Tier.LEVEL)
        self.assertGreater(old, young)

    def test_female_cheaper_than_male(self):
        m = illustrative_rate(Applicant(age=70, gender="male"), Tier.LEVEL)
        f = illustrative_rate(Applicant(age=70, gender="female"), Tier.LEVEL)
        self.assertLess(f, m)

    def test_tobacco_costs_more(self):
        clean = illustrative_rate(Applicant(age=70), Tier.LEVEL)
        smoker = illustrative_rate(Applicant(age=70, tobacco=True), Tier.LEVEL)
        self.assertGreater(smoker, clean)

    def test_worse_tier_costs_more(self):
        level = illustrative_rate(Applicant(age=70), Tier.LEVEL)
        graded = illustrative_rate(Applicant(age=70), Tier.GRADED)
        self.assertGreater(graded, level)

    def test_face_is_clamped_to_product_limits(self):
        app = Applicant(age=70, face_amount=500000)
        q = quote(app, self.carrier, self.product, Tier.LEVEL)
        self.assertEqual(q.face_amount, self.product.face_max)

    def test_illustrative_flag_set_without_a_rate_table(self):
        q = quote(Applicant(age=70), self.carrier, self.product, Tier.LEVEL)
        self.assertTrue(q.illustrative)

    def test_no_shipped_rate_tables_claim_to_be_real(self):
        for table in load_rate_tables().values():
            self.assertTrue(table.illustrative or table.source,
                            f"{table.carrier_id} has neither a source nor the illustrative flag")


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine.load()

    def run_case(self, **kw):
        kw.setdefault("height_in", 68)
        kw.setdefault("weight_lb", 175)
        return self.engine.run(**kw)

    def test_healthy_applicant_gets_day_one_coverage(self):
        report = self.run_case(age=62)
        immediate = [r for r in report.results
                     if r.tier in (Tier.PREFERRED, Tier.LEVEL)]
        self.assertGreater(len(immediate), 15)

    def test_dementia_medication_knocks_out_underwritten_carriers(self):
        report = self.run_case(age=76, medications=["Aricept"])
        underwritten = [r for r in report.results if r.tier != Tier.GI]
        self.assertTrue(all(not r.eligible for r in underwritten))
        self.assertTrue(any(r.eligible and r.tier == Tier.GI for r in report.results))

    def test_guaranteed_issue_always_survives(self):
        report = self.run_case(age=70, conditions=["hospice", "dialysis", "terminal"])
        self.assertTrue(any(r.eligible for r in report.results))
        for result in report.results:
            if result.eligible:
                self.assertEqual(result.tier, Tier.GI)

    def test_recent_heart_attack_is_graded_somewhere_level_elsewhere(self):
        report = self.run_case(
            age=68,
            conditions=[{"name": "heart attack", "months_since_event": 14}],
        )
        tiers = {r.carrier_id: r.tier for r in report.results}
        self.assertEqual(tiers["americo"], Tier.LEVEL)      # 12-month lookback
        self.assertEqual(tiers["transamerica"], Tier.GRADED)  # 24-month lookback

    def test_build_moves_the_tier(self):
        slim = self.run_case(age=60, height_in=66, weight_lb=150)
        heavy = self.run_case(age=60, height_in=66, weight_lb=270)
        slim_moo = next(r for r in slim.results if r.carrier_id == "mutual_of_omaha")
        heavy_moo = next(r for r in heavy.results if r.carrier_id == "mutual_of_omaha")
        self.assertEqual(slim_moo.tier, Tier.LEVEL)
        self.assertGreater(heavy_moo.tier.rank, slim_moo.tier.rank)

    def test_insulin_inferred_from_medication(self):
        report = self.run_case(age=66, medications=["Lantus"])
        diabetes = report.applicant.find("diabetes_type2")
        self.assertIsNotNone(diabetes)
        self.assertTrue(diabetes.attrs.get("insulin"))
        self.assertEqual(diabetes.inferred_from, "Lantus")

    def test_ambiguous_drug_asks_instead_of_assuming(self):
        report = self.run_case(age=66, medications=["Eliquis"])
        self.assertIsNone(report.applicant.find("afib"))
        self.assertTrue(any("Eliquis" in q for q in report.open_questions))

    def test_unknown_detail_produces_a_spread_not_a_verdict(self):
        report = self.run_case(
            age=66, conditions=["cancer"]  # no treatment date given
        )
        uncertain = [r for r in report.results if not r.certain]
        self.assertTrue(uncertain)
        self.assertTrue(report.open_questions)

    def test_age_limits_respected(self):
        report = self.run_case(age=88)
        for result in report.results:
            if result.eligible:
                carrier = next(c for c in self.engine.carriers
                               if c.id == result.carrier_id)
                product = next(p for p in carrier.products
                               if p.id == result.product_id)
                self.assertTrue(product.accepts_age(88), f"{result.carrier_id}")

    def test_state_exclusions_hide_the_carrier(self):
        national = self.run_case(age=65, state="IL")
        excluded = self.run_case(age=65, state="NY")
        self.assertIn("pekin_life", {r.carrier_id for r in national.results})
        self.assertNotIn("pekin_life", {r.carrier_id for r in excluded.results})

    def test_results_are_ordered_best_first(self):
        report = self.run_case(age=65)
        keys = [(r.best_case_tier.rank, r.tier.rank) for r in report.results]
        self.assertEqual(keys, sorted(keys))

    def test_report_serialises(self):
        report = self.run_case(age=70, medications=["metformin"])
        data = report.to_dict()
        self.assertIn("results", data)
        self.assertIn("applicant", data)
        import json
        json.loads(json.dumps(data))  # must be JSON-clean

    def test_questions_use_the_catalog_wording(self):
        # Two carriers use 12- and 6-month TIA lookbacks; both need the same
        # fact, so the agent should get one readable question, not two.
        report = self.run_case(age=68, conditions=["tia"])
        self.assertEqual(report.open_questions, ["How many months since the TIA?"])

    def test_picker_ids_display_as_labels(self):
        report = self.run_case(age=68, conditions=["diabetes_type2"])
        entry = report.applicant.find("diabetes_type2")
        self.assertEqual(entry.raw, "Type 2 diabetes")

    def test_typed_text_is_preserved_verbatim(self):
        report = self.run_case(age=68, conditions=["sugar"])
        self.assertEqual(report.applicant.find("diabetes_type2").raw, "sugar")

    def test_reason_does_not_restate_the_condition(self):
        report = self.run_case(age=68, conditions=["heart_attack"])
        americo = next(r for r in report.results if r.carrier_id == "americo")
        self.assertEqual(
            americo.blocking_reasons, ["Heart attack within 12 months (unconfirmed)"]
        )

    def test_reason_echoes_the_clients_own_words(self):
        report = self.run_case(age=68, conditions=["mini stroke"])
        reasons = [r for res in report.results for r in res.blocking_reasons]
        self.assertTrue(any(r.startswith("mini stroke:") for r in reasons))

    def test_reason_names_the_medication_it_came_from(self):
        report = self.run_case(age=68, medications=["Aricept"])
        reasons = [r for res in report.results for r in res.blocking_reasons]
        self.assertTrue(any("Aricept (from medication)" in r for r in reasons))

    def test_rule_supplied_questions_are_not_overwritten(self):
        report = self.run_case(age=68, conditions=["aneurysm"])
        self.assertIn(
            "Has the aneurysm been surgically repaired?", report.open_questions
        )

    def test_every_carrier_returns_a_verdict(self):
        report = self.run_case(age=70)
        self.assertEqual(len(report.results), len(self.engine.carriers))


if __name__ == "__main__":
    unittest.main(verbosity=2)
