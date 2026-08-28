"""Streamlit front end for the final expense underwriting engine.

    pip install -r requirements.txt
    streamlit run streamlit_app.py

Deploys to Streamlit Community Cloud as-is: point it at this repo and set the
main file to ``streamlit_app.py``.
"""

from __future__ import annotations

import io
import csv
from typing import Any, Dict, List

import streamlit as st

from fex.catalog import Catalog
from fex.engine import Engine
from fex.models import Applicant
from fex.tiers import Tier

st.set_page_config(page_title="Final Expense Underwriter", page_icon="🩺", layout="wide")

TIER_STYLE = {
    Tier.PREFERRED: ("#0b6b3a", "#d8f3e3", "Preferred"),
    Tier.LEVEL: ("#12603f", "#ddf1e6", "Level"),
    Tier.GRADED: ("#8a5a00", "#fdefd0", "Graded"),
    Tier.MODIFIED: ("#8a3b00", "#fde5d2", "Modified"),
    Tier.GI: ("#4a4a6a", "#e6e6f2", "Guaranteed Issue"),
    Tier.DECLINE: ("#8a1c1c", "#fadddd", "Decline"),
}


@st.cache_resource
def get_engine() -> Engine:
    return Engine.load()


def badge(tier: Tier) -> str:
    fg, bg, label = TIER_STYLE[tier]
    return (
        f"<span style='background:{bg};color:{fg};padding:2px 10px;border-radius:12px;"
        f"font-weight:600;font-size:0.85rem;white-space:nowrap'>{label}</span>"
    )


engine = get_engine()
catalog: Catalog = engine.catalog

st.title("Final Expense Eligibility & Quoting")
st.caption(
    "Enter medications and conditions, add height and weight, and see which carriers "
    "issue level, graded, modified or guaranteed issue."
)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Applicant")
    age = st.number_input("Age", min_value=18, max_value=95, value=68, step=1)
    gender = st.radio("Gender", ["male", "female"], horizontal=True)
    tobacco = st.checkbox("Uses tobacco")
    state = st.text_input("State (2-letter, optional)", max_chars=2).upper() or None

    st.subheader("Build")
    c1, c2 = st.columns(2)
    feet = c1.number_input("Height (ft)", min_value=4, max_value=7, value=5)
    inches = c2.number_input("in", min_value=0, max_value=11, value=6)
    weight = st.number_input("Weight (lb)", min_value=70, max_value=500, value=190, step=1)
    height_in = feet * 12 + inches
    bmi = round(703.0 * weight / (height_in**2), 1)
    st.caption(f"BMI **{bmi}**  ·  {feet}'{inches}\"  {weight} lb")

    st.subheader("Coverage")
    mode = st.radio("Solve for", ["Face amount", "Monthly budget"], horizontal=True)
    if mode == "Face amount":
        face = st.number_input(
            "Face amount ($)", min_value=1000, max_value=50000, value=12000, step=500
        )
        budget = None
    else:
        budget = st.number_input(
            "Monthly budget ($)", min_value=10, max_value=800, value=85, step=5
        )
        face = 10000

# ---------------------------------------------------------------- inputs
left, right = st.columns(2)

with left:
    st.subheader("Medications")
    med_text = st.text_area(
        "One per line. Brand or generic; dosages are ignored.",
        placeholder="Eliquis 5mg\nmetformin 500mg\nLantus\nlisinopril",
        height=170,
        label_visibility="visible",
    )
    med_list = [m.strip() for m in med_text.splitlines() if m.strip()]

with right:
    st.subheader("Health conditions")
    all_conditions = sorted(catalog.conditions.values(), key=lambda c: c.label)
    picked = st.multiselect(
        "Search and select",
        options=[c.id for c in all_conditions],
        format_func=lambda cid: catalog.conditions[cid].label,
    )
    free_text = st.text_area(
        "Or type them free-form, one per line",
        placeholder="mini stroke\nsugar\nhigh blood pressure",
        height=90,
    )
    free_list = [c.strip() for c in free_text.splitlines() if c.strip()]

# Resolve everything the user gave us so we know which follow-ups to ask.
staged, unmatched_conditions = catalog.parse_conditions(list(picked) + free_list)
meds, unmatched_meds = catalog.parse_medications(med_list)
inferred, med_questions = engine.infer_from_medications(meds)

active_ids: List[str] = []
for entry in staged + inferred:
    if entry.id not in active_ids and entry.id in catalog.conditions:
        active_ids.append(entry.id)

# ---------------------------------------------------------------- follow-ups
answers: Dict[str, Dict[str, Any]] = {}
if active_ids:
    st.divider()
    st.subheader("Details")
    st.caption(
        "These are the answers carrier rules actually key off. Anything you leave "
        "blank is treated as unknown, and the carrier shows its worst case with the "
        "question listed."
    )
    for cid in active_ids:
        cond = catalog.conditions[cid]
        if not cond.followups:
            continue
        source = next(
            (e.inferred_from for e in inferred if e.id == cid and e.inferred_from), None
        )
        title = cond.label + (f"  ·  from {source}" if source else "")
        with st.expander(title, expanded=True):
            cols = st.columns(min(3, len(cond.followups)))
            for i, f in enumerate(cond.followups):
                key = f"{cid}.{f['key']}"
                ftype = f.get("type", "bool")
                col = cols[i % len(cols)]
                if ftype == "bool":
                    value = col.selectbox(
                        f["question"], ["Unknown", "Yes", "No"], key=key
                    )
                    if value != "Unknown":
                        answers.setdefault(cid, {})[f["key"]] = value == "Yes"
                else:
                    value = col.text_input(f["question"], key=key, placeholder="leave blank if unknown")
                    if value.strip():
                        try:
                            answers.setdefault(cid, {})[f["key"]] = (
                                float(value) if ftype == "float" else int(float(value))
                            )
                        except ValueError:
                            col.warning("Enter a number")

