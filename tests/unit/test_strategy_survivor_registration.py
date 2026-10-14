"""The survivor book's forward record and its input provenance (`V2-P7-008`, task D2).

`strategy_survivor_registration` turns one survivor day -- its facts and its screen -- into the
pre-registered forward record `survivor_book_composite/v1` (horizon `20d`), fingerprints what the
day read without hashing a single row, and scores a filed record again from the store for the
late-record check. P6's machinery (`register_prediction`, `session_record`, the validators) is
reused unchanged; these tests hold the survivor half to it.

## The frame

`tests/unit/test_survivor_facts_panel.py`'s frame, imported rather than copied: its `TRIMMED`
panel is the store the evening of the signal day (Friday 2026-04-10, session 69) leaves, read at
18:30 Asia/Shanghai, with the processed builds of sessions 68 and 69. Every later store is that
store **copied and then written to** through the real writers, the way the panel grows:

- **grown**: `FULL`'s later sessions 70-74 (bars, bands, halts), its later rename and listing,
  and a factor build at session 70 -- content hashes the day read move, nothing the day read
  changes;
- **restated build**: `reversal_1d`'s 16:30 build of the signal day superseded in place by one
  in which `600010.SH` has no value; **value-identical rebuild**: the same build superseded by
  one with the same values under another code commit;
- **late ST rename**: an ST rename of `600001.SH` announced on the signal day, stored the next
  morning under the provider's own clock (midnight of the announcement) -- the shape the morning
  re-check exists for; **early rename**: an ST rename announced three sessions before it, stored
  after filing the same way;
- **late clock**: an ordinary rename dated on the signal day whose availability is the next
  morning's fetch, so the filing instant cannot read the partition at all;
- **probe A**: the traded amount of the eligible `600608.SH` cut in place across its 20-session
  amount window, every row count unchanged; **probe B**: one intraday halt dated 2026-01-06,
  months before the window; **probe A and B** together;
- **older daily year**: a 2025 bar partition stored before the evening (no read of the day
  reaches it unless a held halted security's look-back does);
- **second transform**: a processed build of every factor under another transform, stamped at
  the same 16:30.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Final
from zoneinfo import ZoneInfo

import pydantic
import pytest
from panel_fixtures import EXCHANGE, write_generated_panel
from panel_fixtures import _batch as batch
from panel_fixtures import _close_time as close_time
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from panel_fixtures import _published_time as published_time
from test_survivor_facts_panel import (
    CONFIG,
    FACTORS,
    FULL,
    HALTED,
    IDENTITY,
    SESSIONS,
    SIGNAL,
    SIGNAL_INDEX,
    TRIMMED,
    build_panel,
    evening,
    factor_build,
    instant,
    name_records,
    read_day,
    write_panel,
)

from openalpha_cn import strategy_survivor_registration as registration_module
from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
    validate_feature_ids,
)
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PRICE_DATE_COLUMN,
    SESSION_CLOSE_TIME,
)
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
    NameRecord,
)
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, PanelColumn
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import ScreenResult, SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, CalendarHorizonError
from openalpha_cn.panel.catalog import PanelStorageError, PartitionNotYetKnowableError
from openalpha_cn.panel.store import PanelCatalogBusyError, PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    apply_factor_transform,
    factor_transform_manifest_dataset,
    load_factor_manifests,
    load_factor_transform_manifests,
    processed_factor_dataset,
    write_factor_panels,
    write_processed_factor_panels,
)
from openalpha_cn.panel_ingest import (
    load_trading_calendar,
    session_publication_instant,
    split_panel_batch_by_year,
    write_name_history,
    write_panel_batch,
    write_suspensions,
)
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import (
    COMPOSITE_MODEL_NAME,
    RegisteredConfiguration,
    StrategyRegistrationError,
    batch_digest,
    registered_at,
    session_record,
)
from openalpha_cn.strategy_survivor_facts import (
    AMOUNT_WINDOW_SESSIONS,
    FactsDay,
    SurvivorFactsError,
    build_facts_day,
)
from openalpha_cn.strategy_survivor_registration import (
    SURVIVOR_FAMILY,
    SURVIVOR_HORIZON,
    SURVIVOR_PROVENANCE_DIRECTORY,
    SURVIVOR_PROVENANCE_SCHEMA,
    SURVIVOR_RECOMPUTE_REFUSALS,
    SurvivorCheckArgumentError,
    SurvivorInputProvenance,
    SurvivorRegistrationError,
    read_survivor_provenance,
    recompute_survivor_batch,
    survivor_batch,
    survivor_declaration,
    survivor_input_provenance,
    survivor_provenance_changes,
    survivor_record_is_bound,
    write_survivor_provenance,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

BOOK: Final[SurvivorBookConfig] = CONFIG
"""The facts frame's own book: seed 7, its four factors, a 60-session listing minimum."""
REGISTERED: Final[RegisteredConfiguration] = RegisteredConfiguration(
    config_id="rcf_5b1d0c9e8a7f6e5d4c3b2a19",
    registration_sha256="9f" * 32,
    code_commit="abcdef1234567",
    seed=7,
)
EVENING: Final[datetime] = evening(SIGNAL_INDEX)
"""18:30 Asia/Shanghai on the signal day: the run's pinned `as_of`, which its facts are read at."""
PREDICTED_AT: Final[datetime] = EVENING + timedelta(minutes=4)
FILED: Final[datetime] = EVENING + timedelta(minutes=5)
"""The prediction store's custody stamp, and the provenance's `recorded_at`."""
NEXT_MORNING: Final[datetime] = EVENING + timedelta(hours=14)
"""08:30 on Saturday 2026-04-11: the morning after, before Monday's call auction."""
MIN_COVERAGE: Final[float] = 0.5
MAX_CARRY_SESSIONS: Final[int] = 250
SHORT_CARRY: Final[int] = 2
"""`HALTED` has been halted for three sessions: under this bound it has a row only when held."""
SCOPE_HELD: Final[tuple[str, ...]] = (HALTED, "600010.SH", "600610.SH")
"""A halted holding, a trading eligible one and a delisted one: everything `held` can change."""
LATE_ST: Final[str] = "600001.SH"
RESTATED: Final[str] = "600010.SH"
THINNED: Final[str] = "600608.SH"
"""Probe A's eligible name, its amount cut in place across its window."""
THICKENED: Final[str] = "000009.SZ"
"""The name the liquidity cut lets in when `THINNED` drops below it."""
AMOUNT_WINDOW_FIRST: Final[date] = SESSIONS[SIGNAL_INDEX - AMOUNT_WINDOW_SESSIONS + 1]
"""Monday 2026-03-16, session 50: the first of the 20 sessions whose bars are the eligible names'
amounts."""
DIFFERS: Final = "recompute_differs"
FAILED: Final = "recompute_failed"
SCOPES: Final[tuple[str, ...]] = (DIFFERS, FAILED)


def _script(name: str, directory: Path) -> ModuleType:
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


def daily_selection() -> ModuleType:
    """The daily command, imported only by the tests that call its step-7 helpers."""
    return _script("daily_selection", ROOT / "scripts")


