"""Reading a change, and reading a selection.

All of this is pattern matching over real text in a real repository, so the
tests are too: the failure mode worth catching is not "the regex compiled",
it is "a signature spread over twelve lines was reported as an ordinary
edit", which is exactly what happened the first time this ran against
`routes/tasks.py`.
"""

import subprocess

import pytest

from app import impact


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A small repository where one module is used by two others."""
    root = tmp_path / "demo"
    write(root / "core.py", "MAX_ROWS = 100\n\n\ndef fetch_rows(source):\n    return source[:MAX_ROWS]\n")
    write(root / "report.py", "from core import fetch_rows\n\n\ndef build():\n    return fetch_rows([1, 2])\n")
    write(root / "api.py", "from core import fetch_rows, MAX_ROWS\n\n\ndef handler():\n    return fetch_rows([]), MAX_ROWS\n")
    write(root / "tests" / "test_core.py", "from core import fetch_rows\n\n\ndef test_it():\n    assert fetch_rows([]) == []\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


# --- finding definitions --------------------------------------------------


def test_python_definitions_are_found_with_their_kind():
    text = "CAP = 5\n\n\nclass Thing:\n    def method(self):\n        pass\n\n\ndef top():\n    pass\n"
    found = {s.name: s.kind for s in impact.symbols(text, "a.py")}
    assert found == {"CAP": "constant", "Thing": "class", "method": "method", "top": "function"}


def test_javascript_definitions_are_found():
    text = "export const LIMIT = 5;\nexport function go() {}\nclass Widget {}\n"
    found = {s.name: s.kind for s in impact.symbols(text, "a.js")}
    assert found == {"LIMIT": "constant", "go": "function", "Widget": "class"}


def test_an_unknown_file_type_yields_nothing_rather_than_guessing():
    assert impact.symbols("# title\n\nsome prose\n", "README.md") == []


def test_a_definition_inside_a_comment_is_not_counted():
    text = "# def ghost():\n// def other():\ndef real():\n    pass\n"
    assert [s.name for s in impact.symbols(text, "a.py")] == ["real"]


def test_a_multi_line_signature_is_read_whole():
    """The one that mattered: a FastAPI route's parameters run down the page,
    and reading only the first line made adding one look like an edit."""
    text = "def list_tasks(\n    project_id: int = None,\n    q: str = None,\n):\n    pass\n"
    [symbol] = impact.symbols(text, "a.py")
    assert "project_id" in symbol.signature
    assert "q: str" in symbol.signature
    assert "\n" not in symbol.signature


def test_rewrapping_a_signature_is_not_a_signature_change():
    one_line = "def f(a, b):\n    pass\n"
    wrapped = "def f(\n    a,\n    b,\n):\n    pass\n"
    changes = impact.changed_symbols(one_line, wrapped, "a.py")
    assert [c.change for c in changes] != ["signature_changed"]


def test_symbol_at_returns_the_innermost():
    text = "class Outer:\n    def inner(self):\n        x = 1\n"
    assert impact.symbol_at(text, "a.py", 3).name == "inner"


# --- what changed ---------------------------------------------------------


def test_adding_a_parameter_is_a_signature_change():
    before = "def f(a):\n    return a\n"
    after = "def f(a, b=1):\n    return a\n"
    [change] = impact.changed_symbols(before, after, "a.py")
    assert change.change == "signature_changed"


def test_changing_a_body_is_a_modification_not_a_signature_change():
    before = "def f(a):\n    return a\n"
    after = "def f(a):\n    return a * 2\n"
    [change] = impact.changed_symbols(before, after, "a.py")
    assert change.change == "modified"


def test_added_and_removed_are_both_reported():
    before = "def gone():\n    pass\n"
    after = "def fresh():\n    pass\n"
    changes = {c.name: c.change for c in impact.changed_symbols(before, after, "a.py")}
    assert changes == {"gone": "removed", "fresh": "added"}


def test_the_most_serious_change_is_listed_first():
    before = "def kept(a):\n    pass\n\n\ndef gone():\n    pass\n"
    after = "def kept(a, b):\n    pass\n"
    changes = impact.changed_symbols(before, after, "a.py")
    assert [c.change for c in changes] == ["removed", "signature_changed"]


# --- the outline ----------------------------------------------------------


def test_removing_something_others_use_is_a_risk(repo):
    before = (repo / "core.py").read_text(encoding="utf-8")
    after = "MAX_ROWS = 100\n"
    outline = impact.assess(repo, "core.py", before, after)
    risks = [e for e in outline["effects"] if e["level"] == "risk"]
    assert risks and "fetch_rows" in risks[0]["text"]
    assert "report.py" in risks[0]["text"]


def test_removing_something_nothing_uses_is_reassuring(repo):
    write(repo / "lonely.py", "def unused_helper():\n    pass\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "add")
    outline = impact.assess(repo, "lonely.py", "def unused_helper():\n    pass\n", "")
    good = [e for e in outline["effects"] if e["level"] == "good"]
    assert any("nothing" in e["text"] for e in good)


def test_a_signature_change_names_the_callers(repo):
    before = (repo / "core.py").read_text(encoding="utf-8")
    after = before.replace("def fetch_rows(source):", "def fetch_rows(source, limit):")
    outline = impact.assess(repo, "core.py", before, after)
    text = " ".join(e["text"] for e in outline["effects"])
    assert "different arguments" in text
    assert "report.py" in text and "api.py" in text


def test_a_changed_shared_value_is_flagged(repo):
    before = (repo / "core.py").read_text(encoding="utf-8")
    after = before.replace("MAX_ROWS = 100", "MAX_ROWS = 10")
    outline = impact.assess(repo, "core.py", before, after)
    text = " ".join(e["text"] for e in outline["effects"])
    assert "shared value MAX_ROWS changed" in text
    assert "imports this module" in text


def test_tests_that_cover_the_change_are_reported_as_good(repo):
    before = (repo / "core.py").read_text(encoding="utf-8")
    after = before.replace("def fetch_rows(source):", "def fetch_rows(source, limit):")
    outline = impact.assess(repo, "core.py", before, after)
    good = " ".join(e["text"] for e in outline["effects"] if e["level"] == "good")
    assert "tests/test_core.py" in good


def test_a_deletion_is_always_a_risk(repo):
    outline = impact.assess(repo, "core.py", "MAX_ROWS = 1\n", None)
    assert outline["action"] == "delete"
    assert any(e["level"] == "risk" for e in outline["effects"])


def test_a_new_file_is_a_note_not_a_risk(repo):
    outline = impact.assess(repo, "brand_new.py", None, "def hello():\n    pass\n")
    assert outline["action"] == "create"
    assert not [e for e in outline["effects"] if e["level"] == "risk"]


def test_a_protected_path_is_called_out(repo):
    outline = impact.assess(
        repo, "core.py", "X = 1\n", "X = 2\n", protected=["core.py"]
    )
    assert any("protected" in e["text"] for e in outline["effects"])


def test_a_rewrite_is_distinguished_from_an_edit(repo):
    before = "\n".join(f"line {i}" for i in range(20))
    after = "\n".join(f"other {i}" for i in range(20))
    outline = impact.assess(repo, "notes.txt", before, after)
    assert any("rewrite rather than an edit" in e["text"] for e in outline["effects"])


def test_a_comment_only_change_says_no_definition_moved(repo):
    before = "def f():\n    # old\n    pass\n"
    after = "def f():\n    # new\n    pass\n"
    outline = impact.assess(repo, "a.py", before, after)
    # The body changed, so f is modified -- but nothing was added or removed.
    assert [s["change"] for s in outline["symbols"]] == ["modified"]


def test_the_outline_states_what_it_cannot_see(repo):
    outline = impact.assess(repo, "core.py", "X = 1\n", "X = 2\n")
    assert any("pattern matching, not by parsing" in limit for limit in outline["limits"])


def test_a_file_type_it_cannot_read_says_so_first(repo):
    outline = impact.assess(repo, "data.csv", "a,b\n", "a,c\n")
    assert "not one this can read" in outline["limits"][0]


def test_effects_are_ordered_worst_first(repo):
    before = (repo / "core.py").read_text(encoding="utf-8")
    outline = impact.assess(repo, "core.py", before, "MAX_ROWS = 100\n")
    levels = [e["level"] for e in outline["effects"]]
    assert levels == sorted(levels, key=lambda level: impact.LEVELS.index(level))


# --- references -----------------------------------------------------------


def test_a_name_too_common_to_search_says_so_rather_than_flooding(repo):
    found = impact.references(repo, "id")
    assert found.searched is False
    assert "too short or too common" in found.reason


def test_references_separate_tests_from_the_rest(repo):
    found = impact.references(repo, "fetch_rows", exclude="core.py")
    assert set(found.files) == {"api.py", "report.py"}
    assert found.tests == ["tests/test_core.py"]


def test_the_defining_file_is_excluded(repo):
    found = impact.references(repo, "fetch_rows", exclude="core.py")
    assert "core.py" not in found.files


# --- explaining a selection ----------------------------------------------


def test_explain_names_the_function_the_cursor_is_in(repo):
    result = impact.explain(repo, "core.py", 5, 5)
    assert result["enclosing"]["name"] == "fetch_rows"


def test_explain_reports_who_would_be_affected(repo):
    result = impact.explain(repo, "core.py", 4, 5)
    text = " ".join(c["text"] for c in result["consequences"])
    assert "affects 2 other file(s)" in text
    assert "report.py" in text


def test_explain_reports_the_tests_that_cover_it(repo):
    result = impact.explain(repo, "core.py", 4, 5)
    good = " ".join(c["text"] for c in result["consequences"] if c["level"] == "good")
    assert "tests/test_core.py" in good


def test_explain_identifies_the_shared_values_a_selection_reads(repo):
    result = impact.explain(repo, "core.py", 5, 5)
    assert [s["name"] for s in result["shared_values"]] == ["MAX_ROWS"]
    assert any("every file that imports it" in c["text"] for c in result["consequences"])


def test_explain_says_when_something_is_contained(repo):
    write(repo / "solo.py", "def only_here():\n    return 1\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "solo")
    result = impact.explain(repo, "solo.py", 1, 2)
    assert any(
        "contained here" in c["text"] and c["level"] == "good"
        for c in result["consequences"]
    )


def test_explain_clamps_a_selection_past_the_end_of_the_file(repo):
    result = impact.explain(repo, "core.py", 1, 9999)
    assert result["end_line"] <= len((repo / "core.py").read_text().splitlines())


def test_explain_reports_the_imports_the_selection_uses(repo):
    result = impact.explain(repo, "report.py", 1, 5)
    assert "core" in result["imports_used"]


def test_explain_says_it_did_not_ask_a_model(repo):
    result = impact.explain(repo, "core.py", 1, 2)
    assert any("not by a model" in limit for limit in result["limits"])


# --- not crying wolf ------------------------------------------------------
#
# A real selection over `Overlay.read` in agent.py reported "affects 25
# other file(s)", because `read` appears everywhere. A panel that does that
# on an ordinary change is one you learn to ignore, which is worse than not
# having it.


@pytest.mark.parametrize("name", ["read", "write", "update", "path", "status", "get"])
def test_a_bare_english_verb_is_not_searched(repo, name):
    found = impact.references(repo, name)
    assert found.searched is False
    assert "too short or too common" in found.reason


@pytest.mark.parametrize("name", ["fetch_rows", "check_outgoing", "MAX_ROWS"])
def test_a_compound_name_still_is(repo, name):
    assert impact.references(repo, name).searched is True


def test_a_method_carries_a_caveat_about_name_collisions(repo):
    found = impact.references(repo, "fetch_rows", kind="method")
    assert found.searched is True
    assert "another class with a method called the same thing" in found.caveat


def test_a_module_level_function_carries_no_such_caveat(repo):
    assert impact.references(repo, "fetch_rows", kind="function").caveat is None


def test_the_caveat_reaches_the_explanation(repo):
    write(repo / "klass.py", "class Store:\n    def load_rows(self):\n        return 1\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "klass")
    result = impact.explain(repo, "klass.py", 2, 3)
    assert result["enclosing"]["kind"] == "method"
    assert any("method called the same thing" in c["text"] for c in result["consequences"])


def test_a_name_that_cannot_be_searched_says_so_rather_than_claiming_safety(repo):
    """The dangerous failure: reporting "nothing references this" when the
    truth is "I did not look"."""
    write(repo / "common.py", "def read():\n    return 1\n")
    result = impact.explain(repo, "common.py", 1, 2)
    text = " ".join(c["text"] for c in result["consequences"])
    assert "too short or too common" in text
    assert "contained here" not in text
