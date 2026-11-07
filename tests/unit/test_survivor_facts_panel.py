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

`EXTENDED` is `FULL` with the securities of `EXTRA` added -- shapes the frame's own rows must not
change under, each built by the same rules, with `j` counted on after `FRAME`'s -- so that the
tests that need them leave every count above as it is.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
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

from openalpha_cn import strategy_survivor_facts
from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PRICE_DATE_COLUMN,
)
from openalpha_cn.domain.factor import FactorDefinition
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
from openalpha_cn.domain.survivor_screen import screen
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
    write_stock_universe,
)
from openalpha_cn.strategy_survivor_facts import (
    FactsCoverageError,
    FactsDay,
    FactsNotes,
    MalformedHeldCodeError,
    MissingFactorBuildError,
    StaleRegistryError,
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
DELISTED: Final[str] = "600610.SH"
"""Priced through session 64 and delisted on session 65, four sessions before the signal day --
well within the carry bound, so only "listed on the day" keeps a carried row away from it."""
NEVER_TRADED: Final[str] = "600612.SH"
"""Listed on session 50 and whole-day halted on every session since: listed, and never a bar."""
NEVER_TRADED_INDEX: Final[int] = 50
CHINEXT_302: Final[str] = "302132.SZ"
"""ChiNext under its third prefix (`300114.SZ`'s new code, T3 3.3), with ChiNext's 20% band."""

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
    DELISTED,
    NEVER_TRADED,
    CHINEXT_302,
)
"""Every security of the frame, in the order that gives each its position `j`."""
SECURITIES: Final[tuple[str, ...]] = tuple(sorted(FRAME))
NO_ROW: Final[frozenset[str]] = frozenset({DELISTED_WITH_BARS, DELISTED, NEVER_TRADED})
"""The securities of the frame that have no fact row on the signal day."""
ROWS: Final[tuple[str, ...]] = tuple(code for code in SECURITIES if code not in NO_ROW)
"""The signal day's fact rows, ascending by code."""
LISTED_BEFORE_THE_CALENDAR: Final[date] = date(2025, 1, 2)
NEW_LISTING_INDEX: Final[int] = 60
HALTED_SESSIONS: Final[frozenset[int]] = frozenset({67, 68, 69})
GAPPED_SESSIONS: Final[frozenset[int]] = frozenset({62, 63})
DELISTED_ON: Final[Mapping[str, int]] = MappingProxyType({DELISTED_WITH_BARS: 60, DELISTED: 65})
"""The session each delisted security's registry delisting row is dated (exclusive)."""

GROWTH_FIVE_PERCENT: Final[str] = "300602.SZ"
"""`EXTENDED` only: ChiNext, with a 5% band on every session. ChiNext has not published that
band since its 2020 reform; it is synthetic, there so that "the band is read on the main board
alone" has a 5% band off the main board to ignore."""
BSE_BEFORE_LISTING: Final[str] = "920001.BJ"
"""`EXTENDED` only: priced on every session with a registry listing dated session 72,
2026-04-15 -- T3 3.2's `.BJ` shape, a bar before the registered listing date. Its listing row is
published at that day's midnight, so a read before it has never heard of the code."""
BSE_LISTING_INDEX: Final[int] = 72
UNRECOGNISED_PREFIX: Final[str] = "303001.SZ"
"""`EXTENDED` only: a prefix no board has yet, shaped as a future ChiNext one (ChiNext's 20% band,
listed before the calendar, trading every session). `security_board` files a prefix it does not
know under the main board."""
MISPAIRED_EXCHANGE: Final[str] = "600001.SZ"
"""`EXTENDED` only: a Shanghai main-board prefix on the Shenzhen exchange -- no such code exists,
T8 pairs `600` with SH alone -- and otherwise an ordinary security (the 10% band, listed before
the calendar, trading every session, never renamed). `security_board` calls it main board, and a
prefix without its exchange would pass it as one."""
EXTRA: Final[tuple[str, ...]] = (
    GROWTH_FIVE_PERCENT,
    BSE_BEFORE_LISTING,
    UNRECOGNISED_PREFIX,
    MISPAIRED_EXCHANGE,
)
"""The securities only `EXTENDED` holds, in the order they were added: `j` = 44, 45, ..."""

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
    return (*FRAME, *EXTRA).index(code)


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
    unbuilt: frozenset[tuple[int, int]] = frozenset()
    """`(factor, session)` pairs of `builds` that are not stored: one factor's build missing."""
    extra: tuple[str, ...] = ()
    """Securities of `EXTRA` stored besides `FRAME`'s."""
    registry: bool = True
    """Whether the panel holds a security registry at all."""
    registry_fetched: datetime | None = None
    """When the registry was fetched, if not at its newest row's own instant (`None`). The batch
    then holds only the lifecycle rows dated by that instant -- `TushareProvider` drops a later
    one -- and every lifecycle partition's coverage carries the instant as its `as_of`, as the
    fetch of step 2 of the daily command stamps it."""


FULL: Final[Variant] = Variant()
TRIMMED: Final[Variant] = Variant(last=SIGNAL_INDEX, builds=(68, SIGNAL_INDEX))
EXTENDED: Final[Variant] = Variant(extra=EXTRA)


def stored_securities(variant: Variant) -> tuple[str, ...]:
    """Every security the variant's panel holds, ascending by code."""
    return tuple(sorted((*SECURITIES, *variant.extra)))


def scheduled(code: str, index: int) -> bool:
    """Whether `code` has a bar on session `index` when nothing market-wide goes wrong."""
    if code == NEW_LISTING and index < NEW_LISTING_INDEX:
        return False
    if code == HALTED and index in HALTED_SESSIONS:
        return False
    if code == DELISTED and index >= DELISTED_ON[DELISTED]:
        return False
    if code == NEVER_TRADED:
        return False
    return not (code == GAPPED and index in GAPPED_SESSIONS)


def trades(code: str, index: int, variant: Variant) -> bool:
    if not scheduled(code, index):
        return False
    return not (variant.outage and index == SIGNAL_INDEX and code != ONLY_TRADER)


def banded_on(code: str, index: int) -> bool:
    """Whether a band is published for `code` on session `index`: from its first session on,
    halted sessions included, until it is delisted. One that never traded has no previous close
    to publish a band on."""
    if code == DELISTED:
        return index < DELISTED_ON[DELISTED]
    if code == NEVER_TRADED:
        return False
    return code != NEW_LISTING or index >= NEW_LISTING_INDEX


