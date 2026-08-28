"""Putting it together: infer, evaluate, price, rank."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .build import BuildChart
from .carriers import Carrier, Product, load_carriers, load_rulepacks
from .catalog import Catalog, merge_conditions, normalize
from .models import (
    Applicant,
    ConditionEntry,
    EvaluationReport,
    Finding,
    MedicationEntry,
    ProductResult,
)
from .quoting import RateTable, load_rate_tables, quote
from .tiers import Tier, worst


@dataclass
class Engine:
    catalog: Catalog
    carriers: List[Carrier]
    rate_tables: Dict[str, RateTable]

    @classmethod
    def load(cls) -> "Engine":
        return cls(
            catalog=Catalog.load(),
            carriers=load_carriers(packs=load_rulepacks()),
            rate_tables=load_rate_tables(),
        )

    # ------------------------------------------------------------------
    # Building the applicant
    # ------------------------------------------------------------------
    def build_applicant(
        self,
        age: int,
        gender: str = "male",
        tobacco: bool = False,
        state: Optional[str] = None,
        height_in: Optional[float] = None,
        weight_lb: Optional[float] = None,
        face_amount: float = 10000.0,
        conditions: Optional[Iterable[Any]] = None,
        medications: Optional[Iterable[str]] = None,
    ) -> tuple:
        """Return ``(applicant, unmatched_conditions, unmatched_meds, med_questions)``."""
        reported, unmatched_conditions = self.catalog.parse_conditions(conditions or [])
        meds, unmatched_meds = self.catalog.parse_medications(medications or [])
        inferred, med_questions = self.infer_from_medications(meds)

        applicant = Applicant(
            age=age,
            gender=gender,
            tobacco=tobacco,
            state=state,
            height_in=height_in,
            weight_lb=weight_lb,
            face_amount=face_amount,
            conditions=merge_conditions(list(reported) + list(inferred)),
            medications=meds,
        )
        return applicant, unmatched_conditions, unmatched_meds, med_questions

    def infer_from_medications(
        self, meds: List[MedicationEntry]
    ) -> tuple:
        """Conditions implied by the drug list, plus questions for the ambiguous ones.

        High and medium confidence drugs add the condition outright. Low
        confidence drugs deliberately add nothing -- a drug with several common
        indications tells you to ask, not to assume.
        """
        by_name = {d.ingredient: d for d in self.catalog.drugs}
        inferred: List[ConditionEntry] = []
        questions: List[str] = []

        for med in meds:
            if not med.matched or not med.ingredient:
                continue
            drug = by_name.get(med.ingredient)
            if not drug:
                continue
            label = med.brand or drug.ingredient

            if drug.confidence == "low":
                if drug.implies or drug.note:
                    candidates = ", ".join(
                        self.catalog.label(c) for c in drug.implies
                    )
                    detail = drug.note or ""
                    tail = f" Possible: {candidates}." if candidates else ""
                    questions.append(f"{label}: what is it being taken for? {detail}{tail}".strip())
                continue

            for cond_id in drug.implies:
                attrs = dict(drug.set_attrs.get(cond_id, {}))
                inferred.append(
                    ConditionEntry(
                        id=cond_id,
                        attrs=attrs,
                        raw=f"{label} (from medication)",
                        inferred_from=label,
                    )
                )
            for flag in drug.flags:
                inferred.append(
                    ConditionEntry(id=flag, raw=f"{label} (from medication)", inferred_from=label)
                )
            if drug.confidence == "medium" and drug.note:
                questions.append(f"{label}: {drug.note}")

        return merge_conditions(inferred), questions

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def evaluate_carrier(self, applicant: Applicant, carrier: Carrier) -> Optional[ProductResult]:
        if not carrier.accepts_state(applicant.state):
            return None

        findings: List[Finding] = []
        for rule in carrier.rules:
            findings.extend(rule.evaluate(applicant))
        if carrier.build:
            findings.extend(carrier.build.evaluate(applicant))

        med_findings, med_questions = self._evaluate_medication_list(applicant, carrier)
        findings.extend(med_findings)

        self._improve_questions(findings)
        settled = [f for f in findings if not f.pending]
        pending = [f for f in findings if f.pending]

        tier = worst(carrier.best_tier, *[f.outcome for f in settled]) if settled else carrier.best_tier
        worst_case = worst(tier, *[f.outcome for f in pending]) if pending else tier

        product = self._select_product(carrier, worst_case, applicant)
        best_product = self._select_product(carrier, tier, applicant)

        if product is None and best_product is None:
            return self._declined_result(
                carrier, worst_case, findings, applicant, med_questions
            )

        # Report the conservative landing spot, and show the upside separately.
        # A guaranteed issue plan asks no health questions, so the health rules
        # never drag its tier down -- it issues at its own tier or not at all.
        chosen = product or best_product
        if chosen.bypass_underwriting:
            chosen_tier = chosen.tier
        else:
            chosen_tier = max(worst_case, chosen.tier, key=lambda t: t.rank)

        upside = best_product or chosen
        if upside.bypass_underwriting:
            best_tier = upside.tier
        else:
            best_tier = max(tier, upside.tier, key=lambda t: t.rank)

        priced = quote(applicant, carrier, chosen, chosen_tier, self.rate_tables)

        notes = list(carrier.notes) + list(chosen.notes)
        if not carrier.verified:
            notes.append(
                "Guidelines are unverified defaults - confirm against the carrier's "
                "current field underwriting guide."
            )
        face = chosen.clamp_face(applicant.face_amount, applicant.age)
        if face != applicant.face_amount:
            ceiling = chosen.face_max_for(applicant.age)
            band = (
                f" at issue age {applicant.age}" if ceiling != chosen.face_max else ""
            )
            notes.append(
                f"Face amount adjusted to ${face:,.0f} to fit this product's "
                f"${chosen.face_min:,.0f}-${ceiling:,.0f} range{band}."
            )

        return ProductResult(
            carrier_id=carrier.id,
            carrier_name=carrier.name,
            product_id=chosen.id,
            product_name=chosen.name,
            tier=chosen_tier,
            best_case_tier=best_tier,
            eligible=chosen_tier != Tier.DECLINE,
            findings=sorted(findings, key=lambda f: -f.outcome.rank),
            open_questions=[f.question for f in pending if f.question] + med_questions,
            notes=notes,
            quote=priced,
            benefit_schedule=chosen.benefit_schedule,
            face_min=chosen.face_min,
            face_max=chosen.face_max,
            am_best=carrier.am_best,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _carrier_med_keys(entry: str) -> Set[str]:
        """Expand one carrier list entry into the forms it might be written as.

        Guides list drugs as "Carbidopa/Levodopa", "Megestrol Acetate (Megace)"
        and "Naloxone Hcl", so the slash-separated halves and the parenthesised
        brand each have to be matchable on their own.
        """
        parts = [entry] + re.split(r"[/(),]", entry)
        return {k for k in (normalize(p) for p in parts) if k}

    def _applicant_med_keys(self, med: MedicationEntry) -> Set[str]:
        keys = {normalize(med.raw)}
        if med.ingredient:
            keys.add(normalize(med.ingredient))
            drug = next(
                (d for d in self.catalog.drugs if d.ingredient == med.ingredient), None
            )
            if drug:
                for brand in drug.brands:
                    keys.add(normalize(brand))
        return {k for k in keys if k}

    def _evaluate_medication_list(
        self, applicant: Applicant, carrier: Carrier
    ) -> Tuple[List[Finding], List[str]]:
        """Apply a carrier's own named drug list, if it publishes one.

        A guide that names the drug outright is better evidence than inferring
        a diagnosis from the drug and then rating the diagnosis, so this runs
        alongside the condition rules and, being a finding like any other, the
        worst outcome still wins.
        """
        rules = carrier.medications
        if not rules or not applicant.medications:
            return [], []

        findings: List[Finding] = []
        questions: List[str] = []

        def hit(entry: str, med_keys: Set[str], raw_norm: str) -> bool:
            for key in self._carrier_med_keys(entry):
                if key in med_keys:
                    return True
                if len(key) >= 4 and re.search(rf"\b{re.escape(key)}\b", raw_norm):
                    return True
            return False

        for med in applicant.medications:
            if not med.matched and not med.raw:
                continue
            med_keys = self._applicant_med_keys(med)
            raw_norm = normalize(med.raw)
            label = med.brand or med.ingredient or med.raw

            for tier, names in rules.rated():
                if any(hit(name, med_keys, raw_norm) for name in names):
                    verb = (
                        "is on this carrier's uninsurable medication list"
                        if tier == Tier.DECLINE
                        else f"is on this carrier's {tier.key} medication list"
                    )
                    findings.append(
                        Finding(
                            rule_id=f"med_list_{tier.key}",
                            outcome=tier,
                            reason=f"{label} {verb}",
                            citation=rules.source or None,
                        )
                    )
                    break   # worst list wins; do not also report a lighter one

            if any(hit(name, med_keys, raw_norm) for name in rules.ask):
                questions.append(
                    f"{label}: this carrier requires the reason for this "
                    f"medication on the application."
                )

        return findings, questions

    def _improve_questions(self, findings: List[Finding]) -> None:
        """Replace fallback question wording with the catalog's own follow-up.

        Two carriers with different lookback windows both need the same fact,
        so without this the agent gets "months since event for TIA to settle
        'TIA within 12 months'" and again for 6 months. Asking in the catalog's
        words collapses them to one question the agent can actually read out.
        """
        for finding in findings:
            if not (finding.pending and finding.default_question and finding.condition_id):
                continue
            asks = [
                self.catalog.followup_question(finding.condition_id, key)
                for key in finding.unknown_keys
            ]
            asks = [a for a in asks if a]
            if len(asks) == len(finding.unknown_keys) and asks:
                finding.question = " ".join(dict.fromkeys(asks))

    def _select_product(
        self, carrier: Carrier, tier: Tier, applicant: Applicant
    ) -> Optional[Product]:
        """Best product this carrier will issue at or below ``tier``."""
        candidates = [
            p
            for p in carrier.products
            if p.accepts_age(applicant.age)
            and p.accepts_state(applicant.state)
            and (p.bypass_underwriting or p.tier.rank >= tier.rank)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda p: p.tier.rank)

    def _declined_result(
        self,
        carrier: Carrier,
        tier: Tier,
        findings: List[Finding],
        applicant: Applicant,
        med_questions: Optional[List[str]] = None,
    ) -> ProductResult:
        reasons = list(findings)
        if not any(f.outcome == Tier.DECLINE for f in findings):
            in_age = any(p.accepts_age(applicant.age) for p in carrier.products)
            reasons.append(
                Finding(
                    rule_id="no_product",
                    outcome=Tier.DECLINE,
                    reason=(
                        f"No product available at issue age {applicant.age}"
                        if not in_age
                        else "This carrier has no plan that issues at the required benefit tier"
                    ),
                )
            )
        return ProductResult(
            carrier_id=carrier.id,
            carrier_name=carrier.name,
            product_id="",
            product_name="No offer",
            tier=Tier.DECLINE,
            best_case_tier=Tier.DECLINE,
            eligible=False,
            findings=sorted(reasons, key=lambda f: -f.outcome.rank),
            open_questions=(
                [f.question for f in findings if f.pending and f.question]
                + list(med_questions or [])
            ),
            notes=list(carrier.notes),
            am_best=carrier.am_best,
        )

    # ------------------------------------------------------------------
    def evaluate(self, applicant: Applicant, extra_questions: Optional[List[str]] = None) -> EvaluationReport:
        results: List[ProductResult] = []
        for carrier in self.carriers:
            result = self.evaluate_carrier(applicant, carrier)
            if result is not None:
                results.append(result)

        # Rank by what the case can actually reach once the open questions are
        # answered, then by the conservative outcome, then by price. Sorting on
        # the worst case alone would bury the carriers worth calling first.
        results.sort(
            key=lambda r: (
                r.best_case_tier.rank,
                r.tier.rank,
                r.quote.monthly if r.quote else 1e9,
            )
        )

        questions: List[str] = list(extra_questions or [])
        for result in results:
            for q in result.open_questions:
                if q not in questions:
                    questions.append(q)

        return EvaluationReport(
            applicant=applicant,
            results=results,
            open_questions=questions,
            warnings=self._warnings(applicant),
        )

    def _warnings(self, applicant: Applicant) -> List[str]:
        warnings: List[str] = []
        if applicant.height_in is None or applicant.weight_lb is None:
            warnings.append(
                "No height/weight entered - build is one of the most common reasons a "
                "level case comes back graded, so every carrier is showing its worst case."
            )
        if any(m.matched is False for m in applicant.medications):
            warnings.append(
                "Some medications were not recognised. Check the spelling, or enter the "
                "condition directly."
            )
        return warnings

    # ------------------------------------------------------------------
    def run(self, **kwargs: Any) -> EvaluationReport:
        """Convenience: build the applicant from raw input and evaluate."""
        applicant, bad_conditions, bad_meds, med_questions = self.build_applicant(**kwargs)
        report = self.evaluate(applicant, extra_questions=med_questions)
        report.unmatched_medications = bad_meds
        if bad_conditions:
            report.warnings.append(
                "Not recognised as conditions: " + ", ".join(bad_conditions)
            )
        return report
