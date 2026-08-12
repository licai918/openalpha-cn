"""The transform engine's rules and arithmetic that need no store (`V2-P3-003`).

Four groups, and each closes a hole this repository has fallen into before or a question the
issue's own brief says must have an answer rather than a "does not happen in practice".

**The arithmetic is asserted as numbers, not as shapes.** Task 37 is the counter-example on
file: a constant moved from 0.5 to 0.005 and twenty-one tests stayed green while a mutant came
back to life. So `_quantile`, `_population_stdev` and `_average_ranks` are driven with hand-
computed vectors, the z-score is asserted against the *population* deviation (a `statistics.stdev`
substitution is 22% different at n=3 and fails here), and the winsorization is asserted to move
the deviation the standardization then divides by.

**The two edges the brief names, each with a measured answer.** What is the 1% quantile of three
securities? -- a bound 2% of the way from the smallest value to the second smallest, which clips
a third of the cross section, which is why `min_cross_section` is declared and why the shipped
transform sets it to `1 / lower_quantile`. What happens at zero variance? -- `zscore` and `rank`
both answer `degenerate_cross_section`, `none` passes the values through, and the three are
asserted on one cross section so the difference is visible rather than described.

**Every declared vocabulary member has a branch, and the audit that says so fails.** The
`panel build` failure -- a table that gained a key with no branch and exited 0 with an empty
result -- has four vocabularies to happen in here, and `_refuse_transform_table_drift` is driven
in every direction including the one where it must pass.

**The decoders return from their vocabularies rather than casting.** A stored row written by a
build that knows a sixth coverage code, a non-finite value, or a manifest that no longer
reassembles to its own identity are each refused where the dataset can be named.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.factor import (
    MAX_FACTOR_KEY_LENGTH,
    FactorBuildManifest,
    FactorDefinition,
    FactorField,
    FactorInputRef,
    FactorObservation,
    set_digest,
)
from openalpha_cn.domain.factor_transform import (
    MISSING_VALUE_ACTIONS,
    MISSING_VALUE_COVERAGE_ORDER,
    PROCESSED_COVERAGE_CODES,
    PROCESSED_COVERAGE_ORDER,
    STANDARDIZATION_METHODS,
    STANDARDIZATION_NEUTRAL,
    WINSORIZATION_METHODS,
    FactorTransformError,
    FactorTransformManifest,
    FactorTransformSpec,
    MissingValueAction,
    MissingValuePolicy,
    WinsorizationPolicy,
)
from openalpha_cn.domain.panel_batch import MAX_IDENTIFIER_LENGTH, validate_panel_dataset
from openalpha_cn.panel_doctor import DATASET_CADENCE
from openalpha_cn.panel_factors import (
    _MISSING_VALUE_APPLIERS,
    _STANDARDIZERS,
    _TRANSFORM_MANIFEST_HEAD_COLUMNS,
    _WINSORIZERS,
    CROSS_SECTION_STANDARD,
    FACTOR_DEFINITIONS,
    FACTOR_PROCESSED_DATASET_PREFIX,
    FACTOR_TRANSFORM_MANIFEST_DATASET_PREFIX,
    FACTOR_TRANSFORMS,
    MISSING_VALUE_COLUMNS,
    PROCESSED_CENSUS_COLUMNS,
    PROCESSED_OBSERVATION_DATA_COLUMNS,
    PROCESSED_OBSERVATION_PANEL_COLUMNS,
    REFUSAL_ACTION,
    REVERSAL_1D,
    TRANSFORM_MANIFEST_DATA_COLUMNS,
    TRANSFORM_MANIFEST_PANEL_COLUMNS,
    FactorEngineError,
    FactorPanel,
    ProcessedFactorPanel,
    _average_ranks,
    _fill_with_the_neutral_value,
    _FillContext,
    _mad_bounds,
    _median,
    _population_stdev,
    _processed_coverage_code,
    _processed_observation_from_row,
    _quantile,
    _quantile_bounds,
    _refuse_transform_table_drift,
    _refuse_two_applications_of_one_transform_at_one_as_of,
    _stored_processed_value,
    _transform_manifest_from_row,
    apply_factor_transform,
    factor_transform_manifest_dataset,
    processed_factor_dataset,
    processed_observation_batch,
    transform_manifest_batch,
)

AS_OF: Final[datetime] = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)
BUILT_AT: Final[datetime] = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
COMMIT: Final[str] = "a1b2c3d"

PROCESSED: Final[str] = processed_factor_dataset(REVERSAL_1D)
TRANSFORM_MANIFESTS: Final[str] = factor_transform_manifest_dataset(REVERSAL_1D)


# --- the frame ------------------------------------------------------------------------------------


def _spec(**overrides: Any) -> FactorTransformSpec:
    """A probe spec whose every knob a test can move by naming only that knob."""
    settings: dict[str, Any] = {
        "key": "probe",
        "version": 1,
        "winsorization": WinsorizationPolicy(method="none"),
        "standardization": "zscore",
        "missing_values": MissingValuePolicy(
            not_in_universe="exclude",
            insufficient_history="exclude",
            input_missing="exclude",
            undefined_value="exclude",
        ),
        "min_cross_section": 1,
        **overrides,
    }
    return FactorTransformSpec(**settings)


def _policy(**overrides: str) -> MissingValuePolicy:
    settings: dict[str, Any] = {
        "not_in_universe": "exclude",
        "insufficient_history": "exclude",
        "input_missing": "exclude",
        "undefined_value": "exclude",
        **overrides,
    }
    return MissingValuePolicy(**settings)


def _panel(values: dict[str, float | None], codes: dict[str, str] | None = None) -> FactorPanel:
    """A hand-built `FactorPanel` over `{subject: value}`, so a cross section is one literal.

    Built here rather than through `compute_factor` because every test in this module is about
    what happens to a *cross section of numbers*, and driving each one through a store and a
    partition would put the arithmetic behind five layers that
    `tests/integration/panel/test_factor_transforms.py` already exercises end to end. The panel is
    still a real one: every observation satisfies `validate_factor_observation`, and the manifest
    is a real `FactorBuildManifest` whose `manifest_id` every row carries -- which
    `apply_factor_transform` checks.
    """
    marks = codes or {}
    subjects = tuple(values)
    manifest = FactorBuildManifest(
        factor_id=REVERSAL_1D.factor_id,
        factor_key=REVERSAL_1D.key,
        factor_version=REVERSAL_1D.version,
        as_of=AS_OF,
        date_timezone="Asia/Shanghai",
        code_commit=COMMIT,
        direction=REVERSAL_1D.direction,
        lookback_sessions=REVERSAL_1D.lookback_sessions,
        max_window_sessions=REVERSAL_1D.max_window_sessions,
        lookback_periods=None,
        max_window_periods=None,
        subject_count=len(subjects),
        subject_digest=set_digest(subjects),
        universe_count=len(subjects),
        universe_digest=set_digest(subjects),
        inputs=(
            FactorInputRef(
                dataset="daily",
                year=2026,
                partition_content_hash="bb",
                visible_row_count=len(subjects) * 2,
                withheld_row_count=0,
            ),
        ),
    )
    return FactorPanel(
        definition=REVERSAL_1D,
        manifest=manifest,
        observations=tuple(
            FactorObservation(
                subject=name,
                as_of=AS_OF,
                value=value,
                coverage=marks.get(name, "computed" if value is not None else "input_missing"),
                factor_id=REVERSAL_1D.factor_id,
                manifest_id=manifest.manifest_id,
                input_row_count=2 if value is not None else 1,
                input_session_first=None,
                input_session_last=None,
            )
            for name, value in values.items()
        ),
        built_at=BUILT_AT,
        input_provenance=(),
    )


def _cross_section(*values: float) -> dict[str, float | None]:
    return {f"{index + 1:06d}.SZ": value for index, value in enumerate(values)}


def _apply(panel: FactorPanel, spec: FactorTransformSpec) -> ProcessedFactorPanel:
    return apply_factor_transform(panel, spec, code_commit=COMMIT, built_at=BUILT_AT)


# --- the arithmetic, as numbers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ordered", "fraction", "expected"),
    [
        ((1.0, 2.0, 10.0), 0.0, 1.0),
        ((1.0, 2.0, 10.0), 1.0, 10.0),
        ((1.0, 2.0, 10.0), 0.5, 2.0),
        ((1.0, 2.0, 10.0), 0.25, 1.5),
        ((1.0, 2.0, 10.0), 0.75, 6.0),
        ((1.0, 2.0, 3.0, 4.0), 0.5, 2.5),
        ((5.0,), 0.01, 5.0),
        ((1.0, 2.0, 10.0), 0.01, 1.02),
        ((1.0, 2.0, 10.0), 0.99, 9.84),
    ],
)
def test_the_quantile_rule_is_linear_interpolation_between_order_statistics(
    ordered: tuple[float, ...], fraction: float, expected: float
) -> None:
    """The definition, pinned as numbers so a different rule cannot be substituted silently.

    `statistics.quantiles` uses an *exclusive* rule by default and would answer differently for
    every row here except the endpoints; a nearest-rank rule would answer `1.0` for the last two.
    The last two rows are also the small-cross-section measurement this issue's brief asks for and
    they are asserted exactly: the "1% quantile" of three securities is `1.02`, not the minimum.
    """
    assert _quantile(ordered, fraction) == pytest.approx(expected)


def test_the_quantile_refuses_an_unsorted_sequence_an_empty_one_and_a_bad_fraction() -> None:
    """The precondition is checked rather than documented: an unsorted argument returns a
    plausible number computed from the wrong order statistics, which never surfaces as an error.

    `_mad_bounds` is the caller that would have hit it -- `|x - median|` is V-shaped about the
    median, so an ascending series of values is a descending-then-ascending series of deviations.
    """
    with pytest.raises(FactorTransformError, match="ascending sequence"):
        _quantile((3.0, 1.0, 2.0), 0.5)
    with pytest.raises(FactorTransformError, match="empty cross section"):
        _quantile((), 0.5)
    with pytest.raises(FactorTransformError, match=r"in \[0, 1\]"):
        _quantile((1.0, 2.0), 1.5)


def test_the_median_is_the_quantile_rule_at_one_half_and_not_a_second_definition() -> None:
    assert _median((1.0, 2.0, 3.0, 4.0)) == pytest.approx(_quantile((1.0, 2.0, 3.0, 4.0), 0.5))
    assert _median((1.0, 2.0, 3.0, 4.0)) == pytest.approx(2.5)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((1.0, 3.0, 3.0, 7.0), (1.0, 2.5, 2.5, 4.0)),
        ((7.0, 3.0, 3.0, 1.0), (4.0, 2.5, 2.5, 1.0)),
        ((5.0, 5.0, 5.0), (2.0, 2.0, 2.0)),
        ((2.0,), (1.0,)),
    ],
)
def test_tied_values_share_the_average_of_the_ranks_they_span(
    values: tuple[float, ...], expected: tuple[float, ...]
) -> None:
    """Averaging is what keeps the rank sum at `n (n + 1) / 2` whatever the ties are, which is
    what makes `_standardize_rank`'s centring exact rather than approximately zero. A "minimum
    rank" or "first seen" tie rule would pass a sign-only assertion and fail here."""
    ranks = _average_ranks(values)

    assert ranks == pytest.approx(expected)
    assert sum(ranks) == pytest.approx(len(values) * (len(values) + 1) / 2)


