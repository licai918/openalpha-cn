"""The survivor book's forward record, its input provenance and its recompute (`V2-P7-008`).

A survivor day ends in a screen: every listed security with the reasons it may not be held, and
the composite of every eligible one (`domain/survivor_screen.py`). This module turns that answer
into the record the daily command files before the next session's call auction -- the only
evidence the survivor configuration produces that is fixed before its outcome is known -- and
supplies the two things the late-record check (`V2-P7-018`) needs to hold such a record to it: a
fingerprint of what the day read, and the day scored again from the store.

It reuses P6's machinery unchanged (`strategy_registration`): `register_prediction` files the
batch, `session_record` finds the session's record or refuses a second answer, `registered_at` is
when a record's numbers were fixed and `batch_digest` names the filing a provenance belongs to.
The record is a **candidate forecast** of a registered research configuration: filing it says
what the configuration scored before the outcome existed, and nothing about whether the score
predicts anything.

## What a record declares (`survivor_declaration`, `survivor_batch`)

- **Declaration.** `name` is `COMPOSITE_MODEL_NAME`, the P6 composite's; `family` is
  `SURVIVOR_FAMILY`, which is what tells the two apart (T4 4.1) -- no reader binds a family it
  does not know, and `session_record` keys on the whole declaration, so a P6 record and a survivor
  record of one session never collide. `horizon` is `SURVIVOR_HORIZON` (`20d`): the survivor book
  decides daily, so the P6 precedent (the rebalance interval) would declare `1d`; the design
  evaluates h=20, and h=5 is read off the same record as a secondary horizon written into the
  `V2-P7-006` protocol (T4 4.2(1)). `feature_version` is the registration's `config_id`, `seed`
  the book's (a registration whose seed differs is refused), `code_commit` the registration's,
  and the hyperparameters name the registration's digest and the book's `config_id`.
- **Artifact.** `feature_ids` are the composite factors at the processed tier
  (`<factor>@processed`), sorted; `parameters` give each the weight the composite takes it under
  (the mean of the oriented values: `1/4` each for four); `training_cutoff` is the session's
  16:30 publication, which is also the batch's `as_of` -- the build every value comes from is
  stamped there, and nothing later is read; `training_example_count` is the number of oriented
  values the scope carries.
- **Scope and scores.** One prediction per **eligible** verdict, scored by its composite, sorted
  by code, no abstention -- the loser tail included, since the survivors are a cut the policy
  takes after the screen, and the record is about the screen's scores. The scope is a function of
  the facts and the book alone: `screen` takes no equity (so a book worth more or less registers
  the same names), and `held` changes no eligible verdict -- a holding is read further back only
  when it has no bar today, which makes it `suspended`, which keeps it out of the scope and out
  of the population the liquidity cut is taken over. `survivor_batch` therefore takes no equity
  and no holdings, and refuses a screen that is not the book's screen of the facts it is given.

## What the day read (`survivor_input_provenance`), without hashing a row

P6's provenance digests every row of every partition a day reads (`stored_rows_digest`); applied
to four processed tiers that is about 50 s a day (T6-T7 3.4). The survivor provenance reads the
**catalog** only:

- `builds`: the `transform_manifest_id` of each composite factor's processed build stamped at the
  session's 16:30 -- the one build `build_facts_day` reads (`at=`), for the declared transform;
- `inputs`: for every partition the day reads -- the calendar, the registry and the names in every
  stored year through the session's, the bars in every stored year through it (the held look-back
  can reach back to the panel's first session; the bound is not on the provenance's arguments),
  the session year's halts and bands, and each factor's processed tier and its manifests -- its
  `content_hash`, and a digest of its **date census through the session** (the catalog's count
  of rows per event date, for every date on or before it);
- `held`, as the facts were read with it (the recompute passes the same codes), and
  `facts_digest`, the facts' canonical bytes, kept **as a diagnostic only**: a code priced before
  its listing row is published reads differently in the evening and later, without moving any
  eligible verdict, so two reads of one day are compared as batches, never as facts.

**What it costs, measured once on one machine** (the synthetic frame of
`tests/unit/test_survivor_facts_panel.py`: 16 partitions, best of seven warm runs; the script is
not committed): 0.32 s, two catalog lookups per partition (about 9 ms each, one DuckDB
connection apiece) and four manifest reads, and no partition read. Its cost grows with the
number of partitions and not with their rows. The frame is too small to show the saving -- the
P6 per-row form over the same 16 partitions took 0.31 s there -- which is the point of the
measurement that motivated this form: on the research store the P6 form hashed every row of the
four processed tiers, about 50 s a day (T6-T7 3.4), and those four tiers are judged here by their
four manifest ids.

## When a mismatch may be called `UNVERIFIABLE` (`survivor_provenance_changes`)

The late-record check scores a record again (`recompute_survivor_batch`); equal, it is verified.
When it differs, the record may be admitted as `strategy_registration.UNVERIFIABLE` -- flagged,
counted -- **only** when this function names a change, and is refused otherwise. It names:

- **a build that changed**: the processed build a composite factor's value was read from, at the
  session's 16:30, is now another `transform_manifest_id`, or none;
- **a partition that was restated**: one the day read whose rows **dated on or before the
  session** are not what they were -- its date census through the session differs, or it is no
  longer stored, or one the day would read now holds such rows and was not stored then.

**A moved content hash alone is not a change**, and that is the point of the census. A partition's
`content_hash` moves on every rewrite: an append of later sessions, a later factor build, and
every refetch, which re-stamps `ingested_time` (`providers/tushare.py`) -- the registry rewrites
every lifecycle year it holds on each fetch. So the hash is only the cheap half: unchanged, the
partition is byte-identical and nothing else is consulted; moved, its census through the session
says whether rows the day could read appeared, vanished or moved date. An append adds rows dated
after the session and leaves that census as it was. A factor tier's own partitions are never
judged by rows -- every build moves them, a rerun later on the session's own date included -- only
by the build at 16:30. Measured on the frame: appending four sessions, a rename, a listing and a
build moves 13 of the 16 content hashes the day read and names no change, and the recompute
equals the record; an ST rename announced on the signal day and stored the next morning (under
the provider's own clock, midnight of the announcement, which the filing instant can read)
moves the names' census on that date from 1 to 2 rows and is named; superseding a 16:30 build is
named by its manifest id.

**What it cannot see**, the price of no per-row digest: a rewrite that changes the values of rows
dated on or before the session without adding, removing or re-dating any -- a full `panel build`
that corrects a bar in place under the session's own clocks -- keeps the census. A record whose
recompute differs for that reason is **refused**, not admitted: the check fails closed, by name.

## The recompute (`recompute_survivor_batch`)

`build_facts_day` at the record's `registered_at`, with the `held` its provenance recorded, then
`screen`, then `survivor_batch` under the record's own `predicted_at` -- the same three steps the
evening took, read from the store, no request. A partition the filing instant cannot read
because a row dated within it is clocked later (`PartitionNotYetKnowableError`, the
`readable_instant` rule of `strategy_registration`) is read once more at the newest instant any
partition the day reads became knowable, and only then; any other refusal is the answer. Appends
do not take that path: since `V2-P4-076` every door these loaders take is row-filtered, and a
row dated after the session is not counted against the filing instant -- measured, the grown
frame reads at the filing instant directly.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, cast
from zoneinfo import ZoneInfo

from openalpha_cn.domain import survivor_screen
from openalpha_cn.domain.alpha_model import (
    AlphaModelArtifact,
    AlphaModelDeclaration,
    Prediction,
    PredictionBatch,
)
from openalpha_cn.domain.daily_prices import DAILY_DATASET
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.survivor_book import ScreenResult, SurvivorBookConfig
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PartitionNotYetKnowableError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    factor_transform_manifest_dataset,
    load_factor_transform_manifests,
    processed_factor_dataset,
)
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.strategy_registration import (
    COMPOSITE_MODEL_NAME,
    RegisteredConfiguration,
    StrategyRegistrationError,
    batch_digest,
    registered_at,
)
from openalpha_cn.strategy_survivor_facts import FactsDay, build_facts_day

__all__ = [
    "SURVIVOR_FAMILY",
    "SURVIVOR_HORIZON",
    "SURVIVOR_PROVENANCE_DIRECTORY",
    "SURVIVOR_PROVENANCE_SCHEMA",
    "SURVIVOR_TIER",
    "SurvivorInputPartition",
    "SurvivorInputProvenance",
    "SurvivorRegistrationError",
    "recompute_survivor_batch",
    "survivor_batch",
    "survivor_declaration",
    "survivor_feature_ids",
    "survivor_input_provenance",
    "survivor_provenance_changes",
    "write_survivor_provenance",
]

SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)

SURVIVOR_FAMILY: Final = "survivor_book_composite/v1"
"""The declared family of every survivor record: what tells it from a P6 composite of the same
`name` (`COMPOSITE_MODEL_NAME`). Not a `MODEL_FAMILIES` row -- nothing is fitted."""
SURVIVOR_HORIZON: Final = "20d"
"""The declared horizon: the outcome window the record is judged over (h=20, T4 4.2(1)); h=5 is a
secondary readout of the same record, named in the `V2-P7-006` protocol."""
SURVIVOR_TIER: Final = "processed"
"""The factor tier the composite reads (`build_facts_day`'s narrow read of the processed tier)."""
SURVIVOR_PROVENANCE_SCHEMA: Final = "daily-survivor-input-provenance/v1"
SURVIVOR_PROVENANCE_DIRECTORY: Final = "daily_survivor_provenance"
"""Under the runtime directory: each survivor record's input provenance, `<digest>.json`,
written once (`write_survivor_provenance`)."""

_HISTORY_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    NAMECHANGE_DATASET,
    DAILY_DATASET,
)
"""Read in every stored year through the session's: the calendar and the names from their first
stored year (`_years_through`), the registry beneath its newest year, and the bars as far back as
a held halted security's last bar."""
_SESSION_DATASETS: Final[tuple[str, ...]] = (SUSPENSION_DATASET, PRICE_LIMIT_DATASET)
"""Read in the session's year only: the halt corpus and the published bands."""
_NO_DATES: Final[str] = hashlib.sha256(b"[]").hexdigest()
"""The census digest of a partition with no row through the session, or none at all."""


