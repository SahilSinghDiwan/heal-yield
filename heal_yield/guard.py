"""The assertion-weakening guard (METRICS.md metric 10).

The named false-positive failure mode of autonomous repair is that the model
makes the test pass by making it test nothing. This guard compares a
candidate's source before and after a repair round and rejects the repair when
it deleted the test, weakened an assertion to a tautology, or added a
skip/xfail marker.

It is reported even when it is zero.
"""

from __future__ import annotations

import ast
from typing import Dict, List, Optional, Tuple

TAUTOLOGY_MESSAGE = "assertion is a constant truth"
DELETED_MESSAGE = "candidate was deleted by the repair"
SKIPPED_MESSAGE = "repair added a skip/xfail marker"
STRIPPED_MESSAGE = "repair removed assertions without adding any"


def _find(tree: ast.AST, qualname: str):
    parts = qualname.split(".")
    body = getattr(tree, "body", [])
    node = None
    for part in parts:
        node = None
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if item.name == part:
                    node = item
                    break
        if node is None:
            return None
        body = node.body
    return node


def _decorator_names(node) -> List[str]:
    names = []
    for dec in getattr(node, "decorator_list", []):
        cur = dec.func if isinstance(dec, ast.Call) else dec
        parts = []
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        names.append(".".join(reversed(parts)))
    return names


def _is_tautology(test: ast.AST) -> bool:
    """True when the asserted expression can never be false."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    # `assert x == x`, `assert x is x`
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        if isinstance(test.ops[0], (ast.Eq, ast.Is)):
            try:
                return ast.dump(test.left) == ast.dump(test.comparators[0])
            except Exception:
                return False
    return False


def _assertions(node) -> Tuple[int, int]:
    """(total assertions, tautological assertions) inside a function body."""
    total = 0
    taut = 0
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            total += 1
            if _is_tautology(sub.test):
                taut += 1
        elif isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name.startswith("assert") or name in ("raises", "warns"):
                total += 1
    return total, taut


def _calls_skip(node) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            parts = []
            cur = func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            dotted = ".".join(reversed(parts))
            if dotted.endswith("pytest.skip") or dotted.endswith("pytest.xfail"):
                return True
    return False


def inspect_repair(
    before_source: str, after_source: Optional[str], qualname: str
) -> Dict[str, object]:
    """Judge one repair of one candidate.

    Returns `{"rejected": bool, "reason": str|None}`. A source that no longer
    parses is *not* a weakening -- it is a collect failure, and conflating the
    two would inflate the guard's count with ordinary breakage.
    """
    if after_source is None:
        return {"rejected": True, "reason": DELETED_MESSAGE}

    try:
        after_tree = ast.parse(after_source)
    except SyntaxError:
        return {"rejected": False, "reason": None}
    try:
        before_tree = ast.parse(before_source)
    except SyntaxError:
        before_tree = None

    after_node = _find(after_tree, qualname)
    if after_node is None:
        return {"rejected": True, "reason": DELETED_MESSAGE}

    decorators = _decorator_names(after_node)
    before_decorators: List[str] = []
    before_node = _find(before_tree, qualname) if before_tree is not None else None
    if before_node is not None:
        before_decorators = _decorator_names(before_node)
    for dec in decorators:
        if ("skip" in dec or "xfail" in dec) and dec not in before_decorators:
            return {"rejected": True, "reason": SKIPPED_MESSAGE}
    if _calls_skip(after_node) and not (before_node is not None and _calls_skip(before_node)):
        return {"rejected": True, "reason": SKIPPED_MESSAGE}

    after_total, after_taut = _assertions(after_node)
    if after_total > 0 and after_taut == after_total:
        return {"rejected": True, "reason": TAUTOLOGY_MESSAGE}

    if before_node is not None:
        before_total, _ = _assertions(before_node)
        effective_after = after_total - after_taut
        if before_total > 0 and effective_after == 0:
            return {"rejected": True, "reason": STRIPPED_MESSAGE}

    return {"rejected": False, "reason": None}