# --- the stores ----------------------------------------------------------------------------------


def _copy(source: PanelStore, target: Path) -> PanelStore:
    shutil.copytree(source.root, target)
    return PanelStore(target)


def _rows(source: ColumnarPanelBatch) -> list[dict[str, object]]:
    """Each row of `source` as `{column: value}`, with its subject and its clocks."""
    clocks = source.timeline
    rows: list[dict[str, object]] = []
    for index, subject in enumerate(source.subjects):
        row: dict[str, object] = {
            "subject": subject,
            "event_time": clocks.event_time[index],
            "available_time": clocks.available_time[index],
            "revision_time": clocks.revision_time[index],
        }
        row.update({column.name: column.values[index] for column in source.columns})
        rows.append(row)
    return rows


def _rebatched(source: ColumnarPanelBatch, rows: Sequence[dict[str, object]]) -> ColumnarPanelBatch:
    """`source`'s columns over `rows`, fetched when `source` was."""
    return batch(
        source.dataset,
        subjects=[str(row["subject"]) for row in rows],
        columns=[
            PanelColumn(column.name, column.kind, tuple(row[column.name] for row in rows))
            for column in source.columns
        ],
        event_time=[row["event_time"] for row in rows],  # type: ignore[misc]
        available_time=[row["available_time"] for row in rows],  # type: ignore[misc]
        revision_time=[row["revision_time"] for row in rows],  # type: ignore[misc]
        fetched_at=source.fetched_at,
    )


def _grow(store: PanelStore) -> None:
    """Append `FULL`'s later sessions, rename, listing and session-70 build to `store`."""
    panel = build_panel(FULL)
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
    for _year, part in split_panel_batch_by_year(panel.batch(NAMECHANGE_DATASET)):
        write_name_history(store, part)
    raw = [factor_build(store, panel, factor, 70, FULL) for factor in range(len(FACTORS))]
    write_factor_panels(store, raw)
    write_processed_factor_panels(
        store,
        [
            apply_factor_transform(
                source, IDENTITY, code_commit="abcdef1", built_at=source.built_at
            )
            for source in raw
        ],
    )


def _signal_builds(store: PanelStore, factor: int) -> tuple[list[str], list[str]]:
    """The raw and processed manifest ids of `factor`'s build at the signal day's 16:30."""
    at = instant(SIGNAL_INDEX)
    definition = FACTOR_DEFINITIONS.get(FACTORS[factor])
    raw_ids = [
        manifest.manifest_id
        for manifest in load_factor_manifests(store, definition, years=(2026,), as_of=at)
        if manifest.as_of == at
    ]
    processed_ids = [
        manifest.transform_manifest_id
        for manifest in load_factor_transform_manifests(store, definition, years=(2026,), as_of=at)
        if manifest.as_of == at and manifest.transform_id == IDENTITY.transform_id
    ]
    return raw_ids, processed_ids


def _restate_build(store: PanelStore) -> None:
    """Supersede `reversal_1d`'s 16:30 build of the signal day by one without `RESTATED`."""
    raw_ids, processed_ids = _signal_builds(store, 0)
    variant = dataclasses.replace(
        TRIMMED, undefined=MappingProxyType({SIGNAL_INDEX: frozenset({RESTATED})})
    )
    raw = [factor_build(store, build_panel(variant), 0, SIGNAL_INDEX, variant)]
    write_factor_panels(store, raw, supersedes=raw_ids)
    write_processed_factor_panels(
        store,
        [
            apply_factor_transform(
                source, IDENTITY, code_commit="abcdef1", built_at=source.built_at
            )
            for source in raw
        ],
        supersedes=processed_ids,
    )


def _rebuild_identically(store: PanelStore) -> None:
    """Supersede `reversal_1d`'s 16:30 processed build by the same values under another commit."""
    _raw_ids, processed_ids = _signal_builds(store, 0)
    source = factor_build(store, build_panel(TRIMMED), 0, SIGNAL_INDEX, TRIMMED)
    write_processed_factor_panels(
        store,
        [apply_factor_transform(source, IDENTITY, code_commit="1234567", built_at=source.built_at)],
        supersedes=processed_ids,
    )


SECOND_TRANSFORM: Final[FactorTransformSpec] = FactorTransformSpec.model_validate(
    {**IDENTITY.model_dump(exclude_computed_fields=True), "key": "probe_identity_two"}
)


def _build_a_second_transform(store: PanelStore) -> None:
    panel = build_panel(TRIMMED)
    raw = [factor_build(store, panel, factor, SIGNAL_INDEX, TRIMMED) for factor in range(4)]
    write_processed_factor_panels(
        store,
        [
            apply_factor_transform(
                source, SECOND_TRANSFORM, code_commit="abcdef1", built_at=source.built_at
            )
            for source in raw
        ],
    )


def _rewrite_names(
    store: PanelStore, extra: NameRecord, *, available: datetime | None = None
) -> None:
    """Write the 2026 rename year again with `extra` added, fetched the next morning.

    `available` is `extra`'s availability; `None` is the provider's own clock, midnight of its
    announcement."""
    records = sorted(
        [
            *(
                record
                for group in name_records(TRIMMED).values()
                for record in group
                if record.announced_on.year == 2026
            ),
            extra,
        ],
        key=lambda record: (record.announced_on, record.ts_code, record.name),
    )
    announced = [midnight_shanghai(record.announced_on) for record in records]
    clocks = [
        available if record is extra and available is not None else moment
        for record, moment in zip(records, announced, strict=True)
    ]
    write_name_history(
        store,
        batch(
            NAMECHANGE_DATASET,
            subjects=[record.ts_code for record in records],
            columns=[
                PanelColumn(NAME_COLUMN, "string", tuple(record.name for record in records)),
                PanelColumn(
                    NAME_EFFECTIVE_COLUMN,
                    "string",
                    tuple(record.effective_from.isoformat() for record in records),
                ),
                PanelColumn(
                    NAME_ANNOUNCEMENT_COLUMN,
                    "string",
                    tuple(record.announced_on.isoformat() for record in records),
                ),
                PanelColumn(
                    NAME_REASON_COLUMN, "string", tuple(record.change_reason for record in records)
                ),
            ],
            event_time=announced,
            available_time=clocks,
            fetched_at=NEXT_MORNING,
        ),
    )


def _thin_the_amount(store: PanelStore) -> None:
    """Probe A: `THINNED`'s amount set to 1 on every session of its window; no row added."""
    sessions = {day.isoformat(): index for index, day in enumerate(SESSIONS)}
    bars = build_panel(TRIMMED).batch(DAILY_DATASET)
    rows = _rows(bars)
    for row in rows:
        if row["subject"] == THINNED and sessions[str(row[PRICE_DATE_COLUMN])] >= (
            SIGNAL_INDEX - AMOUNT_WINDOW_SESSIONS + 1
        ):
            row["amount"] = 1.0
    write_panel_batch(store, _rebatched(bars, rows), year=2026)


