"""Provenance written by a run: the endpoint host, and no host paths for artifacts."""

import os
import sys

from heal_yield import cli
from heal_yield.generator import endpoint_host
from heal_yield.loop import LoopConfig, run_loop, write_run
from heal_yield.pytest_harness import display_args
from heal_yield.runstore import RunStore

TARGET = "def double(n):\n    return n * 2\n"

GEN = r'''
import argparse, json, os
p = argparse.ArgumentParser()
for a in ("--module", "--source-file", "--repo", "--out-dir", "--meta-out", "--feedback-file"):
    p.add_argument(a)
p.add_argument("--round", type=int)
a = p.parse_args()
os.makedirs(a.out_dir, exist_ok=True)
with open(os.path.join(a.out_dir, "test_tinypkg_core.py"), "w") as fh:
    fh.write("from tinypkg.core import double\n\n"
             "def test_ok():\n    assert double(2) == 4\n\n"
             "def test_bad():\n    assert double(2) == 5\n")
with open(a.meta_out, "w") as fh:
    json.dump({"model": "fake/1", "prompt_tokens": 0, "completion_tokens": 0,
               "usd": 0.0, "fixture": True,
               "base_url": "https://user:secret@llm.example.com:8443/v1?key=abc"}, fh)
'''


def test_endpoint_host_keeps_host_and_port_only():
    assert endpoint_host("https://api.anthropic.com") == "api.anthropic.com"
    assert endpoint_host("http://localhost:3001/") == "localhost:3001"
    assert endpoint_host("https://u:p@h.example:8443/v1?k=x") == "h.example:8443"
    assert endpoint_host(None) is None
    assert endpoint_host("not a url") is None


def test_display_args_rewrites_only_paths_under_the_run_dir(tmp_path):
    root = str(tmp_path / "run")
    args = ["/usr/bin/python", "--report-log", os.path.join(root, "raw", "x.jsonl"),
            str(tmp_path / "runner"), "t.py::test_a"]
    assert display_args(args, root) == [
        "/usr/bin/python", "--report-log", os.path.join("raw", "x.jsonl"),
        str(tmp_path / "runner"), "t.py::test_a"]


def test_a_run_records_the_endpoint_host_and_relative_artifact_paths(tmp_path):
    repo = tmp_path / "tinypkg"
    (repo / "tinypkg").mkdir(parents=True)
    (repo / "tinypkg" / "__init__.py").write_text("")
    (repo / "tinypkg" / "core.py").write_text(TARGET)
    (repo / "conftest.py").write_text(
        "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n")
    script = tmp_path / "gen.py"
    script.write_text(GEN)
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
    assert run["config"]["endpoints_reported_by_generator"] == ["llm.example.com:8443"]

    class Repo:
        name, repo, tag, license, tier = "tinypkg", "url", "v0", "MIT", "anchor"

    class Manifest:
        version, data = "test", {}

    meta = cli._metadata(run, Repo(), "0" * 40, Manifest())
    assert meta["model"]["endpoint"] == ["llm.example.com:8443"]
    assert "secret" not in repr(meta)

    leaks = []
    for sub in (("raw", "pytest"), ("raw", "model")):
        for dirpath, _d, files in os.walk(store.path(*sub)):
            for fn in files:
                if not fn.endswith(".txt"):
                    continue
                with open(os.path.join(dirpath, fn)) as fh:
                    header = fh.readline()
                if store.root in header:
                    leaks.append(os.path.join(dirpath, fn))
    assert not leaks, leaks
