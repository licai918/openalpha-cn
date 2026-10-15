"""The survivor book's late-record check (`V2-P7-018`, task E1).

Every survivor record is filed after its signal instant -- the daily command files at about
18:30, two hours after the 16:30 publication its scores stand at -- so every one is checked
before the weekly report counts it: bound to the registration, matched to the provenance its run
wrote, and scored again from the store (`strategy_survivor_check.SurvivorRecordCheck`). Equal, it
is verified and the verdict is kept; otherwise it is UNVERIFIABLE when the provenance can name what
changed since the filing, and refused when it cannot.

## The frame

`tests/unit/test_strategy_survivor_registration.py`'s stores, built from the facts frame of
`tests/unit/test_survivor_facts_panel.py` (`panel_fixtures.generate_panel`) and imported rather
than copied: the evening of Friday 2026-04-10 and the stores that copy it and then write to it
through the real writers -- the panel grown by later sessions of the record's own year, the
signal day's 16:30 build superseded, a bar's amount cut in place (probe A). This module adds
three of its own, each a copy of the evening written the same way:

- **refetched**: what the fetches after the record's year has closed do to that year -- the
  registry, the names and the calendar fetched again, the same rows under a later
  `ingested_time`, which moves every one of their content hashes and no value;
- **renamed in place**: the 2025 name of an eligible security (`600001.SH`, `普通0`) corrected in
  place to an ST name, its announcement and effective dates unchanged -- no row added, removed or
  re-dated;
- **vanished**: a 2025 bar year a held, halted security's look-back read, rewritten, and the
  session's band year gone, so the day cannot be scored again at all.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from panel_fixtures import EXCHANGE
from test_strategy_survivor_registration import (
    BOOK,
    EVENING,
    MAX_CARRY_SESSIONS,
    MIN_COVERAGE,
    REGISTERED,
    SECOND_TRANSFORM,
    SHORT_CARRY,
    THINNED,
    Evening,
    _bars_of_2025,
    _copy,
    _grow,
    _rebatched,
    _restate_build,
    _rows,
    _thin_the_amount,
    builds_at_the_signal_instant,
    filed,
    provenance_of,
    the_evening,
)
from test_survivor_facts_panel import (
    HALTED,
    IDENTITY,
    SIGNAL,
    SIGNAL_INDEX,
    TRIMMED,
    build_panel,
    instant,
    write_panel,
)

from openalpha_cn import strategy_survivor_check as check_module
from openalpha_cn import strategy_survivor_registration as registration_module
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.name_history import NAME_COLUMN, NAMECHANGE_DATASET
from openalpha_cn.domain.panel_batch import ColumnarPanelBatch, TimelineColumns
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.store import PanelCatalogBusyError, PanelStore
from openalpha_cn.panel_ingest import (
    session_publication_instant,
    split_panel_batch_by_year,
    stored_rows_digest,
    write_name_history,
    write_panel_batch,
    write_stock_universe,
    write_trading_calendar,
)
from openalpha_cn.strategy_registration import (
    StrategyRegistrationError,
    batch_digest,
    registered_at,
)
from openalpha_cn.strategy_survivor_check import (
    NO_PROVENANCE,
    NOT_BOUND,
    SURVIVOR_VERDICT_DIRECTORY,
    VERDICT_REFUSED,
    VERDICT_UNVERIFIABLE,
    VERDICT_VERIFIED,
    FileSurvivorVerdicts,
    SurvivorCacheKey,
    SurvivorCheckError,
    SurvivorRecordCheck,
    SurvivorRecordVerdict,
    SurvivorVerdictCache,
    survivor_cache_key,
    survivor_provenance_index,
)
from openalpha_cn.strategy_survivor_registration import (
    RECOMPUTE_DIFFERS,
    RECOMPUTE_FAILED,
    SURVIVOR_PROVENANCE_DIRECTORY,
    SurvivorCheckArgumentError,
    SurvivorInputProvenance,
    write_survivor_provenance,
)

REPORT_AS_OF: Final[datetime] = EVENING + timedelta(days=10)
"""When the weekly report runs: the clock its verdict files are stamped with."""
REFETCHED_AT: Final[datetime] = EVENING + timedelta(days=300)
"""When the fetches after the record's year closed ran: the registry, names and calendar again."""
RENAMED: Final[str] = "600001.SH"
"""An eligible security whose 2025 name, `普通0`, is corrected in place to an ST name."""


# --- the stores ----------------------------------------------------------------------------------


