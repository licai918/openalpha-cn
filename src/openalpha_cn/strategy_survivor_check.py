"""The survivor book's late-record check (`V2-P7-018`).

A survivor record is filed by the daily command at about 18:30, after the 16:30 publication its
scores stand at, so nothing in the record witnesses that it carries only what was knowable then.
Before the weekly report counts a record it is held to this check, record by record -- every
bound record, mature or not, so the share of UNVERIFIABLE records is over all of them. There is no
sampling: an unchecked record is not evidence.

## The check (`SurvivorRecordCheck.__call__`), in order

1. **Bound first** (`survivor_record_is_bound`). A record the registered survivor configuration
   does not declare is refused and never recomputed: scored again it would differ, and against a
   moved input it would otherwise be admitted as UNVERIFIABLE.
2. **Its provenance**, by `(session, batch_digest(record.batch))`, from the index of this
   registration's `RT/daily_survivor_provenance/*.json` (`survivor_provenance_index`). The index
   skips another registration's file *before* reading it (`read_survivor_provenance` refuses
   one, so reading every file would refuse the report for a file that is not its business), and
   refuses two files naming one filing -- neither can be told to be the one its run wrote. A
   record with no provenance is refused: there is nothing to say what its day read. One file of
   this registration's -- or one that does not say whose it is -- that does not read back refuses
   the whole index, and so the whole report, not one record: it could be any record's filing,
   and no record can be told not to need it (fail closed; the design is silent here).
3. **The verdict cache**: a verified verdict kept under the key the store gives now
   (`survivor_cache_key`) is served, `cached=True`. Nothing else is ever kept.
4. **The recompute** (`recompute_survivor_batch`, with the provenance): the day scored again from
   the store at the record's custody instant, with the holdings its run read with, compared as
   `(as_of, artifact, predictions)`. Only `SURVIVOR_RECOMPUTE_REFUSALS` means "the day cannot be
   scored again" (scope `recompute_failed`); a `SurvivorCheckArgumentError`, a `TypeError`, a
   busy catalog -- anything else -- propagates.
5. **Equal: verified**, and kept.
6. **Otherwise** `survivor_provenance_changes` under the scope: something named -- UNVERIFIABLE,
   admitted and listed with its changes and scope; nothing named -- **refused**. A value-blind
   in-place correction (a bar's amount cut across its window, every row count unchanged) ends
   here by design: the census cannot see it, and the check fails closed, by name. So does a
   factor tier's partition gone while its 16:30 build still stands: the provenance names the
   factor tiers by their build alone (D2), so a day that cannot be scored again for that reason
   names no change and is refused, not UNVERIFIABLE -- D2's rule, kept, failing closed.

## The verdict cache is keyed by content state, never by census (`survivor_cache_key`)

A census-keyed cache would keep a verdict across an in-place correction -- the census is exactly
what such a correction leaves alone. So the key is what the recompute's inputs hold now:

- every partition of the provenance's `inputs` and `lookback`, and every one its windows reach
  now (a partition stored since, inside a window, is a read the recompute makes), by its
  **content hash** -- `absent` when it is not stored;
- **except** `stock_basic`, `namechange` and `trade_cal`, keyed by a **windowed value digest**:
  `panel_ingest.stored_rows_digest(..., through=session, visible_at=<the session's 16:30>)`, the
  form P6's provenance takes. These three are small, read through the session, and restamped on
  every fetch: the registry rewrites every lifecycle year it holds on each fetch, and a refetch
  moves `ingested_time`, which the content hash covers and the value digest leaves out. Keyed by
  their content hash, the cache would never hit while the daily run continues (design decision
  13). The 16:30 bound leaves nothing out that the recompute reads, although the recompute
  reads at the record's custody instant (or later, on its fallback): the provider makes a row of
  these three visible at its own date's midnight (a calendar row at its year's start, or when it
  was fetched if that is earlier), so a row dated on or before the session is visible by its
  16:30. That is an assumption about the provider's clocks, and a test holds it
  (`test_the_value_digests_16_30_bound_leaves_out_no_row_the_recompute_reads`);
- the **output digest** of each composite factor's processed build stamped at the session's
  16:30, under the record's transform -- the output and not the manifest id, which also moves on
  a value-identical re-run;
- the **check's own settings** the recompute reads the day with -- `exchange`, `min_coverage`,
  `max_carry_sessions` -- which the provenance does not record: the registered configuration's,
  so the same for every record of one registration, and in the key so that a verdict kept under
  one set of them is never served to a check run with another.

So a kept verdict is served for every record whose year has closed -- after it, only those three
datasets are written in that year, and only their stamps move -- and never after any in-place
correction of a row within a window: a bar's or a band's moves its partition's content hash, a
name's, a listing's or a session's moves the value digest. While the record's own year is still
being written (its bars, bands, halts and factor tiers gain sessions), the key moves and the
record is scored again: correct, and the price of not hashing a year of bars per record.

The check holds the catalog's shared side (`PanelStore.reading()`) from the key to the verdict,
as `build_facts_day` holds it across its own read: a panel write lands wholly before a record's
check or wholly after it, so a verdict is always kept under the key of the state it was reached
on. A writer waits for at most one record's check.

**A cache, inside the threat model and no further** (P6's `FileVerdicts`): it saves scoring a
record again whose inputs have not moved; it is not evidence. Verdict files are local and
write-once, `RT/reports/survivor_record_verdicts/<record>.<provenance>.<key digest>.json`, apart
from P6's; one that does not read back as the verified verdict its name promises is a miss with a
warning, never rewritten. A verdict is written under a temporary name of its own writer's, synced,
and linked to its name, which fails when the name is taken -- of two reports keeping one verdict
at once, the second loses and the first's file stands -- and the directory is synced after (not
on Windows, which cannot open a directory to sync it): a crash leaves at most a `.partial`, never
an empty file under a verdict's name, which would be a miss on every later report. Someone who
controls this disk can write a verdict as easily as a record, and nothing local defends against
that.

## What a check costs, measured once on one machine

On the synthetic frame of `tests/unit/test_strategy_survivor_check.py` -- the evening grown by
later sessions, 16 partitions in the key, about 40 securities; `PYTHONDONTWRITEBYTECODE=1`, warm,
best of five; the script is not committed: a check with no verdict cache (the recompute alone)
0.32 s; a first check with the cache (key, recompute, keep) 0.53 s; a check served from the
cache 0.17 s with the small datasets' row hashes already held by the check, 0.19 s without --
the key is catalog lookups, four manifest reads and the value digests of five small partitions.
At 2026 scale the design memo estimates a recompute at about 2.1 s (one facts day) and a key at
0.6-1.2 s, so a first check costs about 2.7-3.3 s and a served one the key alone.

## Wording

The check says whether a record is what the registered configuration scored from what was
knowable when it was filed. It says nothing about whether the scores predict anything: a verified
record is a candidate forecast of a registered research configuration, not a performance claim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, Protocol, get_args

from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.survivor_book import SurvivorBookConfig
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    RowDigestCache,
    session_publication_instant,
    stored_rows_digest,
)
from openalpha_cn.strategy_registration import (
    RegisteredConfiguration,
    StrategyRegistrationError,
    batch_digest,
)
from openalpha_cn.strategy_survivor_registration import (
    _THROUGH_SESSION_DATASETS,
    RECOMPUTE_DIFFERS,
    RECOMPUTE_FAILED,
    SHANGHAI,
    SURVIVOR_PROVENANCE_DIRECTORY,
    SURVIVOR_RECOMPUTE_REFUSALS,
    ChangeScope,
    SurvivorInputProvenance,
    SurvivorRegistrationError,
    _builds_at,
    _factor_datasets,
    _require_the_records_transform,
    _window_partitions,
    _Windows,
    read_survivor_provenance,
    recompute_survivor_batch,
    survivor_provenance_changes,
    survivor_record_is_bound,
)

__all__ = [
    "NOT_BOUND",
    "NO_PROVENANCE",
    "SURVIVOR_VERDICT_DIRECTORY",
    "VERDICT_REFUSED",
    "VERDICT_UNVERIFIABLE",
    "VERDICT_VERIFIED",
    "FileSurvivorVerdicts",
    "SurvivorCacheKey",
    "SurvivorCheckError",
    "SurvivorRecordCheck",
    "SurvivorRecordVerdict",
    "SurvivorVerdict",
    "SurvivorVerdictCache",
    "VerdictScope",
    "survivor_cache_key",
    "survivor_provenance_index",
]

_LOG: Final = logging.getLogger(__name__)

SURVIVOR_VERDICT_DIRECTORY: Final = "reports/survivor_record_verdicts"
"""Under the runtime directory: each verified survivor record's verdict,
`<record>.<provenance>.<key digest>.json`, written once; apart from P6's
`reports/record_verdicts`."""

SurvivorVerdict = Literal["verified", "unverifiable", "refused"]
VERDICT_VERIFIED: Final[SurvivorVerdict] = "verified"
"""Scored again from the store, the record is what it says: counted."""
VERDICT_UNVERIFIABLE: Final[SurvivorVerdict] = "unverifiable"
"""Not scored again equal, and an input it read was corrected since: admitted -- it is an on-time
record in the append-only store -- flagged, counted and listed with its changes, never silently
(P6's `strategy_registration.UNVERIFIABLE`)."""
VERDICT_REFUSED: Final[SurvivorVerdict] = "refused"
"""Not this registration's, without a provenance, or not scored again equal with nothing to point
to: excluded and listed with its reason."""

VerdictScope = Literal["not_bound", "no_provenance", "recompute_differs", "recompute_failed"]
"""Why a record was not verified: before the recompute (`NOT_BOUND`, `NO_PROVENANCE`), or what the
recompute found (`RECOMPUTE_DIFFERS`, `RECOMPUTE_FAILED`)."""
NOT_BOUND: Final[VerdictScope] = "not_bound"
NO_PROVENANCE: Final[VerdictScope] = "no_provenance"
_RECOMPUTED: Final[frozenset[str]] = frozenset({RECOMPUTE_DIFFERS, RECOMPUTE_FAILED})

_VALUE_DIGESTED: Final[frozenset[str]] = frozenset(_THROUGH_SESSION_DATASETS)
"""`stock_basic`, `namechange`, `trade_cal`: keyed by their rows through the session, not by their
content hash (module docstring)."""
_ABSENT: Final = "absent"


class SurvivorCheckError(StrategyRegistrationError):
    """The check cannot be run as asked: this registration's provenance cannot be indexed -- a
    file that cannot be read back, or two files naming one filing. The report is refused."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorRecordVerdict:
    """What the check says of one record, and why.

    `verified`: `scope`, `reason` are `None` and `changes` empty; `cached` says whether a kept
    verdict was served. `unverifiable`: the recompute's scope, the `changes` that excuse it (never
    empty) and the `reason` it was not verified. `refused`: the scope it was refused at, the
    `reason`, no `changes`. Any other combination is refused on construction.
    """

    record_id: str
    session: date
    verdict: SurvivorVerdict
    scope: VerdictScope | None
    changes: tuple[str, ...] = ()
    reason: str | None = None
    cached: bool = False

    def __post_init__(self) -> None:
        if self.verdict not in get_args(SurvivorVerdict):
            raise ValueError(f"verdict {self.verdict!r} is not one of {get_args(SurvivorVerdict)}")
        if self.scope is not None and self.scope not in get_args(VerdictScope):
            raise ValueError(f"scope {self.scope!r} is not one of {get_args(VerdictScope)}")
        verified = self.verdict == VERDICT_VERIFIED
        if verified != (self.scope is None) or verified != (self.reason is None):
            raise ValueError(
                f"a {self.verdict} verdict "
                + ("has no scope and no reason" if verified else "names its scope and reason")
            )
        if self.cached and not verified:
            raise ValueError("only a verified verdict is ever served from the cache")
        unverifiable = self.verdict == VERDICT_UNVERIFIABLE
        if unverifiable != bool(self.changes):
            raise ValueError(
                "an unverifiable verdict names the changes that excuse it; no other verdict does"
            )
        if unverifiable and self.scope not in _RECOMPUTED:
            raise ValueError(
                f"an unverifiable verdict is reached by a recompute, not at scope {self.scope!r}"
            )

    def document(self) -> dict[str, object]:
        """The verdict as the report lists it."""
        return {
            "record_id": self.record_id,
            "session": self.session.isoformat(),
            "verdict": self.verdict,
            "scope": self.scope,
            "changes": list(self.changes),
            "reason": self.reason,
            "cached": self.cached,
        }


# --- the provenance index ------------------------------------------------------------------------


def survivor_provenance_index(
    runtime_dir: Path, *, registration_sha256: str
) -> Mapping[tuple[date, str], SurvivorInputProvenance]:
    """This registration's survivor provenance under `runtime_dir`, by `(session, batch digest)`.

    A file written under another registration is skipped before it is read
    (`read_survivor_provenance` refuses one); every other file is read whole and refused when it
    is not what its name says. Refused with `SurvivorCheckError`: a file whose registration
    cannot be told, one of this registration's that does not read back, and two naming one
    filing. No directory is an empty index.
    """
    directory = runtime_dir / SURVIVOR_PROVENANCE_DIRECTORY
    held: dict[tuple[date, str], tuple[SurvivorInputProvenance, Path]] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        if _written_under_another(path, registration_sha256):
            continue
        try:
            provenance = read_survivor_provenance(path, registration_sha256=registration_sha256)
        except SurvivorRegistrationError as error:
            raise SurvivorCheckError(
                f"{path.name} is not a survivor provenance this check can read ({error})"
            ) from error
        key = (provenance.session, provenance.batch_digest)
        if key in held:
            raise SurvivorCheckError(
                f"two provenance files name the batch {provenance.batch_digest} of "
                f"{provenance.session.isoformat()}: {held[key][1].name} and {path.name}; a filing "
                "has one, and neither can be told to be the one its run wrote"
            )
        held[key] = (provenance, path)
    return MappingProxyType({key: provenance for key, (provenance, _path) in held.items()})


def _written_under_another(path: Path, registration_sha256: str) -> bool:
    """Whether `path` says plainly that another registration wrote it. A file that does not say
    -- not UTF-8 JSON, not an object, no `registration_sha256` string -- is not skipped."""
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    stated = body.get("registration_sha256") if type(body) is dict else None
    return type(stated) is str and stated != registration_sha256


# --- the cache key -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorCacheKey:
    """What a record's recompute reads, as the store holds it now (module docstring).

    `partitions`: `(dataset, year, state)` ascending, `state` being `content:<hash>`,
    `rows:<windowed value digest>` for the three small through-session datasets, or `absent`.
    `builds`: `(factor, output digest)` of each composite factor's 16:30 build, or `absent`.
    `check`: the check's own settings the recompute reads the day with, `(name, text)` by name
    -- `exchange`, `max_carry_sessions`, `min_coverage` (its `repr`).
    """

    partitions: tuple[tuple[str, int, str], ...]
    builds: tuple[tuple[str, str], ...]
    check: tuple[tuple[str, str], ...]

    def document(self) -> dict[str, object]:
        return {
            "partitions": [[dataset, year, state] for dataset, year, state in self.partitions],
            "builds": [[factor, output] for factor, output in self.builds],
            "check": [[name, value] for name, value in self.check],
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.document(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def survivor_cache_key(
    store: PanelStore,
    provenance: SurvivorInputProvenance,
    *,
    transform: FactorTransformSpec,
    exchange: str,
    min_coverage: float,
    max_carry_sessions: int,
    cache: RowDigestCache | None = None,
) -> SurvivorCacheKey:
    """The key a verified verdict of the record `provenance` was written for is kept under.

    `transform` must be the one the record's day read (`SurvivorCheckArgumentError` otherwise).
    `exchange`, `min_coverage` and `max_carry_sessions` are the settings the check recomputes the
    day with, which the provenance does not record: a verdict is about them too (module
    docstring). `cache` shares the row hashes of a partition state between records
    (`stored_rows_digest`).
    """
    _require_the_records_transform(provenance, transform)
    session = provenance.session
    published = session_publication_instant(session)
    factors = tuple(factor for factor, _build, _output in provenance.builds)
    windows = _Windows(
        session=session,
        amount_from=provenance.amount_window_from,
        tiers=frozenset(_factor_datasets(factors)),
    )
    with store.reading():
        keys = sorted(
            {(item.dataset, item.year) for item in provenance.inputs}
            | {(item.dataset, item.year) for item in provenance.lookback}
            | set(_window_partitions(store, windows))
        )
        partitions = tuple(
            (dataset, year, _state(store, dataset, year, session, published, cache))
            for dataset, year in keys
        )
        builds = _builds_at(store, session=session, factors=factors, transform=transform)
    return SurvivorCacheKey(
        partitions=partitions,
        builds=tuple(
            (factor, _ABSENT if build is None else build[1])
            for factor, build in sorted(builds.items())
        ),
        check=(
            ("exchange", exchange),
            ("max_carry_sessions", str(max_carry_sessions)),
            ("min_coverage", repr(min_coverage)),
        ),
    )


def _state(
    store: PanelStore,
    dataset: str,
    year: int,
    session: date,
    published: datetime,
    cache: RowDigestCache | None,
) -> str:
    if dataset in _VALUE_DIGESTED:
        rows = stored_rows_digest(
            store, dataset, year=year, through=session, visible_at=published, cache=cache
        )
        return _ABSENT if rows is None else f"rows:{rows}"
    content = store.partition_content_hash(dataset, year)
    return _ABSENT if content is None else f"content:{content}"


# --- the verdict cache ---------------------------------------------------------------------------


class SurvivorVerdictCache(Protocol):
    """Where a survivor record's verified verdict is kept: under the record id, its provenance's
    digest and the cache key it was verified under. A cache and nothing more."""

    def verified_under(
        self, record_id: str, provenance_digest: str, key: SurvivorCacheKey
    ) -> bool: ...

    def keep(self, record_id: str, provenance_digest: str, key: SurvivorCacheKey) -> None: ...


class FileSurvivorVerdicts:
    """`SurvivorVerdictCache` over `SURVIVOR_VERDICT_DIRECTORY`: one write-once file per verified
    record, provenance and key. `clock` stamps `verified_at`.

    A file that does not read back as the verified verdict its name promises -- not UTF-8 JSON,
    another record, provenance or key, another verdict -- is a miss and is named in a warning; it
    is never rewritten, and a file already at a name is never replaced -- not even one another
    report wrote between this one's look and its write (`_write_once`'s exclusive link)."""

    def __init__(self, runtime_dir: Path, *, clock: Callable[[], datetime]) -> None:
        self._directory = runtime_dir / SURVIVOR_VERDICT_DIRECTORY
        self._clock = clock

    def _path(self, record_id: str, provenance_digest: str, key: SurvivorCacheKey) -> Path:
        return self._directory / f"{record_id}.{provenance_digest}.{key.digest}.json"

    def verified_under(self, record_id: str, provenance_digest: str, key: SurvivorCacheKey) -> bool:
        path = self._path(record_id, provenance_digest, key)
        if not path.is_file():
            return False
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            expected = {
                "record_id": record_id,
                "provenance": provenance_digest,
                "key": key.document(),
                "verdict": VERDICT_VERIFIED,
            }
            if (
                type(body) is not dict
                or {name: body.get(name) for name in expected} != expected
                or type(body.get("verified_at")) is not str
            ):
                raise ValueError("it is not the verified verdict its name promises")
        except (UnicodeDecodeError, ValueError) as error:
            # Accidental corruption is inside the threat model, and a cache entry that does not
            # read back is a miss: the record is scored again, and the file is named.
            _LOG.warning(
                "ignoring the unreadable survivor verdict file %s (%s); the record is scored again",
                path.name,
                error,
            )
            return False
        return True

    def keep(self, record_id: str, provenance_digest: str, key: SurvivorCacheKey) -> None:
        path = self._path(record_id, provenance_digest, key)
        if path.exists():
            return
        body = {
            "record_id": record_id,
            "provenance": provenance_digest,
            "key": key.document(),
            "verdict": VERDICT_VERIFIED,
            "verified_at": self._clock().isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_once(path, json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


def _write_once(path: Path, text: str) -> None:
    """`text` at `path` unless a file is there by then, never replacing one (module docstring).

    The bytes go to a temporary file of this writer's own, are flushed and synced, and take the
    name by a hard link, which fails when the name is taken: of two writers the second loses, and
    the first's file stands as it was written. The directory is synced after the link (not on
    Windows, which cannot open a directory). The temporary file is removed either way."""
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.partial")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


# --- the check -----------------------------------------------------------------------------------


class SurvivorRecordCheck:
    """The late-record check of a registered survivor configuration's records (module
    docstring); calling it on a record returns its `SurvivorRecordVerdict`.

    `book`, `transform`, `exchange`, `min_coverage` and `max_carry_sessions` are the registered
    configuration's, as its daily run read the day with them; `provenance` is this registration's
    index (`survivor_provenance_index`); `verdicts`, when given, keeps and serves verified
    verdicts. Row hashes of the small datasets are shared between the records one check is
    called on."""

    def __init__(
        self,
        store: PanelStore,
        *,
        book: SurvivorBookConfig,
        transform: FactorTransformSpec,
        exchange: str,
        min_coverage: float,
        max_carry_sessions: int,
        registered: RegisteredConfiguration,
        provenance: Mapping[tuple[date, str], SurvivorInputProvenance],
        verdicts: SurvivorVerdictCache | None = None,
    ) -> None:
        self._store = store
        self._book = book
        self._transform = transform
        self._exchange = exchange
        self._min_coverage = min_coverage
        self._max_carry_sessions = max_carry_sessions
        self._registered = registered
        self._provenance = provenance
        self._verdicts = verdicts
        self._digests: RowDigestCache = {}

    def __call__(self, record: PredictionRecord) -> SurvivorRecordVerdict:
        batch = record.batch
        session = batch.as_of.astimezone(SHANGHAI).date()
        declaration = batch.artifact.declaration
        if not survivor_record_is_bound(record, self._book, self._registered):
            return SurvivorRecordVerdict(
                record_id=record.record_id,
                session=session,
                verdict=VERDICT_REFUSED,
                scope=NOT_BOUND,
                reason=f"{record.record_id} is not declared under the registered survivor "
                f"configuration ({declaration.family}, horizon {declaration.horizon}, "
                f"feature_version {declaration.feature_version}); a record is checked only when "
                "it is bound to the registration, and is never scored again otherwise",
            )
        digest = batch_digest(batch)
        provenance = self._provenance.get((session, digest))
        if provenance is None:
            return SurvivorRecordVerdict(
                record_id=record.record_id,
                session=session,
                verdict=VERDICT_REFUSED,
                scope=NO_PROVENANCE,
                reason=f"no provenance of this registration names the batch {digest} of "
                f"{session.isoformat()}; without what its day read, a record filed after its "
                "signal instant cannot be held to it",
            )
        # One hold from the key to the verdict: a panel write lands wholly before the check or
        # wholly after it, so a verdict is kept under the key of the state it was reached on.
        with self._store.reading():
            return self._judged(record, session, provenance)

    def _judged(
        self, record: PredictionRecord, session: date, provenance: SurvivorInputProvenance
    ) -> SurvivorRecordVerdict:
        """Steps 3 to 6 of the check (module docstring), for a bound record and its provenance."""
        batch = record.batch
        record_id = record.record_id
        verdicts = self._verdicts
        key = (
            None
            if verdicts is None
            else survivor_cache_key(
                self._store,
                provenance,
                transform=self._transform,
                exchange=self._exchange,
                min_coverage=self._min_coverage,
                max_carry_sessions=self._max_carry_sessions,
                cache=self._digests,
            )
        )
        if (
            verdicts is not None
            and key is not None
            and verdicts.verified_under(record_id, provenance.digest, key)
        ):
            return SurvivorRecordVerdict(
                record_id=record_id,
                session=session,
                verdict=VERDICT_VERIFIED,
                scope=None,
                cached=True,
            )
        scope: ChangeScope
        try:
            again = recompute_survivor_batch(
                self._store,
                record,
                book=self._book,
                transform=self._transform,
                exchange=self._exchange,
                min_coverage=self._min_coverage,
                max_carry_sessions=self._max_carry_sessions,
                held=provenance.held,
                registered=self._registered,
                provenance=provenance,
            )
        except SURVIVOR_RECOMPUTE_REFUSALS as error:
            scope = RECOMPUTE_FAILED
            failure = f"its day cannot be scored again from the store: {error}"
        else:
            if (again.as_of, again.artifact, again.predictions) == (
                batch.as_of,
                batch.artifact,
                batch.predictions,
            ):
                if verdicts is not None and key is not None:
                    verdicts.keep(record_id, provenance.digest, key)
                return SurvivorRecordVerdict(
                    record_id=record_id,
                    session=session,
                    verdict=VERDICT_VERIFIED,
                    scope=None,
                )
            scope = RECOMPUTE_DIFFERS
            failure = (
                f"its scores for {session.isoformat()} are not the registered configuration's "
                "scored again from the store"
            )
        changes = survivor_provenance_changes(
            self._store, provenance, transform=self._transform, scope=scope
        )
        if changes:
            return SurvivorRecordVerdict(
                record_id=record_id,
                session=session,
                verdict=VERDICT_UNVERIFIABLE,
                scope=scope,
                changes=changes,
                reason=failure,
            )
        return SurvivorRecordVerdict(
            record_id=record_id,
            session=session,
            verdict=VERDICT_REFUSED,
            scope=scope,
            reason=f"{record_id} was filed after its signal instant and {failure}; no input it "
            "read has been corrected since in a way its provenance can name -- a value-blind "
            "correction in place ends here -- so it could carry what arrived after the signal "
            "instant, and it is refused",
        )
