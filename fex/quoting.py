"""Premium calculation.

Real final expense rate tables are proprietary and change often, so this module
does two things:

1. It ships an **illustrative** parametric rate model. Every premium it
   produces is marked ``illustrative: true`` and should be treated as a
   ballpark for comparison, never as a quote you hand a client.
2. It loads **real** per-carrier rate tables from ``fex/data/rates/*.yaml``
   when you drop them in, and those override the model completely. Once a
   carrier has a real table its quotes come back with ``illustrative: false``.

Use ``fex rates template <carrier_id>`` to print a starter file in the right
shape, then fill it in from the carrier's rate book.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .carriers import DATA_DIR, Carrier, Product
from .models import Applicant, Quote
from .tiers import Tier

RATES_DIR = os.path.join(DATA_DIR, "rates")

# --------------------------------------------------------------------------
# Illustrative model
# --------------------------------------------------------------------------

#: Annual premium per $1,000 of face for a male non-tobacco level plan.
#: Anchored to the middle of the final expense market; interpolated between.
BASE_CURVE: Dict[int, float] = {
    40: 32.0, 45: 36.5, 50: 44.0, 55: 53.0, 60: 63.0, 65: 79.0,
    70: 103.0, 75: 139.0, 80: 198.0, 85: 282.0, 89: 350.0,
}

GENDER_FACTOR = {"male": 1.00, "female": 0.82}
TOBACCO_FACTOR = 1.33

TIER_FACTOR = {
    Tier.PREFERRED: 0.92,
    Tier.LEVEL: 1.00,
    Tier.GRADED: 1.22,
    Tier.MODIFIED: 1.32,
    Tier.GI: 1.45,
}

DEFAULT_POLICY_FEE = 36.0
DEFAULT_MONTHLY_FACTOR = 0.0875   # monthly bank draft as a share of annual


def _interpolate(curve: Dict[int, float], age: int) -> float:
    ages = sorted(curve)
    if age <= ages[0]:
        return curve[ages[0]]
    if age >= ages[-1]:
        return curve[ages[-1]]
    for low, high in zip(ages, ages[1:]):
        if low <= age <= high:
            span = high - low
            weight = (age - low) / span if span else 0.0
            return curve[low] + (curve[high] - curve[low]) * weight
    return curve[ages[-1]]


def illustrative_rate(applicant: Applicant, tier: Tier, rate_index: float = 1.0) -> float:
    """Annual premium per $1,000 of face under the illustrative model."""
    rate = _interpolate(BASE_CURVE, applicant.age)
    rate *= GENDER_FACTOR.get(applicant.gender, 1.0)
    if applicant.tobacco:
        rate *= TOBACCO_FACTOR
    rate *= TIER_FACTOR.get(tier, 1.0)
    rate *= rate_index
    return rate


# --------------------------------------------------------------------------
# Real rate tables
# --------------------------------------------------------------------------

@dataclass
class RateTable:
    carrier_id: str
    #: rates[tier_key][gender][tobacco_class] -> {age: annual rate per $1000}
    rates: Dict[str, Dict[str, Dict[str, Dict[int, float]]]] = field(default_factory=dict)
    policy_fee_annual: float = DEFAULT_POLICY_FEE
    monthly_factor: float = DEFAULT_MONTHLY_FACTOR
    illustrative: bool = False
    source: str = ""

    def lookup(self, applicant: Applicant, tier: Tier) -> Optional[float]:
        by_gender = self.rates.get(tier.key)
        if not by_gender:
            return None
        by_tobacco = by_gender.get(applicant.gender) or by_gender.get("unisex")
        if not by_tobacco:
            return None
        curve = by_tobacco.get(applicant.tobacco_class)
        if not curve:
            return None
        return _interpolate(curve, applicant.age)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RateTable":
        rates: Dict[str, Dict[str, Dict[str, Dict[int, float]]]] = {}
        for tier_key, by_gender in (data.get("rates") or {}).items():
            rates[Tier.parse(tier_key).key] = {
                gender: {
                    tob: {int(age): float(rate) for age, rate in curve.items()}
                    for tob, curve in by_tob.items()
                }
                for gender, by_tob in by_gender.items()
            }
        return cls(
            carrier_id=data["carrier"],
            rates=rates,
            policy_fee_annual=float(data.get("policy_fee_annual", DEFAULT_POLICY_FEE)),
            monthly_factor=float(data.get("modal_factor_monthly", DEFAULT_MONTHLY_FACTOR)),
            illustrative=bool(data.get("illustrative", False)),
            source=data.get("source", ""),
        )


def load_rate_tables(directory: Optional[str] = None) -> Dict[str, RateTable]:
    directory = directory or RATES_DIR
    tables: Dict[str, RateTable] = {}
    if not os.path.isdir(directory):
        return tables
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(directory, filename), "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data or "carrier" not in data:
            continue
        table = RateTable.from_dict(data)
        tables[table.carrier_id] = table
    return tables


# --------------------------------------------------------------------------

def quote(
    applicant: Applicant,
    carrier: Carrier,
    product: Product,
    tier: Tier,
    tables: Optional[Dict[str, RateTable]] = None,
) -> Quote:
    """Price ``product`` for ``applicant`` at ``tier``."""
    face = product.clamp_face(applicant.face_amount, applicant.age)
    table = (tables or {}).get(carrier.id)

    rate = table.lookup(applicant, tier) if table else None
    if rate is not None:
        fee = table.policy_fee_annual
        monthly_factor = table.monthly_factor
        illustrative = table.illustrative
        basis = table.source or f"{carrier.name} rate table"
    else:
        rate = illustrative_rate(applicant, tier, carrier.rate_index)
        fee = DEFAULT_POLICY_FEE
        monthly_factor = DEFAULT_MONTHLY_FACTOR
        illustrative = True
        basis = "Illustrative model - not a carrier rate table"

    annual = rate * (face / 1000.0) + fee
    return Quote(
        monthly=annual * monthly_factor,
        annual=annual,
        face_amount=face,
        rate_per_1000_annual=rate,
        policy_fee_annual=fee,
        illustrative=illustrative,
        basis=basis,
    )


def face_for_budget(
    applicant: Applicant,
    carrier: Carrier,
    product: Product,
    tier: Tier,
    monthly_budget: float,
    tables: Optional[Dict[str, RateTable]] = None,
) -> Tuple[float, Quote]:
    """Largest face amount this product will issue inside a monthly budget.

    Final expense is nearly always sold to a budget rather than to a face
    amount, so this is the number an agent actually needs.
    """
    probe = Applicant(**{**applicant.__dict__, "face_amount": 1000.0})
    unit = quote(probe, carrier, product, tier, tables)
    fee_monthly = unit.policy_fee_annual * (unit.monthly / unit.annual)
    per_1000_monthly = unit.monthly - fee_monthly
    if per_1000_monthly <= 0:
        return product.face_min, unit
    raw = 1000.0 * (monthly_budget - fee_monthly) / per_1000_monthly
    face = product.clamp_face(round(raw / 250.0) * 250.0, applicant.age)
    priced = Applicant(**{**applicant.__dict__, "face_amount": face})
    return face, quote(priced, carrier, product, tier, tables)


# --------------------------------------------------------------------------

def rate_template(carrier_id: str, tiers: Optional[List[str]] = None) -> str:
    """A starter rate file for a carrier, ready to fill in from the rate book."""
    tiers = tiers or ["level", "graded"]
    ages = [50, 55, 60, 65, 70, 75, 80, 85]
    lines = [
        f"carrier: {carrier_id}",
        "illustrative: false",
        "source: \"<carrier rate book, edition/date>\"",
        f"policy_fee_annual: {DEFAULT_POLICY_FEE}",
        f"modal_factor_monthly: {DEFAULT_MONTHLY_FACTOR}",
        "# Annual premium per $1,000 of face. Ages between entries are",
        "# interpolated linearly, so listing every fifth age is usually enough.",
        "rates:",
    ]
    for tier in tiers:
        lines.append(f"  {tier}:")
        for gender in ("male", "female"):
            lines.append(f"    {gender}:")
            for tob in ("nontobacco", "tobacco"):
                body = ", ".join(f"{a}: 0.0" for a in ages)
                lines.append(f"      {tob}: {{{body}}}")
    return "\n".join(lines) + "\n"


def import_rates_csv(path: str, carrier_id: str) -> Dict[str, Any]:
    """Read a CSV of ``tier,gender,tobacco,age,annual_rate_per_1000`` rows.

    Returns a dict in the same shape as a rate YAML file, ready to dump.
    """
    rates: Dict[str, Dict[str, Dict[str, Dict[int, float]]]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tier = Tier.parse(row["tier"]).key
            gender = row["gender"].strip().lower()
            tob = row["tobacco"].strip().lower()
            tob = "tobacco" if tob in ("y", "yes", "true", "tobacco", "smoker") else "nontobacco"
            (rates.setdefault(tier, {}).setdefault(gender, {}).setdefault(tob, {}))[
                int(row["age"])
            ] = float(row["annual_rate_per_1000"])
    return {"carrier": carrier_id, "illustrative": False, "rates": rates}
