"""Frozen-corpus replay through the same research and validation path."""

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from openalpha_cn.backtest.validation import OutcomeObservation, OutcomeValidator, ValidationStore
from openalpha_cn.domain.evidence import EvidenceSnapshot, LookAheadViolationError
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
from openalpha_cn.storage.migrations import run_migrations
from openalpha_cn.storage.recovery import SQLiteRecoveryStore
from openalpha_cn.storage.sqlite import SQLiteRunRepository


class ReplayCase(BaseModel):
    """One frozen point-in-time research input and future observation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    trading_day: date
    subject: str = Field(min_length=1, max_length=128)
    as_of: datetime
    evidence: tuple[EvidenceSnapshot, ...]
    outcome: OutcomeObservation

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_point_in_time(self) -> Self:
        if any(item.subject != self.subject for item in self.evidence):
            raise ValueError("replay evidence subject does not match the case")
        if any(not item.visible_at(self.as_of) for item in self.evidence):
            # V2-P0B-014: typed, not a bare ValueError -- see LookAheadViolationError's
            # docstring (domain/evidence.py). ReplayRunner.run() below catches this by
            # type, not by matching this message's text.
            raise LookAheadViolationError("replay corpus contains a look-ahead violation")
        return self


class ReplayCorpus(BaseModel):
    """Versioned frozen replay corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    trading_days: tuple[date, ...]
    cases: tuple[ReplayCase, ...]

    @model_validator(mode="after")
    def validate_index(self) -> Self:
        if len(set(self.trading_days)) != len(self.trading_days):
            raise ValueError("trading_days must be unique")
        run_ids = [case.run_id for case in self.cases]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("replay run_ids must be unique")
        known_days = set(self.trading_days)
        if any(case.trading_day not in known_days for case in self.cases):
            raise ValueError("every replay case must reference an indexed trading day")
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        """Load and validate a UTF-8 frozen corpus."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class ReplayReport(BaseModel):
    """Determinism, point-in-time, and validation results for one corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_cases: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    deterministic_replays: int = Field(ge=0)
    look_ahead_violations: int = Field(ge=0)
    validation_ids: tuple[str, ...]
    failures: tuple[str, ...]

    @computed_field(return_type=float)  # type: ignore[prop-decorator]
    @property
    def success_rate(self) -> float:
        """Return successful cases divided by total cases."""
        return 1.0 if self.total_cases == 0 else self.succeeded / self.total_cases


