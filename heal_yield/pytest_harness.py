"""Running pytest and capturing raw output as the run's evidence.

Every execution writes its stdout, stderr and return code to the run
directory. `heal-yield verify` re-derives each candidate's pass/fail from
*those files* and never re-executes anything, so the shape of what is captured
here is load-bearing: the machine-readable `--report-log` JSONL is the record
of truth, and the human-readable text is captured beside it for a reader.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

#: Written into metadata.yaml; `verify` checks the stored run used these.
COLLECT_ARGS = ["--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"]
RUN_ARGS = ["-q", "--no-header", "-p", "no:cacheprovider", "-p", "no:randomly"]


class ExecutionResult(object):
    """One pytest invocation, as stored and as re-read by `verify`."""

    __slots__ = ("returncode", "stdout", "stderr", "duration_s", "timed_out", "outcomes")

    def __init__(self, returncode, stdout, stderr, duration_s, timed_out, outcomes):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration_s = duration_s
        self.timed_out = timed_out
        self.outcomes = outcomes

    def to_dict(self) -> Dict:
        return {
            "returncode": self.returncode,
            "duration_s": round(self.duration_s, 4),
            "timed_out": self.timed_out,
            "outcomes": self.outcomes,
        }


def _parse_report_log(path: str) -> Dict[str, str]:
    """Read pytest's `--report-log` JSONL into {nodeid: outcome}.

    A test that errors during setup and a test that fails during the call
    phase are both failures here; collapsing them is safe because the
    disposition enum draws the distinction from the collect step, not from
    this one.
    """
    outcomes: Dict[str, str] = {}
    if not os.path.exists(path):
        return outcomes
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("$report_type") != "TestReport":
                continue
            nodeid = rec.get("nodeid")
            outcome = rec.get("outcome")
            when = rec.get("when")
            if not nodeid:
                continue
            if outcome in ("failed", "error"):
                outcomes[nodeid] = "failed"
            elif outcomes.get(nodeid) == "failed":
                continue
            elif when == "call":
                # The call phase is the test body. It overrides setup's
                # `passed`: a pytest.skip() in the body is setup passed,
                # call skipped, and must not be read as a pass.
                outcomes[nodeid] = outcome
            else:
                outcomes.setdefault(nodeid, outcome)
    return outcomes


#: Variables the generated tests' process inherits from the host. Everything
#: else -- API keys, tokens, cloud credentials -- is left out: the code being
#: run was written by a model, and it needs none of it.
ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TZ",
    "LANG", "LANGUAGE",
    "TMPDIR", "TEMP", "TMP",
    "PYTHONPATH", "PYTHONHASHSEED", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONHOME",
    "VIRTUAL_ENV",
    # Windows needs these to start a Python process at all.
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR",
)
ENV_ALLOWED_PREFIXES = ("LC_", "CONDA_")


def scrubbed_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment for a process that runs generated tests.

    An allowlist, not a denylist: a secret under a name nobody thought of is
    excluded by default. `extra` is the caller's own, explicit additions.
    """
    env = {
        k: v for k, v in os.environ.items()
        if k in ENV_ALLOWLIST or k.startswith(ENV_ALLOWED_PREFIXES)
    }
    env.update(extra or {})
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def display_args(args: List[str], root: Optional[str]) -> List[str]:
    """`args` with any path under `root` rewritten relative to `root`."""
    if not root:
        return list(args)
    root = os.path.abspath(root)
    out = []
    for a in args:
        if os.path.isabs(a) and (a == root or a.startswith(root + os.sep)):
            out.append(os.path.relpath(a, root))
        else:
            out.append(a)
    return out


def _strip_params(nodeid: str) -> str:
    """`a.py::test_p[x-1]` -> `a.py::test_p`. The `[` is searched after the path."""
    sep = nodeid.find("::")
    if sep < 0:
        return nodeid
    bracket = nodeid.find("[", sep)
    return nodeid if bracket < 0 else nodeid[:bracket]


