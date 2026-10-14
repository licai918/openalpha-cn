"""The survivor forward report's prediction layer (`V2-P7-018`, Part E task E2).

`strategy_survivor_forward` reads the pre-registered protocol out of a registration's settings,
finds the survivor records the prediction store witnesses, labels each one through the shared
label reader once its outcome window has closed, and puts the labelled records on the
pre-registered non-overlapping sample -- testing it once, and only when the pre-registered
number of periods has matured. Every panel here is generated at test time and written through the
real `panel_ingest` writers (`AGENTS.md` hard rule 6), from the repository's own generators:

- `PANEL` is `panel_fixtures.generate_panel`'s ten-session, eight-security panel (2026-01-05 ..
  2026-01-16, on the generator's whole-2026 calendar) with four shapes the label reader refuses
  by name -- a one-price limit-up session, a timed halt into the close, the shapeless panel's
  whole-day halt, and a termination on the newest session -- plus `strategy_fixtures`' index
  levels for the protocol's 000905.SH. Labels are read through `model_view.OutcomeLabels` at the
  panel's own `AS_OF`, after every session. The securities each shape lands on are the
  generator's own (`_halted_key`, `_timed_key`, `LOCKED_*`, `TERMINATED_*`), imported rather than
  restated.
- the survivor facts frame (`tests/unit/test_survivor_facts_panel.py`'s `TRIMMED` store) for the
  one test that holds the reconstructed survivors to `screen().survivors`: a real facts day, a
  real screen and the real `survivor_batch`.
- the arithmetic of one record (deciles, rank IC, the equal-weight and excess numbers) is held to
  hand-computed values through `HandLabels`, a label source that answers from a table on the real
  calendar; the prediction layer is driven with `RecordOutcome`s built directly.

The protocol values below are test values. They are not the recommendations of the Part E
design (those are decided at the `V2-P7-006` checkpoint) and nothing here may be read as them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import (
    AS_OF,
    EXCHANGE,
    LOCKED_SECURITY_INDEX,
    LOCKED_SESSION_INDEX,
    TERMINATED_SECURITY_INDEX,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from panel_fixtures import _halted_key as halted_key
from panel_fixtures import _timed_key as timed_key
from strategy_fixtures import INDEX_LEVELS
from strategy_fixtures import _index_batch as index_batch
from test_survivor_facts_panel import CONFIG, SIGNAL_INDEX, TRIMMED, evening, read_day, write_panel

from openalpha_cn import strategy_survivor_forward as forward_module
from openalpha_cn.backtest.outcome_statistics import EXACT_SIGN_FLIP_LIMIT, sign_flip_test
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.alpha_model import AlphaModelArtifact, Prediction, PredictionBatch
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.horizon import HorizonUnit, ResearchHorizon
from openalpha_cn.domain.index_prices import IndexBar
from openalpha_cn.domain.labels import (
    LabelRefusal,
    LabelWindow,
    OutcomeLabel,
    WindowReturn,
    build_label_window,
)
from openalpha_cn.domain.prediction_record import PredictionRecord, outcome_known_at_for
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, TradingCalendar
from openalpha_cn.model_view import LabelReach, OutcomeLabels
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_index_prices,
    session_publication_instant,
    write_index_prices,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import (
    RegisteredConfiguration,
    StrategyRegistrationError,
    registration_cutoff,
)
from openalpha_cn.strategy_survivor_check import SurvivorRecordVerdict
from openalpha_cn.strategy_survivor_forward import (
    DESCRIPTIVE_WORDING,
    FORWARD_METRICS,
    NO_VERDICT_WORDING,
    PREDICTION_LAYER_WORDING,
    SURVIVOR_FORWARD_PROTOCOL_KEY,
    SURVIVOR_FORWARD_PROTOCOL_SCHEMA,
    SURVIVOR_FORWARD_REPORT_KIND,
    PredictionLayer,
    RecordOutcome,
    SampledSessions,
    SurvivorForwardError,
    SurvivorForwardProtocol,
    WitnessedRecord,
    check_journals_against_the_store,
    paper_refusal,
    prediction_layer,
    read_forward_protocol,
    record_outcome,
    record_scope,
    report_through,
    sampled_benchmarks,
    sampled_sessions,
    survivor_forward_document,
    survivor_forward_lines,
    witnessed_survivor_records,
)
from openalpha_cn.strategy_survivor_market import PROTOCOL_BENCHMARK, SessionOrdinals
from openalpha_cn.strategy_survivor_registration import (
    survivor_batch,
    survivor_declaration,
    survivor_feature_ids,
)
from openalpha_cn.strategy_survivor_summary import SURVIVOR_WORDING

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

LABEL_SHAPES: Final[tuple[str, ...]] = (
    "daily.close_moves_between_sessions",
    "price_limits.one_price_limit_up",
    "suspension.timed_interruption",
    "universe.termination_on_the_newest_session",
)
PANEL: Final[GeneratedPanel] = generate_panel(shapes=LABEL_SHAPES)
SESSIONS: Final[tuple[date, ...]] = PANEL.sessions
"""2026-01-05 .. 2026-01-16, every weekday open."""
CALENDAR: Final[TradingCalendar] = PANEL.calendar()
"""The generated 2026 calendar: every weekday open but 1 and 2 January, through 31 December."""
SECURITIES: Final[tuple[str, ...]] = PANEL.securities
HALTED, HALTED_ON = halted_key(SESSIONS, SECURITIES)
"""`601318.SH`, halted all of 2026-01-09: no bar that session."""
TIMED, TIMED_ON = timed_key(SESSIONS, SECURITIES)
"""`000001.SZ`, halted 13:00-15:00 on 2026-01-07: still halted into the close."""
LOCKED: Final[str] = SECURITIES[LOCKED_SECURITY_INDEX]
LOCKED_ON: Final[date] = SESSIONS[LOCKED_SESSION_INDEX]
"""`600519.SH`, at its upper limit all of 2026-01-06."""
TERMINATED: Final[str] = SECURITIES[TERMINATED_SECURITY_INDEX]
"""`300750.SZ`, terminated on 2026-01-16, the newest session."""
UNPRICED: Final[str] = "000003.SZ"
"""A code the panel has never heard of: no adjustment history, so the reader answers `None`."""

BOOK: Final[SurvivorBookConfig] = SurvivorBookConfig(seed=7)
"""The default book: four composite factors and the worst two tenths dropped."""
REGISTERED: Final[RegisteredConfiguration] = RegisteredConfiguration(
    config_id="rcf_0f1e2d3c4b5a69788796a5b4",
    registration_sha256="ab" * 32,
    code_commit="abcdef1234567",
    seed=7,
)
ANOTHER_REGISTRATION: Final[RegisteredConfiguration] = dataclasses.replace(
    REGISTERED, config_id="rcf_99999999999999999999999a", registration_sha256="cd" * 32
)
EPOCH: Final[date] = SESSIONS[0]
ORDINALS: Final[SessionOrdinals] = SessionOrdinals(CALENDAR, EPOCH)
"""Ordinal 0 is Monday 2026-01-05; every open weekday of 2026 after it counts one."""

PROTOCOL_BLOCK: Final[Mapping[str, Any]] = {
    "schema_version": "survivor-forward-protocol/v1",
    "anchor_ordinal": 1,
    "primary_horizon": 20,
    "secondary_horizons": [5],
    "primary_metric": "rank_ic",
    "secondary_metrics": ["survivors_minus_ew", "ew_minus_worst_decile"],
    "window_periods": 3,
    "bootstrap_samples": 1000,
    "random_seed": 11,
    "q": 0.2,
}
"""Test values only (see the module docstring)."""

FORBIDDEN_WORDS: Final[tuple[str, ...]] = (
    "significant",
    "effective",
    "confirmed",
    "alpha",
    "outperform",
    "passed",
    "failed",
    "proven",
)


def protocol(**changes: object) -> SurvivorForwardProtocol:
    return read_forward_protocol({SURVIVOR_FORWARD_PROTOCOL_KEY: {**PROTOCOL_BLOCK, **changes}})


# --- records ---------------------------------------------------------------------------------


def batch_on(
    day: date,
    scores: Mapping[str, float],
    *,
    registered: RegisteredConfiguration = REGISTERED,
    book: SurvivorBookConfig = BOOK,
    predicted_at: datetime | None = None,
) -> PredictionBatch:
    """A survivor batch of `day`, shaped as `survivor_batch` files one, with these scores."""
    published = session_publication_instant(day)
    features = survivor_feature_ids(book)
    return PredictionBatch(
        as_of=published,
        predicted_at=published + timedelta(hours=2) if predicted_at is None else predicted_at,
        artifact=AlphaModelArtifact(
            declaration=survivor_declaration(book, registered),
            feature_ids=features,
            training_cutoff=published,
            training_example_count=len(features) * len(scores),
            parameters=tuple((feature, 1.0 / len(features)) for feature in features),
        ),
        predictions=tuple(
            Prediction(ts_code=code, score=score) for code, score in sorted(scores.items())
        ),
    )


def record_of(batch: PredictionBatch) -> PredictionRecord:
    return PredictionRecord(
        batch=batch,
        outcome_known_at=outcome_known_at_for(batch, calendar=CALENDAR, zone=SHANGHAI),
        recorded_at=batch.predicted_at + timedelta(minutes=1),
    )


def witnessed_on(day: date, scores: Mapping[str, float]) -> WitnessedRecord:
    record = record_of(batch_on(day, scores))
    return WitnessedRecord(
        session=day, ordinal=ORDINALS.index_of(day), record_id=record.record_id, record=record
    )


def filed(directory: Path, batch: PredictionBatch, *, at: datetime) -> str:
    """`batch` put into the prediction store at `directory`, its custody clock reading `at`."""
    return (
        FilePredictionStore(directory, clock=lambda: at)
        .put(batch=batch, calendar=CALENDAR, zone=SHANGHAI)
        .record.record_id
    )


def witness(directory: Path, *, through: date = SESSIONS[-1]) -> tuple[WitnessedRecord, ...]:
    return witnessed_survivor_records(
        FilePredictionStore(directory, clock=lambda: AS_OF),
        book=BOOK,
        registered=REGISTERED,
        calendar=CALENDAR,
        ordinals=ORDINALS,
        through=through,
    )


ONE_SCORE: Final[Mapping[str, float]] = {"000002.SZ": 0.5, "600000.SH": 0.25}


# --- label sources ---------------------------------------------------------------------------


class HandLabels:
    """A label source answering from a table on the real calendar, counting every question.

    An answer is a return, a tuple of refusal codes, or `None` (no adjustment history), so the
    arithmetic of one record can be held to hand-computed numbers."""

    def __init__(self, answers: Mapping[str, float | tuple[str, ...] | None]) -> None:
        self.calendar = CALENDAR
        self.answers = dict(answers)
        self.asked: list[str] = []

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow:
        return build_label_window(
            as_of=instant, zone=SHANGHAI, horizon=horizon, calendar=self.calendar
        )

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
        self.asked.append(ts_code)
        answer = self.answers[ts_code]
        if answer is None:
            return None
        if isinstance(answer, tuple):
            return OutcomeLabel(
                ts_code=ts_code,
                window=window,
                refusals=tuple(
                    LabelRefusal(code=code, day=window.entry_day, detail="by hand")
                    for code in answer
                ),
                entry_touch=None,
                exit_touch=None,
                window_return=None,
            )
        return OutcomeLabel(
            ts_code=ts_code,
            window=window,
            refusals=(),
            entry_touch=None,
            exit_touch=None,
            window_return=WindowReturn(
                ts_code=ts_code,
                entry_day=window.entry_day,
                exit_day=window.exit_day,
                published=answer,
                adjusted=answer,
                unadjusted=answer,
                entry_adjusted_close=1.0,
                exit_adjusted_close=1.0 + answer,
                per_session=(),
            ),
        )


class Spy:
    """The shared reader, with every label question it is asked written down."""

    def __init__(self, reader: OutcomeLabels) -> None:
        self.reader = reader
        self.calendar = reader.calendar
        self.asked: list[tuple[str, date]] = []

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow:
        return self.reader.window(instant, horizon=horizon)

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
        self.asked.append((ts_code, window.exit_day))
        return self.reader.label(ts_code, window)


HAND_DAY: Final[date] = SESSIONS[1]
"""Tuesday 2026-01-06: at `horizon=2` its window is 2026-01-07 .. 2026-01-09, closed by `AS_OF`."""


def hand_outcome(
    scores: Mapping[str, float],
    answers: Mapping[str, float | tuple[str, ...] | None],
    *,
    book: SurvivorBookConfig = BOOK,
) -> RecordOutcome:
    return record_outcome(
        HandLabels(answers),
        witnessed_on(HAND_DAY, scores),
        horizon=2,
        book=book,
        csi500=(),
        as_of=AS_OF,
    )


TWENTY: Final[tuple[str, ...]] = tuple(f"6000{n:02d}.SH" for n in range(20))
_REST: Final[tuple[str, ...]] = tuple(
    code for code in TWENTY if code not in ("600005.SH", "600001.SH", "600009.SH")
)
TWENTY_SCORES: Final[Mapping[str, float]] = {
    "600005.SH": -3.0,
    "600001.SH": -1.0,
    "600009.SH": -1.0,
    **{code: (16 - index) * 0.125 for index, code in enumerate(_REST)},
}
"""Twenty names: one worst, a tie straddling the first decile boundary, and seventeen whose score
falls as their code rises, so a cut by code alone, by score alone or by input order differs."""
WORST_FIRST: Final[tuple[str, ...]] = (
    "600005.SH",
    "600001.SH",
    "600009.SH",
    "600019.SH",
    "600018.SH",
    "600017.SH",
    "600016.SH",
    "600015.SH",
    "600014.SH",
    "600013.SH",
    "600012.SH",
    "600011.SH",
    "600010.SH",
    "600008.SH",
    "600007.SH",
    "600006.SH",
    "600004.SH",
    "600003.SH",
    "600002.SH",
    "600000.SH",
)
"""`TWENTY_SCORES` by `(score, ts_code)`, written out by hand."""


def by_position(value: object) -> dict[str, object]:
    """One answer per name of `TWENTY`, `value(position)` with `position` in `WORST_FIRST`."""
    assert callable(value)
    return {code: value(position) for position, code in enumerate(WORST_FIRST)}


# --- the generated panel ---------------------------------------------------------------------


LABEL_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    ADJ_FACTOR_DATASET,
    SUSPENSION_DATASET,
    DAILY_DATASET,
    DAILY_BASIC_DATASET,
    PRICE_LIMIT_DATASET,
)
"""What the label reader reads, and `daily_basic`, which `write_daily_panel` writes with `daily`."""


@pytest.fixture(scope="module")
def panel_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    store = PanelStore(tmp_path_factory.mktemp("forward") / "panel")
    write_generated_panel(store, PANEL, datasets=LABEL_DATASETS)
    write_index_prices(store, [index_batch(SESSIONS)])
    return store


def reader_of(store: PanelStore) -> OutcomeLabels:
    return OutcomeLabels(store, LabelReach(as_of=AS_OF, years=(2026,), exchange=EXCHANGE))


def csi500_of(store: PanelStore) -> tuple[IndexBar, ...]:
    levels = load_index_prices(store, CALENDAR, years=(2026,), as_of=AS_OF, max_staleness=None)
    return levels[PROTOCOL_BENCHMARK]


PANEL_SCORES: Final[Mapping[str, float]] = {
    **{code: float(index) / 8 for index, code in enumerate(SECURITIES)},
    UNPRICED: -1.0,
}


# --- the protocol ----------------------------------------------------------------------------


def test_the_protocol_is_read_from_the_registrations_settings_block() -> None:
    read = read_forward_protocol(
        {"random_seed": 0, SURVIVOR_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK)}
    )

    assert SURVIVOR_FORWARD_PROTOCOL_KEY == "survivor_forward_protocol"
    assert read.schema_version == SURVIVOR_FORWARD_PROTOCOL_SCHEMA == "survivor-forward-protocol/v1"
    assert (read.anchor_ordinal, read.primary_horizon, read.secondary_horizons) == (1, 20, (5,))
    assert (read.primary_metric, read.secondary_metrics) == (
        "rank_ic",
        ("survivors_minus_ew", "ew_minus_worst_decile"),
    )
    assert (read.window_periods, read.bootstrap_samples, read.random_seed, read.q) == (
        3,
        1000,
        11,
        0.2,
    )


def test_the_protocol_has_no_default_and_is_closed_and_frozen() -> None:
    """The values are frozen at `V2-P7-006`; a default here would be a decision taken by code."""
    fields = SurvivorForwardProtocol.model_fields

    assert set(fields) == set(PROTOCOL_BLOCK)
    assert all(field.is_required() for field in fields.values())
    assert SurvivorForwardProtocol.model_config.get("extra") == "forbid"
    assert SurvivorForwardProtocol.model_config.get("frozen") is True


@pytest.mark.parametrize("key", sorted(PROTOCOL_BLOCK))
def test_a_block_missing_any_key_is_refused_by_name(key: str) -> None:
    block = {name: value for name, value in PROTOCOL_BLOCK.items() if name != key}

    with pytest.raises(SurvivorForwardError, match=key):
        read_forward_protocol({SURVIVOR_FORWARD_PROTOCOL_KEY: block})


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        ({}, "survivor_forward_protocol"),
        ({"random_seed": 3, "excess_benchmark": "000905.SH"}, "survivor_forward_protocol"),
        ({SURVIVOR_FORWARD_PROTOCOL_KEY: [1, 2]}, "survivor_forward_protocol"),
        ({SURVIVOR_FORWARD_PROTOCOL_KEY: None}, "survivor_forward_protocol"),
    ],
)
def test_settings_without_a_protocol_block_are_refused_by_name(
    settings: Mapping[str, object], named: str
) -> None:
    with pytest.raises(SurvivorForwardError, match=named):
        read_forward_protocol(settings)


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"schema_version": "survivor-forward-protocol/v2"}, "schema_version"),
        ({"family_size": 1}, "family_size"),
        ({"window_periods": 0}, "window_periods"),
        ({"window_periods": -36}, "window_periods"),
        ({"window_periods": True}, "window_periods"),
        ({"window_periods": 3.0}, "window_periods"),
        ({"primary_horizon": 0}, "primary_horizon"),
        ({"primary_horizon": -20}, "primary_horizon"),
        ({"secondary_horizons": [0]}, "secondary_horizons"),
        ({"secondary_horizons": [-5]}, "secondary_horizons"),
        ({"secondary_horizons": [20]}, "secondary_horizons"),
        ({"secondary_horizons": [5, 5]}, "secondary_horizons"),
        ({"anchor_ordinal": 0}, "anchor_ordinal"),
        ({"primary_metric": "sharpe"}, "primary_metric"),
        ({"secondary_metrics": ["rank_ic"]}, "secondary_metrics"),
        ({"secondary_metrics": ["top_minus_bottom", "top_minus_bottom"]}, "secondary_metrics"),
        ({"q": 0}, "q"),
        ({"q": 1.0}, "q"),
        ({"q": "0.10"}, "q"),
        ({"bootstrap_samples": 10}, "bootstrap_samples"),
        ({"random_seed": -1}, "random_seed"),
    ],
)
def test_an_invalid_protocol_block_is_refused_by_name(
    changes: Mapping[str, object], named: str
) -> None:
    with pytest.raises(SurvivorForwardError, match=named) as refused:
        read_forward_protocol({SURVIVOR_FORWARD_PROTOCOL_KEY: {**PROTOCOL_BLOCK, **changes}})

    assert isinstance(refused.value, StrategyRegistrationError)


# --- the witnessed records -------------------------------------------------------------------


def test_one_bound_on_time_record_a_session_is_witnessed_in_session_order(tmp_path: Path) -> None:
    ids = {
        index: filed(tmp_path, batch_on(SESSIONS[index], ONE_SCORE), at=evening_of(index))
        for index in (3, 1, 2)
    }

    witnessed = witness(tmp_path)

    assert [(w.session, w.ordinal, w.record_id) for w in witnessed] == [
        (SESSIONS[index], index, ids[index]) for index in (1, 2, 3)
    ]
    assert all(w.record.record_id == w.record_id for w in witnessed)


def evening_of(index: int) -> datetime:
    """18:30 Asia/Shanghai on `SESSIONS[index]`."""
    return session_publication_instant(SESSIONS[index]) + timedelta(hours=2)


def test_a_record_another_registration_filed_is_ignored_and_refuses_nothing(
    tmp_path: Path,
) -> None:
    filed(
        tmp_path,
        batch_on(SESSIONS[2], ONE_SCORE, registered=ANOTHER_REGISTRATION),
        at=evening_of(2),
    )
    filed(
        tmp_path,
        batch_on(SESSIONS[3], {"000002.SZ": 0.75}, registered=ANOTHER_REGISTRATION),
        at=evening_of(3),
    )
    bound = filed(tmp_path, batch_on(SESSIONS[3], ONE_SCORE), at=evening_of(3))

    witnessed = witness(tmp_path)

    assert [(w.session, w.record_id) for w in witnessed] == [(SESSIONS[3], bound)]


def test_two_bound_records_for_one_session_are_refused(tmp_path: Path) -> None:
    filed(tmp_path, batch_on(SESSIONS[2], ONE_SCORE), at=evening_of(2))
    filed(tmp_path, batch_on(SESSIONS[2], {"000002.SZ": 0.75}), at=evening_of(2))

    with pytest.raises(SurvivorForwardError, match="2026-01-07") as refused:
        witness(tmp_path)

    assert "2 records" in str(refused.value)


def test_a_record_registered_at_or_after_its_sessions_cutoff_is_not_a_registration(
    tmp_path: Path,
) -> None:
    cutoff = registration_cutoff(CALENDAR, SESSIONS[2])
    assert cutoff == datetime(2026, 1, 8, 9, 15, tzinfo=SHANGHAI)
    late = filed(tmp_path, batch_on(SESSIONS[2], ONE_SCORE), at=cutoff)
    on_time = filed(
        tmp_path,
        batch_on(SESSIONS[3], ONE_SCORE),
        at=registration_cutoff(CALENDAR, SESSIONS[3]) - timedelta(microseconds=1),
    )
    # A late record beside an on-time one of the same session is not a second registration.
    filed(
        tmp_path,
        batch_on(SESSIONS[3], {"000002.SZ": 0.75}, predicted_at=evening_of(3)),
        at=registration_cutoff(CALENDAR, SESSIONS[3]),
    )

    witnessed = witness(tmp_path)

    assert [(w.session, w.record_id) for w in witnessed] == [(SESSIONS[3], on_time)]
    assert late not in {w.record_id for w in witnessed}


def test_a_record_of_the_epoch_or_before_it_is_not_witnessed(tmp_path: Path) -> None:
    ordinals = SessionOrdinals(CALENDAR, SESSIONS[1])
    for index in (0, 1, 2):
        filed(tmp_path, batch_on(SESSIONS[index], ONE_SCORE), at=evening_of(index))

    witnessed = witnessed_survivor_records(
        FilePredictionStore(tmp_path, clock=lambda: AS_OF),
        book=BOOK,
        registered=REGISTERED,
        calendar=CALENDAR,
        ordinals=ordinals,
        through=SESSIONS[-1],
    )

    assert [(w.session, w.ordinal) for w in witnessed] == [(SESSIONS[2], 1)]


def test_a_record_after_through_is_not_witnessed(tmp_path: Path) -> None:
    for index in (1, 2, 3):
        filed(tmp_path, batch_on(SESSIONS[index], ONE_SCORE), at=evening_of(index))

    assert [w.session for w in witness(tmp_path, through=SESSIONS[2])] == [
        SESSIONS[1],
        SESSIONS[2],
    ]


def _filed_stage(item: WitnessedRecord) -> dict[str, Any]:
    """A survivor journal whose evening filed `item`'s record (`daily_selection`'s stage)."""
    return {"prediction": {"registered": True, "record_id": item.record_id}}


def test_every_journal_that_names_a_record_names_the_one_the_store_witnesses() -> None:
    """Task E4: the store is the witness (design 2(a)). A journal that says its evening filed a
    record must name the record the store witnesses for the session; one that says it filed none
    must have none. A journal with no prediction stage claims nothing, a witnessed session with
    no journal is the store's word alone, and a journal after `through` is not read."""
    first = witnessed_on(SESSIONS[1], ONE_SCORE)
    second = witnessed_on(SESSIONS[2], {**ONE_SCORE, "600000.SH": 0.75})
    agreeing = {
        SESSIONS[1]: _filed_stage(first),
        SESSIONS[2]: _filed_stage(second),
        SESSIONS[3]: {"as_of": "pinned, nothing filed yet"},
    }

    check_journals_against_the_store((first, second), agreeing, through=SESSIONS[-1])
    check_journals_against_the_store((first, second), {}, through=SESSIONS[-1])
    check_journals_against_the_store((first,), agreeing, through=SESSIONS[1])
    with pytest.raises(SurvivorForwardError, match=r"2026-01-07 .* the store is the witness"):
        check_journals_against_the_store((first,), agreeing, through=SESSIONS[-1])
    with pytest.raises(SurvivorForwardError, match=r"2026-01-06 .* the store is the witness"):
        check_journals_against_the_store(
            (first,),
            {SESSIONS[1]: {"prediction": {"registered": False, "settle_only": True}}},
            through=SESSIONS[-1],
        )
    with pytest.raises(SurvivorForwardError, match="2026-01-06"):
        check_journals_against_the_store(
            (first,), {SESSIONS[1]: _filed_stage(second)}, through=SESSIONS[-1]
        )


