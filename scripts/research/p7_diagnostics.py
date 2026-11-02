"""Read-only P7 diagnostics: deciles, tails, rank IC and its dispersion (`V2-P7-004`).

What P7 spec section 3 measured with one-off scripts outside the repository, as a tool: for each
signal day of a plan, each score (the survivor composite and each of its factors) and each label
horizon, one cross section read with the forward report's arithmetic
(`backtest.decile_readout.read_cross_section`), then each dated series summarised over the signal
days whose labels do not overlap.

**Exploratory by construction.** A document written without a read-once binding is
`EXPLORATORY_KIND` and carries `EXPLORATORY_WORDING`, `IN_SAMPLE_WORDING` and `CANDIDATE_WORDING`,
and its text opens with the `EXPLORATORY, UNREGISTERED -- NOT EVIDENCE` banner: the research
window was read to choose these factors and thresholds, so nothing here is out of sample, and it
decides what to measure next, not whether anything works. The read-once kind is written only when
a binding is handed in, which only `p7_reading.read_once` does.

The pieces, each replaceable in a test:

* the scores come from a `ScoreSource`; the real one, `PanelScoreSource`, is the daily path's own
  scoring -- `build_facts_day` and `screen` -- so a score here is the registered record's
  definition: the eligible names (`verdict.eligible`) with their composite, and each composite
  factor's oriented value for the same names. The book's `seed` picks holdings and never enters
  the screen, which is why the command's default book is `SurvivorBookConfig(seed=0)`;
* the labels come from a `LabelSource` per signal year; the real one, `panel_label_source`, is
  `model_view.OutcomeLabels` -- the forward report's reader -- over the year before the signal
  year through the year after, never past the window's ceiling (`label_years`);
* the window is the plan's (`p7_windows.SignalPlan`). The command line takes only the research
  window, by name, and has no option for any other.

Rules the engine keeps:

* every signal day of the plan must lie inside the plan's window, and every label window must end
  on or before the window's last day; a breach raises `DiagnosticWindowError` **before** a score
  (or a label) is asked for. The exit is placed on the label reader's own calendar first
  (`p7_windows.label_exit_day`), and a label that calendar cannot place counts as a breach: a
  reader capped at the window's last year stops on the window's last day, so otherwise the
  calendar's own refusal would be filed as a gap. `plan_signal_days` already embargoes such days;
  this is the second check, because a plan is a plain record and can be built by hand;
* a day the data refuses (`DAY_REFUSALS`: the late-record check's
  `SURVIVOR_RECOMPUTE_REFUSALS`, and the label reader's `ModelRunBlockedError` and
  `ModelPanelUnreadableError`) is a **named gap**, and the run goes on. A score refusal is a gap at
  every horizon of the day; a label refusal at the horizon it was met at; a score whose scope is
  empty or holds a number that is not finite is a gap of that score on that day. A gap is named
  by the refusal's type and its `disclosable` message where it has one, so no local store path
  enters a document. **A `DiagnosticWindowError` is never a gap**: it propagates unchanged, and a
  refusal with one anywhere behind it (`__cause__` or `__context__`) raises that window refusal
  itself (a loader that wraps whatever a door raised must not launder a breach into a gap);
* a name without a label keeps its decile and is counted by refusal code (`None` from the reader
  is `no_adjustment_history`), exactly as the forward report counts it.

The summary of a (score, horizon) is over its non-overlapping sample: every `m`-th signal day of
the plan from the first, `m = ceil(horizon / stride)`, so two sampled labels never share a
session. A gap does not move the sample: it reduces `n`. The gaps and the unlabelled counts
listed are the sampled days'. The rank IC's standard deviation, when there is one, is fed to
`backtest.power_analysis` as a *what if*: the power the design would have if the planning sd
were this in-sample value (`EXPLORATORY_POWER_WORDING`) -- never a planning value by itself. In a
read-once document the value is the one reading's, not an in-sample one, and the block says so
(`READ_ONCE_POWER_WORDING`).

The document is deterministic: no random component (`"random_component": "none"`), no set
iteration in what is written, and `config_digest` is the SHA-256 of `grid.canonical_json` of the
document's own `config` and `request`, so it can be recomputed from the document.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Protocol

import grid
from p7_windows import (
    READ_ONCE_NOTE,
    SECOND_USE,
    WINDOWS,
    DiagnosticWindow,
    DiagnosticWindowError,
    SignalPlan,
    WindowCappedStore,
    capped_panel_store,
    label_exit_day,
    plan_signal_days,
)

from openalpha_cn.backtest.decile_readout import (
    DECILES,
    CrossSectionReadout,
    SeriesSummary,
    read_cross_section,
    summarize_series,
)
from openalpha_cn.backtest.power_analysis import (
    CriticalValue,
    PowerError,
    by_first_step_level,
    futility_stop_probability,
    periods_for_power,
    power_of_mean_test,
    power_table_lines,
    power_with_futility,
)
from openalpha_cn.domain.factor_transform import FactorTransformError, FactorTransformSpec
from openalpha_cn.domain.horizon import HorizonUnit, ResearchHorizon
from openalpha_cn.domain.labels import OutcomeLabel
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.model_view import (
    LabelReach,
    ModelPanelUnreadableError,
    ModelRunBlockedError,
    OutcomeLabels,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.panel_ingest import load_trading_calendar, session_publication_instant
from openalpha_cn.runtime.provenance import resolve_code_commit
from openalpha_cn.strategy_survivor_facts import (
    DEFAULT_MAX_CARRY_SESSIONS,
    DEFAULT_MIN_COVERAGE,
    build_facts_day,
)
from openalpha_cn.strategy_survivor_forward import NO_ADJUSTMENT_HISTORY, LabelSource
from openalpha_cn.strategy_survivor_registration import SURVIVOR_RECOMPUTE_REFUSALS

EXPLORATORY_KIND: Final = "p7-diagnostic-exploratory/v1"
READ_ONCE_KIND: Final = "p7-diagnostic-read-once/v1"
EXPLORATORY_WORDING: Final = (
    "exploratory and unregistered: not evidence -- it decides what to measure next and says "
    "nothing about returns"
)
IN_SAMPLE_WORDING: Final = (
    "in-sample: the research window was read to choose these factors and thresholds (P7 spec "
    "section 3), so nothing here is out of sample"
)
READ_ONCE_WORDING: Final = (
    "the one pre-registered read of this window: a first out-of-sample reading, not confirmatory "
    "evidence; confirmatory evidence comes only from forward records registered under V2-P7-006"
)
SECOND_USE_READ_ONCE_WORDING: Final = (
    "the one pre-registered read of this window, which was P6's one-time holdout and has been "
    "used: a secondary, non-confirmatory reading of a spent holdout, not out-of-sample evidence; "
    "confirmatory evidence comes only from forward records registered under V2-P7-006"
)
"""`READ_ONCE_WORDING` for the second-use window: it was P6's one-time holdout, spent, and P7 was
designed after seeing it, so its one reading is never a first out-of-sample one."""
SECOND_USE_WORDING: Final = (
    "2024-01-02..2026-09-24 was P6's one-time holdout and has been used: a secondary, "
    "non-confirmatory reading"
)
READ_ONCE_HEADLINE: Final = "A FIRST OUT-OF-SAMPLE READING, NOT CONFIRMATORY EVIDENCE"
SECOND_USE_HEADLINE: Final = "A SECONDARY, NON-CONFIRMATORY READING OF A SPENT HOLDOUT"
CANDIDATE_WORDING: Final = "every score is a candidate research signal (候选), never a verdict"
EXPLORATORY_POWER_WORDING: Final = (
    "if the planning sd were this exploratory value (in-sample, measured after the factors were "
    "chosen): never a planning value by itself"
)
READ_ONCE_POWER_WORDING: Final = (
    "if the planning sd were this value measured in the one reading of this window: an observed "
    "value of that reading, never a planning value by itself"
)
"""The power block's wording in a read-once document: the sd is the reading's own value."""
COMPOSITE: Final = "composite"
METRICS: Final = ("rank_ic", "ew_minus_worst_decile", "survivors_minus_ew", "top_minus_bottom")

EXCHANGE: Final = "SSE"
"""The calendar every panel build and the survivor day read (`SurvivorAdapterConfig.exchange`)."""
DEFAULT_TAILS: Final[tuple[int, ...]] = (1, 2, 3)
DEFAULT_POWER_MEANS: Final[tuple[float, ...]] = (0.037, 0.05, 0.1)
DEFAULT_POWER_PERIODS: Final[tuple[int, ...]] = (12, 36, 56)
DEFAULT_POWER_TARGET: Final = 0.8
DEFAULT_POWER_TEST_LEVEL: Final = 0.05
"""The P7 route's arithmetic (spec section 3.6): K = 12, 36, 56 periods, true mean ICs 0.037,
0.05 and 0.10, 80% at a one-sided 0.05."""
SEED_NOTE: Final = (
    "the book's seed picks holdings among the survivors and never enters the screen the scores "
    "are read from"
)

DAY_REFUSALS: Final[tuple[type[Exception], ...]] = (
    *SURVIVOR_RECOMPUTE_REFUSALS,
    ModelRunBlockedError,
    ModelPanelUnreadableError,
)
"""What turns a signal day (or one horizon of it) into a named gap, and nothing else: the
late-record check's refusals of the data, and the label reader's two. `DiagnosticWindowError` is
not one of them and must never be (a test holds it)."""

_ROOT: Final[Path] = Path(__file__).resolve().parent


class DiagnosticRequestError(ValueError):
    """A request or an input the diagnostics cannot read, refused by name before or instead of a
    reading. Never a gap."""


class ScoreSource(Protocol):
    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        """Keys: `COMPOSITE` and each of the composite's factor ids; values: the eligible scope's
        `(ts_code, score)`, ascending by code."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelScoreSource:
    """The daily path's own scoring: `build_facts_day` then `screen`, read-only."""

    store: PanelStore
    book: SurvivorBookConfig
    transform: FactorTransformSpec
    exchange: str
    as_of: datetime
    min_coverage: float
    max_carry_sessions: int

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        day = build_facts_day(
            store=self.store,
            session=session,
            as_of=self.as_of,
            config=self.book,
            factor_ids=self.book.composite_factors,
            transform=self.transform,
            exchange=self.exchange,
            min_coverage=self.min_coverage,
            max_carry_sessions=self.max_carry_sessions,
        )
        facts = {fact.ts_code: fact for fact in day.facts}
        eligible = sorted(
            (verdict.ts_code, verdict.composite)
            for verdict in screen(day.facts, self.book).verdicts
            if verdict.eligible
        )
        scores: dict[str, tuple[tuple[str, float], ...]] = {
            COMPOSITE: tuple((code, _present(code, value)) for code, value in eligible)
        }
        for index, factor in enumerate(self.book.composite_factors):
            scores[factor] = tuple(
                (code, _present(code, facts[code].oriented_values[index])) for code, _ in eligible
            )
        return scores


