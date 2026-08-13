"""`V2-P3-012`'s family against real partitions, and the fifth deliverable's composition.

`tests/unit/test_factor_momentum_reversal_rules.py` holds the declarations and the arithmetic.
This file holds everything that needs a store: what each of the four factors answers for a
security whose price history is on disk, which coverage code a halt earns on each of them, and
the three-tier composition that produces industry-relative momentum out of machinery
`V2-P3-003`/`004` already shipped.

## The corpus

Sixteen securities over 165 weekday sessions, 153 of them in 2025 and 12 in 2026, read at an
`as_of` in mid-January 2026. The length is what the family needs -- `MOMENTUM_120_SESSIONS`
declares a reach of 125 -- and the year boundary is not incidental either: roadmap section 11
records that a 120-session momentum evaluated in January is the case the cross-year freshness
scope was corrected for, and `test_a_january_as_of_needs_the_previous_year_named_and_says_so_
when_it_is_not` drives both sides of it.

Every security's price path is a product of declared per-session growth factors over **its own**
sessions, with `pre_close[t] == close[t-1]` on consecutive traded sessions. So every expected
value in this file is a product of `_growth(...)` terms rather than a number copied out of a run,
which is the property this repository's own review asks for: a proof that only checks existence
hangs on a free parameter.

Eight of the sixteen carry a shape, and each was chosen to make **one** declared field separable
from its neighbours rather than to make a test pass:

- **`HALTED_RECENT`** -- one halt inside the newest five sessions, which separates
  `REVERSAL_5_SESSIONS`' span bound (at equality) from the three momenta's.
- **`HALTED_LONG`** -- six halts inside the newest 25 own sessions, which separates
  `MOMENTUM_20_SESSIONS`' span of 30 from `MOMENTUM_60_SESSIONS`' 78.
- **`HALTED_MID`** -- fourteen halts just outside the newest 25, the mirror image one rung up:
  `MOMENTUM_60_SESSIONS`' 78 against `MOMENTUM_120_SESSIONS`' 150.
- **`HALTED_AT_THE_BOUND`** and **`HALTED_DEEP`** -- 25 and 26 halts from the same starting point,
  so their 125-session windows span exactly 150 and 151. One on each side of the widest factor's
  bound, which no other factor in the family brackets; see `HALT_RUNS` for the mutation that
  survived before they existed.
- **`YOUNG`** -- 30 sessions of history, which separates the *counts* 25 / 65 / 125 from every
  span bound, since 30 consecutive sessions put no span in play at all.
- **`NULL_CLOSE`** -- one null close, inside the momenta's window and outside the reversal's,
  which separates `input_missing` from `insufficient_history` and does it per factor.
- **`OUTSIDE`** -- in `subjects` and not in `universe`: `not_in_universe`, which is not a fault.

Eight plain names are left with no shape at all, because a cross section made only of shapes has
no control and because the composition below needs two industry groups of **different** sizes --
the single-group fixture that let `V2-P3-004`'s review pass two inseparable assertions is the
shape this file is built to avoid repeating.

## The composition, and the probe specs it uses

The fifth deliverable is industry-relative momentum, and `panel_factors`' own docstring argues at
length why it is a composition rather than a fifth `FactorDefinition`. The chain is
`compute_factor -> apply_factor_transform -> apply_factor_neutralization`, in memory, touching no
store after the first step.

The two shipped specs both declare `min_cross_section = 100`, which a thirteen-name cross section
cannot clear, so the probes below lower **only** the floors and
`test_the_probe_specs_differ_from_the_shipped_ones_in_their_floors_and_nothing_else` asserts field
by field that nothing else moved. That is `tests/integration/panel/test_factor_neutralizations.py`'
own arrangement and it is taken here for its reason: the arithmetic under test is the shipped
arithmetic, and the floor is the one setting a fixture is allowed to move.

**Nothing here writes or reads back a neutralised partition**, deliberately. Roadmap section 11
records that a neutralised row's four clocks are all the build `as_of` and that residuals for any
day of year Y are therefore invisible to every read before Y ends (`V2-P4-026`, `V2-P4-013`'s hard
prerequisite), together with an instruction to `V2-P3-009`..`013` to stack no further code on the
assumption that they are visible day by day. A composition of three in-memory calls stacks none.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.daily_prices import (
    CLOSE_COLUMN,
    DAILY_AVAILABILITY_TIME,
    DAILY_DATASET,
    PRE_CLOSE_COLUMN,
    PRICE_DATE_COLUMN,
    SESSION_CLOSE_TIME,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorField, FactorObservation
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationSpec,
    SecurityCharacteristic,
    build_industry_market_cap_cross_section,
)
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_L1_COLUMN,
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_MEMBERSHIP_TAXONOMY,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn, TimelineColumns
from openalpha_cn.panel.catalog import ReadinessRequirement
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    CROSS_SECTION_STANDARD,
    MOMENTUM_20_SESSIONS,
    MOMENTUM_60_SESSIONS,
    MOMENTUM_120_SESSIONS,
    REVERSAL_5_SESSIONS,
    SHORT_REVERSAL_SESSIONS,
    FactorEngineError,
    FactorPanel,
    apply_factor_transform,
    compute_factor,
)
from openalpha_cn.panel_ingest import write_panel_batch
from openalpha_cn.panel_neutralization import INDUSTRY_AND_SIZE, apply_factor_neutralization

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

AS_OF: Final[datetime] = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai on Saturday 2026-01-17, so every session of the corpus has published."""

