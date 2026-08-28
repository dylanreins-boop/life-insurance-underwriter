"""The browser engine must agree with the Python engine, case for case.

``web/engine.js`` is a port, not a reimplementation, so any behavioural drift
between the two is a bug. This runs a spread of cases through both and compares
tiers, products, premiums, reasons, inferred conditions and open questions.

Skipped when node is unavailable or the bundle has not been exported.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fex.engine import Engine

BUNDLE = os.path.join(ROOT, "web", "bundle.json")
RUNNER = os.path.join(ROOT, "tools", "parity_runner.js")

CASES = [
    {"age": 62, "gender": "male", "height_in": 70, "weight_lb": 180, "face_amount": 15000},
    {"age": 75, "gender": "female", "tobacco": True, "height_in": 62, "weight_lb": 140,
     "face_amount": 10000},
    {"age": 68, "gender": "male", "height_in": 70, "weight_lb": 200, "face_amount": 10000,
     "conditions": [{"name": "heart attack", "attrs": {"months_since_event": 14}}]},
    {"age": 76, "gender": "female", "height_in": 63, "weight_lb": 150,
     "medications": ["Aricept 10mg"]},
    {"age": 72, "gender": "male", "height_in": 68, "weight_lb": 160,
     "conditions": ["copd", {"name": "oxygen", "attrs": {"for_sleep_apnea_only": False}}]},
    {"age": 60, "gender": "female", "height_in": 64, "weight_lb": 275, "face_amount": 8000},
    {"age": 60, "gender": "female", "height_in": 64, "weight_lb": 95, "face_amount": 8000},
    {"age": 68, "gender": "female", "height_in": 64, "weight_lb": 185, "face_amount": 15000,
     "conditions": [{"name": "diabetes",
                     "attrs": {"insulin": True, "age_at_diagnosis": 52, "complications": False}}],
     "medications": ["metformin 500mg", "lisinopril", "Lantus"]},
    {"age": 71, "gender": "male", "height_in": 68, "weight_lb": 205, "face_amount": 12000,
     "medications": ["eliquis 5mg", "lasix", "metformin", "Plavix", "Coreg"],
     "conditions": [{"name": "heart attack", "attrs": {"months_since_event": 30}}]},
    {"age": 55, "gender": "male", "height_in": 72, "weight_lb": 210, "face_amount": 25000,
     "conditions": [{"name": "cancer", "attrs": {}}]},
    {"age": 80, "gender": "female", "height_in": 61, "weight_lb": 130, "face_amount": 5000,
     "conditions": ["hospice", "dialysis"]},
    {"age": 88, "gender": "male", "height_in": 69, "weight_lb": 175, "face_amount": 10000},
    {"age": 65, "gender": "male", "state": "NY", "height_in": 70, "weight_lb": 190},
    {"age": 65, "gender": "male", "state": "IL", "height_in": 70, "weight_lb": 190},
    {"age": 67, "gender": "female", "height_in": 65, "weight_lb": 170,
     "medications": ["metformim", "zzzznotadrug", "Spiriva", "Seroquel", "Xarelto"]},
    {"age": 70, "gender": "male", "height_in": 70, "weight_lb": 190,
     "conditions": [{"name": "stroke", "attrs": {"months_since_event": 8, "count": 2}},
                    {"name": "type 2 diabetes", "attrs": {"insulin": True}}]},
    {"age": 58, "gender": "female", "height_in": 66, "weight_lb": 160,
     "conditions": [{"name": "type 1 diabetes", "attrs": {"age_at_diagnosis": 12}}]},
    {"age": 70, "gender": "male", "face_amount": 10000},  # no build at all
    {"age": 74, "gender": "female", "height_in": 63, "weight_lb": 155, "face_amount": 40000,
     "medications": ["Entresto", "Jardiance", "Eliquis", "Lipitor"]},
    {"age": 66, "gender": "male", "height_in": 71, "weight_lb": 230, "face_amount": 20000,
     "conditions": [{"name": "copd", "attrs": {"hospitalized_12mo": True}},
                    {"name": "afib", "attrs": {"controlled": True}}]},
]


def digest(report) -> dict:
    return {
        "results": [
            {
                "carrier_id": r.carrier_id,
                "product_id": r.product_id,
                "tier": r.tier.key,
                "best_case_tier": r.best_case_tier.key,
                "eligible": r.eligible,
                "monthly": r.quote.monthly if r.quote else None,
                "reasons": sorted(r.blocking_reasons),
            }
            for r in report.results
        ],
        "open_questions": sorted(report.open_questions),
        "conditions": sorted(
            c.id + ":" + json.dumps(c.attrs, sort_keys=True, separators=(",", ":"))
            for c in report.applicant.conditions
        ),
        "unmatched_medications": sorted(report.unmatched_medications),
    }


@unittest.skipUnless(shutil.which("node"), "node is not installed")
@unittest.skipUnless(os.path.exists(BUNDLE), "run tools/export_bundle.py first")
class ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = Engine.load()
        cls.python = [digest(engine.run(**case)) for case in CASES]
        proc = subprocess.run(
            ["node", RUNNER],
            input=json.dumps(CASES),
            capture_output=True,
            text=True,
            check=True,
        )
        cls.js = json.loads(proc.stdout)

    def test_same_number_of_cases(self):
        self.assertEqual(len(self.python), len(self.js))

    def test_cases_agree(self):
        for i, (py, js) in enumerate(zip(self.python, self.js)):
            case = CASES[i]
            with self.subTest(case=i, age=case.get("age")):
                self.assertEqual(py["conditions"], js["conditions"], "inferred conditions differ")
                self.assertEqual(
                    py["unmatched_medications"], js["unmatched_medications"],
                    "medication matching differs",
                )
                self.assertEqual(py["open_questions"], js["open_questions"], "questions differ")
                self.assertEqual(
                    [r["carrier_id"] for r in py["results"]],
                    [r["carrier_id"] for r in js["results"]],
                    "carrier ordering differs",
                )
                for pr, jr in zip(py["results"], js["results"]):
                    # Premiums are compared unrounded, so this checks the
                    # arithmetic itself rather than the two languages' rounding
                    # modes. Everything else must match exactly.
                    py_monthly = pr.pop("monthly")
                    js_monthly = jr.pop("monthly")
                    self.assertEqual(pr, jr, f"{pr['carrier_id']} differs")
                    if py_monthly is None or js_monthly is None:
                        self.assertEqual(py_monthly, js_monthly, pr["carrier_id"])
                    else:
                        self.assertAlmostEqual(
                            py_monthly, js_monthly, delta=1e-6,
                            msg=f"{pr['carrier_id']} premium differs",
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
