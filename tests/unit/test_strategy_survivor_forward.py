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
from collections.abc import Iterable, Mapping, Sequence
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
from openalpha_cn.backtest.power_analysis import NOT_DETECTABLE_WORDING, POWER_BASIS
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
    OBSERVED_WORDING,
    POWER_NOT_PRE_REGISTERED,
    PREDICTION_LAYER_WORDING,
    SURVIVOR_FORWARD_POWER_KEY,
    SURVIVOR_FORWARD_POWER_SCHEMA,
    SURVIVOR_FORWARD_PROTOCOL_KEY,
    SURVIVOR_FORWARD_PROTOCOL_SCHEMA,
    SURVIVOR_FORWARD_REPORT_KIND,
    PlannedMetric,
    PredictionLayer,
    RecordOutcome,
    SampledPeriod,
    SampledSessions,
    SurvivorForwardError,
    SurvivorForwardPowerPlan,
    SurvivorForwardProtocol,
    WitnessedRecord,
    check_journals_against_the_store,
    paper_refusal,
    prediction_layer,
    read_forward_power,
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
    """The test protocol with `changes`, read for records that declare its primary horizon: the
    layer's tests read short windows (h=2, h=3) off synthetic records, and the reader's pin of the
    primary horizon to the records' declared one is tested on its own
    (`test_a_protocol_whose_primary_horizon_is_not_the_records_declared_one_is_refused`)."""
    block = {**PROTOCOL_BLOCK, **changes}
    return read_forward_protocol(
        {SURVIVOR_FORWARD_PROTOCOL_KEY: block}, record_horizon=f"{block['primary_horizon']}d"
    )


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


def test_a_protocol_whose_primary_horizon_is_not_the_records_declared_one_is_refused() -> None:
    """Seams review 5. Every survivor record declares its horizon (`SURVIVOR_HORIZON`, `20d`),
    and the daily command sizes the next year's calendar fetch for it; a protocol whose primary
    horizon is another would test a horizon no record declares. The reader refuses it by name,
    naming both -- by default against the records' own declared horizon, and against the one a
    caller names for records that declare another. A secondary horizon is read off the same
    record with another window, and is not pinned."""
    from openalpha_cn.domain.horizon import parse_horizon
    from openalpha_cn.strategy_survivor_registration import SURVIVOR_HORIZON

    assert parse_horizon(SURVIVOR_HORIZON).sessions == PROTOCOL_BLOCK["primary_horizon"] == 20
    assert read_forward_protocol({SURVIVOR_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK)}) == (
        read_forward_protocol(
            {SURVIVOR_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK)}, record_horizon="20d"
        )
    )
    ten = {**PROTOCOL_BLOCK, "primary_horizon": 10, "secondary_horizons": [5]}

    with pytest.raises(SurvivorForwardError, match=r"primary_horizon 10.*20d.*20 sessions") as no:
        read_forward_protocol({SURVIVOR_FORWARD_PROTOCOL_KEY: ten})

    assert isinstance(no.value, StrategyRegistrationError)
    with pytest.raises(SurvivorForwardError, match=r"primary_horizon 20.*10d"):
        read_forward_protocol(
            {SURVIVOR_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK)}, record_horizon="10d"
        )
    read = read_forward_protocol({SURVIVOR_FORWARD_PROTOCOL_KEY: ten}, record_horizon="10d")
    assert read.primary_horizon == 10
    with pytest.raises(SurvivorForwardError, match="record_horizon"):
        read_forward_protocol(
            {SURVIVOR_FORWARD_PROTOCOL_KEY: dict(PROTOCOL_BLOCK)}, record_horizon="20"
        )


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
        anchor_ordinal=anchor,
        horizon=horizon,
        through=through,
        sessions=expected,
        exits=tuple(ORDINALS.day_of(ORDINALS.index_of(day) + 1 + horizon) for day in expected),
    )


def test_each_sampled_session_carries_its_label_exit_on_the_calendar_or_none_past_it() -> None:
    """The exit of the label window of a sampled session -- the close `h` sessions after the
    session after it, `build_label_window`'s rule -- is the calendar's, so a position with no
    record has one too; past the stored calendar's last session it is unknown."""
    last = ORDINALS.index_of(CALENDAR.horizon.last_date)

    sampled = sampled_sessions(
        ORDINALS, anchor_ordinal=last - 25, through=ORDINALS.day_of(last), horizon=20
    )

    assert sampled.sessions == (ORDINALS.day_of(last - 25), ORDINALS.day_of(last - 5))
    assert sampled.exits == (ORDINALS.day_of(last - 4), None)
    window = build_label_window(
        as_of=session_publication_instant(sampled.sessions[0]),
        zone=SHANGHAI,
        horizon=ResearchHorizon(count=20, unit=HorizonUnit.trading_days),
        calendar=CALENDAR,
    )
    assert window.exit_day == sampled.exits[0]


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
    assert (sample.matured, sample.window_positions, sample.window_satisfied) == (2, 3, False)
    assert (sample.window_span_sessions, sample.window_sessions) == (60, sample.sampled)
    assert sample.tests == ()
    assert sample.status == "not tested: 2 of 3 pre-registered periods matured"
    assert all(mean.wording == DESCRIPTIVE_WORDING for mean in sample.means)
    assert DESCRIPTIVE_WORDING == "descriptive, not a test"
    assert "p_value" not in repr(sample)
    assert (built.witnessed, built.mature, built.immature) == (41, 40, 1)


def rank_ics(*ordinals: int) -> tuple[float, ...]:
    """`every_session`'s rank IC of the record of each of `ordinals`."""
    return tuple(0.01 * (ordinal % 7) - 0.02 for ordinal in ordinals)


def sessions_of(periods: Sequence[SampledPeriod]) -> list[date]:
    return [period.session for period in periods]


NO_GAP: Final[str] = "0 no record, 0 refused, 0 unverifiable excluded in the sensitivity"


def test_at_the_window_each_metric_is_tested_once_over_the_k_calendar_positions() -> None:
    """The analysis set is the calendar's: the K positions `anchor + h k`, k = 0 .. K - 1, from
    the anchor. Once the K-th has matured each metric is tested once over them; a sampled period
    after the window is described, never tested."""
    outcomes = every_session(61)

    built = layer(outcomes, verified(outcomes), through=61)

    sample = built.headline.non_overlapping
    assert sessions_of(sample.periods) == [day(1), day(21), day(41), day(61)]
    assert sample.window_sessions == (day(1), day(21), day(41))
    assert sessions_of(sample.tested_periods) == [day(1), day(21), day(41)]
    assert (sample.matured, sample.window_satisfied) == (3, True)
    assert dict(sample.in_window) == {
        "periods": 3,
        "pending": 0,
        "no_record": 0,
        "refused": 0,
        "unchecked": 0,
        "unverifiable": 0,
    }
    assert [(test.metric, test.role, test.values) for test in sample.tests] == [
        ("rank_ic", "primary", 3),
        ("survivors_minus_ew", "secondary", 3),
        ("ew_minus_worst_decile", "secondary", 3),
    ]
    first_three = rank_ics(1, 21, 41)
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
    assert sample.status == f"tested on 3 of 3 positions ({NO_GAP})"
    assert not any(word in repr(built).lower() for word in FORBIDDEN_WORDS)


