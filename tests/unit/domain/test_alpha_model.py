"""The `AlphaModel` contract (`V2-P4-011`): what `fit` is given and what `predict` returns.

Every label here is built by `domain/labels.py`'s real path against a real calendar
(`tests/alpha_model_fixtures.py`), so `TrainingSet.training_cutoff` is a session the calendar
holds and an unlabelled window is one `label_outcome` actually refused -- not a dataclass a test
hand-assembled into the shape it wanted to assert about.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import alpha_model_fixtures as fixtures
import pytest
from pydantic import ValidationError

from openalpha_cn.domain._identity import CONTENT_ADDRESS_PATTERN, stable_model_id
from openalpha_cn.domain.alpha_model import (
    KNOWN_ALPHA_MODEL_LIMITATIONS,
    MAX_FEATURE_COUNT,
    AlphaModelArtifact,
    AlphaModelDeclaration,
    AlphaModelError,
    FeatureCrossSection,
    FeatureRow,
    Prediction,
    PredictionBatch,
    TrainingExample,
    TrainingSet,
    artifact_for,
    prediction_batch_for,
)
from openalpha_cn.domain.run import AlphaModelRef

# --- the cross section `predict` is asked about -------------------------------------------


def test_a_feature_cross_section_refuses_an_as_of_with_no_offset() -> None:
    """The one field a point-in-time read cannot do without.

    `domain/time.py::ensure_aware` is the single implementation of the rule and this wraps it,
    so a naive instant cannot reach a model through this contract and the refusal cannot drift
    away from the one every other contract uses.
    """
    with pytest.raises(AlphaModelError, match="carries no offset"):
        FeatureCrossSection(
            as_of=datetime(2026, 6, 30, 8, 30),
            feature_ids=("a",),
            rows=(FeatureRow(ts_code="000001.SZ", values=(1.0,)),),
        )


@pytest.mark.parametrize(
    ("feature_ids", "expected"),
    [
        ((), "names no feature"),
        (("b", "a"), "not strictly increasing"),
        (("a", "a"), "not strictly increasing"),
        ((" ",), "blank id"),
    ],
)
def test_a_feature_cross_section_refuses_a_feature_list_that_cannot_align_a_row(
    feature_ids: tuple[str, ...], expected: str
) -> None:
    """Strictly increasing, because feature values travel positionally.

    `("b", "a")` and `("a", "b")` name the same feature *set* and put each value in a different
    column, so a fitted model asked about the first would score the second's data. Sorting is
    what makes `require_features` an equality check instead of a set comparison that cannot see
    the difference.
    """
    with pytest.raises(AlphaModelError, match=expected):
        FeatureCrossSection(
            as_of=datetime(2026, 6, 30, tzinfo=UTC),
            feature_ids=feature_ids,
            rows=(FeatureRow(ts_code="000001.SZ", values=tuple(1.0 for _ in feature_ids)),),
        )


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ((), "carries no security"),
        (
            (
                ("000001.SZ", (1.0, 2.0)),
                ("000001.SZ", (3.0, 4.0)),
            ),
            "twice at one as_of",
        ),
        ((("000001.SZ", (1.0,)),), "against 2 feature"),
        ((("  ", (1.0, 2.0)),), "naming no security"),
        ((("000001.SZ", (float("nan"), 2.0)),), "not a finite number"),
        ((("000001.SZ", (float("inf"), 2.0)),), "not a finite number"),
    ],
)
def test_a_feature_cross_section_refuses_the_rows_no_model_could_read(
    rows: tuple[tuple[str, tuple[float, ...]], ...], expected: str
) -> None:
    """Five shapes, and each is a different way one `as_of` stops meaning one read."""
    with pytest.raises(AlphaModelError, match=expected):
        FeatureCrossSection(
            as_of=datetime(2026, 6, 30, tzinfo=UTC),
            feature_ids=("a", "b"),
            rows=tuple(FeatureRow(ts_code=code, values=values) for code, values in rows),
        )


def test_the_feature_count_bound_admits_its_own_limit_and_refuses_one_past_it() -> None:
    """A range check on a declared list, driven at the boundary rather than well inside it.

    `FactorTransformSpec.min_cross_section`'s precedent, and the same honesty about what it
    buys: `MAX_FEATURE_COUNT` is far above anything `V2-P4-012` is likely to build, so it is
    not a modelling opinion -- it keeps a malformed list from reaching a fit at all. Both sides
    are driven because a bound nobody crosses is a bound nobody has checked.
    """
    ids = tuple(f"f{index:05d}" for index in range(MAX_FEATURE_COUNT))
    admitted = FeatureCrossSection(
        as_of=datetime(2026, 6, 30, tzinfo=UTC),
        feature_ids=ids,
        rows=(FeatureRow(ts_code="000001.SZ", values=(0.0,) * MAX_FEATURE_COUNT),),
    )

    assert len(admitted.feature_ids) == MAX_FEATURE_COUNT
    with pytest.raises(AlphaModelError, match=f"above MAX_FEATURE_COUNT \\({MAX_FEATURE_COUNT}\\)"):
        FeatureCrossSection(
            as_of=datetime(2026, 6, 30, tzinfo=UTC),
            feature_ids=(*ids, "zzz"),
            rows=(FeatureRow(ts_code="000001.SZ", values=(0.0,) * (MAX_FEATURE_COUNT + 1)),),
        )


def test_a_missing_feature_is_none_and_survives_construction() -> None:
    """`None` is a value this contract carries; a non-finite float is not.

    The pair is the measurement: the same row that is refused with `nan` is admitted with
    `None`, so "abstain rather than impute" is available to an implementation without the
    contract having chosen an imputation.
    """
    section = fixtures.cross_section()

    assert section.value(ts_code="000003.SZ", feature_id=fixtures.MOMENTUM) is None
    assert section.value(ts_code="000002.SZ", feature_id=fixtures.MOMENTUM) == 0.25
    assert section.subjects == ("000001.SZ", "000002.SZ", "000003.SZ")


def test_a_named_cell_lookup_refuses_a_name_the_cross_section_does_not_carry() -> None:
    """Both directions, because a silent `None` here reads exactly like a missing feature."""
    section = fixtures.cross_section()

    with pytest.raises(AlphaModelError, match="not one of this cross section's features"):
        section.value(ts_code="000001.SZ", feature_id="not_a_feature")
    with pytest.raises(AlphaModelError, match="not one of this cross section's securities"):
        section.value(ts_code="999999.SZ", feature_id=fixtures.MOMENTUM)


# --- the training set `fit` is given ------------------------------------------------------


def test_a_training_example_refuses_a_window_the_market_refused() -> None:
    """A halted exit session produces an unlabelled window, and it cannot become a target.

    The refusal is `label_outcome`'s own -- the fixture deletes the exit bar, which is what a
    halt looks like to the label path -- and `OutcomeLabel.realized_return` raises rather than
    returning `0.0` for it. A training set that admitted this example would have to choose
    between crashing at the first `.target` and reading a zero, and a zero teaches the model
    that halts are flat.
    """
    unlabelled = fixtures.outcome_label(
        ts_code="000001.SZ", prediction_day=date(2026, 6, 1), target=0.02, halt_the_exit=True
    )

    assert not unlabelled.is_labelled
    with pytest.raises(AlphaModelError, match="unlabelled window"):
        TrainingExample(label=unlabelled, features=(0.1, 0.2))


def test_a_training_set_refuses_two_horizons_fitted_as_one() -> None:
    """`5d` and `1d` windows in one set is two questions, and an artifact records one horizon."""
    mixed = (
        *fixtures.training_set().examples[:2],
        fixtures.training_example(
            ts_code="000004.SZ",
            prediction_day=date(2026, 6, 1),
            features=(0.4, 0.02),
            target=0.03,
            horizon="5d",
        ),
    )

    with pytest.raises(AlphaModelError, match="mixes horizons"):
        TrainingSet(feature_ids=fixtures.FEATURE_IDS, examples=mixed)


def test_a_training_set_refuses_one_security_twice_on_one_prediction_day() -> None:
    """The one overlap shape a purge cannot repair, refused where the set is assembled.

    `overlapping_windows` refuses the same shape for the same reason; refusing it here as well
    means a `TrainingSet` cannot be constructed in a state whose own `overlaps` property would
    raise.
    """
    examples = fixtures.training_set().examples
    duplicated = (*examples, examples[0])

    with pytest.raises(AlphaModelError, match="two examples on 2026-06-01"):
        TrainingSet(feature_ids=fixtures.FEATURE_IDS, examples=duplicated)
    with pytest.raises(AlphaModelError, match="carries no example"):
        TrainingSet(feature_ids=fixtures.FEATURE_IDS, examples=())


def test_a_training_set_reports_ordinary_overlap_instead_of_refusing_it() -> None:
    """Two prediction days one session apart share a session, and that is the normal shape.

    At horizon `1d` each window spans two sessions, so 6-01's and 6-02's windows share exactly
    one -- three securities, three overlapping pairs. `V2-P4-013` is what acts on this; the
    contract's job is to make it measurable rather than to draw the fold boundary.
    """
    overlaps = fixtures.training_set().overlaps

    assert len(overlaps) == 3
    assert {overlap.ts_code for overlap in overlaps} == {
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
    }
    assert all(len(overlap.shared_sessions) == 1 for overlap in overlaps)
    assert all(overlap.shared_fraction == 0.5 for overlap in overlaps)
    training_set = fixtures.training_set()
    assert training_set.samples == tuple(example.sample for example in training_set.examples)
    assert {sample.ts_code for sample in training_set.samples} == {
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
    }


def test_the_training_cutoff_is_the_last_exit_close_and_not_the_last_prediction_day() -> None:
    """The distinction the whole leakage floor rests on, on a fixture that separates them.

    The latest prediction day is 2026-06-02; its window enters on the 3rd and exits on the 4th.
    A cutoff read off the prediction day would be two sessions early and would let a batch dated
    2026-06-03 through, which is an instant at which the fit's own last outcome was not yet
    realized. The assertion pins the exit close instant and the inequality against the
    prediction day's close, so reading the wrong field cannot pass.
    """
    training_set = fixtures.training_set()
    latest = max(
        (example.label.window for example in training_set.examples),
        key=lambda window: window.exit_day,
    )

    assert latest.prediction_day == date(2026, 6, 2)
    assert latest.exit_day == date(2026, 6, 4)
    assert training_set.training_cutoff == latest.close_instant(latest.exit_day)
    assert training_set.training_cutoff > datetime.combine(
        latest.prediction_day, time(15, 0), tzinfo=fixtures.SHANGHAI
    )
    assert training_set.horizon.text == "1d"


def test_a_training_set_refuses_a_row_whose_width_is_not_the_header_s() -> None:
    """Alignment is checked on the training side too, not only on the cross section."""
    example = fixtures.training_example(
        ts_code="000009.SZ", prediction_day=date(2026, 6, 1), features=(0.1,), target=0.01
    )

    with pytest.raises(AlphaModelError, match="against 2 feature"):
        TrainingSet(feature_ids=fixtures.FEATURE_IDS, examples=(example,))


# --- the declaration and the artifact ------------------------------------------------------


def test_a_declaration_keeps_each_hyperparameter_at_the_type_it_was_given() -> None:
    """Flat scalars, and `True` does not become `1`.

    Worth pinning because pydantic's union coercion is where that would silently happen, and a
    stored artifact whose `use_bias` reads `1` is one nobody can compare against a declaration
    that wrote `True`.

    **`0` and `False` are here because `V2-P4-016`'s signed-zero normalisation is.** That
    validator now rewrites the tuple, and a mutation sweep measured that widening its
    `isinstance(item, float)` guard to `int | float` left every other assertion green while
    turning a declared `0` into `0.0` and a declared `False` into `0.0` -- a type change and, for
    `False`, a *value* change, both invisible to `==`. The two zero-valued scalars are what
    separates the shipped guard from that one.
    """
    declared = AlphaModelDeclaration(
        name="probe",
        family="linear",
        horizon="5d",
        feature_version="features/v1",
        seed=0,
        code_commit="abcdef0",
        hyperparameters=(
            ("alpha", 0.5),
            ("depth", 3),
            ("floor", 0),
            ("label", "ic"),
            ("shuffle", False),
            ("use_bias", True),
        ),
    )

    assert declared.hyperparameters[2] == ("floor", 0)
    assert declared.hyperparameters[4] == ("shuffle", False)
    assert [type(value) for _key, value in declared.hyperparameters] == [
        float,
        int,
        int,
        str,
        bool,
        bool,
    ]


@pytest.mark.parametrize(
    ("hyperparameters", "expected"),
    [
        ((("b", 1), ("a", 2)), "not strictly increasing"),
        ((("a", 1), ("a", 2)), "not strictly increasing"),
        (((" ", 1),), "blank name"),
        ((("a", float("nan")),), "not finite"),
    ],
)
def test_a_declaration_refuses_a_hyperparameter_list_with_two_canonical_spellings(
    hyperparameters: tuple[tuple[str, object], ...], expected: str
) -> None:
    """One declaration, one canonical JSON, therefore one address when `V2-P4-016` takes it."""
    with pytest.raises(ValidationError, match=expected):
        AlphaModelDeclaration(
            name="probe",
            family="linear",
            horizon="5d",
            feature_version="features/v1",
            seed=0,
            code_commit="abcdef0",
            hyperparameters=hyperparameters,  # type: ignore[arg-type]
        )


def test_a_declaration_refuses_a_horizon_no_session_count_exists_for() -> None:
    """`COUNTABLE_HORIZON_PATTERN`, the same narrowing `V2-P4-001` put on `SignalFrame`.

    A `3m` model is a model whose training windows could not have been built at all --
    `build_label_window` raises `HorizonError` for a calendar unit -- so admitting one here
    would let a declaration exist that no `TrainingSet` could ever satisfy.
    """
    with pytest.raises(ValidationError):
        AlphaModelDeclaration(
            name="probe",
            family="linear",
            horizon="3m",
            feature_version="features/v1",
            seed=0,
            code_commit="abcdef0",
        )


def test_artifact_for_measures_the_three_fields_a_caller_could_have_lied_about() -> None:
    """`feature_ids`, `training_cutoff` and `training_example_count` come from the data."""
    training_set = fixtures.training_set()
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=training_set, parameters=(("k", 1.5),)
    )

    assert artifact.feature_ids == fixtures.FEATURE_IDS
    assert artifact.training_cutoff == training_set.training_cutoff
    assert artifact.training_example_count == 6
    assert artifact.parameters == (("k", 1.5),)
    assert artifact.declaration.seed == 7
    assert artifact.declaration.code_commit == "0123456789abcdef"
    assert artifact.declaration.feature_version == "features/v1"


def test_artifact_for_refuses_a_declaration_whose_horizon_its_data_did_not_answer() -> None:
    """A `5d` declaration fitted on `1d` windows would store a number under the wrong question."""
    with pytest.raises(AlphaModelError, match="declares horizon '5d'"):
        artifact_for(
            declaration=fixtures.declaration(horizon="5d"), training_set=fixtures.training_set()
        )


def test_require_features_refuses_a_cross_section_whose_columns_would_shift() -> None:
    """Equality and not a subset check: an extra column moves every feature after it."""
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=fixtures.training_set()
    )
    widened = FeatureCrossSection(
        as_of=datetime(2026, 6, 30, tzinfo=UTC),
        feature_ids=("beta_60d", *fixtures.FEATURE_IDS),
        rows=(FeatureRow(ts_code="000001.SZ", values=(0.9, 0.1, 0.05)),),
    )

    with pytest.raises(AlphaModelError, match=r"extra \['beta_60d'\]"):
        artifact.require_features(widened)
    artifact.require_features(fixtures.cross_section())


def test_the_artifact_is_addressable_by_the_one_hash_function_without_deciding_v2_p4_016() -> None:
    """`V2-P4-016` can take this artifact as it stands, and a changed input moves the address.

    The prefix here is a test's, not a contract's, and that is the whole point: `V2-P4-010` left
    the prefix and the digest field set to `V2-P4-016`, so what `V2-P4-011` owes is an artifact
    `stable_model_id` accepts and whose declared inputs all reach the canonical JSON. Two
    artifacts differing only in `seed` -- a field Implementation Decision 11 names -- must not
    share an address, and the result must satisfy `CONTENT_ADDRESS_PATTERN`, which is what
    `AlphaModelRef.artifact_id` already requires.
    """
    training_set = fixtures.training_set()
    first = artifact_for(declaration=fixtures.declaration(), training_set=training_set)
    reseeded = first.model_copy(
        update={"declaration": first.declaration.model_copy(update={"seed": 8})}
    )

    address = stable_model_id(prefix="probe", model=first)
    other = stable_model_id(prefix="probe", model=reseeded)

    assert address != other
    assert stable_model_id(prefix="probe", model=first) == address
    ref = AlphaModelRef(name=first.declaration.name, artifact_id=address)
    assert ref.artifact_id == address
    import re

    assert re.fullmatch(CONTENT_ADDRESS_PATTERN, address)


# --- the prediction batch `predict` returns -------------------------------------------------


@pytest.mark.parametrize(
    ("score", "abstention"),
    [(None, None), (0.5, "stale"), (float("nan"), None), (float("-inf"), None)],
)
def test_a_prediction_is_a_number_or_a_stated_reason_and_never_both_or_neither(
    score: float | None, abstention: str | None
) -> None:
    """S35's shape, installed before `V2-P4-018` fills in the vocabulary.

    A row carrying neither is a security the batch silently dropped; a row carrying both is two
    answers. A non-finite score is refused rather than stored, because it would survive as far
    as `stable_model_id`, whose `allow_nan=False` would raise there instead of here.
    """
    with pytest.raises(ValidationError):
        Prediction(ts_code="000001.SZ", score=score, abstention=abstention)


def test_a_batch_refuses_an_as_of_before_the_cutoff_its_own_model_was_fitted_through() -> None:
    """The leakage floor, measured at the boundary rather than somewhere safely far from it.

    Equality is admitted -- training through last night's close and predicting as of it is what
    a daily production model does -- so the two cases are one microsecond apart, and an
    implementation that wrote `<=` where this writes `<` fails the first while an implementation
    that dropped the check entirely fails the second.
    """
    training_set = fixtures.training_set()
    artifact = artifact_for(declaration=fixtures.declaration(), training_set=training_set)
    cutoff = training_set.training_cutoff
    rows = (Prediction(ts_code="000001.SZ", score=0.1),)

    on_the_cutoff = PredictionBatch(
        as_of=cutoff, predicted_at=cutoff, artifact=artifact, predictions=rows
    )
    assert on_the_cutoff.as_of == cutoff.astimezone(UTC)

    with pytest.raises(ValidationError, match="realized after the instant"):
        PredictionBatch(
            as_of=cutoff - timedelta(microseconds=1),
            predicted_at=cutoff,
            artifact=artifact,
            predictions=rows,
        )


def test_a_batch_refuses_being_produced_before_the_features_it_read_were_readable() -> None:
    """`predicted_at` is a second clock and not a decoration."""
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=fixtures.training_set()
    )
    as_of = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)

    with pytest.raises(ValidationError, match="before the"):
        PredictionBatch(
            as_of=as_of,
            predicted_at=as_of - timedelta(seconds=1),
            artifact=artifact,
            predictions=(Prediction(ts_code="000001.SZ", score=0.1),),
        )


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ((), "carries no row"),
        (("000002.SZ", "000001.SZ"), "not strictly increasing"),
        (("000001.SZ", "000001.SZ"), "not strictly increasing"),
    ],
)
def test_a_batch_is_one_sorted_row_per_security(codes: tuple[str, ...], expected: str) -> None:
    """Sorted and unique, so two batches over one universe are comparable field by field."""
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=fixtures.training_set()
    )
    as_of = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)

    with pytest.raises(ValidationError, match=expected):
        PredictionBatch(
            as_of=as_of,
            predicted_at=as_of,
            artifact=artifact,
            predictions=tuple(Prediction(ts_code=code, score=0.1) for code in codes),
        )


def test_prediction_batch_for_refuses_a_model_that_dropped_or_invented_a_security() -> None:
    """The coverage check `PredictionBatch` cannot make, because it never sees the cross section.

    Both directions: a security offered and not answered about is a silent drop -- exactly what
    `Prediction.abstention` exists to make visible -- and a security answered about that was
    never offered is a number attached to a name this read did not contain.
    """
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=fixtures.training_set()
    )
    section = fixtures.cross_section()
    as_of = section.as_of

    with pytest.raises(AlphaModelError, match=r"\['000003.SZ'\] carry no row"):
        prediction_batch_for(
            artifact=artifact,
            cross_section=section,
            predicted_at=as_of,
            shelf_life=None,
            predictions=[
                Prediction(ts_code="000001.SZ", score=0.1),
                Prediction(ts_code="000002.SZ", score=0.2),
            ],
        )
    with pytest.raises(AlphaModelError, match=r"\['999999.SZ'\] were never in"):
        prediction_batch_for(
            artifact=artifact,
            cross_section=section,
            predicted_at=as_of,
            shelf_life=None,
            predictions=[
                Prediction(ts_code=code, score=0.1)
                for code in ("000001.SZ", "000002.SZ", "000003.SZ", "999999.SZ")
            ],
        )


def test_prediction_batch_for_sorts_the_rows_and_carries_the_cross_section_s_as_of() -> None:
    """The batch's `as_of` is the read's, not a third thing the caller passes separately."""
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=fixtures.training_set()
    )
    section = fixtures.cross_section()
    predicted_at = section.as_of + timedelta(minutes=5)

    batch = prediction_batch_for(
        artifact=artifact,
        cross_section=section,
        predicted_at=predicted_at,
        shelf_life=None,
        predictions=[
            Prediction(ts_code="000003.SZ", abstention="no value"),
            Prediction(ts_code="000001.SZ", score=0.1),
            Prediction(ts_code="000002.SZ", score=0.2),
        ],
    )

    assert batch.subjects == ("000001.SZ", "000002.SZ", "000003.SZ")
    assert batch.as_of == section.as_of
    assert batch.predicted_at == predicted_at
    assert batch.horizon == "1d"
    assert [row.ts_code for row in batch.scored] == ["000001.SZ", "000002.SZ"]
    assert [row.ts_code for row in batch.abstained] == ["000003.SZ"]


def test_a_stored_batch_is_frozen_so_it_cannot_be_edited_into_agreement() -> None:
    """Story S32's "before outcomes are known" is worth nothing if the batch can be revised."""
    artifact = artifact_for(
        declaration=fixtures.declaration(), training_set=fixtures.training_set()
    )
    as_of = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)
    batch = PredictionBatch(
        as_of=as_of,
        predicted_at=as_of,
        artifact=artifact,
        predictions=(Prediction(ts_code="000001.SZ", score=0.1),),
    )

    with pytest.raises(ValidationError):
        batch.predictions = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        batch.predictions[0].score = 0.9  # type: ignore[misc]
    with pytest.raises(ValidationError):
        artifact.training_cutoff = as_of  # type: ignore[misc]


