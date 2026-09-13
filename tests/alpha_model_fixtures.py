"""Real `OutcomeLabel`s for the `V2-P4-011` contract tests, built the way the pipeline builds them.

`domain/alpha_model.py`'s `TrainingExample` carries a whole `OutcomeLabel` rather than a bare
target float, so a fixture that hand-built one would be exercising the dataclass constructor and
not the contract. Everything here goes through `build_label_window` and `label_outcome` against a
real `TradingCalendar`, a real `AdjustmentHistory`, a real `HaltCorpus` and a real
`StockUniverse` -- which is what makes `TrainingSet.training_cutoff` a measurement of a session
the calendar actually holds, and what lets a test ask for an *unlabelled* window (delete the exit
bar) and get the refusal the real path produces.

Shared here rather than in one test file because `tests/unit/domain/` and
`tests/unit/backtest/` both need it, and `tests/conftest.py`'s own rule sends a fixture needed by
more than one file out of that file. `tests/panel_fixtures.py` is the precedent for a plain
importable helper module rather than a `conftest.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.alpha_model import (
    FEATURE_HYPERPARAMETER,
    REFERENCE_FAMILY,
    FittedSingleFeatureAlphaModel,
    SingleFeatureAlphaModel,
)
from openalpha_cn.domain.adjustment import FactorObservation, build_adjustment_history
from openalpha_cn.domain.alpha_model import (
    AlphaModelDeclaration,
    FeatureCrossSection,
    FeatureRow,
    TrainingExample,
    TrainingSet,
)
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    LabelWindow,
    OutcomeLabel,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.stock_universe import SecurityLifecycle, StockUniverse
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCHANGE = "SZSE"
ENTRY_CLOSE = 10.0
ADJUSTMENT_FACTOR = 100.0
"""A constant 后复权 factor: no ex-rights day inside any fixture window.

