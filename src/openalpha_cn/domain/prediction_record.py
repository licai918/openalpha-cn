"""What a stored prediction is, and which clock says it was stored in time (`V2-P4-017`).

The row is two requirements -- *"预测在结果已知前落库"* and *"回溯重算存为独立制品"* which may not
replace the original -- and Implementation Decision 14 is the long form of both: *"预测先于结果
落库。Daily 与 Paper Portfolio 预测批次在观测窗关闭前不可变且带时间戳。"* Story S32 is the one line
the PRD marks 不可省: out-of-sample predictions persisted **before outcomes are known**, because
that is the only thing separating a discovery from a story told afterwards.

`V2-P4-011` built the batch and left this issue two things by name: *"before the observation
window closed" needs a trading calendar and a store*, and *a backfill being a separate artifact
rather than a replacement is a storage rule*. This module is the calendar half and the contract;
`storage/predictions.py` is the store.

## "Before the outcome is known" is one instant, and it is the label's

A prediction is judged against the `OutcomeLabel` built at the same instant and horizon, so the
answer comes into existence the moment that label's last close prints. `build_label_window` is
already the one function that resolves an instant and a horizon into the sessions an outcome is
measured over, and `LabelWindow.close_instant(exit_day)` already dates the last of them. So
`outcome_known_at_for` is a composition of two functions that existed, not a second reading of
the same calendar -- which matters because a second reading is a second thing that can disagree,
and this repository has paid for that twice (`stable_model_id`'s "another hash", `V2-P4-013`'s
weaker overlap rule).

The deadline is therefore never a parameter. `prediction_record_for` takes a `TradingCalendar`,
which is published data this repository already gates point-in-time, and computes the instant
itself -- `artifact_for`'s rule applied to the one field a liar would most want to choose. A
record whose deadline arrived as an argument could be declared forward by naming a date far
enough away.

## Exactly what is verified, and exactly what is not

Three instants and a deadline give three questions, and only two of them have answers here.

| question | answered by | verifiable |
| --- | --- | --- |
| was this batch **produced** before the outcome existed? | `predicted_at` | **no** |
| did this store **hold** it before the outcome existed? | `recorded_at` | in-process |
| does the record still say what it said? | `record_id`, re-derived | every addressed field |

`predicted_at` is whatever the caller passed to `predict`, and nothing in this repository can
check it: `FittedAlphaModel.predict` takes it as a parameter precisely so a batch is
reproducible, which is the same property that makes it unfalsifiable. So a caller who backdates
gets exactly as far as `unwitnessed` -- because the one clock a caller does not set is the
store's, and a batch stamped in time that reaches the store after the answer exists lands there.
`standing` is a `computed_field` for that reason: a provenance a producer stamps is a provenance
a producer chooses.

**And none of this defends against whoever owns the disk.** Every document is a file on a
local-first single-user filesystem, and an operator who can edit `predicted_at` can edit
`recorded_at`, re-address the file, or set the machine clock before the write. What this store
is against is a *caller* inside the process, and the ordinary and much more common mistake --
running a study today over last year's data and later reading the pile as if it had been
registered in advance. A claim a third party could check would need a timestamp somebody else
controls; this repository has no such thing and this issue does not build one.
`nothing_here_defends_against_whoever_owns_the_disk` is that sentence where a reader meets it.
`V2-P4-016` stated its seed the same way rather than papering it, and the seed's residue is here
too: two seeds still produce byte-identical coefficients and two addresses, so a record names a
**declaration** rather than a run.

## Three standings, because there are three different facts

`forward` is Story S32 and nothing else is: the batch says it was produced in time **and** the
store says it held the bytes in time. `unwitnessed` is a claim this repository cannot check --
stamped in time, received late. `backfill` is a recomputation that says so: produced at or after
the deadline, which is Implementation Decision 14's `回溯重算`. Collapsing the middle one into
either neighbour would be the lie: into `forward` it becomes evidence it is not, and into
`backfill` it accuses a caller whose only fault may have been a slow disk.

## Why a backfill cannot replace an original, and why no guard is needed to say so

`V2-P4-071` met this shape on the factor plane and solved it by turning a whole-partition replace
into an append, with `_refuse_to_drop_a_stored_build` auditing the merge; `V2-P4-073` then
measured that the guard covered only half of it, because the merge had been split in two and the
docstring's *"a write carrying every stored build carries every stored security **by
construction**"* stopped being true when the construction changed. Two things carry over.

The first is the design: **remove the overwrite rather than guard it.** A guard is needed exactly
where one write replaces a unit holding more than the write carries. Here the unit is one record
and the write carries exactly it, so `storage/predictions.py` never merges and there is nothing
to drop.

The second is the warning, and it applies to this module's own "by construction" claim: a
backfill cannot collide with its original because the two disagree about `predicted_at` -- one is
before the deadline and the other is at or after it -- and `predicted_at` reaches the address
through the batch. That is true today and would stop being true the moment somebody excluded a
field. So it is audited rather than asserted: `PREDICTION_RECORD_UNADDRESSED_FIELDS` is passed to
`stable_model_id` and partitioned against `model_fields` in
`tests/unit/domain/test_prediction_record.py`, and `FilePredictionStore.get` re-derives the
address on every read -- `V2-P4-073`'s loss was found on the read side too.

## Implementation Decision 12's third clause, and the half of it that lands here

`V2-P4-013` named this issue for it: *"a prediction persisted before its outcome is known is
untouched because the outcome does not exist yet, which is a stronger guarantee than any split
can give."* That is the forward-looking half, and a `forward` standing is exactly it -- no
selection could have consulted an outcome that had not printed.

The retrospective half -- a final holdout left untouched **through model selection** -- is a
property of a process, not of a record. Nothing a record carries says what its author had already
looked at. What this store does hold is the *denominator*: every declaration ever laid down
against an `as_of` is a document, so "how many models were tried" is a count rather than a
recollection, which is what a multiple-testing policy needs. Counting is not deciding, and
`the_retrospective_half_of_decision_12s_third_clause_leaves_no_trace_in_a_record` says so.

## What is left, and to which issue

- **`V2-P4-018`** owns the abstention vocabulary. `Prediction.abstention` is still free text and
  `standing` is not it -- one names why a security has no number, the other when a batch reached
  the store.
- **`V2-P4-021` owned the model faces, and it built the join.** All three producers of a
  `PredictionBatch` live under `backtest/` and are named on `backtest-studies-touch-no-store`'s
  source list, which forbids them `openalpha_cn.storage`; `storage-no-upward-deps` forbids the
  return edge. So nothing could hand this store a batch until a face above both did, and until
  then `FilePredictionStore` stayed out of `runtime/composition.py`'s container: a twelfth store
  nothing can fill is a field, not a wiring. `model_view.run_daily` is that face and the store is
  now wired, with `build_storage`'s own clock -- which is the whole mechanism behind `standing`,
  since a caller of any of the three faces supplies neither timestamp. (This bullet first said
  *four* contracts barred the outbound edge; the parsed configuration says one, and
  `storage/predictions.py` carries the correction and the reading.)
- **`V2-P4-022`** owns the corpus with a known signal-to-noise ratio. Nothing stored here is a
  claim about alpha.
- **Filling `RunManifest.alpha_model_versions`** was still nobody's when this module landed,
  for the reason `V2-P4-016` recorded: `run_cycle` builds the manifest and there is no
  `AlphaModel` on that path. `V2-P4-021`'s `model daily-run` is what fills it, under a `run_id`
  derived from **this** contract's `record_id` -- so one registered prediction and one stored run
  are idempotent together rather than one of them raising on a repeat.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from types import MappingProxyType
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import build_label_window
from openalpha_cn.domain.time import ensure_aware
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.domain.versioning import ContractVersions

PREDICTION_RECORD_PREFIX: Final[str] = "prd"
"""Three letters, `mdl`/`dec`/`run`/`sgt`'s shape, and measured not to be taken.

