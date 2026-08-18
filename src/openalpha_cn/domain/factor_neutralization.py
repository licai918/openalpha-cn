"""The versioned neutralisation (`V2-P3-004`): the fourth transform D8 names, as its own contract.

Implementation Decision 8, in full: *"原始因子值与预处理分离。去极值、缺失值处理、标准化与中性化
是显式版本化变换。报告可比较 raw / processed / neutralized 表现而不覆盖源观测。"* -- `V2-P3-003`
delivered the first three and deliberately left the fourth; this module is the fourth, and
`raw / processed / neutralized` is the sentence that decides its shape: **three tiers, three
record types, three pairs of datasets**, none of them written over another.

## Why this is a second spec type and not a fourth stage of `FactorTransformSpec`

Both were arguable and the choice is stated with what decided it, because "it is the same
sentence in D8" is an argument for the other one.

- **The inputs are not of the same kind.** Winsorization, standardization and the missing-value
  policy are functions of the factor's own cross section and nothing else, which is exactly what
  lets `apply_factor_transform` take a `FactorPanel` and **no store**. A neutralisation needs two
  *foreign* panel datasets -- an industry assignment and a market capitalisation -- that no
  `FactorPanel` carries. Folding it into `FactorTransformSpec` would give
  `apply_factor_transform` an extra argument that every spec declaring "no neutralisation" does
  not read, which is the shape `WinsorizationPolicy` refuses one level down: *a parameter the
  declared method does not read may not be set*, because an inert parameter still enters the
  content address and gives two identical transforms two identities.
- **The output vocabulary is not the same closed set.** A neutralised row can fail for reasons a
  processed row cannot have -- no industry on the day, no usable market cap, an industry group
  too thin to remove a mean from. Adding those to `ProcessedCoverage` would add census columns to
  `factor_procmn_*` for codes no processed row can ever carry, and would move every stored
  transform's census layout for a fact about a different plane.
- **The composition is a chain and reads better as one.** This module's input is a
  `ProcessedFactorPanel` -- the output of the third transform -- so the two specs compose in D8's
  own order rather than one spec growing a stage that only makes sense last.

What is *not* re-litigated is identity: `neutralization_id` is
`domain/_identity.py::stable_model_id`, the same helper `factor_id`, `manifest_id`,
`transform_id`, `signal_id`, `decision_id` and `evidence_id` come from. `V2-P3-001`'s "reuse it,
do not mint a second hash" applies here unchanged and for its own reason -- a second spelling of
"canonical" is a second thing that can disagree about key order, `ensure_ascii`, separators or
float repr.

## What a cross-sectional regression actually is here, and the four choices that are declared

The regression is the factor value on **industry dummies plus one market-cap regressor**, and the
stored number is the residual. Four things about it change every residual, so all four are
fields on `FactorNeutralizationSpec` rather than constants in an engine:

| declared | what it decides | effect on the residuals |
| --- | --- | --- |
| `industry_level` | 31 groups (L1), 134 (L2) or 346 (L3) | changes the group means entirely |
| `market_cap_measure` | `total_mv` or `circ_mv` | moves them, by a **floor** rather than a figure |
| `market_cap_scale` | `level` or `log` | the same, and by more |
| `participation` | whether an *imputed* processed value is in the regression | changes the sample |

**"A floor rather than a figure" is a deliberate retreat from an earlier claim and is worth
reading as one.** The middle two rows once carried point estimates (`0.0196` and `0.195`, against
"residuals whose rms is 0.995"). They came from one seed of the synthetic probe in
`tests/unit/test_factor_neutralization_rules.py`, whose "circulating" capitalisation is a
`Normal(0.7, 0.25)` float ratio invented in the test and not a stored `circ_mv` -- and the
quantity is not stable enough to quote: over the eight seeds that test now sweeps, the measure gap
spans 0.0067..0.0591 and the scale gap 0.021..1.222, both against residuals whose deviation is 1
by construction. So what this repository asserts is what it can hold: **every seed clears a
floor**, which falsifies "these are formalities" and claims nothing further. A number that varies
by 58x across an arbitrary parameter is not evidence about the market.

**And one thing that looks like a choice and provably is not: re-scaling the regressor.** OLS
residuals are the projection onto the orthogonal complement of the design's column space, and an
affine map of one regressor leaves that space unchanged whenever the constant it adds is already
in the span -- which a complete set of industry dummies always contains, since they sum to one on
every row. So "standardize the market cap first" is not a declarable option here, because it is
not an option: measured on the 5,534-name probe, z-scoring `log(total_mv)` moves every residual by
**4.44e-16** and `1000 * log(cap) + 7` by the same. A field for it would be one that reaches the
identity and decides nothing, which is the defect `FactorTransformManifest` rejected
`date_timezone` for. What is *not* claimed is that `4.44e-16` bounds every affine map: `1e-6 * x
- 3` measures **8.4e-12** on the same probe, because the demeaned regressor's own scale sets how
much cancellation the subtraction costs. The invariance is of the *answer*, not of the bits. See
`MarketCapScale` for what does move them.

## The rank of the design, which is a real question with a measured answer

31 industry dummies plus an intercept is rank-deficient: the dummies sum to the intercept. The
three usual repairs -- drop one industry and keep the intercept, drop the intercept and keep all
31, or impose a sum-to-zero constraint -- differ in what the *coefficients mean* and **not** in
the residuals, because all three span the same column space. That is the half worth measuring
rather than asserting, and it is measured: a dense least-squares solve of `intercept + 30 dummies
+ cap` and one of `31 dummies + cap` agree on every residual of a 5,534-name cross section to
**8.88e-16**, and the closed form this repository actually runs agrees with both to the same
bound. `panel_neutralization.apply_factor_neutralization` therefore takes the second reading --
every industry carries its own intercept and there is no global one -- because it is the only one
of the three that needs no arbitrary reference group, and because the arithmetic it licenses is
`O(n)` rather than a 32x32 solve. See that module for the closed form and the timings.

## Seven coverage codes, and why none of them is `ProcessedCoverage`'s

`NeutralizedCoverage` is a fresh closed set rather than an extension of the processed one, for
the reason above -- and the two whose absence would be a silent defect are named here:

- **`industry_missing` is a code and not a drop.** `KNOWN_INDUSTRY_LIMITATIONS
  .a_security_can_be_unclassified_inside_its_listed_life` measures the residue at 0.02% of the
  2026 market and **2.95%** of the 2015 one, over securities that were listed and trading. A
  neutralisation that silently omitted them would report a cross section 3% narrower than the
  market and nothing on the stored partition would say so.
- **`thin_industry` is a code and not a zero.** A security that is the only member of its
  industry has a residual of **exactly `0.0`** -- it is its own group mean in both `y` and `x`,
  so both demeaned quantities are zero and so is the residual, whatever the slope is. Storing
  that under `neutralized` puts a structural constant into a column a report will rank on, and a
  reader cannot tell it from a security that genuinely sits at its industry's centre. So the
  floor is declared (`min_industry_members`, at least 2) and a group below it gets a code and no
  value. That this costs the *other* securities nothing is measured rather than assumed: a
  singleton contributes `0 * 0` to the slope's numerator and `0` to its denominator, so removing
  it leaves the slope **bit-identical** and every other residual unmoved by exactly `0`.

## The participation rule, and the import-time audit behind it

`ProcessedFactorPanel` deliberately offers `values()` and `measured_values()` and says "the point
of having two methods is that it has to choose rather than inherit". This is the choice, made
once, stored and hashed: `measured_only` regresses the `processed` rows, `measured_and_imputed`
adds the `imputed` ones. Both readings are defensible -- an imputed cross-sectional median is a
number this repository invented, and leaving it out shrinks the sample -- and neither is a
default.

`_refuse_a_participation_table_that_cannot_answer_every_valued_processed_code` runs **at import**
against `PROCESSED_VALUE_CODES`, in `domain/factor_transform.py`'s own idiom and for its measured
reason: a sixth processed code that carried a value would otherwise be dropped from every
regression under both rules -- a name silently missing from a cross section, which is the exact
failure `domain/daily_prices.py` refuses a null `total_mv` to avoid. A module that refuses to
load is a failure a caller cannot route around; a test is a failure only if somebody runs it.

## What is deliberately *not* here

**No arithmetic.** `domain/` imports no numeric library (ADR-0003's `domain-purity` contract) and
a group mean is a function of a cross section, which the panel plane assembles. The regression
lives in `openalpha_cn.panel_neutralization`, and the two tables are reconciled against these
vocabularies at import; see `_refuse_neutralization_table_drift` there.

**No store, and no reader of one.** `IndustryMarketCapCrossSection` is a *value*: the caller
assembles it and stamps it with the `as_of` it was read at, and the engine refuses one whose
`as_of` is not the panel's. See that class for exactly how strong that is and how strong it is
not.

**No `ContractVersions` registration and no exported JSON Schema**, for `domain/factor.py`'s
reasons: a spec is code, and its output is stored as Parquet columns whose schema is the
partition's.

## The ordering constraint this module inherits

`domain/factor.py`'s applies here verbatim: adding, removing or renaming a field of
`FactorNeutralizationSpec` or of `FactorNeutralizationManifest` invalidates every
`neutralization_id` and `neutralization_manifest_id` already stored, and there is no migration.
Every field these contracts are going to need must land before `V2-P3-014` writes the first
immutable artifact.
"""

import json
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.factor import FactorError, FactorNote, cross_section_digest, validate_notes
from openalpha_cn.domain.factor_transform import (
    PROCESSED_COVERAGE_CODES,
    PROCESSED_VALUE_CODES,
    ProcessedCoverage,
    ProcessedFactorObservation,
)
from openalpha_cn.domain.industry_classification import IndustryAssignment
from openalpha_cn.domain.panel_batch import PanelBatchError, validate_panel_identifier
from openalpha_cn.domain.time import ensure_aware