# --- the sampled sessions --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("anchor", "horizon", "through", "expected"),
    [
        (1, 3, SESSIONS[9], (SESSIONS[1], SESSIONS[4], SESSIONS[7])),
        (2, 4, SESSIONS[9], (SESSIONS[2], SESSIONS[6])),
        (4, 1, SESSIONS[6], (SESSIONS[4], SESSIONS[5], SESSIONS[6])),
        (3, 2, SESSIONS[2], ()),
    ],
)
def test_the_sample_is_the_anchor_and_every_horizon_th_calendar_session_after_it(
    anchor: int, horizon: int, through: date, expected: tuple[date, ...]
) -> None:
    sampled = sampled_sessions(ORDINALS, anchor_ordinal=anchor, through=through, horizon=horizon)

    assert sampled == SampledSessions(
        anchor_ordinal=anchor, horizon=horizon, through=through, sessions=expected
    )


def test_the_sample_counts_calendar_sessions_not_days() -> None:
    sampled = sampled_sessions(ORDINALS, anchor_ordinal=1, through=date(2026, 3, 31), horizon=20)

    # Twenty sessions on from Tuesday 2026-01-06 is Tuesday 2026-02-03: four weekends later.
    assert sampled.sessions == (
        date(2026, 1, 6),
        date(2026, 2, 3),
        date(2026, 3, 3),
        date(2026, 3, 31),
    )
    assert [ORDINALS.index_of(day) for day in sampled.sessions] == [1, 21, 41, 61]