def test_a_batch_round_trips_through_json_so_v2_p4_017_can_store_one() -> None:
    """Every field survives serialization, including the artifact carried by value.

    `V2-P4-017` stores these, and a contract whose validators only run on hand-built objects is
    one that has never been read back. The re-validated batch compares equal, which also proves
    the leakage and ordering validators pass on the deserialized form rather than only on the
    constructed one.

    `exclude_computed_fields=True` arrived with `V2-P4-016`, and it is the whole of what that
    issue costs the transport form: a nested `AlphaModelArtifact` now carries an `artifact_id`,
    and `extra="forbid"` means a dump that kept it cannot be re-validated by the model that
    produced it. That is this repository's standing arrangement rather than a new one -- see
    `backtest/factor_experiment.py::experiment_payload`, which states it, and every
    `storage/*.py` writer, which does it -- and the address survives anyway, because it is a
    function of exactly the fields the payload carries.
    """
    artifact = artifact_for(
        declaration=fixtures.declaration(),
        training_set=fixtures.training_set(),
        parameters=(("centre", 0.21), ("sign", 1.0)),
    )
    as_of = datetime(2026, 6, 30, 8, 30, tzinfo=UTC)
    batch = PredictionBatch(
        as_of=as_of,
        predicted_at=as_of,
        artifact=artifact,
        predictions=(
            Prediction(ts_code="000001.SZ", score=0.1),
            Prediction(ts_code="000002.SZ", abstention="no value"),
        ),
    )

    restored = PredictionBatch.model_validate_json(
        batch.model_dump_json(exclude_computed_fields=True)
    )

    assert restored == batch
    assert restored.artifact.parameters == (("centre", 0.21), ("sign", 1.0))
    assert restored.artifact.declaration.hyperparameters == (("feature_id", fixtures.MOMENTUM),)
    assert restored.artifact.artifact_id == batch.artifact.artifact_id