def test_the_deviation_is_the_population_one_and_a_sample_estimator_would_fail_here() -> None:
    """The estimator is a decision and it is asserted as a number rather than as a name.

    For `[1, 2, 3]` the population deviation is `sqrt(2/3) = 0.8165` and the sample one is `1.0`
    -- 22% apart, and every z-score in the cross section moves by that factor. A cross section is
    the population at that `as_of` rather than a sample from one, so `ddof=1` would be correcting
    for a sampling that did not happen and is undefined at `n = 1`, which this contract admits.
    """
    assert _population_stdev((1.0, 2.0, 3.0)) == pytest.approx(0.816496580927726)
    assert _population_stdev((5.0,)) == 0.0
    with pytest.raises(FactorTransformError, match="empty cross section"):
        _population_stdev(())


def test_the_z_score_divides_by_the_population_deviation_this_function_returns() -> None:
    """`_standardize_zscore` computes the deviation inline (it needs the mean anyway), so the two
    would drift silently. This reconciles them on a cross section where the two estimators differ
    measurably."""
    standardized = _STANDARDIZERS["zscore"]((1.0, 2.0, 3.0))

    assert standardized is not None
    assert standardized.scale == pytest.approx(_population_stdev((1.0, 2.0, 3.0)))
    assert standardized.location == pytest.approx(2.0)
    assert standardized.values == pytest.approx((-1.224744871391589, 0.0, 1.224744871391589))


def test_the_centred_rank_has_mean_exactly_zero_at_every_size_including_one() -> None:
    """Which is why the denominator is `n` and not the `n - 1` that would make the range exactly
    `[-0.5, 0.5]`: a rule with a special case at `n = 1` is a branch nothing exercises until the
    day it does."""
    for values in ((7.0,), (1.0, 2.0), (1.0, 2.0, 3.0), (4.0, 1.0, 3.0, 2.0)):
        standardized = _STANDARDIZERS["rank"](values)
        if len(values) == 1:
            assert standardized is None
            continue
        assert standardized is not None
        assert sum(standardized.values) == pytest.approx(0.0)
        assert max(abs(value) for value in standardized.values) < 0.5
        assert standardized.location is None
        assert standardized.scale is None


# --- the two edges the brief names ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected_clipped", "expected_fraction"),
    [(3, 1, 1 / 3), (10, 1, 0.1), (50, 1, 0.02), (100, 1, 0.01), (200, 2, 0.01)],
)
def test_a_one_percent_winsorization_clips_a_third_of_a_three_name_cross_section(
    size: int, expected_clipped: int, expected_fraction: float
) -> None:
    """The measured answer to "what is the 1% quantile of three securities?".

    Under linear interpolation the `q`-quantile sits at position `(n - 1) * q` among the order
    statistics, so on an evenly spaced cross section the number of names strictly below it is
    `ceil((n - 1) q)` -- **and never fewer than one**. That floor of one name is what makes a
    small cross section misbehave: the *fraction* clipped is `max(1/n, ~q)`, so a policy that
    says 1% clips 33% of a three-name cross section, 10% of a ten-name one, 2% of a fifty-name
    one, and only at `n = 1/q = 100` does it first clip what it declares.

    This is the whole justification for `min_cross_section` being declared rather than assumed,
    and for `CROSS_SECTION_STANDARD` setting it to `1 / lower_quantile`. The counts are asserted
    as well as the fractions, because a fraction alone would be satisfied by a winsorizer that
    clipped nothing at `n = 100`.
    """
    spec = _spec(
        winsorization=WinsorizationPolicy(
            method="quantile", lower_quantile=0.01, upper_quantile=0.99
        ),
        standardization="none",
    )
    result = _apply(_panel(_cross_section(*(float(index) for index in range(size)))), spec)

    assert result.statistics.winsorized_low_count == expected_clipped
    assert result.statistics.winsorized_high_count == expected_clipped
    assert result.statistics.winsorized_low_count / size == pytest.approx(expected_fraction)
    assert result.statistics.winsorized_low_count / size >= 0.01


