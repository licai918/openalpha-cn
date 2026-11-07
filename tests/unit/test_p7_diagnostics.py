"""`scripts/research/p7_diagnostics.py` (`V2-P7-004`): the diagnostics engine over a score source
and a label source, its document and its lines, held to hand-computed numbers on generated
inputs; and the two real sources wired over panels generated at test time (the survivor facts
frame for the scores, the forward tests' label panel for the labels). No research store is read."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import AS_OF, EXCHANGE
from test_strategy_survivor_forward import (
    BOOK as FORWARD_BOOK,
)
from test_strategy_survivor_forward import (
    CALENDAR,
    PANEL_SCORES,
    SESSIONS,
    panel_store,  # noqa: F401  (module fixture)
    reader_of,
    witnessed_on,
)
from test_survivor_facts_panel import (
    CONFIG as FRAME_BOOK,
)
from test_survivor_facts_panel import (
    IDENTITY,
    SIGNAL,
    SIGNAL_INDEX,
    evening,
    full_store,  # noqa: F401  (module fixture)
    read_day,
)

from openalpha_cn.backtest.decile_readout import summarize_series
from openalpha_cn.backtest.power_analysis import (
    by_first_step_level,
    futility_stop_probability,
    periods_for_power,
    power_of_mean_test,
    power_table_lines,
    power_with_futility,
)
from openalpha_cn.domain.horizon import ResearchHorizon
from openalpha_cn.domain.labels import LabelWindow, OutcomeLabel, WindowReturn, build_label_window
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import (
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.model_view import ModelPanelUnreadableError, ModelRunBlockedError
from openalpha_cn.panel.store import ColumnSpec, PanelStorageError, PanelStore
from openalpha_cn.strategy_survivor_facts import SurvivorFactsError
from openalpha_cn.strategy_survivor_forward import record_outcome

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RESEARCH: Final[Path] = ROOT / "scripts" / "research"
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")


def _research_module(name: str) -> ModuleType:
    if str(RESEARCH) not in sys.path:
        sys.path.insert(0, str(RESEARCH))
    return importlib.import_module(name)


grid = _research_module("grid")
p7_windows = _research_module("p7_windows")
p7 = _research_module("p7_diagnostics")
FIXTURE_WINDOW = p7_windows.DiagnosticWindow(
    name="fixture", first=date(2026, 1, 5), last=date(2026, 12, 31), ceiling_year=2026
)
"""The generated panels are 2026's; the engine takes any window, the CLI only the research one."""
CODES: Final[tuple[str, ...]] = tuple(f"6000{n:02d}.SH" for n in range(20))
TAIL: Final[Mapping[int, float]] = {0: -0.03, 1: -0.03, 2: -0.01, 3: -0.01}
FORBIDDEN: Final = re.compile(
    r"\b(significant|effective|confirmed|alpha|outperform|passed|failed|proven)\b",
    flags=re.IGNORECASE,
)
"""The forward report's verdict words, as words (`test_strategy_survivor_forward.verdict_words`):
the document's own `provenance` key is vocabulary, not a verdict."""
FORBIDDEN_PREFIXES: Final = re.compile(
    r"\b(significant|confirmed|alpha|effective|outperform)", flags=re.IGNORECASE
)
"""`test_forward_report_survivor.FORBIDDEN`: no trailing boundary, so `effectively` counts too."""
FIXED_CONFIG: Final[Mapping[str, Any]] = {
    "as_of": datetime(2026, 10, 9, tzinfo=SHANGHAI),
    "transform": "probe_identity/v1",
}


class TableLabels:
    """A label source on a calendar (the generated 2026 one unless told) answering from
    `answer(entry_day, code)`, counting every question."""

    def __init__(
        self, answer: Callable[[date, str], float | None], *, calendar: Any = CALENDAR
    ) -> None:
        self.calendar = calendar
        self.answer = answer
        self.asked: list[tuple[str, date]] = []

    def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow:
        return build_label_window(
            as_of=instant, zone=SHANGHAI, horizon=horizon, calendar=self.calendar
        )

    def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
        self.asked.append((ts_code, window.entry_day))
        value = self.answer(window.entry_day, ts_code)
        if value is None:
            return None
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
                published=value,
                adjusted=value,
                unadjusted=value,
                entry_adjusted_close=1.0,
                exit_adjusted_close=1.0 + value,
                per_session=(),
            ),
        )


class FixedScores:
    def __init__(
        self, scored: Mapping[str, float], *, refuse_on: frozenset[date] = frozenset()
    ) -> None:
        self.scored = tuple(sorted(scored.items()))
        self.refuse_on = refuse_on
        self.asked: list[date] = []

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        self.asked.append(session)
        if session in self.refuse_on:
            raise SurvivorFactsError(f"no 16:30 build on {session.isoformat()}")
        return {p7.COMPOSITE: self.scored}


def ranked() -> FixedScores:
    """Code `p` of `CODES` scored `p`: worst first is `CODES`' own order."""
    return FixedScores({code: float(p) for p, code in enumerate(CODES)})


def request(
    *,
    first: date = SESSIONS[0],
    last: date = date(2026, 6, 30),
    stride: int = 20,
    horizons: tuple[int, ...] = (20,),
    window: Any = FIXTURE_WINDOW,
) -> Any:
    plan = p7_windows.plan_signal_days(
        CALENDAR, window=window, first=first, last=last, stride=stride, horizons=horizons
    )
    return p7.DiagnosticRequest(
        plan=plan,
        scores=(p7.COMPOSITE,),
        tails=(1, 2, 3),
        loser_decile_count=2,
        power_means=(0.037, 0.05, 0.1),
        power_periods=(12, 36, 56),
        power_target=0.8,
        power_test_level=0.05,
    )


def tail_answer(entry: date, code: str) -> float:
    """The loser tail of `test_decile_readout`, doubled on every second signal's entry."""
    position = CODES.index(code)
    scale = 2.0 if CALENDAR.trading_days.index(entry) % 40 == 21 else 1.0
    return TAIL.get(position, 0.01) * scale


