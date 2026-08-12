"""The versioned preprocessing transform (`V2-P3-003`): what it declares, and what it produces.

Implementation Decision 8, in full: *"原始因子值与预处理分离。去极值、缺失值处理、标准化与中性化
是显式版本化变换。报告可比较 raw / processed / neutralized 表现而不覆盖源观测。"* -- raw factor
values and preprocessing are separate; winsorization, missing-value handling, standardization and
neutralisation are **explicitly versioned transforms**; a report can compare raw / processed /
neutralized performance **without overwriting the source observations**. Story S18 names the
three this issue owns; neutralisation is `V2-P3-004`'s and is deliberately absent here.

Three sentences, three obligations, and each one is a structure in this module rather than a
convention:

- **"显式版本化"** -> `FactorTransformSpec` is a content-addressed contract. Its `transform_id`
  is `domain/_identity.py::stable_model_id`, the same helper `factor_id`, `manifest_id`,
  `signal_id`, `decision_id` and `evidence_id` come from -- `V2-P3-001`'s "reuse it, do not mint
  a second hash" applies here unchanged, and for the same reason: a second spelling of
  "canonical" is a second thing that can disagree about key order, `ensure_ascii`, separators or
  float repr, and the failure mode of a disagreement is two IDs for one transform.
- **"与原值分离"** -> a `ProcessedFactorObservation` is a *different record type* in a
  *different panel dataset*. There is no code path that writes a processed value into
  `factor_obs_<key>_v<n>`: the observation contract there is `FactorObservation`, whose
  `coverage` vocabulary has no member a processed value could take.
- **"由哪个原值 + 哪个变换版本得到"** -> every processed row carries `transform_id` **and**
  `source_manifest_id` **and** `source_coverage`, and `(source_manifest_id, subject, as_of)` is
  the exact key of the raw row it came from. `FactorTransformManifest` closes the loop by
  hashing `source_observation_digest` -- a content address of the raw `(subject, coverage,
  value)` triples the build actually consumed -- so the identity moves when the numbers move,
  not merely when the *parameters* move.

## Why the missing-value policy has four fields and not one

`FactorObservation` already distinguishes four reasons a security has no value, and they are
not one fact:

| code | what it means | what filling it would do |
| --- | --- | --- |
| `not_in_universe` | the security was **not in the cross section** | invent a member |
| `insufficient_history` | in the universe, too young | score a name on data it does not have |
| `input_missing` | a required cell is null or the row is absent | stand in for a fetch |
| `undefined_value` | every input present, the arithmetic has none | stand in for a definition |

A policy that treated the four alike would be wrong in a way that is easy to state: imputing a
cross-sectional median for `not_in_universe` puts a name that had not listed yet -- or had
already delisted -- into the scored cross section, with a number derived from securities it was
never comparable to. That one is therefore **refused at declaration time** rather than left to
the caller's judgement (`MissingValuePolicy.refuse_a_filled_non_member`); the other three are
genuine choices and are declared per code, with no shared default and no field default, for the
reason `ReadinessRequirement`'s four checks have none: a defaulted policy is a decision that was
never taken reporting as one that was.

`MISSING_VALUE_COVERAGE_ORDER` is the four codes as data, and
`_refuse_a_policy_that_cannot_answer_every_missing_code` runs at import to check the policy's
field names against `FACTOR_COVERAGE_CODES` minus `computed`. A sixth coverage code therefore
cannot arrive without a policy field for it: the module refuses to import. That is the shape
`panel build`'s `PANEL_BUILD_TARGETS` failure asks for -- a table that gained a key with no
branch behind it and answered exit 0 with an empty result -- applied in the direction that
matters here, which is a *vocabulary* gaining a member with no policy behind it.

## The processed coverage vocabulary, and why **two** of its codes carry a value

`FactorCoverage`'s invariant is "exactly `computed` carries a value". `ProcessedCoverage`'s is
"`processed` **or** `imputed` carries one", and the second member is the whole point of having
a missing-value policy at all: an imputed number is a number this repository made up, and a
reader who cannot tell it from a measured one is a reader whose information coefficient is
computed partly on the median it imputed. So the separation is in the *code*, not in a
convention about which cross sections were filled.

Two further rules make the provenance claim structural rather than documentary, and both are
checked in `validate_processed_factor_observation`:

- a `processed` row must carry `source_coverage == "computed"` -- a measured processed value can
  only come from a measured raw one;
- an `imputed` row must carry `source_coverage != "computed"` -- an imputation can only stand in
  where there was nothing.

Without them, `coverage="processed", source_coverage="input_missing"` is constructible and
stores a made-up number under the code that says it was measured.

## What is deliberately *not* here

**No cross-sectional arithmetic.** `domain/` imports no numeric library (ADR-0003's
`domain-purity` contract) and, more to the point, a quantile is a function of a *cross section*
and a cross section is something the panel plane assembles. The winsorizers, standardizers and
imputers live in `openalpha_cn.panel_factors` beside `compute_factor`, and the two tables are
reconciled against these vocabularies at import; see `_refuse_transform_table_drift` there.

**No neutralisation.** `V2-P3-004`. D8 names it in the same sentence as the three here, and it
is the one that needs a cross-sectional regression -- which is also the workload ADR-0003's
2026-08-11 update names as the right place to re-open the numerical-stack question.

**No `ContractVersions` registration and no exported JSON Schema**, for `domain/factor.py`'s
reasons: a transform spec is code, and its output is stored as Parquet columns whose schema is
the partition's.

## The ordering constraint this module inherits

`domain/factor.py`'s applies here verbatim: adding, removing or renaming a field of
`FactorTransformSpec` or of `FactorTransformManifest` invalidates every `transform_id` and
`transform_manifest_id` already stored, and there is no migration. Every field these contracts
are going to need must land before `V2-P3-014` writes the first immutable artifact.
"""

import json
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.factor import (
    FACTOR_COVERAGE_CODES,
    FactorCoverage,
    FactorNote,
    FactorObservation,
    validate_notes,
)
from openalpha_cn.domain.panel_batch import PanelBatchError, validate_panel_identifier
from openalpha_cn.domain.time import ensure_aware


