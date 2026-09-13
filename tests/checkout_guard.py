"""What a pytest session wrote inside the checkout it ran from -- measured, not promised.

D13 I-D. Four tests rewrote tracked modules under `src/` and put them back with
`Path.write_text`, which on Windows writes every "\\n" as "\\r\\n", so both Windows legs of CI
printed `CRLF will be replaced by LF` for exactly those four files after every run. Six more
created a probe module under `src/` and deleted it in `finally`, and thirty `grimp.build_graph`
calls left `.grimp_cache/` at the root. On Linux and macOS the rewrites put back the bytes they
had found, so `git status` showed none of it. A run killed between a write and its `finally` would
have left the planted import in `src/`, and a second run in the same checkout -- or an editor --
could import what the first one was holding there.

`tests/unit/test_no_test_writes_the_checkout.py` reads the test tree for the writes a reading can
see. This is the other half: `tests/conftest.py` takes a `snapshot` of the checkout when a session
starts, when collection finishes and when the session ends, and fails the run on any `changes`
between them. A subprocess, a path assembled from strings and a cache a library keeps on its own
are all seen here and not by the reading.

What a snapshot holds, under the root it is given:

* every file below `src/` and `tests/`, by size and modification time, and on POSIX by change time
  and inode as well -- so a write that put back the very bytes it found is still a write, and so
  is one that put the modification time back too: no user-space call sets a change time back,
  and `os.utime` and `shutil.copy2` both move it. D14 review m-2 measured both of those unseen
  before the change time was read. On Windows `st_ctime` is the creation time and is not read, so
  there they still are;
* every directory below them, by modification time and the names it holds -- so a file created
  and deleted between two snapshots is still seen: the directory's modification time moves and
  its listing does not;
* the names directly under the root -- so a cache directory a tool leaves there is seen. Only the
  names: a file standing directly under the root, `pyproject.toml` say, rewritten in place is not.

What it ignores, because the interpreter and the runner write these as a matter of course:

* everything inside `__pycache__`, which is where importing a module writes its bytecode and the
  only place CPython writes it. A `.pyc` anywhere else is a change: D14 review m-2 planted a
  sourceless `src/package/evil.pyc` while `*.pyc` was ignored wherever it stood, and imported it
  after a green run;
* a directory's moved modification time whenever its listing changed too, because the entry that
  came or went accounts for it. **Any** such change therefore hides a file created and deleted in
  the same directory between the same two snapshots -- a `__pycache__` appearing the first time a
  package is imported, a `.DS_Store`, an entry a test left behind. It is why `tests/conftest.py`
  takes the middle snapshot: collection is when nearly every package is first imported, so the
  window the tests themselves run in seldom has a `__pycache__` appear in it;
* `.DS_Store`, which Finder writes into any directory somebody opens;
* at the root only, `.pytest_cache`, `.coverage` and `.coverage.*`: the cache and the coverage data
  the runner writes there itself;
* what this run writes because its own options told it to (`run_outputs`): the report
  `--junitxml` names; the basetemp `--basetemp` names; pytest's `cache_dir`; and pytest-cov's data
  file and file reports -- `html`, `xml`, `json`, `lcov`, `markdown` and `markdown-append` at the
  destination given after `:`, or where coverage.py's configuration puts them when none is
  (`htmlcov/`, `coverage.xml`, `coverage.json`, `coverage.lcov` by default), and `annotate`'s
  `*,cover` beside each source. D14 I-1: before these were read, each failed a run whose every test
  had passed the first time its target appeared in the checkout, and a basetemp under `tests/`
  failed every run;
* the directories one of those needs and the session began without, which its writer makes on the
  way -- those directories themselves, not whatever else turns up in them. D14 fix review m-1:
  until they were, `--junitxml=reports/junit.xml` failed every run in a fresh checkout with
  `created reports at the checkout root`, and `--junitxml=tests/reports/junit.xml` with `created
  directory tests/reports/`;
* the created-and-removed shape in the directory directly holding a basetemp or coverage.py's
  data file that stood there as the session began: pytest empties the one and makes it again,
  pytest-cov saves a data file of its own beside the other and combines it in, and either leaves
  that directory's listing as it was and its modification time moved. Only those two: D14 fix
  review m-2 measured what exempting the directory of any output cost -- a junit or coverage
  report written over one an earlier run left, or a basetemp named and never made, hid a module a
  test created and removed in `tests/` for the whole session.

What fails a session although no test caused it -- the guard sees that a path changed, never who
changed it:

* anything written under `src/` or `tests/` while the session runs: an editor or an IDE saving,
  `touch`, a formatter, `git checkout`, `stash` or `rebase` in this checkout, a second run in it;
* a name that appears at the root while the session runs: a concurrent `lint-imports` making its
  cache, `uv build` making `dist/`, `ruff` or `mypy` making theirs the first time;
* an output of this run that `run_outputs` does not read, such as another plugin's report file.
  Not the data file pytest-cov saves beside coverage.py's and combines in: it is gone before the
  last snapshot, and a run with the data file moved into `tests/` measured silent.

A run during which `src/` changed did not test one tree, so failing it is the right default even
when the writer was not a test, and every line names its path so the reader can tell which it
was. There are two ways out that are not `--noconftest`, which would take the `GIT_*` protection
and the offline guard with it: run a suite you mean to edit during in a worktree of its own, or
set `OPENALPHA_CHECKOUT_GUARD=report`, which prints the same section and leaves the exit status to
the tests. The default is `fail`, CI sets nothing, and any other value is a usage error.

What it cannot see: a write made after the last snapshot, by a process a test started and left
running -- measured, the session passed and the file appeared two seconds later; a file directly
under the root rewritten in place; on Windows, a same-size rewrite with its modification time put
back; a file created and removed in a directory whose listing changed in the same window, or
beside a basetemp or data file the run remade (both above); and anything outside `src/`, `tests/`
and the root's own listing -- a test that rewrote a file under `docs/`, or wrote inside a
directory that already stands at the root, such as `runtime/`. That last is the one place the
other half decides: the reading sees such a write when its path is one the reading follows --
`(ROOT / "docs" / "notes.md").write_text(...)` is a case of its own test -- and not when a
subprocess, or a path it does not follow, makes it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

WATCHED_TREES: Final[tuple[str, ...]] = ("src", "tests")
"""The two trees a test run imports from, walked in full."""

BYTECODE: Final[str] = "__pycache__"

UNWATCHED_NAMES: Final[frozenset[str]] = frozenset({BYTECODE, ".DS_Store"})
"""Names nobody commits and something other than a test writes; never a change."""

RUNNER_OUTPUT_AT_THE_ROOT: Final[frozenset[str]] = frozenset({".pytest_cache", ".coverage"})
"""What pytest's cache and pytest-cov write at the root. `.coverage.*` is matched by prefix."""

