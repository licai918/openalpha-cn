"""What Benjamini-Hochberg may claim here, and the number without which it claims nothing.

`V2-P5-007` is two halves and the second is the one that gets forgotten. The first is the
procedure: sort the p-values, compare each against `rank * rate / m`, take the largest rank that
passes and reject every rank at or below it. The second is `m` -- **how many hypotheses were
tested** -- and a q-value published without it is not reproducible, because the same p-values
under two different family sizes are two different answers.

## The control is closed-form and every arm is dyadic

`V2-P4-022`'s lesson and `V2-P5-005`'s application of it. Every p-value, every critical value and
every q-value below is a dyadic rational, so each is exact to the last bit and asserted with `==`
rather than `pytest.approx`. The arms are chosen so that each one separates a specific wrong
implementation from the right one:

- **`FAMILY_*`** -- the same two p-values under `m = 2` and under `m = 8`. Two rejections and
  q-values `(0.125, 0.375)` against one rejection and `(0.5, 1.0)`. An implementation that
  recovers the family size by counting the rows it was handed reads `m = 2` in both arms and
  fails the second. This is the arm the whole "record how many hypotheses were tested" half of
  the row lives on.
- **`STEP_UP_*`** -- three p-values of which only the *third* clears its own critical value.
  BH is a step-up procedure: `k* = 3` and all three are rejected. An implementation that tests
  each rank against its own threshold and rejects only where it passes returns one rejection and
  fails here.
- **`BOUNDARY_*`** -- four p-values each exactly equal to its own critical value. BH's comparison
  is `<=`, so all four are rejected; an implementation written with `<` rejects none. The
  equalities are exact because `rank * 0.5 / 8` is `rank / 16` for every rank.
- **`DEPENDENCE_*`** -- one family, one rate, two declared dependence assumptions. Under
  independence both are rejected; under arbitrary dependence the harmonic penalty `H_2 = 1.5`
  halves the critical values and only the first is. The assumption is a caller's declaration that
  changes the answer, not a label attached to it.

One arm separates nothing on its own. `FAMILY_*` alone passes for an implementation that never
steps up; `STEP_UP_*` alone passes for one that ignores the declared family size, because there
the reported rows *are* the family.
"""

import math
from typing import Final

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.multiple_testing import (
    KNOWN_MULTIPLE_TESTING_LIMITATIONS,
    HypothesisTest,
    MultipleTestingRequest,
    control_false_discovery_rate,
    harmonic_number,
)

RATE: Final[float] = 0.5
"""`2**-1`. Absurd as a false-discovery rate and chosen for arithmetic: `rank * 0.5 / 8` is
`rank / 16`, so every critical value in the boundary arm is exact and a p-value can be placed
*on* one rather than near it."""

FAMILY_P_VALUES: Final[tuple[float, float]] = (0.0625, 0.375)
"""`2**-4` and `3 * 2**-3`."""

FAMILY_OF_TWO_Q_VALUES: Final[tuple[float, float]] = (0.125, 0.375)
"""`2 * 0.0625 / 1` and `2 * 0.375 / 2`, both exact, neither clamped."""

FAMILY_OF_EIGHT_Q_VALUES: Final[tuple[float, float]] = (0.5, 1.0)
"""`8 * 0.0625 / 1` and `8 * 0.375 / 2 = 1.5`, the second clamped at 1."""

STEP_UP_P_VALUES: Final[tuple[float, float, float]] = (0.15625, 0.171875, 0.1875)
"""`5 * 2**-5`, `11 * 2**-6`, `3 * 2**-4`. Against critical values `1/16, 2/16, 3/16` only the
third clears its own, and BH rejects all three."""

BOUNDARY_P_VALUES: Final[tuple[float, float, float, float]] = (0.0625, 0.125, 0.1875, 0.25)
"""`rank / 16` for ranks one through four: each p-value **is** its own critical value."""

DEPENDENCE_P_VALUES: Final[tuple[float, float]] = (0.0625, 0.625)
"""`2**-4` and `5 * 2**-3`, under `rate = 0.75` and `m = 2`."""

DEPENDENT_RATE: Final[float] = 0.75
"""`3 * 2**-2`. Chosen so `H_2 = 1.5` divides it exactly: `0.75 / 1.5` is `0.5`."""

HARMONIC_OF_TWO: Final[float] = 1.5
"""`1/1 + 1/2`, exact in binary, which is why the arbitrary-dependence arm asserts with `==`."""