class FactorTransformError(ValueError):
    """Raised for a malformed transform spec, policy, observation or cross section.

    A `ValueError` for `FactorError`'s reason -- every call site that already writes
    `except ValueError` keeps catching it unchanged.

    **Where the line against `panel_factors.FactorEngineError` falls, and why it is there.**
    That class is a `RuntimeError` because a blocked partition or a missing evaluator is "a state
    of the store and of the wiring, not a malformed value". The transform splits along exactly
    that seam and the split is worth stating because it is not the obvious per-module one:

    - **Computing a transform never touches a store.** `apply_factor_transform` takes a
      `FactorPanel` and nothing else -- which is what makes its point-in-time claim structural --
      so every refusal it can raise is about a *value*: a spec that declares a parameter its own
      method ignores, a policy that would impute a value for a security that is not in the cross
      section, a scale estimator that collapsed on data that had dispersion, or a source coverage
      code the policy declared unacceptable. Those, and the contracts in this module, raise
      `FactorTransformError`.
    - **Writing and reading one is a store operation**, and its refusals are the ones
      `write_factor_panels` and `load_factor_observations` already own -- a partition replaced
      whole, a `supersedes` name that matches nothing, a row of the wrong width, a stored code
      this build cannot interpret. `write_processed_factor_panels` even shares
      `_refuse_to_drop_a_stored_build` verbatim with the raw plane, because "a partition is
      replaced whole" is the same fact for both. Those raise `FactorEngineError`.

    The alternative -- one error type for the whole issue -- would have meant either a second
    drop guard that differs from the raw plane's only in the exception it raises, or a shared
    guard whose type contradicts its module's own stated rule.
    """


# --- the three declared vocabularies ------------------------------------------------------------


WinsorizationMethod = Literal["none", "quantile", "mad"]
"""How the tails of a cross section are pulled in, as a closed set rather than free text.

- **`none`** -- no clipping. Not a placeholder: a transform that only standardises is a real
  configuration, and expressing it as "winsorize with parameters that happen to clip nothing"
  would make the stored parameters lie about what was done.
- **`quantile`** -- clip to the empirical `[lower_quantile, upper_quantile]` of the participating
  values. The conventional choice, and the one whose behaviour on a *small* cross section is
  least intuitive; `panel_factors._quantile` pins the interpolation rule and
  `tests/unit/test_factor_transform_rules.py` measures what a 1%/99% policy actually does at
  n=3.
- **`mad`** -- clip to `median +/- mad_scale * MAD`, where `MAD` is the median absolute deviation
  from the median. Robust where a quantile rule is not: the bound is estimated from the middle of
  the distribution rather than from its rank order, so a cross section with a handful of extreme
  values gets bounds that the extremes did not set.

Closed because `V2-P3-014`'s three-tier report groups by what was done, and a sixth spelling of
"clip the tails" would silently become a group of one -- `FactorFamily`'s argument.
"""

WINSORIZATION_METHODS: Final[frozenset[str]] = frozenset(get_args(WinsorizationMethod))

StandardizationMethod = Literal["none", "zscore", "rank"]
"""How the winsorized cross section is put on a comparable scale.

- **`none`** -- pass the (winsorized) values through. It makes no ordering claim at all, which
  is why it is the one method that is never `degenerate_cross_section`; see
  `STANDARDIZATION_NEUTRAL`.
- **`zscore`** -- `(x - mean) / stdev` over the winsorized participants. **The estimator is
  fixed rather than declared, and that is a decision**: `stdev` is the *population* standard
  deviation (divide by `n`), because the cross section at one `as_of` **is** the population
  rather than a sample drawn from one -- a `ddof=1` correction would be compensating for a
  sampling that did not happen, and it is undefined at `n = 1`, which is a cross section this
  contract admits. `panel_factors._population_stdev` carries the definition and a hand-computed
  test vector pins it, so the "obvious" `statistics.stdev` substitution fails rather than
  passing with numbers that are 0.5% different at n=100 and 41% different at n=3.
- **`rank`** -- the average rank of each participant, centred and scaled: `(rank - (n + 1) / 2)
  / n`, which has mean exactly `0` for every `n` including `n = 1`, and no `n - 1` denominator
  to special-case. Ties take the average of the ranks they span. Immune to the outliers
  winsorization exists to handle, which makes `winsorization="none"` a defensible pairing with
  it -- and *not* a no-op pairing, because clipping creates ties and ties move average ranks.

Why there is no `rank_normal` (inverse-normal / van der Waerden) member: it is a monotone
re-mapping of `rank`, so it changes no ordering and no rank correlation, and it would need an
inverse normal CDF -- the first function in this layer that a numerical stack would actually be
carrying its weight for. `V2-P3-004` is where that question is opened with a real workload.
"""

STANDARDIZATION_METHODS: Final[frozenset[str]] = frozenset(get_args(StandardizationMethod))

STANDARDIZATION_NEUTRAL: Final[dict[StandardizationMethod, float | None]] = {
    "none": None,
    "zscore": 0.0,
    "rank": 0.0,
}
"""The value `fill_neutral` imputes under each method, and `None` where there is no such value.

Declared here rather than in the engine because it is a property of what a method *means*, and
because `FactorTransformSpec` has to consult it at validation time: a spec that asks for
`fill_neutral` while standardising with `none` is asking for "the neutral point of the raw
scale", which does not exist -- a factor whose raw values sit around 50 has no reading under
which `0.0` is neutral, and imputing one would place every missing name at an extreme of the
cross section it was supposed to be neutral in. That combination is refused at declaration time.

`zscore` and `rank` both centre on zero by construction -- the z-score's mean is `0` and the
centred average rank's mean is exactly `0` -- so `0.0` is the value of a security that is neither
better nor worse than the cross section, which is what "neutral" has to mean for this to be an
honest name.

`panel_factors._refuse_transform_table_drift` reconciles this table's key set against
`STANDARDIZATION_METHODS` and against the engine's own standardizer table at import, so a fourth
method cannot arrive with a neutral point nobody chose.
"""

