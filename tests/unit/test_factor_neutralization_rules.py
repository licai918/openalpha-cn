"""The neutralisation's arithmetic and its tables (`V2-P3-004`), held to numbers not to shapes.

`test_factor_transform_rules.py`'s instruments pointed one tier down. What is different here is
the first section, and it is the reason this file is longer than the contract's:

**The closed form is verified against a dense least-squares solve written in this file.**
`panel_neutralization._neutralize` does not solve a system -- it removes each industry's mean and
fits one slope, which is the Frisch-Waugh-Lovell residual of a regression on
`[industry dummies, market cap]`. That is an *algebraic claim*, and a test that only checked
properties of the output (residuals sum to zero within each group, residuals are orthogonal to
the regressor) would be satisfied by several wrong implementations. So `_dense_residuals` builds
the design matrix, forms the Gram matrix, solves by Gaussian elimination with partial pivoting
and subtracts the fit -- and the two are compared to a stated bound, in both of the design's
identifications.

The dense solver lives here rather than in `src/` deliberately. It is an *instrument*: 90x slower
than the thing it verifies, needed by nothing that ships, and its presence in `src/` would be a
second definition of what a residual is.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from openalpha_cn.domain.factor import MAX_FACTOR_KEY_LENGTH
from openalpha_cn.domain.factor_neutralization import (
    INDUSTRY_LEVELS,
    MARKET_CAP_MEASURES,
    MARKET_CAP_SCALES,
    NEUTRALIZED_COVERAGE_ORDER,
    PARTICIPATING_PROCESSED_CODES,
    FactorNeutralizationManifest,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.panel_batch import MAX_IDENTIFIER_LENGTH, validate_panel_identifier
from openalpha_cn.panel_doctor import DATASET_CADENCE
from openalpha_cn.panel_factors import (
    FACTOR_DEFINITIONS,
    FactorEngineError,
)
from openalpha_cn.panel_factors import (
    _refuse_to_drop_a_stored_build as _factor_plane_drop_guard,
)
from openalpha_cn.panel_neutralization import (
    _MARKET_CAP_READERS,
    _NEUTRALIZATION_MANIFEST_COLUMN_KINDS,
    _NEUTRALIZATION_MANIFEST_HEAD_COLUMNS,
    _NEUTRALIZED_COLUMN_KINDS,
    _REGRESSORS,
    FACTOR_NEUTRALIZATION_MANIFEST_DATASET_PREFIX,
    FACTOR_NEUTRALIZATIONS,
    FACTOR_NEUTRALIZED_DATASET_PREFIX,
    INDUSTRY_AND_SIZE,
    NEUTRALIZATION_MANIFEST_DATA_COLUMNS,
    NEUTRALIZATION_MANIFEST_PANEL_COLUMNS,
    NEUTRALIZED_CENSUS_COLUMNS,
    NEUTRALIZED_OBSERVATION_PANEL_COLUMNS,
    _neutralization_manifest_from_row,
    _neutralize,
    _neutralized_observation_from_row,
    _population_stdev,
    _processed_code,
    _refuse_neutralization_table_drift,
    _refuse_to_drop_a_stored_build,
    factor_neutralization_manifest_dataset,
    neutralized_factor_dataset,
)

PANEL_SIZE: Final[int] = 5534
"""ADR-0002's stated whole-market cross section: the size the agreement bound is measured at."""

INDUSTRY_COUNT: Final[int] = 31
"""SW2021's measured level-one node count (`domain/industry_classification.py`)."""

AGREEMENT_BOUND: Final[float] = 1e-14
"""How far the closed form and the dense solve may disagree on a 5,534-name cross section.

The measured figure is **8.88e-16** -- three ulps of a residual whose rms is about 1 -- and the
bound is set two orders above it rather than at the measurement, because the measurement is a
function of the random panel below and pinning it exactly would be pinning the seed. A bound this
tight still falsifies every implementation error this test exists to catch: dropping the slope
term moves residuals by 0.02, demeaning `y` and not `x` by 0.02, and using a sample rather than a
population mean by 1e-4.
"""


# --- the dense reference -------------------------------------------------------------------------