def test_v2_p4_016_added_an_address_without_moving_a_single_declared_field() -> None:
    """`V2-P4-011`'s load-bearing framing, checked after the fact rather than promised.

    That issue argued the address should be an **addition**: a changed field set would have moved
    `RunManifest.alpha_model_versions`, `run_manifest_id` and `decision_id` with it, the migration
    `V2-P4-010` already paid for once. `V2-P4-016` did exactly that -- `model_fields` is
    byte-identical to what `V2-P4-011` shipped and the whole of the new surface is one
    `computed_field`, which is what `stable_model_id(exclude_computed_fields=True)` is built to
    ignore, so an artifact's address cannot depend on itself.

    Asserted on both halves. The declared set has no `_id` in it and the computed set has exactly
    one, so a *field* named like an identity in a later issue still fails here.
    """
    declared = set(AlphaModelArtifact.model_fields)

    assert declared == {
        "schema_version",
        "declaration",
        "feature_ids",
        "training_cutoff",
        "training_example_count",
        "parameters",
    }
    assert not any(name == "id" or name.endswith("_id") for name in declared)
    assert set(AlphaModelArtifact.model_computed_fields) == {"artifact_id"}


def test_the_artifact_carries_seven_of_decision_elevens_eleven_fields_and_says_which() -> None:
    """The gap enumerated, and re-counted by the issue that closed one of it.

    Implementation Decision 11 names eleven things a model artifact records. Seven are reachable
    off an `AlphaModelArtifact` since `V2-P4-016` added the content hash to the six `V2-P4-011`
    shipped. Of the remaining four, two belong to the feature plane (`V2-P4-012`: universe
    version, preprocessing) and two were deferred *here* and then deliberately placed elsewhere:
    the split policy is a property of the fold and not of the fit, and the metrics are a
    judgement of the artifact and live on `FoldEvaluation` beside it.

    Asserted as a field-set so that adding one of the four -- or quietly dropping one of the
    seven -- has to be a deliberate act. `content_hash` is *still* absent from `model_fields`,
    which is the point of `test_v2_p4_016_added_an_address_without_moving_a_single_declared_field`
    stated from the other side: the eleventh thing arrived as a computed value.
    """
    artifact = artifact_for(
        declaration=fixtures.declaration(),
        training_set=fixtures.training_set(),
        parameters=(("centre", 0.21),),
    )

    carried = {
        "training cutoff": artifact.training_cutoff,
        "horizon": artifact.declaration.horizon,
        "feature version": artifact.declaration.feature_version,
        "declared parameters": artifact.declaration.hyperparameters,
        "fitted parameters": artifact.parameters,
        "seed": artifact.declaration.seed,
        "code version": artifact.declaration.code_commit,
        "content hash": artifact.artifact_id,
    }
    assert all(value is not None for value in carried.values())

    absent = {"universe", "preprocessing", "split_policy", "metrics", "content_hash"}
    declared_fields = set(AlphaModelArtifact.model_fields) | set(AlphaModelDeclaration.model_fields)
    assert not declared_fields & absent
    detail = next(
        item.detail
        for item in KNOWN_ALPHA_MODEL_LIMITATIONS
        if item.code == "d11_names_eleven_things_and_this_artifact_carries_seven"
    )
    for issue in ("V2-P4-012", "V2-P4-013", "V2-P4-014", "V2-P4-016"):
        assert issue in detail


