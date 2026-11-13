"""The repair round must not cost the run a test that already passes.

Published runs r1-r6 lost 30 candidates to `weakened-rejected` with the reason
"candidate was deleted by the repair": every undecided candidate, passing ones
included, went into the repair prompt, and when the model's rewritten file
left a passing function out, the guard counted it as a deletion. 18 of those
30 had passed at round 0.

The fake generators here reproduce that: at round 1 and later they emit only
the repaired failing test, and leave the passing one out or rewrite it.
"""

import os
import sys

import pytest

from heal_yield import carry_forward, verify
from heal_yield.loop import LoopConfig, run_loop, write_run
from heal_yield.runstore import RunStore

TARGET = "def double(n):\n    return n * 2\n"

# A generator that, on repair, emits only the test it was asked to repair.
OMITTING_GENERATOR = r'''
import argparse, json, os
p = argparse.ArgumentParser()
for a in ("--module", "--source-file", "--repo", "--out-dir", "--meta-out", "--feedback-file"):
    p.add_argument(a)
p.add_argument("--round", type=int)
a = p.parse_args()
os.makedirs(a.out_dir, exist_ok=True)
head = "from tinypkg.core import double\n\n"
if a.round == 0:
    body = (
        "def test_already_green():\n    assert double(2) == 4\n\n"
        "def test_needs_repair():\n    assert double(3) == 7\n"
    )
else:
    body = "def test_needs_repair():\n    assert double(3) == 6\n"
with open(os.path.join(a.out_dir, "test_tinypkg_core.py"), "w") as fh:
    fh.write(head + body)
with open(a.meta_out, "w") as fh:
    json.dump({"model": "fake-omitting/1", "prompt_tokens": 0,
               "completion_tokens": 0, "usd": 0.0, "fixture": True}, fh)
'''

# A generator that, on repair, rewrites the passing test into a failing one.
REWRITING_GENERATOR = OMITTING_GENERATOR.replace(
    'body = "def test_needs_repair():\\n    assert double(3) == 6\\n"',
    'body = ("def test_already_green():\\n    assert double(2) == 5\\n\\n"\n'
    '            "def test_needs_repair():\\n    assert double(3) == 6\\n")',
)


@pytest.fixture()
def target_repo(tmp_path):
    repo = tmp_path / "tinypkg"
    pkg = repo / "tinypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(TARGET)
    (repo / "conftest.py").write_text(
        "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n"
    )
    return repo


def _run(tmp_path, target_repo, script_text):
    script = tmp_path / "gen.py"
    script.write_text(script_text)
    store = RunStore(str(tmp_path / "run"))
    config = LoopConfig(
        repo_dir=str(target_repo),
        package="tinypkg",
        modules=["tinypkg.core"],
        module_files={"tinypkg.core": os.path.join("tinypkg", "core.py")},
        generator_command="%s %s" % (sys.executable, script),
        k=2,
        ceiling_usd=1.0,
        per_candidate_timeout_s=60.0,
        generator_timeout_s=120.0,
        per_candidate_coverage=False,
    )
    run = run_loop(config, store, sha="0" * 40, manifest_version="test")
    write_run(store, run)
    return store, {c["name"]: c for c in run["candidates"]}, run


def test_a_passing_test_the_repair_omitted_is_carried_forward_not_rejected(
    tmp_path, target_repo
):
    store, by_name, run = _run(tmp_path, target_repo, OMITTING_GENERATOR)
    green = by_name["test_already_green"]
    assert green["disposition"] == "passed", green
    assert green["healed_at_round"] == 0
    assert not [r for r in green["rounds"] if r.get("phase") == "repair"], (
        "a passing candidate must not enter the repair round"
    )
    fixed = by_name["test_needs_repair"]
    assert fixed["disposition"] == "passed"
    assert fixed["healed_at_round"] == 1
    assert run["metrics"]["metric_3_first_pass_green_rate"]["numerator"] == 1
    assert run["metrics"]["metric_10_assertion_weakening_guard"]["rejected"] == 0
    assert verify.verify_run(store.root)["ok"]


def test_a_passing_test_the_repair_rewrote_is_carried_forward_unchanged(
    tmp_path, target_repo
):
    _store, by_name, _run_ = _run(tmp_path, target_repo, REWRITING_GENERATOR)
    green = by_name["test_already_green"]
    assert green["disposition"] == "passed", green
    assert green["healed_at_round"] == 0
    installed = (target_repo / "heal_yield_generated" / "test_tinypkg_core.py").read_text()
    assert "assert double(2) == 4" in installed
    assert "assert double(2) == 5" not in installed


def test_feedback_names_only_the_failing_candidates(tmp_path, target_repo):
    store, _by_name, _run_ = _run(tmp_path, target_repo, OMITTING_GENERATOR)
    with open(store.raw("model", "tinypkg.core", "round-1", "feedback.txt")) as fh:
        feedback = fh.read()
    assert "test_needs_repair" in feedback
    assert "### heal_yield_generated/test_tinypkg_core.py::test_already_green" not in feedback


# ------------------------------------------------------------ the splice itself
BEFORE = '''import pytest


def test_keep():
    assert 1 + 1 == 2


class TestBox:
    @pytest.mark.slow
    def test_inner_keep(self):
        assert [1][0] == 1

    def test_inner_fix(self):
        assert 0 == 1


def test_fix():
    assert 0 == 1
'''


def test_splice_restores_kept_functions_and_keeps_repaired_ones():
    after = (
        "import pytest\n\n\n"
        "def test_keep():\n    assert True\n\n\n"
        "class TestBox:\n    def test_inner_fix(self):\n        assert 1 == 1\n\n\n"
        "def test_fix():\n    assert 1 == 1\n"
    )
    merged = carry_forward.splice(BEFORE, after, ["test_keep", "TestBox.test_inner_keep"])
    assert "    assert 1 + 1 == 2" in merged
    assert "assert True" not in merged
    assert "@pytest.mark.slow\n    def test_inner_keep(self):" in merged
    assert "def test_fix():\n    assert 1 == 1" in merged
    assert "        assert 1 == 1" in merged
    compile(merged, "<merged>", "exec")


def test_splice_appends_a_kept_function_whose_class_the_repair_dropped():
    after = "def test_fix():\n    assert 1 == 1\n"
    merged = carry_forward.splice(BEFORE, after, ["test_keep", "TestBox.test_inner_keep"])
    ns = {}
    exec(compile(merged.replace("@pytest.mark.slow", ""), "<merged>", "exec"), ns)
    assert "test_keep" in ns and "test_inner_keep" in vars(ns["TestBox"])
    assert "test_inner_fix" not in vars(ns["TestBox"]), "only kept methods come back"


def test_splice_with_nothing_to_keep_returns_the_repair_verbatim():
    after = "def test_fix():\n    assert 1 == 1\n"
    assert carry_forward.splice(BEFORE, after, []) == after


def test_splice_of_an_unparseable_repair_keeps_the_previous_file():
    assert carry_forward.splice(BEFORE, "def test_fix(:\n", ["test_keep"]) == BEFORE