BUILT_AT: Final[datetime] = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"

STALENESS: Final[timedelta] = timedelta(days=5)
"""One trading week, and it is not a number tuned to this corpus.

The widest gap between `AS_OF` and the newest session visible at it is the weekend one -- Friday
2026-01-16 at 15:00 against Saturday noon, 21 hours -- so any bound above a day would pass. Five
days is what `tests/integration/panel/test_factor_engine.py` already declares for the same reason
and is restated rather than re-derived. Note what it does **not** bound: the 2025 partition's own
newest row is 17 days before `AS_OF`, and the read clears because `read_visible_at` pools the
re-decided checks over `requirement.years` -- which is exactly the cross-year correction roadmap
section 11 records as landing on this issue.
"""

YEARS: Final[tuple[int, ...]] = (2025, 2026)

FIRST_DAY: Final[date] = date(2025, 6, 2)
LAST_DAY: Final[date] = date(2026, 1, 16)
"""165 weekday sessions, and the length is set by a bound rather than by taste.

`MOMENTUM_120_SESSIONS` may span 150 panel sessions, so a security that *overruns* that bound needs
125 own sessions plus 26 missed ones -- 151 -- and a corpus of 144 could not hold one. The first
version of this file was 144 sessions long and a mutation widening that bound from 150 to 151
**survived every test in it**: the arithmetic rule in
`tests/unit/test_factor_momentum_reversal_rules.py` caught it, and nothing here did, because no
security in the corpus sat near the boundary. That is the shape this repository keeps meeting --
a declared field asserted but not separable on the fixture -- and the corpus was lengthened for it
rather than the mutation being written off.
"""

PLAIN: Final[tuple[str, ...]] = (
    "000001.SZ",
    "000002.SZ",
    "000063.SZ",
    "000651.SZ",
    "600000.SH",
    "600036.SH",
    "600519.SH",
    "601318.SH",
)
HALTED_RECENT: Final[str] = "300750.SZ"
HALTED_LONG: Final[str] = "300059.SZ"
HALTED_MID: Final[str] = "002415.SZ"
HALTED_AT_THE_BOUND: Final[str] = "601899.SH"
HALTED_DEEP: Final[str] = "600887.SH"
YOUNG: Final[str] = "688981.SH"
NULL_CLOSE: Final[str] = "688111.SH"
OUTSIDE: Final[str] = "600030.SH"

SECURITIES: Final[tuple[str, ...]] = (
    *PLAIN,
    HALTED_RECENT,
    HALTED_LONG,
    HALTED_MID,
    HALTED_AT_THE_BOUND,
    HALTED_DEEP,
    YOUNG,
    NULL_CLOSE,
    OUTSIDE,
)

UNIVERSE: Final[frozenset[str]] = frozenset(SECURITIES) - {OUTSIDE}

YOUNG_SESSIONS: Final[int] = 30
"""`YOUNG`'s whole history: enough for a 25-session reach and short of a 65-session one."""

NULL_SESSION_FROM_THE_END: Final[int] = 22
"""Where `NULL_CLOSE` carries its null, counted back from the newest session.

Inside `MOMENTUM_20_SESSIONS`' 25-session window and outside `REVERSAL_5_SESSIONS`' five, which is
what makes `input_missing` a *per-factor* answer on one security rather than a whole-row one. The
row is still there -- a null cell, not an absent session -- so the security's own session count is
untouched and the two codes stay separable.
"""

HALT_RUNS: Final[Mapping[str, tuple[int, int]]] = {
    HALTED_RECENT: (3, 1),
    HALTED_LONG: (12, 6),
    HALTED_MID: (40, 14),
    HALTED_AT_THE_BOUND: (70, 25),
    HALTED_DEEP: (70, 26),
}
"""`security -> (how far back the run starts, how long it is)`, counted back from the newest.

`(3, 1)` means the run covers the session three back from the newest, one session long. Each run is
placed against a *declared* bound rather than against an observed outcome:

- `HALTED_RECENT` misses one of the newest five, so its five own sessions span six panel sessions
  -- one over `REVERSAL_5_SESSIONS`' bound of five, and far inside every momentum's.
- `HALTED_LONG` misses six inside the newest 25, so its 25 own sessions span 31: one over
  `MOMENTUM_20_SESSIONS`' 30, and its 65 own span 71, inside `MOMENTUM_60_SESSIONS`' 78.
- `HALTED_MID` misses fourteen starting 40 back, so its newest 25 own sessions are untouched, its
  65 own span 79 -- one over `MOMENTUM_60_SESSIONS`' 78 -- and its 125 own span 139, inside
  `MOMENTUM_120_SESSIONS`' 150.
- `HALTED_AT_THE_BOUND` and `HALTED_DEEP` miss 25 and 26 sessions from the same starting point, so
  their 125 own sessions span **exactly 150** and **151** -- one on each side of
  `MOMENTUM_120_SESSIONS`' bound, and nothing else about them differs. Everything narrower is
  untouched on both: their newest 65 own sessions are consecutive.

The last pair is the correction of a real gap rather than a flourish. `MOMENTUM_120_SESSIONS` is
the only factor here with no wider neighbour to bracket it, and before those two existed a mutation
widening its bound from 150 to 151 passed every test in this file. Now one of them changes coverage
if the bound moves in either direction.
"""