def test_the_limitation_registry_names_thirteen_boundaries_with_no_repeated_code() -> None:
    """`KNOWN_LABEL_LIMITATIONS`' form, and the codes each test in this suite cites.

    Eight at `V2-P4-011`, eleven after `V2-P4-016` rewrote the two that had become false and
    added three about what an address does not prove, thirteen after `V2-P4-018` added two about
    what a shelf life is not -- wall time where a horizon counts sessions, and a verdict on a
    stored record without the bar that produced it.
    """
    codes = [item.code for item in KNOWN_ALPHA_MODEL_LIMITATIONS]

    assert len(codes) == len(set(codes)) == 13
    assert set(codes) == {
        "d11_names_eleven_things_and_this_artifact_carries_seven",
        "the_address_is_over_the_fit_and_not_over_the_rule_that_chose_it",
        "the_manifest_slot_still_admits_an_address_from_another_plane",
        "a_seed_in_the_address_is_read_by_no_model_in_this_build",
        "an_unknown_code_commit_is_one_constant_shared_by_every_build_that_has_none",
        "the_feature_version_is_a_name_this_contract_cannot_check",
        "nothing_forces_an_implementation_through_the_builders",
        "the_leakage_floor_is_not_a_purge_or_an_embargo",
        "a_batch_cannot_tell_a_prediction_from_a_backfill",
        "an_abstention_can_empty_a_ranking_of_predictions",
        "the_reference_implementation_is_not_a_baseline",
        "a_shelf_life_is_wall_time_and_a_horizon_is_sessions",
        "a_stale_record_carries_the_verdict_and_not_the_bar_it_failed",
    }
    assert all(len(item.detail) > 200 for item in KNOWN_ALPHA_MODEL_LIMITATIONS)
