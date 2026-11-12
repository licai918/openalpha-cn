"""The agent family's filing glue (`V2-P7-013`, plan C Task 2.4): declarations and batches.

Plan C's evening command (`scripts/agent_evening.py`, Task 2.5) does three things that belong in
`src/` rather than in the script: it declares the family, it turns a subject's score into a stored
prediction, and it files the batch through the forward-registration protocol. The script keeps
the orchestration -- opening Step 0's runtime read-only, running the cycles, journalling -- and
this module owns the records' shape.

## `agent_kind` is the mark the backtest guard reads

`domain/llm_contamination.py::refuse_llm_prediction_in_backtest` refuses an `agent_`-family
prediction in a strategy backtest unless the declaration carries
`agent_kind="deterministic"`; a missing mark is refused fail-closed. Every declaration this
module builds therefore carries `agent_kind`, and the LLM family carries `"llm_backed"` so the
refusal is the one that says "a model produced this" rather than "a reader cannot tell". A
hyperparameter rather than a declaration field, for the reason
`domain/llm_contamination.py::AGENT_KIND_HYPERPARAMETER` states: the field set of
`AlphaModelDeclaration` is addressed, and a field added for a mark that is not part of a fit would
move the address of every artifact.

## Two scoring sets, two declarations, one session

`name` is `c-{scoring_set}` and `scoring_set` is a hyperparameter, so primary and coverage are
**two declarations**. The store's session lookup keys on the declaration, so one session can hold
both records without either being read as a revision of the other. The two are never pooled and
`AGENT_RULE_FAMILY`'s coverage record is exploratory by construction (plan C decision D2).

## `as_of` is the session's publication instant

`agent_batch` dates the batch at `session_publication_instant(session)` -- 16:30 Shanghai, the
instant the session's panel data becomes knowable (`panel_ingest.DAILY_AVAILABILITY_TIME`). That
is the same instant the evidence read is pinned to, and it is why a batch predicted before 16:30
cannot be built: `PredictionBatch` refuses `predicted_at < as_of` because such a batch read
features that had not published.

`training_cutoff` is that same instant and equality is admitted by the leakage floor: C's inputs
are the session's panel and its published evidence, all readable at 16:30, so the batch stands at
the instant it was fitted from. This is the non-fitted-record precedent of
`strategy_registration.signal_day_batch`, which dates a composite's artifact at the last dated
input it read.

`feature_ids` carries one declared column, `agent_evidence/v1`, because
`AlphaModelArtifact` refuses an empty feature list and an agent's input is an evidence family
rather than a factor column. It is a **declaration**, not a claim that a panel feature was read:
the LLM family's input is stored evidence, and naming that here is more honest than leaving the
one required field to say something false.

## Filing is the protocol, not a copy of it

`file_agent_batch` calls `strategy_forward_session.file_forward_record` and returns the journal's
`prediction` block. Re-filing the same batch is `unchanged`; a different second answer to one
session is refused and the stored one stands; at or after the next open's call auction nothing is
filed. The block's `provenance` is the registration's content digest, read from the declaration's
own hyperparameters -- the record names the configuration that produced it without a path or a
second document, and a declaration that does not carry that digest is refused rather than filed
with an empty provenance.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

from openalpha_cn.domain.agent_forward import (
    ABSTENTION_CODES,
    AGENT_HORIZON,
    AGENT_LLM_FAMILY,
    AGENT_RULE_FAMILY,
    ScoringSet,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_forward_session import (
    ForwardRecordStore,
    file_forward_record,
    prediction_block,
)
from openalpha_cn.strategy_registration import RegisteredConfiguration

AgentKind = Literal["deterministic", "llm_backed"]

AGENT_KIND_HYPERPARAMETER: Final[str] = "agent_kind"
"""The key `domain/llm_contamination.py` reads; restated here as the producer's side."""
REGISTRATION_HYPERPARAMETER: Final[str] = "registration_sha256"
SCORING_SET_HYPERPARAMETER: Final[str] = "scoring_set"
MODEL_HYPERPARAMETER: Final[str] = "model"
PROMPT_HYPERPARAMETER: Final[str] = "prompt"

AGENT_FEATURE_IDS: Final[tuple[str, ...]] = ("agent_evidence/v1",)
"""The one declared input column: the evidence plane, not a panel feature (see the docstring)."""

