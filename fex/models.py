"""Input and output data structures for the underwriting engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .tiers import Tier


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

@dataclass
class ConditionEntry:
    """One health condition reported by (or inferred for) the applicant.

    ``attrs`` holds the follow-up detail that drives most final expense rules:
    how long ago it was diagnosed or treated, whether the applicant is on
    insulin, whether there was hospitalisation, and so on. Anything absent from
    ``attrs`` is genuinely unknown, and the engine turns it into a question
    rather than guessing.
    """

    id: str
    attrs: Dict[str, Any] = field(default_factory=dict)
    #: Free text the user actually typed, kept for display.
    raw: Optional[str] = None
    #: Set when this condition was inferred from a medication rather than typed.
    inferred_from: Optional[str] = None
    #: True when ``raw`` is the client's own words (or a drug name) rather than
    #: the catalog's label. Only then is it worth echoing back in a reason -
    #: prefixing the catalog label onto a rule that already names the condition
    #: just produces "Heart attack: Heart attack within 24 months".
    verbatim: bool = True

    def get(self, key: str, default: Any = None) -> Any:
        return self.attrs.get(key, default)


@dataclass
class MedicationEntry:
    """A medication the applicant reported."""

    raw: str
    #: Canonical ingredient name once matched, e.g. "apixaban".
    ingredient: Optional[str] = None
    brand: Optional[str] = None
    matched: bool = False
    #: Condition ids this drug implies.
    implies: List[str] = field(default_factory=list)
    note: Optional[str] = None


@dataclass
class Applicant:
    age: int
    gender: str = "male"                     # "male" | "female"
    tobacco: bool = False
    state: Optional[str] = None
    height_in: Optional[float] = None
    weight_lb: Optional[float] = None
    face_amount: float = 10000.0
    conditions: List[ConditionEntry] = field(default_factory=list)
    medications: List[MedicationEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.gender = (self.gender or "male").strip().lower()
        if self.gender in ("m", "man"):
            self.gender = "male"
        elif self.gender in ("f", "woman"):
            self.gender = "female"

    @property
    def bmi(self) -> Optional[float]:
        if not self.height_in or not self.weight_lb:
            return None
        return round(703.0 * float(self.weight_lb) / (float(self.height_in) ** 2), 1)

    @property
    def tobacco_class(self) -> str:
        return "tobacco" if self.tobacco else "nontobacco"

    def condition_ids(self) -> List[str]:
        return [c.id for c in self.conditions]

    def find(self, condition_id: str) -> Optional[ConditionEntry]:
        for c in self.conditions:
            if c.id == condition_id:
                return c
        return None

    def facts(self) -> Dict[str, Any]:
        """Applicant-level facts addressable from rule ``applicant:`` blocks."""
        return {
            "age": self.age,
            "gender": self.gender,
            "tobacco": self.tobacco,
            "state": self.state,
            "height_in": self.height_in,
            "weight_lb": self.weight_lb,
            "bmi": self.bmi,
            "face_amount": self.face_amount,
            "condition_count": len(self.conditions),
            "medication_count": len(self.medications),
        }


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------

@dataclass
class Finding:
    """One rule that fired against this applicant."""

    rule_id: str
    outcome: Tier
    reason: str
    condition_id: Optional[str] = None
    #: True when the rule could not be settled because a detail is missing.
    pending: bool = False
    #: Best case if the unknown resolves favourably (only set when pending).
    best_case: Optional[Tier] = None
    question: Optional[str] = None
    citation: Optional[str] = None
    #: Attribute names the rule needed but did not have.
    unknown_keys: List[str] = field(default_factory=list)
    #: True when `question` is the engine's fallback wording rather than one the
    #: rule author supplied, so the engine may replace it with the catalog's.
    default_question: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "outcome": self.outcome.key,
            "outcome_label": self.outcome.label,
            "reason": self.reason,
            "condition_id": self.condition_id,
            "pending": self.pending,
            "best_case": self.best_case.key if self.best_case else None,
            "question": self.question,
            "citation": self.citation,
            "unknown_keys": self.unknown_keys,
        }


@dataclass
class Quote:
    monthly: float
    annual: float
    face_amount: float
    rate_per_1000_annual: float
    policy_fee_annual: float
    illustrative: bool = True
    basis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "monthly": round(self.monthly, 2),
            "annual": round(self.annual, 2),
            "face_amount": self.face_amount,
            "rate_per_1000_annual": round(self.rate_per_1000_annual, 4),
            "policy_fee_annual": self.policy_fee_annual,
            "illustrative": self.illustrative,
            "basis": self.basis,
        }


@dataclass
class ProductResult:
    carrier_id: str
    carrier_name: str
    product_id: str
    product_name: str
    tier: Tier
    #: Outcome if every open question resolves in the applicant's favour.
    best_case_tier: Tier
    eligible: bool
    findings: List[Finding] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    quote: Optional[Quote] = None
    benefit_schedule: Optional[str] = None
    face_min: Optional[float] = None
    face_max: Optional[float] = None
    am_best: Optional[str] = None

    @property
    def certain(self) -> bool:
        return self.tier == self.best_case_tier

    @property
    def blocking_reasons(self) -> List[str]:
        """Reasons for the driving (worst) outcome only."""
        return [f.reason for f in self.findings if f.outcome == self.tier]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "carrier_id": self.carrier_id,
            "carrier_name": self.carrier_name,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "tier": self.tier.key,
            "tier_label": self.tier.label,
            "best_case_tier": self.best_case_tier.key,
            "best_case_tier_label": self.best_case_tier.label,
            "certain": self.certain,
            "eligible": self.eligible,
            "findings": [f.to_dict() for f in self.findings],
            "blocking_reasons": self.blocking_reasons,
            "open_questions": self.open_questions,
            "notes": self.notes,
            "quote": self.quote.to_dict() if self.quote else None,
            "benefit_schedule": self.benefit_schedule,
            "face_min": self.face_min,
            "face_max": self.face_max,
            "am_best": self.am_best,
        }


@dataclass
class EvaluationReport:
    applicant: Applicant
    results: List[ProductResult]
    unmatched_medications: List[str] = field(default_factory=list)
    inferred_conditions: List[ConditionEntry] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def offers(self) -> List[ProductResult]:
        return [r for r in self.results if r.eligible]

    def to_dict(self) -> Dict[str, Any]:
        a = self.applicant
        return {
            "applicant": {
                "age": a.age,
                "gender": a.gender,
                "tobacco": a.tobacco,
                "state": a.state,
                "height_in": a.height_in,
                "weight_lb": a.weight_lb,
                "bmi": a.bmi,
                "face_amount": a.face_amount,
                "conditions": [
                    {
                        "id": c.id,
                        "attrs": c.attrs,
                        "raw": c.raw,
                        "inferred_from": c.inferred_from,
                    }
                    for c in a.conditions
                ],
                "medications": [
                    {
                        "raw": m.raw,
                        "ingredient": m.ingredient,
                        "brand": m.brand,
                        "matched": m.matched,
                        "implies": m.implies,
                        "note": m.note,
                    }
                    for m in a.medications
                ],
            },
            "results": [r.to_dict() for r in self.results],
            "unmatched_medications": self.unmatched_medications,
            "open_questions": self.open_questions,
            "warnings": self.warnings,
        }
