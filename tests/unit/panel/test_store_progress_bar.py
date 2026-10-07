"""Every connection `PanelStore` opens for a read and for a write disables the progress bar
(`V2-P6-021`).

See `openalpha_cn.panel.store::_connect`'s docstring, and
`tests/unit/test_duckdb_progress_bar_guard.py`'s module docstring, for why this matters: DuckDB
prints an ANSI progress bar straight to the real stdout file descriptor for any query running
past `progress_bar_time` (2000ms by default, on by default), which corrupts a caller that parses
this process's stdout -- the research driver's `openalpha factor stale-return-paths`
precondition, or any `--json` command -- whenever a query happens to run long.

**`current_setting('enable_progress_bar')` is not trustworthy under this pytest run, discovered
while writing this file.** A brand-new `duckdb.connect(":memory:")`, no `_connect` involved at
all, reports `current_setting('enable_progress_bar')` as `False` by default the moment it runs
under `pytest` -- reproduced on a single-test, no-project-conftest file, so it is not this
repository's fixtures. What the connection actually *does* when a query is forced past
`progress_bar_time` says otherwise: the stdout of a process running such a query (see
`_forced_stdout` below) carries the ANSI progress bar text every time, `current_setting`'s
`False` notwithstanding. `current_setting` on this DuckDB build, under pytest, answers a
question other than "will this connection print a progress bar" -- and the two tests below still
check it, because `V2-P6-021`'s brief asks for it and it is not actively wrong for a *configured*
connection (the helper's own `SET` always lands and always holds), but neither test relies on it
alone: the file also drives a real, forced-slow query and reads the stdout it left, asserting
nothing lands there, which is the only assertion in this file provably tied to the defect.

The mutation check lives here as `test_removing_the_helpers_config_turns_this_red`, on the stdout
assertion rather than on `current_setting` -- a version of that test built on `current_setting`
alone would stay green with the helper's two `SET` calls deleted, which was caught only by
running it that way while writing this file (see this task's final report for the fuller
account).

**The stdout checked is a child process's, read from a pipe (`V2-P6-034`).** These checks first
redirected this process's fd 1 with `os.dup2` around the store call. On the Windows runners the
mutation check then read an empty file: no bar reached the redirected descriptor, and none
appeared in pytest's capture or the job log either, so the fd assertion was not testing anything
there -- and the negative check passed for the same reason it would have passed with the fix
removed. A child process whose stdout is a pipe from its first instruction is what the defect's
real caller (`subprocess.run(..., capture_output=True)` in the research driver) reads, needs no
descriptor surgery, and is the same capture on every platform. Its bytes are decoded as UTF-8 --
the bar's box-drawing characters are UTF-8, and `read_text()`'s locale default on Windows
(cp1252) would have turned `\u2595` into three other characters and made both checks vacuous.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import openalpha_cn.panel.store as store_module
from openalpha_cn.panel.store import ColumnSpec, PanelStore

DATASET = "progress_bar_probe"

_PROGRESS_BAR_MARKER = "▕"
"""One of the box-drawing characters DuckDB's progress bar renders; absence of it in a capture
is the test's actual pass condition, `current_setting` aside."""


