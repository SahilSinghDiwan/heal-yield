"""Repair feedback: every failing candidate's failure message reaches the model.

The loop kept the last 8,000 characters of each candidate's output, then the
reference generator kept only the last 12,000 characters of the whole file,
so candidates near the top of a long feedback file were cut out entirely.
"""

from heal_yield.generators import anthropic_gen
from heal_yield.loop import FEEDBACK_BUDGET_CHARS, render_feedback


def _artifact(name, filler):
    return (
        "$ /some/python -m pytest -q --report-log /very/long/path.jsonl x::%s\n"
        "# cwd: /somewhere\n# returncode: 1\n# timed_out: False\n# duration_s: 0.1\n"
        "--- stdout ---\nF\n=== FAILURES ===\n%s\n"
        "E       AssertionError: unique-failure-for-%s\n"
        "x.py:12: AssertionError\n=== short test summary info ===\n"
        "FAILED x.py::%s - AssertionError\n1 failed in 0.01s\n"
        "--- stderr ---\n" % (name, filler, name, name)
    )


def test_every_failing_candidate_keeps_its_failure_line_within_the_budget():
    filler = "    some traceback line that goes on and on\n" * 400  # ~17k chars each
    sections = [("x.py::test_%d" % i, _artifact("test_%d" % i, filler)) for i in range(12)]
    text = render_feedback(sections)
    for i in range(12):
        assert "### x.py::test_%d" % i in text
        assert "unique-failure-for-test_%d" % i in text
    assert len(text) <= FEEDBACK_BUDGET_CHARS + 12 * 400, len(text)


def test_short_output_is_kept_whole_minus_the_harness_header():
    text = render_feedback([("x.py::test_a", _artifact("test_a", "short"))])
    assert "unique-failure-for-test_a" in text
    assert "=== FAILURES ===" in text
    assert "--report-log" not in text, "the harness's own command line is not feedback"


def test_the_reference_generator_does_not_truncate_the_feedback_again():
    filler = "    some traceback line that goes on and on\n" * 400
    sections = [("x.py::test_%d" % i, _artifact("test_%d" % i, filler)) for i in range(12)]
    feedback = render_feedback(sections)
    prompt = anthropic_gen.build_prompt("pkg.mod", "def f(): pass\n", feedback, 1)
    for i in range(12):
        assert "unique-failure-for-test_%d" % i in prompt
