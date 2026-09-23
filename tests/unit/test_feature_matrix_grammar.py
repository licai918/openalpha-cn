"""What names a feature column, and what makes two columns two (`V2-P4-012`).

`V2-P4-011` left the feature-matrix grammar, the universe version and preprocessing to this
issue and named it in `domain/alpha_model.py`'s own docstring. This file is the half that
touches no store: the id a column answers to, the content address the declaration takes, and
the measurement behind "two materially different matrices cannot share one `feature_version`".

The store-facing half is `tests/integration/test_feature_matrix_reads.py`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final

import pytest

from openalpha_cn.domain._identity import CONTENT_ADDRESS_PATTERN, stable_model_id
from openalpha_cn.domain.alpha_model import (
    AlphaModelError,
    FeatureCrossSection,
    FeatureRow,
)
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.feature_matrix import (
    FEATURE_MISSING_POLICIES,
    FEATURE_VERSION_PREFIX,
    FeatureColumn,
    FeatureSpec,
    FeatureSpecError,
    feature_spec,
)
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS, FACTOR_TRANSFORMS
from openalpha_cn.panel_neutralization import FACTOR_NEUTRALIZATIONS

REVERSAL: Final = FACTOR_DEFINITIONS.get("reversal_1d/v1")
MOMENTUM: Final = FACTOR_DEFINITIONS.get("momentum_20_sessions/v1")
TRANSFORM: Final = FACTOR_TRANSFORMS.get("cross_section_standard/v1")
NEUTRALIZATION: Final = FACTOR_NEUTRALIZATIONS.get("industry_and_size/v1")


def raw(definition: FactorDefinition = REVERSAL) -> FeatureColumn:
    return FeatureColumn(definition=definition, tier="raw")


def processed(
    definition: FactorDefinition = REVERSAL, transform: FactorTransformSpec = TRANSFORM
) -> FeatureColumn:
    return FeatureColumn(definition=definition, tier="processed", transform=transform)


def neutralized(definition: FactorDefinition = REVERSAL) -> FeatureColumn:
    return FeatureColumn(
        definition=definition,
        tier="neutralized",
        transform=TRANSFORM,
        neutralization=NEUTRALIZATION,
    )


def test_one_factor_at_two_tiers_is_two_features_and_not_one() -> None:
    """The grammar's first job: a `qualified_key` alone cannot name a column.

    `reversal_1d/v1` is stored three times over -- once raw, once per declared transform, once
    per declared neutralisation -- and the three carry different numbers for the same security
    at the same instant. A feature id that were the factor's own handle would give all three
    one name, and a matrix declaring two of them would carry a repeated id, which
    `domain/alpha_model.py::_validate_feature_ids` refuses. So the tier is *in* the id.
    """
    ids = {raw().feature_id, processed().feature_id, neutralized().feature_id}

    assert len(ids) == 3
    assert all(REVERSAL.qualified_key in feature_id for feature_id in ids)
    assert raw().feature_id == "reversal_1d/v1@raw"


def test_two_transforms_of_one_factor_are_two_features() -> None:
    """A derived column names the spec it was derived through, or it names nothing.

    Two winsorization policies over one factor produce two different numbers per security. The
    partition they are stored in is keyed by the factor alone (`processed_factor_dataset` is
    `factor_proc_<key>_v<n>`), so the transform is what the *read* is narrowed by and it has to
    be what the *id* is narrowed by too.
    """
    other = TRANSFORM.model_copy(update={"key": "cross_section_rank"})

    assert processed().feature_id != processed(transform=other).feature_id
    assert other.qualified_key in processed(transform=other).feature_id


def test_the_declared_order_is_not_a_determinant_of_the_feature_version() -> None:
    """Two callers declaring the same columns in two orders declare one matrix.

    `set_digest`'s argument, applied to a declaration rather than to a cross section: the
    positional alignment `domain/alpha_model.py` requires is *derived* from the sorted ids, so
    declaration order is not a fact about the matrix and an identity that moved for it would
    move for nothing. `feature_spec` sorts; `FeatureSpec` refuses anything else.
    """
    forwards = feature_spec(columns=(raw(), raw(MOMENTUM)))
    backwards = feature_spec(columns=(raw(MOMENTUM), raw()))

    assert forwards.feature_ids == backwards.feature_ids
    assert forwards.feature_version == backwards.feature_version
    assert list(forwards.feature_ids) == sorted(forwards.feature_ids)


def test_a_spec_whose_columns_are_not_strictly_increasing_is_refused() -> None:
    """The refusal `feature_spec`'s sort makes unreachable, stated where a hand-built spec meets it.

    `shortlist_view._declared_transform`'s form: a resolver normalises, and the contract still
    refuses, because the contract is constructible without the resolver.
    """
    columns = feature_spec(columns=(raw(), raw(MOMENTUM))).columns

    with pytest.raises(FeatureSpecError, match="strictly increasing"):
        FeatureSpec(columns=tuple(reversed(columns)), missing="abstain")


def test_a_column_declared_twice_is_refused_rather_than_de_duplicated() -> None:
    """Two identical columns are one column stated twice, and the matrix would carry it twice."""
    with pytest.raises(FeatureSpecError, match="twice"):
        feature_spec(columns=(raw(), raw()))


def test_a_spec_naming_no_column_is_refused() -> None:
    """`_validate_feature_ids`' own refusal, reached before a store is opened."""
    with pytest.raises(FeatureSpecError, match="no feature"):
        feature_spec(columns=())