INDUSTRY_A: Final[str] = "801080.SI"
INDUSTRY_B: Final[str] = "801150.SI"
"""Two real SW2021 level-one codes, so the composition regresses over two groups of different size.

The single-group fixture is the shape `V2-P3-004`'s review found could not separate two answers:
on one group, demeaning by industry and demeaning the whole cross section are the same arithmetic,
and `smallest_industry_size` and `largest_industry_size` are the same number.
"""

CAP_BASE: Final[float] = 2_000_000.0
CAP_STEP: Final[float] = 640_000.0
"""`total_mv` in 10k CNY: `CAP_BASE + CAP_STEP * SECURITIES.index(code)`.

Monotone in the fixture's own security order and therefore varying *within* each industry group,
which is what `_neutralize` needs to have a slope at all -- a design whose regressor has no
within-group dispersion is `degenerate_design` rather than a neutralisation.
"""


def _sessions() -> tuple[date, ...]:
    """Every weekday from `FIRST_DAY` through `LAST_DAY`, ascending.

    A weekday filter rather than a `TradingCalendar`, because nothing in this file asks the
    calendar a question: the requirement below waives `required_dates`, and the engine's own
    session grid is the union of the sessions the read returns.
    """
    days: list[date] = []
    day = FIRST_DAY
    while day <= LAST_DAY:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


SESSIONS: Final[tuple[date, ...]] = _sessions()


SESSION_DRIFT: Final[float] = 0.002
SESSION_SWING: Final[float] = 0.001
"""A drift term and an oscillation term, and the ratio between them is load-bearing.

An oscillation alone is what the first version of this corpus carried, and it made the four
horizons **indistinguishable**: the swing's period divides its own sum to zero, so a 20-session
and a 60-session product both sat within 0.0005 of one another and
`test_no_two_factors_answer_with_the_same_number_for_any_security` failed with a gap two orders
below its floor. That is the corpus defect this repository has now met three times -- a fixture on
which two answers coincide -- caught here by the test rather than by a reviewer.

The drift is per security (`0.002 * (seed % 4 + 1)`, so four distinct trends) and the swing is
`0.001` of a 13-session cycle, which leaves the worst gap between any two of one security's four
answers at 0.021 against a floor of 0.01. Both terms are needed: the drift separates the horizons
and the swing keeps the path from being monotone, on which the four would be ordered by
construction and "no two agree" would be arithmetic rather than a measurement.
"""


def _growth(code: str, position: int) -> float:
    """One session's `close / pre_close` for one security, indexed by its **own** session count.

    Deterministic, mixed in sign and different per security, so that no two securities and no two
    horizons collapse onto one number -- which is the coincidence
    `test_no_two_factors_answer_with_the_same_number_for_any_security` refuses.
    """
    seed = SECURITIES.index(code)
    return (
        1.0
        + SESSION_DRIFT * (seed % 4 + 1)
        + SESSION_SWING * (((position * 7 + seed * 5) % 13) - 6)
    )


def _traded(code: str) -> tuple[date, ...]:
    """The sessions this security actually has a row on, ascending."""
    if code == YOUNG:
        return SESSIONS[-YOUNG_SESSIONS:]
    run = HALT_RUNS.get(code)
    if run is None:
        return SESSIONS
    start, length = run
    halted = set(SESSIONS[len(SESSIONS) - start - length + 1 : len(SESSIONS) - start + 1])
    return tuple(day for day in SESSIONS if day not in halted)


def _path(code: str, *, disturb_tail: float = 1.0) -> dict[date, tuple[float | None, float]]:
    """`session -> (close, pre_close)` for one security, compounded over its own sessions.

    `pre_close` is the previous **own** close, which is what a halt resumption publishes when no
    corporate action intervened, so the product of `close / pre_close` over the security's own
    sessions is its true return across the halt as well as across it.

    `disturb_tail` multiplies the growth factor of the newest `SHORT_REVERSAL_SESSIONS` sessions
    and nothing else, which is the one lever
    `test_the_sessions_a_momentum_reads_and_the_sessions_the_reversal_reads_are_disjoint` pulls.
    """
    traded = _traded(code)
    rows: dict[date, tuple[float | None, float]] = {}
    price = 10.0
    for position, day in enumerate(traded):
        factor = _growth(code, position)
        if position >= len(traded) - SHORT_REVERSAL_SESSIONS:
            factor *= disturb_tail
        previous = price
        price *= factor
        close: float | None = price
        if code == NULL_CLOSE and day == SESSIONS[-NULL_SESSION_FROM_THE_END]:
            close = None
        rows[day] = (close, previous)
    return rows


def _expected(code: str, *, compounded: int, skip: int, disturb_tail: float = 1.0) -> float:
    """The value a factor reading `compounded` sessions after `skip` must produce for `code`.

    Built from `_growth` rather than from the stored prices, so a writer that stored the wrong
    column and a reader that read it back the same way cannot agree with each other.
    """
    traded = len(_traded(code))
    total = 1.0
    for position in range(traded - compounded - skip, traded - skip):
        factor = _growth(code, position)
        if position >= traded - SHORT_REVERSAL_SESSIONS:
            factor *= disturb_tail
        total *= factor
    return total - 1.0


