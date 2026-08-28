# Read this before you quote a client

**The carrier guidelines in this repository are unverified defaults.**

They were seeded from general knowledge of how the final expense market
underwrites — the shape of the products, the questions carriers ask, the
lookback windows that are common across the industry. They were **not**
transcribed from any carrier's field underwriting guide, and no carrier has
reviewed or approved them.

Every carrier file carries `verified: false` for exactly this reason. A test in
the suite enforces it, so the flag cannot quietly go missing.

**The premiums are illustrative.** Unless you have dropped a real rate table
into `fex/data/rates/`, every premium comes from a parametric model calibrated
to the middle of the market and scaled by a per-carrier price index. It is
useful for ranking carriers against each other. It is not a quote.

## What this tool is actually good for

- Narrowing thirty carriers to the three or four worth a phone call.
- Catching the knockout you would otherwise have missed — the Aricept on the
  med list, the oxygen, the dialysis.
- Knowing which follow-up question actually changes the answer, so you ask it
  while the client is still in front of you.
- Showing a client *why* a case lands on graded instead of level.

## What it is not

- Not a quoting engine. Run the carrier's own illustration.
- Not a substitute for the field underwriting guide.
- Not a binding indication of insurability. Only the carrier underwrites.

## Making it authoritative

The engine is entirely data-driven, so the path from "useful default" to
"actually correct" is filling in data, not writing code:

1. Open the carrier's current field underwriting guide.
2. Correct the rules in `fex/data/carriers/<carrier>.yaml` to match it.
3. Replace the BMI approximation under `build:` with the guide's printed
   height/weight chart.
4. Set `verified: true`, put the guide's edition or date in `as_of`, and cite
   it in `source_note`.
5. Drop the real rate table into `fex/data/rates/<carrier>.yaml`
   (`fex rates template <carrier_id>` prints the shape).

The test suite keeps this honest without needing to be edited: a carrier
carrying `verified: true` must also carry an `as_of`, a `source_note` naming the
document, and a build chart that is no longer the BMI approximation - and a file
still carrying the seeded `source_note` may not claim to be verified at all.
