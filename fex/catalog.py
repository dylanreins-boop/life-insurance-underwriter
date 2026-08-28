"""The condition and medication catalogs, plus the free-text matchers.

Agents type what the client says -- "sugar", "water pill", "mini stroke",
"eliquis 5mg twice a day" -- so the matchers strip dosing noise and work
through exact names, aliases, substrings and finally a fuzzy pass.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from .carriers import DATA_DIR
from .models import ConditionEntry, MedicationEntry

#: Dosing and administration words that carry no diagnostic signal.
#: Note that a bare number is deliberately NOT in here: "type 2 diabetes" and
#: "type 1 diabetes" are different conditions, and stripping the digit would
#: collapse them onto each other. Bare numbers are removed only on the second
#: matching pass, once the literal text has failed to match anything.
_NOISE = re.compile(
    r"\b(\d+(\.\d+)?\s*(mg|mcg|ug|g|ml|iu|units?|meq|%)"
    r"|mg|mcg|ml|iu|units?|meq|tab|tabs|tablet|tablets|cap|caps|capsule|capsules"
    r"|pill|pills|dose|doses|daily|nightly|weekly|monthly|bid|tid|qid|qd|qhs|prn|po"
    r"|once|twice|three|times|a|per|day|days|week|night|morning|evening|hs|er|xr|sr|cr"
    r"|inhaler|puff|puffs|injection|shot|patch|pen|cream|drops|solution|oral)\b",
    re.I,
)
_BARE_NUMBER = re.compile(r"\b\d+(\.\d+)?\b")
_PUNCT = re.compile(r"[^a-z0-9+/\- ]+")
_SPACES = re.compile(r"\s+")


def normalize(text: str, strip_numbers: bool = False) -> str:
    text = (text or "").strip().lower()
    text = _PUNCT.sub(" ", text)
    text = _NOISE.sub(" ", text)
    if strip_numbers:
        text = _BARE_NUMBER.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


@dataclass
class ConditionDef:
    id: str
    label: str
    category: str
    aliases: List[str] = field(default_factory=list)
    followups: List[Dict[str, Any]] = field(default_factory=list)

    def followup(self, key: str) -> Optional[Dict[str, Any]]:
        for f in self.followups:
            if f.get("key") == key:
                return f
        return None


@dataclass
class DrugDef:
    ingredient: str
    brands: List[str] = field(default_factory=list)
    implies: List[str] = field(default_factory=list)
    set_attrs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    confidence: str = "medium"
    note: Optional[str] = None

    @property
    def display(self) -> str:
        if self.brands:
            return f"{self.ingredient} ({self.brands[0]})"
        return self.ingredient


class Catalog:
    """Condition and medication lookup built from the YAML data files."""

    def __init__(self, conditions: List[ConditionDef], drugs: List[DrugDef]) -> None:
        self.conditions = {c.id: c for c in conditions}
        self.drugs = drugs

        self._cond_index: Dict[str, str] = {}
        for cond in conditions:
            for name in [cond.id.replace("_", " "), cond.label, *cond.aliases]:
                key = normalize(name)
                if key:
                    self._cond_index.setdefault(key, cond.id)

        self._drug_index: Dict[str, DrugDef] = {}
        for drug in drugs:
            for name in [drug.ingredient, *drug.brands]:
                key = normalize(name)
                if key:
                    self._drug_index.setdefault(key, drug)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, data_dir: Optional[str] = None) -> "Catalog":
        data_dir = data_dir or DATA_DIR
        with open(os.path.join(data_dir, "conditions.yaml"), encoding="utf-8") as fh:
            raw_conditions = yaml.safe_load(fh) or []
        with open(os.path.join(data_dir, "medications.yaml"), encoding="utf-8") as fh:
            raw_drugs = yaml.safe_load(fh) or []

        conditions = [
            ConditionDef(
                id=c["id"],
                label=c["label"],
                category=c.get("category", "other"),
                aliases=list(c.get("aliases") or []),
                followups=list(c.get("followups") or []),
            )
            for c in raw_conditions
        ]
        drugs = [
            DrugDef(
                ingredient=d["ingredient"],
                brands=list(d.get("brands") or []),
                implies=list(d.get("implies") or []),
                set_attrs=dict(d.get("set") or {}),
                flags=list(d.get("flags") or []),
                confidence=d.get("confidence", "medium"),
                note=d.get("note"),
            )
            for d in raw_drugs
        ]
        return cls(conditions, drugs)

    # ------------------------------------------------------------------
    def _match(self, text: str, index: Dict[str, Any]) -> Optional[Any]:
        # Pass 1 keeps digits, so "type 2 diabetes" stays distinct from type 1.
        # Pass 2 drops them, which is what rescues "lisinopril 10" or "asa 81".
        for strip_numbers in (False, True):
            key = normalize(text, strip_numbers=strip_numbers)
            if not key:
                continue
            hit = self._match_key(key, index)
            if hit is not None:
                return hit
        return None

    @staticmethod
    def _match_key(key: str, index: Dict[str, Any]) -> Optional[Any]:
        if key in index:
            return index[key]
        # Longest alias contained in the input, on word boundaries.
        best: Optional[Tuple[int, Any]] = None
        for alias, value in index.items():
            if len(alias) < 4:
                continue
            if re.search(rf"\b{re.escape(alias)}\b", key):
                if best is None or len(alias) > best[0]:
                    best = (len(alias), value)
        if best:
            return best[1]
        close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.86)
        return index[close[0]] if close else None

    def match_condition(self, text: str) -> Optional[str]:
        return self._match(text, self._cond_index)

    def match_drug(self, text: str) -> Optional[DrugDef]:
        return self._match(text, self._drug_index)

    # ------------------------------------------------------------------
    def parse_conditions(
        self, entries: Iterable[Any]
    ) -> Tuple[List[ConditionEntry], List[str]]:
        """Turn user input into condition entries.

        Each entry is either a plain string ("copd") or a dict carrying the
        follow-up detail (``{"name": "copd", "oxygen": true}`` or
        ``{"id": "copd", "attrs": {...}}``).
        """
        parsed: List[ConditionEntry] = []
        unmatched: List[str] = []
        for entry in entries or []:
            if isinstance(entry, str):
                name, attrs = entry, {}
            else:
                entry = dict(entry)
                name = entry.pop("name", None) or entry.pop("id", None) or ""
                attrs = entry.pop("attrs", None) or entry
            cond_id = name if name in self.conditions else self.match_condition(str(name))
            if not cond_id:
                unmatched.append(str(name))
                continue
            # When the caller passed an id (a picker selection rather than typed
            # text), show the catalog's label so generated questions read as
            # English rather than as an identifier.
            raw = self.label(cond_id) if str(name) == cond_id else str(name)
            parsed.append(
                ConditionEntry(id=cond_id, attrs=dict(attrs or {}), raw=raw)
            )
        return merge_conditions(parsed), unmatched

    def parse_medications(
        self, entries: Iterable[str]
    ) -> Tuple[List[MedicationEntry], List[str]]:
        meds: List[MedicationEntry] = []
        unmatched: List[str] = []
        for raw in entries or []:
            raw = str(raw).strip()
            if not raw:
                continue
            drug = self.match_drug(raw)
            if not drug:
                meds.append(MedicationEntry(raw=raw, matched=False))
                unmatched.append(raw)
                continue
            # Only claim a brand when the user actually typed one; otherwise the
            # display name should stay the generic they entered.
            brand = next(
                (b for b in drug.brands if normalize(b) and normalize(b) in normalize(raw)),
                None,
            )
            meds.append(
                MedicationEntry(
                    raw=raw,
                    ingredient=drug.ingredient,
                    brand=brand,
                    matched=True,
                    implies=list(drug.implies),
                    note=drug.note,
                )
            )
        return meds, unmatched

    def followup_question(self, condition_id: str, key: str) -> Optional[str]:
        cond = self.conditions.get(condition_id)
        if not cond:
            return None
        f = cond.followup(key)
        return f.get("question") if f else None

    def label(self, condition_id: str) -> str:
        cond = self.conditions.get(condition_id)
        return cond.label if cond else condition_id.replace("_", " ")


def merge_conditions(entries: List[ConditionEntry]) -> List[ConditionEntry]:
    """Collapse duplicate condition ids, merging their attributes."""
    merged: Dict[str, ConditionEntry] = {}
    for entry in entries:
        existing = merged.get(entry.id)
        if existing is None:
            merged[entry.id] = ConditionEntry(
                id=entry.id,
                attrs=dict(entry.attrs),
                raw=entry.raw,
                inferred_from=entry.inferred_from,
            )
            continue
        for key, value in entry.attrs.items():
            existing.attrs.setdefault(key, value)
        if existing.raw is None:
            existing.raw = entry.raw
        # A directly reported condition outranks an inferred one.
        if entry.inferred_from is None:
            existing.inferred_from = None
    return list(merged.values())