def _restamped(source: ColumnarPanelBatch) -> ColumnarPanelBatch:
    """`source`'s rows fetched again at `REFETCHED_AT`: every clock the provider derives from the
    data kept, `ingested_time` -- the fetch instant -- moved (`providers/tushare.py`)."""
    clocks = source.timeline
    return dataclasses.replace(
        source,
        as_of=REFETCHED_AT,
        fetched_at=REFETCHED_AT,
        timeline=TimelineColumns(
            event_time=clocks.event_time,
            available_time=clocks.available_time,
            ingested_time=tuple(REFETCHED_AT for _ in clocks.event_time),
            revision_time=clocks.revision_time,
        ),
    )


def _refetch_after_the_year_closed(store: PanelStore) -> None:
    """The registry, every rename year and the calendar fetched again, value for value."""
    panel = build_panel(TRIMMED)
    write_stock_universe(store, _restamped(panel.batch(STOCK_BASIC_DATASET)))
    for _year, part in split_panel_batch_by_year(panel.batch(NAMECHANGE_DATASET)):
        write_name_history(store, _restamped(part))
    write_trading_calendar(store, _restamped(panel.batch(TRADING_CALENDAR_DATASET)))


def _rename_in_place(store: PanelStore) -> None:
    """`RENAMED`'s 2025 name corrected to `ST普通0`, every date of every row unchanged."""
    for year, part in split_panel_batch_by_year(build_panel(TRIMMED).batch(NAMECHANGE_DATASET)):
        if year != 2025:
            continue
        rows = _rows(part)
        for row in rows:
            if row["subject"] == RENAMED:
                assert row[NAME_COLUMN] == "普通0"
                row[NAME_COLUMN] = "ST普通0"
        write_name_history(store, _rebatched(part, rows))


def _stored(
    name: str, base: str, *changes: Callable[[PanelStore], None]
) -> Callable[..., PanelStore]:
    @pytest.fixture(scope="module", name=name)
    def fixture(request: pytest.FixtureRequest, frame_root: Path) -> PanelStore:
        store = _copy(request.getfixturevalue(base), frame_root / name)
        for change in changes:
            change(store)
        return store

    return fixture


@pytest.fixture(scope="module")
def frame_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("survivor-check")


@pytest.fixture(scope="module")
def evening_store(frame_root: Path) -> PanelStore:
    return write_panel(frame_root / "evening", TRIMMED)


@pytest.fixture(scope="module")
def older_year_store(frame_root: Path, evening_store: PanelStore) -> PanelStore:
    """The evening with a 2025 bar partition stored before it."""
    store = _copy(evening_store, frame_root / "older-year")
    write_panel_batch(store, _bars_of_2025(2), year=2025)
    return store


grown_store = _stored("grown_store", "evening_store", _grow)
restated_store = _stored("restated_store", "evening_store", _restate_build)
probe_a_store = _stored("probe_a_store", "evening_store", _thin_the_amount)
refetched_store = _stored("refetched_store", "evening_store", _refetch_after_the_year_closed)
renamed_store = _stored("renamed_store", "evening_store", _rename_in_place)
vanished_store = _stored(
    "vanished_store",
    "older_year_store",
    lambda store: write_panel_batch(store, _bars_of_2025(3), year=2025),
    lambda store: store.remove_partition(PRICE_LIMIT_DATASET, 2026),
)


# --- one record, its runtime directory and its check ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Filing:
    """One evening's record, the provenance its run wrote, and the runtime directory holding it."""

    day: Evening
    record: PredictionRecord
    provenance: SurvivorInputProvenance
    runtime: Path


def filing(
    store: PanelStore,
    directory: Path,
    *,
    held: Sequence[str] = (),
    max_carry_sessions: int = MAX_CARRY_SESSIONS,
) -> Filing:
    day = the_evening(store, held=held, max_carry_sessions=max_carry_sessions)
    record = filed(store, day.batch, directory / "predictions")
    provenance = provenance_of(store, day)
    runtime = directory / "runtime"
    write_survivor_provenance(runtime, provenance)
    return Filing(day=day, record=record, provenance=provenance, runtime=runtime)


def verdicts_of(runtime: Path, *, at: datetime = REPORT_AS_OF) -> FileSurvivorVerdicts:
    return FileSurvivorVerdicts(runtime, clock=lambda: at)


def check_of(
    store: PanelStore,
    runtime: Path,
    *,
    verdicts: SurvivorVerdictCache | None = None,
    transform: FactorTransformSpec = IDENTITY,
    max_carry_sessions: int = MAX_CARRY_SESSIONS,
    min_coverage: float = MIN_COVERAGE,
    registration_sha256: str = REGISTERED.registration_sha256,
) -> SurvivorRecordCheck:
    return SurvivorRecordCheck(
        store,
        book=BOOK,
        transform=transform,
        exchange=EXCHANGE,
        min_coverage=min_coverage,
        max_carry_sessions=max_carry_sessions,
        registered=REGISTERED,
        provenance=survivor_provenance_index(runtime, registration_sha256=registration_sha256),
        verdicts=verdicts,
    )