def drifting(entry: date, code: str) -> float:
    """A label whose order changes from one signal to the next, so the rank IC has a spread."""
    multiplier = CALENDAR.trading_days.index(entry) % 3 + 2
    return (CODES.index(code) * multiplier % 20) / 1000 - 0.005


# --- the engine --------------------------------------------------------------------------------


def test_the_engine_reads_each_signal_day_as_the_readout_does() -> None:
    read = request(last=date(2026, 3, 31))
    labels = TableLabels(tail_answer)
    readings = p7.run_diagnostics(read, scores=ranked(), labels_for=lambda year: labels)

    assert [item.session for item in readings] == list(read.plan.sessions)
    first = readings[0].readout
    assert first is not None
    assert first.rank_ic == pytest.approx(math.sqrt(324 / 665), abs=1e-12)
    assert first.ew_minus_worst_decile == pytest.approx(0.034, abs=1e-15)
    assert readings[1].readout is not None
    assert readings[1].readout.ew_minus_worst_decile == pytest.approx(0.068, abs=1e-15)
    assert all(item.refusal is None and item.unlabelled_by_code == () for item in readings)


def test_the_summary_is_the_series_summary_of_the_non_overlapping_readings() -> None:
    read = request(stride=5, horizons=(5, 20), last=date(2026, 4, 30))
    readings = p7.run_diagnostics(
        read, scores=ranked(), labels_for=lambda year: TableLabels(tail_answer)
    )
    summary = p7.summarize(read, readings)

    sessions = read.plan.sessions
    assert summary[p7.COMPOSITE]["h5"]["sampled"] == [day.isoformat() for day in sessions]
    assert summary[p7.COMPOSITE]["h20"]["sampled"] == [day.isoformat() for day in sessions[::4]]
    h20 = [item for item in readings if item.horizon == 20 and item.session in sessions[::4]]
    expected = summarize_series(
        [(item.session, item.readout.ew_minus_worst_decile) for item in h20]
    )
    assert summary[p7.COMPOSITE]["h20"]["ew_minus_worst_decile"]["mean"] == expected.mean
    assert summary[p7.COMPOSITE]["h20"]["ew_minus_worst_decile"]["n"] == expected.n
    assert set(summary[p7.COMPOSITE]["h20"]["rank_ic"]) == {
        "n",
        "mean",
        "sd",
        "se",
        "t",
        "share_positive",
        "lag1_autocorrelation",
        "by_year",
        "absence",
    }

    # ceil(20 / 3) = 7 signal days apart, not 20 // 3 = 6: six strides of three is 18 sessions,
    # and two 20-session labels 18 sessions apart overlap.
    every_third = request(stride=3, horizons=(20,), last=date(2026, 4, 30))
    spaced = p7.summarize(
        every_third,
        p7.run_diagnostics(
            every_third, scores=ranked(), labels_for=lambda year: TableLabels(tail_answer)
        ),
    )
    assert spaced[p7.COMPOSITE]["h20"]["sampled"] == [
        day.isoformat() for day in every_third.plan.sessions[::7]
    ]


def test_a_cyclic_no_signal_input_shows_no_structure() -> None:
    """Ten names over ten consecutive sessions at h=1: mean IC 0, every decile's mean excess 0."""
    codes = CODES[:10]
    read = request(stride=1, horizons=(1,), last=SESSIONS[9])

    def cyclic(entry: date, code: str) -> float:
        t = CALENDAR.trading_days.index(entry) - 1
        return ((codes.index(code) + t) % 10) / 100

    summary = p7.summarize(
        read,
        p7.run_diagnostics(
            read,
            scores=FixedScores({code: float(r) for r, code in enumerate(codes)}),
            labels_for=lambda year: TableLabels(cyclic),
        ),
    )

    block = summary[p7.COMPOSITE]["h1"]
    assert block["rank_ic"]["n"] == 10
    assert block["rank_ic"]["mean"] == pytest.approx(0.0, abs=1e-12)
    assert len(block["decile_excess"]) == 10
    assert all(row["mean"] == pytest.approx(0.0, abs=1e-15) for row in block["decile_excess"])


def test_a_refused_day_is_a_named_gap_that_never_moves_the_sample() -> None:
    read = request(last=date(2026, 6, 30))
    sessions = read.plan.sessions
    readings = p7.run_diagnostics(
        read,
        scores=FixedScores(
            {code: float(p) for p, code in enumerate(CODES)},
            refuse_on=frozenset({sessions[1]}),
        ),
        labels_for=lambda year: TableLabels(tail_answer),
    )
    summary = p7.summarize(read, readings)

    assert summary[p7.COMPOSITE]["h20"]["sampled"] == [day.isoformat() for day in sessions]
    assert summary[p7.COMPOSITE]["h20"]["gaps"] == [
        {
            "session": sessions[1].isoformat(),
            "reason": f"SurvivorFactsError: no 16:30 build on {sessions[1].isoformat()}",
        }
    ]
    assert summary[p7.COMPOSITE]["h20"]["rank_ic"]["n"] == len(sessions) - 1
    refused = [item for item in readings if item.session == sessions[1]]
    assert [(item.readout, item.unlabelled_by_code) for item in refused] == [(None, ())]


def test_a_label_refusal_is_a_gap_of_its_own_horizon_only() -> None:
    """A label the reader refuses at h=20 leaves the same day's h=5 reading standing."""

    class RefusingLongLabels(TableLabels):
        def label(self, ts_code: str, window: LabelWindow) -> OutcomeLabel | None:
            if window.horizon.count == 20:
                raise ModelRunBlockedError(f"{ts_code} could not be labelled")
            return super().label(ts_code, window)

    read = request(stride=20, horizons=(5, 20), last=date(2026, 3, 31))
    readings = p7.run_diagnostics(
        read, scores=ranked(), labels_for=lambda year: RefusingLongLabels(tail_answer)
    )

    by_horizon = {
        horizon: [item for item in readings if item.horizon == horizon] for horizon in (5, 20)
    }
    assert all(item.readout is not None and item.refusal is None for item in by_horizon[5])
    assert [item.refusal for item in by_horizon[20]] == [
        "ModelRunBlockedError: 600000.SH could not be labelled"
    ] * len(read.plan.sessions)


