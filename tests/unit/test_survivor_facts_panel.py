"""The panel half of the eligibility-facts adapter (`V2-P7-016`, plan 2 task A2).

`build_facts_day` reads one signal day off a stored panel and hands the kernel one
`SecurityFacts` row per listed security -- a halted one with no bar included -- or refuses by
name. Everything here runs against a panel generated at test time and written through the real
`panel_ingest` writers (`AGENTS.md` hard rule 6); nothing is read from a research store.

## The frame

The securities of `FRAME` over the 75 weekday sessions 2026-01-05 .. 2026-04-17 of
`panel_fixtures`' 2026 calendar (closed only on 1-2 January). The signal day is `SESSIONS[69]`,
Friday 2026-04-10, read at 18:30 Asia/Shanghai -- after that session's 16:30 publication. Every
security is built from two numbers, its position `j` in `FRAME` and the session index `t`.
`FRAME` is the first 40 securities in code order followed by the ones added after review, in the
order they were added, so a new security never moves an existing one's numbers:

- close `10 + j`, constant, except `HALTED`'s `20 + 0.25 t`, so its carried close is a number
  no other session has;
- `amount` (thousand yuan) `1000 (j + 1) + 10 t`, so a 20-session mean is
  `1000 (j + 1) + 10 * mean(t)`;
- the published band `round(pre_close * (1 +/- r), 2)` with `r` 10% (5% for an ST name, 20% on
  ChiNext and STAR);
- each factor's raw value `(k + 1) + (j + 1) / 100 + t / 10000` for the `k`-th factor, built at
  16:30 on a session `t` through the real engine with that value injected as the evaluator, and
  processed by an identity transform (no winsorization, no standardization), so the stored
  processed value **is** that number and an oriented value is it, negated for
  `lower_is_better`.

The engine still decides whether a security has a complete window (`panel_factors`), and the
four factors are chosen for that: `reversal_1d` and `reversal_5_sessions` are strict (a window of
2 and of 5 sessions, `max_window_sessions == lookback_sessions`), `momentum_20_sessions` needs 25
own sessions within 30 and `amihud_60` 60 within 80. Two lower-is-better and two
higher-is-better, so the orientation is visible in both directions.

`FULL` also stores what happens after the signal day -- three more sessions of bars, bands and
halts, a rename announced on 2026-04-13, a listing on 2026-04-15 and a factor build on
2026-04-13 -- and every expectation below is read off it, so each one is also a look-ahead
check. `TRIMMED` is the same panel cut at the signal day.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from panel_fixtures import (
    DAILY_BASIC_EXTRA_COLUMNS,
    EXCHANGE,
    GeneratedPanel,
    generate_panel,
    write_generated_panel,
)
from panel_fixtures import _batch as batch
from panel_fixtures import _close_time as close_time
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from panel_fixtures import _published_time as published_time

from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PRICE_DATE_COLUMN,
)
from openalpha_cn.domain.factor_transform import (
    FactorTransformSpec,
    MissingValuePolicy,
    ProcessedFactorObservation,
    WinsorizationPolicy,
)
from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
    NameRecord,
)
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import (
    DELISTING_EVENT,
    LISTING_EVENT,
    STOCK_BASIC_DATASET,
)
from openalpha_cn.domain.survivor_book import SecurityFacts, SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorEngineError,
    FactorPanel,
    FactorWindow,
    apply_factor_transform,
    compute_factor,
    load_processed_factor_observations,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import (
    daily_requirement,
    session_publication_instant,
    split_panel_batch_by_year,
    write_name_history,
    write_panel_batch,
)
from openalpha_cn.survivor_facts import (
    FactsCoverageError,
    FactsDay,
    FactsNotes,
    SurvivorFactsError,
    build_facts_day,
    require_coverage,
)

# --- the frame ---------------------------------------------------------------------------------

FIRST_SESSION: Final[date] = date(2026, 1, 5)
SESSION_COUNT: Final[int] = 75
SIGNAL_INDEX: Final[int] = 69


def _weekdays(first: date, count: int) -> tuple[date, ...]:
    days: list[date] = []
    day = first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


SESSIONS: Final[tuple[date, ...]] = _weekdays(FIRST_SESSION, SESSION_COUNT)
SIGNAL: Final[date] = SESSIONS[SIGNAL_INDEX]
"""Friday 2026-04-10."""
AS_OF: Final[datetime] = datetime(2026, 4, 10, 10, 30, tzinfo=UTC)
"""18:30 Asia/Shanghai on the signal day: its 16:30 publication has passed, Monday's has not."""
LATER: Final[datetime] = datetime(2026, 4, 18, 4, 0, tzinfo=UTC)
"""Noon Asia/Shanghai the day after the last stored session: everything stored is visible."""

ORDINARY: Final[tuple[str, ...]] = (
    *(f"6000{n:02d}.SH" for n in range(1, 15)),
    *(f"0000{n:02d}.SZ" for n in range(1, 15)),
)
ST_BY_NAME: Final[str] = "600601.SH"
ST_BY_BAND: Final[str] = "600602.SH"
ST_PENDING: Final[str] = "600603.SH"
DELISTING: Final[str] = "600604.SH"
HALTED: Final[str] = "600605.SH"
"""Whole-day halted on sessions 67, 68 and 69: no bar on the signal day."""
GAPPED: Final[str] = "600606.SH"
"""Whole-day halted on sessions 62 and 63, trading again from 64."""
HALT_THROUGH_CLOSE: Final[str] = "600607.SH"
"""Halted 14:00-15:00 on the signal day: it has a bar, and could not trade at the close."""
HALT_IN_MORNING: Final[str] = "600608.SH"
"""Halted 09:30-10:30 on the signal day: it has a bar and traded at the close."""
NEW_LISTING: Final[str] = "600609.SH"
"""Listed on session 60, 2026-03-30."""
CHINEXT: Final[str] = "300601.SZ"
STAR: Final[str] = "688601.SH"
UNREGISTERED: Final[str] = "000999.SZ"
"""Priced every session, absent from the registry (T3 3.3's migrated-code shape)."""
BEFORE_FIRST: Final[str] = "000014.SZ"
"""Its only rename is announced 2026-04-01 and takes effect 2026-04-15: a gap on the signal day."""
RENAMED_LATER: Final[str] = "600001.SH"
"""`FULL` only: an ST rename announced 2026-04-13, after the signal day."""
LISTED_LATER: Final[str] = "600611.SH"
"""`FULL` only: a registry listing dated 2026-04-15."""
ONLY_TRADER: Final[str] = "600001.SH"
"""The one security that trades on the outage day."""
DELISTED_WITH_BARS: Final[str] = "000998.SZ"
"""Delisted on session 60 by the registry (its exclusive day) and priced every session all the
same: a code the registry knows and does not list on the signal day, with a bar on it."""