def _tests(p_values: tuple[float, ...]) -> tuple[HypothesisTest, ...]:
    """One hypothesis per p-value, named `h1..hn` in the order given."""
    return tuple(
        HypothesisTest(
            hypothesis_id=f"h{index}",
            p_value=p_value,
            test="closed-form fixture",
        )
        for index, p_value in enumerate(p_values, start=1)
    )


def _request(
    p_values: tuple[float, ...],
    *,
    family_size: int,
    false_discovery_rate: float = RATE,
    dependence: str = "independent-or-positively-dependent",
) -> MultipleTestingRequest:
    return MultipleTestingRequest(
        tests=_tests(p_values),
        family_size=family_size,
        false_discovery_rate=false_discovery_rate,
        dependence=dependence,  # type: ignore[arg-type]
    )


# --- the declared family size is what the answer turns on ------------------------------------


def test_two_p_values_under_a_family_of_two_reject_both_with_exact_q_values() -> None:
    """The complete-family arm, and the reading a row count would also have produced."""
    report = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=2))

    assert report.family_size == 2
    assert report.reported_hypotheses == 2
    assert report.withheld_hypotheses == 0
    assert report.family_is_complete is True
    assert tuple(verdict.q_value for verdict in report.verdicts) == FAMILY_OF_TWO_Q_VALUES
    assert tuple(verdict.rejected for verdict in report.verdicts) == (True, True)
    assert report.discoveries == 2
    assert report.largest_rejected_rank == 2


def test_the_same_two_p_values_under_a_family_of_eight_reject_only_one() -> None:
    """The arm no row count can reach: four of the eight hypotheses are not in the report.

    Same p-values, same rate, same order. The only thing that moved is the number the caller
    declared it tested, and both the q-values and the rejection set move with it. An
    implementation that derives `m` from `len(tests)` returns this file's previous arm here.
    """
    report = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=8))

    assert report.family_size == 8
    assert report.reported_hypotheses == 2
    assert report.withheld_hypotheses == 6
    assert report.family_is_complete is False
    assert tuple(verdict.q_value for verdict in report.verdicts) == FAMILY_OF_EIGHT_Q_VALUES
    assert tuple(verdict.rejected for verdict in report.verdicts) == (True, False)
    assert report.discoveries == 1
    assert report.largest_rejected_rank == 1


def test_the_declared_family_size_survives_serialization_rather_than_being_recomputed() -> None:
    """A report read back from its own JSON still says eight, with two rows in it.

    This is the row's second half stated as a property: `family_size` is a **stored** field, so
    a reader who parses the document gets the number the search actually ran, and a reader who
    counts `verdicts` gets `2`. The two are different numbers on purpose and the document holds
    the one the q-values were computed against.
    """
    report = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=8))

    restored = type(report).model_validate_json(report.model_dump_json())

    assert restored.family_size == 8
    assert len(restored.verdicts) == 2
    assert restored.family_size != len(restored.verdicts)
    assert tuple(verdict.q_value for verdict in restored.verdicts) == FAMILY_OF_EIGHT_Q_VALUES


def test_a_family_smaller_than_the_rows_it_carries_is_refused_naming_both_numbers() -> None:
    """The only direction that is checkable, and it is checked.

    A caller who tested forty and declares five gets an anti-conservative answer nothing here
    can detect -- `the_family_size_is_declared_and_no_check_can_confirm_it`. A caller who
    declares fewer than the rows they handed over is stating an impossibility, and that is
    refused rather than silently raised to the row count. It is refused on the *contract*, so a
    stored request read back off disk is refused too.
    """
    with pytest.raises(ValidationError) as refusal:
        _request(FAMILY_P_VALUES, family_size=1)

    assert "family_size 1" in str(refusal.value)
    assert "2 hypotheses" in str(refusal.value)


# --- the procedure is a step-up, and its comparison is inclusive -------------------------------


def test_bh_rejects_every_rank_below_the_largest_that_clears_its_own_critical_value() -> None:
    """Only the third p-value clears its own threshold; all three are rejected.

    This is what makes BH a *step-up* procedure rather than three independent comparisons, and
    it is the arm an implementation that rejects `p_i <= critical_i` row by row fails: it
    returns one discovery where the procedure gives three.
    """
    report = control_false_discovery_rate(_request(STEP_UP_P_VALUES, family_size=8))

    criticals = tuple(verdict.critical_value for verdict in report.verdicts)
    assert criticals == (0.0625, 0.125, 0.1875)
    assert tuple(verdict.p_value <= verdict.critical_value for verdict in report.verdicts) == (
        False,
        False,
        True,
    )
    assert tuple(verdict.rejected for verdict in report.verdicts) == (True, True, True)
    assert report.largest_rejected_rank == 3
    assert report.discoveries == 3


