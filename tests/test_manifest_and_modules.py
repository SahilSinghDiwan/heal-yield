import pytest

from heal_yield import manifest as manifest_mod
from heal_yield import modules


def test_the_declared_set_loads_and_is_fully_pinned():
    m = manifest_mod.load()
    assert len(m.repos) == 12
    for r in m.repos:
        assert len(r.sha) == 40
        assert r.tier in ("headline", "anchor")


def test_v1_runs_exactly_the_three_repos_named_before_any_token_was_spent():
    m = manifest_mod.load()
    assert sorted(r.name for r in m.v1_repos()) == ["cachetools", "packaging", "textdistance"]


def test_anchors_are_never_blended_into_the_headline_average():
    m = manifest_mod.load()
    assert m.policy["headline_average_over"] == "headline"
    assert sorted(r.name for r in m.anchors()) == ["dataclasses-json", "python-string-utils"]


def test_a_repo_outside_the_declared_set_is_refused():
    m = manifest_mod.load()
    with pytest.raises(manifest_mod.ManifestError) as exc:
        m.get("something-i-just-thought-of")
    assert "anti-cherry-picking" in str(exc.value)


def test_a_truncated_run_may_not_be_a_headline_repetition():
    m = manifest_mod.load()
    assert m.policy["truncated_runs_may_be_a_headline_rep"] is False
    assert m.policy["repetitions"] == 5


# ------------------------------------------------------------------- modules


def _pkg(tmp_path, files):
    root = tmp_path / "proj"
    pkg = root / "proj"
    pkg.mkdir(parents=True)
    for name, body in files.items():
        path = pkg / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return root


def _statements(n):
    return "\n".join("x%d = %d" % (i, i) for i in range(n)) + "\n"


def test_the_rule_picks_by_statement_count_not_by_hand(tmp_path):
    root = _pkg(tmp_path, {
        "__init__.py": _statements(90),
        "big.py": _statements(60),
        "small.py": _statements(5),
        "mid.py": _statements(30),
        "_version.py": _statements(50),
    })
    result = modules.select_modules(str(root), "proj", top_n=5)
    selected = [s["module"] for s in result["selected"]]
    assert selected == ["proj.big", "proj.mid"]


def test_every_rejection_carries_its_reason_so_the_list_is_re_derivable(tmp_path):
    root = _pkg(tmp_path, {
        "__init__.py": _statements(90),
        "small.py": _statements(5),
        "_version.py": _statements(50),
        "big.py": _statements(60),
    })
    result = modules.select_modules(str(root), "proj")
    reasons = {c["path"].split("/")[-1]: c["excluded_because"] for c in result["considered"]}
    assert "excluded basename" in reasons["__init__.py"]
    assert "fewer than 20 statements" in reasons["small.py"]
    assert "excluded pattern" in reasons["_version.py"]
    assert reasons["big.py"] is None


def test_ties_break_by_path_ascending(tmp_path):
    root = _pkg(tmp_path, {
        "__init__.py": "",
        "zeta.py": _statements(40),
        "alpha.py": _statements(40),
    })
    result = modules.select_modules(str(root), "proj", top_n=2)
    assert [s["module"] for s in result["selected"]] == ["proj.alpha", "proj.zeta"]


def test_a_src_layout_is_found(tmp_path):
    root = tmp_path / "proj"
    pkg = root / "src" / "proj"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(_statements(40))
    result = modules.select_modules(str(root), "proj")
    assert [s["module"] for s in result["selected"]] == ["proj.core"]


def test_docstrings_are_not_counted_as_statements():
    assert modules.count_statements('"""doc"""\nx = 1\n') == 1
