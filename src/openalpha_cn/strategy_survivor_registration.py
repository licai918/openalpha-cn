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

- `builds`: the `transform_manifest_id` and the `processed_observation_digest` of each composite
  factor's processed build stamped at the session's 16:30 -- the one build `build_facts_day` reads
  (`at=`), for the declared transform. The check compares the digest, the build's output: the id
  also moves when a value-identical build is re-run under another code commit;
- `inputs`: every partition **the batch can depend on**, each with its `content_hash` and a digest
  of its **date census within that dataset's window** (the catalog's count of rows per event
  date, for the dates the window holds). The windows are what can move an eligible verdict, and
  no wider (`V2-P7-008` review, Important 1):

  - `suspend_d`, `stk_limit`: **the session alone**, in its year -- `HaltCorpus.state_on` reads
    only the day's row, and the bands are read with `load_price_limits(day=session)`;
  - `daily`: **the `AMOUNT_WINDOW_SESSIONS` sessions ending on the session** (from
    `amount_window_from`, taken from the stored calendar), in their year or two -- an eligible
    name trades on the session and its amount is the mean of these bars; the walk reads further
    back only for a halted name, which is `suspended`, never eligible and never in the
    population the liquidity cut is taken over;
  - `namechange`, `stock_basic`, `trade_cal`: **every date through the session**, in every stored
    year through the session's -- a late rename dated before the session, a late listing or
    termination row and a listing age are all legitimate causes;
  - each factor's processed tier and its manifests: the session's year, recorded and **never
    judged by rows** -- judged by the build at 16:30 (`builds`).

- `lookback`: the older `daily` years -- outside the window -- that a **held** security's
  look-back may have read (recorded only when a held security's row is `suspended`: the walk
  reads back as far as the panel goes for it), with their `content_hash` alone. They cannot move
  the batch; a recompute that could not be made at all may name them;
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
Otherwise the record may be admitted as `strategy_registration.UNVERIFIABLE` -- flagged,
counted -- **only** when this function names a change, and is refused otherwise. What it may name
depends on why the record was not verified (`scope`), because the evidence it needs differs:

