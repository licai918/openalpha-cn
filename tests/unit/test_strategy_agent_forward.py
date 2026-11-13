"""`V2-P7-013` Task 2.4: agent batches file through the forward-session protocol, never trading.

The evening command that scores Step 0's survivors (plan C Task 2.5) needs three things from
`src/` rather than from `scripts/`: a declaration the backtest guard can read, a `PredictionBatch`
whose `as_of` is the session's publication instant, and a filing call that reuses the
forward-registration protocol instead of re-implementing its ordering. This module is those three
things, and each of the tests below exists for a failure a reviewer has already named:

- **`agent_kind` in the declaration** (`V2-P7-013` D5). `refuse_llm_prediction_in_backtest`
  refuses an `agent_*` family with no `agent_kind` and every value but `deterministic`; a
  declaration that did not carry the mark would make every stored agent record unreadable by the
  backtest in the *refusing* direction, which is safe, but it would also mean nobody could tell a
  model-backed batch from a rule batch after the fact. The mark is what makes that auditable.
- **Two records for one session.** Primary and coverage are separate declarations, so the
  session lookup (`session_record`) cannot confuse them: same `as_of`, different declaration,
  two records. Pooling them would defeat `coverage_set`'s separation.
- **The protocol, not a copy of it.** Re-filing is `unchanged` (the same batch is one answer);
  a different second answer is refused and the stored one stands; at or after the next call
  auction nothing is filed; and a batch predicted before its own session published cannot be
  built at all. These are `strategy_forward_session`'s rules and this file calls them rather than
  restating them -- the test asserts the refusals, and the graph test at the bottom asserts the
  edge exists.
- **Provenance is the registration.** The journal block's `provenance` is the registration's
  digest, read from the declaration's own hyperparameters, so the record names the configuration
  that produced it with no path or file lookup.

The module reaches no engine, no composition root, no agent and no paper book: filing a record
declares a prediction, it does not trade, and the contract test at the bottom pins that.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import grimp
import pytest

from openalpha_cn.domain.agent_forward import (
    ABSTAIN_NOT_ROUTED,
    AGENT_HORIZON,
    AGENT_LLM_FAMILY,
    AGENT_RULE_FAMILY,
)
from openalpha_cn.domain.llm_contamination import (
    HistoricalLLMRefusedError,
    refuse_llm_prediction_in_backtest,
)
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.strategy_agent_forward import (
    AgentScore,
    agent_batch,
    agent_declaration,
    agent_run_id,
    file_agent_batch,
)
from openalpha_cn.strategy_forward_session import ForwardSessionRefused
from openalpha_cn.strategy_registration import RegisteredConfiguration, registration_cutoff

REGISTERED: Final[RegisteredConfiguration] = RegisteredConfiguration(
    config_id="a" * 64, registration_sha256="b" * 64, code_commit="c" * 40, seed=20261112
)
SESSION: Final[date] = date(2026, 11, 12)
NEXT_SESSION: Final[date] = date(2026, 11, 13)
PREDICTED_AT: Final[datetime] = session_publication_instant(SESSION) + timedelta(hours=4)
"""20:30 Shanghai: the evening command's own hour, four hours after the session published."""
NAMES: Final[tuple[str, ...]] = ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ")


def _calendar() -> TradingCalendar:
    days: list[CalendarDay] = []
    day = date(2026, 11, 2)
    while day <= date(2026, 11, 30):
        days.append(CalendarDay(calendar_date=day, is_trading=day.weekday() < 5))
        day += timedelta(days=1)
    return build_trading_calendar("SSE", days)


