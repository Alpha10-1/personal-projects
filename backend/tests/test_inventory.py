"""The repository inventory, and the filter that keeps suggestions honest.

Most of these are about one thing: **nothing already built may be proposed
as missing.** That is the requirement the feature exists to meet, and the
way it fails is silent -- a plausible suggestion for something finished last
week, which teaches you to stop reading them.

So the filter is tested from both sides: that it drops what exists, and
that it does not drop what does not.
"""

import subprocess

import pytest

from app import inventory


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A small but realistic project: an API, a model, a page, a test."""
    root = tmp_path / "demo"
    write(
        root / "app" / "main.py",
        '"""The HTTP layer.\n\nRoutes and nothing else."""\n\n'
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n\n"
        '@router.get("/widgets")\n'
        "def list_widgets(limit: int = 50):\n"
        "    return []\n\n\n"
        '@router.post("/widgets")\n'
        "def create_widget():\n"
        "    return {}\n",
    )
    write(
        root / "app" / "models.py",
        "import os\n\nDATABASE_URL = os.getenv('PP_DATABASE_URL')\n\n\n"
        "class Widget:\n"
        '    __tablename__ = "widgets"\n',
    )
    write(
        root / "src" / "components" / "WidgetList.js",
        "/** Shows the widgets. */\nexport function WidgetList() {\n  return null;\n}\n",
    )
    write(root / "src" / "app" / "page.js", "export default function Home() {}\n")
    write(root / "tests" / "test_main.py", "def test_widgets():\n    pass\n")
    write(root / "README.md", "# demo\n")
    write(root / "package.json", '{"name": "demo"}\n')
    write(root / "build" / "junk.js", "// generated\n")
    write(root / ".gitignore", "build/\n")

    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


# --- what the scan finds --------------------------------------------------


def test_it_reads_the_routes_with_their_methods(repo):
    routes = {(r["method"], r["path"]) for r in inventory.scan(repo)["routes"]}
    assert routes == {("GET", "/widgets"), ("POST", "/widgets")}


def test_it_finds_the_tables(repo):
    assert inventory.scan(repo)["tables"] == ["widgets"]


def test_it_finds_the_configuration_it_reads(repo):
    assert "PP_DATABASE_URL" in inventory.scan(repo)["env_vars"]


def test_it_separates_pages_from_components(repo):
    scanned = inventory.scan(repo)
    assert scanned["pages"] == ["src/app/page.js"]
    assert scanned["components"] == ["src/components/WidgetList.js"]


def test_it_counts_tests_separately_from_source(repo):
    counts = inventory.scan(repo)["counts"]
    assert counts["test_files"] == 1
    assert counts["source_files"] > counts["test_files"]


def test_it_ignores_what_git_ignores(repo):
    """Using the index rather than walking means build output is excluded
    exactly as the repository's author decided it should be."""
    paths = {m["path"] for m in inventory.scan(repo)["modules"]}
    assert not any("build/" in p for p in paths)


def test_it_carries_each_module_s_own_description(repo):
    modules = {m["path"]: m for m in inventory.scan(repo)["modules"]}
    assert modules["app/main.py"]["docstring"] == "The HTTP layer."
    assert modules["src/components/WidgetList.js"]["docstring"] == "Shows the widgets."


def test_a_test_file_contributes_no_symbols(repo):
    paths = {m["path"] for m in inventory.scan(repo)["modules"]}
    assert "tests/test_main.py" not in paths


def test_the_prompt_names_the_routes_and_the_modules(repo):
    prompt = inventory.as_prompt(inventory.scan(repo))
    assert "GET /widgets" in prompt
    assert "app/main.py" in prompt
    assert "list_widgets" in prompt


def test_the_prompt_says_when_it_was_cut_short(repo, monkeypatch):
    prompt = inventory.as_prompt(inventory.scan(repo), max_chars=200)
    assert "cut off here" in prompt
    assert "do not assume it is missing" in prompt


def test_a_folder_that_is_not_a_repository_yields_nothing_rather_than_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert inventory.tracked_files(plain) == []


# --- the filter: dropping what is already there --------------------------


def gap(title, look_for):
    return {"title": title, "why": "because", "look_for": look_for}


def test_a_gap_naming_an_existing_function_is_dropped(repo):
    result = inventory.verify(repo, [gap("Add a widget listing", ["list_widgets"])])
    assert result["gaps"] == []
    [dropped] = result["already_done"]
    assert "list_widgets" in dropped["already_done_because"]


def test_a_gap_naming_an_existing_table_is_dropped(repo):
    result = inventory.verify(repo, [gap("Store widgets", ["widgets"])])
    assert result["gaps"] == []
    assert "table" in result["already_done"][0]["already_done_because"]


def test_a_gap_naming_an_existing_config_key_is_dropped(repo):
    result = inventory.verify(repo, [gap("Make the database configurable", ["PP_DATABASE_URL"])])
    assert result["gaps"] == []
    assert "configuration variable" in result["already_done"][0]["already_done_because"]


def test_a_gap_naming_an_existing_file_is_dropped(repo):
    result = inventory.verify(repo, [gap("Build a widget list UI", ["WidgetList.js"])])
    assert result["gaps"] == []
    assert "already exists" in result["already_done"][0]["already_done_because"]


