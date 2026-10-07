"""`V2-P4-022`: a synthetic corpus whose information coefficient is known in closed form.

Every corpus in this repository before this one is deterministic arithmetic. `V2-P4-013`'s
`tests/walk_forward_fixtures.py` plants a leak and says so; `V2-P4-014`'s and `V2-P4-015`'s are
rotations of a spread over `[-1, 1]`; `scripts/generate_replay_corpus.py` is 300 hand-written
event payloads. None of them has a **noise model**, so none of them has a known IC, and a
statistic measured on one of them is a property of a construction rather than an estimate of
anything. This module is the other kind.

## What is planted, and what the closed form is

One draw serves both arms. For every prediction day and every security three independent standard
normals are drawn -- `signal`, `decoy`, `noise` -- and the realized return is

    target = RETURN_SCALE * (beta * signal + noise)

with `beta = ALPHA_BETA` for the known-alpha arm and **`beta = 0.0` for the known-null arm and
nothing else changed**. That is what makes the null a *control* rather than a second corpus: the
features are the same numbers, the noise is the same numbers, and the only difference between the
two panels is one multiplication.

`signal` and `target` are then jointly normal, so the population Pearson correlation is
`beta / sqrt(beta**2 + 1)` and the population **rank** correlation follows from Pearson's own
identity for the bivariate normal, `rho_s = (6/pi) * asin(rho/2)` -- which is `known_rank_ic`
below. `RETURN_SCALE` cancels out of both: a correlation is scale-free, and the scale is here only
to keep a session's return inside a plausible band.

`decoy` reaches no target at all. It is the second column, and it is the reason this corpus can
answer the question in the next section.

## What this corpus can separate

- **The known-alpha arm from the known-null arm.** The null arm's measured `mean_rank_ic` is a
  draw from a distribution centred on zero, and the alpha arm's is not.
- **A fitted model from an unfitted one**, which is the property the model-face product acceptance
  measured that a *one-feature* corpus cannot have: with a single column the score is a monotone
  image of that column's ranks up to a learned sign, so every rank statistic is invariant to the
  fit -- sweeping the embargo from 0 to 15 moved the training set from 780 examples to 2,640 and
  left `mean_rank_ic` identical to twelve decimal places. Two columns is the smallest corpus on
  which the *coefficients* are observable, so the second column is load-bearing rather than
  decorative and `test_a_one_column_corpus_cannot_tell_a_fitted_model_from_a_flat_one` drives the
  contrast both ways.

## What this corpus cannot separate, and it is not a short list

- **A leaked split from a purged one, by any fold statistic.** `V2-P4-014` measured that a rank IC
  cannot do it -- both readings came out at exactly `-1.0`, because a rank correlation is
  invariant to magnitude and the leak lives in the coefficient. Nothing here changes that, and
  nothing here plants a leak: every target is an independent draw, so a training example that
  survives a purge carries no information about a test day whatever the split.
  **`V2-P4-013` owns that fixture and this one does not compete with it.**

  This bullet used to add that "no two prediction days' windows share a session", and that was
  **wrong** -- `test_two_neighbouring_windows_share_a_session_and_still_carry_independent_targets`
  falsified it. A `1d` window's sessions are `(k + 1, k + 2)`, so neighbours share one endpoint
  and the purge really does remove two prediction days at every fold boundary. What is true, and
  is what the closed form needs, is narrower: a `1d` window realizes the return **of its exit
  session**, no two prediction days share an exit, and the shared entry contributes to neither
  one's number.
- **A realistic alpha from nothing.** `ALPHA_BETA` plants a rank IC near `0.32`, which no
  cross-sectional equity signal has. The reason is measured rather than aesthetic: over
  `PREDICTION_DAY_COUNT` days and `SECURITY_COUNT` names the null arm's own `mean_rank_ic` wanders
  by more than a realistic IC of `0.03` in either direction, so a corpus with a realistic plant
  could not tell its own two arms apart.
  `test_this_corpus_cannot_certify_an_ic_the_size_a_real_signal_would_have` measures that band and
  states the consequence. A corpus that can separate its arms is worth more than one whose numbers
  look plausible, and this one is honest about which it is.
- **Anything about the A-share market.** Every number here is a pseudo-random draw. The calendar is
  weekdays, the universe is sixty synthetic codes, and no price was ever quoted.

## Why it lives under `tests/`

`tests/alpha_model_fixtures.py`, `tests/walk_forward_fixtures.py` and `tests/panel_fixtures.py` are
the three that came before, and this is the fourth. The consequence worth naming is that no
`lint-imports` contract reaches it: `pyproject.toml`'s contracts are rooted at `openalpha_cn`, so
a corpus generator under `tests/` joins none of them on arrival. It is held to them anyway, by
hand: the imports are `domain/` contracts, `backtest/walk_forward.py` for the panel join, and
`math` and `random` from the standard library -- no numeric stack, no panel plane, no store and no
face, which is exactly what the three `backtest/` contracts forbid to a module inside the package.
`scripts/verify_publication.py` is the other constraint, and it is why every panel here is
generated at run time rather than checked in.

`random.Random(SEED)` rather than the module-global `random`: `backtest/event_study.py` seeds its
bootstrap the same way and for the same reason, and `runtime/seeding.py` -- the process-wide hook
-- is not reachable from a fixture.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Final, NamedTuple
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.walk_forward import (
    LabelledCrossSection,
    LabelledPanel,
    labelled_panel,
)
from openalpha_cn.domain.adjustment import (
    AdjustmentHistory,
    FactorObservation,
    build_adjustment_history,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModelDeclaration,
    FeatureCrossSection,
    FeatureRow,
)
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
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

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
EXCHANGE: Final[str] = "SZSE"
PREDICTION_TIME: Final[time] = time(9, 0)

SEED: Final[int] = 20260824
"""The one seed this corpus draws from, named because ADR-0003 requires a probe to name one.