def test_a_p_value_exactly_on_its_critical_value_is_rejected_rather_than_missed() -> None:
    """Four p-values, four critical values, four exact equalities, four rejections.

    `rank * 0.5 / 8` is `rank / 16` and every p-value here is written as that, so this is a
    genuine boundary rather than one approached. An implementation using `<` rejects nothing.
    """
    report = control_false_discovery_rate(_request(BOUNDARY_P_VALUES, family_size=8))

    assert tuple(verdict.p_value for verdict in report.verdicts) == BOUNDARY_P_VALUES
    assert tuple(verdict.critical_value for verdict in report.verdicts) == BOUNDARY_P_VALUES
    assert tuple(verdict.rejected for verdict in report.verdicts) == (True, True, True, True)
    assert report.largest_rejected_rank == 4


def test_the_step_up_verdict_and_the_q_value_threshold_agree_on_every_dyadic_family() -> None:
    """`rejected` is derived from `k*`; `q_value <= rate` is the other statement of the same rule.

    Both are computed and only one is used, so a sweep is what keeps them from drifting apart.
    Every p-value is `i / 64`, every family size divides into `0.5` exactly, so this is an
    exhaustive check over exactly representable inputs rather than a random one that might
    disagree by an ulp on a Tuesday.
    """
    grid = tuple(index / 64 for index in (0, 4, 8, 12, 16, 32, 48, 64))
    for family_size in (1, 2, 4, 8, 16):
        for first in grid:
            for second in grid:
                for third in grid:
                    p_values = (first, second, third)
                    if family_size < len(p_values):
                        continue
                    report = control_false_discovery_rate(
                        _request(p_values, family_size=family_size)
                    )
                    assert tuple(verdict.rejected for verdict in report.verdicts) == tuple(
                        verdict.q_value <= report.false_discovery_rate
                        for verdict in report.verdicts
                    ), (family_size, p_values)


def test_q_values_never_decrease_with_rank_and_never_exceed_one() -> None:
    """The two properties an adjusted p-value has by construction, on the arm that clamps.

    `8 * 0.375 / 2` is `1.5`, so without the clamp the second row of the eight-family arm would
    publish a probability above one; and without the running minimum a q-value could fall as the
    p-value rises.
    """
    rising = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=2))
    assert tuple(verdict.q_value for verdict in rising.verdicts) == (0.125, 0.375)

    clamped = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=8))
    assert tuple(verdict.q_value for verdict in clamped.verdicts) == (0.5, 1.0)
    assert clamped.verdicts[-1].q_value == 1.0


# --- the dependence assumption is declared, and it changes the answer --------------------------


def test_an_independent_family_rejects_both_where_an_arbitrary_dependent_one_rejects_one() -> None:
    """One family, one rate, two declared assumptions, two different answers.

    `H_2` is `1.5` exactly, so the arbitrary-dependence arm's critical values are `0.75 / 1.5`
    of the independent arm's -- `(0.25, 0.5)` against `(0.375, 0.75)` -- and its q-values are
    `1.5` times as large. Both are exact. If `dependence` were a label written onto the report
    rather than an input to the arithmetic, these two arms would be bit-identical.
    """
    independent = control_false_discovery_rate(
        _request(DEPENDENCE_P_VALUES, family_size=2, false_discovery_rate=DEPENDENT_RATE)
    )
    arbitrary = control_false_discovery_rate(
        _request(
            DEPENDENCE_P_VALUES,
            family_size=2,
            false_discovery_rate=DEPENDENT_RATE,
            dependence="arbitrary",
        )
    )

    assert independent.dependence_penalty == 1.0
    assert arbitrary.dependence_penalty == HARMONIC_OF_TWO
    assert tuple(verdict.critical_value for verdict in independent.verdicts) == (0.375, 0.75)
    assert tuple(verdict.critical_value for verdict in arbitrary.verdicts) == (0.25, 0.5)
    assert tuple(verdict.q_value for verdict in independent.verdicts) == (0.125, 0.625)
    assert tuple(verdict.q_value for verdict in arbitrary.verdicts) == (0.1875, 0.9375)
    assert tuple(verdict.rejected for verdict in independent.verdicts) == (True, True)
    assert tuple(verdict.rejected for verdict in arbitrary.verdicts) == (True, False)


