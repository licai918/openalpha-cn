"""`V2-P7-015`: the guard on every path that consumes a history-evaluating result.

`tests/unit/runtime/test_llm_history_guard.py` holds the engine's choke point. Three other
paths reach the same contamination by not going through that engine at all:

- `openalpha validation record` / `POST /api/v1/backtests/validate` take a **finished**
  `ResearchRunResult` from a file or a request body, so the engine that produced it -- and its
  guard -- are not in the call stack. `OutcomeValidator.validate` is the only place the row's
  own manifest can be read, so the guard goes at the top of it.
- `openalpha strategy backtest` ranks on **stored predictions** whose declaration names an
  `agent_*` family (D5). The batch was produced by something that is not a fitted model, and
  `strategy_view._prediction_rows` is where the record becomes rows.
- `openalpha validation statistics` reads whatever the writers above stored. Because every
  writer refuses first, the set a reader aggregates is the set the guard admitted -- and that is
  the last assertion here: a refused request must leave the statistics byte-identical.

## Why the validation payload is a mutated engine result

`parse_research_result` re-derives every content address, so a hand-written JSON blob would be
refused for the wrong reason. These tests run a real deterministic cycle and then change the
two fields the row is *about* -- `mode`/`started_at` and `agent_versions` -- which is exactly
the shape a stored result from an older build can carry into a writer today. The manifest
address re-derives from the mutated content, so the payload the route and the command accept is
a self-consistent result the guard -- and only the guard -- can refuse.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from panel_fixtures import EXCHANGE, GeneratedPanel
from strategy_fixtures import READ_AT, write_strategy_corpus
from typer.testing import CliRunner

from openalpha_cn.api.app import create_app
from openalpha_cn.backtest.validation import OutcomeObservation, OutcomeValidator
from openalpha_cn.cli import PanelExit, app
from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
from openalpha_cn.domain.evidence import EvidenceSnapshot
from openalpha_cn.domain.llm_contamination import (
    AGENT_KIND_HYPERPARAMETER,
    HistoricalLLMRefusedError,
)
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.run import AgentVersion, RunMode, VersionRef
from openalpha_cn.domain.run_request import ResearchRunRequest
from openalpha_cn.domain.time import Timeline
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.strategy_view import (
    PREDICTION_COMPONENT,
    backtest_strategy,
    load_strategy_inputs,
    strategy_request,
)

DIGEST: Final[str] = "d" * 64
SUBJECT: Final[str] = "000001.SZ"
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
LLM_AGENT: Final[str] = "llm-judge"

runner: Final[CliRunner] = CliRunner()


# --- validation: a finished result carrying a model-backed agent -------------------------------


def _research_request(frozen_now: datetime) -> ResearchRunRequest:
    evidence = EvidenceSnapshot(
        subject=SUBJECT,
        kind="limit_up",
        timeline=Timeline(
            event_time=frozen_now,
            available_time=frozen_now,
            ingested_time=frozen_now,
            revision_time=frozen_now,
        ),
        source_id="guard.fixture",
        source_license="CC0-1.0",
        redistribution="allowed",
        summary="Guard-path fixture.",
        payload={
            "schema": "a-share-evidence/v1",
            "family": "market_event",
            "facts": {"close": 10.5, "pct_change": 9.99, "board_count": 1},
            "quality_flags": [],
        },
    )
    return ResearchRunRequest(
        run_id="guard-path-run",
        mode="replay",
        subject=SUBJECT,
        as_of=frozen_now,
        evidence=(evidence,),
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
    )


def _payload(
    runtime: Path,
    frozen_now: datetime,
    *,
    forward: bool,
    with_agent: bool = True,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """One real deterministic cycle, its manifest edited into the row under test."""
    result = OpenAlphaSDK(runtime_dir=runtime, clock=lambda: frozen_now).run_research(
        _research_request(frozen_now)
    )
    manifest = result.manifest
    if with_agent:
        manifest = manifest.model_copy(
            update={
                "agent_versions": (AgentVersion(agent_id=LLM_AGENT, kind="llm_backed"),),
                "model_versions": (
                    VersionRef(component="openai-compatible", version="deepseek-chat"),
                ),
            }
        )
    if forward:
        start = frozen_now if started_at is None else started_at
        manifest = manifest.model_copy(
            update={
                "mode": RunMode.paper,
                "started_at": start,
                "finished_at": start,
            }
        )
    return result.model_copy(update={"manifest": manifest}).model_dump(mode="json")


def _observation(frozen_now: datetime, *, days: int = 0) -> dict[str, Any]:
    start = frozen_now + timedelta(days=days)
    return OutcomeObservation(
        observation_start=start,
        observation_end=start + timedelta(days=5),
        start_price=10.0,
        end_price=11.0,
        benchmark_return=0.02,
        transaction_cost=0.005,
        data_quality_notes=("Synthetic outcome.",),
    ).model_dump(mode="json")


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _record_command(runtime: Path, research: Path, observation: Path, *extra: str) -> Any:
    return runner.invoke(
        app,
        [
            "validation",
            "record",
            "--research",
            str(research),
            "--observation",
            str(observation),
            "--runtime-dir",
            str(runtime),
            *extra,
        ],
    )


def test_validation_record_refuses_an_llm_backed_replay_result_by_name(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """A stored result can claim a model-backed agent and no engine guards this face.

    The refusal has to be the writer's own -- `OutcomeValidator.validate` reads the finished
    manifest -- and it has to reach the caller as a `bad_request` (3), not as the
    `internal_error` (5) an unanticipated exception becomes, because a person can act on the
    first and the second only names this file.
    """
    runtime = tmp_path / "runtime"
    payload = _payload(runtime, frozen_now, forward=False)

    outcome = _record_command(
        runtime,
        _write(tmp_path / "run.json", payload),
        _write(tmp_path / "observation.json", _observation(frozen_now)),
    )

    assert outcome.exit_code == int(PanelExit.bad_request), outcome.output
    assert LLM_AGENT in outcome.output
    assert "llm_backed" in outcome.output


def test_validation_record_accepts_a_forward_llm_result_within_the_lag(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """D4's other half: a forward result is what the incremental-IC read consumes.

    Refusing every LLM result would delete `V2-P7-014`'s subject rather than contaminate it, so
    a `paper`/`live`/`daily` result started within `LLM_FORWARD_MAX_LAG` of its `as_of` is
    admitted and stored -- and this is also the control that proves the refusal above is about
    the row and not about parsing.
    """
    runtime = tmp_path / "runtime"
    payload = _payload(runtime, frozen_now, forward=True)

    outcome = _record_command(
        runtime,
        _write(tmp_path / "run.json", payload),
        _write(tmp_path / "observation.json", _observation(frozen_now)),
        "--json",
    )

    assert outcome.exit_code == 0, outcome.output
    stored = json.loads(outcome.stdout)
    assert stored["signal_id"] == payload["signal"]["signal_id"]


def test_the_rest_validate_route_refuses_the_same_result(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The route and the command must refuse one payload identically -- `V2-P5-047`'s rule.

    Driven against the real application, so what is asserted is the response a caller receives:
    a 4xx carrying the guard's sentence, not a 500 from an unhandled `ValueError`.
    """
    runtime = tmp_path / "api-runtime"
    payload = _payload(runtime, frozen_now, forward=False)
    client = TestClient(create_app(runtime_dir=runtime, clock=lambda: frozen_now))

    response = client.post(
        "/api/v1/backtests/validate",
        json={"research": payload, "observation": _observation(frozen_now)},
    )

    assert response.status_code == 422, response.text
    assert LLM_AGENT in response.json()["detail"]
    assert "llm_backed" in response.json()["detail"]