def halt_rows(variant: Variant) -> dict[tuple[str, int], str | None]:
    """`(code, session) -> suspend_timing` for every `S` row; `None` is a whole-day halt."""
    rows: dict[tuple[str, int], str | None] = {}
    rows.update({(HALTED, index): None for index in HALTED_SESSIONS})
    rows.update({(GAPPED, index): None for index in GAPPED_SESSIONS})
    rows.update({(NEVER_TRADED, index): None for index in range(NEVER_TRADED_INDEX, SESSION_COUNT)})
    if variant.outage:
        rows.update(
            {
                (code, SIGNAL_INDEX): None
                for code in stored_securities(variant)
                if code != ONLY_TRADER and scheduled(code, SIGNAL_INDEX)
            }
        )
    else:
        rows[(HALT_THROUGH_CLOSE, SIGNAL_INDEX)] = "14:00-15:00"
        rows[(HALT_IN_MORNING, SIGNAL_INDEX)] = "09:30-10:30"
    return {key: value for key, value in rows.items() if key[1] <= variant.last}


def band_ratio(code: str, index: int) -> float:
    if code in (ST_BY_BAND, GROWTH_FIVE_PERCENT) or (code == ST_BY_NAME and index >= 20):
        return 0.05
    if code == ST_PENDING and index >= 70:
        return 0.05
    if code in (CHINEXT, CHINEXT_302, STAR, UNRECOGNISED_PREFIX):
        return 0.20
    if code == BSE_BEFORE_LISTING:
        return 0.30
    return 0.10


def previous_close(code: str, index: int, variant: Variant) -> float:
    """The close of the newest earlier session `code` traded, else one step before its first."""
    for earlier in range(index - 1, -1, -1):
        if trades(code, earlier, variant):
            return close_of(code, earlier)
    return close_of(code, index) - (0.25 if code == HALTED else 0.0)


def listing_day(code: str) -> date:
    if code == NEVER_TRADED:
        return SESSIONS[NEVER_TRADED_INDEX]
    if code == BSE_BEFORE_LISTING:
        return SESSIONS[BSE_LISTING_INDEX]
    return SESSIONS[NEW_LISTING_INDEX] if code == NEW_LISTING else LISTED_BEFORE_THE_CALENDAR


def lifecycle_rows(variant: Variant) -> list[tuple[str, str, date]]:
    """`(code, lifecycle event, day)` for every registry row the variant stores."""
    rows = [
        (code, LISTING_EVENT, listing_day(code))
        for code in stored_securities(variant)
        if code != UNREGISTERED and listing_day(code) <= SESSIONS[variant.last]
    ]
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
    securities = stored_securities(variant)
    priced = [
        (index, code)
        for index in range(len(sessions))
        for code in securities
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
        for code in securities
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
    fetched = variant.registry_fetched
    lifecycle = [
        row
        for row in lifecycle_rows(variant)
        if fetched is None or midnight_shanghai(row[2]) <= fetched
    ]
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
        fetched_at=_fetched(listed_at) if fetched is None else fetched,
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
        securities=securities,
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

    securities = stored_securities(variant)
    return compute_factor(
        store,
        definition,
        as_of=at,
        subjects=securities,
        universe=frozenset(securities),
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
    registry = (STOCK_BASIC_DATASET,) if variant.registry else ()
    write_generated_panel(
        store,
        panel,
        datasets=(
            TRADING_CALENDAR_DATASET,
            *registry,
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
        if (factor, index) not in variant.unbuilt
    ]
    if not raw:
        return store
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
        {68: frozenset(ORDINARY[:17]), SIGNAL_INDEX: frozenset(ORDINARY[:18])}
    ),
)
"""`reversal_1d` answers `None` for 17 more main-board securities on session 68 and 18 more on
69."""


@pytest.fixture(scope="module")
def coverage_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("coverage"), COVERAGE)


ONE_UNBUILT: Final[Variant] = Variant(
    last=SIGNAL_INDEX, builds=(68, SIGNAL_INDEX), unbuilt=frozenset({(3, SIGNAL_INDEX)})
)
"""`amihud_60` is built on session 68 and not on the signal day; the other three on both."""


@pytest.fixture(scope="module")
def one_unbuilt_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("one-unbuilt"), ONE_UNBUILT)


OUTAGE: Final[Variant] = Variant(last=SIGNAL_INDEX, outage=True, builds=(SIGNAL_INDEX,))


@pytest.fixture(scope="module")
def outage_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("outage"), OUTAGE)


@pytest.fixture(scope="module")
def extended_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("extended"), EXTENDED)


NOTHING_ADMITTED: Final[Variant] = Variant(
    last=SIGNAL_INDEX,
    builds=(68, SIGNAL_INDEX),
    undefined=MappingProxyType({SIGNAL_INDEX: frozenset(SECURITIES)}),
)
"""`reversal_1d` is built at the signal day's instant and answers `None` for every security."""


@pytest.fixture(scope="module")
def nothing_admitted_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("nothing-admitted"), NOTHING_ADMITTED)


NEW_YEAR_INDEX: Final[int] = 0
"""Monday 2026-01-05, the year's first session (the fixture calendar is shut on 1-2 January)."""
LAST_EVENING_OF_2025: Final[datetime] = published_time(date(2025, 12, 31)) + timedelta(hours=2)
"""18:30 Asia/Shanghai on Wednesday 2025-12-31, the evening of the year before's last session."""

NEW_YEAR: Final[Variant] = Variant(builds=(), registry_fetched=instant(NEW_YEAR_INDEX))
"""The registry as step 2 of 5 January writes it, fetched at the earliest instant that step can
fetch at, the session's own 16:30. Every lifecycle row dated by then is a 2025-01-02 listing --
nothing has listed or died in 2026 yet -- so the store holds lifecycle year 2025 and no 2026.
No factor build: the engine cannot build one on a year's first session of a calendar that starts
that year (`factor_stand_in`)."""
NEW_YEAR_WITH_LATER_EVENTS: Final[Variant] = Variant(builds=())
"""The same panel with `FULL`'s registry: lifecycle year 2026 is stored, and every event in it is
dated after 5 January (sessions 50, 60, 65 and 72), so none of them concerns any code that day."""
STALE_REGISTRY: Final[Variant] = Variant(builds=(), registry_fetched=LAST_EVENING_OF_2025)
"""The registry as 31 December's step 2 left it, and nothing has fetched it since: its rows are
`NEW_YEAR`'s, its fetch five days older."""
NO_REGISTRY: Final[Variant] = Variant(builds=(), registry=False)
"""A panel that has never held a security registry."""