MODE_VARIABLE: Final[str] = "OPENALPHA_CHECKOUT_GUARD"
"""The guard's one switch. Unset or empty is `fail`."""

MODES: Final[tuple[str, ...]] = ("fail", "report")

COVERAGE_REPORT_SETTINGS: Final[Mapping[str, tuple[str, str]]] = {
    "html": ("html_dir", "htmlcov"),
    "xml": ("xml_output", "coverage.xml"),
    "json": ("json_output", "coverage.json"),
    "lcov": ("lcov_output", "coverage.lcov"),
}
"""pytest-cov's file reports whose destination, left out, is a coverage.py setting: the setting's
name, and coverage.py's documented default for when the running configuration cannot be read.
pytest-cov gives `markdown` and `markdown-append` a destination of its own, `coverage.md`."""

ANNOTATED_SOURCE: Final[str] = ",cover"
"""What coverage.py's `annotate` report appends to each source's name when given no directory."""


@dataclass(frozen=True)
class Snapshot:
    """One instant of the checkout: see this module's docstring for what is held and why."""

    files: Mapping[str, tuple[int, ...]]
    """Relative POSIX path -> (modification time in ns, size in bytes), followed on POSIX by
    (change time in ns, inode)."""

    directories: Mapping[str, tuple[int, frozenset[str]]]
    """Relative POSIX path -> (modification time in ns, the names it held)."""

    top_level: frozenset[str]
    """The names directly under the root."""