class ReplayRunner:
    """Run every frozen case twice through the shared deterministic core."""

    def __init__(
        self,
        *,
        code_commit: str,
        config_digest: str,
        random_seed: int,
    ) -> None:
        self.code_commit = code_commit
        self.config_digest = config_digest
        self.random_seed = random_seed

    def run(
        self,
        *,
        corpus: ReplayCorpus,
        state_path: Path,
        validation_store: ValidationStore,
        clock: Callable[[], datetime],
    ) -> ReplayReport:
        """Execute the corpus and return explicit failures and validation IDs.

        P0.B acceptance review, Finding 1: this used to construct `SQLiteRunRepository`/
        `SQLiteRecoveryStore` directly against `state_path`, bypassing
        `runtime/composition.py#build_storage` -- the product reviewer verified
        `sdk-replay.sqlite3`/`api-replay.sqlite3` sat permanently at `user_version = 0`,
        with no `schema_migrations` table and no chance of ever receiving a migration
        such as `create_query_path_indexes` (V2-P0B-015). `run_migrations` -- the exact
        engine `build_storage()` calls -- now runs first, before either store below is
        constructed, exactly like every other `state.sqlite3`-shaped database in this
        project (`storage/migrations.py`'s module docstring).

        This database intentionally stays separate from the main `state.sqlite3`
        `sdk.py`/`api/app.py` build via `build_storage()`, rather than being folded into
        it: `SQLiteRunRepository.append_run` rejects any reused `run_id` outright (no
        idempotent-by-content replace, unlike this store's siblings), so merging a frozen
        corpus's `run_id`s into the live production `runs` table would risk a real user's
        run history colliding with -- or simply being interleaved with -- synthetic replay
        data, for no benefit `mode="replay"` on `RunManifest` doesn't already give a
        careful reader for free. One consequence of leaving `run_migrations` unmodified
        here (no replay-specific migration subset): `create_query_path_indexes` requires
        `portfolio_transitions` and `research_reports`, tables a replay-only database never
        constructs, so it stays permanently pending after the demo migration catches up --
        the executor's ordinary stop-at-first-deferral behaviour, not a bug (see
        `tests/unit/backtest/test_replay.py`'s migration tests).

        `validation_store` is different: unlike the run/recovery ledger, a
        `ValidationResult` is idempotent by content-derived `validation_id`
        (`storage/validation.py#SQLiteValidationStore`), so there is no collision risk in
        sharing it, and the whole point of persisting one is to look back at whether a past
        judgement held up -- a question that should not care whether the judgement came
        from a live run or from replaying a frozen corpus. The end-to-end reviewer verified
        that before this fix, `ReplayReport.validation_ids` was computed but never written
        anywhere at all, so nothing on the frozen corpus's 300 cases was retrievable
        through any query interface. Every successful case's result is now appended to the
        caller-supplied `validation_store` -- the same instance `sdk.py`'s
        `self.validation_store` / `api/app.py`'s `validation_store` already build via
        `build_storage()` -- so it is queryable exactly like a result produced by
        `sdk.validate_outcome()` / `POST /api/v1/backtests/validate`
        (`GET /api/v1/backtests/validations/by-decision/{id}` and `by-signal/{id}`,
        `sdk.list_validations_by_decision`/`list_validations_by_signal`), with no new
        query surface needed.

        `clock` is unrelated to `case.as_of` (used per case below to freeze each research
        run's point-in-time clock): it is the real wall-clock callable `run_migrations`
        needs to timestamp a pre-migration backup and the `schema_migrations` audit trail,
        mirroring the caller-supplied clock every other `build_storage()`-routed call site
        already threads through.
        """
        run_migrations(state_path, clock=clock)
        repository = SQLiteRunRepository(state_path)
        recovery_store = SQLiteRecoveryStore(state_path)
        memory = InMemoryResearchMemory()
        validator = OutcomeValidator()
        succeeded = 0
        deterministic = 0
        look_ahead_violations = 0
        validation_ids: list[str] = []
        failures: list[str] = []

        for case in corpus.cases:
            try:
                engine = ResearchEngine(
                    repository=repository,
                    memory=memory,
                    clock=_fixed_clock(case.as_of),
                    recovery_store=recovery_store,
                )
                request = ResearchRunRequest(
                    run_id=case.run_id,
                    mode=RunMode.replay,
                    subject=case.subject,
                    as_of=case.as_of,
                    evidence=case.evidence,
                    code_commit=self.code_commit,
                    config_digest=self.config_digest,
                    random_seed=self.random_seed,
                )
                first = engine.run_cycle(request)
                second = engine.run_cycle(request)
                if first == second:
                    deterministic += 1
                else:
                    failures.append(f"{case.run_id}: nondeterministic replay")
                    continue
                validation = validator.validate(research=first, observation=case.outcome)
                validation_store.append(validation)
                validation_ids.append(validation.validation_id)
                succeeded += 1
            except (RuntimeError, ValueError) as error:
                # V2-P0B-014 / audit F46: classify by exception type, not by matching
                # substrings against str(error). See LookAheadViolationError's docstring
                # (domain/evidence.py) for the two silent failure modes this replaces, and
                # _is_look_ahead_violation's docstring below for why a plain
                # isinstance(error, LookAheadViolationError) check is not enough here.
                if _is_look_ahead_violation(error):
                    look_ahead_violations += 1
                failures.append(f"{case.run_id}: {type(error).__name__}: {error}")

        return ReplayReport(
            total_cases=len(corpus.cases),
            succeeded=succeeded,
            deterministic_replays=deterministic,
            look_ahead_violations=look_ahead_violations,
            validation_ids=tuple(validation_ids),
            failures=tuple(failures),
        )


def _fixed_clock(value: datetime) -> Callable[[], datetime]:
    return lambda: value


def _is_look_ahead_violation(error: Exception) -> bool:
    """Return whether `error` is -- or wraps -- a `LookAheadViolationError`.

    Both raise sites (`ResearchRunRequest.validate_evidence` in `domain/run_request.py`,
    `ReplayCase.validate_point_in_time` above) raise inside a pydantic
    `@model_validator(mode="after")`. Pydantic catches any `ValueError` (or subclass)
    raised there and re-wraps it into its own `pydantic_core.ValidationError` before it
    propagates out of `ResearchRunRequest(...)`/`engine.run_cycle(...)` -- so the exception
    that actually reaches `ReplayRunner.run()`'s `except` clause is always the wrapper, not
    `LookAheadViolationError` itself, and a plain `isinstance(error,
    LookAheadViolationError)` would never be True for either real raise site (confirmed by
    running `ResearchRunRequest` with invisible evidence and inspecting the raised
    exception's type at V2-P0B-014 implementation time).

    Pydantic does not discard the original exception object, though: for every
    `type == "value_error"` entry, `ValidationError.errors()` carries it unchanged at
    `entry["ctx"]["error"]`. Checking that -- instead of parsing `entry["msg"]`, which is
    exactly the string-matching this task removes -- is what actually classifies the
    violation, still entirely independent of wording.

    Also accepts an unwrapped `LookAheadViolationError` directly, so a future call site
    that raises it outside of a pydantic validator is classified correctly too.
    """
    if isinstance(error, LookAheadViolationError):
        return True
    if isinstance(error, ValidationError):
        return any(
            isinstance(item.get("ctx", {}).get("error"), LookAheadViolationError)
            for item in error.errors()
        )
    return False
