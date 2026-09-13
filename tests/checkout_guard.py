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

* every file below `src/` and `tests/`, by size and modification time -- so a write that put back
  the very bytes it found is still a write;
* every directory below them, by modification time and the names it holds -- so a file created
  and deleted between two snapshots is still seen: the directory's modification time moves and
  its listing does not;
* the names directly under the root -- so a cache directory a tool leaves there is seen.

What it ignores, because the interpreter and the runner write these as a matter of course:

* everything inside `__pycache__`, and `*.pyc` anywhere: importing a module writes its bytecode;
* a directory whose listing changed only by a `__pycache__` appearing, which is what the first
  import of a package does to its directory. **This also hides a file created and deleted in the
  same directory between the same two snapshots**, and it is why `tests/conftest.py` takes the
  middle snapshot: collection is when nearly every package is first imported, so the window the
  tests themselves run in seldom has a `__pycache__` appear in it;
* `.DS_Store`, which Finder writes into any directory somebody opens;
* at the root only, `.pytest_cache`, `.coverage` and `.coverage.*`: the cache and the coverage data
  the runner writes there itself;
* what this run writes because its own options told it to (`run_outputs`): the report
  `--junitxml` names; the basetemp `--basetemp` names, which pytest empties and makes again, so
  the directory holding it is not asked about a created-and-removed entry either; pytest's
  `cache_dir`; and pytest-cov's data file and file reports -- `html`, `xml`, `json`, `lcov`,
  `markdown` and `markdown-append` at the destination given after `:`, or where coverage.py's
  configuration puts them when none is (`htmlcov/`, `coverage.xml`, `coverage.json`,
  `coverage.lcov` by default), and `annotate`'s `*,cover` beside each source. D14 I-1: before these
  were read, each failed a run whose every test had passed the first time its target appeared in
  the checkout, and a basetemp under `tests/` failed every run.

What fails a session although no test caused it -- the guard sees that a path changed, never who
changed it:

* anything written under `src/` or `tests/` while the session runs: an editor or an IDE saving,
  `touch`, a formatter, `git checkout`, `stash` or `rebase` in this checkout, a second run in it;
* a name that appears at the root while the session runs: a concurrent `lint-imports` making its
  cache, `uv build` making `dist/`, `ruff` or `mypy` making theirs the first time;
* an output of this run that `run_outputs` does not read: another plugin's report file, or the
  parallel data files coverage.py writes beside a data file its configuration moved out of the
  root.

A run during which `src/` changed did not test one tree, so failing it is the right default even
when the writer was not a test, and every line names its path so the reader can tell which it
was. There are two ways out that are not `--noconftest`, which would take the `GIT_*` protection
and the offline guard with it: run a suite you mean to edit during in a worktree of its own, or
set `OPENALPHA_CHECKOUT_GUARD=report`, which prints the same section and leaves the exit status to
the tests. The default is `fail`, CI sets nothing, and any other value is a usage error.

It sees nothing outside `src/`, `tests/` and the root's own listing: a test that rewrote a file
under `docs/`, or wrote inside a directory that already stands at the root, such as `runtime/`, is
seen by neither half.
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

    files: Mapping[str, tuple[int, int]]
    """Relative POSIX path -> (modification time in ns, size in bytes)."""

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

    def cover(self, path: str) -> bool:
        """Whether `path` is one of these outputs or lies inside one."""
        return path.endswith(tuple(self.suffixes)) or any(
            path == output or path.startswith(f"{output}/") for output in self.paths
        )

    def one_is_directly_in(self, directory: str) -> bool:
        """Whether one of these outputs sits directly inside `directory`.

        Emptying a basetemp and making it again moves its parent's modification time and leaves
        the parent's listing as it was, which is exactly the shape of a file created and removed.
        """
        return any(PurePosixPath(output).parent.as_posix() == directory for output in self.paths)


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
    root is nothing this guard watches.
    """
    started = config.invocation_params.dir
    targets: list[Path] = []
    if config.pluginmanager.hasplugin("cacheprovider"):
        targets.append(_target(str(config.getini("cache_dir")), config.rootpath))
    for option in ("xmlpath", "basetemp"):
        value = getattr(config.option, option, None)
        if value:
            targets.append(_target(str(value), started))
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
        targets.append(_target(str(data_file), started))
    root = Path(os.path.realpath(config.rootpath))
    paths = {
        real.relative_to(root).as_posix()
        for real in (Path(os.path.realpath(target)) for target in targets)
        if real != root and real.is_relative_to(root)
    }
    return RunOutputs(paths=frozenset(paths), suffixes=frozenset(suffixes))


def _target(value: str, base: Path) -> Path:
    candidate = Path(os.path.expandvars(os.path.expanduser(value)))
    return candidate if candidate.is_absolute() else base / candidate


def snapshot(root: Path) -> Snapshot:
    """`root`'s watched trees and top-level names, as they stand now.

    A path that disappears between being listed and being read is left out rather than raised
    about: whatever removed it is a change the next snapshot reports.
    """
    files: dict[str, tuple[int, int]] = {}
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
                if name in UNWATCHED_NAMES or name.endswith(".pyc"):
                    continue
                try:
                    status = os.stat(here / name)
                except FileNotFoundError:
                    continue
                files[(here / name).relative_to(root).as_posix()] = (
                    status.st_mtime_ns,
                    status.st_size,
                )
    return Snapshot(files=files, directories=directories, top_level=frozenset(os.listdir(root)))


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
        elif was != now:
            found.append(f"rewrote {path}")
    for path in sorted(before.directories.keys() | after.directories.keys()):
        if outputs.cover(path):
            continue
        held, holds = before.directories.get(path), after.directories.get(path)
        if held is None:
            found.append(f"created directory {path}/")
        elif holds is None:
            found.append(f"deleted directory {path}/")
        elif held[0] != holds[0] and held[1] == holds[1] and not outputs.one_is_directly_in(path):
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
