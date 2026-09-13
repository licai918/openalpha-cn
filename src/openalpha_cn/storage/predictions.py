"""A directory of predictions, each written once and none ever replaced (`V2-P4-017`).

`domain/prediction_record.py` is the contract and carries the argument for what a stored
prediction proves; this is the store, and it is responsible for exactly two things the contract
cannot do for itself: reading a clock the caller does not own, and never writing where something
is already held.

## Why this layer, which was decided two issues ago and is still the only one that works

`domain/alpha_model.py`'s table names `storage/` for this issue and names what rules the
alternatives out. `V2-P4-017` has to **deserialize** a `PredictionBatch`, which means importing
the contract, and `storage-no-upward-deps` forbids this package `agents`, `runtime`, `product`
and `backtest` on full transitive reachability -- so the contract it deserializes has to sit
*below*. It does: `domain/`.

**Two contracts stand between a batch and this store, one per direction, and the count is
measured rather than recited.** This docstring first said *four* -- the whole `backtest/` set --
and that is false: reading the parsed configuration, `backtest-no-numeric-stack-or-panel-plane`
and `backtest-studies-reach-no-composition-root` do not name `openalpha_cn.storage` at all, and
`ranking-creates-no-portfolio-order` covers two modules, none of which produces a batch. Exactly
one contract bars the outbound edge -- `backtest-studies-touch-no-store`, whose source list names
all three `PredictionBatch` producers (`alpha_model`, `alpha_baseline`, `alpha_tree`) and whose
forbidden list carries `openalpha_cn.storage`, with `backtest/replay.py` and the package
`__init__` exempt. `storage-no-upward-deps` bars the inbound one. Both together are what make the
join a *face's* job (`V2-P4-021`'s) rather than something either side can reach for. That
face is `model_view.run_daily`, and this store is in `runtime/composition.py` since it landed;
`model_view.ModelPredictionStore` is the narrow Protocol it is injected through, so the face
names no concrete store and the composition root stays the only place that decides where
predictions live.

**`V2-P4-062`'s Protocol precedent does not apply here, and the direction is why.** That issue
put `ShortlistDocumentStore` beside its consumer in `shortlist_view` and had
`FileShortlistStore` satisfy it structurally, so that `openalpha_cn.storage` gained no edge into
a module above it; `factor_view.ExperimentDocumentStore` is the same trick for the same reason.
Both exist because the *contract* lived upward. `PredictionRecord` lives downward, so this module
imports it outright and works in typed objects rather than opaque strings -- which is what lets
`get` re-derive an address and refuse a document that no longer matches it. A Protocol here would
buy nothing and cost the check.

## Why there is no merge, and therefore no merge guard

`V2-P4-071` turned a whole-partition replace into an append and put
`_refuse_to_drop_a_stored_build` in front of the merge; `V2-P4-073` measured that the guard
covered the manifest half and not the observation half, because `071` had split one assembly pass
into two independent ones and the docstring's *"a write carrying every stored build carries every
stored security by construction"* stopped being true when the construction changed. The write
succeeded, exited 0, said nothing, and a whole cross section was gone until the next read noticed.

A guard is needed exactly where one write replaces a unit that holds more than the write carries.
Here the unit is one record, the write carries exactly it, and `put` writes only where nothing is
held -- so there is no merge to audit and no "by construction" implication for a later issue to
break. What `073` still contributes is the *read* side: its loss surfaced only when the next read
checked that stored rows still addressed to their manifest, so `get` re-derives every document's
address and refuses one that no longer matches its filename.

The one "by construction" claim this design does rest on -- that a backfill cannot collide with
its original, because the two disagree about `predicted_at` and `predicted_at` reaches the
address -- is audited rather than trusted, by `tests/unit/domain/test_prediction_record.py::
test_every_field_reaches_the_address_except_the_one_this_mapping_names`, which moves each
addressed field in turn.

## Why a directory of documents rather than a `state.sqlite3` table

`storage/factor_experiments.py`'s three reasons and `storage/shortlists.py`'s restatement of
them, transferring here with one of the three changed rather than copied:

1. **The document is already content-addressed**, so a row would add a second identity and a
   schema -- and the schema is what would need a migration of opaque JSON payloads the day
   `alpha-prediction-record/v2` exists.
2. **The precedent is three stores deep** -- `ParquetEvidenceStore`, `FileExperimentStore`,
   `FileShortlistStore` -- and this is where it changes: all three are opaque to their own
   documents and this one is not. It deserializes, because `V2-P4-073`'s read-side re-addressing
   check needs a parsed record to re-address. A directory is still right; what does not carry
   over is "and the store need not understand what it holds".
3. **A batch is large and rare.** One document per model per instant, which is the opposite of
   the row-at-a-time access the `state.sqlite3` tables exist for.

## The storage form `V2-P4-015` left to be measured, and why the measurement did not decide it

That issue asked whether several hundred `(str, float)` rows want a storage form other than the
artifact's own tuple; `V2-P4-016` answered the digest half (0.399 ms over a 900-node ensemble)
and left this half. Measured at full market width -- 5,545 securities, the count `V2-P4-004`
found listed, against a 900-node ensemble:

| document | bytes | dump | write | read | validate | `record_id` |
| --- | --- | --- | --- | --- | --- | --- |
| 3 securities, 3 parameters | 961 | 0.003 ms | 0.062 ms | 0.012 ms | 0.011 ms | 0.011 ms |
| 900 / 900 | 81,062 | 0.215 ms | 0.080 ms | 0.021 ms | 0.688 ms | 0.731 ms |
| 5,545 / 900 | 394,249 | 1.110 ms | 0.176 ms | 0.044 ms | 3.596 ms | 3.466 ms |
| 5,545 / 5,000 | 482,996 | 1.321 ms | 0.203 ms | 0.050 ms | 5.210 ms | 4.730 ms |

A whole store round trip at market width -- dump, write, read, validate -- is **4.9 ms** against
the 4.55 s `V2-P4-015` measured its tree taking to fit: about 1/930th of it, once per model per
instant, and `validate` is 3.6 of those 4.9. Reading the address is a further **3.5 ms**, paid
per read of `record_id` rather than per round trip, which extends `V2-P4-016`'s reading rather
than contradicting it: its 0.399 ms was over an artifact alone, and a record's address is taken
over the whole batch beside it.

**But the number is not what settles it.** The bytes written here *are* the canonical JSON the
address is taken over -- same `model_dump`, same `exclude_computed_fields=True`, less the one
field `PREDICTION_RECORD_UNADDRESSED_FIELDS` names -- so a second storage form would be a second
serialization, and the address would have to be computed from something other than what is
stored. That is the "另造哈希" this repository has refused everywhere (`V2-P4-037` files the
defect a second canonicalisation would be), and it would be wrong at three securities as well as
at five thousand. The measurement's only job is to show that nothing forces the question anyway.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Final, Literal

from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.prediction_record import (
    PREDICTION_RECORD_VERSIONS,
    PredictionRecord,
    is_prediction_record_id,
    prediction_record_for,
)
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.domain.versioning import STORED_DOCUMENT_FAULTS, read_versioned

__all__ = [
    "PREDICTION_DOCUMENT_SUFFIX",
    "FilePredictionStore",
    "PredictionStoreError",
    "PredictionWrite",
]

PREDICTION_DOCUMENT_SUFFIX: Final[str] = ".json"


class PredictionStoreError(RuntimeError):
    """Raised for an unusable key, a lineage that resolves to nothing, or a doctored document.

    A `RuntimeError` rather than a subclass of `PredictionRecordError`, which is
    `ShortlistStoreError`'s arrangement for a reason that reverses here and lands in the same
    place. There it was that this layer *may not* import the consumer's vocabulary; here it may
    -- `domain/` is below -- and the vocabularies are still separate, because these refusals are
    about a **filing system** and not about a prediction. A key that is not an address, a
    `supersedes` naming nothing held, a document whose bytes moved after they were filed, and a
    document whose bytes will not parse back into a record at all are statements about this
    directory.

    **The last of those is `V2-P4-096` and this docstring used to say the opposite.** It read:
    *"`PredictionRecordError` and pydantic's `ValidationError` remain what a malformed prediction
    raises, and they propagate unchanged"* -- and that sentence was describing the defect. It is
    true of `put`, where a caller hands over live objects and the contract judges them; it was
    false of `get`, where the same types are raised about *bytes on a disk this store owns* and
    reached the user as `exit 5`. The two directions are now separate: constructing a record
    still raises the contract's own errors unchanged, and reading one back raises this store's.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class PredictionWrite:
    """What a write did, and the record that is now held under the key.

    `record` is the **held** document, not the arriving one: on `unchanged` it is what was
    already on disk, which is how a caller sees that its write did not replace anything. The two
    differ in `recorded_at` and only there, since that is the one field the address does not
    carry -- so a caller that re-offered a prediction reads back the instant this store *first*
    held it rather than the instant it last saw it.
    """

    record: PredictionRecord
    outcome: Literal["created", "unchanged"]


