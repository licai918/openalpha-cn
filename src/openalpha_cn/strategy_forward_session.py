"""The forward-registration protocol (`V2-P7-013`, R1): file a session's batch before its outcome.

A daily research step's step 7 asks the same four questions of every family, and R1 takes them
out of `scripts/daily_selection.py` so Plan C's step can file through `src/` and import nothing
from `scripts/`:

- **Where does the outcome instant fall?** `outcome_calendar` -- the session's calendar year, and
  the next one when the store holds it, so a window crossing New Year can be placed. A year the
  store does not hold is not asked for: `load_trading_calendar` would refuse it, and refusing a
  record whose outcome window ends inside its own year would be wrong.
- **Does the session already hold this answer?** `held_record` -- `session_record` finds the
  record filed under the same declaration. Holding the same numbers it is reused and nothing is
  written; holding different ones, the registered record stands and this run is refused: a second
  answer to one session would be a revision, which the prediction store exists to make
  impossible.
- **Is it still a prediction?** **Only before the next session's call auction.** The store calls a
  record `forward` when it held it before the outcome window's last close. The daily command's
  rule is stricter, because the book trades the day's scores at the next open, whose price the
  call auction starting at 09:15 fixes: a registration at or after `registration_cutoff` -- the
  command run late, or catching up a missed day -- is refused before anything is filed.
- **What goes in the journal?** `prediction_block` -- the `prediction` stage as the day's journal
  writes it: what the record is, how this run came by it (`created`, or `unchanged` for a record
  held already), and its provenance's digest.

`file_forward_record` is the composite for a family whose provenance is written elsewhere (the P6
day writes it before calling it). `file_with_provenance` is the composite for a family that has
one to write: it reuses a held record's own provenance before anything else (a second provenance
written on a moved clock would name a batch that was never filed), refuses late **before** the
write rather than after it (so no rerun leaves a stray provenance behind), and otherwise writes
the provenance **before** the record it describes. Both raise `ForwardSessionRefused`, whose
`.detail` is the message alone: the caller owns its step numbering -- `scripts/daily_selection.py`
has two sequences (P6's `STEPS` and the survivor day's `SURVIVOR_STEPS`), and this module names
neither.

**What it may reach, and why it takes stores by injection.** `D3`'s contract: this module imports
`domain.*`, `panel.store`, `panel_ingest.load_trading_calendar` and six names from
`strategy_registration` -- and no `storage.*` or `runtime.*` at all. The record store is the
`ForwardRecordStore` Protocol below, satisfied structurally by `FilePredictionStore`; opening it is
the caller's job (the composition root's), which is also why this module cannot be imported by a
step that wants to open its own storage. `tests/unit/test_strategy_forward_session.py` pins the
reach with `grimp` and `tests/unit/test_forward_and_agent_layering.py` keeps it structural.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, tzinfo
from typing import Any, Protocol

from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.prediction_record import PredictionRecord, outcome_known_at_for
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, TradingCalendar
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_trading_calendar
from openalpha_cn.strategy_registration import (
    SHANGHAI,
    HeldRecordLookup,
    StrategyRegistrationError,
    batch_digest,
    registration_cutoff,
    session_record,
)


class ForwardSessionRefused(RuntimeError):
    """A session's batch cannot be filed, or is no longer a prediction.

    `detail` is the message without any caller's step number: the script that called this module
    raises its own `StepFailedError` around it, with the prefix its sequence uses.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class _Written(Protocol):
    """What `ForwardRecordStore.put` hands back: the record under its address, and whether the
    store took custody now (`created`) or already held it (`unchanged`).

    Read-only properties rather than attributes: the concrete `PredictionWrite.outcome` is
    `Literal["created", "unchanged"]`, which is a `str` but not *the same* `str` a mutable
    protocol attribute requires -- a writable attribute is invariant, so the structural match
    would fail and the Protocol would refuse the one store this module files through.
    """

    @property
    def record(self) -> PredictionRecord: ...

    @property
    def outcome(self) -> str: ...


class ForwardRecordStore(HeldRecordLookup, Protocol):
    """What filing a batch needs of a prediction store: the session lookup, and `put`.

    Declared here rather than taken from `openalpha_cn.storage.predictions` so this module has no
    edge into `storage` (`D3`); `FilePredictionStore` satisfies it structurally.
    """

    def put(
        self,
        *,
        batch: PredictionBatch,
        calendar: TradingCalendar,
        zone: tzinfo,
        supersedes: str | None = None,
    ) -> _Written: ...