@dataclass(frozen=True)
class RunOutputs:
    """Where the run being measured writes because its own options said so; no test's write."""

    paths: frozenset[str] = frozenset()
    """Relative POSIX paths under the root; everything at or below each is the run's own."""

    suffixes: frozenset[str] = frozenset()
    """Endings of the file names the run writes beside its sources, wherever those are."""

    made_for_them: frozenset[str] = frozenset()
    """Directories between the root and one of `paths` that did not exist when the session began.

    Whoever writes an output makes them first -- pytest's junit plugin and coverage.py both make
    every missing directory on the way. Only each directory itself is the run's own: a file found
    in one that is not among `paths` is still a change.
    """

    remade: frozenset[str] = frozenset()
    """Those of `paths` that stood there as the session began and that their writer remakes: the
    basetemp, which pytest empties and makes again, and coverage.py's data file, beside which
    pytest-cov saves a data file of its own and then combines it in."""

    def cover(self, path: str) -> bool:
        """Whether `path` is one of these outputs, lies inside one, or was made to hold one."""
        return (
            path in self.made_for_them
            or path.endswith(tuple(self.suffixes))
            or any(path == output or path.startswith(f"{output}/") for output in self.paths)
        )

    def remade_directly_in(self, directory: str) -> bool:
        """Whether one of `remade` sits directly inside `directory`.

        Remaking one moves that directory's modification time and leaves its listing as it was,
        which is exactly the shape of a file created and removed. A report written over the one an
        earlier run left moves neither, and a basetemp named and never made remakes nothing.
        """
        return any(PurePosixPath(output).parent.as_posix() == directory for output in self.remade)


NOTHING_OF_ITS_OWN: Final[RunOutputs] = RunOutputs()


def checkout_mode() -> str:
    """`OPENALPHA_CHECKOUT_GUARD`'s value: `fail` when unset or empty, a usage error if unknown."""
    mode = os.environ.get(MODE_VARIABLE) or "fail"
    if mode not in MODES:
        raise pytest.UsageError(
            f"{MODE_VARIABLE}={mode!r}: the checkout guard's mode is 'fail', the default, or "
            "'report', which prints what was written and leaves the exit status to the tests"
        )
    return mode


def run_outputs(config: pytest.Config) -> RunOutputs:
    """Where this run writes because its own options said so, relative to `config.rootpath`.

    Read off the options themselves: `--junitxml` and `--basetemp`, pytest's `cache_dir`, and
    pytest-cov's file reports and data file. A report given no destination goes where coverage.py's
    configuration puts it, read from the running pytest-cov when it can be and from coverage.py's
    documented defaults when it cannot. Relative targets resolve against the directory pytest was
    started from, as each of those writers resolves them, except pytest's cache directory, which
    pytest resolves against the root -- and which does not exist as a setting at all when the
    cache plugin is off (`-p no:cacheprovider`), and then writes nothing. A target outside the
    root is nothing this guard watches. The directories a target inside it needs and does not have
    yet, as they stand when this is called -- as the session starts -- are the run's as well
    (`RunOutputs.made_for_them`), and so, of the basetemp and the data file, is whichever stands
    there already (`RunOutputs.remade`).
    """
    started = config.invocation_params.dir
    targets: list[Path] = []
    remade: list[Path] = []
    if config.pluginmanager.hasplugin("cacheprovider"):
        targets.append(_target(str(config.getini("cache_dir")), config.rootpath))
    xmlpath = getattr(config.option, "xmlpath", None)
    if xmlpath:
        targets.append(_target(str(xmlpath), started))
    basetemp = getattr(config.option, "basetemp", None)
    if basetemp:
        remade.append(_target(str(basetemp), started))
    suffixes: set[str] = set()
    coverage = config.pluginmanager.getplugin("_cov")
    reports = getattr(getattr(coverage, "options", None), "cov_report", None)
    controller = getattr(coverage, "cov_controller", None)
    settings = getattr(getattr(controller, "cov", None), "config", None)
    if isinstance(reports, Mapping):
        for kind, destination in reports.items():
            if destination:
                targets.append(_target(str(destination), started))
            elif kind in COVERAGE_REPORT_SETTINGS:
                setting, default = COVERAGE_REPORT_SETTINGS[kind]
                targets.append(_target(str(getattr(settings, setting, None) or default), started))
            elif kind == "annotate":
                suffixes.add(ANNOTATED_SOURCE)
    data_file = getattr(settings, "data_file", None)
    if data_file:
        remade.append(_target(str(data_file), started))
    root = Path(os.path.realpath(config.rootpath))
    inside = _inside(root, [*targets, *remade])
    return RunOutputs(
        paths=frozenset(real.relative_to(root).as_posix() for real in inside),
        suffixes=frozenset(suffixes),
        made_for_them=frozenset(
            directory.relative_to(root).as_posix()
            for real in inside
            for directory in _directories_to_make(real, root)
        ),
        remade=frozenset(
            real.relative_to(root).as_posix() for real in _inside(root, remade) if real.exists()
        ),
    )