SECOND: Final = "second_score/v1"


class TwoScores(FixedScores):
    """`ranked()`'s composite, and a second score that is the same except on `on`, where it is
    `second`."""

    def __init__(self, second: tuple[tuple[str, float], ...], *, on: date) -> None:
        super().__init__({code: float(p) for p, code in enumerate(CODES)})
        self.second = second
        self.on = on

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        scored = super().scores_on(session)
        return {**scored, SECOND: self.second if session == self.on else scored[p7.COMPOSITE]}


NAN_FIRST: Final[tuple[tuple[str, float], ...]] = tuple(
    (code, math.nan if p == 0 else float(p)) for p, code in enumerate(CODES)
)


def test_an_empty_scope_or_a_non_finite_score_is_a_named_gap_of_that_day() -> None:
    """An empty scope, or a score that is not a number, does not abort the run: that score's
    readings of that day are gaps, at every horizon, and everything else is read."""
    read = request(last=date(2026, 3, 31), horizons=(5, 20))
    sessions = read.plan.sessions

    emptied = p7.run_diagnostics(
        read, scores=FixedScores({}), labels_for=lambda year: TableLabels(tail_answer)
    )
    assert {item.refusal for item in emptied} == {"empty_scope: the score source scored no name"}
    assert len(emptied) == 2 * len(sessions)

    nan = p7.run_diagnostics(
        read,
        scores=FixedScores(dict(NAN_FIRST)),
        labels_for=lambda year: TableLabels(tail_answer),
    )
    assert {item.refusal for item in nan} == {
        "non_finite_score: the score of ['600000.SH'] is not finite"
    }

    both = dataclasses.replace(read, scores=(p7.COMPOSITE, SECOND))
    for second, reason in (
        ((), "empty_scope: the score source scored no name"),
        (NAN_FIRST, "non_finite_score: the score of ['600000.SH'] is not finite"),
    ):
        readings = p7.run_diagnostics(
            both,
            scores=TwoScores(second, on=sessions[1]),
            labels_for=lambda year: TableLabels(tail_answer),
        )
        refused = [(item.session, item.score, item.horizon) for item in readings if item.refusal]
        assert refused == [(sessions[1], SECOND, 5), (sessions[1], SECOND, 20)]
        assert {item.refusal for item in readings if item.refusal} == {reason}
        assert all(item.readout is not None for item in readings if item.refusal is None)


def test_the_gaps_and_unlabelled_counts_are_the_sampled_days_and_the_line_says_so() -> None:
    """At stride 5 and h=20 every fourth signal day is sampled. A gap and an unlabelled name on a
    day between two sampled ones are in `days` and never in the summary's counts."""
    read = request(stride=5, horizons=(20,), last=date(2026, 4, 30))
    sessions = read.plan.sessions
    unsampled, sampled = sessions[2], sessions[8]
    entries = {CALENDAR.next_trading_day(day): day for day in (unsampled, sampled)}

    def answer(entry: date, code: str) -> float | None:
        return None if entry in entries and code == CODES[5] else tail_answer(entry, code)

    readings = p7.run_diagnostics(
        read,
        scores=FixedScores(
            {code: float(p) for p, code in enumerate(CODES)},
            refuse_on=frozenset({sessions[1], sessions[4]}),
        ),
        labels_for=lambda year: TableLabels(answer),
    )
    document = p7.diagnostic_document(
        read, readings, config=FIXED_CONFIG, code_commit="0" * 40, partitions_read=()
    )
    block = document["summary"][p7.COMPOSITE]["h20"]

    assert sessions[2] not in sessions[::4] and sessions[8] in sessions[::4]
    assert block["gaps"] == [
        {
            "session": sessions[4].isoformat(),
            "reason": f"SurvivorFactsError: no 16:30 build on {sessions[4].isoformat()}",
        }
    ]
    assert block["unlabelled_by_code"] == {"no_adjustment_history": 1}
    assert sum(1 for day in document["days"] if day["refusal"]) == 2
    header = next(line for line in p7.diagnostic_lines(document) if line.startswith("composite"))
    assert "gap and unlabelled counts cover the sampled days only" in header


def test_a_gap_reason_names_no_local_store_path(tmp_path: Path) -> None:
    """A label reader that cannot read its calendar refuses with the store's root in its message
    and a `disclosable` form without it; the document carries the second, so its bytes do not
    depend on where the panel was cloned."""
    root = tmp_path / "clone-zqx7-elsewhere" / "panel"
    PanelStore(root).write_partition("probe", 2026, (ColumnSpec("subject", "VARCHAR"),), [("x",)])
    capped = p7_windows.WindowCappedStore(root, window=FIXTURE_WINDOW)

    def labels_for(year: int) -> Any:
        return p7.panel_label_source(
            capped, years=(year,), exchange=EXCHANGE, as_of=AS_OF, cached_sessions=22
        )

    with pytest.raises(ModelPanelUnreadableError) as raised:
        labels_for(2026)
    assert "clone-zqx7-elsewhere" in str(raised.value)

    read = request(last=date(2026, 3, 31))
    document = p7.diagnostic_document(
        read,
        p7.run_diagnostics(read, scores=ranked(), labels_for=labels_for),
        config=FIXED_CONFIG,
        code_commit="0" * 40,
        partitions_read=(),
    )
    reasons = {gap["reason"] for gap in document["summary"][p7.COMPOSITE]["h20"]["gaps"]}
    assert reasons == {f"ModelPanelUnreadableError: {raised.value.disclosable}"}
    text = json.dumps(document, ensure_ascii=False) + "\n".join(p7.diagnostic_lines(document))
    assert "clone-zqx7-elsewhere" not in text
    assert str(tmp_path) not in text


def test_a_window_refusal_is_never_turned_into_a_gap() -> None:
    class Leaking(FixedScores):
        def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
            raise p7_windows.DiagnosticWindowError("daily [2022] is past the research window")

    with pytest.raises(p7_windows.DiagnosticWindowError):
        p7.run_diagnostics(
            request(), scores=Leaking({}), labels_for=lambda year: TableLabels(tail_answer)
        )
    assert not issubclass(p7_windows.DiagnosticWindowError, p7.DAY_REFUSALS)


