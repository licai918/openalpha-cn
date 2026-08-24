"""The neutralisation contracts (`V2-P3-004`): identity, vocabularies, and the import-time audits.

`domain/test_factor_transform.py`'s instruments pointed at the fourth transform. Three of them
are load-bearing and are the reason this file is not a pile of constructor checks:

1. **Every declared field of `FactorNeutralizationSpec` and of `FactorNeutralizationManifest`
   reaches its identity**, varied one at a time. Roadmap section 9 records the opposite case --
   two fields believed to feed `decision_id` and not doing so, because they were not fields of
   the model that is hashed -- so "the ID exists" is not what is asserted here.
2. **Both content-address helpers move when the *numbers* move**, not only when the parameters
   do. That is the half `FactorBuildManifest` had to learn twice.
3. **The two import-time audits fail in every direction they claim to.** An audit whose only
   call site is the one that passes is an audit nobody has seen fail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.factor import FactorNote
from openalpha_cn.domain.factor_neutralization import (
    ELIGIBILITY_CODES,
    INDUSTRY_LEVEL_FIELDS,
    INDUSTRY_LEVEL_ORDER,
    KNOWN_NEUTRALIZATION_LIMITATIONS,
    MARKET_CAP_MEASURE_ORDER,
    MARKET_CAP_SCALE_ORDER,
    NEUTRALIZATION_LIMITATION_CODES,
    NEUTRALIZED_COVERAGE_ORDER,
    NEUTRALIZED_VALUE_CODES,
    PARTICIPATING_PROCESSED_CODES,
    PARTICIPATION_RULE_ORDER,
    FactorNeutralizationError,
    FactorNeutralizationManifest,
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
    FactorNeutralizationStatistics,
    IndustryMarketCapCrossSection,
    NeutralizedFactorObservation,
    SecurityCharacteristic,
    _refuse_a_level_table_that_does_not_match_the_assignment_contract,
    _refuse_a_participation_table_that_cannot_answer_every_valued_processed_code,
    build_industry_market_cap_cross_section,
    characteristic_digest,
    industry_code_of,
    industry_group_sizes,
    neutralized_observation_digest,
    processed_observation_digest,
    validate_neutralized_factor_observation,
)
from openalpha_cn.domain.factor_transform import (
    PROCESSED_COVERAGE_ORDER,
    PROCESSED_VALUE_CODES,
    ProcessedFactorObservation,
)
from openalpha_cn.domain.industry_classification import IndustryAssignment

AS_OF: Final[datetime] = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
LATER: Final[datetime] = datetime(2026, 1, 13, 4, 0, tzinfo=UTC)


def _spec(**overrides: Any) -> FactorNeutralizationSpec:
    settings: dict[str, Any] = {
        "key": "probe_neutral",
        "version": 1,
        "industry_level": "L1",
        "market_cap_measure": "total_mv",
        "market_cap_scale": "log",
        "participation": "measured_only",
        "min_industry_members": 2,
        "min_cross_section": 10,
        **overrides,
    }
    return FactorNeutralizationSpec(**settings)


def _manifest(**overrides: Any) -> FactorNeutralizationManifest:
    settings: dict[str, Any] = {
        "neutralization_id": "fnz_one",
        "neutralization_key": "probe_neutral",
        "neutralization_version": 1,
        "source_factor_id": "fct_one",
        "source_factor_key": "probe",
        "source_factor_version": 1,
        "source_transform_id": "ftx_one",
        "source_transform_manifest_id": "ftm_one",
        "source_processed_digest": "prc_one",
        "neutralized_observation_digest": "nrs_one",
        "characteristic_digest": "chr_one",
        "as_of": AS_OF,
        "code_commit": "a1b2c3d",
        **overrides,
    }
    return FactorNeutralizationManifest(**settings)


def _characteristic(**overrides: Any) -> SecurityCharacteristic:
    settings: dict[str, Any] = {
        "subject": "000001.SZ",
        "industry_code": "801010.SI",
        "market_cap": 1_000_000.0,
        "is_backfilled": False,
        **overrides,
    }
    return SecurityCharacteristic(**settings)


def _cross_section(**overrides: Any) -> IndustryMarketCapCrossSection:
    settings: dict[str, Any] = {
        "as_of": AS_OF,
        "taxonomy": "SW2021",
        "industry_level": "L1",
        "market_cap_measure": "total_mv",
        "characteristics": [_characteristic()],
        **overrides,
    }
    return build_industry_market_cap_cross_section(**settings)


def _processed(**overrides: Any) -> ProcessedFactorObservation:
    settings: dict[str, Any] = {
        "subject": "000001.SZ",
        "as_of": AS_OF,
        "value": 1.5,
        "coverage": "processed",
        "transform_id": "ftx_one",
        "transform_manifest_id": "ftm_one",
        "source_factor_id": "fct_one",
        "source_manifest_id": "fbm_one",
        "source_coverage": "computed",
        **overrides,
    }
    return ProcessedFactorObservation(**settings)


def _observation(**overrides: Any) -> NeutralizedFactorObservation:
    settings: dict[str, Any] = {
        "subject": "000001.SZ",
        "as_of": AS_OF,
        "value": 0.25,
        "coverage": "neutralized",
        "neutralization_id": "fnz_one",
        "neutralization_manifest_id": "fnm_one",
        "source_factor_id": "fct_one",
        "source_transform_id": "ftx_one",
        "source_transform_manifest_id": "ftm_one",
        "source_coverage": "processed",
        "industry_code": "801010.SI",
        **overrides,
    }
    return NeutralizedFactorObservation(**settings)


# --- the vocabularies ---------------------------------------------------------------------------


def test_the_declared_vocabularies_are_the_closed_sets_this_issue_shipped() -> None:
    """The four closed sets, held as literals so a member cannot arrive unreviewed.

    Equality rather than membership, `FACTOR_COVERAGE_ORDER`'s form: a membership assertion can
    see a code that was renamed and never one that was removed.
    """
    assert NEUTRALIZED_COVERAGE_ORDER == (
        "neutralized",
        "not_a_participant",
        "industry_missing",
        "market_cap_missing",
        "thin_industry",
        "insufficient_cross_section",
        "degenerate_design",
    )
    assert MARKET_CAP_MEASURE_ORDER == ("total_mv", "circ_mv")
    assert MARKET_CAP_SCALE_ORDER == ("level", "log")
    assert PARTICIPATION_RULE_ORDER == ("measured_only", "measured_and_imputed")
    assert INDUSTRY_LEVEL_ORDER == ("L1", "L2", "L3")
    assert {"neutralized"} == NEUTRALIZED_VALUE_CODES
    assert {"industry_missing", "market_cap_missing", "thin_industry"} == ELIGIBILITY_CODES


def test_exactly_one_neutralised_code_carries_a_value_unlike_the_processed_planes_two() -> None:
    """The asymmetry with `ProcessedCoverage`, stated as a comparison rather than in prose.

    The processed plane has two value-carrying codes because it *imputes*; this one has one
    because it does not. A build that could not regress a security gives it a code, never a
    substitute residual -- so there is no `imputed` here to tell apart from a measurement.
    """
    assert len(NEUTRALIZED_VALUE_CODES) == 1
    assert {"processed", "imputed"} == PROCESSED_VALUE_CODES
    assert set() == NEUTRALIZED_VALUE_CODES & PROCESSED_VALUE_CODES


def test_the_participation_table_names_exactly_the_processed_codes_that_carry_a_number() -> None:
    """`measured_only` is the strict half of `measured_and_imputed`, and both are subsets."""
    assert PARTICIPATING_PROCESSED_CODES["measured_only"] == {"processed"}
    assert PARTICIPATING_PROCESSED_CODES["measured_and_imputed"] == {"processed", "imputed"}
    union = {code for codes in PARTICIPATING_PROCESSED_CODES.values() for code in codes}
    assert union == set(PROCESSED_VALUE_CODES)


def test_a_spec_admits_exactly_the_codes_its_declared_rule_names() -> None:
    strict = _spec(participation="measured_only")
    inclusive = _spec(participation="measured_and_imputed")

    assert [code for code in PROCESSED_COVERAGE_ORDER if strict.admits(code)] == ["processed"]
    assert [code for code in PROCESSED_COVERAGE_ORDER if inclusive.admits(code)] == [
        "processed",
        "imputed",
    ]

    with pytest.raises(FactorNeutralizationError, match="not a declared processed coverage code"):
        strict.admits("neutralized")  # type: ignore[arg-type]


# --- the import-time audits ---------------------------------------------------------------------


def test_the_participation_audit_refuses_a_valued_processed_code_no_rule_admits() -> None:
    """The direction with teeth: a sixth processed code carrying a number and no rule for it.

    Driven with an argument rather than by editing the module's globals, which is why the
    function takes them. The failure is what would otherwise happen *silently*: every security
    under the new code dropped from every regression, with no census column able to say so.
    """
    with pytest.raises(
        FactorNeutralizationError, match="carry a processed value and no participation rule"
    ):
        _refuse_a_participation_table_that_cannot_answer_every_valued_processed_code(
            {"measured_only": frozenset({"processed"})}, ("measured_only",)
        )


def test_the_participation_audit_refuses_a_rule_with_no_entry() -> None:
    with pytest.raises(FactorNeutralizationError, match="a rule with no entry raises KeyError"):
        _refuse_a_participation_table_that_cannot_answer_every_valued_processed_code(
            {"measured_only": frozenset({"processed", "imputed"})},
            ("measured_only", "measured_and_imputed"),
        )


def test_the_participation_audit_refuses_an_entry_naming_an_undeclared_processed_code() -> None:
    with pytest.raises(
        FactorNeutralizationError, match="the processed coverage vocabulary does not declare"
    ):
        _refuse_a_participation_table_that_cannot_answer_every_valued_processed_code(
            {"only": frozenset({"processed", "imputed", "smoothed"})}, ("only",)
        )


def test_the_level_audit_refuses_a_level_with_no_field_and_a_field_the_contract_lacks() -> None:
    """Both directions of the second import-time audit, which no per-level test can reach."""
    with pytest.raises(FactorNeutralizationError, match="a level with no field raises KeyError"):
        _refuse_a_level_table_that_does_not_match_the_assignment_contract(
            {"L1": "l1_code"}, ("L1", "L2"), ("l1_code", "l2_code")
        )

    with pytest.raises(FactorNeutralizationError, match="a stale field name fails at the first"):
        _refuse_a_level_table_that_does_not_match_the_assignment_contract(
            {"L1": "level_one_code"}, ("L1",), ("l1_code",)
        )


def test_the_shipped_level_table_reads_fields_the_assignment_contract_actually_has() -> None:
    """The audit's own subject, asserted positively so the check above is not vacuous."""
    assignment = IndustryAssignment(
        ts_code="000001.SZ",
        l1_code="801010.SI",
        l2_code="801011.SI",
        l3_code="850111.SI",
        effective_from=AS_OF.date(),
    )

    assert INDUSTRY_LEVEL_FIELDS == {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}
    assert [industry_code_of(assignment, level) for level in INDUSTRY_LEVEL_ORDER] == [
        "801010.SI",
        "801011.SI",
        "850111.SI",
    ]

    with pytest.raises(FactorNeutralizationError, match="is not a declared industry level"):
        industry_code_of(assignment, "L4")  # type: ignore[arg-type]