class FactorNeutralizationError(ValueError):
    """Raised for a malformed neutralisation spec, cross section, observation or manifest.

    A `ValueError` for `FactorTransformError`'s reason -- every call site that already writes
    `except ValueError` keeps catching it unchanged -- and the line against
    `panel_neutralization.NeutralizationEngineError` falls exactly where
    `FactorTransformError`'s against `FactorEngineError` does: computing a neutralisation touches
    no store, so every refusal it can raise is about a *value*, while writing or reading one is a
    store operation whose refusals are a `RuntimeError`.
    """


# --- the declared vocabularies --------------------------------------------------------------------


IndustryLevel = Literal["L1", "L2", "L3"]
"""Which level of the SW taxonomy supplies the dummies, as a closed set rather than free text.

Measured on the live corpus (`domain/industry_classification.py`): SW2021 has **31** L1 nodes,
**134** L2 and **346** L3. On a ~5,500-name market that is roughly 178, 41 and 16 names per group
-- so the level is not a presentational choice, it decides how many groups fall under
`min_industry_members` and therefore how much of the cross section gets a code instead of a
value. Declared rather than fixed at `L1` for that reason; `L1` is what the shipped
`INDUSTRY_AND_SIZE` uses and it says why.
"""

INDUSTRY_LEVELS: Final[frozenset[str]] = frozenset(get_args(IndustryLevel))

INDUSTRY_LEVEL_ORDER: Final[tuple[IndustryLevel, ...]] = get_args(IndustryLevel)
"""The three levels, coarsest first. Restated as a tuple because a set has no order and this one
decides which code a cross-section builder reads off an `IndustryAssignment`."""

MarketCapMeasure = Literal["total_mv", "circ_mv"]
"""Which `daily_basic` column is the size regressor. **Both are populated on every row measured.**

`domain/daily_prices.py` records the probe: `close`, `turnover_rate`, `total_share`,
`float_share`, `total_mv` and `circ_mv` carried a value on **every one of 51,708 rows across 18
sessions spanning 2001..2026**, which is why a null in any of the six is refused rather than
tolerated -- "`total_mv` and `circ_mv` are P3 neutralisation inputs and a null one silently drops
a name from a regression" is that module's own sentence about this module.

**That probe is about nulls and says nothing about how much the two measures differ**, and the
paragraph break is there so the two are not read as one finding. The difference *is* driven, but
only on the synthetic cross section in
`tests/unit/test_factor_neutralization_rules.py::_panel`, whose "circulating" series is a
`Normal(0.7, 0.25)` float ratio the test invents -- no stored `circ_mv` has ever been through it.
On that probe swapping `log(total_mv)` for `log(circ_mv)` moves a residual by 0.0067..0.0591 over
eight seeds, against residuals whose deviation is 1 by construction, so
`test_the_declared_choices_that_do_move_the_residuals_move_them_by_a_reportable_amount` asserts a
floor every seed clears and this docstring quotes no figure.

What is claimed without a probe is the direction, which is arithmetic rather than statistics:
total market cap is the whole company and circulating market cap excludes the restricted shares,
so on a name whose float is a third of its capital they are two different size variables. Neither
is right for every study, which is why this is declared and stored rather than chosen here.
"""

MARKET_CAP_MEASURES: Final[frozenset[str]] = frozenset(get_args(MarketCapMeasure))

MARKET_CAP_MEASURE_ORDER: Final[tuple[MarketCapMeasure, ...]] = get_args(MarketCapMeasure)

MarketCapScale = Literal["level", "log"]
"""Whether the size regressor is the capitalisation or its natural logarithm.

- **`log`** -- the conventional choice, and the reason is the shape of the variable: A-share
  market caps span four orders of magnitude within a single industry, so a level regressor makes
  the fit a statement about the handful of largest names. Defined for every row this contract
  admits, because `IndustryMarketCapCrossSection` refuses a non-positive capitalisation outright.
- **`level`** -- the raw capitalisation, in the units `daily_basic` serves it in (10k CNY).
  Declarable rather than refused, because a study that wants it should be able to say so and have
  the choice recorded; what it must not be is the silent default.

**This is a declared field because it changes the answers; a re-scaling is not a field because it
does not.** The two halves are different kinds of statement and are worded differently on purpose:

- **The scale moves residuals by a reportable amount, and the amount is not a constant.** On the
  synthetic 5,534-name probe, `level` against `log` moves a residual by 0.021..1.222 across the
  eight seeds `test_the_declared_choices_that_do_move_the_residuals_move_them_by_a_reportable_
  amount` sweeps, against residuals whose deviation is 1 by construction -- a 58x spread over an
  arbitrary parameter. So that test asserts a floor every seed clears, and this docstring quotes
  no single figure: one would be a property of the seed.
- **Re-scaling moves nothing that is not floating-point noise**, because a complete dummy set
  already spans the constant. Z-scoring `log(total_mv)` and `1000 * x + 7` both move a residual by
  **4.44e-16**, three ulps. That figure is *not* a uniform bound over affine maps -- `1e-6 * x -
  3` measures 8.4e-12 on the same probe, because shrinking the regressor by six orders costs six
  orders of relative precision in the subtraction. The invariance is of the answer, not of the
  bits. See this module's docstring for the algebra.
"""

MARKET_CAP_SCALES: Final[frozenset[str]] = frozenset(get_args(MarketCapScale))

MARKET_CAP_SCALE_ORDER: Final[tuple[MarketCapScale, ...]] = get_args(MarketCapScale)

ParticipationRule = Literal["measured_only", "measured_and_imputed"]
"""Whether a value the missing-value policy supplied enters the regression.

`ProcessedFactorPanel.measured_values()` and `.values()` are the two readings and this is where
one of them is chosen, once, in a field that is stored and hashed:

- **`measured_only`** -- only `processed` rows. The reading `V2-P3-005`'s information coefficient
  wants: an imputed cross-sectional median is a number this repository made up, and a statistic
  that consumed it would be measuring the fill rate as much as the factor.
- **`measured_and_imputed`** -- `processed` and `imputed`. Defensible when the fill rate is low
  and the alternative is a regression whose sample moves with it.

The rule decides which securities the group means and the slope are estimated *from*, so it moves
every residual in the cross section rather than only the imputed rows' own.
"""

PARTICIPATION_RULES: Final[frozenset[str]] = frozenset(get_args(ParticipationRule))

PARTICIPATION_RULE_ORDER: Final[tuple[ParticipationRule, ...]] = get_args(ParticipationRule)

PARTICIPATING_PROCESSED_CODES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "measured_only": frozenset({"processed"}),
        "measured_and_imputed": frozenset({"processed", "imputed"}),
    }
)
"""Which processed coverage codes each rule admits into the regression.

Keyed by `str` rather than by `ParticipationRule`, and that widening is deliberate for
`panel_factors._refuse_transform_table_drift`'s measured reason: `Mapping` is invariant in its
key type, so a table annotated with the `Literal` cannot be passed to an audit whose parameter is
keyed by `str` -- and the audit *must* take `str`, or the failure direction it exists to catch (a
key that is not a declared rule) would be unwritable in a test.

A table rather than two `if` branches so that
`_refuse_a_participation_table_that_cannot_answer_every_valued_processed_code` has something to
reconcile against `PROCESSED_VALUE_CODES` at import. The check with teeth is the **union**: a
sixth processed code that carried a value would grow `PROCESSED_VALUE_CODES` and no longer be
covered here, so this module refuses to import rather than dropping every row carrying it from
every regression -- silently, with no census column able to say so.
"""

NeutralizedCoverage = Literal[
    "neutralized",
    "not_a_participant",
    "industry_missing",
    "market_cap_missing",
    "thin_industry",
    "insufficient_cross_section",
    "degenerate_design",
]
"""Whether this security has a residual at this `as_of`, and if not, **why**.

Read in the order `apply_factor_neutralization` decides them, which is not the order they are
declared in -- the two whole-panel codes are decided for the entire cross section at once:

- **`insufficient_cross_section`** -- fewer eligible participants than
  `FactorNeutralizationSpec.min_cross_section`. Whole-panel, `FactorTransformSpec`'s own floor
  one plane up, and a *separate* number from it because the eligible set here is strictly
  narrower: a security needs a processed value **and** an industry **and** a market cap.
- **`degenerate_design`** -- there were enough participants and the market-cap regressor has no
  dispersion left after the industry means are removed, so the slope is `0 / 0`. Whole-panel, for
  `degenerate_cross_section`'s reason: the alternative is to silently fit `beta = 0` and store an
  industry-only residual under a code that says industry **and** size were removed.
- **`not_a_participant`** -- the processed row carried no value at all, or carried one the
  declared `participation` rule excludes. `source_coverage` says which, so this code does not
  collapse the distinction the processed vocabulary spent five members drawing.
- **`industry_missing`** -- the security had a value and the industry cross section has no
  assignment covering this day. Real and permanent: 2.95% of the 2015 listed market.
- **`market_cap_missing`** -- value and industry present, and the size cross section has no
  capitalisation for it. `daily_basic` omits Beijing-board names on historical sessions (60 of
  3,843 on 2020-03-02, all `.BJ`), which `load_daily_valuations` already documents as data rather
  than a fault.
- **`thin_industry`** -- everything present, and the security's industry group holds fewer
  participants than `min_industry_members`. See this module's docstring for why a singleton's
  residual is exactly `0.0` and therefore must not be stored as one.
- **`neutralized`** -- the only code under which a stored number is a residual.

Closed, because `V2-P3-014`'s three-tier report groups by it and a sixth spelling of "no answer"
would silently become a group of one.
"""

