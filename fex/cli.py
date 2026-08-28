"""Command line interface.

    fex quote --age 68 --female --height 5-6 --weight 190 --face 12000 \
              --med "eliquis 5mg" --med metformin --med lantus \
              --condition "diabetes:insulin=yes,age_at_diagnosis=52"

    fex carriers
    fex lookup eliquis
    fex rates template mutual_of_omaha
    fex serve
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional

from .engine import Engine
from .quoting import rate_template
from .tiers import Tier

TIER_COLOR = {
    Tier.PREFERRED: "\033[92m",
    Tier.LEVEL: "\033[92m",
    Tier.GRADED: "\033[93m",
    Tier.MODIFIED: "\033[33m",
    Tier.GI: "\033[94m",
    Tier.DECLINE: "\033[91m",
}
RESET = "\033[0m"


def parse_height(text: str) -> float:
    """Accept 5-6, 5'6", 5ft6, or a plain number of inches."""
    text = text.strip().lower().replace('"', "").replace("ft", "-").replace("'", "-")
    match = re.match(r"^(\d+)\s*-\s*(\d+)$", text)
    if match:
        return int(match.group(1)) * 12 + int(match.group(2))
    return float(text)


def parse_condition(text: str) -> Dict[str, Any]:
    """``"diabetes:insulin=yes,age_at_diagnosis=52"`` -> a condition dict."""
    name, _, rest = text.partition(":")
    attrs: Dict[str, Any] = {}
    for pair in filter(None, (p.strip() for p in rest.split(","))):
        key, _, raw = pair.partition("=")
        key, raw = key.strip(), raw.strip()
        low = raw.lower()
        if low in ("yes", "true", "y"):
            attrs[key] = True
        elif low in ("no", "false", "n"):
            attrs[key] = False
        else:
            try:
                attrs[key] = int(raw) if raw.isdigit() else float(raw)
            except ValueError:
                attrs[key] = raw
    return {"name": name.strip(), "attrs": attrs}


def cmd_quote(args: argparse.Namespace) -> int:
    engine = Engine.load()
    report = engine.run(
        age=args.age,
        gender="female" if args.female else "male",
        tobacco=args.tobacco,
        state=args.state,
        height_in=parse_height(args.height) if args.height else None,
        weight_lb=float(args.weight) if args.weight else None,
        face_amount=float(args.face),
        conditions=[parse_condition(c) for c in (args.condition or [])],
        medications=args.med or [],
    )

    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2)
        print()
        return 0

    applicant = report.applicant
    bmi = f"BMI {applicant.bmi}" if applicant.bmi else "build unknown"
    print(
        f"\n{applicant.age} {applicant.gender}, "
        f"{'tobacco' if applicant.tobacco else 'non-tobacco'}, {bmi}, "
        f"${applicant.face_amount:,.0f} face"
    )
    if applicant.conditions:
        print("Conditions: " + ", ".join(
            f"{c.raw or c.id}" + (" [med]" if c.inferred_from else "")
            for c in applicant.conditions
        ))
    print()

    offers = [r for r in report.results if r.eligible]
    declines = [r for r in report.results if not r.eligible]

    print(f"{'CARRIER':<32}{'TIER':<12}{'MONTHLY':>10}  PRODUCT")
    print("-" * 100)
    for result in offers:
        color = TIER_COLOR[result.tier] if sys.stdout.isatty() else ""
        reset = RESET if sys.stdout.isatty() else ""
        premium = f"${result.quote.monthly:,.2f}" if result.quote else "-"
        flag = "" if result.certain else f"  (could be {result.best_case_tier.key})"
        print(
            f"{result.carrier_name[:31]:<32}{color}{result.tier.key:<12}{reset}"
            f"{premium:>10}  {result.product_name[:34]}{flag}"
        )

    if declines and not args.hide_declines:
        print(f"\nDeclines ({len(declines)}):")
        for result in declines:
            reasons = "; ".join(result.blocking_reasons[:2]) or "no product available"
            print(f"  {result.carrier_name[:31]:<32}{reasons[:60]}")

    if report.open_questions:
        print(f"\nAsk the client ({len(report.open_questions)}):")
        for q in report.open_questions:
            print(f"  - {q}")

    for warning in report.warnings:
        print(f"\n! {warning}")

    print(
        "\nPremiums come from an illustrative model unless a carrier rate table has "
        "been loaded. Guidelines are unverified defaults - confirm against each "
        "carrier's current field underwriting guide."
    )
    return 0


