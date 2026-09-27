"""Shared argument parsing for generators shipped with heal-yield.

A third-party generator does not import this -- the contract is the command
line, not this module. It exists so the two reference generators cannot drift
from each other.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Dict


def parse_args(argv=None, extend=None):
    """Parse the contract's arguments.

    `extend` is a callable given the parser so a generator can declare its own
    flags (a model id, a temperature) on top of the contract. Flags the
    harness does not know about are the generator's business -- that is the
    point of making the seam a command line rather than a Python entry point.
    """
    p = argparse.ArgumentParser(description="heal-yield generator")
    p.add_argument("--module", required=True)
    p.add_argument("--source-file", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--meta-out", required=True)
    p.add_argument("--feedback-file", default=None)
    if extend is not None:
        extend(p)
    return p.parse_args(argv)


def write_meta(path: str, model: str, prompt_tokens: int, completion_tokens: int,
               usd: float, extra: Dict = None) -> None:
    payload = {
        "model": model,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "usd": round(float(usd), 8),
    }
    payload.update(extra or {})
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def read_source(repo: str, source_file: str) -> str:
    path = source_file
    if not os.path.isabs(path):
        path = os.path.join(repo, source_file)
    with open(path, errors="replace") as fh:
        return fh.read()


def read_feedback(path) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, errors="replace") as fh:
        return fh.read()