def _design(groups: list[str], regressor: list[float], *, mode: str) -> list[list[float]]:
    """The design matrix under one of the two identifications of a complete dummy set.

    `all_dummies` gives every industry its own intercept and has no global one; `drop_first` keeps
    a global intercept and omits the first industry's column. They span the same column space, so
    the residuals must agree -- which is the claim
    `test_the_two_identifications_of_the_dummy_set_give_the_same_residuals` measures.
    """
    keys = sorted(set(groups))
    rows: list[list[float]] = []
    for group, value in zip(groups, regressor, strict=True):
        if mode == "all_dummies":
            row = [1.0 if key == group else 0.0 for key in keys]
        elif mode == "drop_first":
            row = [1.0, *(1.0 if key == group else 0.0 for key in keys[1:])]
        else:  # pragma: no cover - the parametrisation covers both declared modes
            raise ValueError(mode)
        row.append(value)
        rows.append(row)
    return rows


def _solve_normal_equations(design: list[list[float]], target: list[float]) -> list[float]:
    """`(X'X)^-1 X'y` by Gaussian elimination with partial pivoting, accumulated with `fsum`.

    The textbook route this repository's engine deliberately does not take. It is here so that
    "the closed form is the OLS residual" is a *comparison* rather than an assertion, and it is
    also the measurement behind the module docstring's conditioning note: the Gram matrix this
    forms has a diagonal spanning 149 to 2.05e17 on a level-capitalisation design.
    """
    width = len(design[0])
    gram = [
        [math.fsum(row[i] * row[j] for row in design) for j in range(width)] for i in range(width)
    ]
    for index in range(width):
        gram[index].append(
            math.fsum(row[index] * value for row, value in zip(design, target, strict=True))
        )
    for column in range(width):
        pivot = max(range(column, width), key=lambda candidate: abs(gram[candidate][column]))
        gram[column], gram[pivot] = gram[pivot], gram[column]
        head = gram[column][column]
        if head == 0.0:  # pragma: no cover - the probe designs are all full rank
            raise ZeroDivisionError(f"singular design at column {column}")
        for row_index in range(width):
            if row_index == column:
                continue
            factor = gram[row_index][column] / head
            for cell in range(column, width + 1):
                gram[row_index][cell] -= factor * gram[column][cell]
    return [gram[index][width] / gram[index][index] for index in range(width)]


def _dense_residuals(
    groups: list[str], regressor: list[float], values: list[float], *, mode: str
) -> list[float]:
    """`y - X b` for the dense design, which is what a residual is by definition."""
    rows = _design(groups, regressor, mode=mode)
    coefficients = _solve_normal_equations(rows, values)
    return [
        value - math.fsum(c * v for c, v in zip(coefficients, row, strict=True))
        for value, row in zip(values, rows, strict=True)
    ]


def _panel(
    seed: int, *, count: int = PANEL_SIZE, industries: int = INDUSTRY_COUNT, log_cap: bool = True
) -> tuple[list[str], list[str], list[float], list[float]]:
    """A whole-market-sized cross section: subjects, industries, regressor, factor values.

    The capitalisations are lognormal about `exp(13)` with a spread of 1.6 in log space, which is
    the shape `domain/daily_prices.py`'s `total_mv` has: four orders of magnitude inside one
    industry. That is exactly the shape that makes `market_cap_scale` a real choice.
    """
    rng = random.Random(seed)
    codes = [f"8010{index:02d}.SI" for index in range(industries)]
    subjects = [f"{index:06d}.SZ" for index in range(count)]
    groups = [rng.choice(codes) for _ in range(count)]
    caps = [math.exp(rng.gauss(13.0, 1.6)) for _ in range(count)]
    regressor = [math.log(cap) if log_cap else cap for cap in caps]
    values = [rng.gauss(0.0, 1.0) for _ in range(count)]
    return subjects, groups, regressor, values


def _max_gap(left: list[float], right: list[float]) -> float:
    return max(abs(a - b) for a, b in zip(left, right, strict=True))


# --- the closed form is the OLS residual ---------------------------------------------------------


