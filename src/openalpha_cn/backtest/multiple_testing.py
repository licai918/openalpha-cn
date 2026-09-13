"""Benjamini-Hochberg false-discovery control, and the family size without which it says nothing.

`V2-P5-007` is two halves. The first is the procedure, which is a sort and two comparisons and
needs no numerical stack: order the p-values, compare `p_(i)` against `i * q / m`, take the
largest rank that clears its own line and reject every rank at or below it. The second half is
`m` -- **how many hypotheses were tested** -- and it is the half that gets forgotten.

**A q-value without the family it was computed against is not reproducible.** The same two
p-values `(0.0625, 0.375)` under a declared family of two are `(0.125, 0.375)` and two
discoveries; under a declared family of eight they are `(0.5, 1.0)` and one. Nothing about the
data moved. So `family_size` is a **required, stored field** here, on the request and on the
report, and it is never inferred from `len(tests)`:

    a hypothesis count recovered later by counting rows is a different number
    the day a row is filtered

That is not hypothetical for this repository. A search that sweeps forty factors and carries the
five that survived a tradeability gate into its report has a family of forty and five rows. A
reader who counts the rows gets five, divides by five instead of by forty, and publishes
q-values eight times too small. The only defence is that the number was written down at the
moment the search ran, and that is what this contract requires.

## What is checkable about a declared number, and what is not

Exactly one direction. `family_size < len(tests)` is an impossibility -- the caller handed over
more rows than it says it tested -- and is refused on the contract, so a request read back off
disk is refused too. `family_size > the truth` is anti-conservative and **nothing here can see
it**; `KNOWN_MULTIPLE_TESTING_LIMITATIONS`'
`the_family_size_is_declared_and_no_check_can_confirm_it` says so rather than implying a check
that does not exist.

The other direction -- more hypotheses tested than reported -- has a guarantee, and it is exact.
An observed row's rank *within the whole family* is at least its rank within the reported rows,
because the withheld p-values can only push it down the list. BH's line rises with rank, so a
row that clears `j * q / m` at its reported rank `j` also clears `r * q / m` at its true rank
`r >= j`. **The reported-rank rejection set is therefore a subset of the whole-family one and
the reported-rank q-values are upper bounds on the whole-family ones.** Truncation makes the
answer conservative; it does not make it wrong.

## The dependence assumption is an input, not a label

BH controls the false discovery rate under independence or positive regression dependency. Under
arbitrary dependence it does not, and Benjamini-Yekutieli's correction -- divide the line by
`H_m = sum(1/i for i in 1..m)` -- is what does. Both are here, chosen by a **required**
`dependence` field with no default, because the permissive reading (independence, which rejects
more) must not also be the cheapest one to ask for. It changes the arithmetic rather than
decorating the answer: on `(0.0625, 0.625)` at `rate = 0.75` and `m = 2`, independence rejects
both and arbitrary dependence rejects one.

What is *not* here is any verification of either assumption. A caller declares it and this module
records what it was handed; see `dependence_is_declared_by_the_caller_and_never_measured`.

## What this module is not

It computes no p-value and checks none. `HypothesisTest.p_value` and `HypothesisTest.test` are
both the caller's, the second is required so a q-value resolves to a named procedure, and a
p-value from a mis-specified test yields a q-value with exactly the same defect. That is the
division `backtest/factor_redundancy.py` already draws for its own numbers -- it publishes no
p-value at all, on three separate grounds -- and this module does not repeal it.

A pure standard-library leaf: `math`, `dataclasses`, `typing` and pydantic, no `openalpha_cn`
import at all. It is on `backtest-studies-touch-no-store`'s and
`backtest-studies-reach-no-composition-root`'s source lists, so it reaches no store, no engine
and no numeric stack -- ADR-0003's nine runtime dependencies are unchanged by it.
"""

import math
from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DependenceAssumption = Literal["independent-or-positively-dependent", "arbitrary"]
"""Which of the two corrections the caller's family warrants, declared and never measured.

`independent-or-positively-dependent` is Benjamini-Hochberg as published: the line is
`rank * rate / family_size`. `arbitrary` is Benjamini-Yekutieli: the same line divided by the
`family_size`-th harmonic number, which is what buys FDR control when the test statistics may
be dependent in any way at all.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class MultipleTestingLimitation:
    """One named thing this module's false-discovery control does not claim to have done."""

    code: str
    detail: str