BREACH: Final = "daily [2022] is past the research window"


class Wrapping(FixedScores):
    """A loader that wraps whatever a door raised (`AssessedPanelRead._read_held` turns any
    exception into `PanelStorageError`): from the window refusal itself, or -- `unrelated` --
    from some other error while the window refusal was being handled."""

    def __init__(self, *, unrelated: bool) -> None:
        super().__init__({})
        self.unrelated = unrelated
        self.breach = p7_windows.DiagnosticWindowError(BREACH)

    def scores_on(self, session: date) -> Mapping[str, tuple[tuple[str, float], ...]]:
        try:
            raise self.breach
        except p7_windows.DiagnosticWindowError as error:
            cause = KeyError("unrelated") if self.unrelated else error
            raise PanelStorageError("daily year=2022 could not be read") from cause


@pytest.mark.parametrize("unrelated", [False, True], ids=["from_it", "while_handling_it"])
def test_a_refusal_raised_around_a_window_refusal_is_never_turned_into_a_gap(
    unrelated: bool,
) -> None:
    """The window refusal itself propagates, not the wrapper: found through `__cause__` and
    through `__context__` alike, so the walk fails closed."""
    scores = Wrapping(unrelated=unrelated)

    with pytest.raises(p7_windows.DiagnosticWindowError) as raised:
        p7.run_diagnostics(
            request(), scores=scores, labels_for=lambda year: TableLabels(tail_answer)
        )
    assert raised.value is scores.breach


def test_a_label_window_past_the_window_is_refused_before_any_label_is_asked() -> None:
    """A plan built by hand, skipping `plan_signal_days`' embargo (a caller's error): the 20-session
    label of 2026-02-16 exits in March, past a window ending 2026-02-27. The engine refuses it
    before it asks a single label."""
    short = p7_windows.DiagnosticWindow(
        name="fixture", first=date(2026, 1, 5), last=date(2026, 2, 27), ceiling_year=2026
    )
    forged = p7_windows.SignalPlan(
        window=short,
        first=date(2026, 2, 16),
        last=date(2026, 2, 16),
        stride=1,
        horizons=(20,),
        sessions=(date(2026, 2, 16),),
        embargoed=(),
    )
    labels = TableLabels(tail_answer)

    with pytest.raises(p7_windows.DiagnosticWindowError, match="2026-02-27"):
        p7.run_diagnostics(
            dataclasses.replace(request(), plan=forged),
            scores=ranked(),
            labels_for=lambda year: labels,
        )
    assert labels.asked == []


def test_a_label_past_a_calendar_that_ends_on_the_windows_last_day_is_refused_too() -> None:
    """The real shape: a reader capped at the window's last year stops on the window's last day
    (2021-12-31 for the research window, 2023-12-29 for validation), so a label exiting after it
    cannot even be placed on the reader's calendar. That is a window refusal raised before any
    label is asked -- never the calendar's `CalendarHorizonError` filed as a gap."""
    last = CALENDAR.trading_days[-1]
    ending = p7_windows.DiagnosticWindow(
        name="fixture", first=date(2026, 1, 5), last=last, ceiling_year=last.year
    )
    day = CALENDAR.trading_days[-3]
    forged = p7_windows.SignalPlan(
        window=ending,
        first=day,
        last=day,
        stride=1,
        horizons=(20,),
        sessions=(day,),
        embargoed=(),
    )
    labels = TableLabels(tail_answer)

    assert last == date(2026, 12, 31)
    with pytest.raises(p7_windows.DiagnosticWindowError, match="2026-12-31"):
        p7.run_diagnostics(
            dataclasses.replace(request(), plan=forged),
            scores=ranked(),
            labels_for=lambda year: labels,
        )
    assert labels.asked == []


def test_a_reader_whose_own_window_ends_past_the_window_is_refused_too() -> None:
    """The reader's own window is checked as well as the calendar's rule: a reader whose window
    runs one session longer than its calendar says (here, by construction) is refused before a
    label is asked, though the calendar's exit is the window's last day itself."""

    class LateLabels(TableLabels):
        def window(self, instant: datetime, *, horizon: ResearchHorizon) -> LabelWindow:
            longer = ResearchHorizon(count=horizon.count + 1, unit=horizon.unit)
            return super().window(instant, horizon=longer)

    day = date(2026, 1, 26)
    last = p7_windows.label_exit_day(CALENDAR, day, 20)
    assert last is not None
    ending = p7_windows.DiagnosticWindow(
        name="fixture", first=date(2026, 1, 5), last=last, ceiling_year=2026
    )
    forged = p7_windows.SignalPlan(
        window=ending,
        first=day,
        last=day,
        stride=1,
        horizons=(20,),
        sessions=(day,),
        embargoed=(),
    )
    labels = LateLabels(tail_answer)

    with pytest.raises(p7_windows.DiagnosticWindowError, match=f"last day {last.isoformat()}"):
        p7.run_diagnostics(
            dataclasses.replace(request(), plan=forged),
            scores=ranked(),
            labels_for=lambda year: labels,
        )
    assert labels.asked == []


def test_a_signal_day_outside_the_plans_window_is_refused_before_any_score_is_asked() -> None:
    """A hand-built plan whose session lies past its own window is refused before it is read."""
    short = p7_windows.DiagnosticWindow(
        name="fixture", first=date(2026, 1, 5), last=date(2026, 2, 27), ceiling_year=2026
    )
    forged = p7_windows.SignalPlan(
        window=short,
        first=date(2026, 1, 5),
        last=date(2026, 3, 2),
        stride=1,
        horizons=(1,),
        sessions=(date(2026, 1, 5), date(2026, 3, 2)),
        embargoed=(),
    )
    scores = ranked()

    with pytest.raises(p7_windows.DiagnosticWindowError, match="2026-03-02"):
        p7.run_diagnostics(
            dataclasses.replace(request(), plan=forged),
            scores=scores,
            labels_for=lambda year: TableLabels(tail_answer),
        )
    assert scores.asked == []