def test_a_position_with_no_record_is_a_gap_that_reduces_n_and_never_moves_the_window() -> None:
    """Position 2 (ordinal 21) has no record: the test is over positions 1 and 3, n = 2 of
    K = 3, and the period after the window (ordinal 61) does not take its place."""
    outcomes = every_session(61, skip=frozenset({21}))

    built = layer(outcomes, verified(outcomes), through=61)

    sample = built.headline.non_overlapping
    assert sample.gaps == ((day(21), "no_record"),)
    assert sessions_of(sample.periods) == [day(1), day(41), day(61)]
    assert day(22) not in set(sessions_of(sample.periods))
    assert sessions_of(sample.tested_periods) == [day(1), day(41)]
    assert (sample.matured, sample.window_satisfied) == (3, True)
    assert sample.tests[0].test == sign_flip_test(
        rank_ics(1, 41), bootstrap_samples=1000, random_seed=11
    )
    assert sample.status == (
        "tested on 2 of 3 positions (1 no record, 0 refused, 0 unverifiable excluded in the "
        "sensitivity)"
    )


def test_a_refused_or_an_unchecked_record_is_a_gap_and_is_counted() -> None:
    """Position 2 refused and position 3 unchecked: both are gaps of the window, which is still
    satisfied -- its third position has matured -- so each metric is tested on what is left, one
    period, which no sign-flip test can be made over."""
    outcomes = every_session(61)
    verdicts = verified(outcomes, prd_0021=REFUSED)
    del verdicts["prd_0041"]

    built = layer(outcomes, verdicts, through=61)

    sample = built.headline.non_overlapping
    assert sample.gaps == ((day(21), "refused"), (day(41), "unchecked"))
    assert sessions_of(sample.periods) == [day(1), day(61)]
    assert sessions_of(sample.tested_periods) == [day(1)]
    assert built.excluded == (("refused", 1), ("unchecked", 1))
    assert sample.window_satisfied
    assert [(test.values, test.test, test.one_sided_p_value) for test in sample.tests] == [
        (1, None, None)
    ] * 3
    assert sample.status == (
        "tested on 1 of 3 positions (0 no record, 1 refused, 1 unchecked, 0 unverifiable "
        "excluded in the sensitivity)"
    )
    assert built.headline.included == 59


def test_a_verdict_that_turns_refused_leaves_the_tested_positions_where_they_were() -> None:
    """One report verifies every record; a later one finds position 2's refused. The window does
    not move out to ordinal 61: the later test is over positions 1 and 3, a subset of the first
    one's, and its status says what it lost."""
    outcomes = every_session(61)

    first = layer(outcomes, verified(outcomes), through=61).headline.non_overlapping
    later = layer(
        outcomes, verified(outcomes, prd_0021=REFUSED), through=61
    ).headline.non_overlapping

    assert sessions_of(first.tested_periods) == [day(1), day(21), day(41)]
    assert sessions_of(later.tested_periods) == [day(1), day(41)]
    assert later.window_sessions == first.window_sessions
    assert later.status.startswith("tested on 2 of 3 positions (0 no record, 1 refused")


def test_the_headline_and_the_sensitivity_are_tested_on_the_same_positions() -> None:
    """Position 2's record is UNVERIFIABLE: the headline is tested on positions 1, 2 and 3, the
    sensitivity -- which turns that session into a gap -- on 1 and 3: the same window less that
    record, never 1, 3 and the period after the window, so the two answers are over the same
    calendar periods."""
    outcomes = every_session(61)

    built = layer(outcomes, verified(outcomes, prd_0021=UNVERIFIABLE), through=61)

    headline = built.headline.non_overlapping
    sensitivity = built.excluding_unverifiable.non_overlapping
    assert sessions_of(headline.tested_periods) == [day(1), day(21), day(41)]
    assert headline.gaps == ()
    assert [test.metric for test in headline.tests] == [
        "rank_ic",
        "survivors_minus_ew",
        "ew_minus_worst_decile",
    ]
    assert sensitivity.gaps == ((day(21), "unverifiable"),)
    assert sensitivity.window_sessions == headline.window_sessions
    assert sessions_of(sensitivity.tested_periods) == [day(1), day(41)]
    assert sensitivity.tests[0].test == sign_flip_test(
        rank_ics(1, 41), bootstrap_samples=1000, random_seed=11
    )
    counted = "0 no record, 0 refused, 1 unverifiable excluded in the sensitivity"
    assert headline.status == f"tested on 3 of 3 positions ({counted})"
    assert sensitivity.status == f"tested on 2 of 3 positions ({counted})"
    assert (
        dict(headline.in_window)["unverifiable"],
        dict(sensitivity.in_window)["unverifiable"],
    ) == (
        0,
        1,
    )
    assert (built.headline.included, built.excluding_unverifiable.included) == (61, 60)


def labelled_at(
    instant: datetime, ordinals: Iterable[int], *, horizon: int = 20
) -> tuple[RecordOutcome, ...]:
    """Records of `ordinals` read at `instant`: each mature exactly when its exit's 16:30 has
    come, its entry and exit the calendar's."""
    outcomes: list[RecordOutcome] = []
    for ordinal in ordinals:
        exit_day = day(ordinal + 1 + horizon)
        mature = instant >= session_publication_instant(exit_day)
        outcomes.append(
            dataclasses.replace(
                made(ordinal, horizon=horizon, mature=mature, rank_ic=rank_ics(ordinal)[0]),
                label_as_of=instant,
                entry_day=day(ordinal + 1),
                exit_day=exit_day,
            )
        )
    return tuple(outcomes)


@pytest.mark.parametrize(("minutes", "satisfied"), [(-1, False), (0, True)])
def test_the_window_is_satisfied_when_its_last_position_matures_even_one_with_no_record(
    minutes: int, satisfied: bool
) -> None:
    """Position 3 (ordinal 41) has no record. Whether it has matured is the calendar's: its
    label window would exit at ordinal 62's close, published at 16:30 that day. From that
    instant the window is satisfied and tested on positions 1 and 2; a minute before, it is not,
    and nothing is tested."""
    instant = session_publication_instant(day(62)) + timedelta(minutes=minutes)
    outcomes = labelled_at(instant, (o for o in range(1, 62) if o != 41))

    built = layer(outcomes, verified(outcomes), through=61)

    sample = built.headline.non_overlapping
    assert (sample.matured, sample.window_satisfied) == (3 if satisfied else 2, satisfied)
    assert sample.gaps == ((day(41), "no_record"),)
    if satisfied:
        assert sessions_of(sample.tested_periods) == [day(1), day(21)]
        assert sample.status == (
            "tested on 2 of 3 positions (1 no record, 0 refused, 0 unverifiable excluded in "
            "the sensitivity)"
        )
    else:
        assert sample.tests == ()
        assert sample.status == "not tested: 2 of 3 pre-registered periods matured"