class FilePredictionStore:
    """A directory of prediction records, each keyed by its own content address.

    Total on a directory that does not exist yet, `FileShortlistStore`'s rule: `list_ids` answers
    `()` and `get` answers `None`, because a fresh install with no predictions in it is the
    ordinary state rather than a fault.

    `clock` has no default. It is the one input this store owns and the caller does not, so the
    composition root passing a real clock and a test passing a fixed one is the whole mechanism
    behind `standing` -- and a default would let a caller construct a store whose clock it had
    quietly chosen after all. `build_label_window`'s `zone` is the precedent for requiring an
    argument rather than defaulting it: a waiver that is a default is an accident.
    """

    def __init__(self, path: Path, *, clock: Callable[[], datetime]) -> None:
        self.path = path
        self._clock = clock

    def put(
        self,
        *,
        batch: PredictionBatch,
        calendar: TradingCalendar,
        zone: tzinfo,
        supersedes: str | None = None,
    ) -> PredictionWrite:
        """Take custody of `batch`, and never write where something is already held.

        There is no `recorded_at` parameter, and that absence is the guarantee: the custody stamp
        comes from this store's own clock, so a caller who backdates `predicted_at` reaches
        `unwitnessed` and cannot reach `forward`. There is no key parameter either -- the address
        is derived from what is being stored -- so no call can name a document to overwrite.

        `supersedes` must name a record this store actually holds. `V2-P4-049` measured what a
        reference checked only against a pattern is worth: a fabricated `run_manifest_id` that
        parsed cleared a `1.0` researched-ratio gate. A recomputation that claims to correct an
        original nobody can produce is the same defect, and refusing it is cheap here because the
        referent is one `get` away.

        Propagates `CalendarHorizonError` and `LabelError` from `outcome_known_at_for` unchanged:
        a batch whose outcome day the published calendar does not reach has no knowable deadline,
        and inventing one -- clamping to the last published session, say -- would fabricate the
        single instant this contract exists to derive.
        """
        if supersedes is not None and self.get(supersedes) is None:
            raise PredictionStoreError(
                f"this recomputation names {supersedes!r} as the record it supersedes and this "
                "store holds no record under that address; a lineage edge that resolves to "
                "nothing claims to correct an original nobody can produce, which is the shape "
                "V2-P4-049 measured clearing a gate with a fabricated reference"
            )
        record = prediction_record_for(
            batch=batch,
            calendar=calendar,
            zone=zone,
            recorded_at=self._clock(),
            supersedes=supersedes,
        )
        # Read the address once. `record_id` is a `computed_field` and this repository does not
        # cache those (ADR-0003), and the measurement in this module's docstring says it is the
        # single most expensive thing a write does -- 3.5 ms at market width, against 1.3 ms for
        # everything else put together.
        record_id = record.record_id
        document = self._document(record_id)
        if document.is_file():
            held = self.get(record_id)
            if held is None:  # pragma: no cover - `is_file` and `get` disagree only on a race
                raise PredictionStoreError(f"{record_id} is filed here and could not be read back")
            return PredictionWrite(record=held, outcome="unchanged")
        self.path.mkdir(parents=True, exist_ok=True)
        temporary = document.with_name(f"{document.name}.partial")
        temporary.write_text(record.model_dump_json(exclude_computed_fields=True), encoding="utf-8")
        temporary.replace(document)
        return PredictionWrite(record=record, outcome="created")

    def get(self, record_id: str) -> PredictionRecord | None:
        """The record held under `record_id`, or `None` when nothing is held under it.

        Reads through `read_versioned`, the single entry point every deserializing store in this
        package uses, then re-derives the address and refuses a document that no longer matches
        the name it is filed under. That second step is `V2-P4-073`'s contribution: the silent
        loss it found surfaced only when the next read checked stored rows against their
        manifest, and a store whose content addressing is only a naming convention stops being
        content-addressed the first time it is convenient.

        What the check cannot see is an edit to `recorded_at`, which
        `PREDICTION_RECORD_UNADDRESSED_FIELDS` keeps out of the address on purpose;
        `the_address_does_not_commit_to_the_custody_stamp` states that cost and
        `tests/unit/test_prediction_store.py::
        test_the_custody_stamp_is_the_one_edit_that_check_cannot_see` measures it.

        ## `V2-P4-096`: the address was checked and the parse was not

        Until that issue this method re-derived the address of every document it could *parse*
        and let every document it could not parse escape as whatever `read_versioned` raised --
        so a write a power cut stopped half way arrived at three product faces as `exit 5` with
        the message withheld, a bare `500 text/plain`, and an unenveloped `JSONDecodeError`,
        while a document with one number *edited* was refused perfectly one line below. The
        contrast was the diagnosis, and it is the fourth instance of the class `V2-P4-080`,
        `V2-P4-084` and `V2-P4-088` each closed one at a time.

        `STORED_DOCUMENT_FAULTS` rather than `except json.JSONDecodeError`, and the measurement
        that settles it is in that constant: three of the four documents this store can be handed
        raise something else. Converted here rather than at either face, because `get` is this
        store's only reader and `put` reads through it -- so one `except` covers the read *and*
        the daily run that would re-register the same prediction, which is the shape `V2-P4-088`
        chose over guarding two call sites separately.
        """
        _refuse_an_unusable_id(record_id)
        document = self._document(record_id)
        if not document.is_file():
            return None
        try:
            record = read_versioned(
                PREDICTION_RECORD_VERSIONS, document.read_text(encoding="utf-8")
            )
        except STORED_DOCUMENT_FAULTS as error:
            raise PredictionStoreError(
                f"the document filed as {record_id} could not be read back as a prediction: "
                f"{type(error).__name__}: {error}. The address is well formed and a file is held "
                "under it, so this is damage to the bytes rather than a question about the "
                "address -- a write this store never finished, or a document something outside "
                "it rewrote. Nothing here can repair it and nothing here will overwrite it: "
                f"remove `predictions/{record_id}{PREDICTION_DOCUMENT_SUFFIX}` from this runtime "
                "directory and re-run `openalpha model daily-run` to register the prediction "
                "again"
            ) from error
        if record.record_id != record_id:
            raise PredictionStoreError(
                f"the document filed as {record_id} no longer addresses to that name -- its "
                f"bytes now produce {record.record_id}. A prediction is stored under the digest "
                "of what it said, so a document that moved is one that was edited after it was "
                "filed, and Story S32 is about exactly the edits that cannot happen"
            )
        return record

    def list_ids(self) -> tuple[str, ...]:
        """Every held `record_id`, ascending.

        Reads the directory rather than an index, and skips a name that is not a well-formed
        address -- including the `.partial` file a crashed write leaves behind.
        `FileShortlistStore.list_ids`' rule.

        **What that rule covers is names, and `V2-P4-096` is where the difference showed.** This
        used to add *"a store that returned one of those would hand a caller a key `get` then
        refuses"*, which claimed more than a directory scan can know: a document damaged after it
        was filed keeps a perfectly well-formed name, so it is listed here and refused by `get`.
        That pair is now the answer rather than a silent disagreement -- `get` names the damage,
        the document, and what to do about it -- and the alternative was measured rather than
        waved off. Verifying each name would mean parsing every body, which the module docstring
        measures at 3.6 ms per document at market width: 0.9 s for a year of one model's daily
        runs and 22 s for five models over five years, on a command whose entire purpose is to be
        the cheap half. It would also *hide* the damaged document, which is the opposite of what
        an operator holding a broken disk needs.
        """
        if not self.path.is_dir():
            return ()
        return tuple(
            sorted(
                entry.name[: -len(PREDICTION_DOCUMENT_SUFFIX)]
                for entry in self.path.iterdir()
                if _is_a_document(entry)
            )
        )

    def _document(self, record_id: str) -> Path:
        return self.path / f"{record_id}{PREDICTION_DOCUMENT_SUFFIX}"