@pytest.mark.parametrize(
    "change",
    [
        {"scores": ()},
        {"scores": (p7.COMPOSITE, p7.COMPOSITE)},
        {"tails": (0,)},
        {"tails": (2, 2)},
        {"loser_decile_count": 10},
        {"power_means": (0.0,)},
        {"power_means": (math.inf,)},
        {"power_periods": (1,)},
        {"power_target": 1.0},
        {"power_test_level": 0.5},
    ],
)
def test_a_request_the_engine_cannot_read_is_refused_before_anything_is_read(
    change: Mapping[str, Any],
) -> None:
    with pytest.raises(p7.DiagnosticRequestError):
        dataclasses.replace(request(), **change)


# --- the real sources --------------------------------------------------------------------------


def test_the_panel_score_source_is_the_screens_eligible_composites(
    full_store: PanelStore,  # noqa: F811
) -> None:
    source = p7.PanelScoreSource(
        store=p7_windows.WindowCappedStore(full_store.root, window=FIXTURE_WINDOW),
        book=FRAME_BOOK,
        transform=IDENTITY,
        exchange=EXCHANGE,
        as_of=evening(SIGNAL_INDEX),
        min_coverage=0.5,
        max_carry_sessions=250,
    )
    scores = source.scores_on(SIGNAL)

    facts = read_day(full_store).facts
    eligible = {v.ts_code: v for v in screen(facts, FRAME_BOOK).verdicts if v.eligible}
    assert eligible
    assert scores[p7.COMPOSITE] == tuple(
        sorted((code, v.composite) for code, v in eligible.items())
    )
    assert set(scores) == {p7.COMPOSITE, *FRAME_BOOK.composite_factors}
    for index, factor in enumerate(FRAME_BOOK.composite_factors):
        assert scores[factor] == tuple(
            sorted(
                (fact.ts_code, fact.oriented_values[index])
                for fact in facts
                if fact.ts_code in eligible
            )
        )


def test_the_panel_label_source_is_the_forward_reports_reader(
    panel_store: PanelStore,  # noqa: F811
) -> None:
    source = p7.panel_label_source(
        p7_windows.WindowCappedStore(panel_store.root, window=FIXTURE_WINDOW),
        years=(2026,),
        exchange=EXCHANGE,
        as_of=AS_OF,
        cached_sessions=4,
    )
    read = dataclasses.replace(
        request(first=SESSIONS[1], last=SESSIONS[1], stride=1, horizons=(2,)),
        loser_decile_count=FORWARD_BOOK.loser_decile_count,
    )
    (reading,) = p7.run_diagnostics(
        read, scores=FixedScores(PANEL_SCORES), labels_for=lambda year: source
    )
    outcome = record_outcome(
        reader_of(panel_store),
        witnessed_on(SESSIONS[1], PANEL_SCORES),
        horizon=2,
        book=FORWARD_BOOK,
        csi500=(),
        as_of=AS_OF,
    )

    readout = reading.readout
    assert readout is not None
    assert outcome.mature
    assert outcome.rank_ic is not None
    assert outcome.investable_ew is not None
    assert outcome.unlabelled_by_code
    assert readout.rank_ic == outcome.rank_ic
    assert readout.equal_weight == outcome.investable_ew
    assert readout.survivors_minus_ew == outcome.survivors_minus_ew
    assert readout.ew_minus_worst_decile == outcome.ew_minus_worst_decile
    assert readout.top_minus_bottom == outcome.top_minus_bottom
    assert reading.unlabelled_by_code == outcome.unlabelled_by_code


def test_the_label_reader_cannot_be_built_past_the_ceiling(
    panel_store: PanelStore,  # noqa: F811
) -> None:
    capped = p7_windows.DiagnosticWindow(
        name="capped", first=date(2025, 1, 2), last=date(2025, 12, 31), ceiling_year=2025
    )
    with pytest.raises(p7_windows.DiagnosticWindowError, match="2025"):
        p7.panel_label_source(
            p7_windows.WindowCappedStore(panel_store.root, window=capped),
            years=(2026,),
            exchange=EXCHANGE,
            as_of=AS_OF,
            cached_sessions=4,
        )
    # The years a signal year's reader spans: the year before through the year after, and never
    # past the window's ceiling.
    assert p7.label_years(2017, ceiling_year=2021) == (2016, 2017, 2018)
    assert p7.label_years(2021, ceiling_year=2021) == (2020, 2021)


# --- the document and its lines ----------------------------------------------------------------


def fixed_document(
    *,
    binding: Mapping[str, Any] | None = None,
    window: Any = FIXTURE_WINDOW,
    stride: int = 20,
    horizons: tuple[int, ...] = (20,),
    config: Mapping[str, Any] = FIXED_CONFIG,
) -> dict[str, Any]:
    read = request(stride=stride, horizons=horizons, window=window)
    readings = p7.run_diagnostics(
        read, scores=ranked(), labels_for=lambda year: TableLabels(drifting)
    )
    return p7.diagnostic_document(  # type: ignore[no-any-return]
        read, readings, config=config, code_commit="0" * 40, partitions_read=(), binding=binding
    )


def _document_bytes() -> bytes:
    """One fixed document's JSON and lines, as the command writes them."""
    document = fixed_document()
    text = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    return (text + "\n".join(p7.diagnostic_lines(document)) + "\n").encode("utf-8")


def verdict_words(document: Mapping[str, Any]) -> list[str]:
    text = json.dumps(document, sort_keys=True, ensure_ascii=False)
    text += "\n".join(p7.diagnostic_lines(document))
    return [*FORBIDDEN.findall(text), *FORBIDDEN_PREFIXES.findall(text)]


def test_every_exploratory_document_and_line_carries_the_exploratory_wording() -> None:
    document = fixed_document()
    lines = p7.diagnostic_lines(document)

    assert document["kind"] == p7.EXPLORATORY_KIND == "p7-diagnostic-exploratory/v1"
    assert document["wording"] == [
        p7.EXPLORATORY_WORDING,
        p7.IN_SAMPLE_WORDING,
        p7.CANDIDATE_WORDING,
    ]
    assert document["binding"] is None
    assert document["provenance"]["random_component"] == "none"
    assert document["provenance"]["code_commit"] == "0" * 40
    assert document["provenance"]["partitions_read"] == []
    assert (
        lines[0]
        == "EXPLORATORY, UNREGISTERED -- NOT EVIDENCE: fixture window 2026-01-05..2026-12-31"
    )
    assert all(wording in "\n".join(lines) for wording in document["wording"])
    assert verdict_words(document) == []
    power = document["summary"][p7.COMPOSITE]["h20"]["power_if_planning_sd_were_this"]
    assert power["wording"] == p7.EXPLORATORY_POWER_WORDING
    assert p7.EXPLORATORY_POWER_WORDING in "\n".join(lines)