def _present(code: str, value: float | None) -> float:
    """An eligible name's score; `screen` makes a name with a missing value ineligible."""
    if value is None:
        raise DiagnosticRequestError(f"{code} is eligible and carries no value")
    return value


def label_years(year: int, *, ceiling_year: int) -> tuple[int, ...]:
    """The partitions a signal year's label reader spans: the year before through the year after
    (`strategy_view._measure_year`), and never past the window's ceiling."""
    return tuple(range(year - 1, min(year + 1, ceiling_year) + 1))


def panel_label_source(
    store: PanelStore,
    *,
    years: Sequence[int],
    exchange: str,
    as_of: datetime,
    cached_sessions: int,
) -> OutcomeLabels:
    """The forward report's label reader over `years`, every label read at `as_of`."""
    return OutcomeLabels(
        store,
        LabelReach(as_of=as_of, years=tuple(years), exchange=exchange),
        cached_sessions=cached_sessions,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagnosticRequest:
    """What to read: the plan, the scores, the tails and the power questions to put."""

    plan: SignalPlan
    scores: tuple[str, ...]
    tails: tuple[int, ...]
    loser_decile_count: int
    power_means: tuple[float, ...]
    power_periods: tuple[int, ...]
    power_target: float
    power_test_level: float

    def __post_init__(self) -> None:
        if not self.scores or len(set(self.scores)) != len(self.scores):
            raise DiagnosticRequestError(
                f"the scores must be named, each once; got {list(self.scores)}"
            )
        if any(not 1 <= k < DECILES for k in self.tails) or len(set(self.tails)) != len(self.tails):
            raise DiagnosticRequestError(
                f"each tail is a distinct number of deciles in 1..{DECILES - 1}; got "
                f"{list(self.tails)}"
            )
        if not 0 <= self.loser_decile_count < DECILES:
            raise DiagnosticRequestError(
                f"loser_decile_count {self.loser_decile_count} is outside 0..{DECILES - 1}"
            )
        if not self.power_means or any(
            not math.isfinite(mean) or mean <= 0 for mean in self.power_means
        ):
            raise DiagnosticRequestError(
                f"each power mean must be positive and finite; got {list(self.power_means)}"
            )
        if not self.power_periods or any(
            type(periods) is not int or periods < 2 for periods in self.power_periods
        ):
            raise DiagnosticRequestError(
                f"each power period count must be a whole number of at least 2; got "
                f"{list(self.power_periods)}"
            )
        if not 0 < self.power_target < 1:
            raise DiagnosticRequestError(
                f"the target power must lie strictly between 0 and 1, not {self.power_target}"
            )
        if not 0 < self.power_test_level < 0.5:
            raise DiagnosticRequestError(
                f"the test level must lie strictly between 0 and 0.5, not {self.power_test_level}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class DayReading:
    """One (signal day, score, horizon): its readout, or the named reason the data refused it."""

    session: date
    score: str
    horizon: int
    readout: CrossSectionReadout | None
    unlabelled_by_code: tuple[tuple[str, int], ...]
    """Names per refusal code, ascending by code; a name with two codes counts under both."""
    refusal: str | None
    """The data's named refusal (a gap). A window breach is never here: it raises."""


# --- the engine ----------------------------------------------------------------------------------


def run_diagnostics(
    request: DiagnosticRequest,
    *,
    scores: ScoreSource,
    labels_for: Callable[[int], LabelSource],
) -> tuple[DayReading, ...]:
    """Every (signal day, horizon, score) of `request`, in that order; see the module docstring.

    `labels_for(year)` is called once per signal year, when the year's first day is read, and the
    previous year's reader is let go first.
    """
    plan = request.plan
    _require_inside(plan)
    readings: list[DayReading] = []
    reader: tuple[int, LabelSource] | None = None
    for day in plan.sessions:
        breach: DiagnosticWindowError | None = None
        try:
            scored = scores.scores_on(day)
            if reader is None or reader[0] != day.year:
                reader = None
                reader = (day.year, labels_for(day.year))
            labels = reader[1]
        except DiagnosticWindowError:
            raise
        except DAY_REFUSALS as error:
            breach = _window_refusal_in(error)
            if breach is None:
                reason = _gap_reason(error)
                readings.extend(
                    _refused(day, score, horizon, reason)
                    for horizon in plan.horizons
                    for score in request.scores
                )
                continue
        if breach is not None:
            # Raised here, after the handler, so the refusal it was found in does not become its
            # context: the window refusal itself propagates, not the wrapper.
            raise breach
        missing = [score for score in request.scores if score not in scored]
        if missing:
            raise DiagnosticRequestError(
                f"the score source has no {missing} on {day.isoformat()}; it scores "
                f"{sorted(scored)}"
            )
        unreadable = {score: _scope_refusal(scored[score]) for score in request.scores}
        for horizon in plan.horizons:
            readings.extend(_horizon_readings(request, day, horizon, labels, scored, unreadable))
    return tuple(readings)


def _horizon_readings(
    request: DiagnosticRequest,
    day: date,
    horizon: int,
    labels: LabelSource,
    scored: Mapping[str, tuple[tuple[str, float], ...]],
    unreadable: Mapping[str, str | None],
) -> list[DayReading]:
    """One horizon of one day, every score: the exit checked on the reader's calendar before any
    label is asked, then the readable scores' labels, or the gap that refused them."""
    window = request.plan.window
    _require_exit_inside(labels.calendar, day, horizon, window)
    readable = [score for score in request.scores if unreadable[score] is None]
    answers: dict[str, OutcomeLabel | None] = {}
    breach: DiagnosticWindowError | None = None
    if readable:
        try:
            answers = _labels_of(labels, day, horizon, window, readable, scored)
        except DiagnosticWindowError:
            raise
        except DAY_REFUSALS as error:
            breach = _window_refusal_in(error)
            if breach is None:
                reason = _gap_reason(error)
                return [
                    _refused(day, score, horizon, unreadable[score] or reason)
                    for score in request.scores
                ]
        if breach is not None:
            raise breach
    readings: list[DayReading] = []
    for score in request.scores:
        refusal = unreadable[score]
        if refusal is None:
            readings.append(_reading(day, score, horizon, scored[score], answers, request))
        else:
            readings.append(_refused(day, score, horizon, refusal))
    return readings


def _scope_refusal(scored: tuple[tuple[str, float], ...]) -> str | None:
    """Why one score's scope on one day cannot be read as a cross section, or `None`: a gap of
    that day, never an abort of the run."""
    if not scored:
        return "empty_scope: the score source scored no name"
    unfinite = sorted(code for code, score in scored if not math.isfinite(score))
    if unfinite:
        return f"non_finite_score: the score of {unfinite} is not finite"
    return None


def _require_exit_inside(
    calendar: TradingCalendar, day: date, horizon: int, window: DiagnosticWindow
) -> None:
    """Refuse a label that exits after `window.last`, or that the reader's calendar cannot place
    at all (`p7_windows.label_exit_day`; a reader capped at the window's last year stops on the
    window's last day, so a label past it is not on its calendar) -- before any label is asked."""
    exit_day = label_exit_day(calendar, day, horizon)
    if exit_day is not None and exit_day <= window.last:
        return
    where = (
        f"cannot be placed on the label reader's calendar, which stops at "
        f"{calendar.horizon.last_date.isoformat()}"
        if exit_day is None
        else f"exits {exit_day.isoformat()}"
    )
    raise DiagnosticWindowError(
        f"the {horizon}-session label of {day.isoformat()} {where}, past the {window.name} "
        f"window's last day {window.last.isoformat()}; {READ_ONCE_NOTE}"
    )


def _require_inside(plan: SignalPlan) -> None:
    window = plan.window
    for day in plan.sessions:
        if not window.first <= day <= window.last:
            raise DiagnosticWindowError(
                f"the signal day {day.isoformat()} is outside the {window.name} window "
                f"{window.first.isoformat()}..{window.last.isoformat()}; {READ_ONCE_NOTE}"
            )


def _labels_of(
    labels: LabelSource,
    day: date,
    horizon: int,
    window: DiagnosticWindow,
    names: Sequence[str],
    scored: Mapping[str, tuple[tuple[str, float], ...]],
) -> dict[str, OutcomeLabel | None]:
    """Every scored name's label over `day`'s `horizon`-session window, asked once per name and
    only after the window is known to end inside `window`."""
    label_window = labels.window(
        session_publication_instant(day),
        horizon=ResearchHorizon(count=horizon, unit=HorizonUnit.trading_days),
    )
    if label_window.exit_day > window.last:
        raise DiagnosticWindowError(
            f"the {horizon}-session label of {day.isoformat()} exits "
            f"{label_window.exit_day.isoformat()}, past the {window.name} window's last day "
            f"{window.last.isoformat()}; {READ_ONCE_NOTE}"
        )
    codes = sorted({code for name in names for code, _ in scored[name]})
    return {code: labels.label(code, label_window) for code in codes}


def _reading(
    day: date,
    score: str,
    horizon: int,
    scored: tuple[tuple[str, float], ...],
    answers: Mapping[str, OutcomeLabel | None],
    request: DiagnosticRequest,
) -> DayReading:
    """`strategy_survivor_forward._labelled`'s count, then `read_cross_section`."""
    values: dict[str, float] = {}
    codes: Counter[str] = Counter()
    for code, _ in scored:
        label = answers[code]
        if label is None:
            codes[NO_ADJUSTMENT_HISTORY] += 1
        elif not label.is_labelled:
            codes.update(sorted({refusal.code for refusal in label.refusals}))
        else:
            values[code] = label.realized_return
    return DayReading(
        session=day,
        score=score,
        horizon=horizon,
        readout=read_cross_section(
            scored, values, loser_decile_count=request.loser_decile_count, tails=request.tails
        ),
        unlabelled_by_code=tuple(sorted(codes.items())),
        refusal=None,
    )


def _refused(day: date, score: str, horizon: int, reason: str) -> DayReading:
    return DayReading(
        session=day,
        score=score,
        horizon=horizon,
        readout=None,
        unlabelled_by_code=(),
        refusal=reason,
    )


def _window_refusal_in(error: BaseException) -> DiagnosticWindowError | None:
    """The window refusal anywhere behind `error`, or `None`.

    Both links of every exception are followed -- `__cause__` (raised from it) and `__context__`
    (raised while it was being handled) -- so a refusal raised from an unrelated error while a
    window refusal was in hand still finds it: the walk fails closed."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, DiagnosticWindowError):
            return current
        pending.extend(
            link for link in (current.__cause__, current.__context__) if link is not None
        )
    return None


def _gap_reason(error: Exception) -> str:
    """The gap's name: the refusal's type and its `disclosable` message where it has one (a
    `ModelViewError`'s, which names no local store path), so a document does not depend on where
    the panel lives."""
    return f"{type(error).__name__}: {getattr(error, 'disclosable', str(error))}"


# --- the summary ---------------------------------------------------------------------------------


def summarize(
    request: DiagnosticRequest,
    readings: Sequence[DayReading],
    *,
    power_wording: str = EXPLORATORY_POWER_WORDING,
) -> dict[str, Any]:
    """`summary[score][f"h{h}"]`: the non-overlapping sample's series; see the module docstring.
    `power_wording` is the power block's (`READ_ONCE_POWER_WORDING` in a read-once document)."""
    plan = request.plan
    found = {(item.session, item.score, item.horizon): item for item in readings}
    summary: dict[str, Any] = {}
    for score in request.scores:
        blocks: dict[str, Any] = {}
        for horizon in plan.horizons:
            every = math.ceil(horizon / plan.stride)
            sampled = plan.sessions[::every]
            chosen: list[DayReading] = []
            for day in sampled:
                reading = found.get((day, score, horizon))
                if reading is None:
                    raise DiagnosticRequestError(
                        f"no reading of {score} at h={horizon} on {day.isoformat()}"
                    )
                chosen.append(reading)
            read = [(item.session, item.readout) for item in chosen if item.readout is not None]
            unlabelled: Counter[str] = Counter()
            for item in chosen:
                unlabelled.update(dict(item.unlabelled_by_code))
            rank_ic = summarize_series(_present_values((day, out.rank_ic) for day, out in read))
            block: dict[str, Any] = {
                "horizon": horizon,
                "every_nth_signal": every,
                "sampled": [day.isoformat() for day in sampled],
                "read": len(read),
                "gaps": [
                    {"session": item.session.isoformat(), "reason": item.refusal}
                    for item in chosen
                    if item.refusal is not None
                ],
                "rank_ic": _series_form(rank_ic),
            }
            for metric in METRICS[1:]:
                block[metric] = _series_form(
                    summarize_series(
                        _present_values((day, getattr(out, metric)) for day, out in read)
                    )
                )
            block["decile_excess"] = [
                {
                    "decile": decile,
                    **_series_form(
                        summarize_series(
                            _present_values((day, out.decile_excess[decile]) for day, out in read)
                        )
                    ),
                }
                for decile in range(DECILES)
            ]
            block["tail_excess"] = [
                {
                    "k": k,
                    **_series_form(
                        summarize_series(
                            _present_values((day, dict(out.tail_excess)[k]) for day, out in read)
                        )
                    ),
                }
                for k in request.tails
            ]
            block["unlabelled_by_code"] = dict(sorted(unlabelled.items()))
            block["power_if_planning_sd_were_this"] = _power_block(
                request, rank_ic.sd, power_wording
            )
            blocks[f"h{horizon}"] = block
        summary[score] = blocks
    return summary


def _present_values(points: Iterable[tuple[date, float | None]]) -> list[tuple[date, float]]:
    return [(day, value) for day, value in points if value is not None]


def _series_form(summary: SeriesSummary) -> dict[str, Any]:
    return {
        "n": summary.n,
        "mean": summary.mean,
        "sd": summary.sd,
        "se": summary.se,
        "t": summary.t,
        "share_positive": summary.share_positive,
        "lag1_autocorrelation": summary.lag1_autocorrelation,
        "by_year": [{"year": year, "n": n, "mean": mean} for year, n, mean in summary.by_year],
        "absence": summary.absence,
    }


def _power_block(
    request: DiagnosticRequest, sd: float | None, wording: str
) -> dict[str, Any] | None:
    """The calculator at `sd`, as a *what if* worded by the document's kind; `None` when the
    series has no spread."""
    if sd is None or not sd > 0:
        return None
    level, target = request.power_test_level, request.power_target
    block: dict[str, Any] = {
        "wording": wording,
        "sd": sd,
        "test_level": level,
        "target_power": target,
    }
    kinds: tuple[CriticalValue, ...] = ("normal", "t")
    for kind in kinds:
        block[kind] = {
            "power": [
                {
                    "periods": periods,
                    "mean": mean,
                    "power": power_of_mean_test(
                        mean=mean, sd=sd, periods=periods, test_level=level, kind=kind
                    ),
                }
                for periods in request.power_periods
                for mean in request.power_means
            ],
            "periods_for_target": [
                {
                    "mean": mean,
                    "periods": periods_for_power(
                        mean=mean, power=target, sd=sd, test_level=level, kind=kind
                    ),
                }
                for mean in request.power_means
            ],
        }
    return block


# --- the document and its lines ------------------------------------------------------------------


def diagnostic_document(
    request: DiagnosticRequest,
    readings: Sequence[DayReading],
    *,
    config: Mapping[str, Any],
    code_commit: str,
    partitions_read: Sequence[tuple[str, int, str | None]],
    binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The diagnostics as one JSON-ready document; exploratory unless `binding` is given."""
    plan = request.plan
    window = plan.window
    request_form = _request_form(request)
    config_form = grid.to_json_value(dict(config))
    digest = hashlib.sha256(
        grid.canonical_json({"config": config_form, "request": request_form}).encode("utf-8")
    ).hexdigest()
    if binding is None:
        kind = EXPLORATORY_KIND
        wording = [EXPLORATORY_WORDING, IN_SAMPLE_WORDING, CANDIDATE_WORDING]
    else:
        kind = READ_ONCE_KIND
        wording = [read_once_wording(window.name), CANDIDATE_WORDING]
        if window.name == SECOND_USE.name:
            wording.append(SECOND_USE_WORDING)
    return {
        "kind": kind,
        "wording": wording,
        "window": _window_form(window),
        "plan": {
            "first": plan.first.isoformat(),
            "last": plan.last.isoformat(),
            "stride": plan.stride,
            "horizons": list(plan.horizons),
            "sessions": [day.isoformat() for day in plan.sessions],
            "embargoed": [day.isoformat() for day in plan.embargoed],
        },
        "request": request_form,
        "config": config_form,
        "provenance": {
            "code_commit": code_commit,
            "config_digest": digest,
            "random_component": "none",
            "partitions_read": [
                {"dataset": dataset, "year": year, "content_hash": content_hash}
                for dataset, year, content_hash in partitions_read
            ],
        },
        "binding": None if binding is None else grid.to_json_value(dict(binding)),
        "days": [_day_form(item) for item in readings],
        "summary": summarize(
            request,
            readings,
            power_wording=(
                EXPLORATORY_POWER_WORDING if binding is None else READ_ONCE_POWER_WORDING
            ),
        ),
    }


def _window_form(window: DiagnosticWindow) -> dict[str, Any]:
    return {
        "name": window.name,
        "first": window.first.isoformat(),
        "last": window.last.isoformat(),
        "ceiling_year": window.ceiling_year,
    }


def _request_form(request: DiagnosticRequest) -> dict[str, Any]:
    """What the request asks, without what the calendar answered (the plan's sessions)."""
    plan = request.plan
    return {
        "window": _window_form(plan.window),
        "first": plan.first.isoformat(),
        "last": plan.last.isoformat(),
        "stride": plan.stride,
        "horizons": list(plan.horizons),
        "scores": list(request.scores),
        "tails": list(request.tails),
        "loser_decile_count": request.loser_decile_count,
        "power_means": list(request.power_means),
        "power_periods": list(request.power_periods),
        "power_target": request.power_target,
        "power_test_level": request.power_test_level,
    }


def _day_form(reading: DayReading) -> dict[str, Any]:
    return {
        "session": reading.session.isoformat(),
        "score": reading.score,
        "horizon": reading.horizon,
        "refusal": reading.refusal,
        "unlabelled_by_code": dict(reading.unlabelled_by_code),
        "readout": None if reading.readout is None else dataclasses.asdict(reading.readout),
    }


def read_once_wording(window_name: str) -> str:
    """A read-once document's first wording, by window: the second-use window's one reading is a
    secondary reading of a spent holdout (`SECOND_USE_READ_ONCE_WORDING`); any other window's is
    a first out-of-sample reading (`READ_ONCE_WORDING`)."""
    return SECOND_USE_READ_ONCE_WORDING if window_name == SECOND_USE.name else READ_ONCE_WORDING


def read_once_banner(reading: str, window_name: str, span: str) -> str:
    """The first line of a read-once document's text, by window (`p7_reading` opens its result
    text with the same line)."""
    headline = SECOND_USE_HEADLINE if window_name == SECOND_USE.name else READ_ONCE_HEADLINE
    return f"PRE-REGISTERED READ-ONCE {reading} -- {headline}: {span}"


def diagnostic_lines(document: Mapping[str, Any]) -> list[str]:
    """The document as text: the banner first, then the wording, the plan and every summary."""
    window = document["window"]
    span = f"{window['name']} window {window['first']}..{window['last']}"
    if document["kind"] == EXPLORATORY_KIND:
        banner = f"EXPLORATORY, UNREGISTERED -- NOT EVIDENCE: {span}"
    else:
        banner = read_once_banner(document["binding"]["reading"], window["name"], span)
    plan, provenance = document["plan"], document["provenance"]
    lines = [banner, *(f"  {wording}" for wording in document["wording"])]
    lines.append(
        f"plan {plan['first']}..{plan['last']}, one signal day every {plan['stride']} sessions, "
        f"horizons {', '.join(str(h) for h in plan['horizons'])}: {len(plan['sessions'])} signal "
        f"days, {len(plan['embargoed'])} embargoed (a label that would end past the window)"
    )
    lines.append(
        f"code {provenance['code_commit']}, config {provenance['config_digest']}, random component "
        f"{provenance['random_component']}, {len(provenance['partitions_read'])} partition(s) read"
    )
    binding = document["binding"]
    if binding is not None:
        lines.append("binding: " + ", ".join(f"{key} {binding[key]}" for key in sorted(binding)))
    for score in document["request"]["scores"]:
        for horizon in plan["horizons"]:
            lines.extend(_block_lines(score, document["summary"][score][f"h{horizon}"]))
    return lines


def _block_lines(score: str, block: Mapping[str, Any]) -> list[str]:
    lines = [
        f"{score} at h={block['horizon']}: {len(block['sampled'])} non-overlapping signal days "
        f"(every {block['every_nth_signal']} of the plan's), {block['read']} read, "
        f"{len(block['gaps'])} gap(s); gap and unlabelled counts cover the sampled days only"
    ]
    lines.extend(f"  {metric}: {_series_text(block[metric])}" for metric in METRICS)
    lines.append(
        "  decile mean excess over equal weight (0 = worst): "
        + "; ".join(f"{row['decile']} {_number(row['mean'])}" for row in block["decile_excess"])
    )
    lines.append(
        "  mean excess after dropping the worst k tenths: "
        + "; ".join(
            f"k={row['k']} {_number(row['mean'])} (n {row['n']})" for row in block["tail_excess"]
        )
    )
    if block["unlabelled_by_code"]:
        lines.append(
            "  unlabelled names: "
            + ", ".join(f"{code} {count}" for code, count in block["unlabelled_by_code"].items())
        )
    lines.extend(f"  gap {gap['session']}: {gap['reason']}" for gap in block["gaps"])
    power = block["power_if_planning_sd_were_this"]
    if power is not None:
        # The block's own wording, chosen by the document's kind (`diagnostic_document`), so the
        # text says what the JSON says.
        lines.append(
            f"  rank_ic power {power['wording']}: sd {_number(power['sd'])}, one-sided "
            f"test level {power['test_level']:g}"
        )
        for kind in ("normal", "t"):
            rows = power[kind]
            lines.append(
                f"    {kind}: "
                + "; ".join(
                    f"K={row['periods']} mean {row['mean']:g} power {row['power']:.3f}"
                    for row in rows["power"]
                )
                + f"; periods for {power['target_power']:g}: "
                + ", ".join(
                    f"mean {row['mean']:g} {row['periods']}" for row in rows["periods_for_target"]
                )
            )
    return lines


def _series_text(series: Mapping[str, Any]) -> str:
    text = (
        f"n {series['n']}, mean {_number(series['mean'])}, sd {_number(series['sd'])}, "
        f"t {_number(series['t'])}, share above zero {_number(series['share_positive'])}, "
        f"lag-1 autocorrelation {_number(series['lag1_autocorrelation'])}"
    )
    if series["by_year"]:
        text += ", by year " + ", ".join(
            f"{row['year']} {_number(row['mean'])} (n {row['n']})" for row in series["by_year"]
        )
    if series["absence"] is not None:
        text += f" ({series['absence']})"
    return text


def _number(value: object) -> str:
    return "absent" if value is None else f"{value:.4g}"


# --- the command line ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ExploreSettings:
    """The `explore` command's settings, checked before any store is opened."""

    as_of: datetime
    first: date
    last: date
    stride: int
    horizons: tuple[int, ...]
    transform: str
    book: SurvivorBookConfig
    scores: tuple[str, ...]
    min_coverage: float
    max_carry_sessions: int

    def config(self, window: DiagnosticWindow) -> dict[str, Any]:
        """What the document records as its configuration (and digests with the request)."""
        return {
            "window": window.name,
            "exchange": EXCHANGE,
            "as_of": self.as_of,
            "first": self.first,
            "last": self.last,
            "stride": self.stride,
            "horizons": list(self.horizons),
            "transform": self.transform,
            "book": self.book.model_dump(mode="json"),
            "book_seed": SEED_NOTE,
            "scores": list(self.scores),
            "min_coverage": self.min_coverage,
            "max_carry_sessions": self.max_carry_sessions,
        }


def _open_sources(
    runtime_dir: Path, *, window: DiagnosticWindow, config: ExploreSettings
) -> tuple[TradingCalendar, ScoreSource, Callable[[int], LabelSource], WindowCappedStore]:
    """The one place the command opens a store: the panel inside `runtime_dir`, capped at
    `window`, and the calendar, scores and labels read off it."""
    store = capped_panel_store(runtime_dir, window)
    calendar = load_trading_calendar(
        store,
        exchange=EXCHANGE,
        years=tuple(range(config.first.year - 1, window.ceiling_year + 1)),
        as_of=config.as_of,
    )
    scores = PanelScoreSource(
        store=store,
        book=config.book,
        transform=FACTOR_TRANSFORMS.get(config.transform),
        exchange=EXCHANGE,
        as_of=config.as_of,
        min_coverage=config.min_coverage,
        max_carry_sessions=config.max_carry_sessions,
    )
    cached_sessions = max(config.horizons) + 2

    def labels_for(year: int) -> LabelSource:
        return panel_label_source(
            store,
            years=label_years(year, ceiling_year=window.ceiling_year),
            exchange=EXCHANGE,
            as_of=config.as_of,
            cached_sessions=cached_sessions,
        )

    return calendar, scores, labels_for, store


def main(argv: Sequence[str] | None = None) -> int:
    """`explore` reads the research window; `power` computes and reads nothing."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "explore":
        return _explore(arguments)
    return _power(arguments)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p7_diagnostics",
        description="Read-only P7 diagnostics: exploratory, unregistered, not evidence.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    research = WINDOWS["research"]
    explore = commands.add_parser(
        "explore",
        help="read the research window's deciles, tails and rank IC",
        description=(
            f"Read the research window ({research.first}..{research.last}) and nothing else; "
            "the validation window and the holdout are read only through p7_reading.read_once."
        ),
    )
    explore.add_argument("--runtime-dir", type=Path, required=True)
    explore.add_argument("--as-of", required=True, help="an aware ISO instant every read is at")
    explore.add_argument("--first", type=date.fromisoformat, required=True)
    explore.add_argument("--last", type=date.fromisoformat, required=True)
    explore.add_argument("--stride", type=int, required=True)
    explore.add_argument("--horizons", required=True, help="comma-separated sessions, e.g. 20,5")
    explore.add_argument("--transform", required=True, help="a declared transform key")
    explore.add_argument("--book", help="a SurvivorBookConfig as JSON (default: seed 0)")
    explore.add_argument("--scores", default=COMPOSITE, help="composite and/or factor ids")
    explore.add_argument("--min-coverage", type=float, default=DEFAULT_MIN_COVERAGE)
    explore.add_argument("--max-carry-sessions", type=int, default=DEFAULT_MAX_CARRY_SESSIONS)
    explore.add_argument("--out", type=Path, required=True)

    power = commands.add_parser("power", help="the power of a design; reads no data")
    power.add_argument("--periods", type=int, required=True)
    power.add_argument("--sd", type=float, required=True)
    power.add_argument("--test-level", type=float, required=True)
    power.add_argument("--means", required=True, help="comma-separated true means")
    power.add_argument("--target-power", type=float, required=True)
    power.add_argument("--critical-value", choices=("normal", "t"), required=True)
    power.add_argument("--interim", type=int)
    power.add_argument("--futility-threshold", type=float)
    power.add_argument("--family-size", type=int)
    power.add_argument("--q", type=float)
    return parser


def _refuse(command: str, error: object, code: int) -> int:
    print(f"p7_diagnostics {command}: {error}", file=sys.stderr)
    return code


def _explore(arguments: argparse.Namespace) -> int:
    window = WINDOWS["research"]
    try:
        settings = _explore_settings(arguments, window)
    except (ValueError, FactorTransformError) as error:
        return _refuse("explore", error, 2)
    try:
        calendar, scores, labels_for, store = _open_sources(
            arguments.runtime_dir, window=window, config=settings
        )
        plan = plan_signal_days(
            calendar,
            window=window,
            first=settings.first,
            last=settings.last,
            stride=settings.stride,
            horizons=settings.horizons,
        )
        request = DiagnosticRequest(
            plan=plan,
            scores=settings.scores,
            tails=DEFAULT_TAILS,
            loser_decile_count=settings.book.loser_decile_count,
            power_means=DEFAULT_POWER_MEANS,
            power_periods=DEFAULT_POWER_PERIODS,
            power_target=DEFAULT_POWER_TARGET,
            power_test_level=DEFAULT_POWER_TEST_LEVEL,
        )
        readings = run_diagnostics(request, scores=scores, labels_for=labels_for)
        document = diagnostic_document(
            request,
            readings,
            config=settings.config(window),
            code_commit=resolve_code_commit(anchor=_ROOT),
            # Taken after the run, outside `PanelStore.reading()`: fine for an exploratory
            # listing, and not to be copied by the read-once path, which must hash with the reads.
            partitions_read=store.partitions_read(),
        )
    except DiagnosticWindowError as error:
        return _refuse("explore", error, 2)
    except DiagnosticRequestError as error:
        return _refuse("explore", error, 2)
    except DAY_REFUSALS as error:
        return _refuse("explore", f"{type(error).__name__}: {error}", 1)
    stem = f"p7-diagnostic-{window.name}-{document['provenance']['config_digest'][:16]}"
    lines = diagnostic_lines(document)
    outputs = (
        (
            arguments.out / f"{stem}.json",
            (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
        ),
        (arguments.out / f"{stem}.txt", ("\n".join(lines) + "\n").encode("utf-8")),
    )
    conflict = _conflicting(outputs)
    if conflict is not None:
        return _refuse("explore", conflict, 1)
    print("\n".join(lines))
    for path, fresh in _write_missing(outputs):
        print(f"written: {path}" if fresh else f"unchanged, nothing written: {path}")
    return 0


def _explore_settings(arguments: argparse.Namespace, window: DiagnosticWindow) -> ExploreSettings:
    """Every setting checked, and the dates held to `window`, before anything is opened."""
    first, last = arguments.first, arguments.last
    if first < window.first:
        raise DiagnosticWindowError(
            f"the plan's first day {first} is before the {window.name} window's first day "
            f"{window.first}"
        )
    if last > window.last:
        raise DiagnosticWindowError(
            f"the plan's last day {last} is past the {window.name} window's last day "
            f"{window.last}; {READ_ONCE_NOTE}"
        )
    as_of = datetime.fromisoformat(arguments.as_of)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise DiagnosticRequestError(f"--as-of {arguments.as_of} carries no timezone")
    horizons = tuple(int(part) for part in arguments.horizons.split(","))
    book = (
        SurvivorBookConfig(seed=0)
        if arguments.book is None
        else SurvivorBookConfig.model_validate_json(arguments.book)
    )
    FACTOR_TRANSFORMS.get(arguments.transform)
    scores = tuple(part.strip() for part in arguments.scores.split(","))
    repeated = sorted({score for score in scores if scores.count(score) > 1})
    if repeated:
        raise DiagnosticRequestError(f"--scores names {repeated} more than once")
    known = (COMPOSITE, *book.composite_factors)
    unknown = [score for score in scores if score not in known]
    if unknown:
        raise DiagnosticRequestError(f"--scores {unknown} are not among {list(known)}")
    return ExploreSettings(
        as_of=as_of,
        first=first,
        last=last,
        stride=arguments.stride,
        horizons=horizons,
        transform=arguments.transform,
        book=book,
        scores=scores,
        min_coverage=arguments.min_coverage,
        max_carry_sessions=arguments.max_carry_sessions,
    )


def _conflicting(outputs: Sequence[tuple[Path, bytes]]) -> str | None:
    """`registry.register`'s rule, checked for every file before any is written: different bytes
    already on disk are refused."""
    for path, content in outputs:
        if path.exists() and path.read_bytes() != content:
            return (
                f"{path} already holds a different document; a diagnostic is written once "
                "(the same configuration read again gave other bytes)"
            )
    return None


def _write_missing(outputs: Sequence[tuple[Path, bytes]]) -> tuple[tuple[Path, bool], ...]:
    """Write each file not yet on disk; the same bytes are not written again. `(path, written)`."""
    done: list[tuple[Path, bool]] = []
    for path, content in outputs:
        fresh = not path.exists()
        if fresh:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        done.append((path, fresh))
    return tuple(done)


def _power(arguments: argparse.Namespace) -> int:
    if (arguments.interim is None) != (arguments.futility_threshold is None):
        return _refuse("power", "--interim and --futility-threshold go together", 2)
    if (arguments.family_size is None) != (arguments.q is None):
        return _refuse("power", "--family-size and --q go together", 2)
    try:
        means = tuple(float(part) for part in arguments.means.split(","))
        kind: CriticalValue = arguments.critical_value
        lines = list(
            power_table_lines(
                periods=arguments.periods,
                sd=arguments.sd,
                test_level=arguments.test_level,
                means=means,
                target_power=arguments.target_power,
                kind=kind,
            )
        )
        if arguments.interim is not None:
            threshold = arguments.futility_threshold
            for mean in means:
                stop = futility_stop_probability(
                    mean=mean,
                    sd=arguments.sd,
                    interim_periods=arguments.interim,
                    threshold=threshold,
                )
                power = power_with_futility(
                    mean=mean,
                    sd=arguments.sd,
                    periods=arguments.periods,
                    interim_periods=arguments.interim,
                    threshold=threshold,
                    test_level=arguments.test_level,
                    kind=kind,
                )
                lines.append(
                    f"  interim look after {arguments.interim} periods, stopping at a mean at or "
                    f"below {threshold:g}: stop probability {stop:.3f} at mean {mean:g}"
                )
                lines.append(f"  power with the interim look: {power:.3f} at mean {mean:g}")
        if arguments.family_size is not None:
            level = by_first_step_level(q=arguments.q, family_size=arguments.family_size)
            lines.append(
                f"  a secondary under Benjamini-Yekutieli (family {arguments.family_size}, q "
                f"{arguments.q:g}) is powered at the first-step level {level:.4f}, a lower bound"
            )
    except (PowerError, ValueError) as error:
        return _refuse("power", error, 2)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
