"""The rule DSL used by the carrier guideline files.

A rule is a small YAML object. The engine walks every rule for a product and
collects a :class:`~fex.models.Finding` for each one that fires; the product's
tier is then the worst outcome across all findings.

Example::

    - id: cancer_recent
      label: Internal cancer treated within 24 months
      conditions: [cancer_internal, leukemia_lymphoma]
      when:
        months_since_treatment: {lt: 24}
      outcome: decline
      question: How many months since the last cancer treatment ended?

``conditions``      fires if the applicant has ANY of these condition ids
``conditions_all``  fires only if the applicant has ALL of these condition ids
``when``            predicates against the matched condition's own attributes
``applicant``       predicates against applicant facts (age, bmi, face_amount...)
``outcome``         the worst tier this finding permits
``on_missing``      what to do when ``when`` references an unknown detail:
                    ``pending`` (default) records both branches and asks a
                    question, ``fire`` assumes the worst, ``skip`` ignores it.

Operators inside a predicate: ``eq ne lt lte gt gte in not_in between exists
is_true is_false contains``. A bare scalar means ``eq``; a bare list means
``in``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import Applicant, ConditionEntry, Finding
from .tiers import Tier

MISSING = object()


class RuleError(ValueError):
    """Raised when a rule file is malformed."""


# --------------------------------------------------------------------------
# Predicate evaluation
# --------------------------------------------------------------------------

def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return _norm(actual) == _norm(expected)
    if op == "ne":
        return _norm(actual) != _norm(expected)
    if op == "in":
        return _norm(actual) in [_norm(v) for v in expected]
    if op == "not_in":
        return _norm(actual) not in [_norm(v) for v in expected]
    if op == "contains":
        return _norm(expected) in _norm(actual)
    if op == "is_true":
        return bool(actual) is bool(expected)
    if op == "is_false":
        return bool(actual) is not bool(expected)
    if op == "between":
        low, high = expected
        return float(low) <= float(actual) <= float(high)
    if op in ("lt", "lte", "gt", "gte"):
        try:
            a, e = float(actual), float(expected)
        except (TypeError, ValueError):
            raise RuleError(f"operator {op!r} needs numbers, got {actual!r}/{expected!r}")
        return {"lt": a < e, "lte": a <= e, "gt": a > e, "gte": a >= e}[op]
    raise RuleError(f"unknown operator: {op!r}")


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().lower()
    return value


def _eval_predicate(spec: Any, actual: Any) -> Optional[bool]:
    """Evaluate one predicate. Returns None when the answer is unknown."""
    if isinstance(spec, dict):
        # `exists` is answerable even when the value is missing.
        if "exists" in spec:
            want = bool(spec["exists"])
            return (actual is not MISSING) is want
        if actual is MISSING:
            return None
        for op, expected in spec.items():
            if not _compare(op, actual, expected):
                return False
        return True
    if actual is MISSING:
        return None
    if isinstance(spec, list):
        return _compare("in", actual, spec)
    if isinstance(spec, bool):
        return bool(actual) is spec
    return _compare("eq", actual, spec)


def _eval_block(block: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[Optional[bool], List[str]]:
    """Evaluate a predicate block against a fact dict.

    Returns ``(result, unknown_keys)``. ``result`` is None when at least one
    predicate could not be answered and no other predicate already ruled the
    block out.
    """
    unknown: List[str] = []
    for key, spec in (block or {}).items():
        actual = facts.get(key, MISSING)
        if actual is None:
            actual = MISSING
        outcome = _eval_predicate(spec, actual)
        if outcome is False:
            return False, []          # definitively does not apply
        if outcome is None:
            unknown.append(key)
    if unknown:
        return None, unknown
    return True, []


# --------------------------------------------------------------------------
# Rule
# --------------------------------------------------------------------------

@dataclass
class Rule:
    id: str
    outcome: Tier
    label: str = ""
    conditions: List[str] = field(default_factory=list)
    conditions_all: List[str] = field(default_factory=list)
    when: Dict[str, Any] = field(default_factory=dict)
    applicant: Dict[str, Any] = field(default_factory=dict)
    question: Optional[str] = None
    reason: Optional[str] = None
    citation: Optional[str] = None
    on_missing: str = "pending"
    note: Optional[str] = None
    #: Set to true on rules contributed by a shared rule pack.
    source: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source: Optional[str] = None) -> "Rule":
        if "id" not in data:
            raise RuleError(f"rule is missing an id: {data!r}")
        if "outcome" not in data:
            raise RuleError(f"rule {data['id']!r} is missing an outcome")
        unknown_keys = set(data) - {
            "id", "outcome", "label", "conditions", "conditions_all", "when",
            "applicant", "question", "reason", "citation", "on_missing", "note",
        }
        if unknown_keys:
            raise RuleError(f"rule {data['id']!r} has unknown keys: {sorted(unknown_keys)}")
        on_missing = data.get("on_missing", "pending")
        if on_missing not in ("pending", "fire", "skip"):
            raise RuleError(f"rule {data['id']!r}: bad on_missing {on_missing!r}")
        return cls(
            id=data["id"],
            outcome=Tier.parse(data["outcome"]),
            label=data.get("label", data["id"].replace("_", " ")),
            conditions=list(data.get("conditions") or []),
            conditions_all=list(data.get("conditions_all") or []),
            when=dict(data.get("when") or {}),
            applicant=dict(data.get("applicant") or {}),
            question=data.get("question"),
            reason=data.get("reason"),
            citation=data.get("citation"),
            on_missing=on_missing,
            note=data.get("note"),
            source=source,
        )

    @property
    def is_global(self) -> bool:
        return not self.conditions and not self.conditions_all

    def _describe(self, entry: Optional[ConditionEntry]) -> str:
        if self.reason:
            return self.reason
        if entry is not None and entry.raw:
            return f"{entry.raw}: {self.label}"
        return self.label

    # ------------------------------------------------------------------
    def evaluate(self, applicant: Applicant) -> List[Finding]:
        """Return every finding this rule produces for the applicant."""
        app_result, app_unknown = _eval_block(self.applicant, applicant.facts())
        if app_result is False:
            return []

        targets = self._targets(applicant)
        if targets is None:
            return []

        findings: List[Finding] = []
        for entry in targets:
            facts = dict(entry.attrs) if entry is not None else {}
            cond_result, cond_unknown = _eval_block(self.when, facts)
            if cond_result is False:
                continue
            unknown = list(app_unknown) + list(cond_unknown)
            pending = bool(unknown) or app_result is None or cond_result is None
            if pending and self.on_missing == "skip":
                continue
            finding = Finding(
                rule_id=self.id,
                outcome=self.outcome,
                reason=self._describe(entry),
                condition_id=entry.id if entry is not None else None,
                pending=pending and self.on_missing == "pending",
                citation=self.citation,
            )
            if finding.pending:
                finding.best_case = Tier.PREFERRED
                finding.question = self.question or self._default_question(unknown, entry)
                finding.reason = f"{finding.reason} (unconfirmed)"
            findings.append(finding)
        return findings

    def _targets(self, applicant: Applicant) -> Optional[List[Optional[ConditionEntry]]]:
        """Which condition entries this rule should be evaluated against."""
        if self.conditions_all:
            if not all(applicant.find(cid) for cid in self.conditions_all):
                return None
            anchor = applicant.find(self.conditions_all[0])
            return [anchor]
        if self.conditions:
            matches = [c for c in applicant.conditions if c.id in self.conditions]
            return matches or None
        return [None]  # global rule

    def _default_question(
        self, unknown: Sequence[str], entry: Optional[ConditionEntry]
    ) -> str:
        subject = entry.raw or entry.id.replace("_", " ") if entry else "the applicant"
        pretty = ", ".join(k.replace("_", " ") for k in unknown) or "further detail"
        return f"Need {pretty} for {subject} to settle '{self.label}'."


def load_rules(items: Sequence[Dict[str, Any]], source: Optional[str] = None) -> List[Rule]:
    return [Rule.from_dict(item, source=source) for item in items or []]


def merge_rules(*groups: Sequence[Rule]) -> List[Rule]:
    """Merge rule groups left to right; a later rule replaces an earlier one
    with the same id. This is how a carrier file overrides a shared rule pack."""
    merged: Dict[str, Rule] = {}
    for group in groups:
        for rule in group:
            merged[rule.id] = rule
    return list(merged.values())
