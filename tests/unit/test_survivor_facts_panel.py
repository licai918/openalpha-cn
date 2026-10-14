"""The panel half of the eligibility-facts adapter (`V2-P7-016`, plan 2 task A2).

`build_facts_day` reads one signal day off a stored panel and hands the kernel one
`SecurityFacts` row per listed security -- a halted one with no bar included -- or refuses by
name. Everything here runs against a panel generated at test time and written through the real
`panel_ingest` writers (`AGENTS.md` hard rule 6); nothing is read from a research store.

## The frame

40 securities over the 75 weekday sessions 2026-01-05 .. 2026-04-17 of `panel_fixtures`' 2026
calendar (closed only on 1-2 January). The signal day is `SESSIONS[69]`, Friday 2026-04-10, read
at 18:30 Asia/Shanghai -- after that session's 16:30 publication. Every security is built from
two numbers, its position `j` in `SECURITIES` and the session index `t`:

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
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from panel_fixtures import (
    DAILY_BASIC_EXTRA_COLUMNS,
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
from openalpha_cn.domain.stock_universe import LISTING_EVENT, STOCK_BASIC_DATASET
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

SECURITIES: Final[tuple[str, ...]] = tuple(
    sorted(
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
    )
)
LISTED_BEFORE_THE_CALENDAR: Final[date] = date(2025, 1, 2)
NEW_LISTING_INDEX: Final[int] = 60
HALTED_SESSIONS: Final[frozenset[int]] = frozenset({67, 68, 69})
GAPPED_SESSIONS: Final[frozenset[int]] = frozenset({62, 63})

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
    return SECURITIES.index(code)


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


def trades(code: str, index: int, variant: Variant) -> bool:
    if code == NEW_LISTING and index < NEW_LISTING_INDEX:
        return False
    if code == HALTED and index in HALTED_SESSIONS:
        return False
    if code == GAPPED and index in GAPPED_SESSIONS:
        return False
    return not (variant.outage and index == SIGNAL_INDEX and code != ONLY_TRADER)


def halt_rows(variant: Variant) -> dict[tuple[str, int], str | None]:
    """`(code, session) -> suspend_timing` for every `S` row; `None` is a whole-day halt."""
    rows: dict[tuple[str, int], str | None] = {}
    rows.update({(HALTED, index): None for index in HALTED_SESSIONS})
    rows.update({(GAPPED, index): None for index in GAPPED_SESSIONS})
    if variant.outage:
        rows.update({(code, SIGNAL_INDEX): None for code in SECURITIES if code != ONLY_TRADER})
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


def listing_rows(variant: Variant) -> list[tuple[str, date]]:
    rows = [
        (code, SESSIONS[NEW_LISTING_INDEX] if code == NEW_LISTING else LISTED_BEFORE_THE_CALENDAR)
        for code in SECURITIES
        if code != UNREGISTERED
    ]
    if variant.last >= 72:
        rows.append((LISTED_LATER, SESSIONS[72]))
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
        if (code != NEW_LISTING or index >= NEW_LISTING_INDEX)
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
    listings = listing_rows(variant)
    listed_at = [midnight_shanghai(day) for _, day in listings]
    registry = batch(
        STOCK_BASIC_DATASET,
        subjects=[code for code, _ in listings],
        columns=[
            PanelColumn("lifecycle_event", "string", tuple(LISTING_EVENT for _ in listings)),
            PanelColumn("lifecycle_date", "string", tuple(day.isoformat() for _, day in listings)),
            PanelColumn(
                "exchange",
                "string",
                tuple("SSE" if code.endswith(".SH") else "SZSE" for code, _ in listings),
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
    write_generated_panel(
        store,
        panel,
        datasets=(
            TRADING_CALENDAR_DATASET,
            STOCK_BASIC_DATASET,
            SUSPENSION_DATASET,
            DAILY_DATASET,
            DAILY_BASIC_DATASET,
            PRICE_LIMIT_DATASET,
        ),
    )
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