Every number below is reproducible from it, so a measured `mean_rank_ic` in a test is an exact
literal rather than a tolerance -- and the tolerance is reserved for the claim that actually needs
one, which is the distance between the measurement and `known_rank_ic`.
"""

SECURITY_COUNT: Final[int] = 60
"""Wide enough that a permuted cross section changes the fit, which three names is not.

`V2-P4-014` measured `_pearson`'s order sensitivity at three sizes -- a permutation changed the
answer 0/400 times at three names, 190/400 at six and 347/400 at sixty -- and concluded that a
three-name corpus cannot tell a sorted fit from an unsorted one. Sixty is that measurement taken
rather than retaken. It is also what makes a daily rank correlation an estimate rather than a
coin: the standard error of a rank IC over `n` names is about `1/sqrt(n - 1)`, so sixty gives
roughly `0.13` a day and thirty days give roughly `0.024` on the mean.
"""

SECURITIES: Final[tuple[str, ...]] = tuple(
    f"{index:06d}.SZ" for index in range(1, SECURITY_COUNT + 1)
)

PREDICTION_DAY_COUNT: Final[int] = 30
SESSION_COUNT: Final[int] = PREDICTION_DAY_COUNT + 2
"""Two sessions past the last prediction day, which is exactly what a `1d` window needs.

`build_label_window` puts a `1d` window's sessions at `(k + 1, k + 2)` for a prediction day at
session `k`, so the last prediction day reads session `PREDICTION_DAY_COUNT + 1`. One session
fewer and the last day would be unlabelled; one more would be a session nothing reads.
"""

HORIZON: Final[str] = "1d"
"""One session, and the choice is what makes each day's target independently settable.

A `5d` window spans five sessions and consecutive prediction days share four of them, so a corpus
at that horizon cannot give day `k` and day `k + 1` independently drawn returns -- the price path
is shared, which is precisely the property `V2-P4-013` built its fixture *for*. Here the
requirement is the opposite one: `target` has to be an independent draw per `(day, security)` or
the closed form below is not the corpus's IC.

