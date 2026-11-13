"""Reading one candidate's outcome out of pytest's node ids.

`run` and `verify` both used `nodeid.endswith(qualname)`. A parametrized test's
ids end in `[...]`, so it was always read as failed (published run r3 has one:
all five cases passed at round 0 and it was recorded `failed`). And a bare
suffix match let `test_a.py::test_x` read the outcome of `other_test_a.py::test_x`.
"""

import os
import sys

from heal_yield import verify
from heal_yield.candidates import Candidate
from heal_yield.loop import LoopConfig, run_loop, write_run
from heal_yield.pytest_harness import outcome_for
from heal_yield.runstore import RunStore

FILE = "heal_yield_generated/test_m.py"


def test_a_plain_node_id_matches():
    assert outcome_for({FILE + "::test_x": "passed"}, FILE, "test_x") == "passed"


def test_a_node_id_under_a_rootdir_prefix_still_matches():
    # Published runs record ids relative to a rootdir above the checkout.
    nid = ".heal-yield-work/cachetools/" + FILE + "::test_x"
    assert outcome_for({nid: "passed"}, FILE, "test_x") == "passed"


def test_a_parametrized_family_passes_only_if_every_case_passed():
    ids = {FILE + "::test_p[a]": "passed", FILE + "::test_p[b-1]": "passed"}
    assert outcome_for(ids, FILE, "test_p") == "passed"
    ids[FILE + "::test_p[c]"] = "failed"
    assert outcome_for(ids, FILE, "test_p") == "failed"


def test_a_parametrized_family_with_a_skipped_case_is_not_passed():
    ids = {FILE + "::test_p[a]": "passed", FILE + "::test_p[b]": "skipped"}
    assert outcome_for(ids, FILE, "test_p") == "skipped"


def test_a_method_in_a_test_class_matches_by_qualname():
    ids = {FILE + "::TestBox::test_x[1]": "passed"}
    assert outcome_for(ids, FILE, "TestBox.test_x") == "passed"
    assert outcome_for(ids, FILE, "test_x") == "failed"


def test_a_same_named_test_in_another_file_is_not_read():
    ids = {"heal_yield_generated/other_test_m.py::test_x": "passed",
           "heal_yield_generated/test_m.py::test_x": "failed"}
    assert outcome_for(ids, FILE, "test_x") == "failed"
    only_other = {"heal_yield_generated/other_test_m.py::test_x": "passed"}
    assert outcome_for(only_other, FILE, "test_x") == "failed"


def test_a_name_that_is_a_suffix_of_another_is_not_read():
    ids = {FILE + "::test_big_x": "passed"}
    assert outcome_for(ids, FILE, "test_x") == "failed"


def test_no_matching_id_is_failed():
    assert outcome_for({}, FILE, "test_x") == "failed"


def test_verify_uses_the_same_reading():
    c = Candidate("id", "m", FILE, "test_p", "test_p", 1, "").to_dict()
    ids = {FILE + "::test_p[a]": "passed", FILE + "::test_p[b]": "passed"}
    assert verify._outcome_from(ids, c) == "passed"


PARAM_GENERATOR = r'''
import argparse, json, os
p = argparse.ArgumentParser()
for a in ("--module", "--source-file", "--repo", "--out-dir", "--meta-out", "--feedback-file"):
    p.add_argument(a)
p.add_argument("--round", type=int)
a = p.parse_args()
os.makedirs(a.out_dir, exist_ok=True)
with open(os.path.join(a.out_dir, "test_tinypkg_core.py"), "w") as fh:
    fh.write("import pytest\nfrom tinypkg.core import double\n\n"
             "@pytest.mark.parametrize('n', [1, 2, 3])\n"
             "def test_double(n):\n    assert double(n) == n + n\n")
with open(a.meta_out, "w") as fh:
    json.dump({"model": "fake-param/1", "prompt_tokens": 0,
               "completion_tokens": 0, "usd": 0.0, "fixture": True}, fh)
'''


def test_a_parametrized_candidate_that_passes_is_recorded_passed_at_round_0(tmp_path):
    repo = tmp_path / "tinypkg"
    (repo / "tinypkg").mkdir(parents=True)
    (repo / "tinypkg" / "__init__.py").write_text("")
    (repo / "tinypkg" / "core.py").write_text("def double(n):\n    return n * 2\n")
    (repo / "conftest.py").write_text(
        "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n")
    script = tmp_path / "gen.py"
    script.write_text(PARAM_GENERATOR)
    store = RunStore(str(tmp_path / "run"))
    config = LoopConfig(
        repo_dir=str(repo), package="tinypkg", modules=["tinypkg.core"],
        module_files={"tinypkg.core": os.path.join("tinypkg", "core.py")},
        generator_command="%s %s" % (sys.executable, script), k=1,
        ceiling_usd=1.0, per_candidate_timeout_s=60.0, generator_timeout_s=120.0,
        per_candidate_coverage=False,
    )
    run = run_loop(config, store, sha="0" * 40, manifest_version="test")
    write_run(store, run)
    (cand,) = run["candidates"]
    assert cand["disposition"] == "passed", cand["rounds"]
    assert cand["healed_at_round"] == 0
    assert verify.verify_run(store.root)["ok"]
