"""Deterministic A-share baseline agents."""

from collections.abc import Mapping
from typing import Literal, cast

from openalpha_cn.agents.base import AgentContext, AgentProvenance, AgentResult
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.risk_flag import RiskFlag, UndeclaredRiskFlagError, parse_risk_flag
from openalpha_cn.domain.signal import SignalFrame

DETERMINISTIC: AgentProvenance = AgentProvenance(kind="deterministic")
"""What all three agents in this module are, stated once (`V2-P4-010`, S40).

One shared instance rather than three identical literals, for the reason `V2-P4-003` records
about `RunMode`: a value written out per class is a value that drifts when somebody edits two
of the three. `AgentProvenance` is frozen, so sharing it is safe.
"""

NO_FEATURE_COLUMNS: frozenset[str] = frozenset()
"""What all three agents in this module read off the panel plane: nothing (`V2-P4-008`, S38).

Named rather than written out as a bare `frozenset()` three times, for the reason above and one
more: this is a *positive* declaration and not an omission. Every one of these agents scores
`context.evidence` and none of them touches `context.features`, so `AgentRouter` selects them on
their evidence families exactly as it did before feature dependencies existed -- and a reader who
meets `feature_dependencies = frozenset()` on an agent should be able to tell "reads no column"
from "somebody forgot", which a name can say and a literal cannot.
"""


def _family(item: EvidenceSnapshot) -> str:
    payload = item.payload
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("family", ""))


def _facts(item: EvidenceSnapshot) -> Mapping[str, object]:
    payload = item.payload
    if not isinstance(payload, Mapping):
        return {}
    facts = payload.get("facts")
    return facts if isinstance(facts, Mapping) else {}


def _quality_flags(items: tuple[EvidenceSnapshot, ...]) -> tuple[RiskFlag, ...]:
    """Every declared risk flag the evidence payloads carry, refusing any string that is not one.

    ## Why this raises rather than copying (`V2-P4-030`)

    This function used to `str()` whatever an evidence payload's `quality_flags` held and hand
    the result to `SignalFrame.risk_flags`, which was `tuple[str, ...]` and accepted it. That is
    the producer half of the open-set defect, and it is reachable from outside the process:
    `EvidenceSnapshot.payload` is a `JsonValue` with no schema, and
    `POST /api/v1/research/run`, `POST /api/v1/research/batches`,
    `POST /api/v1/backtests/replay` and two CLI commands all take evidence straight off a
    request body. `freeze_json` turns a JSON list into the `tuple` the old `isinstance` check
    was looking for, so `{"quality_flags": ["future-data"]}` arrived intact.

    **This list says which paths reach here and never said which of them handle it, and a
    product acceptance read it as the latter** (`V2-P4-101`/`102`). Measured: three of the five
    let the refusal escape -- `500 text/plain`, a Typer traceback, and a batch item recording
    the bare word `ValueError` -- and `POST /api/v1/backtests/replay` (with `openalpha replay
    run`, which shares its runner) was **always correct**, because `ReplayRunner.run` catches
    `(RuntimeError, ValueError)` per case and records `f"{run_id}: {type}: {error}"`. The three
    are fixed and the fourth is the shape they were fixed into; the distinction is written down
    here so the next reader of this list does not have to measure it again.

    The consequence was not that the junk string was ignored. It was scored: an unrecognised
    flag ranks **above** a recognised one on `product/governance.py`'s ladder, so a payload
    misspelling the build's most serious flag moved its candidate *up* a governed screen.

    Refusing is therefore the fail-closed answer and copying was the fail-open one. `RiskFlag`
    raises `UndeclaredRiskFlagError` naming the offending string, so a producer learns which flag
    it spelled wrong at the point it wrote it, instead of shipping a promotion. A caller who
    wants a cosmetic annotation on evidence has the rest of `payload` to put it in;
    `quality_flags` is the field the gates read.

    ## Why the address is attached here (`V2-P4-101`)

    `parse_risk_flag` is handed a bare string and cannot know where it came from. This loop can,
    and it is the only place in the process that can: the REST face turns `evidence_id` and
    `flag_index` into the `loc` of a field error, so a caller is told
    `["body", "evidence", 1, "payload", "quality_flags", 1]` rather than that something,
    somewhere, in a request carrying five thousand items was misspelled. Re-raising rather than
    letting the original escape is what makes that address exist at all -- and it is done with
    `enumerate` over the raw tuple rather than a comprehension precisely so the position is a
    real index and not a guess.

    The first offender wins and the rest are not collected. A payload is refused whole (see
    `test_one_undeclared_flag_refuses_the_whole_signal_rather_than_the_flag`), so a second
    address would describe a request that was already going to be refused by the first.
    """
    flags: set[RiskFlag] = set()
    for item in items:
        payload = item.payload
        if not isinstance(payload, Mapping):
            continue
        raw_flags = payload.get("quality_flags", ())
        if isinstance(raw_flags, tuple):
            for index, flag in enumerate(cast(tuple[object, ...], raw_flags)):
                try:
                    flags.add(parse_risk_flag(str(flag)))
                except UndeclaredRiskFlagError as error:
                    raise UndeclaredRiskFlagError(
                        error.value, evidence_id=item.evidence_id, flag_index=index
                    ) from error
    return tuple(sorted(flags))