@pytest.mark.parametrize("log_cap", [True, False])
def test_the_closed_form_reproduces_a_dense_least_squares_solve(log_cap: bool) -> None:
    """The claim the whole `O(n)` implementation rests on, measured on both regressor scalings.

    Both scalings are driven because the level one is where the textbook objection lives: its
    Gram matrix has a diagonal ratio of 1.37e15, within a factor of ten of double precision's own
    epsilon. Measured, the dense solve still agrees -- a dummy block is orthogonal by
    construction, so the effective conditioning is far better than the diagonal suggests. That is
    why `panel_neutralization`'s docstring claims the closed form for its cost and its absent
    matrix, and does *not* claim it rescued a numerical failure that was observed.
    """
    subjects, groups, regressor, values = _panel(7, log_cap=log_cap)

    fit = _neutralize(subjects, groups, regressor, values)
    assert fit is not None
    closed = [fit.residuals[subject] for subject in subjects]
    dense = _dense_residuals(groups, regressor, values, mode="all_dummies")

    assert _max_gap(closed, dense) < AGREEMENT_BOUND
    assert _population_stdev(closed) > 0.9


def test_the_two_identifications_of_the_dummy_set_give_the_same_residuals() -> None:
    """31 dummies with no intercept, against an intercept and 30 dummies.

    The rank question, answered as a measurement. The two designs differ in what their
    *coefficients* mean and not in their residuals, because they span the same column space -- so
    the choice `panel_neutralization` makes (every industry carries its own intercept, no global
    one) needs no arbitrary reference industry and changes no stored number.
    """
    subjects, groups, regressor, values = _panel(11)

    all_dummies = _dense_residuals(groups, regressor, values, mode="all_dummies")
    drop_first = _dense_residuals(groups, regressor, values, mode="drop_first")
    fit = _neutralize(subjects, groups, regressor, values)
    assert fit is not None

    assert _max_gap(all_dummies, drop_first) < AGREEMENT_BOUND
    assert _max_gap([fit.residuals[subject] for subject in subjects], drop_first) < AGREEMENT_BOUND


def test_rescaling_the_regressor_moves_no_residual_which_is_why_it_is_not_declarable() -> None:
    """The measurement that decides what `FactorNeutralizationSpec` does **not** declare.

    An affine map of the size regressor leaves the design's column space unchanged, because a
    complete dummy set already spans the constant. So "standardize the market cap first" is not a
    policy this contract could record -- it would be a field that reaches the identity and decides
    nothing, which is the defect `FactorTransformManifest` rejected `date_timezone` for.
    """
    subjects, groups, regressor, values = _panel(13)
    baseline = _neutralize(subjects, groups, regressor, values)
    assert baseline is not None

    mean = math.fsum(regressor) / len(regressor)
    deviation = _population_stdev(regressor)
    zscored = _neutralize(subjects, groups, [(v - mean) / deviation for v in regressor], values)
    shifted = _neutralize(subjects, groups, [1000.0 * v + 7.0 for v in regressor], values)
    assert zscored is not None
    assert shifted is not None

    reference = [baseline.residuals[subject] for subject in subjects]
    assert _max_gap([zscored.residuals[s] for s in subjects], reference) < AGREEMENT_BOUND
    assert _max_gap([shifted.residuals[s] for s in subjects], reference) < AGREEMENT_BOUND


def test_the_declared_choices_that_do_move_the_residuals_move_them_by_a_reportable_amount() -> None:
    """The other half, and it is what makes `market_cap_measure` and `market_cap_scale` fields.

    Against a residual set whose population deviation is about 1, swapping the *measure* moves a
    residual by ~0.02 and swapping the *scale* by ~0.2. Both are asserted with a floor rather than
    an exact figure, because the exact figure is a function of the seed and the floor is what
    falsifies "these are formalities".
    """
    subjects, groups, log_total, values = _panel(19)
    rng = random.Random(23)
    # circulating cap is a varying fraction of total, which is what makes the two measures differ
    log_circ = [value + math.log(min(1.0, max(0.05, rng.gauss(0.7, 0.25)))) for value in log_total]
    level_total = [math.exp(value) for value in log_total]

    by_total = _neutralize(subjects, groups, log_total, values)
    by_circ = _neutralize(subjects, groups, log_circ, values)
    by_level = _neutralize(subjects, groups, level_total, values)
    assert by_total is not None
    assert by_circ is not None
    assert by_level is not None

    reference = [by_total.residuals[subject] for subject in subjects]
    assert _population_stdev(reference) == pytest.approx(1.0, abs=0.05)
    assert _max_gap([by_circ.residuals[s] for s in subjects], reference) > 0.005
    assert _max_gap([by_level.residuals[s] for s in subjects], reference) > 0.05


