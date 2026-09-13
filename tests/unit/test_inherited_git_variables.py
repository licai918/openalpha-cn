"""A hook's `GIT_*` variables never reach a test, so the repository they name is never written.

`D13`. Another lane gated each commit with `git rebase --exec "sh gate.sh"`, and git exports
`GIT_DIR` -- and, around its hooks, `GIT_WORK_TREE` and `GIT_INDEX_FILE` -- to the commands it
starts. The tests that build a throwaway repository then aimed `git init`, `git config`, `git add`
and `git commit` at the real one: its shared `.git/config` came back with `core.bare=true`,
`user.email=test@example.com`, `user.name=Test` and a `core.excludesFile` inside pytest's temporary
tree, and its detached HEAD had gained a test commit with `file.txt` in the index. A pre-commit or
pre-push hook that runs this suite is the same environment, which is how the defect reaches a
user's repository and not only this project's.

`tests/conftest.py`'s `pytest_configure` removes every `GIT_*` variable before a single module is
collected. A hook's environment exists only outside a run, so this file measures from outside: a
child pytest whose `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and `GIT_OBJECT_DIRECTORY` name a
sentinel repository under `tmp_path`, and nothing else.

* The tests the D13 census found starting git (`GIT_LAUNCHING_TESTS`) all pass in it, and every
  byte of the sentinel -- config, HEAD, refs, index, objects -- is what it was.
* The same child without `tests/conftest.py` rewrites the sentinel's config the way the real
  repository's was rewritten, which is what makes the first a measurement.
* No phase of an item sees an inherited variable: not module import, not a session-, module- or
  class-scoped fixture, not the test. A function-scoped autouse fixture -- the obvious spelling --
  reaches the last of those only, `V2-P5-031`'s finding about the offline guard in the same file.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

ROOT: Final[Path] = Path(__file__).resolve().parents[2]

GIT_LAUNCHING_TESTS: Final[dict[str, tuple[str, ...]]] = {
    "tests/unit/runtime/test_provenance.py": (),
    "tests/unit/backtest/test_artifact_address_collisions.py": (
        "test_a_real_commit_reaches_the_address_and_the_honest_unknown_is_a_constant",
    ),
    "tests/unit/test_repository_assets.py": (
        "test_publication_gate_survives_a_nested_checkout_and_says_it_skipped_it",
        "test_publication_gate_refuses_secrets_runtime_databases_installers_and_oversized_files",
        "test_the_scratch_repository_ignores_git_templates_user_excludes_and_git_environment",
        "test_publication_gate_accepts_tracked_release_sources",
        "test_no_tracked_file_carries_a_committed_merge_conflict_marker",
    ),
    "tests/unit/test_cli.py": (
        "test_multi_subject_evidence_names_the_subjects_and_the_flag_that_selects_one",
        "test_the_second_subject_researched_under_the_default_run_id_names_that_flag",
    ),
}
"""Every test under `tests/unit/` the D13 census found starting git, by module; `()` is all of it.

The census read the tree -- every `subprocess` call whose argument list begins with `"git"`, every
`resolve_code_commit(`, every run of `scripts/verify_publication.py` -- and then ran `tests/unit`,
`tests/contract` and `tests/replay` with a `git` first on `PATH` that logged which test started
it. The run found what the reading found and two tests more: `test_cli.py`'s pair reach
`resolve_code_commit()` through the CLI's default `--code-commit`, so neither spells git at all.
Three kinds, all of them here:

* a throwaway repository under `tmp_path`, written with `git init`, `config`, `add` and `commit`
  -- `test_provenance.py` and `test_artifact_address_collisions.py`, the two that wrote the real
  repository;
* a scratch repository `test_repository_assets.py` builds without any `GIT_*`
  (`_without_git_variables`) and scans with a copy of the publication gate; the third of these
  sets five hostile `GIT_*` in its own body, after the protection has run;
* this checkout, read: the gate's `git ls-files` and the conflict-marker walk from its root, and
  `rev-parse`/`status` from `src/openalpha_cn/runtime/` for `test_cli.py`'s pair.

