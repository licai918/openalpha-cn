"""Limit events reach the evidence plane through the builder and one composition-root opener.

`V2-P7-011`'s Task 3. The panel adapter (`panel_limit_events.limit_events_on`) produced the
events; this module proves what happens to them at the evidence boundary.

D1 decides the shape. A limit event is discrete, per-security, ~10² rows a day, so it belongs in
the evidence plane as an `EvidenceSnapshot` of an already-declared kind (`limit_up`,
`consecutive_board`, `broken_board`) -- never as a panel row. Hard rule 1's guard is that the
producer emits events only; `domain/test_limit_events.py` caps the count per session and this
file shows the stored rows are one snapshot per event.

Six acceptances:

1. **The snapshots are market-event evidence carrying the derived facts** -- one `EvidenceSnapshot`
   per event, the family and facts the builder's normalizers expect, and the four clocks the
   session's own publication instant implies.
2. **An evidence query before the session's 16:30 sees none of its events.** The clocks are the
   point-in-time contract, so this is what makes the plane safe to read.
3. **Re-running a session writes no second partition.** `evidence_id` excludes `ingested_time`,
   so a second run at a later clock is the same batch and the store returns the same partition.
4. **A restated session is refused by name instead of appended.** A second id set for one session
   is two answers to one question; `refuse_a_restated_session` names it rather than writing both.
5. **The CLI writes through the composition root opener** -- `evidence_store_at`, not a hand-built
   `ParquetEvidenceStore(runtime_dir / "evidence")` (v2 hard rule 5) -- and walks a `--through`
   range one session at a time.
6. **A session with no event writes nothing and says so.** No partition, no directory, one line.

No network: the panel is generated at test time and every read is from disk.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from panel_fixtures import (
    EXCHANGE,
    LIMIT_STREAK_FIRST_SESSION_INDEX,
    LIMIT_STREAK_SECURITY_INDEX,
    GeneratedPanel,
    generate_limit_streak_panel,
    write_generated_panel,
)
from typer.testing import CliRunner

from openalpha_cn.cli import app
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.evidence.limit_events import (
    PANEL_DERIVED_METADATA,
    LimitEvidenceConflictError,
    limit_event_snapshots,
    refuse_a_restated_session,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.panel_limit_events import limit_events_on
from openalpha_cn.storage.parquet import ParquetEvidenceStore

runner = CliRunner()

FETCHED_AT = datetime(2026, 1, 18, 4, 0, tzinfo=UTC)
"""A clock after the panel's last session, so every snapshot is knowable and ingested."""


def _panel(tmp_path: Path) -> tuple[GeneratedPanel, PanelStore]:
    panel = generate_limit_streak_panel()
    store = PanelStore(tmp_path / "panel")
    write_generated_panel(store, panel)
    return panel, store


def _events(panel: GeneratedPanel, store: PanelStore, session_index: int) -> tuple[datetime, tuple]:
    day = panel.sessions[session_index]
    published = session_publication_instant(day)
    return day, limit_events_on(store, session=day, as_of=published, exchange=panel.exchange)


def _ids(snapshots: tuple[EvidenceSnapshot, ...]) -> set[str]:
    return {snapshot.evidence_id for snapshot in snapshots}


def test_snapshots_are_market_event_family_and_carry_the_derived_facts(tmp_path: Path) -> None:
    panel, store = _panel(tmp_path)
    day, events = _events(panel, store, LIMIT_STREAK_FIRST_SESSION_INDEX + 1)
    assert len(events) == 1
    event = events[0]

    snapshots = limit_event_snapshots(events, session=day, fetched_at=FETCHED_AT)

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.subject == event.ts_code
    assert snapshot.kind == event.kind == "consecutive_board"
    assert snapshot.source_id == PANEL_DERIVED_METADATA.provider_id == "openalpha-panel-derived"
    assert snapshot.redistribution == "restricted"
    assert snapshot.source_uri is not None
    assert "Tushare" in snapshot.source_license
    assert "daily" in snapshot.source_license
    assert "stk_limit" in snapshot.source_license
    assert snapshot.summary == event.summary()

    published = session_publication_instant(day)
    assert snapshot.timeline.available_time == published
    assert snapshot.timeline.revision_time == published
    assert snapshot.timeline.ingested_time == FETCHED_AT
    # 15:00 Asia/Shanghai on the session, normalised to UTC by `Timeline`.
    assert snapshot.timeline.event_time == datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC)

    payload = snapshot.payload
    assert payload["schema"] == "a-share-evidence/v1"
    assert payload["family"] == "market_event"
    facts = payload["facts"]
    assert facts["board_count"] == event.board_count == 2
    assert facts["close"] == event.close
    assert facts["pct_change"] == pytest.approx(event.pct_chg)
    assert facts["one_price"] is True
    assert "redistribution_restricted" in payload["quality_flags"]
    assert PANEL_DERIVED_METADATA.caching_policy == "local-permitted"


