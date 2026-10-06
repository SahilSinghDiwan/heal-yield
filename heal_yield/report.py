"""Rendering `report.md` from `run.json`.

JSON is the source of truth; Markdown is rendered from it and never
hand-written. A hand-written report would break the chain that lets `verify`
prove the published number and the stored artifact are the same number.
"""

from __future__ import annotations

from typing import Dict, Optional

from . import dispositions as disp


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else "%.1f%%" % value


def _usd(value: Optional[float]) -> str:
    return "n/a" if value is None else "$%.4f" % value


def render(run: Dict, metrics: Dict, runs_sha: Optional[str] = None) -> str:
    cfg = run["config"]
    repo = run["repo"]
    n = metrics["n"]
    lines = []
    a = lines.append

    a("# heal-yield run: %s @ %s" % (repo["package"], repo["sha"][:7]))
    a("")
    if run["status"] != "complete":
        a("> **status: %s** -- %s" % (run["status"], run.get("truncation_reason") or ""))
        a("> Every percentage below is computed over the N that was reached.")
        a("> A run that is not `complete` can never be one of the 5 headline repetitions.")
        a("")
    a("Generated %s - model `%s` at temperature %s, k = %s."
      % (run["created_utc"], cfg["model"], cfg["temperature"], cfg["k"]))
    if runs_sha:
        a("Recomputed from `heal-yield-runs` commit `%s`." % runs_sha)
    a("")
    a("All definitions are normative in [METRICS.md](../../METRICS.md). Every number "
      "here is recomputed by `heal-yield verify` from the artifacts under `raw/`.")
    a("")

    a("## Headline")
    a("")
    m5 = metrics["metric_5_heal_yield"]
    if m5["pct"] is None:
        a("**Heal yield: suppressed.** %s (numerator %d, denominator %d)"
          % (m5["suppressed_because"], m5["numerator"], m5["denominator"]))
    else:
        a("**Heal yield: %s** (%d of %d candidates that failed on first pass were rescued)"
          % (_pct(m5["pct"]), m5["numerator"], m5["denominator"]))
    a("")

    a("| Metric | Value | Numerator / denominator |")
    a("|---|---|---|")
    m2 = metrics["metric_2_first_pass_build_rate"]
    m3 = metrics["metric_3_first_pass_green_rate"]
    m4 = metrics["metric_4_post_heal_green_rate"]
    a("| 1. Generated candidates (N) | %d | - |" % n)
    a("| 2. First-pass build rate | %s | %d / %d |"
      % (_pct(m2["pct"]), m2["numerator"], m2["denominator"]))
    a("| 3. First-pass green rate | %s | %d / %d |"
      % (_pct(m3["pct"]), m3["numerator"], m3["denominator"]))
    a("| 4. Post-heal green rate (k=%s) | %s | %d / %d |"
      % (m4["k"], _pct(m4["pct"]), m4["numerator"], m4["denominator"]))
    a("| 5. Heal yield | %s | %d / %d |" % (_pct(m5["pct"]), m5["numerator"], m5["denominator"]))
    a("")

    a("### Per-round attribution (metric 4)")
    a("")
    a("| Round | Candidates that passed |")
    a("|---|---|")
    for r in sorted(m4["per_round_attribution"], key=lambda x: int(x)):
        a("| %s | %d |" % (r, m4["per_round_attribution"][r]))
    if not m4["per_round_attribution"]:
        a("| - | 0 |")
    a("")

    m6 = metrics["metric_6_coverage"]
    a("## 6. Coverage")
    a("")
    a("Line and branch are reported separately; branch coverage does not subsume line "
      "coverage in Python. Baseline is measured with the target modules' existing tests "
      "removed. **This is not a claim to improve on the maintainers' suites.**")
    a("")
    a("| | Baseline | Final | Delta |")
    a("|---|---|---|---|")
    base = m6["baseline"] or {}
    fin = m6["final"] or {}
    a("| Line | %s | %s | %s |" % (_pct(base.get("line_pct")), _pct(fin.get("line_pct")),
                                   _pct(m6["delta_line_pct"])))
    a("| Branch | %s | %s | %s |" % (_pct(base.get("branch_pct")), _pct(fin.get("branch_pct")),
                                     _pct(m6["delta_branch_pct"])))
    a("| Line+branch | %s | %s | %s |" % (_pct(base.get("line_and_branch_pct")),
                                          _pct(fin.get("line_and_branch_pct")),
                                          _pct(m6["delta_line_and_branch_pct"])))
    a("")
    a("Net new covered lines: **%d**" % m6["net_new_covered_lines"])
    a("")

    a("## 7. Mutation score")
    a("")
    a("*%s*" % metrics["metric_7_mutation_score"])
    a("")

    m8 = metrics["metric_8_cost"]
    a("## 8. Cost")
    a("")
    a("| | Value |")
    a("|---|---|")
    a("| Total | %s |" % _usd(m8["total_usd"]))
    a("| Prompt tokens | %d |" % m8["prompt_tokens"])
    a("| Completion tokens | %d |" % m8["completion_tokens"])
    a("| Per-run ceiling (operator choice) | %s |" % _usd(m8["ceiling_usd"]))
    a("| Ceiling tripped | %s |" % ("YES" if m8["ceiling_tripped"] else "no"))
    a("| $ / surviving test | %s |" % _usd(m8["usd_per_surviving_test"]))
    a("| $ / net-new covered line | %s |" % _usd(m8["usd_per_net_new_covered_line"]))
    a("")
    a("### Per-repair-round cost attribution")
    a("")
    a("| Round | USD | Prompt tokens | Completion tokens | Calls |")
    a("|---|---|---|---|---|")
    for r in sorted(m8["by_round"], key=lambda x: int(x)):
        b = m8["by_round"][r]
        a("| %s | %s | %d | %d | %d |"
          % (r, _usd(b["usd"]), b["prompt_tokens"], b["completion_tokens"], b["calls"]))
    if not m8["by_round"]:
        a("| - | n/a | 0 | 0 | 0 |")
    a("")

    m9 = metrics["metric_9_dispositions"]
    a("## 9. Disposition ledger")
    a("")
    a("Every candidate has exactly one terminal disposition.")
    a("")
    a("| Disposition | Count | Meaning |")
    a("|---|---|---|")
    for d in disp.DISPOSITIONS:
        a("| `%s` | %d | %s |" % (d, m9["ledger"][d], disp.MEANINGS[d]))
    a("")
    a("**%s**" % m9["invariant_line"])
    a("")
    a("### Never-healed")
    a("")
    a("The same enum, filtered to candidates that entered at least one repair round and "
      "did not survive.")
    a("")
    a("| Disposition | Count |")
    a("|---|---|")
    for d in disp.NEVER_HEALED:
        a("| `%s` | %d |" % (d, m9["never_healed"][d]))
    a("| **total** | **%d** |" % m9["never_healed_total"])
    a("")

    m10 = metrics["metric_10_assertion_weakening_guard"]
    a("## 10. Assertion-weakening guard")
    a("")
    a("Rejected repairs: **%d** (%s)" % (m10["rejected"], m10["note"]))
    a("")

    a("## Provenance")
    a("")
    a("- Modules under test: %s" % ", ".join("`%s`" % m for m in cfg["modules"]))
    a("- Generator: `%s`" % cfg["generator_command"])
    a("- Per-candidate time limit: %ss" % cfg["per_candidate_timeout_s"])
    a("- Flake gate: %d of %d consecutive executions, applied once to the survivor pool"
      % (cfg["flake_gate_executions"], cfg["flake_gate_executions"]))
    a("- Manifest version: %s" % run["manifest_version"])
    a("- Headline-eligible: %s" % ("yes" if metrics["headline_eligible"] else "NO"))
    a("")
    a("> The per-run USD ceiling and the per-candidate time limit above are **this "
      "operator's choices for this run, not heal-yield recommendations**. Both are "
      "declared-as-unset in METRICS.md: no measured value exists for either, and the "
      "tool has no default for either.")
    a("")
    a("Reproduce the verification with no API key, no Docker and no re-execution:")
    a("")
    a("```")
    a("heal-yield verify --run <this directory>")
    a("```")
    a("")
    return "\n".join(lines)