FRAME: Final[tuple[str, ...]] = (
    *sorted(
        (
            *ORDINARY,
            ST_BY_NAME,
            ST_BY_BAND,
            ST_PENDING,
            DELISTING,
            HALTED,
            GAPPED,
            HALT_THROUGH_CLOSE,
            HALT_IN_MORNING,
            NEW_LISTING,
            CHINEXT,
            STAR,
            UNREGISTERED,
        )
    ),
    # Added after review, in the order they were added, so `j` = 40, 41, ...
    DELISTED_WITH_BARS,
)
"""Every security of the frame, in the order that gives each its position `j`."""
SECURITIES: Final[tuple[str, ...]] = tuple(sorted(FRAME))
NO_ROW: Final[frozenset[str]] = frozenset({DELISTED_WITH_BARS})
"""The securities of the frame that have no fact row on the signal day."""
ROWS: Final[tuple[str, ...]] = tuple(code for code in SECURITIES if code not in NO_ROW)
"""The signal day's fact rows, ascending by code."""
LISTED_BEFORE_THE_CALENDAR: Final[date] = date(2025, 1, 2)
NEW_LISTING_INDEX: Final[int] = 60
HALTED_SESSIONS: Final[frozenset[int]] = frozenset({67, 68, 69})
GAPPED_SESSIONS: Final[frozenset[int]] = frozenset({62, 63})
DELISTED_ON: Final[Mapping[str, int]] = MappingProxyType({DELISTED_WITH_BARS: 60})
"""The session each delisted security's registry delisting row is dated (exclusive)."""

FACTORS: Final[tuple[str, ...]] = (
    "reversal_1d/v1",
    "reversal_5_sessions/v1",
    "momentum_20_sessions/v1",
    "amihud_60/v1",
)
IDENTITY: Final[FactorTransformSpec] = FactorTransformSpec(
    key="probe_identity",
    version=1,
    winsorization=WinsorizationPolicy(method="none"),
    standardization="none",
    missing_values=MissingValuePolicy(
        not_in_universe="exclude",
        insufficient_history="exclude",
        ambiguous_filing="exclude",
        input_missing="exclude",
        undefined_value="exclude",
    ),
    min_cross_section=1,
)
"""A transform that leaves the raw value as it is, so a processed value is the injected number.

The shipped `cross_section_standard/v1` needs 100 names (`panel_factors.CROSS_SECTION_STANDARD`)
and this panel has 40, which is why `build_facts_day` takes the transform as an argument."""
COMMIT: Final[str] = "abcdef1"


def position(code: str) -> int:
    return FRAME.index(code)


def instant(index: int) -> datetime:
    """The build instant of session `index`: its 16:30 publication, as the daily command builds."""
    return session_publication_instant(SESSIONS[index])


def raw_value(factor: int, code: str, index: int) -> float:
    """The `factor`-th factor's raw value for `code` in the build at session `index`."""
    return (factor + 1) + (position(code) + 1) / 100 + index / 10000


def close_of(code: str, index: int) -> float:
    if code == HALTED:
        return 20.0 + 0.25 * index
    return 10.0 + position(code)


def amount_of(code: str, index: int) -> float:
    return 1000.0 * (position(code) + 1) + 10.0 * index


# --- the panel -----------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Variant:
    """What one stored panel holds."""

    last: int = SESSION_COUNT - 1
    """The newest session with bars, bands and halts."""
    outage: bool = False
    """Every security but `ONLY_TRADER` whole-day halted on the signal day."""
    builds: tuple[int, ...] = (68, SIGNAL_INDEX, 70)
    """The sessions a factor build is stored at."""
    undefined: Mapping[int, frozenset[str]] = dataclasses.field(
        default_factory=lambda: MappingProxyType({})
    )
    """Per build session, the securities whose `reversal_1d` evaluator answers `None`."""


FULL: Final[Variant] = Variant()
TRIMMED: Final[Variant] = Variant(last=SIGNAL_INDEX, builds=(68, SIGNAL_INDEX))


def scheduled(code: str, index: int) -> bool:
    """Whether `code` has a bar on session `index` when nothing market-wide goes wrong."""
    if code == NEW_LISTING and index < NEW_LISTING_INDEX:
        return False
    if code == HALTED and index in HALTED_SESSIONS:
        return False
    return not (code == GAPPED and index in GAPPED_SESSIONS)


def trades(code: str, index: int, variant: Variant) -> bool:
    if not scheduled(code, index):
        return False
    return not (variant.outage and index == SIGNAL_INDEX and code != ONLY_TRADER)


def banded_on(code: str, index: int) -> bool:
    """Whether a band is published for `code` on session `index`: from its first session on."""
    return code != NEW_LISTING or index >= NEW_LISTING_INDEX


