"""`V2-P4-017`'s contract: what a stored prediction carries, and which clock says what.

Every batch here comes off the reference model in `tests/alpha_model_fixtures.py`, fitted on real
`OutcomeLabel`s against a real `TradingCalendar`, so the deadline these tests compare against is
one the calendar actually holds rather than a datetime typed into a fixture.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from alpha_model_fixtures import SHANGHAI, cross_section, fitted_reference, trading_calendar
from pydantic import ValidationError

from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import build_label_window
from openalpha_cn.domain.prediction_record import (
    KNOWN_PREDICTION_RECORD_LIMITATIONS,
    PREDICTION_RECORD_ID_PATTERN,
    PREDICTION_RECORD_PREFIX,
    PREDICTION_RECORD_UNADDRESSED_FIELDS,
    PREDICTION_RECORD_VERSIONS,
    PredictionRecord,
    is_prediction_record_id,
    outcome_known_at_for,
    prediction_record_for,
)
from openalpha_cn.domain.time import Timeline
from openalpha_cn.domain.versioning import UnknownSchemaVersionError, read_versioned

AS_OF = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
"""15:00 Asia/Shanghai on a Monday the fixture calendar holds, so the window builds."""

IN_TIME = datetime(2026, 6, 15, 7, 30, tzinfo=UTC)
"""Half an hour after the cross section was readable, two days before the outcome prints."""

CUSTODY_IN_TIME = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
AFTER_THE_OUTCOME = datetime(2026, 6, 20, 1, 0, tzinfo=UTC)
LATER_STILL = datetime(2026, 6, 20, 2, 0, tzinfo=UTC)

DEADLINE = datetime(2026, 6, 17, 7, 0, tzinfo=UTC)
"""2026-06-17 15:00 Asia/Shanghai: the close of the last session the 1d outcome window reads.

Written out here so the tests below compare against a stated instant rather than against a second
call of the function they are testing; `test_the_deadline_is_the_close_of_the_outcome_windows_last
_session` is what ties this constant back to the calendar.
"""


def batch(*, predicted_at: datetime = IN_TIME, as_of: datetime = AS_OF) -> PredictionBatch:
    """A real batch off the reference model, dated inside the fixture calendar."""
    return fitted_reference().predict(cross_section(as_of=as_of), predicted_at=predicted_at)


def record(
    *,
    predicted_at: datetime = IN_TIME,
    recorded_at: datetime = CUSTODY_IN_TIME,
    supersedes: str | None = None,
) -> PredictionRecord:
    return prediction_record_for(
        batch=batch(predicted_at=predicted_at),
        calendar=trading_calendar(),
        zone=SHANGHAI,
        recorded_at=recorded_at,
        supersedes=supersedes,
    )


def test_the_deadline_is_the_close_of_the_outcome_windows_last_session() -> None:
    """ "Before the outcome is known" is one instant, and it is the label's own.

    `V2-P4-011` left this as *"needs a calendar and a store"*. The calendar half is not a new
    rule: the outcome a prediction will be judged against is the `OutcomeLabel` built at the same
    instant and horizon, `build_label_window` is what builds that window, and the answer exists
    the moment its last close prints. So the deadline is `close_instant(exit_day)` of the very
    window a label would use -- one function, not a second reading of the same calendar.
    """
    subject = batch()
    window = build_label_window(
        as_of=subject.as_of,
        zone=SHANGHAI,
        horizon=parse_horizon(subject.horizon),
        calendar=trading_calendar(),
    )

    deadline = outcome_known_at_for(subject, calendar=trading_calendar(), zone=SHANGHAI)

    assert deadline == window.close_instant(window.exit_day)
    assert deadline == DEADLINE
    assert deadline > subject.as_of


def test_the_deadline_is_derived_and_a_caller_cannot_offer_one() -> None:
    """`artifact_for`'s rule, applied to the one field a liar would most want to choose.

    A record whose deadline arrived as a parameter would let any batch be declared forward by
    naming a date far enough away. `prediction_record_for` takes a **calendar** instead, which is
    published data this repository already gates point-in-time, and computes the instant itself.
    """
    parameters = set(prediction_record_for.__annotations__)

    assert "outcome_known_at" not in parameters
    assert {"batch", "calendar", "zone", "recorded_at", "supersedes"} <= parameters


def test_a_prediction_the_store_took_custody_of_before_the_outcome_existed_stands_forward() -> None:
    """Story S32, and the only standing that is evidence of it.

    Both instants are before the deadline: the batch says it was produced in time and the store
    says it held the bytes in time. That conjunction is the whole of what this repository can
    assert about "before the outcome was known".
    """
    held = record()

    assert held.standing == "forward"
    assert held.batch.predicted_at < held.outcome_known_at
    assert held.recorded_at < held.outcome_known_at


def test_a_backdated_prediction_cannot_reach_forward_standing_by_stamping_itself() -> None:
    """The liar, and exactly how far the lie gets.

    `predicted_at` is whatever the caller passed to `predict`; nothing in this repository can
    check it. What the caller does not set is when the store took custody, so a batch stamped in
    time that arrives after the answer exists lands as `unwitnessed` -- the standing that says
    the claim is unrefuted and uncorroborated. It is deliberately *not* `forward`, and
    deliberately *not* `backfill` either, because the two failures are different: one is a claim
    this repository cannot check, the other is a recomputation that says so.
    """
    held = record(predicted_at=IN_TIME, recorded_at=AFTER_THE_OUTCOME)

    assert held.batch.predicted_at < held.outcome_known_at
    assert held.recorded_at > held.outcome_known_at
    assert held.standing == "unwitnessed"


def test_a_prediction_produced_after_the_outcome_existed_is_a_backfill() -> None:
    """Implementation Decision 14's `回溯重算`, recognised from the stamps rather than declared."""
    held = record(predicted_at=AFTER_THE_OUTCOME, recorded_at=LATER_STILL)

    assert held.standing == "backfill"


