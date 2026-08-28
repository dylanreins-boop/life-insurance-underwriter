// Runs cases from stdin through the browser engine and prints a comparable
// digest on stdout. Used by tests/test_parity.py.
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const FEX = require(path.join(root, "web", "engine.js"));
const bundle = JSON.parse(fs.readFileSync(path.join(root, "web", "bundle.json"), "utf8"));
const engine = new FEX.Engine(bundle);

const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map((input) => {
  const report = engine.run(input);
  return {
    results: report.results.map((r) => ({
      carrier_id: r.carrier_id,
      product_id: r.product_id,
      tier: r.tier,
      best_case_tier: r.best_case_tier,
      eligible: r.eligible,
      monthly: r.quote ? r.quote.monthly : null,
      reasons: r.blocking_reasons.slice().sort(),
    })),
    open_questions: report.open_questions.slice().sort(),
    conditions: report.applicant.conditions
      .map((c) => c.id + ":" + JSON.stringify(c.attrs, Object.keys(c.attrs).sort()))
      .sort(),
    unmatched_medications: report.unmatched_medications.slice().sort(),
  };
});
process.stdout.write(JSON.stringify(out));