def test_an_evidence_query_before_1630_sees_none_of_the_sessions_events(tmp_path: Path) -> None:
    panel, store = _panel(tmp_path)
    day, events = _events(panel, store, LIMIT_STREAK_FIRST_SESSION_INDEX + 1)
    snapshots = limit_event_snapshots(events, session=day, fetched_at=FETCHED_AT)
    evidence = ParquetEvidenceStore(tmp_path / "evidence")
    evidence.append(snapshots)

    published = session_publication_instant(day)
    assert evidence.query(as_of=published - timedelta(seconds=1)) == ()
    assert _ids(evidence.query(as_of=published)) == _ids(snapshots)


def test_rerunning_a_session_writes_no_second_partition(tmp_path: Path) -> None:
    panel, store = _panel(tmp_path)
    day, events = _events(panel, store, LIMIT_STREAK_FIRST_SESSION_INDEX + 1)
    evidence = ParquetEvidenceStore(tmp_path / "evidence")
    first_partition = evidence.append(
        limit_event_snapshots(events, session=day, fetched_at=FETCHED_AT)
    )

    rebuilt = limit_event_snapshots(events, session=day, fetched_at=FETCHED_AT + timedelta(hours=1))
    assert _ids(rebuilt) == _ids(evidence.query(as_of=session_publication_instant(day)))
    assert evidence.append(rebuilt) == first_partition
    assert len(list((tmp_path / "evidence").glob("part-*.parquet"))) == 1


def test_a_restated_session_is_refused_by_name_instead_of_appended(tmp_path: Path) -> None:
    panel, store = _panel(tmp_path)
    day, events = _events(panel, store, LIMIT_STREAK_FIRST_SESSION_INDEX + 1)
    evidence = ParquetEvidenceStore(tmp_path / "evidence")
    snapshots = limit_event_snapshots(events, session=day, fetched_at=FETCHED_AT)
    evidence.append(snapshots)

    # The same id set is not a restatement, whatever order it arrives in.
    refuse_a_restated_session(evidence, session=day, arriving=snapshots)

    restated = limit_event_snapshots(
        (replace(events[0], pct_chg=events[0].pct_chg + 1.0),),
        session=day,
        fetched_at=FETCHED_AT,
    )
    with pytest.raises(LimitEvidenceConflictError) as raised:
        refuse_a_restated_session(evidence, session=day, arriving=restated)

    assert day.isoformat() in str(raised.value)
    # The refusal is before any append: the stored session is untouched.
    stored = evidence.query(as_of=session_publication_instant(day))
    assert _ids(stored) == _ids(snapshots)


def test_the_cli_writes_through_the_composition_root_opener(tmp_path: Path) -> None:
    panel = generate_limit_streak_panel()
    runtime_dir = tmp_path / "runtime"
    write_generated_panel(PanelStore(runtime_dir / "panel"), panel)
    first = LIMIT_STREAK_FIRST_SESSION_INDEX
    start = panel.sessions[first + 1]  # a consecutive-board session
    through = panel.sessions[first + 3]  # two sessions later; the halt session lies between

    result = runner.invoke(
        app,
        [
            "evidence",
            "limit-events",
            "--session",
            start.isoformat(),
            "--through",
            through.isoformat(),
            "--runtime-dir",
            str(runtime_dir),
            "--exchange",
            EXCHANGE,
        ],
    )

    assert result.exit_code == 0, result.output
    partitions = sorted((runtime_dir / "evidence").glob("part-*.parquet"))
    # Two sessions carried an event; the halt session between them wrote nothing.
    assert len(partitions) == 2
    assert f"NO limit events session={panel.sessions[first + 2].isoformat()}" in result.output

    code = panel.securities[LIMIT_STREAK_SECURITY_INDEX]
    stored = ParquetEvidenceStore(runtime_dir / "evidence").query(
        as_of=session_publication_instant(through)
    )
    assert len(stored) == 2
    assert {snapshot.subject for snapshot in stored} == {code}
    assert {snapshot.kind for snapshot in stored} == {"consecutive_board"}


def test_a_session_with_no_event_writes_nothing_and_says_so(tmp_path: Path) -> None:
    panel = generate_limit_streak_panel()
    runtime_dir = tmp_path / "runtime"
    write_generated_panel(PanelStore(runtime_dir / "panel"), panel)
    day = panel.sessions[0]

    result = runner.invoke(
        app,
        [
            "evidence",
            "limit-events",
            "--session",
            day.isoformat(),
            "--runtime-dir",
            str(runtime_dir),
            "--exchange",
            EXCHANGE,
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"NO limit events session={day.isoformat()}" in result.output
    assert not list((runtime_dir / "evidence").glob("part-*.parquet"))