NEUTRALIZED_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(NeutralizedCoverage))

NEUTRALIZED_COVERAGE_ORDER: Final[tuple[NeutralizedCoverage, ...]] = get_args(NeutralizedCoverage)
"""The declared order, which is also the census key order and the stored column order."""

ELIGIBILITY_CODES: Final[frozenset[str]] = frozenset(
    {"industry_missing", "market_cap_missing", "thin_industry"}
)
"""The three codes that say "the factor value was there and something else was not".

Named as data because two rules read it -- `validate_neutralized_factor_observation` refuses one
of these over a source that carried no value, and the engine decides them in this set's own
order of elimination -- and because a membership test written out twice is two things that drift.
They are the codes a coverage report reads to answer "how much of the market could this build
*not* neutralise, and which of the two inputs was missing", which is the question
`IndustryCoverageReport` answers one plane down and which a single `not_a_participant` would
collapse.
"""

NEUTRALIZED_VALUE_CODES: Final[frozenset[str]] = frozenset({"neutralized"})
"""The one code that carries a value.

`ProcessedCoverage` has two because an imputation is a number; this plane has one because it
imputes nothing -- a security that cannot be regressed gets a code, never a substitute residual.
The set is a `frozenset` rather than a bare comparison so that
`validate_neutralized_factor_observation` reads the same way as its processed twin.
"""

MAX_NEUTRALIZATION_KEY_LENGTH: Final[int] = 40
"""How long a neutralisation key may be, and -- like `MAX_TRANSFORM_KEY_LENGTH` -- this is **not**
a panel-dataset-name budget.

Same arithmetic, one plane further down. The factor is the partition axis
(`factor_neut_<key>_v<n>`); a neutralisation is a *column* in that partition
(`neutralization_id` / `neutralization_key` / `neutralization_version`) and could not be an axis:
the shortest honest prefix plus a 40-character factor key plus `_v999` plus a separator plus a
key of its own plus `_v999` needs at least `6 + 40 + 5 + 1 + 5 = 57` characters before that key
gets a single letter. `panel_neutralization` states the consequence (one partition per factor
holds every neutralisation of it) with the read and write costs that follow.

40 characters is the same room a factor key and a transform key each get, and the value is
validated as a panel identifier for their reason: it is stored as a column value that a later
query filters on, and `qualified_key` splits on `/`.
"""


def _refuse_a_participation_table_that_cannot_answer_every_valued_processed_code(
    table: Mapping[str, frozenset[str]], rules: Sequence[str]
) -> None:
    """Refuse this module at import if a processed code carrying a value has no rule that admits it.

    The direction a per-rule test cannot reach, and `domain/factor_transform.py`'s
    `_refuse_a_policy_that_cannot_answer_every_missing_code` pointed at the neighbouring
    vocabulary. `ProcessedCoverage` declares five codes and exactly two of them carry a number;
    both are named here. A sixth code that carried one -- which is what a later smoothing or
    shrinkage stage would add -- would arrive with **no rule naming it**, and every regression in
    this repository would quietly stop seeing those securities: no exception, no census column,
    and a cross section narrower than the market with nothing on the partition to say so. That is
    `domain/daily_prices.py`'s stated reason for refusing a null `total_mv`, applied to a
    *vocabulary* gaining a member rather than to a cell going empty.

    Three checks rather than one, because they fail differently and a reader has to know which: a
    rule with no entry, an entry naming a code the processed vocabulary does not declare, and a
    valued processed code no rule admits.

    Takes the **table and the rule list** as arguments rather than reading this module's own
    `PARTICIPATING_PROCESSED_CODES` and `PARTICIPATION_RULE_ORDER`, which is what makes all three
    failure directions drivable from a test. It is *not* free of globals and the docstring used to
    imply it was: the two vocabularies it reconciles against -- `PROCESSED_COVERAGE_CODES` and
    `PROCESSED_VALUE_CODES` -- are read straight off `domain/factor_transform.py`, deliberately, so
    that the audit compares against the neighbouring plane's live declaration rather than against
    a copy a caller passed in. All three failures are still reachable through the `table`
    parameter alone, which is the property that matters: an audit whose only call site is the one
    that passes is an audit nobody has seen fail.
    """
    declared = set(rules)
    if set(table) != declared:
        raise FactorNeutralizationError(
            f"the participation rules are {sorted(declared)} and the table names "
            f"{sorted(table)}; a rule with no entry raises KeyError at the first cross section "
            "that declares it, in production, with a message naming neither the rule nor the spec"
        )
    named = {code for codes in table.values() for code in codes}
    unknown = sorted(named - set(PROCESSED_COVERAGE_CODES))
    if unknown:
        raise FactorNeutralizationError(
            f"the participation table admits {unknown}, which the processed coverage vocabulary "
            f"does not declare ({sorted(PROCESSED_COVERAGE_CODES)}); a rule that names a code no "
            "processed row can carry is a rule that admits nothing"
        )
    uncovered = sorted(set(PROCESSED_VALUE_CODES) - named)
    if uncovered:
        raise FactorNeutralizationError(
            f"{uncovered} carry a processed value and no participation rule admits them; every "
            "processed code that holds a number needs a rule that can put it in a regression, "
            "because a code no rule names is a security dropped from every cross section with no "
            "census column able to report it"
        )


_refuse_a_participation_table_that_cannot_answer_every_valued_processed_code(
    PARTICIPATING_PROCESSED_CODES, PARTICIPATION_RULE_ORDER
)


# --- the declared spec ----------------------------------------------------------------------------