def test_the_power_block_is_the_calculator_at_the_series_sd() -> None:
    document = fixed_document()
    block = document["summary"][p7.COMPOSITE]["h20"]
    sd = block["rank_ic"]["sd"]
    power = block["power_if_planning_sd_were_this"]

    assert sd is not None and sd > 0
    assert power["sd"] == sd
    for kind in ("normal", "t"):
        assert power[kind]["power"] == [
            {
                "periods": periods,
                "mean": mean,
                "power": power_of_mean_test(
                    mean=mean, sd=sd, periods=periods, test_level=0.05, kind=kind
                ),
            }
            for periods in (12, 36, 56)
            for mean in (0.037, 0.05, 0.1)
        ]
        assert power[kind]["periods_for_target"] == [
            {
                "mean": mean,
                "periods": periods_for_power(
                    mean=mean, power=0.8, sd=sd, test_level=0.05, kind=kind
                ),
            }
            for mean in (0.037, 0.05, 0.1)
        ]


def test_a_series_with_no_spread_has_no_power_block() -> None:
    """`tail_answer` scales the labels and keeps their order: every rank IC is the same."""
    read = request(last=date(2026, 3, 31))
    summary = p7.summarize(
        read,
        p7.run_diagnostics(read, scores=ranked(), labels_for=lambda y: TableLabels(tail_answer)),
    )
    assert summary[p7.COMPOSITE]["h20"]["rank_ic"]["absence"] == "no_dispersion"
    assert summary[p7.COMPOSITE]["h20"]["power_if_planning_sd_were_this"] is None


def test_a_binding_makes_the_read_once_kind_and_wording() -> None:
    binding = {
        "reading": "probe",
        "registration_sha256": "0" * 64,
        "registration_commit": "0" * 40,
        "claim_sha256": "1" * 64,
    }
    document = fixed_document(binding=binding)

    assert document["kind"] == p7.READ_ONCE_KIND == "p7-diagnostic-read-once/v1"
    assert document["wording"] == [p7.READ_ONCE_WORDING, p7.CANDIDATE_WORDING]
    assert document["binding"] == binding
    assert p7.diagnostic_lines(document)[0] == (
        "PRE-REGISTERED READ-ONCE probe -- A FIRST OUT-OF-SAMPLE READING, NOT CONFIRMATORY "
        "EVIDENCE: fixture window 2026-01-05..2026-12-31"
    )
    assert verdict_words(document) == []

    second = fixed_document(binding=binding, window=p7_windows.SECOND_USE)
    assert second["wording"] == [
        p7.SECOND_USE_READ_ONCE_WORDING,
        p7.CANDIDATE_WORDING,
        p7.SECOND_USE_WORDING,
    ]
    assert p7.diagnostic_lines(second)[0] == (
        "PRE-REGISTERED READ-ONCE probe -- A SECONDARY, NON-CONFIRMATORY READING OF A SPENT "
        "HOLDOUT: second_use window 2024-01-02..2026-09-24"
    )


def test_the_spent_holdout_is_never_called_a_first_out_of_sample_reading() -> None:
    """The second-use window was P6's one-time holdout, already used, and P7 was designed after
    seeing it: its one reading is secondary and non-confirmatory, and neither its JSON nor its
    text may call it a first out-of-sample reading."""
    binding = {
        "reading": "probe",
        "registration_sha256": "0" * 64,
        "registration_commit": "0" * 40,
        "claim_sha256": "1" * 64,
    }
    second = fixed_document(binding=binding, window=p7_windows.SECOND_USE)
    text = json.dumps(second, sort_keys=True, ensure_ascii=False)
    text += "\n" + "\n".join(p7.diagnostic_lines(second))

    assert "first out-of-sample" not in text.lower()
    assert p7.SECOND_USE_READ_ONCE_WORDING == (
        "the one pre-registered read of this window, which was P6's one-time holdout and has "
        "been used: a secondary, non-confirmatory reading of a spent holdout, not out-of-sample "
        "evidence; confirmatory evidence comes only from forward records registered under "
        "V2-P7-006"
    )
    assert verdict_words(second) == []


def test_the_validation_windows_read_once_text_is_unchanged_byte_for_byte() -> None:
    """Only the spent holdout's wording moved: the validation window's (and any other read-once
    window's) is the text it always was."""
    assert p7.READ_ONCE_WORDING == (
        "the one pre-registered read of this window: a first out-of-sample reading, not "
        "confirmatory evidence; confirmatory evidence comes only from forward records registered "
        "under V2-P7-006"
    )
    assert p7.read_once_wording("validation") == p7.READ_ONCE_WORDING
    assert p7.read_once_banner(
        "probe", "validation", "validation window 2022-01-04..2023-12-29"
    ) == (
        "PRE-REGISTERED READ-ONCE probe -- A FIRST OUT-OF-SAMPLE READING, NOT CONFIRMATORY "
        "EVIDENCE: validation window 2022-01-04..2023-12-29"
    )