def _daily_batch(year: int, *, disturb_tail: float = 1.0) -> ColumnarPanelBatch:
    rows = [
        (code, day, *_path(code, disturb_tail=disturb_tail)[day])
        for code in SECURITIES
        for day in _traded(code)
        if day.year == year
    ]
    return ColumnarPanelBatch(
        provider_id="openalpha-cn/tests",
        dataset=DAILY_DATASET,
        kind="daily",
        as_of=AS_OF,
        fetched_at=BUILT_AT,
        status="success",
        subjects=tuple(str(row[0]) for row in rows),
        timeline=TimelineColumns(
            event_time=tuple(_at(row[1], SESSION_CLOSE_TIME) for row in rows),
            available_time=tuple(_at(row[1], DAILY_AVAILABILITY_TIME) for row in rows),
            ingested_time=tuple(_at(row[1], DAILY_AVAILABILITY_TIME) for row in rows),
            revision_time=tuple(_at(row[1], DAILY_AVAILABILITY_TIME) for row in rows),
        ),
        columns=(
            PanelColumn(PRICE_DATE_COLUMN, "string", tuple(row[1].isoformat() for row in rows)),
            PanelColumn(CLOSE_COLUMN, "float", tuple(row[2] for row in rows)),
            PanelColumn(PRE_CLOSE_COLUMN, "float", tuple(row[3] for row in rows)),
        ),
    )


def _at(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=SHANGHAI)


def _written(tmp_path: Path, *, disturb_tail: float = 1.0) -> PanelStore:
    store = PanelStore(tmp_path / "panel")
    for year in YEARS:
        write_panel_batch(store, _daily_batch(year, disturb_tail=disturb_tail), year=year)
    return store


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    """The corpus, written once for the module.

    Module scope rather than per test, and it is safe for exactly one reason that is worth stating
    rather than assumed: **no test in this file writes to this store.** Every one of them calls
    `compute_factor`, which reads; the two that need a *different* corpus
    (`..._are_disjoint` and the industry-code refusal) build their own from `tmp_path`. Writing a
    2,016-row partition twenty times over cost 40 seconds of the suite, and `ADR-0003` records
    that the write path is at least an order of magnitude dearer than the read on this plane.
    """
    return _written(tmp_path_factory.mktemp("momentum"))


def _requirement(
    *,
    years: tuple[int, ...] = YEARS,
    dataset: str = DAILY_DATASET,
    fields: tuple[str, ...] = (CLOSE_COLUMN, PRE_CLOSE_COLUMN),
) -> ReadinessRequirement:
    return ReadinessRequirement(
        dataset=dataset,
        as_of=AS_OF,
        years=years,
        required_dates=None,
        required_subjects=None,
        required_fields=fields,
        max_staleness=STALENESS,
    )


def _compute(store: PanelStore, definition: FactorDefinition, **overrides: Any) -> FactorPanel:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "subjects": SECURITIES,
        "universe": UNIVERSE,
        "requirements": {DAILY_DATASET: _requirement()},
        "code_commit": COMMIT,
        "built_at": BUILT_AT,
        **overrides,
    }
    return compute_factor(store, definition, **settings)


def _by_subject(panel: FactorPanel) -> dict[str, FactorObservation]:
    return {observation.subject: observation for observation in panel.observations}


def _coverage(panel: FactorPanel) -> dict[str, str]:
    return {item.subject: item.coverage for item in panel.observations}


REACHES: Final[tuple[tuple[FactorDefinition, int, int], ...]] = (
    (MOMENTUM_20_SESSIONS, 20, SHORT_REVERSAL_SESSIONS),
    (MOMENTUM_60_SESSIONS, 60, SHORT_REVERSAL_SESSIONS),
    (MOMENTUM_120_SESSIONS, 120, SHORT_REVERSAL_SESSIONS),
    (REVERSAL_5_SESSIONS, 5, 0),
)
"""`(definition, sessions compounded, sessions skipped)` for the four shipped factors."""


# --- the values ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("definition", "compounded", "skip"), REACHES)
def test_each_factor_answers_with_the_product_of_the_sessions_it_declares(
    store: PanelStore, definition: FactorDefinition, compounded: int, skip: int
) -> None:
    """Every plain security's value, as a number derived from the corpus's own growth factors.

    Asserted as a quantity rather than as `is not None`, which this repository has on file as the
    shape of a proof that hangs on a free parameter: an engine that returned the newest close
    would satisfy an existence check on all eight of these.
    """
    panel = _compute(store, definition)
    values = panel.values()

    assert set(values) >= set(PLAIN)
    for code in PLAIN:
        assert values[code] == pytest.approx(
            _expected(code, compounded=compounded, skip=skip), rel=1e-12
        ), code


def test_no_two_factors_answer_with_the_same_number_for_any_security(store: PanelStore) -> None:
    """Four horizons over one price path, held apart on every security that has all four.

    The failure this refuses is the one `V2-P3-004`'s review met twice: a fixture on which two
    stored columns carry the same number lets a test assert both and separate neither. Here that
    would be a corpus flat enough for a five-session and a hundred-and-twenty-session return to
    coincide, and the floor keeps the four an entire percentage point of return apart.
    """
    panels = {definition.key: _compute(store, definition).values() for definition, _, _ in REACHES}

    for code in PLAIN:
        answers = sorted(values[code] for values in panels.values())
        gaps = [right - left for left, right in pairwise(answers)]
        assert len(answers) == 4
        assert min(gaps) > 0.01, (code, answers)


