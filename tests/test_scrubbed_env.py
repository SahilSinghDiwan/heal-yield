"""Generated test code must not run with the host's secrets in its environment.

LLM-written tests are untrusted code. `run_pytest` and the coverage runs used
to hand them a copy of `os.environ`, API keys included.
"""

import os

from heal_yield import coverage_delta
from heal_yield.pytest_harness import run_pytest, scrubbed_env

SECRET = "sk-test-not-a-real-key-0000"

PROBE = '''
import os


def test_probe():
    leaked = sorted(k for k, v in os.environ.items() if v == "%s")
    with open(os.path.join(os.path.dirname(__file__), "leaked.txt"), "w") as fh:
        fh.write(",".join(leaked))
    assert "PATH" in os.environ
''' % SECRET


def _set_secrets(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY",
                 "SOME_RANDOM_VAR"):
        monkeypatch.setenv(name, SECRET)


def test_the_test_environment_is_an_allowlist(monkeypatch):
    _set_secrets(monkeypatch)
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/x")
    env = scrubbed_env()
    assert SECRET not in env.values()
    for kept in ("PATH", "LC_ALL", "CONDA_PREFIX"):
        assert kept in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert scrubbed_env({"EXTRA": "1"})["EXTRA"] == "1", "a caller's explicit env is kept"


def test_run_pytest_does_not_pass_secrets_to_generated_tests(tmp_path, monkeypatch):
    _set_secrets(monkeypatch)
    (tmp_path / "test_probe.py").write_text(PROBE)
    res = run_pytest(cwd=str(tmp_path), targets=["test_probe.py"],
                     artifact_prefix=str(tmp_path / "art" / "probe"), timeout_s=60)
    assert res.outcomes.get("test_probe.py::test_probe") == "passed", res.stdout
    assert (tmp_path / "leaked.txt").read_text() == ""


def test_coverage_runs_do_not_pass_secrets_to_generated_tests(tmp_path, monkeypatch):
    _set_secrets(monkeypatch)
    (tmp_path / "test_probe.py").write_text(PROBE)
    (tmp_path / "mod.py").write_text("X = 1\n")
    coverage_delta.measure(
        repo=str(tmp_path), targets=["test_probe.py"], include_modules=["mod.py"],
        out_xml=str(tmp_path / "cov" / "c.xml"), out_json=str(tmp_path / "cov" / "c.json"),
        timeout_s=120,
    )
    assert (tmp_path / "leaked.txt").read_text() == ""
    assert os.environ["ANTHROPIC_API_KEY"] == SECRET, "the harness's own env is untouched"