def test_the_shipped_transform_sets_its_floor_to_one_over_its_own_quantile() -> None:
    """The derivation above, pinned against the shipped spec so the two cannot drift.

    A `min_cross_section` chosen by taste would be a free parameter holding up the claim that a
    1% winsorization means anything, which is the failure mode Task 37 recorded.
    """
    winsorization = CROSS_SECTION_STANDARD.winsorization

    assert winsorization.lower_quantile is not None
    assert CROSS_SECTION_STANDARD.min_cross_section == round(1 / winsorization.lower_quantile)
    assert winsorization.upper_quantile == pytest.approx(1 - winsorization.lower_quantile)
    assert CROSS_SECTION_STANDARD.standardization == "zscore"
    assert CROSS_SECTION_STANDARD.missing_values.action_for("not_in_universe") == "exclude"
    assert (
        CROSS_SECTION_STANDARD.missing_values.action_for("input_missing")
        == "fill_cross_sectional_median"
    )


def test_a_cross_section_thinner_than_the_declared_floor_produces_no_values_at_all() -> None:
    """The boundary is asserted at exactly `min_cross_section` and one below it, so a floor that
    was off by one -- or ignored -- fails here rather than passing with plausible numbers."""
    spec = _spec(min_cross_section=4)

    thin = _apply(_panel(_cross_section(1.0, 2.0, 3.0)), spec)
    exact = _apply(_panel(_cross_section(1.0, 2.0, 3.0, 4.0)), spec)

    assert dict(thin.coverage_census())["insufficient_cross_section"] == 3
    assert thin.values() == {}
    assert thin.statistics.participant_count == 3
    assert thin.statistics.lower_bound is None
    assert dict(exact.coverage_census())["processed"] == 4
    assert len(exact.values()) == 4


def test_a_thin_cross_section_still_records_which_securities_were_in_it() -> None:
    """A whole-panel refusal must not lose the per-security reason: `source_coverage` is on every
    row, so a reader can still tell the name that was outside the universe from the one whose
    input was null even at an `as_of` that produced nothing."""
    spec = _spec(min_cross_section=10)
    panel = _panel(
        {"000001.SZ": 1.0, "000002.SZ": None, "000003.SZ": None},
        codes={"000003.SZ": "not_in_universe"},
    )

    result = _apply(panel, spec)
    by_subject = {item.subject: item for item in result.observations}

    assert {item.coverage for item in result.observations} == {"insufficient_cross_section"}
    assert by_subject["000001.SZ"].source_coverage == "computed"
    assert by_subject["000002.SZ"].source_coverage == "input_missing"
    assert by_subject["000003.SZ"].source_coverage == "not_in_universe"


@pytest.mark.parametrize(
    ("standardization", "expected"),
    [
        ("zscore", "degenerate_cross_section"),
        ("rank", "degenerate_cross_section"),
        ("none", "processed"),
    ],
)
def test_a_zero_dispersion_cross_section_answers_by_method_and_the_three_answers_differ(
    standardization: str, expected: str
) -> None:
    """The second edge the brief names, with all three answers on one cross section.

    `zscore` divides by zero and `rank` has nothing to order, so both say
    `degenerate_cross_section` -- the same fact under two arithmetics, and answering them
    differently would make the degeneracy depend on a knob about output *shape*. `none` declines
    to order at all, so passing five identical values through is a faithful answer rather than a
    `0 / 0`, and it is the reason the code is decided by the standardizer rather than by a
    `min == max` test in the engine.
    """
    result = _apply(_panel(_cross_section(*([4.0] * 5))), _spec(standardization=standardization))

    assert {item.coverage for item in result.observations} == {expected}
    if expected == "processed":
        assert set(result.values().values()) == {4.0}
        assert result.statistics.scale is None
    else:
        assert result.values() == {}
        assert result.statistics.location is None


def test_a_z_score_whose_variance_overflows_is_degenerate_rather_than_infinite() -> None:
    """The reason the standardizer decides degeneracy instead of the engine testing `min == max`.

    `[1e308, -1e308]` is about as far from a constant cross section as floats go, and its
    population variance overflows to `inf`. A `min == max` test would pass it through, the
    division would produce zeros, and the manifest would record a non-finite `scale` -- which
    `FactorTransformStatistics` refuses, so the build would die with a message about a statistics
    record rather than about the cross section.
    """
    result = _apply(_panel(_cross_section(1e308, -1e308)), _spec())

    assert {item.coverage for item in result.observations} == {"degenerate_cross_section"}


# --- the collapsed estimator ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "winsorization",
    [
        WinsorizationPolicy(method="mad", mad_scale=3.0),
        WinsorizationPolicy(method="quantile", lower_quantile=0.25, upper_quantile=0.75),
    ],
)
def test_bounds_that_collapse_onto_a_dispersed_cross_section_are_refused(
    winsorization: WinsorizationPolicy,
) -> None:
    """Both estimators collapse on the same shape, and applying either would be a destruction.

    `[1, 1, 1, 1, 9]` has a median absolute deviation of zero (four of five values are the
    median), so a `mad` interval is `[1, 1]` and every participant is clipped to `1` -- the 20%
    of the cross section carrying all of the information flattened onto the 80% carrying none.
    The quartile interval is `[1, 1]` for the same reason. That is not pulling in the tails, and
    the outcomes if it were allowed are both silent: `degenerate_cross_section` downstream, which
    reads as "the market had no dispersion today" and is false, or under `none` a column of
    identical numbers with no code at all.
    """
    with pytest.raises(FactorTransformError, match="would clip all 5 participants to one point"):
        _apply(_panel(_cross_section(1.0, 1.0, 1.0, 1.0, 9.0)), _spec(winsorization=winsorization))


def test_the_same_collapse_on_an_already_constant_cross_section_is_not_refused() -> None:
    """The second half of the predicate, and the reason it is `lower == upper` **and** the raw
    values had dispersion.

    Five identical values collapse any estimator and nothing was destroyed by doing so -- that is
    a fact about the market at this `as_of`, and `degenerate_cross_section` is the code for it.
    Refusing here would make an unrelated knob (which winsorization was declared) decide whether
    an all-tied cross section is an answer or an error.
    """
    result = _apply(
        _panel(_cross_section(*([4.0] * 5))),
        _spec(winsorization=WinsorizationPolicy(method="mad", mad_scale=3.0)),
    )

    assert {item.coverage for item in result.observations} == {"degenerate_cross_section"}
    assert result.statistics.lower_bound == pytest.approx(4.0)
    assert result.statistics.upper_bound == pytest.approx(4.0)


