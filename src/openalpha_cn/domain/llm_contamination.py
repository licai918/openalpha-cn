"""`V2-P7-015`: the one refusal that keeps a model out of every path that evaluates history.

A research cycle that names an `llm_backed` agent has done two things a manifest records and
one it cannot: `RunManifest.agent_versions` says an agent of that kind ran,
`RunManifest.model_versions` says which vendor model it called, and nothing anywhere says what
inputs the model was shown or what it returned. For a **forward** cycle that is a declared
limitation of the vendor boundary and not a contamination: the evidence is point-in-time, the
answer arrives after the question reaches the market, and the decision is a candidate until
the outcome is known. For a cycle that **re-evaluates history** -- `replay`, `backtest` -- it
is the failure the whole evaluation exists to avoid. A model asked in 2026 about a session in
2025 may have that session in its weights, in its retrieval, or in the operator's prompt, and
no artefact this build stores can tell a reader which. Re-running the same request twice is
also no longer guaranteed to agree, so `backtest/replay.py`'s determinism claim would be
measured on a component that is not deterministic.

## Two refusals, one error

`refuse_llm_agents_in_history` is the choke-point refusal: a *roster* is refused by the path
that is about to evaluate history. `refuse_llm_prediction_in_backtest` is the prediction-store
refusal (D5): a stored `PredictionBatch` was produced by a declaration that names an
`agent_*` family, and a strategy backtest must not consume it. Both raise
`HistoricalLLMRefusedError`, because a caller -- the CLI, the API route, the engine -- has the
same thing to do with either: refuse the request by name and change nothing.

## D4: the forward lag, and why it is in this module

An LLM-backed agent is admitted on a forward cycle, and only within `LLM_FORWARD_MAX_LAG` of
the `as_of` the cycle claims. The lag is not a second contamination bound on the historical
modes; it is the same fact noticed by waiting instead of by asking. The forward deadline
(`strategy_forward_session`'s 09:15 call-auction cutoff) decides when a forward decision may be
*acted* on; this decides when a model's answer about an `as_of` is still an answer about the
present. D4's 72 hours covers the worst ordinary gap the daily chain has -- Friday's 16:30
close to Monday's catch-up -- and nothing longer is a lag this build has measured, so a cycle
older than that is refused rather than trusted. That makes re-invoking an old forward run's
`run_id` more than 72 hours later a refusal, which is intended and fail-closed.

## Why `domain/`

Both refusals are pure: a mode, two instants, a roster or a declaration in; nothing or an
exception out. Nothing here reads a store, a clock or a model, which is what lets the engine,
`OutcomeValidator` and `strategy_view` -- three layers that may not import each other -- each
call the same rule. `backtest/validation.py` is held to importing `domain` only by
`backtest-studies-touch-no-store`, and `strategy_view` must be able to name the refusal without
importing the engine.

## D5: what a strategy backtest refuses, and the fail-closed edge

Every prediction whose declaration names a family beginning `agent_` is refused on the
`strategy backtest` path regardless of its `standing` (D5). The instruction is stronger than
"never trust an LLM on history" -- C does not trade, and its own evaluation is the incremental
IC read of `V2-P7-014`, which is a forward comparison against realised outcomes rather than a
backtest that consumes the model's scores. A declaration that **is** an agent family and
does **not** declare `agent_kind` is refused (fail-closed): a reader cannot tell an LLM-backed
agent from a deterministic one if the declaration declines to say, and the default of trusting
it would be the silent contamination this module exists to make impossible. Only
`agent_kind="deterministic"` is admitted -- that is the one value D5 names as passing, and an
`agent_*` family that is neither deterministic nor visibly a language model (the declared but
unproduced `learned` kind) is a question for `V2-P7-014`'s read, not for this guard to admit.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from openalpha_cn.domain.alpha_model import AlphaModelDeclaration
from openalpha_cn.domain.run import AgentKind
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.time import ensure_aware

HISTORICAL_RUN_MODES: Final[frozenset[RunMode]] = frozenset({RunMode.replay, RunMode.backtest})
"""The modes that evaluate history, and therefore may run no model-backed agent.

`live`, `paper` and `daily` are forward cycles: their evidence is read point-in-time and their
conclusions reach the market after the question was formed, so an LLM-backed agent is a
declared limitation rather than a leak. Two members and not a complement of the forward set,
because a future mode is historical until somebody measures that it is not.
"""

LLM_FORWARD_MAX_LAG: Final[timedelta] = timedelta(hours=72)
"""D4: how far behind an `as_of` a forward cycle may start and still admit a model answer.

Friday 16:30 Shanghai to Monday's catch-up is the widest ordinary gap the daily chain has, and
72 hours is the bound that covers it. A cycle started later than this is refused on every
mode, not only on `live`: `paper` and `daily` make the same claim about how old their question
is.
"""

AGENT_FAMILY_PREFIX: Final[str] = "agent_"
"""The family prefix that makes an `AlphaModelDeclaration` an agent's rather than a model's.

`AlphaModelDeclaration.family` is fixed by the implementation that will fit the declaration
(`"linear"`, `"single_feature_reference"`), so an agent family is not a fit at all -- it is a
marker that the prediction came out of `agents/`. The registered strategies name their family
`strategy_<kind>` and are therefore not caught by the prefix; nothing under `src/` claims an
`agent_` family today, which is why this refusal lands before the first one exists rather
than after.
"""

AGENT_KIND_HYPERPARAMETER: Final[str] = "agent_kind"
"""The hyperparameter an `agent_` family must declare for a strategy backtest to read it.