def test_an_assignment_with_a_blank_code_at_the_declared_level_names_no_group() -> None:
    """The reachable path past `build_security_industry_history`'s own refusal.

    `IndustryAssignment` is a plain carrier that validates nothing -- its docstring says so -- so
    a blank `l2_code` is constructible and would become a group whose name is the empty string,
    silently merging every such security into one industry that does not exist. The refusal names
    the security, the level and where the rule that should have caught it lives.
    """
    hollow = IndustryAssignment(
        ts_code="000001.SZ",
        l1_code="801010.SI",
        l2_code="  ",
        l3_code="850111.SI",
        effective_from=AS_OF.date(),
    )

    assert industry_code_of(hollow, "L1") == "801010.SI"
    with pytest.raises(FactorNeutralizationError, match="names no group"):
        industry_code_of(hollow, "L2")


# --- the spec ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("key", "other_key"),
        ("version", 2),
        ("industry_level", "L2"),
        ("market_cap_measure", "circ_mv"),
        ("market_cap_scale", "level"),
        ("participation", "measured_and_imputed"),
        ("min_industry_members", 3),
        ("min_cross_section", 11),
    ],
)
def test_every_declared_spec_field_moves_the_identity(field: str, value: object) -> None:
    """One field at a time, `FactorDefinition`'s instrument.

    The parametrisation covers every declared field except `schema_version`, which cannot be
    varied because its type is a one-member `Literal` -- and which
    `test_the_hashed_payload_is_exactly_the_declared_fields` covers instead.
    """
    baseline = _spec()

    assert _spec(**{field: value}).neutralization_id != baseline.neutralization_id