def test_a_derived_column_that_names_no_spec_is_refused_at_declaration() -> None:
    """A processed column with no transform names a partition nobody can narrow.

    Both directions, because a raw column carrying a transform is the same error read the other
    way: the read would ignore it and the id would not say so.
    """
    with pytest.raises(FeatureSpecError, match="transform"):
        FeatureColumn(definition=REVERSAL, tier="processed")
    with pytest.raises(FeatureSpecError, match="neutralization"):
        FeatureColumn(definition=REVERSAL, tier="neutralized", transform=TRANSFORM)
    with pytest.raises(FeatureSpecError, match="raw"):
        FeatureColumn(definition=REVERSAL, tier="raw", transform=TRANSFORM)


def test_a_neutralized_column_names_the_transform_its_residual_was_taken_from() -> None:
    """`neutralized_factor_dataset` is keyed by the factor alone and the row carries the rest.

    `NeutralizedFactorObservation.source_transform_id` is on every stored residual, so which
    processed values a residual was regressed off is recoverable -- and two neutralisations of
    one factor through two transforms are two different numbers. The id says which, and the
    read checks it (`tests/integration/test_feature_matrix_reads.py::
    test_a_residual_taken_from_another_transform_is_refused_rather_than_scored`).
    """
    other = TRANSFORM.model_copy(update={"key": "cross_section_rank"})
    column = FeatureColumn(
        definition=REVERSAL,
        tier="neutralized",
        transform=other,
        neutralization=NEUTRALIZATION,
    )

    assert TRANSFORM.qualified_key in neutralized().feature_id
    assert column.feature_id != neutralized().feature_id


def test_the_feature_version_is_the_one_hash_function_this_repository_has() -> None:
    """`domain/_identity.py::stable_model_id` over the spec, and nothing computed beside it.

    `V2-P4-037` files the defect a second canonicalisation would be, and this is the assertion
    that keeps the property rather than the sentence: the value is recomputed here through the
    shared helper, so a bespoke `sha256` inside the module would produce a different string.
    """
    spec = feature_spec(columns=(raw(), processed()))

    assert spec.feature_version == stable_model_id(prefix=FEATURE_VERSION_PREFIX, model=spec)
    assert re.fullmatch(CONTENT_ADDRESS_PATTERN, spec.feature_version)
    assert spec.feature_version.startswith(f"{FEATURE_VERSION_PREFIX}_")