def halt_rows(variant: Variant) -> dict[tuple[str, int], str | None]:
    """`(code, session) -> suspend_timing` for every `S` row; `None` is a whole-day halt."""
    rows: dict[tuple[str, int], str | None] = {}
    rows.update({(HALTED, index): None for index in HALTED_SESSIONS})
    rows.update({(GAPPED, index): None for index in GAPPED_SESSIONS})
    if variant.outage:
        rows.update(
            {
                (code, SIGNAL_INDEX): None
                for code in SECURITIES
                if code != ONLY_TRADER and scheduled(code, SIGNAL_INDEX)
            }
        )
    else:
        rows[(HALT_THROUGH_CLOSE, SIGNAL_INDEX)] = "14:00-15:00"
        rows[(HALT_IN_MORNING, SIGNAL_INDEX)] = "09:30-10:30"
    return {key: value for key, value in rows.items() if key[1] <= variant.last}


def band_ratio(code: str, index: int) -> float:
    if code == ST_BY_BAND or (code == ST_BY_NAME and index >= 20):
        return 0.05
    if code == ST_PENDING and index >= 70:
        return 0.05
    if code in (CHINEXT, STAR):
        return 0.20
    return 0.10


def previous_close(code: str, index: int, variant: Variant) -> float:
    """The close of the newest earlier session `code` traded, else one step before its first."""
    for earlier in range(index - 1, -1, -1):
        if trades(code, earlier, variant):
            return close_of(code, earlier)
    return close_of(code, index) - (0.25 if code == HALTED else 0.0)


def listing_day(code: str) -> date:
    return SESSIONS[NEW_LISTING_INDEX] if code == NEW_LISTING else LISTED_BEFORE_THE_CALENDAR


def lifecycle_rows(variant: Variant) -> list[tuple[str, str, date]]:
    """`(code, lifecycle event, day)` for every registry row the variant stores."""
    rows = [(code, LISTING_EVENT, listing_day(code)) for code in SECURITIES if code != UNREGISTERED]
    rows.extend(
        (code, DELISTING_EVENT, SESSIONS[index])
        for code, index in sorted(DELISTED_ON.items())
        if index <= variant.last
    )
    if variant.last >= 72:
        rows.append((LISTED_LATER, LISTING_EVENT, SESSIONS[72]))
    return rows


def name_records(variant: Variant) -> dict[str, tuple[NameRecord, ...]]:
    def record(code: str, name: str, effective: date, announced: date) -> NameRecord:
        return NameRecord(
            ts_code=code,
            name=name,
            effective_from=effective,
            announced_on=announced,
            change_reason="其他",
        )

    baseline = date(2025, 3, 3)
    records: dict[str, list[NameRecord]] = {
        code: [record(code, f"普通{index}", baseline, baseline)]
        for index, code in enumerate(ORDINARY)
        if index % 2 == 0
    }
    records[BEFORE_FIRST] = [record(BEFORE_FIRST, "改名甲", date(2026, 4, 15), date(2026, 4, 1))]
    records[ST_BY_NAME] = [
        record(ST_BY_NAME, "甲公司", baseline, baseline),
        record(ST_BY_NAME, "*ST甲", date(2026, 2, 2), date(2026, 1, 30)),
    ]
    records[ST_PENDING] = [
        record(ST_PENDING, "乙公司", baseline, baseline),
        record(ST_PENDING, "ST乙", date(2026, 4, 13), date(2026, 4, 10)),
    ]
    records[DELISTING] = [
        record(DELISTING, "丙公司", baseline, baseline),
        record(DELISTING, "丙退", date(2026, 3, 2), date(2026, 2, 27)),
    ]
    if variant.last >= 70:
        records[RENAMED_LATER].append(
            record(RENAMED_LATER, "ST丁", date(2026, 4, 14), date(2026, 4, 13))
        )
    return {code: tuple(group) for code, group in records.items()}


def _fetched(available: Sequence[datetime]) -> datetime:
    return max(available)


