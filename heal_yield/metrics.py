"""Metrics 1-6, 8, 9, 10, computed from `run.json` alone.

This module never executes pytest, never calls a model and never touches the
target checkout. It is a pure function from the stored record to the published
numbers, which is exactly what makes `heal-yield verify` possible: `run` and
`verify` call the same function, and a disagreement between them is a bug in
one of them, not a difference of method.

Metric 7 (mutation score) is deferred to increment 2 and declared as deferred.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from . import dispositions as disp
from .cost import CostLedger, derived

#: Below this denominator the heal-yield ratio is unstable and is suppressed
#: entirely rather than printed with a caveat (METRICS.md metric 5).
HEAL_YIELD_MIN_DENOMINATOR = 10

DEFERRED = {
    "mutation_score": "deferred to increment 2; see METRICS.md metric 7",
}


def _pct(numerator: int, denominator: int) -> Optional[float]:
    if not denominator:
        return None
    return 100.0 * numerator / denominator


def compute(run: Dict) -> Dict:
    """Compute every published number from a `run.json` payload."""
    candidates: List[Dict] = run["candidates"]
    n = len(candidates)

    ledger_counts = disp.tally(c["disposition"] for c in candidates)
    invariant_ok = True
    invariant_detail = ""
    try:
        disp.check_sum(ledger_counts, n)
    except disp.InvariantViolation as exc:
        invariant_ok = False
        invariant_detail = str(exc)

    builds_0 = sum(1 for c in candidates if c.get("built_round_0"))
    passed = [c for c in candidates if c["disposition"] == "passed"]
    passes_0 = [c for c in passed if c.get("healed_at_round") == 0]
    passes_k = passed

    per_round: Dict[str, int] = {}
    for c in passes_k:
        key = str(c.get("healed_at_round"))
        per_round[key] = per_round.get(key, 0) + 1

    heal_denominator = n - len(passes_0)
    heal_numerator = len(passes_k) - len(passes_0)
    heal_yield = None
    heal_suppressed_reason = None
    if heal_denominator < HEAL_YIELD_MIN_DENOMINATOR:
        heal_suppressed_reason = (
            "denominator %d is below the minimum of %d; the ratio is unstable"
            % (heal_denominator, HEAL_YIELD_MIN_DENOMINATOR)
        )
    else:
        heal_yield = 100.0 * heal_numerator / heal_denominator

    coverage = run.get("coverage", {}) or {}
    net_new_lines = int(coverage.get("net_new_covered_lines") or 0)

    ledger = CostLedger.from_dict(run["cost"])
    cost_derived = derived(ledger, surviving_tests=len(passes_k), net_new_lines=net_new_lines)

    never_healed = {d: ledger_counts.get(d, 0) for d in disp.NEVER_HEALED}

    return {
        "n": n,
        "metric_1_generated_candidates": n,
        "metric_2_first_pass_build_rate": {
            "numerator": builds_0,
            "denominator": n,
            "pct": _pct(builds_0, n),
        },
        "metric_3_first_pass_green_rate": {
            "numerator": len(passes_0),
            "denominator": n,
            "pct": _pct(len(passes_0), n),
        },
        "metric_4_post_heal_green_rate": {
            "numerator": len(passes_k),
            "denominator": n,
            "pct": _pct(len(passes_k), n),
            "k": run["config"]["k"],
            "per_round_attribution": per_round,
        },
        "metric_5_heal_yield": {
            "numerator": heal_numerator,
            "denominator": heal_denominator,
            "pct": heal_yield,
            "suppressed_because": heal_suppressed_reason,
        },
        "metric_6_coverage": {
            "baseline": coverage.get("baseline"),
            "final": coverage.get("final"),
            "delta_line_pct": _delta(coverage, "line_pct"),
            "delta_branch_pct": _delta(coverage, "branch_pct"),
            "delta_line_and_branch_pct": _delta(coverage, "line_and_branch_pct"),
            "net_new_covered_lines": net_new_lines,
        },
        "metric_7_mutation_score": DEFERRED["mutation_score"],
        "metric_8_cost": {
            "cost_basis": "priced" if ledger.metered else "unmetered",
            "total_usd": round(ledger.spent, 6) if ledger.metered else None,
            "prompt_tokens": sum(r.prompt_tokens for r in ledger.records),
            "completion_tokens": sum(r.completion_tokens for r in ledger.records),
            "by_round": ledger.by_round(),
            "ceiling_usd": ledger.ceiling_usd,
            "ceiling_tripped": ledger.tripped,
            "usd_per_surviving_test": (
                cost_derived["usd_per_surviving_test"] if ledger.metered else None),
            "usd_per_net_new_covered_line": (
                cost_derived["usd_per_net_new_covered_line"] if ledger.metered else None),
        },
        "metric_9_dispositions": {
            "ledger": ledger_counts,
            "never_healed": never_healed,
            "never_healed_total": sum(never_healed.values()),
            "invariant_ok": invariant_ok,
            "invariant_line": disp.assertion_line(ledger_counts, n),
            "invariant_detail": invariant_detail,
        },
        "metric_10_assertion_weakening_guard": {
            "rejected": ledger_counts.get("weakened-rejected", 0),
            "note": "reported even when zero",
        },
        "status": run.get("status", "complete"),
        "fixture": bool(run.get("fixture")),
        # A fixture run describes the harness, not a model. Barring it here is
        # mechanical rather than a habit someone has to remember.
        "headline_eligible": (
            run.get("status", "complete") == "complete" and not run.get("fixture")
        ),
    }


def _delta(coverage: Dict, key: str) -> Optional[float]:
    baseline = (coverage.get("baseline") or {}).get(key)
    final = (coverage.get("final") or {}).get(key)
    if baseline is None or final is None:
        return None
    return final - baseline
