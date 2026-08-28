# Final Expense Underwriting & Quoting Tool

Enter a client's **medications**, **health conditions**, and **height and
weight**. Get back, for every carrier in the file, which final expense whole
life product they would land on — **level**, **graded**, **modified**,
**guaranteed issue**, or **decline** — with a premium and the reason for the
outcome.

> **Read [DISCLAIMER.md](DISCLAIMER.md) first.** The carrier guidelines shipped
> here are unverified defaults and the premiums are illustrative. This tool
> narrows the field; the carrier's own guide and illustration close the case.

---

## Quick start

```bash
pip install -r requirements.txt

# Web UI
streamlit run streamlit_app.py

# Or the command line
python -m fex.cli quote --age 68 --female --height 5-4 --weight 190 --face 12000 \
    --med "eliquis 5mg" --med metformin --med lantus \
    --condition "diabetes:insulin=yes,age_at_diagnosis=52"
```

The engine itself needs only PyYAML. Streamlit is only for the web UI.

---

## What it does

**Reads a real med list.** Brand or generic, dosages and instructions ignored.
`Eliquis 5mg twice a day`, `metformin 500mg`, `Lantus`, `water pill` all match.
267 drugs, 376 brand names.

**Infers conditions from drugs — but only when the drug is unambiguous.**
Aricept means dementia. Lantus means insulin-dependent diabetes, and it sets the
insulin flag, which is what actually moves the tier. Eliquis means *ask*: it is
prescribed for atrial fibrillation and for blood clots, and those two underwrite
differently, so the tool raises the question instead of guessing.

**Treats unknowns as unknowns.** If you don't know how many months since the
cancer treatment ended, the carrier shows the conservative outcome, flags the
result as unsettled, and puts the question on a list to ask the client. It never
silently assumes the good answer or the bad one.

**Applies each carrier's build chart.** Height and weight are a top-three reason
a level case comes back graded. Every carrier has its own chart, so the same
5'4" 250 lb client is level at one carrier, graded at the next, and declined at a
third — and the tool shows you which.

**Prices and ranks.** Sorted by what the case can reach once the open questions
are answered, then by the conservative outcome, then by price.

---

## Example

```
$ python -m fex.cli quote --age 68 --height 5-10 --weight 200 --face 10000 \
      --condition "heart attack:months_since_event=14" --hide-declines

68 male, non-tobacco, BMI 28.7, $10,000 face
Conditions: heart attack

CARRIER                         TIER           MONTHLY  PRODUCT
----------------------------------------------------------------------------
Americo                         level          $89.67   Eagle Premier (Level)
Royal Neighbors of America      level          $92.37   Simplified Issue Whole Life
Liberty Bankers Life            graded        $109.50   SIMPL Whole Life - Graded
Mutual of Omaha                 graded        $111.30   Living Promise Graded Benefit
...
```

Americo and Royal Neighbors use a 12-month cardiac lookback; most of the market
uses 24. That difference is the whole reason to run the case.

---

## How eligibility is decided

Each carrier's rules produce a **tier**, worst finding wins. The tier then maps
onto the carrier's product portfolio:

| Tier | Meaning |
|---|---|
| `preferred` | Day-one full benefit at the carrier's best rate |
| `level` | Day-one full benefit |
| `graded` | Reduced benefit in years 1–2 (commonly 30% / 70%) |
| `modified` | Return of premium plus interest in years 1–2 |
| `gi` | Guaranteed issue — no health questions, 2-year ROP period |
| `decline` | No offer |

Guaranteed issue plans ask no health questions, so they bypass the rules
entirely and stay available even when everything else declines.

Every result reports two tiers: the **conservative** landing spot given what you
know right now, and the **best case** once the open questions are answered. When
they differ, the tool tells you which question closes the gap.

---

## The rule format

Carrier guidelines are YAML, not code. A rule looks like this:

```yaml
- id: lv_heart_attack
  label: Heart attack within 24 months
  conditions: [heart_attack]
  when:
    months_since_event: {lt: 24}
  outcome: graded
```

Shared rule packs in `fex/data/rulepacks.yaml` hold the market-standard
screens (`core_knockouts`, `level_screen_standard`, `graded_screen_standard`,
and liberal/conservative variants). A carrier pulls in the packs it matches and
then overrides only where it genuinely differs:

```yaml
underwriting:
  # Later packs override earlier ones by rule id.
  extends: [core_knockouts, level_screen_standard, level_screen_liberal]
  disable: [ko_cirrhosis]
  rules:
    - id: moo_insulin_early
      label: Insulin started before age 30
      conditions: [diabetes_type1, diabetes_type2]
      when: {age_at_diagnosis: {lt: 30}, insulin: true}
      outcome: decline
```

Operators: `eq ne lt lte gt gte in not_in between exists is_true is_false
contains`. A bare scalar means `eq`; a bare list means `in`.

`on_missing` controls what happens when a rule needs a detail you don't have:
`pending` (default — record both branches and ask), `fire` (assume the worst),
or `skip` (ignore).

---

## Layout

```
fex/
  tiers.py       Tier ordering; "worst finding wins"
  models.py      Applicant, findings, results
  rules.py       The rule DSL and its evaluator
  build.py       Height/weight charts
  catalog.py     Condition + medication matching from free text
  carriers.py    Carrier and product loading
  quoting.py     Illustrative rate model + real rate table loading
  engine.py      Orchestration
  cli.py         Command line
  data/
    conditions.yaml     78 conditions, 394 aliases, follow-up questions
    medications.yaml    267 drugs with confidence levels
    rulepacks.yaml      Shared underwriting screens
    carriers/           30 carrier files
    rates/              Drop real rate tables here
streamlit_app.py
tests/
```

## Commands

```bash
fex quote ...                       # run a case (see --help)
fex carriers -v                     # every carrier, product, age and face range
fex lookup eliquis                  # what a drug implies and what to ask
fex lookup "congestive heart failure"
fex rates template mutual_of_omaha  # starter rate file to fill in
```

## Adding a carrier

Copy any file in `fex/data/carriers/`, change the ids, point `extends:` at the
packs that match the carrier's application, and add rules only where it differs
from the pack. The test suite validates that every condition a rule references
actually exists, so a typo fails the build rather than silently never firing.

## Tests

```bash
python -m unittest discover -s tests -v
```

56 tests covering the rule evaluator, build charts, the medication matcher,
data integrity across all 30 carrier files, and end-to-end scenarios.