`tests/integration/` reads this checkout the third way as well -- `test_factor_build.py`,
`test_api_provenance_resolution.py`, `test_cli_research.py` and the omitted-provenance faces of
`test_model_interfaces.py` and `test_shortlist_interfaces.py` -- and is left out of the child for
its running time. Nothing depends on this table being complete: the protection is a hook over the
whole run, and `test_no_phase_of_an_item_sees_a_git_variable_the_run_inherited` measures it for a
test no table names.
"""


def _stripped_of_git_variables(environment: Mapping[str, str]) -> dict[str, str]:
    """`environment` less every `GIT_*` variable, compared the way `tests/conftest.py` compares."""
    return {
        name: value for name, value in environment.items() if not name.upper().startswith("GIT_")
    }


def _git(*arguments: str) -> None:
    """Run git to build the sentinel, with none of this process's `GIT_*` in its way."""
    subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        env=_stripped_of_git_variables(os.environ),
    )


def _sentinel_repository(tmp_path: Path) -> Path:
    """A repository with one commit, standing where a hook's variables would point.

    Its template and hooks path are an empty directory, and its identity and signing are given on
    the command line, so nothing about this machine's git setup -- a template's hooks, a global
    `core.hooksPath`, `commit.gpgsign` -- runs anything here or refuses the commit.
    """
    sentinel = tmp_path / "sentinel"
    empty = tmp_path / "empty"
    empty.mkdir()
    _git("init", "-q", f"--template={empty}", str(sentinel))
    (sentinel / "tracked.txt").write_text("sentinel\n", encoding="utf-8")
    _git("-C", str(sentinel), "add", "tracked.txt")
    _git(
        "-C",
        str(sentinel),
        "-c",
        "user.name=Sentinel",
        "-c",
        "user.email=sentinel@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        f"core.hooksPath={empty}",
        "commit",
        "-q",
        "-m",
        "sentinel",
    )
    return sentinel


def _snapshot(repository: Path) -> dict[str, str]:
    """Every path under `repository`, `.git/` included, mapped to the SHA-256 of its bytes.

    Read off the filesystem and not through git, because `git status` refreshes the index it
    reads: a snapshot taken that way could write the very file it is measuring.
    """
    return {
        path.relative_to(repository).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "directory"
        )
        for path in sorted(repository.rglob("*"))
    }


def _changed(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, str]:
    """Each path added, removed or rewritten between two snapshots, and which of the three."""
    return {
        path: "added" if path not in before else "removed" if path not in after else "rewritten"
        for path in sorted(before.keys() | after.keys())
        if before.get(path) != after.get(path)
    }


def _hook_variables(sentinel: Path) -> dict[str, str]:
    """What git hands a hook or an `--exec` command, every one of them naming `sentinel`.

    `GIT_INDEX_FILE` and `GIT_OBJECT_DIRECTORY` -- the second is what a `pre-receive` hook's
    quarantine sets -- are the half a protection that removed only `GIT_DIR` and `GIT_WORK_TREE`
    would miss: a throwaway repository's `git add` would still write its index and its blob here.
    """
    git_dir = sentinel / ".git"
    return {
        "GIT_DIR": str(git_dir),
        "GIT_WORK_TREE": str(sentinel),
        "GIT_INDEX_FILE": str(git_dir / "index"),
        "GIT_OBJECT_DIRECTORY": str(git_dir / "objects"),
    }


