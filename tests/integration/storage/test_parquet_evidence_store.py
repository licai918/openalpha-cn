from datetime import UTC, datetime, timedelta
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
