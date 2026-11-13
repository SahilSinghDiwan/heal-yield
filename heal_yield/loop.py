"""The reference loop: generate -> run -> read failure -> repair -> re-run.

The loop is commodity. The ledger is the point. This module exists so the
harness has something to measure out of the box; any other generator can be
swapped in through the subprocess contract in `generator.py`, and the
measurement is identical either way.

Everything the loop decides is written to `run.json` with a path to the raw
file the decision was read from, so `verify` can reach the same conclusion
without executing anything.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
from typing import Dict, List, Optional

from . import candidates as cand_mod
from . import carry_forward, coverage_delta, dispositions, guard, metrics
from .cost import CeilingTripped, CostLedger
from .generator import Generator
from .pytest_harness import outcome_for, run_pytest
from .runstore import RunStore

#: Where generated tests are written inside the target checkout. One directory,
#: deleted and recreated per run, so a run never inherits a previous run's files.
GENERATED_DIR = "heal_yield_generated"

FLAKE_GATE_EXECUTIONS = 5


class UnsetLimit(ValueError):
    """A limit that has no measured value was left for the tool to guess.

    METRICS.md declares the per-run USD ceiling and the per-candidate time
    limit as unset. They are meant to be fixed from an observed run, and no
    run has been observed, so there is no honest default to fall back to.
    Failing here is the point: a silent default becomes a published number.
    """


class GeneratorFailed(Exception):
    """The generator command did not exit 0, or outlived its timeout.

    The contract (`generator.py`) is "exit 0 on success". Anything else is a
    failure of the generator or of its environment -- a missing API key, a
    rate limit, a retired model id -- and says nothing about what a model can
    do. It ends the run with `status: generator-failed` rather than being
    counted as `no-output`, which means "the model emitted nothing usable"
    and would be a false statement about a model that was never reached.
    """


def _invoke(generator: Generator, ledger: CostLedger, **kwargs):
    """Call the generator and record its usage; raise if it failed.

    The usage is recorded *before* the failure is raised, for the same reason
    the ceiling is checked after recording: a call that was billed and then
    failed must still appear in the published cost.
    """
    result = generator.invoke(**kwargs)
    ledger.record(result.usage)
    if result.returncode != 0:
        tail = [ln.strip() for ln in result.stderr.splitlines() if ln.strip()]
        raise GeneratorFailed(
            "generator failed for %s at round %d (exit %s): %s"
            % (kwargs["module"], kwargs["round_index"], result.returncode,
               (tail[-1] if tail else "no stderr")[:300])
        )
    return result


class LoopConfig(object):
    def __init__(
        self,
        repo_dir: str,
        package: str,
        modules: List[str],
        module_files: Dict[str, str],
        generator_command: str,
        k: int = 3,
        ceiling_usd: Optional[float] = None,
        per_candidate_timeout_s: Optional[float] = None,
        generator_timeout_s: float = 300.0,
        model: Optional[str] = None,
        temperature: float = 0.0,
        repetition: Optional[int] = None,
        per_candidate_coverage: bool = True,
    ):
        self.repo_dir = os.path.abspath(repo_dir)
        self.package = package
        self.modules = modules
        self.module_files = module_files
        self.generator_command = generator_command
        self.k = k
        # Neither of these has a project default, and neither may be guessed.
        # Both are supposed to be set from an observed run; no run has happened,
        # so they are declared-as-unset and the caller must name a value.
        if ceiling_usd is None:
            raise UnsetLimit(
                "the per-run USD ceiling is declared-as-unset: it has never been "
                "measured, so heal-yield will not invent one. Pass --ceiling-usd "
                "with the amount you are willing to lose."
            )
        if per_candidate_timeout_s is None:
            raise UnsetLimit(
                "the per-candidate time limit is declared-as-unset: it has never "
                "been measured, so heal-yield will not invent one. Pass "
                "--per-candidate-timeout with a value you chose."
            )
        self.ceiling_usd = float(ceiling_usd)
        self.per_candidate_timeout_s = float(per_candidate_timeout_s)
        self.generator_timeout_s = generator_timeout_s
        self.model = model
        self.temperature = temperature
        self.repetition = repetition
        self.per_candidate_coverage = per_candidate_coverage

    def to_dict(self) -> Dict:
        return {
            "package": self.package,
            "modules": self.modules,
            "module_files": self.module_files,
            "generator_command": self.generator_command,
            "k": self.k,
            "ceiling_usd": self.ceiling_usd,
            "per_candidate_timeout_s": self.per_candidate_timeout_s,
            "generator_timeout_s": self.generator_timeout_s,
            "model": self.model,
            "temperature": self.temperature,
            "repetition": self.repetition,
            "flake_gate_executions": FLAKE_GATE_EXECUTIONS,
            "generated_dir": GENERATED_DIR,
            "per_candidate_coverage": self.per_candidate_coverage,
        }


class _CandidateState(object):
    def __init__(self, candidate):
        self.c = candidate
        self.disposition: Optional[str] = None
        self.healed_at_round: Optional[int] = None
        self.built_round_0 = False
        self.rounds: List[Dict] = []
        self.failure_signatures: List[str] = []
        self.guard_reason: Optional[str] = None

    def to_dict(self) -> Dict:
        d = self.c.to_dict()
        d.update(
            {
                "disposition": self.disposition,
                "healed_at_round": self.healed_at_round,
                "built_round_0": self.built_round_0,
                "rounds": self.rounds,
                "failure_signatures": self.failure_signatures,
                "guard_reason": self.guard_reason,
            }
        )
        return d


def failure_signature(text: str) -> str:
    """A stable fingerprint of a failure, for the no-progress abort.

    Line numbers, temp paths and object addresses are stripped: a repair that
    only shifted a line is not progress, and treating it as progress would
    spend two more rounds proving it.
    """
    cleaned = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", text)
    cleaned = re.sub(r":\d+:", ":LINE:", cleaned)
    cleaned = re.sub(r"line \d+", "line LINE", cleaned)
    cleaned = re.sub(r"/tmp/[^\s\"']+", "/tmp/PATH", cleaned)
    keep = [
        ln.strip()
        for ln in cleaned.splitlines()
        if ln.startswith("E ") or ln.strip().startswith("E ") or "Error" in ln
    ]
    return hashlib.sha256("\n".join(keep[-20:]).encode("utf-8")).hexdigest()[:16]


def run_loop(config: LoopConfig, store: RunStore, sha: str, manifest_version: str) -> Dict:
    store.ensure()
    generator = Generator(config.generator_command, config.generator_timeout_s)
    ledger = CostLedger(config.ceiling_usd)
    fixture_seen = False
    status = "complete"
    truncation_reason = None

    gen_root = os.path.join(config.repo_dir, GENERATED_DIR)
    if os.path.isdir(gen_root):
        shutil.rmtree(gen_root)
    os.makedirs(gen_root)
    open(os.path.join(gen_root, "__init__.py"), "w").close()

    include_modules = [config.module_files[m] for m in config.modules]

    # -- baseline coverage, with the target modules' own tests already removed
    baseline = coverage_delta.measure(
        repo=config.repo_dir,
        targets=[],
        include_modules=include_modules,
        out_xml=store.coverage_path("baseline", "xml"),
        out_json=store.coverage_path("baseline", "json"),
        timeout_s=max(config.per_candidate_timeout_s * 10, 600),
        ignore=GENERATED_DIR,
    )
    coverage_delta.write_summary(store.coverage_path("baseline", "summary.json"), baseline)

    states: List[_CandidateState] = []

    try:
        # ---------------- round 0: generate ----------------
        for module in config.modules:
            out_dir = store.generated_dir(module, 0)
            result = _invoke(
                generator,
                ledger,
                module=module,
                source_file=config.module_files[module],
                repo=config.repo_dir,
                out_dir=out_dir,
                round_index=0,
                artifact_dir=store.model_dir(module, 0),
            )
            fixture_seen = fixture_seen or bool(result.meta.get("fixture"))
            emitted = _install(out_dir, gen_root, module)
            module_candidates = []
            for rel in emitted:
                with open(os.path.join(config.repo_dir, rel)) as fh:
                    src = fh.read()
                module_candidates.extend(cand_mod.extract_from_source(src, module, rel))
            if not module_candidates:
                # A generator that emitted nothing usable is a counted line in
                # the ledger, not a silently shorter run.
                st = _CandidateState(
                    cand_mod.Candidate(
                        id="%s::<no-output>" % module,
                        module=module,
                        file="",
                        name="<no-output>",
                        qualname="<no-output>",
                        lineno=0,
                        source_sha256="",
                    )
                )
                st.disposition = "no-output"
                states.append(st)
            else:
                states.extend(_CandidateState(c) for c in module_candidates)
            ledger.check_ceiling()

        n = len(states)

        # ---------------- build check ----------------
        for module in config.modules:
            files = sorted({s.c.file for s in states if s.c.module == module and s.c.file})
            for rel in files:
                prefix = store.pytest_prefix(module, "collect-round-0-%s" % _fslug(rel))
                res = run_pytest(
                    cwd=config.repo_dir,
                    targets=[rel],
                    artifact_prefix=prefix,
                    timeout_s=config.per_candidate_timeout_s,
                    collect_only=True,
                )
                collected = res.returncode == 0
                for s in states:
                    if s.c.file == rel:
                        s.built_round_0 = collected
                        s.rounds.append(
                            {
                                "round": 0,
                                "phase": "collect",
                                "ok": collected,
                                "artifact": os.path.relpath(prefix + ".txt", store.root),
                            }
                        )
                        if not collected:
                            s.disposition = "collect-error"

        # ---------------- rounds 0..k: run and repair ----------------
        for round_index in range(0, config.k + 1):
            active = [s for s in states if s.disposition is None]
            if not active:
                break
            if round_index > 0:
                # Only failing candidates are repaired. A passing one is not
                # put in front of the model at all, and its function is
                # carried forward unchanged through the repair of its file.
                failing = [s for s in active if s.healed_at_round is None]
                if not failing:
                    break
                _repair(config, store, generator, ledger, failing, round_index,
                        passing=[s for s in active if s.healed_at_round is not None])
                ledger.check_ceiling()
                active = [s for s in states if s.disposition is None]

            for s in active:
                prefix = store.pytest_prefix(
                    s.c.module, "round-%d-%s" % (round_index, _fslug(s.c.id))
                )
                res = run_pytest(
                    cwd=config.repo_dir,
                    targets=[s.c.nodeid],
                    artifact_prefix=prefix,
                    timeout_s=config.per_candidate_timeout_s,
                )
                artifact = os.path.relpath(prefix + ".txt", store.root)
                if res.timed_out:
                    s.disposition = "timeout"
                    s.rounds.append(
                        {"round": round_index, "phase": "run", "outcome": "timeout",
                         "artifact": artifact}
                    )
                    continue
                outcome = _outcome_for(res.outcomes, s.c)
                s.rounds.append(
                    {"round": round_index, "phase": "run", "outcome": outcome,
                     "artifact": artifact}
                )
                if outcome == "passed":
                    if s.healed_at_round is None:
                        s.healed_at_round = round_index
                else:
                    s.healed_at_round = None
                    sig = failure_signature(res.stdout + res.stderr)
                    s.failure_signatures.append(sig)
                    sigs = s.failure_signatures
                    if len(sigs) >= 2 and sigs[-1] == sigs[-2]:
                        s.disposition = "no-progress-abort"

            # Anything still failing after the last round is terminal.
            if round_index == config.k:
                for s in states:
                    if s.disposition is None and s.healed_at_round is None:
                        s.disposition = "still-failing"

    except (CeilingTripped, GeneratorFailed) as exc:
        # Both end the run early and both are published in full. Neither can
        # be a headline repetition: eligibility requires `status: complete`.
        status = "truncated" if isinstance(exc, CeilingTripped) else "generator-failed"
        truncation_reason = str(exc)
        for s in states:
            if s.disposition is None and s.healed_at_round is None:
                s.disposition = "still-failing"

    # ---------------- terminal flake gate ----------------
    survivors = [s for s in states if s.disposition is None and s.healed_at_round is not None]
    if survivors:
        node_ids = [s.c.nodeid for s in survivors]
        per_exec: List[Dict[str, str]] = []
        for i in range(1, FLAKE_GATE_EXECUTIONS + 1):
            prefix = store.gate_prefix(i)
            res = run_pytest(
                cwd=config.repo_dir,
                targets=node_ids,
                artifact_prefix=prefix,
                timeout_s=config.per_candidate_timeout_s * max(len(node_ids), 1),
            )
            per_exec.append(res.outcomes)
        for s in survivors:
            outcomes = [_outcome_for(e, s.c) for e in per_exec]
            s.rounds.append(
                {"phase": "flake-gate", "outcomes": outcomes,
                 "artifact": os.path.relpath(store.gate_prefix(1) + ".txt", store.root)}
            )
            if all(o == "passed" for o in outcomes) and len(outcomes) == FLAKE_GATE_EXECUTIONS:
                s.disposition = "passed"
            else:
                s.disposition = "still-flaky"
                s.healed_at_round = None

    # ---------------- final coverage ----------------
    # "After merging only the surviving tests" (METRICS.md metric 6).
    passed_nodes = [s.c.nodeid for s in states if s.disposition == "passed"]
    final = coverage_delta.measure(
        repo=config.repo_dir,
        targets=passed_nodes,
        include_modules=include_modules,
        out_xml=store.coverage_path("final", "xml"),
        out_json=store.coverage_path("final", "json"),
        timeout_s=max(config.per_candidate_timeout_s * 10, 600),
        # The project's own surviving suite plus the survivors -- not the
        # survivors alone. See `coverage_delta.measure`.
        with_suite=True,
        ignore=GENERATED_DIR,
    )
    coverage_delta.write_summary(store.coverage_path("final", "summary.json"), final)

    if config.per_candidate_coverage:
        _mark_no_coverage_increase(config, store, states, baseline, include_modules)

    # Anything still undecided cannot exist: assign explicitly rather than
    # letting a None reach the ledger and break the invariant silently.
    for s in states:
        if s.disposition is None:
            s.disposition = "still-failing"

    n = len(states)
    run = {
        "schema": "heal-yield/run/1",
        "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "status": status,
        "truncation_reason": truncation_reason,
        "fixture": fixture_seen,
        "repo": {"package": config.package, "sha": sha, "dir": os.path.basename(config.repo_dir)},
        "manifest_version": manifest_version,
        "config": _config_with_model(config, ledger),
        "candidates": [s.to_dict() for s in states],
        "cost": ledger.to_dict(),
        "coverage": {
            "baseline": _strip(baseline),
            "final": _strip(final),
            "net_new_covered_lines": coverage_delta.net_new_covered_lines(baseline, final),
        },
    }
    run["metrics"] = metrics.compute(run)
    counts = dispositions.tally(s.disposition for s in states)
    dispositions.check_sum(counts, n)
    return run


# ---------------------------------------------------------------- helpers


def _config_with_model(config: LoopConfig, ledger: CostLedger) -> Dict:
    """The run's configuration, with the model id the generator actually reported.

    The harness does not choose the model -- the generator does, and says which
    in every usage record. So the published model id is read from those
    records. An operator-supplied label is kept if one was given, but it never
    replaces what the generator reported: both are written down.
    """
    cfg = config.to_dict()
    reported = sorted({r.model for r in ledger.records if r.model})
    cfg["models_reported_by_generator"] = reported
    if cfg["model"] is None:
        cfg["model"] = ", ".join(reported) if reported else "unknown"
    return cfg


def _strip(summary: Dict) -> Dict:
    out = dict(summary)
    out.pop("covered_lines", None)
    return out


def _fslug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)[:120]


def _outcome_for(outcomes: Dict[str, str], candidate) -> str:
    return outcome_for(outcomes, candidate.file, candidate.qualname)


#: Total characters of captured output in one module's repair feedback. Split
#: evenly across the failing candidates, never below the per-candidate floor,
#: so every candidate's failure message survives however many there are.
FEEDBACK_BUDGET_CHARS = 12000
FEEDBACK_MIN_PER_CANDIDATE = 1200


def _excerpt(artifact_text: str, budget: int) -> str:
    """One candidate's captured output, cut to `budget` characters.

    The harness's own header (the command line, cwd, return code) is dropped:
    it is evidence for a reader, not feedback for the model. If the rest is
    still too long, the assertion lines (`E ...`, `>`) are kept first and the
    remaining budget is the tail of the output, where pytest prints the
    failing line and the summary.
    """
    body = artifact_text
    marker = "--- stdout ---\n"
    if marker in body:
        body = body.split(marker, 1)[1]
    body = body.replace("\n--- stderr ---\n", "\n").rstrip() + "\n"
    if len(body) <= budget:
        return body
    key = [ln for ln in body.splitlines() if ln.startswith(("E ", ">"))]
    key_text = "\n".join(key)
    if len(key_text) > budget // 2:
        key_text = key_text[-(budget // 2):]
    tail_room = max(budget - len(key_text) - 48, 0)
    tail = body[-tail_room:] if tail_room else ""
    omitted = "[... %d characters omitted ...]\n" % (len(body) - len(tail))
    return (key_text + "\n" if key_text else "") + omitted + tail


def render_feedback(sections: List) -> str:
    """The feedback file for one module: one `### <id>` section per failing candidate."""
    if not sections:
        return ""
    per = max(FEEDBACK_BUDGET_CHARS // len(sections), FEEDBACK_MIN_PER_CANDIDATE)
    out = []
    for cid, text in sections:
        out.append("### %s\n%s\n" % (cid, _excerpt(text, per)))
    return "\n".join(out)


def _install(out_dir: str, gen_root: str, module: str) -> List[str]:
    """Copy what the generator emitted into the checkout, return repo-relative paths."""
    installed = []
    repo_dir = os.path.dirname(gen_root)
    for fn in sorted(os.listdir(out_dir)):
        if not (fn.startswith("test_") and fn.endswith(".py")):
            continue
        dest = os.path.join(gen_root, fn)
        shutil.copyfile(os.path.join(out_dir, fn), dest)
        installed.append(os.path.relpath(dest, repo_dir))
    return installed


def _repair(config, store, generator, ledger, active, round_index, passing=()) -> None:
    """One repair round: re-prompt per module carrying the captured output.

    `active` is the failing candidates; only they are put in the feedback and
    judged by the guard. `passing` are candidates that currently pass: each
    one's function is restored from the previous file after the repair, so a
    repair that dropped or rewrote it cannot cost the run a passing test.
    """
    by_module: Dict[str, List] = {}
    for s in active:
        by_module.setdefault(s.c.module, []).append(s)
    keep_by_file: Dict[str, List[str]] = {}
    for s in passing:
        if s.c.file:
            keep_by_file.setdefault(s.c.file, []).append(s.c.qualname)

    for module, group in sorted(by_module.items()):
        feedback_path = os.path.join(store.model_dir(module, round_index), "feedback.txt")
        os.makedirs(os.path.dirname(feedback_path), exist_ok=True)
        sections = []
        for s in group:
            last = [r for r in s.rounds if r.get("phase") == "run"]
            if not last:
                continue
            art = store.path(last[-1]["artifact"])
            text = ""
            if os.path.exists(art):
                with open(art, errors="replace") as src:
                    text = src.read()
            sections.append((s.c.id, text))
        with open(feedback_path, "w") as fh:
            fh.write(render_feedback(sections))

        before_sources = {}
        module_files = {s.c.file for s in group} | {
            s.c.file for s in passing if s.c.module == module and s.c.file}
        for rel in sorted(module_files):
            full = os.path.join(config.repo_dir, rel)
            if os.path.exists(full):
                with open(full) as fh:
                    before_sources[rel] = fh.read()

        out_dir = store.generated_dir(module, round_index)
        _invoke(
            generator,
            ledger,
            module=module,
            source_file=config.module_files[module],
            repo=config.repo_dir,
            out_dir=out_dir,
            round_index=round_index,
            artifact_dir=store.model_dir(module, round_index),
            feedback_file=feedback_path,
        )

        gen_root = os.path.join(config.repo_dir, GENERATED_DIR)
        emitted = _install(out_dir, gen_root, module)
        after_sources = {}
        for rel in emitted:
            full = os.path.join(config.repo_dir, rel)
            with open(full) as fh:
                after_sources[rel] = fh.read()
            keep = keep_by_file.get(rel)
            if keep and rel in before_sources:
                merged = carry_forward.splice(before_sources[rel], after_sources[rel], keep)
                if merged != after_sources[rel]:
                    # The model's own file stays under raw/generated/ as it was
                    # emitted; what actually ran is stored beside it.
                    carried = os.path.join(out_dir, "carried-forward")
                    os.makedirs(carried, exist_ok=True)
                    with open(os.path.join(carried, os.path.basename(rel)), "w") as fh:
                        fh.write(merged)
                    with open(full, "w") as fh:
                        fh.write(merged)
                    after_sources[rel] = merged

        for s in group:
            after = after_sources.get(s.c.file)
            before = before_sources.get(s.c.file, "")
            verdict = guard.inspect_repair(before, after, s.c.qualname)
            s.rounds.append(
                {"round": round_index, "phase": "repair", "guard": verdict,
                 "artifact": os.path.relpath(
                     os.path.join(store.model_dir(module, round_index), "generator.txt"),
                     store.root)}
            )
            if verdict["rejected"]:
                s.disposition = "weakened-rejected"
                s.guard_reason = verdict["reason"]


def _mark_no_coverage_increase(config, store, states, baseline, include_modules) -> None:
    """A test that passes but covers nothing new is a counted disposition, not a pass."""
    base_lines = baseline.get("covered_lines") or {}
    for s in states:
        if s.disposition != "passed":
            continue
        out_xml = store.coverage_path("per-candidate-%s" % _fslug(s.c.id), "xml")
        out_json = store.coverage_path("per-candidate-%s" % _fslug(s.c.id), "json")
        try:
            summary = coverage_delta.measure(
                repo=config.repo_dir,
                targets=[s.c.nodeid],
                include_modules=include_modules,
                out_xml=out_xml,
                out_json=out_json,
                timeout_s=config.per_candidate_timeout_s * 2,
            )
        except Exception:
            continue
        new = 0
        for filename, lines in (summary.get("covered_lines") or {}).items():
            new += len(set(lines) - set(base_lines.get(filename, [])))
        s.rounds.append({"phase": "coverage", "net_new_lines": new,
                         "artifact": os.path.relpath(out_xml, store.root)})
        if new == 0:
            s.disposition = "no-coverage-increase"
            s.healed_at_round = None


def write_run(store: RunStore, run: Dict) -> None:
    store.write_json("run.json", run)
    store.write_checksums()


def load_run(store: RunStore) -> Dict:
    return store.read_json("run.json")


__all__ = [
    "LoopConfig",
    "GeneratorFailed",
    "run_loop",
    "write_run",
    "load_run",
    "failure_signature",
    "GENERATED_DIR",
    "FLAKE_GATE_EXECUTIONS",
]