def test_the_sessions_a_momentum_reads_and_the_sessions_the_reversal_reads_are_disjoint(
    tmp_path: Path,
) -> None:
    """The skip, measured end to end against two stores rather than read off a window column.

    The two corpora differ in exactly one thing: the growth factor of every security's newest five
    sessions. If a momentum read any of them its value would move, and if the reversal read none
    of them its value would not. The stored window columns cannot show this -- both factors'
    windows end on the same session, because `MOMENTUM_20_SESSIONS` *declares* 25 sessions and
    reads the oldest 20 of them -- so the pair of stores is what the claim needs.
    """
    plain = _written(tmp_path / "plain")
    disturbed = _written(tmp_path / "disturbed", disturb_tail=1.5)

    for definition, compounded, skip in REACHES:
        before = _compute(plain, definition).values()
        after = _compute(disturbed, definition).values()
        moved = {code for code in PLAIN if abs(after[code] - before[code]) > 1e-12}
        assert moved == (set() if skip else set(PLAIN)), definition.key
        assert after[PLAIN[0]] == pytest.approx(
            _expected(PLAIN[0], compounded=compounded, skip=skip, disturb_tail=1.5), rel=1e-12
        )

    windows = {
        definition.key: _by_subject(_compute(plain, definition))[PLAIN[0]]
        for definition, _, _ in REACHES
    }
    assert (
        windows[MOMENTUM_20_SESSIONS.key].input_session_last
        == windows[REVERSAL_5_SESSIONS.key].input_session_last
    )


# --- the coverage codes a halt earns on each factor -----------------------------------------------


def test_a_halt_inside_the_newest_five_refuses_only_the_reversal(store: PanelStore) -> None:
    """`REVERSAL_5_SESSIONS`' span bound is at equality, and that is what one halt costs.

    `HALTED_RECENT` has five own sessions spanning six panel sessions, which is one over a bound
    of five and far inside every momentum's. So the same security, the same `as_of` and the same
    rows produce `insufficient_history` on one factor and a value on the other three -- which is
    the separation the two settings exist for.
    """
    codes = {
        definition.key: _coverage(_compute(store, definition))[HALTED_RECENT]
        for definition, _, _ in REACHES
    }

    assert codes[REVERSAL_5_SESSIONS.key] == "insufficient_history"
    assert set(codes.values()) == {"insufficient_history", "computed"}
    assert codes[MOMENTUM_20_SESSIONS.key] == "computed"
    assert codes[MOMENTUM_60_SESSIONS.key] == "computed"
    assert codes[MOMENTUM_120_SESSIONS.key] == "computed"


@pytest.mark.parametrize(
    ("code", "refused"),
    (
        (HALTED_LONG, MOMENTUM_20_SESSIONS),
        (HALTED_MID, MOMENTUM_60_SESSIONS),
        (HALTED_DEEP, MOMENTUM_120_SESSIONS),
    ),
)
def test_each_momentums_span_bound_refuses_a_halt_run_its_neighbours_tolerate(
    store: PanelStore, code: str, refused: FactorDefinition
) -> None:
    """One halt run, one refusal, and every other factor in the family answers.

    This is the assertion that makes `max_window_sessions` a *per-factor* field rather than a
    number three definitions happen to carry: `HALTED_LONG`'s six missing sessions put its 25 own
    sessions one panel session over 30 while leaving its 65 own sessions seven inside 78,
    `HALTED_MID`'s fourteen do the same one rung up, and `HALTED_DEEP`'s twenty-six do it on the
    widest rung, whose bound no other factor here brackets. A single shared bound could not produce
    these three answers on these three securities.
    """
    codes = {
        definition.key: _coverage(_compute(store, definition))[code] for definition, _, _ in REACHES
    }

    assert codes[refused.key] == "insufficient_history"
    assert [key for key, value in codes.items() if value != "computed"] == [refused.key]


def test_the_widest_span_bound_is_driven_from_both_sides_by_one_session(
    store: PanelStore,
) -> None:
    """`MOMENTUM_120_SESSIONS`' bound of 150, with a security on each side of it.

    `HALTED_AT_THE_BOUND` and `HALTED_DEEP` differ in one halted session and nothing else, so their
    125-session windows span exactly 150 and 151. The first is `computed` and would stop being so
    if the bound were narrowed by one; the second is `insufficient_history` and would stop being so
    if it were widened by one. The span is read off the stored window columns rather than inferred
    from the coverage code, so the two halves cannot both be satisfied by a fixture that provokes
    neither -- which is precisely what the corpus did before these two securities existed.
    """
    observations = _by_subject(_compute(store, MOMENTUM_120_SESSIONS))
    spans = {
        code: SESSIONS.index(item.input_session_last) - SESSIONS.index(item.input_session_first) + 1
        for code, item in observations.items()
        if code in {HALTED_AT_THE_BOUND, HALTED_DEEP}
        and item.input_session_first is not None
        and item.input_session_last is not None
    }

    assert spans == {HALTED_AT_THE_BOUND: 150, HALTED_DEEP: 151}
    assert observations[HALTED_AT_THE_BOUND].coverage == "computed"
    assert observations[HALTED_DEEP].coverage == "insufficient_history"
    assert MOMENTUM_120_SESSIONS.max_window_sessions == 150


