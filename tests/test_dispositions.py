import pytest

from heal_yield import dispositions as disp


def test_every_member_has_a_meaning():
    assert set(disp.DISPOSITIONS) == set(disp.MEANINGS)


def test_never_healed_is_a_subset_of_the_enum():
    assert set(disp.NEVER_HEALED) <= set(disp.DISPOSITIONS)


def test_tally_is_dense_even_at_zero():
    counts = disp.tally(["passed", "passed", "still-failing"])
    assert counts["passed"] == 2
    assert counts["timeout"] == 0
    assert set(counts) == set(disp.DISPOSITIONS)


def test_tally_rejects_a_disposition_outside_the_enum():
    with pytest.raises(disp.InvariantViolation):
        disp.tally(["passed", "mostly-fine"])


def test_dispositions_must_sum_to_n():
    counts = disp.tally(["passed", "still-flaky"])
    disp.check_sum(counts, 2)
    with pytest.raises(disp.InvariantViolation) as exc:
        disp.check_sum(counts, 3)
    assert "difference -1" in str(exc.value)


def test_assertion_line_reports_the_failure_rather_than_raising():
    counts = disp.tally(["passed"])
    assert "FAILED" in disp.assertion_line(counts, 9)
    assert "OK" in disp.assertion_line(counts, 1)