class FactorNeutralizationSpec(BaseModel):
    """One versioned neutralisation: four declared choices, two floors, and nothing unhashable.

    D8's "显式版本化" for the fourth transform. `neutralization_id` is
    `stable_model_id(prefix="fnz")` over every field below, so two builds that regressed against
    a different industry level, a different capitalisation, a different scale, a different sample
    **or** under different floors cannot share an identity -- and a build that used the same ones
    reproduces it.

    `version` is the human-facing half and is bumped when the *meaning* changes. It is not
    redundant with `neutralization_id`: a content address is stable and opaque, and a reader
    holding two partitions needs to know which of two neutralisations came later, which a hash
    cannot say.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-neutralization/v1"] = "factor-neutralization/v1"
    key: str = Field(min_length=1, max_length=MAX_NEUTRALIZATION_KEY_LENGTH)
    version: int = Field(ge=1, le=999)
    industry_level: IndustryLevel
    market_cap_measure: MarketCapMeasure
    market_cap_scale: MarketCapScale
    participation: ParticipationRule
    min_industry_members: int = Field(ge=2, le=1000)
    """The fewest participants an industry needs before its members get a residual.

    Mandatory, with no default, and **the lower bound is 2 rather than 1, which is the one bound
    in this contract that refuses a declarable configuration.** `min_cross_section` deliberately
    admits `1` and says "the contract's job is to record which was chosen, not to choose"; this
    one does not, and the asymmetry is the point.

    A one-member group's residual is not a number that happens to be small. It is **exactly
    `0.0`**, for every factor, every market cap and every slope: the security is its own group
    mean in both `y` and `x`, so `y - mean_y` and `x - mean_x` are both zero and the residual is
    `0 - beta * 0`. Storing it under `neutralized` would put a structural constant in the column
    `V2-P3-005` computes a rank correlation on and `V2-P3-014` ranks, indistinguishable from a
    security that genuinely sits at its industry's centre. That is the same failure the processed
    plane spends a second coverage code (`imputed`) avoiding, and the cheaper fix here is to make
    the degenerate case undeclarable.

    Refusing it costs the rest of the cross section nothing, which is measured rather than
    assumed: a singleton contributes `0 * 0` to the slope's numerator and `0` to its denominator,
    so the slope is **bit-identical** with and without it and every other security's residual
    moves by exactly `0`. `tests/unit/test_factor_neutralization_rules.py::
    test_a_one_member_industry_has_a_residual_of_exactly_zero_and_moves_no_other` holds both
    halves.

    Above 2 is an ordinary declared judgement: a five-name industry estimates a mean from five
    observations, and a study that wants a floor there says so and has it stored. The upper bound
    is 1000 -- a range check on a stored integer, above SW2021's largest measured L1 (`801890.SI`
    机械设备, 625 current members plus 229 superseded) so that a floor no industry can meet is
    declarable and produces `thin_industry` for everybody rather than being refused at
    declaration time, which is `min_cross_section`'s own argument for admitting a vacuous floor.
    """
    min_cross_section: int = Field(ge=1, le=10000)
    """The fewest **eligible** participants for which this neutralisation produces values at all.

    Mandatory, with no default, and not redundant with `FactorTransformSpec.min_cross_section`
    even when the two carry the same number: that floor is counted over the securities with a
    *processed* value, and this one over the securities that have a processed value **and** an
    industry assignment on the day **and** a market capitalisation. On the 2015 market the second
    set is measurably smaller -- 2,694 of 2,776 listed names were classified (97.05%) -- so a
    transform that cleared its floor can still hand a neutralisation a cross section that does
    not clear this one, and the whole-panel `insufficient_cross_section` is the answer rather than
    an exception, for `apply_factor_transform`'s reason: a thin cross section at some historical
    `as_of` is an answer about the market, and a build that raised on it could not backfill a
    year that contains one.

    The upper bound is 10,000 for `FactorTransformSpec.min_cross_section`'s stated reason and
    with its stated limits: it is a range check on a stored integer and **not** a vacuity guard,
    since 10,000 sits above the ~5,500-name whole-market cross section ADR-0002 sizes the panel
    plane against.
    """

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        try:
            validate_panel_identifier(value, role="neutralization key")
        except PanelBatchError as error:
            raise ValueError(str(error)) from error
        return value

    @model_validator(mode="after")
    def refuse_a_floor_no_industry_can_leave_room_under(self) -> Self:
        """Refuse `min_industry_members` above `min_cross_section`.

        Not tidiness. Both floors are counted over the same eligible securities, and with
        `min_industry_members > min_cross_section` the two can contradict each other on a real
        cross section: one sitting exactly on `min_cross_section` has fewer members in total than
        the per-industry floor demands, so **no** industry in it can be admissible. The build then
        clears its whole-panel floor, reports that many participants, and codes every single one
        of them `thin_industry` -- a manifest saying "enough names" over a partition with no
        values in it. Refusing the spec is cheaper than explaining that row.

        The implication runs one way only, which is why this is a bound and not an equality:
        `min_industry_members <= min_cross_section` does **not** promise that some industry will
        clear the member floor (a wide, finely split cross section can still code everybody), and
        no declaration could promise that, because it is a fact about the market on the day. What
        the bound removes is the configuration where the contradiction is guaranteed in advance.
        """
        if self.min_industry_members > self.min_cross_section:
            raise ValueError(
                f"min_industry_members {self.min_industry_members} exceeds min_cross_section "
                f"{self.min_cross_section}; both are counted over the eligible participants, so a "
                "cross section that just clears the whole-panel floor could not hold one "
                "admissible industry -- the build would report enough participants and give every "
                "one of them thin_industry"
            )
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def neutralization_id(self) -> str:
        """The content address of this neutralisation: every declared field, canonically hashed."""
        return stable_model_id(prefix="fnz", model=self)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def qualified_key(self) -> str:
        """`"industry_and_size/v1"` -- the human handle a CLI or a report shows."""
        return f"{self.key}/v{self.version}"

    def admits(self, coverage: ProcessedCoverage) -> bool:
        """Whether a processed row under `coverage` enters this build's regression.

        Resolved through `PARTICIPATING_PROCESSED_CODES` rather than through a `match` over two
        branches, so the import-time audit is the only place the set of codes is enumerated --
        `MissingValuePolicy.action_for`'s argument, and for its reason: a branch table beside a
        declared table is a second copy of a closed set, and two copies drift.
        """
        if coverage not in PROCESSED_COVERAGE_CODES:
            raise FactorNeutralizationError(
                f"{coverage!r} is not a declared processed coverage code; expected one of "
                f"{sorted(PROCESSED_COVERAGE_CODES)}"
            )
        return coverage in PARTICIPATING_PROCESSED_CODES[self.participation]


@dataclass(frozen=True, slots=True)
class FactorNeutralizationRegistry:
    """Every neutralisation this build knows, refusing the two shapes `FactorRegistry` refuses.

    A frozen tuple rather than a decorator-populated dict, for `FactorRegistry`'s reason: a
    registry populated by import side effects has a content that depends on which modules
    happened to be imported, and every "for each declared neutralisation" audit would then be
    asking a question whose answer changes with import order.
    """

    specs: tuple[FactorNeutralizationSpec, ...]
    notes: tuple[FactorNote, ...] = ()
    """The prose about these specs, out of every content address. See `factor.FactorNote`."""

    def __post_init__(self) -> None:
        if not self.specs:
            raise FactorNeutralizationError(
                "a neutralisation registry must declare at least one spec; an empty one satisfies "
                "every per-spec check vacuously"
            )
        keys = [item.qualified_key for item in self.specs]
        if len(set(keys)) != len(keys):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise FactorNeutralizationError(
                f"{duplicates} is declared more than once; two specs answering to one name make "
                "a lookup arbitrary -- bump `version` on the restatement"
            )
        validate_notes(
            self.notes,
            declared=tuple(keys),
            role="neutralisation",
            error=FactorNeutralizationError,
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
    def neutralization_ids(self) -> tuple[str, ...]:
        return tuple(item.neutralization_id for item in self.specs)

    def get(self, qualified_key: str) -> FactorNeutralizationSpec:
        for item in self.specs:
            if item.qualified_key == qualified_key:
                return item
        raise FactorNeutralizationError(
            f"{qualified_key!r} is not a declared neutralisation; this build knows "
            f"{list(self.qualified_keys)}"
        )

    def by_id(self, neutralization_id: str) -> FactorNeutralizationSpec:
        """The direction a stored neutralised row needs: a column carries the id alone."""
        for item in self.specs:
            if item.neutralization_id == neutralization_id:
                return item
        raise FactorNeutralizationError(
            f"{neutralization_id!r} is not a neutralisation this build declares; it knows "
            f"{list(self.neutralization_ids)}"
        )


# --- the second cross section: what a security *is*, rather than what it scored -------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SecurityCharacteristic:
    """One security's industry and market capitalisation on one day.

    A plain carrier with no validation of its own, `IndustryAssignment`'s precedent: a nominal
    type is not a boundary, so the rules live once, in `build_industry_market_cap_cross_section`.

    `is_backfilled` is `IndustryAnswer.is_backfilled` carried rather than recomputed, and it is
    here for `IndexWeights.as_published_on`'s reason: `index_member_all` expresses the entire
    history in a taxonomy that came into force 2021-12-13, so an answer for an earlier day is a
    label the classification did not have then. Losing that caveat has to be an act rather than
    an omission, and the act this contract permits is reading a count off the manifest.
    """

    subject: str
    industry_code: str
    market_cap: float
    is_backfilled: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class IndustryMarketCapCrossSection:
    """The industry and size cross section a neutralisation regresses against, as a **value**.

    Build it with `build_industry_market_cap_cross_section`; this constructor is not a boundary
    and validates nothing.

    ## What this type is for, and exactly how strong its point-in-time claim is

    `apply_factor_transform` takes a `FactorPanel` and no store, and `V2-P3-003` argued that this
    is what makes its point-in-time property *structural* rather than promised: it cannot read a
    row that was not knowable because it cannot read a row. A neutralisation needs two foreign
    panel datasets, so the obvious implementation gives the engine a `PanelStore` and re-opens
    every visibility question the factor engine settled.

    That is not what happens here, and the substitute is stated at the strength it has:

    - **The engine still takes no store.** `apply_factor_neutralization`'s parameters are a
      `ProcessedFactorPanel`, a spec, this value, and two provenance arguments; there is no
      `PanelStore`, no `as_of`, no universe and no `ReadinessRequirement`, and
      `test_the_neutralisation_takes_no_store_and_therefore_no_second_visibility_rule` reads the
      signature and its annotations rather than trusting this paragraph.
    - **This value is stamped with the instant it was read at**, and the engine refuses one whose
      `as_of` is not the panel's own. So a cross section assembled at a *later* `as_of` cannot be
      handed to an earlier panel by accident -- the mismatch is a refusal, not a silent join.
    - **Its content is in the identity.** `characteristic_digest` hashes the taxonomy, the level,
      the measure and every `(subject, industry, cap, backfilled)` tuple, so
      `neutralization_manifest_id` moves when the industries or the capitalisations move and not
      merely when the parameters do. That is `observation_digest`'s lesson, which
      `FactorBuildManifest` had to learn twice.

    **What is *not* claimed.** A caller can construct this by hand, stamp it with the right
    `as_of` and populate it from rows that were not knowable then -- exactly as a caller can
    hand-assemble a `FactorPanel` whose observations were never computed. Nothing at this layer
    can tell the two apart, because at this layer there is no store to re-derive them from. The
    obstacle is one layer up and it is the ordinary one:
    `panel_neutralization.load_industry_market_cap_cross_section` is the *only* builder in `src/`,
    it reads both datasets through `PanelStore.read_if_ready` -- the unfiltered, fail-closed door,
    which refuses a partition whose newest row post-dates `as_of` rather than filtering it -- and
    `tests/unit/panel/test_visible_read_callers.py` is why a second builder taking the filtered
    door would be a reviewed act. So the neutralisation opens **no new visibility door at all**;
    it consumes the two that `V2-P1-010` and `V2-P1-002` already built, at their strictest
    setting.
    """

    as_of: datetime
    taxonomy: str
    industry_level: IndustryLevel
    market_cap_measure: MarketCapMeasure
    characteristics: tuple[SecurityCharacteristic, ...]
    """The securities that have **both** inputs. Ascending by subject, each appearing once."""
    without_industry: tuple[str, ...]
    """The securities the builder asked about and found no industry assignment for, ascending.

    **Carried rather than dropped, and that is the whole reason this type has three collections
    instead of one.** A builder that returned only the complete rows would leave the engine unable
    to tell "no industry" from "no market cap" from "never asked about", and the first two are
    separate declared codes because they are separate facts about separate datasets -- the
    industry residue is 2.95% of the 2015 listed market and permanent, while a missing
    capitalisation is `daily_basic` omitting Beijing-board names on historical sessions. One code
    for both would report a classification problem as a price-feed one on every affected name.
    """
    without_market_cap: tuple[str, ...]
    """The securities that had an industry and no capitalisation, ascending. See above."""

    def subjects(self) -> tuple[str, ...]:
        """Every security this cross section answers for at all, in any of the three senses."""
        return tuple(
            sorted(
                [item.subject for item in self.characteristics]
                + list(self.without_industry)
                + list(self.without_market_cap)
            )
        )

    def get(self, subject: str) -> SecurityCharacteristic | None:
        """This security's complete characteristic, or `None` when it has no such row.

        `None` rather than a refusal, because a security with no industry or no market cap is the
        ordinary residue this plane exists to *count* and forcing it through an exception would
        make the common case an error. `without_industry` and `without_market_cap` are how a
        caller turns that `None` into the right one of two codes; the engine refuses a subject
        that is in none of the three, which is a different fault entirely.
        """
        for item in self.characteristics:
            if item.subject == subject:
                return item
        return None

    @property
    def backfilled_count(self) -> int:
        """How many complete answers here predate the taxonomy that labels them."""
        return sum(1 for item in self.characteristics if item.is_backfilled)


def build_industry_market_cap_cross_section(
    *,
    as_of: datetime,
    taxonomy: str,
    industry_level: IndustryLevel,
    market_cap_measure: MarketCapMeasure,
    characteristics: Collection[SecurityCharacteristic],
    without_industry: Collection[str] = (),
    without_market_cap: Collection[str] = (),
) -> IndustryMarketCapCrossSection:
    """Assemble the second cross section, in any order, refusing rather than repairing.

    Seven refusals, and the three that are not bookkeeping are stated:

    - **A non-positive market capitalisation is refused outright** rather than coded. A listed
      company's capitalisation is a positive number by construction, `domain/daily_prices.py`
      measured `total_mv` and `circ_mv` populated on all 51,708 rows of its 18-session probe, and
      that module's own sentence about this one is that a null "silently drops a name from a
      regression". A zero or a negative is the same fault wearing a number, and it is the one
      input that would make `market_cap_scale="log"` undefined -- refusing it here is what lets
      the `log` branch have no special case at all.
    - **A duplicated subject is refused** rather than de-duplicated, `observation_digest`'s rule:
      two answers to one question would give two different cross sections one content address,
      and `get` would return whichever came first.
    - **A subject appearing in two of the three collections is refused.** "This name has an
      industry and no cap" and "this name has no industry" are each other's contradiction on the
      first clause, and the engine decides a security's code by asking the three in order -- so
      the overlap would resolve silently to whichever it asked first.

    The two residue collections default to empty rather than being mandatory, and that default is
    a deliberate asymmetry with the rest of this repository's "no defaults" rule: an empty residue
    is the *only* honest reading of "these are the securities I have complete data for" when a
    caller builds a probe cross section by hand, whereas a `None` would make every caller state a
    fact about names it never asked about. The store-side builder always passes both explicitly,
    and the engine's own guard is what makes an under-covering cross section a refusal rather than
    a silent narrowing.
    """
    ensure_aware(as_of)
    if not taxonomy or taxonomy != taxonomy.strip():
        raise FactorNeutralizationError(
            f"taxonomy must be a non-empty string without surrounding whitespace; got {taxonomy!r}"
        )
    if industry_level not in INDUSTRY_LEVELS:
        raise FactorNeutralizationError(
            f"{industry_level!r} is not a declared industry level; expected one of "
            f"{sorted(INDUSTRY_LEVELS)}"
        )
    if market_cap_measure not in MARKET_CAP_MEASURES:
        raise FactorNeutralizationError(
            f"{market_cap_measure!r} is not a declared market cap measure; expected one of "
            f"{sorted(MARKET_CAP_MEASURES)}"
        )
    by_subject: dict[str, SecurityCharacteristic] = {}
    for item in characteristics:
        _require_subject(item.subject)
        if not item.industry_code or item.industry_code != item.industry_code.strip():
            raise FactorNeutralizationError(
                f"{item.subject}'s industry_code must be a non-empty string without surrounding "
                f"whitespace; got {item.industry_code!r}. A security with no industry belongs in "
                "without_industry, where the engine codes it industry_missing"
            )
        if not math.isfinite(item.market_cap) or item.market_cap <= 0.0:
            raise FactorNeutralizationError(
                f"{item.subject} carries market_cap {item.market_cap!r}; a listed company's "
                "capitalisation is a finite positive number, and daily_basic served total_mv and "
                "circ_mv on every one of the 51,708 rows domain/daily_prices.py probed. A "
                "non-positive one is a fault wearing a number, and it is the only input under "
                "which the declared log scale would have no value"
            )
        if item.subject in by_subject:
            raise FactorNeutralizationError(
                f"{item.subject} appears more than once in this cross section; two answers to one "
                "question would give two different cross sections one content address, and the "
                "lookup would silently return whichever came first"
            )
        by_subject[item.subject] = item
    residues: dict[str, tuple[str, ...]] = {}
    for name, group in (
        ("without_industry", without_industry),
        ("without_market_cap", without_market_cap),
    ):
        ordered = sorted(group)
        for subject in ordered:
            _require_subject(subject)
        if len(set(ordered)) != len(ordered):
            duplicated = sorted({code for code in ordered if ordered.count(code) > 1})
            raise FactorNeutralizationError(
                f"{duplicated} appear more than once in {name}; a residue is a set of securities "
                "and a repeated one would be counted twice in a census"
            )
        residues[name] = tuple(ordered)
    overlapping = sorted(
        (set(by_subject) & set(residues["without_industry"]))
        | (set(by_subject) & set(residues["without_market_cap"]))
        | (set(residues["without_industry"]) & set(residues["without_market_cap"]))
    )
    if overlapping:
        raise FactorNeutralizationError(
            f"{overlapping} appear in more than one of this cross section's three collections; "
            "having both inputs, having no industry and having no capitalisation are mutually "
            "exclusive statements, and the engine decides a security's coverage code by asking "
            "the three in order -- so an overlap resolves to whichever it asks first"
        )
    return IndustryMarketCapCrossSection(
        as_of=ensure_aware(as_of),
        taxonomy=taxonomy,
        industry_level=industry_level,
        market_cap_measure=market_cap_measure,
        characteristics=tuple(by_subject[key] for key in sorted(by_subject)),
        without_industry=residues["without_industry"],
        without_market_cap=residues["without_market_cap"],
    )


def _require_subject(value: str) -> None:
    if not value or value != value.strip():
        raise FactorNeutralizationError(
            "a cross section names its securities as non-empty strings without surrounding "
            f"whitespace; got {value!r}"
        )


def characteristic_digest(cross_section: IndustryMarketCapCrossSection) -> str:
    """A content address for the industry and size cross section a build consumed.

    `observation_digest`'s twin on the second input, and it exists for that function's exact
    reason: without it, `neutralization_manifest_id` would be blind to the very industries and
    capitalisations that decided the residuals, and the determinant audit would be exempting the
    `characteristics` argument on a promise instead of measuring it.

    **The taxonomy and the level are hashed with the tuples rather than being manifest fields**,
    and that placement is a claim. A vintage label is a property of the *data* (which
    classification these codes belong to) and not of the build's parameters, so a manifest field
    for it would be a second statement of the same fact that could disagree with the digest --
    and `index_member_all` speaks exactly one vintage today, which would make a separate hashed
    field a constant that reaches the identity and decides nothing. The measure is hashed too,
    because a cross section carrying `total_mv` and one carrying `circ_mv` are the same *shape*
    holding two different size variables, and a caller who assembled the wrong one would otherwise
    reproduce an identity the numbers do not belong to.

    The same canonicalisation `stable_model_id`, `set_digest` and `observation_digest` use --
    `json.dumps` with fixed separators, `ensure_ascii=False` and `allow_nan=False`.
    `allow_nan=False` is inherited rather than load-bearing here: a non-finite capitalisation
    cannot reach this function, because `build_industry_market_cap_cross_section` refuses one, and
    the reachable path is a hand-built `IndustryMarketCapCrossSection` -- so the refusal is
    translated rather than propagated, and names the subjects that carry it.
    """
    subjects = [
        *(item.subject for item in cross_section.characteristics),
        *cross_section.without_industry,
        *cross_section.without_market_cap,
    ]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise FactorNeutralizationError(
            f"{duplicates} appears more than once in this cross section; a duplicated security is "
            "two answers to one question, and a digest that hashed both would give two different "
            "cross sections one address"
        )
    payload: list[object] = [
        cross_section.taxonomy,
        cross_section.industry_level,
        cross_section.market_cap_measure,
        sorted(
            [item.subject, item.industry_code, item.market_cap, item.is_backfilled]
            for item in cross_section.characteristics
        ),
        sorted(cross_section.without_industry),
        sorted(cross_section.without_market_cap),
    ]
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except ValueError as error:
        offending = sorted(
            item.subject
            for item in cross_section.characteristics
            if not math.isfinite(item.market_cap)
        )
        raise FactorNeutralizationError(
            f"{offending} carry a non-finite market capitalisation (nan or an infinity), so this "
            "cross section has no content address: the canonical form this digest hashes refuses "
            "one, and hashing a substitute would mint an address for a cross section nobody can "
            "reproduce. build_industry_market_cap_cross_section refuses such a row, so this one "
            "was assembled by constructing IndustryMarketCapCrossSection directly"
        ) from error
    return f"chr_{sha256(canonical).hexdigest()[:24]}"


def processed_observation_digest(observations: Sequence[ProcessedFactorObservation]) -> str:
    """A content address for the processed cross section a neutralisation consumed.

    `observation_digest`'s twin one tier down, and it exists for that function's stated reason
    rather than for a new one: a `transform_manifest_id` identifies a transform's *inputs and
    parameters*, so two `ProcessedFactorPanel`s carrying one manifest and different values are
    constructible -- `apply_factor_transform` will not produce them and `ProcessedFactorPanel` is
    a public frozen dataclass that can. Without this field, this build's identity would be blind
    to the very numbers it neutralised, and the determinant audit in
    `tests/integration/panel/test_factor_neutralizations.py` would be exempting the `panel`
    argument on a promise instead of measuring it.

    The `(subject, coverage, value)` triple rather than the whole row, matching
    `observation_digest` exactly: `transform_id`, `transform_manifest_id` and the two `source_*`
    columns are the *same* on every row of one panel by a guard, so hashing them per row would
    add 5,534 copies of one constant to the payload and nothing to the address.
    """
    subjects = [item.subject for item in observations]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise FactorNeutralizationError(
            f"{duplicates} appears more than once in the processed panel; a duplicated security "
            "is two answers to one question, and a digest that hashed both would give two "
            "different cross sections one address"
        )
    try:
        return cross_section_digest(
            ((item.subject, item.coverage, item.value) for item in observations), prefix="prc"
        )
    except FactorError as error:
        offending = sorted(
            item.subject
            for item in observations
            if item.value is not None and not math.isfinite(item.value)
        )
        raise FactorNeutralizationError(
            f"{offending} carry a non-finite processed value in the source cross section, so it "
            "has no content address: the canonical form this digest hashes refuses one, and "
            "hashing a substitute would mint an address for a cross section nobody can "
            "reproduce. validate_processed_factor_observation refuses such a row at both of its "
            "call sites, so it reached this panel through a subclass that overrode __post_init__"
        ) from error


# --- what one neutralisation was made of ---------------------------------------------------------


class FactorNeutralizationManifest(BaseModel):
    """What one application of a neutralisation was made of, as a content address.

    `FactorTransformManifest`'s discipline one plane further down, with both of its measured
    lessons already applied rather than re-learned:

    - **Every declared field enters `neutralization_manifest_id`**, and
      `tests/unit/domain/test_factor_neutralization.py` varies each one alone and asserts it moves.
    - **`built_at` is deliberately not a field.** Re-applying the same neutralisation to the same
      processed build must reproduce the ID, or the identity cannot detect drift and a rebuild can
      never be written past `_refuse_to_drop_a_stored_build`. The wall clock is recorded as the
      partition's `fetched_at`.
    - **Both inputs are digested, not merely named.** `source_processed_digest` addresses the
      processed cross section and `characteristic_digest` addresses the industry-and-size one; a
      manifest identifies a computation's *declared* inputs, so two panels carrying one
      `transform_manifest_id` and different numbers are constructible and this build's answers
      would otherwise share an identity with theirs.

    **`date_timezone` is deliberately absent**, `FactorTransformManifest`'s claim unchanged: this
    build resolves no date. It reads values, codes, industries and capitalisations off records
    that already carry an aware `as_of`, and the only timezone in its life is the one
    `write_neutralized_factor_panels` uses to decide a partition **year**.

    **`industry_taxonomy` is deliberately absent too, and that absence is a different claim.** The
    vintage is hashed -- inside `characteristic_digest`, with the codes it labels -- rather than
    being a field here, because it is a property of the data rather than of the declaration, and a
    second statement of one fact is a second thing that can disagree with the first. It is stored
    as a manifest *column* for a reader of the partition, exactly as the policy columns are.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-neutralization-manifest/v1"] = (
        "factor-neutralization-manifest/v1"
    )
    neutralization_id: str = Field(min_length=1, max_length=64)
    neutralization_key: str = Field(min_length=1, max_length=MAX_NEUTRALIZATION_KEY_LENGTH)
    neutralization_version: int = Field(ge=1, le=999)
    source_factor_id: str = Field(min_length=1, max_length=64)
    source_factor_key: str = Field(min_length=1, max_length=64)
    source_factor_version: int = Field(ge=1, le=999)
    source_transform_id: str = Field(min_length=1, max_length=64)
    source_transform_manifest_id: str = Field(min_length=1, max_length=64)
    """The `FactorTransformManifest.transform_manifest_id` of the processed build consumed.

    With a row's own `subject` and `as_of` it is the exact key of a row in
    `factor_proc_<key>_v<n>`, which is the join
    `tests/integration/panel/test_factor_neutralizations.py` performs rather than describes -- and
    it is one link of a chain, since that processed row carries `source_manifest_id` pointing at
    the raw observation in turn. Three tiers, two pointers, no copies.
    """
    source_processed_digest: str = Field(min_length=1, max_length=64)
    characteristic_digest: str = Field(min_length=1, max_length=64)
    neutralized_observation_digest: str = Field(min_length=1, max_length=64)
    """`neutralized_observation_digest` of the residuals this application produced.

    `FactorBuildManifest.observation_digest` and
    `FactorTransformManifest.processed_observation_digest`, at the top of the chain. This contract
    already addressed **both** of its inputs and, like the two below it, said nothing about its
    output -- and here that gap was the widest of the three, because a neutralised residual is
    what `openalpha factor run`'s acceptance verdict is computed from and no tier above exists to
    address it by accident.

    Hashed rather than recorded; see `FactorBuildManifest.observation_digest` for why a digest
    outside the identity is a column a tamperer edits in the same pass as the values it describes.
    `panel_neutralization._seal_neutralized_panel` computes it and
    `panel_neutralization.load_neutralized_factor_observations` checks it."""
    as_of: datetime
    code_commit: str = Field(min_length=7, max_length=64)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def neutralization_manifest_id(self) -> str:
        """The content address of this application. See this class's docstring."""
        return stable_model_id(prefix="fnm", model=self)


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorNeutralizationStatistics:
    """The numbers behind the residuals: recorded, stored, **never hashed**.

    `FactorTransformStatistics`' arrangement for its reason -- these are *outputs*, and an
    identity computed from a build's outputs is one nobody can predict before running the build.

    They are nonetheless the difference between a neutralisation that is auditable and one that is
    not, and each of the eight answers a question a reader of the partition would otherwise have
    to re-derive from the values:

    - `participant_count` / `industry_count` -- how wide the regression actually was, and over how
      many groups. A build that neutralised 40 names against 3 industries and one that did 5,000
      against 31 are byte-indistinguishable in the value column.
    - `smallest_industry_size` / `largest_industry_size` -- the group-size distribution's ends,
      which is what makes `min_industry_members` falsifiable on a stored partition: a floor of 2
      that admitted a group of 2 is a different build from one whose smallest group was 180.
    - `backfilled_industry_count` -- how many participants were labelled by a taxonomy that did
      not exist on the day asked about. `IndustryAnswer.is_backfilled`'s caveat, carried to
      storage so that losing it is an act.
    - `market_cap_slope` -- the coefficient the residual removed. Sign and magnitude are the whole
      of what "size neutralised" means on this cross section.
    - `market_cap_dispersion` -- the within-industry sum of squared deviations of the regressor,
      which is the slope's own denominator and therefore the quantity `degenerate_design` tests.
      Stored so that a build sitting near the edge is visible before it falls over it.
    - `residual_dispersion` -- the population standard deviation of the stored residuals. Against
      the processed values' own scale it is how much the neutralisation removed, which is the one
      number a three-tier report needs and cannot get from a census.
    """

    participant_count: int
    industry_count: int
    smallest_industry_size: int
    largest_industry_size: int
    backfilled_industry_count: int
    market_cap_slope: float | None
    market_cap_dispersion: float | None
    residual_dispersion: float | None

    def __post_init__(self) -> None:
        for name in (
            "participant_count",
            "industry_count",
            "smallest_industry_size",
            "largest_industry_size",
            "backfilled_industry_count",
        ):
            if int(getattr(self, name)) < 0:
                raise FactorNeutralizationError(f"{name} cannot be negative")
        if self.smallest_industry_size > self.largest_industry_size:
            raise FactorNeutralizationError(
                f"the smallest industry holds {self.smallest_industry_size} participants and the "
                f"largest {self.largest_industry_size}; the two run backwards"
            )
        if self.largest_industry_size > self.participant_count:
            raise FactorNeutralizationError(
                f"the largest industry holds {self.largest_industry_size} of "
                f"{self.participant_count} participants; a group cannot be wider than the cross "
                "section it partitions"
            )
        if self.backfilled_industry_count > self.participant_count:
            raise FactorNeutralizationError(
                f"{self.backfilled_industry_count} of {self.participant_count} participants are "
                "reported backfilled; a caveat cannot cover more securities than there were"
            )
        if self.industry_count > self.participant_count:
            raise FactorNeutralizationError(
                f"{self.industry_count} industries hold {self.participant_count} participants "
                "between them; a partition cannot have more parts than members"
            )
        for name in ("market_cap_slope", "market_cap_dispersion", "residual_dispersion"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise FactorNeutralizationError(
                    f"{name} is {value!r}, which is not a finite number"
                )
        if self.market_cap_dispersion is not None and self.market_cap_dispersion <= 0.0:
            raise FactorNeutralizationError(
                f"market_cap_dispersion is {self.market_cap_dispersion!r}; a regression whose "
                "slope divided by zero is the degenerate_design code's own case and must not be "
                "recorded as a completed one"
            )
        if self.residual_dispersion is not None and self.residual_dispersion < 0.0:
            raise FactorNeutralizationError(
                f"residual_dispersion is {self.residual_dispersion!r}; a standard deviation is "
                "not negative"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class NeutralizedFactorObservation:
    """One security's residual at one `as_of`, and the processed answer it came from.

    A frozen dataclass rather than a pydantic model for `ProcessedFactorObservation`'s reason: it
    is constructed once per `(security, as_of, neutralisation)` and a whole-market cross section
    is 5,534 of them.

    The invariants are in `validate_neutralized_factor_observation`, which `__post_init__` calls
    and which the write path calls again -- the two-call-site shape `domain/factor.py`,
    `panel/catalog.py` and `domain/factor_transform.py` all argue, and which was *measured* on the
    factor observation: a three-line subclass overriding `__post_init__` put an empty subject, a
    negative row count and a backwards window into a Parquet partition.

    The load-bearing rule is the one that keeps the third tier honest the way the second one is:
    **a `neutralized` row must carry a `source_coverage` its own build's participation rule
    admits.** Without it, `coverage="neutralized", source_coverage="source_not_computed"` is
    constructible and stores a residual for a security that had no factor value at all.
    """

    subject: str
    as_of: datetime
    value: float | None
    coverage: NeutralizedCoverage
    neutralization_id: str
    neutralization_manifest_id: str
    source_factor_id: str
    source_transform_id: str
    source_transform_manifest_id: str
    """The processed build this row's input came from. With `subject` and `as_of`, its exact key."""
    source_coverage: ProcessedCoverage
    """The processed row's own five-code marker, carried rather than joined for.

    Not redundant with the join, `ProcessedFactorObservation.source_coverage`'s argument one plane
    down: a reader of this partition alone can tell a security that had no factor value from one
    whose value was imputed and excluded by the participation rule -- a distinction
    `not_a_participant` would otherwise collapse -- without opening the processed partition at all.
    """
    industry_code: str | None
    """The industry group whose mean was removed, or `None` where none was.

    Stored rather than joined for, and it is the column that makes a stored residual
    *interpretable*: the number in `value` is a deviation from a group, and a reader who cannot
    see which group is holding a residual whose meaning is a join away -- against a dataset whose
    own module docstring spends four paragraphs on the fact that its labels are backfilled.

    `None` for exactly the codes that carry no value, which is what
    `validate_neutralized_factor_observation` enforces: a coded row has no group, because there
    either was no industry, no capitalisation, no admissible group, or no cross section at all.
    """

    def __post_init__(self) -> None:
        # Normalised rather than merely required to be aware, for `FactorObservation`'s measured
        # reason: a stored instant read back out of DuckDB arrives tagged with the session's own
        # timezone rather than UTC, and the same instant under two labels is one dictionary key
        # only after this line.
        object.__setattr__(self, "as_of", ensure_aware(self.as_of))
        validate_neutralized_factor_observation(self)


def validate_neutralized_factor_observation(observation: NeutralizedFactorObservation) -> None:
    """Every rule a stored neutralised observation has to satisfy, as a function with two sites.

    `NeutralizedFactorObservation.__post_init__` is one and
    `panel_neutralization.neutralized_observation_batch` is the other, for the reason
    `validate_processed_factor_observation` has two: a `__post_init__` is a method, a frozen
    dataclass with `slots=True` is still subclassable, and the write boundary is the last place a
    row that skipped the constructor's rules can be stopped before it is a column in a Parquet
    file.
    """
    if not observation.subject:
        raise FactorNeutralizationError("a neutralised observation must name a subject")
    ensure_aware(observation.as_of)
    if observation.coverage not in NEUTRALIZED_COVERAGE_CODES:
        raise FactorNeutralizationError(
            f"{observation.coverage!r} is not a declared neutralised coverage code; expected one "
            f"of {sorted(NEUTRALIZED_COVERAGE_CODES)}"
        )
    if observation.source_coverage not in PROCESSED_COVERAGE_CODES:
        raise FactorNeutralizationError(
            f"{observation.source_coverage!r} is not a declared processed coverage code; expected "
            f"one of {sorted(PROCESSED_COVERAGE_CODES)}"
        )
    carries_value = observation.coverage in NEUTRALIZED_VALUE_CODES
    if (observation.value is None) == carries_value:
        raise FactorNeutralizationError(
            f"{observation.subject} at {observation.as_of.isoformat()} reports coverage "
            f"{observation.coverage!r} with value {observation.value!r}; exactly "
            f"{sorted(NEUTRALIZED_VALUE_CODES)} carries a value, and every other code carries None"
        )
    if observation.value is not None and not math.isfinite(observation.value):
        raise FactorNeutralizationError(
            f"{observation.subject} at {observation.as_of.isoformat()} reports coverage "
            f"{observation.coverage!r} with value {observation.value!r}; a non-finite residual "
            "poisons every downstream mean, rank and correlation built on the column, and no "
            "declared code carries one"
        )
    if (observation.industry_code is None) == carries_value:
        raise FactorNeutralizationError(
            f"{observation.subject} at {observation.as_of.isoformat()} reports coverage "
            f"{observation.coverage!r} with industry_code {observation.industry_code!r}; a "
            "residual is a deviation from a group and must name it, and a row with no residual "
            "was in no group -- there was no industry, no capitalisation, no admissible group, or "
            "no cross section at all"
        )
    from_a_value = observation.source_coverage in PROCESSED_VALUE_CODES
    if observation.coverage == "neutralized" and not from_a_value:
        raise FactorNeutralizationError(
            f"{observation.subject} at {observation.as_of.isoformat()} is stored as 'neutralized' "
            f"with source_coverage {observation.source_coverage!r}; a residual is what is left of "
            "a processed value after a regression, and the only processed codes that carry a "
            f"value are {sorted(PROCESSED_VALUE_CODES)}"
        )
    if observation.coverage in ELIGIBILITY_CODES and not from_a_value:
        raise FactorNeutralizationError(
            f"{observation.subject} at {observation.as_of.isoformat()} is stored as "
            f"{observation.coverage!r} with source_coverage {observation.source_coverage!r}; all "
            "three of those codes say the security had a processed value and something else was "
            "missing, so a source that carried no value contradicts them -- that row belongs "
            "under 'not_a_participant'"
        )
    if observation.industry_code is not None and not observation.industry_code.strip():
        raise FactorNeutralizationError(
            f"{observation.subject} at {observation.as_of.isoformat()} carries industry_code "
            f"{observation.industry_code!r}; a blank group name is not a group"
        )


def neutralized_observation_digest(observations: Sequence[NeutralizedFactorObservation]) -> str:
    """A content address for the residual cross section a neutralisation produced.

    `processed_observation_digest`'s twin one tier further up, and the tier it closes is the top
    one: nothing consumes a neutralised panel inside this repository, so unlike the two below it
    this cross section has no manifest above it that could have addressed it by accident.
    `V2-P3-019` measured what that meant -- `factor_neut_<key>_v<n>` was a Parquet file whose
    residuals could be edited with `neutralization_manifest_id` and the sealed `experiment_id`
    both unmoved, and the neutralised tier is the one `openalpha factor run`'s acceptance verdict
    is *read off*: it is the tier the attribution grid compares the other two against.

    The `(subject, coverage, value)` triple rather than the whole row, matching its two siblings
    exactly. `industry_code` is deliberately outside it for the reason the raw tier's window
    columns are: it is a label on the regression a residual came out of rather than the residual,
    and the same disclosure covers both.
    """
    subjects = [item.subject for item in observations]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise FactorNeutralizationError(
            f"{duplicates} appears more than once in the neutralised panel; a duplicated security "
            "is two answers to one question, and a digest that hashed both would give two "
            "different cross sections one address"
        )
    try:
        return cross_section_digest(
            ((item.subject, item.coverage, item.value) for item in observations), prefix="nrs"
        )
    except FactorError as error:
        offending = sorted(
            item.subject
            for item in observations
            if item.value is not None and not math.isfinite(item.value)
        )
        raise FactorNeutralizationError(
            f"{offending} carry a non-finite residual, so this cross section has no content "
            "address: the canonical form this digest hashes refuses one, and hashing a "
            "substitute would mint an address for a cross section nobody can reproduce. "
            "validate_neutralized_factor_observation refuses such a row at both of its call "
            "sites, so it reached this panel through a subclass that overrode __post_init__"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class NeutralizationLimitation:
    """One named boundary on what a stored neutralisation can be trusted to answer."""

    code: str
    detail: str


KNOWN_NEUTRALIZATION_LIMITATIONS: Final[tuple[NeutralizationLimitation, ...]] = (
    NeutralizationLimitation(
        code="no_cross_section_is_neutralisable_before_2021_12_13",
        detail=(
            "The industry regressor cannot be read at any earlier as_of at all, which is a "
            "refusal of the whole build rather than a filter that thins it. providers/tushare.py "
            "floors every index_member_all row's available_time at the SW2021 taxonomy's "
            "effective date, 2021-12-13, because index_member_all expresses the entire history in "
            "a vintage that did not exist before then -- all 31 distinct l1_code values it uses "
            "are SW2021's and its earliest in_date is 1984-05-09. Measured on the stored corpus "
            "in KNOWN_INDUSTRY_LIMITATIONS.no_cross_section_before_the_taxonomy_is_readable_at_"
            "all: at as_of 2015-06-30 every partition blocks with not_yet_knowable. So a "
            "V2-P4-013 walk-forward that wants a neutralised factor for a 2015 session needs a "
            "source that published a classification in 2015, which this is not; what this plane "
            "can honestly neutralise is the SW2021 era."
        ),
    ),
    NeutralizationLimitation(
        code="an_industry_answer_inside_the_era_can_still_be_backfilled",
        detail=(
            "The availability floor is a bound on the as_of, not on the day asked about. A build "
            "at as_of 2021-12-20 whose panel's own as_of resolves to a session before 2021-12-13 "
            "gets an SW2021 label for a day SW2021 did not cover, and IndustryAnswer."
            "is_backfilled reports it. That is not refused, because refusing it would refuse a "
            "legitimate first week of the era; it is counted. Every participant's flag is hashed "
            "into characteristic_digest and the total is stored as the manifest's "
            "backfilled_industry_count column, so a build whose industries were all backfilled is "
            "distinguishable on the partition from one whose were not -- which is the treatment "
            "IndexWeights.as_published_on gets for the same hazard."
        ),
    ),
    NeutralizationLimitation(
        code="the_residual_is_orthogonal_to_the_design_and_not_to_size_itself",
        detail=(
            "A stored residual is orthogonal to the industry dummies and to the ONE declared "
            "market-cap regressor, over the participants of THIS cross section. It is not "
            "orthogonal to a differently scaled size variable, so 'size neutralised' is a "
            "statement about market_cap_measure and market_cap_scale TOGETHER and reading it as a "
            "general one is an overclaim. HOW FAR FROM ORTHOGONAL IS DELIBERATELY NOT QUOTED AS A "
            "NUMBER HERE, and the retraction is part of the entry: this text used to say 'up to "
            "0.195 where their rms is 0.995, and against log(circ_mv) by up to 0.0196'. Those "
            "three figures came from ONE seed of the synthetic probe in "
            "tests/unit/test_factor_neutralization_rules.py -- whose circulating capitalisation "
            "is a Normal(0.7, 0.25) float ratio the test invents, never a stored circ_mv -- and "
            "the quantity is not stable enough to quote: across the eight seeds that test now "
            "sweeps the scale gap spans 0.021..1.222 and the measure gap 0.0067..0.0591, against "
            "residuals whose deviation is 1 by construction. What IS asserted is that every seed "
            "clears a floor, which falsifies 'these are formalities' and claims nothing further. "
            "What is invariant is affine re-scaling of the declared regressor, because a complete "
            "dummy set spans the constant: z-scoring it and 1000*x+7 each move a residual by "
            "4.44e-16. That is not a uniform bound over affine maps -- 1e-6*x-3 measures 8.4e-12 "
            "on the same probe -- so the invariance is of the answer and not of the bits."
        ),
    ),
    NeutralizationLimitation(
        code="the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused",
        detail=(
            "HALF OF THIS ENTRY WAS RETRACTED BY V2-P4-026 AND THE RETRACTION IS PART OF IT. It "
            "used to read 'the two foreign inputs are read whole partition so a mid-year as_of is "
            "refused' and to say that no neutralisation could be built at ANY as_of inside the "
            "year its price partition covers, that a whole-year backfill was therefore a year-end "
            "operation, and that a residual for any trading day of year Y carried an "
            "available_time after Y's last session. THAT IS NO LONGER TRUE OF daily_basic. "
            "load_daily_valuations now takes panel_ingest._read_visible_price_session, which "
            "scans the year partition with WHERE available_time <= as_of under a trade_date "
            "filter, so a session that published before as_of is answerable from inside its own "
            "year and a session that did not is refused BY NAME rather than answered with an "
            "empty cross section. The row filter is sound for this dataset and not for the other "
            "because providers/tushare.py dates every price row's availability at 16:30 on its "
            "own trade_date, so one session's rows share one instant and a session read is "
            "all-or-nothing; index_member_all has no such shape, which is why "
            "SecurityIndustryHistory.answerable_through exists and why that half was not moved. "
            "WHAT REMAINS, AND IT IS NOW THE WHOLE OF THIS ENTRY: index_member_all is still read "
            "through load_industry_histories, which takes PanelStore.read_if_ready, and that call "
            "decides not_yet_knowable on a partition's MAX available_time -- so a membership year "
            "is unreadable at every as_of that precedes its newest assignment, whatever the "
            "as_of's own session had, and the refusal is of the whole build rather than a "
            "thinning of the cross section. Measured on the generated fixture panel at as_of "
            "2026-01-12T04:00Z, noon Asia/Shanghai on the sixth of ten sessions: with an interval "
            "opening 2026-01-14 the 2026 partition's max available_time is 2026-01-13T16:00Z and "
            "the read blocks with not_yet_knowable, while the same panel without that interval "
            "assembles the whole cross section at that as_of -- daily_basic no longer contributes "
            "a refusal to either case. On the real corpus the blocking shape is the annual "
            "constituent review moving hundreds of names at once (613 assignments start "
            "2021-07-30, 255 on 2022-07-29), so a walk-forward replaying a panel fetched today "
            "meets it once a year rather than never. The levers are unchanged: "
            "load_industry_market_cap_cross_section's membership_years (narrowing has its own "
            "cost -- answerable_through then refuses a day past the last year read) and choosing "
            "an as_of after the membership partition closes. Filed as V2-P4-027, which is where "
            "the remaining half is solved; this entry is what it has to make false. "
            "THE SECOND HOP THIS ENTRY USED TO CARRY IS GONE AND THAT IS THE ACCEPTANCE. "
            "neutralized_observation_batch still stamps every clock of every row with the BUILD's "
            "as_of, which is the right design for a derived row, but that as_of is no longer "
            "forced to the year end -- so a residual built at a mid-year as_of is stamped there "
            "and load_neutralized_factor_observations, which reads through read_visible_at, "
            "returns it at that same as_of. Driven by tests/integration/panel/"
            "test_factor_neutralizations.py::"
            "test_a_residual_built_at_a_mid_year_as_of_is_visible_at_that_same_as_of. The two "
            "tests that pinned the old shape still pass and still mean what they meant: a "
            "residual is invisible BEFORE the as_of it was computed at, which is the point-in-"
            "time rule and not a granularity defect."
        ),
    ),
    NeutralizationLimitation(
        code="a_thin_industry_is_coded_rather_than_pooled",
        detail=(
            "A security whose industry holds fewer than min_industry_members participants gets no "
            "residual. It is not pooled into a neighbouring industry, not folded up the taxonomy "
            "to its L2 parent, and not given the whole cross section's mean -- each of those "
            "would be a number derived from securities the classification says it is not "
            "comparable to, which is MissingValuePolicy.refuse_a_filled_non_member's own "
            "argument. The cost is real and is a function of the declared level: SW2021 has 31 L1 "
            "nodes, 134 L2 and 346 L3, so on a ~5,500-name market the mean group holds about 178, "
            "41 and 16 names respectively, and an L3 build on a thin historical cross section "
            "will code materially more of it than an L1 build will. thin_industry is a census "
            "column so that how much is readable rather than inferred."
        ),
    ),
)
"""Named boundaries on what a stored neutralisation answers, each measured rather than reasoned.

**Not an enumeration of every way a neutralisation could mislead.** These are the five this issue
could demonstrate: two from the industry corpus's own measured clock, one from a 5,534-name
arithmetic probe, one from the readiness rule this plane reads that corpus through, and one from
the taxonomy's measured node counts.
"""


NEUTRALIZATION_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    item.code for item in KNOWN_NEUTRALIZATION_LIMITATIONS
)


INDUSTRY_LEVEL_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}
)
"""Which `IndustryAssignment` field each declared level reads.

A table rather than three `if` branches at the one call site, so that
`_refuse_a_level_table_that_does_not_match_the_assignment_contract` has something to reconcile at
import -- against `INDUSTRY_LEVELS` in one direction and against `IndustryAssignment`'s own field
names in the other. Both directions are reachable faults: a fourth level added to
`IndustryLevel` would raise `KeyError` from a dict lookup at the first cross section that
declared it, and a renamed field on `IndustryAssignment` -- which is a `slots=True` dataclass, so
`getattr` on a stale name raises rather than returning `None` -- would fail at the first
assembly, in production, with a message naming neither the level nor the spec.
"""


