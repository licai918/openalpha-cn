"""`V2-P4-017`'s store: predictions land before the outcome exists, and nothing replaces them.

Every batch here comes off the reference model against the fixture calendar, so a "deadline" in
this file is a session close the calendar holds. The clock is injected in every test -- a store
that read `datetime.now()` would make these order-dependent, and the whole point of the field it
stamps is that a caller does not choose it.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from alpha_model_fixtures import SHANGHAI, cross_section, fitted_reference, trading_calendar
from pydantic import ValidationError

from openalpha_cn.api.app import ModelDailyRunApiRequest
from openalpha_cn.domain.alpha_model import PredictionBatch
from openalpha_cn.domain.labels import LabelError
from openalpha_cn.domain.prediction_record import PREDICTION_RECORD_VERSIONS
from openalpha_cn.domain.trading_calendar import CalendarHorizonError
from openalpha_cn.domain.versioning import (
    STORED_DOCUMENT_FAULTS,
    IdentityRewriteRequiredError,
    UnknownSchemaVersionError,
)
from openalpha_cn.model_view import DailyRunRequest
from openalpha_cn.sdk import OpenAlphaSDK
from openalpha_cn.storage.predictions import (
    PREDICTION_DOCUMENT_SUFFIX,
    FilePredictionStore,
    PredictionStoreError,
)

SOURCE_ROOT: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "openalpha_cn"

AS_OF = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
IN_TIME = datetime(2026, 6, 15, 7, 30, tzinfo=UTC)
CUSTODY_IN_TIME = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
CUSTODY_LATER = datetime(2026, 6, 16, 8, 0, tzinfo=UTC)
AFTER_THE_OUTCOME = datetime(2026, 6, 20, 1, 0, tzinfo=UTC)
LATER_STILL = datetime(2026, 6, 20, 2, 0, tzinfo=UTC)


def clock_at(*instants: datetime) -> Callable[[], datetime]:
    """A clock that reads each instant in turn and then repeats the last one."""
    readings = list(instants)

    def read() -> datetime:
        return readings.pop(0) if len(readings) > 1 else readings[0]

    return read


def store(path: Path, *instants: datetime) -> FilePredictionStore:
    return FilePredictionStore(path, clock=clock_at(*(instants or (CUSTODY_IN_TIME,))))


def batch(*, predicted_at: datetime = IN_TIME, as_of: datetime = AS_OF) -> PredictionBatch:
    return fitted_reference().predict(
        cross_section(as_of=as_of), predicted_at=predicted_at, shelf_life=None
    )


def put(subject: FilePredictionStore, **kwargs: object) -> object:
    return subject.put(calendar=trading_calendar(), zone=SHANGHAI, **kwargs)  # type: ignore[arg-type]


def test_a_prediction_lands_under_its_own_address_and_reads_back_identical(tmp_path: Path) -> None:
    """The whole path: a real fit, a real cross section, a real calendar, a document on disk."""
    subject = store(tmp_path)

    written = put(subject, batch=batch())

    assert written.outcome == "created"  # type: ignore[attr-defined]
    held = written.record  # type: ignore[attr-defined]
    assert held.standing == "forward"
    assert subject.list_ids() == (held.record_id,)
    assert subject.get(held.record_id) == held


def test_the_store_stamps_custody_from_its_own_clock_and_never_from_the_caller(
    tmp_path: Path,
) -> None:
    """What survives a liar, stated as the difference two stores make to one batch.

    The batch is byte-identical in both -- same fit, same `as_of`, same `predicted_at` claiming
    to be two days before the outcome prints. What differs is only when each store read its own
    clock, and that alone decides whether the claim is corroborated. `put` has no `recorded_at`
    parameter at all, which is what makes this structural rather than conventional.
    """
    in_time = put(store(tmp_path / "a", CUSTODY_IN_TIME), batch=batch())
    late = put(store(tmp_path / "b", AFTER_THE_OUTCOME), batch=batch())

    assert in_time.record.batch == late.record.batch  # type: ignore[attr-defined]
    assert in_time.record.standing == "forward"  # type: ignore[attr-defined]
    assert late.record.standing == "unwitnessed"  # type: ignore[attr-defined]
    assert "recorded_at" not in FilePredictionStore.put.__annotations__


def test_a_recomputation_lands_beside_the_original_and_leaves_it_byte_for_byte(
    tmp_path: Path,
) -> None:
    """Implementation Decision 14's second clause, measured on the bytes rather than argued.

    `V2-P4-071` faced this shape on the factor plane and turned a whole-partition replace into an
    append; `V2-P4-073` then measured that the guard auditing that merge covered only half of it,
    because the merge had been split in two and a "by construction" implication had quietly
    broken. The lesson taken here is the first half of that pair -- **remove the overwrite rather
    than guard it**. There is no merge in this store: one record, one document, written once. So
    the original is not preserved by a check that could have a hole in it; there is no write path
    that could touch it.
    """
    subject = store(tmp_path, CUSTODY_IN_TIME, LATER_STILL)
    original = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    document = tmp_path / f"{original.record_id}{PREDICTION_DOCUMENT_SUFFIX}"
    before = document.read_bytes()

    recomputed = put(
        subject, batch=batch(predicted_at=AFTER_THE_OUTCOME), supersedes=original.record_id
    ).record  # type: ignore[attr-defined]

    assert recomputed.record_id != original.record_id
    assert recomputed.standing == "backfill"
    assert recomputed.supersedes == original.record_id
    assert document.read_bytes() == before
    assert subject.get(original.record_id) == original
    assert sorted(subject.list_ids()) == sorted((original.record_id, recomputed.record_id))


def test_a_second_write_of_one_prediction_keeps_the_document_already_held(tmp_path: Path) -> None:
    """`FileShortlistStore.put`'s `unchanged` rule, and the reason it is sharper here.

    The two documents differ in exactly one field, `recorded_at`, which is the one field the
    address does not carry -- so the second write reaches the first's key. Keeping the held
    document means the store's answer to "when did you first hold this" is the *first* time, not
    the most recent, and the returned record is the held one so a caller can see that.
    """
    subject = store(tmp_path, CUSTODY_IN_TIME, CUSTODY_LATER)
    first = put(subject, batch=batch())
    document = tmp_path / f"{first.record.record_id}{PREDICTION_DOCUMENT_SUFFIX}"  # type: ignore[attr-defined]
    before = document.read_bytes()

    second = put(subject, batch=batch())

    assert second.outcome == "unchanged"  # type: ignore[attr-defined]
    assert second.record.recorded_at == CUSTODY_IN_TIME  # type: ignore[attr-defined]
    assert second.record == first.record  # type: ignore[attr-defined]
    assert document.read_bytes() == before
    assert subject.list_ids() == (first.record.record_id,)  # type: ignore[attr-defined]


def test_a_recomputation_naming_a_record_this_store_does_not_hold_is_refused(
    tmp_path: Path,
) -> None:
    """A lineage edge that resolves to nothing is worse than no edge at all.

    `V2-P4-049` is the precedent and it is a measured one: a `run_manifest_id` that only had to
    *match a pattern* let a fabricated signal clear a 1.0 gate. A `supersedes` checked only
    against `PREDICTION_RECORD_ID_PATTERN` would be the same defect -- a recomputation that
    claims to correct an original nobody can produce.
    """
    subject = store(tmp_path, LATER_STILL)

    with pytest.raises(PredictionStoreError, match="holds no record"):
        put(
            subject,
            batch=batch(predicted_at=AFTER_THE_OUTCOME),
            supersedes="prd_" + "0" * 24,
        )

    assert subject.list_ids() == ()


def _prediction_put_call_sites() -> set[tuple[str, frozenset[str]]]:
    """Every `<something>.put(batch=...)` call in `src/`, with the keywords it passes.

    Keyed on `batch=`, which is this store's own shape -- the watchlist, shortlist and
    experiment stores all take something else -- and read off the AST rather than off a grep so
    that a keyword split across lines still counts.
    """
    found: set[tuple[str, frozenset[str]]] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "put":
                continue
            keywords = frozenset(item.arg for item in node.keywords if item.arg is not None)
            if "batch" in keywords:
                found.add((path.relative_to(SOURCE_ROOT).as_posix(), keywords))
    return found


def test_the_supersedes_lineage_is_contract_only_and_no_shipped_face_can_supply_one() -> None:
    """`V2-P4-093`: the referent check above cannot fire in shipped code, and that is recorded.

    `put` refuses a `supersedes` naming nothing held, which is `V2-P4-049`'s lesson applied. The
    acceptance measured that nothing can reach it: `run_daily` is the only caller and passes
    three keywords, and none of the three faces above it carries a fourth -- no CLI flag, no
    field on either request model, no SDK parameter. So
    `test_a_recomputation_naming_a_record_this_store_does_not_hold_is_refused` exercises a
    contract, not a path a user can walk.

    Exposing it was considered and not done, and the reason is recorded rather than implied:
    every face that would have to carry the flag also has to answer *which* record is being
    corrected, and the only honest source of that answer is a `record_id` the caller read off an
    earlier run -- which is `held_prediction`'s address and not a daily run's input.
    `the_supersedes_edge_is_contract_only_because_no_face_offers_a_record_to_name` is where a
    reader meets it, and this test is what turns wiring one into a red rather than a surprise.
    """
    assert _prediction_put_call_sites() == {
        ("model_view.py", frozenset({"batch", "calendar", "zone"}))
    }
    assert "supersedes" in inspect.signature(FilePredictionStore.put).parameters

    assert "supersedes" not in DailyRunRequest.__annotations__
    assert "supersedes" not in ModelDailyRunApiRequest.model_fields
    assert "supersedes" not in inspect.signature(OpenAlphaSDK.run_daily_model).parameters


def test_a_document_edited_after_it_was_filed_is_refused_on_read_by_name(tmp_path: Path) -> None:
    """`V2-P4-073`'s other half: the loss it found was found on the *read* side.

    That issue's silent data loss surfaced only when `_refuse_rows_that_are_not_the_answers_
    their_manifest_addresses` next read the partition. So this store re-derives the address of
    every document it hands back and refuses one that no longer addresses to the name it is
    filed under -- which is what turns "the bytes are content-addressed" from a naming
    convention into a check.
    """
    subject = store(tmp_path)
    held = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    document = tmp_path / f"{held.record_id}{PREDICTION_DOCUMENT_SUFFIX}"
    payload = json.loads(document.read_text(encoding="utf-8"))
    payload["batch"]["predicted_at"] = "2026-06-15T07:45:00Z"
    document.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PredictionStoreError, match="no longer addresses"):
        subject.get(held.record_id)


def test_the_custody_stamp_is_the_one_edit_that_check_cannot_see(tmp_path: Path) -> None:
    """The cost of excluding `recorded_at` from the address, measured rather than asserted.

    `the_address_does_not_commit_to_the_custody_stamp` is the limitation this measures. Editing
    the custody stamp changes `standing` from `forward` to `unwitnessed` and the document still
    re-derives to its own filename, so the read-side check above passes. That is a hole only for
    somebody who already has the disk, and closing it would open a worse one -- a retried write
    filing a second document for one forecast. Both directions are on the record.
    """
    subject = store(tmp_path)
    held = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    document = tmp_path / f"{held.record_id}{PREDICTION_DOCUMENT_SUFFIX}"
    payload = json.loads(document.read_text(encoding="utf-8"))
    payload["recorded_at"] = "2026-06-20T01:00:00Z"
    document.write_text(json.dumps(payload), encoding="utf-8")

    doctored = subject.get(held.record_id)

    assert doctored is not None
    assert doctored.record_id == held.record_id
    assert held.standing == "forward"
    assert doctored.standing == "unwitnessed"


def test_a_key_that_is_not_an_address_is_refused_rather_than_sanitised(tmp_path: Path) -> None:
    """`_refuse_an_unusable_id`'s rule: the key becomes a filename, so check it, never repair it.

    Sanitising turns a wrong key into a plausible one and files a document under it. `prd_` plus
    24 lowercase hex characters contains no separator and nothing a filesystem treats specially,
    so a key that matches cannot name anything outside this directory.

    The **too long** and **trailing newline** cases are the two a mutation sweep found this test
    had been missing, and they are the two that matter most for a token that becomes a filename:
    they are what a widened `{24,}` and a `match`-instead-of-`fullmatch` would let through, and
    neither is caught by the too-short or wrong-alphabet cases beside them.
    """
    subject = store(tmp_path)

    for refused in (
        "../../etc/passwd",
        "prd_" + "0" * 23,
        "prd_" + "0" * 25,
        "prd_" + "0" * 24 + "\n",
        "prd_" + "g" * 24,
        "PRD_" + "0" * 24,
    ):
        with pytest.raises(PredictionStoreError, match="is not a record_id"):
            subject.get(refused)


def test_a_store_whose_directory_does_not_exist_yet_answers_nothing(tmp_path: Path) -> None:
    """A fresh install has no predictions, which is a state and not a fault."""
    subject = store(tmp_path / "never-written")

    assert subject.list_ids() == ()
    assert subject.get("prd_" + "0" * 24) is None


def test_a_partial_file_from_a_crashed_write_is_never_offered_as_a_key(tmp_path: Path) -> None:
    """`FileShortlistStore.list_ids`' rule: never hand back a key `get` then refuses.

    The `.partial` file is **manufactured** here rather than produced, and that is a stated
    weakness rather than a shortcut. `put` writes to a temporary and renames, `FileShortlistStore`
    and `FileExperimentStore`'s arrangement, so a crash mid-write leaves a `.partial` and the key
    stays unheld; replacing that with a direct `write_text` would leave a truncated document under
    a valid key, which `get` then raises on instead of answering `None`. A mutation sweep confirmed
    the substitution survives every test here, and it survives **correctly**: the two are
    indistinguishable across any write that completed, and the one that separates them is a crash
    no unit test can stage without reaching inside the store.
    """
    subject = store(tmp_path)
    held = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    (tmp_path / f"{held.record_id}{PREDICTION_DOCUMENT_SUFFIX}.partial").write_text("{", "utf-8")
    (tmp_path / "notes.txt").write_text("not a record", encoding="utf-8")

    assert subject.list_ids() == (held.record_id,)


def test_a_document_written_by_a_newer_build_fails_by_name(tmp_path: Path) -> None:
    """`read_versioned` is the read path, so a version this build cannot read says which.

    The `UnknownSchemaVersionError` sentence survives inside this store's refusal rather than
    replacing it, which is `V2-P4-096`'s whole point: the version registry's message is the
    actionable half -- it names the contract, the version found and the versions this build
    reads -- and it was arriving at three product faces as `exit 5` with the message *withheld*,
    because nothing above `storage/` catches a `ValueError` out of `domain/versioning.py`. This
    test asserted the escape before that issue and asserts the envelope now; both halves of the
    sentence are checked so a refusal that dropped the diagnosis to gain the type would be red.
    """
    subject = store(tmp_path)
    held = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    document = tmp_path / f"{held.record_id}{PREDICTION_DOCUMENT_SUFFIX}"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            "alpha-prediction-record/v1", "alpha-prediction-record/v2", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(PredictionStoreError) as raised:
        subject.get(held.record_id)

    assert "could not be read back as a prediction" in str(raised.value)
    assert "alpha-prediction-record/v2" in str(raised.value)
    assert isinstance(raised.value.__cause__, UnknownSchemaVersionError)


def test_a_document_that_will_not_parse_is_refused_by_this_store_and_not_by_python(
    tmp_path: Path,
) -> None:
    """`V2-P4-096`: the parse was the one thing `get` did not check.

    Four documents and three exception types, because `except json.JSONDecodeError` -- the fault
    the issue was reported as -- covers exactly one of them. `STORED_DOCUMENT_FAULTS` is the
    tuple, and it is named at `read_versioned` rather than here so that the day that function
    grows a fourth exit, the store that reads through it is not the place anybody has to
    remember.

    The refusal names the record, says the bytes rather than the address are the problem, and
    names the file to remove: this store has no `delete`, so the remedy is the operator's and a
    message that did not say so would leave the document blocking its own re-registration
    forever.
    """
    damaged: dict[str, Callable[[str], str]] = {
        "truncated": lambda text: text[: len(text) // 2],
        "an array rather than an object": lambda text: f"[{text}]",
        "a schema_version this build has not got": lambda text: text.replace(
            "alpha-prediction-record/v1", "alpha-prediction-record/v9", 1
        ),
        "a field of the wrong type": lambda text: json.dumps(
            {**json.loads(text), "recorded_at": ["not", "an", "instant"]}
        ),
    }
    subject = store(tmp_path)
    held = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    document = tmp_path / f"{held.record_id}{PREDICTION_DOCUMENT_SUFFIX}"
    filed = document.read_text(encoding="utf-8")

    causes: list[type[BaseException]] = []
    for described, damage in damaged.items():
        document.write_text(damage(filed), encoding="utf-8")

        with pytest.raises(PredictionStoreError) as raised:
            subject.get(held.record_id)

        message = str(raised.value)
        assert held.record_id in message, described
        assert "could not be read back as a prediction" in message, described
        assert f"predictions/{held.record_id}{PREDICTION_DOCUMENT_SUFFIX}" in message, described
        assert raised.value.__cause__ is not None, described
        causes.append(type(raised.value.__cause__))

    assert set(causes) == {json.JSONDecodeError, UnknownSchemaVersionError, ValidationError}, (
        "four documents have to reach three exception types, or this corpus is testing "
        "`except json.JSONDecodeError` under another name"
    )


def test_an_unreadable_document_refuses_the_write_that_would_replace_it(tmp_path: Path) -> None:
    """`put` reads through `get`, which is why one `except` was enough.

    Two readers, not one: the collision branch (`is_file()` -> `get`) and the `supersedes`
    check. A caller re-offering the identical prediction is the ordinary case -- `put` is
    idempotent by design and a repeated daily run is expected to report `unchanged` -- so an
    unreadable document had to refuse *that* path too, and it did so as a bare `JSONDecodeError`
    out of a store whose face catches `PredictionStoreError`.

    Refused rather than overwritten. "Never write where something is already held" is this
    store's one guarantee, and a document that cannot be parsed is still one that is held; a
    `put` that repaired the directory by clobbering would be the escape hatch `V2-P4-071`'s
    `--supersedes-raw` was removed for.
    """
    subject = store(tmp_path)
    held = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    document = tmp_path / f"{held.record_id}{PREDICTION_DOCUMENT_SUFFIX}"
    filed = document.read_text(encoding="utf-8")
    document.write_text(filed[: len(filed) // 2], encoding="utf-8")

    with pytest.raises(PredictionStoreError, match="could not be read back as a prediction"):
        put(subject, batch=batch())

    assert document.read_text(encoding="utf-8") == filed[: len(filed) // 2]
    assert subject.list_ids() == (held.record_id,)


def test_the_named_faults_are_what_reading_a_document_raises_and_not_what_a_bug_raises() -> None:
    """`STORED_DOCUMENT_FAULTS`' membership, both directions, because both were decisions.

    **In**: the three `read_versioned` reaches on damaged bytes, plus
    `IdentityRewriteRequiredError`, which no registry read through this store can produce today
    -- `PREDICTION_RECORD_VERSIONS` has one version and therefore no upgrade to refuse. It is
    kept for `V2-P4-084`'s precedent, and this assertion is the pin that precedent asks for: a
    mutation deleting the unreachable arm survives every corpus in this repository, so the reason
    it stays is written down where a sweep will find it rather than left to be rediscovered.

    **Out**: the `RuntimeError` `read_versioned` raises when an upgrade chain does not converge.
    That is a `ContractVersions` whose upgrades cycle -- a defect in this build -- and a store
    that enveloped it would report its own bug to the user as a damaged document.
    """
    named = (
        json.JSONDecodeError,
        UnknownSchemaVersionError,
        IdentityRewriteRequiredError,
        ValidationError,
    )

    assert named == STORED_DOCUMENT_FAULTS
    assert RuntimeError not in STORED_DOCUMENT_FAULTS
    assert not any(issubclass(RuntimeError, fault) for fault in STORED_DOCUMENT_FAULTS)
    assert set(PREDICTION_RECORD_VERSIONS.upgrades) == set(), (
        "an upgrade registered here makes IdentityRewriteRequiredError reachable, and this "
        "test's stated reason for keeping the unreachable arm stops being the reason"
    )


def test_a_batch_the_calendar_cannot_reach_is_refused_rather_than_given_a_deadline(
    tmp_path: Path,
) -> None:
    """A prediction with no knowable deadline is not a prediction this store can judge.

    The fixture calendar ends on 2026-06-30, so a batch dated at its tail has no exit session.
    Repairing that here -- clamping to the last published day, say -- would invent the one
    instant this whole contract is built to derive.
    """
    subject = store(tmp_path)
    tail = datetime(2026, 6, 29, 7, 0, tzinfo=UTC)

    with pytest.raises((CalendarHorizonError, LabelError)):
        put(subject, batch=batch(as_of=tail, predicted_at=tail))

    assert subject.list_ids() == ()


def test_nothing_this_store_offers_can_remove_or_rewrite_a_held_record(tmp_path: Path) -> None:
    """The API census: "cannot replace the original" is a statement about the whole surface.

    Three methods, and none of them takes a key it would write to. `put` derives its own key
    from the record's content and writes only where nothing is held; there is no `delete`, no
    `replace`, no `supersede` that removes, and no parameter anywhere that names a document to
    overwrite. `V2-P4-071`'s escape hatch was `--supersedes-raw`, which *erased*; `supersedes`
    here is a reference and erases nothing.
    """
    surface = {name for name in vars(FilePredictionStore) if not name.startswith("_")}

    assert surface == {"put", "get", "list_ids"}
    assert set(FilePredictionStore.put.__annotations__) == {
        "batch",
        "calendar",
        "zone",
        "supersedes",
        "return",
    }


def test_the_store_holds_the_denominator_a_multiple_testing_policy_needs(tmp_path: Path) -> None:
    """The half of Implementation Decision 12's third clause a store *can* answer.

    "The final holdout left untouched through selection" has a retrospective half no record can
    carry -- nothing says what its author had already looked at. What is countable is how many
    declarations were laid down against one instant, and that is the denominator a
    multiple-testing policy needs. Counting is not correcting, and the correction is not here.
    """
    subject = store(tmp_path, CUSTODY_IN_TIME)
    first = put(subject, batch=batch()).record  # type: ignore[attr-defined]
    second = put(subject, batch=batch(predicted_at=datetime(2026, 6, 15, 7, 45, tzinfo=UTC))).record  # type: ignore[attr-defined]

    held = tuple(subject.get(record_id) for record_id in subject.list_ids())
    against_this_instant = [item for item in held if item is not None and item.batch.as_of == AS_OF]

    assert len(against_this_instant) == 2
    assert {item.record_id for item in against_this_instant} == {
        first.record_id,
        second.record_id,
    }