- **`recompute_differs`** -- the day was scored again and the scores differ. Only what can move
  the batch may excuse that: **a build whose output changed** (the processed build at the
  session's 16:30 now holds other values, or none), and **a partition restated within its
  window** -- its windowed census
  differs, or it is gone while its window held rows, or one the windows reach is stored since
  with rows inside its window. Nothing outside the windows is ever named here: an older `daily`
  year rebuilt, an intraday halt months before the session, a whole-year `stk_limit` rebuild that
  moved an earlier date's count -- none can move this batch, so none may excuse its mismatch.
- **`recompute_failed`** -- the day could not be scored again at all (a refusal of the store,
  `build_facts_day` or the record vocabulary). Then any partition the day read may be the cause,
  so beside the above it names a partition the day read that is gone, whatever its window held,
  and a `lookback` year gone or rewritten since the filing.

**A caller's error is never an excuse.** The check binds a record before recomputing it
(`survivor_record_is_bound`, the declaration compared whole). The provenance records the
transform the day read (`transform_key`, `transform_id`); handed another, this function and the
recompute raise `SurvivorCheckArgumentError` -- under another transform the 16:30 build is another
tier's or none, which would otherwise be named as a vanished build. The recompute holds the same
for holdings, batch and registration when handed the record's provenance.

**A moved content hash alone is never a change**, and that is the point of the census. A
partition's `content_hash` moves on every rewrite: an append of later sessions, a later factor
build, and every refetch, which re-stamps `ingested_time` (`providers/tushare.py`) -- the registry
rewrites every lifecycle year it holds on each fetch. So the hash is only the cheap half:
unchanged, the partition is byte-identical and nothing else is consulted; moved, its windowed
census says whether rows the batch could depend on appeared, vanished or moved date. An append
adds rows dated after the session and leaves every window as it was. Measured on the frame:
appending four sessions, a rename, a listing and a build moves 13 of the 16 content hashes the
day read and names nothing in either scope, and the recompute equals the record; an ST rename
announced on the signal day (or three sessions before it) and stored the next morning, under the
provider's own clock which the filing instant can read, moves the names' census on that date and
is named; superseding a 16:30 build is named.

**The census is blind twice over, and both blindnesses are stated rather than hidden:**

- **subject-blind**: it counts rows per date, not per security, so a change for one security
  inside a window (a late rename of one name) excuses a mismatch in another's score. The windows
  bound how far that reaches; they do not remove it.
- **value-blind**: a rewrite that changes the values of rows inside a window without adding,
  removing or re-dating any -- a full `panel build` correcting a bar in place under the session's
  own clocks, the review's probe A, an eligible name's amount cut across its window -- keeps every
  census. A record whose recompute differs for that reason is **refused**, never admitted: the
  check fails closed, by name.

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

**Reading at the filing instant and reading at the newest instant answer alike**, which is why
the order above is safe rather than a choice that moves results. Every loader the day reads is
census-reconciled (`panel_ingest._read_visible_event_dated_rows`, the price session reads) and
asks about rows dated on or before the session, each visible from its own clock: a later instant
sees only rows dated after the session, which no window holds, or rows dated within it whose
clock is later -- exactly the rows the filing instant refuses to read past
(`PartitionNotYetKnowableError`) rather than answering short. So the later read differs from the
filing-time read only where the filing-time read could not be made.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, Literal, cast
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
from openalpha_cn.panel_ingest import load_trading_calendar, session_publication_instant
from openalpha_cn.strategy_registration import (
    COMPOSITE_MODEL_NAME,
    RegisteredConfiguration,
    StrategyRegistrationError,
    batch_digest,
    registered_at,
)
from openalpha_cn.strategy_survivor_facts import (
    AMOUNT_WINDOW_SESSIONS,
    FactsDay,
    build_facts_day,
)

__all__ = [
    "RECOMPUTE_DIFFERS",
    "RECOMPUTE_FAILED",
    "SURVIVOR_FAMILY",
    "SURVIVOR_HORIZON",
    "SURVIVOR_PROVENANCE_DIRECTORY",
    "SURVIVOR_PROVENANCE_SCHEMA",
    "SURVIVOR_TIER",
    "ChangeScope",
    "SurvivorCheckArgumentError",
    "SurvivorInputPartition",
    "SurvivorInputProvenance",
    "SurvivorLookbackPartition",
    "SurvivorRegistrationError",
    "recompute_survivor_batch",
    "survivor_batch",
    "survivor_declaration",
    "survivor_feature_ids",
    "survivor_input_provenance",
    "survivor_provenance_changes",
    "survivor_record_is_bound",
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

_THROUGH_SESSION_DATASETS: Final[tuple[str, ...]] = (
    TRADING_CALENDAR_DATASET,
    STOCK_BASIC_DATASET,
    NAMECHANGE_DATASET,
)
"""Windowed through the session, in every stored year through the session's: the calendar and
the names from their first stored year (`_years_through`), the registry beneath its newest year."""
_ON_SESSION_DATASETS: Final[tuple[str, ...]] = (SUSPENSION_DATASET, PRICE_LIMIT_DATASET)
"""Windowed on the session alone, in its year: the halt corpus and the published bands."""
_NO_DATES: Final[str] = hashlib.sha256(b"[]").hexdigest()
"""The census digest of a partition with no row inside its window, or none at all."""

ChangeScope = Literal["recompute_differs", "recompute_failed"]
"""Why a record was not verified, which decides what may excuse it (module docstring)."""
RECOMPUTE_DIFFERS: Final[ChangeScope] = "recompute_differs"
"""The day was scored again and the scores differ: only what can move the batch may excuse it."""
RECOMPUTE_FAILED: Final[ChangeScope] = "recompute_failed"
"""The day could not be scored again: any partition the day read may be the cause."""
_SCOPES: Final[frozenset[str]] = frozenset({RECOMPUTE_DIFFERS, RECOMPUTE_FAILED})


class SurvivorRegistrationError(StrategyRegistrationError):
    """A survivor day cannot be put as a record, or its provenance cannot be taken or read.

    A `StrategyRegistrationError`, so a caller holding P6's refusal holds this one too."""


class SurvivorCheckArgumentError(ValueError):
    """The late-record check was handed inputs that do not belong together -- another scope,
    another transform, another record's provenance or holdings.

    A caller's error, never a change in the store: it is raised, and is not one of the refusals a
    check may treat as "this record cannot be recomputed", so it can never become an excuse."""


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
    """SHA-256 of the catalog's date census within the dataset's window: `[[event date, row
    count], ...]` for every event date the window holds, ascending. No row is read."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorLookbackPartition:
    """An older `daily` year a held security's look-back may have read: outside every window,
    so it cannot move the batch, and recorded by its content hash alone."""

    dataset: str
    year: int
    content_hash: str


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
    transform_key: str
    """The processed tier's transform (`qualified_key`), so a check handed another one refuses
    instead of reading another tier's absence as a change."""
    transform_id: str
    """The same transform's content address: two specs under one key differ here."""
    amount_window_from: date
    """The first of the `AMOUNT_WINDOW_SESSIONS` sessions ending on the session: where the `daily`
    window opens."""
    builds: tuple[tuple[str, str, str], ...]
    """`(factor, transform_manifest_id, processed_observation_digest)` of each composite factor's
    processed build at the session's 16:30, ascending by factor. The id names the build; the
    digest -- over each security's `(subject, coverage, value)`, what the facts read -- is what
    the check compares, since the id also moves on a value-identical re-run under another code
    commit."""
    inputs: tuple[SurvivorInputPartition, ...]
    """Every partition the batch can depend on, ascending by `(dataset, year)`."""
    lookback: tuple[SurvivorLookbackPartition, ...]
    """The older `daily` years a held security's look-back may have read, ascending by year."""
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
            "transform_key": self.transform_key,
            "transform_id": self.transform_id,
            "amount_window_from": self.amount_window_from.isoformat(),
            "builds": [[factor, build, output] for factor, build, output in self.builds],
            "inputs": [
                [item.dataset, item.year, item.content_hash, item.dates_digest]
                for item in self.inputs
            ],
            "lookback": [[item.dataset, item.year, item.content_hash] for item in self.lookback],
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
                transform_key=str(body["transform_key"]),
                transform_id=str(body["transform_id"]),
                amount_window_from=date.fromisoformat(str(body["amount_window_from"])),
                builds=tuple((str(item[0]), str(item[1]), str(item[2])) for item in builds),
                inputs=tuple(
                    SurvivorInputPartition(
                        dataset=str(item[0]),
                        year=int(cast(int, item[1])),
                        content_hash=str(item[2]),
                        dates_digest=str(item[3]),
                    )
                    for item in inputs
                ),
                lookback=tuple(
                    SurvivorLookbackPartition(
                        dataset=str(item[0]),
                        year=int(cast(int, item[1])),
                        content_hash=str(item[2]),
                    )
                    for item in cast(Sequence[Sequence[object]], body["lookback"])
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
    exchange: str,
    recorded_at: datetime,
) -> SurvivorInputProvenance:
    """Fingerprint what `facts`' day read, from the catalog alone, for the record of `batch`.

    `batch` must be the survivor batch of `facts`' session under `registered`; its feature ids
    name the factors whose 16:30 builds are fingerprinted, under `transform`. `exchange` names the
    stored calendar the `daily` window is counted on (the facts' own).
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
    held = frozenset(facts.held)
    # The walk reads past the window only for a security with no bar today; held, it reads as
    # far back as the panel goes. Such a row is `suspended` (a held name halted through the close
    # with a bar is too, which only records more than was read).
    looked_back = any(fact.suspended for fact in facts.facts if fact.ts_code in held)
    with store.reading():
        builds = _builds_at(store, session=session, factors=factors, transform=transform)
        missing = sorted(factor for factor, build in builds.items() if build is None)
        if missing:
            raise SurvivorRegistrationError(
                f"{missing} have no {transform.qualified_key} build at "
                f"{session_publication_instant(session).isoformat()}; the facts of "
                f"{session.isoformat()} were read from one, so the store changed under the run"
            )
        windows = _Windows(
            session=session,
            amount_from=_amount_window_from(store, session=session, exchange=exchange),
            tiers=frozenset(_factor_datasets(factors)),
        )
        inputs = tuple(
            partition
            for dataset, year in _window_partitions(store, windows)
            if (partition := _partition(store, dataset, year, windows)) is not None
        )
        lookback = (
            tuple(
                SurvivorLookbackPartition(dataset=DAILY_DATASET, year=year, content_hash=content)
                for year in store.registered_years(DAILY_DATASET)
                if year < windows.amount_from.year
                and (content := store.partition_content_hash(DAILY_DATASET, year)) is not None
            )
            if looked_back
            else ()
        )
    return SurvivorInputProvenance(
        registration_sha256=registered.registration_sha256,
        config_id=registered.config_id,
        session=session,
        batch_digest=batch_digest(batch),
        recorded_at=recorded_at,
        held=facts.held,
        facts_digest=_facts_digest(facts),
        transform_key=transform.qualified_key,
        transform_id=transform.transform_id,
        amount_window_from=windows.amount_from,
        builds=tuple((factor, *cast(tuple[str, str], builds[factor])) for factor in sorted(builds)),
        inputs=inputs,
        lookback=lookback,
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
    store: PanelStore,
    provenance: SurvivorInputProvenance,
    *,
    transform: FactorTransformSpec,
    scope: ChangeScope,
) -> tuple[str, ...]:
    """What the store no longer holds as the record's day read it, by name; `()` when nothing.

    The late-record check may call a record it could not verify `UNVERIFIABLE` only when this
    names something, and refuses it otherwise. `scope` is why it was not verified --
    `RECOMPUTE_DIFFERS` or `RECOMPUTE_FAILED` -- and decides what may be named (module docstring:
    a changed 16:30 build and a partition restated within its window; and, only for a recompute
    that failed, a partition the day read that is gone and a look-back year gone or rewritten --
    never a content hash that merely moved).
    """
    if scope not in _SCOPES:
        raise SurvivorCheckArgumentError(
            f"scope {scope!r} is neither {RECOMPUTE_DIFFERS!r} nor {RECOMPUTE_FAILED!r}"
        )
    _require_the_records_transform(provenance, transform)
    session = provenance.session
    published = session_publication_instant(session)
    factors = tuple(factor for factor, _build, _output in provenance.builds)
    windows = _Windows(
        session=session,
        amount_from=provenance.amount_window_from,
        tiers=frozenset(_factor_datasets(factors)),
    )
    recorded = {(item.dataset, item.year): item for item in provenance.inputs}
    changes: list[str] = []
    with store.reading():
        now = _builds_at(store, session=session, factors=factors, transform=transform)
        for factor, _build, output in provenance.builds:
            stands = now[factor]
            # The output, not the id: a value-identical re-run under another code commit or
            # `built_at` moves the id and changes no value the facts read.
            if stands is None:
                changes.append(
                    f"{factor}: the {transform.qualified_key} build at {published.isoformat()} "
                    f"is no longer stored (it held {output})"
                )
            elif stands[1] != output:
                changes.append(
                    f"{factor}: the {transform.qualified_key} build at {published.isoformat()} "
                    f"now holds {stands[1]}, not {output}"
                )
        for dataset, year in sorted(set(recorded) | set(_window_partitions(store, windows))):
            if dataset in windows.tiers:
                continue
            before = recorded.get((dataset, year))
            content = store.partition_content_hash(dataset, year)
            if before is not None and content == before.content_hash:
                continue
            current = None if content is None else _partition(store, dataset, year, windows)
            dates = _NO_DATES if current is None else current.dates_digest
            held_rows = _NO_DATES if before is None else before.dates_digest
            if content is None:
                if before is not None and (dates != held_rows or scope == RECOMPUTE_FAILED):
                    changes.append(f"{dataset}:{year} (no longer stored)")
                continue
            if dates == held_rows:
                continue
            reach = windows.describe(dataset)
            changes.append(
                f"{dataset}:{year} (stored since, with rows {reach})"
                if before is None
                else f"{dataset}:{year} (its rows {reach} were restated)"
            )
        if scope == RECOMPUTE_FAILED:
            for item in provenance.lookback:
                content = store.partition_content_hash(item.dataset, item.year)
                if content is None:
                    changes.append(f"{item.dataset}:{item.year} (no longer stored)")
                elif content != item.content_hash:
                    changes.append(
                        f"{item.dataset}:{item.year} (rewritten since the filing; a held "
                        "security's look-back read it)"
                    )
    return tuple(changes)


# --- the recompute -------------------------------------------------------------------------------


def survivor_record_is_bound(
    record: PredictionRecord, book: SurvivorBookConfig, registered: RegisteredConfiguration
) -> bool:
    """Whether `record` is this registered survivor configuration's: its declaration is the one
    `survivor_declaration` gives (P6's `record_is_bound`, for this family). The late-record check
    asks it before recomputing; a record that is not bound is refused, never recomputed."""
    return record.batch.artifact.declaration == survivor_declaration(book, registered)


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
    provenance: SurvivorInputProvenance | None = None,
) -> PredictionBatch:
    """`record`'s day scored again from the store, under the registered configuration.

    Read at the record's `registered_at`, with `held` -- the codes its provenance recorded -- and
    put under the record's own `predicted_at`, so it is compared with the record as
    `(as_of, artifact, predictions)`. At the newest instant the day's partitions became knowable
    only when the filing instant cannot read one of them because a row in it is clocked later
    (module docstring); any other refusal propagates.

    `provenance`, the record's own (the late-record check always has it), is held against the
    other arguments first: another transform, other holdings, another batch or another
    registration is the caller's error and raises `SurvivorCheckArgumentError`, before any read.
    Bind the record first (`survivor_record_is_bound`): a record declared otherwise is not this
    configuration's to recompute.
    """
    if provenance is not None:
        _require_the_records_transform(provenance, transform)
        if tuple(sorted(set(held))) != provenance.held:
            raise SurvivorCheckArgumentError(
                f"the recompute is asked to read with held {sorted(set(held))} and the record's "
                f"provenance read with {list(provenance.held)}; a held code changes the day's "
                "reads, so it is read with the codes the evening had"
            )
        if batch_digest(record.batch) != provenance.batch_digest:
            raise SurvivorCheckArgumentError(
                f"the provenance names the batch {provenance.batch_digest} and the record holds "
                f"{batch_digest(record.batch)}; it is another record's provenance"
            )
        if (provenance.registration_sha256, provenance.config_id) != (
            registered.registration_sha256,
            registered.config_id,
        ):
            raise SurvivorCheckArgumentError(
                f"the provenance was written under the registration {provenance.config_id} "
                f"({provenance.registration_sha256}), not {registered.config_id} "
                f"({registered.registration_sha256})"
            )
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


def _require_the_records_transform(
    provenance: SurvivorInputProvenance, transform: FactorTransformSpec
) -> None:
    """Refuse a transform other than the one the record's day read: under it the 16:30 build
    is another tier's or none, which is the caller's error and never a change."""
    if (transform.qualified_key, transform.transform_id) != (
        provenance.transform_key,
        provenance.transform_id,
    ):
        raise SurvivorCheckArgumentError(
            f"the check is handed the transform {transform.qualified_key} "
            f"({transform.transform_id}) and the record's day read {provenance.transform_key} "
            f"({provenance.transform_id})"
        )


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


@dataclass(frozen=True, slots=True, kw_only=True)
class _Windows:
    """The event dates of each dataset that can move the batch (module docstring)."""

    session: date
    amount_from: date
    tiers: frozenset[str]
    """Each factor's processed tier and its manifests: windowed on the session, never judged."""

    def bounds(self, dataset: str) -> tuple[date | None, date]:
        """The first (`None`: no bound) and last event date of `dataset`'s window."""
        if dataset in _ON_SESSION_DATASETS or dataset in self.tiers:
            return self.session, self.session
        if dataset == DAILY_DATASET:
            return self.amount_from, self.session
        return None, self.session

    def describe(self, dataset: str) -> str:
        first, last = self.bounds(dataset)
        if first is None:
            return f"through {last.isoformat()}"
        if first == last:
            return f"on {last.isoformat()}"
        return f"from {first.isoformat()} through {last.isoformat()}"


def _window_partitions(store: PanelStore, windows: _Windows) -> tuple[tuple[str, int], ...]:
    """Every `(dataset, year)` a window reaches: the through-session datasets in every stored
    year through the session's, the bars in the amount window's year or two, and the rest in the
    session's year."""
    session = windows.session
    keys = {
        (dataset, year)
        for dataset in _THROUGH_SESSION_DATASETS
        for year in store.registered_years(dataset)
        if year <= session.year
    }
    keys.update((DAILY_DATASET, year) for year in range(windows.amount_from.year, session.year + 1))
    keys.update(
        (dataset, session.year) for dataset in (*_ON_SESSION_DATASETS, *sorted(windows.tiers))
    )
    return tuple(sorted(keys))


def _every_partition_read(
    store: PanelStore, *, session: date, factors: Sequence[str]
) -> tuple[tuple[str, int], ...]:
    """Every `(dataset, year)` the facts day may read, windows or not -- a held security's
    look-back reaches every stored bar year: what `_readable_instant` must cover."""
    keys = {
        (dataset, year)
        for dataset in (*_THROUGH_SESSION_DATASETS, DAILY_DATASET)
        for year in store.registered_years(dataset)
        if year <= session.year
    }
    keys.update(
        (dataset, session.year) for dataset in (*_ON_SESSION_DATASETS, *_factor_datasets(factors))
    )
    return tuple(sorted(keys))


def _amount_window_from(store: PanelStore, *, session: date, exchange: str) -> date:
    """The first of the `AMOUNT_WINDOW_SESSIONS` sessions ending on `session`, on the stored
    calendar of the session's year and, when stored, the year before."""
    stored = set(store.registered_years(TRADING_CALENDAR_DATASET))
    if session.year not in stored:
        raise SurvivorRegistrationError(
            f"the panel holds no {TRADING_CALENDAR_DATASET} {session.year}; the facts of "
            f"{session.isoformat()} were counted on it, so the store changed under the run"
        )
    years = tuple(year for year in (session.year - 1, session.year) if year in stored)
    calendar = load_trading_calendar(
        store, exchange=exchange, years=years, as_of=session_publication_instant(session)
    )
    sessions = calendar.trading_days_between(calendar.horizon.first_date, session)
    return sessions[-AMOUNT_WINDOW_SESSIONS:][0]


def _partition(
    store: PanelStore, dataset: str, year: int, windows: _Windows
) -> SurvivorInputPartition | None:
    """One partition as the catalog holds it now, its census cut to the dataset's window, or
    `None` when it is not stored."""
    content = store.partition_content_hash(dataset, year)
    if content is None:
        return None
    coverage = store.read_coverage(dataset, year)
    first, last = windows.bounds(dataset)
    census = (
        []
        if coverage is None
        else [
            [entry.event_date.isoformat(), entry.row_count]
            for entry in sorted(coverage.dates, key=lambda entry: entry.event_date)
            if (first is None or first <= entry.event_date) and entry.event_date <= last
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
) -> dict[str, tuple[str, str] | None]:
    """Each factor's processed build under `transform` stamped at `session`'s 16:30, as its
    `(transform_manifest_id, processed_observation_digest)`, or `None` when there is none."""
    published = session_publication_instant(session)
    found: dict[str, tuple[str, str] | None] = {}
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
                (manifest.transform_manifest_id, manifest.processed_observation_digest)
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
    for dataset, year in _every_partition_read(store, session=session, factors=factors):
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