def test_a_read_once_document_never_says_in_sample_and_the_exploratory_one_is_unchanged() -> None:
    """`V2-P7-004` fix round 1, ruling 5: the rank IC's power block of the one reading of a window
    is a value of that reading, not an in-sample one; the exploratory wording stays as it was."""
    binding = {
        "reading": "probe",
        "registration_sha256": "0" * 64,
        "registration_commit": "0" * 40,
        "claim_sha256": "1" * 64,
    }
    read_once = fixed_document(binding=binding)
    text = json.dumps(read_once, sort_keys=True, ensure_ascii=False)
    text += "\n" + "\n".join(p7.diagnostic_lines(read_once))
    power = read_once["summary"][p7.COMPOSITE]["h20"]["power_if_planning_sd_were_this"]

    assert p7.READ_ONCE_POWER_WORDING == (
        "if the planning sd were this value measured in the one reading of this window: an "
        "observed value of that reading, never a planning value by itself"
    )
    assert power["wording"] == p7.READ_ONCE_POWER_WORDING
    assert f"  rank_ic power {p7.READ_ONCE_POWER_WORDING}: sd " in text
    assert "in-sample" not in text
    assert "exploratory" not in text

    exploratory = fixed_document()
    lines = "\n".join(p7.diagnostic_lines(exploratory))
    assert p7.EXPLORATORY_POWER_WORDING == (
        "if the planning sd were this exploratory value (in-sample, measured after the factors "
        "were chosen): never a planning value by itself"
    )
    assert (
        exploratory["summary"][p7.COMPOSITE]["h20"]["power_if_planning_sd_were_this"]["wording"]
        == p7.EXPLORATORY_POWER_WORDING
    )
    assert f"  rank_ic power {p7.EXPLORATORY_POWER_WORDING}: sd " in lines
    assert p7.READ_ONCE_POWER_WORDING not in lines


def test_two_runs_write_the_same_bytes_under_two_hash_seeds() -> None:
    here = _document_bytes()
    assert here == _document_bytes()
    command = (
        "import sys; "
        f"sys.path[:0] = [{str(ROOT / 'tests' / 'unit')!r}, {str(ROOT / 'tests')!r}]; "
        "import test_p7_diagnostics as t; sys.stdout.buffer.write(t._document_bytes())"
    )
    for seed in ("0", "7"):
        child = subprocess.run(
            [sys.executable, "-c", command],
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT / "src")},
            check=True,
            capture_output=True,
        )
        assert child.stdout == here, seed


def test_the_config_digest_moves_with_any_request_or_config_field() -> None:
    later = {**FIXED_CONFIG, "as_of": FIXED_CONFIG["as_of"] + timedelta(days=1)}
    other = {**FIXED_CONFIG, "transform": "cross_section_standard/v1"}
    documents = [
        fixed_document(),
        fixed_document(stride=10),
        fixed_document(horizons=(5, 20)),
        fixed_document(config=later),
        fixed_document(config=other),
    ]
    digests = [document["provenance"]["config_digest"] for document in documents]

    assert len(set(digests)) == len(digests)
    for document in documents:
        recomputed = hashlib.sha256(
            grid.canonical_json(
                {"config": document["config"], "request": document["request"]}
            ).encode("utf-8")
        ).hexdigest()
        assert document["provenance"]["config_digest"] == recomputed


# --- the command line --------------------------------------------------------------------------


EXPLORE: Final[tuple[str, ...]] = (
    "explore",
    "--as-of",
    "2026-10-09T00:00:00+08:00",
    "--stride",
    "20",
    "--horizons",
    "20",
    "--transform",
    "cross_section_standard/v1",
)


def test_explore_refuses_a_date_past_the_research_window_before_opening_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def opened(*args: object, **kwargs: object) -> None:
        raise AssertionError("a store was opened")

    monkeypatch.setattr(p7, "_open_sources", opened)
    code = p7.main(
        [
            *EXPLORE,
            "--runtime-dir",
            str(tmp_path),
            "--first",
            "2021-11-01",
            "--last",
            "2022-01-04",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 2
    error = capsys.readouterr().err
    assert "validation" in error
    assert "read_once" in error
    assert not (tmp_path / "out").exists()


def weekday_calendar(first: date, last: date) -> TradingCalendar:
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return build_trading_calendar(
        "SSE", [CalendarDay(calendar_date=day, is_trading=day.weekday() < 5) for day in days]
    )


STUB_CALENDAR: Final[TradingCalendar] = weekday_calendar(date(2016, 12, 1), date(2017, 12, 29))


def stub_sources(monkeypatch: pytest.MonkeyPatch, scores: Any = None) -> list[Mapping[str, Any]]:
    """`p7._open_sources` replaced by generated 2017 sources; returns the calls it was given."""
    opened: list[Mapping[str, Any]] = []

    def sources(*args: object, **kwargs: Any) -> tuple[Any, ...]:
        opened.append(kwargs)
        return (
            STUB_CALENDAR,
            ranked() if scores is None else scores,
            lambda year: TableLabels(
                lambda entry, code: TAIL.get(CODES.index(code), 0.01), calendar=STUB_CALENDAR
            ),
            SimpleNamespace(partitions_read=lambda: ()),
        )

    monkeypatch.setattr(p7, "_open_sources", sources)
    return opened


def explore_2017(tmp_path: Path, *extra: str) -> list[str]:
    return [
        *EXPLORE,
        "--runtime-dir",
        str(tmp_path),
        "--first",
        "2017-01-03",
        "--last",
        "2017-06-30",
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]


def test_explore_writes_once_and_a_rerun_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opened = stub_sources(monkeypatch)
    out = tmp_path / "out"
    argv = explore_2017(tmp_path)

    assert p7.main(argv) == 0
    assert opened[0]["window"] is p7_windows.RESEARCH
    first_run = capsys.readouterr().out
    (json_path,) = out.glob("p7-diagnostic-research-*.json")
    document = json.loads(json_path.read_text(encoding="utf-8"))
    stem = f"p7-diagnostic-research-{document['provenance']['config_digest'][:16]}"
    assert json_path.name == f"{stem}.json"
    text_path = out / f"{stem}.txt"
    assert document["kind"] == p7.EXPLORATORY_KIND
    assert document["window"]["name"] == "research"
    assert text_path.read_text(encoding="utf-8").splitlines() == p7.diagnostic_lines(document)
    written = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (json_path, text_path)
    }
    assert f"written: {json_path}" in first_run.splitlines()
    assert f"written: {text_path}" in first_run.splitlines()

    assert p7.main(argv) == 0
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in written} == written
    second_run = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("written:") for line in second_run)
    assert f"unchanged, nothing written: {json_path}" in second_run
    assert f"unchanged, nothing written: {text_path}" in second_run

    json_path.write_bytes(json_path.read_bytes() + b" ")
    assert p7.main(argv) != 0
    assert "already holds a different document" in capsys.readouterr().err
    assert text_path.read_bytes() == written[text_path][0]