def _halt_in_january(store: PanelStore) -> None:
    """Probe B: one intraday halt of `RESTATED` on 2026-01-06, months before any window."""
    halts = build_panel(TRIMMED).batch(SUSPENSION_DATASET)
    day = SESSIONS[1]
    rows = [
        *_rows(halts),
        {
            "subject": RESTATED,
            "event_time": close_time(day),
            "available_time": published_time(day),
            "revision_time": published_time(day),
            PRICE_DATE_COLUMN: day.isoformat(),
            "suspend_type": "S",
            "suspend_timing": "10:00-10:30",
        },
    ]
    rows.sort(key=lambda row: (row["event_time"], str(row["subject"])))  # type: ignore[arg-type,return-value]
    write_suspensions(store, [_rebatched(halts, rows)])


def _bars_of_2025(sessions: int) -> ColumnarPanelBatch:
    """A 2025 bar partition: two securities on the last `sessions` weekdays of December."""
    days = [date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31)][-sessions:]
    priced = [(day, code) for day in days for code in ("600001.SH", "600002.SH")]
    available = [published_time(day) for day, _code in priced]
    return batch(
        DAILY_DATASET,
        subjects=[code for _day, code in priced],
        columns=[
            PanelColumn(PRICE_DATE_COLUMN, "string", tuple(day.isoformat() for day, _ in priced)),
            *(
                PanelColumn(name, "float", tuple(10.0 for _ in priced))
                for name in ("open", "high", "low", "close", "pre_close")
            ),
            PanelColumn("pct_chg", "float", tuple(0.0 for _ in priced)),
            PanelColumn("vol", "float", tuple(100.0 for _ in priced)),
            PanelColumn("amount", "float", tuple(1000.0 for _ in priced)),
        ],
        event_time=[close_time(day) for day, _code in priced],
        available_time=available,
        fetched_at=max(available),
    )


def _store_fixture(name: str, *changes: Callable[[PanelStore], None]) -> Callable[..., PanelStore]:
    @pytest.fixture(scope="module", name=name)
    def fixture(frame_root: Path, evening_store: PanelStore) -> PanelStore:
        store = _copy(evening_store, frame_root / name)
        for change in changes:
            change(store)
        return store

    return fixture


@pytest.fixture(scope="module")
def frame_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("survivor-registration")


@pytest.fixture(scope="module")
def evening_store(frame_root: Path) -> PanelStore:
    return write_panel(frame_root / "evening", TRIMMED)


@pytest.fixture(scope="module")
def older_year_store(frame_root: Path, evening_store: PanelStore) -> PanelStore:
    """The evening with a 2025 bar partition stored before it."""
    store = _copy(evening_store, frame_root / "older-year")
    write_panel_batch(store, _bars_of_2025(2), year=2025)
    return store


grown_store = _store_fixture("grown_store", _grow)
restated_store = _store_fixture("restated_store", _restate_build)
identical_store = _store_fixture("identical_store", _rebuild_identically)
second_transform_store = _store_fixture("second_transform_store", _build_a_second_transform)
probe_a_store = _store_fixture("probe_a_store", _thin_the_amount)
probe_b_store = _store_fixture("probe_b_store", _halt_in_january)
probe_ab_store = _store_fixture("probe_ab_store", _thin_the_amount, _halt_in_january)
late_st_store = _store_fixture(
    "late_st_store",
    lambda store: _rewrite_names(
        store,
        NameRecord(
            ts_code=LATE_ST,
            name="ST丁",
            effective_from=date(2026, 4, 13),
            announced_on=SIGNAL,
            change_reason="其他",
        ),
    ),
)
early_rename_store = _store_fixture(
    "early_rename_store",
    lambda store: _rewrite_names(
        store,
        NameRecord(
            ts_code=LATE_ST,
            name="ST丁",
            effective_from=date(2026, 4, 9),
            announced_on=SESSIONS[SIGNAL_INDEX - 3],
            change_reason="其他",
        ),
    ),
)
late_clock_store = _store_fixture(
    "late_clock_store",
    lambda store: _rewrite_names(
        store,
        NameRecord(
            ts_code=RESTATED,
            name="普通新",
            effective_from=date(2026, 4, 20),
            announced_on=SIGNAL,
            change_reason="其他",
        ),
        available=NEXT_MORNING,
    ),
)


# --- one evening ---------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Evening:
    facts: FactsDay
    screen: ScreenResult
    batch: PredictionBatch


def book_with(**changes: object) -> SurvivorBookConfig:
    """`BOOK` with `changes`, read back through the model so its address is recomputed."""
    return SurvivorBookConfig.model_validate(
        {**BOOK.model_dump(exclude_computed_fields=True), **changes}
    )


def the_evening(
    store: PanelStore,
    *,
    held: Sequence[str] = (),
    book: SurvivorBookConfig = BOOK,
    max_carry_sessions: int = MAX_CARRY_SESSIONS,
) -> Evening:
    facts = read_day(store, held=held, config=book, max_carry_sessions=max_carry_sessions)
    result = screen(facts.facts, book)
    return Evening(
        facts=facts,
        screen=result,
        batch=survivor_batch(
            facts=facts, screen=result, book=book, registered=REGISTERED, predicted_at=PREDICTED_AT
        ),
    )


def filed(store: PanelStore, batch_: PredictionBatch, directory: Path) -> PredictionRecord:
    """`batch_` put into a prediction store whose custody clock reads `FILED`, on `store`'s
    calendar."""
    calendar = load_trading_calendar(store, exchange=EXCHANGE, years=(2026,), as_of=EVENING)
    return (
        FilePredictionStore(directory, clock=lambda: FILED)
        .put(batch=batch_, calendar=calendar, zone=SHANGHAI)
        .record
    )


def recompute(
    store: PanelStore,
    record: PredictionRecord,
    *,
    held: Sequence[str] = (),
    max_carry_sessions: int = MAX_CARRY_SESSIONS,
    transform: FactorTransformSpec = IDENTITY,
    provenance: SurvivorInputProvenance | None = None,
) -> PredictionBatch:
    return recompute_survivor_batch(
        store,
        record,
        book=BOOK,
        transform=transform,
        exchange=EXCHANGE,
        min_coverage=MIN_COVERAGE,
        max_carry_sessions=max_carry_sessions,
        held=held,
        registered=REGISTERED,
        provenance=provenance,
    )


def scored(batch_: PredictionBatch) -> tuple[object, ...]:
    """What the late-record check compares: instant, artifact and every score."""
    return (batch_.as_of, batch_.artifact, batch_.predictions)


def provenance_of(store: PanelStore, day: Evening) -> SurvivorInputProvenance:
    return survivor_input_provenance(
        store,
        facts=day.facts,
        batch=day.batch,
        registered=REGISTERED,
        transform=IDENTITY,
        exchange=EXCHANGE,
        recorded_at=FILED,
    )


def changes(store: PanelStore, provenance: SurvivorInputProvenance, scope: str) -> tuple[str, ...]:
    return survivor_provenance_changes(
        store,
        provenance,
        transform=IDENTITY,
        scope=scope,  # type: ignore[arg-type]
    )


