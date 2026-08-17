"""The information coefficient (`V2-P3-005`): IC, rank IC, decay and stability.

Four properties this file exists to hold, each of which is a place the whole statistic silently
turns into something else:

1. **The forward return is the label contract's.** `domain/daily_prices.py` measured
   `close[t]/close[t-1] - 1` at `-0.530973%` where the truth is `+2.742230%`, with the sign
   reversed, on `000001.SZ`'s 2026-06-12 ex-dividend session. An IC computed against that path is
   an IC of the wrong sign on exactly the securities that had a corporate action, and nothing
   about the float would say so. `test_the_forward_return_is_the_labels_adjusted_return_and_not
   _the_raw_close_ratio` pins the number this module correlates against to
   `OutcomeLabel.realized_return` with `==`, and against the wrong path with `!=`, on a window
   whose two paths differ by 3.27 percentage points.
2. **A fill never reaches a statistic derived from it.** `V2-P3-003` stored imputed values under
   their own code precisely so a later consumer could decline them, and this is that consumer.
   The test that matters is not that `imputed` is counted -- it is that *including it would have
   moved the answer*, which is what separates a live exclusion from a fixture where either choice
   looks the same.
3. **`direction` reaches the sign, and the fixture separates the two answers.** Two definitions
   differing in nothing but `direction` are run over the same cross sections, and the assertion is
   not that both produce a number: it is that the oriented IC of one is the exact negation of the
   other's and that the stability summaries come out `sign_consistency=1.0` against `0.0`.
4. **Every number this module reports has a fixture that separates it from its neighbour.** The
   census is built so that no two of its seven cells share a value -- `V2-P3-004`'s review found a
   column that was asserted and whose assertion could not tell two answers apart, because the
   fixture made them equal.
"""

from __future__ import annotations

import math
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIER_ORDER,
    IC_COVERAGE_ORDER,
    IC_LIMITATION_CODES,
    IC_METHOD_ORDER,
    KNOWN_IC_LIMITATIONS,
    MAXIMUM_IC_AS_OFS,
    MAXIMUM_IC_SECURITIES,
    MINIMUM_IC_AS_OFS,
    MINIMUM_IC_SECURITIES,
    RAW_COVERAGE_ORDER,
    TIER_ADMITTED_CODES,
    TIER_COVERAGE_ORDER,
    TIER_VALUE_CODES,
    FactorICError,
    FactorICSpec,
    FactorICStudy,
    ICCensus,
    ICDecayRung,
    ICObservationPair,
    ICPoint,
    _pearson,
    _refuse_a_tier_table_that_disagrees_with_its_own_contract,
    average_ranks,
    neutralized_cross_section,
    processed_cross_section,
    raw_cross_section,
)
from openalpha_cn.domain.adjustment import FactorObservation as AdjustmentFactor
from openalpha_cn.domain.adjustment import build_adjustment_history
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.factor import (
    FactorDefinition,
    FactorField,
    FactorObservation,
)
from openalpha_cn.domain.factor_neutralization import NeutralizedFactorObservation
from openalpha_cn.domain.factor_transform import ProcessedFactorObservation
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
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar
from openalpha_cn.panel_factors import FACTOR_COVERAGE_ORDER, _average_ranks

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
AS_OF: Final[datetime] = datetime(2026, 6, 10, 8, 30, tzinfo=UTC)
"""16:30 Asia/Shanghai on 2026-06-10 -- `DAILY_AVAILABILITY_TIME`, so the prediction day is the
10th and the entry is the 11th."""

CALENDAR = build_trading_calendar(
    "SZSE",
    [
        CalendarDay(
            calendar_date=date(2026, 6, 1) + timedelta(days=offset),
            is_trading=(date(2026, 6, 1) + timedelta(days=offset)).weekday() < 5,
        )
        for offset in range(180)
    ],
)

UNIVERSE = StockUniverse(
    snapshot_date=date(2026, 12, 31),
    securities=tuple(
        SecurityLifecycle(ts_code=f"{index:06d}.SZ", exchange="SZSE", listed_on=date(1991, 4, 3))
        for index in range(1, 60)
    ),
)
"""A tuple rather than a generator, which is not a style choice: `StockUniverse.securities` is
iterated once per `status_on` call, so a generator is exhausted by the first security asked
about and every later one raises `'000002.SZ' is not in the registry snapshot`."""


def code(index: int) -> str:
    return f"{index:06d}.SZ"


def _definition(direction: str = "higher_is_better", *, key: str = "probe_ic") -> FactorDefinition:
    """A one-session, session-axis factor. `direction` is the only field the tests vary."""
    return FactorDefinition(
        key=key,
        version=1,
        family="momentum_reversal",
        direction=direction,  # type: ignore[arg-type]
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )


def _spec(
    direction: str = "higher_is_better",
    *,
    method: str = "spearman",
    min_securities: int = 3,
    min_as_ofs: int = 2,
    key: str = "probe_ic",
) -> FactorICSpec:
    return FactorICSpec(
        definition=_definition(direction, key=key),
        method=method,  # type: ignore[arg-type]
        min_securities=min_securities,
        min_as_ofs=min_as_ofs,
    )


def _bar(ts_code: str, day: date, *, close: float, pre_close: float) -> DailyBar:
    return DailyBar(
        ts_code=ts_code,
        trade_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1000.0,
        amount=10000.0,
    )


def _window(*, as_of: datetime = AS_OF, horizon: str = "1d") -> LabelWindow:
    return build_label_window(
        as_of=as_of, zone=SHANGHAI, horizon=parse_horizon(horizon), calendar=CALENDAR
    )


def _label(
    ts_code: str,
    *,
    window: LabelWindow | None = None,
    total_return: float = 0.0,
    unpriced: bool = False,
) -> OutcomeLabel:
    """A real `label_outcome` over a synthetic price path whose cumulative return is chosen.

    Adjustment factors are `1.0` on every session, so `WindowReturn.adjusted` is
    `close_exit / close_entry - 1` exactly, and each bar's `pre_close` is the previous session's
    close, so the published path agrees with it term by term. `unpriced=True` deletes the exit bar
    and produces a genuinely refused label -- the shape `ICCensus.unlabelled_count` counts.
    """
    span = window if window is not None else _window()
    steps = span.session_count
    growth = (1.0 + total_return) ** (1.0 / steps)
    bars: dict[date, DailyBar] = {}
    price = 100.0
    for position, day in enumerate(span.sessions):
        if position == 0:
            bars[day] = _bar(ts_code, day, close=price, pre_close=price)
            continue
        moved = price * growth
        bars[day] = _bar(ts_code, day, close=moved, pre_close=price)
        price = moved
    if unpriced:
        del bars[span.exit_day]
    return label_outcome(
        span,
        ts_code=ts_code,
        bars=bars,
        factors=build_adjustment_history(
            ts_code,
            [
                AdjustmentFactor(ts_code=ts_code, observed_on=day, factor=1.0)
                for day in span.sessions
            ],
        ),
        limits={
            day: PriceLimit(ts_code=ts_code, trade_date=day, up_limit=10_000.0, down_limit=0.01)
            for day in span.sessions
        },
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=UNIVERSE,
    )


def _labels(
    returns: Mapping[str, float], *, window: LabelWindow | None = None
) -> dict[str, OutcomeLabel]:
    return {
        name: _label(name, window=window, total_return=value) for name, value in returns.items()
    }


def _raw(
    subject: str,
    *,
    value: float | None,
    coverage: str = "computed",
    as_of: datetime = AS_OF,
) -> FactorObservation:
    return FactorObservation(
        subject=subject,
        as_of=as_of,
        value=value,
        coverage=coverage,  # type: ignore[arg-type]
        factor_id="fct_probe",
        manifest_id="fmn_probe",
        input_row_count=1 if value is not None else 0,
        input_session_first=date(2026, 6, 10) if value is not None else None,
        input_session_last=date(2026, 6, 10) if value is not None else None,
    )


