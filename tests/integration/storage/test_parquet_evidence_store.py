import contextlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import openalpha_cn.storage.parquet as parquet_module
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.storage.parquet import ParquetEvidenceStore

_PROGRESS_BAR_MARKER = "▕"
"""One of the box-drawing characters DuckDB's progress bar renders (see the V2-P6-021 section
below); absence of it in a real-fd-1 capture is what that section's tests actually trust."""


@contextlib.contextmanager
def _capture_real_stdout(target: Path) -> Iterator[None]:
    """Redirect the real OS-level fd 1 to `target` for the duration of the block.

    DuckDB's progress bar is written by the C++ extension straight to the process's stdout file
    descriptor, bypassing Python's `sys.stdout` object -- pytest's own capture does not see it
    either. Mirrors `tests/unit/panel/test_store_progress_bar.py::_capture_real_stdout`.
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


def evidence(*, subject: str, event_hour: int, available_hour: int) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        subject=subject,
        kind="limit_up",
        timeline=Timeline(
            event_time=datetime(2026, 7, 24, event_hour, 0, tzinfo=UTC),
            available_time=datetime(2026, 7, 24, available_hour, 0, tzinfo=UTC),
            ingested_time=datetime(2026, 7, 24, available_hour, 1, tzinfo=UTC),
            revision_time=datetime(2026, 7, 24, available_hour, 0, tzinfo=UTC),
        ),
        source_id="synthetic.limit-up",
        source_uri=f"fixture://{subject}",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary=f"{subject} reached its daily limit.",
        payload={"close": 10.5},
    )


def test_point_in_time_query_uses_available_time_not_event_time(tmp_path: Path) -> None:
    store = ParquetEvidenceStore(tmp_path / "events")
    visible = evidence(subject="000001.SZ", event_hour=9, available_hour=10)
    future = evidence(subject="000002.SZ", event_hour=9, available_hour=11)
    store.append((visible, future))

    result = store.query(as_of=datetime(2026, 7, 24, 10, 30, tzinfo=UTC))

    assert result == (visible,)


AVAILABLE_AT = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
REVISED_AT = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)


def versioned(*, revised: bool) -> EvidenceSnapshot:
    """One record in two versions: as first published, and as revised two days later.

    The revision changes the payload, so the two carry different content hashes and different
    evidence IDs and can sit in the store side by side -- which is the shape an append-only
    evidence plane holds when the original was imported before the revision was.
    """
    return EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(
            event_time=datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
            available_time=AVAILABLE_AT,
            ingested_time=REVISED_AT if revised else AVAILABLE_AT,
            revision_time=REVISED_AT if revised else AVAILABLE_AT,
        ),
        source_id="synthetic.limit-up",
        source_uri="fixture://000001.SZ",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="000001.SZ reached its daily limit.",
        payload={"close": 10.45 if revised else 10.5},
    )


def test_a_revised_version_is_withheld_until_its_revision_and_the_original_answers_before_it(
    tmp_path: Path,
) -> None:
    """The store keeps both versions; the query answers with the one knowable at `as_of`.

    Between first availability and the revision only the original is returned; from the
    revision instant on, both are, because the plane is append-only and nothing here chooses
    between versions. Boundaries on both sides are asserted, so a predicate that stopped
    reading the revision clock -- or read it with `<` -- goes red here.
    """
    store = ParquetEvidenceStore(tmp_path / "events")
    original = versioned(revised=False)
    revised = versioned(revised=True)
    store.append((original,))
    store.append((revised,))

    assert original.evidence_id != revised.evidence_id
    assert store.query(as_of=AVAILABLE_AT) == (original,)
    assert store.query(as_of=REVISED_AT - timedelta(microseconds=1)) == (original,)
    assert store.query(as_of=REVISED_AT) == tuple(
        sorted((original, revised), key=lambda item: item.evidence_id)
    )


def test_a_store_holding_only_the_revised_version_answers_nothing_before_the_revision(
    tmp_path: Path,
) -> None:
    """The lossy side of the same rule, stated rather than left to be inferred: the version
    before the revision exists only if it was stored, and a store that first saw the record
    after it was revised has nothing to answer with inside the window."""
    store = ParquetEvidenceStore(tmp_path / "events")
    revised = versioned(revised=True)
    store.append((revised,))

    assert store.query(as_of=AVAILABLE_AT) == ()
    assert store.query(as_of=REVISED_AT - timedelta(microseconds=1)) == ()
    assert store.query(as_of=REVISED_AT) == (revised,)


def test_query_can_filter_subject_and_kind(tmp_path: Path) -> None:
    store = ParquetEvidenceStore(tmp_path / "events")
    first = evidence(subject="000001.SZ", event_hour=9, available_hour=10)
    second = evidence(subject="000002.SZ", event_hour=9, available_hour=10)
    store.append((first, second))

    result = store.query(
        as_of=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        subject="000002.SZ",
        kind="limit_up",
    )

    assert result == (second,)


def test_append_is_idempotent_for_the_same_evidence_batch(tmp_path: Path) -> None:
    store = ParquetEvidenceStore(tmp_path / "events")
    item = evidence(subject="000001.SZ", event_hour=9, available_hour=10)

    first_path = store.append((item,))
    second_path = store.append((item,))

    assert first_path == second_path
    assert len(tuple((tmp_path / "events").glob("*.parquet"))) == 1


# --- V2-P6-021: every connection this store opens disables the progress bar -----------------
#
# See `openalpha_cn.panel.store::_connect`'s docstring (mirrored on
# `openalpha_cn.storage.parquet::_connect`) and `tests/unit/test_duckdb_progress_bar_guard.py`'s
# module docstring for why: DuckDB prints an ANSI progress bar straight to the real stdout file
# descriptor for any query running past `progress_bar_time`, which corrupts a caller parsing
# this process's stdout. Spying on `parquet_module._connect` -- the only place this module may
# call `duckdb.connect` (enforced by the guard test) -- rather than on `duckdb.connect` itself,
# so the setting is read off the exact connection `append`/`query` used.
#
# `current_setting('enable_progress_bar')` turned out not to be trustworthy under this pytest
# run: a brand-new `duckdb.connect(":memory:")`, no `_connect` involved at all, reports it as
# `False` by default the moment it runs under `pytest` (reproduced on a single-test,
# no-project-conftest file, so this is a DuckDB/pytest interaction, not this repository's
# fixtures) -- yet a query forced past `progress_bar_time` on that same "False"-reporting,
# unconfigured connection still prints the bar to real fd 1. The two `current_setting` tests
# below stay (`V2-P6-021`'s brief asks for exactly this check, and it is not actively wrong for
# a *configured* connection, since the helper's own `SET` always lands), but the assertion this
# file actually trusts is `test_append_and_query_print_nothing_to_stdout_even_forced_past_the_
# threshold` and its mutation check, both against real fd 1.


def _spy_on_connect(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    original = parquet_module._connect
    observed: list[bool] = []

    def spy() -> object:
        connection = original()
        (value,) = connection.execute("select current_setting('enable_progress_bar')").fetchone()
        observed.append(value)
        return connection

    monkeypatch.setattr(parquet_module, "_connect", spy)
    return observed


def _force_every_connection_past_the_progress_bar_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make every connection `_connect` returns behave like it is running a slow query.

    Applies `SET progress_bar_time = 0` strictly after whatever the current `_connect` (real or
    mutated) already did to the connection, so this never races the helper's own setup
    statements -- see the twin helper in `tests/unit/panel/test_store_progress_bar.py`, which
    this mirrors for the same reason.
    """
    current = parquet_module._connect

    def forced() -> object:
        connection = current()
        connection.execute("SET progress_bar_time = 0")
        return connection

    monkeypatch.setattr(parquet_module, "_connect", forced)


