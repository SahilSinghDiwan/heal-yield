import pytest

from heal_yield import metrics
from heal_yield.cost import CeilingTripped, CostLedger, UsageRecord, derived


def _candidate(cid, disposition, healed=None, built=True):
    return {
        "id": cid,
        "module": "m",
        "file": "test_m.py",
        "name": cid,
        "qualname": cid,
        "lineno": 1,
        "source_sha256": "",
        "disposition": disposition,
        "healed_at_round": healed,
        "built_round_0": built,
        "rounds": [],
        "failure_signatures": [],
        "guard_reason": None,
    }


def _run(candidates, cost=None, coverage=None, status="complete"):
    return {
        "status": status,
        "config": {"k": 3},
        "candidates": candidates,
        "cost": cost or CostLedger(10.0).to_dict(),
        "coverage": coverage or {},
    }


def test_heal_yield_is_suppressed_below_a_denominator_of_ten():
    cands = [_candidate("t%d" % i, "passed", healed=0) for i in range(3)]
    cands += [_candidate("f%d" % i, "still-failing") for i in range(4)]
    m = metrics.compute(_run(cands))["metric_5_heal_yield"]
    assert m["pct"] is None
    assert "below the minimum" in m["suppressed_because"]


def test_heal_yield_uses_the_definition_from_metrics_md():
    # N = 20; 4 passed at round 0; 6 more rescued by repair.
    cands = [_candidate("p%d" % i, "passed", healed=0) for i in range(4)]
    cands += [_candidate("h%d" % i, "passed", healed=1) for i in range(6)]
    cands += [_candidate("x%d" % i, "still-failing") for i in range(10)]
    m = metrics.compute(_run(cands))
    assert m["n"] == 20
    assert m["metric_3_first_pass_green_rate"]["numerator"] == 4
    assert m["metric_4_post_heal_green_rate"]["numerator"] == 10
    # (10 - 4) / (20 - 4)
    assert m["metric_5_heal_yield"]["pct"] == pytest.approx(37.5)


def test_pass_at_round_zero_then_failing_the_gate_is_not_a_first_pass_success():
    cands = [_candidate("a", "still-flaky", healed=None)]
    cands += [_candidate("b%d" % i, "passed", healed=0) for i in range(2)]
    m = metrics.compute(_run(cands))
    assert m["metric_3_first_pass_green_rate"]["numerator"] == 2
    assert m["metric_9_dispositions"]["ledger"]["still-flaky"] == 1


def test_the_invariant_is_reported_in_the_metrics_block():
    m = metrics.compute(_run([_candidate("a", "passed", healed=0)]))
    assert m["metric_9_dispositions"]["invariant_ok"]
    assert "OK" in m["metric_9_dispositions"]["invariant_line"]


def test_metric_seven_is_declared_as_deferred_rather_than_omitted():
    m = metrics.compute(_run([_candidate("a", "passed", healed=0)]))
    assert "deferred" in m["metric_7_mutation_score"]


def test_the_guard_is_reported_even_when_zero():
    m = metrics.compute(_run([_candidate("a", "passed", healed=0)]))
    assert m["metric_10_assertion_weakening_guard"]["rejected"] == 0


def test_a_truncated_run_is_never_headline_eligible():
    m = metrics.compute(_run([_candidate("a", "passed", healed=0)], status="truncated"))
    assert m["headline_eligible"] is False


def test_a_fixture_run_is_never_headline_eligible():
    run = _run([_candidate("a", "passed", healed=0)])
    run["fixture"] = True
    assert metrics.compute(run)["headline_eligible"] is False


# --------------------------------------------------------------------- cost


def test_cost_is_attributed_per_repair_round():
    ledger = CostLedger(10.0)
    ledger.record(UsageRecord(0, "m", 100, 200, 1.0, "x"))
    ledger.record(UsageRecord(1, "m", 50, 60, 0.5, "x"))
    ledger.record(UsageRecord(1, "m", 50, 60, 0.5, "x"))
    by_round = ledger.by_round()
    assert by_round["0"]["usd"] == 1.0
    assert by_round["1"]["usd"] == 1.0
    assert by_round["1"]["calls"] == 2


def test_the_ceiling_trips_loudly_and_after_the_call_is_recorded():
    ledger = CostLedger(1.0)
    ledger.record(UsageRecord(0, "m", 1, 1, 1.25, "x"))
    with pytest.raises(CeilingTripped):
        ledger.check_ceiling()
    # The overspend is still in the published total; a cost may never read low.
    assert ledger.spent == 1.25
    assert ledger.tripped


def test_derived_costs_are_none_rather_than_a_division_by_zero():
    ledger = CostLedger(10.0)
    ledger.record(UsageRecord(0, "m", 1, 1, 2.0, "x"))
    d = derived(ledger, surviving_tests=0, net_new_lines=0)
    assert d["usd_per_surviving_test"] is None
    assert d["usd_per_net_new_covered_line"] is None


def test_a_ledger_round_trips_through_json():
    ledger = CostLedger(5.0)
    ledger.record(UsageRecord(2, "m", 7, 8, 0.25, "x"))
    again = CostLedger.from_dict(ledger.to_dict())
    assert again.spent == ledger.spent
    assert again.by_round() == ledger.by_round()
