"""Checking out a pinned benchmark repo and applying the baseline rule.

Two things happen here that a reader must be able to re-derive from the SHA
alone: the checkout is pinned to the manifest's commit, and the target
modules' existing tests are deleted before the run so that every target starts
from the same baseline. Both are recorded in `metadata.yaml`.

Deleting the maintainers' tests is not a claim to improve on them. It exists so
that heal yield is not confounded by how well-tested the package already was.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, List


class CheckoutError(Exception):
    pass


def _git(args: List[str], cwd: str) -> str:
    proc = subprocess.run(
        ["git"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900
    )
    out = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise CheckoutError("git %s failed:\n%s" % (" ".join(args), out[:4000]))
    return out


def clone_pinned(url: str, sha: str, dest: str) -> str:
    """Clone and hard-pin to the manifest SHA. Refuses to run on anything else."""
    if os.path.isdir(os.path.join(dest, ".git")):
        _git(["fetch", "--all", "--tags"], dest)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        parent = os.path.dirname(os.path.abspath(dest))
        _git(["clone", url, os.path.basename(dest)], parent)
    _git(["checkout", "--force", sha], dest)
    _git(["clean", "-fdx"], dest)
    head = _git(["rev-parse", "HEAD"], dest).strip()
    if head != sha:
        raise CheckoutError("HEAD is %s, manifest pins %s" % (head, sha))
    return head


def _module_stem(module_path: str) -> str:
    return re.sub(r"\.py$", "", os.path.basename(module_path))


def apply_baseline_rule(repo_dir: str, module_files: Dict[str, str]) -> Dict:
    """Delete the existing tests that target the modules under test.

    Conservative on purpose: a test file is removed only when its name
    references a target module, or when it lives in a test directory named
    after one. Wholesale deletion of the suite would change the baseline for
    modules that are not under test and make the coverage delta meaningless.
    """
    stems = {_module_stem(p) for p in module_files.values()}
    removed: List[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_dir):
        if ".git" in dirpath.split(os.sep):
            continue
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            if not (fn.startswith("test_") or fn.endswith("_test.py")):
                continue
            stem = re.sub(r"^test_|_test\.py$|\.py$", "", fn)
            if stem in stems or any(s and s in fn for s in stems):
                path = os.path.join(dirpath, fn)
                os.remove(path)
                removed.append(os.path.relpath(path, repo_dir))
    return {
        "rule": "target modules' existing tests deleted before the run",
        "disclaimer": "NOT a claim to improve on the maintainers' suites",
        "target_module_stems": sorted(stems),
        "removed": sorted(removed),
        "removed_count": len(removed),
    }


def wipe_generated(repo_dir: str, generated_dir: str) -> None:
    path = os.path.join(repo_dir, generated_dir)
    if os.path.isdir(path):
        shutil.rmtree(path)