def test_the_hashed_payload_is_exactly_the_declared_fields() -> None:
    """Every declared field is in the payload and nothing else is.

    The half a per-field variation test cannot reach: a model that declared the *wrong* fields
    would pass every assertion above. `schema_version` is in the payload and is why the
    parametrisation above stops one short.
    """
    spec = _spec()
    payload = spec.model_dump(mode="json", exclude_computed_fields=True)

    assert set(payload) == set(FactorNeutralizationSpec.model_fields)
    assert "schema_version" in payload
    assert "neutralization_id" not in payload
    assert "qualified_key" not in payload


def test_two_specs_with_the_same_settings_share_an_identity() -> None:
    assert _spec().neutralization_id == _spec().neutralization_id
    assert _spec().qualified_key == "probe_neutral/v1"


def test_a_one_member_industry_floor_is_refused_at_declaration_time() -> None:
    """The one bound in this contract that refuses a declarable configuration, and why.

    A singleton's residual is exactly `0.0` by construction whatever the slope is, so storing it
    under `neutralized` would put a structural constant in the column a report ranks on. Unlike
    `min_cross_section`, which admits `1` and records the choice, this floor makes the degenerate
    case undeclarable.
    """
    with pytest.raises(ValidationError, match="greater than or equal to 2"):
        _spec(min_industry_members=1)

    assert _spec(min_industry_members=2).min_industry_members == 2


def test_a_per_industry_floor_above_the_whole_panel_floor_is_refused() -> None:
    with pytest.raises(ValidationError, match="exceeds min_cross_section"):
        _spec(min_industry_members=11, min_cross_section=10)

    assert _spec(min_industry_members=10, min_cross_section=10).min_industry_members == 10