# ---------------------------------------------------------------- evaluate
condition_input: List[Dict[str, Any]] = []
for entry in staged:
    condition_input.append(
        {"name": entry.id, "attrs": {**entry.attrs, **answers.get(entry.id, {})}}
    )
extra_answers = {
    cid: vals for cid, vals in answers.items() if cid not in {e.id for e in staged}
}

applicant, _, bad_meds, questions = engine.build_applicant(
    age=int(age),
    gender=gender,
    tobacco=tobacco,
    state=state,
    height_in=float(height_in),
    weight_lb=float(weight),
    face_amount=float(face),
    conditions=condition_input,
    medications=med_list,
)
# Fold the answers given for medication-inferred conditions back onto the applicant.
for entry in applicant.conditions:
    entry.attrs.update(extra_answers.get(entry.id, {}))

report = engine.evaluate(applicant, extra_questions=questions)
report.unmatched_medications = bad_meds

if budget is not None:
    from fex.quoting import face_for_budget

    by_id = {c.id: c for c in engine.carriers}
    for result in report.results:
        carrier = by_id.get(result.carrier_id)
        if not carrier or not result.eligible:
            continue
        product = next((p for p in carrier.products if p.id == result.product_id), None)
        if not product:
            continue
        solved_face, solved_quote = face_for_budget(
            applicant, carrier, product, result.tier, float(budget), engine.rate_tables
        )
        result.quote = solved_quote

st.divider()

# ---------------------------------------------------------------- results
offers = [r for r in report.results if r.eligible]
declines = [r for r in report.results if not r.eligible]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Carriers checked", len(report.results))
m2.metric("Offers", len(offers))
m3.metric("Day-one benefit", sum(1 for r in offers if r.tier in (Tier.PREFERRED, Tier.LEVEL)))
m4.metric("Open questions", len(report.open_questions))

for warning in report.warnings:
    st.warning(warning)
if unmatched_conditions:
    st.warning("Not recognised as conditions: " + ", ".join(unmatched_conditions))
if bad_meds:
    st.warning("Medications not recognised: " + ", ".join(bad_meds))

if report.open_questions:
    with st.expander(f"Ask the client ({len(report.open_questions)})", expanded=True):
        for q in report.open_questions:
            st.markdown(f"- {q}")

st.subheader(f"Offers ({len(offers)})")
if not offers:
    st.error("No carrier in the file issues on this case.")

for result in offers:
    head = st.container()
    cols = head.columns([3, 1.4, 1.5, 2.6])
    cols[0].markdown(
        f"**{result.carrier_name}**  \n<span style='color:#666;font-size:0.85rem'>"
        f"{result.product_name}</span>",
        unsafe_allow_html=True,
    )
    cols[1].markdown(badge(result.tier), unsafe_allow_html=True)
    if result.quote:
        star = "" if not result.quote.illustrative else "*"
        cols[2].markdown(
            f"**${result.quote.monthly:,.2f}**{star}/mo  \n"
            f"<span style='color:#666;font-size:0.8rem'>${result.quote.face_amount:,.0f} face</span>",
            unsafe_allow_html=True,
        )
    if not result.certain:
        cols[3].markdown(
            f"Could reach {badge(result.best_case_tier)} once the open questions are answered",
            unsafe_allow_html=True,
        )
    elif result.blocking_reasons:
        cols[3].caption(result.blocking_reasons[0])

    with st.expander("Why", expanded=False):
        st.caption(result.benefit_schedule)
        if result.am_best:
            st.caption(f"AM Best: {result.am_best}")
        if result.findings:
            for f in result.findings:
                mark = "❓" if f.pending else "•"
                st.markdown(f"{mark} **{f.outcome.label}** — {f.reason}")
        else:
            st.markdown("• Nothing on the application downgrades this case.")
        for note in result.notes:
            st.caption(note)

if declines:
    with st.expander(f"Declines ({len(declines)})"):
        for result in declines:
            reasons = "; ".join(result.blocking_reasons[:3]) or "No product available"
            st.markdown(f"**{result.carrier_name}** — {reasons}")

# ---------------------------------------------------------------- export
buf = io.StringIO()
writer = csv.writer(buf)
writer.writerow(
    ["Carrier", "Product", "Tier", "Best case", "Monthly", "Face", "AM Best", "Reasons"]
)
for result in report.results:
    writer.writerow(
        [
            result.carrier_name,
            result.product_name,
            result.tier.label,
            result.best_case_tier.label,
            f"{result.quote.monthly:.2f}" if result.quote else "",
            f"{result.quote.face_amount:.0f}" if result.quote else "",
            result.am_best or "",
            "; ".join(result.blocking_reasons),
        ]
    )
st.download_button(
    "Download comparison (CSV)", buf.getvalue(), "fe_comparison.csv", "text/csv"
)

st.divider()
st.caption(
    "**Premiums marked with an asterisk come from an illustrative model, not a carrier "
    "rate book, and are for comparison only.** Carrier guidelines in this tool are "
    "unverified defaults seeded from general market knowledge. Confirm every rule and "
    "rate against the carrier's current field underwriting guide before quoting a client."
)