def build_panel(variant: Variant) -> GeneratedPanel:
    """`panel_fixtures.generate_panel`'s calendar with this module's securities and prices."""
    base = generate_panel(window=(SESSIONS[0], SESSIONS[-1]))
    assert base.sessions == SESSIONS, "the 2026 fixture calendar is open on every weekday here"
    sessions = SESSIONS[: variant.last + 1]
    priced = [
        (index, code)
        for index in range(len(sessions))
        for code in SECURITIES
        if trades(code, index, variant)
    ]
    available = [published_time(SESSIONS[index]) for index, _ in priced]
    closes = [close_of(code, index) for index, code in priced]
    pre_closes = [previous_close(code, index, variant) for index, code in priced]
    trade_dates = tuple(SESSIONS[index].isoformat() for index, _ in priced)
    bars = batch(
        DAILY_DATASET,
        subjects=[code for _, code in priced],
        columns=[
            PanelColumn(PRICE_DATE_COLUMN, "string", trade_dates),
            *(PanelColumn(name, "float", tuple(closes)) for name in ("open", "high", "low")),
            PanelColumn("close", "float", tuple(closes)),
            PanelColumn("pre_close", "float", tuple(pre_closes)),
            PanelColumn(
                "pct_chg",
                "float",
                tuple(
                    (value / before - 1.0) * 100.0
                    for value, before in zip(closes, pre_closes, strict=True)
                ),
            ),
            PanelColumn("vol", "float", tuple(100.0 for _ in priced)),
            PanelColumn("amount", "float", tuple(amount_of(code, index) for index, code in priced)),
        ],
        event_time=[close_time(SESSIONS[index]) for index, _ in priced],
        available_time=available,
        fetched_at=_fetched(available),
    )
    valuations = batch(
        DAILY_BASIC_DATASET,
        subjects=[code for _, code in priced],
        columns=[
            PanelColumn(PRICE_DATE_COLUMN, "string", trade_dates),
            PanelColumn("close", "float", tuple(closes)),
            *(
                PanelColumn(name, "float", tuple(1.0 for _ in priced))
                for name in DAILY_BASIC_EXTRA_COLUMNS
            ),
        ],
        event_time=[close_time(SESSIONS[index]) for index, _ in priced],
        available_time=available,
        fetched_at=_fetched(available),
    )
    banded = [
        (index, code)
        for index in range(len(sessions))
        for code in SECURITIES
        if banded_on(code, index)
    ]
    band_available = [published_time(SESSIONS[index]) for index, _ in banded]
    # A band is published on the previous close; for a halted session that is the last close.
    references = [previous_close(code, index, variant) for index, code in banded]
    limits = batch(
        PRICE_LIMIT_DATASET,
        subjects=[code for _, code in banded],
        columns=[
            PanelColumn(
                PRICE_DATE_COLUMN,
                "string",
                tuple(SESSIONS[index].isoformat() for index, _ in banded),
            ),
            PanelColumn(
                "up_limit",
                "float",
                tuple(
                    round(reference * (1 + band_ratio(code, index)), 2)
                    for reference, (index, code) in zip(references, banded, strict=True)
                ),
            ),
            PanelColumn(
                "down_limit",
                "float",
                tuple(
                    round(reference * (1 - band_ratio(code, index)), 2)
                    for reference, (index, code) in zip(references, banded, strict=True)
                ),
            ),
        ],
        event_time=[close_time(SESSIONS[index]) for index, _ in banded],
        available_time=band_available,
        fetched_at=_fetched(band_available),
    )
    halts = sorted(halt_rows(variant).items(), key=lambda item: (item[0][1], item[0][0]))
    halt_available = [published_time(SESSIONS[index]) for (_, index), _ in halts]
    suspensions = batch(
        SUSPENSION_DATASET,
        subjects=[code for (code, _), _ in halts],
        columns=[
            PanelColumn(
                PRICE_DATE_COLUMN,
                "string",
                tuple(SESSIONS[index].isoformat() for (_, index), _ in halts),
            ),
            PanelColumn("suspend_type", "string", tuple("S" for _ in halts)),
            PanelColumn("suspend_timing", "string", tuple(timing for _, timing in halts)),
        ],
        event_time=[close_time(SESSIONS[index]) for (_, index), _ in halts],
        available_time=halt_available,
        fetched_at=_fetched(halt_available),
    )
    lifecycle = lifecycle_rows(variant)
    listed_at = [midnight_shanghai(day) for _, _, day in lifecycle]
    registry = batch(
        STOCK_BASIC_DATASET,
        subjects=[code for code, _, _ in lifecycle],
        columns=[
            PanelColumn("lifecycle_event", "string", tuple(event for _, event, _ in lifecycle)),
            PanelColumn(
                "lifecycle_date", "string", tuple(day.isoformat() for _, _, day in lifecycle)
            ),
            PanelColumn(
                "exchange",
                "string",
                tuple("SSE" if code.endswith(".SH") else "SZSE" for code, _, _ in lifecycle),
            ),
        ],
        event_time=listed_at,
        available_time=listed_at,
        fetched_at=_fetched(listed_at),
    )
    names = name_records(variant)
    # `panel_fixtures._namechange_batch`'s projection, fetched when its newest row was knowable:
    # that builder dates every batch at its January `AS_OF`, before most renames here.
    renamed = sorted(
        (record for group in names.values() for record in group),
        key=lambda record: (record.announced_on, record.ts_code, record.name),
    )
    announced = [midnight_shanghai(record.announced_on) for record in renamed]
    renames = batch(
        NAMECHANGE_DATASET,
        subjects=[record.ts_code for record in renamed],
        columns=[
            PanelColumn(NAME_COLUMN, "string", tuple(record.name for record in renamed)),
            PanelColumn(
                NAME_EFFECTIVE_COLUMN,
                "string",
                tuple(record.effective_from.isoformat() for record in renamed),
            ),
            PanelColumn(
                NAME_ANNOUNCEMENT_COLUMN,
                "string",
                tuple(record.announced_on.isoformat() for record in renamed),
            ),
            PanelColumn(
                NAME_REASON_COLUMN, "string", tuple(record.change_reason for record in renamed)
            ),
        ],
        event_time=announced,
        available_time=announced,
        fetched_at=_fetched(announced),
    )
    return dataclasses.replace(
        base,
        securities=SECURITIES,
        sessions=sessions,
        batches=MappingProxyType(
            {
                TRADING_CALENDAR_DATASET: base.batch(TRADING_CALENDAR_DATASET),
                STOCK_BASIC_DATASET: registry,
                NAMECHANGE_DATASET: renames,
                DAILY_DATASET: bars,
                DAILY_BASIC_DATASET: valuations,
                SUSPENSION_DATASET: suspensions,
                PRICE_LIMIT_DATASET: limits,
            }
        ),
        name_records=MappingProxyType(names),
        as_of=LATER,
    )


def factor_build(
    store: PanelStore, panel: GeneratedPanel, factor: int, index: int, variant: Variant
) -> FactorPanel:
    definition = FACTOR_DEFINITIONS.get(FACTORS[factor])
    at = instant(index)
    undefined = variant.undefined.get(index, frozenset()) if factor == 0 else frozenset()

    def evaluate(window: FactorWindow) -> float | None:
        if window.subject in undefined:
            return None
        return raw_value(factor, window.subject, index)

    return compute_factor(
        store,
        definition,
        as_of=at,
        subjects=SECURITIES,
        universe=frozenset(SECURITIES),
        requirements={
            DAILY_DATASET: daily_requirement(
                panel.calendar(), years=(2026,), as_of=at, max_staleness=timedelta(days=5)
            )
        },
        code_commit=COMMIT,
        built_at=at,
        evaluators={definition.qualified_key: evaluate},
    )