def test_the_two_floors_have_the_range_checks_they_declare() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1000"):
        _spec(min_industry_members=1001, min_cross_section=10000)
    with pytest.raises(ValidationError, match="less than or equal to 10000"):
        _spec(min_cross_section=10001)

    assert _spec(min_cross_section=10000).min_cross_section == 10000


def test_a_key_that_is_not_a_panel_identifier_is_refused() -> None:
    with pytest.raises(ValidationError, match="neutralization key"):
        _spec(key="not a key")


def test_prose_cannot_reach_this_identity_because_it_cannot_enter_this_contract() -> None:
    """The inherited defect, measured as closed rather than pinned as a cost.

    This test used to assert the opposite: two specs whose only difference was prose described
    the same arithmetic and had two identities, so every stored `neutralization_manifest_id`
    moved for a typo fix, and the rule was "edit `summary` only with a version bump". `V2-P3-004`
    then had to break that rule to retract three unreproducible figures, recorded the exception
    in the field's own docstring, and gave it an expiry. This is the expiry: all three contracts
    dropped the field together, so there is no `summary=` to pass and no rule left to break.
    """
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _spec(summary="remove the industry mean, then the size slope")

    assert "summary" not in FactorNeutralizationSpec.model_fields
    assert "summary" not in FactorNeutralizationManifest.model_fields


# --- the registry -------------------------------------------------------------------------------


def test_an_empty_registry_is_refused() -> None:
    with pytest.raises(FactorNeutralizationError, match="at least one spec"):
        FactorNeutralizationRegistry(())


def test_two_specs_answering_to_one_name_are_refused() -> None:
    with pytest.raises(FactorNeutralizationError, match="declared more than once"):
        FactorNeutralizationRegistry((_spec(), _spec(market_cap_scale="level")))


def test_a_registry_resolves_by_qualified_key_and_by_identity() -> None:
    spec = _spec()
    registry = FactorNeutralizationRegistry((spec,))

    assert registry.get("probe_neutral/v1") is spec
    assert registry.by_id(spec.neutralization_id) is spec
    assert registry.qualified_keys == ("probe_neutral/v1",)
    assert registry.neutralization_ids == (spec.neutralization_id,)

    with pytest.raises(FactorNeutralizationError, match="is not a declared neutralisation"):
        registry.get("absent/v1")
    with pytest.raises(FactorNeutralizationError, match="is not a neutralisation this build"):
        registry.by_id("fnz_absent")


def test_the_registry_resolves_prose_and_refuses_it_for_an_undeclared_handle() -> None:
    """`FactorRegistry.note_for`'s three ways on the third registry, so the contract is the same
    contract everywhere rather than three that happen to be spelled alike: written prose comes
    back, a declared spec with nothing written about it answers `None`, and a handle this build
    does not declare is a fault rather than an absence."""
    written = FactorNote(subject="probe_neutral/v1", summary="industry mean, then the size slope")
    registry = FactorNeutralizationRegistry((_spec(), _spec(key="other_neutral")), notes=(written,))

    assert registry.note_for("probe_neutral/v1") == written.summary
    assert registry.note_for("other_neutral/v1") is None
    with pytest.raises(FactorNeutralizationError, match="is not a declared neutralisation"):
        registry.note_for("absent/v1")
    with pytest.raises(FactorNeutralizationError, match="is not a declared neutralisation, so"):
        FactorNeutralizationRegistry(
            (_spec(),), notes=(FactorNote(subject="ghost/v1", summary="x"),)
        )


# --- the second cross section --------------------------------------------------------------------


def test_a_cross_section_orders_its_three_collections_and_reports_them_together() -> None:
    cross = _cross_section(
        characteristics=[_characteristic(subject="600000.SH"), _characteristic()],
        without_industry=["600519.SH", "000002.SZ"],
        without_market_cap=["300750.SZ"],
    )

    assert cross.subjects() == ("000001.SZ", "000002.SZ", "300750.SZ", "600000.SH", "600519.SH")
    assert cross.without_industry == ("000002.SZ", "600519.SH")
    assert cross.without_market_cap == ("300750.SZ",)
    assert [item.subject for item in cross.characteristics] == ["000001.SZ", "600000.SH"]
    assert cross.get("000001.SZ") is not None
    assert cross.get("000002.SZ") is None


def test_a_non_positive_market_capitalisation_is_refused_rather_than_coded() -> None:
    """The one input that would make the declared `log` scale undefined.

    Refused at the contract so the `log` branch has no special case at all, and because
    `domain/daily_prices.py` already refuses a null `total_mv` at the write for the reason it
    states: a null one silently drops a name from a regression.
    """
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(FactorNeutralizationError, match="finite positive number"):
            _cross_section(characteristics=[_characteristic(market_cap=bad)])


def test_a_subject_in_two_of_the_three_collections_is_refused() -> None:
    with pytest.raises(FactorNeutralizationError, match="more than one of this cross section"):
        _cross_section(without_industry=["000001.SZ"])
    with pytest.raises(FactorNeutralizationError, match="more than one of this cross section"):
        _cross_section(without_industry=["600000.SH"], without_market_cap=["600000.SH"])


