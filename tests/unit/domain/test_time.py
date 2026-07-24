from datetime import UTC, datetime, timedelta, timezone

import pytest

from openalpha_cn.domain.time import (
    Timeline,
    ensure_aware,
    is_visible_at,
)


def test_ensure_aware_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ensure_aware(datetime(2026, 7, 24, 9, 30))


def test_ensure_aware_normalizes_to_utc() -> None:
    china_time = datetime(
        2026,
        7,
        24,
        9,
        30,
        tzinfo=timezone(timedelta(hours=8)),
    )

    assert ensure_aware(china_time) == datetime(2026, 7, 24, 1, 30, tzinfo=UTC)


def test_visibility_uses_available_time_not_event_time() -> None:
    timeline = Timeline(
        event_time=datetime(2026, 7, 24, 9, 30, tzinfo=UTC),
        available_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
        ingested_time=datetime(2026, 7, 24, 9, 36, tzinfo=UTC),
        revision_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
    )

    assert is_visible_at(timeline, datetime(2026, 7, 24, 9, 34, tzinfo=UTC)) is False
    assert is_visible_at(timeline, datetime(2026, 7, 24, 9, 35, tzinfo=UTC)) is True


def test_timeline_rejects_ingestion_before_information_was_available() -> None:
    with pytest.raises(ValueError, match="ingested_time"):
        Timeline(
            event_time=datetime(2026, 7, 24, 9, 30, tzinfo=UTC),
            available_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
            ingested_time=datetime(2026, 7, 24, 9, 34, tzinfo=UTC),
            revision_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
        )


def test_timeline_rejects_revision_before_initial_availability() -> None:
    with pytest.raises(ValueError, match="revision_time"):
        Timeline(
            event_time=datetime(2026, 7, 24, 9, 30, tzinfo=UTC),
            available_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
            ingested_time=datetime(2026, 7, 24, 9, 36, tzinfo=UTC),
            revision_time=datetime(2026, 7, 24, 9, 34, tzinfo=UTC),
        )