At `1d` a prediction day's window is `(k + 1, k + 2)` and its realized return is the return **of
session `k + 2`** -- the exit. Neighbouring windows therefore *do* share a session, one's exit
being the next one's entry, and this module first claimed otherwise until
`test_two_neighbouring_windows_share_a_session_and_still_carry_independent_targets` measured it.
The shared session contributes to neither number, no two prediction days share an exit, and the
targets are independent. The purge is live all the same and removes two prediction days at every
fold boundary.
"""

DECOY: Final[str] = "decoy_column"
SIGNAL: Final[str] = "signal_column"
FEATURE_IDS: Final[tuple[str, ...]] = (DECOY, SIGNAL)
"""Two columns, strictly increasing, and only the second reaches a target.

`decoy_column` sorts before `signal_column`, which `validate_feature_ids` requires. Two rather
than one because the reported statistics of a one-column corpus are invariant to the fit; two
rather than three because two is the smallest number at which the coefficients are observable and
every column past the second buys nothing this corpus needs.
"""

ALPHA_BETA: Final[float] = 0.35
"""The known-alpha arm's plant, and it is deliberately far larger than any real signal.

`known_rank_ic(ALPHA_BETA)` is about `0.317`. A cross-sectional equity signal with a rank IC of
`0.3` does not exist. The alternative is a corpus whose two arms it cannot tell apart -- see
`test_this_corpus_cannot_certify_an_ic_the_size_a_real_signal_would_have`, which measures the
null arm's own wander and shows a realistic `0.03` falling inside it.
"""

NULL_BETA: Final[float] = 0.0
"""The control: the same draws with the coefficient set to zero, and nothing else changed."""

RETURN_SCALE: Final[float] = 0.01
"""What a standard normal is multiplied by before it is a session's return.

A correlation is scale-free, so this reaches neither arm's IC. It is here so the close path is a
plausible one -- a one-percent daily standard deviation -- and so `label_outcome`'s price-limit
and adjustment checks are exercised on numbers rather than on absurdities.
"""

ENTRY_CLOSE: Final[float] = 10.0
ADJUSTMENT_FACTOR: Final[float] = 100.0
"""A constant 后复权 factor, `tests/walk_forward_fixtures.py`'s reason.

`session_returns` cross-checks the published path against the factor path and refuses a session
where they disagree, so a fixture that moved the factor without moving `pre_close` would fail
inside `label_outcome` for a reason that has nothing to do with a signal.
"""

Draw = tuple[float, float, float]
"""One `(signal, decoy, noise)` triple -- the three draws behind one `(day, security)` cell."""


def known_rank_ic(beta: float) -> float:
    """The population rank IC of `signal` against `beta * signal + noise`, in closed form.

    Both are jointly normal, so Pearson is `beta / sqrt(beta**2 + 1)` and Spearman follows from
    the bivariate normal identity `rho_s = (6 / pi) * asin(rho / 2)`. `RETURN_SCALE` does not
    appear because a correlation is invariant to a positive scaling of either variable.

    This is the number a benchmark exists to be compared against, and it is computed rather than
    measured on purpose: a "known" IC read off the corpus by the same code path under test would
    be a tautology.
    """
    pearson = beta / math.sqrt(beta * beta + 1.0)
    return 6.0 / math.pi * math.asin(pearson / 2.0)


@lru_cache(maxsize=1)
def trading_calendar(*, exchange: str = EXCHANGE) -> TradingCalendar:
    """A weekday calendar wide enough that no window falls off either end."""
    start = date(2026, 1, 1)
    return build_trading_calendar(
        exchange,
        [
            CalendarDay(
                calendar_date=start + timedelta(days=offset),
                is_trading=(start + timedelta(days=offset)).weekday() < 5,
            )
            for offset in range(365)
        ],
    )


@lru_cache(maxsize=1)
def sessions() -> tuple[date, ...]:
    """The corpus's session axis: the calendar's first `SESSION_COUNT` open days."""
    return trading_calendar().trading_days[:SESSION_COUNT]


@lru_cache(maxsize=1)
def prediction_days() -> tuple[date, ...]:
    """The sessions this corpus asks a question on, in time order."""
    return sessions()[:PREDICTION_DAY_COUNT]


def as_of_for(day: date, *, at: time = PREDICTION_TIME) -> datetime:
    """The instant a cross section dated `day` is read at."""
    return datetime.combine(day, at, tzinfo=SHANGHAI)


@lru_cache(maxsize=1)
def draws() -> tuple[tuple[Draw, ...], ...]:
    """Every `(signal, decoy, noise)` triple, indexed `[prediction day][security]`.

    Drawn once for the whole corpus and cached, which is what makes the two arms a controlled
    comparison: `alpha_panel()` and `null_panel()` read the *same* tuple and differ only in the
    coefficient they multiply `signal` by. Two `Random(SEED)` instances consumed in two different
    orders would have produced two corpora whose difference nobody could attribute.

    The draw order is `day`-major and `security`-minor, stated because it is the only thing that
    makes `SEED` a reproduction rather than a decoration: changing `SECURITY_COUNT` changes every
    number after the first row, and changing `PREDICTION_DAY_COUNT` changes none of them.
    """
    generator = random.Random(SEED)
    return tuple(
        tuple(
            (generator.gauss(0.0, 1.0), generator.gauss(0.0, 1.0), generator.gauss(0.0, 1.0))
            for _index in range(SECURITY_COUNT)
        )
        for _day in range(PREDICTION_DAY_COUNT)
    )


def targets(*, beta: float) -> tuple[tuple[float, ...], ...]:
    """One arm's realized returns, indexed `[prediction day][security]`."""
    return tuple(
        tuple(RETURN_SCALE * (beta * signal + noise) for signal, _decoy, noise in row)
        for row in draws()
    )