def outcome_for(outcomes: Dict[str, str], file: str, qualname: str) -> str:
    """One candidate's outcome, read from `{nodeid: outcome}`.

    The match is on the whole node path -- file and qualname -- after the
    parameter suffix is removed, so a parametrized family is found and a
    same-named test in another file is not. pytest may report ids relative
    to a rootdir above the checkout, so a `<prefix>/` before the file is
    allowed. A family passes only if every case passed; any failed case makes
    it failed; otherwise the first other outcome (e.g. `skipped`) is returned.
    A candidate with no matching id is failed.
    """
    target = "%s::%s" % (file, qualname.replace(".", "::"))
    found: List[str] = []
    for nodeid, outcome in outcomes.items():
        base = _strip_params(nodeid)
        if base == target or base.endswith("/" + target):
            found.append(outcome)
    if not found:
        return "failed"
    if "failed" in found:
        return "failed"
    for outcome in found:
        if outcome != "passed":
            return outcome
    return "passed"


def run_pytest(
    cwd: str,
    targets: List[str],
    artifact_prefix: str,
    timeout_s: float,
    extra_args: Optional[List[str]] = None,
    collect_only: bool = False,
    env: Optional[Dict[str, str]] = None,
    python: Optional[str] = None,
    display_root: Optional[str] = None,
) -> ExecutionResult:
    """Run pytest and write every raw byte of it next to the run.

    `artifact_prefix` is an absolute path stem; this writes `<stem>.txt` and,
    for a real execution, `<stem>.jsonl` (the report log `verify` reads).
    `display_root` is the run directory: paths under it are written into the
    stored header relative to it, so a published run carries no host path
    for its own artifacts. The command that runs is unchanged.
    """
    os.makedirs(os.path.dirname(artifact_prefix), exist_ok=True)
    report_log = artifact_prefix + ".jsonl"
    args = [python or sys.executable, "-m", "pytest"]
    args += COLLECT_ARGS if collect_only else RUN_ARGS
    if not collect_only:
        args += ["--report-log", report_log]
    args += list(extra_args or [])
    args += list(targets)

    run_env = scrubbed_env(env)

    import time

    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
        stdout = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or b"").decode("utf-8", "replace")
        stderr = (exc.stderr or b"").decode("utf-8", "replace")
        returncode = -9
    duration = time.time() - started

    with open(artifact_prefix + ".txt", "w") as fh:
        fh.write("$ " + " ".join(display_args(args, display_root)) + "\n")
        fh.write("# cwd: %s\n" % cwd)
        fh.write("# returncode: %s\n" % returncode)
        fh.write("# timed_out: %s\n" % timed_out)
        fh.write("# duration_s: %.4f\n" % duration)
        fh.write("--- stdout ---\n")
        fh.write(stdout)
        fh.write("\n--- stderr ---\n")
        fh.write(stderr)

    outcomes = {} if collect_only else _parse_report_log(report_log)
    return ExecutionResult(returncode, stdout, stderr, duration, timed_out, outcomes)


def read_execution(artifact_prefix: str) -> Tuple[Optional[int], bool, Dict[str, str]]:
    """Re-read a stored execution without running anything.

    This is the `verify` path: the same pass/fail decision, derived only from
    bytes on disk.
    """
    txt = artifact_prefix + ".txt"
    if not os.path.exists(txt):
        return None, False, {}
    returncode = None
    timed_out = False
    with open(txt, "r", errors="replace") as fh:
        for line in fh:
            if line.startswith("# returncode: "):
                try:
                    returncode = int(line.split(": ", 1)[1].strip())
                except ValueError:
                    returncode = None
            elif line.startswith("# timed_out: "):
                timed_out = line.split(": ", 1)[1].strip() == "True"
            elif line.startswith("--- stdout ---"):
                break
    return returncode, timed_out, _parse_report_log(artifact_prefix + ".jsonl")


def collect_error_files(result: ExecutionResult) -> List[str]:
    """Files named in a failed `--collect-only`."""
    files: List[str] = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        line = line.strip()
        if line.startswith("ERROR ") or "errors during collection" in line:
            parts = line.split()
            for part in parts:
                if part.endswith(".py"):
                    files.append(part)
    return sorted(set(files))