KNOWN_MULTIPLE_TESTING_LIMITATIONS: Final[tuple[MultipleTestingLimitation, ...]] = (
    MultipleTestingLimitation(
        code="a_q_value_is_not_the_probability_that_one_discovery_is_false",
        detail=(
            "The false discovery rate is an expectation over the whole rejected set: at "
            "rate 0.10 with twenty discoveries, two of them are expected to be false, and the "
            "procedure says nothing whatever about which two. A single rejected hypothesis "
            "carrying q = 0.10 is not '90% likely to be real' -- that reading is the "
            "family-wise error rate, which BH deliberately does not control and which Holm or "
            "Bonferroni would. Nothing in this module reports a family-wise quantity, and a "
            "report of one discovery is exactly where the two readings diverge most."
        ),
    ),
    MultipleTestingLimitation(
        code="every_p_value_here_is_the_callers_and_none_is_computed_or_checked",
        detail=(
            "control_false_discovery_rate sorts and compares. It does not know what test "
            "produced a p-value, whether that test's assumptions held, or whether the number "
            "is a p-value at all rather than a rescaled t-statistic. HypothesisTest.test is a "
            "required free-text name so the verdict resolves to a stated procedure, and it is "
            "never parsed or validated against anything. A p-value from a mis-specified test "
            "yields a q-value with the same defect and no smaller."
        ),
    ),
    MultipleTestingLimitation(
        code="the_family_size_is_declared_and_no_check_can_confirm_it",
        detail=(
            "Exactly one direction is checkable and it is checked: family_size below the "
            "number of rows handed over is an impossibility and is refused on the contract, "
            "naming both numbers. The anti-conservative direction is not. A caller who swept "
            "forty specifications and declares five gets q-values computed against five, and "
            "nothing in a request says how many were really tried -- the count lives in the "
            "search that ran, not in the rows that survived it. domain/prediction_record.py "
            "makes the same observation about its own store and calls the count a denominator "
            "that 'a multiple-testing policy needs'; supplying one is still the caller's job."
        ),
    ),
    MultipleTestingLimitation(
        code="a_withheld_hypothesis_makes_the_answer_conservative_and_not_wrong",
        detail=(
            "When family_size exceeds the reported rows, the withheld p-values are unknown and "
            "the ranks used are the reported ones. A row's rank within the whole family is at "
            "least its reported rank, and BH's line rises with rank, so every rejection made "
            "here would also have been made by the whole family: the rejection set is a subset "
            "and the q-values are upper bounds. What that costs is real discoveries -- a "
            "truncated report finds fewer -- and it is stated here because it is the one "
            "property truncation does guarantee, which is easy to mistake for 'truncation is "
            "harmless'."
        ),
    ),
    MultipleTestingLimitation(
        code="dependence_is_declared_by_the_caller_and_never_measured",
        detail=(
            "The dependence field selects between BH's line and BY's harmonic-penalised one, "
            "and it is a declaration. Nothing here inspects the hypotheses for dependence, "
            "because nothing here has the test statistics -- only their p-values, which carry "
            "no joint structure at all. Two overlapping backtest windows on correlated names "
            "produce dependent p-values that look exactly like independent ones in this "
            "request, so a caller who declares independence out of habit gets an answer whose "
            "stated guarantee does not hold, and the report will still say "
            "'independent-or-positively-dependent' because that is what it was told."
        ),
    ),
    MultipleTestingLimitation(
        code="the_family_of_models_tried_is_a_different_family_this_module_never_joins",
        detail=(
            "domain/prediction_record.py records that the prediction store holds 'how many "
            "models were tried' as a count rather than a recollection, and model_view.py "
            "records that evaluations are deliberately not registered there. Those are a "
            "different family from the one a caller passes here, and this module cannot read "
            "either store -- backtest-studies-touch-no-store forbids them "
            "openalpha_cn.storage. So two searches over one body of data can each be "
            "controlled at rate q and their union controlled at nothing: a family is whatever "
            "one request declares, and joining two of them is a decision no code here makes."
        ),
    ),
)
"""What the control below does not claim, stated where the control is computed.

Two are about the inputs (the p-values and the dependence structure are the caller's), two are
about the family (its size is declared, and truncating it is conservative rather than free),
one is about the reading of a q-value, and one is about the family this module cannot see.
"""