def test_with_no_record_at_all_a_position_has_matured_once_its_exit_is_on_or_before_through() -> (
    None
):
    """No outcome handed in, so the layer has no `as_of`: a position counts as matured when its
    exit is on or before `through`, whose 09:15 cutoff the report has passed -- so the exit's
    16:30 has too. Through ordinal 61 the third position (exit 62) has not; through 62 it has,
    and the window is satisfied with nothing in it to test: n = 0 of 3."""
    short = layer((), {}, through=61).headline.non_overlapping
    full = layer((), {}, through=62).headline.non_overlapping

    assert (short.matured, short.window_satisfied, short.tests) == (2, False, ())
    assert (full.matured, full.window_satisfied) == (3, True)
    assert [(test.values, test.test) for test in full.tests] == [(0, None)] * 3
    assert full.status == (
        "tested on 0 of 3 positions (3 no record, 0 refused, 0 unverifiable excluded in the "
        "sensitivity)"
    )


def test_an_outcome_whose_exit_is_not_its_positions_exit_on_the_calendar_is_refused() -> None:
    """A position's maturity is read off the sample's calendar when it has no record and off
    its record's outcome when it has one, so the two must agree on its exit."""
    outcomes = tuple(
        dataclasses.replace(outcome, exit_day=day(51)) if outcome.ordinal == 21 else outcome
        for outcome in labelled_at(LAYER_AS_OF, range(1, 42))
    )

    with pytest.raises(SurvivorForwardError, match=day(21).isoformat()):
        layer(outcomes, verified(outcomes), through=41)


def test_a_secondary_horizon_is_drawn_over_the_primary_windows_calendar_span() -> None:
    """K = 3 periods of 20 sessions span 60 sessions from the anchor. At h=5 the window is the
    floor(3 * 20 / 5) = 12 positions over the same span -- ordinals 1, 6, .., 56, whose last
    exits at ordinal 62, as the primary's last does -- and its one test, of the primary metric
    as a secondary, is over all twelve: never over the first three five-session periods."""
    outcomes = tuple(made(o, horizon=5, rank_ic=rank_ics(o)[0]) for o in range(1, 62))

    built = layer(outcomes, verified(outcomes), through=61, horizon=5)

    assert (built.horizon, built.role) == (5, "secondary")
    sample = built.headline.non_overlapping
    positions = tuple(range(1, 57, 5))
    assert (sample.window_positions, sample.window_span_sessions) == (12, 60)
    assert sample.sampled == tuple(day(o) for o in range(1, 62, 5))
    assert sample.window_sessions == tuple(day(o) for o in positions)
    assert sessions_of(sample.tested_periods) == [day(o) for o in positions]
    [test] = sample.tests
    assert (test.metric, test.role, test.values) == ("rank_ic", "secondary", 12)
    assert test.test == sign_flip_test(rank_ics(*positions), bootstrap_samples=1000, random_seed=11)
    assert sample.status == f"tested on 12 of 12 positions ({NO_GAP})"


@pytest.mark.parametrize(("horizon", "positions"), [(20, 3), (5, 12), (7, 8), (40, 1), (60, 1)])
def test_every_horizon_gets_the_whole_positions_that_fit_the_primary_windows_span(
    horizon: int, positions: int
) -> None:
    """floor(K * h_primary / h): K = 3 and h_primary = 20 span 60 sessions."""
    read = protocol(secondary_horizons=[] if horizon == 20 else [horizon])

    assert read.window_positions(horizon) == positions


def test_a_secondary_horizon_longer_than_the_primary_windows_span_is_refused_by_name() -> None:
    """It would have no position in the window: there is nothing to pre-register."""
    with pytest.raises(SurvivorForwardError, match="span"):
        protocol(secondary_horizons=[61])
    with pytest.raises(SurvivorForwardError, match="horizon 9"):
        protocol().window_positions(9)


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


def test_the_layer_counts_its_unverifiable_records_and_says_which_way_left_out_names_bias_it() -> (
    None
):
    """The layer carries how many of its records the check found UNVERIFIABLE -- in the headline,
    out of the sensitivity; a refused one is excluded from both and is not among them -- and,
    beside every unlabelled count, which way leaving those names out biases the numbers: a
    delisting is a large loss that is then missing, so the worst decile likely reads high."""
    outcomes = every_session(41)
    verdicts = verified(outcomes, prd_0021=UNVERIFIABLE, prd_0030=UNVERIFIABLE, prd_0031=REFUSED)

    built = layer(outcomes, verdicts, through=41)

    assert built.unverifiable == 2
    assert built.excluded == (("refused", 1),)
    wording = forward_module.UNLABELLED_WORDING
    assert (built.headline.unlabelled_wording, built.excluding_unverifiable.unlabelled_wording) == (
        wording,
        wording,
    )
    for said in ("delisted_in_window", "left out", "biased upward", "ew_minus_worst_decile"):
        assert said in wording, said
    assert not any(word in wording.lower() for word in FORBIDDEN_WORDS)


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
    "g0_is_one_preregistered_read_of_2022_2023",
    "spent_window_2024_01_02_to_2026_09_24",
    "paper_equity_is_not_evidence",
    "t8_expected_numbers_are_optimistic",
    "ic_2021",
    "h5_ic_dispersion_unmeasured",
    "unlabelled_names_left_out",
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
    power: SurvivorForwardPowerPlan | None = None,
    **changes: object,
) -> dict[str, Any]:
    """The document of `outcomes` (by horizon) under the test protocol with `changes`, the
    sample drawn through the session of ordinal `through` at every horizon, and the power plan
    the registration carries (`None`: none)."""
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
        power=power,
        integrity=INTEGRITY,
    )


def codes_of(document: Mapping[str, Any]) -> list[str]:
    return [item["code"] for item in document["cannot_be_evaluated_yet"]["items"]]


def verdict_words(document: Mapping[str, Any]) -> list[str]:
    """Every `FORBIDDEN_WORDS` word the document's JSON or its printed lines say, as a word: the
    check's own scope names (`recompute_failed`, `no_provenance`) are vocabulary, not verdicts."""
    text = json.dumps(document, sort_keys=True) + "\n".join(survivor_forward_lines(document))
    return re.findall(rf"\b({'|'.join(FORBIDDEN_WORDS)})\b", text, flags=re.IGNORECASE)