def cmd_carriers(args: argparse.Namespace) -> int:
    engine = Engine.load()
    for carrier in sorted(engine.carriers, key=lambda c: c.name):
        tiers = ", ".join(sorted({p.tier.key for p in carrier.products}, key=len))
        mark = "verified" if carrier.verified else "unverified"
        print(f"{carrier.name[:38]:<40}{carrier.am_best or '-':<6}{tiers:<32}{mark}")
        if args.verbose:
            for product in carrier.products:
                print(
                    f"    {product.name[:44]:<46}{product.tier.key:<10}"
                    f"ages {product.issue_age_min}-{product.issue_age_max}  "
                    f"${product.face_min:,.0f}-${product.face_max:,.0f}"
                )
    print(f"\n{len(engine.carriers)} carriers")
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    engine = Engine.load()
    catalog = engine.catalog
    term = " ".join(args.term)

    drug = catalog.match_drug(term)
    if drug:
        print(f"\nMedication: {drug.ingredient}")
        if drug.brands:
            print(f"  Brands:     {', '.join(drug.brands)}")
        print(f"  Confidence: {drug.confidence}")
        if drug.implies:
            print("  Implies:    " + ", ".join(catalog.label(c) for c in drug.implies))
        if drug.flags:
            print(f"  Flags:      {', '.join(drug.flags)}")
        if drug.note:
            print(f"  Note:       {drug.note}")

    cond_id = catalog.match_condition(term)
    if cond_id:
        cond = catalog.conditions[cond_id]
        print(f"\nCondition: {cond.label}  ({cond.id})")
        print(f"  Category: {cond.category}")
        if cond.aliases:
            print(f"  Aliases:  {', '.join(cond.aliases[:12])}")
        for f in cond.followups:
            print(f"  Ask:      {f['question']}  [{f['key']}: {f.get('type','bool')}]")
        hits = [
            c.name for c in engine.carriers
            for r in c.rules
            if cond_id in r.conditions or cond_id in r.conditions_all
        ]
        if hits:
            print(f"  Referenced by {len(set(hits))} carriers' rules")

    if not drug and not cond_id:
        print(f"No match for {term!r}")
        return 1
    print()
    return 0


def cmd_rates(args: argparse.Namespace) -> int:
    if args.action == "template":
        print(rate_template(args.carrier))
        return 0
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(host=args.host, port=args.port)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fex", description="Final expense whole life eligibility and quoting"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    q = sub.add_parser("quote", help="run a case against every carrier")
    q.add_argument("--age", type=int, required=True)
    q.add_argument("--female", action="store_true")
    q.add_argument("--tobacco", action="store_true")
    q.add_argument("--state")
    q.add_argument("--height", help="5-6, 5'6\" or inches")
    q.add_argument("--weight", help="pounds")
    q.add_argument("--face", default=10000)
    q.add_argument("--med", action="append", help="repeatable")
    q.add_argument(
        "--condition",
        action="append",
        help="repeatable, e.g. \"diabetes:insulin=yes,age_at_diagnosis=52\"",
    )
    q.add_argument("--json", action="store_true")
    q.add_argument("--hide-declines", action="store_true")
    q.set_defaults(func=cmd_quote)

    c = sub.add_parser("carriers", help="list carriers and products")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_carriers)

    l = sub.add_parser("lookup", help="look up a drug or condition")
    l.add_argument("term", nargs="+")
    l.set_defaults(func=cmd_lookup)

    r = sub.add_parser("rates", help="work with carrier rate tables")
    r.add_argument("action", choices=["template"])
    r.add_argument("carrier")
    r.set_defaults(func=cmd_rates)

    s = sub.add_parser("serve", help="run the local web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