def test_a_duplicated_subject_is_refused_in_the_complete_rows_and_in_a_residue() -> None:
    with pytest.raises(FactorNeutralizationError, match="appears more than once in this cross"):
        _cross_section(characteristics=[_characteristic(), _characteristic()])
    with pytest.raises(FactorNeutralizationError, match="appear more than once in without_"):
        _cross_section(without_industry=["600000.SH", "600000.SH"])


def test_a_blank_industry_code_or_subject_is_refused() -> None:
    with pytest.raises(FactorNeutralizationError, match="non-empty string"):
        _cross_section(characteristics=[_characteristic(industry_code=" ")])
    with pytest.raises(FactorNeutralizationError, match="non-empty string"):
        _cross_section(characteristics=[_characteristic(subject="")])
    with pytest.raises(FactorNeutralizationError, match="non-empty string"):
        _cross_section(without_industry=[" "])


def test_a_malformed_frame_is_refused() -> None:
    with pytest.raises(FactorNeutralizationError, match="non-empty string"):
        _cross_section(taxonomy=" ")
    with pytest.raises(FactorNeutralizationError, match="not a declared industry level"):
        _cross_section(industry_level="L4")
    with pytest.raises(FactorNeutralizationError, match="not a declared market cap measure"):
        _cross_section(market_cap_measure="free_mv")
    with pytest.raises(ValueError, match="timezone"):
        _cross_section(as_of=datetime(2026, 1, 12, 4, 0))


def test_the_backfill_count_reports_the_complete_rows_that_predate_their_taxonomy() -> None:
    cross = _cross_section(
        characteristics=[
            _characteristic(is_backfilled=True),
            _characteristic(subject="600000.SH", is_backfilled=False),
            _characteristic(subject="600519.SH", is_backfilled=True),
        ]
    )

    assert cross.backfilled_count == 2


# --- the two digests ------------------------------------------------------------------------------


def test_the_characteristic_digest_moves_when_any_part_of_the_cross_section_moves() -> None:
    """Every component of the second input, varied one at a time.

    The residue lists are in here for the reason the complete rows are: a build whose market
    became 3% less classifiable regressed a different cross section, and an identity blind to
    that would let the two share a partition.
    """
    baseline = characteristic_digest(_cross_section())

    assert characteristic_digest(_cross_section(taxonomy="SW2014")) != baseline
    assert characteristic_digest(_cross_section(industry_level="L2")) != baseline
    assert characteristic_digest(_cross_section(market_cap_measure="circ_mv")) != baseline
    assert (
        characteristic_digest(
            _cross_section(characteristics=[_characteristic(industry_code="801020.SI")])
        )
        != baseline
    )
    assert (
        characteristic_digest(_cross_section(characteristics=[_characteristic(market_cap=2.0)]))
        != baseline
    )
    assert (
        characteristic_digest(_cross_section(characteristics=[_characteristic(is_backfilled=True)]))
        != baseline
    )
    assert characteristic_digest(_cross_section(without_industry=["600000.SH"])) != baseline
    assert characteristic_digest(_cross_section(without_market_cap=["600000.SH"])) != baseline


def test_the_characteristic_digest_is_order_free_and_starts_with_its_prefix() -> None:
    one = _cross_section(
        characteristics=[_characteristic(), _characteristic(subject="600000.SH")],
        without_industry=["300750.SZ", "600519.SH"],
    )
    other = _cross_section(
        characteristics=[_characteristic(subject="600000.SH"), _characteristic()],
        without_industry=["600519.SH", "300750.SZ"],
    )

    assert characteristic_digest(one) == characteristic_digest(other)
    assert characteristic_digest(one).startswith("chr_")


def test_a_hand_built_cross_section_with_a_non_finite_cap_is_refused_by_name() -> None:
    """The reachable path past `build_industry_market_cap_cross_section`'s own refusal.

    The message names the subjects rather than propagating `json.dumps`' bare "Out of range
    float values are not JSON compliant", whose reader is somebody holding thousands of rows
    with no way to find the one.
    """
    hand_built = IndustryMarketCapCrossSection(
        as_of=AS_OF,
        taxonomy="SW2021",
        industry_level="L1",
        market_cap_measure="total_mv",
        characteristics=(_characteristic(market_cap=float("inf")),),
        without_industry=(),
        without_market_cap=(),
    )

    with pytest.raises(FactorNeutralizationError, match="non-finite market capitalisation"):
        characteristic_digest(hand_built)


def test_a_duplicated_subject_in_a_hand_built_cross_section_has_no_address() -> None:
    hand_built = IndustryMarketCapCrossSection(
        as_of=AS_OF,
        taxonomy="SW2021",
        industry_level="L1",
        market_cap_measure="total_mv",
        characteristics=(_characteristic(),),
        without_industry=("000001.SZ",),
        without_market_cap=(),
    )

    with pytest.raises(FactorNeutralizationError, match="appears more than once"):
        characteristic_digest(hand_built)