MissingValueAction = Literal[
    "exclude",
    "fill_cross_sectional_median",
    "fill_neutral",
    "refuse",
]
"""What the transform does with a security whose raw observation is not `computed`.

- **`exclude`** -- no processed value; the row is stored with `source_not_computed` and the
  source code it came from. The honest default for anything, and the only legal action for
  `not_in_universe`.
- **`fill_cross_sectional_median`** -- impute the median of the **processed** participants.
  Under `zscore` that is close to but not equal to `0.0` on any skewed cross section, which is
  exactly what distinguishes it from the next one.
- **`fill_neutral`** -- impute `STANDARDIZATION_NEUTRAL[standardization]`. Refused at
  declaration time when the standardization has no neutral point.
- **`refuse`** -- the presence of this code in the cross section is a fault, and
  `apply_factor_transform` raises rather than producing a panel. The action for a caller who
  would rather a build fail than quietly carry a hole: an `undefined_value` census that grew
  from 0 to 400 overnight is a broken factor, and a policy that imputed it would report the
  same numbers it reported yesterday.

Every imputation is stored under `imputed` rather than `processed`, so no fill is ever
indistinguishable from a measurement. See this module's docstring.
"""

MISSING_VALUE_ACTIONS: Final[frozenset[str]] = frozenset(get_args(MissingValueAction))

MISSING_VALUE_ACTION_ORDER: Final[tuple[MissingValueAction, ...]] = get_args(MissingValueAction)

FILL_ACTIONS: Final[frozenset[str]] = frozenset({"fill_cross_sectional_median", "fill_neutral"})
"""The two actions that produce a number. Named as data because two rules read it: the refusal
of a filled `not_in_universe`, and the refusal of `fill_neutral` under a method with no neutral
point. A membership test against a literal pair written twice is two things that can drift."""

MISSING_VALUE_COVERAGE_ORDER: Final[tuple[FactorCoverage, ...]] = (
    "not_in_universe",
    "insufficient_history",
    "input_missing",
    "undefined_value",
)
"""The four non-`computed` coverage codes, in `FACTOR_COVERAGE_ORDER`'s own order.

Restated as a tuple rather than derived by subtracting from a frozenset, because a set has no
order and this tuple decides the order of four stored manifest columns; a column order that
changed with the hash seed would make two identical builds write two different partitions.
Reconciled against `FACTOR_COVERAGE_CODES` at import by
`_refuse_a_policy_that_cannot_answer_every_missing_code`, which is what keeps the restatement a
pin rather than a copy.
"""

ProcessedCoverage = Literal[
    "processed",
    "imputed",
    "source_not_computed",
    "insufficient_cross_section",
    "degenerate_cross_section",
]
"""Whether this security has a processed value at this `as_of`, and if not, **why**.

Read in the order `apply_factor_transform` decides them, which is not the order they are
declared in -- the two cross-section codes are decided for the whole panel at once, before any
security is looked at:

- **`insufficient_cross_section`** -- fewer participants than `FactorTransformSpec
  .min_cross_section`. A whole-panel answer: a cross-sectional statistic estimated from three
  securities is not a cross-sectional statistic, and the number of securities below which this
  build declines to produce one is *declared*, not guessed at by the engine.
- **`degenerate_cross_section`** -- there were enough participants and the declared
  standardization found nothing to order: a zero standard deviation for `zscore`, an all-tied
  cross section for `rank`. Also whole-panel. `none` never produces it, because passing values
  through makes no ordering claim; see `STANDARDIZATION_NEUTRAL`.
- **`source_not_computed`** -- the raw observation was not `computed` and the policy for its
  code is `exclude`. `source_coverage` says which of the four it was, so this code does not
  collapse the distinction the raw vocabulary spent five members drawing.
- **`imputed`** -- the raw observation was not `computed` and the policy filled it. Carries a
  value that no security produced.
- **`processed`** -- the raw observation was `computed` and survived the pipeline. The only code
  under which a stored number is a transformed measurement.

Closed, and both cross-section codes are whole-panel rather than per-security, because
`V2-P3-014`'s three-tier report has to be able to say "this as_of produced nothing, and here is
which of the two reasons" without re-deriving it from the values.
"""

PROCESSED_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(ProcessedCoverage))

PROCESSED_COVERAGE_ORDER: Final[tuple[ProcessedCoverage, ...]] = get_args(ProcessedCoverage)
"""The declared order, which is also the census key order and the stored column order."""

PROCESSED_VALUE_CODES: Final[frozenset[str]] = frozenset({"processed", "imputed"})
"""The two codes that carry a value. `FactorCoverage`'s "exactly `computed`" has two members
here, and `validate_processed_factor_observation` enforces the pair rather than the singleton."""

MAX_TRANSFORM_KEY_LENGTH: Final[int] = 40
"""How long a transform key may be, and -- unlike `MAX_FACTOR_KEY_LENGTH` -- this is **not** a
panel-dataset-name budget.

The difference is worth stating because the two constants look alike and are load-bearing for
different things. A factor is the *partition axis* of the factor plane: each one gets
`factor_obs_<key>_v<n>`, so `MAX_FACTOR_KEY_LENGTH` is sized so that the longest declarable key
still fits `MAX_IDENTIFIER_LENGTH`. A transform is **not** an axis -- it is a column
(`transform_id` / `transform_key` / `transform_version`) inside the factor's own processed
dataset -- and it could not be one: the shortest honest prefix plus a 40-character factor key
plus `_v999` plus a separator plus a transform key plus `_v999` needs at least
`6 + 40 + 5 + 1 + 5 = 57` characters before the transform key gets a single letter, leaving six.
`panel_factors` states the consequence (one processed partition per factor holds every transform
of it) with the read and write costs that follow.

So this bound is a stored-column budget and a readability one: 40 characters is the same room a
factor key gets, and the value is validated as a panel identifier for the reason a factor key is
-- it is stored as a column value that a later query filters on, and `qualified_key` splits on
`/`.
"""