def _child_environment(sentinel: Path) -> dict[str, str]:
    """This process's environment the way a hook would pass it on, aimed at `sentinel` alone.

    Every `GIT_*` this process holds is dropped before the sentinel's are added -- the conftest has
    already removed the inherited ones, and this does not lean on it -- so each git variable the
    child sees names the sentinel, which the assertion below checks rather than assumes.
    `PYTEST_ADDOPTS` goes too: what the child selects is this file's decision, not the caller's.
    """
    environment = _stripped_of_git_variables(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.update(_hook_variables(sentinel))
    elsewhere = {
        name: value
        for name, value in environment.items()
        if name.upper().startswith("GIT_") and not Path(value).is_relative_to(sentinel)
    }
    assert elsewhere == {}, elsewhere
    return environment


def _pytest(
    *arguments: str, cwd: Path, environment: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    """A child pytest. Its outcome is the caller's to read, so a failing child is not an error."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments, "-q", "-p", "no:cacheprovider"],
        cwd=cwd,
        env=dict(environment),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def _node_ids(tests: Mapping[str, tuple[str, ...]]) -> list[str]:
    ids: list[str] = []
    for module, names in tests.items():
        ids.extend([f"{module}::{name}" for name in names] or [module])
    return ids


def _passing_tests(report: Path) -> dict[str, set[str]]:
    """Module -> names of the tests a JUnit report records, having asserted each of them passed.

    A skip counts against it like a failure: a skipped test starts no git, and a child that skipped
    everything would leave any sentinel untouched without having measured anything.
    """
    passing: dict[str, set[str]] = {}
    for case in ElementTree.parse(report).iter("testcase"):
        outcome = [child.tag for child in case if child.tag in {"failure", "error", "skipped"}]
        assert outcome == [], f"{case.get('classname')}::{case.get('name')} -> {outcome}"
        module = f"{case.get('classname', '').replace('.', '/')}.py"
        passing.setdefault(module, set()).add(case.get("name", "").split("[")[0])
    return passing


def test_the_tests_that_start_git_leave_the_repository_a_hook_names_untouched(
    tmp_path: Path,
) -> None:
    """The D13 incident's environment, aimed at a sentinel, and not one byte of it written.

    Passing matters as much as the sentinel does: a child that failed before it reached git would
    leave the sentinel untouched too, so every test `GIT_LAUNCHING_TESTS` names must be in the
    report as passed, and nothing there may have failed, errored or been skipped.

    Measured in D13 with `tests/conftest.py`'s two hooks deleted: the sentinel's config gained
    `email = t@example.com` and `name = T` -- `test_artifact_address_collisions.py`'s, the later of
    the two writers -- and its reinitialisation copied git's default template into `.git/`, while
    HEAD, refs, index and objects were untouched. Removing only `GIT_DIR` and `GIT_WORK_TREE`
    instead rewrote the sentinel's index and added objects to it, through `GIT_INDEX_FILE` and
    `GIT_OBJECT_DIRECTORY`; without those two in `_hook_variables` that mutation survives.
    """
    sentinel = _sentinel_repository(tmp_path)
    before = _snapshot(sentinel)
    report = tmp_path / "report.xml"

    finished = _pytest(
        *_node_ids(GIT_LAUNCHING_TESTS),
        f"--basetemp={tmp_path / 'child'}",
        f"--junitxml={report}",
        cwd=ROOT,
        environment=_child_environment(sentinel),
    )

    changed = _changed(before, _snapshot(sentinel))
    assert changed == {}, (
        f"the repository the hook's variables named was written: {changed}; its config reads\n"
        + (sentinel / ".git" / "config").read_text(encoding="utf-8")
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
    passing = _passing_tests(report)
    for module, names in GIT_LAUNCHING_TESTS.items():
        assert passing.get(module), f"nothing in {module} ran"
        assert set(names) <= passing[module], set(names) - passing[module]


def test_without_the_conftest_the_same_child_rewrites_the_sentinel_config(tmp_path: Path) -> None:
    """The non-vacuity of the test above: the incident, reproduced on the sentinel.

    `--noconftest` takes away `tests/conftest.py` and nothing `test_provenance.py` needs, so the
    protection is the only difference from the run above. Without it, `_init_repo`'s
    `git config user.email test@example.com` lands in the sentinel's config -- the line D13 found
    in the real repository's. Were the variables misnamed or the snapshot blind, this is the test
    that would go red, instead of the one above going green for nothing.
    """
    sentinel = _sentinel_repository(tmp_path)
    before = _snapshot(sentinel)

    _pytest(
        "tests/unit/runtime/test_provenance.py",
        "--noconftest",
        f"--basetemp={tmp_path / 'child'}",
        cwd=ROOT,
        environment=_child_environment(sentinel),
    )

    assert _changed(before, _snapshot(sentinel)).get(".git/config") == "rewritten"
    assert "test@example.com" in (sentinel / ".git" / "config").read_text(encoding="utf-8")


PHASE_PROBE: Final[str] = textwrap.dedent(
    """
    import os

    import pytest

    SEEN = []


    def record(phase):
        inherited = sorted(name for name in os.environ if name.upper().startswith("GIT_"))
        SEEN.append(phase + "=" + ("+".join(inherited) or "none"))


    record("import")


    @pytest.fixture(scope="session")
    def s():
        record("session")


    @pytest.fixture(scope="module")
    def m():
        record("module")


    @pytest.fixture(scope="class")
    def c():
        record("class")


    def test_probe(s, m, c):
        record("call")
        print("PHASES " + " ".join(SEEN))
    """
)
"""A test module that records, at five points in pytest's cycle, which `GIT_*` it can see."""

PHASE_RUNNER: Final[str] = textwrap.dedent(
    """
    import os
    import sys

    import pytest

    code = pytest.main(sys.argv[1:])
    after = sorted(name for name in os.environ if name.upper().startswith("GIT_"))
    print("AFTER " + ("+".join(after) or "none"))
    sys.exit(code)
    """
)
"""`pytest.main` inside the child's own process, so what a run leaves behind can be read as well."""


def _run_phase_probe(tmp_path: Path, *, with_the_conftest: bool) -> dict[str, str]:
    """Run `PHASE_PROBE` under a hook's variables and report what each phase saw.

    `-p conftest` with `tests/` on `PYTHONPATH` loads this repository's own `tests/conftest.py` as
    a plugin, and `--confcutdir` at the probe's directory keeps pytest from finding any other --
    `tests/unit/test_offline_suite.py::_run_scope_probe`'s arrangement, so the toggle is the only
    difference between the two runs. `after` is the environment once `pytest.main` has returned,
    which is where `pytest_unconfigure` hands the variables back.
    """
    directory = tmp_path / "probe"
    directory.mkdir()
    probe = directory / "test_phase_probe.py"
    probe.write_text(PHASE_PROBE, encoding="utf-8")
    environment = _child_environment(tmp_path / "sentinel")
    environment["PYTHONPATH"] = str(ROOT / "tests")
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            PHASE_RUNNER,
            str(probe),
            "-q",
            "-s",
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(directory),
            *(["-p", "conftest"] if with_the_conftest else []),
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    lines = finished.stdout.splitlines()
    phases = [line.removeprefix("PHASES ") for line in lines if line.startswith("PHASES ")]
    after = [line.removeprefix("AFTER ") for line in lines if line.startswith("AFTER ")]
    assert phases and after, f"the probe reported nothing; stdout was:\n{finished.stdout}"
    return {**dict(entry.split("=", 1) for entry in phases[0].split()), "after": after[0]}


def test_no_phase_of_an_item_sees_a_git_variable_the_run_inherited(tmp_path: Path) -> None:
    """Module import, a fixture of every scope, the test: none of them sees one.

    And the run hands them back when it ends -- `after` -- because a process that calls
    `pytest.main` is somebody else's, and this protection is about the run, not about them.
    """
    hook = "+".join(sorted(_hook_variables(tmp_path)))

    assert _run_phase_probe(tmp_path, with_the_conftest=True) == {
        "import": "none",
        "session": "none",
        "module": "none",
        "class": "none",
        "call": "none",
        "after": hook,
    }


def test_the_phase_probe_can_tell_an_inherited_variable_from_a_removed_one(
    tmp_path: Path,
) -> None:
    """The same probe without `-p conftest`: every phase sees all four variables.

    Without this, a probe that could not see the environment at all would report `none` five times
    and the test above would be green while measuring nothing.
    """
    hook = "+".join(sorted(_hook_variables(tmp_path)))

    assert _run_phase_probe(tmp_path, with_the_conftest=False) == {
        phase: hook for phase in ("import", "session", "module", "class", "call", "after")
    }
