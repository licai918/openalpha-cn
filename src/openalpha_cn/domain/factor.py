"""The versioned factor definition registry (`V2-P3-001`), and the records a computation of
one produces (`V2-P3-002`'s contracts, minus the engine).

## The properties, and why each is a field rather than a convention

A factor definition has to answer a fixed set of questions before anything is allowed to compute
it, and every one of them is load-bearing for a *different* downstream issue. They are fields on
`FactorDefinition` rather than notebook conventions because each is read by code:

- **stable identity** -> `factor_id`. `V2-P3-002` stamps it on every observation and
  `V2-P3-014` keys immutable artifacts by it.
- **version** -> `version`. A bump mints a new `factor_id`, so a restated factor cannot
  overwrite the observations of the one it replaces.
- **family** -> `family`. `V2-P3-008`'s redundancy analysis groups by it, and `009`-`013`
  populate one each.
- **required fields** -> `required_fields`. `V2-P3-002`'s coverage check: which panel columns
  must be present and non-null before a value may be produced.
- **how far back on the session axis** -> `lookback_sessions` / `max_window_sessions`.
  Point-in-time: a 120-session momentum factor needs 120 sessions **at or before `as_of`**, and
  a security with fewer gets `insufficient_history` rather than a quiet `None`. The count alone
  says nothing about *when* those sessions were, which is a hole with a measured shape -- a
  security halted for three months has a "120-session momentum" spanning 210 calendar days and
  the observation reported it as `computed` -- so the reach carries a span bound as well as a
  count. See both fields' own docstrings.
- **how far back on the report-period axis** -> `lookback_periods` / `max_window_periods`. The
  same pair of questions asked of a *filing* rather than of a session, because an EP or a ROE
  is not a session count at all. See "The second axis" below.
- **direction** -> `direction`. `V2-P3-005`'s IC sign; a rank correlation is uninterpretable
  until somebody has said which end of the cross section is the good one.

`V2-P3-001`'s acceptance names six of these; the reaches are the correction of defects the six
had between them, and each is a *declared* property rather than an engine constant for the
reason the others are -- a one-session reversal and a 120-session momentum tolerate different
amounts of stretch, and an engine that picked one number for both would be making a factor's
decision.

## The second axis, and why it is two nullable pairs rather than four numbers

`V2-P3-009`..`011` (EP/BP/SP/EPcut, ROE/ROIC/margin stability/accruals, revenue and net-income
year-on-year plus acceleration) are all built on filings, and a filing does not live on the
session axis:

- **A filing's own index is its report period.** `providers/tushare.py::_announcement_timeline`
  dates every statement row at `ann_date` on **all four** clocks, so the panel's `event_time` for
  a filing is the day it was disclosed. Two facts follow and both were measured against the
  engine before this change: an annual and a Q1 announced on one day are two rows under one
  `(subject, event_time)` key, which `compute_factor` refuses outright, and one period announced
  twice occupies two slots of a "lookback window" that was counting periods in the reader's head.
  So the reach for a statement input has to be counted in **periods**, and the engine has to
  index those inputs by period. `panel_factors::PERIOD_INDEXED_DATASETS` is the axis table.
- **A factor declares exactly the axes it reads.** `required_fields` already says which datasets
  a factor touches, so which axes it is on is *derivable* -- and a reach declared for an axis
  the factor does not read would be a number in the content address that decides nothing, while
  a missing one would be an unbounded window. Both pairs are therefore `int | None` and
  `validate_each_axis_is_declared_exactly_when_it_is_read` makes the correspondence an
  equivalence in both directions. A statement-only factor declares no session reach, and a
  `lookback_sessions=1` on it would have been a field with no reader on that one definition.
  `V2-P3-010`'s four and `V2-P3-011`'s three are the shipped ones: the quality family's ROE reads
  `income` and `balancesheet` and no session dataset -- the `fina_indicator.roe` this paragraph
  named until that issue argued it out is a *cumulative-period* return that no arithmetic converts
  to a trailing one, so the family computes the ratio instead of reading it -- and the growth
  family reads `income` alone.
- **Year-on-year is not a third dimension.** A revenue YoY at period *P* needs *P* and *P-4*;
  the acceleration of it needs one more step back again. Those are the same two numbers with
  larger values -- `lookback_periods=5` hands the evaluator a window whose `[-5]` is the
  year-earlier period -- and what makes index arithmetic on that window legitimate is
  `max_window_periods == lookback_periods`, which is the contract's way of saying "no missed
  filing inside the window". Cumulative-to-single-quarter differencing needs exactly the same
  guarantee, which is why *that* choice is the factor's and this precondition is the contract's.

  **That sentence was false when it was first written and it is the engine that made it true.**
  The span was measured against the panel's own period set -- the union of the `report_period`s
  the read returned -- and a quarter no security in the cross section filed is not in that union,
  so a five-period window straddling it "spanned five". Measured: a security missing 2024-12-31
  in a build where nobody filed 2024-12-31 was `computed`, with a `[-5]`-to-`[-1]` interval of
  fifteen months; add one other security that filed the period and the same build answered
  `insufficient_history`. `panel_factors::_period_span` now counts on the fiscal-quarter grid,
  which is knowable without reading a row, so what the equality buys is a property of the
  security rather than of the cross section it was built with.

## The ordering constraint this module is under, and why it is stated here

Adding, removing or renaming a field of `FactorDefinition` or of `FactorBuildManifest` changes
the hashed payload and therefore **invalidates every `factor_id` and `manifest_id` already
stored**. There is no migration: `domain/versioning.py`'s `ContractVersions` is deliberately not
registered for these models (they are never stored as JSON rows), so a stored observation's
`factor_id` column cannot be rewritten by anything this repository owns.

The consequence is a *sequencing* rule rather than a prohibition: **every field this contract is
going to need must land before `V2-P3-014` writes the first immutable artifact.** Today there is
no production factor partition anywhere, so the cost of a field is zero; after `014` it is a
corpus with no path back. The report-period reach is the field this contract used to owe, and it
lands here for that reason -- together with the removal of `summary`, because both move
`factor_id` and paying that once is the whole point of doing them in one change.

## Identity is `stable_model_id`, not a hash of this module's own devising

`V2-P3-001` says so, and the reason is worth restating rather than cited: this repository
already has five content-addressed contracts (`ProviderRecord`, `SignalFrame`,
`DecisionLedger`, `ValidationResult`, and `ResearchReport` through `single_version`), all
derived by `domain/_identity.py::stable_model_id` from the canonical JSON of the model's own
declared fields. A sixth hash function would be a sixth thing that can disagree about
canonicalisation -- key order, `ensure_ascii`, separators, float repr -- and the failure mode
of a disagreement is two IDs for one factor, which is exactly what an ID exists to prevent.

Two consequences follow and are pinned by tests rather than assumed:

- **`schema_version` is a real field and therefore enters the hash** (roadmap section 8
  measured this on four contracts). That is wanted here: a `factor-definition/v2` genuinely
  describes a different contract, and its observations must not collide with a `v1` one's.
- **Every declared field reaches the identity.** Roadmap section 9 records the opposite
  case -- `config_digest` and `random_seed` were believed to feed `decision_id` and do not,
  because they are not fields of the model that is hashed. `tests/unit/domain/test_factor.py`
  therefore varies each field of `FactorDefinition` and of `FactorBuildManifest` one at a
  time and asserts the ID moves, rather than asserting that it exists.

## An identity has two halves, and the second one is the harder one

"Every declared field reaches the identity" is only half a contract, and on its own it is
satisfiable by a model that declares the wrong fields. Both halves are now stated and both are
measured, because this contract had a defect in each direction:

- **What changed must move the ID.** The half above. Its failure mode is a *collision*: two
  builds over different cross sections produced the same `manifest_id` and one silently
  overwrote the other's numbers, because the manifest recorded `subject_count` and
  `universe_count` and not the sets. `subject_digest` and `universe_digest` close it, and
  `tests/integration/panel/test_factor_engine.py` audits it against `compute_factor`'s own
  signature rather than against a hand-written list -- every parameter of that function is
  either shown to move the ID or is carried in an exemption table with the reason it does not.
  A tenth parameter fails the audit until somebody classifies it.
- **What did not change must not move the ID.** Its failure mode is the opposite and was
  equally real: `FactorInputRef` carried the provider's `batch_digest`, which hashes
  `fetched_at`, so re-fetching an input partition whose rows were byte-identical moved every
  `manifest_id` built from it -- and, because a stored build may not be dropped, the recomputed
  build could then never be written. The wall clock is out of the identity on the input side
  now for exactly the reason `built_at` is out of it on the build side; `FactorInputProvenance`
  is where it went.

## Prose is recorded and is **not** a field, which is a correction rather than an omission

`FactorDefinition` carried a `summary` from `V2-P3-001` until this change, and so did
`FactorTransformSpec` and `FactorNeutralizationSpec`. Every one of them was inside its own
content address, which made **fixing a typo move the identity of every stored build without
changing a single number** -- precisely the shape `FactorTransformManifest`'s docstring rejects
`date_timezone` for ("reaches the identity and decides nothing"). The bill had already been paid
twice: `V2-P3-003`'s review recorded it as an inherited convention and left it, and `V2-P3-004`
had to knowingly break its own "edit only with a version bump" rule to retract three figures that
could not be reproduced.

The fix is a *contract shape* change and not a hashing change, because `stable_model_id` hashes
whatever the model declares and a field carved out of the hash with `exclude=True` is exactly the
shape roadmap section 9's `config_digest` had. So the prose left the models and became
`FactorNote`, a plain frozen dataclass no `stable_model_id` is ever applied to, carried by the
three registries beside the specs it is about. Editing a note moves nothing;
`tests/unit/domain/test_factor.py::
test_a_prose_edit_moves_no_identity_because_prose_is_not_a_field` builds two registries that
differ only in their notes and asserts every `factor_id` in them is byte-identical, and the same
file asserts that `summary=` is no longer *accepted* by any of the three contracts.

## What is deliberately *not* here

**No formula.** A `FactorDefinition` is data: it must survive `model_dump(mode="json")` to be
hashed, and a callable does not. The evaluator that turns a window of panel rows into a number
lives in `openalpha_cn.panel_factors` beside the engine that calls it, and the two tables are
bound at run time -- `FACTOR_DEFINITIONS` and `FACTOR_EVALUATORS` must have identical key sets,
and the engine refuses a definition with no evaluator. That binding exists because of a
measured failure elsewhere in this repository: a table gained a key with no branch behind it
and the command exited 0 with an empty result.

**No concrete factor family.** `V2-P3-009`..`013` own those. What ships here is the contract
and the closed vocabularies; every definition lives in `panel_factors` beside the evaluator it is
bound to, and each says in its own docstring what it does and does not claim -- the engine's
verification factor and `V2-P3-012`'s momentum-and-reversal family alike.

**No `ContractVersions` registration and no exported JSON Schema.** `domain/versioning.py`'s
registry dispatches *stored JSON rows* back to a pydantic model, and a factor definition is
never stored as JSON -- it is code, and its observations are stored as Parquet columns whose
schema is the partition's. `domain/schema.py`'s `CONTRACT_MODELS` is the five contracts the
HTTP face publishes; a factor's public face is `V2-P3-015`, not this issue. `ColumnarPanelBatch`
made the same two choices for the same reasons.

## Why `FactorObservation` is a dataclass while the two identity carriers are pydantic

`FactorDefinition` and `FactorBuildManifest` are constructed once per *build*. A
`FactorObservation` is constructed once per *(security, as_of)*: a whole-market cross section is
5,534 of them, and a year of daily as_ofs is 1.35e6. That is the scale `domain/panel_batch.py`
exists to keep pydantic away from (6.10 us/row measured for `ProviderRecord` construction
alone), so the per-row record follows `ColumnarPanelBatch`'s precedent and not `SignalFrame`'s.
It still validates the invariants that would otherwise be silent lies -- a `computed`
observation carries a *finite* value and a non-`computed` one carries none.

Those checks live in `validate_factor_observation`, a module-level function the dataclass's
`__post_init__` calls, rather than only in `__post_init__`. `panel/catalog.py` argues the shape
at length for its own records: validation reachable only through a constructor is validation a
subclass turns off by overriding it, and a frozen dataclass with `slots=True` is still
subclassable. Measured on the version that had only the method: a three-line subclass overriding
`__post_init__` constructed observations with an empty `subject`, a negative `input_row_count`
and a backwards window, and the write path stored all three. The remedy is one rule with two
call sites -- the constructor and `panel_factors.factor_observation_batch`, which re-checks every
observation it is about to turn into a column -- rather than two copies of one rule.
"""