_AGENT_FAMILIES: Final[frozenset[str]] = frozenset({AGENT_LLM_FAMILY, AGENT_RULE_FAMILY})
_SCORING_SETS: Final[frozenset[str]] = frozenset({"primary", "coverage"})


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentScore:
    """One subject's answer in an agent batch: a number, or a coded abstention, and its run.

    The shape mirrors `domain/alpha_model.py::Prediction` (exactly one of `score` and
    `abstention`, a finite score) and adds the `run_id` of the cycle that produced it, which the
    stored contract does not carry. Validated at construction rather than left to the batch: an
    invalid row is a producer bug, and the batch validator's message would name the row index
    rather than the subject.
    """

    ts_code: str
    score: float | None
    abstention: str | None
    run_id: str

    def __post_init__(self) -> None:
        if not self.ts_code.strip():
            raise ValueError("an agent score carries a blank ts_code")
        if not self.run_id.strip():
            raise ValueError(f"{self.ts_code}: an agent score carries a blank run_id")
        if (self.score is None) == (self.abstention is None):
            raise ValueError(
                f"{self.ts_code} carries score={self.score!r} and abstention="
                f"{self.abstention!r}; a row is a number or a stated reason there is none, "
                "never both and never neither"
            )
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError(
                f"{self.ts_code} carries score={self.score!r}, which is not finite; abstain "
                "instead, which says so where a reader can see it"
            )
        if self.abstention is not None and self.abstention not in ABSTENTION_CODES:
            raise ValueError(
                f"{self.ts_code} carries abstention {self.abstention!r}, outside C's closed "
                f"vocabulary {sorted(ABSTENTION_CODES)}"
            )


def agent_run_id(*, config_id: str, session: date, family: str, ts_code: str) -> str:
    """A deterministic run id for one cycle: `(config, session, family, security)`.

    Deterministic rather than random because the run's identity is what a reviewer recomputes;
    the security is in the key and the scoring set is not, so primary and coverage share the one
    cycle that scored the name -- which is what makes the two records' overlapping names
    comparable and keeps `--no-llm` runs byte-identical.
    """
    payload = f"{config_id}|{session.isoformat()}|{family}|{ts_code}"
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"agent-{session:%Y%m%d}-{ts_code}-{digest}"


def agent_declaration(
    *,
    family: str,
    scoring_set: ScoringSet,
    agent_kind: AgentKind,
    registered: RegisteredConfiguration,
    model_ref: str | None,
    prompt_ref: str | None,
) -> AlphaModelDeclaration:
    """The declaration every agent batch of one family and scoring set carries.

    `name` is `c-{scoring_set}`; `family` is the agent family; `horizon` is C's one stored
    horizon; `feature_version`, `seed` and `code_commit` come from the registration, so two
    batches of one registration address identically. The hyperparameters carry the four facts a
    reader of a stored record cannot recover otherwise: the agent kind (the backtest guard),
    the registration digest (this module's provenance), the scoring set (never pooled), and the
    model and prompt references an LLM-backed batch was produced with. The LLM and rule
    families refuse each other's shape: a half-declared LLM record cannot be audited, and a
    rule record naming a model is claiming a call that never happened.
    """
    if family not in _AGENT_FAMILIES:
        raise ValueError(
            f"the agent declaration names family {family!r}; C's two families are "
            f"{sorted(_AGENT_FAMILIES)}"
        )
    if scoring_set not in _SCORING_SETS:
        raise ValueError(
            f"the agent declaration names scoring set {scoring_set!r}; the two sets are "
            f"{sorted(_SCORING_SETS)} and are filed as separate records"
        )
    if agent_kind not in ("deterministic", "llm_backed"):
        raise ValueError(f"the agent declaration names agent kind {agent_kind!r}")
    if family == AGENT_LLM_FAMILY:
        if agent_kind != "llm_backed":
            raise ValueError(
                f"{AGENT_LLM_FAMILY} is the model-backed family; declaring it as "
                f"{agent_kind!r} would let a model-produced record through the deterministic "
                "backtest path"
            )
        if not (model_ref and model_ref.strip()) or not (prompt_ref and prompt_ref.strip()):
            raise ValueError(
                f"{AGENT_LLM_FAMILY} must declare both the model reference and the prompt "
                f"reference; got model={model_ref!r} prompt={prompt_ref!r}. A model-backed "
                "record that names neither cannot be audited after the alias moves"
            )
    else:
        if agent_kind != "deterministic":
            raise ValueError(
                f"{AGENT_RULE_FAMILY} is deterministic; declaring it {agent_kind!r} would "
                "refuse it from the backtest it is allowed to drive"
            )
        if model_ref is not None or prompt_ref is not None:
            raise ValueError(
                f"{AGENT_RULE_FAMILY} declares no model and no prompt; got "
                f"model={model_ref!r} prompt={prompt_ref!r}. Naming one claims a call the "
                "deterministic family never made"
            )

    hyperparameters: list[tuple[str, bool | int | float | str]] = [
        (AGENT_KIND_HYPERPARAMETER, agent_kind),
        (REGISTRATION_HYPERPARAMETER, registered.registration_sha256),
        (SCORING_SET_HYPERPARAMETER, scoring_set),
    ]
    if model_ref is not None:
        hyperparameters.append((MODEL_HYPERPARAMETER, model_ref))
    if prompt_ref is not None:
        hyperparameters.append((PROMPT_HYPERPARAMETER, prompt_ref))

    return AlphaModelDeclaration(
        name=f"c-{scoring_set}",
        family=family,
        horizon=AGENT_HORIZON,
        feature_version=registered.config_id,
        seed=registered.seed,
        code_commit=registered.code_commit,
        hyperparameters=tuple(sorted(hyperparameters)),
    )


