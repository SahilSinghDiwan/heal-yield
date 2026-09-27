from heal_yield import candidates, guard

TWO_TESTS = """
import pytest

def test_alpha():
    assert 1 == 1

def test_beta():
    assert 2 == 2

def helper():
    return 3

class TestGamma:
    def test_inner(self):
        assert True
"""


def test_n_is_one_test_function_not_one_file():
    found = candidates.extract_from_source(TWO_TESTS, "m", "test_m.py")
    assert [c.qualname for c in found] == ["test_alpha", "test_beta", "TestGamma.test_inner"]


def test_parametrize_family_collapses_to_one_candidate():
    src = (
        "import pytest\n"
        "@pytest.mark.parametrize('x', [1, 2, 3])\n"
        "def test_p(x):\n"
        "    assert x\n"
    )
    found = candidates.extract_from_source(src, "m", "test_m.py")
    assert len(found) == 1


def test_an_unparseable_file_still_produces_a_candidate_so_n_does_not_shrink():
    found = candidates.extract_from_source("def test_x(:\n", "m", "test_m.py")
    assert len(found) == 1
    assert found[0].name == "<unparseable>"


def test_nodeid_addresses_exactly_one_candidate():
    found = candidates.extract_from_source(TWO_TESTS, "m", "pkg/test_m.py")
    ids = {c.qualname: c.nodeid for c in found}
    assert ids["test_alpha"] == "pkg/test_m.py::test_alpha"
    assert ids["TestGamma.test_inner"] == "pkg/test_m.py::TestGamma::test_inner"


# ------------------------------------------------------------------ the guard

BEFORE = "def test_a():\n    assert compute() == 42\n"


def test_guard_rejects_a_deleted_test():
    v = guard.inspect_repair(BEFORE, "def test_other():\n    assert 1\n", "test_a")
    assert v["rejected"] and v["reason"] == guard.DELETED_MESSAGE


def test_guard_rejects_a_tautology():
    v = guard.inspect_repair(BEFORE, "def test_a():\n    assert True\n", "test_a")
    assert v["rejected"] and v["reason"] == guard.TAUTOLOGY_MESSAGE


def test_guard_rejects_x_equals_x():
    after = "def test_a():\n    x = compute()\n    assert x == x\n"
    v = guard.inspect_repair(BEFORE, after, "test_a")
    assert v["rejected"] and v["reason"] == guard.TAUTOLOGY_MESSAGE


def test_guard_rejects_a_newly_added_skip_marker():
    after = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert compute() == 42\n"
    v = guard.inspect_repair(BEFORE, after, "test_a")
    assert v["rejected"] and v["reason"] == guard.SKIPPED_MESSAGE


def test_guard_rejects_stripping_all_assertions():
    v = guard.inspect_repair(BEFORE, "def test_a():\n    compute()\n", "test_a")
    assert v["rejected"] and v["reason"] == guard.STRIPPED_MESSAGE


def test_guard_accepts_a_real_repair():
    after = "def test_a():\n    assert compute() == 43\n"
    v = guard.inspect_repair(BEFORE, after, "test_a")
    assert not v["rejected"]


def test_a_syntax_error_is_a_collect_failure_not_a_weakening():
    v = guard.inspect_repair(BEFORE, "def test_a(:\n", "test_a")
    assert not v["rejected"]