def test_the_residual_is_orthogonal_to_both_halves_of_the_design() -> None:
    """The defining property, asserted as two inner products rather than as prose.

    Every industry's residuals sum to zero (the dummies are removed) and the residuals are
    uncorrelated with the within-industry demeaned regressor (the slope is removed). A build that
    demeaned only one of `y` and `x` passes neither.
    """
    subjects, groups, regressor, values = _panel(29)
    fit = _neutralize(subjects, groups, regressor, values)
    assert fit is not None

    grouped: dict[str, list[float]] = {}
    grouped_x: dict[str, list[float]] = {}
    for subject, group, cap in zip(subjects, groups, regressor, strict=True):
        grouped.setdefault(group, []).append(fit.residuals[subject])
        grouped_x.setdefault(group, []).append(cap)
    means = {key: math.fsum(items) / len(items) for key, items in grouped_x.items()}

    assert max(abs(math.fsum(items) / len(items)) for items in grouped.values()) < 1e-14
    covariance = math.fsum(
        (cap - means[group]) * fit.residuals[subject]
        for subject, group, cap in zip(subjects, groups, regressor, strict=True)
    )
    assert abs(covariance) < 1e-12


def test_a_one_member_industry_has_a_residual_of_exactly_zero_and_moves_no_other() -> None:
    """Both halves of the argument for `min_industry_members >= 2`, measured.

    The first half is why the floor exists: the lone member is its own group mean in `y` and in
    `x`, so its residual is `0.0` **exactly** -- not approximately, and not a function of the
    data -- and storing it under `neutralized` would put a structural constant into a column a
    report ranks on.

    The second half is why refusing it costs nothing: the singleton contributes `0 * 0` to the
    slope's numerator and `0` to its denominator, so the slope is **bit-identical** with and
    without it and every other residual is unchanged by exactly `0`. `==` rather than a tolerance,
    because that is the claim.
    """
    subjects, groups, regressor, values = _panel(31, count=400, industries=8)
    groups[0] = "999999.SI"

    with_lone = _neutralize(subjects, groups, regressor, values)
    assert with_lone is not None
    assert with_lone.residuals[subjects[0]] == 0.0

    kept = [index for index in range(len(subjects)) if groups[index] != "999999.SI"]
    without = _neutralize(
        [subjects[i] for i in kept],
        [groups[i] for i in kept],
        [regressor[i] for i in kept],
        [values[i] for i in kept],
    )
    assert without is not None

    assert without.slope == with_lone.slope
    assert without.dispersion == with_lone.dispersion
    assert all(without.residuals[subjects[i]] == with_lone.residuals[subjects[i]] for i in kept)


def test_a_regressor_with_no_within_industry_dispersion_is_a_degenerate_design() -> None:
    """`degenerate_design`'s own case, decided by the arithmetic rather than by a `min == max`.

    The two names have *different* capitalisations and the design is still degenerate, because
    each is alone in its group after demeaning -- which is the distinction a `min == max` test on
    the raw regressor cannot draw, and the reason `_neutralize` returns `None` rather than the
    caller inspecting the input.
    """
    assert _neutralize(["a", "b"], ["x", "y"], [1.0, 2.0], [0.5, -0.5]) is None
    assert _neutralize(["a", "b"], ["x", "x"], [1.0, 1.0], [0.5, -0.5]) is None
    assert _neutralize(["a", "b"], ["x", "x"], [1.0, 2.0], [0.5, -0.5]) is not None


def test_an_overflowing_design_is_degenerate_rather_than_infinite() -> None:
    """A dispersion that overflows to `inf` reports degeneracy instead of storing a non-finite.

    `_standardize_zscore`'s lesson on this plane: floating-point overflow is reachable on values
    that are very much not equal, and the honest answer is the code that says "there is nothing to
    say here" rather than a residual no declared coverage code carries.
    """
    huge = [1e308, -1e308, 1e308, -1e308]

    values = [1.0, 2.0, 3.0, 4.0]

    assert _neutralize(["a", "b", "c", "d"], ["x", "x", "y", "y"], huge, values) is None