@pytest.fixture(scope="module")
def new_year_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("new-year"), NEW_YEAR)


@pytest.fixture(scope="module")
def new_year_with_later_events_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("new-year-later-events"), NEW_YEAR_WITH_LATER_EVENTS)


@pytest.fixture(scope="module")
def stale_registry_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("stale-registry"), STALE_REGISTRY)


@pytest.fixture(scope="module")
def no_registry_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("no-registry"), NO_REGISTRY)


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
    # build is acceptable is the caller's question (`build_facts_day` refuses it by name, with the
    # factor and the instant). A partition that was never written still refuses.
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
    # amihud_60 (60 own sessions within 80): every security but NEW_LISTING (10 sessions) and
    # NEVER_TRADED (none).
    assert sorted(admitted) == sorted(set(SECURITIES) - {NEW_LISTING, NEVER_TRADED})
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
            "boards": list(day.boards),
            "universe_size": day.universe_size,
            "scored_size": day.scored_size,
            "held": list(day.held),
            "held_status": [status.model_dump(mode="json") for status in day.held_status],
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

    - board: `600*`/`000*` main, `300*`/`302*` growth, `688*` star;
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
        board="growth" if code in (CHINEXT, CHINEXT_302) else "star" if code == STAR else "main",
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
    # 41 rows, 38 on the main board (CHINEXT, CHINEXT_302 and STAR are not); of those 36 are
    # scored -- HALTED has no bar today and NEW_LISTING lacks two values.
    assert len(day.facts) == 41
    assert day.boards == CONFIG.boards
    assert (day.universe_size, day.scored_size) == (38, 36)


def test_the_coverage_counts_only_rows_on_the_boards_the_book_trades(
    full_store: PanelStore,
) -> None:
    # Review Minor 4: a trading holding with a missing value IS sold, so a guard over every board
    # would let a main-board outage hide behind ChiNext and STAR rows. With all three boards
    # allowed, CHINEXT, CHINEXT_302 and STAR (all trading, all with every value) count again.
    wider = SurvivorBookConfig(
        seed=7,
        composite_factors=FACTORS,
        min_listing_sessions=60,
        boards=("main", "growth", "star"),
    )
    day = read_day(full_store, config=wider)

    assert len(day.facts) == 41
    assert (day.universe_size, day.scored_size) == (41, 39)


def test_a_302_code_is_chinext_so_a_main_board_book_refuses_its_board(
    full_store: PanelStore,
) -> None:
    # Review Important 2. `302132.SZ` is ChiNext (`domain/price_limits.py`'s measurement, T3 7
    # item 8). Filed under the main board, a main-board-only book would have no reason to refuse
    # it and could buy it. As ChiNext it is refused for its board alone (old enough, trading,
    # every value). Its band is ChiNext's 20%, so `special_treatment is False` would hold on
    # either board and says nothing about the band rule; that the 5% band is read on the main
    # board alone is the next test's, on a ChiNext security whose band is 5%.
    day = read_day(full_store)
    fact = {fact.ts_code: fact for fact in day.facts}[CHINEXT_302]
    verdict = {verdict.ts_code: verdict for verdict in screen(day.facts, CONFIG).verdicts}[
        CHINEXT_302
    ]

    assert CONFIG.boards == ("main",)
    assert fact.board == "growth"
    assert fact.special_treatment is False
    assert verdict.reasons == ("board_not_allowed",)


def test_a_five_percent_band_off_the_main_board_does_not_make_a_security_st(
    extended_store: PanelStore,
) -> None:
    # Re-review Minor 4. GROWTH_FIVE_PERCENT is ChiNext with a 5% band on every session and no
    # rename on file; ST_BY_BAND is main board with the same band and no rename either. Only the
    # main-board one is ST: off the main board the band is no independent witness (T3 2.2), so
    # the adapter does not offer it to `name_status` at all.
    day = read_day(extended_store)
    facts = {fact.ts_code: fact for fact in day.facts}

    assert facts[GROWTH_FIVE_PERCENT].board == "growth"
    assert facts[GROWTH_FIVE_PERCENT].special_treatment is False
    assert facts[ST_BY_BAND].special_treatment is True
    assert day.notes.st_by_band == 1  # ST_BY_BAND alone


def test_a_code_filed_under_the_main_board_by_default_gets_no_row_and_is_named(
    extended_store: PanelStore,
) -> None:
    # Brief item 8. `security_board` files `303001.SZ` under the main board because it does not
    # know the prefix -- the shape of the `302*` defect, again. A main-board-only book would
    # have every reason to buy it (old enough, trading, every value, a 20% band that is not ST).
    # Only the eight prefixes T8 measured count as main board here, each on its own exchange: it
    # has no row, is not in the coverage guard's 38, and is counted and named. With no row it is
    # never screened, so it can never be bought. `600001.SZ` (A2-fix2 concern 3) has a
    # Shanghai main-board prefix on the Shenzhen exchange, where no such code exists, and an
    # ordinary security's every other fact: the same treatment.
    day = read_day(extended_store)
    screened = {verdict.ts_code for verdict in screen(day.facts, CONFIG).verdicts}

    for code in (UNRECOGNISED_PREFIX, MISPAIRED_EXCHANGE):
        assert code not in {fact.ts_code for fact in day.facts}
        assert code not in screened
    assert (day.universe_size, day.scored_size) == (38, 36)
    assert day.notes.unrecognised_board == 2
    assert day.notes.unrecognised_board_codes == (UNRECOGNISED_PREFIX, MISPAIRED_EXCHANGE)


def test_a_held_code_on_an_unrecognised_board_is_told_so(extended_store: PanelStore) -> None:
    day = read_day(
        extended_store, held=(UNRECOGNISED_PREFIX, MISPAIRED_EXCHANGE, GROWTH_FIVE_PERCENT)
    )

    assert {status.ts_code: status.status for status in day.held_status} == {
        UNRECOGNISED_PREFIX: "board_unrecognised",
        MISPAIRED_EXCHANGE: "board_unrecognised",
        GROWTH_FIVE_PERCENT: "row",
    }
    assert {UNRECOGNISED_PREFIX, MISPAIRED_EXCHANGE}.isdisjoint(fact.ts_code for fact in day.facts)