def _processed(
    subject: str,
    *,
    value: float | None,
    coverage: str = "processed",
    source_coverage: str = "computed",
    as_of: datetime = AS_OF,
) -> ProcessedFactorObservation:
    return ProcessedFactorObservation(
        subject=subject,
        as_of=as_of,
        value=value,
        coverage=coverage,  # type: ignore[arg-type]
        transform_id="ftx_probe",
        transform_manifest_id="ftm_probe",
        source_factor_id="fct_probe",
        source_manifest_id="fmn_probe",
        source_coverage=source_coverage,  # type: ignore[arg-type]
    )


def _neutralized(
    subject: str,
    *,
    value: float | None,
    coverage: str = "neutralized",
    source_coverage: str = "processed",
    industry_code: str | None = "801080",
    as_of: datetime = AS_OF,
) -> NeutralizedFactorObservation:
    return NeutralizedFactorObservation(
        subject=subject,
        as_of=as_of,
        value=value,
        coverage=coverage,  # type: ignore[arg-type]
        neutralization_id="fnz_probe",
        neutralization_manifest_id="fnm_probe",
        source_factor_id="fct_probe",
        source_transform_id="ftx_probe",
        source_transform_manifest_id="ftm_probe",
        source_coverage=source_coverage,  # type: ignore[arg-type]
        industry_code=industry_code,
    )


MONOTONE_SCORES: Final[dict[str, float]] = {code(index): float(index) for index in range(1, 6)}
"""Five securities scored 1..5 -- the score ranks are `1, 2, 3, 4, 5` with no ties."""

NEARLY_MONOTONE_RETURNS: Final[dict[str, float]] = {
    code(1): 0.01,
    code(2): 0.02,
    code(3): 0.03,
    code(4): 0.20,
    code(5): 0.04,
}
"""Return ranks `1, 2, 3, 5, 4` against score ranks `1, 2, 3, 4, 5`: `sum d^2 = 2`, so Spearman's
`1 - 6 * 2 / (5 * 24)` is exactly `0.9`. Hand-computable, and different from `1.0`, so a rank IC
that silently ignored the swap would look wrong rather than right.

**The `0.20` is what makes this fixture able to tell the two methods apart, and the first version
of it could not.** With `0.05` in that slot the returns are an exact affine image of the ranks and
the Pearson comes out `0.9` as well -- so `test_the_rank_ic_is_the_hand_computed_spearman_and_the
_pearson_is_a_different_number` passed while proving nothing about `method`, which is exactly the
defect `V2-P3-004`'s review found one plane down. Moving that one return out to `0.20` leaves
every rank untouched and puts the Pearson at `0.48`; both numbers are hand-checkable and they are
0.42 apart."""

SPEARMAN_OF_THE_FIXTURE: Final[float] = 0.9
PEARSON_OF_THE_FIXTURE: Final[float] = 0.48


def _raw_section(
    scores: Mapping[str, float] = MONOTONE_SCORES,
    returns: Mapping[str, float] | None = None,
    *,
    as_of: datetime = AS_OF,
    window: LabelWindow | None = None,
):
    chosen = NEARLY_MONOTONE_RETURNS if returns is None else returns
    return raw_cross_section(
        as_of=as_of,
        observations=[_raw(name, value=value, as_of=as_of) for name, value in scores.items()],
        labels=_labels(chosen, window=window),
    )


# --------------------------------------------------------------------------------------------
# The two rules this module shares with the transform plane, pinned rather than assumed
# --------------------------------------------------------------------------------------------


def test_the_rank_this_module_assigns_is_the_rank_the_transform_plane_assigns() -> None:
    """`average_ranks` and `panel_factors._average_ranks` are two implementations of one rule.

    They are two because this module is a standard-library leaf and that one sits over the panel
    plane, so a shared helper would put an edge from `backtest/` into DuckDB. Two implementations
    of one rule is exactly the shape this repository pins rather than trusts -- see
    `test_the_engines_period_selection_is_the_domains_filing_for` for the same treatment one plane
    down -- so this drives both over 300 random vectors dense in ties and compares element-wise.

    Ties are the half that matters: an implementation that broke them by index instead of
    averaging would agree on every distinct-valued input and disagree on almost every real cross
    section, where a discretised factor ties constantly.
    """
    generator = random.Random(20260812)
    checked = 0
    tied = 0
    for _case in range(300):
        size = generator.randint(1, 40)
        values = [float(generator.randint(0, 4)) for _index in range(size)]
        assert average_ranks(values) == _average_ranks(values), values
        checked += 1
        tied += len(set(values)) < len(values)

    assert checked == 300
    assert tied > 250, "the corpus has to be dense in ties, or it pins the easy half of the rule"
    assert average_ranks((1.0, 3.0, 3.0, 7.0)) == (1.0, 2.5, 2.5, 4.0)


def test_the_raw_census_order_is_the_engines_census_order() -> None:
    """The raw census's column order is the engine's, derived rather than restated.

    `RAW_COVERAGE_ORDER` is built out of `domain/factor_transform.MISSING_VALUE_COVERAGE_ORDER`
    because this module may not import `panel_factors`. A test may, so the two tuples are held
    equal here -- a report whose census columns were in a different order from the engine's would
    make two renderings of one build impossible to diff.
    """
    assert RAW_COVERAGE_ORDER == FACTOR_COVERAGE_ORDER
    assert TIER_COVERAGE_ORDER["raw"] == FACTOR_COVERAGE_ORDER


def test_a_two_name_cross_section_correlates_perfectly_whatever_the_two_names_did() -> None:
    """Why `MINIMUM_IC_SECURITIES` is 3, measured over random pairs rather than asserted once.

    Two points that tie on neither axis lie on one line, so `|r| = 1` for any pair -- the number
    carries a sign and nothing else. The assertion is on `round(abs(r), 15)` and not on `1.0`
    because that is what the arithmetic actually produces: eight of ten pairs come out
    `0.9999999999999998`, and an identity assertion here would have been a claim the
    implementation falsifies.
    """
    generator = random.Random(4)
    magnitudes = set()
    exact = 0
    for _case in range(200):
        xs = [generator.uniform(-1e6, 1e6) for _index in range(2)]
        ys = [generator.uniform(-1.0, 1.0) for _index in range(2)]
        pearson = _pearson(xs, ys)
        spearman = _pearson(average_ranks(xs), average_ranks(ys))
        magnitudes.add(round(abs(pearson), 15))
        magnitudes.add(round(abs(spearman), 15))
        exact += abs(pearson) == 1.0

    assert magnitudes == {1.0}
    assert 0 < exact < 200, "the last-bit rounding is real in both directions on this corpus"
    assert MINIMUM_IC_SECURITIES == 3


# --------------------------------------------------------------------------------------------
# The forward return, and where it comes from
# --------------------------------------------------------------------------------------------


def test_the_forward_return_is_the_labels_adjusted_return_and_not_the_raw_close_ratio() -> None:
    """The measured hazard, driven on the session that produced it.

    `000001.SZ` on 2026-06-12: `close/pre_close` gives `+2.742230%`, the `adj_factor` path
    `+2.742251%`, and `close[t]/close[t-1]` gives `-0.530973%` -- the sign reversed. This builds a
    label over exactly those two bars and asserts the number this module correlates against is
    the label's own, with `==`, and is not the wrong path, with `!=`. The two differ by 3.27
    percentage points here, so no fixture tolerance could confuse them.
    """
    window = _window()
    entry, exit_day = window.entry_day, window.exit_day
    ex_rights = label_outcome(
        window,
        ts_code=code(1),
        bars={
            entry: _bar(code(1), entry, close=11.30, pre_close=11.32),
            exit_day: _bar(code(1), exit_day, close=11.24, pre_close=10.94),
        },
        factors=build_adjustment_history(
            code(1),
            [
                AdjustmentFactor(ts_code=code(1), observed_on=entry, factor=134.5794),
                AdjustmentFactor(ts_code=code(1), observed_on=exit_day, factor=139.008),
            ],
        ),
        limits={
            day: PriceLimit(ts_code=code(1), trade_date=day, up_limit=1000.0, down_limit=0.01)
            for day in window.sessions
        },
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=UNIVERSE,
    )
    others = {name: _label(name, total_return=0.01) for name in (code(2), code(3), code(4))}

    section = raw_cross_section(
        as_of=AS_OF,
        observations=[_raw(name, value=1.0) for name in (code(1), code(2), code(3), code(4))],
        labels={code(1): ex_rights, **others},
    )
    measured = {pair.subject: pair.forward_return for pair in section.pairs}

    assert measured[code(1)] == ex_rights.realized_return
    assert measured[code(1)] == pytest.approx(0.0274225, abs=1e-7)
    assert measured[code(1)] != pytest.approx(11.24 / 11.30 - 1.0, abs=1e-4)
    wrong_path = 11.24 / 11.30 - 1.0
    assert wrong_path == pytest.approx(-0.0053097, abs=1e-7)
    assert measured[code(1)] - wrong_path == pytest.approx(0.0327, abs=1e-4)


