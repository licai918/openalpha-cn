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
  the runner writes there itself.

What it cannot tell apart is a write by a test from a write by anything else working in the same
checkout at the same time -- an editor saving a file under `src/`, a second test run, a formatter.
It reports both. A run during which `src/` changed did not test one tree, so failing it is the
right answer either way, and every line names its path so the reader can tell which it was. It
sees nothing outside `src/`, `tests/` and the root's own listing: a test that rewrote a file under
`docs/`, or wrote inside a directory that already stands at the root, such as `runtime/`, is seen
by neither half.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

WATCHED_TREES: Final[tuple[str, ...]] = ("src", "tests")
"""The two trees a test run imports from, walked in full."""

BYTECODE: Final[str] = "__pycache__"

UNWATCHED_NAMES: Final[frozenset[str]] = frozenset({BYTECODE, ".DS_Store"})
"""Names nobody commits and something other than a test writes; never a change."""

RUNNER_OUTPUT_AT_THE_ROOT: Final[frozenset[str]] = frozenset({".pytest_cache", ".coverage"})
"""What pytest's cache and pytest-cov write at the root. `.coverage.*` is matched by prefix."""


@dataclass(frozen=True)
class Snapshot:
    """One instant of the checkout: see this module's docstring for what is held and why."""

    files: Mapping[str, tuple[int, int]]
    """Relative POSIX path -> (modification time in ns, size in bytes)."""

    directories: Mapping[str, tuple[int, frozenset[str]]]
    """Relative POSIX path -> (modification time in ns, the names it held)."""

    top_level: frozenset[str]
    """The names directly under the root."""


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


def changes(before: Snapshot, after: Snapshot) -> list[str]:
    """Every difference between two snapshots that something other than the runner made."""
    found: list[str] = []
    for path in sorted(before.files.keys() | after.files.keys()):
        was, now = before.files.get(path), after.files.get(path)
        if was is None:
            found.append(f"created {path}")
        elif now is None:
            found.append(f"deleted {path}")
        elif was != now:
            found.append(f"rewrote {path}")
    for path in sorted(before.directories.keys() | after.directories.keys()):
        held, holds = before.directories.get(path), after.directories.get(path)
        if held is None:
            found.append(f"created directory {path}/")
        elif holds is None:
            found.append(f"deleted directory {path}/")
        elif held[0] != holds[0] and held[1] == holds[1]:
            found.append(
                f"created and removed something in {path}/ -- its modification time moved and "
                "its listing did not"
            )
    for name in sorted(after.top_level - before.top_level):
        if not _written_by_the_runner(name):
            found.append(f"created {name} at the checkout root")
    for name in sorted(before.top_level - after.top_level):
        if not _written_by_the_runner(name):
            found.append(f"deleted {name} from the checkout root")
    return found


def _written_by_the_runner(name: str) -> bool:
    return (
        name in UNWATCHED_NAMES
        or name in RUNNER_OUTPUT_AT_THE_ROOT
        or name.startswith(".coverage.")
    )