def builds_at_the_signal_instant(
    store: PanelStore, transform: FactorTransformSpec = IDENTITY
) -> dict[str, tuple[str, str]]:
    at = instant(SIGNAL_INDEX)
    found: dict[str, tuple[str, str]] = {}
    for factor in FACTORS:
        for manifest in load_factor_transform_manifests(
            store, FACTOR_DEFINITIONS.get(factor), years=(2026,), as_of=at
        ):
            if manifest.as_of == at and manifest.transform_id == transform.transform_id:
                found[factor] = (
                    manifest.transform_manifest_id,
                    manifest.processed_observation_digest,
                )
    return found


def batch_bytes(store_root: str) -> str:
    """The subprocess half of the hash-seed pin: the evening's batch off an existing store."""
    facts = read_day(PanelStore(Path(store_root)))
    return survivor_batch(
        facts=facts,
        screen=screen(facts.facts, BOOK),
        book=BOOK,
        registered=REGISTERED,
        predicted_at=PREDICTED_AT,
    ).model_dump_json()


# --- the declaration -----------------------------------------------------------------------------


def test_the_declaration_is_the_survivor_composite_under_the_registration() -> None:
    declaration = survivor_declaration(BOOK, REGISTERED)

    assert SURVIVOR_FAMILY == "survivor_book_composite/v1"
    assert SURVIVOR_HORIZON == "20d"
    assert declaration == AlphaModelDeclaration(
        name=COMPOSITE_MODEL_NAME,
        family="survivor_book_composite/v1",
        horizon="20d",
        feature_version=REGISTERED.config_id,
        seed=BOOK.seed,
        code_commit=REGISTERED.code_commit,
        hyperparameters=(
            ("registration_sha256", REGISTERED.registration_sha256),
            ("survivor_config_id", BOOK.config_id),
        ),
    )
    assert parse_horizon(declaration.horizon).text == "20d"


def test_a_registration_whose_seed_is_not_the_books_is_refused() -> None:
    other = dataclasses.replace(REGISTERED, seed=BOOK.seed + 1)

    with pytest.raises(SurvivorRegistrationError, match=r"seed 8.*seed 7"):
        survivor_declaration(BOOK, other)
    assert issubclass(SurvivorRegistrationError, StrategyRegistrationError)