def _is_a_document(entry: Path) -> bool:
    parts = entry.name.split(".")
    return (
        entry.is_file()
        and len(parts) == 2
        and f".{parts[1]}" == PREDICTION_DOCUMENT_SUFFIX
        and is_prediction_record_id(parts[0])
    )


def _refuse_an_unusable_id(record_id: str) -> None:
    """Refuse a key that is not an address this repository computed.

    The path safety, and a *shape* check rather than an escaping one for `FileShortlistStore`'s
    measured reason: `prd_` plus 24 lowercase hex characters contains no separator, no `.` and
    nothing a filesystem treats specially, so a key that matches cannot name anything outside
    this directory. Sanitising instead would turn `../../etc/passwd` into a plausible key and
    file a document under it.

    The predicate is `domain/prediction_record.py`'s own rather than a pattern compiled here.
    `FileShortlistStore` had to declare its own because `shortlist_view` sits above this package
    and cannot be imported from it; nothing forces that here, and a second spelling of one
    pattern is a second thing that can disagree about it.
    """
    if not is_prediction_record_id(record_id):
        raise PredictionStoreError(
            f"{record_id!r} is not a record_id; this store is keyed by each record's own content "
            "address (`prd_` and 24 lowercase hex characters), and a key that did not come from "
            "there is a filename rather than a content address"
        )
