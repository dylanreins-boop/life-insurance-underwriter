/* UI layer. All underwriting logic lives in engine.js. */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, attrs, children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of [].concat(children || [])) {
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  };
  const money = (n) => "$" + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const money0 = (n) => "$" + Math.round(n).toLocaleString();

  let engine = null;
  const state = {
    age: 68, gender: "female", tobacco: false, state: "",
    feet: 5, inches: 4, weight: 180,
    mode: "face", face: 12000, budget: 85,
    conditions: [],          // [{id, attrs}]
    medText: "",
    answers: {},             // {condId: {key: value}}
    showDeclines: false,
  };

  // ------------------------------------------------------------ inputs
  function heightIn() { return Number(state.feet) * 12 + Number(state.inches); }
  function medList() {
    return state.medText.split("\n").map((s) => s.trim()).filter(Boolean);
  }

  function currentInput() {
    const conditions = state.conditions.map((c) => ({
      name: c.id,
      attrs: Object.assign({}, c.attrs, state.answers[c.id] || {}),
    }));
    return {
      age: Number(state.age),
      gender: state.gender,
      tobacco: state.tobacco,
      state: state.state ? state.state.toUpperCase() : null,
      height_in: heightIn(),
      weight_lb: Number(state.weight),
      face_amount: Number(state.mode === "face" ? state.face : 10000),
      conditions,
      medications: medList(),
    };
  }

  // Apply answers given for medication-inferred conditions, which are not in
  // state.conditions and so cannot carry their attributes through the input.
  function applyInferredAnswers(applicant) {
    for (const entry of applicant.conditions) {
      const answers = state.answers[entry.id];
      if (answers) Object.assign(entry.attrs, answers);
    }
  }

  function faceForBudget(applicant, carrier, product, tierKey, budget) {
    const unit = FEX.quote(engine.bundle, Object.assign({}, applicant, { face_amount: 1000 }), carrier, product, tierKey);
    const feeMonthly = unit.policy_fee_annual * (unit.monthly / unit.annual);
    const per1000 = unit.monthly - feeMonthly;
    if (per1000 <= 0) return product.face_min;
    const raw = (1000 * (budget - feeMonthly)) / per1000;
    return FEX.clampFace(product, Math.round(raw / 250) * 250, applicant.age);
  }

  // ----------------------------------------------------------- rendering
  function renderApplicantPanel() {
    const bmi = Math.round((703 * state.weight) / Math.pow(heightIn(), 2) * 10) / 10;
    $("#bmi").innerHTML = `BMI <b>${bmi}</b> &middot; ${state.feet}'${state.inches}" &middot; ${state.weight} lb`;
  }

  function renderMedFeedback() {
    const box = $("#med-feedback");
    box.innerHTML = "";
    const parsed = engine.catalog.parseMedications(medList());
    for (const med of parsed.medications) {
      if (!med.matched) {
        box.appendChild(el("span", { class: "chip unknown" }, [
          el("span", { text: med.raw }),
          el("span", { class: "imp", text: "not recognised" }),
        ]));
        continue;
      }
      const drug = engine.catalog.drugByIngredient.get(med.ingredient);
      const implies = (drug.implies || []).map((c) => engine.catalog.label(c)).join(", ");
      const tail =
        drug.confidence === "low" ? "ask why" : implies || (drug.flags || []).join(", ") || "no rating";
      box.appendChild(el("span", { class: "chip" }, [
        el("span", { text: med.brand || med.ingredient }),
        el("span", { class: "imp", text: "→ " + tail }),
      ]));
    }
  }

  function renderConditionChips() {
    const box = $("#cond-chips");
    box.innerHTML = "";
    for (const c of state.conditions) {
      box.appendChild(el("span", { class: "chip" }, [
        el("span", { text: engine.catalog.label(c.id) }),
        el("button", {
          type: "button", "aria-label": "Remove", text: "×",
          onclick: () => {
            state.conditions = state.conditions.filter((x) => x.id !== c.id);
            delete state.answers[c.id];
            refresh();
          },
        }),
      ]));
    }
  }

  function renderFollowups(applicant) {
    const box = $("#followups");
    box.innerHTML = "";
    const seen = [];
    for (const entry of applicant.conditions) {
      const def = engine.catalog.conditions.get(entry.id);
      if (!def || !(def.followups || []).length) continue;
      if (seen.includes(entry.id)) continue;
      seen.push(entry.id);

      const card = el("div", { class: "followup" }, [
        el("h3", { text: def.label }),
        entry.inferred_from ? el("div", { class: "src", text: "from " + entry.inferred_from }) : null,
      ]);
      for (const f of def.followups) {
        const answered = (state.answers[entry.id] || {})[f.key];
        const q = el("div", { class: "q" }, [el("span", { text: f.question })]);
        if ((f.type || "bool") === "bool") {
          const seg = el("div", { class: "seg" });
          for (const [label, value] of [["Unknown", undefined], ["Yes", true], ["No", false]]) {
            seg.appendChild(el("button", {
              type: "button",
              // "Unknown" is the starting state, not an answer, so it is styled
              // quietly - only a real Yes/No takes the accent.
              class: value === undefined ? "neutral" : null,
              "aria-pressed": String(answered === value),
              text: label,
              onclick: () => setAnswer(entry.id, f.key, value),
            }));
          }
          q.appendChild(seg);
        } else {
          q.appendChild(el("input", {
            type: "number", value: answered === undefined ? "" : answered,
            placeholder: "leave blank if unknown",
            oninput: (e) => {
              const raw = e.target.value.trim();
              setAnswer(entry.id, f.key, raw === "" ? undefined : Number(raw), true);
            },
          }));
        }
        card.appendChild(q);
      }
      box.appendChild(card);
    }
    $("#followups-wrap").style.display = seen.length ? "" : "none";
  }

  function setAnswer(condId, key, value, quiet) {
    const bag = state.answers[condId] || (state.answers[condId] = {});
    if (value === undefined) delete bag[key];
    else bag[key] = value;
    refresh(quiet);
  }

  function findingLine(f) {
    const mark = f.pending ? "❓" : "•";
    return el("li", {}, [
      document.createTextNode(mark + " "),
      el("span", { class: "badge " + f.outcome, text: FEX.TIERS[f.outcome].label }),
      document.createTextNode(" " + f.reason),
    ]);
  }

  function renderResult(r) {
    const summary = el("summary", {}, [
      el("div", {}, [
        el("div", { class: "name", text: r.carrier_name }),
        el("div", { class: "prod" }, [
          document.createTextNode(r.product_name),
          r.am_best ? el("span", { class: "rating", text: "  \u00b7  AM Best " + r.am_best }) : null,
        ]),
      ]),
      el("span", { class: "badge " + r.tier, text: FEX.TIERS[r.tier].label }),
      r.quote
        ? el("div", { class: "price" }, [
            el("b", { text: money(r.quote.monthly) + (r.quote.illustrative ? "*" : "") }),
            el("small", { text: money0(r.quote.face_amount) + " face / mo" }),
          ])
        : el("div", { class: "price" }),
      el("div", { class: "note", text: !r.certain
          ? "Could reach " + FEX.TIERS[r.best_case_tier].label + " once the open questions are answered"
          : (r.blocking_reasons[0] || "") }),
    ]);

    const body = el("div", { class: "body" });
    if (r.benefit_schedule) body.appendChild(el("p", { class: "cite", text: r.benefit_schedule }));
    const list = el("ul");
    if (r.findings.length) r.findings.forEach((f) => list.appendChild(findingLine(f)));
    else list.appendChild(el("li", { text: "• Nothing on the application downgrades this case." }));
    body.appendChild(list);
    for (const note of r.notes) body.appendChild(el("p", { class: "cite", text: note }));

    return el("details", { class: "result t-" + r.tier + (r.eligible ? "" : " declined") }, [summary, body]);
  }

  function render(report) {
    const offers = report.results.filter((r) => r.eligible);
    const declines = report.results.filter((r) => !r.eligible);
    const dayOne = offers.filter((r) => r.tier === "preferred" || r.tier === "level");

    const metrics = $("#metrics");
    metrics.innerHTML = "";
    for (const [k, v] of [
      ["Carriers checked", report.results.length],
      ["Offers", offers.length],
      ["Day-one benefit", dayOne.length],
      ["Open questions", report.open_questions.length],
    ]) {
      metrics.appendChild(el("div", { class: "metric" }, [
        el("div", { class: "v", text: String(v) }),
        el("div", { class: "k", text: k }),
      ]));
    }

    const warnBox = $("#warnings");
    warnBox.innerHTML = "";
    for (const w of report.warnings) warnBox.appendChild(el("div", { class: "warn", text: w }));

    const ask = $("#ask");
    ask.innerHTML = "";
    if (report.open_questions.length) {
      const ul = el("ul");
      report.open_questions.forEach((q) => ul.appendChild(el("li", { text: q })));
      ask.appendChild(el("section", { class: "card ask" }, [
        el("div", { class: "hd" }, [
          el("h2", { text: "Ask the client" }),
          el("span", { class: "n lbl", text: report.open_questions.length + " open" }),
        ]),
        el("div", { class: "bd" }, [
          el("p", { class: "hint", text: "Each of these changes at least one carrier's answer." }),
          ul,
        ]),
      ]));
    }

    const out = $("#results");
    out.innerHTML = "";
    if (!offers.length) {
      out.appendChild(el("div", { class: "empty", text: "No carrier in this file issues on the case as entered." }));
    }
    offers.forEach((r) => out.appendChild(renderResult(r)));

    const decBox = $("#declines");
    decBox.innerHTML = "";
    if (declines.length) {
      decBox.appendChild(el("div", { class: "section-head" }, [
        el("h2", { text: "Declines" }),
        el("span", { class: "n", text: declines.length + " carriers" }),
      ]));
      if (state.showDeclines) declines.forEach((r) => decBox.appendChild(renderResult(r)));
    }
    $("#toggle-declines").textContent =
      (state.showDeclines ? "Hide" : "Show") + " declines (" + declines.length + ")";
    $("#toggle-declines").style.display = declines.length ? "" : "none";
  }

  // -------------------------------------------------------------- CSV
  function downloadCsv(report) {
    const rows = [["Carrier", "Product", "Tier", "Best case", "Monthly", "Face", "AM Best", "Reasons"]];
    for (const r of report.results) {
      rows.push([
        r.carrier_name, r.product_name, FEX.TIERS[r.tier].label,
        FEX.TIERS[r.best_case_tier].label,
        r.quote ? r.quote.monthly.toFixed(2) : "",
        r.quote ? String(Math.round(r.quote.face_amount)) : "",
        r.am_best || "", r.blocking_reasons.join("; "),
      ]);
    }
    const csv = rows
      .map((row) => row.map((c) => '"' + String(c).replace(/"/g, '""') + '"').join(","))
      .join("\n");
    navigator.clipboard.writeText(csv).then(
      () => flash("Comparison copied to the clipboard as CSV."),
      () => flash("Could not reach the clipboard.")
    );
  }

  function flash(message) {
    const box = $("#warnings");
    const node = el("div", { class: "warn", text: message });
    box.appendChild(node);
    setTimeout(() => node.remove(), 4000);
  }

  // ----------------------------------------------------------- refresh
  let lastReport = null;
  function refresh(quiet) {
    const built = engine.buildApplicant(currentInput());
    applyInferredAnswers(built.applicant);
    const report = engine.evaluate(built.applicant, built.questions);
    report.unmatched_medications = built.unmatchedMedications;

    if (state.mode === "budget") {
      const byId = new Map(engine.carriers.map((c) => [c.id, c]));
      for (const r of report.results) {
        if (!r.eligible) continue;
        const carrier = byId.get(r.carrier_id);
        const product = carrier && carrier.products.find((p) => p.id === r.product_id);
        if (!product) continue;
        const face = faceForBudget(built.applicant, carrier, product, r.tier, Number(state.budget));
        r.quote = FEX.quote(
          engine.bundle,
          Object.assign({}, built.applicant, { face_amount: face }),
          carrier, product, r.tier
        );
      }
    }

    lastReport = report;
    renderApplicantPanel();
    renderMedFeedback();
    renderConditionChips();
    if (!quiet) renderFollowups(built.applicant);
    render(report);
  }

  // ------------------------------------------------------------- wiring
  function bind(id, key, transform) {
    const node = $(id);
    node.value = state[key];
    node.addEventListener("input", () => {
      state[key] = transform ? transform(node.value) : node.value;
      refresh();
    });
  }

  function setupTypeahead() {
    const input = $("#cond-search");
    const box = $("#cond-suggest");
    const all = Array.from(engine.catalog.conditions.values());

    function close() { box.innerHTML = ""; box.style.display = "none"; }

    function add(id) {
      if (!state.conditions.some((c) => c.id === id)) state.conditions.push({ id, attrs: {} });
      input.value = "";
      close();
      refresh();
    }

    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      if (q.length < 2) return close();
      const hits = all
        .filter((c) =>
          c.label.toLowerCase().includes(q) ||
          (c.aliases || []).some((a) => a.toLowerCase().includes(q)))
        .filter((c) => !state.conditions.some((x) => x.id === c.id))
        .slice(0, 10);
      box.innerHTML = "";
      if (!hits.length) {
        const guess = engine.catalog.matchCondition(q);
        if (guess && !state.conditions.some((x) => x.id === guess)) {
          hits.push(engine.catalog.conditions.get(guess));
        }
      }
      if (!hits.length) return close();
      for (const c of hits) {
        box.appendChild(el("div", {
          onclick: () => add(c.id),
          text: c.label,
        }, [el("span", { class: "cat", text: c.category })]));
      }
      box.style.display = "";
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = box.querySelector("div");
        if (first) first.click();
      } else if (e.key === "Escape") close();
    });
    document.addEventListener("click", (e) => {
      if (!box.contains(e.target) && e.target !== input) close();
    });
  }

  function boot(bundle) {
    engine = new FEX.Engine(bundle);

    bind("#age", "age", Number);
    bind("#state", "state", (v) => v.toUpperCase().slice(0, 2));
    bind("#feet", "feet", Number);
    bind("#inches", "inches", Number);
    bind("#weight", "weight", Number);
    bind("#face", "face", Number);
    bind("#budget", "budget", Number);
    bind("#meds", "medText");

    $("#gender").querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        state.gender = b.dataset.value;
        $("#gender").querySelectorAll("button").forEach((x) =>
          x.setAttribute("aria-pressed", String(x === b)));
        refresh();
      });
    });
    $("#mode").querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        state.mode = b.dataset.value;
        $("#mode").querySelectorAll("button").forEach((x) =>
          x.setAttribute("aria-pressed", String(x === b)));
        $("#face-wrap").style.display = state.mode === "face" ? "" : "none";
        $("#budget-wrap").style.display = state.mode === "budget" ? "" : "none";
        refresh();
      });
    });
    $("#tobacco").addEventListener("change", (e) => {
      state.tobacco = e.target.checked;
      refresh();
    });
    $("#toggle-declines").addEventListener("click", () => {
      state.showDeclines = !state.showDeclines;
      refresh();
    });
    $("#copy-csv").addEventListener("click", () => lastReport && downloadCsv(lastReport));
    $("#reset").addEventListener("click", () => {
      state.conditions = [];
      state.answers = {};
      state.medText = "";
      $("#meds").value = "";
      refresh();
    });

    setupTypeahead();
    $("#generated").textContent = "Rulebase built " + (bundle.generated || "").slice(0, 10);
    refresh();
  }

  window.FEX_BOOT = boot;
})();