def test_a_refused_label_is_counted_rather_than_read_as_a_flat_return() -> None:
    """A halted, limit-locked or unpriced security contributes nothing and is not a zero.

    Substituting `0.0` would put a return into the correlation that the security did not have,
    and across a heavy day it would make the IC partly a measurement of the halt rate. The count
    is what makes the drop visible: the sample size falls and the census says why.
    """
    labels = _labels(NEARLY_MONOTONE_RETURNS)
    labels[code(5)] = _label(code(5), unpriced=True)

    section = raw_cross_section(
        as_of=AS_OF,
        observations=[_raw(name, value=value) for name, value in MONOTONE_SCORES.items()],
        labels=labels,
    )

    assert labels[code(5)].is_labelled is False
    assert section.census.unlabelled_count == 1
    assert section.census.admitted_count == 4
    assert [pair.subject for pair in section.pairs] == [code(1), code(2), code(3), code(4)]
    assert all(pair.forward_return != 0.0 for pair in section.pairs)


def test_a_label_window_that_is_not_forward_of_the_as_of_is_refused() -> None:
    """A window entering on the `as_of`'s own session prices a move that had already happened.

    The `as_of` here is 16:30 Asia/Shanghai on the 11th and the window still enters on the 11th,
    whose 15:00 close is ninety minutes in the past. The refusal names both dates, so the repair
    -- move the `as_of` back or the window forward -- is readable from the message.
    """
    late = datetime(2026, 6, 11, 8, 30, tzinfo=UTC)

    with pytest.raises(FactorICError, match="scores a move that had already happened"):
        raw_cross_section(
            as_of=late,
            observations=[_raw(name, value=1.0, as_of=late) for name in MONOTONE_SCORES],
            labels=_labels(NEARLY_MONOTONE_RETURNS),
        )


def test_labels_over_two_windows_at_one_as_of_are_refused() -> None:
    """Half a cross section labelled over one session and half over ten is two quantities."""
    labels = _labels(NEARLY_MONOTONE_RETURNS)
    labels[code(5)] = _label(code(5), window=_window(horizon="10d"), total_return=0.04)

    with pytest.raises(FactorICError, match="span 2 different windows"):
        raw_cross_section(
            as_of=AS_OF,
            observations=[_raw(name, value=value) for name, value in MONOTONE_SCORES.items()],
            labels=labels,
        )


def test_a_label_for_a_security_the_cross_section_never_scored_is_refused() -> None:
    """The two sides read from two different universes, which a silent drop would hide."""
    labels = _labels({**NEARLY_MONOTONE_RETURNS, code(9): 0.07})

    with pytest.raises(FactorICError, match=r"\['000009.SZ'\] carries a label and no raw"):
        raw_cross_section(
            as_of=AS_OF,
            observations=[_raw(name, value=value) for name, value in MONOTONE_SCORES.items()],
            labels=labels,
        )


def test_an_observation_stamped_at_another_as_of_is_refused() -> None:
    """A factor value from one day against a forward return from another."""
    observations = [_raw(name, value=value) for name, value in MONOTONE_SCORES.items()]
    observations[2] = _raw(code(3), value=3.0, as_of=datetime(2026, 6, 9, 8, 30, tzinfo=UTC))

    with pytest.raises(FactorICError, match="stamped at another instant"):
        raw_cross_section(
            as_of=AS_OF, observations=observations, labels=_labels(NEARLY_MONOTONE_RETURNS)
        )


def test_one_security_scored_twice_at_one_as_of_is_refused() -> None:
    observations = [_raw(name, value=value) for name, value in MONOTONE_SCORES.items()]
    observations.append(_raw(code(1), value=99.0))

    with pytest.raises(FactorICError, match=r"\['000001.SZ'\] appears more than once"):
        raw_cross_section(
            as_of=AS_OF, observations=observations, labels=_labels(NEARLY_MONOTONE_RETURNS)
        )


def test_an_empty_label_set_is_refused_rather_than_reported_as_a_thin_sample() -> None:
    with pytest.raises(FactorICError, match="no labels were offered"):
        raw_cross_section(as_of=AS_OF, observations=[_raw(code(1), value=1.0)], labels={})


# --------------------------------------------------------------------------------------------
# Which observations enter, and what the census says about the rest
# --------------------------------------------------------------------------------------------


def test_only_the_admitted_codes_reach_the_correlation_and_every_other_is_counted() -> None:
    """The six-code raw vocabulary, with a distinct count on every cell of the census.

    Eight numbers -- five admitted pairs, one unlabelled, two unmatched, three
    `not_in_universe`, four `insufficient_history`, eight `ambiguous_filing`, six
    `input_missing`, seven `undefined_value` -- chosen pairwise distinct on purpose.
    `V2-P3-004`'s review found a column whose assertion could not separate two answers because
    the fixture made them equal; a census where two cells shared a value would let a
    transposition pass. `ambiguous_filing` is `V2-P3-018`'s member and is here at a count of
    eight rather than appended at zero, for that same reason: a cell nothing ever populates is a
    column this test cannot tell from a missing one.

    `sample_size` is asserted here rather than only on the thin and degenerate cross sections,
    and that is a repair rather than belt and braces: on those two fixtures every subject is
    admitted, so `sample_size` and `census.subject_count` are the same number and a
    `sample_size` that reported the census read exactly right. A mutation run found it -- it was
    the one surviving mutant of thirty-five. Here the two are 5 and 28.
    """
    computed = {code(index): float(index) for index in range(1, 9)}
    labels = _labels({code(index): 0.01 * index for index in range(1, 6)})
    labels[code(6)] = _label(code(6), unpriced=True)
    observations = [_raw(name, value=value) for name, value in computed.items()]
    observations += [
        _raw(code(10 + index), value=None, coverage="not_in_universe") for index in range(3)
    ]
    observations += [
        _raw(code(20 + index), value=None, coverage="insufficient_history") for index in range(4)
    ]
    observations += [
        _raw(code(50 + index), value=None, coverage="ambiguous_filing") for index in range(8)
    ]
    observations += [
        _raw(code(30 + index), value=None, coverage="input_missing") for index in range(6)
    ]
    observations += [
        _raw(code(40 + index), value=None, coverage="undefined_value") for index in range(7)
    ]

    section = raw_cross_section(as_of=AS_OF, observations=observations, labels=labels)
    point = FactorICStudy(_spec()).measure(section)
    cells = [
        section.census.admitted_count,
        section.census.unlabelled_count,
        section.census.unmatched_count,
        *(count for _code, count in section.census.excluded_by_coverage),
    ]

    assert point.sample_size == 5
    assert point.sample_size != section.census.subject_count
    assert section.census.subject_count == 36
    assert section.census.admitted_count == 5
    assert section.census.unlabelled_count == 1
    assert section.census.unmatched_count == 2
    assert section.census.excluded_by_coverage == (
        ("not_in_universe", 3),
        ("insufficient_history", 4),
        ("ambiguous_filing", 8),
        ("input_missing", 6),
        ("undefined_value", 7),
    )
    assert {pair.subject for pair in section.pairs} == {code(index) for index in range(1, 6)}
    assert cells == [5, 1, 2, 3, 4, 8, 6, 7]
    assert len(set(cells)) == len(cells), "two cells sharing a value would let a transposition pass"
    assert sum(cells) == section.census.subject_count