`V2-P4-016`'s question asked again with one more prefix in the tree. The census is
`tests/unit/domain/test_manifest_component_provenance.py::live_prefixes`, which reads every
prefix all three of this repository's address builders are called with off the source tree by
AST -- because the hand-written list it replaced had gone stale *and* was wrong about what it
contained. A prefix is the only thing that tells two content addresses over two different
contracts apart, so a reused one would make a stored `prd_...` ambiguous about which builder
produced it.
"""

_ADDRESS_SHAPE_SOURCE: Final[str] = rf"{PREDICTION_RECORD_PREFIX}_[0-9a-f]{{24}}"
"""The one spelling of what `stable_model_id(prefix="prd", ...)` produces. **Unanchored.**

Both forms below are derived from this, `ALPHA_MODEL_ARTIFACT_ID_PATTERN`'s rule that a second
spelling of one fact is a second thing that can disagree about it -- but the two *anchorings* are
genuinely different needs and a mutation sweep is what made that visible rather than a reading.
"""

PREDICTION_RECORD_ID_PATTERN: Final[str] = rf"^{_ADDRESS_SHAPE_SOURCE}$"
"""The anchored form, for pydantic's `Field(pattern=...)`, where the anchors are load-bearing.

Attached to `PredictionRecord.supersedes`, so a recomputation cannot name its antecedent with a
string somebody typed. Measured rather than assumed: pydantic's `pattern` is **search**-shaped,
so the same expression without the trailing `$` accepts `prd_<24 hex>zz` and `prd_<24 hex>\\n`
outright. `test_the_lineage_pattern_needs_both_anchors_and_the_key_check_needs_neither` drives
both.
"""

_ADDRESS_SHAPE: Final[re.Pattern[str]] = re.compile(_ADDRESS_SHAPE_SOURCE)
"""The unanchored form, `fullmatch`ed -- `SHORTLIST_ID_PATTERN`'s arrangement, and its reason.