def test_the_mad_bounds_are_the_median_plus_and_minus_the_scaled_deviation() -> None:
    """The magnitude, so a `mad` rule that used the mean, the standard deviation or a different
    multiplier fails here rather than clipping something plausible.

    `[1, 2, 3, 4, 100]`: median 3, deviations `[2, 1, 0, 1, 97]` whose median is 1, so a
    `mad_scale` of 2 gives `[1, 5]`.
    """
    bounds = _mad_bounds(
        WinsorizationPolicy(method="mad", mad_scale=2.0), (1.0, 2.0, 3.0, 4.0, 100.0)
    )

    assert bounds is not None
    assert bounds == pytest.approx((1.0, 5.0))


# --- the order of operations ---------------------------------------------------------------------


def test_the_deviation_the_z_score_divides_by_is_the_winsorized_one() -> None:
    """Clipping before standardizing is the entire reason to clip, and reversing the two changes
    every number -- so the order is measured rather than asserted.

    `[1, 2, 3, 4, 100]` with a `[1, 5]` mad interval winsorizes to `[1, 2, 3, 4, 5]`: mean 3,
    population deviation `sqrt(2) = 1.414`. The raw cross section's deviation is 39.0, 27 times
    larger, so a transform that standardized first would put four of the five names within 0.6 of
    zero and the outlier at 2.0 -- which is the outlier setting the scale that was supposed to be
    protected from it.
    """
    spec = _spec(winsorization=WinsorizationPolicy(method="mad", mad_scale=2.0))

    result = _apply(_panel(_cross_section(1.0, 2.0, 3.0, 4.0, 100.0)), spec)

    assert result.statistics.location == pytest.approx(3.0)
    assert result.statistics.scale == pytest.approx(_population_stdev((1.0, 2.0, 3.0, 4.0, 5.0)))
    assert result.statistics.scale == pytest.approx(2**0.5)
    assert _population_stdev((1.0, 2.0, 3.0, 4.0, 100.0)) == pytest.approx(39.012818, rel=1e-6)
    assert result.values()["000005.SZ"] == pytest.approx(2 / 2**0.5)


def test_winsorizing_before_ranking_creates_ties_that_move_the_ranks() -> None:
    """Which is why `winsorization="none"` beside `rank` is a defensible pairing and **not** a
    no-op one: a rank is invariant to a monotone re-mapping and clipping is not monotone -- it
    collapses the tail onto one value, and tied values share an average rank.

    `[1, 2, 3, 4, 100]` ranks as `[1, 2, 3, 4, 5]` unclipped. Clipped to `[1, 5]` it becomes
    `[1, 2, 3, 4, 5]` in value terms with no tie -- so the demonstration needs two names in the
    tail: `[1, 2, 3, 90, 100]` clipped to `[1, 5]` gives `[1, 2, 3, 5, 5]`, and the top two tie.
    """
    unclipped = _apply(
        _panel(_cross_section(1.0, 2.0, 3.0, 90.0, 100.0)),
        _spec(standardization="rank"),
    )
    clipped = _apply(
        _panel(_cross_section(1.0, 2.0, 3.0, 90.0, 100.0)),
        _spec(
            standardization="rank",
            winsorization=WinsorizationPolicy(method="mad", mad_scale=2.0),
        ),
    )

    assert unclipped.values()["000004.SZ"] != clipped.values()["000004.SZ"]
    assert clipped.values()["000004.SZ"] == pytest.approx(clipped.values()["000005.SZ"])
    assert unclipped.values()["000004.SZ"] != pytest.approx(unclipped.values()["000005.SZ"])


# --- the missing-value policy, branch by branch --------------------------------------------------


def test_each_declared_action_produces_the_row_it_says_it_does() -> None:
    """One assertion per branch, including the two that carry a value.

    The two fills are asserted against *different* numbers on a skewed cross section, which is
    the sentinel: on a symmetric one the median of the z-scores is `0.0` and
    `fill_cross_sectional_median` would be indistinguishable from `fill_neutral`, so a mutant
    that implemented the first as a constant zero would survive.
    """
    values = _cross_section(1.0, 2.0, 3.0, 4.0, 40.0)
    values["999999.SZ"] = None
    panel = _panel(values)
    median_of_processed = _median(sorted(_apply(panel, _spec()).measured_values().values()))

    excluded = _apply(panel, _spec(missing_values=_policy(input_missing="exclude")))
    filled = _apply(
        panel, _spec(missing_values=_policy(input_missing="fill_cross_sectional_median"))
    )
    neutral = _apply(panel, _spec(missing_values=_policy(input_missing="fill_neutral")))

    assert excluded.values().get("999999.SZ") is None
    assert dict(excluded.coverage_census())["source_not_computed"] == 1
    assert filled.values()["999999.SZ"] == pytest.approx(median_of_processed)
    assert neutral.values()["999999.SZ"] == 0.0
    assert filled.values()["999999.SZ"] != pytest.approx(0.0)
    assert filled.imputed_subjects() == ("999999.SZ",)
    assert filled.statistics.imputed_count == 1
    with pytest.raises(FactorTransformError, match="input_missing: 1 security"):
        _apply(panel, _spec(missing_values=_policy(input_missing="refuse")))


def test_a_filled_observation_does_not_move_the_statistics_it_was_filled_from() -> None:
    """The load-bearing half of "only `computed` observations participate", measured.

    If a `fill_cross_sectional_median` imputation re-entered the mean and deviation it was drawn
    from, every processed value in the cross section would move with the *coverage rate* -- the
    same market on the same day would standardize differently because one security's filing was
    late. Byte-identical values on the computed names, and a `transform_manifest_id` that moves,
    because the source cross section genuinely is different and the identity must say so.
    """
    clean = _panel(_cross_section(1.0, 2.0, 3.0, 4.0, 40.0))
    with_holes = _panel(
        {**_cross_section(1.0, 2.0, 3.0, 4.0, 40.0), "900001.SZ": None, "900002.SZ": None}
    )
    spec = _spec(missing_values=_policy(input_missing="fill_cross_sectional_median"))

    first = _apply(clean, spec)
    second = _apply(with_holes, spec)

    assert second.measured_values() == first.measured_values()
    assert second.statistics.location == first.statistics.location
    assert second.statistics.scale == first.statistics.scale
    assert second.statistics.imputed_count == 2
    assert second.manifest.transform_manifest_id != first.manifest.transform_manifest_id


def test_a_refused_source_code_raises_even_when_the_cross_section_is_too_thin() -> None:
    """The ordering `REFUSAL_ACTION` documents, and it is a real difference rather than a detail.

    A caller who declared "an `undefined_value` in this cross section is a fault" gets that
    answer at an `as_of` whose cross section was also too thin to process, because the fault is
    in the inputs and the thinness is a separate fact about them. A per-observation check would
    have returned a panel of `insufficient_cross_section` and said nothing.
    """
    panel = _panel({"000001.SZ": 1.0, "000002.SZ": None}, codes={"000002.SZ": "undefined_value"})

    with pytest.raises(FactorTransformError, match="undefined_value: 1 security"):
        _apply(
            panel,
            _spec(min_cross_section=500, missing_values=_policy(undefined_value="refuse")),
        )