def test_before_the_primary_window_the_document_counts_describes_and_tests_nothing() -> None:
    """Twenty-five mature daily records and a window of three: at h=20 two of the window's
    positions have matured (1 and 21), so nothing is tested -- "not tested: 2 of 3". At h=5 the
    window is twelve positions over the same 60 sessions and five have matured: no test there
    either, no correction of the secondary family, no p-value anywhere and no verdict word."""
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
    assert (twenty["window_positions"], twenty["window_span_sessions"]) == (3, 60)
    assert twenty["window_sessions"] == [day(1).isoformat(), day(21).isoformat()]
    assert twenty["in_window"] == {
        "periods": 2,
        "pending": 0,
        "no_record": 0,
        "refused": 0,
        "unchecked": 0,
        "unverifiable": 0,
    }
    five = document["prediction_layer"]["h5"]["non_overlapping"]
    assert (five["matured"], five["window_positions"], five["window_satisfied"]) == (5, 12, False)
    assert (five["test"], five["status"]) == (
        None,
        "not tested: 5 of 12 pre-registered periods matured",
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
    for name in (
        "protocol",
        "records",
        "prediction_layer",
        "paper_ledger",
        "relative",
        "cannot_be_evaluated_yet",
    ):
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


def test_a_secondary_whose_window_matures_first_still_waits_for_the_primary_window() -> None:
    """h=7 against h=20 with K = 3: the secondary's window is floor(60 / 7) = 8 positions, the
    last (ordinal 50) exiting at ordinal 58, four sessions before the primary's. Here its eight
    have matured and the primary's third has not: the secondary is tested only in the one
    analysis, at the primary window -- no test and no p-value yet."""
    outcomes = {
        20: every_session(41, immature=frozenset({41})),
        7: tuple(made(o, horizon=7) for o in range(1, 51)),
    }

    document = reported(outcomes, checked_by(outcomes[20]), through=50, secondary_horizons=[7])

    seven = document["prediction_layer"]["h7"]["non_overlapping"]
    assert (seven["matured"], seven["window_positions"], seven["window_satisfied"]) == (
        8,
        8,
        True,
    )
    assert seven["test"] is None
    assert seven["status"] == (
        "not tested: a secondary is tested in the one analysis at the primary window "
        "(2 of 3 primary periods matured)"
    )
    assert "p_value" not in json.dumps(document)


def test_at_the_primary_window_the_secondary_family_is_corrected_by_benjamini_yekutieli() -> None:
    """A window of twelve two-session periods, primary h=2 and a secondary h=3: every metric is
    tested once, exactly -- at h=2 over the window's twelve positions, at h=3 over the eight
    that fit the same 24 sessions. A constant positive excess has the smallest one-sided p
    twelve sign flips allow, 1/4096 (eight allow 1/256); a constant negative one -- a worst
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
    assert two["status"] == f"tested on 12 of 12 positions ({NO_GAP})"
    tests = {test["metric"]: test for test in two["test"]}
    assert (tests["survivors_minus_ew"]["exact"], tests["survivors_minus_ew"]["sign_patterns"]) == (
        True,
        4096,
    )
    assert tests["survivors_minus_ew"]["one_sided_p_value"] == pytest.approx(1 / 4096)
    assert tests["ew_minus_worst_decile"]["one_sided_p_value"] == pytest.approx(1 - 1 / 4096)
    three = document["prediction_layer"]["h3"]["non_overlapping"]
    assert (three["window_positions"], three["test"][0]["values"]) == (8, 8)
    assert three["test"][0]["one_sided_p_value"] == pytest.approx(1 / 256)
    family = document["prediction_layer"]["secondary_family"]
    assert family["family"] == ["h2.survivors_minus_ew", "h2.ew_minus_worst_decile", "h3.rank_ic"]
    assert (family["dependence"], family["q"], family["family_size"]) == ("arbitrary", 0.2, 3)
    corrected = {item["hypothesis_id"]: item for item in family["headline"]["tests"]}
    expected = by_by_hand(
        {name: item["one_sided_p_value"] for name, item in corrected.items()}, family_size=3
    )
    assert expected["h2.survivors_minus_ew"] == pytest.approx(5.5 / 4096)
    assert expected["h2.ew_minus_worst_decile"] == 1.0
    for name, q_value in expected.items():
        assert corrected[name]["q_value"] == pytest.approx(q_value)
    assert [corrected[name]["below_q"] for name in family["family"]] == [True, False, True]
    assert all(
        set(item) == {"hypothesis_id", "one_sided_p_value", "q_value", "below_q"}
        for item in family["headline"]["tests"]
    )
    assert family["headline"]["status"] == "applied over 3 of 3 secondary hypotheses"
    lines = survivor_forward_lines(document)
    assert any(
        line.startswith("    h2.ew_minus_worst_decile: one-sided p ")
        and line.endswith(", q-value 1.000000, q-value <= q: False")
        for line in lines
    )
    assert not any("rejected" in line for line in lines)
    assert "window_not_satisfied" not in codes_of(document)


def test_a_secondary_without_a_p_value_still_counts_in_the_declared_family_size() -> None:
    """Two secondaries are declared -- the survivors over the equal weight at h=2 and the rank IC
    at h=3 -- and the h=3 one has no p-value: no rank IC was measured there. The one p-value
    there is is still corrected as one of two: its q-value is H_2 * 2 * p = 3p, never p."""
    outcomes = {
        2: tuple(made(o, horizon=2) for o in range(1, 35)),
        3: tuple(made(o, horizon=3, rank_ic=None) for o in range(1, 35)),
    }

    document = reported(
        outcomes,
        checked_by(outcomes[2]),
        through=34,
        primary_horizon=2,
        secondary_horizons=[3],
        secondary_metrics=["survivors_minus_ew"],
        window_periods=12,
    )

    family = document["prediction_layer"]["secondary_family"]
    assert (family["family"], family["family_size"]) == (["h2.survivors_minus_ew", "h3.rank_ic"], 2)
    [absent] = document["prediction_layer"]["h3"]["non_overlapping"]["test"]
    assert (absent["metric"], absent["one_sided_p_value"]) == ("rank_ic", None)
    [corrected] = family["headline"]["tests"]
    assert corrected["hypothesis_id"] == "h2.survivors_minus_ew"
    assert corrected["one_sided_p_value"] == pytest.approx(1 / 4096)
    assert corrected["q_value"] == pytest.approx(3 / 4096)
    assert corrected["q_value"] == pytest.approx(
        by_by_hand({"h2.survivors_minus_ew": 1 / 4096}, family_size=2)["h2.survivors_minus_ew"]
    )
    assert family["headline"]["status"] == (
        "applied over 1 of 2 secondary hypotheses; 1 without a p-value count in the family"
    )


def test_a_refused_record_inside_the_tested_sample_compromises_it_and_one_after_does_not() -> None:
    """Decision 9: a refused record whose session is one of the window's positions -- the
    sample the test is made over -- prints the test "compromised (n refused)". One refused after
    the window is a gap of the sample and leaves the test as it is."""
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
        "compromised (1 refused): tested on 2 of 3 positions (0 no record, 1 refused, 0 "
        "unverifiable excluded in the sensitivity)"
    )
    assert sample["test"] is not None
    later = after["prediction_layer"]["h20"]["non_overlapping"]
    assert later["refused_in_sample"] == 0
    assert later["status"] == f"tested on 3 of 3 positions ({NO_GAP})"
    assert {"session": day(81).isoformat(), "reason": "refused"} in later["gaps"]
    in_window = after["records"]["shares"]["in_sample"]
    assert (in_window["checked"], in_window["refused"]) == (3, 0)
    assert after["records"]["shares"]["overall"]["refused"] == 1


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


def test_an_unverifiable_record_is_explained_where_it_is_listed_and_in_the_document() -> None:
    """I8. A normal late-published name change (an ST announcement dated on or before the
    session, published after the evening's read) makes that day's record UNVERIFIABLE; a reader
    takes the word for "contaminated". So the document says what it means -- filed on time, an
    input restated after the evening's instant, what it read no longer re-established, counted in
    the headline, out of the sensitivity reading, not discarded, and no evidence either way of
    what the record carried -- and the lines print it once under the records that need it, each of
    which ends saying it is not discarded. A report without one prints no such line."""
    outcomes = {20: every_session(25), 5: ()}
    verdicts = checked_by(
        outcomes[20],
        prd_0004=(
            "unverifiable",
            "recompute_differs",
            ("namechange:2026 (its rows through 2026-01-09 were restated)",),
        ),
    )

    document = reported(outcomes, verdicts, through=25)
    lines = survivor_forward_lines(document)
    clean = survivor_forward_lines(reported(outcomes, checked_by(outcomes[20]), through=25))

    meaning = forward_module.UNVERIFIABLE_MEANING
    assert document["records"]["unverifiable_meaning"] == meaning
    for words in (
        "filed on time",
        "evening's instant",
        "a name-change announcement dated on or before the session",
        "published after the evening's read",
        "T3",
        "can no longer be re-established",
        "excluded from the sensitivity reading, not discarded",
        "neither",
    ):
        assert words in meaning, words
    [listed] = [line for line in lines if line.startswith("  UNVERIFIABLE prd_0004")]
    assert listed.endswith(
        "-- counted in the headline, excluded from the sensitivity reading, not discarded"
    )
    assert lines[lines.index(listed) + 1] == f"  UNVERIFIABLE means: {meaning}"
    assert not any(line.startswith("  UNVERIFIABLE means:") for line in clean)


def test_a_tested_mean_below_zero_says_it_is_opposite_to_the_registered_direction() -> None:
    """M10. Every tested metric is oriented so that greater than zero supports the screen, and
    the one-sided p is for that direction; a mean below zero -- an h5 rank IC of -0.379 with a
    one-sided p of 0.996 -- is printed correctly but never said to have the opposite sign. The
    test line now says so, and only for a mean below zero."""
    outcomes = {
        20: tuple(made(o, rank_ic=-0.03 - 0.001 * o) for o in range(1, 82)),
        5: (),
    }

    lines = survivor_forward_lines(reported(outcomes, checked_by(outcomes[20]), through=81))

    clause = forward_module.OPPOSITE_SIGN_CLAUSE
    assert "opposite to the registered direction" in clause
    [ranked] = [line for line in lines if line.startswith("    rank_ic (primary): 3 periods")]
    assert ranked.endswith(f"; {clause}")
    supported = [line for line in lines if line.startswith("    survivors_minus_ew (secondary):")]
    assert supported and not any(clause in line for line in supported)


def test_each_layer_states_its_unverifiable_count_and_the_unlabelled_bias_in_both_readings() -> (
    None
):
    outcomes = {20: every_session(25), 5: ()}
    verdicts = checked_by(
        outcomes[20], prd_0004=("unverifiable", "recompute_differs", ("daily 2026: census moved",))
    )

    document = reported(outcomes, verdicts, through=25)

    twenty = document["prediction_layer"]["h20"]
    wording = forward_module.UNLABELLED_WORDING
    assert twenty["unverifiable"] == 1
    assert twenty["unlabelled_wording"] == wording
    assert twenty["excluding_unverifiable"]["unlabelled_wording"] == wording
    lines = survivor_forward_lines(document)
    assert any(
        line.startswith("  h20 (primary)")
        and line.endswith("UNVERIFIABLE 1, in the headline and excluded in the sensitivity")
        for line in lines
    )
    assert f"    unlabelled names: {wording}" in lines
    assert f"  - {wording}" in lines


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
    assert empty["cannot_be_evaluated_yet"]["wording"] == SURVIVOR_WORDING
    statements = {
        item["code"]: item["statement"] for item in empty["cannot_be_evaluated_yet"]["items"]
    }
    assert statements["no_survivor_record_yet"] == (
        "no survivor record has been filed under this registration yet"
    )
    assert statements["unlabelled_names_left_out"] == forward_module.UNLABELLED_WORDING
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
            power=None,
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
        "compromised (2 refused): tested on 10 of 12 positions"
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


# --- the power section (`V2-P7-018`) -----------------------------------------------------------
#
# `survivor_forward_power` is a settings block beside the protocol: the pre-registered design the
# power is stated for. The power is always the plan's -- a planning sd, a test level, a true mean
# the planner chose to ask about -- and the sd a window shows is described after its test, never
# fed back in. The numbers below are the route's hand arithmetic (sd 0.15, one-sided 0.05).

POWER_BLOCK: Final[Mapping[str, Any]] = {
    "schema_version": "survivor-forward-power/v1",
    "test_level": 0.05,
    "target_power": 0.8,
    "critical_value": "normal",
    "assumed_lag1_autocorrelation": 0.0,
    "minimum_periods": 2,
    "planned": [
        {
            "horizon": 20,
            "metric": "rank_ic",
            "planning_sd": 0.15,
            "planning_means": [0.037, 0.05, 0.1],
            "sd_source": "test value",
        },
        {
            "horizon": 20,
            "metric": "ew_minus_worst_decile",
            "planning_sd": 0.03,
            "planning_means": [0.01],
            "sd_source": "test value",
        },
    ],
}
"""Test values only (module docstring): not the V2-P7-006 numbers."""


def power_plan(
    read: SurvivorForwardProtocol | None = None, **changes: object
) -> SurvivorForwardPowerPlan:
    plan = read_forward_power(
        {SURVIVOR_FORWARD_POWER_KEY: {**POWER_BLOCK, **changes}},
        protocol=protocol() if read is None else read,
    )
    assert plan is not None
    return plan


def test_a_registration_without_a_power_block_has_no_plan_and_one_with_a_bad_block_is_refused() -> (
    None
):
    assert read_forward_power({}, protocol=protocol()) is None
    for changes, said in (
        ({"extra": 1}, "survivor_forward_power"),
        ({"critical_value": "z"}, "critical_value"),
        ({"minimum_periods": 4}, "minimum_periods"),
        ({"planned": [POWER_BLOCK["planned"][1]]}, "primary"),
        ({"planned": [*POWER_BLOCK["planned"], POWER_BLOCK["planned"][0]]}, "repeat"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "metric": "top_minus_bottom"}]}, "not tested"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "horizon": 7}]}, "horizon 7"),
    ):
        with pytest.raises(SurvivorForwardError, match=said):
            power_plan(**changes)


def test_the_power_plan_has_no_default_and_is_closed_and_frozen() -> None:
    """The plan is frozen at the checkpoint like the protocol: a default here would be a design
    chosen by code."""
    for model in (SurvivorForwardPowerPlan, PlannedMetric):
        assert all(field.is_required() for field in model.model_fields.values())
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("frozen") is True
    assert set(SurvivorForwardPowerPlan.model_fields) == set(POWER_BLOCK)
    assert SURVIVOR_FORWARD_POWER_KEY == "survivor_forward_power"
    assert (
        power_plan().schema_version == SURVIVOR_FORWARD_POWER_SCHEMA == "survivor-forward-power/v1"
    )


@pytest.mark.parametrize("key", sorted(POWER_BLOCK))
def test_a_power_block_missing_any_key_is_refused_by_name(key: str) -> None:
    block = {name: value for name, value in POWER_BLOCK.items() if name != key}

    with pytest.raises(SurvivorForwardError, match=key):
        read_forward_power({SURVIVOR_FORWARD_POWER_KEY: block}, protocol=protocol())


@pytest.mark.parametrize("key", sorted(POWER_BLOCK["planned"][0]))
def test_a_planned_metric_missing_any_key_is_refused_by_name(key: str) -> None:
    item = {name: value for name, value in POWER_BLOCK["planned"][0].items() if name != key}

    with pytest.raises(SurvivorForwardError, match=key):
        power_plan(planned=[item, POWER_BLOCK["planned"][1]])


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"schema_version": "survivor-forward-power/v2"}, "schema_version"),
        ({"test_level": 0.5}, "test_level"),
        ({"test_level": 0.0}, "test_level"),
        ({"target_power": 1.0}, "target_power"),
        ({"target_power": 0.0}, "target_power"),
        ({"assumed_lag1_autocorrelation": 1.0}, "assumed_lag1_autocorrelation"),
        ({"assumed_lag1_autocorrelation": -1.0}, "assumed_lag1_autocorrelation"),
        ({"assumed_lag1_autocorrelation": float("nan")}, "assumed_lag1_autocorrelation"),
        ({"minimum_periods": 1}, "minimum_periods"),
        ({"minimum_periods": True}, "minimum_periods"),
        ({"planned": []}, "planned"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "planning_sd": 0.0}]}, "planning_sd"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "planning_sd": float("inf")}]}, "planning_sd"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "planning_means": []}]}, "planning_means"),
        (
            {"planned": [{**POWER_BLOCK["planned"][0], "planning_means": [0.05, 0.0]}]},
            "planning_means",
        ),
        ({"planned": [{**POWER_BLOCK["planned"][0], "planning_means": [-0.05]}]}, "planning_means"),
        ({"planned": [{**POWER_BLOCK["planned"][0], "sd_source": "  "}]}, "sd_source"),
    ],
)
def test_a_power_block_with_a_value_out_of_range_is_refused_by_name(
    changes: Mapping[str, object], said: str
) -> None:
    with pytest.raises(SurvivorForwardError, match=said):
        power_plan(**changes)


@pytest.mark.parametrize("block", [[1, 2], None, "plan"])
def test_a_power_block_that_is_not_an_object_is_refused_by_name(block: object) -> None:
    with pytest.raises(SurvivorForwardError, match="survivor_forward_power"):
        read_forward_power({SURVIVOR_FORWARD_POWER_KEY: block}, protocol=protocol())


def test_the_power_section_states_the_planned_power_at_k_as_hand_computed() -> None:
    """K = 36 at sd 0.15, one-sided 0.05, normal: 0.435, 0.639, 0.991; the mean reaching 0.8 is
    0.0622; 0.8 needs 102, 56 and 14 periods (the route's arithmetic)."""
    read = protocol(window_periods=36)
    document = reported(
        {20: (), 5: ()},
        {},
        through=1,
        power=power_plan(read, minimum_periods=30),
        window_periods=36,
    )

    power = document["power"]
    assert (power["wording"], power["status"]) == (
        SURVIVOR_WORDING,
        "pre-registered (survivor-forward-power/v1)",
    )
    assert (power["basis"], power["reading_rule"]) == (POWER_BASIS, NOT_DETECTABLE_WORDING)
    primary = power["rows"][0]
    assert (
        primary["horizon"],
        primary["metric"],
        primary["role"],
        primary["planned_periods"],
    ) == (20, "rank_ic", "primary", 36)
    assert [row["power"] for row in primary["planned"]] == pytest.approx(
        [0.43452959661329793, 0.6387600313123356, 0.9907422946265046], abs=1e-12
    )
    assert primary["smallest_mean_at_target"] == pytest.approx(0.062161871513109646, abs=1e-12)
    assert [row["periods"] for row in primary["periods_for_target"]] == [102, 56, 14]


def test_the_t_convention_is_computed_as_registered() -> None:
    read = protocol(window_periods=36)
    document = reported(
        {20: (), 5: ()},
        {},
        through=1,
        power=power_plan(read, critical_value="t", minimum_periods=30),
        window_periods=36,
    )

    primary = document["power"]["rows"][0]
    assert [row["power"] for row in primary["planned"][:2]] == pytest.approx(
        [0.417001, 0.621882], abs=1e-6
    )
    assert primary["periods_for_target"][1] == {"mean": 0.05, "periods": 57}


def test_a_secondary_row_is_powered_at_the_first_benjamini_yekutieli_step() -> None:
    """Family {h20 survivors_minus_ew, h20 ew_minus_worst_decile, h5 rank_ic}: m = 3, q = 0.2, so
    the level is 0.2 / (3 * (1 + 1/2 + 1/3)) = 0.2 / 5.5 = 0.036364 (z = 1.79454). At K = 3, sd
    0.03, a true mean of 0.01: power Phi(sqrt(3) / 3 - 1.79454) = 0.111766; the mean reaching 0.8
    is (1.79454 + 0.84162) * 0.03 / sqrt(3) = 0.0456596; 0.8 needs ceil((2.63616 * 3) ** 2) = 63
    periods. At the plan's own 0.05 the last two would be 0.0430670 and 56 -- each figure of the
    row is at the family's level, not only the first."""
    document = reported({20: (), 5: ()}, {}, through=1, power=power_plan())

    secondary = document["power"]["rows"][1]
    assert secondary["role"] == "secondary"
    assert document["prediction_layer"]["secondary_family"]["family_size"] == 3
    assert secondary["test_level"] == pytest.approx(0.2 / 5.5, abs=1e-15)
    assert "lower bound under Benjamini-Yekutieli" in secondary["test_level_basis"]
    assert secondary["planned"][0]["power"] == pytest.approx(0.111766, abs=1e-6)
    assert secondary["periods_for_target"] == [{"mean": 0.01, "periods": 63}]
    assert secondary["smallest_mean_at_target"] == pytest.approx(0.0456596245, abs=1e-9)


def test_a_gap_reduces_the_usable_periods_and_the_power_and_is_held_to_the_minimum() -> None:
    """Position 2 of 3 (ordinal 21) has no record: n = 2 of 3, below a minimum of 3."""
    outcomes = every_session(61, skip=frozenset({21}))
    document = reported(
        {20: outcomes, 5: ()},
        checked_by(outcomes),
        through=61,
        power=power_plan(minimum_periods=3),
    )

    window = document["power"]["rows"][0]["headline"]
    assert (window["positions"], window["gaps"], window["usable_at_most"]) == (3, 1, 2)
    assert (window["tested"], window["n"], window["n_basis"]) == (2, 2, "tested")
    # Phi(sqrt(2) * mean / 0.15 - 1.644854) at the three planned means, by hand
    assert [row["power"] for row in window["at_n"]] == pytest.approx(
        [0.097485, 0.120308, 0.241326], abs=1e-6
    )
    assert (window["minimum_periods"], window["below_minimum"]) == (3, True)


def test_an_unverifiable_record_is_a_gap_of_the_sensitivity_window_and_not_of_the_headline() -> (
    None
):
    """Ordinal 41 is UNVERIFIABLE: the headline keeps its period (P6's convention) and the
    sensitivity turns it into a gap, so the two windows are about the same positions and differ
    by one usable period."""
    outcomes = every_session(61)
    verdicts = checked_by(outcomes, prd_0041=("unverifiable", "recompute_differs", ("x",)))
    document = reported(
        {20: outcomes, 5: ()}, verdicts, through=61, power=power_plan(minimum_periods=3)
    )

    row = document["power"]["rows"][0]
    headline, sensitivity = row["headline"], row["excluding_unverifiable"]
    assert (headline["positions"], headline["gaps"], headline["usable_at_most"]) == (3, 0, 3)
    assert (headline["tested"], headline["n"], headline["below_minimum"]) == (3, 3, False)
    assert (sensitivity["positions"], sensitivity["gaps"]) == (3, 1)
    assert (sensitivity["usable_at_most"], sensitivity["tested"], sensitivity["n"]) == (2, 2, 2)
    assert sensitivity["below_minimum"] is True


def test_a_refused_and_an_unchecked_record_are_gaps_of_both_windows() -> None:
    outcomes = every_session(61)
    verdicts = checked_by(outcomes, prd_0021=("refused", "no_provenance", ()))
    del verdicts["prd_0041"]
    document = reported({20: outcomes, 5: ()}, verdicts, through=61, power=power_plan())

    row = document["power"]["rows"][0]
    for name in ("headline", "excluding_unverifiable"):
        assert (row[name]["positions"], row[name]["gaps"], row[name]["usable_at_most"]) == (3, 2, 1)
        assert (row[name]["tested"], row[name]["n"]) == (1, 1)
        assert row[name]["at_n"] is None


def test_the_observed_sd_is_described_and_never_used_for_power() -> None:
    """After the window: the rank ICs of ordinals 1 and 41 are -0.01 and 0.04; before it, nothing
    is observed. The planned and the window powers are the plan's whatever was observed."""
    before = reported(
        {20: every_session(25), 5: ()},
        checked_by(every_session(25)),
        through=25,
        power=power_plan(),
    )
    observed_before = before["power"]["rows"][0]["headline"]["observed"]
    assert (observed_before["sd"], before["power"]["rows"][0]["headline"]["n_basis"]) == (
        None,
        "usable_at_most",
    )

    outcomes = every_session(61, skip=frozenset({21}))
    after = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=61, power=power_plan())
    observed = after["power"]["rows"][0]["headline"]["observed"]
    assert observed["wording"] == OBSERVED_WORDING
    assert observed["sd"] == pytest.approx(statistics.stdev([-0.01, 0.04]), abs=1e-15)
    assert observed["lag1_autocorrelation"] is None
    assert after["power"]["rows"][0]["planned"] == before["power"]["rows"][0]["planned"]


def test_an_observed_lag1_autocorrelation_is_described_and_never_enters_the_plan() -> None:
    """The rank ICs of ordinals 1, 21 and 41 are -0.01, -0.02 and 0.04: in hundredths -1, -2, 4,
    mean 1/3, deviations -4/3, -7/3, 11/3; the lag-1 products sum to -49/9 over 186/9. The
    negative observed correlation changes neither the plan's equivalent periods nor its powers."""
    outcomes = every_session(61)
    after = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=61, power=power_plan())
    before = reported({20: (), 5: ()}, {}, through=1, power=power_plan())

    observed = after["power"]["rows"][0]["headline"]["observed"]
    assert observed["lag1_autocorrelation"] == pytest.approx(-49 / 186, abs=1e-12)
    assert observed["sd"] == pytest.approx(statistics.stdev([-0.01, -0.02, 0.04]), abs=1e-15)
    for name in ("planned", "periods_for_target", "autocorrelation", "smallest_mean_at_target"):
        assert after["power"]["rows"][0][name] == before["power"]["rows"][0][name], name


def test_the_serial_correlation_is_the_plans_and_a_negative_one_is_not_credited() -> None:
    """Assumed lag-1 0.5 leaves K (1 - 0.5) / (1 + 0.5) = 12 independent periods of 36: 0.312 at a
    mean of 0.05 and 0.747 at 0.1 (the route's hand figures). A negative assumed correlation
    would credit more independent periods than there are: it is held at 0, and the document says
    so."""
    read = protocol(window_periods=36)
    positive = reported(
        {20: (), 5: ()},
        {},
        through=1,
        power=power_plan(read, assumed_lag1_autocorrelation=0.5, minimum_periods=30),
        window_periods=36,
    )

    serial = positive["power"]["rows"][0]["autocorrelation"]
    assert (serial["assumed_lag1"], serial["lag1_used"]) == (0.5, 0.5)
    assert serial["independent_period_equivalent"] == pytest.approx(12.0, abs=1e-12)
    assert serial["lag1_note"] is None
    assert [item["power"] for item in serial["power_at_equivalent"][1:]] == pytest.approx(
        [0.31201, 0.74683], abs=1e-5
    )

    negative = reported(
        {20: (), 5: ()},
        {},
        through=1,
        power=power_plan(read, assumed_lag1_autocorrelation=-0.5, minimum_periods=30),
        window_periods=36,
    )

    held = negative["power"]["rows"][0]["autocorrelation"]
    assert (held["assumed_lag1"], held["lag1_used"]) == (-0.5, 0.0)
    assert held["independent_period_equivalent"] == 36
    assert "not credited" in held["lag1_note"]
    assert held["power_at_equivalent"] == negative["power"]["rows"][0]["planned"]


def test_a_question_the_design_cannot_answer_is_a_named_absence_not_a_traceback() -> None:
    """A mean needing more than 100,000 periods, and an assumed lag-1 that leaves fewer than two
    independent periods: the module refuses both, the section says so and states the rest."""
    document = reported(
        {20: (), 5: ()},
        {},
        through=1,
        power=power_plan(
            assumed_lag1_autocorrelation=0.99,
            planned=[{**POWER_BLOCK["planned"][0], "planning_means": [1e-9, 0.05]}],
        ),
    )

    row = document["power"]["rows"][0]
    assert [item["periods"] for item in row["periods_for_target"]] == [None, 56]
    assert row["planned"][1]["power"] is not None
    assert all(item["power"] is None for item in row["autocorrelation"]["power_at_equivalent"])
    assert row["autocorrelation"]["independent_period_equivalent"] < 2
    assert any("100000" in absence for absence in row["absences"])
    assert any("at least 2" in absence for absence in row["absences"])
    assert verdict_words(document) == []
    assert any("not stated" in line for line in survivor_forward_lines(document))


def test_a_secondary_horizon_with_one_position_cannot_be_powered_and_says_so() -> None:
    """The primary window spans 60 sessions: a secondary horizon of 60 has one position, and a
    t or normal test needs at least two periods."""
    read = protocol(secondary_horizons=[60])
    secondary = {**POWER_BLOCK["planned"][0], "horizon": 60, "planning_means": [0.05]}
    plan = power_plan(read, planned=[POWER_BLOCK["planned"][0], secondary])
    document = reported({20: (), 60: ()}, {}, through=1, power=plan, secondary_horizons=[60])

    row = document["power"]["rows"][1]
    assert (row["horizon"], row["role"], row["planned_periods"]) == (60, "secondary", 1)
    assert row["planned"] == [{"mean": 0.05, "power": None}]
    assert row["smallest_mean_at_target"] is None
    assert row["headline"]["at_n"] is None
    assert any("at least 2" in absence for absence in row["absences"])
    assert survivor_forward_lines(document)


def test_without_a_power_plan_the_section_says_power_cannot_be_stated() -> None:
    document = reported({20: (), 5: ()}, {}, through=1)

    assert document["power"] == {
        "wording": SURVIVOR_WORDING,
        "status": POWER_NOT_PRE_REGISTERED,
        "basis": POWER_BASIS,
        "reading_rule": NOT_DETECTABLE_WORDING,
        "plan": None,
        "rows": [],
    }
    assert any(POWER_NOT_PRE_REGISTERED in line for line in survivor_forward_lines(document))


def test_a_plan_read_against_another_protocol_is_refused_by_the_document_too() -> None:
    """The plan plans h20 `ew_minus_worst_decile`, a secondary of the default protocol; a
    document under a protocol that does not test it is not this plan's."""
    plan = power_plan()

    with pytest.raises(SurvivorForwardError, match="ew_minus_worst_decile"):
        reported(
            {20: (), 5: ()}, {}, through=1, power=plan, secondary_metrics=["survivors_minus_ew"]
        )


def test_a_plan_for_a_horizon_the_protocol_does_not_read_is_refused_not_a_key_error() -> None:
    """The plan was read under a protocol with a secondary horizon of 7 and plans it; the document
    is built under the default protocol, whose secondary horizon is 5: a refusal by name, not the
    bare `KeyError: 7` of a layer the document does not hold."""
    planned = [POWER_BLOCK["planned"][0], {**POWER_BLOCK["planned"][0], "horizon": 7}]
    plan = power_plan(protocol(secondary_horizons=[7]), planned=planned)

    with pytest.raises(SurvivorForwardError, match="horizon 7"):
        reported({20: (), 5: ()}, {}, through=1, power=plan)


def test_the_document_holds_a_plan_to_its_own_protocol_primary_and_minimum_too() -> None:
    """A plan read under one protocol and handed to a document of another is checked again: the
    document's primary test must be planned, and the minimum cannot exceed its primary window."""
    unplanned = power_plan()
    with pytest.raises(SurvivorForwardError, match="primary"):
        reported(
            {20: (), 5: ()},
            {},
            through=1,
            power=unplanned,
            primary_metric="survivors_minus_ew",
            secondary_metrics=["rank_ic", "ew_minus_worst_decile"],
        )

    demanding = power_plan(protocol(window_periods=36), minimum_periods=30)
    with pytest.raises(SurvivorForwardError, match="minimum_periods"):
        reported({20: (), 5: ()}, {}, through=1, power=demanding)


def test_the_power_lines_carry_the_reading_rule_and_no_verdict_word() -> None:
    outcomes = every_session(61)
    document = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=61, power=power_plan())

    lines = survivor_forward_lines(document)
    assert any(line.startswith("power (survivor-forward-power/v1") for line in lines)
    assert any(NOT_DETECTABLE_WORDING in line for line in lines)
    assert verdict_words(document) == []