def _target(value: str, base: Path) -> Path:
    candidate = Path(os.path.expandvars(os.path.expanduser(value)))
    return candidate if candidate.is_absolute() else base / candidate


def _inside(root: Path, targets: list[Path]) -> list[Path]:
    """Each of `targets` that lies below `root`, as a real path."""
    return [
        real
        for real in (Path(os.path.realpath(target)) for target in targets)
        if real != root and real.is_relative_to(root)
    ]


def _directories_to_make(path: Path, root: Path) -> list[Path]:
    """The directories between `root` and `path` that do not exist yet, nearest first."""
    missing: list[Path] = []
    for directory in path.parents:
        if directory == root or directory.exists():
            break
        missing.append(directory)
    return missing


def snapshot(root: Path) -> Snapshot:
    """`root`'s watched trees and top-level names, as they stand now.

    A path that disappears between being listed and being read is left out rather than raised
    about: whatever removed it is a change the next snapshot reports.
    """
    files: dict[str, tuple[int, ...]] = {}
    directories: dict[str, tuple[int, frozenset[str]]] = {}
    for tree in WATCHED_TREES:
        for current, names, filenames in os.walk(root / tree):
            here = Path(current)
            try:
                modified = os.stat(here).st_mtime_ns
            except FileNotFoundError:
                continue
            directories[here.relative_to(root).as_posix()] = (
                modified,
                frozenset(names) | frozenset(filenames),
            )
            names[:] = [name for name in names if name != BYTECODE]
            for name in filenames:
                if name in UNWATCHED_NAMES:
                    continue
                try:
                    status = os.stat(here / name)
                except FileNotFoundError:
                    continue
                files[(here / name).relative_to(root).as_posix()] = (
                    status.st_mtime_ns,
                    status.st_size,
                    *_what_cannot_be_put_back(status),
                )
    return Snapshot(files=files, directories=directories, top_level=frozenset(os.listdir(root)))


def _what_cannot_be_put_back(status: os.stat_result) -> tuple[int, ...]:
    """On POSIX, a file's change time and inode; nothing on Windows.

    A change time moves with every write, `utime`, `chmod` and link, and no user-space call sets
    it back; an inode is a different one after a file is replaced. On Windows `st_ctime` is the
    creation time -- Python 3.12 deprecates it there in favour of `st_birthtime` -- so it is not
    read, and a same-size rewrite with its modification time put back is not seen there.
    """
    return () if os.name == "nt" else (status.st_ctime_ns, status.st_ino)


def changes(
    before: Snapshot, after: Snapshot, outputs: RunOutputs = NOTHING_OF_ITS_OWN
) -> list[str]:
    """Every difference between two snapshots that neither the runner nor `outputs` accounts for."""
    found: list[str] = []
    for path in sorted(before.files.keys() | after.files.keys()):
        if outputs.cover(path):
            continue
        was, now = before.files.get(path), after.files.get(path)
        if was is None:
            found.append(f"created {path}")
        elif now is None:
            found.append(f"deleted {path}")
        elif was[:2] != now[:2]:
            found.append(f"rewrote {path}")
        elif was != now:
            found.append(
                f"changed {path} -- its size and modification time are what they were, its "
                "change time or inode is not"
            )
    for path in sorted(before.directories.keys() | after.directories.keys()):
        if outputs.cover(path):
            continue
        held, holds = before.directories.get(path), after.directories.get(path)
        if held is None:
            found.append(f"created directory {path}/")
        elif holds is None:
            found.append(f"deleted directory {path}/")
        elif held[0] != holds[0] and held[1] == holds[1] and not outputs.remade_directly_in(path):
            found.append(
                f"created and removed something in {path}/ -- its modification time moved and "
                "its listing did not"
            )
    for name in sorted(after.top_level - before.top_level):
        if not _written_by_the_runner(name) and not outputs.cover(name):
            found.append(f"created {name} at the checkout root")
    for name in sorted(before.top_level - after.top_level):
        if not _written_by_the_runner(name) and not outputs.cover(name):
            found.append(f"deleted {name} from the checkout root")
    return found


def _written_by_the_runner(name: str) -> bool:
    return (
        name in UNWATCHED_NAMES
        or name in RUNNER_OUTPUT_AT_THE_ROOT
        or name.startswith(".coverage.")
    )