def write_panel(root: Path, variant: Variant) -> PanelStore:
    store = PanelStore(root / "panel")
    panel = build_panel(variant)
    prices = () if variant.outage else (DAILY_DATASET, DAILY_BASIC_DATASET)
    write_generated_panel(
        store,
        panel,
        datasets=(
            TRADING_CALENDAR_DATASET,
            STOCK_BASIC_DATASET,
            SUSPENSION_DATASET,
            *prices,
            PRICE_LIMIT_DATASET,
        ),
    )
    if variant.outage:
        # `write_daily_panel` refuses a session with under half the median cross section
        # (`_refuse_thin_price_sessions`) -- the first defence against this day, and the reason
        # the outage store is written past it, with the partition writer, to reach the second.
        write_panel_batch(store, panel.batch(DAILY_DATASET), year=2026)
    # One announcement year per partition (`write_name_history`): 2025 and 2026 here.
    for _year, part in split_panel_batch_by_year(panel.batch(NAMECHANGE_DATASET)):
        write_name_history(store, part)
    raw = [
        factor_build(store, panel, factor, index, variant)
        for index in variant.builds
        for factor in range(len(FACTORS))
    ]
    write_factor_panels(store, raw)
    write_processed_factor_panels(
        store,
        [
            apply_factor_transform(source, IDENTITY, code_commit=COMMIT, built_at=source.built_at)
            for source in raw
        ],
    )
    return store


@pytest.fixture(scope="module")
def full_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("full"), FULL)


@pytest.fixture(scope="module")
def trimmed_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("trimmed"), TRIMMED)


COVERAGE: Final[Variant] = Variant(
    last=SIGNAL_INDEX,
    builds=(68, SIGNAL_INDEX),
    undefined=MappingProxyType(
        {68: frozenset(ORDINARY[:18]), SIGNAL_INDEX: frozenset(ORDINARY[:19])}
    ),
)
"""`reversal_1d` answers `None` for 18 more securities on session 68 and 19 more on 69."""


@pytest.fixture(scope="module")
def coverage_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("coverage"), COVERAGE)


OUTAGE: Final[Variant] = Variant(last=SIGNAL_INDEX, outage=True, builds=(SIGNAL_INDEX,))


@pytest.fixture(scope="module")
def outage_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("outage"), OUTAGE)


# --- the narrow read: one build by its instant (`panel_factors`) ----------------------------------


def stored_rows(
    store: PanelStore, factor: int, *, as_of: datetime, at: datetime | None = None
) -> tuple[ProcessedFactorObservation, ...]:
    definition = FACTOR_DEFINITIONS.get(FACTORS[factor])
    if at is None:
        return load_processed_factor_observations(
            store, definition, IDENTITY, years=(2026,), as_of=as_of
        )
    return load_processed_factor_observations(
        store, definition, IDENTITY, years=(2026,), as_of=as_of, at=at
    )


def bits(rows: Sequence[ProcessedFactorObservation]) -> list[tuple[object, ...]]:
    """Every field of every row, the value as its exact IEEE bits, ordered by subject."""
    return sorted(
        (
            row.subject,
            row.as_of,
            None if row.value is None else row.value.hex(),
            row.coverage,
            row.transform_id,
            row.transform_manifest_id,
            row.source_factor_id,
            row.source_manifest_id,
            row.source_coverage,
        )
        for row in rows
    )


@pytest.mark.parametrize("factor", range(len(FACTORS)))
@pytest.mark.parametrize("build", [68, SIGNAL_INDEX, 70])
def test_the_narrow_read_is_the_whole_year_read_cut_to_one_build_bit_for_bit(
    full_store: PanelStore, factor: int, build: int
) -> None:
    whole = stored_rows(full_store, factor, as_of=LATER)
    narrow = stored_rows(full_store, factor, as_of=LATER, at=instant(build))

    assert {row.as_of for row in whole} == {instant(index) for index in FULL.builds}
    expected = [row for row in whole if row.as_of == instant(build)]
    assert len(narrow) == len(expected) == len(SECURITIES)
    assert bits(narrow) == bits(expected)


def test_a_later_build_is_not_part_of_an_earlier_instants_answer(full_store: PanelStore) -> None:
    # Builds at 68, 69 and 70 are all visible at `LATER`; asking for 68's instant returns 68's
    # 40 rows and none of the others, whose raw values differ in the fourth decimal (t / 10000).
    rows = stored_rows(full_store, 0, as_of=LATER, at=instant(68))

    assert {row.as_of for row in rows} == {instant(68)}
    by_subject = {row.subject: row.value for row in rows}
    assert by_subject[ORDINARY[0]] == raw_value(0, ORDINARY[0], 68)


def test_an_instant_with_no_build_answers_with_no_rows(full_store: PanelStore) -> None:
    # Session 67 has no build. Empty, not an error, by choice: the narrow read must be the
    # whole-year read cut to one instant, and that cut is empty too; whether a day without a
    # build is acceptable is the caller's question (`build_facts_day` refuses it through its
    # coverage guard, with the counts). A partition that was never written still refuses.
    narrow = stored_rows(full_store, 0, as_of=LATER, at=instant(67))
    whole = stored_rows(full_store, 0, as_of=LATER)

    assert narrow == ()
    assert [row for row in whole if row.as_of == instant(67)] == []


def test_a_build_not_yet_visible_at_the_read_is_refused_by_name(full_store: PanelStore) -> None:
    # The 2026-04-13 build is stored, and at 18:30 on 2026-04-10 it had not happened. The
    # whole-year read at that instant leaves it out silently -- it is a year, and builds after
    # the read are simply not part of it -- but a read that names that one instant is asking
    # about the future, and an empty answer would read as "no build that day".
    whole = stored_rows(full_store, 0, as_of=AS_OF)
    assert {row.as_of for row in whole} == {instant(68), instant(SIGNAL_INDEX)}

    with pytest.raises(FactorEngineError, match=r"2026-04-13T16:30:00\+08:00.*after.*2026-04-10"):
        stored_rows(full_store, 0, as_of=AS_OF, at=instant(70))


