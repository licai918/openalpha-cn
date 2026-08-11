"""The versioned factor definition registry (`V2-P3-001`), and the records a computation of
one produces (`V2-P3-002`'s contracts, minus the engine).

## The six properties, and why each is a field rather than a convention

A factor definition has to answer six questions before anything is allowed to compute it, and
every one of them is load-bearing for a *different* downstream issue. They are fields on
`FactorDefinition` rather than notebook conventions because each is read by code:

- **stable identity** -> `factor_id`. `V2-P3-002` stamps it on every observation and
  `V2-P3-014` keys immutable artifacts by it.
- **version** -> `version`. A bump mints a new `factor_id`, so a restated factor cannot
  overwrite the observations of the one it replaces.
- **family** -> `family`. `V2-P3-008`'s redundancy analysis groups by it, and `009`-`013`
  populate one each.
- **required fields** -> `required_fields`. `V2-P3-002`'s coverage check: which panel columns
  must be present and non-null before a value may be produced.
- **lookback window** -> `lookback_sessions`. Point-in-time: a 120-session momentum factor
  needs 120 sessions **at or before `as_of`**, and a security with fewer gets
  `insufficient_history` rather than a quiet `None`.
- **direction** -> `direction`. `V2-P3-005`'s IC sign; a rank correlation is uninterpretable
  until somebody has said which end of the cross section is the good one.

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

## What is deliberately *not* here

**No formula.** A `FactorDefinition` is data: it must survive `model_dump(mode="json")` to be
hashed, and a callable does not. The evaluator that turns a window of panel rows into a number
lives in `openalpha_cn.panel_factors` beside the engine that calls it, and the two tables are
bound at run time -- `FACTOR_DEFINITIONS` and `FACTOR_EVALUATORS` must have identical key sets,
and the engine refuses a definition with no evaluator. That binding exists because of a
measured failure elsewhere in this repository: a table gained a key with no branch behind it
and the command exited 0 with an empty result.

**No concrete factor family.** `V2-P3-009`..`013` own those. What ships here is the contract
and the closed vocabularies; the one definition that exists today is `panel_factors`' own
verification factor, and it says so in its own docstring.

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
It still validates the one invariant that would otherwise be a silent lie -- a `computed`
observation carries a value and a non-`computed` one does not.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from openalpha_cn.domain._identity import stable_model_id
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
- **`input_missing`** -- enough sessions, but at least one required `(dataset, column)` is null
  on one of them, or the security has no row in one of the required datasets on a session the
  others cover. `daily_basic` omitting Beijing-board names on historical sessions
  (`panel_ingest.load_daily_valuations` measured 60 of 3,843 on 2020-03-02) is the shape.
- **`undefined_value`** -- every input was present and the arithmetic still has no answer: a
  zero denominator, or a result that is not finite. Kept separate from `input_missing` because
  the remedy is different -- one is a fetch, the other is the factor's own definition.
- **`computed`** -- and only then is `value` not `None`.
"""

FACTOR_COVERAGE_CODES: Final[frozenset[str]] = frozenset(get_args(FactorCoverage))
"""`FactorCoverage`'s members as data. Closed for the reason `READINESS_ISSUE_CODES` is:
`V2-P3-007`'s coverage report groups by them and `V2-P3-005` has to exclude the non-`computed`
ones from a correlation rather than treat them as zeros.
"""

MAX_FACTOR_KEY_LENGTH: Final[int] = 48