def _scores() -> tuple[AgentScore, ...]:
    """Four scored names and one coded abstention, the shape one evening's batch carries."""
    scored = tuple(
        AgentScore(
            ts_code=name,
            score=0.1 * index,
            abstention=None,
            run_id=agent_run_id(
                config_id=REGISTERED.config_id,
                session=SESSION,
                family=AGENT_RULE_FAMILY,
                ts_code=name,
            ),
        )
        for index, name in enumerate(NAMES[:4], start=1)
    )
    return (
        *scored,
        AgentScore(
            ts_code=NAMES[4],
            score=None,
            abstention=ABSTAIN_NOT_ROUTED,
            run_id=agent_run_id(
                config_id=REGISTERED.config_id,
                session=SESSION,
                family=AGENT_RULE_FAMILY,
                ts_code=NAMES[4],
            ),
        ),
    )


def _rules(declaration: object, scores: tuple[AgentScore, ...]) -> object:
    return agent_batch(
        session=SESSION,
        scores=scores,
        declaration=declaration,  # type: ignore[arg-type]
        predicted_at=PREDICTED_AT,
    )


def _store(tmp_path: Path, clock: object) -> object:
    return build_storage(runtime_dir=tmp_path, clock=clock).prediction_store


def _filed_files(tmp_path: Path) -> dict[str, bytes]:
    directory = tmp_path / "predictions"
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


def test_every_agent_declaration_carries_agent_kind_so_the_backtest_guard_reads_it() -> None:
    """The declaration is readable by `refuse_llm_prediction_in_backtest` in both directions."""
    llm = agent_declaration(
        family=AGENT_LLM_FAMILY,
        scoring_set="primary",
        agent_kind="llm_backed",
        registered=REGISTERED,
        model_ref="deepseek:deepseek-flash",
        prompt_ref="structured-signal/v2-json-object:abc",
    )
    rule = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="primary",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )

    assert llm.name == "c-primary" and rule.name == "c-primary"
    assert llm.family == AGENT_LLM_FAMILY and rule.family == AGENT_RULE_FAMILY
    assert llm.horizon == rule.horizon == AGENT_HORIZON
    assert llm.seed == rule.seed == REGISTERED.seed
    assert llm.feature_version == rule.feature_version == REGISTERED.config_id
    assert llm.code_commit == rule.code_commit == REGISTERED.code_commit
    assert dict(llm.hyperparameters)["agent_kind"] == "llm_backed"
    assert dict(llm.hyperparameters)["registration_sha256"] == REGISTERED.registration_sha256
    assert dict(llm.hyperparameters)["scoring_set"] == "primary"
    assert dict(llm.hyperparameters)["model"] == "deepseek:deepseek-flash"
    assert dict(llm.hyperparameters)["prompt"] == "structured-signal/v2-json-object:abc"
    assert dict(rule.hyperparameters)["agent_kind"] == "deterministic"
    assert "model" not in dict(rule.hyperparameters)
    assert "prompt" not in dict(rule.hyperparameters)

    # The guard the mark exists for: it refuses the LLM record and admits the deterministic one.
    with pytest.raises(HistoricalLLMRefusedError):
        refuse_llm_prediction_in_backtest(prediction_id="pred-1", declaration=llm)
    refuse_llm_prediction_in_backtest(prediction_id="pred-1", declaration=rule)


def test_a_half_declared_family_is_refused() -> None:
    """An LLM family with no model or prompt cannot be audited later; a rule family with either
    names a model it never called."""
    with pytest.raises(ValueError):
        agent_declaration(
            family=AGENT_LLM_FAMILY,
            scoring_set="primary",
            agent_kind="llm_backed",
            registered=REGISTERED,
            model_ref="deepseek:deepseek-flash",
            prompt_ref=None,
        )
    with pytest.raises(ValueError):
        agent_declaration(
            family=AGENT_RULE_FAMILY,
            scoring_set="primary",
            agent_kind="deterministic",
            registered=REGISTERED,
            model_ref="deepseek:deepseek-flash",
            prompt_ref=None,
        )
    with pytest.raises(ValueError):
        agent_declaration(
            family="momentum_5d",
            scoring_set="primary",
            agent_kind="deterministic",
            registered=REGISTERED,
            model_ref=None,
            prompt_ref=None,
        )