class HypothesisTest(BaseModel):
    """One hypothesis, its p-value, and the name of whatever produced it.

    `test` is required and has no default. A q-value is a statement about a procedure, and a
    report whose rows do not say which procedure they came from puts the reader in the position
    `V2-P5-005` put them in: a number that implies more than it knows.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    hypothesis_id: str = Field(min_length=1, max_length=128)
    p_value: float = Field(ge=0, le=1)
    test: str = Field(min_length=1, max_length=256)


class MultipleTestingRequest(BaseModel):
    """A family of tested hypotheses, the size of that family, and the two declared policies.

    Every field that decides how permissive the answer is is mandatory. `family_size` because
    it is the whole second half of `V2-P5-007`, and `dependence` because independence is the
    assumption that rejects more.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tests: tuple[HypothesisTest, ...] = Field(min_length=1)
    family_size: int = Field(ge=1)
    """How many hypotheses the search that produced `tests` actually tested.

    At least `len(tests)`, and greater whenever rows were filtered out on the way to the report.
    Never inferred: see this module's docstring, and
    `the_family_size_is_declared_and_no_check_can_confirm_it` for the direction no check covers.
    """
    false_discovery_rate: float = Field(gt=0, lt=1)
    dependence: DependenceAssumption

    @model_validator(mode="after")
    def validate_the_family_holds_the_rows_it_carries(self) -> Self:
        identifiers = {item.hypothesis_id for item in self.tests}
        if len(identifiers) != len(self.tests):
            raise ValueError("every hypothesis_id in one family must be distinct")
        if self.family_size < len(self.tests):
            raise ValueError(
                f"family_size {self.family_size} is smaller than the {len(self.tests)} "
                "hypotheses this request carries; a family cannot be smaller than the rows "
                "reported out of it"
            )
        return self


class HypothesisVerdict(BaseModel):
    """One hypothesis's rank, line, adjusted p-value and verdict under the declared family."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str = Field(min_length=1, max_length=128)
    p_value: float = Field(ge=0, le=1)
    test: str = Field(min_length=1, max_length=256)
    rank: int = Field(ge=1)
    critical_value: float = Field(ge=0)
    """`rank * false_discovery_rate / (family_size * dependence_penalty)`.

    Carried per row rather than left to the reader, because it is the only thing that makes the
    verdict legible: a rejected row whose p-value is *above* its own line is the step-up
    procedure working, and without this column that reads as a bug.
    """
    q_value: float = Field(ge=0, le=1)
    rejected: bool


class MultipleTestingReport(BaseModel):
    """The controlled family: its declared size, its policies, and one verdict per reported row.

    `family_size`, `reported_hypotheses` and `withheld_hypotheses` are three **stored** numbers
    held to each other by a validator rather than one number and two derivations, so a document
    whose rows were edited after the fact fails to parse instead of quietly re-deriving a
    smaller family. That is `ValidationResult.validate_window_and_attribution`'s shape applied
    to a count.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    family_size: int = Field(ge=1)
    reported_hypotheses: int = Field(ge=1)
    withheld_hypotheses: int = Field(ge=0)
    false_discovery_rate: float = Field(gt=0, lt=1)
    dependence: DependenceAssumption
    dependence_penalty: float = Field(ge=1)
    """`1.0` under independence, `H_family_size` under arbitrary dependence."""
    verdicts: tuple[HypothesisVerdict, ...] = Field(min_length=1)
    discoveries: int = Field(ge=0)
    largest_rejected_rank: int = Field(ge=0)
    """BH's `k*`: the largest rank clearing its own line, or `0` when no rank does."""

    @model_validator(mode="after")
    def validate_the_counts_reconcile(self) -> Self:
        if self.reported_hypotheses != len(self.verdicts):
            raise ValueError("reported_hypotheses must be the number of verdicts carried")
        if self.family_size != self.reported_hypotheses + self.withheld_hypotheses:
            raise ValueError(
                "family_size must be reported_hypotheses plus withheld_hypotheses; "
                f"{self.family_size} is not {self.reported_hypotheses} + "
                f"{self.withheld_hypotheses}"
            )
        if self.discoveries != sum(1 for verdict in self.verdicts if verdict.rejected):
            raise ValueError("discoveries must be the number of rejected verdicts")
        return self

    @property
    def family_is_complete(self) -> bool:
        """Whether every hypothesis tested is reported here.

        A property rather than a field: it is a consequence of the two counts above, and a
        second stored boolean would be a number derived from a number, which is how
        `REGISTRY_ENTRY_COUNTS`' own docstring drifted.
        """
        return self.withheld_hypotheses == 0

    def verdict_for(self, hypothesis_id: str) -> HypothesisVerdict | None:
        """The verdict for one hypothesis, or `None` when this family never carried it."""
        for verdict in self.verdicts:
            if verdict.hypothesis_id == hypothesis_id:
                return verdict
        return None


