"""Carry passing tests forward through a repair round, byte for byte.

A repair prompt asks the model for the complete corrected file. The model may
leave a passing test out, or rewrite it. Neither is a result about the passing
test: it was never asked to be repaired. So after each repair the harness
takes the model's file and puts every passing function back exactly as it was
in the previous file -- replaced in place when the repair still defines it,
appended when the repair dropped it.

Only the kept functions are restored. Module-level code (imports, helpers,
fixtures) is the repair's, so a passing test can still break when the repair
changes something it depends on; the loop re-runs every undecided candidate
after each repair, and that run is what records such a break.
"""

from __future__ import annotations

import ast
from typing import Dict, List, Optional, Sequence, Tuple


def _top(tree: ast.Module, name: str, kinds) -> Optional[ast.AST]:
    for node in tree.body:
        if isinstance(node, kinds) and node.name == name:
            return node
    return None


_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _locate(tree: ast.Module, qualname: str) -> Optional[ast.AST]:
    parts = qualname.split(".")
    if len(parts) == 1:
        return _top(tree, parts[0], _FUNC)
    if len(parts) == 2:
        cls = _top(tree, parts[0], ast.ClassDef)
        if cls is None:
            return None
        for sub in cls.body:
            if isinstance(sub, _FUNC) and sub.name == parts[1]:
                return sub
    return None


def _span(node: ast.AST) -> Tuple[int, int]:
    """0-based [start, end) line span, decorators included."""
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return start - 1, node.end_lineno


def _reindent(lines: List[str], from_col: int, to_col: int) -> List[str]:
    if from_col == to_col:
        return list(lines)
    out = []
    for ln in lines:
        stripped = ln[from_col:] if ln[:from_col].strip() == "" else ln.lstrip()
        out.append((" " * to_col + stripped) if ln.strip() else ln)
    return out


def _lines(source: str) -> List[str]:
    lines = source.splitlines(True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


def splice(before: str, after: str, keep: Sequence[str]) -> str:
    """The repaired file `after`, with each qualname in `keep` restored from `before`.

    If `after` does not parse there is nothing to splice into, and putting the
    previous file back is the only way to keep the passing tests; the failing
    ones then re-run in their previous form and are judged on that.
    """
    keep = [q for q in keep if q]
    if not keep:
        return after
    try:
        before_tree = ast.parse(before)
    except SyntaxError:
        return after
    try:
        after_tree = ast.parse(after)
    except SyntaxError:
        return before

    before_lines = _lines(before)
    after_lines = _lines(after)

    replacements: List[Tuple[int, int, List[str]]] = []
    append_top: List[List[str]] = []
    append_to_class: Dict[str, List[List[str]]] = {}
    new_classes: Dict[str, List[List[str]]] = {}

    for qualname in keep:
        old = _locate(before_tree, qualname)
        if old is None:
            continue
        o_start, o_end = _span(old)
        old_src = before_lines[o_start:o_end]
        new = _locate(after_tree, qualname)
        if new is not None:
            n_start, n_end = _span(new)
            replacements.append(
                (n_start, n_end, _reindent(old_src, old.col_offset, new.col_offset))
            )
            continue
        if "." not in qualname:
            append_top.append(old_src)
            continue
        cls_name = qualname.split(".")[0]
        if _top(after_tree, cls_name, ast.ClassDef) is not None:
            append_to_class.setdefault(cls_name, []).append(old_src)
        else:
            new_classes.setdefault(cls_name, []).append(old_src)

    # Methods to add into a class the repair still has: insert after its last line.
    for cls_name, chunks in append_to_class.items():
        cls = _top(after_tree, cls_name, ast.ClassDef)
        indent = cls.body[0].col_offset if cls.body else cls.col_offset + 4
        block: List[str] = []
        for chunk in chunks:
            col = len(chunk[0]) - len(chunk[0].lstrip())
            block += ["\n"] + _reindent(chunk, col, indent)
        replacements.append((cls.end_lineno, cls.end_lineno, block))

    merged = list(after_lines)
    for start, end, text in sorted(replacements, key=lambda r: r[0], reverse=True):
        merged[start:end] = text

    for chunk in append_top:
        merged += ["\n", "\n"] + chunk
    for cls_name, chunks in new_classes.items():
        old_cls = _top(before_tree, cls_name, ast.ClassDef)
        c_start = min([old_cls.lineno] + [d.lineno for d in old_cls.decorator_list]) - 1
        first = old_cls.body[0]
        first_start = _span(first)[0] if hasattr(first, "decorator_list") else first.lineno - 1
        header = before_lines[c_start:first_start]
        merged += ["\n", "\n"] + header
        for i, chunk in enumerate(chunks):
            merged += (["\n"] if i else []) + chunk
    return "".join(merged)


__all__ = ["splice"]