def test_the_extended_panels_extra_securities_change_no_row_of_the_frame(
    full_store: PanelStore, extended_store: PanelStore
) -> None:
    # What the `EXTENDED` tests say about the frame's own securities holds for `FULL`'s rows:
    # read evening or later, every row of a `FRAME` security is the same in both panels.
    for as_of in (evening(SIGNAL_INDEX), LATER):
        full = read_day(full_store, as_of=as_of).facts
        extended = read_day(extended_store, as_of=as_of).facts
        assert tuple(fact for fact in extended if fact.ts_code in FRAME) == full


def test_a_registry_code_not_listed_on_the_day_has_no_row_even_with_a_bar(
    full_store: PanelStore,
) -> None:
    # DELISTED_WITH_BARS left the registry on session 60 and is priced on the signal day all the
    # same (a `.BJ` code priced before its listing date, T3 3.2, is the same case the other way
    # round). The registry knows it and does not list it that day, so its bar does not bring it
    # in: only a code the registry has no listing row for at all is added through its bar.
    # Re-review Minor 3: the exclusion is counted, so it is not silent.
    day = read_day(full_store)

    assert DELISTED_WITH_BARS not in {fact.ts_code for fact in day.facts}
    assert day.notes.no_listing_row == 1  # UNREGISTERED alone
    assert day.notes.not_listed_with_bar == 1  # DELISTED_WITH_BARS alone


def test_a_code_priced_before_its_listing_is_counted_once_the_registry_knows_it(
    extended_store: PanelStore,
) -> None:
    # BSE_BEFORE_LISTING has a bar on the signal day and a listing dated 2026-04-15. At 18:30 on
    # the 10th that listing is not yet published: the registry has never heard of the code, so
    # its bar brings it in aged 0, `listing_too_recent`, like UNREGISTERED. Read after the 15th
    # the registry knows it and lists it from the 15th, not on the 10th: no row, and counted.
    # The count is not a row: every other row of the day is unchanged. So the two reads of this
    # day differ, by that one row and those two counts.
    evening_day = read_day(extended_store)
    later_day = read_day(extended_store, as_of=LATER)
    fact = {fact.ts_code: fact for fact in evening_day.facts}[BSE_BEFORE_LISTING]

    assert (fact.board, fact.listed_sessions) == ("bse", 0)
    assert (evening_day.notes.no_listing_row, evening_day.notes.not_listed_with_bar) == (2, 1)
    assert BSE_BEFORE_LISTING not in {fact.ts_code for fact in later_day.facts}
    assert (later_day.notes.no_listing_row, later_day.notes.not_listed_with_bar) == (1, 2)
    assert later_day.facts == tuple(
        fact for fact in evening_day.facts if fact.ts_code != BSE_BEFORE_LISTING
    )


def test_a_code_the_registry_has_never_listed_gets_a_row_aged_zero_and_is_counted(
    full_store: PanelStore,
) -> None:
    # UNREGISTERED has a bar on the day and no listing row at all (T3 3.3's migrated-code
    # shape): it is in the universe, aged 0 so the screen says `listing_too_recent`.
    day = read_day(full_store)
    fact = {fact.ts_code: fact for fact in day.facts}[UNREGISTERED]

    assert fact.listed_sessions == 0
    assert day.notes.no_listing_row == 1


def test_a_security_delisted_before_the_day_has_no_row_although_its_last_bar_is_in_reach(
    full_store: PanelStore,
) -> None:
    # DELISTED traded through session 64 and left the registry on 65; its last bar is five
    # sessions back, inside the carry bound. Only "listed on the signal day" keeps it out --
    # read every registry code instead and it would get a carried `suspended=True` row, and a
    # delisted holding would be frozen instead of sold. Nothing counts it: it is not listed.
    day = read_day(full_store)

    assert DELISTED not in {fact.ts_code for fact in day.facts}
    assert day.notes.suspended_without_bar == 1  # HALTED alone
    assert day.notes.no_bar_within_carry == 1  # NEVER_TRADED alone


def test_a_read_long_after_the_day_gives_the_evening_reads_bytes(full_store: PanelStore) -> None:
    # A later task re-reads the signal day the next morning, after it. At `LATER` everything
    # `FULL` stores is visible -- three more sessions, the 2026-04-13 rename and build, the
    # 2026-04-15 listing -- and none of it may reach the day's facts.
    assert fingerprint(read_day(full_store, as_of=LATER)) == fingerprint(read_day(full_store))


# --- the registry on a year's first session (A3 review Important 2) ------------------------------

NEW_YEAR_CONFIG: Final[SurvivorBookConfig] = SurvivorBookConfig(
    seed=7, composite_factors=FACTORS, min_listing_sessions=1
)
"""A listing minimum the year's first session can age: the calendar starts 2026-01-01, so a 2025
listing counts one session on 5 January (`listing_age_sessions` refuses a truncated count below
the minimum)."""


@pytest.fixture
def factor_stand_in(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, datetime | None]]:
    """The factor tier, the one read the year's first session replaces; every other read is real.

    The engine refuses to build a factor on a calendar's first session -- the visible panel has
    one session and the shortest window here needs two (`FactorEngineError`) -- and this panel's
    calendar starts in 2026. The stand-in answers one row that carries no value, about a code no
    panel here holds: a build exists at the instant, so the day is not refused for a missing
    build, and no security is scored. It records what it was asked: each factor, at which
    instant."""
    asked: list[tuple[str, datetime | None]] = []

    def stand_in(
        _store: PanelStore,
        definition: FactorDefinition,
        _transform: FactorTransformSpec,
        *,
        years: Sequence[int],
        as_of: datetime,
        at: datetime | None = None,
    ) -> tuple[object, ...]:
        asked.append((definition.qualified_key, at))
        return (SimpleNamespace(subject="000000.SZ", value=None, coverage="not_in_universe"),)

    monkeypatch.setattr(strategy_survivor_facts, "load_processed_factor_observations", stand_in)
    return asked


def read_new_year(store: PanelStore, **overrides: object) -> FactsDay:
    """5 January at its evening, under `factor_stand_in`: nothing is scored, so the coverage floor
    is 0."""
    arguments: dict[str, object] = {"config": NEW_YEAR_CONFIG, "min_coverage": 0.0}
    arguments.update(overrides)
    return read_day(store, NEW_YEAR_INDEX, **arguments)