def test_the_signal_days_build_is_visible_at_the_read(full_store: PanelStore) -> None:
    rows = stored_rows(full_store, 3, as_of=AS_OF, at=instant(SIGNAL_INDEX))
    admitted = {
        row.subject: row.value
        for row in rows
        if row.coverage in TIER_ADMITTED_CODES["processed"] and row.value is not None
    }
    # amihud_60 (60 own sessions within 80): every security but NEW_LISTING (10 sessions).
    assert sorted(admitted) == sorted(set(SECURITIES) - {NEW_LISTING})
    assert admitted[HALTED] == raw_value(3, HALTED, SIGNAL_INDEX)


# --- one day's facts ------------------------------------------------------------------------------

CONFIG: Final[SurvivorBookConfig] = SurvivorBookConfig(
    seed=7, composite_factors=FACTORS, min_listing_sessions=60
)
"""The default boards (`("main",)`), and a listing minimum of 60 so a 70-session calendar can
age the securities listed before it (`listing_age_sessions` refuses a truncated count below it)."""


def evening(index: int) -> datetime:
    """18:30 Asia/Shanghai on session `index`: after its publication, before the next one's."""
    return instant(index) + timedelta(hours=2)


def read_day(store: PanelStore, index: int = SIGNAL_INDEX, **overrides: object) -> FactsDay:
    arguments: dict[str, object] = {
        "store": store,
        "session": SESSIONS[index],
        "as_of": evening(index),
        "config": CONFIG,
        "factor_ids": FACTORS,
        "transform": IDENTITY,
        "exchange": EXCHANGE,
    }
    arguments.update(overrides)
    return build_facts_day(**arguments)  # type: ignore[arg-type]


def fingerprint(day: FactsDay) -> str:
    """Every byte the day carries, as one canonical JSON string."""
    return json.dumps(
        {
            "session": day.session.isoformat(),
            "facts": [fact.model_dump(mode="json") for fact in day.facts],
            "notes": dataclasses.asdict(day.notes),
            "universe_size": day.universe_size,
            "scored_size": day.scored_size,
        },
        sort_keys=True,
    )


def read_signal_day(store_root: str) -> str:
    """The subprocess half of the hash-seed test: the signal day off an existing store."""
    return fingerprint(read_day(PanelStore(Path(store_root))))


def mean(values: Sequence[float]) -> Decimal:
    return sum((Decimal(str(value)) for value in values), Decimal(0)) / len(values)


def expected_fact(code: str) -> SecurityFacts:
    """The hand-derived row for `code` on the signal day (session 69), from the frame's rules.

    - board: `600*`/`000*` main, `300*` growth, `688*` star;
    - listed sessions: listed 2025-01-02, before the calendar's 2026-01-01, so counted from it:
      2026-01-05 .. 2026-04-10 is 70 sessions (>= the 60 minimum); `NEW_LISTING` from session
      60, 10 sessions; `UNREGISTERED` has no listing row, so 0;
    - special treatment: ST by name, by a pending ST name, by the 5% band, or a delisting name;
    - suspended: no bar today (`HALTED`) or halted through the close (`HALT_THROUGH_CLOSE`);
    - close: today's, or `HALTED`'s last, 20 + 0.25 * 66 = 36.5;
    - average amount: over the security's bars in sessions 50..69, or `HALTED`'s last 20 bars
      (sessions 47..66);
    - oriented values: the signal-day build's processed value, negated for the two
      lower-is-better factors; `None` where the engine had no complete window -- `NEW_LISTING`
      in the 25- and 60-session ones. `HALTED` has all four: the engine's window is the
      security's own sessions, and its last two (65, 66) and last five (62..66) are unbroken,
      so the build values it as of its last trading session, and the adapter takes that.
    """
    if code == HALTED:
        close, bars = close_of(HALTED, 66), range(47, 67)
    else:
        close = close_of(code, SIGNAL_INDEX)
        bars = range(50, 70)
    amounts = [amount_of(code, index) for index in bars if trades(code, index, FULL)]
    oriented: list[float | None] = []
    for factor in range(len(FACTORS)):
        missing = code == NEW_LISTING and factor in (2, 3)
        sign = -1.0 if factor in (0, 1) else 1.0
        oriented.append(None if missing else sign * raw_value(factor, code, SIGNAL_INDEX))
    return SecurityFacts(
        ts_code=code,
        board="growth" if code == CHINEXT else "star" if code == STAR else "main",
        listed_sessions=0 if code == UNREGISTERED else 10 if code == NEW_LISTING else 70,
        special_treatment=code in (ST_BY_NAME, ST_BY_BAND, ST_PENDING, DELISTING),
        suspended=code in (HALTED, HALT_THROUGH_CLOSE),
        close=Decimal(str(close)),
        avg_amount=mean(amounts),
        oriented_values=tuple(oriented),
    )


def test_every_listed_security_gets_exactly_one_hand_derived_fact(full_store: PanelStore) -> None:
    day = read_day(full_store)

    assert day.session == SIGNAL
    assert [fact.ts_code for fact in day.facts] == list(ROWS)  # ascending, one each
    assert day.facts == tuple(expected_fact(code) for code in ROWS)
    # 40 rows; 38 scored -- HALTED has no bar today and NEW_LISTING lacks two values.
    assert (day.universe_size, day.scored_size) == (40, 38)


def test_a_registry_code_not_listed_on_the_day_has_no_row_even_with_a_bar(
    full_store: PanelStore,
) -> None:
    # DELISTED_WITH_BARS left the registry on session 60 and is priced on the signal day all the
    # same (a `.BJ` code priced before its listing date, T3 3.2, is the same case the other way
    # round). The registry knows it and does not list it that day, so its bar does not bring it
    # in: only a code the registry has no listing row for at all is added through its bar.
    day = read_day(full_store)

    assert DELISTED_WITH_BARS not in {fact.ts_code for fact in day.facts}
    assert day.notes.no_listing_row == 1  # UNREGISTERED alone


