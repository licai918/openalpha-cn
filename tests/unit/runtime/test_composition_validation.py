"""Proves `build_storage()` wires a usable `validation_store` (V2-P0B-010).

Complements `test_composition_migrations.py`'s numeric assertions on schema versions:
this test exercises the actual `StorageContainer.validation_store` field end to end
(append, then query by `decision_id`/`signal_id`) after exactly one `build_storage()`
call against a brand-new `runtime_dir` -- the real-usability property the
`create_validation_results` migration's ordering (before the demo migration; see
`storage/migrations.py`) exists to guarantee. Without that ordering, this test would fail
with `sqlite3.OperationalError: no such table: validation_results`, because the demo
migration's routine first-call deferral would have blocked it from ever running.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from openalpha_cn.domain.validation import AttributionTerm, ValidationResult
from openalpha_cn.runtime.composition import build_storage


def test_validation_store_is_immediately_usable_after_one_fresh_build_storage_call(
    tmp_path: Path, migration_clock: Callable[[], datetime], migration_now: datetime
) -> None:
    runtime_dir = tmp_path / "runtime"

    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    result = ValidationResult(
        signal_id="sig_composition",
        decision_id="dec_composition",
        observation_start=migration_now,
        observation_end=migration_now + timedelta(days=5),
        realized_return=0.08,
        benchmark_return=0.02,
        transaction_cost=0.003,
        attribution=(AttributionTerm(category="rule", name="decision-policy", contribution=0.057),),
        confidence=0.5,
    )

    storage.validation_store.append(result)

    assert storage.validation_store.list_by_decision("dec_composition") == (result,)
    assert storage.validation_store.list_by_signal("sig_composition") == (result,)
