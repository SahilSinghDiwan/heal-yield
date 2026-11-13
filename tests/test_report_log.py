"""Parsing pytest's --report-log into one outcome per node id."""

import json

from heal_yield.pytest_harness import _parse_report_log


def _write(tmp_path, records):
    path = tmp_path / "log.jsonl"
    path.write_text("\n".join(
        json.dumps(dict({"$report_type": "TestReport"}, **r)) for r in records) + "\n")
    return str(path)


def _phases(nodeid, setup, call, teardown="passed"):
    out = [{"nodeid": nodeid, "when": "setup", "outcome": setup}]
    if call is not None:
        out.append({"nodeid": nodeid, "when": "call", "outcome": call})
    out.append({"nodeid": nodeid, "when": "teardown", "outcome": teardown})
    return out


def test_a_skip_inside_the_test_body_is_skipped_not_passed(tmp_path):
    # pytest.skip() in the body: setup passed, call skipped.
    path = _write(tmp_path, _phases("t.py::test_x", "passed", "skipped"))
    assert _parse_report_log(path) == {"t.py::test_x": "skipped"}


def test_a_skip_marker_is_skipped(tmp_path):
    path = _write(tmp_path, _phases("t.py::test_x", "skipped", None))
    assert _parse_report_log(path) == {"t.py::test_x": "skipped"}


def test_a_pass_is_passed(tmp_path):
    path = _write(tmp_path, _phases("t.py::test_x", "passed", "passed"))
    assert _parse_report_log(path) == {"t.py::test_x": "passed"}


def test_a_failure_in_any_phase_is_failed_and_stays_failed(tmp_path):
    path = _write(tmp_path, _phases("t.py::a", "passed", "failed")
                  + _phases("t.py::b", "passed", "passed", teardown="failed")
                  + _phases("t.py::c", "failed", None))
    assert _parse_report_log(path) == {"t.py::a": "failed", "t.py::b": "failed",
                                       "t.py::c": "failed"}
