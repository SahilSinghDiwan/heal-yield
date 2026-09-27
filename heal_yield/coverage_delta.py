"""Coverage measurement, before and after (METRICS.md metric 6).

Line coverage and branch coverage are reported separately *and* as a combined
figure, never as one blended "coverage" number: in Python, branch coverage does
not subsume line coverage, so a single number hides which one moved.

Baseline is measured with the target modules' existing tests already removed
(the manifest's `baseline_rule`), so every target starts from the same place.
This is not a claim to improve on the maintainers' suites.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Dict, List, Optional
from xml.etree import ElementTree


class CoverageError(Exception):
    pass


def measure(
    repo: str,
    targets: List[str],
    include_modules: List[str],
    out_xml: str,
    out_json: str,
    timeout_s: float,
    python: Optional[str] = None,
) -> Dict:
    """Run pytest under coverage over `targets`, restricted to the modules under test.

    `targets` may be empty: that is the baseline with the module's tests
    deleted and nothing generated yet, and it must still produce a real
    measurement rather than an assumed zero, because some target lines are
    executed at import time by other packages' tests.
    """
    os.makedirs(os.path.dirname(out_xml), exist_ok=True)
    exe = python or sys.executable
    data_file = os.path.join(os.path.dirname(out_xml), ".coverage")
    env = dict(os.environ)
    env["COVERAGE_FILE"] = data_file
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    include = ",".join(include_modules) if include_modules else None
    base = [exe, "-m", "coverage", "run", "--branch"]
    if include:
        base += ["--include", include]
    base += ["-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
    # An empty `targets` means the baseline: whatever of the project's own
    # suite survived the baseline rule. That is the honest starting point --
    # the target modules' tests are gone, everything else still runs, and some
    # target lines are genuinely executed by other packages' tests.
    base += list(targets)

    subprocess.run(
        base, cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout_s,
    )
    subprocess.run(
        [exe, "-m", "coverage", "xml", "-o", out_xml],
        cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s,
    )
    if not os.path.exists(out_xml):
        # coverage.py refuses to write a report from an empty dataset. A
        # baseline with the target's tests deleted and nothing generated yet is
        # exactly that case, and it is a real measurement -- every statement
        # uncovered -- not a missing one. Write it from static analysis so the
        # run directory always carries a file `verify` can recompute from.
        write_zero_xml(repo, include_modules, out_xml)
    subprocess.run(
        [exe, "-m", "coverage", "json", "-o", out_json],
        cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s,
    )
    return summarise_xml(out_xml, include_modules)


def write_zero_xml(repo: str, include_modules: List[str], out_xml: str) -> None:
    """Emit a Cobertura XML in which every statement of every target is uncovered.

    Uses coverage.py's own parser, so the statement and branch totals are the
    same ones a real report would have used -- the zero is measured against the
    identical denominator, not asserted.
    """
    from coverage.parser import PythonParser

    lines_out = []
    for rel in include_modules:
        path = rel if os.path.isabs(rel) else os.path.join(repo, rel)
        parser = PythonParser(filename=path)
        parser.parse_source()
        exits = parser.exit_counts()
        rows = []
        for lineno in sorted(parser.statements):
            n_exits = exits.get(lineno, 1)
            if n_exits > 1:
                rows.append(
                    '        <line number="%d" hits="0" branch="true" '
                    'condition-coverage="0%% (0/%d)"/>' % (lineno, n_exits)
                )
            else:
                rows.append('        <line number="%d" hits="0"/>' % lineno)
        lines_out.append(
            '    <class filename="%s" name="%s">\n      <lines>\n%s\n      </lines>\n    </class>'
            % (rel, os.path.basename(rel), "\n".join(rows))
        )

    xml = (
        '<?xml version="1.0" ?>\n<coverage branch-rate="0" line-rate="0">\n'
        "  <packages>\n    <package name=\".\">\n      <classes>\n"
        + "\n".join(lines_out)
        + "\n      </classes>\n    </package>\n  </packages>\n</coverage>\n"
    )
    os.makedirs(os.path.dirname(os.path.abspath(out_xml)), exist_ok=True)
    with open(out_xml, "w") as fh:
        fh.write(xml)


def summarise_xml(path: str, include_modules: Optional[List[str]] = None) -> Dict:
    """Read a Cobertura XML into the three figures METRICS.md defines.

    `verify` calls this on the stored XML, so nothing here may depend on the
    checkout still existing.
    """
    tree = ElementTree.parse(path)
    root = tree.getroot()
    wanted = set(os.path.normpath(m) for m in (include_modules or []))

    lines_total = lines_covered = 0
    branches_total = branches_covered = 0
    covered_line_numbers: Dict[str, List[int]] = {}

    for cls in root.iter("class"):
        filename = os.path.normpath(cls.get("filename", ""))
        if wanted and filename not in wanted and not any(
            filename.endswith(os.sep + w) or filename == w for w in wanted
        ):
            continue
        hits: List[int] = []
        for line in cls.iter("line"):
            lines_total += 1
            hit = int(line.get("hits", "0"))
            if hit:
                lines_covered += 1
                hits.append(int(line.get("number", "0")))
            if line.get("branch") == "true":
                cond = line.get("condition-coverage", "")
                # e.g. "50% (1/2)"
                if "(" in cond and "/" in cond:
                    taken, total = cond.split("(", 1)[1].rstrip(")").split("/")
                    branches_total += int(total)
                    branches_covered += int(taken)
        if hits:
            covered_line_numbers[filename] = sorted(hits)

    return {
        "lines_total": lines_total,
        "lines_covered": lines_covered,
        "line_pct": (100.0 * lines_covered / lines_total) if lines_total else None,
        "branches_total": branches_total,
        "branches_covered": branches_covered,
        "branch_pct": (100.0 * branches_covered / branches_total) if branches_total else None,
        "line_and_branch_pct": _combined(
            lines_covered, lines_total, branches_covered, branches_total
        ),
        "covered_lines": covered_line_numbers,
    }


def _combined(lc: int, lt: int, bc: int, bt: int) -> Optional[float]:
    denom = lt + bt
    if not denom:
        return None
    return 100.0 * (lc + bc) / denom


def net_new_covered_lines(baseline: Dict, final: Dict) -> int:
    """Lines covered after that were not covered before.

    A set difference, not a subtraction of totals: tests can trade coverage of
    one line for another, and the subtraction would report that as zero change.
    """
    total = 0
    base = baseline.get("covered_lines") or {}
    fin = final.get("covered_lines") or {}
    for filename, lines in fin.items():
        before = set(base.get(filename, []))
        total += len(set(lines) - before)
    return total


def write_summary(path: str, payload: Dict) -> None:
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