def _number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


class MarketAgent:
    """Score limit-up, consecutive-board, and broken-board evidence."""

    agent_id = "market-agent"
    evidence_families = frozenset({"market_event"})
    feature_dependencies = NO_FEATURE_COLUMNS
    provenance = DETERMINISTIC

    def analyze(self, context: AgentContext) -> AgentResult:
        items = tuple(item for item in context.evidence if _family(item) == "market_event")
        scores = {
            "limit_up": 0.65,
            "consecutive_board": 0.85,
            "broken_board": -0.65,
        }
        strength = sum(scores.get(item.kind, 0.0) for item in items) / len(items)
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish" if strength > 0.15 else "bearish" if strength < -0.15 else "neutral"
        )
        signal = SignalFrame(
            subject=context.subject,
            as_of=context.as_of,
            direction=direction,
            strength=strength,
            confidence=min(0.9, 0.55 + len(items) * 0.1),
            horizon="5d",
            evidence_ids=tuple(item.evidence_id for item in items),
            confirmation_conditions=("Event strength persists with confirming volume.",),
            invalidation_conditions=("Price closes below the event-day low.",),
            risk_flags=_quality_flags(items),
        )
        return AgentResult(
            agent_id=self.agent_id,
            signal=signal,
            rationale="Deterministic board-event score from visible market evidence.",
        )


class ThemeAgent:
    """Score theme evidence using its normalized relevance value."""

    agent_id = "theme-agent"
    evidence_families = frozenset({"theme", "catalyst", "disclosure"})
    feature_dependencies = NO_FEATURE_COLUMNS
    provenance = DETERMINISTIC

    def analyze(self, context: AgentContext) -> AgentResult:
        items = tuple(item for item in context.evidence if _family(item) in self.evidence_families)
        scores: list[float] = []
        for item in items:
            facts = _facts(item)
            score = _number(facts.get("score"), default=0.6)
            scores.append(max(-1.0, min(1.0, (score - 0.5) * 2)))
        strength = sum(scores) / len(scores)
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish" if strength > 0.15 else "bearish" if strength < -0.15 else "neutral"
        )
        signal = SignalFrame(
            subject=context.subject,
            as_of=context.as_of,
            direction=direction,
            strength=strength,
            confidence=min(0.85, 0.5 + len(items) * 0.1),
            horizon="10d",
            evidence_ids=tuple(item.evidence_id for item in items),
            confirmation_conditions=("Theme evidence gains independent confirmation.",),
            invalidation_conditions=("Catalyst timing or theme relevance weakens.",),
            risk_flags=_quality_flags(items),
        )
        return AgentResult(
            agent_id=self.agent_id,
            signal=signal,
            rationale="Deterministic theme and catalyst relevance score.",
        )


class CapitalAgent:
    """Score normalized capital-flow evidence."""

    agent_id = "capital-agent"
    evidence_families = frozenset({"capital"})
    feature_dependencies = NO_FEATURE_COLUMNS
    provenance = DETERMINISTIC

    def analyze(self, context: AgentContext) -> AgentResult:
        items = tuple(item for item in context.evidence if _family(item) == "capital")
        net_inflow = sum(_number(_facts(item).get("net_inflow")) for item in items)
        strength = 0.4 if net_inflow > 0 else -0.4 if net_inflow < 0 else 0.0
        direction: Literal["bullish", "bearish", "neutral"] = (
            "bullish" if strength > 0 else "bearish" if strength < 0 else "neutral"
        )
        signal = SignalFrame(
            subject=context.subject,
            as_of=context.as_of,
            direction=direction,
            strength=strength,
            confidence=0.6,
            horizon="5d",
            evidence_ids=tuple(item.evidence_id for item in items),
            confirmation_conditions=("Net inflow remains positive across the next observation.",),
            invalidation_conditions=("Capital flow reverses materially.",),
            risk_flags=_quality_flags(items),
        )
        return AgentResult(
            agent_id=self.agent_id,
            signal=signal,
            rationale="Deterministic sign-based capital-flow score.",
        )


def baseline_agents() -> tuple[MarketAgent, ThemeAgent, CapitalAgent]:
    """Return built-in agents in stable routing order."""
    return (MarketAgent(), ThemeAgent(), CapitalAgent())