def test_the_stored_window_of_a_refused_span_is_the_window_it_was_refused_for(
    store: PanelStore,
) -> None:
    """A span overrun records its window; a count shortfall has none to record.

    Two `insufficient_history` observations that a fifth coverage code would have split, kept
    distinguishable on the stored row instead -- which is `_classify`'s own argument, driven on
    two securities of this corpus rather than asserted in prose.
    """
    observations = _by_subject(_compute(store, MOMENTUM_120_SESSIONS))

    short = observations[YOUNG]
    assert short.coverage == "insufficient_history"
    assert short.input_session_first is None and short.input_session_last is None

    overrun = _by_subject(_compute(store, MOMENTUM_20_SESSIONS))[HALTED_LONG]
    assert overrun.coverage == "insufficient_history"
    assert overrun.input_session_first is not None
    assert overrun.input_session_last == SESSIONS[-1]
    assert (
        SESSIONS.index(overrun.input_session_last) - SESSIONS.index(overrun.input_session_first) + 1
        == 31
    )


def test_a_short_history_is_a_count_shortfall_on_the_two_wider_factors_only(
    store: PanelStore,
) -> None:
    """`YOUNG` has 30 sessions: enough for a reach of 25 and short of one of 65.

    The count and the span are different fields and this security is where the count alone
    decides. Its 30 sessions are consecutive, so no span bound is in play at all -- an assertion
    that only ever provoked `insufficient_history` through a halt would leave `lookback_sessions`
    and `max_window_sessions` inseparable.
    """
    codes = {
        definition.key: _coverage(_compute(store, definition))[YOUNG]
        for definition, _, _ in REACHES
    }

    assert codes == {
        MOMENTUM_20_SESSIONS.key: "computed",
        MOMENTUM_60_SESSIONS.key: "insufficient_history",
        MOMENTUM_120_SESSIONS.key: "insufficient_history",
        REVERSAL_5_SESSIONS.key: "computed",
    }


def test_a_null_close_is_input_missing_on_the_factors_whose_window_contains_it(
    store: PanelStore,
) -> None:
    """One null cell, 22 sessions back: inside three windows and outside the fourth.

    A row with a null column is not a missing session -- the security's own session count is
    untouched -- so this separates `input_missing` from `insufficient_history` on one security,
    and separates it per factor rather than per row. The window's completeness is checked over the
    **declared** window, so a null in a session a momentum declares and does not read still
    refuses it: that is the engine's rule and not this family's, and it is asserted rather than
    assumed because the alternative reading would make the answer depend on the skip.
    """
    codes = {
        definition.key: _coverage(_compute(store, definition))[NULL_CLOSE]
        for definition, _, _ in REACHES
    }

    assert codes == {
        MOMENTUM_20_SESSIONS.key: "input_missing",
        MOMENTUM_60_SESSIONS.key: "input_missing",
        MOMENTUM_120_SESSIONS.key: "input_missing",
        REVERSAL_5_SESSIONS.key: "computed",
    }


def test_a_name_outside_the_universe_is_not_a_data_fault_on_any_of_the_four(
    store: PanelStore,
) -> None:
    """`OUTSIDE` has every row and no value, on all four, with `input_row_count` zero.

    The distinction the coverage vocabulary spends a code on: a name that should have no value is
    not a name whose data is missing, and reporting `input_missing` for it would put a permanent
    false defect on every historical cross section.
    """
    for definition, _, _ in REACHES:
        observation = _by_subject(_compute(store, definition))[OUTSIDE]
        assert observation.coverage == "not_in_universe"
        assert observation.value is None
        assert observation.input_row_count == 0


def test_the_whole_census_is_the_shapes_this_corpus_declares(store: PanelStore) -> None:
    """Every one of the sixteen securities accounted for, on the widest factor.

    A census rather than a spot check, because a per-security assertion cannot see a security that
    silently changed code -- and because `V2-P3-002`'s own lesson is that a build in which nothing
    was computable looks exactly like one that scored the market until somebody counts.
    """
    census = _compute(store, MOMENTUM_120_SESSIONS).coverage_census()

    assert dict(census) == {
        "computed": 12,
        "not_in_universe": 1,
        "insufficient_history": 2,
        "input_missing": 1,
        "undefined_value": 0,
    }


# --- the request-side fault a 120-session reach creates -------------------------------------------


def test_a_january_as_of_needs_the_previous_year_named_and_says_so_when_it_is_not(
    store: PanelStore,
) -> None:
    """Roadmap section 11's consequence "lands directly on `V2-P3-012`", driven both ways.

    A 125-session window evaluated on 2026-01-17 reaches back into 2025, so a requirement naming
    only 2026 makes `insufficient_history` arithmetic for the entire cross section -- which is a
    fault in the request rather than an answer about the data, and `compute_factor` refuses it.
    Naming both years computes. The refusal is asserted on a `match` narrow enough to say which
    rule fired: a generic `FactorEngineError` here would also be raised by four other guards.
    """
    with pytest.raises(FactorEngineError, match=r"needs 125 sessions and the visible panel over"):
        _compute(
            store,
            MOMENTUM_120_SESSIONS,
            requirements={DAILY_DATASET: _requirement(years=(2026,))},
        )

    both = _compute(store, MOMENTUM_120_SESSIONS)
    assert both.coverage_census()["computed"] == 12


# --- why industry-relative momentum is not a fifth definition -------------------------------------