def test_explore_refuses_a_repeated_score_before_opening_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opened = stub_sources(monkeypatch)

    assert p7.main(explore_2017(tmp_path, "--scores", "composite,composite")) == 2
    assert "composite" in capsys.readouterr().err
    assert opened == []
    assert not (tmp_path / "out").exists()


def test_explore_refuses_a_request_its_sources_cannot_answer_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default book's first factor is a valid `--scores` name, and the stub scores only the
    composite: the engine's `DiagnosticRequestError`, met after the store is opened, is exit 2 and
    its message, not a traceback."""
    stub_sources(monkeypatch)

    code = p7.main(explore_2017(tmp_path, "--scores", "composite,residual_vol_60/v1"))

    assert code == 2
    assert "the score source has no ['residual_vol_60/v1']" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_explore_files_a_day_with_an_empty_scope_as_a_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_sources(monkeypatch, scores=FixedScores({}))

    assert p7.main(explore_2017(tmp_path)) == 0
    (json_path,) = (tmp_path / "out").glob("p7-diagnostic-research-*.json")
    document = json.loads(json_path.read_text(encoding="utf-8"))
    block = document["summary"][p7.COMPOSITE]["h20"]
    assert block["sampled"] and block["read"] == 0
    assert {gap["reason"] for gap in block["gaps"]} == {
        "empty_scope: the score source scored no name"
    }
    assert len(block["gaps"]) == len(block["sampled"])


def test_the_power_subcommand_prints_the_calculators_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = [
        "power",
        "--periods",
        "36",
        "--sd",
        "0.15",
        "--test-level",
        "0.05",
        "--means",
        "0.037,0.05,0.1",
        "--target-power",
        "0.8",
        "--critical-value",
        "normal",
    ]
    table = list(
        power_table_lines(
            periods=36,
            sd=0.15,
            test_level=0.05,
            means=(0.037, 0.05, 0.1),
            target_power=0.8,
            kind="normal",
        )
    )

    assert p7.main(base) == 0
    assert capsys.readouterr().out.splitlines() == table

    assert p7.main([*base, "--interim", "18", "--futility-threshold", "0"]) == 0
    printed = capsys.readouterr().out.splitlines()
    interim: list[str] = []
    for mean in (0.037, 0.05, 0.1):
        stop = futility_stop_probability(mean=mean, sd=0.15, interim_periods=18, threshold=0.0)
        power = power_with_futility(
            mean=mean,
            sd=0.15,
            periods=36,
            interim_periods=18,
            threshold=0.0,
            test_level=0.05,
            kind="normal",
        )
        interim.append(
            f"  interim look after 18 periods, stopping at a mean at or below 0: stop "
            f"probability {stop:.3f} at mean {mean:g}"
        )
        interim.append(f"  power with the interim look: {power:.3f} at mean {mean:g}")
    assert printed == [*table, *interim]
    assert (
        "  interim look after 18 periods, stopping at a mean at or below 0: stop probability "
        "0.079 at mean 0.05"
    ) in printed
    assert "  power with the interim look: 0.631 at mean 0.05" in printed

    assert p7.main([*base, "--family-size", "3", "--q", "0.1"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert by_first_step_level(q=0.1, family_size=3) == pytest.approx(0.1 / 5.5)
    assert printed == [
        *table,
        "  a secondary under Benjamini-Yekutieli (family 3, q 0.1) is powered at the first-step "
        "level 0.0182, a lower bound",
    ]


def explore_help() -> str:
    """The `explore` subcommand's help, formatted by argparse itself (the text its help flag
    prints) -- `test_cli_help.py` holds the flag's literal to one module, and this is a plain
    argparse parser, not the Rich-rendered CLI that helper exists for."""
    parser = p7._parser()
    (commands,) = (
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return str(commands.choices["explore"].format_help())


def test_only_the_cli_names_the_research_window() -> None:
    help_text = explore_help()
    assert "--first" in help_text
    assert "--window" not in help_text
    research = p7_windows.RESEARCH
    assert f"{research.first}..{research.last}" in help_text


def test_the_help_reads_the_research_windows_dates_from_the_window_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dates are the table's, not a copy typed into the help: a moved window moves them."""
    moved = p7_windows.DiagnosticWindow(
        name="research", first=date(2015, 2, 2), last=date(2021, 6, 30), ceiling_year=2021
    )
    monkeypatch.setattr(p7, "WINDOWS", {"research": moved})

    assert "2015-02-02..2021-06-30" in explore_help()


def test_explore_refuses_a_runtime_directory_inside_the_main_checkout_before_opening_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI's one store opening refuses by the runtime-directory rule, not by a later error:
    deleting `_open_sources`'s check must fail the type assertion below, because the empty panel
    would otherwise be refused only when the catalogue turns out to be missing."""
    checkout = tmp_path / "checkout"
    runtime = checkout / "runtime"
    (runtime / "panel").mkdir(parents=True)
    monkeypatch.setattr(p7_windows, "MAIN_CHECKOUT", checkout.resolve())
    refusals: list[BaseException] = []
    opens = p7._open_sources

    def watched(*args: Any, **kwargs: Any) -> Any:
        try:
            return opens(*args, **kwargs)
        except BaseException as error:
            refusals.append(error)
            raise

    monkeypatch.setattr(p7, "_open_sources", watched)

    code = p7.main(
        [
            *EXPLORE,
            "--runtime-dir",
            str(runtime),
            "--first",
            "2017-01-03",
            "--last",
            "2017-12-29",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert len(refusals) == 1
    assert isinstance(refusals[0], p7_windows.RuntimeDirectoryRefusedError)
    assert "main checkout" in str(refusals[0])
    assert code == 2
    assert "main checkout" in capsys.readouterr().err
    assert list((runtime / "panel").iterdir()) == []
    assert not (runtime / "panel" / p7_windows.CATALOG_FILE).exists()
    assert not (tmp_path / "out").exists()
