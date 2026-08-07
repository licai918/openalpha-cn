"""Frozen-corpus replay through the same research and validation path."""

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.backtest.validation import OutcomeObservation, OutcomeValidator
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.runtime.contracts import ResearchRunRequest
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.runtime.memory import InMemoryResearchMemory
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
            raise ValueError("replay corpus contains a look-ahead violation")
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

    def run(self, *, corpus: ReplayCorpus, state_path: Path) -> ReplayReport:
        """Execute the corpus and return explicit failures and validation IDs."""
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
                    mode="replay",
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
                validation_ids.append(validation.validation_id)
                succeeded += 1
            except (RuntimeError, ValueError) as error:
                message = str(error)
                if "look-ahead" in message or "not visible" in message:
                    look_ahead_violations += 1
                failures.append(f"{case.run_id}: {type(error).__name__}: {message}")

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
