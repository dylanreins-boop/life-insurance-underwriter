"""Loading carriers, their product portfolios and their underwriting rules.

A carrier file describes one company. Underwriting is defined once at the
carrier level and produces a single tier for the applicant; the product
portfolio then decides which policy that tier actually maps to. That mirrors
how final expense is really sold -- you take one application to a carrier and
the carrier tells you which of its plans the client landed on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import yaml

from .build import BuildChart
from .rules import Rule, load_rules, merge_rules
from .tiers import Tier

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CARRIER_DIR = os.path.join(DATA_DIR, "carriers")


@dataclass
class Product:
    id: str
    name: str
    tier: Tier
    issue_age_min: int = 0
    issue_age_max: int = 120
    face_min: float = 0.0
    face_max: float = 1_000_000.0
    #: (min_age, max_age, max_face) bands. Carriers routinely shrink the face
    #: maximum at older ages, so quoting the headline maximum to an 82-year-old
    #: would be wrong.
    face_bands: List[tuple] = field(default_factory=list)
    benefit_schedule: str = ""
    #: Guaranteed issue plans ask no health questions, so they stay available
    #: even when the health rules produce a decline.
    bypass_underwriting: bool = False
    states_excluded: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    #: Documents cover products, not companies. A carrier's field guide often
    #: describes two of its three plans, so verification is tracked per product
    #: rather than being an all-or-nothing claim about the company.
    verified: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Product":
        ages = data.get("issue_ages") or {}
        face = data.get("face") or {}
        return cls(
            id=data["id"],
            name=data["name"],
            tier=Tier.parse(data["tier"]),
            issue_age_min=int(ages.get("min", 0)),
            issue_age_max=int(ages.get("max", 120)),
            face_min=float(face.get("min", 0)),
            face_max=float(face.get("max", 1_000_000)),
            face_bands=[
                (int(b["ages"][0]), int(b["ages"][1]), float(b["max"]))
                for b in (face.get("bands") or [])
            ],
            benefit_schedule=data.get("benefit_schedule", ""),
            bypass_underwriting=bool(data.get("bypass_underwriting", False)),
            states_excluded=[s.upper() for s in (data.get("states_excluded") or [])],
            notes=list(data.get("notes") or []),
            verified=bool(data.get("verified", False)),
        )

    def accepts_age(self, age: int) -> bool:
        return self.issue_age_min <= age <= self.issue_age_max

    def accepts_state(self, state: Optional[str]) -> bool:
        return not state or state.upper() not in self.states_excluded

    def face_max_for(self, age: int) -> float:
        """Face maximum at this issue age, honouring any published age bands.

        If bands are published but none covers this age, fall back to the
        smallest of them rather than the headline maximum. A gap in the bands is
        a transcription mistake, and in a quoting tool the safe direction to be
        wrong is downward.
        """
        if not self.face_bands:
            return self.face_max
        for low, high, limit in self.face_bands:
            if low <= age <= high:
                return limit
        return min(limit for _, _, limit in self.face_bands)

    def clamp_face(self, face: float, age: Optional[int] = None) -> float:
        ceiling = self.face_max if age is None else self.face_max_for(age)
        return max(self.face_min, min(ceiling, face))


@dataclass
class MedicationRules:
    """A carrier's own named medication list.

    Some field guides skip the diagnosis entirely and name the drugs: "proposed
    insureds currently taking any of the following are not eligible". That is a
    more direct and more reliable signal than inferring a condition from the
    drug and then rating the condition, so when a carrier publishes such a list
    it is matched first.

    ``ask`` is the "please state the reason for this medication on the
    application" list. It does not change the tier; it raises a question.
    """

    decline: List[str] = field(default_factory=list)
    graded: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    ask: List[str] = field(default_factory=list)
    source: str = ""

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["MedicationRules"]:
        if not data:
            return None
        return cls(
            decline=list(data.get("decline") or []),
            graded=list(data.get("graded") or []),
            modified=list(data.get("modified") or []),
            ask=list(data.get("ask") or []),
            source=data.get("source", ""),
        )

    def rated(self) -> List[tuple]:
        """(tier, names) pairs, worst first, for the lists that change the tier."""
        return [
            (Tier.DECLINE, self.decline),
            (Tier.MODIFIED, self.modified),
            (Tier.GRADED, self.graded),
        ]


@dataclass
class Carrier:
    id: str
    name: str
    products: List[Product]
    rules: List[Rule]
    build: Optional[BuildChart] = None
    am_best: Optional[str] = None
    rate_index: float = 1.0
    underwriting_style: str = ""
    verified: bool = False
    as_of: str = ""
    source_note: str = ""
    notes: List[str] = field(default_factory=list)
    states_excluded: List[str] = field(default_factory=list)
    extends: List[str] = field(default_factory=list)
    medications: Optional[MedicationRules] = None

    def accepts_state(self, state: Optional[str]) -> bool:
        return not state or state.upper() not in self.states_excluded

    @property
    def best_tier(self) -> Tier:
        return min((p.tier for p in self.products), key=lambda t: t.rank, default=Tier.DECLINE)


# --------------------------------------------------------------------------

DEFAULT_BUILD = {"bmi": {"min": 17, "preferred_max": 32, "level_max": 40, "graded_max": 47}}


def load_rulepacks(path: Optional[str] = None) -> Dict[str, List[Rule]]:
    path = path or os.path.join(DATA_DIR, "rulepacks.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {name: load_rules(items, source=name) for name, items in raw.items()}


def load_carrier(data: Dict[str, Any], packs: Dict[str, List[Rule]]) -> Carrier:
    uw = data.get("underwriting") or {}
    extends = list(uw.get("extends") or [])
    missing = [p for p in extends if p not in packs]
    if missing:
        raise KeyError(f"carrier {data['id']!r} extends unknown rule pack(s): {missing}")
    groups: List[Sequence[Rule]] = [packs[name] for name in extends]
    groups.append(load_rules(uw.get("rules"), source=data["id"]))

    disabled = set(uw.get("disable") or [])
    rules = [r for r in merge_rules(*groups) if r.id not in disabled]

    return Carrier(
        id=data["id"],
        name=data["name"],
        products=[Product.from_dict(p) for p in data.get("products") or []],
        rules=rules,
        build=BuildChart.from_dict(data.get("build") or DEFAULT_BUILD),
        am_best=data.get("am_best"),
        rate_index=float(data.get("rate_index", 1.0)),
        underwriting_style=data.get("underwriting_style", ""),
        verified=bool(data.get("verified", False)),
        as_of=str(data.get("as_of", "")),
        source_note=data.get("source_note", ""),
        notes=list(data.get("notes") or []),
        states_excluded=[s.upper() for s in (data.get("states_excluded") or [])],
        extends=extends,
        medications=MedicationRules.from_dict(data.get("medications")),
    )


def load_carriers(
    directory: Optional[str] = None, packs: Optional[Dict[str, List[Rule]]] = None
) -> List[Carrier]:
    directory = directory or CARRIER_DIR
    packs = packs if packs is not None else load_rulepacks()
    carriers: List[Carrier] = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(directory, filename), "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data:
            continue
        carriers.append(load_carrier(data, packs))
    return carriers