def test_an_industry_code_is_declarable_as_a_factor_input_and_refused_at_the_read(
    tmp_path: Path,
) -> None:
    """The first of the two reasons `V2-P3-012` delivers a composition rather than a definition.

    `FactorField`'s validation is syntactic -- it refuses a name that could not be a panel dataset
    or an unquoted SQL identifier, and it has no store to ask what a column holds -- so
    `index_member_all.l1_code` **is** declarable. What refuses it is `_numeric`, at the read, on
    the ground that a factor input must be a stored number. So an industry cannot be a factor's
    input even before the deeper obstacle applies: an evaluator is handed one security's window
    and never a cross section, and an industry-relative value is a statistic over peers.
    """
    store = PanelStore(tmp_path / "panel")
    day = SESSIONS[-1]
    write_panel_batch(
        store,
        ColumnarPanelBatch(
            provider_id="openalpha-cn/tests",
            dataset=INDUSTRY_MEMBERSHIP_DATASET,
            kind="index_member_all",
            as_of=AS_OF,
            fetched_at=BUILT_AT,
            status="success",
            subjects=PLAIN,
            timeline=TimelineColumns(
                event_time=tuple(_at(day, SESSION_CLOSE_TIME) for _ in PLAIN),
                available_time=tuple(_at(day, SESSION_CLOSE_TIME) for _ in PLAIN),
                ingested_time=tuple(_at(day, SESSION_CLOSE_TIME) for _ in PLAIN),
                revision_time=tuple(_at(day, SESSION_CLOSE_TIME) for _ in PLAIN),
            ),
            columns=(PanelColumn(INDUSTRY_L1_COLUMN, "string", tuple(INDUSTRY_A for _ in PLAIN)),),
        ),
        year=2026,
    )
    definition = FactorDefinition(
        key="probe_industry_relative",
        version=1,
        family="momentum_reversal",
        direction="higher_is_better",
        required_fields=(
            FactorField(dataset=INDUSTRY_MEMBERSHIP_DATASET, column=INDUSTRY_L1_COLUMN),
        ),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )

    with pytest.raises(FactorEngineError, match=r"holds str for .*must be a stored number"):
        compute_factor(
            store,
            definition,
            as_of=AS_OF,
            subjects=PLAIN,
            universe=frozenset(PLAIN),
            requirements={
                INDUSTRY_MEMBERSHIP_DATASET: _requirement(
                    years=(2026,),
                    dataset=INDUSTRY_MEMBERSHIP_DATASET,
                    fields=(INDUSTRY_L1_COLUMN,),
                )
            },
            code_commit=COMMIT,
            built_at=BUILT_AT,
            evaluators={definition.qualified_key: lambda window: 0.0},
        )


# --- the composition that delivers industry-relative momentum -------------------------------------


def _probe_transform() -> FactorTransformSpec:
    """`CROSS_SECTION_STANDARD` with its floor lowered to fit a thirteen-name cross section."""
    return CROSS_SECTION_STANDARD.model_copy(
        update={"key": "probe_cross_section_standard", "min_cross_section": 4}
    )


def _probe_neutralization() -> FactorNeutralizationSpec:
    """`INDUSTRY_AND_SIZE` with both floors lowered and nothing else touched."""
    return INDUSTRY_AND_SIZE.model_copy(
        update={"key": "probe_industry_and_size", "min_cross_section": 4}
    )


def _characteristics(subjects: Sequence[str]) -> Any:
    """The industry and market-cap cross section, as a **value** rather than out of a store.

    `build_industry_market_cap_cross_section` takes values by design -- that is the whole of
    `apply_factor_neutralization`'s point-in-time argument -- so nothing here needs
    `index_member_all` or `daily_basic` on disk. The split is by position in `SECURITIES` so that
    the two groups are of different sizes on whatever cross section the factor produced.
    """
    return build_industry_market_cap_cross_section(
        as_of=AS_OF,
        taxonomy=INDUSTRY_MEMBERSHIP_TAXONOMY,
        industry_level=INDUSTRY_AND_SIZE.industry_level,
        market_cap_measure=INDUSTRY_AND_SIZE.market_cap_measure,
        characteristics=tuple(
            SecurityCharacteristic(
                subject=code,
                industry_code=INDUSTRY_A if SECURITIES.index(code) < 4 else INDUSTRY_B,
                market_cap=CAP_BASE + CAP_STEP * SECURITIES.index(code),
                is_backfilled=False,
            )
            for code in subjects
        ),
    )


def _industry_relative(values: Mapping[str, float], groups: Mapping[str, str]) -> dict[str, float]:
    """`value - the mean value of this security's own industry`: industry-relative momentum.

    Fifteen lines of arithmetic written here rather than imported, because it is the *reference*
    the shipped composition is held against and a reference that called the implementation would
    be asserting that a function equals itself.
    """
    totals: dict[str, list[float]] = {}
    for code, value in values.items():
        totals.setdefault(groups[code], []).append(value)
    means = {group: math.fsum(items) / len(items) for group, items in totals.items()}
    return {code: value - means[groups[code]] for code, value in values.items()}


def _composed(store: PanelStore, definition: FactorDefinition = MOMENTUM_20_SESSIONS) -> Any:
    """`compute_factor -> apply_factor_transform -> apply_factor_neutralization`, in memory."""
    raw = _compute(store, definition)
    processed = apply_factor_transform(
        raw, _probe_transform(), code_commit=COMMIT, built_at=BUILT_AT
    )
    neutralized = apply_factor_neutralization(
        processed,
        _probe_neutralization(),
        _characteristics(SECURITIES),
        code_commit=COMMIT,
        built_at=BUILT_AT,
    )
    return processed, neutralized


