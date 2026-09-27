"""The closed disposition enum and its invariant.

Normative source: METRICS.md, metric 9. Every candidate has exactly one
terminal disposition drawn from this enum. There is never a second,
independent counter: metric 9 is this enum grouped, and "never-healed" is
this enum filtered.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

#: The closed enum, in report order. Adding a member is a breaking change to
#: every published number and must be a versioned event.
DISPOSITIONS: List[str] = [
    "passed",
    "collect-error",
    "build-failure",
    "still-failing",
    "still-flaky",
    "no-progress-abort",
    "no-coverage-increase",
    "timeout",
    "no-output",
    "weakened-rejected",
]

MEANINGS: Dict[str, str] = {
    "passed": "survived the terminal 5-run gate",
    "collect-error": "its file failed to collect",
    "build-failure": "failed the project's collect/build command",
    "still-failing": "failed after all k repair rounds",
    "still-flaky": "failed >=1 of 5 in the terminal gate",
    "no-progress-abort": "identical failure signature across two rounds",
    "no-coverage-increase": "passed but covered nothing new",
    "timeout": "exceeded the per-candidate time limit",
    "no-output": "the model emitted nothing usable",
    "weakened-rejected": "rejected by the assertion-weakening guard",
}

#: Metric 9's "never-healed" view: entered at least one repair round and did
#: not survive. A filter over the same enum, never a separate tally.
NEVER_HEALED: List[str] = [
    "still-failing",
    "still-flaky",
    "no-progress-abort",
    "weakened-rejected",
]

#: Dispositions that count as a surviving test.
SURVIVING: List[str] = ["passed"]


class InvariantViolation(AssertionError):
    """`dispositions sum to N` did not hold."""


def tally(dispositions: Iterable[str]) -> Dict[str, int]:
    """Group dispositions into a dense ledger with every enum member present.

    Every member is present even at zero: a disposition that never fires is
    evidence too, and a table whose rows appear and disappear between runs
    cannot be diffed.
    """
    counts = {d: 0 for d in DISPOSITIONS}
    for d in dispositions:
        if d not in counts:
            raise InvariantViolation(
                "disposition %r is not in the closed enum %r" % (d, DISPOSITIONS)
            )
        counts[d] += 1
    return counts


def check_sum(counts: Dict[str, int], n: int) -> None:
    """Raise unless the ledger sums to N exactly.

    This is the assertion printed in every report and re-checked by
    `heal-yield verify`.
    """
    unknown = sorted(set(counts) - set(DISPOSITIONS))
    if unknown:
        raise InvariantViolation("ledger carries non-enum dispositions: %r" % unknown)
    total = sum(counts.values())
    if total != n:
        raise InvariantViolation(
            "dispositions sum to %d, N is %d (difference %+d)" % (total, n, total - n)
        )


def assertion_line(counts: Dict[str, int], n: int) -> str:
    """The literal line that goes in the report."""
    try:
        check_sum(counts, n)
    except InvariantViolation as exc:
        return "dispositions sum to N FAILED -- %s" % exc
    return "dispositions sum to N = %d OK" % n