def test_every_determinant_of_a_spec_moves_its_feature_version() -> None:
    """The audit `factor_neutralization.py` runs on its own identity, run on this one.

    A claim that "two materially different matrices cannot share one `feature_version`" is only
    as good as the enumeration behind it, so the enumeration is the test: every field of
    `FeatureSpec` and every field of a `FeatureColumnRef`, each changed alone, must move the
    address. `stable_model_id` is called with no `exclude`, so the only way a field can fail
    this is by not being on the model at all -- which is what the both-directions check below
    is for.
    """
    spec = feature_spec(columns=(raw(), processed()))
    baseline = spec.feature_version
    moved: dict[str, str] = {}

    moved["missing"] = feature_spec(
        columns=(raw(), processed()), missing="drop_security"
    ).feature_version
    moved["tier"] = feature_spec(columns=(processed(), processed(MOMENTUM))).feature_version
    moved["factor_id"] = feature_spec(
        columns=(raw(), processed(REVERSAL.model_copy(update={"lookback_sessions": 9})))
    ).feature_version
    moved["transform_id"] = feature_spec(
        columns=(raw(), processed(transform=TRANSFORM.model_copy(update={"version": 2})))
    ).feature_version
    moved["neutralization_id"] = feature_spec(columns=(raw(), neutralized())).feature_version
    moved["columns"] = feature_spec(columns=(raw(),)).feature_version

    assert baseline not in moved.values(), moved
    assert len(set(moved.values())) == len(moved), moved

    dumped = spec.model_dump(mode="json", exclude_computed_fields=True)
    assert set(dumped) == {"schema_version", "columns", "missing"}
    assert set(dumped["columns"][0]) == {
        "schema_version",
        "feature_id",
        "tier",
        "factor_id",
        "transform_id",
        "neutralization_id",
    }


def test_a_redefined_factor_that_kept_its_version_moves_the_version_and_not_the_ids() -> None:
    """The case a readable id cannot separate and a content address can (`V2-P4-062`'s method).

    `FactorDefinition.qualified_key` is `key/vN` and its docstring says the quiet part: *"Two
    definitions can share a `qualified_key` and differ in `factor_id` (a redefinition that
    forgot to bump `version`)"*. `FactorRegistry` refuses two such definitions **in one
    collection**, which says nothing about two *builds* separated by a commit -- and
    `factor_observation_dataset` is `factor_obs_<key>_v<n>`, so both builds write into one
    partition.

    So the two spellings are measured against each other on that exact case: a 2-session
    reversal and a 9-session one, same key, same version. The ids are equal -- which is what a
    grammar built out of handles buys, and why the handle cannot be the whole answer -- and the
    `feature_version`s are not.
    """
    redefined = REVERSAL.model_copy(update={"lookback_sessions": 9})
    before = feature_spec(columns=(raw(),))
    after = feature_spec(columns=(raw(redefined),))

    assert redefined.qualified_key == REVERSAL.qualified_key
    assert redefined.factor_id != REVERSAL.factor_id
    assert after.feature_ids == before.feature_ids
    assert after.feature_version != before.feature_version


def test_two_matrices_that_share_every_id_and_differ_in_preprocessing_differ_in_version() -> None:
    """Preprocessing is `V2-P4-012`'s too, so it is inside the address and not beside it.

    `abstain` hands a model a `None`; `cross_section_median` hands it a number no security
    reported. Same columns, same ids, same order, and a cell that disagrees -- which is exactly
    the shape `feature_ids` cannot see, because the policy is not spelled in any of them.
    """
    versions = {
        policy: feature_spec(columns=(raw(), processed()), missing=policy).feature_version
        for policy in FEATURE_MISSING_POLICIES
    }

    assert len(set(versions.values())) == len(FEATURE_MISSING_POLICIES)
    ids = {
        feature_spec(columns=(raw(), processed()), missing=policy).feature_ids
        for policy in FEATURE_MISSING_POLICIES
    }
    assert len(ids) == 1


def test_the_declared_ids_are_a_feature_list_the_alpha_model_contract_accepts() -> None:
    """The contract this grammar exists to satisfy, driven rather than described.

    `_validate_feature_ids` refuses blank, repeated, unsorted and over-long lists, and it is
    `FeatureCrossSection`'s constructor that runs it. Building one out of `feature_ids` is what
    makes "the grammar produces a legal feature list" a measurement instead of an inspection of
    the sort order.
    """
    spec = feature_spec(columns=(raw(), processed(), neutralized(), raw(MOMENTUM)))

    section = FeatureCrossSection(
        as_of=datetime(2026, 1, 16, 9, 0, tzinfo=UTC),
        feature_ids=spec.feature_ids,
        rows=(FeatureRow(ts_code="000001.SZ", values=(0.1, 0.2, 0.3, 0.4)),),
    )

    assert section.feature_ids == spec.feature_ids
    with pytest.raises(AlphaModelError):
        FeatureCrossSection(
            as_of=section.as_of,
            feature_ids=tuple(reversed(spec.feature_ids)),
            rows=section.rows,
        )
