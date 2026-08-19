"""`ReplayRunner` classifies look-ahead violations by exception type (V2-P0B-014).

Audit F46: the replay runner used to recognise a look-ahead violation by matching
substrings ("look-ahead", "not visible") against `str(error)`. That string-matching had
two silent failure modes:

1. Any message rewrite -- translation, added context, rewording -- silently zeroed
   `ReplayReport.look_ahead_violations`, and the frozen-corpus test only asserts the
   *count*, so it would never notice.
2. Any unrelated `ValueError` that happened to share those words got miscounted as a
   look-ahead violation.

`test_look_ahead_violation_is_still_detected_after_its_message_is_rewritten` below is the
one test the brief calls out as the actual acceptance criterion: it proves detection
survives an arbitrary message change (failure mode 1).
`test_unrelated_value_error_with_look_ahead_wording_is_not_miscounted` proves the reverse
(failure mode 2). Both drive the real `ReplayRunner.run()` code path -- not a helper
extracted just for the test -- by monkeypatching `ResearchEngine.run_cycle` to raise a
controlled exception, since `ReplayCase.validate_point_in_time` already rejects any
genuinely invisible evidence before a case can reach the runner's loop at all (see
`test_replay_case_with_invisible_evidence_raises_look_ahead_violation_error` below for
that raise site in isolation).

Both real raise sites live inside a pydantic `@model_validator(mode="after")`, so pydantic
re-wraps whatever they raise into its own `ValidationError` before it can reach
`ReplayRunner.run()`'s `except` clause -- `_is_look_ahead_violation` (`backtest/replay.py`)
unwraps that to find the original exception. `test_is_look_ahead_violation_unwraps_a_real_
pydantic_validation_error` and `test_is_look_ahead_violation_rejects_an_unrelated_real_
validation_error` exercise that unwrapping directly against real `ValidationError`s (not
hand-built fakes), because -- as the first of those two tests' docstring explains -- that
branch cannot actually be reached through `ReplayRunner.run()` in production today: the two
model validators check the identical predicate over the identical inputs, so they can never
disagree given a `ReplayCorpus` built the normal way.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.replay import (
    ReplayCase,
    ReplayCorpus,
    ReplayReport,
    ReplayRunner,
    _is_look_ahead_violation,
)
from openalpha_cn.backtest.validation import OutcomeObservation
from openalpha_cn.domain.evidence import EvidenceSnapshot, LookAheadViolationError
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.time import Timeline
from openalpha_cn.runtime.contracts import ResearchRunResult
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.storage.migrations import (
    ADD_RUNS_MODE_PROJECTION_VERSION,
    BASELINE_VERSION,
    CREATE_QUERY_PATH_INDEXES_VERSION,
    CREATE_VALIDATION_RESULTS_VERSION,
    DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    REWRITE_CONTRACT_IDENTITIES_VERSION,
    SPLIT_BATCH_TASK_ITEMS_VERSION,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.sqlite import SQLiteRunRepository
from openalpha_cn.storage.validation import SQLiteValidationStore


def _migrated_validation_store(
    path: Path, *, clock: Callable[[], datetime]
) -> SQLiteValidationStore:
    """Build a real, migrated `SQLiteValidationStore` the same way `build_storage()` does.

    `SQLiteValidationStore` deliberately never creates its own table (see its module
    docstring): `run_migrations()` must run first, exactly like `test_validation_store.py`'s
    `store` fixture and `runtime/composition.py#build_storage`.
    """
    run_migrations(path, clock=clock)
    return SQLiteValidationStore(path)


def _successful_single_case_corpus(
    *,
    run_id: str,
    frozen_now: datetime,
    evidence_item: EvidenceSnapshot,
) -> ReplayCorpus:
    """A one-case corpus whose evidence is genuinely visible at `as_of`, so the case
    runs the real `ResearchEngine` end to end (twice, deterministically) and succeeds --
    unlike `_single_case_corpus` above (empty evidence, used only with a monkeypatched
    `run_cycle` that never reaches the validator)."""
    case = ReplayCase(
        run_id=run_id,
        trading_day=frozen_now.date(),
        subject="000001.SZ",
        as_of=frozen_now,
        evidence=(evidence_item,),
        outcome=OutcomeObservation(
            observation_start=frozen_now,
            observation_end=frozen_now + timedelta(hours=1),
            start_price=10.0,
            end_price=10.5,
            benchmark_return=0.01,
            transaction_cost=0.001,
        ),
    )
    return ReplayCorpus(
        schema_version="openalpha-replay-corpus/v1",
        trading_days=(frozen_now.date(),),
        cases=(case,),
    )


def _single_case_corpus(*, as_of: datetime, trading_day: date) -> ReplayCorpus:
    case = ReplayCase(
        run_id="replay_case_look_ahead",
        trading_day=trading_day,
        subject="000001.SZ",
        as_of=as_of,
        evidence=(),
        outcome=OutcomeObservation(
            observation_start=as_of,
            observation_end=as_of + timedelta(hours=1),
            start_price=10.0,
            end_price=10.5,
            benchmark_return=0.01,
            transaction_cost=0.001,
        ),
    )
    return ReplayCorpus(
        schema_version="openalpha-replay-corpus/v1",
        trading_days=(trading_day,),
        cases=(case,),
    )


def _run_with_engine_raising(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    error: Exception,
    clock: Callable[[], datetime],
) -> ReplayReport:
    as_of = datetime(2026, 7, 24, 9, 35, tzinfo=UTC)
    trading_day = date(2026, 7, 24)
    corpus = _single_case_corpus(as_of=as_of, trading_day=trading_day)

    def _fake_run_cycle(self: ResearchEngine, request: ResearchRunRequest) -> ResearchRunResult:
        raise error

    monkeypatch.setattr(ResearchEngine, "run_cycle", _fake_run_cycle)

    validation_store = _migrated_validation_store(tmp_path / "state.sqlite3", clock=clock)
    return ReplayRunner(
        code_commit="0123456789abcdef",
        config_digest="d" * 64,
        random_seed=7,
    ).run(
        corpus=corpus,
        state_path=tmp_path / "replay.sqlite3",
        validation_store=validation_store,
        clock=clock,
    )


def test_look_ahead_violation_is_still_detected_after_its_message_is_rewritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    migration_clock: Callable[[], datetime],
) -> None:
    """The core acceptance test: same type, deliberately different wording.

    This message contains neither "look-ahead" nor "not visible" -- the two substrings
    the old code matched on -- so a classifier that still depended on message text would
    report zero violations here. A type-based classifier must still report one.
    """
    reworded = LookAheadViolationError(
        "point-in-time guard rejected this observation; see the audit trail for details"
    )

    report = _run_with_engine_raising(monkeypatch, tmp_path, error=reworded, clock=migration_clock)

    assert report.total_cases == 1
    assert report.look_ahead_violations == 1
    assert len(report.failures) == 1


def test_unrelated_value_error_with_look_ahead_wording_is_not_miscounted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    migration_clock: Callable[[], datetime],
) -> None:
    """A plain `ValueError` that happens to contain the old magic words must not count.

    This is failure mode 2 from the audit: the old substring match would have counted
    this as a look-ahead violation purely because of its wording, even though it is a
    different exception type raised for an unrelated reason.
    """
    unrelated = ValueError(
        "cache entry for this symbol is not visible in the currently loaded look-ahead index shard"
    )

    report = _run_with_engine_raising(monkeypatch, tmp_path, error=unrelated, clock=migration_clock)

    assert report.total_cases == 1
    assert report.look_ahead_violations == 0
    assert len(report.failures) == 1


def test_runtime_error_is_recorded_as_a_failure_without_being_counted_as_look_ahead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    migration_clock: Callable[[], datetime],
) -> None:
    """Existing `except (RuntimeError, ValueError)` behaviour is unchanged by the split."""
    report = _run_with_engine_raising(
        monkeypatch, tmp_path, error=RuntimeError("unrelated engine failure"), clock=migration_clock
    )

    assert report.total_cases == 1
    assert report.look_ahead_violations == 0
    assert len(report.failures) == 1


def test_is_look_ahead_violation_unwraps_a_real_pydantic_validation_error() -> None:
    """Direct unit test of the unwrapping branch, against a *real* `ValidationError`.

    `ReplayCase.validate_point_in_time` already rejects any corpus case whose evidence is
    genuinely invisible at its own `as_of` -- before a `ReplayCorpus` can even be built --
    so `ResearchRunRequest.validate_evidence` (constructed from that same case's already-
    validated `evidence`/`as_of`) can never actually disagree and raise inside
    `ReplayRunner.run()`'s try block in practice: the two checks run the identical
    `EvidenceSnapshot.visible_at` predicate over the identical inputs. That makes the
    `isinstance(error, ValidationError)` branch in `_is_look_ahead_violation` structurally
    unreachable through today's only production call path -- a defense-in-depth branch,
    not a dead one, since nothing stops a call site from constructing `ResearchRunRequest`
    directly with unvalidated evidence. This test exercises that branch directly, using a
    `ValidationError` pydantic itself raised (not a hand-built fake), so the unwrapping
    logic is proven correct independent of whether today's call graph happens to reach it.
    """
    frozen_now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    not_yet_visible = EvidenceSnapshot(
        subject="000001.SZ",
        kind="limit_up",
        timeline=Timeline(
            event_time=frozen_now,
            available_time=frozen_now + timedelta(hours=1),
            ingested_time=frozen_now + timedelta(hours=1),
            revision_time=frozen_now + timedelta(hours=1),
        ),
        source_id="synthetic.a-share",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Synthetic limit-up.",
        payload={"schema": "a-share-evidence/v1", "family": "market_event", "facts": {}},
    )

    with pytest.raises(ValidationError) as exc_info:
        ResearchRunRequest(
            run_id="run_unwrap",
            mode="replay",
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(not_yet_visible,),
            code_commit="0123456789abcdef",
            config_digest="d" * 64,
            random_seed=7,
        )

    assert _is_look_ahead_violation(exc_info.value) is True


def test_is_look_ahead_violation_rejects_an_unrelated_real_validation_error() -> None:
    """A real pydantic `ValidationError` from an unrelated cause must not be misclassified."""
    with pytest.raises(ValidationError) as exc_info:
        OutcomeObservation(
            observation_start=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            observation_end=datetime(2026, 7, 24, 9, 0, tzinfo=UTC),  # ends before it starts
            start_price=10.0,
            end_price=10.5,
            benchmark_return=0.01,
            transaction_cost=0.001,
        )

    assert _is_look_ahead_violation(exc_info.value) is False


def test_replay_case_with_invisible_evidence_raises_look_ahead_violation_error(
    evidence,
    frozen_now: datetime,
) -> None:
    """The second raise site (`backtest/replay.py`, not `domain/`) is typed too.

    Like `ResearchRunRequest.validate_evidence`, this validator runs inside a pydantic
    `@model_validator(mode="after")`, so pydantic re-wraps the raised
    `LookAheadViolationError` into its own `ValidationError` -- the underlying exception
    object it preserves at `errors()[0]["ctx"]["error"]` is what must carry the type.
    """
    base: EvidenceSnapshot = evidence(
        kind="limit_up",
        facts={"close": 10.5, "pct_change": 9.99, "board_count": 1},
    )
    not_yet_visible = base.model_copy(
        update={
            "timeline": Timeline(
                event_time=frozen_now,
                available_time=frozen_now + timedelta(hours=1),
                ingested_time=frozen_now + timedelta(hours=1),
                revision_time=frozen_now + timedelta(hours=1),
            )
        }
    )

    with pytest.raises(ValidationError) as exc_info:
        ReplayCase(
            run_id="replay_case_invisible",
            trading_day=date(2026, 7, 24),
            subject="000001.SZ",
            as_of=frozen_now,
            evidence=(not_yet_visible,),
            outcome=OutcomeObservation(
                observation_start=frozen_now,
                observation_end=frozen_now + timedelta(hours=1),
                start_price=10.0,
                end_price=10.5,
                benchmark_return=0.01,
                transaction_cost=0.001,
            ),
        )

    underlying = exc_info.value.errors()[0]["ctx"]["error"]
    assert isinstance(underlying, LookAheadViolationError)


# --- P0.B acceptance review Finding 1: the replay database is migrated, and replay-
# produced validation results are persisted and retrievable ---------------------------


def test_run_migrates_a_fresh_replay_database_before_constructing_any_store(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Before this fix, `ReplayRunner.run()` constructed `SQLiteRunRepository` /
    `SQLiteRecoveryStore` directly against `state_path`, bypassing `run_migrations()`
    entirely -- the acceptance reviewer verified `sdk-replay.sqlite3`/`api-replay.sqlite3`
    sat permanently at `user_version = 0`, with no `schema_migrations` table at all.
    `.run()` now runs the same migration engine `build_storage()` uses, first, exactly
    like every other `state.sqlite3`-shaped database in this project -- so this database's
    schema is no longer silently frozen at whatever it started as.

    First-call shape mirrors `test_composition_migrations.py`'s
    `test_build_storage_stamps_a_fresh_runtime_dir_past_baseline_without_crashing`:
    migrations run before `SQLiteRunRepository` is constructed, so on a brand-new
    `state_path`, `runs` does not exist yet and the demo migration defers.
    """
    corpus = ReplayCorpus(schema_version="openalpha-replay-corpus/v1", trading_days=(), cases=())
    state_path = tmp_path / "replay.sqlite3"
    validation_store = _migrated_validation_store(tmp_path / "state.sqlite3", clock=migration_clock)

    ReplayRunner(code_commit="0123456789abcdef", config_digest="d" * 64, random_seed=7).run(
        corpus=corpus,
        state_path=state_path,
        validation_store=validation_store,
        clock=migration_clock,
    )

    status = read_status(state_path)
    assert status.current_version != 0
    assert status.current_version == CREATE_VALIDATION_RESULTS_VERSION
    assert [m.version for m in status.pending] == [
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
    ]


