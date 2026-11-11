"""The survivor book's confirmatory sample, continued across registrations by recompute
equivalence (`V2-P7-023`).

## Why

A survivor registration fixes a scoring definition; the paper book is the record layer the
definition is settled in. What a confirmatory sample can be evidence about is the scoring
definition and nothing else, so a successor registration that scores a predecessor's session
again and finds the same batch is scoring the same sample under the same rule -- and that
session's candidate record belongs in the successor's sample. "Another registration opens
another paper book" therefore no longer decides whether the sample continues
(`docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` section 2.5). The paper book still
does not continue: a new registration opens a new book, and this module says what the
confirmatory sample is, not where the book is settled.

## The contract

A registration names the registration it supersedes in
``settings[SURVIVOR_CONTINUATION_KEY]``: a `survivor-continuation/v1` object carrying the
predecessor's repository-relative POSIX path, its registration sha256 and its `config_id`, with
`extra="forbid"` and no default on any field. No block means no predecessor. A block present but
unreadable is refused naming every field that does not validate, never defaulted and never
ignored.

The block is not part of `config`. That is the same decision the two pre-registered protocol
blocks make (`survivor_forward_power`, `survivor_forward_stop_rule` in
`strategy_survivor_forward`), for four reasons:

- A registration file is not a stored row. Its top level is checked only for its `schema`
  (`scripts/research/registry.py`), and `settings` is free JSON; the two blocks above were added
  the same way.
- The survivor `config` is a closed three-key document (`strategy_survivor.REGISTRATION_KEYS`),
  so a fourth key would be refused by every reader, and `config_id` is the identity of the
  scoring definition -- it must not move with a reference that says nothing about scoring.
- Old code reading a new registration ignores the block and continues nothing, which is the
  conservative failure direction rather than a wrong merge; and it cannot run a new registration
  anyway, because admission requires the registration's own `code_commit`.
- New code reading an old registration finds no block and behaves exactly as it did.

No migration is needed, and no stored shape moves: `PredictionRecord`, snapshots, ledger rows,
source files and verdict caches keep their exact shapes. A predecessor written by an earlier
build whose survivor block this build cannot read is refused by name at the chain's reader (see
the next section), rather than read through a widened reader; that would be a migration
task of its own.

## The chain

A predecessor may itself name a predecessor. The chain is read root first, every link through
this build's reader, to `MAX_CONTINUATION_DEPTH` at most; a sha256 appearing twice in it is a
cycle and is refused. Each predecessor contributes only the records of sessions in `(its own
epoch, the next link's epoch]` -- the sessions it was the current registration for -- so the
links' sessions tile the chain without overlapping (plan R3).

## What may be continued into what

`require_continuable` refuses, naming both registrations' sha256 and the field that differs, in
this order: the predecessor is the successor itself; another `adapter.exchange`; another
`adapter.prediction_horizon`; another `adapter.benchmark`; any of the pre-registered protocol
blocks (`survivor_forward_protocol`, `survivor_forward_power`, `survivor_forward_stop_rule`)
whose JSON text is not equal -- absent on both sides is equal, absent on one side is a
difference; a predecessor whose `epoch_session` is not before the successor's; and a smoke
predecessor for a successor that is not itself a smoke registration. There is no path that
continues a registration these checks do not admit.

## Decisions and truncation

`judge_continued_day` takes one predecessor session against the successor's configuration. A
record the predecessor's own check refused is `predecessor_refused`, and it is not scored again;
any other is scored again from the stored panel, with the holdings its provenance recorded and
under the successor's registration, and nothing is requested of a provider. Equal bytes join the
sample; a refusal in `SURVIVOR_RECOMPUTE_REFUSALS` is `recompute_failed`; anything else is
`differs`.

`continued_sample` sorts the candidates by session and asks the judge of each, oldest first,
until the first day it does not call `identical`. That day is marked, the sample is truncated
there, every later day is `after_truncation` and is never scored again -- so a corrected panel
can only move the truncation earlier, and the earlier `identical` days are recomputed by every
report rather than frozen once (plan U5, U7). A session carrying two predecessor records, or a
record on or before the sample's own epoch, is refused.

## Wording

What a continued sample adds is a predecessor's candidate prediction records -- the scores it
filed, with their own check's verdict -- and never an upgrade of the evidence's standing; every
document keeps `strategy_survivor_summary.SURVIVOR_WORDING`. The paper book is not continued.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.strategy_registration import (
    RegisteredConfiguration,
    StrategyRegistrationError,
)
from openalpha_cn.strategy_survivor import SurvivorRegistration
from openalpha_cn.strategy_survivor_check import VERDICT_REFUSED, SurvivorRecordVerdict
from openalpha_cn.strategy_survivor_forward import (
    SURVIVOR_FORWARD_POWER_KEY,
    SURVIVOR_FORWARD_PROTOCOL_KEY,
    SURVIVOR_FORWARD_STOP_RULE_KEY,
    WitnessedRecord,
)
from openalpha_cn.strategy_survivor_registration import (
    SURVIVOR_RECOMPUTE_REFUSALS,
    SurvivorInputProvenance,
    recompute_survivor_batch,
)
from openalpha_cn.strategy_survivor_summary import SURVIVOR_WORDING

__all__ = [
    "AFTER_TRUNCATION",
    "CONTINUATION_RULE_WORDING",
    "DIFFERS",
    "IDENTICAL",
    "MAX_CONTINUATION_DEPTH",
    "NO_PREDECESSOR_WORDING",
    "PREDECESSOR_REFUSED",
    "RECOMPUTE_FAILED",
    "SMOKE_SETTINGS_KEY",
    "SURVIVOR_CONTINUATION_KEY",
    "SURVIVOR_CONTINUATION_SCHEMA",
    "ChainLink",
    "ContinuationCandidate",
    "ContinuationOutcome",
    "ContinuationResult",
    "ContinuationTarget",
    "ContinuedDay",
    "SurvivorContinuation",
    "SurvivorContinuationError",
    "continuation_block",
    "continuation_bytes",
    "continuation_target",
    "continued_sample",
    "judge_continued_day",
    "no_continuation_section",
    "read_survivor_continuation",
    "require_continuable",
]

SURVIVOR_CONTINUATION_KEY: Final = "survivor_continuation"
SURVIVOR_CONTINUATION_SCHEMA: Final = "survivor-continuation/v1"
MAX_CONTINUATION_DEPTH: Final = 16
SMOKE_SETTINGS_KEY: Final = "smoke_run"
"""`scripts/daily_selection.SMOKE_SETTINGS_KEY`, spelled here because `src/` imports no script;
`tests/unit/scripts/test_daily_selection_dispatch.py` holds the two equal."""
_CONTINUED_BLOCKS: Final[tuple[str, ...]] = (
    SURVIVOR_FORWARD_PROTOCOL_KEY,
    SURVIVOR_FORWARD_POWER_KEY,
    SURVIVOR_FORWARD_STOP_RULE_KEY,
)
"""The pre-registered blocks a chain keeps identical (plan U3), from `strategy_survivor_forward`."""

ContinuationOutcome = Literal[
    "identical", "differs", "recompute_failed", "predecessor_refused", "after_truncation"
]
"""What one predecessor session's comparison with the successor came to."""
IDENTICAL: Final[ContinuationOutcome] = "identical"
"""The successor scores the session again as the record does: it joins the sample."""
DIFFERS: Final[ContinuationOutcome] = "differs"
"""The successor scores the session otherwise: it is where the sample is truncated."""
RECOMPUTE_FAILED: Final[ContinuationOutcome] = "recompute_failed"
"""The session cannot be scored again from the stored panel: it is where the sample is truncated."""
PREDECESSOR_REFUSED: Final[ContinuationOutcome] = "predecessor_refused"
"""The predecessor's own late-record check refused the record: it is where the sample is
truncated, and the day is never scored again."""
AFTER_TRUNCATION: Final[ContinuationOutcome] = "after_truncation"
"""A later predecessor session: the sample was already truncated before it, and it is never asked
of the judge or scored again (plan U5)."""

