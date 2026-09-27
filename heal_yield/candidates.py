"""Candidate extraction: N = one generated test function.

METRICS.md, "The unit": one `test_*` function is one candidate; a
`@pytest.mark.parametrize` family collapses to the single function that
declares it; a file that fails to collect counts every test function in it as
`collect-error` and N does not shrink.

Extraction is therefore a purely syntactic operation on the emitted source and
must work on a file that pytest cannot import. Nothing here executes the
generated code.
"""

from __future__ import annotations

import ast
import hashlib
import os
from typing import Dict, List, Optional


class Candidate(object):
    """One generated test function."""

    __slots__ = ("id", "module", "file", "name", "qualname", "lineno", "source_sha256")

    def __init__(self, id, module, file, name, qualname, lineno, source_sha256):
        self.id = id
        self.module = module
        self.file = file
        self.name = name
        self.qualname = qualname
        self.lineno = lineno
        self.source_sha256 = source_sha256

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "module": self.module,
            "file": self.file,
            "name": self.name,
            "qualname": self.qualname,
            "lineno": self.lineno,
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Candidate":
        return cls(
            d["id"],
            d["module"],
            d["file"],
            d["name"],
            d["qualname"],
            d["lineno"],
            d["source_sha256"],
        )

    @property
    def nodeid(self) -> str:
        """The pytest node id, used to select exactly this candidate."""
        return "%s::%s" % (self.file, self.qualname.replace(".", "::"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iter_test_functions(tree: ast.AST):
    """Yield (name, qualname, lineno) for every test function in a module AST.

    Walks classes one level deep, which is what pytest collects
    (`TestFoo::test_bar`). Nested functions inside a test are not candidates:
    pytest does not collect them.
    """
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                yield node.name, node.name, node.lineno
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if sub.name.startswith("test"):
                        yield sub.name, "%s.%s" % (node.name, sub.name), sub.lineno


def extract_from_source(
    source: str, module: str, file_path: str, unparseable_id: Optional[str] = None
) -> List[Candidate]:
    """Extract candidates from generated source.

    A file that does not even parse still produces one candidate, carrying
    disposition `collect-error` upstream -- otherwise a syntactically broken
    emission would silently shrink N, which is the exact failure METRICS.md
    forbids.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        cid = unparseable_id or "%s::<unparseable>" % file_path
        return [
            Candidate(
                id=cid,
                module=module,
                file=file_path,
                name="<unparseable>",
                qualname="<unparseable>",
                lineno=0,
                source_sha256=_sha(source),
            )
        ]

    out = []
    seen = set()
    for name, qualname, lineno in _iter_test_functions(tree):
        if qualname in seen:
            # Redefinition: pytest keeps the last one, so does the ledger.
            continue
        seen.add(qualname)
        out.append(
            Candidate(
                id="%s::%s" % (file_path, qualname),
                module=module,
                file=file_path,
                name=name,
                qualname=qualname,
                lineno=lineno,
                source_sha256=_sha(source),
            )
        )
    return out


def extract_from_dir(directory: str, module: str, root: Optional[str] = None) -> List[Candidate]:
    """Extract every candidate from every `test_*.py` under `directory`."""
    root = root or directory
    found: List[Candidate] = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for fn in sorted(filenames):
            if not (fn.startswith("test_") and fn.endswith(".py")):
                continue
            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, root)
            with open(abs_path, "r") as fh:
                src = fh.read()
            found.extend(extract_from_source(src, module, rel))
    found.sort(key=lambda c: (c.file, c.lineno, c.qualname))
    return found