_CHILD: str = """
import sys
from pathlib import Path

import openalpha_cn.panel.store as store_module
from openalpha_cn.panel.store import ColumnSpec, PanelStore

root, mode = Path(sys.argv[1]), sys.argv[2]
if mode == "unconfigured":
    import duckdb

    def _unconfigured(database, *, read_only=False):
        return duckdb.connect(database, read_only=read_only)

    store_module._connect = _unconfigured
configured = store_module._connect


def _forced(*args, **kwargs):
    connection = configured(*args, **kwargs)
    connection.execute("SET progress_bar_time = 0")
    return connection


store_module._connect = _forced
store = PanelStore(root)
store.write_partition(
    "progress_bar_probe", 2026, (ColumnSpec("subject", "VARCHAR"),), [("000001.SZ",)]
)
sys.stdout.write("\\n--- read ---\\n")
sys.stdout.flush()
rows = store.query("progress_bar_probe", year=2026, columns=("subject",))
assert rows == [("000001.SZ",)], rows
"""
"""A child process that writes one partition and reads it back through `PanelStore`, every
connection forced past the progress-bar threshold (`SET progress_bar_time = 0`, applied strictly
after whatever `_connect` already did, so it never races the helper's own setup). `configured`
keeps the real `_connect`; `unconfigured` replaces it with a bare `duckdb.connect` -- the helper's
two `SET` statements deleted, the exact edit `V2-P6-021`'s review round asks be tried."""

_READ_PHASE = "\n--- read ---\n"


def _forced_stdout(root: Path, mode: str) -> tuple[str, str]:
    """The child's stdout around its write and around its read, as a pipe delivered it."""
    finished = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), mode], capture_output=True, check=False
    )
    stdout = finished.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    assert finished.returncode == 0, finished.stderr.decode("utf-8", errors="replace")
    write, separator, read = stdout.partition(_READ_PHASE)
    assert separator, f"the child never reached its read: {stdout!r}"
    return write, read


def _spy_on_connect(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Wrap `store_module._connect` to record `current_setting('enable_progress_bar')` on every
    connection it opens, without changing what it returns to its real caller."""
    original = store_module._connect
    observed: list[bool] = []

    def spy(*args: object, **kwargs: object) -> object:
        connection = original(*args, **kwargs)  # type: ignore[arg-type]
        (value,) = connection.execute("select current_setting('enable_progress_bar')").fetchone()
        observed.append(value)
        return connection

    monkeypatch.setattr(store_module, "_connect", spy)
    return observed


def test_a_write_partition_connection_reports_the_progress_bar_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = _spy_on_connect(monkeypatch)

    store = PanelStore(tmp_path)
    store.write_partition(DATASET, 2026, (ColumnSpec("subject", "VARCHAR"),), [("000001.SZ",)])

    assert observed, "write_partition opened no connection through _connect -- proves nothing"
    assert observed == [False] * len(observed)


def test_a_read_connection_reports_the_progress_bar_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PanelStore(tmp_path)
    store.write_partition(DATASET, 2026, (ColumnSpec("subject", "VARCHAR"),), [("000001.SZ",)])

    observed = _spy_on_connect(monkeypatch)
    rows = store.query(DATASET, year=2026, columns=("subject",))

    assert rows == [("000001.SZ",)]
    assert observed, "query() opened no connection through _connect -- this test proves nothing"
    assert observed == [False] * len(observed)


def test_a_write_and_a_read_connection_print_nothing_to_stdout_even_forced_past_the_threshold(
    tmp_path: Path,
) -> None:
    """The decisive check: every connection `PanelStore` opens forced to behave as if its query
    were slow, and neither the write nor the read leaves any progress-bar text on the stdout a
    caller reads through a pipe."""
    write, read = _forced_stdout(tmp_path / "panel", "configured")

    assert _PROGRESS_BAR_MARKER not in write
    assert _PROGRESS_BAR_MARKER not in read


def test_removing_the_helpers_config_turns_this_red(tmp_path: Path) -> None:
    """The mutation check: with the two `SET enable_progress_bar...` statements patched out of
    `_connect`, the same forced write DOES print a progress bar to the child's stdout -- proving
    the check above is pinned to the helper's config, not to some other reason a bar might never
    appear."""
    write, _read = _forced_stdout(tmp_path / "panel", "unconfigured")

    assert _PROGRESS_BAR_MARKER in write, (
        "expected the mutated (unconfigured) _connect to leak a progress bar to stdout; it did "
        f"not ({write!r}), which would mean this file's stdout assertion is not actually testing "
        "V2-P6-021's fix"
    )
