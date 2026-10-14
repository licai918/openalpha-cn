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
  in which `600010.SH` has no value;
- **late ST rename**: an ST rename of `600001.SH` announced on the signal day, stored the next
  morning under the provider's own clock (midnight of the announcement) -- the shape the morning
  re-check exists for;
- **late clock**: an ordinary rename dated on the signal day whose availability is the next
  morning's fetch, so the filing instant cannot read the partition at all.
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
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Final
from zoneinfo import ZoneInfo

import pytest
from panel_fixtures import EXCHANGE, write_generated_panel
from panel_fixtures import _batch as batch
from panel_fixtures import _midnight_shanghai as midnight_shanghai
from test_survivor_facts_panel import (
    CONFIG,
    FACTORS,
    FULL,
    HALTED,
    IDENTITY,
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
    SESSION_CLOSE_TIME,
)
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
    NameRecord,
)
from openalpha_cn.domain.panel_batch import PanelColumn
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import ScreenResult, SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, CalendarHorizonError
from openalpha_cn.panel.catalog import PartitionNotYetKnowableError
from openalpha_cn.panel.store import PanelStore
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
from openalpha_cn.strategy_survivor_facts import FactsDay, build_facts_day
from openalpha_cn.strategy_survivor_registration import (
    SURVIVOR_FAMILY,
    SURVIVOR_HORIZON,
    SURVIVOR_PROVENANCE_DIRECTORY,
    SURVIVOR_PROVENANCE_SCHEMA,
    SurvivorInputProvenance,
    SurvivorRegistrationError,
    recompute_survivor_batch,
    survivor_batch,
    survivor_declaration,
    survivor_input_provenance,
    survivor_provenance_changes,
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
SCOPE_HELD: Final[tuple[str, ...]] = (HALTED, "600010.SH", "600610.SH")
"""A halted holding, a trading eligible one and a delisted one: everything `held` can change."""
LATE_ST: Final[str] = "600001.SH"
RESTATED: Final[str] = "600010.SH"


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


def _restate_build(store: PanelStore) -> None:
    """Supersede `reversal_1d`'s 16:30 build of the signal day by one without `RESTATED`."""
    at = instant(SIGNAL_INDEX)
    definition = FACTOR_DEFINITIONS.get(FACTORS[0])
    raw_ids = [
        manifest.manifest_id
        for manifest in load_factor_manifests(store, definition, years=(2026,), as_of=at)
        if manifest.as_of == at
    ]
    processed_ids = [
        manifest.transform_manifest_id
        for manifest in load_factor_transform_manifests(store, definition, years=(2026,), as_of=at)
        if manifest.as_of == at
    ]
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


@pytest.fixture(scope="module")
def frame_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("survivor-registration")


@pytest.fixture(scope="module")
def evening_store(frame_root: Path) -> PanelStore:
    return write_panel(frame_root / "evening", TRIMMED)


@pytest.fixture(scope="module")
def grown_store(frame_root: Path, evening_store: PanelStore) -> PanelStore:
    store = _copy(evening_store, frame_root / "grown")
    _grow(store)
    return store


@pytest.fixture(scope="module")
def restated_store(frame_root: Path, evening_store: PanelStore) -> PanelStore:
    store = _copy(evening_store, frame_root / "restated")
    _restate_build(store)
    return store


@pytest.fixture(scope="module")
def late_st_store(frame_root: Path, evening_store: PanelStore) -> PanelStore:
    store = _copy(evening_store, frame_root / "late-st")
    _rewrite_names(
        store,
        NameRecord(
            ts_code=LATE_ST,
            name="ST丁",
            effective_from=date(2026, 4, 13),
            announced_on=SIGNAL,
            change_reason="其他",
        ),
    )
    return store


@pytest.fixture(scope="module")
def late_clock_store(frame_root: Path, evening_store: PanelStore) -> PanelStore:
    store = _copy(evening_store, frame_root / "late-clock")
    _rewrite_names(
        store,
        NameRecord(
            ts_code=RESTATED,
            name="普通新",
            effective_from=date(2026, 4, 20),
            announced_on=SIGNAL,
            change_reason="其他",
        ),
        available=NEXT_MORNING,
    )
    return store


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
    store: PanelStore, *, held: Sequence[str] = (), book: SurvivorBookConfig = BOOK
) -> Evening:
    facts = read_day(store, held=held, config=book)
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
    store: PanelStore, record: PredictionRecord, *, held: Sequence[str] = ()
) -> PredictionBatch:
    return recompute_survivor_batch(
        store,
        record,
        book=BOOK,
        transform=IDENTITY,
        exchange=EXCHANGE,
        min_coverage=MIN_COVERAGE,
        max_carry_sessions=MAX_CARRY_SESSIONS,
        held=held,
        registered=REGISTERED,
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
        recorded_at=FILED,
    )


def builds_at_the_signal_instant(store: PanelStore) -> dict[str, str]:
    at = instant(SIGNAL_INDEX)
    found: dict[str, str] = {}
    for factor in FACTORS:
        for manifest in load_factor_transform_manifests(
            store, FACTOR_DEFINITIONS.get(factor), years=(2026,), as_of=at
        ):
            if manifest.as_of == at and manifest.transform_id == IDENTITY.transform_id:
                found[factor] = manifest.transform_manifest_id
    return found


