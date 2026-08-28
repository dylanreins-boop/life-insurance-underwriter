"""Height/weight ("build") handling.

Final expense carriers publish a build chart: for each height, the maximum
weight that still qualifies for each product tier, plus a minimum weight below
which the applicant is declined. Charts differ enough between carriers that
build alone routinely moves a client from level to graded -- which is why this
is modelled per carrier rather than as one global table.

Two ways to express a chart in a carrier file::

    build:
      chart:                       # explicit, as printed in the field guide
        62: {min: 95, level_max: 227, graded_max: 251}
        63: {min: 98, level_max: 235, graded_max: 259}

    build:
      bmi:                         # approximation when only BMI limits are known
        min: 17
        preferred_max: 32
        level_max: 40
        graded_max: 46

Two things about how a chart is read:

* Each ``*_max`` is the last weight that still qualifies for that tier, so
  going over ``level_max`` drops the applicant to graded, over ``graded_max``
  drops them to modified, and so on.
* The **highest column in the chart is the maximum insurable weight**. Past it
  the applicant is off the chart entirely and the result is ``over_max``
  (decline by default), not merely the next tier down. A chart that publishes
  only ``level_max`` is therefore saying "over this weight we do not write at
  all" -- if the carrier really does offer graded above that line, give the
  chart a ``graded_max`` column too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Applicant, Finding
from .tiers import Tier

#: Heights covered by generated charts, in inches (4'6" through 6'10").
HEIGHT_RANGE = range(54, 83)

_TIER_KEYS = [
    ("preferred_max", Tier.LEVEL),   # over preferred limit -> falls to level
    ("level_max", Tier.GRADED),      # over level limit -> falls to graded
    ("graded_max", Tier.MODIFIED),   # over graded limit -> falls to modified
    ("modified_max", Tier.GI),       # over modified limit -> GI only
]


def weight_for_bmi(height_in: float, bmi: float) -> float:
    """Weight in pounds that puts ``height_in`` at exactly ``bmi``."""
    return round(bmi * (float(height_in) ** 2) / 703.0)


def chart_from_bmi(limits: Dict[str, float]) -> Dict[int, Dict[str, float]]:
    """Expand BMI limits into a pounds-per-height chart."""
    chart: Dict[int, Dict[str, float]] = {}
    for h in HEIGHT_RANGE:
        row: Dict[str, float] = {}
        for key, bmi in limits.items():
            row[key] = weight_for_bmi(h, float(bmi))
        chart[h] = row
    return chart


@dataclass
class BuildChart:
    """A carrier's build limits, plus the outcome when the applicant is over."""

    rows: Dict[int, Dict[str, float]] = field(default_factory=dict)
    over_max_outcome: Tier = Tier.DECLINE
    under_min_outcome: Tier = Tier.GRADED
    source: str = "bmi-approximation"

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["BuildChart"]:
        if not data:
            return None
        if "chart" in data:
            rows = {int(h): {k: float(v) for k, v in row.items()}
                    for h, row in data["chart"].items()}
            source = data.get("source", "carrier build chart")
        elif "bmi" in data:
            rows = chart_from_bmi(data["bmi"])
            source = data.get("source", "bmi-approximation")
        else:
            return None
        return cls(
            rows=rows,
            over_max_outcome=Tier.parse(data.get("over_max", "decline")),
            under_min_outcome=Tier.parse(data.get("under_min", "graded")),
            source=source,
        )

    # ------------------------------------------------------------------
    def row_for(self, height_in: float) -> Optional[Dict[str, float]]:
        if not self.rows:
            return None
        h = int(round(float(height_in)))
        if h in self.rows:
            return self.rows[h]
        # Clamp to the nearest published height rather than extrapolating.
        nearest = min(self.rows, key=lambda k: abs(k - h))
        return self.rows[nearest]

    def limits_for(self, height_in: float) -> Dict[str, float]:
        return dict(self.row_for(height_in) or {})

    def evaluate(self, applicant: Applicant) -> List[Finding]:
        """Findings driven purely by the applicant's build."""
        if applicant.height_in is None or applicant.weight_lb is None:
            return [
                Finding(
                    rule_id="build_unknown",
                    outcome=Tier.DECLINE,
                    best_case=Tier.PREFERRED,
                    pending=True,
                    reason="Height and weight not provided (unconfirmed)",
                    question="What is the applicant's height and weight?",
                )
            ]
        row = self.row_for(applicant.height_in)
        if not row:
            return []
        weight = float(applicant.weight_lb)
        ft, inch = divmod(int(round(applicant.height_in)), 12)
        who = f"{ft}'{inch}\" {weight:.0f} lb (BMI {applicant.bmi})"

        minimum = row.get("min")
        if minimum is not None and weight < float(minimum):
            return [
                Finding(
                    rule_id="build_under_min",
                    outcome=self.under_min_outcome,
                    reason=f"Build {who} is under the {minimum:.0f} lb minimum for this height",
                    citation=self.source,
                )
            ]

        # Walk the tiers from best to worst; the first limit the applicant
        # exceeds sets the outcome.
        worst_key = None
        outcome = None
        for key, tier in _TIER_KEYS:
            limit = row.get(key)
            if limit is None:
                continue
            if weight > float(limit):
                worst_key, outcome = key, tier
        if outcome is None:
            return []
        # Above every published limit means outside the build chart entirely.
        highest = max(
            (k for k, _ in _TIER_KEYS if row.get(k) is not None),
            key=lambda k: row[k],
            default=None,
        )
        if worst_key == highest and self.over_max_outcome.rank > outcome.rank:
            outcome = self.over_max_outcome
        limit = row[worst_key]
        return [
            Finding(
                rule_id=f"build_over_{worst_key}",
                outcome=outcome,
                reason=(
                    f"Build {who} exceeds the {limit:.0f} lb "
                    f"{worst_key.replace('_max', '')} limit for this height"
                ),
                citation=self.source,
            )
        ]