class SurvivorRegistrationError(StrategyRegistrationError):
    """A survivor day cannot be put as a record, or its provenance cannot be taken or read.

    A `StrategyRegistrationError`, so a caller holding P6's refusal holds this one too."""


# --- the record ----------------------------------------------------------------------------------


def survivor_feature_ids(book: SurvivorBookConfig) -> tuple[str, ...]:
    """The composite factors at the processed tier, `<factor>@processed`, sorted."""
    return tuple(sorted(f"{factor}@{SURVIVOR_TIER}" for factor in book.composite_factors))


def survivor_declaration(
    book: SurvivorBookConfig, registered: RegisteredConfiguration
) -> AlphaModelDeclaration:
    """The declaration every record of the registered survivor configuration carries."""
    if registered.seed != book.seed:
        raise SurvivorRegistrationError(
            f"the registration declares seed {registered.seed} and its survivor book seed "
            f"{book.seed}; a record declares one seed, and the book's is the one its selection "
            "draws with"
        )
    return AlphaModelDeclaration(
        name=COMPOSITE_MODEL_NAME,
        family=SURVIVOR_FAMILY,
        horizon=SURVIVOR_HORIZON,
        feature_version=registered.config_id,
        seed=book.seed,
        code_commit=registered.code_commit,
        hyperparameters=(
            ("registration_sha256", registered.registration_sha256),
            ("survivor_config_id", book.config_id),
        ),
    )


