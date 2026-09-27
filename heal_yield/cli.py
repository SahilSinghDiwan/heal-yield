"""`heal-yield` command line. CLI only in v1; there is no GitHub Action.

    heal-yield manifest                 show the declared benchmark set
    heal-yield modules --repo <name>    apply the mechanical module rule
    heal-yield run --repo <name>        one measured run (the generator may cost money)
    heal-yield verify --run <dir>       recompute every number from stored artifacts
    heal-yield report --run <dir>       re-render report.md from run.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
from typing import Dict, List, Optional

import yaml

from . import checkout
from . import manifest as manifest_mod
from . import metrics as metrics_mod
from . import modules as modules_mod
from . import report as report_mod
from . import verify as verify_mod
from .loop import GENERATED_DIR, LoopConfig, run_loop, write_run
from .runstore import RunStore, run_dir_name

DEFAULT_RUNS_DIR = os.environ.get("HEAL_YIELD_RUNS", "../heal-yield-runs/runs")
#: The free fixture generator. Built from `sys.executable` rather than the
#: string "python": a venv that has no `python` on PATH (uv-created ones often
#: do not) would otherwise fail deep inside the loop with a FileNotFoundError.
STUB_GENERATOR = "%s -m heal_yield.generators.stub_gen" % shlex.quote(sys.executable)


def _manifest(args) -> manifest_mod.Manifest:
    return manifest_mod.load(getattr(args, "manifest", None))


# ------------------------------------------------------------------ manifest
def cmd_manifest(args) -> int:
    m = _manifest(args)
    print("manifest %s (declared %s)" % (m.version, m.data.get("declared")))
    print()
    print("%-22s %-10s %-9s %-8s %s" % ("repo", "tag", "tier", "v1 run", "sha"))
    for r in m.repos:
        print("%-22s %-10s %-9s %-8s %s"
              % (r.name, r.tag, r.tier, "yes" if r.v1_run else "-", r.sha[:12]))
    print()
    print("Headline average is computed over tier=headline only; anchors are never blended.")
    print("Repetitions required for a headline claim: %s"
          % m.policy.get("repetitions"))
    return 0


# ------------------------------------------------------------------- modules
def cmd_modules(args) -> int:
    m = _manifest(args)
    repo = m.get(args.repo)
    repo_dir = args.checkout or os.path.join(args.work_dir, repo.name)
    if not os.path.isdir(repo_dir):
        checkout.clone_pinned(repo.repo, repo.sha, repo_dir)
    selection = modules_mod.select_modules(repo_dir, repo.name, top_n=args.top)
    if args.json:
        json.dump(selection, sys.stdout, indent=2)
        print()
        return 0
    print("%s @ %s -- top %d modules by statement count" % (repo.name, repo.sha[:7], args.top))
    for s in selection["selected"]:
        print("  %-50s %5d statements" % (s["module"], s["statements"]))
    print()
    print("Derived from the SHA by the manifest's module_selection_rule; never hand-picked.")
    return 0


# ----------------------------------------------------------------------- run
def cmd_run(args) -> int:
    m = _manifest(args)
    repo = m.get(args.repo)
    repo_dir = args.checkout or os.path.join(args.work_dir, repo.name)

    if args.generator == STUB_GENERATOR:
        sys.stderr.write(
            "note: running the FIXTURE generator. The result describes the harness, "
            "not a model, and is mechanically barred from being a headline repetition.\n"
        )
    elif not args.yes_spend:
        sys.stderr.write(
            "refusing to start: %r is not the fixture generator and this run will spend "
            "money against a real model.\nRe-run with --yes-spend to authorise it, "
            "with --ceiling-usd set to the amount you are willing to lose.\n"
            % args.generator
        )
        return 2

    print("checking out %s @ %s" % (repo.name, repo.sha[:7]))
    sha = checkout.clone_pinned(repo.repo, repo.sha, repo_dir)
    checkout.wipe_generated(repo_dir, GENERATED_DIR)

    selection = modules_mod.select_modules(repo_dir, repo.name, top_n=args.top)
    module_files = {s["module"]: s["path"] for s in selection["selected"]}
    module_list = list(module_files)
    if not module_list:
        sys.stderr.write("the module rule selected nothing in %s\n" % repo_dir)
        return 1
    print("modules: %s" % ", ".join(module_list))

    baseline_report = checkout.apply_baseline_rule(repo_dir, module_files)
    print("baseline rule removed %d existing test files" % baseline_report["removed_count"])

    if args.install:
        proc = subprocess.run([sys.executable, "-m", "pip", "install", "-e", ".", "-q"],
                              cwd=repo_dir)
        if proc.returncode != 0:
            sys.stderr.write(
                "warning: `pip install -e .` failed in %s (exit %d). The run continues, but "
                "imports of the target package may fail and be recorded as candidate "
                "failures that are really environment failures.\n"
                % (repo_dir, proc.returncode)
            )

    date = args.date or datetime.date.today().isoformat()
    run_root = os.path.join(args.runs_dir, run_dir_name(date, repo.name, sha, args.repetition))
    store = RunStore(run_root)
    store.ensure()

    config = LoopConfig(
        repo_dir=repo_dir,
        package=repo.name,
        modules=module_list,
        module_files=module_files,
        generator_command=args.generator,
        k=args.k,
        ceiling_usd=args.ceiling_usd,
        per_candidate_timeout_s=args.per_candidate_timeout,
        generator_timeout_s=args.generator_timeout,
        model=args.model,
        temperature=args.temperature,
        repetition=args.repetition,
        per_candidate_coverage=not args.no_per_candidate_coverage,
    )

    run = run_loop(config, store, sha=sha, manifest_version=m.version)
    run["baseline_rule"] = baseline_report
    run["metrics"] = metrics_mod.compute(run)

    store.write_json("modules.json", selection)
    store.write_text(
        "manifest.snapshot.yaml",
        yaml.safe_dump(m.snapshot([repo.name]), sort_keys=False, default_flow_style=False),
    )
    store.write_text("metadata.yaml", yaml.safe_dump(_metadata(run, repo, sha, m),
                                                     sort_keys=False, default_flow_style=False))
    write_run(store, run)
    store.write_text("report.md", report_mod.render(run, run["metrics"]))

    print()
    print(report_mod.render(run, run["metrics"]))
    print("run written to %s" % run_root)
    return 0


def _metadata(run: Dict, repo, sha: str, m) -> Dict:
    cfg = run["config"]
    return {
        "schema": "heal-yield/metadata/1",
        "created_utc": run["created_utc"],
        "status": run["status"],
        "fixture": run.get("fixture", False),
        "headline_eligible": run["metrics"]["headline_eligible"],
        "repo": {"name": repo.name, "url": repo.repo, "tag": repo.tag, "sha": sha,
                 "license": repo.license, "tier": repo.tier},
        "manifest": {"version": m.version, "declared": m.data.get("declared")},
        "model": {"id": cfg["model"], "temperature": cfg["temperature"]},
        "loop": {"k": cfg["k"], "flake_gate_executions": cfg["flake_gate_executions"],
                 "generator_command": cfg["generator_command"]},
        "limits": {"per_candidate_timeout_s": cfg["per_candidate_timeout_s"],
                   "generator_timeout_s": cfg["generator_timeout_s"],
                   "ceiling_usd": cfg["ceiling_usd"]},
        "modules": cfg["modules"],
        "baseline_rule": run.get("baseline_rule"),
        "tool": {"name": "heal-yield", "version": __import__("heal_yield").__version__,
                 "python": sys.version.split()[0]},
    }


# -------------------------------------------------------------------- verify
def cmd_verify(args) -> int:
    result = verify_mod.verify_run(args.run)
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(verify_mod.render_verification(result))
    return 0 if result["ok"] else 1


# -------------------------------------------------------------------- report
def cmd_report(args) -> int:
    store = RunStore(args.run)
    run = store.read_json("run.json")
    text = report_mod.render(run, metrics_mod.compute(run), runs_sha=args.runs_sha)
    if args.write:
        store.write_text("report.md", text)
        print("wrote %s" % store.path("report.md"))
    else:
        print(text)
    return 0


# ---------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="heal-yield", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=None, help="path to the benchmark manifest")
    sub = p.add_subparsers(dest="command")

    m = sub.add_parser("manifest", help="show the declared benchmark set")
    m.set_defaults(func=cmd_manifest)

    mods = sub.add_parser("modules", help="apply the mechanical module-selection rule")
    mods.add_argument("--repo", required=True)
    mods.add_argument("--checkout", default=None)
    mods.add_argument("--work-dir", default=".heal-yield-work")
    mods.add_argument("--top", type=int, default=modules_mod.DEFAULT_TOP_N)
    mods.add_argument("--json", action="store_true")
    mods.set_defaults(func=cmd_modules)

    r = sub.add_parser("run", help="one measured run")
    r.add_argument("--repo", required=True, help="a name from the declared manifest")
    r.add_argument("--generator", default=STUB_GENERATOR)
    r.add_argument("--model", default="stub-fixture-generator/1")
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--k", type=int, default=3)
    # No defaults, deliberately. METRICS.md declares both of these unset: they
    # are supposed to be fixed from an observed run, no run has been observed,
    # and a default here would become a published number nobody measured.
    r.add_argument("--ceiling-usd", type=float, required=True,
                   help="REQUIRED, no default. Hard per-run USD ceiling; tripping it "
                        "truncates the run, loudly. heal-yield has no recommended value: "
                        "none has been measured. Name the amount you are willing to lose.")
    r.add_argument("--per-candidate-timeout", type=float, required=True,
                   help="REQUIRED, no default. Per-candidate time limit in seconds; "
                        "exceeding it is disposition `timeout`. No measured value exists "
                        "yet, so heal-yield will not pick one for you.")
    r.add_argument("--generator-timeout", type=float, default=600.0)
    r.add_argument("--repetition", type=int, default=None)
    r.add_argument("--date", default=None)
    r.add_argument("--checkout", default=None)
    r.add_argument("--work-dir", default=".heal-yield-work")
    r.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    r.add_argument("--top", type=int, default=modules_mod.DEFAULT_TOP_N)
    r.add_argument("--install", action="store_true",
                   help="pip install -e the checkout before running")
    r.add_argument("--no-per-candidate-coverage", action="store_true")
    r.add_argument("--yes-spend", action="store_true",
                   help="required for any generator that is not the free fixture")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="recompute every number from stored artifacts")
    v.add_argument("--run", required=True)
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_verify)

    rep = sub.add_parser("report", help="re-render report.md from run.json")
    rep.add_argument("--run", required=True)
    rep.add_argument("--runs-sha", default=None)
    rep.add_argument("--write", action="store_true")
    rep.set_defaults(func=cmd_report)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