def test_the_probe_specs_differ_from_the_shipped_ones_in_their_floors_and_nothing_else() -> None:
    """What makes the composition below a statement about the shipped configuration.

    A probe that quietly used `level` capitalisations or `measured_and_imputed` participation
    would be testing a neutralisation this build does not ship, and the difference would be
    invisible in every assertion that followed. Compared field by field off the models' own
    dumps rather than by naming the fields, so a sixth setting added to either contract joins this
    check without anybody remembering to.
    """
    for probe, shipped, floors in (
        (_probe_transform(), CROSS_SECTION_STANDARD, {"key", "min_cross_section"}),
        (_probe_neutralization(), INDUSTRY_AND_SIZE, {"key", "min_cross_section"}),
    ):
        left = probe.model_dump(mode="json")
        right = shipped.model_dump(mode="json")
        declared = set(type(shipped).model_fields)
        moved = {name for name in declared if left[name] != right[name]}
        assert moved == floors, (probe.key, moved)


def test_the_industry_relative_momentum_is_the_neutralised_residual_of_a_momentum_factor(
    store: PanelStore,
) -> None:
    """`V2-P3-012`'s fifth deliverable, as three calls and one hand-computed reference.

    The residual `INDUSTRY_AND_SIZE` stores is, by Frisch-Waugh-Lovell,
    `(y - mean_y_g) - beta * (x - mean_x_g)`. The first term is industry-relative momentum and
    the second is what the size regressor removes on top of it, so the whole is checked against a
    reference built here out of the processed values, the industry map and the stored slope --
    none of which is read back off the implementation being checked.

    The cross section is the thirteen securities whose 20-session momentum was `computed`; the
    imputed one and the refused ones are `not_a_participant` under `measured_only`, which is the
    shipped participation rule and the reading `V2-P3-005` wants.
    """
    processed, neutralized = _composed(store)
    residuals = neutralized.values()
    groups = neutralized.industries()
    measured = processed.measured_values()
    slope = neutralized.statistics.market_cap_slope

    assert set(residuals) == set(measured)
    assert len(residuals) == 13
    assert set(groups.values()) == {INDUSTRY_A, INDUSTRY_B}
    assert slope is not None

    relative = _industry_relative(measured, groups)
    sizes = {code: math.log(CAP_BASE + CAP_STEP * SECURITIES.index(code)) for code in residuals}
    demeaned_size = _industry_relative(sizes, groups)
    for code, residual in residuals.items():
        assert residual == pytest.approx(
            relative[code] - slope * demeaned_size[code], rel=1e-9, abs=1e-12
        ), code

    assert _coverage(_compute(store, MOMENTUM_20_SESSIONS))[NULL_CLOSE] == "input_missing"
    codes = {item.subject: item.coverage for item in neutralized.observations}
    assert codes[NULL_CLOSE] == "not_a_participant"


def test_the_size_term_is_what_separates_the_residual_from_a_pure_industry_demeaning(
    store: PanelStore,
) -> None:
    """The honest half: the shipped residual is industry-relative **and** size-orthogonal.

    No declarable `FactorNeutralizationSpec` removes industry alone -- `market_cap_scale` has two
    members and neither is "none" -- so what this repository can produce today is the two together,
    and the difference is `beta * (x - mean_x_g)` rather than nothing. Asserted as a floor against
    the residuals' own dispersion rather than as a figure, which is the form `V2-P3-004`'s review
    had to retreat to after three point estimates could not be reproduced: the gap is a property
    of the cross section, and a number quoted here would be a property of this fixture.
    """
    _, neutralized = _composed(store)
    residuals = neutralized.values()
    relative = _industry_relative(_composed(store)[0].measured_values(), neutralized.industries())
    dispersion = neutralized.statistics.residual_dispersion

    assert dispersion is not None and dispersion > 0.0
    gaps = [abs(relative[code] - residuals[code]) for code in residuals]
    assert max(gaps) > 0.1 * dispersion
    assert neutralized.statistics.market_cap_slope != 0.0


def test_the_two_industry_groups_are_different_sizes_so_demeaning_is_not_a_global_mean(
    store: PanelStore,
) -> None:
    """The fixture defect `V2-P3-004`'s review found, refused here rather than repeated.

    On one industry group, demeaning by group and demeaning the whole cross section are the same
    arithmetic, and `smallest_industry_size` / `largest_industry_size` are one number twice. Both
    halves are driven: the two groups are asserted to be of different sizes off the stored
    statistics, and the group-demeaned reference is asserted to differ from the globally demeaned
    one by more than floating-point noise.
    """
    _, neutralized = _composed(store)
    statistics = neutralized.statistics
    measured = _composed(store)[0].measured_values()

    assert statistics.industry_count == 2
    assert statistics.smallest_industry_size != statistics.largest_industry_size
    assert statistics.smallest_industry_size + statistics.largest_industry_size == 13

    by_group = _industry_relative(measured, neutralized.industries())
    globally = _industry_relative(measured, dict.fromkeys(measured, "_ONE_"))
    assert max(abs(by_group[code] - globally[code]) for code in measured) > 1e-6
