"""Export the whole rulebase to a single JSON bundle for the browser engine.

Rules are exported already merged (packs resolved, overrides applied, disabled
rules removed) so the JavaScript side never has to know about rule packs.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fex import quoting
from fex.build import BuildChart
from fex.carriers import DEFAULT_BUILD, load_carriers, load_rulepacks
from fex.catalog import Catalog

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "bundle.json")


def rule_to_dict(rule) -> dict:
    data = {
        "id": rule.id,
        "label": rule.label,
        "outcome": rule.outcome.key,
        "on_missing": rule.on_missing,
    }
    if rule.conditions:
        data["conditions"] = rule.conditions
    if rule.conditions_all:
        data["conditions_all"] = rule.conditions_all
    if rule.when:
        data["when"] = rule.when
    if rule.applicant:
        data["applicant"] = rule.applicant
    if rule.question:
        data["question"] = rule.question
    if rule.reason:
        data["reason"] = rule.reason
    if rule.citation:
        data["citation"] = rule.citation
    return data


def build_to_dict(chart: BuildChart) -> dict:
    return {
        "rows": {str(h): row for h, row in sorted(chart.rows.items())},
        "over_max": chart.over_max_outcome.key,
        "under_min": chart.under_min_outcome.key,
        "source": chart.source,
    }


def main() -> int:
    catalog = Catalog.load()
    carriers = load_carriers(packs=load_rulepacks())
    tables = quoting.load_rate_tables()

    bundle = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "conditions": [
            {
                "id": c.id,
                "label": c.label,
                "category": c.category,
                "aliases": c.aliases,
                "followups": c.followups,
            }
            for c in catalog.conditions.values()
        ],
        "drugs": [
            {
                "ingredient": d.ingredient,
                "brands": d.brands,
                "implies": d.implies,
                "set": d.set_attrs,
                "flags": d.flags,
                "confidence": d.confidence,
                "note": d.note,
            }
            for d in catalog.drugs
        ],
        "carriers": [
            {
                "id": c.id,
                "name": c.name,
                "am_best": c.am_best,
                "rate_index": c.rate_index,
                "verified": c.verified,
                "underwriting_style": c.underwriting_style,
                "notes": c.notes,
                "states_excluded": c.states_excluded,
                "build": build_to_dict(c.build) if c.build else build_to_dict(
                    BuildChart.from_dict(DEFAULT_BUILD)
                ),
                "products": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "tier": p.tier.key,
                        "issue_age_min": p.issue_age_min,
                        "issue_age_max": p.issue_age_max,
                        "face_min": p.face_min,
                        "face_max": p.face_max,
                        "benefit_schedule": p.benefit_schedule,
                        "bypass_underwriting": p.bypass_underwriting,
                        "states_excluded": p.states_excluded,
                        "notes": p.notes,
                    }
                    for p in c.products
                ],
                "rules": [rule_to_dict(r) for r in c.rules],
            }
            for c in carriers
        ],
        "pricing": {
            "base_curve": {str(k): v for k, v in quoting.BASE_CURVE.items()},
            "gender_factor": quoting.GENDER_FACTOR,
            "tobacco_factor": quoting.TOBACCO_FACTOR,
            "tier_factor": {t.key: f for t, f in quoting.TIER_FACTOR.items()},
            "policy_fee_annual": quoting.DEFAULT_POLICY_FEE,
            "monthly_factor": quoting.DEFAULT_MONTHLY_FACTOR,
        },
        "rate_tables": {
            cid: {
                "rates": {
                    tier: {
                        g: {t: {str(a): r for a, r in curve.items()}
                            for t, curve in by_t.items()}
                        for g, by_t in by_g.items()
                    }
                    for tier, by_g in table.rates.items()
                },
                "policy_fee_annual": table.policy_fee_annual,
                "monthly_factor": table.monthly_factor,
                "illustrative": table.illustrative,
                "source": table.source,
            }
            for cid, table in tables.items()
        },
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, separators=(",", ":"), sort_keys=False)
    size = os.path.getsize(OUT) / 1024
    print(
        f"wrote {OUT} ({size:.0f} KB): "
        f"{len(bundle['carriers'])} carriers, "
        f"{len(bundle['conditions'])} conditions, {len(bundle['drugs'])} drugs, "
        f"{sum(len(c['rules']) for c in bundle['carriers'])} resolved rules"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