def test_an_imputed_processed_value_is_counted_and_never_correlated() -> None:
    """The load-bearing exclusion, and the fixture separates the two answers.

    Counting `imputed` under its own code proves nothing on its own -- what proves the exclusion
    is live is that admitting it *would have changed the IC*. The imputed name is filled at the
    bottom of the score cross section (`0.0`, below every measured value) and given a middling
    return, so admitting it drags a rank IC of `0.9` down to `1 - 6 * 24 / (6 * 35)`, which is
    `0.3142857...`. The two numbers are 0.59 apart, so a build that let the fill through would
    report a visibly different answer rather than the same one.
    """
    imputed_score = 0.0
    observations = [
        *(_processed(name, value=value) for name, value in MONOTONE_SCORES.items()),
        _processed(
            code(6), value=imputed_score, coverage="imputed", source_coverage="input_missing"
        ),
    ]
    returns = {**NEARLY_MONOTONE_RETURNS, code(6): 0.06}
    labels = _labels(returns)
    study = FactorICStudy(_spec())

    excluded = processed_cross_section(as_of=AS_OF, observations=observations, labels=labels)
    admitted_anyway = processed_cross_section(
        as_of=AS_OF,
        observations=[
            *(_processed(name, value=value) for name, value in MONOTONE_SCORES.items()),
            _processed(code(6), value=imputed_score),
        ],
        labels=labels,
    )

    assert dict(excluded.census.excluded_by_coverage)["imputed"] == 1
    assert excluded.census.admitted_count == 5
    assert admitted_anyway.census.admitted_count == 6
    assert study.measure(excluded).raw_ic == pytest.approx(SPEARMAN_OF_THE_FIXTURE, abs=1e-12)
    assert study.measure(admitted_anyway).raw_ic == pytest.approx(1 - 6 * 24 / (6 * 35), abs=1e-12)
    assert study.measure(admitted_anyway).raw_ic == pytest.approx(0.3142857142857143, abs=1e-12)
    assert study.measure(excluded).raw_ic != study.measure(admitted_anyway).raw_ic


def test_the_three_tiers_are_measured_separately_and_can_disagree() -> None:
    """D8's "compare raw, processed and neutralised" made structural.

    The three tiers carry three different values for one security at one `as_of` -- which is the
    ordinary case, since a transform reorders nothing but a neutralisation removes an industry
    mean -- and each is correlated against the same forward returns. The three ICs come out
    different, so a report that read one tier's column and labelled it another's would be caught.
    """
    labels = _labels(NEARLY_MONOTONE_RETURNS)
    study = FactorICStudy(_spec())
    reversed_scores = {name: -value for name, value in MONOTONE_SCORES.items()}
    shuffled = dict(zip(MONOTONE_SCORES, (3.0, 1.0, 2.0, 5.0, 4.0), strict=True))

    raw = study.measure(
        raw_cross_section(
            as_of=AS_OF,
            observations=[_raw(n, value=v) for n, v in MONOTONE_SCORES.items()],
            labels=labels,
        )
    )
    processed = study.measure(
        processed_cross_section(
            as_of=AS_OF,
            observations=[_processed(n, value=v) for n, v in reversed_scores.items()],
            labels=labels,
        )
    )
    neutralized = study.measure(
        neutralized_cross_section(
            as_of=AS_OF,
            observations=[_neutralized(n, value=v) for n, v in shuffled.items()],
            labels=labels,
        )
    )

    assert (raw.tier, processed.tier, neutralized.tier) == ("raw", "processed", "neutralized")
    assert raw.raw_ic == pytest.approx(0.9, abs=1e-12)
    assert processed.raw_ic == pytest.approx(-0.9, abs=1e-12)
    assert neutralized.raw_ic == pytest.approx(0.7, abs=1e-12)
    assert len({raw.raw_ic, processed.raw_ic, neutralized.raw_ic}) == 3


def test_each_tier_admits_exactly_the_codes_its_own_contract_says_carry_a_measurement() -> None:
    """The two tables, and the one cell they differ in."""
    declared = {
        "raw": frozenset({"computed"}),
        "processed": frozenset({"processed"}),
        "neutralized": frozenset({"neutralized"}),
    }

    assert declared == TIER_ADMITTED_CODES
    gaps = {
        tier: sorted(TIER_VALUE_CODES[tier] - TIER_ADMITTED_CODES[tier])
        for tier in FACTOR_TIER_ORDER
    }
    assert gaps == {"raw": [], "processed": ["imputed"], "neutralized": []}


def test_an_admitted_code_with_no_value_is_refused_rather_than_skipped() -> None:
    """The row that skipped its own constructor. Reachable only through a subclass, which is
    exactly the path `validate_factor_observation` exists in two places for."""
    with pytest.raises(FactorICError, match="skipped its own constructor"):
        from openalpha_cn.backtest.factor_ic import ic_cross_section

        ic_cross_section(
            as_of=AS_OF,
            tier="raw",
            rows=[(code(index), float(index), "computed") for index in range(1, 5)]
            + [(code(5), None, "computed")],
            labels=_labels(NEARLY_MONOTONE_RETURNS),
        )


def test_a_coverage_code_the_tier_does_not_declare_is_refused() -> None:
    from openalpha_cn.backtest.factor_ic import ic_cross_section

    with pytest.raises(FactorICError, match="which the raw tier does not declare"):
        ic_cross_section(
            as_of=AS_OF,
            tier="raw",
            rows=[(name, value, "processed") for name, value in MONOTONE_SCORES.items()],
            labels=_labels(NEARLY_MONOTONE_RETURNS),
        )


def test_a_tier_nobody_declared_is_refused() -> None:
    from openalpha_cn.backtest.factor_ic import ic_cross_section

    with pytest.raises(FactorICError, match="is not a declared tier"):
        ic_cross_section(
            as_of=AS_OF,
            tier="residual",  # type: ignore[arg-type]
            rows=[],
            labels={},
        )


# --------------------------------------------------------------------------------------------
# The two ICs, and the codes that say there is none
# --------------------------------------------------------------------------------------------


def test_the_rank_ic_is_the_hand_computed_spearman_and_the_pearson_is_a_different_number() -> None:
    """One cross section, two methods, two answers -- which is what makes the field load-bearing.

    Spearman is `1 - 6 * sum(d^2) / (n(n^2 - 1))` and this fixture's `sum(d^2)` is 2, so `0.9`
    can be checked by hand. So can the Pearson: the score deviations are `-2, -1, 0, 1, 2` and the
    return deviations `-0.05, -0.04, -0.03, 0.14, -0.02`, giving `0.24 / (sqrt(10) * sqrt(0.025))`
    = `0.24 / 0.5` = `0.48`. The two are 0.42 apart, so a study that ignored `method` and always
    ranked fails here rather than looking right -- which is what the first version of this fixture
    did. See `NEARLY_MONOTONE_RETURNS`.
    """
    section = _raw_section()

    spearman = FactorICStudy(_spec(method="spearman")).measure(section)
    pearson = FactorICStudy(_spec(method="pearson")).measure(section)

    assert spearman.raw_ic == pytest.approx(SPEARMAN_OF_THE_FIXTURE, abs=1e-12)
    assert pearson.raw_ic == pytest.approx(PEARSON_OF_THE_FIXTURE, abs=1e-12)
    assert spearman.raw_ic != pearson.raw_ic
    assert abs(spearman.raw_ic - pearson.raw_ic) == pytest.approx(0.42, abs=1e-12)
    assert (spearman.method, pearson.method) == ("spearman", "pearson")


