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
`progress_bar_time` says otherwise: capturing real fd 1 around such a query (see
`_capture_real_stdout` below) catches the ANSI progress bar text every time, `current_setting`'s
`False` notwithstanding. `current_setting` on this DuckDB build, under pytest, answers a
question other than "will this connection print a progress bar" -- and the two tests below still
check it, because `V2-P6-021`'s brief asks for it and it is not actively wrong for a *configured*
connection (the helper's own `SET` always lands and always holds), but neither test relies on it
alone: each also drives a real, forced-slow query through real fd 1 and asserts nothing lands
there, which is the only assertion in this file that is provably tied to the actual defect.

The mutation check lives here as `test_removing_the_helpers_config_turns_this_red`, on the fd
assertion rather than on `current_setting` -- a version of that test built on `current_setting`
alone would stay green with the helper's two `SET` calls deleted, which was caught only by
running it that way while writing this file (see this task's final report for the fuller
account).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

import openalpha_cn.panel.store as store_module
from openalpha_cn.panel.store import ColumnSpec, PanelStore

DATASET = "progress_bar_probe"

_PROGRESS_BAR_MARKER = "▕"
"""One of the box-drawing characters DuckDB's progress bar renders; absence of it in a capture
is the test's actual pass condition, `current_setting` aside."""


@contextlib.contextmanager
def _capture_real_stdout(target: Path) -> Iterator[None]:
    """Redirect the real OS-level fd 1 to `target` for the duration of the block.

    DuckDB's progress bar is written by the C++ extension straight to the process's stdout file
    descriptor, bypassing Python's `sys.stdout` object -- `pytest`'s own output capture (and
    `capsys`/`capfd`'s *reported* text) does not see it either, which is what makes
    `current_setting` this file's only other option and why that option turned out to matter.
    Redirecting the real fd directly, then restoring it, is the one capture that actually sees
    what a subprocess's piped stdout would.
    """
    target.write_text("")
    saved_fd = os.dup(1)
    written_fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.dup2(written_fd, 1)
    os.close(written_fd)
    try:
        yield
    finally:
        os.dup2(saved_fd, 1)
        os.close(saved_fd)


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


def _force_every_connection_past_the_progress_bar_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make every connection `_connect` returns behave like it is running a slow query.

    Wraps the *current* `store_module._connect` (whatever a test already patched it to, real or
    mutated) and adds one statement after it: `SET progress_bar_time = 0`, so every later query
    on that connection is over DuckDB's threshold regardless of how long it actually takes. This
    runs strictly after whatever `_connect` itself already did to the connection -- unlike
    patching `duckdb.connect` directly, which would race the helper's own setup statements and
    corrupt them too, proving nothing about the helper's config specifically.
    """
    current = store_module._connect

    def forced(*args: object, **kwargs: object) -> object:
        connection = current(*args, **kwargs)  # type: ignore[arg-type]
        connection.execute("SET progress_bar_time = 0")
        return connection

    monkeypatch.setattr(store_module, "_connect", forced)


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisive check: force every connection `PanelStore` opens to behave as if its query
    were slow (`SET progress_bar_time = 0`, applied only after the real helper has already run
    -- see `_force_every_connection_past_the_progress_bar_threshold`), capture real fd 1 around
    a write and around a read, and assert neither left any progress-bar text there."""
    _force_every_connection_past_the_progress_bar_threshold(monkeypatch)
    store = PanelStore(tmp_path)

    write_capture = tmp_path / "write.stdout"
    with _capture_real_stdout(write_capture):
        store.write_partition(DATASET, 2026, (ColumnSpec("subject", "VARCHAR"),), [("000001.SZ",)])
    assert _PROGRESS_BAR_MARKER not in write_capture.read_text()

    read_capture = tmp_path / "read.stdout"
    with _capture_real_stdout(read_capture):
        rows = store.query(DATASET, year=2026, columns=("subject",))
    assert rows == [("000001.SZ",)]
    assert _PROGRESS_BAR_MARKER not in read_capture.read_text()


def test_removing_the_helpers_config_turns_this_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation check: with the two `SET enable_progress_bar...` statements patched out of
    `_connect` (the exact edit `V2-P6-021`'s review round asks be tried), a write forced past the
    progress-bar threshold DOES print to real fd 1 -- proving the fd assertion above is actually
    pinned to the helper's config, not to some other reason a bar might never appear."""

    def unconfigured_connect(database: str, *, read_only: bool = False) -> object:
        import duckdb

        return duckdb.connect(database, read_only=read_only)

    monkeypatch.setattr(store_module, "_connect", unconfigured_connect)
    _force_every_connection_past_the_progress_bar_threshold(monkeypatch)
    store = PanelStore(tmp_path)

    capture = tmp_path / "mutated.stdout"
    with _capture_real_stdout(capture):
        store.write_partition(DATASET, 2026, (ColumnSpec("subject", "VARCHAR"),), [("000001.SZ",)])

    assert _PROGRESS_BAR_MARKER in capture.read_text(), (
        "expected the mutated (unconfigured) _connect to leak a progress bar to stdout; it did "
        "not, which would mean this file's fd-capture assertion is not actually testing "
        "V2-P6-021's fix"
    )