def agent_batch(
    *,
    session: date,
    scores: Sequence[AgentScore],
    declaration: AlphaModelDeclaration,
    predicted_at: datetime,
) -> PredictionBatch:
    """One session's agent answers as a stored `PredictionBatch`.

    `as_of` is the session's publication instant (16:30 Shanghai) and the artifact's
    `training_cutoff` equals it -- see the module docstring for why equality is the right floor.
    `training_example_count` is the number of rows: the number of subjects the cycle was asked
    about, scored or abstained, because that is the batch's own denominator and
    `AlphaModelArtifact` refuses zero.
    """
    if not scores:
        raise ValueError(
            f"{session.isoformat()}: an agent batch carries no row; an empty batch is "
            "indistinguishable from a session that never ran"
        )
    as_of = session_publication_instant(session)
    rows = tuple(
        sorted(
            (
                Prediction(ts_code=item.ts_code, score=item.score)
                if item.score is not None
                else Prediction(ts_code=item.ts_code, abstention=item.abstention)
                for item in scores
            ),
            key=lambda row: row.ts_code,
        )
    )
    artifact = AlphaModelArtifact(
        declaration=declaration,
        feature_ids=AGENT_FEATURE_IDS,
        training_cutoff=as_of,
        training_example_count=len(rows),
    )
    return PredictionBatch(
        as_of=as_of,
        predicted_at=predicted_at,
        artifact=artifact,
        predictions=rows,
    )


def _registration_digest(batch: PredictionBatch) -> str:
    """The declaring registration's digest, from the declaration's own hyperparameters."""
    declared = dict(batch.artifact.declaration.hyperparameters).get(REGISTRATION_HYPERPARAMETER)
    if not isinstance(declared, str) or not declared.strip():
        raise ValueError(
            f"the agent batch's declaration names no {REGISTRATION_HYPERPARAMETER!r} "
            "hyperparameter; the filed record's provenance would be empty, and a prediction "
            "that cannot name the configuration that produced it is not auditable"
        )
    return declared


def file_agent_batch(
    store: ForwardRecordStore,
    batch: PredictionBatch,
    *,
    calendar: TradingCalendar,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """File `batch` through the forward-registration protocol; return its journal block.

    The order -- held-record lookup, call-auction refusal, `outcome_known_at_for`, `clock()`,
    `put` -- is `strategy_forward_session.file_forward_record`'s, and this function adds only the
    provenance the block owes: the registration digest from the declaration. The returned mapping
    is `prediction_block`'s thirteen-key stage, which is what the evening command journals.
    """
    record, outcome = file_forward_record(store, batch, calendar=calendar, clock=clock)
    return prediction_block(
        record,
        outcome,
        provenance=_registration_digest(record.batch),
    )