import json
import math
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.financial_statements import FINANCIAL_STATEMENT_DATASETS
from openalpha_cn.domain.panel_batch import (
    RESERVED_COLUMN_NAMES,
    PanelBatchError,
    validate_panel_dataset,
    validate_panel_identifier,
)
from openalpha_cn.domain.time import ensure_aware


class FactorError(ValueError):
    """Raised for a malformed factor definition, registry or observation.

    A subclass of `ValueError` for the reason `LookAheadViolationError` is: every call site
    that already writes `except ValueError` keeps catching it unchanged.
    """


FactorFamily = Literal[
    "value",
    "quality",
    "growth",
    "momentum_reversal",
    "volatility_liquidity",
]
"""The five families `V2-P3-009`..`013` populate, as a closed set rather than free text.

One issue per member, in roadmap order: `009` value (EP / BP / SP / EPcut), `010` quality
(ROE / ROIC / gross-margin stability / accruals), `011` growth (revenue and net-income
year-on-year, plus acceleration), `012` momentum and reversal (20/60/120-session, industry
relative, 5-session reversal), `013` volatility and liquidity (residual and idiosyncratic
volatility, turnover, Amihud). Closed because `V2-P3-008`'s redundancy analysis groups by
family and `V2-P3-014`'s three-tier report is produced per family: a sixth spelling of
"momentum" would silently become a group of one.
"""

FACTOR_FAMILIES: Final[frozenset[str]] = frozenset(get_args(FactorFamily))
"""`FactorFamily`'s members as data, for the checks that have to enumerate them."""

FactorDirection = Literal["higher_is_better", "lower_is_better"]
"""Which end of the cross section the factor claims is the good one.

`V2-P3-005` needs this to read an IC's sign: a rank correlation of `-0.03` is evidence *for* a
`lower_is_better` factor and *against* a `higher_is_better` one, and nothing about the number
says which. Stated on the definition rather than inferred per report, because inferring it from
the sign of the measured IC is how a factor gets declared to work in whichever direction it
happened to come out.
"""

FACTOR_DIRECTIONS: Final[frozenset[str]] = frozenset(get_args(FactorDirection))