def test_a_year_with_no_registry_event_yet_is_read_through_the_newest_stored_lifecycle_year(
    new_year_store: PanelStore,
    new_year_with_later_events_store: PanelStore,
    factor_stand_in: list[tuple[str, datetime | None]],
) -> None:
    # `stock_basic` is filed by lifecycle year. Since `V2-P7-025` a `panel build` whose fetch
    # carries no row for its observation year records that year as a zero-row partition, so a
    # 025-built store holds 2026 and reads the session's own year; this fixture is the shape a
    # store has without that record (one built before it). Read through the newest stored year,
    # the day is the one a store holding 2026 gives when nothing in it is about 5 January -- byte
    # for byte, the held statuses included.
    held = (DELISTED_WITH_BARS, NEW_LISTING, UNREGISTERED)
    day = read_new_year(new_year_store, held=held)

    assert new_year_store.registered_years(STOCK_BASIC_DATASET) == (2025,)
    assert new_year_with_later_events_store.registered_years(STOCK_BASIC_DATASET) == (2025, 2026)
    assert fingerprint(day) == fingerprint(
        read_new_year(new_year_with_later_events_store, held=held)
    )
    # And they are 5 January's rows: every security with a 2025 listing -- DELISTED_WITH_BARS
    # and DELISTED too, whose delistings are 2026's (sessions 60 and 65) -- each one session
    # old, and UNREGISTERED through its bar, aged 0. NEW_LISTING and NEVER_TRADED list in 2026
    # and have no bar yet: no row, and a held NEW_LISTING is a code the registry has not heard of.
    assert [fact.ts_code for fact in day.facts] == [
        code for code in SECURITIES if code not in (NEW_LISTING, NEVER_TRADED)
    ]
    assert {fact.ts_code: fact.listed_sessions for fact in day.facts} == {
        fact.ts_code: 0 if fact.ts_code == UNREGISTERED else 1 for fact in day.facts
    }
    assert (day.notes.no_listing_row, day.notes.not_listed_with_bar) == (1, 0)
    assert {status.ts_code: status.status for status in day.held_status} == {
        DELISTED_WITH_BARS: "row",
        NEW_LISTING: "not_in_registry",
        UNREGISTERED: "not_in_registry",
    }
    # Each read asked the factor tier for every factor at the session's own 16:30 build.
    assert factor_stand_in == [(factor, instant(NEW_YEAR_INDEX)) for factor in FACTORS] * 2


def test_a_registry_not_fetched_since_before_the_session_published_is_refused_by_name(
    stale_registry_store: PanelStore, factor_stand_in: list[tuple[str, datetime | None]]
) -> None:
    # The registry 31 December's step 2 left, read on 5 January. Its rows are `NEW_YEAR`'s, so
    # nothing in the partitions says it is stale; only its fetch instant does. Step 2 of
    # 5 January fetches the whole registry at or after that session's 16:30 and records that
    # instant on every lifecycle year the fetch holds, so a newest stored year fetched before it
    # is a registry no fetch has written since -- one that would miss a listing or a delisting
    # of the days between, and with no 2026 partition to say so.
    with pytest.raises(StaleRegistryError) as refused:
        read_new_year(stale_registry_store)

    message = str(refused.value)
    assert STOCK_BASIC_DATASET in message
    assert "2025" in message  # the newest lifecycle year the store holds
    assert "2025-12-31T18:30:00+08:00" in message  # when it was fetched
    assert "2026-01-05T16:30:00+08:00" in message  # when the session published
    assert factor_stand_in == []  # refused before any factor is read


def test_the_fetch_step_2_makes_for_the_session_is_what_lets_that_registry_be_read(
    tmp_path: Path,
    new_year_store: PanelStore,
    factor_stand_in: list[tuple[str, datetime | None]],
) -> None:
    store = write_panel(tmp_path, STALE_REGISTRY)
    with pytest.raises(StaleRegistryError):
        read_new_year(store)
    before = store.read_coverage(STOCK_BASIC_DATASET, 2025)

    # Step 2 of 5 January: the whole registry fetched again, at that session's 16:30. Nothing
    # has listed or died since, so the 2025 partition keeps its rows and only its coverage is
    # recorded again -- with the new fetch's instant, which is what the read is judged on.
    write_stock_universe(store, build_panel(NEW_YEAR).batch(STOCK_BASIC_DATASET))
    after = store.read_coverage(STOCK_BASIC_DATASET, 2025)

    assert store.registered_years(STOCK_BASIC_DATASET) == (2025,)
    assert before is not None
    assert after is not None
    assert after.partition_content_hash == before.partition_content_hash
    assert (before.as_of, after.as_of) == (LAST_EVENING_OF_2025, instant(NEW_YEAR_INDEX))
    assert fingerprint(read_new_year(store)) == fingerprint(read_new_year(new_year_store))


def test_a_panel_that_never_held_a_registry_is_refused_by_name(
    no_registry_store: PanelStore, factor_stand_in: list[tuple[str, datetime | None]]
) -> None:
    # Nothing to fall back to: with no lifecycle year at or before the session's, the store has
    # never held a registry, and a universe read from it would list no security at all.
    assert no_registry_store.registered_years(STOCK_BASIC_DATASET) == ()
    with pytest.raises(SurvivorFactsError, match="no stock_basic partition at or before 2026"):
        read_new_year(no_registry_store)
    assert factor_stand_in == []


def test_the_stale_registry_error_is_a_named_facts_error() -> None:
    assert issubclass(StaleRegistryError, SurvivorFactsError)


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
    assert (facts[CHINEXT].board, facts[CHINEXT_302].board, facts[STAR].board) == (
        "growth",
        "growth",
        "star",
    )