class FactorField(BaseModel):
    """One panel column a factor reads: `FactorField(dataset="daily", column="close")`.

    Both halves are validated with the **panel plane's own** identifier rules rather than with
    a local regular expression, so a field reference that could not name a real partition
    column cannot be declared at all: `validate_panel_dataset` is what `PanelStore` applies to
    a directory name and `validate_panel_identifier` is what `PanelColumn` applies to a column
    name before it reaches DDL. A definition is data that a later engine turns into SQL, and
    validating it here means the refusal names the *definition* rather than surfacing several
    layers down as a binder error.
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
    """One versioned factor: the six properties, and nothing that cannot be hashed.

    See this module's docstring for the property-by-property argument and for why identity is
    `stable_model_id` rather than a hash of this module's own devising.
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
    lookback_sessions: int = Field(ge=1, le=2000)
    """How many sessions of visible history the factor needs at `as_of`, inclusive.

    Sessions of the session-indexed panel, not calendar days: a 120-session momentum factor
    needs 120 *open* sessions, and the calendar is what says which those are. `1` means "this
    session only".

    The upper bound is 2000, a little over eight A-share years, because a window wider than the
    panel's own partition granularity is a request the engine cannot serve from the years a
    caller names without that being visible; see `panel_factors.compute_factor`'s `years`
    argument. What this field deliberately does *not* express is a *report-period* reach -- an
    EP factor needs "the latest filing knowable at `as_of`", which is not a session count.
    `V2-P3-009`..`011` will need a second dimension for that, and adding one now would be a
    field with no reader.
    """
    summary: str = Field(min_length=1, max_length=2000)

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

    def columns_of(self, dataset: str) -> tuple[str, ...]:
        """The columns this factor reads from `dataset`, in declared order."""
        return tuple(item.column for item in self.required_fields if item.dataset == dataset)


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
    """One `(dataset, year)` partition a build read, and what it got out of it.

    This is `V2-P3-002`'s "input reference" at the granularity the panel plane can actually
    prove. Two of the fields are re-provable facts about the *partition* rather than
    descriptions of it:

    - `batch_digest` is `PartitionCoverage.batch_digest`, i.e. the `ColumnarPanelBatch`
      `content_digest` the provider's own batch carried, which additionally covers
      `provider_id`, `kind`, `as_of`, `fetched_at`, `source_uri` and `schema_version`. It
      answers "is this partition still what that provider sent at that point in time".
    - `partition_content_hash` is `PartitionRef.content_hash`, which covers
      `(dataset, year, column names and SQL types, rows)`. It answers "is this the same write".

    Both are kept because neither replaces the other; `panel/catalog.py::PartitionCoverage`
    argues that at length and a rebuild that carried only one of them would inherit exactly the
    confusion that argument exists to prevent.

    `visible_row_count` and `withheld_row_count` are the two halves of the point-in-time read:
    how many rows of the partition were at or before `as_of` on the availability clock, and how
    many were held back. The second number is the one that makes a short read *stated* rather
    than inferred -- see `panel/store.py::PanelStore.read_visible_at`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dataset: str = Field(min_length=1, max_length=64)
    year: int = Field(ge=1900, le=2999)
    batch_digest: str = Field(min_length=1, max_length=256)
    partition_content_hash: str = Field(min_length=1, max_length=256)
    visible_row_count: int = Field(ge=0)
    withheld_row_count: int = Field(ge=0)


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
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["factor-build-manifest/v1"] = "factor-build-manifest/v1"
    factor_id: str = Field(min_length=1, max_length=64)
    factor_key: str = Field(min_length=1, max_length=MAX_FACTOR_KEY_LENGTH)
    factor_version: int = Field(ge=1, le=999)
    as_of: datetime
    date_timezone: str = Field(min_length=1, max_length=64)
    code_commit: str = Field(min_length=7, max_length=64)
    lookback_sessions: int = Field(ge=1, le=2000)
    subject_count: int = Field(ge=1)
    """How many securities were asked about. At least one: a build over an empty cross section
    produces no observation and would leave a manifest describing nothing."""
    universe_count: int = Field(ge=0)
    """How many securities the caller declared to be in the cross section at `as_of`.

    Zero is legal and is not the same as a missing check: a universe that is genuinely empty at
    some historical `as_of` makes every observation `not_in_universe`, which is an answer. What
    is *not* legal is leaving it unstated -- `panel_factors.compute_factor` takes the universe
    as a mandatory argument for the reason `ReadinessRequirement`'s four checks have no
    defaults."""
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

    The one invariant enforced here is the one whose violation would be a silent lie: a
    `computed` observation carries a value and every other coverage code does not. Without it,
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

    def __post_init__(self) -> None:
        if not self.subject:
            raise FactorError("an observation must name a subject")
        # Normalised rather than merely required to be aware, because a stored observation
        # read back out of DuckDB arrives tagged with the *session's* timezone rather than UTC
        # (`domain/panel_batch.py` measured an instant written as `2024-06-28T07:00Z` reading
        # back as `America/Toronto`), and `V2-P3-005` groups observations by `as_of`. The same
        # instant under two labels is one dictionary key only after this line.
        object.__setattr__(self, "as_of", ensure_aware(self.as_of))
        if self.coverage not in FACTOR_COVERAGE_CODES:
            raise FactorError(
                f"{self.coverage!r} is not a declared coverage code; expected one of "
                f"{sorted(FACTOR_COVERAGE_CODES)}"
            )
        if (self.value is None) == (self.coverage == "computed"):
            raise FactorError(
                f"{self.subject} at {self.as_of.isoformat()} reports coverage "
                f"{self.coverage!r} with value {self.value!r}; exactly the `computed` code "
                "carries a value, and every other code carries None"
            )
        if self.input_row_count < 0:
            raise FactorError("input_row_count cannot be negative")
        if (self.input_session_first is None) != (self.input_session_last is None):
            raise FactorError(
                "input_session_first and input_session_last are both present or both absent; "
                "a window with only one end is not a window"
            )
        if (
            self.input_session_first is not None
            and self.input_session_last is not None
            and self.input_session_first > self.input_session_last
        ):
            raise FactorError(
                f"the input window {self.input_session_first} to {self.input_session_last} runs "
                "backwards"
            )