def test_the_two_whole_panel_codes_reach_every_row_including_the_uncomputed_ones() -> None:
    """ "There is no processed cross section at this `as_of`" is the dominant fact.

    Reporting `source_not_computed` for some names while others said `degenerate_cross_section`
    would suggest the first group could have been processed and merely lacked an input, which is
    false -- nothing was processed.
    """
    panel = _panel({"000001.SZ": 4.0, "000002.SZ": 4.0, "000003.SZ": None})

    result = _apply(panel, _spec())

    assert dict(result.coverage_census()) == {
        "processed": 0,
        "imputed": 0,
        "source_not_computed": 0,
        "insufficient_cross_section": 0,
        "degenerate_cross_section": 3,
    }


# --- the tables ----------------------------------------------------------------------------------


def test_the_shipped_tables_name_exactly_the_declared_vocabularies() -> None:
    assert {str(key) for key in _WINSORIZERS} == WINSORIZATION_METHODS
    assert {str(key) for key in _STANDARDIZERS} == STANDARDIZATION_METHODS
    assert {str(key) for key in STANDARDIZATION_NEUTRAL} == STANDARDIZATION_METHODS
    assert {str(key) for key in _MISSING_VALUE_APPLIERS} | {REFUSAL_ACTION} == (
        MISSING_VALUE_ACTIONS
    )
    assert REFUSAL_ACTION not in _MISSING_VALUE_APPLIERS


@pytest.mark.parametrize(
    ("removed", "expected"),
    [
        ("winsorizer", "winsorization method"),
        ("standardizer", "standardization method"),
        ("applier", "must be exactly one of the two"),
    ],
)
def test_the_drift_audit_refuses_a_vocabulary_member_with_no_branch(
    removed: str, expected: str
) -> None:
    """The `panel build` failure in its transform-layer form: a declared `WinsorizationMethod`
    with no winsorizer raises `KeyError` from a dict lookup at the first cross section that uses
    it, in production, with a message naming neither the method nor the spec."""
    winsorizers = dict(_WINSORIZERS)
    standardizers = dict(_STANDARDIZERS)
    appliers = dict(_MISSING_VALUE_APPLIERS)
    if removed == "winsorizer":
        del winsorizers["mad"]
    elif removed == "standardizer":
        del standardizers["rank"]
    else:
        del appliers["fill_neutral"]

    with pytest.raises(FactorTransformError, match=expected):
        _refuse_transform_table_drift(
            winsorizers, standardizers, appliers, PROCESSED_COVERAGE_ORDER
        )


def test_the_drift_audit_refuses_an_implementation_nothing_declares() -> None:
    """The other direction, which fails differently: a branch with no declared member is one no
    spec can ever reach, so it is dead code wearing a table entry's badge."""
    extra: dict[Any, Any] = {**_WINSORIZERS, "trimmed": _WINSORIZERS["none"]}

    with pytest.raises(FactorTransformError, match="implemented with nothing declaring them"):
        _refuse_transform_table_drift(
            extra, _STANDARDIZERS, _MISSING_VALUE_APPLIERS, PROCESSED_COVERAGE_ORDER
        )


def test_the_drift_audit_refuses_a_refusal_that_also_claims_to_impute() -> None:
    """An action in both halves is one applied twice -- and the entry it would need in the
    applier table cannot exist, because `refuse` produces no row."""
    appliers: dict[MissingValueAction, Any] = {
        **_MISSING_VALUE_APPLIERS,
        REFUSAL_ACTION: _MISSING_VALUE_APPLIERS["exclude"],
    }

    with pytest.raises(FactorTransformError, match="must be exactly one of the two"):
        _refuse_transform_table_drift(
            _WINSORIZERS, _STANDARDIZERS, appliers, PROCESSED_COVERAGE_ORDER
        )


@pytest.mark.parametrize(
    "order",
    [
        ("processed", "imputed", "source_not_computed", "insufficient_cross_section"),
        (*PROCESSED_COVERAGE_ORDER, "processed"),
    ],
)
def test_the_drift_audit_refuses_a_census_order_that_lost_or_repeated_a_code(
    order: tuple[str, ...],
) -> None:
    """The fifth check. `PROCESSED_CENSUS_COLUMNS` is derived from this tuple, so a code dropped
    from it is a stored column that silently stops existing and a code repeated in it is two
    columns with one name -- neither of which the vocabulary itself would notice."""
    with pytest.raises(FactorTransformError, match="the processed census order is"):
        _refuse_transform_table_drift(_WINSORIZERS, _STANDARDIZERS, _MISSING_VALUE_APPLIERS, order)


def test_the_drift_audit_passes_only_on_agreement() -> None:
    """The sentinel: if `_refuse_transform_table_drift` raised unconditionally, every test above
    would pass while proving nothing about agreement."""
    assert (
        _refuse_transform_table_drift(
            _WINSORIZERS, _STANDARDIZERS, _MISSING_VALUE_APPLIERS, PROCESSED_COVERAGE_ORDER
        )
        is None
    )


def test_the_shipped_registry_declares_one_transform_and_resolves_it_both_ways() -> None:
    assert FACTOR_TRANSFORMS.qualified_keys == ("cross_section_standard/v1",)
    assert FACTOR_TRANSFORMS.by_id(CROSS_SECTION_STANDARD.transform_id) is CROSS_SECTION_STANDARD


# --- the guards that only a hand-built panel can reach -------------------------------------------


def test_a_source_panel_whose_rows_belong_to_another_build_cannot_be_transformed() -> None:
    """What makes `(source_manifest_id, subject, as_of)` a *proved* key of the raw partition.

    `compute_factor` stamps one `manifest_id` on every observation it produces, so this never
    fires on its output -- but `FactorPanel` is a public frozen dataclass anybody can construct,
    and a hand-assembled one with a stray row would store a processed value whose provenance
    pointer names a build that does not hold it. A dangling reference written as a fact is worse
    than a missing one.
    """
    panel = _panel(_cross_section(1.0, 2.0, 3.0))
    stray = FactorObservation(
        subject="900001.SZ",
        as_of=AS_OF,
        value=4.0,
        coverage="computed",
        factor_id=REVERSAL_1D.factor_id,
        manifest_id="fmn_somebody_elses_build",
        input_row_count=2,
        input_session_first=None,
        input_session_last=None,
    )
    mixed = FactorPanel(
        definition=panel.definition,
        manifest=panel.manifest,
        observations=(*panel.observations, stray),
        built_at=BUILT_AT,
        input_provenance=(),
    )

    with pytest.raises(FactorTransformError, match="carry a build, a factor or an as_of"):
        _apply(mixed, _spec())


def test_a_panel_whose_manifest_describes_a_different_factor_cannot_be_transformed() -> None:
    other = FactorDefinition(
        key="other_probe",
        version=1,
        family="value",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=2,
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
    )
    panel = _panel(_cross_section(1.0, 2.0))
    mismatched = FactorPanel(
        definition=other,
        manifest=panel.manifest,
        observations=panel.observations,
        built_at=BUILT_AT,
        input_provenance=(),
    )

    with pytest.raises(FactorTransformError, match="its definition is"):
        _apply(mismatched, _spec())


def test_an_empty_source_panel_is_refused_rather_than_answered_emptily() -> None:
    panel = _panel(_cross_section(1.0))
    empty = FactorPanel(
        definition=panel.definition,
        manifest=panel.manifest,
        observations=(),
        built_at=BUILT_AT,
        input_provenance=(),
    )

    with pytest.raises(FactorTransformError, match="at least one observation"):
        _apply(empty, _spec())


