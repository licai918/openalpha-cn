"""Proves `build_storage()` wires a usable `model_usage_store` (V2-P0B-011).

Companion to `test_composition_validation.py`'s pattern for a different store: exercises the
actual `StorageContainer.model_usage_store` field end to end (append, then list) after exactly
one `build_storage()` call against a brand-new `runtime_dir` -- the same shape as every other
store this container assembles.

`SQLiteModelUsageStore` (`storage/models.py`) has existed, been schema-complete, and been
tested (`tests/unit/models/test_model_governance.py`,
`tests/integration/storage/test_model_usage_store.py`) since `V2-P0B-011`. None of that made it
reachable from `build_storage()`: before this test existed to say so, `storage.model_usage_store`
was not an attribute of `StorageContainer` at all, so this test failed with
`AttributeError: 'StorageContainer' object has no attribute 'model_usage_store'` -- not a
behavioral defect in the store itself (which was already correct, per its own test suite), but
a composition-root gap identical in shape to the one `test_composition_validation.py` guards
against for `validation_store`.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openalpha_cn.models.governance import ModelUsageRecord
from openalpha_cn.runtime.composition import build_storage


def test_model_usage_store_is_immediately_usable_after_one_fresh_build_storage_call(
    tmp_path: Path, migration_clock: Callable[[], datetime], migration_now: datetime
) -> None:
    runtime_dir = tmp_path / "runtime"

    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    record = ModelUsageRecord(
        request_id="req_composition",
        provider_id="prov_composition",
        model="model-composition",
        input_tokens=1_000,
        output_tokens=250,
        total_tokens=1_250,
        attempts=1,
        estimated_cost=Decimal("0.004500"),
        occurred_at=migration_now,
    )

    storage.model_usage_store.append(record)

    assert storage.model_usage_store.list() == (record,)
    assert storage.model_usage_store.list(provider_id="prov_composition") == (record,)
    assert storage.model_usage_store.list(provider_id="missing") == ()


def test_model_usage_store_shares_the_container_wide_state_sqlite3_file(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Same `runtime_dir / "state.sqlite3"` every other SQLite-backed store here uses --
    not a private file of its own -- which is the entire point of routing it through
    `build_storage()` instead of a caller hand-rolling its own path."""
    runtime_dir = tmp_path / "runtime"

    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    assert storage.model_usage_store.path == runtime_dir / "state.sqlite3"
    assert storage.model_usage_store.path == storage.job_store.path