def test_the_standing_is_computed_and_a_caller_cannot_supply_one() -> None:
    """A provenance a producer stamps is a provenance a producer chooses."""
    assert "standing" not in PredictionRecord.model_fields
    assert "standing" in PredictionRecord.model_computed_fields

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PredictionRecord(
            batch=batch(),
            outcome_known_at=DEADLINE,
            recorded_at=CUSTODY_IN_TIME,
            standing="forward",  # type: ignore[call-arg]
        )


def test_only_a_backfill_may_name_the_record_it_recomputes() -> None:
    """A lineage edge is a recomputation's, and an original has nobody to point at.

    The refusal is what makes `supersedes` mean one thing. Without it a forward record could name
    an earlier forward record and the two would read as a revision -- which is the shape
    Implementation Decision 14 exists to forbid.
    """
    original = record()

    superseding = record(
        predicted_at=AFTER_THE_OUTCOME, recorded_at=LATER_STILL, supersedes=original.record_id
    )
    assert superseding.supersedes == original.record_id
    assert superseding.standing == "backfill"

    with pytest.raises(ValidationError, match="only a backfill recomputes"):
        record(supersedes=original.record_id)
    with pytest.raises(ValidationError, match="only a backfill recomputes"):
        record(recorded_at=AFTER_THE_OUTCOME, supersedes=original.record_id)


def test_a_recomputation_addresses_somewhere_the_original_never_could() -> None:
    """Implementation Decision 14's second clause, as arithmetic rather than as a store rule.

    "回溯重算存为独立制品" -- and never replacing the original -- is usually read as a rule about
    writing. It is stronger here: a backfill of one prediction cannot *collide* with its original,
    because the
    two disagree about `predicted_at` -- forward is before the deadline and a backfill is at or
    after it -- and `predicted_at` reaches the address through the batch. So the store never has
    to choose between two documents under one key; there is no such pair.
    """
    original = record()
    recomputed = record(predicted_at=AFTER_THE_OUTCOME, recorded_at=LATER_STILL)

    assert original.batch.predictions == recomputed.batch.predictions
    assert original.batch.artifact == recomputed.batch.artifact
    assert original.record_id != recomputed.record_id


