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

    def test_unverified_carriers_are_flagged(self):
        # Nothing in the repo is transcribed from a real guide yet, so every
        # carrier must still be carrying the unverified flag.
        for carrier in self.carriers:
            self.assertFalse(carrier.verified, f"{carrier.id} claims to be verified")

    def test_rule_pack_override_order(self):
        # Americo pulls in the liberal pack after the standard one, so its
        # heart attack lookback must be the shorter of the two.
        americo = next(c for c in self.carriers if c.id == "americo")
        rule = next(r for r in americo.rules if r.id == "lv_heart_attack")
        self.assertEqual(rule.when["months_since_event"]["lt"], 12)


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

    def test_every_carrier_returns_a_verdict(self):
        report = self.run_case(age=70)
        self.assertEqual(len(report.results), len(self.engine.carriers))


if __name__ == "__main__":
    unittest.main(verbosity=2)