FactorCoverage = Literal[
    "computed",
    "not_in_universe",
    "insufficient_history",
    "ambiguous_filing",
    "input_missing",
    "undefined_value",
]
"""Whether this security had a value at this `as_of`, and if not, **why** -- never a bool.

`V2-P3-002`'s acceptance asks the coverage marker to separate "cannot be computed because data
is missing" from "cannot be computed because the security is not in the universe", and a
boolean cannot carry that distinction. The precedents are `domain/price_limits.py`'s three-state
`TradingState` and `panel_gate.py`'s eight closed refusal codes; the argument against a bool is
the one P2's technical acceptance made about `is_blocked` -- a boolean does not distinguish an
injected violation from an ordinary absence, and only a code set does.

Read in order of precedence, which is how `panel_factors._classify` applies them:

- **`not_in_universe`** -- the security was not in the cross section the caller declared for
  this `as_of`. This is not a data fault: a name that had not listed yet, or had already
  delisted, *should* have no value, and reporting `input_missing` for it would put a permanent
  false defect on every historical cross section.
- **`insufficient_history`** -- in the universe, but the visible panel holds fewer than
  `lookback_sessions` sessions for it at or before `as_of`. This is the point-in-time
  consequence of the lookback window and it is a *first-class answer*, not an error: a name
  that listed nine sessions ago genuinely has no 120-session momentum.
- **`ambiguous_filing`** (`V2-P3-018`) -- the row is there, it is there **more than once** under
  one `(security, report period, announcement)` triple, and the versions disagree in a column
  this factor reads. The publisher stated the number twice and contradicted itself; no column of
  these four endpoints orders the versions, and `domain/financial_statements.py` measured that
  no ordering rule can be invented from `update_flag` either -- "take the row with the higher
  flag" fails in all three directions on a 53-security sample, and 81.7% of the duplication is
  in `fina_indicator`, which has no such column at all. **Only the report-period axis can reach
  this code**, because it is the only axis on which a second row of one key is a *version*
  rather than a fault; a second row of one `(security, session)` still refuses the build.
- **`input_missing`** -- enough sessions, but at least one required `(dataset, column)` is null
  on one of them, or the security has no row in one of the required datasets on a session the
  others cover. `daily_basic` omitting Beijing-board names on historical sessions
  (`panel_ingest.load_daily_valuations` measured 60 of 3,843 on 2020-03-02) is the shape.
- **`undefined_value`** -- every input was present and the arithmetic still has no answer: a
  zero denominator, or a result that is not finite. Kept separate from `input_missing` because
  the remedy is different -- one is a fetch, the other is the factor's own definition.
- **`computed`** -- and only then is `value` not `None`.

The three negative codes about *inputs* are separated by what would fix them, and that is what
makes the boundary testable rather than a matter of naming. On one partition, one security, one
report period: delete the cell and the answer is `input_missing`; restore it and the answer is
`computed`; add a second row of that filing carrying the same cell and the answer is still
`computed` (one fact stated twice collapses); make the second row disagree and the answer is
`ambiguous_filing`. A **fetch** repairs the first and cannot repair the last -- re-fetching
returns the same two rows -- and neither is `undefined_value`, whose inputs were all present and
whose repair is a change to the factor's own definition.
"""

FACTOR_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(FactorCoverage))
"""`FactorCoverage`'s members as data. Closed for the reason `READINESS_ISSUE_CODES` is:
`V2-P3-007`'s coverage report groups by them and `V2-P3-005` has to exclude the non-`computed`
ones from a correlation rather than treat them as zeros.
"""

PERIOD_INDEXED_DATASETS: Final[frozenset[str]] = frozenset(FINANCIAL_STATEMENT_DATASETS)
"""The panel datasets whose rows a factor reads **by report period** rather than by session.

The four statement endpoints, and the reason is a property of their clocks rather than a
classification of their subject matter. `providers/tushare.py::_announcement_timeline` gives every
statement row `event_time == available_time == ann_date`, so the panel's own session column for a
filing is *the day it was disclosed*, and disclosure days are neither one-per-period nor
one-period-per-day:

- **Many periods on one day.** An A-share issuer routinely discloses its annual report and its
  first-quarter report together. Under a session index those are two rows of one
  `(subject, event_time)` key, which `panel_factors._read_dataset` refuses -- so before this
  table existed, an ordinary `income` input made the engine raise for the whole build.
- **One period on many days.** A restatement announced later is a second row for the same period.
  Under a session index it fills a second slot of a window that was meant to count periods.

Derived from `domain/financial_statements.FINANCIAL_STATEMENT_DATASETS` rather than written out,
so a fifth statement endpoint joins the period axis without anybody remembering to extend a list;
`tests/unit/test_factor_engine_rules.py` asserts the correspondence in both directions. It is
`frozenset` rather than the domain's tuple because every reader of it asks membership.
"""

MAX_FACTOR_KEY_LENGTH: Final[int] = 40
"""How long a factor key may be, and the bound is a *storage* budget rather than a taste.

`panel_factors` files each factor's observations in its own panel dataset, whose name is built
from this key and the version (`factor_obs_<key>_v<n>`), and `MAX_IDENTIFIER_LENGTH` caps a
panel dataset at 63 characters. The longest name the longer of the two prefixes can produce is
therefore `len("factor_manifest_") + MAX_FACTOR_KEY_LENGTH + len("_v999")`, and a key that
overran it would be a definition that constructs and then cannot be written -- a refusal at
write time for a fault declared at definition time.

The relationship is asserted rather than commented: `tests/unit/test_factor_engine_rules.py::
test_the_longest_legal_factor_key_still_names_a_legal_panel_dataset` builds the worst case out
of both constants. Widening either one alone fails there.
"""