def test_an_append_connection_reports_the_progress_bar_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = _spy_on_connect(monkeypatch)

    store = ParquetEvidenceStore(tmp_path / "events")
    store.append((evidence(subject="000001.SZ", event_hour=9, available_hour=10),))

    assert observed, "append() opened no connection through _connect -- this test proves nothing"
    assert observed == [False] * len(observed)


def test_a_query_connection_reports_the_progress_bar_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParquetEvidenceStore(tmp_path / "events")
    item = evidence(subject="000001.SZ", event_hour=9, available_hour=10)
    store.append((item,))

    observed = _spy_on_connect(monkeypatch)
    result = store.query(as_of=datetime(2026, 7, 24, 12, 0, tzinfo=UTC))

    assert result == (item,)
    assert observed, "query() opened no connection through _connect -- this test proves nothing"
    assert observed == [False] * len(observed)


def test_append_and_query_print_nothing_to_stdout_even_forced_past_the_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisive check: force every connection this store opens to behave as if its query
    were slow, capture real fd 1 (DuckDB's progress bar bypasses Python's `sys.stdout`, so
    nothing short of the real file descriptor sees it) around an `append` and around a `query`,
    and assert neither left any progress-bar text there."""
    _force_every_connection_past_the_progress_bar_threshold(monkeypatch)
    store = ParquetEvidenceStore(tmp_path / "events")
    item = evidence(subject="000001.SZ", event_hour=9, available_hour=10)

    append_capture = tmp_path / "append.stdout"
    with _capture_real_stdout(append_capture):
        store.append((item,))
    assert _PROGRESS_BAR_MARKER not in append_capture.read_text()

    query_capture = tmp_path / "query.stdout"
    with _capture_real_stdout(query_capture):
        result = store.query(as_of=datetime(2026, 7, 24, 12, 0, tzinfo=UTC))
    assert result == (item,)
    assert _PROGRESS_BAR_MARKER not in query_capture.read_text()


def test_removing_the_helpers_config_turns_this_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation check: with the `SET` statements patched out of `_connect`, an append
    forced past the progress-bar threshold DOES print to real fd 1 -- proving the fd assertion
    above is actually pinned to the helper's config."""
    import duckdb

    monkeypatch.setattr(parquet_module, "_connect", lambda: duckdb.connect(":memory:"))
    _force_every_connection_past_the_progress_bar_threshold(monkeypatch)
    store = ParquetEvidenceStore(tmp_path / "events")

    capture = tmp_path / "mutated.stdout"
    with _capture_real_stdout(capture):
        store.append((evidence(subject="000001.SZ", event_hour=9, available_hour=10),))

    assert _PROGRESS_BAR_MARKER in capture.read_text(), (
        "expected the mutated (unconfigured) _connect to leak a progress bar to stdout; it did "
        "not, which would mean this file's fd-capture assertion is not actually testing "
        "V2-P6-021's fix"
    )