def survivor_batch(
    *,
    facts: FactsDay,
    screen: ScreenResult,
    book: SurvivorBookConfig,
    registered: RegisteredConfiguration,
    predicted_at: datetime,
) -> PredictionBatch:
    """The day's eligible composites as the batch the daily command registers.

    `screen` must be `domain.survivor_screen.screen(facts.facts, book)` -- recomputed here and
    refused otherwise, so a record is always the book's screen of the facts it names. The module
    docstring says what each field declares and why the scope takes neither equity nor `held`.
    """
    session = facts.session
    published = session_publication_instant(session)
    if predicted_at < published:
        raise SurvivorRegistrationError(
            f"{session.isoformat()} publishes at {published.isoformat()} (16:30 "
            f"Asia/Shanghai) and the batch is predicted at {predicted_at.isoformat()}, before "
            "the facts it scores existed"
        )
    if facts.boards != book.boards:
        raise SurvivorRegistrationError(
            f"the facts were read for the boards {facts.boards} and the book trades "
            f"{book.boards}; they are another book's facts"
        )
    if screen != survivor_screen.screen(facts.facts, book):
        raise SurvivorRegistrationError(
            f"the screen handed in for {session.isoformat()} is not the book's screen of these "
            "facts; a record is the registered configuration's screen of the facts it read"
        )
    eligible = [verdict for verdict in screen.verdicts if verdict.eligible]
    if not eligible:
        raise SurvivorRegistrationError(
            f"no security is eligible on {session.isoformat()}; there is no score to register"
        )
    scope = frozenset(verdict.ts_code for verdict in eligible)
    values = sum(
        1
        for fact in facts.facts
        if fact.ts_code in scope
        for value in fact.oriented_values
        if value is not None
    )
    features = survivor_feature_ids(book)
    weight = 1.0 / len(book.composite_factors)
    artifact = AlphaModelArtifact(
        declaration=survivor_declaration(book, registered),
        feature_ids=features,
        training_cutoff=published,
        training_example_count=values,
        parameters=tuple((feature, weight) for feature in features),
    )
    return PredictionBatch(
        as_of=published,
        predicted_at=predicted_at,
        artifact=artifact,
        predictions=tuple(
            Prediction(ts_code=verdict.ts_code, score=verdict.composite) for verdict in eligible
        ),
    )