READING_RULE_PENDING: Final[str] = "  reading rule: printed once the primary window is satisfied"


def test_the_power_lines_sit_after_the_secondary_family_and_before_the_paper_ledger() -> None:
    outcomes = every_session(25)
    document = reported({20: outcomes, 5: ()}, checked_by(outcomes), through=25, power=power_plan())
    lines = survivor_forward_lines(document)

    def first(prefix: str) -> int:
        return next(index for index, line in enumerate(lines) if line.startswith(prefix))

    assert first("  secondary family") < first("power (") < first("paper ledger")
    assert lines[first("paper ledger") - 1] == READING_RULE_PENDING
    assert survivor_forward_lines(json.loads(json.dumps(document, sort_keys=True))) == lines
    text = "\n".join(lines[first("power (") : first("paper ledger")])
    assert "test level" in text and "minimum periods" in text


def test_a_plan_prints_no_p_value_wording_before_the_window_and_the_reading_rule_after_it() -> None:
    """The reading rule says what a one-sided p-value above the test level means. Before the
    pre-registered window no one-sided p is printed anywhere -- the guard every other pre-window
    report keeps, with a plan as without one -- so the rule waits, and a short neutral line says
    that it does. Once the primary window is satisfied the block ends with the rule."""
    before_outcomes = every_session(25)
    before = reported(
        {20: before_outcomes, 5: ()},
        checked_by(before_outcomes),
        through=25,
        power=power_plan(),
    )
    before_lines = survivor_forward_lines(before)

    assert before["protocol"]["window_satisfied"] is False
    assert not any("one-sided p" in line for line in before_lines)
    assert not any(NOT_DETECTABLE_WORDING in line for line in before_lines)
    assert READING_RULE_PENDING in before_lines
    for word in ("p-value", "one-sided p", "significan", "detect"):
        assert word not in READING_RULE_PENDING
    assert "p_value" not in json.dumps(before)
    assert verdict_words(before) == []

    after_outcomes = every_session(61)
    after = reported(
        {20: after_outcomes, 5: ()},
        checked_by(after_outcomes),
        through=61,
        power=power_plan(),
    )
    after_lines = survivor_forward_lines(after)

    assert after["protocol"]["window_satisfied"] is True
    assert READING_RULE_PENDING not in after_lines
    end = next(index for index, line in enumerate(after_lines) if line.startswith("paper ledger"))
    assert after_lines[end - 1] == f"  {NOT_DETECTABLE_WORDING}"
    assert verdict_words(after) == []