`$` also matches *before* a final newline, which `domain/panel_batch.py` records being measured
one plane down where `"close\\n"` was accepted as a column name and written into Parquet. This
token becomes a filename component, so the same defect here files a document whose name carries a
newline. `re.fullmatch` against a pattern with no `$` in it cannot be fooled that way, and
compiling the unanchored source rather than reusing the anchored constant is what keeps the two
questions from being answered by one expression that is only right for one of them.
"""

PredictionStanding = Literal["forward", "unwitnessed", "backfill"]
"""Where one record sits relative to the instant its outcome became knowable.

A closed set of three, `ICStabilityCoverage`'s shape, and each of the three is a different fact
rather than a different degree of one -- see this module's docstring for why the middle one is
not collapsible into either neighbour.
"""


class PredictionRecordError(ValueError):
    """Raised when a batch cannot be turned into a record at all.

    A `ValueError` for `AlphaModelError`'s reason: every refusal on this contract is about a
    value that cannot mean what it says, and a caller catching `ValueError` catches pydantic's
    refusals from the same construction in the same clause.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class PredictionRecordLimitation:
    """One named boundary on what a stored prediction can be trusted to prove."""

    code: str
    detail: str


PREDICTION_RECORD_UNADDRESSED_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "recorded_at": (
            "The store's own clock reading at the moment it took custody -- "
            "`RUN_MANIFEST_UNADDRESSED_FIELDS`' first kind of excluded field, a wall clock. "
            "Addressing it would give one prediction a second address every time it was "
            "re-offered, so a write that crashed after the disk and before the acknowledgement "
            "would file two documents for one forecast and inflate every count taken over this "
            "store -- including the multiple-testing denominator this module's docstring says "
            "the store holds. What the exclusion costs is stated rather than hidden: the "
            "address does not commit to the custody stamp, so a document whose `recorded_at` "
            "was edited still re-derives to the name it is filed under, and `standing` is "
            "computed from it. That is a special case of the general fact that nothing here "
            "defends against whoever owns the disk, and it is not closable by a second address."
        )
    }
)
"""Every `PredictionRecord` field that is **recorded but not addressed** -- and there is one.

`ARTIFACT_UNADDRESSED_FIELDS`' shape with an entry instead of without one, and load-bearing for
the same reason: it is passed to `stable_model_id`, so adding a key really does remove that field
from the address. `tests/unit/domain/test_prediction_record.py` partitions
`PredictionRecord.model_fields` against it and moves each addressed field in turn, so field *n+1*
fails until somebody either measures it moving the address or writes down why it may not.

`exclude` reaches only the top level, so nothing inside `batch` can be kept out through this
mapping -- which is exactly the property "a backfill cannot collide with its original" rests on,
since `predicted_at` lives one level down.
"""