# --- the provenance ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorInputPartition:
    """One stored partition the day read, as the catalog stood when the record was filed."""

    dataset: str
    year: int
    content_hash: str
    dates_digest: str
    """SHA-256 of the catalog's date census through the session: `[[event date, row count], ...]`
    for every event date on or before it, ascending. No row is read."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorInputProvenance:
    """What a survivor day read before its record was filed (`V2-P7-008`).

    Written by the daily command before the record, content-addressed and never rewritten
    (`write_survivor_provenance`), for the same reason as P6's `InputProvenance`: a field of the
    record would be a contract change. `batch_digest` names the whole batch it was written for.
    See the module docstring for each field and for what the check makes of them.
    """

    registration_sha256: str
    config_id: str
    session: date
    batch_digest: str
    recorded_at: datetime
    held: tuple[str, ...]
    facts_digest: str
    """The facts' canonical bytes: a diagnostic, never the comparison (module docstring)."""
    builds: tuple[tuple[str, str], ...]
    """`(factor, transform_manifest_id)` of each composite factor's processed build at the
    session's 16:30, ascending by factor."""
    inputs: tuple[SurvivorInputPartition, ...]
    """Every partition the day read, ascending by `(dataset, year)`."""
    schema: str = SURVIVOR_PROVENANCE_SCHEMA

    def document(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "registration_sha256": self.registration_sha256,
            "config_id": self.config_id,
            "session": self.session.isoformat(),
            "batch_digest": self.batch_digest,
            "recorded_at": self.recorded_at.isoformat(),
            "held": list(self.held),
            "facts_digest": self.facts_digest,
            "builds": [[factor, build] for factor, build in self.builds],
            "inputs": [
                [item.dataset, item.year, item.content_hash, item.dates_digest]
                for item in self.inputs
            ],
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.document(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_document(cls, body: Mapping[str, object]) -> SurvivorInputProvenance:
        schema = body.get("schema")
        if schema != SURVIVOR_PROVENANCE_SCHEMA:
            raise SurvivorRegistrationError(
                f"not a {SURVIVOR_PROVENANCE_SCHEMA} document (its schema is {schema!r})"
            )
        try:
            builds = cast(Sequence[Sequence[object]], body["builds"])
            inputs = cast(Sequence[Sequence[object]], body["inputs"])
            return cls(
                registration_sha256=str(body["registration_sha256"]),
                config_id=str(body["config_id"]),
                session=date.fromisoformat(str(body["session"])),
                batch_digest=str(body["batch_digest"]),
                recorded_at=datetime.fromisoformat(str(body["recorded_at"])),
                held=tuple(str(code) for code in cast(Sequence[object], body["held"])),
                facts_digest=str(body["facts_digest"]),
                builds=tuple((str(item[0]), str(item[1])) for item in builds),
                inputs=tuple(
                    SurvivorInputPartition(
                        dataset=str(item[0]),
                        year=int(cast(int, item[1])),
                        content_hash=str(item[2]),
                        dates_digest=str(item[3]),
                    )
                    for item in inputs
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise SurvivorRegistrationError(
                f"not a {SURVIVOR_PROVENANCE_SCHEMA} document this module can read ({error!r})"
            ) from error


def survivor_input_provenance(
    store: PanelStore,
    *,
    facts: FactsDay,
    batch: PredictionBatch,
    registered: RegisteredConfiguration,
    transform: FactorTransformSpec,
    recorded_at: datetime,
) -> SurvivorInputProvenance:
    """Fingerprint what `facts`' day read, from the catalog alone, for the record of `batch`.

    `batch` must be the survivor batch of `facts`' session under `registered`; its feature ids
    name the factors whose 16:30 builds are fingerprinted, under `transform`.
    """
    session = facts.session
    declaration = batch.artifact.declaration
    if declaration.family != SURVIVOR_FAMILY or declaration.feature_version != (
        registered.config_id
    ):
        raise SurvivorRegistrationError(
            f"the batch declares {declaration.family} under {declaration.feature_version}, not "
            f"{SURVIVOR_FAMILY} under {registered.config_id}; this provenance is a survivor "
            "record's"
        )
    if batch.as_of != session_publication_instant(session):
        raise SurvivorRegistrationError(
            f"the batch stands at {batch.as_of.isoformat()} and the facts are of "
            f"{session.isoformat()}; a provenance names the day its record scores"
        )
    factors = _factors_of(batch.artifact.feature_ids)
    with store.reading():
        builds = _builds_at(store, session=session, factors=factors, transform=transform)
        missing = sorted(factor for factor, build in builds.items() if build is None)
        if missing:
            raise SurvivorRegistrationError(
                f"{missing} have no {transform.qualified_key} build at "
                f"{session_publication_instant(session).isoformat()}; the facts of "
                f"{session.isoformat()} were read from one, so the store changed under the run"
            )
        inputs = tuple(
            partition
            for dataset, year in _read_partitions(store, session=session, factors=factors)
            if (partition := _partition(store, dataset, year, session=session)) is not None
        )
    return SurvivorInputProvenance(
        registration_sha256=registered.registration_sha256,
        config_id=registered.config_id,
        session=session,
        batch_digest=batch_digest(batch),
        recorded_at=recorded_at,
        held=facts.held,
        facts_digest=_facts_digest(facts),
        builds=tuple((factor, cast(str, builds[factor])) for factor in sorted(builds)),
        inputs=inputs,
    )


def write_survivor_provenance(runtime_dir: Path, provenance: SurvivorInputProvenance) -> Path:
    """File a survivor record's provenance before the record, under its own digest.

    Written once and never rewritten: a file already there is left as it is when it says the
    same, and refused when it says something else (or is not UTF-8 text at all)."""
    path = runtime_dir / SURVIVOR_PROVENANCE_DIRECTORY / f"{provenance.digest}.json"
    text = json.dumps(provenance.document(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if path.is_file():
        try:
            held = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            held = None
        if held != text:
            raise SurvivorRegistrationError(
                f"{path} is held and says something else; a provenance is written once"
            )
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return path


def survivor_provenance_changes(
    store: PanelStore, provenance: SurvivorInputProvenance, *, transform: FactorTransformSpec
) -> tuple[str, ...]:
    """What the store no longer holds as the record's day read it, by name; `()` when nothing.

    The late-record check may call a record whose recompute differs `UNVERIFIABLE` only when
    this names something, and refuses it otherwise (module docstring: a changed 16:30 build, or a
    partition restated through the session -- never a content hash that merely moved).
    """
    session = provenance.session
    published = session_publication_instant(session)
    factors = tuple(factor for factor, _build in provenance.builds)
    tiers = frozenset(_factor_datasets(factors))
    recorded = {(item.dataset, item.year): item for item in provenance.inputs}
    changes: list[str] = []
    with store.reading():
        now = _builds_at(store, session=session, factors=factors, transform=transform)
        for factor, build in provenance.builds:
            if now[factor] != build:
                changes.append(
                    f"{factor}: the {transform.qualified_key} build at {published.isoformat()} "
                    f"is {now[factor] or 'not stored'}, not {build}"
                )
        keys = set(recorded) | set(_read_partitions(store, session=session, factors=factors))
        for dataset, year in sorted(keys):
            if dataset in tiers:
                continue
            before = recorded.get((dataset, year))
            content = store.partition_content_hash(dataset, year)
            if before is not None and content == before.content_hash:
                continue
            if content is None:
                if before is not None:
                    changes.append(f"{dataset}:{year} (no longer stored)")
                continue
            current = _partition(store, dataset, year, session=session)
            dates = _NO_DATES if current is None else current.dates_digest
            if dates == (_NO_DATES if before is None else before.dates_digest):
                continue
            changes.append(
                f"{dataset}:{year} (stored since, with rows through {session.isoformat()})"
                if before is None
                else f"{dataset}:{year} (its rows through {session.isoformat()} were restated)"
            )
    return tuple(changes)


# --- the recompute -------------------------------------------------------------------------------


def recompute_survivor_batch(
    store: PanelStore,
    record: PredictionRecord,
    *,
    book: SurvivorBookConfig,
    transform: FactorTransformSpec,
    exchange: str,
    min_coverage: float,
    max_carry_sessions: int,
    held: Collection[str],
    registered: RegisteredConfiguration,
) -> PredictionBatch:
    """`record`'s day scored again from the store, under the registered configuration.

    Read at the record's `registered_at`, with `held` -- the codes its provenance recorded -- and
    put under the record's own `predicted_at`, so it is compared with the record as
    `(as_of, artifact, predictions)`. At the newest instant the day's partitions became knowable
    only when the filing instant cannot read one of them because a row in it is clocked later
    (module docstring); any other refusal propagates.
    """
    session = record.batch.as_of.astimezone(SHANGHAI).date()
    filed = registered_at(record)

    def scored_at(at: datetime) -> PredictionBatch:
        facts = build_facts_day(
            store=store,
            session=session,
            as_of=at,
            config=book,
            factor_ids=book.composite_factors,
            transform=transform,
            exchange=exchange,
            min_coverage=min_coverage,
            max_carry_sessions=max_carry_sessions,
            held=held,
        )
        return survivor_batch(
            facts=facts,
            screen=survivor_screen.screen(facts.facts, book),
            book=book,
            registered=registered,
            predicted_at=record.batch.predicted_at,
        )

    try:
        return scored_at(filed)
    except (RuntimeError, ValueError) as error:
        if not _not_yet_knowable(error):
            raise
        readable = _readable_instant(
            store, session=session, factors=tuple(sorted(book.composite_factors))
        )
        if readable is None or readable <= filed:
            raise
    return scored_at(readable)


# --- helpers -------------------------------------------------------------------------------------


def _factors_of(feature_ids: Sequence[str]) -> tuple[str, ...]:
    """The factors a survivor artifact's `<factor>@processed` feature ids name, sorted."""
    suffix = f"@{SURVIVOR_TIER}"
    foreign = [feature for feature in feature_ids if not feature.endswith(suffix)]
    if foreign:
        raise SurvivorRegistrationError(
            f"{foreign} are not {SURVIVOR_TIER}-tier features; a survivor record reads its "
            f"factors at the {SURVIVOR_TIER} tier"
        )
    return tuple(sorted(feature.removesuffix(suffix) for feature in feature_ids))


def _factor_datasets(factors: Sequence[str]) -> tuple[str, ...]:
    """Each factor's processed tier and its transform manifests."""
    found: list[str] = []
    for factor in factors:
        definition = FACTOR_DEFINITIONS.get(factor)
        found += [
            processed_factor_dataset(definition),
            factor_transform_manifest_dataset(definition),
        ]
    return tuple(found)


def _read_partitions(
    store: PanelStore, *, session: date, factors: Sequence[str]
) -> tuple[tuple[str, int], ...]:
    """Every `(dataset, year)` the day reads: the history datasets in every stored year through
    the session's, and the rest in the session's year."""
    keys = {
        (dataset, year)
        for dataset in _HISTORY_DATASETS
        for year in store.registered_years(dataset)
        if year <= session.year
    }
    keys.update(
        (dataset, session.year) for dataset in (*_SESSION_DATASETS, *_factor_datasets(factors))
    )
    return tuple(sorted(keys))


def _partition(
    store: PanelStore, dataset: str, year: int, *, session: date
) -> SurvivorInputPartition | None:
    """One partition as the catalog holds it now, or `None` when it is not stored."""
    content = store.partition_content_hash(dataset, year)
    if content is None:
        return None
    coverage = store.read_coverage(dataset, year)
    census = (
        []
        if coverage is None
        else [
            [entry.event_date.isoformat(), entry.row_count]
            for entry in sorted(coverage.dates, key=lambda entry: entry.event_date)
            if entry.event_date <= session
        ]
    )
    return SurvivorInputPartition(
        dataset=dataset,
        year=year,
        content_hash=content,
        dates_digest=hashlib.sha256(
            json.dumps(census, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    )


def _builds_at(
    store: PanelStore, *, session: date, factors: Sequence[str], transform: FactorTransformSpec
) -> dict[str, str | None]:
    """Each factor's processed build under `transform` stamped at `session`'s 16:30, by its
    `transform_manifest_id`, or `None` when there is none."""
    published = session_publication_instant(session)
    found: dict[str, str | None] = {}
    for factor in factors:
        definition = FACTOR_DEFINITIONS.get(factor)
        if (
            store.partition_content_hash(
                factor_transform_manifest_dataset(definition), session.year
            )
            is None
        ):
            found[factor] = None
            continue
        builds = sorted(
            {
                manifest.transform_manifest_id
                for manifest in load_factor_transform_manifests(
                    store, definition, years=(session.year,), as_of=published
                )
                if manifest.as_of == published and manifest.transform_id == transform.transform_id
            }
        )
        if len(builds) > 1:
            raise SurvivorRegistrationError(
                f"{factor} holds {len(builds)} {transform.qualified_key} builds at "
                f"{published.isoformat()} ({builds}); a day reads one"
            )
        found[factor] = builds[0] if builds else None
    return found


def _facts_digest(facts: FactsDay) -> str:
    body = {
        "session": facts.session.isoformat(),
        "facts": [fact.model_dump(mode="json") for fact in facts.facts],
        "notes": dataclasses.asdict(facts.notes),
        "boards": list(facts.boards),
        "universe_size": facts.universe_size,
        "scored_size": facts.scored_size,
        "held": list(facts.held),
        "held_status": [status.model_dump(mode="json") for status in facts.held_status],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _readable_instant(
    store: PanelStore, *, session: date, factors: Sequence[str]
) -> datetime | None:
    """The newest instant any partition the day reads became knowable, or `None` when none is
    stored: at or after it every one of them can be read (`strategy_registration`'s rule)."""
    newest: datetime | None = None
    for dataset, year in _read_partitions(store, session=session, factors=factors):
        coverage = store.read_coverage(dataset, year)
        if coverage is not None and (newest is None or coverage.max_available_time > newest):
            newest = coverage.max_available_time
    return newest


def _not_yet_knowable(error: BaseException) -> bool:
    """Whether `error` is, at its root, a partition holding a row dated within reach and
    knowable only after the instant asked (`PartitionNotYetKnowableError`)."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, PartitionNotYetKnowableError):
            return True
        seen = seen.__cause__
    return False