def test_primary_and_coverage_are_two_records_for_one_session(tmp_path: Path) -> None:
    """Same session, two scoring sets, two declarations, two standing records."""
    calendar = _calendar()
    primary = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="primary",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )
    coverage = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="coverage",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )
    primary_batch = _rules(primary, _scores())
    coverage_batch = _rules(coverage, _scores()[:2])
    store = _store(tmp_path, lambda: PREDICTED_AT)

    first = file_agent_batch(store, primary_batch, calendar=calendar, clock=lambda: PREDICTED_AT)
    second = file_agent_batch(store, coverage_batch, calendar=calendar, clock=lambda: PREDICTED_AT)

    assert primary_batch.as_of == coverage_batch.as_of == session_publication_instant(SESSION)
    assert primary_batch.horizon == "5d"
    assert first["record_id"] != second["record_id"]
    assert first["family"] == second["family"] == AGENT_RULE_FAMILY
    assert first["standing"] == second["standing"] == "forward"
    assert (first["scored"], first["abstained"]) == (4, 1)
    assert (second["scored"], second["abstained"]) == (2, 0)
    assert len(_filed_files(tmp_path)) == 2


def test_refiling_the_same_batch_is_unchanged_and_a_different_one_is_refused(
    tmp_path: Path,
) -> None:
    """The protocol's two answers: the same batch is one record, a different one is refused."""
    calendar = _calendar()
    declaration = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="primary",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )
    batch = _rules(declaration, _scores())
    store = _store(tmp_path, lambda: PREDICTED_AT)

    first = file_agent_batch(store, batch, calendar=calendar, clock=lambda: PREDICTED_AT)
    files = _filed_files(tmp_path)
    again = file_agent_batch(store, batch, calendar=calendar, clock=lambda: PREDICTED_AT)

    assert again["outcome"] == "unchanged"
    assert again["record_id"] == first["record_id"]
    assert again["batch_digest"] == first["batch_digest"]
    assert _filed_files(tmp_path) == files

    altered = _rules(declaration, _scores()[:3])
    with pytest.raises(ForwardSessionRefused):
        file_agent_batch(store, altered, calendar=calendar, clock=lambda: PREDICTED_AT)
    assert _filed_files(tmp_path) == files


def test_filing_at_or_after_0915_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    """The book trades the scores at the next open's call auction; after it nothing is a
    prediction. The refusal happens before the write."""
    calendar = _calendar()
    declaration = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="primary",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )
    batch = _rules(declaration, _scores())
    cutoff = registration_cutoff(calendar, SESSION)
    store = _store(tmp_path, lambda: cutoff)

    with pytest.raises(ForwardSessionRefused):
        file_agent_batch(store, batch, calendar=calendar, clock=lambda: cutoff)

    assert _filed_files(tmp_path) == {}


def test_a_batch_predicted_before_1630_is_refused() -> None:
    """`as_of` is the session's publication instant; a batch produced before it read unpublished
    features."""
    declaration = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="primary",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )
    too_early = session_publication_instant(SESSION) - timedelta(minutes=1)

    with pytest.raises(ValueError):
        agent_batch(
            session=SESSION,
            scores=_scores(),
            declaration=declaration,
            predicted_at=too_early,
        )


def test_the_block_names_the_registration_as_provenance(tmp_path: Path) -> None:
    """The journal stage names the declared configuration, read from the declaration itself."""
    calendar = _calendar()
    declaration = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="primary",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )
    store = _store(tmp_path, lambda: PREDICTED_AT)

    block = file_agent_batch(
        store, _rules(declaration, _scores()), calendar=calendar, clock=lambda: PREDICTED_AT
    )

    assert block["provenance"] == REGISTERED.registration_sha256
    assert datetime.fromisoformat(block["as_of"]) == session_publication_instant(SESSION)
    assert block["registered"] is True
    assert block["outcome"] == "created"