def test_the_address_carries_the_prefix_and_the_shape_every_address_here_carries() -> None:
    held = record()

    assert held.record_id.startswith(f"{PREDICTION_RECORD_PREFIX}_")
    assert PREDICTION_RECORD_PREFIX == "prd"
    assert re.fullmatch(PREDICTION_RECORD_ID_PATTERN, held.record_id)


def test_the_lineage_pattern_needs_both_anchors_and_the_key_check_needs_neither() -> None:
    """Two consumers of one shape, and a mutation sweep is what showed they want two anchorings.

    Three survivors said the same thing from three sides -- dropping the `$`, widening `{24}` to
    `{24,}`, and swapping `fullmatch` for `match` all left every test green -- and the reading
    they forced is that this module had one expression answering two questions where it is only
    right for one.

    - **pydantic's `pattern` is search-shaped**, so the anchored form is what `supersedes` needs:
      without the `$` it takes `prd_<24 hex>zz` outright.
    - **`is_prediction_record_id` uses `fullmatch` on the unanchored form**, which is
      `SHORTLIST_ID_PATTERN`'s arrangement and its reason: `$` also matches *before* a final
      newline, so `re.match` against the anchored form takes a key ending in `\\n` -- and this
      token becomes a filename component.

    The newline case landed differently from the way this test first asserted it, and the
    correction is worth keeping: `PredictionRecord` carries `str_strip_whitespace=True`, so a
    `supersedes` with a trailing newline is **normalised** before the pattern ever runs and what
    is stored is the clean address. That is safe, and it is safe for a different reason than the
    anchors -- which is exactly why the key check may not lean on it. `is_prediction_record_id`
    is handed raw filesystem input and strips nothing.
    """
    good = "prd_" + "0" * 24

    assert is_prediction_record_id(good)
    for refused in (good + "0", good + "zz", good + "\n", "x" + good, good.upper()):
        assert not is_prediction_record_id(refused), refused

    def with_supersedes(value: str) -> PredictionRecord:
        return PredictionRecord(
            batch=batch(predicted_at=AFTER_THE_OUTCOME),
            outcome_known_at=DEADLINE,
            recorded_at=LATER_STILL,
            supersedes=value,
        )

    for refused in (good + "0", good + "zz", "x" + good, good.upper()):
        with pytest.raises(ValidationError, match="String should match pattern"):
            with_supersedes(refused)

    assert with_supersedes(good + "\n").supersedes == good


def test_producing_or_receiving_a_batch_exactly_when_the_outcome_prints_is_not_before_it() -> None:
    """Both boundaries are `strictly before`, and both were mutation survivors until measured.

    At the instant the last close prints the answer exists, so a number computed then was not
    computed before it and custody taken then was not taken before it. Both comparisons are
    therefore exclusive, which is the **opposite** of the call `PredictionBatch` makes about
    `as_of == training_cutoff` -- and deliberately so: there, equality means a model trained
    through last night's close predicting as of it, which is what a daily model does; here,
    equality means the forecast and its answer exist at the same instant.
    """
    produced_at_the_deadline = record(predicted_at=DEADLINE, recorded_at=DEADLINE)
    received_at_the_deadline = record(predicted_at=IN_TIME, recorded_at=DEADLINE)

    assert produced_at_the_deadline.standing == "backfill"
    assert received_at_the_deadline.standing == "unwitnessed"


def test_a_store_that_took_custody_the_instant_a_batch_was_produced_is_admitted() -> None:
    """`Timeline`'s own call, and the one place this record's orderings are inclusive.

    `ingested_time == available_time` is legal for every `Timeline` in this repository -- a
    record produced and filed in the same tick is a fast path or a coarse clock, not a fault --
    so the refusal above it is `<` and not `<=`.
    """
    same_tick = record(predicted_at=IN_TIME, recorded_at=IN_TIME)

    assert same_tick.recorded_at == same_tick.batch.predicted_at
    assert same_tick.standing == "forward"
    assert (
        Timeline(
            event_time=same_tick.batch.as_of,
            available_time=same_tick.batch.predicted_at,
            ingested_time=same_tick.recorded_at,
            revision_time=same_tick.recorded_at,
        ).ingested_time
        == same_tick.batch.predicted_at
    )


