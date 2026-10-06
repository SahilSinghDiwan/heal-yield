"""End to end, offline, free.

The whole harness runs here against a tiny package built in `tmp_path`, with
the fixture generator. No network, no API key, no Docker -- the tool's
credibility depends on being testable inside the constraint it sells.
"""

import json
import os
import shutil
import sys

import pytest

from heal_yield import report, verify
from heal_yield.loop import LoopConfig, run_loop, write_run
from heal_yield.runstore import RunStore

TARGET = '''
"""A tiny package with enough branches to move a coverage number."""


def classify(n):
    if n < 0:
        return "negative"
    if n == 0:
        return "zero"
    if n % 2 == 0:
        return "even"
    return "odd"


def bounded(value, low, high):
    if low > high:
        raise ValueError("low above high")
    if value < low:
        return low
    if value > high:
        return high
    return value


class Accumulator:
    def __init__(self):
        self.total = 0

    def add(self, n):
        self.total += n
        return self.total

    def reset(self):
        self.total = 0
        return self
'''

STUB = "%s -m heal_yield.generators.stub_gen" % sys.executable


@pytest.fixture()
def target_repo(tmp_path):
    repo = tmp_path / "tinypkg"
    pkg = repo / "tinypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .core import *  # noqa\n")
    (pkg / "core.py").write_text(TARGET)
    (repo / "conftest.py").write_text(
        "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )
    return repo


@pytest.fixture()
def finished_run(tmp_path, target_repo):
    store = RunStore(str(tmp_path / "run"))
    config = LoopConfig(
        repo_dir=str(target_repo),
        package="tinypkg",
        modules=["tinypkg.core"],
        module_files={"tinypkg.core": os.path.join("tinypkg", "core.py")},
        generator_command=STUB,
        k=3,
        ceiling_usd=1.0,
        per_candidate_timeout_s=60.0,
        generator_timeout_s=120.0,
        model="stub-fixture-generator/1",
    )
    run = run_loop(config, store, sha="0" * 40, manifest_version="test")
    write_run(store, run)
    store.write_text("report.md", report.render(run, run["metrics"]))
    return store, run


def test_the_loop_produces_a_ledger_that_sums_to_n(finished_run):
    _store, run = finished_run
    m = run["metrics"]
    assert m["n"] > 0
    assert sum(m["metric_9_dispositions"]["ledger"].values()) == m["n"]
    assert m["metric_9_dispositions"]["invariant_ok"]


def test_the_repair_loop_actually_rescues_something(finished_run):
    _store, run = finished_run
    per_round = run["metrics"]["metric_4_post_heal_green_rate"]["per_round_attribution"]
    assert any(int(r) >= 1 for r in per_round), (
        "the fixture scripts a candidate that fails at round 0 and is repaired at "
        "round 1; if nothing is attributed past round 0 the repair path never ran"
    )


def test_a_candidate_that_never_heals_lands_in_the_never_healed_breakdown(finished_run):
    _store, run = finished_run
    assert run["metrics"]["metric_9_dispositions"]["never_healed_total"] >= 1


def test_a_passing_test_that_covers_nothing_new_is_its_own_disposition(finished_run):
    _store, run = finished_run
    ledger = run["metrics"]["metric_9_dispositions"]["ledger"]
    assert ledger["no-coverage-increase"] >= 1


def test_coverage_is_reported_as_line_branch_and_combined_never_blended(finished_run):
    _store, run = finished_run
    m6 = run["metrics"]["metric_6_coverage"]
    assert m6["final"]["line_pct"] is not None
    assert m6["final"]["branch_pct"] is not None
    assert m6["final"]["line_and_branch_pct"] is not None
    assert m6["net_new_covered_lines"] > 0


def test_verify_recomputes_every_number_without_re_executing_anything(finished_run):
    store, _run = finished_run
    result = verify.verify_run(store.root)
    failed = [c for c in result["checks"] if not c["ok"]]
    assert not failed, failed
    assert result["ok"]


def test_verify_catches_a_tampered_artifact(finished_run):
    store, _run = finished_run
    target = None
    for rel in store.iter_raw_files():
        if rel.endswith(".txt"):
            target = store.path(rel)
            break
    assert target
    with open(target, "a") as fh:
        fh.write("\n# edited after the fact\n")
    result = verify.verify_run(store.root)
    integrity = [c for c in result["checks"] if c["name"] == "integrity"][0]
    assert not integrity["ok"]
    assert result["ok"] is False


def test_verify_catches_a_number_edited_in_run_json(finished_run):
    store, run = finished_run
    run["metrics"]["metric_5_heal_yield"]["pct"] = 99.9
    with open(store.path("run.json"), "w") as fh:
        json.dump(run, fh, indent=2, sort_keys=True)
    result = verify.verify_run(store.root)
    arithmetic = [c for c in result["checks"] if c["name"] == "arithmetic"][0]
    assert not arithmetic["ok"]


def test_verify_catches_a_disposition_swapped_to_passed(finished_run):
    store, run = finished_run
    for c in run["candidates"]:
        if c["disposition"] in ("still-failing", "no-progress-abort"):
            c["disposition"] = "passed"
            c["healed_at_round"] = 0
            break
    else:
        pytest.fail("the fixture should leave at least one unhealed candidate")
    with open(store.path("run.json"), "w") as fh:
        json.dump(run, fh, indent=2, sort_keys=True)
    result = verify.verify_run(store.root)
    check = [c for c in result["checks"] if c["name"] == "disposition-evidence"][0]
    assert not check["ok"], "verify accepted a `passed` with no flake-gate record behind it"
    assert not result["ok"]


def test_verify_needs_no_network_or_target_checkout(finished_run, target_repo, tmp_path):
    """The run directory alone must be enough. Delete the checkout and re-verify."""
    store, _run = finished_run
    shutil.rmtree(str(target_repo))
    result = verify.verify_run(store.root)
    assert result["ok"], "verify depended on something outside the run directory"


def test_the_report_is_rendered_from_json_not_hand_written(finished_run):
    store, run = finished_run
    text = report.render(run, run["metrics"])
    assert "dispositions sum to N" in text
    assert "not a claim to improve on the maintainers' suites" in text.lower()
    assert str(run["metrics"]["n"]) in text


def test_the_unset_limits_have_no_default_and_must_be_named(target_repo):
    """METRICS.md declares both limits unset; the code must refuse to guess."""
    from heal_yield.loop import UnsetLimit

    common = dict(
        repo_dir=str(target_repo),
        package="tinypkg",
        modules=["tinypkg.core"],
        module_files={"tinypkg.core": os.path.join("tinypkg", "core.py")},
        generator_command=STUB,
    )
    with pytest.raises(UnsetLimit) as no_ceiling:
        LoopConfig(per_candidate_timeout_s=60.0, **common)
    assert "USD ceiling" in str(no_ceiling.value)

    with pytest.raises(UnsetLimit) as no_timeout:
        LoopConfig(ceiling_usd=1.0, **common)
    assert "per-candidate time limit" in str(no_timeout.value)


def test_the_cli_refuses_a_run_that_does_not_name_both_limits(capsys):
    from heal_yield.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--repo", "cachetools"])
    err = capsys.readouterr().err
    assert "--ceiling-usd" in err and "--per-candidate-timeout" in err


def test_the_paid_generator_accepts_the_documented_model_flag(tmp_path, monkeypatch):
    """The command the README prints must at least parse. It must not spend."""
    from heal_yield.generators import anthropic_gen

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = anthropic_gen.main([
        "--model", "claude-sonnet-4-5",
        "--module", "pkg.mod",
        "--source-file", "pkg/mod.py",
        "--repo", str(tmp_path),
        "--out-dir", str(tmp_path / "out"),
        "--round", "0",
        "--meta-out", str(tmp_path / "usage.json"),
    ])
    # Parsed fine, then refused for want of a key -- no request was made.
    assert rc == 2


def test_a_real_generator_needs_yes_spend(tmp_path, capsys):
    from heal_yield.cli import main as cli_main

    rc = cli_main([
        "run", "--repo", "cachetools",
        "--generator", "python -m heal_yield.generators.anthropic_gen",
        "--ceiling-usd", "5",
        "--per-candidate-timeout", "60",
        "--work-dir", str(tmp_path),
    ])
    assert rc == 2
    assert "--yes-spend" in capsys.readouterr().err


def test_a_fixture_run_is_barred_from_being_a_headline_repetition(finished_run):
    store, run = finished_run
    assert run["fixture"] is True
    assert run["metrics"]["headline_eligible"] is False
    result = verify.verify_run(store.root)
    check = [c for c in result["checks"] if c["name"] == "headline-eligibility"][0]
    assert check["ok"] is True
    assert "NOT eligible" in check["detail"]


# ---------------------------------------------------------------- coverage
OTHER = '''
"""A second target module that no pre-existing test imports."""


def double(n):
    if n is None:
        return None
    return n * 2
'''

EXISTING_SUITE = '''
from tinypkg.core import Accumulator, bounded


def test_bounded_clamps_low():
    assert bounded(-5, 0, 10) == 0


def test_accumulator_adds():
    assert Accumulator().add(3) == 3
'''


@pytest.fixture()
def run_on_a_repo_with_its_own_suite(tmp_path, target_repo):
    """A target that, like every real one, still has tests after the baseline rule."""
    (target_repo / "tinypkg" / "other.py").write_text(OTHER)
    tests = target_repo / "tests"
    tests.mkdir()
    (tests / "test_existing.py").write_text(EXISTING_SUITE)
    store = RunStore(str(tmp_path / "run"))
    config = LoopConfig(
        repo_dir=str(target_repo),
        package="tinypkg",
        modules=["tinypkg.core", "tinypkg.other"],
        module_files={
            "tinypkg.core": os.path.join("tinypkg", "core.py"),
            "tinypkg.other": os.path.join("tinypkg", "other.py"),
        },
        generator_command=STUB,
        k=1,
        ceiling_usd=1.0,
        per_candidate_timeout_s=60.0,
        generator_timeout_s=120.0,
    )
    run = run_loop(config, store, sha="0" * 40, manifest_version="test")
    write_run(store, run)
    return store, run


def test_final_coverage_is_the_surviving_suite_plus_the_survivors(
    run_on_a_repo_with_its_own_suite,
):
    """Metric 6 is before vs after *merging* the survivors.

    Measuring the survivors alone compared a whole suite against a handful of
    generated tests: the first rehearsal against `cachetools` printed a line
    coverage delta of -47.4% next to "net new covered lines: 16".
    """
    _store, run = run_on_a_repo_with_its_own_suite
    cov = run["coverage"]
    assert cov["baseline"]["lines_covered"] > 0, "the existing suite should cover something"
    assert cov["final"]["lines_covered"] >= cov["baseline"]["lines_covered"]
    assert cov["final"]["branches_covered"] >= cov["baseline"]["branches_covered"]
    m6 = run["metrics"]["metric_6_coverage"]
    assert m6["delta_line_pct"] >= 0
    assert (
        cov["final"]["lines_covered"] - cov["baseline"]["lines_covered"]
        == m6["net_new_covered_lines"]
    )


def test_baseline_and_final_coverage_share_one_denominator(run_on_a_repo_with_its_own_suite):
    """A target module nobody imported is 0% covered, not absent.

    `tinypkg.other` is imported by no pre-existing test. If the baseline drops
    it, the baseline and final percentages are over different totals and their
    difference means nothing.
    """
    store, run = run_on_a_repo_with_its_own_suite
    cov = run["coverage"]
    assert cov["baseline"]["lines_total"] == cov["final"]["lines_total"]
    assert cov["baseline"]["branches_total"] == cov["final"]["branches_total"]
    with open(store.coverage_path("baseline", "xml")) as fh:
        assert "other.py" in fh.read()
    assert verify.verify_run(store.root)["ok"]


def test_verify_catches_a_passed_backed_by_a_flake_gate_record_nobody_ran(finished_run):
    """`passed` must be re-derived from the gate's raw output, not from run.json.

    Relabel a candidate that never healed as `passed`, hand it a five-pass
    gate record and recompute the metrics block so the arithmetic agrees. On
    the first cachetools rehearsal this moved heal yield from 5.3% to 26.3%
    and `verify` still printed VERIFIED.
    """
    from heal_yield import metrics

    store, run = finished_run
    for c in run["candidates"]:
        if c["disposition"] in ("still-failing", "no-progress-abort"):
            c["disposition"] = "passed"
            c["healed_at_round"] = 1
            c["rounds"].append({
                "phase": "flake-gate",
                "outcomes": ["passed"] * 5,
                "artifact": "raw/pytest/gate/exec-1.txt",
            })
            break
    else:
        pytest.fail("the fixture should leave at least one unhealed candidate")
    run["metrics"] = metrics.compute(run)
    with open(store.path("run.json"), "w") as fh:
        json.dump(run, fh, indent=2, sort_keys=True)

    result = verify.verify_run(store.root)
    evidence = [c for c in result["checks"] if c["name"] == "evidence"][0]
    assert not evidence["ok"], "verify accepted a flake-gate record with no execution behind it"
    assert "flake gate" in evidence["problems"][0]
    assert not result["ok"]