def test_run_catches_up_the_demo_migration_on_a_second_call_but_the_index_migration_never_lands(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """A second `.run()` call against the same `state_path` lets `runs` (created by the
    first call's `SQLiteRunRepository` construction) satisfy the demo migration's
    precondition -- mirrors `test_build_storage_catches_up_the_demo_migration_on_a_second_
    call`. Unlike `build_storage()`'s `state.sqlite3`, which eventually constructs all
    nine stores (including `SQLitePortfolioLedger`/`SQLiteReportStore`), a replay-only
    database never constructs those two -- so `create_query_path_indexes` (V2-P0B-015),
    which requires `portfolio_transitions` and `research_reports` to exist, can never
    satisfy its precondition here and stays pending forever -- and so, transitively, does
    everything ordered after it, which is now `rewrite_contract_identities` (`V2-P4-001`),
    `add_runs_mode_projection` (`V2-P4-002`) and `split_batch_task_items` (`V2-P4-019`). The
    last of those would defer here on its own account anyway: a replay database never
    constructs `SQLiteBatchTaskStore` either, so it has no `batch_tasks` to split, and no
    batch rows that would need splitting if it did.
    That is harmless here and is the reason `V2-P4-001` bumped no contract a replay database
    writes without also being able to rewrite it: a replay database's `runs`/`decisions` rows
    are written by this build, at the current version, so there is nothing for the identity
    rewrite to do against one. `V2-P4-002` is harmless for the complementary reason: its
    column and index are in `SQLiteRunRepository`'s own `CREATE TABLE`, so a replay database's
    `runs` carries both from the moment the store creates it -- the pending migration has
    nothing left to retrofit, only an audit row it never gets to write. That is the
    deliberate, documented consequence of routing this
    database through the same unmodified migration engine everything else uses (see
    `ReplayRunner.run`'s docstring) instead of inventing a replay-specific migration subset.
    """
    corpus = ReplayCorpus(schema_version="openalpha-replay-corpus/v1", trading_days=(), cases=())
    state_path = tmp_path / "replay.sqlite3"
    validation_store = _migrated_validation_store(tmp_path / "state.sqlite3", clock=migration_clock)
    runner = ReplayRunner(code_commit="0123456789abcdef", config_digest="d" * 64, random_seed=7)

    runner.run(
        corpus=corpus,
        state_path=state_path,
        validation_store=validation_store,
        clock=migration_clock,
    )
    runner.run(
        corpus=corpus,
        state_path=state_path,
        validation_store=validation_store,
        clock=migration_clock,
    )

    status = read_status(state_path)
    assert status.current_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert status.current_version > BASELINE_VERSION
    assert [m.version for m in status.pending] == [
        CREATE_QUERY_PATH_INDEXES_VERSION,
        REWRITE_CONTRACT_IDENTITIES_VERSION,
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
    ]


def test_run_persists_a_successful_cases_validation_result_and_it_is_retrievable(
    tmp_path: Path,
    evidence: Callable[..., EvidenceSnapshot],
    frozen_now: datetime,
) -> None:
    """Finding 1's second half: `ReplayRunner.run()` computed a `ValidationResult` per
    case and put its `validation_id` into `ReplayReport.validation_ids`, but never called
    a validation store -- the end-to-end reviewer confirmed the frozen corpus's 300
    validation IDs could not be retrieved through any query interface, because nothing
    persisted them. `.run()` now appends every successful case's result to an injected
    `validation_store` -- the same store `sdk.py`/`api/app.py` already build via
    `build_storage()` -- so a validation produced by replay is queryable exactly like one
    produced by `sdk.validate_outcome()` / `POST /api/v1/backtests/validate`
    (`test_validation_interfaces.py`).

    Retrieval here goes through the same two steps a real caller would use: look up the
    case's `decision_id` from the (now-migrated) replay run repository by `run_id`, then
    query the validation store by that `decision_id` -- exactly
    `test_api_provenance_resolution.py`'s existing pattern of reading a replay-produced
    manifest back by `run_id` through `SQLiteRunRepository`, extended one hop further.
    """
    item = evidence(kind="limit_up", facts={"close": 10.5, "pct_change": 9.99, "board_count": 1})
    corpus = _successful_single_case_corpus(
        run_id="replay_persistence_case", frozen_now=frozen_now, evidence_item=item
    )

    def clock() -> datetime:
        return frozen_now

    state_path = tmp_path / "replay.sqlite3"
    validation_store = _migrated_validation_store(tmp_path / "state.sqlite3", clock=clock)

    runner = ReplayRunner(code_commit="0123456789abcdef", config_digest="d" * 64, random_seed=7)
    report = runner.run(
        corpus=corpus,
        state_path=state_path,
        validation_store=validation_store,
        clock=clock,
    )

    assert report.succeeded == 1
    assert report.failures == ()
    assert len(report.validation_ids) == 1

    decision = SQLiteRunRepository(state_path).get_decision_for_run("replay_persistence_case")
    assert decision is not None
    by_decision = validation_store.list_by_decision(decision.decision_id)
    assert len(by_decision) == 1
    assert by_decision[0].validation_id == report.validation_ids[0]
    assert validation_store.list_by_signal(by_decision[0].signal_id) == by_decision