def test_validation_statistics_is_unchanged_because_every_writer_passes_the_guard(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The reader aggregates only what the writers admitted, byte for byte.

    Two accepted forward observations clear `MINIMUM_INTERVAL_SAMPLE_SIZE`, so `statistics`
    answers rather than refusing; a refused replay result arriving afterwards must change
    nothing it can see. If the guard ran after `validation_store.append` instead of inside
    `validate`, this assertion is what would go red.
    """
    runtime = tmp_path / "runtime"
    accepted = _payload(runtime, frozen_now, forward=True)
    research = _write(tmp_path / "run.json", accepted)
    for index, offset in enumerate((0, 7)):
        recorded = _record_command(
            runtime,
            research,
            _write(tmp_path / f"observation-{index}.json", _observation(frozen_now, days=offset)),
            "--json",
        )
        assert recorded.exit_code == 0, recorded.output
    signal_id = accepted["signal"]["signal_id"]

    def statistics() -> Any:
        return runner.invoke(
            app,
            [
                "validation",
                "statistics",
                "--signal",
                signal_id,
                "--family-size",
                "1",
                "--dependence",
                "independent-or-positively-dependent",
                "--runtime-dir",
                str(runtime),
                "--json",
            ],
        )

    before = statistics()
    assert before.exit_code == 0, before.output

    refused = _record_command(
        runtime,
        _write(tmp_path / "refused.json", _payload(runtime, frozen_now, forward=False)),
        _write(tmp_path / "observation-refused.json", _observation(frozen_now)),
    )
    assert refused.exit_code == int(PanelExit.bad_request), refused.output

    after = statistics()
    assert after.exit_code == 0, after.output
    assert after.stdout == before.stdout


def test_the_validator_refuses_before_it_computes_any_term(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """The guard is the first statement of `OutcomeValidator.validate`, not a later filter.

    Driven directly because the two faces above cannot distinguish "refused before the
    arithmetic" from "refused and then nothing was stored": a validator that computed the
    attribution first and refused at the end would pass both. This is the one call both faces
    make, so guarding here is what makes their refusals the same refusal.
    """
    payload = _payload(tmp_path / "runtime", frozen_now, forward=False)
    from openalpha_cn.research_result_io import parse_research_result

    research = parse_research_result(payload)
    observation = OutcomeObservation.model_validate(_observation(frozen_now))

    with pytest.raises(HistoricalLLMRefusedError, match=LLM_AGENT):
        OutcomeValidator().validate(research=research, observation=observation)


# --- strategy backtest: the stored prediction's declaration ------------------------------------

POSITIVE_SCORES: Final[tuple[float, ...]] = (0.9, 0.8, 0.7)
"""Strictly decreasing, so the ranking is the security order and a tie cannot mask a bug."""


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> tuple[PanelStore, GeneratedPanel]:
    root = tmp_path_factory.mktemp("guard-strategy")
    panel = write_strategy_corpus(root)
    return PanelStore(root / "panel"), panel


def _declaration(*, family: str, agent_kind: str | None = AGENT_KIND_HYPERPARAMETER) -> Any:
    hyperparameters: tuple[tuple[str, bool | int | float | str], ...] = (
        ((AGENT_KIND_HYPERPARAMETER, agent_kind),) if agent_kind is not None else ()
    )
    return AlphaModelDeclaration(
        name="agent-declared",
        family=family,
        horizon="1d",
        feature_version="features/v1",
        seed=0,
        code_commit="0123456789abcdef",
        hyperparameters=hyperparameters,
    )


def _batch(
    *,
    day: date,
    declaration: AlphaModelDeclaration,
    scores: Mapping[str, float],
) -> PredictionBatch:
    published = session_publication_instant(day)
    return PredictionBatch(
        as_of=published,
        predicted_at=published,
        artifact=AlphaModelArtifact(
            declaration=declaration,
            feature_ids=("reversal_1d/v1@raw",),
            training_cutoff=published,
            training_example_count=len(scores),
            parameters=(("weight", 1.0),),
        ),
        predictions=tuple(
            Prediction(ts_code=code, score=value) for code, value in sorted(scores.items())
        ),
    )


def _record(
    *,
    day: date,
    declaration: AlphaModelDeclaration,
    scores: Mapping[str, float],
) -> PredictionRecord:
    batch = _batch(day=day, declaration=declaration, scores=scores)
    return PredictionRecord(
        batch=batch,
        outcome_known_at=batch.as_of + timedelta(days=10),
        recorded_at=batch.predicted_at,
    )


def _scores(panel: GeneratedPanel) -> dict[str, float]:
    return {code: score for code, score in zip(panel.securities[:3], POSITIVE_SCORES, strict=True)}


def _request(panel: GeneratedPanel, *, prediction_ids: tuple[str, ...]) -> Any:
    return strategy_request(
        components=(),
        prediction_ids=prediction_ids,
        combine="zscore_sum",
        transform=None,
        neutralization=None,
        start=panel.sessions[1],
        end=panel.sessions[-1],
        as_of=READ_AT,
        exchange=EXCHANGE,
        rebalance_every_sessions=3,
        holding_count=3,
        buffer_rank=None,
        max_industry_weight=None,
    )


def test_strategy_backtest_refuses_an_llm_backed_agent_prediction_by_name(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """D5: regardless of standing. The record here is an ordinary in-time one.

    Nothing about the timing makes this prediction wrong; what makes it unusable is that an
    `agent_*` declaration declaring its kind `llm_backed` says a language model produced these
    scores, and a historical evaluation may not rank on them.
    """
    store, panel = corpus
    held = _record(
        day=panel.sessions[1],
        declaration=_declaration(family="agent_judgement", agent_kind="llm_backed"),
        scores=_scores(panel),
    )

    with pytest.raises(HistoricalLLMRefusedError, match=held.record_id):
        backtest_strategy(
            store,
            _request(panel, prediction_ids=(held.record_id,)),
            predictions={held.record_id: held}.get,
        )


def test_strategy_backtest_refuses_an_agent_family_that_does_not_declare_its_kind(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """Fail-closed, per D5: silence is not determinism.

    A declaration an `agent_*` family with no `agent_kind` hyperparameter cannot be told apart
    from a model-backed one, and the default of admitting it would be the silent contamination
    this issue exists to make impossible.
    """
    store, panel = corpus
    held = _record(
        day=panel.sessions[1],
        declaration=_declaration(family="agent_judgement", agent_kind=None),
        scores=_scores(panel),
    )

    with pytest.raises(HistoricalLLMRefusedError, match="agent_kind") as refused:
        backtest_strategy(
            store,
            _request(panel, prediction_ids=(held.record_id,)),
            predictions={held.record_id: held}.get,
        )
    assert held.record_id in str(refused.value)


def test_strategy_backtest_accepts_a_deterministic_agent_prediction(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """The admitted row, and it has to actually trade -- not merely not-raise.

    Three records, one per rebalance signal day, so every period has a cross section: the test
    asserts the book's periods carry the ranked names rather than only that no exception was
    raised, because a guard that admitted the declaration and then read no rows would leave the
    second assertion green and the backtest empty.
    """
    store, panel = corpus
    declaration = _declaration(family="agent_rule", agent_kind="deterministic")
    scores = _scores(panel)
    records = tuple(
        _record(day=panel.sessions[index], declaration=declaration, scores=scores)
        for index in (1, 4, 7)
    )
    by_id = {record.record_id: record for record in records}

    result = backtest_strategy(
        store,
        _request(panel, prediction_ids=tuple(by_id)),
        predictions=by_id.get,
    )

    assert [period.start for period in result.periods] == [
        panel.sessions[1],
        panel.sessions[4],
        panel.sessions[7],
    ]
    traded = {fill.subject for period in result.periods for fill in period.fills}
    assert traded == set(scores), "the ranked names are the ones the book bought"
    inputs = load_strategy_inputs(
        store,
        _request(panel, prediction_ids=tuple(by_id)),
        predictions=by_id.get,
    )
    assert {row.component for row in inputs.scores} == {PREDICTION_COMPONENT}
    assert {row.value for row in inputs.scores} == set(POSITIVE_SCORES)


def test_a_declaration_whose_family_is_not_an_agent_family_is_not_refused(
    corpus: tuple[PanelStore, GeneratedPanel],
) -> None:
    """The guard's boundary: a fitted `strategy_`/`MODEL_FAMILIES` declaration is not an agent's.

    The prefix is the whole filter, so a prediction from something this guard has no opinion
    about still reaches the book -- otherwise the first strategy registration would be refused
    as if it were a model answer.
    """
    store, panel = corpus
    declaration = _declaration(family="strategy_composite", agent_kind=None)
    scores = _scores(panel)
    records = tuple(
        _record(day=panel.sessions[index], declaration=declaration, scores=scores)
        for index in (1, 4, 7)
    )
    by_id = {record.record_id: record for record in records}

    result = backtest_strategy(
        store,
        _request(panel, prediction_ids=tuple(by_id)),
        predictions=by_id.get,
    )

    assert any(period.fills for period in result.periods)
    assert {fill.subject for period in result.periods for fill in period.fills} <= set(scores)


def test_the_refusal_names_the_prediction_and_the_declaration(
    frozen_now: datetime,
) -> None:
    """One sentence a user can act on: which prediction, which family, what was declared."""
    from openalpha_cn.domain.llm_contamination import refuse_llm_prediction_in_backtest

    declaration = _declaration(family="agent_judgement", agent_kind="llm_backed")
    with pytest.raises(HistoricalLLMRefusedError) as refused:
        refuse_llm_prediction_in_backtest(prediction_id="prd_" + "0" * 24, declaration=declaration)

    message = str(refused.value)
    assert "prd_" + "0" * 24 in message
    assert "agent_judgement" in message
    assert "agent_kind='llm_backed'" in message


def test_the_forward_payload_still_carries_the_lag_in_its_message(
    tmp_path: Path, frozen_now: datetime
) -> None:
    """D4's lag is a number the caller needs, not a boolean.

    A result started late is refused by the *forward* half of the guard even though its mode is
    not historical; the sentence has to say how late, or a user cannot tell a clock fault from a
    rerun of an old `run_id`.
    """
    runtime = tmp_path / "late-runtime"
    payload = _payload(
        runtime,
        frozen_now,
        forward=True,
        started_at=frozen_now + timedelta(hours=73),
    )

    outcome = _record_command(
        runtime,
        _write(tmp_path / "late.json", payload),
        _write(tmp_path / "late-observation.json", _observation(frozen_now)),
    )

    assert outcome.exit_code == int(PanelExit.bad_request), outcome.output
    assert LLM_AGENT in outcome.output
    assert "3 days, 1:00:00" in outcome.output