def batch_bytes(store_root: str) -> str:
    """The subprocess half of the hash-seed test: the evening's batch off an existing store."""
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
    unheld = the_evening(evening_store)
    held = the_evening(evening_store, held=SCOPE_HELD)

    assert held.facts.held == tuple(sorted(SCOPE_HELD))
    assert held.facts != unheld.facts
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


def test_processes_with_different_hash_seeds_build_the_same_batch_bytes(
    evening_store: PanelStore,
) -> None:
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
            str(here.parent),
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
"""Every partition the evening reads: the calendar, the registry and the names through the
session's year, the day's bars, halts and bands, and each factor's processed tier and its
manifests. `daily_basic` and the raw factor tier are stored and never read."""


def test_the_provenance_names_the_four_builds_and_every_partition_the_day_read(
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
        "builds",
        "inputs",
    }
    assert document["schema"] == SURVIVOR_PROVENANCE_SCHEMA == "daily-survivor-input-provenance/v1"
    assert provenance.registration_sha256 == REGISTERED.registration_sha256
    assert provenance.config_id == REGISTERED.config_id
    assert provenance.session == SIGNAL
    assert provenance.batch_digest == batch_digest(day.batch)
    assert provenance.recorded_at == FILED
    assert provenance.held == tuple(sorted(SCOPE_HELD))
    assert dict(provenance.builds) == builds_at_the_signal_instant(evening_store)
    assert len(provenance.builds) == 4
    assert {(item.dataset, item.year) for item in provenance.inputs} == READ_PARTITIONS
    for item in provenance.inputs:
        assert item.content_hash == evening_store.partition_content_hash(item.dataset, item.year)
    # The census of rows per event date through the session, not one digest per row: a partition
    # contributes one short string whatever its size.
    assert all(len(item.dates_digest) == 64 for item in provenance.inputs)
    inputs = document["inputs"]
    assert isinstance(inputs, list) and all(len(entry) == 4 for entry in inputs)


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
                recorded_at=FILED,
            )


def test_a_document_of_another_schema_is_refused() -> None:
    with pytest.raises(SurvivorRegistrationError, match="daily-survivor-input-provenance/v1"):
        SurvivorInputProvenance.from_document({"schema": "daily-selection-input-provenance/v2"})
    with pytest.raises(SurvivorRegistrationError, match="not a"):
        SurvivorInputProvenance.from_document(
            {"schema": SURVIVOR_PROVENANCE_SCHEMA, "registration_sha256": "x"}
        )


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

    again = recompute(grown_store, record, held=provenance.held)

    assert scored(again) == scored(record.batch)
    assert again == record.batch
    # Measured on the frame: appending four sessions, a rename, a listing and a build moves 13 of
    # the 16 content hashes the day read (the bars, bands, halts, the 2026 registry and names, and
    # all eight factor partitions) -- and not one census through the session, nor one build.
    assert len(moved) == 13
    assert survivor_provenance_changes(grown_store, provenance, transform=IDENTITY) == ()


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

    again = recompute(restated_store, record)

    assert scored(again) != scored(record.batch)
    assert RESTATED in day.batch.subjects
    assert RESTATED not in again.subjects
    assert survivor_provenance_changes(restated_store, provenance, transform=IDENTITY) == (
        f"reversal_1d/v1: the {IDENTITY.qualified_key} build at "
        f"{instant(SIGNAL_INDEX).isoformat()} is "
        f"{builds_at_the_signal_instant(restated_store)['reversal_1d/v1']}, not "
        f"{dict(provenance.builds)['reversal_1d/v1']}",
    )


def test_a_row_dated_on_the_session_and_stored_after_filing_is_a_restatement(
    evening_store: PanelStore, late_st_store: PanelStore, tmp_path: Path
) -> None:
    day = the_evening(evening_store)
    record = filed(evening_store, day.batch, tmp_path / "predictions")
    provenance = provenance_of(evening_store, day)

    again = recompute(late_st_store, record)

    def renames_on_the_signal_day(store: PanelStore) -> int:
        coverage = store.read_coverage(NAMECHANGE_DATASET, 2026)
        assert coverage is not None
        return {entry.event_date: entry.row_count for entry in coverage.dates}[SIGNAL]

    assert LATE_ST in day.batch.subjects
    assert LATE_ST not in again.subjects
    # The row arrived after filing under a clock the filing instant reads (midnight of the
    # announcement), so only the census says it is new: one rename on 2026-04-10, then two.
    assert (renames_on_the_signal_day(evening_store), renames_on_the_signal_day(late_st_store)) == (
        1,
        2,
    )
    assert survivor_provenance_changes(late_st_store, provenance, transform=IDENTITY) == (
        f"{NAMECHANGE_DATASET}:2026 (its rows through {SIGNAL.isoformat()} were restated)",
    )


def test_a_partition_the_day_read_that_is_gone_is_a_restatement(
    evening_store: PanelStore, frame_root: Path
) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))
    store = _copy(evening_store, frame_root / "gone")
    assert store.remove_partition(NAMECHANGE_DATASET, 2025)

    assert survivor_provenance_changes(store, provenance, transform=IDENTITY) == (
        f"{NAMECHANGE_DATASET}:2025 (no longer stored)",
    )


def test_an_untouched_store_names_no_change(evening_store: PanelStore) -> None:
    provenance = provenance_of(evening_store, the_evening(evening_store))

    assert survivor_provenance_changes(evening_store, provenance, transform=IDENTITY) == ()


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