def _refuse_a_level_table_that_does_not_match_the_assignment_contract(
    table: Mapping[str, str], levels: Collection[str], fields: Collection[str]
) -> None:
    """Refuse this module at import if the level table and its two neighbours disagree.

    `domain/factor_transform.py::_refuse_a_policy_that_cannot_answer_every_missing_code`'s shape
    on a different pair of closed sets, and both inputs are arguments rather than module globals
    so that both failure directions are drivable. An audit whose only call site is the one that
    passes is an audit nobody has seen fail.
    """
    if set(table) != set(levels):
        raise FactorNeutralizationError(
            f"the declared industry levels are {sorted(set(levels))} and the field table names "
            f"{sorted(table)}; a level with no field raises KeyError at the first cross section "
            "that declares it"
        )
    unknown = sorted(set(table.values()) - set(fields))
    if unknown:
        raise FactorNeutralizationError(
            f"the field table reads {unknown} off an industry assignment, and the assignment "
            f"contract declares {sorted(fields)}; a stale field name fails at the first assembly "
            "rather than here, because IndustryAssignment is a slots dataclass and getattr on a "
            "name it does not carry raises"
        )


_refuse_a_level_table_that_does_not_match_the_assignment_contract(
    INDUSTRY_LEVEL_FIELDS, INDUSTRY_LEVELS, IndustryAssignment.__slots__
)


