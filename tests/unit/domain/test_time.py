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


def test_visibility_waits_for_the_revision_clock() -> None:
    """A version revised at 10:00 is not what anyone could read at 9:59, even though the record
    itself first became available at 9:35: the stored version is the revised one, and before its
    revision instant nobody had seen it."""
    timeline = Timeline(
        event_time=datetime(2026, 7, 24, 9, 30, tzinfo=UTC),
        available_time=datetime(2026, 7, 24, 9, 35, tzinfo=UTC),
        ingested_time=datetime(2026, 7, 24, 10, 1, tzinfo=UTC),
        revision_time=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
    )

    assert is_visible_at(timeline, datetime(2026, 7, 24, 9, 35, tzinfo=UTC)) is False
    assert is_visible_at(timeline, datetime(2026, 7, 24, 9, 59, tzinfo=UTC)) is False
    assert is_visible_at(timeline, datetime(2026, 7, 24, 10, 0, tzinfo=UTC)) is True


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