CONTINUATION_RULE_WORDING: Final = (
    "a predecessor's on-time bound record joins this registration's confirmatory sample only "
    "when this registration's configuration, scoring that session again from the stored panel, "
    "gives the same batch (the registration's identity aside); the first session that does not "
    "is marked and the sample is truncated there -- no later predecessor session joins it; the "
    "paper book is not continued: a new registration opens a new book"
)
NO_PREDECESSOR_WORDING: Final = (
    "this registration names no predecessor: its confirmatory sample is its own records, from "
    "its own epoch"
)

_MISSING: Final = object()


class SurvivorContinuationError(StrategyRegistrationError):
    """A registration's predecessor cannot be read, or cannot be continued into it."""


class SurvivorContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["survivor-continuation/v1"]
    supersedes_path: StrictStr
    """The predecessor registration's path in the repository, POSIX, relative."""
    supersedes_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    supersedes_config_id: Annotated[StrictStr, Field(min_length=1)]

    @field_validator("supersedes_path")
    @classmethod
    def _a_relative_posix_json_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or ".." in path.parts
            or path.suffix != ".json"
            or path.as_posix() != value
        ):
            raise ValueError(
                f"{value!r} is not a relative POSIX path to a .json file inside the repository"
            )
        return value


def continuation_block(*, path: str, sha256: str, config_id: str) -> dict[str, str]:
    """The block a successor registration writes into `settings[SURVIVOR_CONTINUATION_KEY]` --
    for `V2-P7-006b`'s register step; validated before it is returned."""
    block = SurvivorContinuation.model_validate(
        {
            "schema_version": SURVIVOR_CONTINUATION_SCHEMA,
            "supersedes_path": path,
            "supersedes_sha256": sha256,
            "supersedes_config_id": config_id,
        }
    )
    return block.model_dump(mode="json")