def test_pearson_follows_one_outlier_where_the_rank_ic_does_not() -> None:
    """Why both methods exist, on a cross section that separates them by more than rounding.

    One security's return is moved to 100x the others' while its *rank* is unchanged. The rank IC
    does not move at all -- it reads an ordering -- and the Pearson moves by more than a tenth.
    A fixture where the two methods agreed would leave `ICMethod` a field nothing depends on.
    """
    outlier = {**NEARLY_MONOTONE_RETURNS, code(4): 5.0}
    base = _raw_section()
    stretched = _raw_section(returns=outlier)

    spearman_base = FactorICStudy(_spec(method="spearman")).measure(base)
    spearman_out = FactorICStudy(_spec(method="spearman")).measure(stretched)
    pearson_base = FactorICStudy(_spec(method="pearson")).measure(base)
    pearson_out = FactorICStudy(_spec(method="pearson")).measure(stretched)

    assert spearman_base.raw_ic == spearman_out.raw_ic
    assert pearson_out.raw_ic is not None and pearson_base.raw_ic is not None
    assert abs(pearson_out.raw_ic - pearson_base.raw_ic) > 0.1


def test_a_cross_section_thinner_than_the_declared_floor_is_a_code_and_not_a_raise() -> None:
    """A loop over a year of as_ofs has to be able to keep going past a thin day.

    The sample size is reported on the coded point too, which is what
    `KNOWN_IC_LIMITATIONS`' sample-floor entry needs: a report can say "four names, floor five"
    rather than only "no answer".
    """
    scores = {code(index): float(index) for index in range(1, 5)}
    section = _raw_section(scores, {name: 0.01 * index for index, name in enumerate(scores, 1)})

    point = FactorICStudy(_spec(min_securities=5)).measure(section)

    assert point.coverage == "insufficient_sample"
    assert point.sample_size == 4
    assert (point.raw_ic, point.ic) == (None, None)


def test_a_cross_section_with_nothing_to_order_names_the_side_that_collapsed() -> None:
    """`degenerate_scores` and `degenerate_returns` are two findings, not one.

    A factor that produced one value for the whole market is a defect in the factor; a market
    that moved identically for every name is a fact about the day. One code would make them
    indistinguishable on a stored report, which is what `FactorCoverage` spent five members
    refusing. The precedence -- scores before returns -- is driven by the third case, where both
    collapse and the factor is named.
    """
    study = FactorICStudy(_spec())
    flat_scores = dict.fromkeys(MONOTONE_SCORES, 2.0)
    flat_returns = dict.fromkeys(NEARLY_MONOTONE_RETURNS, 0.02)

    tied_factor = study.measure(_raw_section(flat_scores))
    tied_market = study.measure(_raw_section(returns=flat_returns))
    both = study.measure(_raw_section(flat_scores, flat_returns))

    assert tied_factor.coverage == "degenerate_scores"
    assert tied_market.coverage == "degenerate_returns"
    assert both.coverage == "degenerate_scores"
    assert tied_factor.sample_size == tied_market.sample_size == 5
    assert set(IC_COVERAGE_ORDER) == {
        "measured",
        "insufficient_sample",
        "degenerate_scores",
        "degenerate_returns",
    }


def test_a_correlation_whose_sums_of_squares_would_overflow_reports_the_right_number() -> None:
    """`_pearson` scales its deviations before squaring them, and the unscaled expression is run
    beside it to show what that buys.

    The failure is **a wrong number and not an error**, which is why this test computes the
    unscaled form rather than describing it. `sum(d * d)` reaches `inf` past
    `sqrt(float_info.max / n)`; the numerator stays finite, so the quotient is `finite / inf`,
    which is `0.0` -- a perfectly ordered cross section reported as an IC of exactly zero, a
    number `ICPoint` accepts without complaint. A `nan` would at least have been refused.
    """
    huge = {code(index): float(index) * 1e200 for index in range(1, 6)}
    section = _raw_section(huge)
    scores, returns = section.scores, section.forward_returns
    mean_x = sum(scores) / len(scores)
    mean_y = sum(returns) / len(returns)
    dx = [value - mean_x for value in scores]
    dy = [value - mean_y for value in returns]
    unscaled = sum(a * b for a, b in zip(dx, dy, strict=True)) / (
        math.sqrt(sum(a * a for a in dx)) * math.sqrt(sum(b * b for b in dy))
    )

    point = FactorICStudy(_spec(method="pearson")).measure(section)

    assert math.isinf(sum(a * a for a in dx))
    assert unscaled == 0.0
    assert point.coverage == "measured"
    assert point.raw_ic is not None
    assert -1.0 <= point.raw_ic <= 1.0
    assert point.raw_ic == pytest.approx(PEARSON_OF_THE_FIXTURE, abs=1e-12)
    assert math.sqrt(sys.float_info.max / 5534) == pytest.approx(1.8023e152, rel=1e-4)


# --------------------------------------------------------------------------------------------
# direction
# --------------------------------------------------------------------------------------------


def test_the_declared_direction_decides_the_sign_and_reaches_the_stability_summary() -> None:
    """`FactorDefinition.direction`'s first consumer, on a fixture that separates the two answers.

    Two definitions differing in nothing but `direction` -- which mints two different
    `factor_id`s -- are run over the *same* three cross sections. The raw correlation is identical
    on both, which is the point: it is the *oriented* number that moves, exactly negated, and the
    aggregate moves with it. `sign_consistency` comes out `1.0` against `0.0` and the mean ICs are
    exact negations, so a build that dropped the orientation would not merely produce a different
    number here -- it would produce the same one twice.
    """
    sections = [
        _raw_section(as_of=as_of, window=_window(as_of=as_of))
        for as_of in (
            datetime(2026, 6, 10, 8, 30, tzinfo=UTC),
            datetime(2026, 6, 11, 8, 30, tzinfo=UTC),
            datetime(2026, 6, 12, 8, 30, tzinfo=UTC),
        )
    ]
    higher = FactorICStudy(_spec("higher_is_better"))
    lower = FactorICStudy(_spec("lower_is_better"))

    up = [higher.measure(section) for section in sections]
    down = [lower.measure(section) for section in sections]
    up_summary = higher.summarize(up)
    down_summary = lower.summarize(down)

    assert [point.raw_ic for point in up] == [point.raw_ic for point in down]
    assert [point.ic for point in up] == [pytest.approx(0.9, abs=1e-12)] * 3
    assert [point.ic for point in down] == [pytest.approx(-0.9, abs=1e-12)] * 3
    assert up[0].ic == -down[0].ic != 0.0
    assert up_summary.mean_ic == -down_summary.mean_ic
    assert (up_summary.sign_consistency, down_summary.sign_consistency) == (1.0, 0.0)
    assert (up_summary.positive_count, down_summary.positive_count) == (3, 0)
    assert (up_summary.negative_count, down_summary.negative_count) == (0, 3)
    assert up_summary.icir is None and down_summary.icir is None
    assert up[0].factor_id != down[0].factor_id


def test_the_direction_travels_from_the_definition_and_cannot_be_passed_separately() -> None:
    """There is no `direction` argument anywhere in this module's public surface.

    A function taking a `FactorDirection` is one a caller can hand the other value to, which is
    how a factor comes to be declared to work in whichever direction it came out.
    `FactorICSpec.definition` makes the wrong direction unreachable rather than discouraged.
    """
    spec = _spec("lower_is_better")

    assert spec.direction == spec.definition.direction == "lower_is_better"
    assert spec.factor_id == spec.definition.factor_id
    assert spec.orient(0.25) == -0.25
    assert _spec("higher_is_better").orient(0.25) == 0.25
    assert "direction" not in FactorICSpec.model_fields


def test_an_oriented_ic_that_contradicts_its_direction_is_not_constructible() -> None:
    """The rule as a contract rather than as a convention in one function.

    Without it `direction='lower_is_better', raw_ic=0.04, ic=0.04` builds, and a report reading
    `ic` says the factor worked when the measurement says it did not.
    """
    fields = {
        "as_of": AS_OF,
        "tier": "raw",
        "method": "spearman",
        "factor_id": "fct_probe",
        "horizon_sessions": 1,
        "coverage": "measured",
        "sample_size": 5,
    }

    with pytest.raises(ValidationError, match="orientation that contradicts the declaration"):
        ICPoint(direction="lower_is_better", raw_ic=0.04, ic=0.04, **fields)  # type: ignore[arg-type]

    assert ICPoint(direction="lower_is_better", raw_ic=0.04, ic=-0.04, **fields).ic == -0.04  # type: ignore[arg-type]
    assert ICPoint(direction="higher_is_better", raw_ic=0.04, ic=0.04, **fields).ic == 0.04  # type: ignore[arg-type]