def test_a_computed_observation_carrying_no_value_is_refused_at_the_transform() -> None:
    """`validate_factor_observation`'s rule has two call sites and a `__post_init__` is still
    overridable, which `domain/factor.py` measured: a three-line subclass put an empty subject
    and a backwards window into a Parquet partition. This is the transform's own boundary check
    on the same hole -- a `computed` row with no number would otherwise be silently dropped from
    the participants while still counting toward `min_cross_section`.
    """

    class _Unchecked(FactorObservation):
        def __post_init__(self) -> None:
            return None

    panel = _panel(_cross_section(1.0, 2.0))
    smuggled = _Unchecked(
        subject="900001.SZ",
        as_of=AS_OF,
        value=None,
        coverage="computed",
        factor_id=REVERSAL_1D.factor_id,
        manifest_id=panel.manifest.manifest_id,
        input_row_count=2,
        input_session_first=None,
        input_session_last=None,
    )
    mixed = FactorPanel(
        definition=panel.definition,
        manifest=panel.manifest,
        observations=(*panel.observations, smuggled),
        built_at=BUILT_AT,
        input_provenance=(),
    )

    with pytest.raises(FactorTransformError, match="observations carry no value"):
        _apply(mixed, _spec())


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (
            WinsorizationPolicy.model_construct(
                method="quantile", lower_quantile=None, upper_quantile=None, mad_scale=None
            ),
            "declares no quantiles",
        ),
        (
            WinsorizationPolicy.model_construct(
                method="mad", lower_quantile=None, upper_quantile=None, mad_scale=None
            ),
            "declares no mad_scale",
        ),
    ],
)
def test_a_policy_that_skipped_validation_is_refused_where_it_is_read(
    policy: WinsorizationPolicy, expected: str
) -> None:
    """`model_construct` skips every validator, which is exactly the hole `panel/catalog.py`
    argues about `__post_init__`: validation reachable only through one door is validation the
    other door turns off. The winsorizer therefore checks what the contract already refuses,
    where the message can say which door was used.
    """
    with pytest.raises(FactorTransformError, match=expected):
        (_quantile_bounds if policy.method == "quantile" else _mad_bounds)(policy, (1.0, 2.0))


def test_a_neutral_fill_with_no_neutral_point_is_refused_where_it_is_applied() -> None:
    """The same hole one contract over: `FactorTransformSpec`'s validator refuses `fill_neutral`
    beside `standardization="none"`, and the applier refuses it again where the message can say
    the spec came through `model_construct`."""
    with pytest.raises(FactorTransformError, match="no neutral point"):
        _fill_with_the_neutral_value(_FillContext(processed_median=0.5, neutral=None))


# --- the same door on the way out ----------------------------------------------------------------
#
# `ProcessedFactorPanel` is a public frozen dataclass for exactly the reason `FactorPanel` is, and
# the guards above were only on the way in. Everything below drives
# `_refuse_a_processed_panel_that_does_not_own_its_rows`, which is what closes the output side.


_OTHER_SPEC: Final[FactorTransformSpec] = _spec(
    key="probe_other",
    winsorization=WinsorizationPolicy(method="quantile", lower_quantile=0.05, upper_quantile=0.95),
    standardization="rank",
)


def _processed_panel(**overrides: Any) -> ProcessedFactorPanel:
    result = _apply(_panel(_cross_section(1.0, 2.0, 3.0)), _spec())
    return dataclasses.replace(result, **overrides)


@pytest.mark.parametrize("build", [processed_observation_batch, transform_manifest_batch])
def test_a_processed_panel_carrying_a_spec_that_did_not_produce_it_reaches_no_column(
    build: Callable[[ProcessedFactorPanel], object],
) -> None:
    """The defect this guard closes, at both of the boundaries that would have written it.

    `transform_manifest_batch` takes its ten head columns off `panel.manifest` and its nine
    policy columns off `panel.spec`, and nothing reconciled the two. Measured before the guard:
    `dataclasses.replace(result, spec=other)` stored one row whose `transform_id` and
    `transform_key` were the first transform's and whose `standardization_method` was `'rank'`
    and `winsorization_method` `'quantile'` -- neither of which produced the z-scores in the
    processed partition beside it. `_transform_manifest_from_row`'s identity self-check cannot
    see it, because it reassembles only the ten head columns and those agree with each other.

    That falsifies `TRANSFORM_MANIFEST_DATA_COLUMNS`' stated reason for storing the policy at all
    -- "a projection of `transform_id`" -- and it falsifies it in the direction that matters,
    since the columns exist because "a processed `value` column is *uninterpretable* without
    knowing whether it is a z-score, a centred rank or a raw winsorized number".
    """
    with pytest.raises(FactorEngineError, match="does not describe the transform and factor"):
        build(_processed_panel(spec=_OTHER_SPEC))


def test_a_processed_panel_filed_under_another_factors_definition_reaches_no_column() -> None:
    """The definition half of the same guard, and it is not decoration: the *dataset name* both
    batches are filed under comes off `panel.definition` while `source_factor_id` comes off the
    manifest, so a mismatched pair writes a row into one factor's partition claiming to be about
    another's -- `_refuse_a_source_panel_that_does_not_own_its_observations`' failure mode with
    the arrow reversed."""
    other = FactorDefinition(
        key="other_probe",
        version=1,
        family="value",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=2,
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
    )

    with pytest.raises(FactorEngineError, match="source_factor_id is"):
        transform_manifest_batch(_processed_panel(definition=other))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transform_id", "ftx_somebody_elses_transform"),
        ("transform_manifest_id", "ftm_somebody_elses_build"),
        ("source_manifest_id", "fmn_somebody_elses_build"),
    ],
)
def test_a_processed_row_pointing_at_another_build_reaches_no_column(
    field: str, value: str
) -> None:
    """One row at a time, because one row is all it takes.

    A row's `transform_manifest_id` is the pointer at the manifest row the *same call* writes,
    and `load_processed_factor_observations` filters on the row's own `transform_id` -- so a row
    carrying another transform's is a row the transform that stored it cannot read back, and a
    row carrying another build's `source_manifest_id` is the dangling pointer
    `_refuse_a_source_panel_that_does_not_own_its_observations` refuses on the way in.
    """
    result = _apply(_panel(_cross_section(1.0, 2.0, 3.0)), _spec())
    first, *rest = result.observations
    tampered = dataclasses.replace(
        result, observations=(dataclasses.replace(first, **{field: value}), *rest)
    )

    with pytest.raises(FactorEngineError, match="carry a transform, a build or an as_of"):
        processed_observation_batch(tampered)


def test_the_two_batch_builders_agree_that_a_well_formed_panel_is_well_formed() -> None:
    """The direction an audit is worthless without: `apply_factor_transform`'s own output passes
    both guards, so the refusals above are about hand-assembly rather than about the engine."""
    result = _apply(_panel(_cross_section(1.0, 2.0, 3.0)), _spec())

    assert processed_observation_batch(result).dataset == PROCESSED
    assert transform_manifest_batch(result).dataset == TRANSFORM_MANIFESTS