def test_a_secondary_window_that_matured_first_is_not_counted_as_tested_in_the_power_section() -> (
    None
):
    """h=7 against h=20 with K = 3: the secondary's eight positions have matured and the
    primary's third has not, so the document prints no test for it (one analysis, at the primary
    window): its `n` is the most the window can still have, not a tested count, and nothing of it
    is observed."""
    read = protocol(secondary_horizons=[7])
    planned = [POWER_BLOCK["planned"][0], {**POWER_BLOCK["planned"][0], "horizon": 7}]
    outcomes = {
        20: every_session(41, immature=frozenset({41})),
        7: tuple(made(o, horizon=7) for o in range(1, 51)),
    }

    document = reported(
        outcomes,
        checked_by(outcomes[20]),
        through=50,
        secondary_horizons=[7],
        power=power_plan(read, planned=planned),
    )

    window = document["power"]["rows"][1]["headline"]
    assert (window["positions"], window["matured"]) == (8, 8)
    assert (window["tested"], window["n_basis"]) == (None, "usable_at_most")
    assert window["n"] == window["usable_at_most"] == 8 - window["gaps"]
    assert window["observed"]["sd"] is None
    assert document["prediction_layer"]["h7"]["non_overlapping"]["test"] is None


def test_the_g0_item_states_the_gate_the_2026_11_01_route_adopted() -> None:
    """G-0 (spec 5.2, rewritten 2026-11-01) is one pre-registered, read-once reading of
    2022-01-04..2023-12-29 against a stop rule written before it: a first out-of-sample reading,
    never confirmatory evidence. The item every report prints no longer says it waits for
    "conclusions on the research and validation windows"."""
    document = reported({20: (), 5: ()}, {}, through=0)
    items = document["cannot_be_evaluated_yet"]["items"]
    statements = {item["code"]: item["statement"] for item in items}

    statement = statements["g0_is_one_preregistered_read_of_2022_2023"]
    assert "2022-01-04..2023-12-29" in statement
    assert "V2-P7-024" in statement
    assert "never confirmatory evidence" in statement
    assert "g0_needs_research_and_validation_conclusions" not in statements
    assert not any("research and validation windows" in text for text in statements.values())
    assert f"  - {statement}" in survivor_forward_lines(document)
    assert verdict_words(document) == []