def test_a_point_carrying_a_correlation_under_a_no_answer_code_is_not_constructible() -> None:
    fields = {
        "as_of": AS_OF,
        "tier": "raw",
        "method": "spearman",
        "direction": "higher_is_better",
        "factor_id": "fct_probe",
        "horizon_sessions": 1,
        "sample_size": 2,
    }

    with pytest.raises(ValidationError, match="exactly the 'measured' code carries both"):
        ICPoint(coverage="insufficient_sample", raw_ic=0.5, ic=0.5, **fields)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="exactly the 'measured' code carries both"):
        ICPoint(coverage="measured", raw_ic=None, ic=None, **fields)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match=r"outside \[-1, 1\]"):
        ICPoint(coverage="measured", raw_ic=1.5, ic=1.5, **fields)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="not a finite correlation"):
        ICPoint(coverage="measured", raw_ic=float("nan"), ic=0.0, **fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# stability
# --------------------------------------------------------------------------------------------


def _series(
    values: Sequence[float | None], *, direction: str = "higher_is_better"
) -> list[ICPoint]:
    """A hand-built series, so the stability arithmetic is checked against numbers and not a fit.

    `None` is a point that produced no IC. Built directly rather than through `measure`, because
    what is under test here is the reduction and driving it through the cross-section machinery
    would make the expected mean a function of a price path.
    """
    points = []
    for offset, value in enumerate(values):
        raw = None if value is None else (value if direction == "higher_is_better" else -value)
        points.append(
            ICPoint(
                as_of=AS_OF + timedelta(days=offset),
                tier="raw",
                method="spearman",
                direction=direction,  # type: ignore[arg-type]
                factor_id=_definition(direction).factor_id,
                horizon_sessions=1,
                coverage="measured" if value is not None else "insufficient_sample",
                sample_size=5 if value is not None else 2,
                raw_ic=raw,
                ic=value,
            )
        )
    return points


def test_the_stability_summary_reports_mean_dispersion_icir_and_sign_consistency() -> None:
    """Every statistic against a number computed by hand from the same five ICs.

    `0.10, -0.02, 0.06, 0.00, 0.06` has mean `0.04`, sample standard deviation
    `sqrt(0.0096 / 4) = 0.04898979...`, so the ICIR is `0.8164965...`. Three of the five are
    positive, one is negative and one is exactly zero, so `sign_consistency` is `0.6` -- and the
    zero stays in the denominator, which is why it is `0.6` and not `0.75`.
    """
    summary = FactorICStudy(_spec()).summarize(_series([0.10, -0.02, 0.06, 0.00, 0.06]))

    assert summary.coverage == "measured"
    assert summary.measured_count == 5
    assert len(summary.as_ofs) == 5
    assert summary.mean_ic == pytest.approx(0.04, abs=1e-15)
    assert summary.stdev_ic == pytest.approx(0.04898979485566356, abs=1e-15)
    assert summary.icir == pytest.approx(0.8164965809277261, abs=1e-12)
    assert (summary.positive_count, summary.negative_count, summary.zero_count) == (3, 1, 1)
    assert summary.sign_consistency == 0.6
    assert summary.icir == pytest.approx(summary.mean_ic / summary.stdev_ic, abs=1e-15)


def test_a_point_that_produced_no_ic_is_dropped_from_the_statistics_and_not_read_as_zero() -> None:
    """`measured_count` beside `len(as_ofs)` is what makes the attrition visible.

    A degenerate or thin `as_of` contributing `0.0` would pull the mean towards zero and inflate
    the dispersion, and a report showing only the mean could not tell that from a weak factor.
    """
    summary = FactorICStudy(_spec()).summarize(_series([0.10, None, 0.02, None, 0.06]))
    without_gaps = FactorICStudy(_spec()).summarize(_series([0.10, 0.02, 0.06]))

    assert (len(summary.as_ofs), summary.measured_count) == (5, 3)
    assert summary.mean_ic == without_gaps.mean_ic == pytest.approx(0.06, abs=1e-15)
    assert summary.sign_consistency == 1.0
    assert summary.positive_count + summary.negative_count + summary.zero_count == 3


def test_an_icir_over_a_series_with_no_dispersion_is_none_rather_than_infinite() -> None:
    """The deliberate divergence from `event_study.py`, and the two reasons for it.

    `EventStudy.analyze` answers `math.inf` when its standard error is zero, which discards the
    sign of a constant negative series and produces a non-finite number this repository's own
    storage contracts refuse on every other plane. `None` says "there is no dispersion to divide
    by", and the negative series proves the sign is not what is lost.
    """
    flat_up = FactorICStudy(_spec()).summarize(_series([0.05, 0.05, 0.05]))
    flat_down = FactorICStudy(_spec()).summarize(_series([-0.05, -0.05, -0.05]))

    assert flat_up.stdev_ic == flat_down.stdev_ic == 0.0
    assert flat_up.icir is None and flat_down.icir is None
    assert flat_up.mean_ic == pytest.approx(0.05, abs=1e-15)
    assert flat_down.mean_ic == pytest.approx(-0.05, abs=1e-15)
    assert flat_up.mean_ic == -flat_down.mean_ic
    assert (flat_up.sign_consistency, flat_down.sign_consistency) == (1.0, 0.0)


def test_a_series_shorter_than_the_declared_as_of_floor_carries_counts_and_no_statistics() -> None:
    """How many as_ofs it takes to talk about stability is declared, not hard-coded.

    The same three points clear a floor of 3 and do not clear a floor of 4, and the counts are
    reported either way so a reader can see how far short the series fell.
    """
    points = _series([0.10, None, 0.02, None, 0.06])

    cleared = FactorICStudy(_spec(min_as_ofs=3)).summarize(points)
    short = FactorICStudy(_spec(min_as_ofs=4)).summarize(points)

    assert cleared.coverage == "measured"
    assert short.coverage == "insufficient_as_ofs"
    assert (short.mean_ic, short.stdev_ic, short.icir, short.sign_consistency) == (
        None,
        None,
        None,
        None,
    )
    assert short.measured_count == cleared.measured_count == 3
    assert short.positive_count == 3
    assert len(short.as_ofs) == 5


def test_the_two_floors_are_declared_fields_with_arithmetic_lower_bounds() -> None:
    """Neither floor has a default, and neither can be set below the value its statistic needs.

    `min_securities` cannot go below 3 because two points always correlate perfectly, and
    `min_as_ofs` cannot go below 2 because a sample standard deviation of one number does not
    exist. The upper bounds are range checks on stored integers, not vacuity guards -- a floor
    above today's market is declarable, for `FactorTransformSpec.min_cross_section`'s reason.
    """
    with pytest.raises(ValidationError, match="greater than or equal to 3"):
        _spec(min_securities=2)
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        _spec(min_as_ofs=1)
    with pytest.raises(ValidationError, match="less than or equal to 10000"):
        _spec(min_securities=MAXIMUM_IC_SECURITIES + 1)
    with pytest.raises(ValidationError, match="less than or equal to 10000"):
        _spec(min_as_ofs=MAXIMUM_IC_AS_OFS + 1)

    assert _spec(min_securities=MAXIMUM_IC_SECURITIES).min_securities == 10_000
    assert (MINIMUM_IC_SECURITIES, MINIMUM_IC_AS_OFS) == (3, 2)
    assert set(FactorICSpec.model_fields) == {
        "definition",
        "method",
        "min_securities",
        "min_as_ofs",
    }


def test_a_series_that_is_not_one_study_is_refused() -> None:
    """A mean over two horizons, two tiers or two factors is the average of two quantities."""
    study = FactorICStudy(_spec())
    points = _series([0.10, 0.02, 0.06])

    with pytest.raises(FactorICError, match="horizon_sessions=10 against 1"):
        study.summarize(
            [points[0], points[1].model_copy(update={"horizon_sessions": 10}), points[2]]
        )
    with pytest.raises(FactorICError, match="tier='processed' against 'raw'"):
        study.summarize([points[0], points[1].model_copy(update={"tier": "processed"}), points[2]])
    with pytest.raises(FactorICError, match="appears more than once in this series"):
        study.summarize([points[0], points[0], points[1]])
    with pytest.raises(FactorICError, match="at least one point"):
        study.summarize([])


def test_a_series_measured_for_another_factor_is_refused_by_the_study_that_holds_the_spec() -> None:
    """The study's own `factor_id` is checked, not only the series' internal agreement.

    A series that agrees with itself and was measured for a different definition would otherwise
    be summarised under this spec's direction, which is the one thing the spec exists to fix.
    """
    other = _series([0.10, 0.02, 0.06])
    stranger = [point.model_copy(update={"factor_id": "fct_someone_else"}) for point in other]

    with pytest.raises(FactorICError, match="measured for factor 'fct_someone_else'"):
        FactorICStudy(_spec()).summarize(stranger)


# --------------------------------------------------------------------------------------------
# decay
# --------------------------------------------------------------------------------------------


def _rung_points(horizon_sessions: int, values: Sequence[float | None]) -> list[ICPoint]:
    return [
        point.model_copy(update={"horizon_sessions": horizon_sessions}) for point in _series(values)
    ]


def test_the_decay_curve_is_ordered_by_horizon_and_reports_each_rungs_attrition() -> None:
    """The axis is the horizon in sessions, and the rungs are sorted by it whatever order they
    arrive in -- so the shape of the curve is not an artefact of the call.

    Each rung's `measured_count` is reported against the common offered sample, which is what
    separates a decaying factor from a shrinking one: here the 20-session rung lost two of its
    five as_ofs to refused labels, and the curve says so.
    """
    curve = FactorICStudy(_spec()).decay(
        [
            _rung_points(20, [0.02, None, 0.01, None, 0.02]),
            _rung_points(1, [0.10, 0.09, 0.11, 0.10, 0.10]),
            _rung_points(5, [0.06, 0.05, 0.07, 0.06, 0.06]),
        ]
    )

    assert curve.horizons == (1, 5, 20)
    assert [rung.summary.measured_count for rung in curve.rungs] == [5, 5, 3]
    assert curve.mean_ics == (
        pytest.approx(0.10, abs=1e-15),
        pytest.approx(0.06, abs=1e-15),
        pytest.approx(0.0166666666666666, abs=1e-12),
    )
    assert curve.mean_ics[0] > curve.mean_ics[1] > curve.mean_ics[2]
    assert (curve.tier, curve.method, curve.direction) == ("raw", "spearman", "higher_is_better")


def test_a_decay_curve_whose_rungs_were_asked_about_different_days_is_refused() -> None:
    """The rule that keeps a fall in IC from being a sample that shrank underneath it.

    The two rungs here carry the same statistics and different offered `as_of`s, so the
    difference between them is entirely the sample. Without this check the curve would render and
    a reader would read decay off it.
    """
    short = _rung_points(1, [0.10, 0.09, 0.11])
    long = [
        point.model_copy(update={"as_of": point.as_of + timedelta(days=90)})
        for point in _rung_points(5, [0.02, 0.01, 0.03])
    ]

    with pytest.raises(ValidationError, match="asked about the same days"):
        FactorICStudy(_spec()).decay([short, long])


def test_a_decay_curve_needs_two_horizons_and_refuses_a_repeated_one() -> None:
    """One point is not a curve, and two rungs at one horizon are two answers to one question."""
    study = FactorICStudy(_spec())
    rung = _rung_points(5, [0.06, 0.05, 0.07])

    with pytest.raises(FactorICError, match="at least two horizons"):
        study.decay([rung])
    with pytest.raises(ValidationError, match="strictly increasing"):
        study.decay([rung, _rung_points(5, [0.02, 0.01, 0.03])])


def test_a_rung_filed_under_a_horizon_its_numbers_did_not_come_from_is_refused() -> None:
    summary = FactorICStudy(_spec()).summarize(_rung_points(5, [0.06, 0.05, 0.07]))

    with pytest.raises(ValidationError, match="cannot be a second source of truth"):
        ICDecayRung(horizon_sessions=1, summary=summary)


def test_the_decay_axis_inherits_the_horizon_contracts_countability_rule() -> None:
    """A `3m` rung is not constructible, because `ResearchHorizon.sessions` refuses a calendar
    span rather than multiplying by a sessions-per-month constant nobody measured."""
    from openalpha_cn.domain.horizon import HorizonError

    calendar_horizon = parse_horizon("3m")

    with pytest.raises(HorizonError, match="not a whole number of trading sessions"):
        _ = calendar_horizon.sessions
    assert parse_horizon("10d").sessions == 10


def test_a_longer_horizon_reads_a_longer_window_and_a_different_cumulative_return() -> None:
    """The decay axis is the label window's length, driven through the real label contract.

    The same securities are labelled at 1, 5 and 20 sessions from one prediction day; the entry
    is the same session on all three and the exit moves out, so the return each rung correlates
    against is cumulative from one entry. `KNOWN_IC_LIMITATIONS` records that this is cumulative
    rather than marginal.
    """
    windows = {sessions: _window(horizon=f"{sessions}d") for sessions in (1, 5, 20)}
    sections = {sessions: _raw_section(window=window) for sessions, window in windows.items()}

    assert {sessions: section.entry_day for sessions, section in sections.items()} == {
        1: date(2026, 6, 11),
        5: date(2026, 6, 11),
        20: date(2026, 6, 11),
    }
    assert sections[1].exit_day == date(2026, 6, 12)
    assert sections[5].exit_day == date(2026, 6, 18)
    assert sections[20].exit_day == date(2026, 7, 9)
    assert [sections[key].horizon.sessions for key in (1, 5, 20)] == [1, 5, 20]
    assert {
        FactorICStudy(_spec()).measure(section).horizon_sessions for section in sections.values()
    } == {1, 5, 20}


# --------------------------------------------------------------------------------------------
# the census's own arithmetic, the import-time audit, and the limitation registry
# --------------------------------------------------------------------------------------------


def test_a_census_that_does_not_add_up_is_refused() -> None:
    """The property that makes every one of the five numbers un-fudgeable."""
    honest = ICCensus(
        tier="raw",
        subject_count=10,
        admitted_count=4,
        excluded_by_coverage=(
            ("not_in_universe", 1),
            ("insufficient_history", 2),
            ("ambiguous_filing", 0),
            ("input_missing", 1),
            ("undefined_value", 0),
        ),
        unlabelled_count=1,
        unmatched_count=1,
    )

    with pytest.raises(FactorICError, match="has lost one of them"):
        replace(honest, admitted_count=3)
    with pytest.raises(FactorICError, match="cannot be told from one whose count is zero"):
        replace(honest, excluded_by_coverage=(("not_in_universe", 1), ("input_missing", 3)))
    with pytest.raises(FactorICError, match="is not a declared tier"):
        replace(honest, tier="residual")  # type: ignore[arg-type]
    with pytest.raises(FactorICError, match="cannot be negative"):
        replace(honest, admitted_count=-1, subject_count=5)

    assert honest.subject_count == 10


def test_a_summary_whose_numbers_contradict_each_other_is_not_constructible() -> None:
    """`ICSummary`'s validator, driven at the contract rather than only through `summarize`.

    Every branch here is unreachable from `summarize`, which is the point: the contract is what a
    later reader -- `V2-P3-014`'s report, or a store that reassembles one from columns -- is held
    to, and a rule only the producer obeys is a rule the reader can break.
    """
    honest = FactorICStudy(_spec()).summarize(_series([0.10, -0.02, 0.06]))

    with pytest.raises(ValidationError, match="not a finite statistic"):
        honest.model_copy(update={"mean_ic": float("inf")}).model_validate(
            honest.model_dump() | {"mean_ic": float("inf")}
        )
    with pytest.raises(ValidationError, match="exactly the 'measured' code carries the statistics"):
        honest.model_validate(honest.model_dump() | {"coverage": "insufficient_as_ofs"})
    with pytest.raises(ValidationError, match="cannot carry an icir"):
        honest.model_validate(
            honest.model_dump()
            | {
                "coverage": "insufficient_as_ofs",
                "mean_ic": None,
                "stdev_ic": None,
                "sign_consistency": None,
            }
        )
    with pytest.raises(ValidationError, match="cannot measure an as_of it was not given"):
        honest.model_validate(honest.model_dump() | {"measured_count": 9, "positive_count": 7})
    with pytest.raises(ValidationError, match="every measured IC is positive, negative or zero"):
        honest.model_validate(honest.model_dump() | {"positive_count": 0})
    with pytest.raises(ValidationError, match="distinct and ascending"):
        honest.model_validate(honest.model_dump() | {"as_ofs": tuple(reversed(honest.as_ofs))})

    assert honest.coverage == "measured"


def test_a_decay_curve_whose_rung_is_a_different_study_is_refused() -> None:
    """A rung measured on another tier makes the curve a comparison of two different things."""
    rungs = [_rung_points(1, [0.10, 0.09, 0.11]), _rung_points(5, [0.06, 0.05, 0.07])]
    study = FactorICStudy(_spec())
    curve = study.decay(rungs)
    swapped = curve.rungs[1].summary.model_copy(update={"tier": "processed"})

    with pytest.raises(ValidationError, match="a comparison of two different things"):
        curve.model_validate(
            curve.model_dump()
            | {
                "rungs": (
                    curve.rungs[0].model_dump(),
                    {"horizon_sessions": 5, "summary": swapped.model_dump()},
                )
            }
        )

    assert study.spec is study.spec and study.spec.method == "spearman"


def test_a_census_cell_cannot_be_negative() -> None:
    with pytest.raises(FactorICError, match="excluded-coverage count cannot be negative"):
        ICCensus(
            tier="raw",
            subject_count=0,
            admitted_count=1,
            excluded_by_coverage=(
                ("not_in_universe", -1),
                ("insufficient_history", 0),
                ("ambiguous_filing", 0),
                ("input_missing", 0),
                ("undefined_value", 0),
            ),
            unlabelled_count=0,
            unmatched_count=0,
        )


def test_a_pair_refuses_a_non_finite_term_and_an_empty_subject() -> None:
    """The forward return arrives from a contract no factor plane has seen, so its finiteness is
    checked at this boundary too."""
    with pytest.raises(FactorICError, match="must name a subject"):
        ICObservationPair(subject="  ", score=1.0, forward_return=0.1)
    with pytest.raises(FactorICError, match="forward_return is nan"):
        ICObservationPair(subject=code(1), score=1.0, forward_return=float("nan"))
    with pytest.raises(FactorICError, match="score is inf"):
        ICObservationPair(subject=code(1), score=float("inf"), forward_return=0.1)


def test_every_record_names_the_as_of_the_window_the_factor_and_the_tier_it_came_from() -> None:
    """The provenance columns, asserted rather than merely rendered.

    Written after a column-by-column falsification run over every field of every record this
    module reports: each one was perturbed after the implementation produced it, and seven came
    back unnoticed -- `ICPoint.as_of`, `ICPoint.direction`, `ICSummary.factor_id`,
    `ICDecayCurve.factor_id`, `ICCensus.tier`, `ICCrossSection.as_of` and
    `ICCrossSection.prediction_day`. Coverage said 100% of the module the whole time, because
    coverage has no opinion about a field that is written and never read. These are the
    assertions that make the seven readable.
    """
    section = _raw_section()
    point = FactorICStudy(_spec("lower_is_better")).measure(section)
    series = [_rung_points(1, [0.10, 0.09, 0.11]), _rung_points(5, [0.06, 0.05, 0.07])]
    curve = FactorICStudy(_spec()).decay(series)

    assert section.as_of == AS_OF
    assert section.prediction_day == date(2026, 6, 10)
    assert section.entry_day == date(2026, 6, 11)
    assert section.census.tier == section.tier == "raw"
    assert point.as_of == AS_OF
    assert point.direction == "lower_is_better"
    assert point.factor_id == _definition("lower_is_better").factor_id
    assert curve.factor_id == _definition().factor_id
    assert {rung.summary.factor_id for rung in curve.rungs} == {_definition().factor_id}
    assert _definition().factor_id != _definition("lower_is_better").factor_id


def test_the_tier_table_audit_refuses_every_disagreement_it_exists_to_catch() -> None:
    """The import-time audit, driven in all four of its failure directions.

    An audit whose only call site is the one that passes is an audit nobody has seen fail, which
    is why `_refuse_a_tier_table_that_disagrees_with_its_own_contract` takes its tables as
    arguments rather than reading the module's globals. The live call at import is the fifth case
    and is what makes a sixth processed coverage code a load failure rather than a silent drop
    from every correlation.
    """
    order = dict(TIER_COVERAGE_ORDER)
    values = dict(TIER_VALUE_CODES)
    admitted = dict(TIER_ADMITTED_CODES)

    with pytest.raises(FactorICError, match="TIER_COVERAGE_ORDER is keyed by"):
        _refuse_a_tier_table_that_disagrees_with_its_own_contract(
            FACTOR_TIER_ORDER, {"raw": order["raw"]}, values, admitted
        )
    with pytest.raises(FactorICError, match="has to be a permutation of the vocabulary"):
        _refuse_a_tier_table_that_disagrees_with_its_own_contract(
            FACTOR_TIER_ORDER, {**order, "raw": ("computed",)}, values, admitted
        )
    with pytest.raises(FactorICError, match="which the raw contract does not declare"):
        _refuse_a_tier_table_that_disagrees_with_its_own_contract(
            FACTOR_TIER_ORDER, order, {**values, "raw": frozenset({"invented"})}, admitted
        )
    with pytest.raises(FactorICError, match="which carries no value"):
        _refuse_a_tier_table_that_disagrees_with_its_own_contract(
            FACTOR_TIER_ORDER,
            order,
            values,
            {**admitted, "raw": frozenset({"computed", "input_missing"})},
        )
    with pytest.raises(FactorICError, match="the contract vocabularies cover"):
        _refuse_a_tier_table_that_disagrees_with_its_own_contract(
            ("raw", "processed"),
            {key: order[key] for key in ("raw", "processed")},
            {key: values[key] for key in ("raw", "processed")},
            {key: admitted[key] for key in ("raw", "processed")},
        )

    _refuse_a_tier_table_that_disagrees_with_its_own_contract(
        FACTOR_TIER_ORDER, order, values, admitted
    )


def test_the_known_ic_limitations_are_exactly_these_five_codes() -> None:
    """The set-literal binding `tests/unit/test_known_limitation_registries.py` requires.

    Equality rather than membership, for that module's stated reason: a membership assertion can
    see a code that was renamed and never one that was removed. The year-end-snapshot entry is
    the one `V2-P3-004`'s review required this issue to carry, and it is asserted by name.
    """
    declared = {
        "neutralised_residuals_are_read_at_a_year_end_snapshot",
        "the_forward_return_is_cumulative_rather_than_marginal",
        "an_ic_series_over_overlapping_windows_is_autocorrelated",
        "a_declared_sample_floor_of_three_is_legal_and_is_almost_all_noise",
        "the_windows_dating_zone_is_the_callers_and_is_not_checked_against_the_exchange",
    }

    assert declared == IC_LIMITATION_CODES
    snapshot = next(
        item
        for item in KNOWN_IC_LIMITATIONS
        if item.code == "neutralised_residuals_are_read_at_a_year_end_snapshot"
    )
    assert "read_visible_at" in snapshot.detail
    assert "V2-P4-026" in snapshot.detail
    assert "V2-P3-004" in snapshot.detail
    assert len({item.code for item in KNOWN_IC_LIMITATIONS}) == len(KNOWN_IC_LIMITATIONS)


def test_the_closed_vocabularies_are_the_ones_the_report_groups_by() -> None:
    assert IC_METHOD_ORDER == ("pearson", "spearman")
    assert FACTOR_TIER_ORDER == ("raw", "processed", "neutralized")
    assert IC_COVERAGE_ORDER[0] == "measured"