def close_paths(*, beta: float) -> dict[str, tuple[float, ...]]:
    """One close series per security, laid out so each `1d` window realizes its own target.

    Session `k + 2` carries prediction day `k`'s return, because that is where
    `build_label_window` puts a `1d` window's exit. Session `1` is flat: no prediction day's
    window reads it, so a number there would be one nothing can observe.
    """
    arm = targets(beta=beta)
    paths: dict[str, tuple[float, ...]] = {}
    for index, ts_code in enumerate(SECURITIES):
        series = [ENTRY_CLOSE, ENTRY_CLOSE]
        for day in range(PREDICTION_DAY_COUNT):
            series.append(series[-1] * (1.0 + arm[day][index]))
        paths[ts_code] = tuple(series)
    return paths


def _bars(ts_code: str, path: Sequence[float]) -> dict[date, DailyBar]:
    axis = sessions()
    bars: dict[date, DailyBar] = {}
    for index, day in enumerate(axis):
        close = path[index]
        pre_close = path[index - 1] if index else close
        bars[day] = DailyBar(
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
    return bars


class _PricePlane(NamedTuple):
    """Every per-security input `label_outcome` needs for one arm, built once."""

    bars: dict[str, dict[date, DailyBar]]
    factors: dict[str, AdjustmentHistory]
    limits: dict[str, dict[date, PriceLimit]]
    universe: StockUniverse


@lru_cache(maxsize=2)
def _price_plane(beta: float) -> _PricePlane:
    """Build one arm's whole price plane, cached on the arm.

    Cached because the alternative is what `tests/walk_forward_fixtures.py` does -- rebuild the
    bars, the adjustment history and the limit table inside every `labels_for` call -- and this
    corpus asks for labels sixty securities wide on thirty days, where a four-name fixture's
    arrangement stops being free.
    """
    axis = sessions()
    paths = close_paths(beta=beta)
    return _PricePlane(
        bars={ts_code: _bars(ts_code, paths[ts_code]) for ts_code in SECURITIES},
        factors={
            ts_code: build_adjustment_history(
                ts_code,
                [
                    FactorObservation(
                        ts_code=ts_code, observed_on=session, factor=ADJUSTMENT_FACTOR
                    )
                    for session in axis
                ],
            )
            for ts_code in SECURITIES
        },
        limits={
            ts_code: {
                session: PriceLimit(
                    ts_code=ts_code, trade_date=session, up_limit=10_000.0, down_limit=0.01
                )
                for session in axis
            }
            for ts_code in SECURITIES
        },
        universe=StockUniverse(
            snapshot_date=axis[-1],
            securities=tuple(
                SecurityLifecycle(ts_code=ts_code, exchange=EXCHANGE, listed_on=date(1991, 4, 3))
                for ts_code in SECURITIES
            ),
        ),
    )


def labels_for(day: date, *, beta: float) -> tuple[OutcomeLabel, ...]:
    """Every security's outcome label for one prediction day, off this arm's price path."""
    plane = _price_plane(beta)
    window = build_label_window(
        as_of=as_of_for(day),
        zone=SHANGHAI,
        horizon=parse_horizon(HORIZON),
        calendar=trading_calendar(),
    )
    halts = halt_corpus_for_years({}, years=(sessions()[0].year,))
    return tuple(
        label_outcome(
            window,
            ts_code=ts_code,
            bars=plane.bars[ts_code],
            factors=plane.factors[ts_code],
            limits=plane.limits[ts_code],
            halts=halts,
            universe=plane.universe,
        )
        for ts_code in SECURITIES
    )


def cross_section_for(
    day: date, *, feature_ids: tuple[str, ...] = FEATURE_IDS
) -> FeatureCrossSection:
    """One prediction day's columns. Identical in both arms -- only the labels differ.

    `feature_ids` narrows the corpus to a subset of `FEATURE_IDS`, and the only subset anything
    asks for is `(SIGNAL,)`. It exists so this module's claim that a second column is load-bearing
    can be *driven* rather than asserted in prose: a one-column reading of the same draws is what
    shows the reported statistics going invariant to the fit.

    **The check below refuses one thing and used to refuse three.** A mutation sweep survived a
    mutant that deleted it entirely, and the reason was that two of the three conditions are
    `FeatureCrossSection`'s own: measured, that contract refuses an unsorted list ("not strictly
    increasing") and an empty one ("a model fitted on nothing has no input to vary"). Restating
    them here was one check plus a place for the two to disagree, so they are gone. What the
    contract accepts and this corpus must not is a *name it never drew* -- there is no such
    column in `draws()`, so the alternative is a `KeyError` from the lookup below.
    """
    unknown = set(feature_ids) - set(FEATURE_IDS)
    if unknown:
        raise ValueError(
            f"{sorted(unknown)} is not a column this corpus draws; it holds {list(FEATURE_IDS)}, "
            "and a cross section carrying anything else would be a fixture measuring something "
            "nobody planted"
        )
    index = prediction_days().index(day)
    row = draws()[index]
    columns = {DECOY: 1, SIGNAL: 0}
    return FeatureCrossSection(
        as_of=as_of_for(day),
        feature_ids=feature_ids,
        rows=tuple(
            FeatureRow(
                ts_code=ts_code,
                values=tuple(cell[columns[feature_id]] for feature_id in feature_ids),
            )
            for ts_code, cell in zip(SECURITIES, row, strict=True)
        ),
    )


def panel(
    *,
    beta: float,
    days: Sequence[date] | None = None,
    feature_ids: tuple[str, ...] = FEATURE_IDS,
) -> LabelledPanel:
    """One arm, joined: `beta=ALPHA_BETA` for the plant, `beta=NULL_BETA` for the control."""
    return labelled_panel(
        LabelledCrossSection(
            cross_section=cross_section_for(day, feature_ids=feature_ids),
            labels=labels_for(day, beta=beta),
        )
        for day in (prediction_days() if days is None else days)
    )


@lru_cache(maxsize=4)
def alpha_panel(*, feature_ids: tuple[str, ...] = FEATURE_IDS) -> LabelledPanel:
    """The known-alpha arm."""
    return panel(beta=ALPHA_BETA, feature_ids=feature_ids)


@lru_cache(maxsize=4)
def null_panel(*, feature_ids: tuple[str, ...] = FEATURE_IDS) -> LabelledPanel:
    """The known-null control: the same features, the same noise, and no signal in the target."""
    return panel(beta=NULL_BETA, feature_ids=feature_ids)


def declaration(
    *, name: str = "known_signal_baseline", family: str = "cross_sectional_rank"
) -> AlphaModelDeclaration:
    """A declaration over this corpus's two columns at its own horizon."""
    return AlphaModelDeclaration(
        name=name,
        family=family,
        horizon=HORIZON,
        feature_version="features/v1",
        seed=SEED,
        code_commit="0123456789abcdef",
    )