def test_a_gap_naming_an_existing_route_is_dropped(repo):
    result = inventory.verify(repo, [gap("Expose widgets over HTTP", ["/widgets"])])
    assert result["gaps"] == []


def test_one_matching_term_is_enough_to_drop_it(repo):
    """Deliberately strict. Suggesting what is already built is the failure
    this exists to prevent, so any evidence of it counts."""
    result = inventory.verify(
        repo, [gap("Something", ["nowhere_at_all", "list_widgets", "also_absent"])]
    )
    assert result["gaps"] == []


def test_a_genuine_gap_survives(repo):
    result = inventory.verify(
        repo, [gap("Add rate limiting", ["rate_limit", "slowapi", "throttle"])]
    )
    assert result["already_done"] == []
    [kept] = result["gaps"]
    assert kept["verified"] is True
    assert kept["checked"] == ["rate_limit", "slowapi", "throttle"]


def test_a_dropped_gap_is_reported_rather_than_hidden(repo):
    """A filter nobody can see is one nobody can correct."""
    result = inventory.verify(
        repo,
        [gap("Add rate limiting", ["throttle"]), gap("List widgets", ["list_widgets"])],
    )
    assert len(result["gaps"]) == 1
    assert len(result["already_done"]) == 1
    assert result["already_done"][0]["title"] == "List widgets"


def test_a_gap_with_nothing_to_check_is_kept_but_marked(repo):
    result = inventory.verify(repo, [{"title": "Vague", "why": "x", "look_for": []}])
    [kept] = result["gaps"]
    assert kept["verified"] is False


@pytest.mark.parametrize("term", ["a", "to", "the"])
def test_a_term_too_short_to_mean_anything_is_not_treated_as_evidence(repo, term):
    built = inventory.index(repo)
    assert inventory.already_there(repo, term, built) is None


def test_a_match_only_in_a_test_file_does_not_count_as_built(repo):
    """A name that appears only in a test is a test for something that does
    not exist yet, which is the opposite of evidence that it does."""
    write(repo / "tests" / "test_future.py", "def test_rate_limiting_someday():\n    pass\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "wip")
    built = inventory.index(repo)
    assert inventory.already_there(repo, "test_rate_limiting_someday", built) is None


# --- the weaker warning ---------------------------------------------------
#
# The exact-term check only works if the model guesses the right
# identifier. On a real run it proposed "configure protected file patterns
# per project" against a codebase that already had `protected_paths` and
# `protected_patterns`, and the gap survived because it had searched for
# `pp-agent-ignore` instead. This is the second line: a warning, never a
# discard, because it is imprecise on purpose.


def test_identifiers_are_split_into_whole_words():
    assert inventory.words_of("protected_patterns") == {"protected", "patterns"}
    assert inventory.words_of("FeedbackUpdate") == {"feedback", "update"}
    assert inventory.words_of("MAX_READ_BYTES") == {"max", "read", "bytes"}


def test_a_word_inside_another_word_is_not_a_match():
    """`backup` is not in `FeedbackUpdate`, however much a substring search
    would like it to be."""
    assert "backup" not in inventory.words_of("FeedbackUpdate")


@pytest.fixture
def wordy(repo):
    write(
        repo / "app" / "agent.py",
        "DEFAULT_PROTECTED = ()\n\n\ndef protected_patterns(raw):\n    return []\n",
    )
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "agent")
    return inventory.index(repo)


def test_a_distinctive_word_surfaces_what_already_exists(wordy):
    found = inventory.related("Configure protected file patterns per project", wordy)
    assert "DEFAULT_PROTECTED" in found
    assert "protected_patterns" in found


def test_a_word_in_too_many_definitions_is_not_evidence(repo):
    for n in range(8):
        write(repo / "app" / f"m{n}.py", f"def widget_thing_{n}():\n    pass\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "many")
    built = inventory.index(repo)
    assert inventory.related("Improve the widget experience", built) == []


def test_filler_words_in_a_title_are_ignored(wordy):
    assert inventory.related("Expose a configure option", wordy) == []


def test_the_warning_never_discards_a_gap(repo, wordy):
    """It is imprecise by design, so it may only warn.

    Here the title matches `protected_patterns` on a word, but the terms
    the model asked to be checked are genuinely absent -- so the gap stays,
    carrying the warning.
    """
    result = inventory.verify(
        repo,
        [
            {
                "title": "Configure protected file patterns per project",
                "why": "x",
                "look_for": ["pp_agent_ignore", "agent_config_file"],
            }
        ],
        wordy,
    )
    assert result["already_done"] == []
    assert len(result["gaps"]) == 1
    assert result["gaps"][0]["possibly_related"]


def test_a_kept_gap_carries_the_warning(repo, wordy):
    result = inventory.verify(
        repo,
        [{"title": "Configure protected file patterns", "why": "x", "look_for": ["pp_agent_ignore"]}],
        wordy,
    )
    [kept] = result["gaps"]
    assert kept["verified"] is True
    assert "protected_patterns" in kept["possibly_related"]