def _refuse_a_policy_that_cannot_answer_every_missing_code(
    declared_fields: Collection[str], order: Sequence[str]
) -> None:
    """Refuse this module at import if the policy does not have one field per missing code.

    The direction a per-field test cannot reach. `MissingValuePolicy` names four coverage codes;
    `FactorCoverage` declares five. A sixth coverage code added to `domain/factor.py` -- which is
    exactly what `V2-P3-007`'s coverage report or a later engine change might want -- would
    otherwise arrive with **no policy at all** for it, and `action_for` would raise at run time
    on the first cross section that contained one, in production, at whatever `as_of` first
    produced it.

    Checked against `FACTOR_COVERAGE_CODES` rather than against a restated list, and run at
    import rather than only in a test, for `panel_factors._refuse_table_drift`'s reason: a module
    that refuses to load is a failure a caller cannot route around, while a test is a failure
    only if somebody runs it.

    Takes its two inputs as arguments rather than reading the module's own globals, so that both
    failure directions are drivable. An audit whose only call site is the one that passes is an
    audit nobody has seen fail, which is the shape `_refuse_table_drift`'s own sentinel test
    exists to rule out.
    """
    declared = set(declared_fields)
    expected = set(FACTOR_COVERAGE_CODES) - {"computed"}
    if declared != expected:
        raise FactorTransformError(
            f"MissingValuePolicy declares actions for {sorted(declared)} and the coverage "
            f"vocabulary's non-computed codes are {sorted(expected)}; every reason a security "
            "has no raw value needs its own declared action, because filling a not_in_universe "
            "name and filling an input_missing one are not the same decision"
        )
    if set(order) != expected or len(set(order)) != len(order):
        raise FactorTransformError(
            f"MISSING_VALUE_COVERAGE_ORDER is {list(order)} and the vocabulary's non-computed "
            f"codes are {sorted(expected)}; the tuple decides four stored column names and must "
            "not drift from the set it restates"
        )


# --- the declared policies ------------------------------------------------------------------------


class WinsorizationPolicy(BaseModel):
    """A winsorization method **and** the parameters that method reads -- neither without the other.

    One flat model with three optional parameters rather than a discriminated union, so that
    the hashed payload is flat and "every declared field reaches `transform_id`" is directly
    assertable on the key set. What keeps that flatness honest is the cross-field rule below:
    **a parameter that the declared method does not read may not be set.**

    That refusal is not tidiness. A `WinsorizationPolicy(method="mad", mad_scale=3.0,
    lower_quantile=0.01)` reads, to anybody scanning a stored manifest row, like a 1% winsorization
    -- and it is not one; the quantile is inert. Worse, the inert parameter is *in the content
    address*, so two specs that behave identically get two `transform_id`s and two partitions'
    worth of numbers that nothing can reconcile. Refusing it makes the identity a function of
    what the transform does rather than of what somebody typed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: WinsorizationMethod
    lower_quantile: float | None = None
    upper_quantile: float | None = None
    """The empirical quantiles `method="quantile"` clips to, as fractions in `[0, 1]`.

    Strictly ordered (`lower < upper`) rather than merely distinct, because equal quantiles are
    a policy whose bounds are a single point: every participant is clipped to the median, which
    is not winsorization by any reading and is the exact shape `panel_factors` refuses at run
    time when a *scale estimator* collapses onto dispersed data. Refusing the declarable version
    of it here means the run-time refusal is about the data rather than about the spec.
    """
    mad_scale: float | None = None
    """How many median absolute deviations from the median `method="mad"` keeps.

    Strictly positive. Zero would put both bounds on the median -- the same collapse the
    quantile ordering rule refuses -- and a negative multiplier inverts the interval, which is
    an upper bound below the lower one and clips everything to whichever of the two `min`/`max`
    happens to reach first.
    """

    @model_validator(mode="after")
    def validate_the_method_reads_exactly_the_parameters_that_are_set(self) -> Self:
        quantiles = (self.lower_quantile, self.upper_quantile)
        if self.method == "none":
            if any(item is not None for item in (*quantiles, self.mad_scale)):
                raise ValueError(
                    "winsorization method 'none' clips nothing and reads no parameter, so "
                    f"lower_quantile={self.lower_quantile!r}, "
                    f"upper_quantile={self.upper_quantile!r} and mad_scale={self.mad_scale!r} "
                    "must all be unset; a parameter this method ignores would still enter "
                    "transform_id and would give two identical transforms two identities"
                )
            return self
        if self.method == "quantile":
            if self.mad_scale is not None:
                raise ValueError(
                    "winsorization method 'quantile' does not read mad_scale; a parameter this "
                    "method ignores would still enter transform_id"
                )
            if self.lower_quantile is None or self.upper_quantile is None:
                raise ValueError(
                    "winsorization method 'quantile' needs both lower_quantile and "
                    f"upper_quantile; got {self.lower_quantile!r} and {self.upper_quantile!r}"
                )
            for name, value in zip(("lower_quantile", "upper_quantile"), quantiles, strict=True):
                if value is None or not 0.0 <= value <= 1.0:
                    raise ValueError(f"{name} must be a fraction in [0, 1]; got {value!r}")
            if self.lower_quantile >= self.upper_quantile:
                raise ValueError(
                    f"lower_quantile {self.lower_quantile} is not below upper_quantile "
                    f"{self.upper_quantile}; equal or crossed quantiles put both winsorization "
                    "bounds on one point, which clips the whole cross section to it rather than "
                    "pulling in its tails"
                )
            return self
        if self.mad_scale is None:
            raise ValueError("winsorization method 'mad' needs mad_scale")
        if any(item is not None for item in quantiles):
            raise ValueError(
                "winsorization method 'mad' does not read lower_quantile or upper_quantile; a "
                "parameter this method ignores would still enter transform_id"
            )
        if not self.mad_scale > 0.0:
            raise ValueError(
                f"mad_scale must be strictly positive; got {self.mad_scale!r}. Zero puts both "
                "bounds on the median and a negative multiplier inverts the interval"
            )
        return self


class MissingValuePolicy(BaseModel):
    """One declared action per non-`computed` coverage code. Four fields, no defaults.

    See this module's docstring for why the four are not one decision. The single rule enforced
    here is the one whose violation is not a matter of taste: **a security that was not in the
    cross section may not be given a value.** Everything else is the caller's declared judgement,
    and the point of the contract is that the judgement is *stored* and *hashed* rather than
    living in whoever ran the build.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    not_in_universe: MissingValueAction
    insufficient_history: MissingValueAction
    input_missing: MissingValueAction
    undefined_value: MissingValueAction

    @model_validator(mode="after")
    def refuse_a_filled_non_member(self) -> Self:
        if self.not_in_universe in FILL_ACTIONS:
            raise ValueError(
                f"not_in_universe cannot be {self.not_in_universe!r}: a security the caller "
                "declared to be outside the cross section at this as_of is not a security with "
                "missing data -- a name that had not listed yet, or had already delisted, "
                "should have no value -- and imputing one puts it into the scored cross section "
                "with a number derived from securities it was never comparable to. 'exclude' "
                "and 'refuse' are the two legal actions for it"
            )
        return self

    def action_for(self, coverage: FactorCoverage) -> MissingValueAction:
        """The declared action for one non-`computed` coverage code.

        Resolved through `getattr` against the vocabulary rather than through a `match` over
        four branches, so that the audit at import (one field per code) is the *only* place the
        set of codes is enumerated. A branch table beside the field table is a second copy of a
        closed set, which is what `FACTOR_COVERAGE_ORDER` already carries a reconciliation test
        for.

        Returned *from* `MISSING_VALUE_ACTION_ORDER` rather than cast, `panel_factors
        ._coverage_code`'s form: the value came out of a model field whose type says it is one of
        four, and returning the matched member is what makes that true at run time as well.
        """
        if coverage == "computed":
            raise FactorTransformError(
                "action_for is asked about a missing-value code and 'computed' is not one; a "
                "computed observation is a participant in the cross section, not a hole in it"
            )
        declared = str(getattr(self, coverage))
        for action in MISSING_VALUE_ACTION_ORDER:
            if action == declared:
                return action
        raise FactorTransformError(
            f"the action declared for {coverage!r} is {declared!r}, which is not one of "
            f"{list(MISSING_VALUE_ACTION_ORDER)}"
        )