def outcome_known_at_for(
    batch: PredictionBatch, *, calendar: TradingCalendar, zone: tzinfo
) -> datetime:
    """The instant this batch's outcome becomes knowable: its label window's last close.

    Composed from `build_label_window` and `LabelWindow.close_instant` rather than computed here,
    because the outcome a prediction is judged against **is** the `OutcomeLabel` built at the
    same instant and horizon. A calculation of its own would be a second reading of one calendar.

    `zone` has no default, `build_label_window`'s rule: an instant is not a session date until a
    timezone says so, and a second default is a second place for one answer to drift.

    Propagates `CalendarHorizonError` when the outcome day falls outside the published calendar,
    and `LabelError` when the zone is too far west. Neither is repaired: a batch whose outcome
    day the calendar does not reach has no deadline, and inventing one is exactly the failure
    this contract exists to prevent.
    """
    window = build_label_window(
        as_of=batch.as_of,
        zone=zone,
        horizon=parse_horizon(batch.horizon),
        calendar=calendar,
    )
    return window.close_instant(window.exit_day)


class PredictionRecord(BaseModel):
    """One `PredictionBatch`, the instant its outcome becomes knowable, and when it was held.

    Frozen and `extra="forbid"`, `PredictionBatch`'s configuration, and for a sharper reason
    here: a stored record edited into agreement with what happened is the whole failure S32 names.

    Carries **no declared standing and no declared id**, and both are `computed_field`s -- the
    arrangement `AlphaModelArtifact.artifact_id` established, extended by one. `standing` is
    computed because a provenance a producer stamps is a provenance a producer chooses;
    `record_id` is computed because an address a producer supplies is not an address.

    Build it with `prediction_record_for`, which derives `outcome_known_at` from a calendar.
    This constructor is not the boundary -- `artifact_for` and `prediction_batch_for` are the
    precedents -- and the one thing it can check without a calendar is that the deadline is after
    the instant the batch stands at, which holds for every real window because an outcome window
    opens on the session *after* the prediction day.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["alpha-prediction-record/v1"] = "alpha-prediction-record/v1"
    batch: PredictionBatch
    outcome_known_at: datetime
    recorded_at: datetime
    supersedes: str | None = Field(default=None, pattern=PREDICTION_RECORD_ID_PATTERN)

    @field_validator("outcome_known_at", "recorded_at")
    @classmethod
    def normalize_instants(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_the_record_is_ordered_and_its_lineage_is_a_recomputations(self) -> Self:
        if self.outcome_known_at <= self.batch.as_of:
            raise ValueError(
                f"this batch stands at {self.batch.as_of.isoformat()} and its outcome window "
                f"closes at or before it, at {self.outcome_known_at.isoformat()}; an outcome "
                "window opens on the session after the prediction day, so a deadline that early "
                "describes an answer that already existed when the question was asked"
            )
        if self.recorded_at < self.batch.predicted_at:
            raise ValueError(
                f"this record was taken into custody at {self.recorded_at.isoformat()}, before "
                f"the batch it holds was produced at {self.batch.predicted_at.isoformat()}; "
                "Timeline's ingested_time may not precede its available_time, and a store that "
                "held a forecast before it existed is a clock fault rather than a record"
            )
        if self.supersedes is not None and self.standing != "backfill":
            raise ValueError(
                f"this record stands as {self.standing!r} and names {self.supersedes!r} as the "
                "record it supersedes; only a backfill recomputes something, because a "
                "prediction produced before its outcome existed has nothing to correct. A "
                "forward record naming an earlier one would read as a revision, which is "
                "exactly what Implementation Decision 14 forbids"
            )
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def record_id(self) -> str:
        """This record's content address (`prd_` and 24 hex characters).

        `stable_model_id` over every declared field less `PREDICTION_RECORD_UNADDRESSED_FIELDS`,
        which names `recorded_at` and says why. Not cached, ADR-0003's rule about
        `computed_field`s: read it once outside a loop.
        """
        return stable_model_id(
            prefix=PREDICTION_RECORD_PREFIX,
            model=self,
            exclude=frozenset(PREDICTION_RECORD_UNADDRESSED_FIELDS),
        )

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def standing(self) -> PredictionStanding:
        """Where this record sits relative to the instant its outcome became knowable.

        Two comparisons against one deadline, and the order matters. A batch produced at or after
        the deadline is a `backfill` whatever the store's clock says, because the answer already
        existed when the number was computed. Otherwise the store's own reading decides:
        `forward` when it held the bytes in time, `unwitnessed` when the claim is in time and
        this repository cannot corroborate it.
        """
        if self.batch.predicted_at >= self.outcome_known_at:
            return "backfill"
        return "forward" if self.recorded_at < self.outcome_known_at else "unwitnessed"


def prediction_record_for(
    *,
    batch: PredictionBatch,
    calendar: TradingCalendar,
    zone: tzinfo,
    recorded_at: datetime,
    supersedes: str | None = None,
) -> PredictionRecord:
    """Seal `batch` against the calendar's answer to when its outcome becomes knowable.

    The deadline is derived here and is not a parameter, which is the whole point of the
    function: `artifact_for` derives the three fields a caller would otherwise type, and this
    derives the one a caller would otherwise choose.

    `recorded_at` **is** a parameter, and deliberately: a hidden `datetime.now()` here would make
    every record unreproducible and every test order-dependent, which is `FittedAlphaModel
    .predict`'s own argument about `predicted_at`. The store is what reads a real clock, and
    `FilePredictionStore.put` is the only caller that should pass one it did not choose.
    """
    return PredictionRecord(
        batch=batch,
        outcome_known_at=outcome_known_at_for(batch, calendar=calendar, zone=zone),
        recorded_at=recorded_at,
        supersedes=supersedes,
    )


PREDICTION_RECORD_VERSIONS: ContractVersions[PredictionRecord] = ContractVersions(
    name="alpha-prediction-record",
    current_version="alpha-prediction-record/v1",
    versions={"alpha-prediction-record/v1": PredictionRecord},
)
"""`V2-P4-011`'s open question, answered by this issue storing a row.

