"""`heal-yield verify` -- recompute every published number from stored artifacts.

No re-run, no API key, no Docker, no trust required. This module is allowed to
read files under a run directory and nothing else. It never imports the target
package, never starts pytest and never contacts a model. If it ever needed to,
the tool's central claim would be false.

Four independent checks, reported separately because they fail for different
reasons:

  1. **integrity** -- do the artifacts still hash to what `SHA256SUMS` says?
  2. **ledger** -- does every candidate carry exactly one enum disposition,
     and do the dispositions sum to N?
  3. **evidence** -- does every candidate's recorded outcome have a raw file
     behind it, and does re-reading that file give the same outcome?
  4. **arithmetic** -- does recomputing the metrics from `run.json` reproduce
     the metrics block that was published?
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from . import coverage_delta
from . import dispositions as disp
from . import metrics as metrics_mod
from .pytest_harness import read_execution
from .runstore import RunStore


class Check(object):
    def __init__(self, name: str, ok: bool, detail: str = "", problems: Optional[List[str]] = None):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.problems = problems or []

    def to_dict(self) -> Dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "problems": self.problems[:50], "problem_count": len(self.problems)}


def verify_run(run_dir: str) -> Dict:
    store = RunStore(run_dir)
    run = store.read_json("run.json")
    checks = [
        _check_integrity(store),
        _check_ledger(run),
        _check_evidence(store, run),
        _check_disposition_evidence(run),
        _check_arithmetic(run),
        _check_coverage(store, run),
        _check_headline_eligibility(run),
    ]
    return {
        "run_dir": os.path.abspath(run_dir),
        "status": run.get("status"),
        "n": len(run["candidates"]),
        "ok": all(c.ok for c in checks),
        "checks": [c.to_dict() for c in checks],
        "recomputed_metrics": metrics_mod.compute(run),
    }


def _check_integrity(store: RunStore) -> Check:
    result = store.verify_checksums()
    problems = (
        ["changed: " + p for p in result["changed"]]
        + ["missing: " + p for p in result["missing"]]
        + ["unlisted: " + p for p in result["unlisted"]]
    )
    if not os.path.exists(store.path("SHA256SUMS")):
        return Check("integrity", False, "no SHA256SUMS in the run directory")
    return Check(
        "integrity",
        not problems,
        "%d raw artifacts hashed" % len(list(store.iter_raw_files())),
        problems,
    )


def _check_ledger(run: Dict) -> Check:
    candidates = run["candidates"]
    n = len(candidates)
    problems = []
    for c in candidates:
        d = c.get("disposition")
        if d is None:
            problems.append("%s has no disposition" % c["id"])
        elif d not in disp.DISPOSITIONS:
            problems.append("%s carries non-enum disposition %r" % (c["id"], d))
    if problems:
        return Check("ledger", False, "N = %d" % n, problems)
    counts = disp.tally(c["disposition"] for c in candidates)
    try:
        disp.check_sum(counts, n)
    except disp.InvariantViolation as exc:
        return Check("ledger", False, str(exc))
    return Check("ledger", True, disp.assertion_line(counts, n))


def _check_evidence(store: RunStore, run: Dict) -> Check:
    """Re-derive each candidate's outcome from the raw pytest output.

    This is the check that makes the rest mean anything: the ledger could sum
    to N perfectly while describing a run that never happened.
    """
    problems: List[str] = []
    checked = 0
    for c in run["candidates"]:
        if c["disposition"] == "no-output":
            continue
        rounds = [r for r in c.get("rounds", []) if r.get("phase") == "run"]
        if not rounds and c["disposition"] not in ("collect-error", "build-failure",
                                                   "weakened-rejected"):
            problems.append("%s has no recorded execution" % c["id"])
            continue
        for r in rounds:
            artifact = r.get("artifact")
            if not artifact:
                problems.append("%s round %s records no artifact" % (c["id"], r.get("round")))
                continue
            prefix = store.path(artifact)[: -len(".txt")]
            if not os.path.exists(prefix + ".txt"):
                problems.append("%s round %s: artifact %s is missing"
                                % (c["id"], r.get("round"), artifact))
                continue
            returncode, timed_out, outcomes = read_execution(prefix)
            checked += 1
            recorded = r.get("outcome")
            if recorded == "timeout":
                if not timed_out:
                    problems.append("%s round %s recorded a timeout the artifact does not show"
                                    % (c["id"], r.get("round")))
                continue
            derived_outcome = _outcome_from(outcomes, c)
            if derived_outcome != recorded:
                problems.append(
                    "%s round %s: recorded %r, artifact says %r"
                    % (c["id"], r.get("round"), recorded, derived_outcome)
                )
    return Check("evidence", not problems, "%d executions re-read from disk" % checked, problems)


def _check_disposition_evidence(run: Dict) -> Check:
    """Every disposition must be entailed by the candidate's own recorded rounds.

    Re-reading the raw output is not enough on its own: a run whose executions
    all match their artifacts could still hand a failing candidate the
    `passed` label. This is the check that closes that gap, and it is why
    `passed` is the most expensive claim in the file to make -- it requires a
    flake-gate record of five consecutive passes.
    """
    gate_n = run["config"].get("flake_gate_executions", 5)
    problems: List[str] = []

    for c in run["candidates"]:
        d = c.get("disposition")
        rounds = c.get("rounds", [])
        runs = [r for r in rounds if r.get("phase") == "run"]
        gates = [r for r in rounds if r.get("phase") == "flake-gate"]
        repairs = [r for r in rounds if r.get("phase") == "repair"]
        collects = [r for r in rounds if r.get("phase") == "collect"]
        covers = [r for r in rounds if r.get("phase") == "coverage"]

        if d == "passed":
            if not gates:
                problems.append("%s is `passed` with no flake-gate record" % c["id"])
            else:
                outcomes = gates[-1].get("outcomes", [])
                if len(outcomes) != gate_n or any(o != "passed" for o in outcomes):
                    problems.append(
                        "%s is `passed` but its flake gate reads %r" % (c["id"], outcomes)
                    )
            if c.get("healed_at_round") is None:
                problems.append("%s is `passed` with no round attributed" % c["id"])
        elif d == "still-flaky":
            if not gates or all(o == "passed" for o in gates[-1].get("outcomes", [])):
                problems.append("%s is `still-flaky` but no gate execution failed" % c["id"])
        elif d == "collect-error":
            if not any(r.get("ok") is False for r in collects):
                problems.append("%s is `collect-error` with no failed collect" % c["id"])
        elif d == "no-progress-abort":
            sigs = c.get("failure_signatures", [])
            if len(sigs) < 2 or sigs[-1] != sigs[-2]:
                problems.append(
                    "%s is `no-progress-abort` without two identical failure signatures"
                    % c["id"]
                )
        elif d == "weakened-rejected":
            if not any((r.get("guard") or {}).get("rejected") for r in repairs):
                problems.append("%s is `weakened-rejected` with no guard rejection" % c["id"])
        elif d == "timeout":
            if not any(r.get("outcome") == "timeout" for r in runs):
                problems.append("%s is `timeout` with no timed-out execution" % c["id"])
        elif d == "no-coverage-increase":
            if not any(r.get("net_new_lines") == 0 for r in covers):
                problems.append(
                    "%s is `no-coverage-increase` but no measurement says zero" % c["id"]
                )
        elif d == "still-failing":
            if runs and runs[-1].get("outcome") == "passed":
                problems.append("%s is `still-failing` but its last execution passed" % c["id"])
        elif d == "no-output":
            if c.get("file"):
                problems.append("%s is `no-output` but names an emitted file" % c["id"])

    return Check(
        "disposition-evidence",
        not problems,
        "%d dispositions entailed by their own rounds" % len(run["candidates"]),
        problems,
    )


def _outcome_from(outcomes: Dict[str, str], candidate: Dict) -> str:
    suffix = candidate["qualname"].replace(".", "::")
    for nodeid, outcome in outcomes.items():
        if nodeid.endswith(suffix):
            return outcome
    return "failed"


def _check_arithmetic(run: Dict) -> Check:
    published = run.get("metrics")
    if not published:
        return Check("arithmetic", False, "run.json carries no published metrics block")
    recomputed = metrics_mod.compute(run)
    problems = []
    for key in sorted(recomputed):
        if published.get(key) != recomputed[key]:
            problems.append("%s: published %r, recomputed %r"
                            % (key, published.get(key), recomputed[key]))
    return Check("arithmetic", not problems,
                 "%d metric blocks recomputed" % len(recomputed), problems)


def _check_coverage(store: RunStore, run: Dict) -> Check:
    """Re-read the stored coverage XML rather than trusting the summary."""
    problems = []
    include = list((run["config"].get("module_files") or {}).values())
    for which in ("baseline", "final"):
        xml = store.coverage_path(which, "xml")
        if not os.path.exists(xml):
            problems.append("%s coverage XML is missing" % which)
            continue
        summary = coverage_delta.summarise_xml(xml, include)
        stored = (run.get("coverage") or {}).get(which) or {}
        for key in ("lines_total", "lines_covered", "branches_total", "branches_covered"):
            if stored.get(key) != summary.get(key):
                problems.append("%s.%s: stored %r, XML says %r"
                                % (which, key, stored.get(key), summary.get(key)))
    return Check("coverage", not problems, "coverage recomputed from stored XML", problems)


def _check_headline_eligibility(run: Dict) -> Check:
    """Does the run's own eligibility flag agree with its status and fixture bit?

    Two things disqualify a run as one of the 5 repetitions behind a headline
    claim, and both are mechanical: a status other than `complete`, and a
    fixture generator. The check is that the stored flag says so -- a run that
    quietly called itself eligible is the failure this exists to catch.
    """
    status = run.get("status", "complete")
    fixture = bool(run.get("fixture"))
    expected = status == "complete" and not fixture
    stored = (run.get("metrics") or {}).get("headline_eligible")
    reasons = []
    if status != "complete":
        reasons.append("status %s" % status)
    if fixture:
        reasons.append("fixture generator: describes the harness, not a model")
    detail = (
        "status complete, real generator; eligible as a repetition"
        if expected
        else "NOT eligible as a headline repetition (%s)" % "; ".join(reasons)
    )
    if stored is not None and bool(stored) != expected:
        return Check("headline-eligibility", False,
                     "run.json claims headline_eligible=%r but %s" % (stored, detail))
    return Check("headline-eligibility", True, detail)


def render_verification(result: Dict) -> str:
    lines = ["heal-yield verify -- %s" % result["run_dir"], ""]
    for c in result["checks"]:
        lines.append("[%s] %-22s %s" % ("OK" if c["ok"] else "FAIL", c["name"], c["detail"]))
        for p in c["problems"][:10]:
            lines.append("        - %s" % p)
        if c["problem_count"] > 10:
            lines.append("        ... and %d more" % (c["problem_count"] - 10))
    lines.append("")
    lines.append("VERIFIED" if result["ok"] else "NOT VERIFIED")
    return "\n".join(lines)