class FactorTransformSpec(BaseModel):
    """One versioned preprocessing transform: the three policies, and nothing that cannot be hashed.

    D8's "显式版本化" made concrete. `transform_id` is `stable_model_id(prefix="ftx")` over every
    field below, so two builds that used different quantiles, a different standardization, a
    different missing-value action **or** a different `min_cross_section` cannot share an
    identity -- and a build that used the same ones reproduces it.

    `version` is the human-facing half of that and is bumped when the *meaning* changes. It is
    not redundant with `transform_id`: a content address is stable and opaque, and a reader
    holding two partitions needs to know which of two transforms came later, which a hash cannot
    say.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-transform/v1"] = "factor-transform/v1"
    key: str = Field(min_length=1, max_length=MAX_TRANSFORM_KEY_LENGTH)
    version: int = Field(ge=1, le=999)
    winsorization: WinsorizationPolicy
    standardization: StandardizationMethod
    missing_values: MissingValuePolicy
    min_cross_section: int = Field(ge=1, le=10000)
    """The fewest participants for which this transform will produce values at all.

    Mandatory, with no default, and it is the declared answer to the question a cross-sectional
    statistic cannot answer for itself: *what is the 1% quantile of three securities?* The
    arithmetic has an answer -- `panel_factors._quantile` interpolates between order statistics,
    so the "1% quantile" of three points sits 2% of the way from the smallest value to the next
    one -- and it is not the answer the policy's author meant: the bound's position is a function
    of `n` rather than of the tail, and because the interval always excludes at least one name,
    the fraction actually clipped is `max(1 / n, q)`. A "1% winsorization" clips a third of a
    three-name cross section. Measured, with the counts and the fractions, in
    `tests/unit/test_factor_transform_rules.py`.

    This field is what makes that a *declaration* instead of a surprise. A build whose cross
    section is thinner than the number stated here produces `insufficient_cross_section` for
    every security and stores the count, rather than producing values whose bounds were set by
    the cross section's size. `1` is legal and means "always produce something", which is a
    defensible choice for a `rank` transform and a poor one for a 1% quantile winsorization; the
    contract's job is to record which was chosen, not to choose.

    **The upper bound is 10,000, and it is a range check on a stored integer rather than a
    vacuity guard.** That distinction is written out because the obvious reading is wrong and was
    written here once: 10,000 is *above* the ~5,500-name whole-market cross section ADR-0002
    sizes the panel plane against, so a spec that can never produce a value on today's market --
    `min_cross_section=10000` -- is declarable and this contract accepts it. Measured: 10,000
    validates, 10,001 does not, and the shipped registry's own market is 5,534 names. A bound
    that permits the case it is said to rule out is not a guard, so the guard is not claimed.

    **It is not claimed for a reason, and the reason is that the vacuity is an answer here.** A
    floor above the cross section produces `insufficient_cross_section` for every security, with
    the participant count stored on the manifest -- the same coded, auditable outcome a genuinely
    thin cross section at some historical `as_of` produces, which `apply_factor_transform`
    deliberately does not raise on. `FactorRegistry`'s empty-registry refusal is not the analogy:
    an empty registry has nothing to answer *with*, while this has an answer and stores it.
    Refusing a high floor at declaration time would also mean hard-coding today's listing count
    into a contract, so a listing wave -- or a caller declaring a transform for a single-industry
    cross section of 40 names -- would be arguing with a constant nobody re-measured.

    What 10,000 buys, then, is what a `Field(le=...)` buys anywhere: the stored
    `min_cross_section` column holds a plausible cross-section size rather than an unbounded
    integer, and a caller who typed a share count into it is refused at the contract instead of
    at the first build. `tests/unit/domain/test_factor_transform.py::
    test_the_floor_bound_is_a_range_check_and_admits_a_transform_wider_than_the_market` holds
    both halves, so the justification cannot drift back to the one this bound does not support.
    """

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        try:
            validate_panel_identifier(value, role="transform key")
        except PanelBatchError as error:
            raise ValueError(str(error)) from error
        return value

    @model_validator(mode="after")
    def refuse_a_neutral_fill_with_no_neutral_point(self) -> Self:
        neutral = STANDARDIZATION_NEUTRAL[self.standardization]
        if neutral is not None:
            return self
        filled = sorted(
            code
            for code in MISSING_VALUE_COVERAGE_ORDER
            if self.missing_values.action_for(code) == "fill_neutral"
        )
        if filled:
            raise ValueError(
                f"{filled} declare fill_neutral and standardization {self.standardization!r} has "
                "no neutral point: nothing was centred, so there is no value that means 'neither "
                "better nor worse than the cross section'. A factor whose raw values sit around "
                "50 would have every missing name imputed at 0.0, which is an extreme of the "
                "cross section rather than the middle of it. Use "
                "fill_cross_sectional_median, or standardize"
            )
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def transform_id(self) -> str:
        """The content address of this transform: every declared field, canonically hashed."""
        return stable_model_id(prefix="ftx", model=self)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def qualified_key(self) -> str:
        """`"cross_section_zscore/v1"` -- the human handle a CLI or a report shows."""
        return f"{self.key}/v{self.version}"


@dataclass(frozen=True, slots=True)
class FactorTransformRegistry:
    """Every transform this build knows, refusing the two shapes `FactorRegistry` refuses.

    A frozen tuple rather than a decorator-populated dict, for `FactorRegistry`'s reason: a
    registry populated by import side effects has a content that depends on which modules
    happened to be imported, and every "for each declared transform" audit would then be asking
    a question whose answer changes with import order.

    Written out rather than made generic over `FactorRegistry`. A shared implementation would
    need a `Protocol` with `qualified_key` and `_id`, and the two registries' *refusals* are
    what matters rather than their storage -- they would be the part not shared. Concrete and
    twenty lines is the trade this repository makes elsewhere (`ContractVersions` and
    `MIGRATIONS` are two hand-written tuples for the same reason).
    """

    specs: tuple[FactorTransformSpec, ...]
    notes: tuple[FactorNote, ...] = ()
    """The prose about these specs, out of every content address. See `factor.FactorNote`."""

    def __post_init__(self) -> None:
        if not self.specs:
            raise FactorTransformError(
                "a transform registry must declare at least one spec; an empty one satisfies "
                "every per-spec check vacuously"
            )
        keys = [item.qualified_key for item in self.specs]
        if len(set(keys)) != len(keys):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise FactorTransformError(
                f"{duplicates} is declared more than once; two specs answering to one name make "
                "a lookup arbitrary -- bump `version` on the restatement"
            )
        validate_notes(
            self.notes,
            declared=tuple(keys),
            role="transform",
            error=FactorTransformError,
        )

    def note_for(self, qualified_key: str) -> str | None:
        """The prose about `key/vN`, or `None` when this registry carries none for it.

        `FactorRegistry.note_for`'s contract, including that an undeclared handle is refused by
        `get` rather than answered `None`.
        """
        self.get(qualified_key)
        for note in self.notes:
            if note.subject == qualified_key:
                return note.summary
        return None

    @property
    def qualified_keys(self) -> tuple[str, ...]:
        return tuple(item.qualified_key for item in self.specs)

    @property
    def transform_ids(self) -> tuple[str, ...]:
        return tuple(item.transform_id for item in self.specs)

    def get(self, qualified_key: str) -> FactorTransformSpec:
        for item in self.specs:
            if item.qualified_key == qualified_key:
                return item
        raise FactorTransformError(
            f"{qualified_key!r} is not a declared transform; this build knows "
            f"{list(self.qualified_keys)}"
        )

    def by_id(self, transform_id: str) -> FactorTransformSpec:
        """The direction a stored processed row needs: a column carries `transform_id` alone."""
        for item in self.specs:
            if item.transform_id == transform_id:
                return item
        raise FactorTransformError(
            f"{transform_id!r} is not a transform this build declares; it knows "
            f"{list(self.transform_ids)}"
        )


_refuse_a_policy_that_cannot_answer_every_missing_code(
    MissingValuePolicy.model_fields, MISSING_VALUE_COVERAGE_ORDER
)


# --- what one application of a transform was made of ---------------------------------------------


def observation_digest(observations: Sequence[FactorObservation]) -> str:
    """A content address for the raw cross section a transform consumed: subject, code and value.

    **Why this exists, and why `source_manifest_id` alone is not enough.** A build manifest
    identifies the *inputs and parameters* of a factor computation, not its outputs, so two
    `FactorPanel`s carrying the same `manifest_id` and different observations are constructible
    -- `compute_factor` will not produce them, but nothing in this layer receives its output from
    `compute_factor` rather than from a caller. Without this field, a transform's identity would
    be blind to the very numbers it transformed, and `tests/integration/panel/
    test_factor_transforms.py`'s determinant audit would be exempting the `panel` argument on a
    promise instead of measuring it -- `test_a_source_panel_whose_numbers_moved_moves_the_
    transform_identity` is the measurement, built by hand because `compute_factor` will not
    produce that state and `FactorPanel` is a public frozen dataclass that can.

    Sorted by subject and de-duplicated by refusal rather than by dropping, because a repeated
    subject is two answers to one question and a digest that silently kept one of them would
    make two different cross sections share an address. `compute_factor` already refuses a
    duplicated subject; this is the same rule at the boundary that hashes them, which is
    `validate_factor_observation`'s two-call-site argument applied to a set rather than to a row.

    The same canonicalisation `stable_model_id` and `set_digest` use -- `json.dumps` with fixed
    separators, `ensure_ascii=False` and `allow_nan=False`. `allow_nan=False` is load-bearing
    rather than inherited: a non-finite value cannot reach a stored observation (three separate
    guards see to that), and if one ever did, this function refuses to hash it rather than
    minting an address for a cross section that cannot be reproduced.

    **That refusal is translated rather than propagated.** `json.dumps` raises a bare
    `ValueError("Out of range float values are not JSON compliant")`, which names no security, no
    dataset and no remedy -- the reader of it is somebody holding a panel of thousands of rows
    with no way to find the one. The reachable path is a `FactorObservation` subclass that
    overrode `__post_init__` -- the same door `validate_factor_observation`'s two call sites
    exist for -- so the message says which subjects carry it and which code they belong under.
    """
    subjects = [item.subject for item in observations]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise FactorTransformError(
            f"{duplicates} appears more than once in the source panel; a duplicated security is "
            "two answers to one question, and a digest that hashed both would give two different "
            "cross sections one address"
        )
    payload = sorted([item.subject, item.coverage, item.value] for item in observations)
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except ValueError as error:
        offending = sorted(
            item.subject
            for item in observations
            if item.value is not None and not math.isfinite(item.value)
        )
        raise FactorTransformError(
            f"{offending} carry a non-finite value (nan or an infinity) in the source cross "
            "section, so it has no content address: the canonical form this digest hashes "
            "refuses one, and hashing a substitute would mint an address for a cross section "
            "nobody can reproduce. A non-finite raw value belongs under the undefined_value "
            "coverage code with value None, and validate_factor_observation refuses it at both "
            "of its call sites -- a row carrying one reached this panel through a "
            "FactorObservation subclass that overrode __post_init__"
        ) from error
    return f"obs_{sha256(canonical).hexdigest()[:24]}"


class FactorTransformManifest(BaseModel):
    """What one application of a transform was made of, as a content address.

    `FactorBuildManifest`'s discipline, one layer up, and with both of its measured lessons
    already applied rather than re-learned:

    - **Every declared field enters `transform_manifest_id`**, and
      `tests/unit/domain/test_factor_transform.py` varies each one alone and asserts the ID moves.
    - **`built_at` is deliberately not a field.** Re-applying the same transform to the same
      source build must reproduce the ID, or the identity cannot detect drift and a rebuild can
      never be written past `_refuse_to_drop_a_stored_build`. The wall clock is recorded as the
      partition's `fetched_at`, which is `FactorPanel`'s arrangement.
    - **`source_observation_digest` is a field**, which is the half `FactorBuildManifest` had to
      learn twice: what *determines* the answers has to be in the identity, not merely what was
      declared. See `observation_digest`.

    **`date_timezone` is deliberately absent, and its absence is a claim.** `FactorBuildManifest`
    carries one because `compute_factor` resolves a UTC instant into a *session date* in that
    zone, and the resolution changes which rows land in a window. A transform resolves no date:
    it reads values and coverage codes off observations that already carry an aware `as_of`, and
    the only timezone in its life is the one `write_processed_factor_panels` uses to decide which
    partition **year** a row belongs to -- a storage question, settled at the write, exactly as
    it is for `write_factor_panels`. A field here would be one that reaches the identity and
    decides nothing, which is the mirror image of the defect roadmap section 9 records.

    **One field used to have that property and no longer does**, which is worth recording because
    it was the counter-example this paragraph had to disclose. `transform_id` is a hash of
    `FactorTransformSpec`, which carried a `summary` -- so a typo fixed there moved every stored
    `transform_manifest_id` and changed no number, the very thing `date_timezone` is refused for.
    It was an inherited convention (`FactorDefinition.summary` sat inside `factor_id` the same
    way) and it was disclosed with its cost rather than fixed twice. All three contracts dropped
    it together in one change, and the prose lives in `domain/factor.py::FactorNote`, which no
    `stable_model_id` is applied to. So the rule this class states about `date_timezone` now
    holds without an exception beside it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-transform-manifest/v1"] = "factor-transform-manifest/v1"
    transform_id: str = Field(min_length=1, max_length=64)
    transform_key: str = Field(min_length=1, max_length=MAX_TRANSFORM_KEY_LENGTH)
    transform_version: int = Field(ge=1, le=999)
    source_factor_id: str = Field(min_length=1, max_length=64)
    source_factor_key: str = Field(min_length=1, max_length=64)
    source_factor_version: int = Field(ge=1, le=999)
    source_manifest_id: str = Field(min_length=1, max_length=64)
    """The `FactorBuildManifest.manifest_id` of the raw build this transform consumed.

    Half of the answer to "which raw value produced this processed one": together with a row's
    `subject` and `as_of` it is the exact key of a row in `factor_obs_<key>_v<n>`, which is the
    join `tests/integration/panel/test_factor_transforms.py` performs rather than describes.
    """
    source_observation_digest: str = Field(min_length=1, max_length=64)
    as_of: datetime
    code_commit: str = Field(min_length=7, max_length=64)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def transform_manifest_id(self) -> str:
        """The content address of this application. See this class's docstring."""
        return stable_model_id(prefix="ftm", model=self)


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorTransformStatistics:
    """The numbers behind the numbers: recorded, stored, **never hashed**.

    `FactorInputProvenance`'s arrangement for a different reason. These are *outputs* -- the
    bounds that were applied, how many values each of them moved, the location and scale that
    were estimated, how many rows were imputed -- and an identity computed from a build's outputs
    is one nobody can predict before running the build, which defeats the purpose of having one.

    They are nonetheless the difference between a transform that is auditable and one that is
    not, and this repository has the counter-example: `coverage_census()` existed and spoke only
    to a caller that asked, so a build in which nothing was computed reached Parquet looking
    exactly like one that scored the whole market. `winsorized_low_count` and
    `winsorized_high_count` are the same instrument pointed at the same failure here -- a 1%
    winsorization on a 3-name cross section is a declared policy whose effect is entirely a
    function of `n`, and the only thing that makes it visible on a stored partition is a count of
    what it actually moved.
    """

    participant_count: int
    winsorized_low_count: int
    winsorized_high_count: int
    imputed_count: int
    lower_bound: float | None
    upper_bound: float | None
    location: float | None
    """The centre the standardization removed -- the mean, for `zscore`. `None` for a method
    that estimates no location (`none`, `rank`) and for a build that produced no values."""
    scale: float | None
    """The spread the standardization divided by -- the population standard deviation, for
    `zscore`. `None` for the same two cases as `location`."""

    def __post_init__(self) -> None:
        for name in (
            "participant_count",
            "winsorized_low_count",
            "winsorized_high_count",
            "imputed_count",
        ):
            if int(getattr(self, name)) < 0:
                raise FactorTransformError(f"{name} cannot be negative")
        clipped = self.winsorized_low_count + self.winsorized_high_count
        if clipped > self.participant_count:
            raise FactorTransformError(
                f"{clipped} values were clipped and only {self.participant_count} participated; "
                "a winsorizer cannot move a value that was not in the cross section"
            )
        if (self.lower_bound is None) != (self.upper_bound is None):
            raise FactorTransformError(
                "lower_bound and upper_bound are both present or both absent; an interval with "
                "one end is not an interval"
            )
        for name in ("lower_bound", "upper_bound", "location", "scale"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise FactorTransformError(f"{name} is {value!r}, which is not a finite number")
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise FactorTransformError(
                f"the winsorization interval {self.lower_bound} to {self.upper_bound} runs "
                "backwards"
            )
        if self.scale is not None and self.scale <= 0.0:
            raise FactorTransformError(
                f"scale is {self.scale!r}; a standardization that divided by zero or by a "
                "negative number is the degenerate_cross_section code's own case and must not "
                "be recorded as a completed one"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessedFactorObservation:
    """One security's processed answer at one `as_of`, and the raw answer it came from.

    A frozen dataclass rather than a pydantic model for `FactorObservation`'s reason: it is
    constructed once per `(security, as_of, transform)` and a whole-market cross section is 5,534
    of them.

    The invariants are in `validate_processed_factor_observation`, which `__post_init__` calls
    and which the write path calls again -- `domain/factor.py` and `panel/catalog.py` both argue
    the two-call-site shape, and it was *measured* on the factor observation: a three-line
    subclass overriding `__post_init__` put an empty subject, a negative row count and a
    backwards window into a Parquet partition.

    The load-bearing rules are the two that make "与原值分离" structural rather than documentary:
    a `processed` row must come from a `computed` source, and an `imputed` row must not. Without
    them `coverage="processed", source_coverage="input_missing"` is constructible and stores an
    imputed number under the code that says it was measured -- which is the one thing this whole
    contract exists to prevent.
    """

    subject: str
    as_of: datetime
    value: float | None
    coverage: ProcessedCoverage
    transform_id: str
    transform_manifest_id: str
    source_factor_id: str
    source_manifest_id: str
    """The build that produced the raw observation. With `subject` and `as_of`, its exact key."""
    source_coverage: FactorCoverage
    """The raw observation's own coverage code, carried rather than joined for.

    Not redundant with the join. A reader of this partition alone can tell a security that was
    outside the cross section from one whose input was null -- a distinction the raw vocabulary
    spent five members drawing and which `source_not_computed` would otherwise collapse into
    one -- without opening the raw partition at all. And it is what the two structural rules
    above are checked against.
    """

    def __post_init__(self) -> None:
        # Normalised rather than merely required to be aware, for `FactorObservation`'s measured
        # reason: a stored instant read back out of DuckDB arrives tagged with the session's own
        # timezone rather than UTC, and the same instant under two labels is one dictionary key
        # only after this line.
        object.__setattr__(self, "as_of", ensure_aware(self.as_of))
        validate_processed_factor_observation(self)


def validate_processed_factor_observation(observation: ProcessedFactorObservation) -> None:
    """Every rule a stored processed observation has to satisfy, as a function with two call sites.

    `ProcessedFactorObservation.__post_init__` is one and
    `panel_factors.processed_observation_batch` is the other, for the reason
    `validate_factor_observation` has two: a `__post_init__` is a method, a frozen dataclass with
    `slots=True` is still subclassable, and the write boundary is the last place a row that
    skipped the constructor's rules can be stopped before it is a column in a Parquet file.
    """
    if not observation.subject:
        raise FactorTransformError("a processed observation must name a subject")
    ensure_aware(observation.as_of)
    if observation.coverage not in PROCESSED_COVERAGE_CODES:
        raise FactorTransformError(
            f"{observation.coverage!r} is not a declared processed coverage code; expected one "
            f"of {sorted(PROCESSED_COVERAGE_CODES)}"
        )
    if observation.source_coverage not in FACTOR_COVERAGE_CODES:
        raise FactorTransformError(
            f"{observation.source_coverage!r} is not a declared factor coverage code; expected "
            f"one of {sorted(FACTOR_COVERAGE_CODES)}"
        )
    carries_value = observation.coverage in PROCESSED_VALUE_CODES
    if (observation.value is None) == carries_value:
        raise FactorTransformError(
            f"{observation.subject} at {observation.as_of.isoformat()} reports coverage "
            f"{observation.coverage!r} with value {observation.value!r}; exactly "
            f"{sorted(PROCESSED_VALUE_CODES)} carry a value, and every other code carries None"
        )
    if observation.value is not None and not math.isfinite(observation.value):
        raise FactorTransformError(
            f"{observation.subject} at {observation.as_of.isoformat()} reports coverage "
            f"{observation.coverage!r} with value {observation.value!r}; a non-finite processed "
            "value poisons every downstream mean, rank and regression built on the column, and "
            "no declared code carries one"
        )
    if observation.coverage == "processed" and observation.source_coverage != "computed":
        raise FactorTransformError(
            f"{observation.subject} at {observation.as_of.isoformat()} is stored as 'processed' "
            f"with source_coverage {observation.source_coverage!r}; a processed value is a "
            "transformed measurement, and the only raw code that carries a measurement is "
            "'computed'. An imputed value belongs under 'imputed'"
        )
    if observation.coverage == "imputed" and observation.source_coverage == "computed":
        raise FactorTransformError(
            f"{observation.subject} at {observation.as_of.isoformat()} is stored as 'imputed' "
            "with source_coverage 'computed'; the security had a measured value, so there was "
            "nothing for the missing-value policy to stand in for"
        )
    if observation.coverage == "source_not_computed" and observation.source_coverage == "computed":
        raise FactorTransformError(
            f"{observation.subject} at {observation.as_of.isoformat()} is stored as "
            "'source_not_computed' with source_coverage 'computed'; the two statements are each "
            "other's negation"
        )