def test_the_notes_count_every_assumption_of_the_day(full_store: PanelStore) -> None:
    # Names: 14 ordinary baselines; ST by name (*ST甲), pending (ST乙 announced on the day) and
    # by band (600602, 5% with no history) one each; one delisting name (丙退); 13 ordinary codes
    # plus the nine others with no rename on file (CHINEXT_302 among them) are "never renamed"
    # (22), and 000014.SZ's only rename takes effect after the day (before_first, 1). The rename
    # of 600001.SH announced on 2026-04-13 is not visible on the 10th, so it is still an ordinary
    # baseline. NEVER_TRADED is listed and has never had a bar: no row, and the one
    # `no_bar_within_carry`. DELISTED_WITH_BARS is known, not listed, and has a bar: the one
    # `not_listed_with_bar` (DELISTED, with no bar today, is not counted anywhere).
    assert read_day(full_store).notes == FactsNotes(
        name_unknown_assumed_ordinary=23,
        suspended_without_bar=1,
        no_listing_row=1,
        delisting=1,
        st=3,
        st_by_name=1,
        st_pending=1,
        st_by_band=1,
        name_unknown_no_record=22,
        name_unknown_before_first=1,
        no_bar_within_carry=1,
        not_listed_with_bar=1,
        unrecognised_board=0,
        unrecognised_board_codes=(),
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
    assert (beyond.universe_size, beyond.scored_size) == (37, 36)  # main board only
    assert beyond.notes.no_bar_within_carry == 2  # HALTED and NEVER_TRADED
    assert beyond.notes.suspended_without_bar == 0


def test_a_held_name_halted_beyond_the_carry_bound_keeps_its_row_on_its_last_close(
    full_store: PanelStore,
) -> None:
    # Review Important 1. Bounded, a holding halted for longer than `max_carry_sessions` would
    # lose its row, be sold as unassessed (`EXIT_ELIGIBILITY_LOST`) on a sell that cannot fill,
    # and have its slot refilled. Held, both look-backs run as far as the panel goes: HALTED's
    # last bar is three sessions back and the bound is two, yet it is carried on that close,
    # with the mean of its last 20 bars (47..66, also past the bound).
    not_held = read_day(full_store, max_carry_sessions=2)
    held = read_day(full_store, max_carry_sessions=2, held=(HALTED,))

    assert HALTED not in {fact.ts_code for fact in not_held.facts}
    assert not_held.notes.no_bar_within_carry == 2
    fact = {fact.ts_code: fact for fact in held.facts}[HALTED]
    assert fact.suspended is True
    assert fact.close == Decimal("36.5")
    assert fact.avg_amount == Decimal("35565")
    assert held.notes.no_bar_within_carry == 1  # NEVER_TRADED alone
    assert held.notes.suspended_without_bar == 1


def test_a_held_code_that_never_had_a_bar_has_no_row_and_is_counted(
    full_store: PanelStore,
) -> None:
    # NEVER_TRADED is listed (session 50) and has never traded: even unbounded there is no close
    # to carry, so it gets no row and is counted. The caller must refuse such a day by name --
    # the kernel would otherwise sell the holding as unassessed.
    day = read_day(full_store, held=(NEVER_TRADED,))

    assert NEVER_TRADED not in {fact.ts_code for fact in day.facts}
    assert day.notes.no_bar_within_carry == 1


def test_holding_a_code_the_registry_does_not_list_on_the_day_gives_it_no_row(
    full_store: PanelStore,
) -> None:
    # Re-review Minor 1. Holding a code unbounds its look-back; it does not bring a code the
    # registry does not list on the day into the universe. DELISTED's last bar (session 64) is
    # in reach and DELISTED_WITH_BARS has one today, yet held, neither gets a row -- a row would
    # freeze a delisted holding instead of selling it -- and the day is the unheld day's.
    unheld = read_day(full_store)
    day = read_day(full_store, held=(DELISTED, DELISTED_WITH_BARS))

    assert {DELISTED, DELISTED_WITH_BARS}.isdisjoint(fact.ts_code for fact in day.facts)
    assert day.facts == unheld.facts
    assert day.notes == unheld.notes


def test_holding_one_code_leaves_every_other_codes_carry_bound_in_place(
    full_store: PanelStore,
) -> None:
    # Re-review Minor 2. NEVER_TRADED is held, HALTED is not. With a bound of 2 sessions
    # HALTED's last bar (three sessions back) is out of reach whether or not something else is
    # held: holding a code unbounds that code's look-back and no other's.
    unheld = read_day(full_store, max_carry_sessions=2)
    day = read_day(full_store, max_carry_sessions=2, held=(NEVER_TRADED,))

    assert HALTED not in {fact.ts_code for fact in day.facts}
    assert day.notes.no_bar_within_carry == 2  # HALTED and NEVER_TRADED
    assert day.facts == unheld.facts
    assert day.notes == unheld.notes


def test_a_halted_names_amount_window_stays_within_the_carry_bound(
    full_store: PanelStore,
) -> None:
    # Review Minor 2. With a bound of 10, HALTED's bars within reach are sessions 59..66 (three
    # to ten sessions back): eight bars, mean t = 62.5, so 1000 * 35 + 10 * 62.5 = 35625 -- the
    # mean of the bars within the bound, not a walk on until 20 are found. Held, the window is
    # its last 20 bars again (47..66: 35565).
    bounded = {fact.ts_code: fact for fact in read_day(full_store, max_carry_sessions=10).facts}
    held = {
        fact.ts_code: fact
        for fact in read_day(full_store, max_carry_sessions=10, held=(HALTED,)).facts
    }

    assert bounded[HALTED].avg_amount == mean([amount_of(HALTED, t) for t in range(59, 67)])
    assert bounded[HALTED].avg_amount == Decimal("35625")
    assert held[HALTED].avg_amount == Decimal("35565")


def test_held_codes_given_as_one_string_are_refused(full_store: PanelStore) -> None:
    # `"600605.SH"` is a collection of ten one-character codes, none of which is held.
    with pytest.raises(SurvivorFactsError, match="held"):
        read_day(full_store, held=HALTED)


NOT_IN_REGISTRY: Final[str] = "600999.SH"
"""A well-spelled code no panel here has heard of: no listing row, no bar."""


def test_every_held_code_is_told_what_became_of_it(full_store: PanelStore) -> None:
    # Re-review G and brief item 6: the caller decides per held code, so the day says, for each
    # one, why it has a row or why not. Passed unsorted and with a repeat; given back sorted,
    # once each. UNREGISTERED keeps its row (a bar today, aged 0) and is `not_in_registry` all
    # the same: the caller refuses a day holding a code the registry has no row for. DELISTED
    # (no bar today) and DELISTED_WITH_BARS (a bar today) are both `delisted`: the registry
    # dates their delisting on or before the day.
    unheld = read_day(full_store)
    day = read_day(
        full_store,
        held=[
            NOT_IN_REGISTRY,
            HALTED,
            NEVER_TRADED,
            DELISTED,
            ORDINARY[0],
            DELISTED_WITH_BARS,
            UNREGISTERED,
            HALTED,
        ],
    )

    assert day.held == tuple(
        sorted(
            {
                NOT_IN_REGISTRY,
                HALTED,
                NEVER_TRADED,
                DELISTED,
                ORDINARY[0],
                DELISTED_WITH_BARS,
                UNREGISTERED,
            }
        )
    )
    assert {status.ts_code: status.status for status in day.held_status} == {
        ORDINARY[0]: "row",
        HALTED: "row",
        NEVER_TRADED: "no_bar_in_reach",
        DELISTED: "delisted",
        DELISTED_WITH_BARS: "delisted",
        UNREGISTERED: "not_in_registry",
        NOT_IN_REGISTRY: "not_in_registry",
    }
    assert [status.ts_code for status in day.held_status] == list(day.held)
    assert {fact.ts_code: fact for fact in day.facts}[UNREGISTERED].listed_sessions == 0
    assert (day.facts, day.notes) == (unheld.facts, unheld.notes)
    assert (unheld.held, unheld.held_status) == ((), ())


def test_a_held_code_the_registry_lists_only_after_the_day_is_not_yet_listed_not_delisted(
    full_store: PanelStore,
) -> None:
    # The paper book's write-off (irreversible) is for a delisted name: not listed on the day and
    # no bar to freeze it on. LISTED_LATER has no bar anywhere and is not listed on the signal
    # day either -- but because the registry dates its listing 2026-04-15, after the day, and it
    # has not delisted. A read of the 10th made after the 15th knows the row; it must not say
    # `delisted`. (The evening of the 10th has not heard of it: `not_in_registry`.)
    evening_day = read_day(full_store, held=(LISTED_LATER,))
    later_day = read_day(full_store, as_of=LATER, held=(LISTED_LATER,))

    assert [(s.ts_code, s.status) for s in evening_day.held_status] == [
        (LISTED_LATER, "not_in_registry")
    ]
    assert [(s.ts_code, s.status) for s in later_day.held_status] == [
        (LISTED_LATER, "not_yet_listed")
    ]
    assert LISTED_LATER not in {fact.ts_code for fact in later_day.facts}
    assert later_day.facts == read_day(full_store, as_of=LATER).facts


def test_a_held_code_priced_before_its_listing_is_not_yet_listed_and_has_no_row(
    extended_store: PanelStore,
) -> None:
    # BSE_BEFORE_LISTING has a bar on the signal day and a registry listing dated 2026-04-15
    # (T3 3.2's `.BJ` shape). Held: at 18:30 on the 10th the registry has never heard of it, so it
    # is `not_in_registry` and keeps its row aged 0; read after the 15th the registry knows it and
    # does not list it yet -- `not_yet_listed`, no row although a bar is there, counted once in
    # `not_listed_with_bar` as it is unheld.
    evening_day = read_day(extended_store, held=(BSE_BEFORE_LISTING,))
    later_day = read_day(extended_store, as_of=LATER, held=(BSE_BEFORE_LISTING,))
    unheld = read_day(extended_store, as_of=LATER)

    assert {s.ts_code: s.status for s in evening_day.held_status} == {
        BSE_BEFORE_LISTING: "not_in_registry"
    }
    assert BSE_BEFORE_LISTING in {fact.ts_code for fact in evening_day.facts}
    assert {s.ts_code: s.status for s in later_day.held_status} == {
        BSE_BEFORE_LISTING: "not_yet_listed"
    }
    assert BSE_BEFORE_LISTING not in {fact.ts_code for fact in later_day.facts}
    assert (later_day.facts, later_day.notes) == (unheld.facts, unheld.notes)
    assert later_day.notes.not_listed_with_bar == 2  # DELISTED_WITH_BARS and BSE_BEFORE_LISTING


def test_the_registry_says_which_way_a_held_code_is_not_listed(extended_store: PanelStore) -> None:
    # Four held codes the registry knows and does not list on the 10th, read after the 15th: two
    # whose delisting is dated on or before the day (DELISTED_WITH_BARS on session 60, DELISTED on
    # 65) and two whose listing is dated after it (LISTED_LATER and BSE_BEFORE_LISTING, session
    # 72). Two have a bar on the day and two have none, in each pair, so the status is not a
    # function of the bar.
    day = read_day(
        extended_store,
        as_of=LATER,
        held=(LISTED_LATER, BSE_BEFORE_LISTING, DELISTED, DELISTED_WITH_BARS),
    )

    assert {s.ts_code: s.status for s in day.held_status} == {
        DELISTED_WITH_BARS: "delisted",
        DELISTED: "delisted",
        LISTED_LATER: "not_yet_listed",
        BSE_BEFORE_LISTING: "not_yet_listed",
    }
    assert {DELISTED, DELISTED_WITH_BARS, LISTED_LATER, BSE_BEFORE_LISTING}.isdisjoint(
        fact.ts_code for fact in day.facts
    )


@pytest.fixture
def listed_later_ends_before_it_begins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry the loader returns, with LISTED_LATER's row made to contradict itself: a
    delisting dated session 65 against a listing dated session 72. `build_stock_universe` refuses
    such a row, and `StockUniverse`'s constructor does not, so it is built past the validator."""
    load = strategy_survivor_facts.load_stock_universe

    def contradictory(*args: object, **kwargs: object) -> object:
        universe = load(*args, **kwargs)  # type: ignore[arg-type]
        return dataclasses.replace(
            universe,
            securities=tuple(
                dataclasses.replace(entry, delisted_on=SESSIONS[65])
                if entry.ts_code == LISTED_LATER
                else entry
                for entry in universe.securities
            ),
        )

    monkeypatch.setattr(strategy_survivor_facts, "load_stock_universe", contradictory)


def test_a_held_code_whose_registry_row_ends_before_it_begins_refuses_the_day_by_name(
    full_store: PanelStore, listed_later_ends_before_it_begins: None
) -> None:
    # LISTED_LATER is not listed on the day and has no bar, and the row has a delisting date on
    # or before the day -- so is it `delisted`? Its listing date is after the day too, so it has
    # not begun: the write-off must not fire on it, and `not_yet_listed` would hide that it also
    # ended. No status is true of it; the day is refused, naming the code.
    with pytest.raises(SurvivorFactsError, match=LISTED_LATER):
        read_day(full_store, as_of=LATER, held=(LISTED_LATER,))


def test_a_registry_row_that_contradicts_itself_costs_a_day_that_does_not_hold_it_nothing(
    full_store: PanelStore, listed_later_ends_before_it_begins: None
) -> None:
    # The refusal is about the holding. The same row of a code nobody holds is not listed on the
    # day, has no bar and gets no row, whatever its dates say: the day is the one it always was.
    assert fingerprint(read_day(full_store, as_of=LATER)) == fingerprint(read_day(full_store))


def test_the_held_codes_are_part_of_the_days_bytes(full_store: PanelStore) -> None:
    # Holding HALTED changes no fact here (its last bar is inside the bound either way), and
    # the day still differs: `held` is an input the caller records with the day and passes
    # unchanged to any re-read.
    held = read_day(full_store, held=(HALTED,))
    unheld = read_day(full_store)

    assert held.facts == unheld.facts
    assert fingerprint(held) != fingerprint(unheld)
    assert fingerprint(read_day(full_store, held=(HALTED,), as_of=LATER)) == fingerprint(held)


@pytest.mark.parametrize(
    "code",
    [
        "600605.sh",  # lower case
        " 600605.SH",  # padded
        "600605.SH ",
        "600605.SH\n",  # a trailing newline (`$` would let it through)
        "600605",  # the bare six digits
        "60605.SH",
        "600605.SS",  # another vendor's suffix
        "\uff16\uff10\uff10\uff16\uff10\uff15.SH",  # full-width digits (`\d` would admit them)
        "",
    ],
    ids=[
        "lower-case",
        "leading-space",
        "trailing-space",
        "trailing-newline",
        "bare-digits",
        "five-digits",
        "other-suffix",
        "full-width",
        "empty",
    ],
)
def test_a_held_code_spelled_wrongly_is_refused_by_name(full_store: PanelStore, code: str) -> None:
    # Re-review Minor 8. A misspelled holding matches no security, so it would be read as not
    # held: its look-back bounded, and nothing in the day would say that it is a holding.
    with pytest.raises(MalformedHeldCodeError, match=re.escape(repr(code))):
        read_day(full_store, held=(HALTED, code))


def test_a_held_code_that_is_not_a_string_is_refused_by_name(full_store: PanelStore) -> None:
    with pytest.raises(MalformedHeldCodeError, match="600605"):
        read_day(full_store, held=(600605,))


def test_the_malformed_held_code_error_is_a_named_facts_error() -> None:
    assert issubclass(MalformedHeldCodeError, SurvivorFactsError)


def test_a_coverage_exactly_at_the_threshold_is_accepted(coverage_store: PanelStore) -> None:
    # Session 68, main board only: 38 rows, 36 scored (HALTED has no bar, NEW_LISTING lacks two
    # values), and 17 more lose `reversal_1d`: 19 of 38 is exactly the default 0.5.
    day = read_day(coverage_store, 68)

    assert (day.universe_size, day.scored_size) == (38, 19)


def test_a_coverage_below_the_threshold_is_refused_with_both_counts(
    coverage_store: PanelStore,
) -> None:
    # Session 69: 18 more lose `reversal_1d`, so 18 of 38 -- one short of half.
    with pytest.raises(FactsCoverageError, match=r"18 of 38.*0\.5"):
        read_day(coverage_store, SIGNAL_INDEX)


def test_a_wholesale_outage_is_refused_rather_than_handed_to_the_kernel(
    outage_store: PanelStore,
) -> None:
    # Every security but 600001.SH is halted all day on the signal day. Each still gets its row,
    # carried on the 68th session's close, and the engine even values each one off its own last
    # sessions -- so counting rows with values alone would pass this day. A carried row is not
    # scored: 1 of 37 main-board rows is far below 0.5. (A session with no bar at all cannot be
    # stored -- `write_daily_panel` refuses a year missing an open session -- and the bar read
    # refuses a session it cannot see; those are the other ways an outage stops the day.)
    #
    # 40 rows, not 41: UNREGISTERED is in the universe only through a bar on the day (it has no
    # listing row), and today it has none. It could never have been held -- it is always
    # `listing_too_recent` -- so it has no holding to protect. 37 of the 40 are main board.
    with pytest.raises(FactsCoverageError, match=r"1 of 37"):
        read_day(outage_store)

    # What the guard saw, with the guard switched off: 39 carried rows, 39 rows carrying every
    # value (NEW_LISTING still lacks its two long windows), yet only 600001.SH traded.
    seen = read_day(outage_store, min_coverage=0.0)
    assert len(seen.facts) == 40
    assert (seen.universe_size, seen.scored_size) == (37, 1)
    assert UNREGISTERED not in {fact.ts_code for fact in seen.facts}
    assert seen.notes.suspended_without_bar == 39
    assert sum(1 for fact in seen.facts if None not in fact.oriented_values) == 39


def test_a_day_with_no_factor_build_is_refused_by_name(full_store: PanelStore) -> None:
    # Review Minor 7. `FULL` builds on sessions 68, 69 and 70 and not on 67, so the narrow read
    # at 67's instant answers nothing. That used to surface only as "0 of 38 ... have a bar and
    # every factor value"; it is refused by name -- the factor and the instant -- before the
    # coverage guard, whatever its threshold.
    missing = re.escape(
        f"reversal_1d/v1 has no probe_identity/v1 build at {instant(67).isoformat()}"
    )
    with pytest.raises(MissingFactorBuildError, match=missing):
        read_day(full_store, 67)
    with pytest.raises(MissingFactorBuildError, match=missing):
        read_day(full_store, 67, min_coverage=0.0)


def test_one_factor_with_no_build_on_the_day_is_refused_by_its_own_name(
    one_unbuilt_store: PanelStore,
) -> None:
    # The other three factors were built at the signal day's instant; amihud_60 was not.
    missing = re.escape(
        f"amihud_60/v1 has no probe_identity/v1 build at {instant(SIGNAL_INDEX).isoformat()}"
    )
    with pytest.raises(MissingFactorBuildError, match=missing):
        read_day(one_unbuilt_store)


def test_a_build_that_admits_no_value_is_a_coverage_refusal_not_a_missing_build(
    nothing_admitted_store: PanelStore,
) -> None:
    # Re-review Minor 5. `reversal_1d` was built at the signal day's instant and answered
    # `None` for every security: the build exists and admits no value. That is the coverage
    # guard's "0 of 38" -- the missing-build refusal is for an instant with no build at all.
    with pytest.raises(FactsCoverageError, match=r"\b0 of 38\b"):
        read_day(nothing_admitted_store)


def test_the_missing_build_error_is_a_named_facts_error() -> None:
    assert issubclass(MissingFactorBuildError, SurvivorFactsError)


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
