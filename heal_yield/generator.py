"""The generator seam: a subprocess contract, not a Python entry point.

Ticket 06 decision 9. A Python entry point would exclude Copilot, Playwright
`healer` and everything else CLI-shaped -- i.e. most of the field worth
measuring. So a generator is *any command* that writes test files to a
directory.

The contract, in full:

    <command> --module <dotted.path>
              --source-file <path to the module under test>
              --repo <path to the checkout>
              --out-dir <directory to write test_*.py into>
              --round <0..k>
              --meta-out <path to write a JSON usage record>
              [--feedback-file <path to captured build/test output>]

The generator must:
  * write zero or more `test_*.py` files into `--out-dir`;
  * write `--meta-out` as JSON with at least
    `{"model": str, "prompt_tokens": int, "completion_tokens": int, "usd": float}`;
  * exit 0 on success.

Everything else -- prompts, retries, which model -- is the generator's
business. The harness measures; it does not prescribe.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Dict, List, Optional

from .cost import UsageRecord


class GeneratorError(Exception):
    pass


class GeneratorResult(object):
    __slots__ = ("returncode", "usage", "stdout", "stderr", "meta")

    def __init__(self, returncode, usage, stdout, stderr, meta):
        self.returncode = returncode
        self.usage = usage
        self.stdout = stdout
        self.stderr = stderr
        self.meta = meta


class Generator(object):
    """A generator command, invoked per module per round."""

    def __init__(self, command: str, timeout_s: float):
        self.command = command
        self.argv: List[str] = shlex.split(command)
        if not self.argv:
            raise GeneratorError("empty generator command")
        self.timeout_s = timeout_s

    def invoke(
        self,
        module: str,
        source_file: str,
        repo: str,
        out_dir: str,
        round_index: int,
        artifact_dir: str,
        feedback_file: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> GeneratorResult:
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(artifact_dir, exist_ok=True)
        meta_out = os.path.join(artifact_dir, "usage.json")
        argv = list(self.argv) + [
            "--module", module,
            "--source-file", source_file,
            "--repo", repo,
            "--out-dir", out_dir,
            "--round", str(round_index),
            "--meta-out", meta_out,
        ]
        if feedback_file:
            argv += ["--feedback-file", feedback_file]

        run_env = dict(os.environ)
        run_env.update(env or {})
        # The generator is told where to put its transcript. A generator that
        # ignores this simply publishes no transcript, and the run says so.
        run_env["HEAL_YIELD_ARTIFACT_DIR"] = artifact_dir

        try:
            proc = subprocess.run(
                argv,
                cwd=repo,
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_s,
            )
            stdout = proc.stdout.decode("utf-8", "replace")
            stderr = proc.stderr.decode("utf-8", "replace")
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode("utf-8", "replace")
            stderr = (exc.stderr or b"").decode("utf-8", "replace")
            returncode = -9

        with open(os.path.join(artifact_dir, "generator.txt"), "w") as fh:
            fh.write("$ " + " ".join(argv) + "\n")
            fh.write("# returncode: %s\n" % returncode)
            fh.write("--- stdout ---\n" + stdout)
            fh.write("\n--- stderr ---\n" + stderr)

        meta: Dict = {}
        if os.path.exists(meta_out):
            try:
                with open(meta_out) as fh:
                    meta = json.load(fh)
            except ValueError:
                meta = {}

        usage = UsageRecord(
            round=round_index,
            module=module,
            prompt_tokens=meta.get("prompt_tokens", 0),
            completion_tokens=meta.get("completion_tokens", 0),
            usd=meta.get("usd", 0.0),
            model=meta.get("model"),
        )
        return GeneratorResult(returncode, usage, stdout, stderr, meta)
