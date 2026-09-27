"""The mechanical module-selection rule (manifest `module_selection_rule`).

Applied identically to every repo, never hand-picked, so that a reader can
derive the exact module list from the pinned SHA alone. Hand-picking would
reintroduce the discretion that SHA-pinning removed.

Rule: take the 5 source modules with the highest statement count, where a
module is a .py file under the package root, excluding __init__.py,
__main__.py, _compat*/compat*/_version*/version*, anything under a tests/ or
test/ path, and any module with fewer than 20 statements. Ties by path,
ascending.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import re
from typing import Dict, List, Optional

DEFAULT_TOP_N = 5
MIN_STATEMENTS = 20

EXCLUDED_BASENAMES = ("__init__.py", "__main__.py", "conftest.py", "setup.py")
EXCLUDED_PATTERNS = ("_compat*", "compat*", "_version*", "version*")
EXCLUDED_PATH_PARTS = ("tests", "test", "testing", ".tox", ".venv", "build", "docs")

# Nodes coverage.py counts as statements: every simple and compound statement
# header. This mirrors coverage.py's own arc-free statement definition closely
# enough to be reproducible without importing the target package.
_NOT_A_STATEMENT = (ast.Module, ast.expr, ast.arguments, ast.arg, ast.keyword,
                    ast.comprehension, ast.alias, ast.withitem)


def count_statements(source: str) -> int:
    """Count executable statements in a module without importing it."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, _NOT_A_STATEMENT):
            continue
        if isinstance(node, ast.stmt):
            # A bare docstring expression is not an executable statement to
            # coverage.py.
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    continue
            n += 1
        elif isinstance(node, (ast.ExceptHandler,)):
            n += 1
    return n


def _excluded(rel_path: str) -> Optional[str]:
    parts = rel_path.split(os.sep)
    base = parts[-1]
    if base in EXCLUDED_BASENAMES:
        return "excluded basename"
    for part in parts[:-1]:
        if part in EXCLUDED_PATH_PARTS:
            return "under an excluded directory (%s)" % part
    for pattern in EXCLUDED_PATTERNS:
        if fnmatch.fnmatch(base, pattern + ".py") or fnmatch.fnmatch(base, pattern):
            return "matches excluded pattern %r" % pattern
    if base.startswith("test_") or base.endswith("_test.py"):
        return "is a test module"
    return None


def find_package_root(repo_dir: str, package_name: str) -> str:
    """Locate the importable package directory inside a checkout.

    Handles both flat (`<repo>/<pkg>/`) and src (`<repo>/src/<pkg>/`) layouts,
    and the single-module case (`<repo>/<pkg>.py`).
    """
    normalized = package_name.replace("-", "_")
    for candidate in (
        os.path.join(repo_dir, "src", normalized),
        os.path.join(repo_dir, normalized),
        os.path.join(repo_dir, "src", package_name),
        os.path.join(repo_dir, package_name),
    ):
        if os.path.isdir(candidate):
            return candidate
    for candidate in (
        os.path.join(repo_dir, "src", normalized + ".py"),
        os.path.join(repo_dir, normalized + ".py"),
    ):
        if os.path.isfile(candidate):
            return os.path.dirname(candidate)
    raise LookupError(
        "no package directory for %r under %r" % (package_name, repo_dir)
    )


def import_path(package_root: str, module_path: str, package_name: str) -> str:
    rel = os.path.relpath(module_path, package_root)
    dotted = re.sub(r"\.py$", "", rel).replace(os.sep, ".")
    if os.path.basename(package_root).replace("-", "_") == package_name.replace("-", "_"):
        return "%s.%s" % (package_name.replace("-", "_"), dotted)
    return dotted


def select_modules(
    repo_dir: str, package_name: str, top_n: int = DEFAULT_TOP_N
) -> Dict[str, object]:
    """Apply the rule and return the selection *plus its whole audit trail*.

    The rejected modules and the reason each was rejected are returned, not
    just the winners: a selection a reader cannot re-derive is a hand-pick with
    extra steps.
    """
    package_root = find_package_root(repo_dir, package_name)
    considered: List[Dict[str, object]] = []
    for dirpath, dirnames, filenames in os.walk(package_root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_PATH_PARTS]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, package_root)
            reason = _excluded(rel)
            with open(abs_path, "r", errors="replace") as fh:
                statements = count_statements(fh.read())
            if reason is None and statements < MIN_STATEMENTS:
                reason = "fewer than %d statements (%d)" % (MIN_STATEMENTS, statements)
            considered.append(
                {
                    "path": os.path.relpath(abs_path, repo_dir),
                    "module": import_path(package_root, abs_path, package_name),
                    "statements": statements,
                    "excluded_because": reason,
                }
            )

    eligible = [c for c in considered if c["excluded_because"] is None]
    eligible.sort(key=lambda c: (-c["statements"], c["path"]))
    selected = eligible[:top_n]
    return {
        "package": package_name,
        "package_root": os.path.relpath(package_root, repo_dir),
        "rule": {
            "top_n": top_n,
            "min_statements": MIN_STATEMENTS,
            "excluded_basenames": list(EXCLUDED_BASENAMES),
            "excluded_patterns": list(EXCLUDED_PATTERNS),
            "excluded_path_parts": list(EXCLUDED_PATH_PARTS),
            "tie_break": "path ascending",
        },
        "selected": selected,
        "considered": considered,
    }