def test_the_harmonic_penalty_is_taken_over_the_declared_family_and_not_the_reported_rows() -> None:
    """`H_m` uses the same `m` the critical values do, which is the declared one.

    Two rows under a declared family of four take `H_4`, not `H_2`. A penalty computed off the
    rows would make the arbitrary-dependence arm *more* permissive than the family it declares.
    """
    report = control_false_discovery_rate(
        _request(DEPENDENCE_P_VALUES, family_size=4, dependence="arbitrary")
    )

    assert report.dependence_penalty == math.fsum(1 / index for index in range(1, 5))
    assert report.dependence_penalty != HARMONIC_OF_TWO


def test_a_stand_in_p_value_of_one_can_never_clear_its_own_line() -> None:
    """Why `backtest/outcome_statistics.py` leaves untested cohorts out for a *reporting* reason.

    An untested cohort could have been carried into the family at `p = 1.0` and the arithmetic
    would have been unharmed: every critical value is `rank * rate / (family_size * penalty)`
    with `rank <= family_size` and `rate < 1`, so it is strictly below one and a stand-in row
    can never be rejected and can never raise `k*`. The reason those cohorts are excluded is
    therefore not that they would loosen or tighten the control -- an earlier draft of that
    module's docstring claimed they would tighten it, which this test falsifies -- but that a
    q-value would be published for a cohort nothing was measured on.
    """
    for family_size in (1, 2, 3, 8, 16):
        for rate in (0.001, 0.05, 0.5, 0.99):
            report = control_false_discovery_rate(
                MultipleTestingRequest(
                    tests=tuple(
                        HypothesisTest(hypothesis_id=f"h{rank}", p_value=1.0, test="stand-in")
                        for rank in range(1, family_size + 1)
                    ),
                    family_size=family_size,
                    false_discovery_rate=rate,
                    dependence="independent-or-positively-dependent",
                )
            )
            assert max(verdict.critical_value for verdict in report.verdicts) < 1.0
            assert report.largest_rejected_rank == 0
            assert report.discoveries == 0


def test_a_harmonic_number_over_no_terms_is_refused_rather_than_returning_zero() -> None:
    """`harmonic_number` is public and its guard is unreachable from the request contract.

    `MultipleTestingRequest.family_size` is `ge=1`, so nothing that goes through
    `control_false_discovery_rate` can reach this. It is exported because the penalty is the
    thing a reader most wants to recompute by hand, and `H_0 = 0` would silently turn BY's
    correction into a division by zero one line later.
    """
    assert harmonic_number(1) == 1.0
    assert harmonic_number(2) == HARMONIC_OF_TWO
    with pytest.raises(ValueError):
        harmonic_number(0)


def test_a_hypothesis_the_family_never_carried_resolves_to_no_verdict() -> None:
    """`verdict_for` answers `None` rather than raising or returning the nearest row.

    A caller joining a report back onto its own list of hypotheses needs "this one is not in
    here" to be an answer, because a family that withheld rows has exactly that case by design.
    """
    report = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=8))

    assert report.verdict_for("h1") is not None
    assert report.verdict_for("h9") is None


# --- what the request refuses -----------------------------------------------------------------


def test_two_hypotheses_under_one_identifier_are_refused() -> None:
    """A duplicated id makes the rank a report publishes unresolvable to a hypothesis."""
    with pytest.raises(ValidationError):
        MultipleTestingRequest(
            tests=(
                HypothesisTest(hypothesis_id="h1", p_value=0.1, test="fixture"),
                HypothesisTest(hypothesis_id="h1", p_value=0.2, test="fixture"),
            ),
            family_size=2,
            false_discovery_rate=RATE,
            dependence="independent-or-positively-dependent",
        )


@pytest.mark.parametrize("rate", [0.0, 1.0, -0.5, 1.5])
def test_a_false_discovery_rate_outside_the_open_unit_interval_is_refused(rate: float) -> None:
    """`0` rejects nothing whatever the data says and `1` rejects on any p-value at all."""
    with pytest.raises(ValidationError):
        _request(FAMILY_P_VALUES, family_size=2, false_discovery_rate=rate)


def test_the_dependence_assumption_has_no_default_and_must_be_written_down() -> None:
    """The permissive reading must not also be the cheapest one to ask for.

    Independence is what BH's control needs and it is the assumption that rejects more, so a
    default would hand it to every caller who never thought about it.
    """
    with pytest.raises(ValidationError):
        MultipleTestingRequest(
            tests=_tests(FAMILY_P_VALUES),
            family_size=2,
            false_discovery_rate=RATE,
        )  # type: ignore[call-arg]