def outcome_calendar(
    store: PanelStore, exchange: str, session: date, as_of: datetime
) -> TradingCalendar:
    """The calendar the record's outcome instant is derived on: the session's year, and the
    next when it is stored, so a window crossing New Year can be placed."""
    stored = set(store.registered_years(TRADING_CALENDAR_DATASET))
    years = tuple(year for year in (session.year, session.year + 1) if year in stored)
    return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)


def held_record(store: HeldRecordLookup, batch: PredictionBatch) -> PredictionRecord | None:
    """The record `store` already holds for `batch`'s session under its declaration, if any.

    A conflicting answer raises `ForwardSessionRefused` wrapping the registration error, which
    carries the message naming the record that stands; its `__cause__` is that error.
    """
    try:
        return session_record(store, batch)
    except StrategyRegistrationError as error:
        raise ForwardSessionRefused(str(error)) from error


def file_forward_record(
    store: ForwardRecordStore,
    batch: PredictionBatch,
    *,
    calendar: TradingCalendar,
    clock: Callable[[], datetime],
) -> tuple[PredictionRecord, str]:
    """File `batch` unless the session already has its record; return `(record, outcome)`.

    A daily command re-run the same evening must not file a second answer, and a registration at
    or after the next session's call auction must not file one at all. `outcome` is `"created"`
    when this call took custody and `"unchanged"` when the session's record was already held.

    The statement order and the clock-call order are load-bearing and tested: the session lookup,
    then the cut-off, then `outcome_known_at_for`, then `clock()`, then `put`.
    """
    held = held_record(store, batch)
    if held is not None:
        return held, "unchanged"
    day = batch.as_of.astimezone(SHANGHAI).date()
    cutoff = registration_cutoff(calendar, day)
    deadline = outcome_known_at_for(batch, calendar=calendar, zone=SHANGHAI)
    now = clock()
    if now >= cutoff:
        raise ForwardSessionRefused(
            f"the scores of {day.isoformat()} are traded at the next session's open, whose call "
            f"auction starts {cutoff.isoformat()}, and it is {now.isoformat()}: part of their "
            f"outcome is published already (all of it by {deadline.isoformat()}), so registered "
            "now they would not be a prediction made before its outcome. Nothing was filed"
        )
    written = store.put(batch=batch, calendar=calendar, zone=SHANGHAI)
    return written.record, written.outcome


def prediction_block(record: PredictionRecord, outcome: str, *, provenance: str) -> dict[str, Any]:
    """The journal's `prediction` stage for `record`: what it is, how this run came by it
    (`created`, or `unchanged` for a record held already), and its provenance's digest."""
    declaration = record.batch.artifact.declaration
    return {
        "registered": True,
        "record_id": record.record_id,
        "family": declaration.family,
        "horizon": declaration.horizon,
        "outcome": outcome,
        "standing": record.standing,
        "as_of": record.batch.as_of.isoformat(),
        "outcome_known_at": record.outcome_known_at.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "scored": len(record.batch.scored),
        "abstained": len(record.batch.abstained),
        "batch_digest": batch_digest(record.batch),
        "provenance": provenance,
    }


def file_with_provenance(
    *,
    store: HeldRecordLookup,
    batch: PredictionBatch,
    now: datetime,
    cutoff: datetime,
    late: Callable[[datetime], str],
    held_provenance: Callable[[PredictionRecord], str],
    write_provenance: Callable[[], str],
    register: Callable[[PredictionBatch], tuple[PredictionRecord, str]],
) -> dict[str, Any]:
    """File a batch whose provenance is written first, and return the journal's stage for it.

    **A record held for the session is reused before anything is written**: its own provenance,
    the one written before it, is what the journal names, and nothing is written, whatever the
    clock. Otherwise, with no record held, the run is refused once `now` has reached `cutoff` --
    before the provenance, so a rerun leaves no stray one behind -- with `late(now)`'s message.
    Only then is the provenance written, strictly before `register(batch)`, and the returned
    block names the digest `write_provenance()` returned.

    `late`, `held_provenance`, `write_provenance` and `register` are injected so the caller owns
    its own refusal wording, provenance directory and storage construction; this function owns
    their order.
    """
    held = held_record(store, batch)
    if held is not None:
        return prediction_block(held, "unchanged", provenance=held_provenance(held))
    if now >= cutoff:
        raise ForwardSessionRefused(late(now))
    provenance = write_provenance()
    record, outcome = register(batch)
    return prediction_block(record, outcome, provenance=provenance)