@pytest.mark.parametrize(
    ("arguments", "named"),
    [
        ({"anchor_ordinal": 0}, "anchor"),
        ({"horizon": 0}, "horizon"),
        ({"through": date(2026, 1, 10)}, "2026-01-10"),
        ({"through": date(2025, 12, 31)}, "2025-12-31"),
    ],
)
def test_a_sample_that_cannot_be_drawn_is_refused_by_name(
    arguments: Mapping[str, object], named: str
) -> None:
    given: dict[str, Any] = {"anchor_ordinal": 1, "through": SESSIONS[9], "horizon": 3, **arguments}

    with pytest.raises(SurvivorForwardError, match=named):
        sampled_sessions(ORDINALS, **given)


# --- one record's scope: deciles and survivors -----------------------------------------------


def test_deciles_are_taken_over_the_whole_scope_worst_first_by_score_then_code() -> None:
    scope = record_scope(batch_on(HAND_DAY, TWENTY_SCORES), book=BOOK)

    assert tuple(name.ts_code for name in scope.names) == WORST_FIRST
    assert tuple(name.position for name in scope.names) == tuple(range(20))
    assert tuple(name.decile for name in scope.names) == tuple(p // 2 for p in range(20))
    # The tie at -1.0 straddles the first boundary: the lower code is in decile 0.
    assert {name.ts_code: name.decile for name in scope.names}["600001.SH"] == 0
    assert {name.ts_code: name.decile for name in scope.names}["600009.SH"] == 1
    assert scope.survivors == tuple(
        sorted(WORST_FIRST[4:], key=lambda code: (-TWENTY_SCORES[code], code))
    )
    assert scope.survivors[:3] == ("600000.SH", "600002.SH", "600003.SH")


def test_a_decile_is_floor_of_ten_positions_over_n_and_the_cut_is_n_tenths_rounded_down() -> None:
    eight = {code: float(index) for index, code in enumerate(SECURITIES)}

    scope = record_scope(batch_on(HAND_DAY, eight), book=BOOK)

    assert tuple(name.decile for name in scope.names) == (0, 1, 2, 3, 5, 6, 7, 8)
    # 8 * 2 // 10 = 1: one name is dropped although two sit in deciles 0 and 1.
    assert len(scope.survivors) == 7
    assert scope.survivors[-1] == SECURITIES[1]


@pytest.fixture(scope="module")
def frame_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("frame"), TRIMMED)


@pytest.mark.parametrize("loser_decile_count", [0, 2, 5])
def test_the_survivors_rebuilt_from_a_record_are_the_screens_survivors(
    frame_store: PanelStore, loser_decile_count: int
) -> None:
    book = SurvivorBookConfig.model_validate(
        {
            **CONFIG.model_dump(exclude_computed_fields=True),
            "loser_decile_count": loser_decile_count,
        }
    )
    facts = read_day(frame_store, config=book)
    screened = screen(facts.facts, book)
    batch = survivor_batch(
        facts=facts,
        screen=screened,
        book=book,
        registered=REGISTERED,
        predicted_at=evening(SIGNAL_INDEX) + timedelta(minutes=4),
    )

    scope = record_scope(batch, book=book)

    assert scope.survivors == tuple(name.ts_code for name in screened.survivors)
    assert len(scope.names) == len(batch.predictions)
    assert len(scope.survivors) == len(scope.names) - len(scope.names) * loser_decile_count // 10