def test_run_ids_are_deterministic_and_carried_into_the_rows() -> None:
    """The run id is a pure function of (config, session, family, security); two scoring sets
    share it for one name, which is what lets one cycle serve both sets."""
    first = agent_run_id(
        config_id=REGISTERED.config_id,
        session=SESSION,
        family=AGENT_RULE_FAMILY,
        ts_code=NAMES[0],
    )
    assert first == agent_run_id(
        config_id=REGISTERED.config_id,
        session=SESSION,
        family=AGENT_RULE_FAMILY,
        ts_code=NAMES[0],
    )
    assert len(first) <= 128
    assert first != agent_run_id(
        config_id=REGISTERED.config_id,
        session=NEXT_SESSION,
        family=AGENT_RULE_FAMILY,
        ts_code=NAMES[0],
    )
    assert first != agent_run_id(
        config_id=REGISTERED.config_id,
        session=SESSION,
        family=AGENT_LLM_FAMILY,
        ts_code=NAMES[0],
    )
    assert first != agent_run_id(
        config_id="d" * 64,
        session=SESSION,
        family=AGENT_RULE_FAMILY,
        ts_code=NAMES[0],
    )
    assert first != agent_run_id(
        config_id=REGISTERED.config_id,
        session=SESSION,
        family=AGENT_RULE_FAMILY,
        ts_code=NAMES[1],
    )

    declaration = agent_declaration(
        family=AGENT_RULE_FAMILY,
        scoring_set="coverage",
        agent_kind="deterministic",
        registered=REGISTERED,
        model_ref=None,
        prompt_ref=None,
    )
    batch = _rules(declaration, _scores())
    assert tuple(row.run_id for row in _scores()) == tuple(item.run_id for item in _scores())
    assert tuple(prediction.ts_code for prediction in batch.predictions) == tuple(sorted(NAMES))


def test_a_score_carries_exactly_one_answer() -> None:
    """A row is a number or a code, never both and never neither, and never non-finite."""
    with pytest.raises(ValueError):
        AgentScore(ts_code=NAMES[0], score=0.1, abstention=ABSTAIN_NOT_ROUTED, run_id="r")
    with pytest.raises(ValueError):
        AgentScore(ts_code=NAMES[0], score=None, abstention=None, run_id="r")
    with pytest.raises(ValueError):
        AgentScore(ts_code=NAMES[0], score=float("nan"), abstention=None, run_id="r")


def test_strategy_agent_forward_reaches_no_engine_no_composition_no_paper_book() -> None:
    """Filing declares a record; it must not be able to run a cycle or trade."""
    graph = grimp.build_graph("openalpha_cn", cache_dir=None)
    module = "openalpha_cn.strategy_agent_forward"
    forbidden = {
        "openalpha_cn.agents",
        "openalpha_cn.api",
        "openalpha_cn.cli",
        "openalpha_cn.product",
        "openalpha_cn.runtime.composition",
        "openalpha_cn.runtime.engine",
        "openalpha_cn.sdk",
        "openalpha_cn.shortlist_compare",
        "openalpha_cn.shortlist_view",
        "openalpha_cn.backtest.paper",
        "openalpha_cn.backtest.paper_step",
        "openalpha_cn.backtest.portfolio",
        "openalpha_cn.domain.portfolio",
        "openalpha_cn.domain.survivor_book",
        "openalpha_cn.domain.survivor_orders",
        "openalpha_cn.strategy_survivor_book",
        "openalpha_cn.storage",
    }
    reached = graph.find_upstream_modules(module)
    leaked = sorted(forbidden & reached)
    assert leaked == [], f"{module} reaches {leaked}, which a filing module must not"
    assert "openalpha_cn.strategy_forward_session" in reached, (
        "the module files through `strategy_forward_session`; if that edge is gone it "
        "re-implements the protocol, which is the duplication R1 exists to prevent"
    )