def test_two_panels_sharing_a_manifest_are_one_application_however_their_specs_are_labelled() -> (
    None
):
    """`_refuse_two_applications_of_one_transform_at_one_as_of` keyed on `panel.spec` and the
    rows it guards are filed under `panel.manifest`, so the two disagreed about what a duplicate
    is. Measured before the fix: `[result, replace(result, spec=other)]` was accepted, the
    manifest partition came back holding **two rows under one `transform_manifest_id`**, and an
    eight-name cross section read back as sixteen processed rows -- "a reader left to choose
    between them", which is the thing that function's own docstring says it exists to prevent.

    Keyed off the manifest now, and asserted here without a store because the key is a pure
    function of the panels.
    """
    result = _apply(_panel(_cross_section(1.0, 2.0, 3.0)), _spec())

    with pytest.raises(FactorEngineError, match="more than one application"):
        _refuse_two_applications_of_one_transform_at_one_as_of(
            [result, dataclasses.replace(result, spec=_OTHER_SPEC)]
        )


# --- the stored shape ----------------------------------------------------------------------------


def test_each_factor_is_filed_under_two_more_datasets_for_its_processed_values() -> None:
    assert processed_factor_dataset(REVERSAL_1D) == "factor_proc_reversal_1d_v1"
    assert factor_transform_manifest_dataset(REVERSAL_1D) == "factor_procmn_reversal_1d_v1"
    assert (
        len(
            {
                processed_factor_dataset(REVERSAL_1D),
                factor_transform_manifest_dataset(REVERSAL_1D),
            }
        )
        == 2
    )


def test_the_longest_legal_factor_key_still_names_a_legal_processed_dataset() -> None:
    """The dataset-name budget, built as the actual worst case rather than restated as a number.

    It is also the arithmetic that forces the transform to be a **column** rather than a
    partition axis: the shortest honest prefix plus the longest factor key plus two `_v999`
    suffixes and a separator leaves six characters for a transform key.
    """
    longest = FactorDefinition(
        key="k" * MAX_FACTOR_KEY_LENGTH,
        version=999,
        family="value",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=2,
        max_window_sessions=2,
        lookback_periods=None,
        max_window_periods=None,
    )

    for name in (processed_factor_dataset(longest), factor_transform_manifest_dataset(longest)):
        validate_panel_dataset(name)
        assert len(name) <= MAX_IDENTIFIER_LENGTH

    widest = max(FACTOR_PROCESSED_DATASET_PREFIX, FACTOR_TRANSFORM_MANIFEST_DATASET_PREFIX, key=len)
    assert len(widest) + MAX_FACTOR_KEY_LENGTH + len("_v999") <= MAX_IDENTIFIER_LENGTH
    assert len("fproc_") + MAX_FACTOR_KEY_LENGTH + 2 * len("_v999") + 1 > MAX_IDENTIFIER_LENGTH - 7


def test_the_processed_planes_datasets_are_derived_and_therefore_have_no_cadence() -> None:
    """`DATASET_CADENCE` says how often an *upstream* publishes, and a processed partition has no
    upstream at all -- it is derived from a partition that is itself derived. Read off the live
    registry, so a factor `V2-P3-009`..`013` adds is covered without anybody extending a list."""
    derived = {
        name
        for definition in FACTOR_DEFINITIONS.definitions
        for name in (
            processed_factor_dataset(definition),
            factor_transform_manifest_dataset(definition),
        )
    }

    assert derived
    assert not (derived & set(DATASET_CADENCE))


def test_the_stored_processed_columns_carry_the_provenance_pointer_and_the_two_codes() -> None:
    """D8's "由哪个原值 + 哪个变换版本得到" as a column list: the transform's identity, the source
    build's identity, and the raw row's own coverage code so the four reasons do not collapse."""
    assert PROCESSED_OBSERVATION_PANEL_COLUMNS[0] == "subject"
    assert set(PROCESSED_OBSERVATION_DATA_COLUMNS) == {
        "transform_id",
        "transform_key",
        "transform_version",
        "value",
        "coverage",
        "transform_manifest_id",
        "source_factor_id",
        "source_manifest_id",
        "source_coverage",
    }


def test_the_stored_census_is_one_column_per_declared_processed_coverage_code() -> None:
    """Asserted in both directions, so a column that stopped being derived from the vocabulary is
    caught rather than a set that merely overlaps it."""
    assert tuple(f"census_{code}" for code in PROCESSED_COVERAGE_ORDER) == PROCESSED_CENSUS_COLUMNS
    assert set(PROCESSED_CENSUS_COLUMNS) <= set(TRANSFORM_MANIFEST_DATA_COLUMNS)
    assert len(PROCESSED_CENSUS_COLUMNS) == len(PROCESSED_COVERAGE_CODES)


def test_the_stored_policy_columns_are_one_per_non_computed_coverage_code() -> None:
    """A sixth `FactorCoverage` member brings a policy field (the domain's import audit) and a
    stored column (this), so the declared policy and what is recorded of it cannot drift."""
    assert tuple(f"missing_{code}" for code in MISSING_VALUE_COVERAGE_ORDER) == (
        MISSING_VALUE_COLUMNS
    )
    assert set(MISSING_VALUE_COLUMNS) <= set(TRANSFORM_MANIFEST_DATA_COLUMNS)
    assert {
        "winsorization_method",
        "winsorization_lower_quantile",
        "winsorization_upper_quantile",
        "winsorization_mad_scale",
        "standardization_method",
        "min_cross_section",
    } <= set(TRANSFORM_MANIFEST_DATA_COLUMNS)


def test_the_stored_statistics_columns_make_a_declared_winsorization_falsifiable() -> None:
    """`coverage_census()` existed and spoke only to a caller that asked, and a build in which
    nothing was computed reached Parquet looking exactly like one that scored the whole market.
    These are the same instrument pointed at the winsorization: a 1% policy that clipped one name
    out of three clipped a third of the cross section, and only a count says so."""
    assert {
        "participant_count",
        "winsorized_low_count",
        "winsorized_high_count",
        "imputed_count",
        "lower_bound",
        "upper_bound",
        "location",
        "scale",
    } <= set(TRANSFORM_MANIFEST_DATA_COLUMNS)


def test_the_head_column_slice_is_an_audit_handle_and_nothing_in_src_reads_it() -> None:
    """`_TRANSFORM_MANIFEST_HEAD_COLUMNS` describes the stored layout; it does not enforce it.

    Its docstring used to read as though the column *order* were load-bearing -- "the ten columns
    `FactorTransformManifest` is reassembled from". It is not: `_transform_manifest_from_row`
    zips `TRANSFORM_MANIFEST_PANEL_COLUMNS` against the row and addresses every cell by name, so
    a hashed field moved down the tuple would change nothing at run time. The constant's only
    consumer is the test below, and this asserts that -- an `ast.Load` of the name anywhere in
    `src/` would mean the correction has gone stale and the constant has acquired a run-time job
    that needs its own argument.
    """
    source_root = Path(__file__).resolve().parents[2] / "src" / "openalpha_cn"
    name = "_TRANSFORM_MANIFEST_HEAD_COLUMNS"
    mentions = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*.py")
        if name in path.read_text(encoding="utf-8")
    }
    read_sites = [
        node
        for node in ast.walk(
            ast.parse((source_root / "panel_factors.py").read_text(encoding="utf-8"))
        )
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
    ]

    assert mentions == {"panel_factors.py"}
    assert read_sites == []


