"""Cost accounting at per-repair-round resolution (METRICS.md metric 8).

CoverUp published a total. This publishes the split -- what round 0, 1, 2 and 3
each cost -- so a reader can see whether k = 3 pays for itself. That is cheap
to record and it is the second uncommon disclosure sitting next to the first.

The per-run USD ceiling is *enforced* here. A tripped ceiling is a loud,
recorded outcome: the run is published in full with `status: truncated` and can
never be one of the 5 headline repetitions.
"""

from __future__ import annotations

from typing import Dict, List, Optional


class CeilingTripped(Exception):
    """The per-run USD ceiling was reached. The run truncates, loudly."""

    def __init__(self, spent: float, ceiling: float):
        self.spent = spent
        self.ceiling = ceiling
        super(CeilingTripped, self).__init__(
            "per-run USD ceiling tripped: spent $%.4f of $%.2f" % (spent, ceiling)
        )


class UsageRecord(object):
    """One model call, attributed to the repair round that made it."""

    __slots__ = ("round", "module", "prompt_tokens", "completion_tokens", "usd", "model")

    def __init__(self, round, module, prompt_tokens, completion_tokens, usd, model):
        self.round = round
        self.module = module
        self.prompt_tokens = int(prompt_tokens)
        self.completion_tokens = int(completion_tokens)
        self.usd = float(usd)
        self.model = model

    def to_dict(self) -> Dict:
        return {
            "round": self.round,
            "module": self.module,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "usd": self.usd,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "UsageRecord":
        return cls(
            d["round"],
            d["module"],
            d["prompt_tokens"],
            d["completion_tokens"],
            d["usd"],
            d.get("model"),
        )


class CostLedger(object):
    """Append-only usage records plus the ceiling check."""

    def __init__(self, ceiling_usd: float):
        self.ceiling_usd = float(ceiling_usd)
        self.records: List[UsageRecord] = []
        self.tripped = False

    @property
    def spent(self) -> float:
        return sum(r.usd for r in self.records)

    def record(self, usage: UsageRecord) -> None:
        self.records.append(usage)

    def check_ceiling(self) -> None:
        """Raise `CeilingTripped` when the run has spent its budget.

        Checked *after* a call is recorded, never before: refusing to record a
        call that was already billed would make the published cost lower than
        the real one, which is the one direction the number must never err.
        """
        if self.spent >= self.ceiling_usd:
            self.tripped = True
            raise CeilingTripped(self.spent, self.ceiling_usd)

    def would_exceed(self, estimate_usd: float) -> bool:
        return (self.spent + estimate_usd) >= self.ceiling_usd

    def by_round(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for r in self.records:
            key = str(r.round)
            bucket = out.setdefault(
                key, {"usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
            )
            bucket["usd"] += r.usd
            bucket["prompt_tokens"] += r.prompt_tokens
            bucket["completion_tokens"] += r.completion_tokens
            bucket["calls"] += 1
        return out

    def to_dict(self) -> Dict:
        return {
            "ceiling_usd": self.ceiling_usd,
            "tripped": self.tripped,
            "total_usd": round(self.spent, 6),
            "total_prompt_tokens": sum(r.prompt_tokens for r in self.records),
            "total_completion_tokens": sum(r.completion_tokens for r in self.records),
            "by_round": self.by_round(),
            "records": [r.to_dict() for r in self.records],
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "CostLedger":
        ledger = cls(d["ceiling_usd"])
        ledger.tripped = bool(d.get("tripped"))
        for rec in d.get("records", []):
            ledger.records.append(UsageRecord.from_dict(rec))
        return ledger


def derived(
    ledger: CostLedger, surviving_tests: int, net_new_lines: int
) -> Dict[str, Optional[float]]:
    """The two derived figures. `None` rather than a division by zero.

    A run that rescued nothing has no dollars-per-test; printing `0.00` or
    `inf` there would both be lies.
    """
    total = ledger.spent
    return {
        "usd_per_surviving_test": (total / surviving_tests) if surviving_tests else None,
        "usd_per_net_new_covered_line": (total / net_new_lines) if net_new_lines else None,
    }