def test_a_record_with_an_abstention_is_not_a_survivor_record() -> None:
    batch = batch_on(HAND_DAY, ONE_SCORE)
    abstaining = batch.model_copy(
        update={
            "predictions": (
                *batch.predictions,
                Prediction(ts_code="600519.SH", abstention="incomplete"),
            )
        }
    )

    with pytest.raises(SurvivorForwardError, match=re.escape("600519.SH")):
        record_scope(abstaining, book=BOOK)


# --- one record's outcome: the arithmetic ------------------------------------------------------


def test_the_rank_ic_is_a_tie_aware_spearman_computed_by_hand() -> None:
    """Scores (1, 2, 2, 4) rank (1, 2.5, 2.5, 4); labels (1/8, 3/8, 1/4, 1/4) rank
    (1, 4, 2.5, 2.5). Centred: (-1.5, 0, 0, 1.5) and (-1.5, 1.5, 0, 0); the cross product sums
    to 2.25 and each square to 4.5, so the correlation is 2.25 / 4.5 = 0.5 -- where the raw
    values' Pearson is 0.324 and ranks that broke the ties by order would give 0.4."""
    codes = ("000002.SZ", "600000.SH", "600519.SH", "601318.SH")
    outcome = hand_outcome(
        dict(zip(codes, (1.0, 2.0, 2.0, 4.0), strict=True)),
        dict(zip(codes, (0.125, 0.375, 0.25, 0.25), strict=True)),
    )

    assert outcome.rank_ic == pytest.approx(0.5, abs=1e-15)
    assert ("rank_ic", "fewer_than_two_labelled") not in outcome.absent


def test_the_equal_weight_and_the_three_excesses_are_means_over_the_labelled_scope() -> None:
    """Label `p / 32` at worst-first position `p`: EW 9.5/32; the survivors (positions 4..19)
    11.5/32, so 2/32 over EW; decile 0 (0, 1) 0.5/32, so EW leads it by 9/32; decile 9 (18, 19)
    18.5/32, so 18/32 over decile 0."""
    outcome = hand_outcome(TWENTY_SCORES, by_position(lambda p: p / 32))

    assert outcome.mature
    assert (outcome.scope, outcome.survivors, outcome.labelled, outcome.unlabelled) == (
        20,
        16,
        20,
        0,
    )
    assert outcome.investable_ew == 9.5 / 32
    assert outcome.survivors_minus_ew == 2 / 32
    assert outcome.ew_minus_worst_decile == 9 / 32
    assert outcome.top_minus_bottom == 18 / 32
    assert outcome.rank_ic == pytest.approx(
        statistics.correlation(
            (1.0, 2.5, 2.5, *(float(rank) for rank in range(4, 21))),
            tuple(float(rank) for rank in range(1, 21)),
        )
    )
    assert outcome.scope_by_decile == (2,) * 10
    assert outcome.absent == (("csi500", "benchmark_close_missing"),)


@pytest.mark.parametrize("sign", [1, -1], ids=["scores-order-the-labels", "reversed"])
def test_every_metric_is_above_zero_when_the_scores_order_the_labels_and_below_when_reversed(
    sign: int,
) -> None:
    """Every metric the protocol may test shares one direction: greater than zero supports the
    screen. Labels rising with the score -- the worst-scored names do worst -- put each one above
    zero: the rank IC, the survivors over the equal weight, the equal weight over the worst
    decile (EW 9.5/32 less decile 0's 0.5/32, +9/32) and the top decile over the worst. Labels
    falling with the score put each one below."""
    outcome = hand_outcome(TWENTY_SCORES, by_position(lambda p: sign * p / 32))

    values = {metric: outcome.value(metric) for metric in FORWARD_METRICS}

    assert set(values) == {
        "rank_ic",
        "survivors_minus_ew",
        "ew_minus_worst_decile",
        "top_minus_bottom",
    }
    assert values["ew_minus_worst_decile"] == sign * 9 / 32
    assert all(value is not None and sign * value > 0 for value in values.values()), values