def test_the_family_size_has_no_default_and_must_be_written_down() -> None:
    """`V2-P5-007`'s second half as a constructor rule rather than as a convention."""
    with pytest.raises(ValidationError):
        MultipleTestingRequest(
            tests=_tests(FAMILY_P_VALUES),
            false_discovery_rate=RATE,
            dependence="independent-or-positively-dependent",
        )  # type: ignore[call-arg]


@pytest.mark.parametrize("p_value", [-0.0001, 1.0001])
def test_a_p_value_outside_the_closed_unit_interval_is_refused(p_value: float) -> None:
    with pytest.raises(ValidationError):
        HypothesisTest(hypothesis_id="h1", p_value=p_value, test="fixture")


def test_a_hypothesis_must_name_the_test_that_produced_its_p_value() -> None:
    """`every_p_value_here_is_the_callers_and_none_is_computed_or_checked` needs somewhere to
    point: the name is carried onto the verdict so a q-value resolves to a stated procedure."""
    with pytest.raises(ValidationError):
        HypothesisTest(hypothesis_id="h1", p_value=0.5, test="")

    report = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=2))
    assert tuple(verdict.test for verdict in report.verdicts) == (
        "closed-form fixture",
        "closed-form fixture",
    )


def test_ties_are_ordered_by_hypothesis_identifier_so_two_runs_agree() -> None:
    """Equal p-values still need a total order, and content is the only stable one available."""
    report = control_false_discovery_rate(
        MultipleTestingRequest(
            tests=(
                HypothesisTest(hypothesis_id="zulu", p_value=0.25, test="fixture"),
                HypothesisTest(hypothesis_id="alpha", p_value=0.25, test="fixture"),
            ),
            family_size=2,
            false_discovery_rate=RATE,
            dependence="independent-or-positively-dependent",
        )
    )

    assert tuple(verdict.hypothesis_id for verdict in report.verdicts) == ("alpha", "zulu")
    assert tuple(verdict.rank for verdict in report.verdicts) == (1, 2)


def test_a_report_whose_family_is_smaller_than_its_own_rows_cannot_be_constructed() -> None:
    """The refusal is on the contract and not only in the function that builds it.

    A `MultipleTestingReport` parsed from an edited document is the path a function-level guard
    does not cover, and it is the path a stored report is read back through.
    """
    report = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=8))
    document = report.model_dump()
    document["family_size"] = 1

    with pytest.raises(ValidationError):
        type(report).model_validate(document)


# --- the registry -----------------------------------------------------------------------------


def test_the_registry_names_every_limitation_this_module_declares() -> None:
    """Equality rather than membership, the form every registry in this repository has."""
    assert {entry.code for entry in KNOWN_MULTIPLE_TESTING_LIMITATIONS} == {
        "a_q_value_is_not_the_probability_that_one_discovery_is_false",
        "every_p_value_here_is_the_callers_and_none_is_computed_or_checked",
        "the_family_size_is_declared_and_no_check_can_confirm_it",
        "a_withheld_hypothesis_makes_the_answer_conservative_and_not_wrong",
        "dependence_is_declared_by_the_caller_and_never_measured",
        "the_family_of_models_tried_is_a_different_family_this_module_never_joins",
    }


def test_a_withheld_hypothesis_only_ever_shrinks_the_rejection_set() -> None:
    """The one guarantee truncation *does* give, driven rather than asserted in prose.

    An observed row's rank among the whole family is at least its rank among the reported rows,
    and BH's threshold rises with rank, so the reported-rank answer is a subset of the
    whole-family answer. That is what
    `a_withheld_hypothesis_makes_the_answer_conservative_and_not_wrong` claims, and here it is
    measured: the same three p-values under a family of three and under a family of eight give
    a rejection set that only ever loses members.
    """
    complete = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=2))
    truncated = control_false_discovery_rate(_request(FAMILY_P_VALUES, family_size=8))

    complete_rejected = {verdict.hypothesis_id for verdict in complete.verdicts if verdict.rejected}
    truncated_rejected = {
        verdict.hypothesis_id for verdict in truncated.verdicts if verdict.rejected
    }
    assert truncated_rejected < complete_rejected
    assert all(
        truncated.verdicts[index].q_value >= complete.verdicts[index].q_value
        for index in range(len(complete.verdicts))
    )
