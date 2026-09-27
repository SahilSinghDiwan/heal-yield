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
            elif when == "call" and nodeid not in outcomes:
                outcomes[nodeid] = outcome
            elif nodeid not in outcomes:
                outcomes.setdefault(nodeid, outcome)
    return outcomes


def run_pytest(
    cwd: str,
    targets: List[str],
    artifact_prefix: str,
    timeout_s: float,
    extra_args: Optional[List[str]] = None,
    collect_only: bool = False,
    env: Optional[Dict[str, str]] = None,
    python: Optional[str] = None,
) -> ExecutionResult:
    """Run pytest and write every raw byte of it next to the run.

    `artifact_prefix` is an absolute path stem; this writes `<stem>.txt` and,
    for a real execution, `<stem>.jsonl` (the report log `verify` reads).
    """
    os.makedirs(os.path.dirname(artifact_prefix), exist_ok=True)
    report_log = artifact_prefix + ".jsonl"
    args = [python or sys.executable, "-m", "pytest"]
    args += COLLECT_ARGS if collect_only else RUN_ARGS
    if not collect_only:
        args += ["--report-log", report_log]
    args += list(extra_args or [])
    args += list(targets)

    run_env = dict(os.environ)
    run_env.update(env or {})
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"

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
        fh.write("$ " + " ".join(args) + "\n")
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