Constant on purpose. `WindowReturn` computes the published path and the factor path
independently and `session_returns` refuses a session where they disagree beyond its own
tolerance, so a fixture that moved the factor without moving `pre_close` to match would fail
inside `label_outcome` for a reason that has nothing to do with this contract.
"""


def trading_calendar() -> TradingCalendar:
    """June 2026, weekends closed -- twenty-two sessions, enough for any fixture window."""
    return build_trading_calendar(
        EXCHANGE,
        [
            CalendarDay(
                calendar_date=date(2026, 6, day),
                is_trading=date(2026, 6, day).weekday() < 5,
            )
            for day in range(1, 31)
        ],
    )


def _bar(ts_code: str, day: date, close: float, pre_close: float) -> DailyBar:
    return DailyBar(
        ts_code=ts_code,
        trade_date=day,
        open=close,
        high=max(close, pre_close),
        low=min(close, pre_close),
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1000.0,
        amount=close * 1000.0,
    )


def outcome_label(
    *,
    ts_code: str,
    prediction_day: date,
    target: float,
    horizon: str = "1d",
    halt_the_exit: bool = False,
) -> OutcomeLabel:
    """One real label whose `realized_return` is `target`, or a refused one.

    The closes are laid out so the published path (`close / pre_close` chained over the
    window's sessions after the entry) and the factor path (`close_exit * f / close_entry * f`)
    come out identical: every session's `pre_close` is the previous session's `close`, and the
    factor never moves. `halt_the_exit` deletes the exit session's bar, which is how the real
    pipeline produces an unlabelled window.
    """
    window = build_label_window(
        as_of=datetime.combine(prediction_day, time(9, 0), tzinfo=SHANGHAI),
        zone=SHANGHAI,
        horizon=parse_horizon(horizon),
        calendar=trading_calendar(),
    )
    sessions = window.sessions
    step = (1.0 + target) ** (1.0 / (len(sessions) - 1))
    closes = [ENTRY_CLOSE * step**index for index in range(len(sessions))]
    bars = {
        day: _bar(ts_code, day, closes[index], closes[index - 1] if index else closes[0])
        for index, day in enumerate(sessions)
    }
    if halt_the_exit:
        del bars[window.exit_day]
    return _label(ts_code=ts_code, window=window, bars=bars)


def _label(*, ts_code: str, window: LabelWindow, bars: dict[date, DailyBar]) -> OutcomeLabel:
    return label_outcome(
        window,
        ts_code=ts_code,
        bars=bars,
        factors=build_adjustment_history(
            ts_code,
            [
                FactorObservation(ts_code=ts_code, observed_on=day, factor=ADJUSTMENT_FACTOR)
                for day in window.sessions
            ],
        ),
        limits={
            day: PriceLimit(ts_code=ts_code, trade_date=day, up_limit=1000.0, down_limit=0.01)
            for day in window.sessions
        },
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=StockUniverse(
            snapshot_date=date(2026, 6, 30),
            securities=(
                SecurityLifecycle(ts_code=ts_code, exchange=EXCHANGE, listed_on=date(1991, 4, 3)),
            ),
        ),
    )


def training_example(
    *,
    ts_code: str,
    prediction_day: date,
    features: Sequence[float | None],
    target: float,
    horizon: str = "1d",
) -> TrainingExample:
    """One labelled row: a real outcome, and the feature values that preceded it."""
    return TrainingExample(
        label=outcome_label(
            ts_code=ts_code, prediction_day=prediction_day, target=target, horizon=horizon
        ),
        features=tuple(features),
    )


MOMENTUM = "momentum_20d"
VALUE = "value_ep"
FEATURE_IDS = (MOMENTUM, VALUE)
"""Two features, strictly increasing: `momentum_20d` sorts before `value_ep`."""


SECURITIES = ("000001.SZ", "000002.SZ", "000003.SZ")
FIRST_FOLD = (date(2026, 6, 1), date(2026, 6, 2))
SECOND_FOLD = (date(2026, 6, 3), date(2026, 6, 4))


def training_set(
    *,
    horizon: str = "1d",
    days: Sequence[date] = FIRST_FOLD,
    reverse: bool = False,
    usable_momentum: int | None = None,
    flat_momentum: bool = False,
) -> TrainingSet:
    """Six labelled rows over three securities and two prediction days.

    `momentum_20d` rises across the three securities and so does the target, so the declared
    feature is **positively** related to it and a reference fit learns `sign = +1`. `reverse`
    flips the targets alone, which is what lets a test measure the learned sign moving instead
    of asserting that it does; `usable_momentum` keeps a value on only the first `n` rows,
    which is how a fit with too few usable rows is produced without also changing the labels.
    Zero and one are both needed: a check written `< 1` instead of `< 2` still refuses zero, so
    only the one-row case separates the two. `flat_momentum` gives every row the same value,
    which is a real market shape -- a feature that has not varied over the training window --
    and the one that leaves one side of the reference model's centre split empty.
    """
    momentum = (0.20, 0.20, 0.20) if flat_momentum else (0.10, 0.20, 0.30)
    value = (0.05, 0.04, 0.03)
    targets = (0.02, 0.04, 0.06)
    if reverse:
        targets = tuple(reversed(targets))
    examples = []
    for offset, day in enumerate(days):
        for index, ts_code in enumerate(SECURITIES):
            position = offset * len(SECURITIES) + index
            keeps_value = usable_momentum is None or position < usable_momentum
            examples.append(
                training_example(
                    ts_code=ts_code,
                    prediction_day=day,
                    features=(
                        momentum[index] + (0.0 if flat_momentum else 0.02 * offset)
                        if keeps_value
                        else None,
                        value[index],
                    ),
                    target=targets[index] - 0.01 * offset,
                    horizon=horizon,
                )
            )
    return TrainingSet(feature_ids=FEATURE_IDS, examples=tuple(examples))


def declaration(*, feature_id: str = MOMENTUM, horizon: str = "1d") -> AlphaModelDeclaration:
    """A declaration the reference model answers to."""
    return AlphaModelDeclaration(
        name="reference_momentum",
        family=REFERENCE_FAMILY,
        horizon=horizon,
        feature_version="features/v1",
        seed=7,
        code_commit="0123456789abcdef",
        hyperparameters=((FEATURE_HYPERPARAMETER, feature_id),),
    )


def cross_section(
    *,
    as_of: datetime = datetime(2026, 6, 30, 8, 30, tzinfo=UTC),
    rows: Sequence[tuple[str, tuple[float | None, ...]]] | None = None,
) -> FeatureCrossSection:
    """A feature cross section aligned to `FEATURE_IDS`, dated after the fixture cutoff."""
    offered = (
        rows
        if rows is not None
        else (
            ("000001.SZ", (0.05, 0.05)),
            ("000002.SZ", (0.25, 0.04)),
            ("000003.SZ", (None, 0.03)),
        )
    )
    return FeatureCrossSection(
        as_of=as_of,
        feature_ids=FEATURE_IDS,
        rows=tuple(FeatureRow(ts_code=ts_code, values=values) for ts_code, values in offered),
    )


def fitted_reference() -> FittedSingleFeatureAlphaModel:
    """The reference model, fitted on `training_set()`."""
    return SingleFeatureAlphaModel(declaration=declaration()).fit(training_set())