def test_the_processed_digest_moves_with_the_values_and_not_with_the_provenance() -> None:
    """What makes exempting the `panel` argument a measurement rather than a promise.

    A `transform_manifest_id` identifies a transform's inputs, so two processed panels carrying
    one manifest and different numbers are constructible. The digest is what sees the difference;
    the provenance columns are identical on every row of one panel by a guard, so hashing them
    per row would add copies of a constant and nothing to the address.
    """
    baseline = processed_observation_digest([_processed()])

    assert processed_observation_digest([_processed(value=1.6)]) != baseline
    assert processed_observation_digest([_processed(subject="600000.SH")]) != baseline
    assert (
        processed_observation_digest(
            [_processed(coverage="imputed", source_coverage="input_missing")]
        )
        != baseline
    )
    assert processed_observation_digest([_processed(transform_id="ftx_two")]) == baseline
    assert processed_observation_digest([_processed(source_manifest_id="fbm_two")]) == baseline
    assert baseline.startswith("prc_")


def test_the_processed_digest_refuses_a_duplicated_subject() -> None:
    with pytest.raises(FactorNeutralizationError, match="appears more than once"):
        processed_observation_digest([_processed(), _processed()])


def test_the_processed_digest_refuses_a_non_finite_value_by_name() -> None:
    """The reachable path, which is the one the message describes.

    `validate_processed_factor_observation` refuses a non-finite value at both of its call sites,
    so the only way such a row reaches a digest is a subclass that overrode `__post_init__` --
    which is exactly what the refusal tells its reader. Built that way here rather than with
    `dataclasses.replace`, which re-runs the constructor and would make this test assert about
    the *processed* plane's guard instead of this one's.
    """

    class Bypassing(ProcessedFactorObservation):
        def __post_init__(self) -> None:  # pragma: no cover - the point is that it does nothing
            return

    poisoned = Bypassing(
        subject="000001.SZ",
        as_of=AS_OF,
        value=float("nan"),
        coverage="processed",
        transform_id="ftx_one",
        transform_manifest_id="ftm_one",
        source_factor_id="fct_one",
        source_manifest_id="fbm_one",
        source_coverage="computed",
    )

    with pytest.raises(FactorNeutralizationError, match="non-finite processed value"):
        processed_observation_digest([poisoned])


# --- the manifest --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("neutralization_id", "fnz_two"),
        ("neutralization_key", "other"),
        ("neutralization_version", 2),
        ("source_factor_id", "fct_two"),
        ("source_factor_key", "other"),
        ("source_factor_version", 2),
        ("source_transform_id", "ftx_two"),
        ("source_transform_manifest_id", "ftm_two"),
        ("source_processed_digest", "prc_two"),
        ("characteristic_digest", "chr_two"),
        ("neutralized_observation_digest", "nrs_two"),
        ("as_of", LATER),
        ("code_commit", "9999999"),
    ],
)
def test_every_declared_manifest_field_moves_the_identity(field: str, value: object) -> None:
    baseline = _manifest()

    assert (
        _manifest(**{field: value}).neutralization_manifest_id
        != baseline.neutralization_manifest_id
    )


def test_the_manifest_carries_no_wall_clock_and_no_timezone_and_no_taxonomy() -> None:
    """Three deliberate absences, each of which is a claim rather than an omission.

    `built_at` would make a rebuild of an unchanged build unwritable past the drop guard;
    `date_timezone` would reach the identity and decide nothing, because this build resolves no
    date; `industry_taxonomy` would be a second statement of a fact `characteristic_digest`
    already hashes, and two statements of one fact can disagree.
    """
    declared = set(FactorNeutralizationManifest.model_fields)

    assert "built_at" not in declared
    assert "date_timezone" not in declared
    assert "industry_taxonomy" not in declared
    assert declared == {
        "schema_version",
        "neutralization_id",
        "neutralization_key",
        "neutralization_version",
        "source_factor_id",
        "source_factor_key",
        "source_factor_version",
        "source_transform_id",
        "source_transform_manifest_id",
        "source_processed_digest",
        "characteristic_digest",
        "neutralized_observation_digest",
        "as_of",
        "code_commit",
    }


def test_the_manifest_normalises_its_instant_and_refuses_a_naive_one() -> None:
    assert _manifest().as_of == AS_OF
    with pytest.raises(ValidationError, match="timezone"):
        _manifest(as_of=datetime(2026, 1, 12, 4, 0))


def test_re_declaring_the_same_manifest_reproduces_its_identity() -> None:
    assert _manifest().neutralization_manifest_id == _manifest().neutralization_manifest_id
    assert _manifest().neutralization_manifest_id.startswith("fnm_")


# --- the statistics ------------------------------------------------------------------------------