That issue wrote: *"a version registry earns its migration machinery when something has stored a
row -- which is `V2-P4-017`'s."* This is that row, and `read_versioned` is the single entry point
every deserializing store in this package reads through -- so a `PredictionRecord` written by a
newer build reaching older code fails by name instead of failing as a `Literal` mismatch a caller
cannot act on.

**One registry and not five.** `read_versioned` reads `schema_version` off the payload's
top-level dict, so only the document root can carry a chain; `PredictionBatch`, `Prediction`,
`AlphaModelArtifact` and `AlphaModelDeclaration` are nested and keep their `Literal`s. The
consequence is worth stating rather than discovering: bumping any of those four is a bump of this
record too, and the upgrade that arrives with it will be a **refusing** one for
`refuse_run_manifest_v2_upgrade`'s reason -- this record's stored key is its own content address,
so a transparent upcast would move the name every reference already holds.
"""


KNOWN_PREDICTION_RECORD_LIMITATIONS: Final[tuple[PredictionRecordLimitation, ...]] = (
    PredictionRecordLimitation(
        code="nothing_here_defends_against_whoever_owns_the_disk",
        detail=(
            "Every guarantee in this module is against a caller inside the process. The "
            "documents are files on a local-first single-user filesystem (ADR-0002), so an "
            "operator can edit any stamp, delete any document, re-address a file after "
            "changing it, or set the machine clock back before a write, and no check here "
            "would notice. What is defended is the ordinary and far commoner mistake: a study "
            "run today over last year's data, stored, and later read as if it had been "
            "registered in advance. A claim a third party could check needs a timestamp "
            "somebody else controls -- a notary, a signed receipt, an external log -- and this "
            "repository has none and this issue builds none."
        ),
    ),
    PredictionRecordLimitation(
        code="the_address_does_not_commit_to_the_custody_stamp",
        detail=(
            "`recorded_at` is in PREDICTION_RECORD_UNADDRESSED_FIELDS, so a stored document "
            "whose custody stamp was edited still re-derives to the filename it sits under, and "
            "`FilePredictionStore.get`'s re-addressing check cannot see it -- while `standing` "
            "is computed from exactly that field. Including it would close this and open a "
            "worse one: one prediction would take a second address every time it was re-offered "
            "(measured in `test_every_field_reaches_the_address_except_the_one_this_mapping_"
            "names`), so a retried write would file two documents for one forecast. The choice "
            "is between a hole an operator can walk through and a defect an ordinary retry "
            "walks into, and it is only a real hole for someone who already has the disk."
        ),
    ),
    PredictionRecordLimitation(
        code="a_deadline_is_only_as_honest_as_the_calendar_it_was_derived_from",
        detail=(
            "`outcome_known_at_for` cannot be lied to with a datetime, but it can be lied to "
            "with a calendar: a TradingCalendar with sessions removed pushes `exit_day` later "
            "and moves the deadline with it, which turns a backfill into a forward record. What "
            "stops that in practice is that the calendar is ingested panel data with its own "
            "point-in-time gate and its own KNOWN_CALENDAR_LOOKAHEAD registry, not an argument "
            "invented at the call site -- but this contract does not check which calendar it "
            "was handed, and cannot, because a record does not carry one."
        ),
    ),
    PredictionRecordLimitation(
        code="the_store_never_checks_that_its_own_clock_moved_forward",
        detail=(
            "`FilePredictionStore` stamps `recorded_at` from an injected clock and compares it "
            "to one deadline. It never compares it to the custody stamps already on disk, so a "
            "clock that went backwards between two writes produces two records whose stored "
            "order contradicts their stamps and nothing objects. Checking would cost a scan of "
            "every held document on every write, which is the access pattern this store is "
            "shaped to avoid; an append-only custody log with a monotonicity check is the "
            "cheaper answer and is left to whichever issue first needs custody order to be "
            "evidence rather than bookkeeping."
        ),
    ),
    PredictionRecordLimitation(
        code="the_retrospective_half_of_decision_12s_third_clause_leaves_no_trace_in_a_record",
        detail=(
            "Implementation Decision 12's third clause asks for a final holdout left untouched "
            "through model **selection**. The forward-looking half lands here and is stronger "
            "than any split -- a `forward` record could not have consulted an outcome that had "
            "not printed, which is the sentence V2-P4-013 wrote when it declined the clause. "
            "The retrospective half does not: nothing a record carries says what its author had "
            "already looked at when choosing the declaration, and a store cannot know. What "
            "this store does supply is the denominator -- every declaration laid down against "
            "an as_of is a document, so how many were tried is a count rather than a "
            "recollection -- and supplying a denominator is not applying a correction."
        ),
    ),
    PredictionRecordLimitation(
        code="a_backfill_with_no_antecedent_is_admitted_because_most_recomputations_have_none",
        detail=(
            "`supersedes` is optional, so a backfill may name nothing. Refusing that would "
            "refuse every ordinary historical backtest, which recomputes a prediction for a day "
            "no forward record exists for -- the common case, not the exception. The cost is "
            "that lineage is a discipline where it could have been a gate: a recomputation that "
            "really does replace an earlier answer and omits `supersedes` reads as an "
            "unrelated backfill. Nothing is lost when it happens, because the original is still "
            "held and still readable; what is lost is the edge between them."
        ),
    ),
    PredictionRecordLimitation(
        code="the_supersedes_edge_is_contract_only_because_no_face_offers_a_record_to_name",
        detail=(
            "Stronger than the entry above it, and measured by V2-P4-093 rather than intended: "
            "`supersedes` is not merely optional, it is unreachable. `model_view.run_daily` is "
            "the only caller of `FilePredictionStore.put` in `src/` and passes three keywords, "
            "and none of the three faces above it carries a fourth -- no CLI flag, no field on "
            "`DailyRunRequest` or `ModelDailyRunApiRequest`, no SDK parameter. So the "
            "V2-P4-049-style referent check inside `put`, which refuses a `supersedes` naming "
            "nothing held, cannot fire in shipped code: it guards a contract rather than a path "
            "a user can walk, exactly as `load_index_prices` was a gated reader with no caller "
            "until V2-P4-083 wired one. Exposing it was considered and declined here, and the "
            "reason is the flag's own argument: every face would have to answer *which* record "
            "is being corrected, and the only honest source of that is a `record_id` read off "
            "an earlier run -- `held_prediction`'s address, not a daily run's input -- so the "
            "flag belongs to whichever issue first gives a face a reason to hold one. What "
            "stops this from being prose is "
            "`test_the_supersedes_lineage_is_contract_only_and_no_shipped_face_can_supply_one`, "
            "which reads the call sites off the AST and goes red the day somebody wires it."
        ),
    ),
    PredictionRecordLimitation(
        code="one_fit_still_has_two_addresses_so_a_record_names_a_declaration_and_not_a_run",
        detail=(
            "V2-P4-016 measured that `seed` enters `AlphaModelArtifact.artifact_id` and does "
            "not affect the fit: two seeds give byte-identical coefficients and two addresses. "
            "A record carries the artifact by value and inherits that whole, so 'which model "
            "produced this prediction' resolves to a declaration rather than to a run, and two "
            "records of one forecast under two seeds are two records with two addresses and "
            "identical numbers. Nothing here narrows it -- the address is V2-P4-016's and this "
            "issue is an addition to it, exactly as that issue was an addition to V2-P4-011."
        ),
    ),
)
"""Eight named boundaries on what a stored prediction proves.

Each code is asserted by set equality in `tests/unit/domain/test_prediction_record.py`, which is
the shape every registry in this repository uses: equality rather than membership, because
membership cannot see a deletion.
"""


def is_prediction_record_id(value: str) -> bool:
    """Whether `value` is an address this contract produced, and nothing else.

    Exported so the store can refuse an unusable key without spelling the pattern again --
    `FileShortlistStore`'s `SHORTLIST_ID_PATTERN` had to declare its own because
    `shortlist_view` sits *above* `storage/` and cannot be imported from it. This contract sits
    below, so the check can be shared instead of copied.
    """
    return _ADDRESS_SHAPE.fullmatch(value) is not None