def test_the_population_deviation_is_the_two_pass_one_and_refuses_an_empty_set() -> None:
    """A hand-computed vector, so the "obvious" `statistics.stdev` substitution fails here.

    `statistics.stdev` divides by `n - 1`: on this four-point set it returns 1.29099... against
    the population 1.11803..., which is 15% different. The cross section at one `as_of` **is** the
    population, so a sample correction would be compensating for a sampling that did not happen.
    """
    assert _population_stdev([1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.1180339887498949, rel=1e-15)
    assert _population_stdev([5.0]) == 0.0

    with pytest.raises(FactorEngineError, match="empty residual set"):
        _population_stdev([])


# --- the tables -----------------------------------------------------------------------------------


def test_the_shipped_tables_cover_their_vocabularies_exactly() -> None:
    """The audit's subject, asserted positively so the failure directions below are not vacuous."""
    assert {str(key) for key in _REGRESSORS} == MARKET_CAP_SCALES
    assert {str(key) for key in _MARKET_CAP_READERS} == MARKET_CAP_MEASURES
    assert set(NEUTRALIZED_COVERAGE_ORDER) == set(
        NEUTRALIZED_CENSUS_COLUMNS and NEUTRALIZED_COVERAGE_ORDER
    )
    assert _REGRESSORS["level"](2.5) == 2.5
    assert _REGRESSORS["log"](math.e) == pytest.approx(1.0, rel=1e-15)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {"regressors": {"log": _REGRESSORS["log"]}},
            "market cap scale vocabulary and its table disagree",
        ),
        (
            {"readers": {"total_mv": _MARKET_CAP_READERS["total_mv"]}},
            "market cap measure vocabulary and its reader table disagree",
        ),
        (
            {"participation": {"measured_only": frozenset({"processed"})}},
            "a rule with no entry admits nothing",
        ),
        (
            {"coverage_order": ("neutralized", "not_a_participant")},
            "the neutralised census order is",
        ),
        ({"levels": {"L1"}}, "a level with no column behind it"),
    ],
)
def test_the_table_drift_audit_fails_in_every_direction_it_claims_to(
    kwargs: dict[str, Any], message: str
) -> None:
    """Five failure directions, all drivable because every input is an argument.

    An audit whose only call site is the one that passes is an audit nobody has seen fail --
    `_refuse_table_drift`'s own lesson, which earned it a third test.
    """
    settings: dict[str, Any] = {
        "regressors": _REGRESSORS,
        "readers": _MARKET_CAP_READERS,
        "participation": PARTICIPATING_PROCESSED_CODES,
        "coverage_order": NEUTRALIZED_COVERAGE_ORDER,
        "levels": INDUSTRY_LEVELS,
        **kwargs,
    }

    with pytest.raises(FactorEngineError, match=message):
        _refuse_neutralization_table_drift(
            settings["regressors"],
            settings["readers"],
            settings["participation"],
            settings["coverage_order"],
            settings["levels"],
        )


def test_a_duplicated_census_code_is_refused_even_though_the_set_matches() -> None:
    """The half a set comparison misses: an order with a repeat and the right members."""
    with pytest.raises(FactorEngineError, match="two copies of a closed set drift"):
        _refuse_neutralization_table_drift(
            _REGRESSORS,
            _MARKET_CAP_READERS,
            PARTICIPATING_PROCESSED_CODES,
            (*NEUTRALIZED_COVERAGE_ORDER, "neutralized"),
            INDUSTRY_LEVELS,
        )


def test_the_drop_guard_is_the_same_object_the_factor_plane_uses() -> None:
    """Object identity, so a later rename cannot fork one refusal into two that drift.

    "A partition is replaced whole" is the same fact on the raw, processed and neutralised planes,
    and the unit has the same shape on all three: a manifest partition's subject is a build id. A
    second copy of the guard would differ only in the file it lives in, and the direction it would
    drift is that one plane stops protecting a partition.
    """
    assert _refuse_to_drop_a_stored_build is _factor_plane_drop_guard


# --- the datasets ---------------------------------------------------------------------------------


def test_the_longest_legal_factor_key_still_names_a_legal_neutralised_dataset() -> None:
    """The name-length budget, built out of the constants rather than restated.

    `14 + 40 + 5 = 59` against `MAX_IDENTIFIER_LENGTH`'s 63. Widening `MAX_FACTOR_KEY_LENGTH` or
    either prefix fails here instead of at the first write.
    """
    longest = "k" * MAX_FACTOR_KEY_LENGTH
    for prefix in (
        FACTOR_NEUTRALIZED_DATASET_PREFIX,
        FACTOR_NEUTRALIZATION_MANIFEST_DATASET_PREFIX,
    ):
        name = f"{prefix}{longest}_v999"
        assert len(name) <= MAX_IDENTIFIER_LENGTH
        validate_panel_identifier(name, role="dataset")

    assert len(FACTOR_NEUTRALIZATION_MANIFEST_DATASET_PREFIX) + MAX_FACTOR_KEY_LENGTH + 5 == 59


