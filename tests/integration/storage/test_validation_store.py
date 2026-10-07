"""Tests for `SQLiteValidationStore` (V2-P0B-010): the durable outcome-validation ledger.

Integration-level, like the other `storage/` SQLite tests: idempotency, conflict
rejection, and the two query paths (`decision_id`, `signal_id`) all depend on real
SQLite behavior, not something a plain-Python double could meaningfully stand in for.

`SQLiteValidationStore` deliberately does not create its own table (see its module
docstring), so every test here runs `run_migrations()` first -- exactly the sequence
`build_storage()` uses -- before constructing the store.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from openalpha_cn.domain.validation import AttributionTerm, ValidationResult
from openalpha_cn.storage.migrations import run_migrations
from openalpha_cn.storage.validation import SQLiteValidationStore


def _result(
    *,
    signal_id: str,
    decision_id: str,
    observation_start: datetime,
    realized_return: float = 0.1,
    benchmark_return: float = 0.02,
    transaction_cost: float = 0.005,
) -> ValidationResult:
    net_active_return = realized_return - benchmark_return - transaction_cost
    return ValidationResult(
        signal_id=signal_id,
        decision_id=decision_id,
        observation_start=observation_start,
        observation_end=observation_start + timedelta(days=5),
        realized_return=realized_return,
        benchmark_return=benchmark_return,
        transaction_cost=transaction_cost,
        attribution=(
            AttributionTerm(
                category="rule", name="decision-policy", contribution=net_active_return
            ),
        ),
        confidence=0.6,
    )


@pytest.fixture
def store(tmp_path: Path, frozen_clock):
    path = tmp_path / "state.sqlite3"
    run_migrations(path, clock=frozen_clock)
    return SQLiteValidationStore(path)


def test_append_is_idempotent_for_the_identical_result(store: SQLiteValidationStore, frozen_now):
    result = _result(signal_id="sig_a", decision_id="dec_a", observation_start=frozen_now)

    store.append(result)
    store.append(result)

    assert store.list_by_decision("dec_a") == (result,)


def test_append_rejects_a_conflicting_reuse_of_the_same_validation_id(
    store: SQLiteValidationStore, frozen_now, tmp_path: Path
):
    """`validation_id` is content-derived (like `report_id`/`order_id`'s uniqueness
    constraint), so two *different* `ValidationResult` objects essentially never share
    one in practice. To exercise the conflict branch, this hand-inserts a row directly
    via `sqlite3` under an ID that a real object will also produce, with a different
    payload -- reproducing the only way this can happen: a corrupted or hand-edited row,
    not a hash collision between two honestly-computed results.
    """
    result = _result(signal_id="sig_b", decision_id="dec_b", observation_start=frozen_now)
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO validation_results (validation_id, decision_id, signal_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (result.validation_id, "dec_b", "sig_b", '{"tampered": true}'),
        )

    with pytest.raises(ValueError, match="validation_id conflicts"):
        store.append(result)


def test_list_by_decision_returns_only_that_decisions_results_in_append_order(
    store: SQLiteValidationStore, frozen_now
):
    first = _result(signal_id="sig_c", decision_id="dec_c", observation_start=frozen_now)
    second = _result(
        signal_id="sig_c",
        decision_id="dec_c",
        observation_start=frozen_now,
        realized_return=0.2,
    )
    other = _result(signal_id="sig_d", decision_id="dec_d", observation_start=frozen_now)
    store.append(first)
    store.append(second)
    store.append(other)

    assert store.list_by_decision("dec_c") == (first, second)
    assert store.list_by_decision("dec_missing") == ()


def test_list_by_signal_returns_only_that_signals_results_in_append_order(
    store: SQLiteValidationStore, frozen_now
):
    first = _result(signal_id="sig_e", decision_id="dec_e", observation_start=frozen_now)
    second = _result(
        signal_id="sig_e",
        decision_id="dec_f",
        observation_start=frozen_now,
        realized_return=0.3,
    )
    other = _result(signal_id="sig_g", decision_id="dec_g", observation_start=frozen_now)
    store.append(first)
    store.append(second)
    store.append(other)

    assert store.list_by_signal("sig_e") == (first, second)
    assert store.list_by_signal("sig_missing") == ()