def test_a_code_the_registry_has_never_listed_gets_a_row_aged_zero_and_is_counted(
    full_store: PanelStore,
) -> None:
    # UNREGISTERED has a bar on the day and no listing row at all (T3 3.3's migrated-code
    # shape): it is in the universe, aged 0 so the screen says `listing_too_recent`.
    day = read_day(full_store)
    fact = {fact.ts_code: fact for fact in day.facts}[UNREGISTERED]

    assert fact.listed_sessions == 0
    assert day.notes.no_listing_row == 1


@pytest.mark.parametrize(
    ("code", "close", "avg_amount", "oriented"),
    [
        # HALTED, j = 34: last bar session 66, close 20 + 0.25 * 66 = 36.5; its last 20 bars
        # are sessions 47..66, mean t = 56.5, so 1000 * 35 + 10 * 56.5 = 35565.
        # Values off its own last sessions (k + 1) + 0.35 + 0.0069: the two reversals are
        # lower-is-better, so -1.3569 and -2.3569; momentum 3.3569 and amihud 4.3569 as stored.
        (HALTED, "36.5", "35565", (-1.3569, -2.3569, 3.3569, 4.3569)),
        # GAPPED, j = 35: no bar on 62 and 63, so 18 bars in 50..69; the sum of t is
        # 1190 - 125 = 1065, so the mean is 1000 * 36 + 10 * 1065 / 18 = 36591.666...
        (GAPPED, "45.0", str(Decimal(658650) / 18), (-1.3669, -2.3669, 3.3669, 4.3669)),
        # NEW_LISTING, j = 38: bars from session 60, so 10 bars, mean t = 64.5:
        # 1000 * 39 + 645 = 39645. Too young for the 25- and 60-session windows.
        (NEW_LISTING, "48.0", "39645", (-1.3969, -2.3969, None, None)),
        # 000001.SZ, j = 0: the plain case, 1000 * 1 + 10 * 59.5 = 1595.
        ("000001.SZ", "10.0", "1595", (-1.0169, -2.0169, 3.0169, 4.0169)),
    ],
    ids=["halted", "gapped", "new-listing", "plain"],
)
def test_the_arithmetic_of_four_rows_by_hand(
    full_store: PanelStore,
    code: str,
    close: str,
    avg_amount: str,
    oriented: tuple[float | None, ...],
) -> None:
    fact = {fact.ts_code: fact for fact in read_day(full_store).facts}[code]

    assert fact.close == Decimal(close)
    assert fact.avg_amount == Decimal(avg_amount)
    assert fact.oriented_values == pytest.approx(oriented)


def test_a_halted_security_with_no_bar_still_has_a_row_carried_on_its_last_close(
    full_store: PanelStore,
) -> None:
    # Review Focus 1: without this row the kernel would sell the holding as unassessed.
    fact = {fact.ts_code: fact for fact in read_day(full_store).facts}[HALTED]

    assert fact.suspended is True
    assert fact.close == Decimal("36.5")  # not 36.75 (session 67), which never traded


def test_a_halt_through_the_close_is_suspended_and_a_morning_halt_is_not(
    full_store: PanelStore,
) -> None:
    facts = {fact.ts_code: fact for fact in read_day(full_store).facts}

    assert facts[HALT_THROUGH_CLOSE].suspended is True  # 14:00-15:00 spans the 14:57 auction
    assert facts[HALT_IN_MORNING].suspended is False  # 09:30-10:30 resumed before the close
    assert facts[GAPPED].suspended is False  # halted on 62 and 63, trading today


def test_boards_the_account_cannot_trade_still_get_their_row(full_store: PanelStore) -> None:
    # The kernel gives them `board_not_allowed`; the adapter does not drop them.
    facts = {fact.ts_code: fact for fact in read_day(full_store).facts}

    assert CONFIG.boards == ("main",)
    assert (facts[CHINEXT].board, facts[STAR].board) == ("growth", "star")


def test_the_notes_count_every_assumption_of_the_day(full_store: PanelStore) -> None:
    # Names: 14 ordinary baselines; ST by name (*ST甲), pending (ST乙 announced on the day) and
    # by band (600602, 5% with no history) one each; one delisting name (丙退); 13 ordinary codes
    # plus the eight others with no rename on file are "never renamed" (21), and 000014.SZ's only
    # rename takes effect after the day (before_first, 1). The rename of 600001.SH announced on
    # 2026-04-13 is not visible on the 10th, so it is still an ordinary baseline.
    assert read_day(full_store).notes == FactsNotes(
        name_unknown_assumed_ordinary=22,
        suspended_without_bar=1,
        no_listing_row=1,
        delisting=1,
        st=3,
        st_by_name=1,
        st_pending=1,
        st_by_band=1,
        name_unknown_no_record=21,
        name_unknown_before_first=1,
        no_bar_within_carry=0,
    )


def test_data_after_the_signal_day_changes_no_fact(
    full_store: PanelStore, trimmed_store: PanelStore
) -> None:
    # `FULL` holds three more sessions of bars, bands and halts (HALTED trades again, ST_PENDING's
    # band turns 5%), an ST rename of 600001.SH announced 2026-04-13, a listing on 2026-04-15 and
    # a factor build on 2026-04-13. None of it was knowable at 18:30 on 2026-04-10.
    assert fingerprint(read_day(full_store)) == fingerprint(read_day(trimmed_store))


def test_a_listed_security_with_no_bar_within_the_carry_bound_has_no_row_and_is_counted(
    full_store: PanelStore,
) -> None:
    # HALTED's last bar is session 66, three sessions before the signal day.
    within = read_day(full_store, max_carry_sessions=3)
    beyond = read_day(full_store, max_carry_sessions=2)

    assert HALTED in {fact.ts_code for fact in within.facts}
    assert HALTED not in {fact.ts_code for fact in beyond.facts}
    assert (beyond.universe_size, beyond.scored_size) == (39, 38)
    assert beyond.notes.no_bar_within_carry == 1
    assert beyond.notes.suspended_without_bar == 0