def test_a_factor_names_two_neutralised_datasets_that_are_not_the_processed_planes() -> None:
    definition = FACTOR_DEFINITIONS.definitions[0]

    assert neutralized_factor_dataset(definition).startswith("factor_neut_")
    assert factor_neutralization_manifest_dataset(definition).startswith("factor_neutmn_")
    assert neutralized_factor_dataset(definition) != factor_neutralization_manifest_dataset(
        definition
    )


def test_the_neutralised_planes_datasets_are_derived_and_therefore_have_no_cadence() -> None:
    """Against the live registry, so a factor added by `V2-P3-009`..`013` is covered.

    `DATASET_CADENCE` maps a *fetched* dataset to how often its upstream publishes. A derived
    dataset has no upstream, so `panel doctor` and `panel_gate` refuse to be asked about one.
    """
    for definition in FACTOR_DEFINITIONS.definitions:
        assert neutralized_factor_dataset(definition) not in DATASET_CADENCE
        assert factor_neutralization_manifest_dataset(definition) not in DATASET_CADENCE


def test_every_stored_column_has_a_declared_sql_kind_and_no_kind_is_orphaned() -> None:
    """Both directions: a column with no kind fails at DDL, a kind with no column rots."""
    assert set(_NEUTRALIZED_COLUMN_KINDS) == set(NEUTRALIZED_OBSERVATION_PANEL_COLUMNS) - {
        "subject"
    }
    assert set(_NEUTRALIZATION_MANIFEST_COLUMN_KINDS) == set(
        NEUTRALIZATION_MANIFEST_PANEL_COLUMNS
    ) - {"subject"}


def test_the_stored_head_columns_are_exactly_the_hashed_manifests_own_fields() -> None:
    """The audit handle `_NEUTRALIZATION_MANIFEST_HEAD_COLUMNS` exists for.

    Nothing in `src/` reads that slice -- the decoder addresses cells by name -- so its only
    consumer is this reconciliation. A thirteenth manifest field, or a hashed field that stopped
    being stored, fails here instead of at the first read-back.

    `as_of` is stored as `as_of_time`, which is `FACTOR_MANIFEST_DATA_COLUMNS`' own convention
    and `TRANSFORM_MANIFEST_DATA_COLUMNS`' after it: a bare `as_of` in a panel partition reads
    like one of the four reserved clock columns and is not one of them.
    """
    hashed = (set(FactorNeutralizationManifest.model_fields) - {"schema_version", "as_of"}) | {
        "as_of_time"
    }

    assert set(_NEUTRALIZATION_MANIFEST_HEAD_COLUMNS) == hashed
    assert len(_NEUTRALIZATION_MANIFEST_HEAD_COLUMNS) == 12
    assert NEUTRALIZATION_MANIFEST_DATA_COLUMNS[:12] == _NEUTRALIZATION_MANIFEST_HEAD_COLUMNS


def test_the_census_columns_are_derived_from_the_vocabulary_in_its_declared_order() -> None:
    assert (
        tuple(f"census_{code}" for code in NEUTRALIZED_COVERAGE_ORDER) == NEUTRALIZED_CENSUS_COLUMNS
    )
    assert set(NEUTRALIZED_CENSUS_COLUMNS) <= set(NEUTRALIZATION_MANIFEST_DATA_COLUMNS)


# --- the shipped neutralisation ------------------------------------------------------------------


def test_the_shipped_neutralisation_is_the_one_this_build_declares() -> None:
    """Every setting held to a literal, because each one changes every stored residual."""
    assert FACTOR_NEUTRALIZATIONS.qualified_keys == ("industry_and_size/v1",)
    assert INDUSTRY_AND_SIZE.industry_level == "L1"
    assert INDUSTRY_AND_SIZE.market_cap_measure == "total_mv"
    assert INDUSTRY_AND_SIZE.market_cap_scale == "log"
    assert INDUSTRY_AND_SIZE.participation == "measured_only"
    assert INDUSTRY_AND_SIZE.min_industry_members == 2
    assert INDUSTRY_AND_SIZE.min_cross_section == 100