def key_of(
    store: PanelStore,
    provenance: SurvivorInputProvenance,
    *,
    transform: FactorTransformSpec = IDENTITY,
    **settings: object,
) -> SurvivorCacheKey:
    """`survivor_cache_key` under the frame's check settings, `settings` changing any of them."""
    return survivor_cache_key(
        store,
        provenance,
        transform=transform,
        **{  # type: ignore[arg-type]
            "exchange": EXCHANGE,
            "min_coverage": MIN_COVERAGE,
            "max_carry_sessions": MAX_CARRY_SESSIONS,
            **settings,
        },
    )


def kept_files(runtime: Path) -> list[Path]:
    directory = runtime / SURVIVOR_VERDICT_DIRECTORY
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


@pytest.fixture
def recomputes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The record ids the check scored again: `recompute_survivor_batch` counted, not replaced."""
    called: list[str] = []
    real = check_module.recompute_survivor_batch

    def counted(store: PanelStore, record: PredictionRecord, **arguments: object) -> object:
        called.append(record.record_id)
        return real(store, record, **arguments)  # type: ignore[arg-type]

    monkeypatch.setattr(check_module, "recompute_survivor_batch", counted)
    return called


def foreign(record: PredictionRecord) -> PredictionRecord:
    """`record` declared at another horizon: a record of the same day that the registered survivor
    configuration does not declare, so it is not bound."""
    batch = record.batch
    declaration = batch.artifact.declaration.model_copy(update={"horizon": "5d"})
    artifact = batch.artifact.model_copy(update={"declaration": declaration})
    return record.model_copy(update={"batch": batch.model_copy(update={"artifact": artifact})})


# --- step 1: bound first -------------------------------------------------------------------------


def test_a_record_not_bound_is_refused_and_never_recomputed(
    evening_store: PanelStore, restated_store: PanelStore, tmp_path: Path, recomputes: list[str]
) -> None:
    # The foreign record even has a provenance naming its batch, and the store it is checked
    # against superseded the 16:30 build it read: recomputed, it would differ and that build would
    # be named -- an UNVERIFIABLE admission of a record the registration never declared.
    held = filing(evening_store, tmp_path)
    other = foreign(held.record)
    write_survivor_provenance(
        held.runtime,
        dataclasses.replace(held.provenance, batch_digest=batch_digest(other.batch)),
    )

    verdict = check_of(restated_store, held.runtime, verdicts=verdicts_of(held.runtime))(other)

    assert (verdict.verdict, verdict.scope, verdict.changes, verdict.cached) == (
        VERDICT_REFUSED,
        NOT_BOUND,
        (),
        False,
    )
    assert verdict.record_id == other.record_id
    assert verdict.session == SIGNAL
    assert verdict.reason is not None and "not declared" in verdict.reason
    assert recomputes == []
    assert kept_files(held.runtime) == []


# --- step 2: the provenance index ----------------------------------------------------------------


def test_the_index_holds_this_registrations_provenance_by_session_and_batch(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    held = filing(evening_store, tmp_path)

    index = survivor_provenance_index(
        held.runtime, registration_sha256=REGISTERED.registration_sha256
    )

    assert dict(index) == {(SIGNAL, batch_digest(held.record.batch)): held.provenance}
    assert survivor_provenance_index(tmp_path / "nothing-here", registration_sha256="9f" * 32) == {}


def test_the_index_skips_another_registrations_provenance_before_reading_it(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    # `read_survivor_provenance` refuses a file written under another registration; called on
    # every file, one foreign file would refuse the whole report.
    held = filing(evening_store, tmp_path)
    another = dataclasses.replace(held.provenance, registration_sha256="0" * 64)
    write_survivor_provenance(held.runtime, another)
    assert len(list((held.runtime / SURVIVOR_PROVENANCE_DIRECTORY).glob("*.json"))) == 2

    index = survivor_provenance_index(
        held.runtime, registration_sha256=REGISTERED.registration_sha256
    )

    assert list(index.values()) == [held.provenance]


def test_two_provenance_files_naming_one_filing_refuse_the_index(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    held = filing(evening_store, tmp_path)
    second = dataclasses.replace(
        held.provenance, recorded_at=held.provenance.recorded_at.replace(second=59)
    )
    write_survivor_provenance(held.runtime, second)

    with pytest.raises(SurvivorCheckError) as refused:
        survivor_provenance_index(held.runtime, registration_sha256=REGISTERED.registration_sha256)

    message = str(refused.value)
    assert f"{held.provenance.digest}.json" in message
    assert f"{second.digest}.json" in message
    assert issubclass(SurvivorCheckError, StrategyRegistrationError)


@pytest.mark.parametrize(
    "body",
    [b"\xff\xfe not text", b"{not json", json.dumps({"schema": "elsewhere/v1"}).encode()],
)
def test_a_provenance_file_that_cannot_be_read_refuses_the_index(
    evening_store: PanelStore, tmp_path: Path, body: bytes
) -> None:
    # A file whose registration cannot be told is never skipped: it could be this registration's.
    held = filing(evening_store, tmp_path)
    broken = held.runtime / SURVIVOR_PROVENANCE_DIRECTORY / f"{'a' * 64}.json"
    broken.write_bytes(body)

    with pytest.raises(SurvivorCheckError, match=broken.name):
        survivor_provenance_index(held.runtime, registration_sha256=REGISTERED.registration_sha256)


def test_a_record_with_no_provenance_is_refused_and_never_recomputed(
    evening_store: PanelStore, tmp_path: Path, recomputes: list[str]
) -> None:
    held = filing(evening_store, tmp_path)
    (held.runtime / SURVIVOR_PROVENANCE_DIRECTORY / f"{held.provenance.digest}.json").unlink()

    verdict = check_of(evening_store, held.runtime)(held.record)

    assert (verdict.verdict, verdict.scope, verdict.changes, verdict.cached) == (
        VERDICT_REFUSED,
        NO_PROVENANCE,
        (),
        False,
    )
    assert verdict.reason is not None and batch_digest(held.record.batch) in verdict.reason
    assert recomputes == []


# --- steps 4 to 6: the recompute and what it may be excused by -----------------------------------


def test_a_record_is_verified_on_the_store_it_was_filed_on(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    held = filing(evening_store, tmp_path)

    verdict = check_of(evening_store, held.runtime)(held.record)

    assert verdict == SurvivorRecordVerdict(
        record_id=held.record.record_id,
        session=SIGNAL,
        verdict=VERDICT_VERIFIED,
        scope=None,
        changes=(),
        reason=None,
        cached=False,
    )


def test_a_record_is_verified_after_later_sessions_are_appended_then_served_from_the_cache(
    evening_store: PanelStore, grown_store: PanelStore, tmp_path: Path, recomputes: list[str]
) -> None:
    held = filing(evening_store, tmp_path)
    check = check_of(grown_store, held.runtime, verdicts=verdicts_of(held.runtime))

    first = check(held.record)
    second = check(held.record)

    assert (first.verdict, first.cached) == (VERDICT_VERIFIED, False)
    assert (second.verdict, second.cached) == (VERDICT_VERIFIED, True)
    assert recomputes == [held.record.record_id]
    assert len(kept_files(held.runtime)) == 1


def test_a_superseded_16_30_build_makes_the_record_unverifiable(
    evening_store: PanelStore, restated_store: PanelStore, tmp_path: Path
) -> None:
    held = filing(evening_store, tmp_path)
    before = {factor: output for factor, _build, output in held.provenance.builds}
    after = builds_at_the_signal_instant(restated_store)["reversal_1d/v1"][1]

    verdict = check_of(restated_store, held.runtime, verdicts=verdicts_of(held.runtime))(
        held.record
    )

    named = (
        f"reversal_1d/v1: the {IDENTITY.qualified_key} build at "
        f"{instant(SIGNAL_INDEX).isoformat()} now holds {after}, not {before['reversal_1d/v1']}",
    )
    assert (verdict.verdict, verdict.scope, verdict.changes, verdict.cached) == (
        VERDICT_UNVERIFIABLE,
        RECOMPUTE_DIFFERS,
        named,
        False,
    )
    assert verdict.reason is not None
    # The document the report lists an UNVERIFIABLE record by: what it is, why, and what changed.
    assert verdict.document() == {
        "record_id": held.record.record_id,
        "session": SIGNAL.isoformat(),
        "verdict": "unverifiable",
        "scope": "recompute_differs",
        "changes": list(named),
        "reason": verdict.reason,
        "cached": False,
    }
    assert json.loads(json.dumps(verdict.document())) == verdict.document()
    # Only a verified verdict is kept.
    assert kept_files(held.runtime) == []


def test_a_value_blind_in_place_bar_correction_is_refused_not_unverifiable(
    evening_store: PanelStore, probe_a_store: PanelStore, tmp_path: Path
) -> None:
    # Probe A: the amount of an eligible name cut across its window, no row added, removed or
    # re-dated. The batch moves; the census the provenance holds does not, so nothing can be
    # named and the record is refused by name -- never admitted.
    held = filing(evening_store, tmp_path)
    assert THINNED in held.day.batch.subjects

    verdict = check_of(probe_a_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)

    assert (verdict.verdict, verdict.scope, verdict.changes, verdict.cached) == (
        VERDICT_REFUSED,
        RECOMPUTE_DIFFERS,
        (),
        False,
    )
    assert verdict.reason is not None and "scored again" in verdict.reason
    assert kept_files(held.runtime) == []


def test_a_day_that_cannot_be_scored_again_with_a_partition_gone_is_unverifiable(
    older_year_store: PanelStore, vanished_store: PanelStore, tmp_path: Path
) -> None:
    # A held, halted security's look-back read the 2025 bars, which were rewritten since; the
    # session's bands are gone, so the day cannot be read at all. Only a recompute that could not
    # be made may name what the look-back read -- the scope is passed through, not assumed.
    held = filing(older_year_store, tmp_path, held=(HALTED,), max_carry_sessions=SHORT_CARRY)
    assert [item.year for item in held.provenance.lookback] == [2025]

    verdict = check_of(vanished_store, held.runtime, max_carry_sessions=SHORT_CARRY)(held.record)

    assert (verdict.verdict, verdict.scope, verdict.changes, verdict.cached) == (
        VERDICT_UNVERIFIABLE,
        RECOMPUTE_FAILED,
        (
            f"{PRICE_LIMIT_DATASET}:2026 (no longer stored)",
            f"{DAILY_DATASET}:2025 (rewritten since the filing; a held security's look-back read "
            "it)",
        ),
        False,
    )
    assert verdict.reason is not None and "cannot be scored again" in verdict.reason


def test_a_callers_error_inside_the_recompute_propagates(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    # The index of another registration handed to this registration's check: a caller's error.
    # Turned into "cannot be recomputed", it would be a refusal -- or, against a moved partition,
    # an UNVERIFIABLE admission.
    held = filing(evening_store, tmp_path)
    another = dataclasses.replace(held.provenance, registration_sha256="0" * 64)
    write_survivor_provenance(held.runtime, another)
    check = check_of(evening_store, held.runtime, registration_sha256="0" * 64)

    with pytest.raises(SurvivorCheckArgumentError, match="registration"):
        check(held.record)
    with pytest.raises(SurvivorCheckArgumentError, match="transform"):
        check_of(evening_store, held.runtime, transform=SECOND_TRANSFORM)(held.record)
    with pytest.raises(SurvivorCheckArgumentError, match="transform"):
        check_of(
            evening_store,
            held.runtime,
            transform=SECOND_TRANSFORM,
            verdicts=verdicts_of(held.runtime),
        )(held.record)


@pytest.mark.parametrize(
    "error",
    [
        TypeError("a programming error inside the recompute"),
        PanelCatalogBusyError("the catalog stayed held", side="shared", timeout=1.0),
        SurvivorCheckArgumentError("a caller's error inside the recompute"),
    ],
    ids=["type-error", "catalog-busy", "argument-error"],
)
def test_only_the_named_refusals_mean_a_day_cannot_be_scored_again(
    evening_store: PanelStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    held = filing(evening_store, tmp_path)

    def broken(**_arguments: object) -> object:
        raise error

    monkeypatch.setattr(registration_module, "build_facts_day", broken)
    with pytest.raises(type(error), match=str(error)):
        check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)
    assert kept_files(held.runtime) == []


# --- step 3: the verdict cache -------------------------------------------------------------------


def test_the_cache_hits_after_the_fetches_that_follow_the_records_year(
    evening_store: PanelStore, refetched_store: PanelStore, tmp_path: Path, recomputes: list[str]
) -> None:
    # Once the record's year has closed, nothing of it is written again except the three small
    # through-session datasets, which every fetch restamps: their content hashes all move, their
    # rows through the session do not. A cache keyed by their content hash would never hit while
    # the daily run continues.
    held = filing(evening_store, tmp_path)
    assert (
        check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(
            held.record
        ).cached
        is False
    )
    moved = [
        (item.dataset, item.year)
        for item in held.provenance.inputs
        if refetched_store.partition_content_hash(item.dataset, item.year) != item.content_hash
    ]
    assert moved == [
        (NAMECHANGE_DATASET, 2025),
        (NAMECHANGE_DATASET, 2026),
        (STOCK_BASIC_DATASET, 2025),
        (STOCK_BASIC_DATASET, 2026),
        (TRADING_CALENDAR_DATASET, 2026),
    ]

    verdict = check_of(refetched_store, held.runtime, verdicts=verdicts_of(held.runtime))(
        held.record
    )

    assert (verdict.verdict, verdict.cached) == (VERDICT_VERIFIED, True)
    assert recomputes == [held.record.record_id]
    assert len(kept_files(held.runtime)) == 1


def test_the_cache_misses_while_the_records_own_year_is_still_written(
    evening_store: PanelStore, grown_store: PanelStore, tmp_path: Path, recomputes: list[str]
) -> None:
    # Later sessions of the same year move the bars, bands, halts and factor tiers the day read:
    # content state, so the verdict is not served and the record is scored again.
    held = filing(evening_store, tmp_path)
    check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)

    verdict = check_of(grown_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)

    assert (verdict.verdict, verdict.cached) == (VERDICT_VERIFIED, False)
    assert recomputes == [held.record.record_id] * 2
    assert len(kept_files(held.runtime)) == 2


@pytest.mark.parametrize("corrected", ["probe_a_store", "renamed_store"])
def test_the_cache_misses_after_a_row_is_corrected_in_place(
    request: pytest.FixtureRequest, evening_store: PanelStore, tmp_path: Path, corrected: str
) -> None:
    # A bar's amount (content hash) and a 2025 name (windowed value digest) corrected in place:
    # no row added, removed or re-dated, so a census-keyed cache would serve the kept verdict. The
    # check scores the day again instead, and refuses it.
    store: PanelStore = request.getfixturevalue(corrected)
    held = filing(evening_store, tmp_path)
    check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)
    assert len(kept_files(held.runtime)) == 1

    verdict = check_of(store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)

    assert (verdict.verdict, verdict.scope, verdict.changes, verdict.cached) == (
        VERDICT_REFUSED,
        RECOMPUTE_DIFFERS,
        (),
        False,
    )


def test_the_key_names_the_content_of_every_partition_and_the_rows_of_the_three_small_ones(
    evening_store: PanelStore, renamed_store: PanelStore, tmp_path: Path
) -> None:
    held = filing(evening_store, tmp_path)
    published = instant(SIGNAL_INDEX)

    key = key_of(evening_store, held.provenance)

    states = {(dataset, year): state for dataset, year, state in key.partitions}
    assert set(states) == {(item.dataset, item.year) for item in held.provenance.inputs}
    for item in held.provenance.inputs:
        if item.dataset in (STOCK_BASIC_DATASET, NAMECHANGE_DATASET, TRADING_CALENDAR_DATASET):
            rows = stored_rows_digest(
                evening_store, item.dataset, year=item.year, through=SIGNAL, visible_at=published
            )
            assert states[(item.dataset, item.year)] == f"rows:{rows}"
        else:
            assert states[(item.dataset, item.year)] == f"content:{item.content_hash}"
    assert key.builds == tuple(
        (factor, output) for factor, _build, output in held.provenance.builds
    )
    assert isinstance(key, SurvivorCacheKey)
    assert key.digest == key_of(evening_store, held.provenance).digest
    assert key != key_of(renamed_store, held.provenance)
    with pytest.raises(SurvivorCheckArgumentError, match="transform"):
        key_of(evening_store, held.provenance, transform=SECOND_TRANSFORM)


def test_the_key_names_a_partition_the_windows_reach_that_is_stored_since(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    # The day read no 2026 registry year (the New Year shape): one stored since, with rows on or
    # before the session, is a registry the recompute reads -- the key must move with it.
    held = filing(evening_store, tmp_path)
    without = dataclasses.replace(
        held.provenance,
        inputs=tuple(
            item
            for item in held.provenance.inputs
            if (item.dataset, item.year) != (STOCK_BASIC_DATASET, 2026)
        ),
    )

    key = key_of(evening_store, without)

    assert (STOCK_BASIC_DATASET, 2026) in {(dataset, year) for dataset, year, _ in key.partitions}


def test_an_unreadable_verdict_file_is_a_miss_with_a_warning(
    evening_store: PanelStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    held = filing(evening_store, tmp_path)
    check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)
    (path,) = kept_files(held.runtime)
    path.write_bytes(b"\xff not a verdict")

    with caplog.at_level(logging.WARNING, logger=check_module.__name__):
        verdict = check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(
            held.record
        )

    assert (verdict.verdict, verdict.cached) == (VERDICT_VERIFIED, False)
    assert any(path.name in message for message in caplog.messages)
    # Write-once: the file a check could not read is left as it is, never rewritten.
    assert path.read_bytes() == b"\xff not a verdict"


def test_a_verdict_file_naming_another_verdict_is_a_miss(
    evening_store: PanelStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    held = filing(evening_store, tmp_path)
    check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)
    (path,) = kept_files(held.runtime)
    body = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**body, "verdict": "refused"}), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=check_module.__name__):
        verdict = check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(
            held.record
        )

    assert verdict.cached is False
    assert any(path.name in message for message in caplog.messages)


def test_a_verdict_is_written_once_and_a_different_one_never_overwrites_it(
    evening_store: PanelStore, grown_store: PanelStore, tmp_path: Path
) -> None:
    held = filing(evening_store, tmp_path)
    key = key_of(evening_store, held.provenance)
    grown = key_of(grown_store, held.provenance)
    assert key != grown
    verdicts = verdicts_of(held.runtime)

    verdicts.keep(held.record.record_id, held.provenance.digest, key)
    (path,) = kept_files(held.runtime)
    written = path.read_bytes()
    verdicts_of(held.runtime, at=REPORT_AS_OF + timedelta(days=7)).keep(
        held.record.record_id, held.provenance.digest, key
    )
    verdicts.keep(held.record.record_id, held.provenance.digest, grown)

    assert path.read_bytes() == written
    assert len(kept_files(held.runtime)) == 2
    assert path.name == f"{held.record.record_id}.{held.provenance.digest}.{key.digest}.json"
    assert json.loads(written) == {
        "record_id": held.record.record_id,
        "provenance": held.provenance.digest,
        "key": key.document(),
        "verdict": "verified",
        "verified_at": REPORT_AS_OF.isoformat(),
    }
    assert verdicts.verified_under(held.record.record_id, held.provenance.digest, key)
    assert verdicts.verified_under(held.record.record_id, held.provenance.digest, grown)
    assert not verdicts.verified_under(held.record.record_id, "0" * 64, key)


def test_no_panel_write_lands_while_a_record_is_checked(
    evening_store: PanelStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The check holds one state of the store from its key to its verdict, so a verdict is kept
    # under the key of the state it was reached on. A writer elsewhere waits for the check; a
    # write from inside it -- here, a panel update right after the recompute -- cannot take the
    # catalog at all, and nothing is kept.
    shutil.copytree(evening_store.root, tmp_path / "held")
    store = PanelStore(tmp_path / "held", catalog_lock_timeout=0.2)
    held = filing(store, tmp_path)
    before = {
        (item.dataset, item.year): store.partition_content_hash(item.dataset, item.year)
        for item in held.provenance.inputs
    }
    real = check_module.recompute_survivor_batch

    def then_written(store_: PanelStore, record: PredictionRecord, **arguments: object) -> object:
        again = real(store_, record, **arguments)  # type: ignore[arg-type]
        _grow(store_)
        return again

    monkeypatch.setattr(check_module, "recompute_survivor_batch", then_written)
    with pytest.raises(PanelCatalogBusyError):
        check_of(store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)

    assert kept_files(held.runtime) == []
    assert {
        (item.dataset, item.year): store.partition_content_hash(item.dataset, item.year)
        for item in held.provenance.inputs
    } == before


# --- the check's own settings in the key, the 16:30 bound, the write-once file (E1 review) ------


@pytest.mark.parametrize(
    ("setting", "other"),
    [
        ("exchange", "SSE" if EXCHANGE != "SSE" else "SZSE"),
        ("min_coverage", MIN_COVERAGE / 2),
        ("max_carry_sessions", SHORT_CARRY),
    ],
)
def test_the_key_names_the_checks_own_settings(
    evening_store: PanelStore, tmp_path: Path, setting: str, other: object
) -> None:
    # The recompute reads the day with the registered exchange, coverage floor and carry bound: a
    # verdict kept under one set of them is a verdict about that set, never served to another.
    held = filing(evening_store, tmp_path)

    key = key_of(evening_store, held.provenance)

    assert key.check == (
        ("exchange", EXCHANGE),
        ("max_carry_sessions", str(MAX_CARRY_SESSIONS)),
        ("min_coverage", repr(MIN_COVERAGE)),
    )
    assert key.document()["check"] == [list(item) for item in key.check]
    moved = key_of(evening_store, held.provenance, **{setting: other})
    assert (moved.partitions, moved.builds) == (key.partitions, key.builds)
    assert moved.digest != key.digest


def test_a_verdict_kept_under_one_coverage_floor_is_not_served_under_another(
    evening_store: PanelStore, tmp_path: Path, recomputes: list[str]
) -> None:
    held = filing(evening_store, tmp_path)
    check_of(evening_store, held.runtime, verdicts=verdicts_of(held.runtime))(held.record)

    verdict = check_of(
        evening_store,
        held.runtime,
        verdicts=verdicts_of(held.runtime),
        min_coverage=MIN_COVERAGE / 2,
    )(held.record)

    assert (verdict.verdict, verdict.cached) == (VERDICT_VERIFIED, False)
    assert recomputes == [held.record.record_id] * 2
    assert len(kept_files(held.runtime)) == 2


def test_the_value_digests_16_30_bound_leaves_out_no_row_the_recompute_reads(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    # The key digests `stock_basic`, `namechange` and `trade_cal` through the session at its
    # 16:30, while the recompute reads at the record's custody instant (later): sound only while
    # every row of the three dated on or before the session is visible by that 16:30. The
    # provider's clocks are what make it so -- a registry or a name row is visible at its own
    # date's midnight, a calendar row at the start of its year at the latest -- and the frame's
    # stores hold them so: read later, the three digest the same.
    from openalpha_cn.providers.tushare import _CLOCK_BUILDERS, TUSHARE_DATASETS

    descriptors = {item.dataset: item for item in TUSHARE_DATASETS}
    for dataset in (STOCK_BASIC_DATASET, NAMECHANGE_DATASET, TRADING_CALENDAR_DATASET):
        descriptor = descriptors[dataset]
        build = _CLOCK_BUILDERS[descriptor.clock]
        for day in (date(2026, 1, 1), SIGNAL, date(2026, 12, 31)):
            row = {descriptor.date_field: day.strftime("%Y%m%d")}
            for fetched in (instant(SIGNAL_INDEX) - timedelta(days=400), REFETCHED_AT):
                timeline = build(row, descriptor.date_field, fetched)
                assert timeline.available_time <= session_publication_instant(day), (
                    dataset,
                    day,
                    fetched,
                )

    held = filing(evening_store, tmp_path)
    published = instant(SIGNAL_INDEX)
    custody = registered_at(held.record)
    assert custody > published
    for item in held.provenance.inputs:
        if item.dataset not in (STOCK_BASIC_DATASET, NAMECHANGE_DATASET, TRADING_CALENDAR_DATASET):
            continue
        digests = {
            stored_rows_digest(
                evening_store, item.dataset, year=item.year, through=SIGNAL, visible_at=at
            )
            for at in (published, custody, REFETCHED_AT)
        }
        assert len(digests) == 1, (item.dataset, item.year)


def test_a_verdict_written_meanwhile_by_another_report_is_never_replaced(
    evening_store: PanelStore, tmp_path: Path
) -> None:
    # Two reports keeping the same verdict at once: the one that finds the name taken loses, and
    # the file the other wrote stands as it was written -- no check-then-replace window.
    held = filing(evening_store, tmp_path)
    key = key_of(evening_store, held.provenance)
    name = f"{held.record.record_id}.{held.provenance.digest}.{key.digest}.json"
    directory = held.runtime / SURVIVOR_VERDICT_DIRECTORY
    theirs = b'{"written": "by the other report"}\n'

    def meanwhile() -> datetime:
        # The other report's write lands after this one looked and before it wrote.
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(theirs)
        return REPORT_AS_OF

    FileSurvivorVerdicts(held.runtime, clock=meanwhile).keep(
        held.record.record_id, held.provenance.digest, key
    )

    assert (directory / name).read_bytes() == theirs
    assert sorted(path.name for path in directory.iterdir()) == [name]


def test_a_kept_verdict_is_synced_before_it_takes_its_name_and_the_name_after(
    evening_store: PanelStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A crash must not leave an empty or half-written file under a verdict's name, which would
    # be a miss on every later report: the bytes are synced under a temporary name, take the
    # verdict's name in one step, and the directory is synced after (not on Windows, which
    # cannot open a directory to sync it).
    import os
    import stat

    held = filing(evening_store, tmp_path)
    key = key_of(evening_store, held.provenance)
    events: list[tuple[str, ...]] = []
    real_fsync, real_link = os.fsync, os.link

    def fsync(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        events.append(("fsync", kind))
        real_fsync(descriptor)

    def link(source: object, target: object) -> None:
        events.append(("link",))
        real_link(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "link", link)

    verdicts_of(held.runtime).keep(held.record.record_id, held.provenance.digest, key)

    expected = [("fsync", "file"), ("link",)]
    if os.name != "nt":
        expected.append(("fsync", "directory"))
    assert events == expected
    assert [path.name for path in kept_files(held.runtime)] == [
        f"{held.record.record_id}.{held.provenance.digest}.{key.digest}.json"
    ]
    assert not list((held.runtime / SURVIVOR_VERDICT_DIRECTORY).glob("*.partial"))


# --- the verdict ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"verdict": VERDICT_VERIFIED, "scope": RECOMPUTE_DIFFERS, "reason": None},
        {"verdict": VERDICT_VERIFIED, "scope": None, "reason": "a reason"},
        {"verdict": VERDICT_UNVERIFIABLE, "scope": RECOMPUTE_DIFFERS, "reason": "r"},
        {"verdict": VERDICT_UNVERIFIABLE, "scope": NOT_BOUND, "reason": "r", "changes": ("c",)},
        {"verdict": VERDICT_REFUSED, "scope": None, "reason": "r"},
        {"verdict": VERDICT_REFUSED, "scope": NOT_BOUND, "reason": "r", "changes": ("c",)},
        {"verdict": VERDICT_REFUSED, "scope": NOT_BOUND, "reason": "r", "cached": True},
        {"verdict": "admitted", "scope": None, "reason": None},
    ],
)
def test_a_verdict_holds_together(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SurvivorRecordVerdict(
            **{  # type: ignore[arg-type]
                "record_id": "prd_x",
                "session": SIGNAL,
                "changes": (),
                "cached": False,
                **fields,
            }
        )