def harmonic_number(count: int) -> float:
    """`sum(1/i for i in 1..count)`, Benjamini-Yekutieli's penalty for arbitrary dependence.

    `math.fsum` rather than a running total because the penalty divides every critical value in
    the report and a correctly-rounded sum is what makes `H_2 == 1.5` hold to the last bit -- the
    identity the arbitrary-dependence arm of `tests/unit/backtest/test_multiple_testing.py`
    asserts with `==`.
    """
    if count < 1:
        raise ValueError("a harmonic number is taken over at least one term")
    return math.fsum(1 / index for index in range(1, count + 1))


def control_false_discovery_rate(request: MultipleTestingRequest) -> MultipleTestingReport:
    """Apply Benjamini-Hochberg (or Benjamini-Yekutieli) across one declared family.

    The whole procedure is a sort and two comparisons, which is why it fits inside ADR-0003's
    nine runtime dependencies while a t-distribution quantile would not.

    **Ordering.** By `(p_value, hypothesis_id)`. Ties on the p-value are ordinary -- an exact
    randomization test over eight sign patterns can only produce eight distinct p-values -- and
    a rank that depended on dictionary order would give one family two reports.

    **The line.** `rank * rate / (family_size * penalty)`, computed with the *declared*
    `family_size` and never with `len(tests)`, and with the penalty taken over the declared
    family too.

    **The step up.** `k*` is the largest rank whose p-value is at or below its own line -- at or
    below, because BH's comparison is inclusive and a p-value landing exactly on `k * q / m` is
    a rejection. Every rank at or below `k*` is then rejected, including ranks that fail their
    own line: that is what makes this a step-up procedure rather than `n` separate tests, and it
    is the single most commonly mis-implemented part of it.

    **The q-value** is the adjusted p-value: `min(1, min over j >= i of penalty * m * p_(j) / j)`,
    a running minimum taken from the largest rank downwards so the column cannot fall as the
    p-value rises. It is reported beside `rejected` rather than instead of it, and the two agree
    -- `test_the_step_up_verdict_and_the_q_value_threshold_agree_on_every_dyadic_family` sweeps
    a grid of exactly representable p-values across five family sizes to keep them agreeing.
    """
    penalty = (
        1.0
        if request.dependence == "independent-or-positively-dependent"
        else (harmonic_number(request.family_size))
    )
    denominator = request.family_size * penalty
    ordered = sorted(request.tests, key=lambda item: (item.p_value, item.hypothesis_id))
    criticals = tuple(
        rank * request.false_discovery_rate / denominator for rank in range(1, len(ordered) + 1)
    )

    largest_rejected_rank = 0
    for index, item in enumerate(ordered):
        if item.p_value <= criticals[index]:
            largest_rejected_rank = index + 1

    q_values: list[float] = []
    running = math.inf
    for index in range(len(ordered) - 1, -1, -1):
        running = min(running, penalty * request.family_size * ordered[index].p_value / (index + 1))
        q_values.append(min(1.0, running))
    q_values.reverse()

    verdicts = tuple(
        HypothesisVerdict(
            hypothesis_id=item.hypothesis_id,
            p_value=item.p_value,
            test=item.test,
            rank=index + 1,
            critical_value=criticals[index],
            q_value=q_values[index],
            rejected=index + 1 <= largest_rejected_rank,
        )
        for index, item in enumerate(ordered)
    )
    return MultipleTestingReport(
        family_size=request.family_size,
        reported_hypotheses=len(verdicts),
        withheld_hypotheses=request.family_size - len(verdicts),
        false_discovery_rate=request.false_discovery_rate,
        dependence=request.dependence,
        dependence_penalty=penalty,
        verdicts=verdicts,
        discoveries=largest_rejected_rank,
        largest_rejected_rank=largest_rejected_rank,
    )