def read_survivor_continuation(settings: Mapping[str, object]) -> SurvivorContinuation | None:
    """The block in a registration's settings; `None` when there is none; otherwise
    `SurvivorContinuationError` naming every field that does not validate (`<key>.<field>: msg`)."""
    if not isinstance(settings, Mapping):
        raise SurvivorContinuationError(
            f"a registration's settings are {type(settings).__name__}, not a mapping of fields"
        )
    block = settings.get(SURVIVOR_CONTINUATION_KEY, _MISSING)
    if block is _MISSING:
        return None
    if not isinstance(block, Mapping):
        raise SurvivorContinuationError(
            f"the registration's {SURVIVOR_CONTINUATION_KEY} is {type(block).__name__}, not a "
            f"{SURVIVOR_CONTINUATION_SCHEMA} object"
        )
    try:
        return SurvivorContinuation.model_validate(dict(block))
    except ValidationError as error:
        problems = "; ".join(
            f"{SURVIVOR_CONTINUATION_KEY}.{'.'.join(str(part) for part in problem['loc'])}: "
            f"{problem['msg']}"
            for problem in error.errors()
        )
        raise SurvivorContinuationError(
            f"the registration's {SURVIVOR_CONTINUATION_KEY} does not validate: {problems}"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class ChainLink:
    """One registration of a chain, read: its identity, its settings and its survivor book."""

    registration_sha256: str
    config_id: str
    code_commit: str
    settings: Mapping[str, Any]
    survivor: SurvivorRegistration

    @property
    def declared(self) -> RegisteredConfiguration:
        return RegisteredConfiguration(
            config_id=self.config_id,
            registration_sha256=self.registration_sha256,
            code_commit=self.code_commit,
            seed=self.survivor.book.seed,
        )

    @property
    def epoch(self) -> date:
        return self.survivor.adapter.epoch_session

    @property
    def smoke(self) -> bool:
        return SMOKE_SETTINGS_KEY in self.settings


def _name(link: ChainLink) -> str:
    """A registration as a refusal names it: the first sixteen digits of its sha256."""
    return link.registration_sha256[:16]


def _block_text(settings: Mapping[str, Any], key: str) -> str:
    """A pre-registered block's JSON text (`null` when the settings name none). Two blocks are
    equal when their texts are, so a difference in a key's order is not a difference (plan U3)."""
    return json.dumps(settings.get(key), sort_keys=True, ensure_ascii=False)


def _refuse_another(*, successor: ChainLink, predecessor: ChainLink, what: str, why: str) -> None:
    """Refuse a predecessor and a successor that differ in `what`, naming both by sha256."""
    raise SurvivorContinuationError(
        f"{_name(successor)} cannot continue {_name(predecessor)}: {what}; {why}"
    )


def require_continuable(successor: ChainLink, predecessor: ChainLink) -> None:
    """Refuse (`SurvivorContinuationError`), naming both registrations' sha256[:16] and the
    field, in this order: the predecessor is the successor itself; another `adapter.exchange`;
    another `adapter.prediction_horizon`; another `adapter.benchmark`; any of `_CONTINUED_BLOCKS`
    not JSON-equal (`json.dumps(..., sort_keys=True)` of `settings.get(key)`, absent on both
    sides is equal); the predecessor's epoch not before the successor's; a smoke predecessor for
    a successor that is not a smoke registration."""
    if successor.registration_sha256 == predecessor.registration_sha256:
        _refuse_another(
            successor=successor,
            predecessor=predecessor,
            what=(
                f"the registration {_name(predecessor)} names itself as its predecessor "
                "(the same registration sha256)"
            ),
            why="a chain runs backwards in time and a registration cannot continue itself",
        )
    successor_adapter, predecessor_adapter = (
        successor.survivor.adapter,
        predecessor.survivor.adapter,
    )
    if successor_adapter.exchange != predecessor_adapter.exchange:
        _refuse_another(
            successor=successor,
            predecessor=predecessor,
            what=(
                f"the predecessor's adapter.exchange is {predecessor_adapter.exchange!r} and the "
                f"successor's is {successor_adapter.exchange!r}"
            ),
            why="one chain scores one market's calendar",
        )
    if successor_adapter.prediction_horizon != predecessor_adapter.prediction_horizon:
        _refuse_another(
            successor=successor,
            predecessor=predecessor,
            what=(
                "the predecessor's adapter.prediction_horizon is "
                f"{predecessor_adapter.prediction_horizon!r} and the successor's is "
                f"{successor_adapter.prediction_horizon!r}"
            ),
            why="one chain judges the records of one outcome window",
        )
    if successor_adapter.benchmark != predecessor_adapter.benchmark:
        _refuse_another(
            successor=successor,
            predecessor=predecessor,
            what=(
                f"the predecessor's adapter.benchmark is {predecessor_adapter.benchmark!r} and "
                f"the successor's is {successor_adapter.benchmark!r}"
            ),
            why="one chain is measured against one benchmark",
        )
    for key in _CONTINUED_BLOCKS:
        predecessor_text = _block_text(predecessor.settings, key)
        successor_text = _block_text(successor.settings, key)
        if predecessor_text != successor_text:
            _refuse_another(
                successor=successor,
                predecessor=predecessor,
                what=(
                    f"settings.{key} differs ({predecessor_text} against {successor_text}), and "
                    "a chain keeps one pre-registered protocol"
                ),
                why="a protocol block names the read the sample will be judged by",
            )
    if predecessor.epoch >= successor.epoch:
        _refuse_another(
            successor=successor,
            predecessor=predecessor,
            what=(
                f"the predecessor's epoch_session {predecessor.epoch.isoformat()} is not before "
                f"the successor's {successor.epoch.isoformat()}"
            ),
            why="a chain runs forward in time, and each link owns the sessions after its epoch",
        )
    if predecessor.smoke and not successor.smoke:
        _refuse_another(
            successor=successor,
            predecessor=predecessor,
            what=(
                "the predecessor is a smoke registration (its settings.name the "
                f"{SMOKE_SETTINGS_KEY} block) and the successor is not"
            ),
            why="smoke output is not a test of the protocol and never enters a registration "
            "that is not a smoke one",
        )


# --- the bytes, one day, and the sample ---------------------------------------------------------

_DECLARED_SCORE_FIELDS: Final[tuple[str, ...]] = ("family", "horizon", "name")
"""The declaration's fields that name what the scores are, not which registration filed them."""


def continuation_bytes(batch: PredictionBatch) -> bytes:
    """The bytes two registrations' batches of one session are compared by (plan U2):
    `batch.model_dump(mode="json", exclude_computed_fields=True)` with the declaration reduced to
    `name`, `family` and `horizon` -- `feature_version`, `seed`, `code_commit` and
    `hyperparameters` name the registration, not the score -- as canonical JSON
    (`sort_keys=True, separators=(",", ":"), ensure_ascii=False`), UTF-8."""
    body = batch.model_dump(mode="json", exclude_computed_fields=True)
    declaration = body["artifact"]["declaration"]
    body["artifact"]["declaration"] = {key: declaration[key] for key in _DECLARED_SCORE_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuationTarget:
    """What the successor scores a predecessor's session again with."""

    book: SurvivorBookConfig
    transform: FactorTransformSpec
    exchange: str
    min_coverage: float
    max_carry_sessions: int
    registered: RegisteredConfiguration


def continuation_target(link: ChainLink) -> ContinuationTarget:
    """The successor's target, read off its registration (`FACTOR_TRANSFORMS.get` of its
    adapter's transform)."""
    adapter = link.survivor.adapter
    return ContinuationTarget(
        book=link.survivor.book,
        transform=FACTOR_TRANSFORMS.get(adapter.transform),
        exchange=adapter.exchange,
        min_coverage=adapter.min_coverage,
        max_carry_sessions=adapter.max_carry_sessions,
        registered=link.declared,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuationCandidate:
    """One predecessor session's on-time bound record, with its own check's verdict and the
    provenance its run wrote (`None` only when that check refused it for having none)."""

    item: WitnessedRecord
    registration_sha256: str
    own: SurvivorRecordVerdict
    provenance: SurvivorInputProvenance | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuedDay:
    candidate: ContinuationCandidate
    outcome: ContinuationOutcome
    reason: str | None
    """`None` exactly when `outcome` is `identical`; enforced in `__post_init__` (ValueError)."""

    def __post_init__(self) -> None:
        if self.outcome == IDENTICAL and self.reason is not None:
            raise ValueError(
                f"an identical day names no reason, and this one gives {self.reason!r}"
            )
        if self.outcome != IDENTICAL and self.reason is None:
            raise ValueError(f"a {self.outcome} day names the reason it did not join the sample")

    def document(self) -> dict[str, Any]:
        """`{"session", "record_id", "registration_sha256", "outcome", "own_verdict", "reason"}`."""
        return {
            "session": self.candidate.item.session.isoformat(),
            "record_id": self.candidate.item.record_id,
            "registration_sha256": self.candidate.registration_sha256,
            "outcome": self.outcome,
            "own_verdict": self.candidate.own.verdict,
            "reason": self.reason,
        }


def _score_rows(batch: PredictionBatch) -> dict[str, tuple[float | None, str | None]]:
    """Each security's answer in `batch`: its score or the reason it abstained."""
    return {row.ts_code: (row.score, row.abstention) for row in batch.predictions}


def judge_continued_day(
    store: PanelStore, candidate: ContinuationCandidate, *, target: ContinuationTarget
) -> ContinuedDay:
    """One predecessor session against the successor's configuration:
    - its own check refused it -> `predecessor_refused` (reason: the own verdict's reason), and
      nothing is recomputed;
    - otherwise `recompute_survivor_batch(store, candidate.item.record, book=target.book,
      transform=target.transform, exchange=target.exchange, min_coverage=target.min_coverage,
      max_carry_sessions=target.max_carry_sessions, held=candidate.provenance.held,
      registered=target.registered, provenance=None)` -- called through this module's own
      `recompute_survivor_batch` name, so a test can count it;
      `SURVIVOR_RECOMPUTE_REFUSALS` -> `recompute_failed` (reason names the refusal); equal
      `continuation_bytes` -> `identical`; else `differs` (reason names how many scores, and
      whether the scope, differ).
    A `provenance` of `None` with an own verdict that is not refused is a caller's error:
    `ValueError`. Anything outside the refusal set propagates."""
    if candidate.own.verdict == VERDICT_REFUSED:
        return ContinuedDay(
            candidate=candidate,
            outcome=PREDECESSOR_REFUSED,
            reason=(
                candidate.own.reason
                or f"the predecessor's own check refused {candidate.item.record_id}"
            ),
        )
    if candidate.provenance is None:
        raise ValueError(
            f"{candidate.item.record_id} carries no provenance and its own check did not refuse "
            "it; only a refused record may be judged without the holdings its evening read"
        )
    session = candidate.item.session
    try:
        again = recompute_survivor_batch(
            store,
            candidate.item.record,
            book=target.book,
            transform=target.transform,
            exchange=target.exchange,
            min_coverage=target.min_coverage,
            max_carry_sessions=target.max_carry_sessions,
            held=candidate.provenance.held,
            registered=target.registered,
            provenance=None,
        )
    except SURVIVOR_RECOMPUTE_REFUSALS as error:
        return ContinuedDay(
            candidate=candidate,
            outcome=RECOMPUTE_FAILED,
            reason=f"{session.isoformat()} cannot be scored again from the stored panel: {error}",
        )
    record = candidate.item.record.batch
    if continuation_bytes(again) == continuation_bytes(record):
        return ContinuedDay(candidate=candidate, outcome=IDENTICAL, reason=None)
    before, after = _score_rows(record), _score_rows(again)
    names = set(before) | set(after)
    another = sum(1 for name in names if before.get(name) != after.get(name))
    reason = (
        f"scored again under this registration, {session.isoformat()} differs: {another} of "
        f"{len(names)} names give another score"
    )
    if set(before) != set(after):
        reason += ", and its scope is another set of names"
    return ContinuedDay(candidate=candidate, outcome=DIFFERS, reason=reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuationResult:
    chain: tuple[ChainLink, ...]
    """The predecessors, root first."""
    sample_epoch: date
    days: tuple[ContinuedDay, ...]

    @property
    def merged(self) -> tuple[ContinuationCandidate, ...]:
        """The candidates whose day is `identical`, by session."""
        return tuple(day.candidate for day in self.days if day.outcome == IDENTICAL)

    @property
    def truncated_at(self) -> ContinuedDay | None:
        """The first day that is neither `identical` nor `after_truncation`."""
        for day in self.days:
            if day.outcome not in (IDENTICAL, AFTER_TRUNCATION):
                return day
        return None

    def document(self) -> dict[str, Any]:
        """{"wording": SURVIVOR_WORDING, "rule": CONTINUATION_RULE_WORDING,
        "chain": [{"registration_sha256", "config_id", "epoch_session"} root first],
        "sample_epoch": iso, "candidates": len(days), "merged": len(merged),
        "truncated_at": iso | None,
        "truncation": {"session", "record_id", "outcome", "reason"} | None,
        "days": [day.document() ...]}"""
        truncated = self.truncated_at
        return {
            "wording": SURVIVOR_WORDING,
            "rule": CONTINUATION_RULE_WORDING,
            "chain": [
                {
                    "registration_sha256": link.registration_sha256,
                    "config_id": link.config_id,
                    "epoch_session": link.epoch.isoformat(),
                }
                for link in self.chain
            ],
            "sample_epoch": self.sample_epoch.isoformat(),
            "candidates": len(self.days),
            "merged": len(self.merged),
            "truncated_at": (
                None if truncated is None else truncated.candidate.item.session.isoformat()
            ),
            "truncation": (
                None
                if truncated is None
                else {
                    "session": truncated.candidate.item.session.isoformat(),
                    "record_id": truncated.candidate.item.record_id,
                    "outcome": truncated.outcome,
                    "reason": truncated.reason,
                }
            ),
            "days": [day.document() for day in self.days],
        }


def continued_sample(
    candidates: Sequence[ContinuationCandidate],
    *,
    judge: Callable[[ContinuationCandidate], ContinuedDay],
    chain: Sequence[ChainLink],
    sample_epoch: date,
) -> ContinuationResult:
    """Every candidate by session, oldest first; `judge` is asked of each until the first one it
    does not call `identical`, and of none after it -- each later candidate is `after_truncation`
    (reason: f"after the truncation at {session}"), never recomputed. Two candidates of one session
    refuse (`SurvivorContinuationError`, "two predecessor records for"); a candidate on or before
    `sample_epoch` refuses ("is not after the sample's epoch")."""
    ordered = sorted(candidates, key=lambda candidate: candidate.item.session)
    seen: set[date] = set()
    for candidate in ordered:
        session = candidate.item.session
        if session <= sample_epoch:
            raise SurvivorContinuationError(
                f"the predecessor record {candidate.item.record_id} for {session.isoformat()} is "
                f"not after the sample's epoch {sample_epoch.isoformat()}; a predecessor "
                "contributes only the sessions it was the current registration for"
            )
        if session in seen:
            raise SurvivorContinuationError(
                f"{session.isoformat()} carries two predecessor records for the sample (one is "
                f"{candidate.item.record_id}); a session has one"
            )
        seen.add(session)
    days: list[ContinuedDay] = []
    truncated: ContinuedDay | None = None
    for candidate in ordered:
        if truncated is not None:
            days.append(
                ContinuedDay(
                    candidate=candidate,
                    outcome=AFTER_TRUNCATION,
                    reason=(
                        f"after the truncation at {truncated.candidate.item.session.isoformat()}"
                    ),
                )
            )
            continue
        day = judge(candidate)
        days.append(day)
        if day.outcome != IDENTICAL:
            truncated = day
    return ContinuationResult(chain=tuple(chain), sample_epoch=sample_epoch, days=tuple(days))


def no_continuation_section() -> dict[str, Any]:
    """The `continuation` section of a registration that names no predecessor:
    {"wording": SURVIVOR_WORDING, "rule": CONTINUATION_RULE_WORDING, "statement":
    NO_PREDECESSOR_WORDING, "chain": [], "sample_epoch": None, "candidates": 0, "merged": 0,
    "truncated_at": None, "truncation": None, "days": []}"""
    return {
        "wording": SURVIVOR_WORDING,
        "rule": CONTINUATION_RULE_WORDING,
        "statement": NO_PREDECESSOR_WORDING,
        "chain": [],
        "sample_epoch": None,
        "candidates": 0,
        "merged": 0,
        "truncated_at": None,
        "truncation": None,
        "days": [],
    }