def test_the_shipped_floor_matches_the_transform_that_has_to_run_first() -> None:
    """A neutralisation floor *below* the transform's would produce numbers on a cross section the
    transform itself declined to standardize -- and the eligible set here is strictly narrower,
    since a name needs a processed value and an industry and a capitalisation.
    """
    from openalpha_cn.panel_factors import CROSS_SECTION_STANDARD

    assert INDUSTRY_AND_SIZE.min_cross_section >= CROSS_SECTION_STANDARD.min_cross_section


def test_a_neutralisation_key_is_a_panel_identifier_and_fits_a_stored_column() -> None:
    spec: FactorNeutralizationSpec = INDUSTRY_AND_SIZE

    validate_panel_identifier(spec.key, role="neutralization key")
    assert spec.qualified_key == f"{spec.key}/v{spec.version}"


# --- the decoders ---------------------------------------------------------------------------------


DATASET: Final[str] = "factor_neut_probe_v1"
ROW_AS_OF: Final[datetime] = datetime(2026, 1, 16, 8, 30, tzinfo=UTC)


def test_a_row_of_the_wrong_width_is_refused_rather_than_unpacked_into_whatever_fits() -> None:
    """Both decoders, and the refusal names the columns it expected.

    Positional unpacking into a dataclass whose field types the checker believes are closed sets
    is how a partition written by a different build becomes an object nobody can distinguish from
    a sound one -- so the width is checked before anything is read.
    """
    with pytest.raises(FactorEngineError, match="values, expected"):
        _neutralized_observation_from_row(("too", "short"), dataset=DATASET)
    with pytest.raises(FactorEngineError, match="values, expected"):
        _neutralization_manifest_from_row(("too", "short"), dataset=DATASET)


def test_a_clock_cell_that_is_not_a_datetime_is_refused_by_both_decoders() -> None:
    """A stored instant that arrived as text is a partition this build cannot interpret.

    DuckDB hands a `TIMESTAMPTZ` back as a `datetime`, so this fires only on a partition whose
    column kind is not the one this module declares -- which is precisely the case where silently
    parsing the string would produce a row that looks sound.
    """
    observation = ("2026-01-16", *("x",) * len(NEUTRALIZED_OBSERVATION_PANEL_COLUMNS))
    manifest = ["x"] * len(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS)
    manifest[NEUTRALIZATION_MANIFEST_PANEL_COLUMNS.index("as_of_time")] = "2026-01-16"

    with pytest.raises(FactorEngineError, match="not a datetime"):
        _neutralized_observation_from_row(observation, dataset=DATASET)
    with pytest.raises(FactorEngineError, match="not a datetime"):
        _neutralization_manifest_from_row(tuple(manifest), dataset=DATASET)


def test_a_source_coverage_the_processed_vocabulary_does_not_declare_is_refused() -> None:
    """The neighbouring plane's vocabulary, decoded *from* its order rather than cast.

    Separate from `_neutralized_code` rather than parameterised, so the refusal can name **which**
    plane's vocabulary the stored code failed against -- which is the whole of what makes the
    message actionable to somebody holding a partition.
    """
    assert _processed_code("imputed", dataset=DATASET) == "imputed"

    with pytest.raises(FactorEngineError, match="the processed vocabulary does not declare"):
        _processed_code("computed", dataset=DATASET)


def test_a_manifest_row_whose_identity_does_not_reassemble_is_refused() -> None:
    """The decoder's self-check, driven without a store.

    Every field this reads is one the identity was computed from, so a decoder that dropped or
    mistyped one would hand back a build nobody ever ran, under the id a caller then uses in
    `supersedes`.
    """
    cells: dict[str, object] = dict.fromkeys(NEUTRALIZATION_MANIFEST_PANEL_COLUMNS, "x")
    cells["subject"] = "fnm_not_this_one"
    cells["neutralization_version"] = 1
    cells["source_factor_version"] = 1
    cells["as_of_time"] = ROW_AS_OF
    cells["code_commit"] = "a1b2c3d"
    row = tuple(cells[name] for name in NEUTRALIZATION_MANIFEST_PANEL_COLUMNS)

    with pytest.raises(FactorEngineError, match="reassembles to"):
        _neutralization_manifest_from_row(row, dataset=DATASET)