def test_unlabelled_names_are_counted_per_decile_and_per_code_and_left_out_of_every_number() -> (
    None
):
    answers = by_position(lambda p: p / 32)
    answers["600005.SH"] = ("halted_session", "missing_bar", "halted_session")  # position 0
    answers["600019.SH"] = ("locked_at_limit",)  # position 3, decile 1
    answers["600000.SH"] = None  # position 19, decile 9

    outcome = hand_outcome(TWENTY_SCORES, answers)

    assert (outcome.labelled, outcome.unlabelled) == (17, 3)
    assert outcome.unlabelled_by_code == (
        ("halted_session", 1),
        ("locked_at_limit", 1),
        ("missing_bar", 1),
        ("no_adjustment_history", 1),
    )
    assert outcome.unlabelled_by_decile == (1, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    labelled = [p / 32 for p in range(20) if p not in (0, 3, 19)]
    assert outcome.investable_ew == pytest.approx(statistics.fmean(labelled))
    assert outcome.survivors_minus_ew == pytest.approx(
        statistics.fmean(p / 32 for p in range(4, 19)) - statistics.fmean(labelled)
    )
    assert outcome.ew_minus_worst_decile == pytest.approx(statistics.fmean(labelled) - 1 / 32)
    assert outcome.top_minus_bottom == pytest.approx(18 / 32 - 1 / 32)


@pytest.mark.parametrize(
    ("answers", "code"),
    [
        ({"000002.SZ": 0.1, "600000.SH": ("halted_session",)}, "fewer_than_two_labelled"),
        ({"000002.SZ": 0.1, "600000.SH": 0.1}, "labels_tied"),
    ],
)
def test_a_rank_ic_with_a_degenerate_side_is_absent_with_a_code(
    answers: Mapping[str, float | tuple[str, ...]], code: str
) -> None:
    outcome = hand_outcome({"000002.SZ": 1.0, "600000.SH": 2.0}, answers)

    assert outcome.rank_ic is None
    assert ("rank_ic", code) in outcome.absent
    assert outcome.investable_ew is not None


def test_a_rank_ic_over_tied_scores_is_absent_with_a_code() -> None:
    outcome = hand_outcome(
        {"000002.SZ": 1.0, "600000.SH": 1.0}, {"000002.SZ": 0.1, "600000.SH": 0.2}
    )

    assert outcome.rank_ic is None
    assert ("rank_ic", "scores_tied") in outcome.absent


def test_an_empty_top_decile_leaves_top_minus_bottom_absent_with_a_code() -> None:
    codes = ("000002.SZ", "600000.SH", "600519.SH", "601318.SH")
    outcome = hand_outcome(
        dict(zip(codes, (1.0, 2.0, 3.0, 4.0), strict=True)),
        dict(zip(codes, (0.1, 0.2, 0.3, 0.4), strict=True)),
    )

    assert outcome.scope_by_decile == (1, 0, 1, 0, 0, 1, 0, 1, 0, 0)
    assert outcome.top_minus_bottom is None
    assert ("top_minus_bottom", "no_labelled_name_in_the_top_decile") in outcome.absent


# --- one record's outcome: the shared reader on the generated panel --------------------------


@pytest.mark.parametrize(
    ("index", "horizon", "unlabelled"),
    [
        (
            1,
            3,
            {
                "halted_into_the_close": [TIMED],
                "halted_session": [HALTED],
                "missing_bar": [HALTED],
                "no_adjustment_history": [UNPRICED],
            },
        ),
        (
            0,
            2,
            {"locked_at_limit": [LOCKED], "no_adjustment_history": [UNPRICED]},
        ),
        (
            3,
            5,
            {
                "delisted_in_window": [TERMINATED],
                "halted_session": [HALTED],
                "missing_bar": [HALTED],
                "no_adjustment_history": [UNPRICED],
            },
        ),
    ],
)
def test_labels_are_the_shared_readers_from_the_close_after_the_session_for_h_sessions(
    panel_store: PanelStore,
    index: int,
    horizon: int,
    unlabelled: Mapping[str, list[str]],
) -> None:
    assert (SESSIONS[1], SESSIONS[2], SESSIONS[4]) == (LOCKED_ON, TIMED_ON, HALTED_ON)
    day = SESSIONS[index]
    outcome = record_outcome(
        reader_of(panel_store),
        witnessed_on(day, PANEL_SCORES),
        horizon=horizon,
        book=BOOK,
        csi500=csi500_of(panel_store),
        as_of=AS_OF,
    )

    entry = SESSIONS[index + 1]
    exit_day = SESSIONS[index + 1 + horizon]
    assert (outcome.entry_day, outcome.exit_day, outcome.mature) == (entry, exit_day, True)
    fresh = reader_of(panel_store)
    window = fresh.window(
        session_publication_instant(day),
        horizon=ResearchHorizon(count=horizon, unit=HorizonUnit.trading_days),
    )
    refused = {code for codes in unlabelled.values() for code in codes}
    returns = []
    for code in sorted(PANEL_SCORES):
        label = fresh.label(code, window)
        if code in refused:
            assert label is None or label.refusals
        else:
            assert label is not None
            returns.append(label.realized_return)
    assert outcome.unlabelled_by_code == tuple(
        (code, len(names)) for code, names in sorted(unlabelled.items())
    )
    assert (outcome.scope, outcome.labelled, outcome.unlabelled) == (
        len(PANEL_SCORES),
        len(returns),
        len(refused),
    )
    assert outcome.investable_ew == pytest.approx(statistics.fmean(returns))
    levels = INDEX_LEVELS[PROTOCOL_BENCHMARK]
    assert outcome.csi500 == pytest.approx(levels[index + 1 + horizon] / levels[index + 1] - 1)


def test_the_benchmark_must_be_the_protocols_index(panel_store: PanelStore) -> None:
    levels = load_index_prices(
        panel_store, CALENDAR, years=(2026,), as_of=AS_OF, max_staleness=None
    )

    with pytest.raises(SurvivorForwardError, match=re.escape("000300.SH")):
        record_outcome(
            reader_of(panel_store),
            witnessed_on(SESSIONS[1], PANEL_SCORES),
            horizon=3,
            book=BOOK,
            csi500=levels["000300.SH"],
            as_of=AS_OF,
        )


def test_h5_reads_the_same_record_with_a_five_session_window(panel_store: PanelStore) -> None:
    witnessed = witnessed_on(SESSIONS[1], PANEL_SCORES)
    spy = Spy(reader_of(panel_store))

    five = record_outcome(
        spy, witnessed, horizon=5, book=BOOK, csi500=csi500_of(panel_store), as_of=AS_OF
    )
    asked_at_five = list(spy.asked)
    twenty = record_outcome(
        spy, witnessed, horizon=20, book=BOOK, csi500=csi500_of(panel_store), as_of=AS_OF
    )

    assert (
        (five.record_id, five.session, five.scope)
        == (
            twenty.record_id,
            twenty.session,
            twenty.scope,
        )
        == (witnessed.record_id, SESSIONS[1], len(PANEL_SCORES))
    )
    assert (five.horizon, five.entry_day, five.exit_day) == (5, SESSIONS[2], SESSIONS[7])
    assert five.mature and not twenty.mature
    assert sorted({code for code, _ in asked_at_five}) == sorted(PANEL_SCORES)
    assert {exit_day for _, exit_day in asked_at_five} == {SESSIONS[7]}
    # The record's own `outcome_known_at` is the h=20 window's last close.
    assert twenty.exit_day == witnessed.record.outcome_known_at.date()
    assert spy.asked == asked_at_five


def test_an_immature_record_is_counted_and_never_labelled(panel_store: PanelStore) -> None:
    """Mature exactly at the exit day's 16:30 publication, and not a microsecond before."""
    witnessed = witnessed_on(SESSIONS[1], PANEL_SCORES)
    published = session_publication_instant(SESSIONS[5])
    before, at = Spy(reader_of(panel_store)), Spy(reader_of(panel_store))

    immature = record_outcome(
        before,
        witnessed,
        horizon=3,
        book=BOOK,
        csi500=csi500_of(panel_store),
        as_of=published - timedelta(microseconds=1),
    )
    mature = record_outcome(
        at, witnessed, horizon=3, book=BOOK, csi500=csi500_of(panel_store), as_of=published
    )

    assert before.asked == []
    assert not immature.mature
    assert (immature.labelled, immature.unlabelled, immature.unlabelled_by_code) == (0, 0, ())
    assert (immature.rank_ic, immature.investable_ew, immature.csi500) == (None, None, None)
    assert immature.scope == len(PANEL_SCORES)
    assert immature.label_as_of == published - timedelta(microseconds=1)
    assert mature.mature
    assert len(at.asked) == len(PANEL_SCORES)


def test_no_label_whose_exit_is_after_the_reports_as_of_is_ever_read(
    panel_store: PanelStore,
) -> None:
    """2026-01-13 at h=5 enters on 2026-01-14 and exits on 2026-01-21, after every stored
    session: reading it would be a look-ahead, which the reader refuses
    (`ModelPanelUnreadableError`) -- so labelling an immature record fails here twice over."""
    spy = Spy(reader_of(panel_store))

    outcome = record_outcome(
        spy,
        witnessed_on(SESSIONS[6], PANEL_SCORES),
        horizon=5,
        book=BOOK,
        csi500=csi500_of(panel_store),
        as_of=AS_OF,
    )

    assert (outcome.entry_day, outcome.exit_day) == (date(2026, 1, 14), date(2026, 1, 21))
    assert not outcome.mature
    assert spy.asked == []


def test_a_window_past_a_calendar_that_reaches_the_report_is_immature_not_refused() -> None:
    """A December record whose window runs into a year the stored calendar does not hold yet."""
    labels = HandLabels(dict.fromkeys(ONE_SCORE, 0.1))
    day = CALENDAR.trading_days[-3]
    batch = batch_on(day, ONE_SCORE)
    witnessed = WitnessedRecord(
        session=day,
        ordinal=ORDINALS.index_of(day),
        record_id="prd_december",
        record=PredictionRecord(
            batch=batch,
            outcome_known_at=datetime(2027, 1, 28, 15, 0, tzinfo=SHANGHAI),
            recorded_at=batch.predicted_at + timedelta(minutes=1),
        ),
    )

    outcome = record_outcome(
        labels,
        witnessed,
        horizon=20,
        book=BOOK,
        csi500=(),
        as_of=datetime(2026, 12, 31, 18, 0, tzinfo=SHANGHAI),
    )

    assert not outcome.mature
    assert (outcome.entry_day, outcome.exit_day) == (CALENDAR.trading_days[-2], None)
    assert labels.asked == []
    with pytest.raises(SurvivorForwardError, match="calendar"):
        record_outcome(
            labels,
            witnessed,
            horizon=20,
            book=BOOK,
            csi500=(),
            as_of=datetime(2027, 3, 1, 18, 0, tzinfo=SHANGHAI),
        )


def test_a_record_before_the_readers_calendar_is_refused_not_called_immature() -> None:
    """A reader read over the wrong years has no session for the record at all: the record is
    then unknown, and calling it immature would keep it out of every number for ever."""
    day = date(2025, 12, 30)
    batch = batch_on(day, ONE_SCORE)
    witnessed = WitnessedRecord(
        session=day,
        ordinal=1,
        record_id="prd_last_year",
        record=PredictionRecord(
            batch=batch,
            outcome_known_at=datetime(2026, 1, 29, 15, 0, tzinfo=SHANGHAI),
            recorded_at=batch.predicted_at + timedelta(minutes=1),
        ),
    )
    labels = HandLabels(dict.fromkeys(ONE_SCORE, 0.1))

    with pytest.raises(SurvivorForwardError, match="2025-12-30"):
        record_outcome(labels, witnessed, horizon=20, book=BOOK, csi500=(), as_of=AS_OF)
    assert labels.asked == []


def test_a_metric_with_fewer_than_two_values_in_the_window_is_not_tested_and_says_why() -> None:
    outcomes = (made(1, rank_ic=None), made(21, rank_ic=0.02), made(41, rank_ic=None))

    built = layer(outcomes, verified(outcomes), through=41)

    primary, *secondaries = built.headline.non_overlapping.tests
    assert (primary.metric, primary.values, primary.test, primary.one_sided_p_value) == (
        "rank_ic",
        1,
        None,
        None,
    )
    assert primary.mean == pytest.approx(0.02)
    assert primary.absence is not None
    assert "at least 2" in primary.absence
    assert all(test.test is not None and test.absence is None for test in secondaries)


@pytest.mark.parametrize("horizon", [0, -5])
def test_a_horizon_that_is_not_positive_is_refused(horizon: int) -> None:
    with pytest.raises(SurvivorForwardError, match="horizon"):
        record_outcome(
            HandLabels(dict.fromkeys(ONE_SCORE, 0.1)),
            witnessed_on(HAND_DAY, ONE_SCORE),
            horizon=horizon,
            book=BOOK,
            csi500=(),
            as_of=AS_OF,
        )


# --- the prediction layer ----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Verdict:
    """`strategy_survivor_check.SurvivorRecordVerdict`'s two fields the layer reads."""

    verdict: str
    changes: tuple[str, ...] = ()


VERIFIED: Final[Verdict] = Verdict("verified")
UNVERIFIABLE: Final[Verdict] = Verdict("unverifiable", ("daily 2026: census moved",))
REFUSED: Final[Verdict] = Verdict("refused")
LAYER_AS_OF: Final[datetime] = datetime(2026, 6, 30, 18, 0, tzinfo=SHANGHAI)


def made(
    ordinal: int,
    *,
    horizon: int = 20,
    mature: bool = True,
    rank_ic: float | None = 0.05,
    **values: float | None,
) -> RecordOutcome:
    """A record's outcome on the session of `ordinal`, its numbers given directly."""
    numbers: dict[str, float | None] = {
        "investable_ew": 0.01,
        "survivors_minus_ew": 0.002,
        "ew_minus_worst_decile": 0.003,
        "top_minus_bottom": 0.004,
        "csi500": 0.005,
        **values,
    }
    if not mature:
        numbers = dict.fromkeys(numbers)
        rank_ic = None
    return RecordOutcome(
        record_id=f"prd_{ordinal:04d}",
        session=ORDINALS.day_of(ordinal),
        ordinal=ordinal,
        horizon=horizon,
        label_as_of=LAYER_AS_OF,
        entry_day=None,
        exit_day=None,
        mature=mature,
        scope=10,
        survivors=8,
        labelled=9 if mature else 0,
        unlabelled=1 if mature else 0,
        unlabelled_by_code=(("halted_session", 1),) if mature else (),
        scope_by_decile=(1,) * 10,
        unlabelled_by_decile=(1, *(0,) * 9) if mature else (0,) * 10,
        rank_ic=rank_ic,
        investable_ew=numbers["investable_ew"],
        survivors_minus_ew=numbers["survivors_minus_ew"],
        ew_minus_worst_decile=numbers["ew_minus_worst_decile"],
        top_minus_bottom=numbers["top_minus_bottom"],
        csi500=numbers["csi500"],
        absent=(),
    )


def every_session(
    last: int, *, skip: frozenset[int] = frozenset(), immature: frozenset[int] = frozenset()
) -> tuple[RecordOutcome, ...]:
    return tuple(
        made(ordinal, rank_ic=0.01 * (ordinal % 7) - 0.02, mature=ordinal not in immature)
        for ordinal in range(1, last + 1)
        if ordinal not in skip
    )


def verified(outcomes: tuple[RecordOutcome, ...], **overrides: Verdict) -> dict[str, Verdict]:
    verdicts: dict[str, Verdict] = {outcome.record_id: VERIFIED for outcome in outcomes}
    verdicts.update(overrides)
    return verdicts


def layer(
    outcomes: tuple[RecordOutcome, ...],
    verdicts: Mapping[str, Verdict],
    *,
    through: int,
    horizon: int = 20,
    **changes: object,
) -> PredictionLayer:
    read = protocol(**changes)
    return prediction_layer(
        outcomes,
        verdicts,
        protocol=read,
        sampled=sampled_sessions(
            ORDINALS,
            anchor_ordinal=read.anchor_ordinal,
            through=ORDINALS.day_of(through),
            horizon=horizon,
        ),
    )


def day(ordinal: int) -> date:
    return ORDINALS.day_of(ordinal)


def test_before_the_window_there_are_counts_and_descriptive_means_and_no_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_test(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a test was made before the pre-registered window")

    monkeypatch.setattr(forward_module, "sign_flip_test", no_test)
    outcomes = every_session(41, immature=frozenset({41}))

    built = layer(outcomes, verified(outcomes), through=41)

    sample = built.headline.non_overlapping
    assert sample.sampled == (day(1), day(21), day(41))
    assert [period.session for period in sample.periods] == [day(1), day(21)]
    assert sample.pending == (day(41),)
    assert (sample.matured, sample.window_periods, sample.window_satisfied) == (2, 3, False)
    assert sample.tests == ()
    assert sample.status == "not tested: 2 of 3 pre-registered periods matured"
    assert all(mean.wording == DESCRIPTIVE_WORDING for mean in sample.means)
    assert DESCRIPTIVE_WORDING == "descriptive, not a test"
    assert "p_value" not in repr(sample)
    assert (built.witnessed, built.mature, built.immature) == (41, 40, 1)


def test_at_the_window_each_metric_of_the_horizon_is_tested_once_over_the_first_k_periods() -> None:
    outcomes = every_session(61)

    built = layer(outcomes, verified(outcomes), through=61)

    sample = built.headline.non_overlapping
    assert [period.session for period in sample.periods] == [day(1), day(21), day(41), day(61)]
    assert (sample.matured, sample.window_satisfied) == (4, True)
    assert [(test.metric, test.role, test.values) for test in sample.tests] == [
        ("rank_ic", "primary", 3),
        ("survivors_minus_ew", "secondary", 3),
        ("ew_minus_worst_decile", "secondary", 3),
    ]
    first_three = tuple(0.01 * (ordinal % 7) - 0.02 for ordinal in (1, 21, 41))
    primary = sample.tests[0]
    assert primary.test == sign_flip_test(first_three, bootstrap_samples=1000, random_seed=11)
    assert primary.mean == pytest.approx(statistics.fmean(first_three))
    assert primary.test is not None
    expected_one_sided = (
        primary.test.p_value / 2
        if statistics.fmean(first_three) > 0
        else 1 - primary.test.p_value / 2
    )
    assert primary.one_sided_p_value == pytest.approx(expected_one_sided)
    assert sample.status == "tested once, over the first 3 of 4 matured periods"
    assert not any(word in repr(built).lower() for word in FORBIDDEN_WORDS)


def test_a_sampled_session_with_no_record_is_a_gap_and_does_not_shift_later_picks() -> None:
    outcomes = every_session(61, skip=frozenset({21}))

    built = layer(outcomes, verified(outcomes), through=61)

    sample = built.headline.non_overlapping
    assert sample.gaps == ((day(21), "no_record"),)
    assert [period.session for period in sample.periods] == [day(1), day(41), day(61)]
    assert day(22) not in {period.session for period in sample.periods}
    values = tuple(0.01 * (ordinal % 7) - 0.02 for ordinal in (1, 41, 61))
    assert sample.tests[0].test == sign_flip_test(values, bootstrap_samples=1000, random_seed=11)


def test_a_refused_or_an_unchecked_record_is_a_gap_and_is_counted() -> None:
    outcomes = every_session(61)
    verdicts = verified(outcomes, prd_0021=REFUSED)
    del verdicts["prd_0041"]

    built = layer(outcomes, verdicts, through=61)

    sample = built.headline.non_overlapping
    assert sample.gaps == ((day(21), "refused"), (day(41), "unchecked"))
    assert [period.session for period in sample.periods] == [day(1), day(61)]
    assert built.excluded == (("refused", 1), ("unchecked", 1))
    assert sample.tests == ()
    assert built.headline.included == 59


def test_excluding_unverifiable_turns_its_sampled_session_into_a_gap() -> None:
    outcomes = every_session(41)

    built = layer(outcomes, verified(outcomes, prd_0021=UNVERIFIABLE), through=41)

    headline = built.headline.non_overlapping
    sensitivity = built.excluding_unverifiable.non_overlapping
    assert [period.session for period in headline.periods] == [day(1), day(21), day(41)]
    assert headline.gaps == ()
    assert [test.metric for test in headline.tests] == [
        "rank_ic",
        "survivors_minus_ew",
        "ew_minus_worst_decile",
    ]
    assert sensitivity.gaps == ((day(21), "unverifiable"),)
    assert [period.session for period in sensitivity.periods] == [day(1), day(41)]
    assert sensitivity.tests == ()
    assert sensitivity.status == "not tested: 2 of 3 pre-registered periods matured"
    assert (built.headline.included, built.excluding_unverifiable.included) == (41, 40)


def test_the_descriptive_means_are_over_every_included_mature_daily_record() -> None:
    outcomes = (
        made(1, rank_ic=0.1),
        made(2, rank_ic=0.3),
        made(3, rank_ic=-0.2),
        made(4, rank_ic=None),
        made(5, mature=False),
        made(6, rank_ic=0.9),
    )
    verdicts = verified(outcomes, prd_0006=REFUSED)

    built = layer(outcomes, verdicts, through=6)

    means = {mean.quantity: mean for mean in built.headline.descriptive}
    assert list(means) == [
        "rank_ic",
        "investable_ew",
        "survivors_minus_ew",
        "ew_minus_worst_decile",
        "top_minus_bottom",
        "csi500",
    ]
    assert (means["rank_ic"].n, means["rank_ic"].mean) == (3, pytest.approx(0.2 / 3))
    assert (means["investable_ew"].n, means["investable_ew"].mean) == (4, pytest.approx(0.01))
    assert {field.name for field in dataclasses.fields(means["rank_ic"])} == {
        "quantity",
        "n",
        "mean",
        "wording",
    }
    assert all(mean.wording == DESCRIPTIVE_WORDING for mean in means.values())
    assert built.headline.unlabelled_by_code == (("halted_session", 4),)
    assert built.headline.unlabelled_by_decile == (4, *(0,) * 9)
    assert built.headline.scope_by_decile == (4,) * 10


@pytest.mark.parametrize(
    "metric", ["rank_ic", "survivors_minus_ew", "ew_minus_worst_decile", "top_minus_bottom"]
)
@pytest.mark.parametrize("sign", [1, -1], ids=["positive-mean", "negative-mean"])
def test_the_one_sided_p_of_every_metric_is_small_for_a_positive_mean_and_large_for_a_negative(
    metric: str, sign: int
) -> None:
    """The one-sided p is for "mean > 0", which supports the screen whichever metric is tested.
    Three periods of one sign: of the eight sign patterns only the observed one and its negation
    reach the sum's size, so the two-sided p is 1/4 exactly; the one-sided p is 1/8 for a
    positive mean and 7/8 for a negative one -- never half the two-sided p whatever the sign."""
    outcomes = tuple(made(ordinal, **{metric: sign * 0.004}) for ordinal in (1, 21, 41))

    built = layer(
        outcomes, verified(outcomes), through=41, primary_metric=metric, secondary_metrics=[]
    )

    [test] = built.headline.non_overlapping.tests
    assert (test.metric, test.values, test.mean) == (metric, 3, pytest.approx(sign * 0.004))
    assert test.test is not None
    assert (test.test.p_value, test.test.exact) == (0.25, True)
    assert test.one_sided_p_value == (0.125 if sign > 0 else 0.875)


def test_a_secondary_horizon_tests_only_the_primary_metric_and_as_a_secondary() -> None:
    outcomes = tuple(made(ordinal, horizon=5) for ordinal in range(1, 12))

    built = layer(outcomes, verified(outcomes), through=11, horizon=5)

    assert (built.horizon, built.role) == (5, "secondary")
    assert [period.session for period in built.headline.non_overlapping.periods] == [
        day(1),
        day(6),
        day(11),
    ]
    assert [(test.metric, test.role) for test in built.headline.non_overlapping.tests] == [
        ("rank_ic", "secondary")
    ]


def test_the_layer_carries_its_wording_and_no_verdict_word() -> None:
    outcomes = every_session(61)

    built = layer(outcomes, verified(outcomes), through=61)

    assert built.wording == PREDICTION_LAYER_WORDING
    assert PREDICTION_LAYER_WORDING == (
        "candidate forecast of a registered research configuration; not a performance claim"
    )
    assert built.label_as_of == LAYER_AS_OF


def test_an_inconsistent_layer_is_refused_by_name() -> None:
    outcomes = every_session(5)
    verdicts = verified(outcomes)

    with pytest.raises(SurvivorForwardError, match="horizon"):
        layer(outcomes, verdicts, through=5, horizon=7)
    with pytest.raises(SurvivorForwardError, match="horizon"):
        layer((*outcomes, made(6, horizon=5)), verdicts, through=6)
    with pytest.raises(SurvivorForwardError, match=day(3).isoformat()):
        layer((*outcomes, dataclasses.replace(made(3), record_id="prd_again")), verdicts, through=5)
    with pytest.raises(SurvivorForwardError, match="anchor"):
        prediction_layer(
            outcomes,
            verdicts,
            protocol=protocol(),
            sampled=sampled_sessions(ORDINALS, anchor_ordinal=2, through=day(5), horizon=20),
        )
    with pytest.raises(SurvivorForwardError, match="maybe"):
        layer(outcomes, verified(outcomes, prd_0002=Verdict("maybe")), through=5)


SEEDED_PERIODS: Final[int] = EXACT_SIGN_FLIP_LIMIT + 1


def seeded_layer() -> PredictionLayer:
    """Thirteen two-session periods: past the exact enumeration, so the patterns are drawn."""
    outcomes = tuple(
        made(ordinal, horizon=2, rank_ic=(-1) ** ordinal * 0.01 * (ordinal % 5) + 0.004)
        for ordinal in range(1, 2 * SEEDED_PERIODS)
    )
    return layer(
        outcomes,
        verified(outcomes),
        through=2 * SEEDED_PERIODS - 1,
        horizon=2,
        primary_horizon=2,
        secondary_horizons=[],
        secondary_metrics=[],
        window_periods=SEEDED_PERIODS,
    )


def seeded_layer_digest() -> str:
    return hashlib.sha256(repr(seeded_layer()).encode()).hexdigest()


def test_the_seeded_sign_flip_is_deterministic_and_byte_identical_under_two_hash_seeds() -> None:
    built = seeded_layer()
    test = built.headline.non_overlapping.tests[0].test

    assert test is not None
    assert (test.exact, test.random_seed, test.sign_patterns) == (False, 11, 1000)
    assert seeded_layer() == built
    here = Path(__file__).resolve()
    program = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('survivor_forward_test', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec);"
        "sys.modules['survivor_forward_test'] = module;"
        "spec.loader.exec_module(module);"
        "print(module.seeded_layer_digest())"
    )
    path = os.pathsep.join(
        part
        for part in (
            str(ROOT / "src"),
            str(ROOT / "tests"),
            os.fspath(here.parent),
            os.environ.get("PYTHONPATH"),
        )
        if part
    )
    for hash_seed in ("0", "1"):
        completed = subprocess.run(
            [sys.executable, "-c", program, str(here)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": path},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == seeded_layer_digest()


# --- the report's document and its lines (task E4) -----------------------------------------------
#
# `survivor_forward_document` puts the layers, the late-record check's verdicts and the paper
# record into one `survivor_forward_report/v1` document; `survivor_forward_lines` prints it. The
# layers here are E2's own, driven by `made` outcomes; the verdicts are E1's
# `SurvivorRecordVerdict`; the paper record is refused unless a test hands one in.

INTEGRITY: Final[str] = "integrity: the caller's threat model, handed in"
NO_BOOK: Final[str] = "the paper book holds no snapshot"
ALWAYS: Final[tuple[str, ...]] = (
    "g0_needs_research_and_validation_conclusions",
    "spent_window_2024_01_02_to_2026_09_24",
    "paper_equity_is_not_evidence",
    "t8_expected_numbers_are_optimistic",
    "ic_2021",
    "h5_ic_dispersion_unmeasured",
    "no_null_signal_control",
)


def checked_by(
    outcomes: tuple[RecordOutcome, ...], **overrides: tuple[str, str | None, tuple[str, ...]]
) -> dict[str, SurvivorRecordVerdict]:
    """E1's verdict of each record of `outcomes`: verified, unless `overrides` names it with its
    `(verdict, scope, changes)`."""
    verdicts: dict[str, SurvivorRecordVerdict] = {}
    for outcome in outcomes:
        verdict, scope, changes = overrides.get(outcome.record_id, ("verified", None, ()))
        verdicts[outcome.record_id] = SurvivorRecordVerdict(
            record_id=outcome.record_id,
            session=outcome.session,
            verdict=verdict,  # type: ignore[arg-type]
            scope=scope,  # type: ignore[arg-type]
            changes=changes,
            reason=None if verdict == "verified" else f"by hand: {verdict}",
        )
    return verdicts


def reported(
    outcomes: Mapping[int, tuple[RecordOutcome, ...]],
    verdicts: Mapping[str, SurvivorRecordVerdict],
    *,
    through: int,
    paper: Mapping[str, Any] | None = None,
    **changes: object,
) -> dict[str, Any]:
    """The document of `outcomes` (by horizon) under the test protocol with `changes`, the
    sample drawn through the session of ordinal `through` at every horizon."""
    read = protocol(**changes)
    layers = tuple(
        prediction_layer(
            outcomes.get(horizon, ()),
            verdicts,
            protocol=read,
            sampled=sampled_sessions(
                ORDINALS, anchor_ordinal=read.anchor_ordinal, through=day(through), horizon=horizon
            ),
        )
        for horizon in (read.primary_horizon, *read.secondary_horizons)
    )
    return survivor_forward_document(
        as_of=LAYER_AS_OF,
        config_id=REGISTERED.config_id,
        registration_sha256=REGISTERED.registration_sha256,
        protocol=read,
        ordinals=ORDINALS,
        through=day(through),
        witnessed=tuple(sorted({item.session for group in outcomes.values() for item in group})),
        verdicts=verdicts,
        layers=layers,
        paper=paper_refusal(NO_BOOK) if paper is None else paper,
        integrity=INTEGRITY,
    )


def codes_of(document: Mapping[str, Any]) -> list[str]:
    return [item["code"] for item in document["cannot_be_evaluated_yet"]]


def verdict_words(document: Mapping[str, Any]) -> list[str]:
    """Every `FORBIDDEN_WORDS` word the document's JSON or its printed lines say, as a word: the
    check's own scope names (`recompute_failed`, `no_provenance`) are vocabulary, not verdicts."""
    text = json.dumps(document, sort_keys=True) + "\n".join(survivor_forward_lines(document))
    return re.findall(rf"\b({'|'.join(FORBIDDEN_WORDS)})\b", text, flags=re.IGNORECASE)


def test_before_the_primary_window_the_document_counts_describes_and_tests_nothing() -> None:
    """Twenty-five mature daily records and a window of three: at h=20 two sampled periods have
    matured (1 and 21), so nothing is tested -- "not tested: 2 of 3". At h=5 five have, but a
    secondary is tested only in the one analysis at the primary window: no test there either,
    no correction of the secondary family, no p-value anywhere and no verdict word."""
    outcomes = {20: every_session(25), 5: tuple(made(o, horizon=5) for o in range(1, 26))}

    document = reported(outcomes, checked_by(outcomes[20]), through=25)

    assert document["kind"] == SURVIVOR_FORWARD_REPORT_KIND
    assert (document["protocol"]["periods_matured"], document["protocol"]["window_satisfied"]) == (
        2,
        False,
    )
    twenty = document["prediction_layer"]["h20"]["non_overlapping"]
    assert (twenty["test"], twenty["status"]) == (
        None,
        "not tested: 2 of 3 pre-registered periods matured",
    )
    five = document["prediction_layer"]["h5"]["non_overlapping"]
    assert (five["matured"], five["window_satisfied"], five["test"]) == (5, True, None)
    assert five["status"] == (
        "not tested: a secondary is tested in the one analysis at the primary window "
        "(2 of 3 primary periods matured)"
    )
    family = document["prediction_layer"]["secondary_family"]
    assert family["headline"] == {
        "status": "not applied: before the pre-registered window there is no p-value to "
        "correct (2 of 3 primary periods matured)",
        "tests": [],
    }
    assert "p_value" not in json.dumps(document)
    assert not any("one-sided p" in line for line in survivor_forward_lines(document))
    assert verdict_words(document) == []
    assert codes_of(document) == ["window_not_satisfied", *ALWAYS]
    for name in ("protocol", "records", "prediction_layer", "paper_ledger", "relative"):
        assert document[name]["wording"] == SURVIVOR_WORDING, name
    for name in ("h20", "h5"):
        assert document["prediction_layer"][name]["wording"] == SURVIVOR_WORDING
        assert document["prediction_layer"][name]["forecast_wording"] == PREDICTION_LAYER_WORDING
    assert document["wording"] == [SURVIVOR_WORDING, PREDICTION_LAYER_WORDING, NO_VERDICT_WORDING]
    assert NO_VERDICT_WORDING == "no verdict before the pre-registered window"


def by_by_hand(p_values: Mapping[str, float], *, family_size: int) -> dict[str, float]:
    """Benjamini-Yekutieli's adjusted p-values written out: ascending by `(p, id)`, the running
    minimum of `H_m * m * p_(j) / j` from the largest rank down, capped at one."""
    harmonic = sum(1 / index for index in range(1, family_size + 1))
    ranked = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = float("inf")
    for rank in range(len(ranked), 0, -1):
        name, p_value = ranked[rank - 1]
        running = min(running, harmonic * family_size * p_value / rank)
        adjusted[name] = min(1.0, running)
    return adjusted


def test_at_the_primary_window_the_secondary_family_is_corrected_by_benjamini_yekutieli() -> None:
    """A window of twelve two-session periods, primary h=2 and a secondary h=3: every metric is
    tested once over its first twelve periods, exactly. A constant positive excess has the
    smallest one-sided p twelve sign flips allow, 1/4096; a constant negative one -- a worst
    decile ahead of the equal weight -- the largest.
    The secondary family -- the two secondary metrics at h=2 and the rank IC at h=3 -- is then
    corrected under arbitrary dependence, by hand: the two small p-values clear q, the large one
    does not."""
    outcomes = {
        2: tuple(
            made(o, horizon=2, rank_ic=0.01 * (o % 7) - 0.02, ew_minus_worst_decile=-0.003)
            for o in range(1, 35)
        ),
        3: tuple(made(o, horizon=3, rank_ic=0.004 + 0.001 * (o % 3)) for o in range(1, 35)),
    }

    document = reported(
        outcomes,
        checked_by(outcomes[2]),
        through=34,
        primary_horizon=2,
        secondary_horizons=[3],
        window_periods=12,
    )

    assert document["protocol"]["window_satisfied"] is True
    two = document["prediction_layer"]["h2"]["non_overlapping"]
    assert two["status"] == "tested once, over the first 12 of 17 matured periods"
    tests = {test["metric"]: test for test in two["test"]}
    assert (tests["survivors_minus_ew"]["exact"], tests["survivors_minus_ew"]["sign_patterns"]) == (
        True,
        4096,
    )
    assert tests["survivors_minus_ew"]["one_sided_p_value"] == pytest.approx(1 / 4096)
    assert tests["ew_minus_worst_decile"]["one_sided_p_value"] == pytest.approx(1 - 1 / 4096)
    three = document["prediction_layer"]["h3"]["non_overlapping"]
    assert three["test"][0]["one_sided_p_value"] == pytest.approx(1 / 4096)
    family = document["prediction_layer"]["secondary_family"]
    assert family["family"] == ["h2.survivors_minus_ew", "h2.ew_minus_worst_decile", "h3.rank_ic"]
    assert (family["dependence"], family["q"], family["family_size"]) == ("arbitrary", 0.2, 3)
    corrected = {item["hypothesis_id"]: item for item in family["headline"]["tests"]}
    expected = by_by_hand(
        {name: item["one_sided_p_value"] for name, item in corrected.items()}, family_size=3
    )
    assert expected["h2.survivors_minus_ew"] == pytest.approx(5.5 / 4096 / 2)
    assert expected["h2.ew_minus_worst_decile"] == 1.0
    for name, q_value in expected.items():
        assert corrected[name]["q_value"] == pytest.approx(q_value)
    assert [corrected[name]["rejected"] for name in family["family"]] == [True, False, True]
    assert family["headline"]["status"] == "applied over 3 of 3 secondary hypotheses"
    assert "window_not_satisfied" not in codes_of(document)


def test_a_refused_record_inside_the_tested_sample_compromises_it_and_one_after_does_not() -> None:
    """Decision 9: a refused record whose session falls in the sample the test is made over --
    up to the K-th matured period -- prints the test "compromised (n refused)". One refused
    after that period is a gap of the sample and leaves the test as it is."""
    outcomes = {20: every_session(81), 5: ()}

    inside = reported(
        outcomes,
        checked_by(outcomes[20], prd_0021=("refused", "recompute_differs", ())),
        through=81,
    )
    after = reported(
        outcomes,
        checked_by(outcomes[20], prd_0081=("refused", "recompute_differs", ())),
        through=81,
    )

    sample = inside["prediction_layer"]["h20"]["non_overlapping"]
    assert sample["refused_in_sample"] == 1
    assert sample["status"] == (
        "compromised (1 refused): tested once, over the first 3 of 4 matured periods"
    )
    assert sample["test"] is not None
    later = after["prediction_layer"]["h20"]["non_overlapping"]
    assert later["refused_in_sample"] == 0
    assert later["status"] == "tested once, over the first 3 of 4 matured periods"
    assert {"session": day(81).isoformat(), "reason": "refused"} in later["gaps"]


def test_the_lines_list_the_refusals_first_then_the_layers_ledger_relative_wording_integrity() -> (
    None
):
    """The records' refusals and UNVERIFIABLE records are printed before the prediction layer,
    the paper ledger after it (here refused, in its own line), then the relative return, what
    cannot be evaluated yet, the wording and the integrity statement last. A document read back
    from its sorted JSON prints the same lines."""
    outcomes = {20: every_session(25), 5: ()}
    verdicts = checked_by(
        outcomes[20],
        prd_0003=("refused", "no_provenance", ()),
        prd_0004=("unverifiable", "recompute_differs", ("daily 2026: census moved",)),
    )

    document = reported(outcomes, verdicts, through=25)
    lines = survivor_forward_lines(document)

    def first(prefix: str) -> int:
        return next(index for index, line in enumerate(lines) if line.startswith(prefix))

    assert (
        first("records:")
        < first("  REFUSED prd_0003")
        < first("  UNVERIFIABLE prd_0004")
        < first("prediction layer")
        < first("paper ledger")
        < first("relative return")
        < first("cannot be evaluated yet:")
        < first("wording:")
    )
    assert "daily 2026: census moved" in lines[first("  UNVERIFIABLE prd_0004")]
    assert lines[first("paper ledger")] == (
        f"paper ledger -- refused: {NO_BOOK}; the prediction layer above stands"
    )
    assert lines[-4:] == [
        f"wording: {SURVIVOR_WORDING}",
        f"wording: {PREDICTION_LAYER_WORDING}",
        f"wording: {NO_VERDICT_WORDING}",
        INTEGRITY,
    ]
    assert survivor_forward_lines(json.loads(json.dumps(document, sort_keys=True))) == lines
    records = document["records"]
    assert records["by_verdict"] == {
        "verified": 23,
        "unverifiable": 1,
        "refused": 1,
        "unchecked": 0,
    }
    assert records["by_scope"]["no_provenance"] == 1
    assert [item["record_id"] for item in records["refused"]] == ["prd_0003"]
    assert records["unverifiable"][0]["changes"] == ["daily 2026: census moved"]
    for section in ("paper_ledger", "relative"):
        assert document[section] == {"wording": SURVIVOR_WORDING, "refused": NO_BOOK}


def test_what_cannot_be_evaluated_yet_is_said_only_while_it_holds() -> None:
    """No record at all: no record exists, no outcome has matured, the window is not satisfied,
    and the standing items. Records none of which has matured: the first item goes."""
    empty = reported({20: (), 5: ()}, {}, through=0)
    pending = every_session(5, immature=frozenset(range(1, 6)))
    young = reported({20: pending, 5: ()}, checked_by(pending), through=5)

    assert codes_of(empty) == [
        "no_survivor_record_yet",
        "first_outcome_not_matured",
        "window_not_satisfied",
        *ALWAYS,
    ]
    assert codes_of(young) == ["first_outcome_not_matured", "window_not_satisfied", *ALWAYS]
    statements = {item["code"]: item["statement"] for item in empty["cannot_be_evaluated_yet"]}
    assert statements["first_outcome_not_matured"].startswith(
        "the first h=20 outcome matures about 21 sessions after the first evening"
    )
    assert "+0.037" in statements["ic_2021"]
    assert "2024-01-02..2026-09-24" in statements["spent_window_2024_01_02_to_2026_09_24"]
    assert "V2-P7-002" in statements["no_null_signal_control"]
    assert (empty["records"]["witnessed"], empty["records"]["first_session"]) == (0, None)
    assert empty["records"]["missing_sessions"] == []
    assert young["records"]["missing_sessions"] == []


def test_the_missing_sessions_are_the_closed_sessions_after_the_epoch_without_a_record() -> None:
    outcomes = every_session(8, skip=frozenset({3, 6}))

    document = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=9)

    assert document["records"]["missing_sessions"] == [
        day(3).isoformat(),
        day(6).isoformat(),
        day(9).isoformat(),
    ]
    assert (document["records"]["first_session"], document["records"]["newest_session"]) == (
        day(1).isoformat(),
        day(8).isoformat(),
    )


def test_the_report_runs_through_the_newest_session_whose_cutoff_has_passed() -> None:
    """A session is a gap or missing only once its record can no longer be filed -- 09:15 of the
    next session has come -- or when a record of it is already held."""
    thursday, friday = date(2026, 1, 15), date(2026, 1, 16)
    saturday = datetime(2026, 1, 17, 12, 0, tzinfo=SHANGHAI)

    assert report_through(CALENDAR, ORDINALS, as_of=saturday, witnessed=()) == thursday
    assert report_through(CALENDAR, ORDINALS, as_of=saturday, witnessed=(friday,)) == friday
    monday_auction = datetime(2026, 1, 19, 9, 15, tzinfo=SHANGHAI)
    assert report_through(CALENDAR, ORDINALS, as_of=monday_auction, witnessed=()) == friday
    early = datetime(2026, 1, 6, 9, 0, tzinfo=SHANGHAI)
    assert report_through(CALENDAR, ORDINALS, as_of=early, witnessed=()) == EPOCH


def test_the_relative_return_reads_the_sampled_periods_and_names_the_ones_left_out() -> None:
    """The relative return's windows are the headline's sampled periods at the primary horizon,
    each from its record's entry close to its exit close with its equal weight and CSI 500; a
    period with a benchmark missing is left out and named in the document."""
    outcomes = tuple(
        dataclasses.replace(
            made(o, csi500=None if o == 21 else 0.005),
            entry_day=day(o + 1),
            exit_day=day(o + 21),
        )
        for o in range(1, 62)
    )
    built = layer(outcomes, verified(outcomes), through=61)

    picked = sampled_benchmarks(built, outcomes)

    assert [(item.record_id, item.entry, item.exit) for item in picked] == [
        ("prd_0001", day(2), day(22)),
        ("prd_0041", day(42), day(62)),
        ("prd_0061", day(62), day(82)),
    ]
    assert {(item.investable_ew, item.csi500) for item in picked} == {(0.01, 0.005)}
    paper = {
        "paper_ledger": {"wording": SURVIVOR_WORDING},
        "relative": {"wording": SURVIVOR_WORDING, "windows_given": 3},
    }
    document = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=61, paper=paper)
    assert document["relative"]["windows_left_out"] == [
        {"record_id": "prd_0021", "session": day(21).isoformat(), "missing": ["csi500"]}
    ]


def test_a_document_needs_one_layer_per_registered_horizon() -> None:
    read = protocol()
    only = prediction_layer(
        (),
        {},
        protocol=read,
        sampled=sampled_sessions(ORDINALS, anchor_ordinal=1, through=day(3), horizon=20),
    )
    with pytest.raises(SurvivorForwardError, match="horizon"):
        survivor_forward_document(
            as_of=LAYER_AS_OF,
            config_id=REGISTERED.config_id,
            registration_sha256=REGISTERED.registration_sha256,
            protocol=read,
            ordinals=ORDINALS,
            through=day(3),
            witnessed=(),
            verdicts={},
            layers=(only,),
            paper=paper_refusal(NO_BOOK),
            integrity=INTEGRITY,
        )


def seeded_document() -> dict[str, Any]:
    """A document with every kind of entry: refused, UNVERIFIABLE and verified records, gaps,
    a compromised test and a corrected secondary family."""
    outcomes = {
        2: tuple(made(o, horizon=2, rank_ic=0.01 * (o % 7) - 0.02) for o in range(1, 35)),
        3: tuple(made(o, horizon=3, rank_ic=0.004 + 0.001 * (o % 3)) for o in range(1, 35)),
    }
    verdicts = checked_by(
        outcomes[2],
        prd_0003=("refused", "no_provenance", ()),
        prd_0007=("unverifiable", "recompute_failed", ("daily 2026: partition gone", "a b")),
        prd_0011=("refused", "recompute_differs", ()),
    )
    return reported(
        outcomes,
        verdicts,
        through=34,
        primary_horizon=2,
        secondary_horizons=[3],
        window_periods=12,
    )


def seeded_document_digest() -> str:
    return hashlib.sha256(json.dumps(seeded_document(), sort_keys=True).encode()).hexdigest()


def test_the_document_is_byte_identical_under_two_hash_seeds() -> None:
    document = seeded_document()
    assert document["prediction_layer"]["h2"]["non_overlapping"]["status"].startswith(
        "compromised (2 refused): tested once"
    )
    here = Path(__file__).resolve()
    program = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('survivor_forward_test', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec);"
        "sys.modules['survivor_forward_test'] = module;"
        "spec.loader.exec_module(module);"
        "print(module.seeded_document_digest())"
    )
    path = os.pathsep.join(
        part
        for part in (
            str(ROOT / "src"),
            str(ROOT / "tests"),
            os.fspath(here.parent),
            os.environ.get("PYTHONPATH"),
        )
        if part
    )
    digests = set()
    for hash_seed in ("0", "1"):
        completed = subprocess.run(
            [sys.executable, "-c", program, str(here)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": path},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        digests.add(completed.stdout.strip())
    assert digests == {seeded_document_digest()}
