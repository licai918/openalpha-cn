"""Point-in-time rules shared by ingestion, research, and replay."""

from dataclasses import dataclass, fields
from datetime import UTC, datetime


def ensure_aware(value: datetime) -> datetime:
    """Return ``value`` in UTC and reject datetimes without an offset."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class Timeline:
    """The four clocks required to reproduce what was knowable at a point in time."""

    event_time: datetime
    available_time: datetime
    ingested_time: datetime
    revision_time: datetime

    def __post_init__(self) -> None:
        for field in fields(self):
            value = ensure_aware(getattr(self, field.name))
            object.__setattr__(self, field.name, value)

        if self.ingested_time < self.available_time:
            raise ValueError("ingested_time cannot precede available_time")
        if self.revision_time < self.available_time:
            raise ValueError("revision_time cannot precede available_time")


def is_visible_at(timeline: Timeline, as_of: datetime) -> bool:
    """Return whether the information was available to a researcher at ``as_of``."""
    return timeline.available_time <= ensure_aware(as_of)
