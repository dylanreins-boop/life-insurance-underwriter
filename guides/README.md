# Drop carrier documents here

This folder is where the real documents go so their rules can be transcribed
into `fex/data/carriers/` and `fex/data/rates/`.

## What to put in

Per carrier, most useful first:

1. **Field underwriting guide** — the knockout questions, the lookback windows,
   the build chart. This is the document that matters most; it settles almost
   every rule in the file.
2. **Application** — the actual Part 1 / Part 2 / Part 3 question wording. Worth
   having because the tool's job is to predict how those questions get answered,
   and the exact wording often carries a qualifier the guide summarises away.
3. **Rate book** — annual premium per $1,000 by age, gender, tobacco class and
   plan, plus the policy fee and the modal factors.
4. **Product outline / state availability grid** — issue ages, face minimums and
   maximums, and which states the plan is not sold in.

PDFs are ideal, including scanned ones. Spreadsheets and screenshots work too.

## Naming

Anything readable is fine, but this makes it unambiguous:

```
guides/mutual_of_omaha/2026-living-promise-field-guide.pdf
guides/mutual_of_omaha/2026-living-promise-rates.pdf
guides/aetna/protection-series-underwriting.pdf
```

Matching the folder name to the carrier's `id` in `fex/data/carriers/` (see
`fex carriers`) removes any guessing about which file belongs to which.

## Two ways to hand them over

- **Commit them here and push.** Works because this repository is private.
  If it ever goes public, purge this folder from history first — field
  underwriting guides are distributed to appointed agents, not published.
- **Attach them in chat instead**, and nothing proprietary is committed at all.
  Better if there is any doubt about what your carrier contracts allow.

## What gets extracted

For each carrier:

- Knockout questions become `outcome: decline` rules.
- Lookback windows become `when: {months_since_event: {lt: N}}` on the existing
  rule ids, so the carrier file states only where it differs from the market.
- The printed height/weight table replaces the BMI approximation under `build:`
  with a `chart:` of real pounds per height.
- Issue ages, face bands and state exclusions go onto the products.
- The rate book becomes `fex/data/rates/<carrier>.yaml`, which overrides the
  illustrative model completely and drops the asterisk from that carrier's
  premiums.
- `verified: true`, `as_of:` set to the guide's edition or date, and
  `source_note:` citing the document it came from.

Anything a guide does not address stays an explicit default rather than being
invented, and genuine ambiguities get raised rather than guessed. Every rule
transcribed gets a test pinning the case that rule exists to catch, so a later
edit that breaks it fails the suite.

## Checking progress

```bash
fex carriers        # the last column reads verified / unverified
```
