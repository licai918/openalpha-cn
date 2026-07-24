from datetime import UTC, datetime
from pathlib import Path

from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import Timeline
from openalpha_cn.storage.parquet import ParquetEvidenceStore


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