def industry_code_of(assignment: IndustryAssignment, level: IndustryLevel) -> str:
    """The industry code one assignment carries at one declared level.

    Resolved through `INDUSTRY_LEVEL_FIELDS` rather than through three branches, so the
    import-time audit is the only place the set of levels is enumerated --
    `MissingValuePolicy.action_for`'s argument, and for its reason: a branch table beside a
    declared table is a second copy of a closed set, and two copies drift.
    """
    if level not in INDUSTRY_LEVELS:
        raise FactorNeutralizationError(
            f"{level!r} is not a declared industry level; expected one of {sorted(INDUSTRY_LEVELS)}"
        )
    code = str(getattr(assignment, INDUSTRY_LEVEL_FIELDS[level]))
    if not code or code != code.strip():
        raise FactorNeutralizationError(
            f"{assignment.ts_code}'s {level} code is {code!r}; an assignment with a blank code at "
            "the declared level names no group, and build_security_industry_history refuses one, "
            "so this assignment was constructed directly"
        )
    return code


def industry_group_sizes(codes: Sequence[str]) -> Mapping[str, int]:
    """How many participants each industry holds, as a read-only mapping.

    Here rather than in the engine because it is the quantity `min_industry_members` is compared
    against and `smallest_industry_size` / `largest_industry_size` are read off, and one
    definition of "how big is this group" is what keeps the declared floor and the stored
    statistic talking about the same number.
    """
    sizes: dict[str, int] = {}
    for code in codes:
        sizes[code] = sizes.get(code, 0) + 1
    return MappingProxyType(sizes)