def _statistics(**overrides: Any) -> FactorNeutralizationStatistics:
    settings: dict[str, Any] = {
        "participant_count": 100,
        "industry_count": 5,
        "smallest_industry_size": 10,
        "largest_industry_size": 40,
        "backfilled_industry_count": 0,
        "market_cap_slope": -0.05,
        "market_cap_dispersion": 12.5,
        "residual_dispersion": 0.98,
        **overrides,
    }
    return FactorNeutralizationStatistics(**settings)


def test_the_statistics_refuse_every_shape_that_would_describe_no_real_build() -> None:
    assert _statistics().participant_count == 100

    with pytest.raises(FactorNeutralizationError, match="cannot be negative"):
        _statistics(participant_count=-1)
    with pytest.raises(FactorNeutralizationError, match="the two run backwards"):
        _statistics(smallest_industry_size=41)
    with pytest.raises(FactorNeutralizationError, match="cannot be wider than the cross section"):
        _statistics(largest_industry_size=101)
    with pytest.raises(FactorNeutralizationError, match="cannot cover more securities"):
        _statistics(backfilled_industry_count=101)
    with pytest.raises(FactorNeutralizationError, match="more parts than members"):
        _statistics(industry_count=101, largest_industry_size=100)
    with pytest.raises(FactorNeutralizationError, match="not a finite number"):
        _statistics(market_cap_slope=float("inf"))
    with pytest.raises(FactorNeutralizationError, match="degenerate_design code's own case"):
        _statistics(market_cap_dispersion=0.0)
    with pytest.raises(FactorNeutralizationError, match="a standard deviation is not negative"):
        _statistics(residual_dispersion=-0.1)


def test_an_empty_build_records_its_participant_count_and_nothing_else() -> None:
    """The shape both whole-panel codes produce: a count that survives, everything else absent."""
    empty = _statistics(
        participant_count=4,
        industry_count=0,
        smallest_industry_size=0,
        largest_industry_size=0,
        market_cap_slope=None,
        market_cap_dispersion=None,
        residual_dispersion=None,
    )

    assert empty.participant_count == 4
    assert empty.market_cap_slope is None


# --- the stored observation -----------------------------------------------------------------------


def test_a_neutralised_row_carries_a_value_and_a_group_or_neither() -> None:
    """The pairing rule, which is what makes a stored residual interpretable.

    A residual is a deviation from a group and must name it; a row with no residual was in no
    group. The two halves are one rule because either alone is satisfiable by a row that lies.
    """
    assert _observation().value == 0.25

    with pytest.raises(FactorNeutralizationError, match="carries None"):
        _observation(value=None)
    with pytest.raises(FactorNeutralizationError, match="must name it"):
        _observation(industry_code=None)
    with pytest.raises(FactorNeutralizationError, match="must name it"):
        _observation(value=None, coverage="industry_missing", industry_code="801010.SI")
    assert (
        _observation(value=None, coverage="industry_missing", industry_code=None).industry_code
        is None
    )


def test_a_neutralised_row_must_come_from_a_processed_code_that_carried_a_value() -> None:
    for code in ("processed", "imputed"):
        assert _observation(source_coverage=code).source_coverage == code

    with pytest.raises(FactorNeutralizationError, match="only processed codes that carry a value"):
        _observation(source_coverage="source_not_computed")


def test_the_three_eligibility_codes_contradict_a_source_that_carried_nothing() -> None:
    """`industry_missing`, `market_cap_missing` and `thin_industry` all assert a value existed."""
    for code in sorted(ELIGIBILITY_CODES):
        assert _observation(value=None, coverage=code, industry_code=None).coverage == code
        with pytest.raises(FactorNeutralizationError, match="belongs under 'not_a_participant'"):
            _observation(
                value=None,
                coverage=code,
                industry_code=None,
                source_coverage="insufficient_cross_section",
            )


def test_an_undeclared_code_in_either_vocabulary_is_refused() -> None:
    with pytest.raises(FactorNeutralizationError, match="not a declared neutralised coverage"):
        _observation(coverage="squashed")
    with pytest.raises(FactorNeutralizationError, match="not a declared processed coverage"):
        _observation(source_coverage="computed")


def test_a_non_finite_residual_is_refused_and_a_blank_group_is_too() -> None:
    with pytest.raises(FactorNeutralizationError, match="non-finite residual"):
        _observation(value=float("nan"))
    with pytest.raises(FactorNeutralizationError, match="a blank group name is not a group"):
        _observation(industry_code="  ")
    with pytest.raises(FactorNeutralizationError, match="must name a subject"):
        _observation(subject="")


