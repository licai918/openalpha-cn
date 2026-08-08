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
) -> ReplayReport:
    as_of = datetime(2026, 7, 24, 9, 35, tzinfo=UTC)
    trading_day = date(2026, 7, 24)
    corpus = _single_case_corpus(as_of=as_of, trading_day=trading_day)

    def _fake_run_cycle(self: ResearchEngine, request: ResearchRunRequest) -> ResearchRunResult:
        raise error

    monkeypatch.setattr(ResearchEngine, "run_cycle", _fake_run_cycle)

    return ReplayRunner(
        code_commit="0123456789abcdef",
        config_digest="d" * 64,
        random_seed=7,
    ).run(corpus=corpus, state_path=tmp_path / "replay.sqlite3")


def test_look_ahead_violation_is_still_detected_after_its_message_is_rewritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The core acceptance test: same type, deliberately different wording.

    This message contains neither "look-ahead" nor "not visible" -- the two substrings
    the old code matched on -- so a classifier that still depended on message text would
    report zero violations here. A type-based classifier must still report one.
    """
    reworded = LookAheadViolationError(
        "point-in-time guard rejected this observation; see the audit trail for details"
    )

    report = _run_with_engine_raising(monkeypatch, tmp_path, error=reworded)

    assert report.total_cases == 1
    assert report.look_ahead_violations == 1
    assert len(report.failures) == 1


def test_unrelated_value_error_with_look_ahead_wording_is_not_miscounted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A plain `ValueError` that happens to contain the old magic words must not count.

    This is failure mode 2 from the audit: the old substring match would have counted
    this as a look-ahead violation purely because of its wording, even though it is a
    different exception type raised for an unrelated reason.
    """
    unrelated = ValueError(
        "cache entry for this symbol is not visible in the currently loaded look-ahead index shard"
    )

    report = _run_with_engine_raising(monkeypatch, tmp_path, error=unrelated)

    assert report.total_cases == 1
    assert report.look_ahead_violations == 0
    assert len(report.failures) == 1


def test_runtime_error_is_recorded_as_a_failure_without_being_counted_as_look_ahead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Existing `except (RuntimeError, ValueError)` behaviour is unchanged by the split."""
    report = _run_with_engine_raising(
        monkeypatch, tmp_path, error=RuntimeError("unrelated engine failure")
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