def test_every_field_reaches_the_address_except_the_one_this_mapping_names() -> None:
    """`ARTIFACT_UNADDRESSED_FIELDS`' audit, with one entry instead of none.

    `recorded_at` is the store's own clock reading, which is `RUN_MANIFEST_UNADDRESSED_FIELDS`'
    first kind of excluded field. Addressing it would give one prediction a second address every
    time it was re-offered, so a crashed-and-retried write would file two documents for one
    forecast and inflate every count taken over this store.
    """
    declared = set(PredictionRecord.model_fields)
    excluded = set(PREDICTION_RECORD_UNADDRESSED_FIELDS)

    assert excluded == {"recorded_at"}
    assert excluded <= declared

    baseline = record()
    for field in sorted(declared - excluded - {"schema_version"}):
        moved = {
            "batch": record(predicted_at=datetime(2026, 6, 15, 7, 45, tzinfo=UTC)),
            "outcome_known_at": baseline.model_copy(
                update={"outcome_known_at": DEADLINE.replace(hour=8)}
            ),
            "supersedes": record(
                predicted_at=AFTER_THE_OUTCOME,
                recorded_at=LATER_STILL,
                supersedes=baseline.record_id,
            ),
        }[field]
        assert moved.record_id != baseline.record_id, field

    later_custody = baseline.model_copy(update={"recorded_at": LATER_STILL})
    assert later_custody.record_id == baseline.record_id


def test_a_record_carries_three_of_timelines_four_clocks_and_adds_a_deadline() -> None:
    """Which clocks a stored prediction needs, measured against the contract that owns them.

    `Timeline` is this repository's four-clock shape and its two orderings are exactly the two a
    stored prediction needs: the store cannot have received a batch before it was produced, and
    nothing may precede the instant the prediction became available. The fourth clock is the one
    a prediction store must not have. `revision_time` is where the evidence plane records that a
    fact was reissued *in place*; Implementation Decision 14 forbids exactly that here, so the
    only honest value for it is a copy of `ingested_time` -- which is another way of saying a
    record is never revised, and a recomputation is a second record.
    """
    held = record()

    timeline = Timeline(
        event_time=held.batch.as_of,
        available_time=held.batch.predicted_at,
        ingested_time=held.recorded_at,
        revision_time=held.recorded_at,
    )

    assert timeline.event_time <= timeline.available_time <= timeline.ingested_time
    assert timeline.revision_time == timeline.ingested_time
    assert held.outcome_known_at > timeline.event_time
    assert not any(
        held.outcome_known_at == getattr(timeline, clock)
        for clock in ("event_time", "available_time", "ingested_time", "revision_time")
    )


def test_a_store_cannot_have_held_a_batch_before_it_was_produced() -> None:
    """`Timeline`'s `ingested_time >= available_time`, enforced where this contract can see it."""
    with pytest.raises(ValidationError, match="before the batch it holds was produced"):
        record(predicted_at=IN_TIME, recorded_at=datetime(2026, 6, 15, 7, 15, tzinfo=UTC))


def test_a_deadline_at_or_before_the_instant_the_batch_stands_at_is_refused() -> None:
    """A hand-built record cannot claim its outcome was already knowable when it was made.

    The constructor is not the boundary -- `prediction_record_for` is, and it derives the
    deadline -- but the one thing a deadline can be checked against without a calendar is the
    batch's own `as_of`, because an outcome window opens on the session *after* the prediction
    day and therefore always closes strictly later.
    """
    with pytest.raises(ValidationError, match="closes at or before"):
        PredictionRecord(batch=batch(), outcome_known_at=AS_OF, recorded_at=CUSTODY_IN_TIME)