def test_the_write_boundary_refuses_a_row_that_skipped_the_constructor() -> None:
    """The second call site, and why there is one.

    A frozen dataclass with `slots=True` is still subclassable, and a subclass overriding
    `__post_init__` was *measured* one plane up to put an empty subject and a backwards window
    into a Parquet partition.
    """

    class Bypassing(NeutralizedFactorObservation):
        def __post_init__(self) -> None:  # pragma: no cover - the point is that it does nothing
            return

    smuggled = Bypassing(
        subject="000001.SZ",
        as_of=AS_OF,
        value=0.25,
        coverage="industry_missing",
        neutralization_id="fnz_one",
        neutralization_manifest_id="fnm_one",
        source_factor_id="fct_one",
        source_transform_id="ftx_one",
        source_transform_manifest_id="ftm_one",
        source_coverage="processed",
        industry_code="801010.SI",
    )

    with pytest.raises(FactorNeutralizationError, match="every other code carries None"):
        validate_neutralized_factor_observation(smuggled)


def test_group_sizes_count_every_member_and_nothing_else() -> None:
    assert industry_group_sizes(["a", "b", "a", "a", "c"]) == {"a": 3, "b": 1, "c": 1}
    assert industry_group_sizes([]) == {}


# --- the limitation registry ----------------------------------------------------------------------


def test_the_neutralisation_limitations_are_the_five_this_issue_measured() -> None:
    """Set equality, `KNOWN_ADJUSTMENT_LIMITATIONS`' form since `V2-P1-005`.

    Equality rather than membership because a membership assertion is additive: it can see a code
    that was renamed and never one that was removed. This is also the executable reference
    `tests/unit/test_known_limitation_registries.py` requires every declared code to have.

    The fourth is the sharpest and is driven rather than merely declared:
    `tests/integration/panel/test_factor_neutralizations.py::
    test_a_stored_membership_year_the_caller_did_not_name_refuses_the_day_on_this_builder`
    reproduces it against real partitions. **It has been renamed twice as its subject shrank** --
    from `the_two_foreign_inputs_are_read_whole_partition_...` to
    `the_industry_input_is_read_whole_partition_...` to what it is now -- and each rename went red
    here first, which is the whole reason this is an equality.
    """
    assert {
        "no_cross_section_is_neutralisable_before_2021_12_13",
        "an_industry_answer_inside_the_era_can_still_be_backfilled",
        "the_residual_is_orthogonal_to_the_design_and_not_to_size_itself",
        "a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it",
        "a_thin_industry_is_coded_rather_than_pooled",
    } == NEUTRALIZATION_LIMITATION_CODES
    assert len(KNOWN_NEUTRALIZATION_LIMITATIONS) == len(NEUTRALIZATION_LIMITATION_CODES)
    assert all(item.detail.strip() for item in KNOWN_NEUTRALIZATION_LIMITATIONS)


def test_the_residual_digest_is_the_third_tier_of_the_same_address() -> None:
    """`V2-P3-019`'s top tier, and the one with nothing above it to address it by accident.

    The raw tier's answers are addressed by `FactorTransformManifest.source_observation_digest`
    and the processed tier's by `FactorNeutralizationManifest.source_processed_digest` -- both by
    the manifest of the tier that *consumed* them. Nothing consumes a neutralised panel inside
    this repository, and it is the tier the attribution grid's verdict is read off, so its own
    manifest is the only place its answers could be addressed from.

    The same three positions as its two siblings and the same two exclusions: the provenance
    pointers are constant on every row of one panel by a guard, and `industry_code` is a label on
    the regression a residual came out of rather than the residual.
    """
    baseline = neutralized_observation_digest([_observation()])

    assert neutralized_observation_digest([_observation(value=0.26)]) != baseline
    assert neutralized_observation_digest([_observation(subject="600000.SH")]) != baseline
    assert (
        neutralized_observation_digest(
            [_observation(coverage="industry_missing", value=None, industry_code=None)]
        )
        != baseline
    )
    assert neutralized_observation_digest([_observation(industry_code="801020.SI")]) == baseline
    assert neutralized_observation_digest([_observation(neutralization_id="fnz_two")]) == baseline
    assert baseline.startswith("nrs_")


def test_the_residual_digest_refuses_a_duplicated_subject() -> None:
    with pytest.raises(FactorNeutralizationError, match="appears more than once"):
        neutralized_observation_digest([_observation(), _observation()])


def test_the_residual_digest_refuses_a_non_finite_residual_by_name() -> None:
    """`test_the_processed_digest_refuses_a_non_finite_value_by_name` one tier up, and built the
    same way: through a subclass that overrode `__post_init__`, because that is the only door a
    non-finite residual can reach a digest through and it is what the refusal tells its reader."""

    class Bypassing(NeutralizedFactorObservation):
        def __post_init__(self) -> None:  # pragma: no cover - the point is that it does nothing
            return

    poisoned = Bypassing(
        subject="000001.SZ",
        as_of=AS_OF,
        value=float("inf"),
        coverage="neutralized",
        neutralization_id="fnz_one",
        neutralization_manifest_id="fnm_one",
        source_factor_id="fct_one",
        source_transform_id="ftx_one",
        source_transform_manifest_id="ftm_one",
        source_coverage="processed",
        industry_code="801010.SI",
    )

    with pytest.raises(FactorNeutralizationError, match="non-finite residual"):
        neutralized_observation_digest([poisoned])