def test_the_stored_head_columns_are_exactly_the_hashed_manifests_own_fields() -> None:
    """The slice length is the only number in `_TRANSFORM_MANIFEST_HEAD_COLUMNS`, and a number
    somebody has to remember is one that goes stale. An eleventh manifest field fails here.

    One rename is spelt out rather than tolerated: the `as_of` field is stored as `as_of_time`,
    which is `FACTOR_MANIFEST_DATA_COLUMNS`' own convention -- `as_of` alone in a panel partition
    reads like one of the four reserved clock columns and is not one of them.
    """
    assert set(_TRANSFORM_MANIFEST_HEAD_COLUMNS) == (
        set(FactorTransformManifest.model_fields) - {"schema_version", "as_of"}
    ) | {"as_of_time"}
    assert "as_of_time" in TRANSFORM_MANIFEST_DATA_COLUMNS
    assert TRANSFORM_MANIFEST_PANEL_COLUMNS[0] == "subject"


# --- the decoders --------------------------------------------------------------------------------


def _processed_row(**overrides: object) -> tuple[object, ...]:
    cells: dict[str, object] = {
        "event_time": AS_OF,
        "subject": "000001.SZ",
        "transform_id": "ftx_probe",
        "transform_key": "probe",
        "transform_version": 1,
        "value": 1.25,
        "coverage": "processed",
        "transform_manifest_id": "ftm_probe",
        "source_factor_id": "fct_probe",
        "source_manifest_id": "fmn_probe",
        "source_coverage": "computed",
        **overrides,
    }
    return tuple(cells.values())


def test_a_stored_processed_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(FactorEngineError, match="expected 11"):
        _processed_observation_from_row(("too", "few"), dataset=PROCESSED)


def test_a_well_formed_stored_processed_row_decodes_into_every_field_it_came_from() -> None:
    """The positive half, without which the refusals below would be satisfied by a decoder that
    rejected everything."""
    observation = _processed_observation_from_row(_processed_row(), dataset=PROCESSED)

    assert observation.subject == "000001.SZ"
    assert observation.as_of == AS_OF
    assert observation.value == pytest.approx(1.25)
    assert observation.coverage == "processed"
    assert observation.transform_id == "ftx_probe"
    assert observation.transform_manifest_id == "ftm_probe"
    assert observation.source_factor_id == "fct_probe"
    assert observation.source_manifest_id == "fmn_probe"
    assert observation.source_coverage == "computed"


def test_a_stored_processed_coverage_code_this_build_does_not_declare_is_refused() -> None:
    with pytest.raises(FactorEngineError, match="this build does not declare"):
        _processed_observation_from_row(
            _processed_row(value=None, coverage="probably_fine"), dataset=PROCESSED
        )
    assert _processed_coverage_code("imputed", dataset=PROCESSED) == "imputed"


def test_a_stored_source_coverage_code_this_build_does_not_declare_is_refused() -> None:
    """Both coverage columns are decoded *from* their vocabularies, not cast -- a partition
    written by a build that knew a sixth raw code would otherwise decode into a dataclass whose
    `source_coverage` the type system believes is one of five and is not."""
    with pytest.raises(Exception, match=r"does not declare|not a declared"):
        _processed_observation_from_row(
            _processed_row(coverage="imputed", source_coverage="probably_fine"), dataset=PROCESSED
        )


@pytest.mark.parametrize("stored", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_a_stored_processed_value_that_is_not_finite_is_refused(stored: object) -> None:
    """`float(str(cell))` parses `'nan'` and `'inf'` without complaint, and no declared processed
    coverage code carries one -- so such a row is one nothing describes. Driven from both a text
    cell and a float cell because the decoder stringifies before parsing, and a guard on only one
    of the two would look like it worked."""
    with pytest.raises(FactorEngineError, match="not a finite number"):
        _stored_processed_value(stored, dataset=PROCESSED)


def test_a_stored_processed_row_whose_event_clock_is_not_an_instant_is_refused() -> None:
    with pytest.raises(FactorEngineError, match="not a datetime"):
        _processed_observation_from_row(_processed_row(event_time="2026-01-12"), dataset=PROCESSED)


def _manifest_row(**overrides: object) -> tuple[object, ...]:
    manifest = FactorTransformManifest(
        transform_id="ftx_probe",
        transform_key="probe",
        transform_version=1,
        source_factor_id="fct_probe",
        source_factor_key="reversal_1d",
        source_factor_version=1,
        source_manifest_id="fmn_probe",
        source_observation_digest="obs_probe",
        as_of=AS_OF,
        code_commit=COMMIT,
    )
    cells: dict[str, object] = {
        "subject": manifest.transform_manifest_id,
        "transform_id": manifest.transform_id,
        "transform_key": manifest.transform_key,
        "transform_version": manifest.transform_version,
        "source_factor_id": manifest.source_factor_id,
        "source_factor_key": manifest.source_factor_key,
        "source_factor_version": manifest.source_factor_version,
        "source_manifest_id": manifest.source_manifest_id,
        "source_observation_digest": manifest.source_observation_digest,
        "as_of_time": manifest.as_of,
        "code_commit": manifest.code_commit,
        "winsorization_method": "none",
        "winsorization_lower_quantile": None,
        "winsorization_upper_quantile": None,
        "winsorization_mad_scale": None,
        "standardization_method": "zscore",
        "min_cross_section": 1,
        **dict.fromkeys(MISSING_VALUE_COLUMNS, "exclude"),
        **dict.fromkeys(PROCESSED_CENSUS_COLUMNS, 0),
        "participant_count": 3,
        "winsorized_low_count": 0,
        "winsorized_high_count": 0,
        "imputed_count": 0,
        "lower_bound": None,
        "upper_bound": None,
        "location": 2.0,
        "scale": 0.8,
        **overrides,
    }
    assert tuple(cells) == TRANSFORM_MANIFEST_PANEL_COLUMNS
    return tuple(cells.values())


def test_a_stored_transform_manifest_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(FactorEngineError, match="expected 34"):
        _transform_manifest_from_row(("too", "few"), dataset=TRANSFORM_MANIFESTS)


def test_a_stored_transform_manifest_reassembles_to_the_identity_it_was_filed_under() -> None:
    manifest = _transform_manifest_from_row(_manifest_row(), dataset=TRANSFORM_MANIFESTS)

    assert manifest.transform_id == "ftx_probe"
    assert manifest.source_manifest_id == "fmn_probe"
    assert manifest.as_of == AS_OF


def test_a_stored_transform_manifest_that_does_not_reassemble_to_its_own_identity_is_refused() -> (
    None
):
    """The only thing that makes `load_factor_transform_manifests`' output trustworthy: every
    field it reads is one the identity was computed from, so a decoder that dropped or mistyped
    one would hand back a build nobody ever ran -- under the ID a caller then names in
    `supersedes`."""
    with pytest.raises(FactorEngineError, match="reassembles to"):
        _transform_manifest_from_row(
            _manifest_row(subject="ftm_not_this_one"), dataset=TRANSFORM_MANIFESTS
        )


def test_a_stored_transform_manifest_whose_as_of_is_not_an_instant_is_refused() -> None:
    with pytest.raises(FactorEngineError, match="not a datetime"):
        _transform_manifest_from_row(
            _manifest_row(as_of_time="2026-01-12"), dataset=TRANSFORM_MANIFESTS
        )
