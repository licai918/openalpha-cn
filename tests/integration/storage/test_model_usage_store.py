"""Tests for `SQLiteModelUsageStore` (V2-P0B-011): durable per-request usage accounting.

Integration-level, like the other `storage/` SQLite tests: append idempotency, conflict
rejection, and provider-scoped listing all depend on real SQLite behavior, not something
a plain-Python double could meaningfully stand in for.

This store predates the migration engine (V2-P0B-004) -- it moved here from
`models/governance.py` (V2-P0B-011), where it already created its own table as a
constructor side effect, exactly like `storage/memory.py`, `storage/sqlite.py`, and
`storage/batch.py` still do. It is not retrofitted onto `storage/migrations.py`'s
`create_validation_results`-style migration-only pattern: unlike `SQLiteValidationStore`,
its table already existed before this move, and moving code is not "the table needs to
be rebuilt" (see `storage/migrations.py`'s module docstring) -- there is no schema change
here at all, just a relocation of an unchanged class.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from openalpha_cn.models.governance import ModelUsageRecord
from openalpha_cn.storage.models import SQLiteModelUsageStore


def _record(*, request_id: str, provider_id: str = "test") -> ModelUsageRecord:
    return ModelUsageRecord(
        request_id=request_id,
        provider_id=provider_id,
        model="model",
        input_tokens=1_000,
        output_tokens=500,
        total_tokens=1_500,
        attempts=1,
        estimated_cost=Decimal("0.006000"),
        occurred_at=datetime(2026, 7, 24, 10, 30, tzinfo=UTC),
    )


@pytest.fixture
def store(tmp_path: Path) -> SQLiteModelUsageStore:
    return SQLiteModelUsageStore(tmp_path / "state.sqlite3")


def test_append_is_idempotent_for_the_identical_record(store: SQLiteModelUsageStore) -> None:
    record = _record(request_id="req-a")

    store.append(record)
    store.append(record)

    assert store.list(provider_id="test") == (record,)


def test_append_rejects_a_conflicting_reuse_of_the_same_request_id(
    store: SQLiteModelUsageStore,
) -> None:
    first = _record(request_id="req-b")
    second = _record(request_id="req-b", provider_id="other")

    store.append(first)

    with pytest.raises(ValueError, match="request_id conflicts"):
        store.append(second)


def test_list_filters_by_provider_id_and_returns_append_order(
    store: SQLiteModelUsageStore,
) -> None:
    first = _record(request_id="req-c", provider_id="alpha")
    second = _record(request_id="req-d", provider_id="alpha")
    other = _record(request_id="req-e", provider_id="beta")
    store.append(first)
    store.append(second)
    store.append(other)

    assert store.list(provider_id="alpha") == (first, second)
    assert store.list(provider_id="missing") == ()


def test_list_without_a_provider_filter_returns_every_record_in_append_order(
    store: SQLiteModelUsageStore,
) -> None:
    first = _record(request_id="req-f", provider_id="alpha")
    second = _record(request_id="req-g", provider_id="beta")
    store.append(first)
    store.append(second)

    assert store.list() == (first, second)