def set_digest(values: Collection[str]) -> str:
    """A content address for a *set* of names: order-free, duplicate-free, stable across runs.

    What `FactorBuildManifest` needs and what a count cannot give it. Two builds over cross
    sections of the same size are two different builds producing two different sets of numbers,
    and a manifest that recorded only `subject_count` gave them one identity -- measured: two
    builds over `['000001.SZ', '000002.SZ']` and `['600000.SH', '600519.SH']` produced the same
    `manifest_id`, and writing the second silently replaced the first's observations.

    Sorted and de-duplicated rather than hashed in the order given, because the order of a cross
    section is not part of what a build *is*: `compute_factor` produces one independent
    observation per subject, so shuffling the argument produces the same answers, and an
    identity that moved for it would fail the other half of the contract (what did not change
    must not move the ID).

    The same canonicalisation `stable_model_id` uses -- `json.dumps` with fixed separators and
    `ensure_ascii=False` -- for the reason that function exists: a second spelling of "canonical"
    is a second thing that can disagree about one.
    """
    canonical = json.dumps(
        sorted(set(values)), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()
    return f"set_{sha256(canonical).hexdigest()[:24]}"


class FactorField(BaseModel):
    """One panel column a factor reads: `FactorField(dataset="daily", column="close")`.

    Both halves are validated with the **panel plane's own** identifier rules rather than with
    a local regular expression: `validate_panel_dataset` is what `PanelStore` applies to a
    directory name and `validate_panel_identifier` is what `PanelColumn` applies to a column
    name before it reaches DDL. A definition is data that a later engine turns into SQL, and
    validating it here means the refusal names the *definition* rather than surfacing several
    layers down as a binder error.

    **What that check is and is not.** It is *syntactic*. It refuses a name that could not be a
    panel dataset or an unquoted SQL identifier at all, and it refuses the batch contract's own
    reserved columns. It does **not** ask whether `daily.nonexistent` names a column that any
    partition holds -- nothing at this layer has a store to ask, and `domain/` may not import
    `panel/` to get one. A reference to a column that does not exist is therefore declarable,
    and fails at `compute_factor` instead: fail-closed, and the refusal names the dataset and
    the column. That is a deferral rather than a gap, and it is stated here because the earlier
    wording ("a field reference that could not name a real partition column cannot be declared
    at all") claimed the check it does not make.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dataset: str = Field(min_length=1, max_length=64)
    column: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_panel_names(self) -> Self:
        try:
            validate_panel_dataset(self.dataset)
            validate_panel_identifier(self.column)
        except PanelBatchError as error:
            raise ValueError(str(error)) from error
        if self.column in RESERVED_COLUMN_NAMES:
            raise ValueError(
                f"{self.column!r} is one of the batch contract's own reserved columns "
                f"({sorted(RESERVED_COLUMN_NAMES)}) and cannot be a factor input. `subject` is "
                "the security the observation is about and the four clocks are what decides "
                "whether a row may be read at all -- a factor that scored one of them would be "
                "scoring the point-in-time machinery rather than the data"
            )
        return self

    @property
    def qualified_name(self) -> str:
        return f"{self.dataset}.{self.column}"


class FactorDefinition(BaseModel):
    """One versioned factor: the declared properties, and nothing that decides nothing.

    See this module's docstring for the property-by-property argument, for why identity is
    `stable_model_id` rather than a hash of this module's own devising, and for why prose is a
    `FactorNote` on the registry rather than a field here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-definition/v1"] = "factor-definition/v1"
    key: str = Field(min_length=1, max_length=MAX_FACTOR_KEY_LENGTH)
    """The human name, stable across versions: `"reversal_1d"`.

    Constrained to a plain panel identifier because it is stored as a column value that a later
    query filters on, and because `qualified_key` splits on `/` -- a key containing one would
    make `"a/b/v1"` ambiguous.
    """
    version: int = Field(ge=1, le=999)
    """Bumped whenever the *meaning* changes, which mints a new `factor_id`.

    An integer rather than a `"v1"` string because the only operations anyone performs on it
    are equality and ordering, and because a restatement that keeps the same `key` must be
    orderable against the version it replaces.
    """
    family: FactorFamily
    direction: FactorDirection
    required_fields: tuple[FactorField, ...] = Field(min_length=1)
    """Every panel column this factor reads, in declared order.

    At least one, because a factor that declares no input is one whose coverage check can never
    find a shortfall -- the same vacuity `ReadinessRequirement` refuses with `empty_requirement`.
    Duplicates are refused: a repeated field would be read twice and would make
    `FactorBuildManifest.inputs` describe one partition under two entries.
    """
    lookback_sessions: int | None = Field(ge=1, le=2000)
    """How many sessions of visible history the factor needs at `as_of`, inclusive.

    Sessions of the session-indexed panel, not calendar days: a 120-session momentum factor
    needs 120 *open* sessions, and the calendar is what says which those are. `1` means "this
    session only".

    The upper bound is 2000, a little over eight A-share years, because a window wider than the
    panel's own partition granularity is a request the engine cannot serve from the years a
    caller names without that being visible; see `panel_factors.compute_factor`'s `years`
    argument.

    **`None` means "this factor is not on the session axis at all"**, and it is a declaration
    rather than a default: `validate_each_axis_is_declared_exactly_when_it_is_read` refuses
    `None` for a factor whose `required_fields` name any dataset outside
    `PERIOD_INDEXED_DATASETS`, and refuses a number for one whose fields are all filings.
    `V2-P3-010`'s quality family and `V2-P3-011`'s growth family are the shipped cases --
    `return_on_equity_ttm` reads `income` and `balancesheet` and no price at all, `revenue_yoy`
    reads `income` and no price at all -- and a `lookback_sessions=1` on any of those seven would
    be a number in the content address that no branch of the engine could read.
    """
    max_window_sessions: int | None = Field(ge=1, le=4000)
    """How many *panel* sessions the `lookback_sessions` window is allowed to span.

    `lookback_sessions` counts a security's **own** rows and says nothing about when they were,
    which is a hole with a measured shape rather than a theoretical one. Two securities in one
    build, one lookback, one coverage code between them:

        000001.SZ  computed  0.25  window 2026-06-29..2026-06-30  (1 day)
        000002.SZ  computed  0.50  window 2026-01-05..2026-06-30  (176 days)

    Both are "the last two sessions this security traded". The second is a +50% move across half
    a year reported by a factor whose declared window is one session's close-to-close simple
    return, marked `computed`, and handed to `FactorPanel.values()` for a rank correlation.
    Scaled to `V2-P3-012`'s 120-session momentum, a name halted for three months earns a
    "120-session momentum" spanning 210 calendar days.

    So the window carries a reach as well as a count, and a window that needs more panel
    sessions than this to hold `lookback_sessions` of the security's own is
    `insufficient_history` -- which is the honest code for it: the security does not have enough
    history *near `as_of`*, which is the only kind a point-in-time factor can use.

    Counted in panel sessions rather than calendar days because the panel's own session set is
    what the engine has (it is the union of every session the visible read returned) and a
    calendar-day bound would be a second calendar to disagree with the first. Must be at least
    `lookback_sessions`: N of a security's own sessions always span at least N panel sessions,
    so a smaller bound would be a factor that can never compute anything.

    Equality is the strict setting and it is a real one: `max_window_sessions ==
    lookback_sessions` means "no missed session inside the window", which is what a one-session
    return should mean. A wider setting is the number of missed sessions the factor tolerates.

    `None` exactly when `lookback_sessions` is `None`; see that field.
    """
    lookback_periods: int | None = Field(ge=1, le=60)
    """How many of the security's own **report periods** the factor needs, knowable at `as_of`.

    Counted in filings and not in sessions, which is the whole reason this pair exists: an EP
    factor needs "the latest filing knowable at `as_of`", and a ROE needs that one; a
    gross-margin *stability* needs several; a revenue year-on-year needs the period four
    quarters back as well as the latest one. None of those is a number of trading days.

    "The security's own periods, knowable at `as_of`" is exactly the set
    `financial_statements.StatementHistory.periods_on(day)` answers, and the most recent member
    of it is what `latest_filing_on` returns -- the most recent **period**, which is not the most
    recent *announcement*: a security may announce an earlier period after a later one, measured
    at 12 of `income`'s 3,796 answerable days over a 76-security probe. `panel_factors` selects
    on the same key for the same reason, and
    `tests/integration/panel/test_factor_report_periods.py::
    test_the_engines_period_selection_is_the_domains_filing_for` pins the two against each other
    on one corpus rather than leaving them two implementations of one rule.

    The upper bound is 60, fifteen A-share years of quarterly filings. Wide enough that no
    factor in `V2-P3-009`..`013` argues with it (`011`'s year-on-year acceleration is the widest
    at nine periods) and narrow enough that a caller who typed a session count into it is refused
    at the contract.

    **`None` means "this factor reads no filing"**, under the same equivalence
    `lookback_sessions` is under: declared exactly when `required_fields` names a member of
    `PERIOD_INDEXED_DATASETS`.
    """
    max_window_periods: int | None = Field(ge=1, le=120)
    """How many **fiscal quarters** the `lookback_periods` window is allowed to span.

    `max_window_sessions`' argument, transposed, and it is not decoration on this axis either:
    `lookback_periods` counts a security's own filings and says nothing about whether they are
    consecutive. A name that stopped filing for two years and resumed has "the last four
    periods" spanning twelve, and a year-on-year computed from `window[-1]` against `window[-5]`
    on such a window compares two periods that are not a year apart.

    **Counted on the calendar's quarter grid and not on the panel's period set**, which is where
    the two axes part company and is the correction of a real defect rather than a preference.
    `max_window_sessions` counts panel sessions because the sessions a cross section returns *are*
    the trading calendar -- every security is quoted on every open day, so a session missing from
    the union is a day the market was shut. Report periods carry no such guarantee: a period
    missing from the union means only that nobody in this build filed it. Measured, changing
    nothing but whether one other security filed the quarter this one skipped, `computed` with a
    fifteen-month "year-on-year" became `insufficient_history` -- so the panel-set measure made
    this contract's guarantee a property of the build's composition. `panel_factors
    ::FISCAL_QUARTER_ENDS` is the grid instead: a PRC listed company's accounting year is the
    calendar year, so the quarters between two period ends are enumerable without consulting a
    row. That is not the "fiscal-quarter arithmetic of this module's own devising" the engine
    declines to perform -- that one would have to rule on which quarter a *non*-quarter-end
    `end_date` belongs to, and `panel_factors::_report_period` refuses such a period outright.

    Equality is the strict setting and is the one the year-on-year factors need:
    `max_window_periods == lookback_periods` means **"no missed filing inside the window"**,
    which is what makes `window[-5]` the year-earlier period and what makes differencing two
    cumulative periods into a single quarter arithmetic rather than a guess. Which of those a
    factor does is the factor's business; that the window it does it on is contiguous is this
    contract's.

    `None` exactly when `lookback_periods` is `None`.
    """

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        try:
            validate_panel_identifier(value, role="factor key")
        except PanelBatchError as error:
            raise ValueError(str(error)) from error
        return value

    @model_validator(mode="after")
    def validate_fields_are_distinct(self) -> Self:
        names = [item.qualified_name for item in self.required_fields]
        if len(set(names)) != len(names):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            raise ValueError(
                f"required_fields names {duplicates} more than once; a repeated column would be "
                "read twice and would make the build manifest describe one partition twice"
            )
        return self

    @model_validator(mode="after")
    def validate_each_axis_is_declared_exactly_when_it_is_read(self) -> Self:
        """Each reach is declared **iff** `required_fields` puts this factor on that axis.

        An equivalence rather than two independent checks, because each direction fails
        differently and both are reachable. A reach declared for an axis the factor does not read
        is a number inside `factor_id` that no branch of `panel_factors` can consult -- the "field
        with no reader" this contract refused a report-period dimension for until it had one. A
        reach *missing* for an axis the factor does read is the opposite and is worse: the engine
        has no bound to apply, so the window would be whatever the read happened to return.

        `required_fields` has `min_length=1`, so at least one axis is always declared and a
        definition with no reach at all is unconstructible.

        **The span bound is pinned at a count of one**, on the same argument one step further in.
        A one-point window spans exactly one panel point on either axis -- `panel_factors
        ::_session_span` and `_period_span` both return 1 for a window whose ends coincide -- so
        every setting of `max_window_*` above 1 decides the same nothing, and this contract's
        objection to a reach that no branch can consult applies to it word for word. Left free, it
        was measured to be inert: raising `earnings_yield_ttm`'s `max_window_sessions` from 1 to
        50 and `book_to_price`'s `max_window_periods` from 1 to 8 turned only the declaration
        tests red and left the engine's and the integration suite's entirely green -- two
        behaviourally identical factors carrying two different `factor_id`s, which is the one
        thing a content address may not do. This refuses that pair rather than documenting it, and
        it is a validator and not a field, so it moves no `factor_id` that exists.
        """
        for axis, datasets, lookback, window, names in (
            (
                "session",
                self.session_datasets,
                self.lookback_sessions,
                self.max_window_sessions,
                ("lookback_sessions", "max_window_sessions"),
            ),
            (
                "report-period",
                self.period_datasets,
                self.lookback_periods,
                self.max_window_periods,
                ("lookback_periods", "max_window_periods"),
            ),
        ):
            count_name, window_name = names
            if (lookback is None) != (window is None):
                raise ValueError(
                    f"{count_name} and {window_name} are stated together or not at all; a count "
                    "with no span bound is a window whose reach nothing decides, and a span "
                    "bound with no count is a bound on a window that is never formed"
                )
            if bool(datasets) != (lookback is not None):
                stated = "declares" if lookback is not None else "declares no"
                raise ValueError(
                    f"this factor {stated} {axis} reach and reads {sorted(datasets)} on the "
                    f"{axis} axis; {count_name} is declared exactly when required_fields names a "
                    "dataset on that axis -- a reach for an axis nothing is read on is a number "
                    "in the content address that no branch can consult, and a missing one is an "
                    "unbounded window"
                )
            if lookback is not None and window is not None and window < lookback:
                raise ValueError(
                    f"{window_name} {window} is narrower than {count_name} {lookback}; "
                    f"{lookback} of a security's own {axis} points span at least that many panel "
                    f"{axis} points, so this factor could never compute a value for anybody"
                )
            if lookback == 1 and window is not None and window > 1:
                raise ValueError(
                    f"{window_name} {window} is stated beside {count_name} 1; one of a "
                    f"security's own {axis} points spans exactly one, so no setting above 1 can "
                    "ever bind and two factors that compute identically would carry different "
                    f"factor_ids -- declare {window_name}=1, which is what a count of one means"
                )
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def factor_id(self) -> str:
        """The content address of this definition: every declared field, canonically hashed."""
        return stable_model_id(prefix="fct", model=self)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def qualified_key(self) -> str:
        """`"reversal_1d/v1"` -- the human handle a CLI or a report shows.

        A computed field, so it is excluded from `factor_id`'s hash and adds no second
        canonicalisation to disagree with the first. Two definitions can share a
        `qualified_key` and differ in `factor_id` (a redefinition that forgot to bump
        `version`); `FactorRegistry` is what refuses that, because it is a property of a
        *collection* and not of a definition.
        """
        return f"{self.key}/v{self.version}"

    @property
    def datasets(self) -> tuple[str, ...]:
        """The distinct panel datasets this factor reads, in first-declared order."""
        seen: dict[str, None] = {}
        for item in self.required_fields:
            seen.setdefault(item.dataset, None)
        return tuple(seen)

    @property
    def session_datasets(self) -> tuple[str, ...]:
        """The datasets this factor reads on the **session** axis, in first-declared order."""
        return tuple(name for name in self.datasets if name not in PERIOD_INDEXED_DATASETS)

    @property
    def period_datasets(self) -> tuple[str, ...]:
        """The datasets this factor reads on the **report-period** axis, in declared order.

        The partition of `datasets` `PERIOD_INDEXED_DATASETS` induces. Derived rather than
        declared for the reason the axis table is derived: a factor should not be able to say
        which axis `income` is on, because then two factors could disagree about it.
        """
        return tuple(name for name in self.datasets if name in PERIOD_INDEXED_DATASETS)

    def columns_of(self, dataset: str) -> tuple[str, ...]:
        """The columns this factor reads from `dataset`, in declared order."""
        return tuple(item.column for item in self.required_fields if item.dataset == dataset)


@dataclass(frozen=True, slots=True)
class FactorNote:
    """Prose about one declared contract: recorded, resolvable, **never hashed**.

    Where `FactorDefinition.summary`, `FactorTransformSpec.summary` and
    `FactorNeutralizationSpec.summary` went, and the move is the correction of a defect all three
    shared. Prose decides nothing, so a typo fix in any of them moved a content address and
    therefore the identity of every stored build derived from it, without changing a number.

    A separate dataclass rather than an `exclude=True` field on the specs, for
    `FactorInputProvenance`'s reason stated one level up: the property these contracts are under
    is "every field of the hashed model reaches the identity", and a field carved out of the hash
    is exactly the shape roadmap section 9's `config_digest` had. Splitting keeps that property
    literally true and auditable by asserting on the hashed payload's key set. A `dataclass`
    rather than a `BaseModel` because nothing validates prose beyond its being non-empty and
    because the surest way for a record never to acquire a content address is for it not to be
    the kind of object `stable_model_id` accepts.

    `subject` is the `qualified_key` the note is about, so a note is self-describing rather than
    positional: a registry that paired notes to specs by index would silently re-attribute every
    note the moment somebody inserted a spec. The three registries audit that every note names a
    contract they declare, which is the same shape as `panel_factors._refuse_table_drift`.
    """

    subject: str
    summary: str

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise FactorError("a note must name the contract it is about")
        if not self.summary.strip():
            raise FactorError(f"the note for {self.subject!r} carries no prose")


def validate_notes(
    notes: tuple[FactorNote, ...],
    *,
    declared: tuple[str, ...],
    role: str,
    error: type[ValueError] = FactorError,
) -> None:
    """Refuse notes that do not name contracts this registry declares, or name one twice.

    One function with three call sites -- `FactorRegistry`, `FactorTransformRegistry` and
    `FactorNeutralizationRegistry` -- rather than three copies, because the rule is the same rule
    and the three registries' *refusals* are what their own classes are written out separately
    for. A note for an undeclared contract is prose about nothing, which is how a note survives
    the removal of the spec it described and goes on being shown; two notes for one contract make
    `note_for` arbitrary.

    `error` is the caller's own exception type rather than this module's, so a registry refuses
    its notes with the same class it refuses everything else with: a caller that writes
    `except FactorTransformError` around a registry construction should not have one of that
    object's refusals escape as `FactorError` because the rule happens to be shared. Every one of
    the three is a `ValueError`, so the parameter narrows the type rather than widening it, and
    each registry's own test asserts on its own class.

    The converse -- every declared contract has a note -- is deliberately **not** enforced here.
    A registry a test builds out of two probe specs has nothing to say about them, and forcing a
    sentence would produce the placeholder prose `V2-P0B-009` deleted elsewhere. It is required of
    the three *shipped* registries instead, where it is a real obligation, by
    `tests/unit/test_factor_engine_rules.py::test_every_shipped_contract_carries_its_prose`.
    """
    subjects = [note.subject for note in notes]
    if len(set(subjects)) != len(subjects):
        duplicates = sorted({name for name in subjects if subjects.count(name) > 1})
        raise error(
            f"{duplicates} carries more than one {role} note; two notes about one contract make "
            "the lookup arbitrary"
        )
    orphans = sorted(set(subjects) - set(declared))
    if orphans:
        raise error(
            f"{orphans} is not a declared {role}, so its note is prose about nothing; this "
            f"registry declares {list(declared)}"
        )


@dataclass(frozen=True, slots=True)
class FactorRegistry:
    """Every factor this build knows, keyed two ways and refusing two shapes.

    A plain frozen collection rather than a mutable module-level dict with a `@register`
    decorator, for the reason `storage/migrations.py`'s `MIGRATIONS` tuple and
    `domain/versioning.py`'s `ContractVersions` are: a registry that is populated by import
    side effects has a content that depends on which modules happened to be imported, and the
    audit that matters here ("every definition has an evaluator") would then be asking a
    question whose answer changes with import order.

    Two refusals, each closing a shape that would otherwise pass quietly:

    - **Empty.** A registry with no definitions satisfies every "for each definition" assertion
      vacuously. That is the exact failure this repository measured once already, in the other
      direction (a table gained a key with no branch behind it and the command exited 0 with an
      empty result), and an empty registry is the same fault with the halves swapped.
    - **A repeated `qualified_key`.** Two definitions that answer to one name make `get()`
      arbitrary, and the likely cause is a restatement that forgot to bump `version`.

    A third refusal was written and then deleted, which is worth recording because deleting it
    was the point. "Two entries with different names and the same `factor_id`" reads like an
    obvious guard and is a branch nothing can reach: `key` and `version` are both hashed into
    the content address, so two definitions with distinct `qualified_key`s have distinct
    `factor_id`s, and two with the same one are refused by the check above before the ID is
    consulted. Keeping it would have put an unreachable branch in a registry whose whole job is
    to make unreachable branches visible.
    """

    definitions: tuple[FactorDefinition, ...]
    notes: tuple[FactorNote, ...] = ()
    """The prose about these definitions, out of every content address. See `FactorNote`."""

    def __post_init__(self) -> None:
        if not self.definitions:
            raise FactorError(
                "a factor registry must declare at least one definition; an empty one satisfies "
                "every per-definition check vacuously"
            )
        keys = [item.qualified_key for item in self.definitions]
        if len(set(keys)) != len(keys):
            duplicates = sorted({key for key in keys if keys.count(key) > 1})
            raise FactorError(
                f"{duplicates} is declared more than once; two definitions answering to one "
                "name make a lookup arbitrary -- bump `version` on the restatement"
            )
        validate_notes(self.notes, declared=tuple(keys), role="factor")

    def note_for(self, qualified_key: str) -> str | None:
        """The prose about `key/vN`, or `None` when this registry carries none for it.

        `None` rather than a refusal: a registry with no notes is legitimate (see
        `validate_notes`), so "there is nothing written about this factor" is an answer and not a
        fault. Asking about a factor the registry does not declare is a fault, and `get` is where
        it is refused -- reached first, so a typo in the handle does not read as absent prose.
        """
        self.get(qualified_key)
        for note in self.notes:
            if note.subject == qualified_key:
                return note.summary
        return None

    @property
    def qualified_keys(self) -> tuple[str, ...]:
        """Every `key/vN` handle, in declared order."""
        return tuple(item.qualified_key for item in self.definitions)

    @property
    def factor_ids(self) -> tuple[str, ...]:
        return tuple(item.factor_id for item in self.definitions)

    def get(self, qualified_key: str) -> FactorDefinition:
        """The definition answering to `key/vN`, or a refusal that names what is declared."""
        for item in self.definitions:
            if item.qualified_key == qualified_key:
                return item
        raise FactorError(
            f"{qualified_key!r} is not a declared factor; this build knows "
            f"{list(self.qualified_keys)}"
        )

    def by_id(self, factor_id: str) -> FactorDefinition:
        """The definition with this content address, or a refusal.

        The direction a stored observation needs: a partition column carries `factor_id` and
        nothing else, so reading one back means resolving it here.
        """
        for item in self.definitions:
            if item.factor_id == factor_id:
                return item
        raise FactorError(
            f"{factor_id!r} is not a factor this build declares; it knows {list(self.factor_ids)}"
        )


class FactorInputRef(BaseModel):
    """One `(dataset, year)` partition a build read, and what it got out of it -- **hashed**.

    This is `V2-P3-002`'s "input reference" at the granularity the panel plane can actually
    prove, reduced to the facts that are properties of the *content*. Every field here enters
    `FactorBuildManifest.manifest_id`, so every field here has to be one that a re-fetch of
    identical rows does not move:

    - `partition_content_hash` is `PartitionRef.content_hash`, which covers
      `(dataset, year, column names and SQL types, rows)`. It answers "is this the same write",
      and it is exactly stable across a re-fetch that changed nothing.
    - `visible_row_count` and `withheld_row_count` are the two halves of the point-in-time read:
      how many rows of the partition were at or before `as_of` on the availability clock, and
      how many were held back. Both are functions of the content and of `as_of`. The second is
      the number that makes a short read *stated* rather than inferred -- see
      `panel/store.py::PanelStore.read_visible_at`.

    **`batch_digest` is deliberately not here**, and its absence is the correction of a measured
    defect rather than a simplification. `PartitionCoverage.batch_digest` hashes the provider
    batch's `fetched_at` -- that class's own docstring says "the digest changes on every
    refetch" -- so while it was a field of the hashed model, re-fetching a partition whose rows
    were byte-identical moved every `manifest_id` derived from it. Measured on the fixture
    panel: `partition_content_hash` unchanged, row count unchanged, every observation value
    identical, `manifest_id` moved. And because `panel_factors._refuse_to_drop_stored_rows` will
    not let a stored build be dropped, the recomputed build could then never be written and the
    old one could no longer be re-derived -- the coverage record that held its `batch_digest`
    had already been overwritten.

    It is still recorded, because "is this partition still what that provider sent at that point
    in time" is a real question: `FactorInputProvenance` carries it, `FactorPanel` holds it, and
    the manifest dataset stores it beside the hashed fields. That is the same arrangement
    `built_at` gets, for the same reason.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dataset: str = Field(min_length=1, max_length=64)
    year: int = Field(ge=1900, le=2999)
    partition_content_hash: str = Field(min_length=1, max_length=256)
    visible_row_count: int = Field(ge=0)
    withheld_row_count: int = Field(ge=0)


class FactorInputProvenance(BaseModel):
    """The provider-side digest of one input partition: recorded, stored, **never hashed**.

    `PartitionCoverage.batch_digest` is the `ColumnarPanelBatch.content_digest` the provider's
    own batch carried, which covers `provider_id`, `kind`, `as_of`, `fetched_at`, `source_uri`
    and `schema_version` on top of the rows. It answers a question `partition_content_hash`
    cannot -- "is this partition still what that provider sent at that point in time" -- and
    `panel/catalog.py::PartitionCoverage` argues at length that neither replaces the other.

    Both claims are still true. What changed is *where* it lives: a field that moves on every
    re-fetch cannot be part of a content address whose whole purpose is to be reproducible, so
    it is carried on `FactorPanel` alongside `built_at` and written to the manifest dataset as
    `input_batch_digest`. Out of the identity, on the record -- the arrangement roadmap section 9
    says was wanted and not had.

    A separate model rather than an `exclude=True` field on `FactorInputRef`, because the
    property this contract is under is "every field of the hashed model reaches the identity"
    and a field carved out of the hash is exactly the shape section 9's `config_digest` had.
    Splitting the model keeps that property literally true and auditable by asserting on the
    hashed payload's key set.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dataset: str = Field(min_length=1, max_length=64)
    year: int = Field(ge=1900, le=2999)
    batch_digest: str = Field(min_length=1, max_length=256)


class FactorBuildManifest(BaseModel):
    """What one factor computation was made of, as a content address.

    `V2-P3-002`'s "build manifest" requirement, and the shape is chosen against roadmap
    section 9's measured mistake rather than around it. There, `config_digest` and
    `random_seed` were *believed* to feed `decision_id` and did not -- because they are fields
    of `RunManifest`, and `RunManifest` is not one of the models `stable_model_id` is applied
    to. The lesson is not "hash more things"; it is **"a field only reaches an identity if it
    is a field of the hashed model, and that has to be measured rather than assumed"**. So:

    - Every field declared here enters `manifest_id`, and
      `tests/unit/domain/test_factor.py::test_every_manifest_field_reaches_the_identity` varies
      each one alone and asserts the ID moves.
    - **`built_at` is deliberately not a field.** The wall clock at which a build ran is not
      part of what the build *is*: recomputing the same factor from the same partitions at the
      same `as_of` must produce the same `manifest_id`, or the identity cannot be used to
      detect drift. The wall clock is still recorded -- `panel_factors` writes it as the
      observation partition's `ColumnarPanelBatch.fetched_at`, hence
      `PartitionCoverage.fetched_at` -- so it is available and out of the identity, which is
      the arrangement section 9 says was wanted and not had.
    - `code_commit` **is** a field, and mandatory with no default. Different code may compute a
      different number from the same rows, so an identity that ignored it would claim
      reproducibility it cannot deliver. It has no default for the reason `V2-P0B-009` removed
      `"development"` and `"0" * 64`: a placeholder that looks like a value is worse than an
      argument the caller has to supply. `runtime/provenance.py::resolve_code_commit` is what a
      face should pass; `panel_factors` cannot call it itself, because no top-level `panel_*`
      module may import `runtime` (`tests/unit/test_panel_ingest_import_isolation.py`).

    ## The other half: what did not change must not move the identity

    The rule above is one direction and this contract had a defect in each. The second
    direction is that a build recomputed from unchanged inputs must reproduce its ID, and it is
    what makes `manifest_id` usable as a *recovery* path rather than only as a drift detector:
    `panel_factors.write_factor_panels` refuses to drop a stored build, so a rebuild whose ID
    moved for no reason is a rebuild that can never be written and whose predecessor can no
    longer be re-derived.

    Two things follow, and both are absences:

    - **`built_at` is not a field**, as above.
    - **No input field may carry a wall clock.** `FactorInputRef` was reduced to the facts that
      are properties of the partition's *content*; the provider's `batch_digest`, which hashes
      `fetched_at`, moved to `FactorInputProvenance`. See both classes.

    ## What determines the answers has to be declared, not just what was declared hashed

    Roadmap section 9's lesson has a second half that an equivalence test cannot reach: varying
    each declared field and watching the ID move proves nothing about a determinant that was
    never declared. This manifest had one -- two of them. It recorded `subject_count` and
    `universe_count` and not the sets, so two builds over different cross sections of the same
    size shared one `manifest_id`, and a build whose universe excluded everybody produced a
    panel of `not_in_universe` under the same identity as the build that had scored them all.
    Writing the second silently replaced the first's numbers and nothing stored could say which
    build produced which.

    `subject_digest` and `universe_digest` are the fix; the audit that keeps a *third* one from
    arriving unnoticed is in `tests/integration/panel/test_factor_engine.py`, and it reads
    `compute_factor`'s own signature rather than a hand-written list.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-build-manifest/v1"] = "factor-build-manifest/v1"
    factor_id: str = Field(min_length=1, max_length=64)
    factor_key: str = Field(min_length=1, max_length=MAX_FACTOR_KEY_LENGTH)
    factor_version: int = Field(ge=1, le=999)
    as_of: datetime
    date_timezone: str = Field(min_length=1, max_length=64)
    code_commit: str = Field(min_length=7, max_length=64)
    direction: FactorDirection
    """Which end of the cross section this build claimed was the good one.

    A projection of `factor_id` like `lookback_sessions`, and carried for a sharper reason than
    readability: a stored `value` column is *uninterpretable* without it. A rank correlation of
    `-0.03` is evidence for a `lower_is_better` factor and against a `higher_is_better` one, and
    nothing about the number says which -- so a reader of the observation partition who cannot
    resolve `factor_id` against a registry cannot read the sign of what is stored. `V2-P3-005`
    is its first consumer."""
    lookback_sessions: int | None = Field(ge=1, le=2000)
    max_window_sessions: int | None = Field(ge=1, le=4000)
    """`FactorDefinition.max_window_sessions` as this build applied it.

    Carried on the manifest for the reason `lookback_sessions` is: it is a *bound on the
    answers*, and a reader holding a stored observation should not have to resolve `factor_id`
    against a registry to learn how far the window behind a `computed` value was allowed to
    reach. It is determined by `factor_id`, which is also here; that is a projection for
    readability, not a second source of truth, and `compute_factor` sets both from one
    definition."""
    lookback_periods: int | None = Field(ge=1, le=60)
    max_window_periods: int | None = Field(ge=1, le=120)
    """The report-period reach as this build applied it, both halves, for the session pair's
    reason and one sharper: a stored observation whose window is a set of *filings* is
    uninterpretable without knowing how many and how far apart they were allowed to be, and the
    session columns say nothing at all about that axis. `None` on both for a factor that reads no
    filing, which is what `FactorDefinition`'s own equivalence guarantees -- so a manifest cannot
    claim a period bound for a build that never formed a period window."""
    subject_count: int = Field(ge=1)
    """How many securities were asked about. At least one: a build over an empty cross section
    produces no observation and would leave a manifest describing nothing."""
    subject_digest: str = Field(min_length=1, max_length=64)
    """`set_digest` of the cross section this build was asked about.

    The discriminator `subject_count` is not. Two builds over two-name cross sections that share
    no name are two different builds, and while the manifest recorded only the count they shared
    an identity and one silently overwrote the other. See this class's docstring.

    Both are kept because they answer different questions -- the count is what a report prints,
    the digest is what an identity needs -- and neither is derivable from the other."""
    universe_count: int = Field(ge=0)
    """How many securities the caller declared to be in the cross section at `as_of`.

    Zero is legal and is not the same as a missing check: a universe that is genuinely empty at
    some historical `as_of` makes every observation `not_in_universe`, which is an answer. What
    is *not* legal is leaving it unstated -- `panel_factors.compute_factor` takes the universe
    as a mandatory argument for the reason `ReadinessRequirement`'s four checks have no
    defaults."""
    universe_digest: str = Field(min_length=1, max_length=64)
    """`set_digest` of that universe, for `subject_digest`'s reason and one sharper.

    The universe decides `not_in_universe`, which decides whether a security is scored at all.
    Two builds over one cross section with disjoint universes of equal size produced completely
    disjoint sets of values and shared a `manifest_id`."""
    inputs: tuple[FactorInputRef, ...] = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware(value)

    @model_validator(mode="after")
    def validate_inputs_are_distinct(self) -> Self:
        keys = [(item.dataset, item.year) for item in self.inputs]
        if len(set(keys)) != len(keys):
            duplicates = sorted(
                f"{name}/{year}" for name, year in set(keys) if keys.count((name, year)) > 1
            )
            raise ValueError(
                f"inputs names partition(s) {duplicates} more than once; one partition read "
                "twice would be counted twice in visible_row_count"
            )
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def manifest_id(self) -> str:
        """The content address of this build. See this class's docstring for what is in it."""
        return stable_model_id(prefix="fmn", model=self)


@dataclass(frozen=True, slots=True, kw_only=True)
class FactorObservation:
    """One security's answer at one `as_of`: the value, or the reason there is none.

    A frozen dataclass rather than a pydantic model; see this module's docstring for the
    per-row cost argument that decides it.

    The invariants are in `validate_factor_observation`, which `__post_init__` calls and which
    the write path calls again; see this module's docstring for why one rule with two call sites
    rather than a method. The load-bearing one is that a `computed` observation carries a
    **finite** value and every other coverage code carries none. Without it,
    `value=None, coverage="computed"` and `value=0.0, coverage="not_in_universe"` are both
    constructible, and both read downstream as a number -- the first as a missing one, the
    second as a real zero in a cross section the security was never in.
    """

    subject: str
    as_of: datetime
    value: float | None
    coverage: FactorCoverage
    factor_id: str
    manifest_id: str
    input_row_count: int
    """How many visible input rows fed this observation, across every required dataset.

    The row-level half of "input reference": `FactorBuildManifest.inputs` names the partitions,
    and this says how much of them this security's answer actually rests on. Zero for a
    `not_in_universe` observation, which reads nothing.
    """
    input_session_first: date | None
    input_session_last: date | None
    """The first and last session of the window this value was computed over, or `None` when
    there was no window. Together with `input_row_count` this is what makes a stored
    observation re-derivable without re-running the engine's session selection."""
    input_period_first: date | None = None
    input_period_last: date | None = None
    """The first and last **report period** of the filing window, on the same terms.

    A second pair rather than a reinterpretation of the session pair, because a factor can be on
    both axes at once -- `V2-P3-009`'s EP reads a price and a filing -- and one pair of columns
    would then have to hold two different kinds of date for one row.

    Defaulted to `None` where the session pair is not, and the asymmetry is deliberate: absent is
    the answer for every factor that reads no filing, which is most of them, and it is a *value*
    rather than an omission. `panel_factors._classify` passes both explicitly on every path all
    the same, and `validate_factor_observation` holds the pair to the same both-or-neither and
    not-backwards rules the session pair is under, so a default cannot smuggle in half a window.
    """

    def __post_init__(self) -> None:
        # Normalised rather than merely required to be aware, because a stored observation
        # read back out of DuckDB arrives tagged with the *session's* timezone rather than UTC
        # (`domain/panel_batch.py` measured an instant written as `2024-06-28T07:00Z` reading
        # back as `America/Toronto`), and `V2-P3-005` groups observations by `as_of`. The same
        # instant under two labels is one dictionary key only after this line.
        object.__setattr__(self, "as_of", ensure_aware(self.as_of))
        validate_factor_observation(self)


def validate_factor_observation(observation: FactorObservation) -> None:
    """Every rule a stored observation has to satisfy, as a function with two call sites.

    `FactorObservation.__post_init__` is one; `panel_factors.factor_observation_batch` is the
    other, and it exists because a `__post_init__` is a method and a method is overridable.
    `panel/catalog.py` made the same move for the same reason, and the version of this contract
    that had only the method was measured to accept a three-line subclass's observations with an
    empty `subject`, an `input_row_count` of `-5` and a window running backwards -- all the way
    into a Parquet partition.

    The finiteness rule is here rather than only in `panel_factors._classify`. `_classify` does
    check `math.isfinite` on an evaluator's return, and that is where `undefined_value` comes
    from; what it does not cover is either boundary around it. Measured on the version that had
    only that check: `FactorObservation(value=float("nan"), coverage="computed")` constructed,
    wrote, and read back as `computed` with a `nan` in it, and `FactorPanel.values()` handed it
    to a correlation. `undefined_value` exists precisely to keep a non-finite number out of the
    `computed` code, and a rule that lives in one private function is a rule the type boundary
    and the read boundary do not have.
    """
    if not observation.subject:
        raise FactorError("an observation must name a subject")
    ensure_aware(observation.as_of)
    if observation.coverage not in FACTOR_COVERAGE_CODES:
        raise FactorError(
            f"{observation.coverage!r} is not a declared coverage code; expected one of "
            f"{sorted(FACTOR_COVERAGE_CODES)}"
        )
    if (observation.value is None) == (observation.coverage == "computed"):
        raise FactorError(
            f"{observation.subject} at {observation.as_of.isoformat()} reports coverage "
            f"{observation.coverage!r} with value {observation.value!r}; exactly the `computed` "
            "code carries a value, and every other code carries None"
        )
    if observation.value is not None and not math.isfinite(observation.value):
        raise FactorError(
            f"{observation.subject} at {observation.as_of.isoformat()} reports coverage "
            f"{observation.coverage!r} with value {observation.value!r}; a non-finite number is "
            "what the `undefined_value` code exists to carry, and one stored as `computed` "
            "poisons every downstream mean and rank built on the column"
        )
    if observation.input_row_count < 0:
        raise FactorError("input_row_count cannot be negative")
    for axis, first, last in (
        ("session", observation.input_session_first, observation.input_session_last),
        ("period", observation.input_period_first, observation.input_period_last),
    ):
        if (first is None) != (last is None):
            raise FactorError(
                f"input_{axis}_first and input_{axis}_last are both present or both absent; "
                "a window with only one end is not a window"
            )
        if first is not None and last is not None and first > last:
            raise FactorError(f"the input {axis} window {first} to {last} runs backwards")