def test_a_coverage_exactly_at_the_threshold_is_accepted(coverage_store: PanelStore) -> None:
    # Session 68: 38 rows are scored (HALTED has no bar, NEW_LISTING lacks two values), and 18
    # more lose `reversal_1d`: 20 of 40 is exactly the default 0.5.
    day = read_day(coverage_store, 68)

    assert (day.universe_size, day.scored_size) == (40, 20)


def test_a_coverage_below_the_threshold_is_refused_with_both_counts(
    coverage_store: PanelStore,
) -> None:
    # Session 69: 19 more lose `reversal_1d`, so 19 of 40 -- one short of half.
    with pytest.raises(FactsCoverageError, match=r"19 of 40.*0\.5"):
        read_day(coverage_store, SIGNAL_INDEX)


def test_a_wholesale_outage_is_refused_rather_than_handed_to_the_kernel(
    outage_store: PanelStore,
) -> None:
    # Every security but 600001.SH is halted all day on the signal day. Each still gets its row,
    # carried on the 68th session's close, and the engine even values each one off its own last
    # sessions -- so counting rows with values alone would pass this day. A carried row is not
    # scored: 1 of 40 is far below 0.5. (A session with no bar at all cannot be stored --
    # `write_daily_panel` refuses a year missing an open session -- and the bar read refuses a
    # session it cannot see; those are the other ways an outage stops the day.)
    #
    # 39 rows, not 40: UNREGISTERED is in the universe only through a bar on the day (it has no
    # listing row), and today it has none. It could never have been held -- it is always
    # `listing_too_recent` -- so it has no holding to protect.
    with pytest.raises(FactsCoverageError, match=r"1 of 39"):
        read_day(outage_store)

    # What the guard saw, with the guard switched off: 38 carried rows, 38 rows carrying every
    # value (NEW_LISTING still lacks its two long windows), yet only 600001.SH traded.
    seen = read_day(outage_store, min_coverage=0.0)
    assert (seen.universe_size, seen.scored_size) == (39, 1)
    assert UNREGISTERED not in {fact.ts_code for fact in seen.facts}
    assert seen.notes.suspended_without_bar == 38
    assert sum(1 for fact in seen.facts if None not in fact.oriented_values) == 38


def test_a_day_with_no_factor_build_is_refused_by_the_coverage_guard(
    full_store: PanelStore,
) -> None:
    # `FULL` builds on sessions 68, 69 and 70 and not on 67: every value is `None`.
    with pytest.raises(FactsCoverageError, match=r"0 of 40"):
        read_day(full_store, 67)


@pytest.mark.parametrize(
    ("scored", "universe", "threshold", "refused"),
    [
        (20, 40, 0.5, False),  # exactly half
        (19, 40, 0.5, True),
        (3, 4, 0.75, False),
        (2, 4, 0.75, True),
        (0, 4, 0.0, False),  # a threshold of 0 accepts any non-empty day
        (0, 0, 0.0, True),  # an empty universe is refused whatever the threshold
        (0, 0, 0.5, True),
    ],
)
def test_the_coverage_rule(scored: int, universe: int, threshold: float, refused: bool) -> None:
    if refused:
        with pytest.raises(FactsCoverageError, match=rf"{scored} of {universe}"):
            require_coverage(scored_size=scored, universe_size=universe, min_coverage=threshold)
    else:
        require_coverage(scored_size=scored, universe_size=universe, min_coverage=threshold)


@pytest.mark.parametrize("threshold", [-0.1, 1.5, float("nan"), float("inf")])
def test_a_threshold_that_is_not_a_fraction_is_refused(threshold: float) -> None:
    with pytest.raises(SurvivorFactsError, match="min_coverage"):
        require_coverage(scored_size=1, universe_size=1, min_coverage=threshold)


def test_the_coverage_error_is_a_named_facts_error() -> None:
    assert issubclass(FactsCoverageError, SurvivorFactsError)


def test_a_read_before_the_session_published_is_refused(full_store: PanelStore) -> None:
    # 16:00 Asia/Shanghai on the signal day: the session publishes at 16:30.
    with pytest.raises(SurvivorFactsError, match="16:30"):
        read_day(full_store, as_of=instant(SIGNAL_INDEX) - timedelta(minutes=30))


def test_factor_ids_that_are_not_the_configured_composite_are_refused(
    full_store: PanelStore,
) -> None:
    with pytest.raises(SurvivorFactsError, match="composite_factors"):
        read_day(full_store, factor_ids=FACTORS[::-1])


def test_a_carry_bound_below_one_session_is_refused(full_store: PanelStore) -> None:
    with pytest.raises(SurvivorFactsError, match="max_carry_sessions"):
        read_day(full_store, max_carry_sessions=0)


def test_a_day_the_exchange_was_shut_is_refused(full_store: PanelStore) -> None:
    saturday = date(2026, 4, 11)
    with pytest.raises(SurvivorFactsError, match="2026-04-11"):
        read_day(full_store, session=saturday, as_of=evening(SIGNAL_INDEX) + timedelta(days=1))


def test_the_same_input_gives_the_same_bytes(full_store: PanelStore) -> None:
    assert fingerprint(read_day(full_store)) == fingerprint(read_day(full_store))


def test_processes_with_different_hash_seeds_read_the_same_bytes(full_store: PanelStore) -> None:
    here = Path(__file__).resolve()
    program = (
        "import hashlib, importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('facts_panel', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec);"
        "sys.modules['facts_panel'] = module;"
        "spec.loader.exec_module(module);"
        "print(hashlib.sha256(module.read_signal_day(sys.argv[2]).encode()).hexdigest())"
    )
    tree = here.parents[2]
    path = os.pathsep.join(
        part
        for part in (str(tree / "src"), str(tree / "tests"), os.environ.get("PYTHONPATH"))
        if part
    )
    reference = hashlib.sha256(fingerprint(read_day(full_store)).encode()).hexdigest()

    for hash_seed in ("0", "1", "424242"):
        completed = subprocess.run(
            [sys.executable, "-c", program, str(here), str(full_store.root)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": path},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == reference