def test_a_record_is_bound_only_under_the_registered_declaration(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    declaration = day.batch.artifact.declaration
    p6_shaped = record.model_copy(
        update={
            "batch": day.batch.model_copy(
                update={
                    "artifact": day.batch.artifact.model_copy(
                        update={
                            "declaration": declaration.model_copy(
                                update={"family": "strategy_static"}
                            )
                        }
                    )
                }
            )
        }
    )

    assert survivor_record_is_bound(record, BOOK, REGISTERED)
    assert not survivor_record_is_bound(p6_shaped, BOOK, REGISTERED)
    assert not survivor_record_is_bound(
        record, BOOK, dataclasses.replace(REGISTERED, config_id="rcf_another")
    )
    assert not survivor_record_is_bound(record, book_with(capital=BOOK.capital * 2), REGISTERED)


# --- the batch -----------------------------------------------------------------------------------


def test_the_batch_scores_every_eligible_name_with_its_composite_and_nothing_else(
    evening_store: PanelStore,
) -> None:
    day = the_evening(evening_store)
    eligible = [verdict for verdict in day.screen.verdicts if verdict.eligible]
    survivors = {name.ts_code for name in day.screen.survivors}

    assert len(eligible) == 21
    assert day.batch.predictions == tuple(
        Prediction(ts_code=verdict.ts_code, score=verdict.composite) for verdict in eligible
    )
    assert day.batch.abstained == ()
    # The scope is the eligible set, not the survivors: the loser tail is registered too.
    losers = {verdict.ts_code for verdict in eligible} - survivors
    assert len(losers) == 4
    assert losers <= set(day.batch.subjects)
    # Nothing with a reason is registered: ST, halted, illiquid, too young, off the board.
    refused = {verdict.ts_code for verdict in day.screen.verdicts if not verdict.eligible}
    assert len(refused) == 20
    assert not refused & set(day.batch.subjects)


def test_the_artifact_is_measured_off_the_day(evening_store: PanelStore) -> None:
    day = the_evening(evening_store)
    signal_instant = session_publication_instant(SIGNAL)

    assert day.batch.as_of == signal_instant
    assert day.batch.predicted_at == PREDICTED_AT
    assert day.batch.artifact == AlphaModelArtifact(
        declaration=survivor_declaration(BOOK, REGISTERED),
        feature_ids=(
            "amihud_60/v1@processed",
            "momentum_20_sessions/v1@processed",
            "reversal_1d/v1@processed",
            "reversal_5_sessions/v1@processed",
        ),
        training_cutoff=signal_instant,
        training_example_count=4 * 21,
        parameters=(
            ("amihud_60/v1@processed", 0.25),
            ("momentum_20_sessions/v1@processed", 0.25),
            ("reversal_1d/v1@processed", 0.25),
            ("reversal_5_sessions/v1@processed", 0.25),
        ),
    )


def test_the_batch_takes_no_equity_and_is_the_same_whatever_is_held(
    evening_store: PanelStore,
) -> None:
    assert set(inspect.signature(survivor_batch).parameters) == {
        "facts",
        "screen",
        "book",
        "registered",
        "predicted_at",
    }
    # Under a two-session carry bound `HALTED` (halted three sessions) has no row unless it is
    # held: held, it is carried on its last close -- a row only `held` puts there, which must
    # never enter the scope.
    unheld = the_evening(evening_store, max_carry_sessions=SHORT_CARRY)
    held = the_evening(evening_store, held=SCOPE_HELD, max_carry_sessions=SHORT_CARRY)
    carried = {fact.ts_code: fact for fact in held.facts.facts}

    assert HALTED not in {fact.ts_code for fact in unheld.facts.facts}
    assert carried[HALTED].suspended is True
    assert held.facts.held == tuple(sorted(SCOPE_HELD))
    assert HALTED not in held.batch.subjects
    assert held.batch.model_dump_json() == unheld.batch.model_dump_json()

    # A book worth twice as much is another configuration with the same scores.
    rich = the_evening(evening_store, book=book_with(capital=BOOK.capital * 2))
    assert rich.batch.predictions == unheld.batch.predictions
    assert rich.batch.artifact.training_example_count == (
        unheld.batch.artifact.training_example_count
    )
    assert rich.batch.artifact.declaration != unheld.batch.artifact.declaration


def test_a_screen_that_is_not_the_books_screen_of_the_facts_is_refused(
    evening_store: PanelStore,
) -> None:
    day = the_evening(evening_store)
    other = screen(day.facts.facts, book_with(loser_decile_count=5))
    shrunk = ScreenResult(verdicts=day.screen.verdicts[1:], survivors=())

    for wrong in (other, shrunk):
        with pytest.raises(SurvivorRegistrationError, match="screen"):
            survivor_batch(
                facts=day.facts,
                screen=wrong,
                book=BOOK,
                registered=REGISTERED,
                predicted_at=PREDICTED_AT,
            )


def test_facts_read_for_other_boards_are_refused(evening_store: PanelStore) -> None:
    wider = book_with(boards=("main", "growth"))
    facts = read_day(evening_store, config=wider)

    with pytest.raises(SurvivorRegistrationError, match="boards"):
        survivor_batch(
            facts=facts,
            screen=screen(facts.facts, BOOK),
            book=BOOK,
            registered=REGISTERED,
            predicted_at=PREDICTED_AT,
        )


def test_a_batch_predicted_before_its_session_published_is_refused(
    evening_store: PanelStore,
) -> None:
    day = the_evening(evening_store)

    with pytest.raises(SurvivorRegistrationError, match="16:30"):
        survivor_batch(
            facts=day.facts,
            screen=day.screen,
            book=BOOK,
            registered=REGISTERED,
            predicted_at=session_publication_instant(SIGNAL) - timedelta(minutes=1),
        )


def test_the_existing_validators_accept_the_batch(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)

    validate_feature_ids(day.batch.artifact.feature_ids, role="a survivor record")
    assert (
        PredictionBatch.model_validate_json(day.batch.model_dump_json(exclude_computed_fields=True))
        == day.batch
    )
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    held = FilePredictionStore(tmp_path / "predictions", clock=lambda: FILED).get(record.record_id)
    assert held == record
    assert held is not None and held.batch == day.batch
    assert registered_at(record) == FILED


def test_register_prediction_files_it_with_its_outcome_21_sessions_after_the_signal_day(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    daily = daily_selection()
    day = the_evening(evening_store)
    calendar = daily._outcome_calendar(evening_store, EXCHANGE, SIGNAL, EVENING)

    record, outcome = daily.register_prediction(
        tmp_path, day.batch, calendar=calendar, clock=lambda: FILED
    )
    again, repeated = daily.register_prediction(
        tmp_path,
        day.batch.model_copy(update={"predicted_at": PREDICTED_AT + timedelta(minutes=30)}),
        calendar=calendar,
        clock=lambda: FILED + timedelta(minutes=30),
    )

    assert outcome == "created"
    assert record.standing == "forward"
    assert record.outcome_known_at == datetime.combine(
        calendar.shift(SIGNAL, 21), SESSION_CLOSE_TIME, SHANGHAI
    )
    assert record.outcome_known_at.date() == date(2026, 5, 11)
    assert (repeated, again.record_id) == ("unchanged", record.record_id)


def test_the_next_years_calendar_is_needed_exactly_when_the_records_window_leaves_the_year(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    daily = daily_selection()
    day = the_evening(evening_store)
    calendar = load_trading_calendar(evening_store, exchange=EXCHANGE, years=(2026,), as_of=EVENING)
    december = [session for session in calendar.trading_days if session.year == 2026][-30:]
    left = {
        session: sum(1 for later in calendar.trading_days if session < later <= date(2026, 12, 31))
        for session in december
    }
    enough = max(session for session, count in left.items() if count == 21)
    short = min(session for session, count in left.items() if count == 20)

    def put(session: date) -> PredictionRecord:
        """The evening's scores moved to `session`'s evening and filed then."""
        published = session_publication_instant(session)
        moved = day.batch.model_copy(
            update={"as_of": published, "predicted_at": published + timedelta(hours=2)}
        )
        store = FilePredictionStore(
            tmp_path / "predictions", clock=lambda: published + timedelta(hours=2, minutes=1)
        )
        return store.put(batch=moved, calendar=calendar, zone=SHANGHAI).record

    assert not daily.next_year_calendar_needed(calendar, session=enough, horizon=20)
    assert daily.next_year_calendar_needed(calendar, session=short, horizon=20)
    assert put(enough).outcome_known_at.date() == date(2026, 12, 31)
    with pytest.raises(CalendarHorizonError):
        put(short)


def test_session_record_reuses_the_held_record_and_refuses_another_answer(
    evening_store: PanelStore, late_st_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    store = FilePredictionStore(tmp_path / "predictions", clock=lambda: FILED)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    rerun = day.batch.model_copy(update={"predicted_at": PREDICTED_AT + timedelta(hours=1)})
    other = the_evening(late_st_store).batch

    assert session_record(store, rerun) == record
    assert other.predictions != day.batch.predictions
    with pytest.raises(StrategyRegistrationError, match="registered record stands"):
        session_record(store, other)


def test_a_p6_record_of_the_same_day_cannot_collide_with_the_survivor_record(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    # The P6 composite declaration as `strategy_registration.registered_declaration` writes it
    # for a static source under the same registration.
    p6 = PredictionBatch(
        as_of=day.batch.as_of,
        predicted_at=PREDICTED_AT,
        artifact=AlphaModelArtifact(
            declaration=AlphaModelDeclaration(
                name=COMPOSITE_MODEL_NAME,
                family="strategy_static",
                horizon="5d",
                feature_version=REGISTERED.config_id,
                seed=REGISTERED.seed,
                code_commit=REGISTERED.code_commit,
                hyperparameters=(
                    ("combine", "rank_mean"),
                    ("registration_sha256", REGISTERED.registration_sha256),
                ),
            ),
            feature_ids=day.batch.artifact.feature_ids,
            training_cutoff=day.batch.as_of,
            training_example_count=day.batch.artifact.training_example_count,
            parameters=day.batch.artifact.parameters,
        ),
        predictions=tuple(
            Prediction(ts_code=item.ts_code, score=-(item.score or 0.0))
            for item in day.batch.predictions
        ),
    )
    store = FilePredictionStore(tmp_path / "predictions", clock=lambda: FILED)
    p6_record = filed(evening_store, p6, tmp_path / "predictions")

    assert session_record(store, day.batch) is None
    survivor_record = filed(evening_store, day.batch, tmp_path / "predictions")
    assert survivor_record.record_id != p6_record.record_id
    assert session_record(store, p6) == p6_record
    assert session_record(store, day.batch) == survivor_record


def test_regression_pin_the_batch_bytes_do_not_move_with_the_hash_seed(
    evening_store: PanelStore,
) -> None:
    # A cheap regression pin, not evidence: every ordering the batch could inherit from a set is
    # already refused by the validators (codes strictly ascending, parameters and feature ids
    # sorted), so no mutant the suite would otherwise miss fails here. It stays to catch a
    # serialisation that starts depending on the seed.
    here = Path(__file__).resolve()
    program = (
        "import hashlib, importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('survivor_registration', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec);"
        "sys.modules['survivor_registration'] = module;"
        "spec.loader.exec_module(module);"
        "print(hashlib.sha256(module.batch_bytes(sys.argv[2]).encode()).hexdigest())"
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
    reference = hashlib.sha256(batch_bytes(str(evening_store.root)).encode()).hexdigest()

    for hash_seed in ("0", "1", "424242"):
        completed = subprocess.run(
            [sys.executable, "-c", program, str(here), str(evening_store.root)],
            env={**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": path},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == reference


# --- the input provenance ------------------------------------------------------------------------

READ_PARTITIONS: Final[frozenset[tuple[str, int]]] = frozenset(
    {
        (TRADING_CALENDAR_DATASET, 2026),
        (STOCK_BASIC_DATASET, 2025),
        (STOCK_BASIC_DATASET, 2026),
        (NAMECHANGE_DATASET, 2025),
        (NAMECHANGE_DATASET, 2026),
        (DAILY_DATASET, 2026),
        (SUSPENSION_DATASET, 2026),
        (PRICE_LIMIT_DATASET, 2026),
        *(
            (dataset(FACTOR_DEFINITIONS.get(factor)), 2026)
            for factor in FACTORS
            for dataset in (processed_factor_dataset, factor_transform_manifest_dataset)
        ),
    }
)
"""Every partition the evening reads in what can move its batch: the calendar, the registry and
the names through the session's year, the amount window's bars, the session's halts and bands,
and each factor's processed tier and its manifests. `daily_basic` and the raw factor tier are
stored and never read."""


def test_the_provenance_names_the_builds_the_windows_and_every_partition_they_touch(
    evening_store: PanelStore,
) -> None:
    day = the_evening(evening_store, held=SCOPE_HELD)
    provenance = provenance_of(evening_store, day)
    document = provenance.document()

    assert set(document) == {
        "schema",
        "registration_sha256",
        "config_id",
        "session",
        "batch_digest",
        "recorded_at",
        "held",
        "facts_digest",
        "transform_key",
        "transform_id",
        "amount_window_from",
        "builds",
        "inputs",
        "lookback",
    }
    assert document["schema"] == SURVIVOR_PROVENANCE_SCHEMA == "daily-survivor-input-provenance/v1"
    assert provenance.registration_sha256 == REGISTERED.registration_sha256
    assert provenance.config_id == REGISTERED.config_id
    assert provenance.session == SIGNAL
    assert provenance.batch_digest == batch_digest(day.batch)
    assert provenance.recorded_at == FILED
    assert provenance.held == tuple(sorted(SCOPE_HELD))
    assert (provenance.transform_key, provenance.transform_id) == (
        IDENTITY.qualified_key,
        IDENTITY.transform_id,
    )
    assert provenance.amount_window_from == AMOUNT_WINDOW_FIRST == date(2026, 3, 16)
    assert {factor: (build, output) for factor, build, output in provenance.builds} == (
        builds_at_the_signal_instant(evening_store)
    )
    assert len(provenance.builds) == 4
    assert {(item.dataset, item.year) for item in provenance.inputs} == READ_PARTITIONS
    for item in provenance.inputs:
        assert item.content_hash == evening_store.partition_content_hash(item.dataset, item.year)
    # One short census digest per partition, whatever its size; no row is hashed.
    assert all(len(item.dates_digest) == 64 for item in provenance.inputs)
    inputs = document["inputs"]
    assert isinstance(inputs, list) and all(len(entry) == 4 for entry in inputs)
    # No older bar year exists, so the held look-back read nothing outside the window.
    assert provenance.lookback == ()


def test_an_older_bar_year_is_recorded_only_when_a_held_halted_security_could_read_it(
    older_year_store: PanelStore,
) -> None:
    plain = provenance_of(older_year_store, the_evening(older_year_store))
    carried = provenance_of(
        older_year_store,
        the_evening(older_year_store, held=(HALTED,), max_carry_sessions=SHORT_CARRY),
    )

    assert (DAILY_DATASET, 2025) not in {(item.dataset, item.year) for item in plain.inputs}
    assert plain.lookback == ()
    assert (DAILY_DATASET, 2025) not in {(item.dataset, item.year) for item in carried.inputs}
    assert [(item.dataset, item.year) for item in carried.lookback] == [(DAILY_DATASET, 2025)]
    assert carried.lookback[0].content_hash == older_year_store.partition_content_hash(
        DAILY_DATASET, 2025
    )


def test_the_provenance_is_the_same_document_however_often_it_is_taken(
    evening_store: PanelStore,
) -> None:
    first = provenance_of(evening_store, the_evening(evening_store))
    second = provenance_of(evening_store, the_evening(evening_store))

    assert first == second
    assert first.digest == second.digest
    assert (
        first.digest
        == hashlib.sha256(
            json.dumps(first.document(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    assert SurvivorInputProvenance.from_document(json.loads(json.dumps(first.document()))) == first
    # The facts' bytes are recorded as a diagnostic, and they move with what `held` adds.
    with_held = provenance_of(evening_store, the_evening(evening_store, held=SCOPE_HELD))
    assert with_held.facts_digest != first.facts_digest
    assert with_held.batch_digest == first.batch_digest


def test_a_provenance_is_taken_only_for_the_survivor_record_of_its_facts_day(
    evening_store: PanelStore,
) -> None:
    day = the_evening(evening_store)
    declaration = day.batch.artifact.declaration
    p6_shaped = day.batch.model_copy(
        update={
            "artifact": day.batch.artifact.model_copy(
                update={"declaration": declaration.model_copy(update={"family": "strategy_static"})}
            )
        }
    )
    other_registration = dataclasses.replace(REGISTERED, config_id="rcf_another")
    later = day.batch.model_copy(update={"as_of": day.batch.as_of + timedelta(days=3)})

    for batch_, registered in (
        (p6_shaped, REGISTERED),
        (day.batch, other_registration),
        (later, REGISTERED),
    ):
        with pytest.raises(SurvivorRegistrationError):
            survivor_input_provenance(
                evening_store,
                facts=day.facts,
                batch=batch_,
                registered=registered,
                transform=IDENTITY,
                exchange=EXCHANGE,
                recorded_at=FILED,
            )


def test_another_transforms_build_at_the_same_instant_is_not_the_records(
    second_transform_store: PanelStore,
) -> None:
    provenance = provenance_of(second_transform_store, the_evening(second_transform_store))

    assert len(builds_at_the_signal_instant(second_transform_store, SECOND_TRANSFORM)) == 4
    assert {factor: (build, output) for factor, build, output in provenance.builds} == (
        builds_at_the_signal_instant(second_transform_store)
    )
    for scope in SCOPES:
        assert changes(second_transform_store, provenance, scope) == ()


def test_two_builds_of_one_transform_at_the_signal_instant_are_refused(
    evening_store: PanelStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = the_evening(evening_store)
    real = registration_module.load_factor_transform_manifests

    def doubled(*args: object, **kwargs: object) -> tuple[object, ...]:
        found = real(*args, **kwargs)  # type: ignore[arg-type]
        return (*found, *(item.model_copy(update={"code_commit": "fffffff"}) for item in found))

    monkeypatch.setattr(registration_module, "load_factor_transform_manifests", doubled)
    with pytest.raises(SurvivorRegistrationError, match="builds at"):
        provenance_of(evening_store, day)


def _document(provenance: SurvivorInputProvenance, **changes: object) -> dict[str, object]:
    return {**json.loads(json.dumps(provenance.document())), **changes}


def test_a_document_of_another_schema_is_refused_for_its_schema(
    evening_store: PanelStore,
) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))

    with pytest.raises(SurvivorRegistrationError, match="its schema is"):
        SurvivorInputProvenance.from_document(
            _document(provenance, schema="daily-selection-input-provenance/v2")
        )


def test_a_document_is_read_only_in_its_exact_shape(evening_store: PanelStore) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))
    good = _document(provenance)
    missing = dict(good)
    del missing["lookback"]
    inputs = good["inputs"]
    assert isinstance(inputs, list)
    first_input = inputs[0]
    wrong = {
        "an unknown key": _document(provenance, note="hello"),
        "a missing key": missing,
        "held as one string": _document(provenance, held="600605.SH"),
        "a year as text": _document(
            provenance, inputs=[[*first_input[:1], "2026", *first_input[2:]]]
        ),
        "a year as a boolean": _document(
            provenance, inputs=[[*first_input[:1], True, *first_input[2:]]]
        ),
        "a build of two fields": _document(provenance, builds=[["reversal_1d/v1", "ftm_x"]]),
        "a number as the config id": _document(provenance, config_id=7),
        "a naive filing instant": _document(provenance, recorded_at="2026-04-10T18:35:00"),
    }

    assert SurvivorInputProvenance.from_document(good) == provenance
    for what, body in wrong.items():
        with pytest.raises(SurvivorRegistrationError, match="not a"):
            SurvivorInputProvenance.from_document(body)
        assert what


def test_the_provenance_is_written_once_under_its_digest(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))

    path = write_survivor_provenance(tmp_path, provenance)
    written = path.read_bytes()
    again = write_survivor_provenance(tmp_path, provenance)

    assert path == tmp_path / SURVIVOR_PROVENANCE_DIRECTORY / f"{provenance.digest}.json"
    assert again == path
    assert path.read_bytes() == written
    assert SurvivorInputProvenance.from_document(json.loads(written)) == provenance
    assert sorted(item.name for item in path.parent.iterdir()) == [path.name]

    path.write_text(written.decode("utf-8").replace(provenance.config_id, "rcf_other"), "utf-8")
    with pytest.raises(SurvivorRegistrationError, match="says something else"):
        write_survivor_provenance(tmp_path, provenance)


def test_a_provenance_file_is_read_back_only_when_it_is_what_its_name_says_and_this_registrations(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))
    path = write_survivor_provenance(tmp_path, provenance)
    text = path.read_text(encoding="utf-8")

    assert read_survivor_provenance(path, registration_sha256=REGISTERED.registration_sha256) == (
        provenance
    )
    with pytest.raises(SurvivorRegistrationError, match="registration"):
        read_survivor_provenance(path, registration_sha256="0" * 64)

    renamed = path.with_name("0" * 64 + ".json")
    renamed.write_text(text, encoding="utf-8")
    with pytest.raises(SurvivorRegistrationError, match="not what its name says"):
        read_survivor_provenance(renamed, registration_sha256=REGISTERED.registration_sha256)

    for body in (b"\xff\xfe not text", b"{not json"):
        damaged = path.with_name(hashlib.sha256(body).hexdigest() + ".json")
        damaged.write_bytes(body)
        with pytest.raises(SurvivorRegistrationError, match="not a"):
            read_survivor_provenance(damaged, registration_sha256=REGISTERED.registration_sha256)


# --- the recompute, and when a mismatch may be called unverifiable -------------------------------


def test_the_recompute_scores_the_filed_day_at_its_custody_instant(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")

    assert recompute(evening_store, record) == record.batch


def test_the_recompute_equals_the_record_after_later_sessions_are_appended(
    evening_store: PanelStore, grown_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store, held=SCOPE_HELD)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)
    moved = [
        item
        for item in provenance.inputs
        if grown_store.partition_content_hash(item.dataset, item.year) != item.content_hash
    ]

    again = recompute(grown_store, record, held=provenance.held, provenance=provenance)

    assert scored(again) == scored(record.batch)
    assert again == record.batch
    # Measured on the frame: appending four sessions, a rename, a listing and a build moves 13 of
    # the 16 content hashes the day read (the bars, bands, halts, the 2026 registry and names, and
    # all eight factor partitions) -- and not one windowed census, nor one build's output.
    assert len(moved) == 13
    for scope in SCOPES:
        assert changes(grown_store, provenance, scope) == ()


def test_the_recompute_reads_at_the_readable_instant_when_its_filing_instant_cannot_be_read(
    evening_store: PanelStore, late_clock_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")

    with pytest.raises(PartitionNotYetKnowableError):
        build_facts_day(
            store=late_clock_store,
            session=SIGNAL,
            as_of=registered_at(record),
            config=BOOK,
            factor_ids=FACTORS,
            transform=IDENTITY,
            exchange=EXCHANGE,
        )
    assert scored(recompute(late_clock_store, record)) == scored(record.batch)


def test_a_restated_build_makes_the_recompute_differ_and_is_named(
    evening_store: PanelStore, restated_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)
    before = {factor: output for factor, _build, output in provenance.builds}["reversal_1d/v1"]
    after = builds_at_the_signal_instant(restated_store)["reversal_1d/v1"][1]

    again = recompute(restated_store, record)

    assert scored(again) != scored(record.batch)
    assert RESTATED in day.batch.subjects
    assert RESTATED not in again.subjects
    for scope in SCOPES:
        assert changes(restated_store, provenance, scope) == (
            f"reversal_1d/v1: the {IDENTITY.qualified_key} build at "
            f"{instant(SIGNAL_INDEX).isoformat()} now holds {after}, not {before}",
        )


def test_a_value_identical_rebuild_of_the_signal_days_build_names_nothing(
    evening_store: PanelStore, identical_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)
    recorded = {factor: build for factor, build, _output in provenance.builds}

    assert (
        builds_at_the_signal_instant(identical_store)["reversal_1d/v1"][0]
        != (recorded["reversal_1d/v1"])
    )
    assert recompute(identical_store, record) == record.batch
    for scope in SCOPES:
        assert changes(identical_store, provenance, scope) == ()


def _renames_on(store: PanelStore, day: date) -> int:
    coverage = store.read_coverage(NAMECHANGE_DATASET, 2026)
    assert coverage is not None
    return {entry.event_date: entry.row_count for entry in coverage.dates}.get(day, 0)


def test_a_row_dated_on_the_session_and_stored_after_filing_is_a_restatement(
    evening_store: PanelStore, late_st_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)

    again = recompute(late_st_store, record)

    assert LATE_ST in day.batch.subjects
    assert LATE_ST not in again.subjects
    # The row arrived after filing under a clock the filing instant reads (midnight of the
    # announcement), so only the census says it is new: one rename on 2026-04-10, then two.
    assert (_renames_on(evening_store, SIGNAL), _renames_on(late_st_store, SIGNAL)) == (1, 2)
    for scope in SCOPES:
        assert changes(late_st_store, provenance, scope) == (
            f"{NAMECHANGE_DATASET}:2026 (its rows through {SIGNAL.isoformat()} were restated)",
        )


def test_a_row_dated_before_the_session_and_stored_after_filing_is_a_restatement(
    evening_store: PanelStore, early_rename_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)
    announced = SESSIONS[SIGNAL_INDEX - 3]

    again = recompute(early_rename_store, record)

    assert LATE_ST not in again.subjects
    assert (_renames_on(evening_store, announced), _renames_on(early_rename_store, announced)) == (
        0,
        1,
    )
    for scope in SCOPES:
        assert changes(early_rename_store, provenance, scope) == (
            f"{NAMECHANGE_DATASET}:2026 (its rows through {SIGNAL.isoformat()} were restated)",
        )


def test_a_partition_the_day_read_that_is_gone_is_a_restatement(
    evening_store: PanelStore, frame_root: Path
) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))
    store = _copy(evening_store, frame_root / "gone")
    assert store.remove_partition(NAMECHANGE_DATASET, 2025)

    for scope in SCOPES:
        assert changes(store, provenance, scope) == (
            f"{NAMECHANGE_DATASET}:2025 (no longer stored)",
        )


def test_a_partition_stored_since_with_rows_on_or_before_the_session_is_named(
    evening_store: PanelStore,
) -> None:
    # The New Year shape: the session's registry year held no lifecycle event yet when the
    # record was filed, so the day read the year before; a 2026 partition with a row dated on or
    # before the session is a registry the day did not read.
    provenance = provenance_of(evening_store, the_evening(evening_store))
    without = dataclasses.replace(
        provenance,
        inputs=tuple(
            item
            for item in provenance.inputs
            if (item.dataset, item.year) != (STOCK_BASIC_DATASET, 2026)
        ),
    )

    for scope in SCOPES:
        assert changes(evening_store, without, scope) == (
            f"{STOCK_BASIC_DATASET}:2026 (stored since, with rows through {SIGNAL.isoformat()})",
        )


def test_an_untouched_store_names_no_change(evening_store: PanelStore) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))

    for scope in SCOPES:
        assert changes(evening_store, provenance, scope) == ()


def test_probe_a_an_amount_cut_in_place_is_refused_not_excused(
    evening_store: PanelStore, probe_a_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)

    again = recompute(probe_a_store, record)

    assert set(day.batch.subjects) - set(again.subjects) == {THINNED}
    assert set(again.subjects) - set(day.batch.subjects) == {THICKENED}
    # Value-blind: no row was added, removed or re-dated, so nothing is named and the check
    # refuses the record.
    assert changes(probe_a_store, provenance, DIFFERS) == ()


def test_probe_b_a_halt_months_before_the_session_names_nothing(
    evening_store: PanelStore, probe_b_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)

    assert recompute(probe_b_store, record) == record.batch
    assert probe_b_store.partition_content_hash(SUSPENSION_DATASET, 2026) != next(
        item.content_hash for item in provenance.inputs if item.dataset == SUSPENSION_DATASET
    )
    for scope in SCOPES:
        assert changes(probe_b_store, provenance, scope) == ()


def test_probes_a_and_b_together_are_refused_the_unrelated_halt_excuses_nothing(
    evening_store: PanelStore, probe_ab_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)

    assert scored(recompute(probe_ab_store, record)) != scored(record.batch)
    assert changes(probe_ab_store, provenance, DIFFERS) == ()


def test_an_older_bar_year_rebuilt_or_gone_never_excuses_a_mismatch(
    older_year_store: PanelStore, tmp_path: Path
) -> None:
    plain = provenance_of(older_year_store, the_evening(older_year_store))
    carried = provenance_of(
        older_year_store,
        the_evening(older_year_store, held=(HALTED,), max_carry_sessions=SHORT_CARRY),
    )
    rebuilt = _copy(older_year_store, tmp_path / "rebuilt")
    write_panel_batch(rebuilt, _bars_of_2025(3), year=2025)
    gone = _copy(older_year_store, tmp_path / "gone")
    assert gone.remove_partition(DAILY_DATASET, 2025)

    for store in (rebuilt, gone):
        for scope in SCOPES:
            assert changes(store, plain, scope) == ()
        assert changes(store, carried, DIFFERS) == ()
    # Only a recompute that could not be made may name what a held security's look-back read.
    assert changes(rebuilt, carried, FAILED) == (
        f"{DAILY_DATASET}:2025 (rewritten since the filing; a held security's look-back read it)",
    )
    assert changes(gone, carried, FAILED) == (f"{DAILY_DATASET}:2025 (no longer stored)",)


def test_another_transform_is_a_caller_error_not_a_change(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)

    for scope in SCOPES:
        with pytest.raises(SurvivorCheckArgumentError, match="transform"):
            survivor_provenance_changes(
                evening_store,
                provenance,
                transform=SECOND_TRANSFORM,
                scope=scope,  # type: ignore[arg-type]
            )
    with pytest.raises(SurvivorCheckArgumentError, match="transform"):
        recompute(evening_store, record, transform=SECOND_TRANSFORM, provenance=provenance)
    with pytest.raises(SurvivorCheckArgumentError, match="held"):
        recompute(evening_store, record, held=(HALTED,), provenance=provenance)
    # A provenance taken with other holdings still names this batch (holdings never move it), so
    # another record's provenance is one naming another batch.
    another_records = dataclasses.replace(provenance, batch_digest="0" * 64)
    with pytest.raises(SurvivorCheckArgumentError, match="batch"):
        recompute(evening_store, record, provenance=another_records)
    another_registrations = dataclasses.replace(provenance, registration_sha256="0" * 64)
    with pytest.raises(SurvivorCheckArgumentError, match="registration"):
        recompute(evening_store, record, provenance=another_registrations)
    with pytest.raises(SurvivorCheckArgumentError, match="scope"):
        survivor_provenance_changes(
            evening_store,
            provenance,
            transform=IDENTITY,
            scope="anything",  # type: ignore[arg-type]
        )


def test_only_named_refusals_mean_a_record_cannot_be_recomputed(
    evening_store: PanelStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusals = set(SURVIVOR_RECOMPUTE_REFUSALS)
    assert {
        SurvivorFactsError,
        SurvivorRegistrationError,
        PanelStorageError,
        pydantic.ValidationError,
    } <= refusals
    assert not any(
        issubclass(kind, refusal)
        for kind in (
            TypeError,
            KeyError,
            AttributeError,
            ValueError,
            RuntimeError,
            PanelCatalogBusyError,
            SurvivorCheckArgumentError,
        )
        for refusal in refusals
    )

    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")

    def broken(**_arguments: object) -> FactsDay:
        raise TypeError("a programming error inside the recompute")

    monkeypatch.setattr(registration_module, "build_facts_day", broken)
    with pytest.raises(TypeError, match="programming error"):
        recompute(evening_store, record)


def test_the_recompute_declares_the_registrations_declaration_not_the_records(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    # A record whose declaration is not the registration's -- here its horizon -- is not what
    # the registered configuration scores again, so the two never compare equal.
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    declaration = day.batch.artifact.declaration.model_copy(update={"horizon": "5d"})
    artifact = day.batch.artifact.model_copy(update={"declaration": declaration})
    foreign = record.model_copy(
        update={"batch": day.batch.model_copy(update={"artifact": artifact})}
    )

    again = recompute(evening_store, foreign)

    assert again.artifact.declaration == survivor_declaration(BOOK, REGISTERED)
    assert scored(again) != scored(foreign.batch)