A hyperparameter rather than a new declaration field: the field set of
`AlphaModelDeclaration` reaches the artifact address, so a field added for a mark that is not
part of what was fitted would move the address of every artifact -- and `V2-P4-016`'s field
survey is the gate that would go red. A flat scalar hyperparameter is exactly what this
contract has room for.
"""


class HistoricalLLMRefusedError(ValueError):
    """A model-backed agent (or its prediction) was refused on a path that evaluates history.

    A `ValueError` for the reason `domain/evidence.py::LookAheadViolationError` is one: every
    refusal here is about a value that cannot mean what it says -- a run that claims not to
    read the future while composing a component whose inputs are not pinned -- and callers
    already catch `ValueError` on these paths. The type is what the CLI/API map to a 4xx, so
    it is deliberately distinct from a generic `ValueError`: a refusal a user can act on is
    not a server fault.
    """


def refuse_llm_agents_in_history(
    *,
    path: str,
    mode: RunMode,
    as_of: datetime,
    ran_at: datetime,
    agents: Sequence[tuple[str, AgentKind]],
) -> None:
    """Refuse a roster containing an `llm_backed` agent that would run on `path`.

    `path` is the caller's own name for the surface (`"engine"`, `"validation"`, a command
    name) and appears verbatim in the message: this refusal reaches a user through three
    faces, and "refused" without "by which face" is the sentence none of them can act on.
    `agents` are the ones the caller is about to run -- for the engine, the **selected** roster
    rather than the composed one, because an agent the router dropped contaminates nothing.

    Refused when either:

    - `mode in HISTORICAL_RUN_MODES` -- the cycle re-evaluates history and a model's inputs
      are not pinned by anything this build stores; or
    - the cycle started more than `LLM_FORWARD_MAX_LAG` after the `as_of` it claims -- a
      forward cycle whose question is three days old is the same leak reached by waiting.

    Both instants are normalised through `domain.time.ensure_aware` before the comparison and
    appear in the message, so a reader can see the lag that decided it. Equality is admitted:
    72 hours exactly is D4's bound, and `<` versus `<=` is the whole of D4's boundary.
    """
    llm_agents = tuple(agent_id for agent_id, kind in agents if kind == "llm_backed")
    if not llm_agents:
        return
    declared_as_of = ensure_aware(as_of)
    started = ensure_aware(ran_at)
    lag = started - declared_as_of
    roster = ", ".join(repr(agent_id) for agent_id in llm_agents)
    subject = "it declares" if len(llm_agents) == 1 else "they declare"
    detail = (
        f"(mode={mode.value}, as_of={declared_as_of.isoformat()}, "
        f"ran_at={started.isoformat()}, lag={lag})"
    )
    if mode in HISTORICAL_RUN_MODES:
        raise HistoricalLLMRefusedError(
            f"{path} refuses {roster}: {subject} kind 'llm_backed', and this cycle evaluates "
            f"history {detail} -- a model asked about that past may already have its "
            "outcome, and no artefact stored by this build records what it was shown or "
            "what it returned"
        )
    if lag > LLM_FORWARD_MAX_LAG:
        raise HistoricalLLMRefusedError(
            f"{path} refuses {roster}: {subject} kind 'llm_backed', and this forward cycle "
            f"started more than the {LLM_FORWARD_MAX_LAG} forward lag after the as_of it "
            f"claims {detail} -- a model answering about a question that old may have seen "
            "the outcome it is being asked to forecast"
        )


def refuse_llm_prediction_in_backtest(
    *,
    prediction_id: str,
    declaration: AlphaModelDeclaration,
) -> None:
    """Refuse a stored prediction an `agent_` family produced, per D5.

    Called after the record is looked up and before any of its rows are read, so a refused
    prediction contributes no row, no session and no score to the backtest -- the store's own
    record survives, which is what makes the refusal auditable rather than a deletion.

    Families not beginning `AGENT_FAMILY_PREFIX` are admitted unchanged: a `strategy_` or a
    `MODEL_FAMILIES` declaration is a fitted or composite artefact, not an agent's output, and
    this guard has no opinion about it. For an agent family, only
    `AGENT_KIND_HYPERPARAMETER="deterministic"` is admitted; a missing kind is refused
    (fail-closed) and so is every other declared value.
    """
    family = declaration.family
    if not family.startswith(AGENT_FAMILY_PREFIX):
        return
    declared = dict(declaration.hyperparameters).get(AGENT_KIND_HYPERPARAMETER)
    if declared is None:
        raise HistoricalLLMRefusedError(
            f"strategy backtest refuses prediction {prediction_id}: its declaration names the "
            f"agent family {family!r} and declares no {AGENT_KIND_HYPERPARAMETER!r} "
            "hyperparameter, so a reader cannot tell whether a model produced it; this path "
            "refuses what it cannot rule out rather than admitting it"
        )
    if declared == "deterministic":
        return
    raise HistoricalLLMRefusedError(
        f"strategy backtest refuses prediction {prediction_id}: its declaration names the "
        f"agent family {family!r} declaring {AGENT_KIND_HYPERPARAMETER}={declared!r}, and "
        "only a deterministic agent's prediction may drive a historical evaluation (D5)"
    )