def test_a_stored_document_carries_what_was_declared_and_nothing_that_was_derived() -> None:
    """The first thing measured here was a claim of this issue's own, and it was false.

    A record with `extra="forbid"` and two `computed_field`s does **not** round trip through a
    plain `model_dump_json`: the dump carries `record_id`, `standing` and -- one level down --
    `artifact_id`, and re-validating refuses all three as extra inputs. The repository had
    already answered this and this issue had not looked: `storage/sqlite.py:191` writes
    `manifest.model_dump_json(exclude_computed_fields=True)`, and `V2-P4-016` chose a computed
    address for `AlphaModelArtifact` at a moment when nothing had ever stored one.

    Which turns out to be the right shape rather than a workaround, because the exclusion is the
    **same** one `stable_model_id` applies: the bytes a document is made of are the bytes the
    address is taken over, less the one field `PREDICTION_RECORD_UNADDRESSED_FIELDS` names. A
    document that carried its own address could carry a wrong one; this one cannot carry an
    address at all, so the filename is the only place it is written and `get` re-derives it.
    """
    held = record()
    payload = held.model_dump_json(exclude_computed_fields=True)

    stored = json.loads(payload)
    assert sorted(stored) == [
        "batch",
        "outcome_known_at",
        "recorded_at",
        "schema_version",
        "supersedes",
    ]
    assert "artifact_id" not in stored["batch"]["artifact"]
    assert PredictionRecord.model_validate_json(payload) == held

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PredictionRecord.model_validate_json(held.model_dump_json())


def test_a_stored_row_reads_back_through_the_one_versioned_read_path() -> None:
    """`V2-P4-011` asked whether these contracts earn a `ContractVersions`; storing a row is when.

    That issue's criterion, verbatim: *"a version registry earns its migration machinery when
    something has stored a row -- which is `V2-P4-017`'s."* This is that issue, and the registry
    is one rather than five: `read_versioned` dispatches on the payload's **top-level**
    `schema_version`, so only the document root can carry a chain. The four nested `Literal`s
    below it stay `Literal`s, and the consequence is worth stating where a reader meets it --
    bumping `PredictionBatch` or `AlphaModelArtifact` is a bump of this record too.

    The registry's `name` is asserted against the version string it prefixes rather than against
    the raised message. A mutation survived here: `UnknownSchemaVersionError` quotes the version
    it *found*, so a `match=` on `"alpha-prediction-record"` is satisfied by the payload's own
    `alpha-prediction-record/v2` even when the registry has been renamed to something else
    entirely -- an assertion passing on the wrong half of its own message.
    """
    held = record()
    payload = held.model_dump_json(exclude_computed_fields=True)

    assert read_versioned(PREDICTION_RECORD_VERSIONS, payload) == held
    assert PREDICTION_RECORD_VERSIONS.current_version == "alpha-prediction-record/v1"
    assert PREDICTION_RECORD_VERSIONS.current_version.startswith(
        f"{PREDICTION_RECORD_VERSIONS.name}/"
    )
    assert PREDICTION_RECORD_VERSIONS.name == "alpha-prediction-record"

    with pytest.raises(UnknownSchemaVersionError, match="alpha-prediction-record"):
        read_versioned(
            PREDICTION_RECORD_VERSIONS,
            payload.replace("alpha-prediction-record/v1", "alpha-prediction-record/v2", 1),
        )


def test_the_known_limitations_are_named_rather_than_argued_away() -> None:
    """Set equality rather than membership, because membership cannot see a deletion."""
    assert {entry.code for entry in KNOWN_PREDICTION_RECORD_LIMITATIONS} == {
        "nothing_here_defends_against_whoever_owns_the_disk",
        "the_address_does_not_commit_to_the_custody_stamp",
        "a_deadline_is_only_as_honest_as_the_calendar_it_was_derived_from",
        "the_store_never_checks_that_its_own_clock_moved_forward",
        "the_retrospective_half_of_decision_12s_third_clause_leaves_no_trace_in_a_record",
        "a_backfill_with_no_antecedent_is_admitted_because_most_recomputations_have_none",
        "one_fit_still_has_two_addresses_so_a_record_names_a_declaration_and_not_a_run",
    }
