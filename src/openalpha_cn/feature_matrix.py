"""The versioned feature matrix (`V2-P4-012`): what a fit is given, read out of the panel plane.

Story S26 in one line -- *"Reproducible feature matrices with feature and universe versions"* --
and `V2-P4-011` named exactly what it left here, in `domain/alpha_model.py`'s own docstring:
*"`V2-P4-012` owns the versioned feature matrix. `feature_version` here is a declared string and
`feature_ids` is a declared list; the producer that reads the panel plane, resolves the universe
and stamps the version is that issue's."* Three things, and this module is all three.

## The grammar, and the two spellings it needs rather than one

A column of this matrix is not a factor. `reversal_1d/v1` is stored three times over -- raw, once
per declared transform, once per declared neutralisation -- and the three carry different numbers
for the same security at the same instant. So a column is a **(factor, tier, transform,
neutralisation)** tuple, and its `feature_id` spells all four:

    reversal_1d/v1@raw
    reversal_1d/v1@processed:cross_section_standard/v1
    reversal_1d/v1@neutralized:cross_section_standard/v1:industry_and_size/v1

Readable, and readable is the whole of what it is for: `feature_ids` travels onto every
`FeatureCrossSection`, every `AlphaModelArtifact` and every stored prediction, and an operator
reading a refusal has to be able to tell which column moved. The three spellings are the three
planes' own (`factor_obs_*`, `factor_proc_*`, `factor_neut*`), taken from
`backtest/factor_ic.py::FactorTier` rather than declared a second time here.

**And a readable handle cannot be the identity, which is measured rather than asserted.**
`FactorDefinition.qualified_key`'s own docstring says the quiet part: *"Two definitions can share
a `qualified_key` and differ in `factor_id` (a redefinition that forgot to bump `version`);
`FactorRegistry` is what refuses that, because it is a property of a collection and not of a
definition."* Two *builds* separated by a commit are not one collection, and
`factor_observation_dataset` is `factor_obs_<key>_v<n>`, so both of them write into one partition.
A 2-session reversal and a 9-session one therefore share every `feature_id` this grammar can
produce. `tests/unit/test_feature_matrix_grammar.py::
test_a_redefined_factor_that_kept_its_version_moves_the_version_and_not_the_ids` is that case, and
what separates the two is `FeatureSpec` addressing `factor_id`, `transform_id` and
`neutralization_id` -- the content addresses -- while the ids stay handles.

## What `feature_version` addresses, and what it deliberately does not

`stable_model_id(prefix="feat", model=FeatureSpec)`. One hash function, the one this repository
has; `V2-P4-037` files the defect a second canonicalisation would be, and
`test_the_feature_version_is_the_one_hash_function_this_repository_has` recomputes the value
through the shared helper so a bespoke `sha256` here would produce a different string.

It addresses the **recipe**: the ordered columns by content address, each column's tier, and the
missing-value policy. Every one of those is a determinant and each is measured moving the address
alone (`test_every_determinant_of_a_spec_moves_its_feature_version`), with the field set of the
dumped model asserted in the same test so a determinant that is *not* on the model fails there
rather than being discovered later. `stable_model_id` is called with no `exclude`, so nothing is
recorded-but-unaddressed.

It addresses **no data**. Two matrices built from one spec on two days, or against two stores,
share a `feature_version` -- which is correct, because a feature version is a property of a
declaration and `AlphaModelDeclaration.feature_version` is a field a model carries *before* it has
seen a store. What the data side is addressed by is `universe_version` below, and
`the_two_versions_do_not_address_the_stored_values_they_were_read_from` is the boundary written
down where a reader meets it.

**Declaration order is not a determinant.** `feature_spec` sorts, and `FeatureSpec` refuses a
column list that is not strictly increasing. The positional alignment
`domain/alpha_model.py::validate_feature_ids` requires is *derived* from the sorted ids, so the
order a caller typed is not a fact about the matrix, and an identity that moved for it would move
for nothing -- `set_digest`'s argument, and `FactorInputRef`'s defect read the other way.

## What a universe version means here

`set_digest` over the securities the registry lists on the session the cross section is about --
`backtest/candidate_ranking.py`'s `universe_digest` is the same measurement one plane over, and
reusing its meaning is what stops "universe" being two things in one pipeline.

**It survives `V2-P4-059`'s downward widening because it is measured on the resolved membership
and never on what the caller asked for.** That issue made `load_stock_universe` read every
lifecycle year the store holds *below* the earliest requested one, asked for or not, because the
partition is keyed by the year a security's life **changed** -- so `--year 2026` means "the
securities that listed or died in 2026", which on a real market is eleven names out of 5,545. A
universe version derived from `years` would therefore have been a version of the question rather
than of the answer: the same `years=(2026,)` names one market on a store whose earlier lifecycle
partitions are ingested and a much smaller one on a store whose are not.

Two tests pin it and neither does it alone, which is stated because either alone reads like it
does. `test_the_universe_version_is_the_market_and_not_the_year_that_was_asked_for` writes a 1996
lifecycle partition into a live store between two otherwise identical reads: the request does not
move, the market grows by a name and so does the version -- but so would a version derived from
`UniverseCompleteness.years_read`, which widened too.
`test_a_terminated_security_leaves_the_next_sessions_matrix_and_its_universe_version` is the other
direction: one store, one `years`, one `years_read`, two sessions whose markets differ by a
termination. Only `set_digest` over the listed set passes both.

## How the producer reaches the panel plane, and why that seam

`V2-P4-032` solved the neighbouring problem -- panel tiers into a `ComponentCrossSection` at one
`as_of` -- and its answer is taken here rather than re-argued. **This module opens no new way to
read.** Every value comes back through `load_factor_observations`,
`load_processed_factor_observations` or `load_neutralized_factor_observations`, the three
`read_visible_at` callers `V2-P3-002` and `V2-P3-019` built, so a row stamped after the requested
`as_of` is filtered out one layer down and never reaches this module; the calendar and the
registry come back through `panel_ingest`'s own loaders, which `V2-P4-076` and `V2-P4-061` moved
onto per-session and per-event-date reads precisely so that a cross section built at an earlier
instant is answerable at all.

There are **two `as_of`s in this module and the asymmetry is the point**, exactly as in
`load_shortlist_cross_section`:

    factor tiers ............. request.as_of         (what was knowable when you asked)
    calendar, registry ....... the resolved instant  (what the values themselves saw)

The resolved instant is the newest stored build every declared column shares, which is at or
before the requested one, so the second read is strictly more conservative than the first and
never less.

`newest_published_session` rather than the instant's own calendar day, and that is `V2-P4-077`
inherited rather than re-derived: a build stamped 00:30 on a Friday is about Thursday's close,
because a session becomes knowable at 16:30 and the factor inputs were clamped at the previous
one. Asking the registry for Friday would cut a matrix from a market its own values never saw.
The rule has one implementation, in `panel_ingest`, and each face translates its refusal into its
own vocabulary -- `shortlist_view._pricing_session` is the other caller and neither imports the
other.

**Where this module differs from that one, deliberately: the rows are the universe.**
`_component_cross_section` does *not* narrow its stored rows to the registry, on the ground that
`CrossSectionScreen._read_components` already drops a row for a security `universe` does not name,
and one rule in two places is two rules that can disagree. There is no second filter here:
`FeatureCrossSection` carries whatever rows it is given and `predict` answers about every one of
them, so a delisted security with a stale stored factor value would be scored. The row set is
therefore the listed set, and every listed security gets a row whether the store has values for it
or not -- which is `V2-P4-011`'s **scored or abstained, never absent** taken one layer up: a
security the panel has nothing for arrives as a row of `None` and is abstained on, rather than
vanishing from a matrix with nothing to say it was ever there.

## Preprocessing, which is this issue's and is inside the address

`domain/alpha_model.py` says `None` rather than a sentinel because *"an imputed value is a
decision and this contract is not where it is taken"*. This is where it is taken, and it is taken
by declaration:

- **`abstain`** -- a missing cell stays `None`. The default, and the only policy under which the
  matrix asserts nothing the panel did not measure.
- **`drop_security`** -- a security missing any declared cell is not a row. Cheapest to reason
  about and the most expensive in coverage; a matrix it empties is refused rather than returned.
- **`cross_section_median`** -- a missing cell takes the median of that column's admitted values
  **at the same instant**. Cross-sectional and therefore look-ahead-free by construction: the only
  inputs are cells of the same cross section, so no information from after `as_of` can enter. A
  time-series fill would need a read of earlier sessions and a whole-sample median would be a
  look-ahead outright; neither is offered, and that is the reason.

**A cell is missing when it is not `admitted`, not when it is `null`.** `TIER_ADMITTED_CODES` is
the table, `backtest/factor_ic.py`'s and not a copy of it, and it differs from "carries a number"
in exactly one cell: the processed tier stores a value under `imputed`, which that table does not
admit. So a transform's own imputation does not reach this matrix as a number --
`a_processed_value_the_transform_imputed_is_read_as_missing` is the entry that says so, and the
reason is that two imputations stacked make a cell nobody can attribute to either.

## What one instant costs, which is `V2-P4-013`'s problem and is stated here

Every section re-reads the calendar and the registry, at that section's own resolved instant, and
that is not an oversight to be cached away: an earlier section's registry read is a *different*
read, and sharing one would be the look-ahead this module is built to avoid. What it costs is
already measured next door. `load_stock_universe`'s own "Cost, measured rather than estimated"
records that readiness is assessed once per year read, so N lifecycle partitions cost N**2 coverage
lookups -- **4.0 s per call on a 36-year registry over a 5,545-security market** -- and it names
the consequence in advance: *"`factor build` and `shortlist run` now pay it too, and they pay it
per prediction instant... it would stop being right on a walk-forward with hundreds of instants."*

`build_feature_matrix` is that walk-forward's producer, so this is where that sentence comes due.
It is filed against `PanelStore` rather than worked around here, for the reason given there: the
remedy is one assessment plus N reads, which is a change to `read_if_ready`'s contract shared with
fourteen callers, and doing it locally would mean stepping around the fail-closed door every other
loader takes. Nothing here caches, and a matrix over hundreds of instants will be slow before it
is wrong -- which is the direction to be slow in.

## What is deliberately left to a named issue

- **`V2-P4-013`** owns the walk-forward split, purge and embargo, and owns turning these sections
  into a `TrainingSet`: the labels are `domain/labels.py`'s and the fold boundaries are that
  issue's. `FeatureMatrix` hands it dated cross sections and stops there.
- **`V2-P4-014`** owns the linear/ranking baseline. This module said it would be the first caller
  `require_declared_features` exists for; that issue delivered `backtest/alpha_baseline.py` and
  corrected the pointer, because `backtest-no-numeric-stack-or-panel-plane` lists
  `openalpha_cn.feature_matrix` among the modules forbidden to the whole `backtest/` package, so
  no study under it can call this function at all. The first caller is whichever issue first
  holds a declaration and a matrix together, which is a composition above both planes rather than
  a study on one -- see the two paragraphs on `require_declared_features` itself.
  `V2-P4-021` arrived there first, through `model_view._model_request`.
- **`V2-P4-016` landed** and nothing here moved: `AlphaModelArtifact` already carried
  `declaration.feature_version`, so a matrix's recipe reaches the artifact's digest as it stands.
  It answered the universe question **no**. `AlphaModelDeclaration` keeps its one version slot,
  because a `universe_version` is a property of the matrix that was *read* rather than of the
  recipe that was declared or of the fit that consumed it -- and the artifact already records
  what the universe cost it, as `training_example_count`. What that leaves open is stated where a
  reader of the model plane meets it, as
  `the_address_is_over_the_fit_and_not_over_the_rule_that_chose_it`: two universes producing the
  same number of rows, the same cutoff and the same coefficients are one address.
- **`V2-P4-017`** owns persistence. Nothing here is stored: a matrix is rebuilt from a spec, a
  store and an `as_of`, which is what "reproducible" in S26 means and is why there is no document
  and no `*_id` on this plane.
- **`V2-P4-021` landed and this module still carries no envelope and no renderer**, which is
  what keeping the face above it bought: `model_view` translates every `FeatureMatrixError` into
  its own `blocked` or `panel_unreadable` row and renders the answers, so the three-face drift
  `V2-P4-033` filed has one place to be prevented rather than three. What that issue did add here
  is one read -- `stored_cross_section_instants`, so a face can take a range of prediction days
  rather than one flag per instant.
- **`AlphaModelDeclaration.feature_version` stays a free string.** Narrowing it to
  `CONTENT_ADDRESS_PATTERN` would refuse a model whose features came from somewhere this producer
  is not, and `V2-P4-011` declared that field before this module existed. What binds the two
  instead is `require_declared_features`, which is a check at the join rather than a pattern on a
  field -- and the join is where the mismatch actually costs something.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal, Self, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, model_validator

from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES, FactorTier
from openalpha_cn.domain._identity import stable_model_id
from openalpha_cn.domain.alpha_model import (
    AlphaModelDeclaration,
    AlphaModelError,
    FeatureCrossSection,
    FeatureRow,
    validate_feature_ids,
)
from openalpha_cn.domain.factor import FactorDefinition, FactorError, set_digest
from openalpha_cn.domain.factor_neutralization import FactorNeutralizationSpec
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.stock_universe import StockUniverse, StockUniverseError
from openalpha_cn.domain.trading_calendar import TradingCalendar, TradingCalendarError
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FactorEngineError,
    load_factor_observations,
    load_processed_factor_observations,
)
from openalpha_cn.panel_ingest import (
    load_stock_universe,
    load_trading_calendar,
    newest_published_session,
)
from openalpha_cn.panel_neutralization import (
    NeutralizationEngineError,
    load_neutralized_factor_observations,
)

__all__ = [
    "FEATURE_DATE_ZONE",
    "FEATURE_MATRIX_LIMITATION_CODES",
    "FEATURE_MISSING_POLICIES",
    "FEATURE_SPEC_SCHEMA_VERSION",
    "FEATURE_VERSION_PREFIX",
    "KNOWN_FEATURE_MATRIX_LIMITATIONS",
    "FeatureColumn",
    "FeatureColumnRef",
    "FeatureMatrix",
    "FeatureMatrixBlockedError",
    "FeatureMatrixError",
    "FeatureMatrixLimitation",
    "FeatureMatrixRequest",
    "FeatureMatrixSection",
    "FeatureMatrixUnreadableError",
    "FeatureMissingPolicy",
    "FeatureSpec",
    "FeatureSpecError",
    "build_feature_matrix",
    "feature_spec",
    "load_feature_cross_section",
    "require_declared_features",
    "stored_cross_section_instants",
]

_T = TypeVar("_T")

FEATURE_DATE_ZONE: Final[str] = DEFAULT_DATE_TIMEZONE
"""The zone a stored build's instant is resolved to a session in.

`panel/catalog.py`'s own default rather than a `ZoneInfo` declared here, which is where
`shortlist_view.SHORTLIST_DATE_ZONE` and this one differ in form and not in value: that face
needs a `ZoneInfo` because it also takes an instant's calendar day, and this module never does --
every day it computes comes back from `newest_published_session`, which takes the zone by name.
Aliased rather than used inline so the one place a zone is decided is greppable.
"""

FEATURE_SPEC_SCHEMA_VERSION: Final[str] = "feature-spec/v1"
"""The version of the declaration a `feature_version` is taken over.

A `Literal` constant and **not** a `ContractVersions` registry, `domain/alpha_model.py`'s reason
restated: `domain/schema.py` exports the five stable v1 boundaries Implementation Decision 1
names, this is not one of them, and a version registry earns its migration machinery when
something has stored a row. Nothing stores a `FeatureSpec` -- `V2-P4-017` owns what is stored on
this chain, and what it stores is a prediction.
"""

FEATURE_VERSION_PREFIX: Final[str] = "feat"
"""The prefix every `feature_version` carries. See `domain/_identity.py`."""

FeatureMissingPolicy = Literal["abstain", "drop_security", "cross_section_median"]
"""What this matrix does with a cell the panel did not measure. See this module's docstring."""

FEATURE_MISSING_POLICIES: Final[tuple[FeatureMissingPolicy, ...]] = get_args(FeatureMissingPolicy)
"""The three policies as data, in declared order, so an audit can drive each one."""

TIER_SEPARATOR: Final[str] = "@"
SPEC_SEPARATOR: Final[str] = ":"
"""The two punctuation marks a `feature_id` is assembled from.

Named rather than inlined because `_feature_id` and every test that reads one apart have to
agree, and because neither may occur inside a `qualified_key`: `MAX_FACTOR_KEY_LENGTH`'s pattern
admits lowercase, digits and underscores only, so the two marks cannot be produced by a key and a
column's four parts are recoverable from its id.
"""


class FeatureMatrixError(RuntimeError):
    """Anything this module refuses, so a caller can catch the plane rather than the case.

    A `RuntimeError` to match `shortlist_view.ShortlistViewError` rather than
    `domain/alpha_model.py`'s `AlphaModelError`: the faults below are about a *store* and a
    request, which is what that hierarchy is for, while `AlphaModelError` is a malformed
    contract value and this module lets that one through untouched.
    """


class FeatureSpecError(FeatureMatrixError):
    """A declaration that is wrong before any store is opened."""


class FeatureMatrixUnreadableError(FeatureMatrixError):
    """The panel plane refused a read this matrix needs."""


class FeatureMatrixBlockedError(FeatureMatrixError):
    """The store answered and there is still no matrix to build."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureMatrixLimitation:
    """One named boundary on what a matrix built here can be trusted to say."""

    code: str
    detail: str


KNOWN_FEATURE_MATRIX_LIMITATIONS: Final[tuple[FeatureMatrixLimitation, ...]] = (
    FeatureMatrixLimitation(
        code="the_two_versions_do_not_address_the_stored_values_they_were_read_from",
        detail=(
            "`feature_version` addresses the recipe and `universe_version` addresses the market, "
            "and neither addresses a number. Two stores holding different values for one factor "
            "at one instant produce two different matrices under one pair of versions. What "
            "would close it is a digest over the cells, which `domain/factor.py::"
            "cross_section_digest` already computes per stored build and which "
            "`load_factor_observations` already checks each build against -- so the property "
            "'these values are the ones their manifest addresses' is enforced one layer down "
            "and is not restated here. Carrying a third address on the matrix was left to "
            "`V2-P4-016`, and that issue declined it: an artifact's digest is over what the fit "
            "consumed, and a matrix is neither stored nor addressed on this plane, so a third "
            "version here would be an address for something no reader can look up. The same gap "
            "is stated on the model plane as "
            "`the_address_is_over_the_fit_and_not_over_the_rule_that_chose_it`, and closing it "
            "means a digest over the training rows, which is `V2-P4-017`'s to store."
        ),
    ),
    FeatureMatrixLimitation(
        code="a_processed_value_the_transform_imputed_is_read_as_missing",
        detail=(
            "`TIER_ADMITTED_CODES` admits `processed` and not `imputed`, so a cell the declared "
            "transform filled arrives here as missing and this matrix's own policy decides it. "
            "That is deliberate -- two imputations stacked make a cell attributable to neither "
            "-- and it costs coverage: under `missing='abstain'` a security the transform had a "
            "number for is abstained on. `ProcessedFactorObservation.coverage` is what a caller "
            "would read to recover it, and admitting it would be a fourth knob on `FeatureSpec` "
            "rather than a default that could be changed quietly."
        ),
    ),
    FeatureMatrixLimitation(
        code="a_median_fill_is_measured_over_the_admitted_cells_of_one_instant_only",
        detail=(
            "`cross_section_median` is look-ahead-free because its only inputs are cells of the "
            "same cross section, and that is also its whole limitation: on an instant where a "
            "column is thin the median is taken over few securities, and the matrix records the "
            "policy rather than the sample it was computed on. A column with no admitted cell "
            "at all is refused instead of filled, because there is no median to take."
        ),
    ),
    FeatureMatrixLimitation(
        code="one_declared_column_missing_a_stored_build_refuses_the_whole_instant",
        detail=(
            "`_resolve_instant` requires every declared column to share one stored build "
            "instant, so a matrix over five columns is unbuildable at an instant where four "
            "were built and the fifth was not. That is `factor_view`'s "
            "`the_three_tiers_must_have_been_built_at_the_same_instants` across columns instead "
            "of across tiers and for its reason -- a row assembled from one factor's Friday and "
            "another's Monday is a row about two markets -- and the cost is that a partially "
            "built panel yields nothing rather than a narrower matrix. Screening on the subset "
            "that already shares an instant is the caller's remedy."
        ),
    ),
    FeatureMatrixLimitation(
        code="a_universe_version_says_who_was_listed_and_not_who_was_tradeable",
        detail=(
            "`StockUniverse.listed_on` is the registry's membership. A halted security, one at "
            "its price limit, and one under a risk warning are all listed, and this plane reads "
            "none of `suspend_d`, `stk_limit` or `namechange` -- `backtest/cross_section.py`'s "
            "tradeability filter is where that lives, one plane over, and it operates on a "
            "shortlist rather than on a training matrix. A model fitted here may therefore "
            "learn from securities it could not have traded; `V2-P4-013` is where a fold's "
            "eligibility rule would go if one is wanted."
        ),
    ),
)
"""What a matrix built here cannot be trusted to say.

`tests/unit/test_feature_matrix_rules.py::
test_the_known_feature_matrix_limitations_are_the_five_this_plane_declares` holds this tuple by
equality, and each entry is driven by a test of its own on one of the two planes below.
"""

FEATURE_MATRIX_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    item.code for item in KNOWN_FEATURE_MATRIX_LIMITATIONS
)


class FeatureColumnRef(BaseModel):
    """One column of the matrix, by content address -- the unit `feature_version` is taken over.

    Addresses rather than definitions, and that is the whole design of this model. Embedding a
    `FactorDefinition` would put its `FactorNote` prose inside the identity, which is the thing
    `domain/factor.py` moved *out* of every content address on purpose; embedding only
    `qualified_key` would give a redefinition that kept its version one address for two factors.
    `factor_id`, `transform_id` and `neutralization_id` are already the exact granularity -- each
    is `stable_model_id` over its own contract's fields, prose excluded -- so this model is four
    strings and a tier, and carries no validator of its own: what a legal combination of them is
    belongs to `FeatureColumn`, which is the only thing that builds one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["feature-column/v1"] = "feature-column/v1"
    feature_id: str
    tier: FactorTier
    factor_id: str
    transform_id: str | None = None
    neutralization_id: str | None = None


class FeatureSpec(BaseModel):
    """The recipe a matrix is built from, and the thing `feature_version` addresses.

    A pydantic model for `AlphaModelDeclaration`'s reason: `stable_model_id` takes a `BaseModel`,
    and this repository has one hash function. Everything that carries *data* on this plane --
    `FeatureColumn`, `FeatureMatrixRequest`, `FeatureMatrixSection`, `FeatureMatrix` -- is a
    frozen dataclass instead, which is `domain/alpha_model.py`'s split restated: Implementation
    Decision 31 forbids a per-row pydantic rebuild on a panel query path, a whole-market cross
    section is ~5,500 rows, so what is **addressed** is pydantic and what is **read** is not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["feature-spec/v1"] = "feature-spec/v1"
    columns: tuple[FeatureColumnRef, ...]
    missing: FeatureMissingPolicy = "abstain"

    @model_validator(mode="after")
    def validate_columns_are_a_legal_feature_list(self) -> Self:
        """`validate_feature_ids`' rules, reached through the contract that owns them.

        Imported from `domain/alpha_model.py` rather than restated, because the rule is that
        module's -- strictly increasing, non-blank, at most `MAX_FEATURE_COUNT` -- and a second
        copy here would be one check plus a place for this one to fall behind it. The refusal is
        re-raised as a `FeatureSpecError` so a caller of this module catches one hierarchy.
        """
        try:
            validate_feature_ids([item.feature_id for item in self.columns], role="a feature spec")
        except AlphaModelError as error:
            raise FeatureSpecError(str(error)) from error
        return self

    @property
    def feature_ids(self) -> tuple[str, ...]:
        """The declared columns' ids, which is the header every cross section aligns to."""
        return tuple(item.feature_id for item in self.columns)

    @property
    def feature_version(self) -> str:
        """This recipe's content address, through the one hash function this repository has."""
        return stable_model_id(prefix=FEATURE_VERSION_PREFIX, model=self)


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureColumn:
    """One column, resolved: the definitions a read needs and the id it answers to.

    The resolved twin of `FeatureColumnRef`, and the pair is `shortlist_view`'s
    `ShortlistRunRequest`/`ShortlistSpec` arrangement: the resolver holds real definitions
    because a loader needs them, and the addressed model holds ids because a hash must not move
    for prose.
    """

    definition: FactorDefinition
    tier: FactorTier
    transform: FactorTransformSpec | None = None
    neutralization: FactorNeutralizationSpec | None = None

    def __post_init__(self) -> None:
        if self.tier == "raw" and (self.transform is not None or self.neutralization is not None):
            raise FeatureSpecError(
                f"a raw column of {self.definition.qualified_key} carries a transform or a "
                "neutralisation; the raw tier is the factor's own values in its own units and "
                "no derived spec narrows that read, so a declared one would be recorded in the "
                "id and ignored by the loader"
            )
        if self.tier in {"processed", "neutralized"} and self.transform is None:
            raise FeatureSpecError(
                f"a {self.tier} column of {self.definition.qualified_key} names no transform; "
                "`load_processed_factor_observations` narrows one partition by exactly that "
                "spec, and a neutralised residual records the transform it was taken from"
            )
        if self.tier == "neutralized" and self.neutralization is None:
            raise FeatureSpecError(
                f"a neutralized column of {self.definition.qualified_key} names no "
                "neutralization; the residual partition is keyed by the factor alone and the "
                "spec is what narrows it"
            )
        if self.tier == "processed" and self.neutralization is not None:
            raise FeatureSpecError(
                f"a processed column of {self.definition.qualified_key} carries a "
                "neutralization, which no processed read consults"
            )

    @property
    def declared_transform(self) -> FactorTransformSpec:
        """The transform a derived read is narrowed by, refused at the read rather than assumed.

        `shortlist_view._declared_transform`'s form and its stated reason. `__post_init__` above
        already refuses a derived column that carries none, so this cannot fire through a
        constructed `FeatureColumn` -- and it is written anyway, because a bare `assert` is the
        same statement with `-O` able to delete it, and because a reader of `_rows_for` should
        find the precondition at the read rather than two calls away.
        """
        if self.transform is None:
            raise FeatureSpecError(
                f"a {self.tier} read of {self.definition.qualified_key} needs a transform to "
                "narrow the partition by, and this column carries none"
            )
        return self.transform

    @property
    def declared_neutralization(self) -> FactorNeutralizationSpec:
        """`declared_transform`'s twin, for the residual partition."""
        if self.neutralization is None:
            raise FeatureSpecError(
                f"a neutralized read of {self.definition.qualified_key} needs a neutralization "
                "to narrow the partition by, and this column carries none"
            )
        return self.neutralization

    @property
    def feature_id(self) -> str:
        """The handle this column travels under. See this module's docstring for the grammar."""
        parts = [f"{self.definition.qualified_key}{TIER_SEPARATOR}{self.tier}"]
        if self.transform is not None:
            parts.append(self.transform.qualified_key)
        if self.neutralization is not None:
            parts.append(self.neutralization.qualified_key)
        return SPEC_SEPARATOR.join(parts)

    @property
    def ref(self) -> FeatureColumnRef:
        """This column by content address, which is what `feature_version` is taken over."""
        return FeatureColumnRef(
            feature_id=self.feature_id,
            tier=self.tier,
            factor_id=self.definition.factor_id,
            transform_id=None if self.transform is None else self.transform.transform_id,
            neutralization_id=(
                None if self.neutralization is None else self.neutralization.neutralization_id
            ),
        )


def feature_spec(
    *, columns: Sequence[FeatureColumn], missing: FeatureMissingPolicy = "abstain"
) -> FeatureSpec:
    """Sort the declared columns and address them; refuse a column stated twice.

    Sorting rather than refusing an unsorted list, and refusing a repeat rather than
    de-duplicating it, and the asymmetry is deliberate: an order is not a claim, so normalising
    it costs a caller nothing and buys one address per recipe, while a repeated column *is* a
    claim -- that the matrix carries the same values in two positions -- and silently keeping one
    of them would answer a question nobody asked.
    """
    ordered = sorted(columns, key=lambda column: column.feature_id)
    seen: set[str] = set()
    for column in ordered:
        if column.feature_id in seen:
            raise FeatureSpecError(
                f"{column.feature_id} is declared twice; a matrix carrying one column in two "
                "positions is two copies of one measurement, and a fit would weight it twice"
            )
        seen.add(column.feature_id)
    return FeatureSpec(columns=tuple(column.ref for column in ordered), missing=missing)


def require_declared_features(declaration: AlphaModelDeclaration, spec: FeatureSpec, /) -> None:
    """Refuse a declaration whose `feature_version` is not this matrix's.

    What makes "versioned" executable rather than decorative. `AlphaModelDeclaration
    .feature_version` is a free string -- `V2-P4-011` declared it before this module existed, and
    narrowing it to `CONTENT_ADDRESS_PATTERN` would refuse a model whose features came from
    somewhere this producer is not -- so the binding is a check at the join instead of a pattern
    on a field. The join is also where a mismatch costs something: a model declaring one recipe
    and fitted on another produces an `AlphaModelArtifact` recording a `feature_version` its
    `feature_ids` did not come from, and `V2-P4-016` then addresses that artifact under a recipe
    nobody can re-derive -- the address is faithful to the declaration either way, which is
    exactly why the declaration has to be checked here rather than trusted there.

    It is a free function rather than a method on either side because neither contract may reach
    the other: `domain/alpha_model.py` is in `domain/`, which `domain-purity` forbids every
    sibling to, and this module reaches a store.

    **Its caller is `V2-P4-021`'s `model_view._model_request`, and `V2-P4-014` was never
    able to be the one this docstring first said it would be.**
    That issue delivered `backtest/alpha_baseline.py`, under `backtest/` for the reason
    `walk_forward.py` is -- everything in it is stdlib arithmetic over `domain/` contracts --
    and `backtest-no-numeric-stack-or-panel-plane` lists `openalpha_cn.feature_matrix` among the
    modules forbidden to that whole package. So a `backtest/` study structurally cannot call
    this, and the sentence was never achievable rather than merely unfulfilled. The caller is
    whoever first holds a declaration and a matrix in one place, which is a **composition** above
    both planes: `V2-P4-017` when a fit is persisted against the matrix it read, or `V2-P4-021`'s
    faces, whichever arrives first. `KNOWN_BASELINE_LIMITATIONS` carries the same correction on
    the other side of the seam, under the code naming what nothing checks about a declared
    feature version.
    """
    if declaration.feature_version != spec.feature_version:
        raise FeatureSpecError(
            f"{declaration.name} declares feature_version {declaration.feature_version!r} and "
            f"is offered a matrix built to {spec.feature_version!r} "
            f"({list(spec.feature_ids)}); a fit recorded under a recipe it did not consume is "
            "one nobody can rebuild"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureMatrixRequest:
    """One matrix's whole question: which columns, over which instants, out of which years.

    A frozen dataclass and not a pydantic model, `FeatureCrossSection`'s reason: nothing stores
    this and it is on a panel query path. `spec` is the addressed half and is computed here so a
    caller cannot hand a request one recipe and a fit another.
    """

    columns: tuple[FeatureColumn, ...]
    years: tuple[int, ...]
    exchange: str
    as_ofs: tuple[datetime, ...]
    missing: FeatureMissingPolicy = "abstain"

    def __post_init__(self) -> None:
        if not self.years:
            raise FeatureSpecError(
                "a feature matrix request names no year; the factor partitions are keyed by "
                "year and a read of none returns nothing to build a matrix out of"
            )
        if not self.as_ofs:
            raise FeatureSpecError(
                "a feature matrix request names no as_of; a matrix over no instant is an empty "
                "success, which is the shape this plane exists to make unavailable"
            )
        if list(self.as_ofs) != sorted(set(self.as_ofs)):
            raise FeatureSpecError(
                f"the requested instants {[item.isoformat() for item in self.as_ofs]} are not "
                "strictly increasing; a matrix is time-ordered by construction because "
                "`V2-P4-013` splits it in time, and a repeated instant is one cross section "
                "counted twice"
            )

    @property
    def spec(self) -> FeatureSpec:
        """This request's recipe, sorted and addressed."""
        return feature_spec(columns=self.columns, missing=self.missing)

    @property
    def feature_version(self) -> str:
        return self.spec.feature_version


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureMatrixSection:
    """One instant's cross section, and what it was cut from.

    A record rather than three return values, `ShortlistCrossSection`'s reason: the three are
    only meaningful together. `as_of` is the **resolved** instant -- the newest stored build
    every declared column shares -- and never the request's own, so a caller reading this back
    sees which cross section it actually got.
    """

    as_of: datetime
    session: date
    universe: tuple[str, ...]
    universe_version: str
    cross_section: FeatureCrossSection

    @property
    def subjects(self) -> tuple[str, ...]:
        """The securities this section carries rows for, which `missing` may narrow."""
        return self.cross_section.subjects


@dataclass(frozen=True, slots=True, kw_only=True)
class FeatureMatrix:
    """Every requested instant's cross section under one recipe.

    The unit S26 asks for. `feature_version` is one string for the whole matrix because the
    recipe does not vary across instants; `universe_version` is a digest over the sections'
    own `(session, universe)` pairs, because the market does.
    """

    spec: FeatureSpec
    sections: tuple[FeatureMatrixSection, ...]

    @property
    def feature_version(self) -> str:
        return self.spec.feature_version

    @property
    def feature_ids(self) -> tuple[str, ...]:
        return self.spec.feature_ids

    @property
    def universe_version(self) -> str:
        """A content address for the markets this matrix was cut from, session by session.

        `set_digest` over `"<session>:<that session's universe_version>"`, which is the existing
        helper applied to pairs rather than a second canonicalisation -- the sessions are
        distinct by construction, so a set is the right shape and the de-duplication cannot lose
        one. It is deliberately **not** a digest over the union of the memberships: a matrix
        whose market gained a security halfway through is a different matrix from one whose
        market held it throughout, and a union calls those two the same.
        """
        return set_digest(
            [
                f"{section.session.isoformat()}{SPEC_SEPARATOR}{section.universe_version}"
                for section in self.sections
            ]
        )


_StoredRow = tuple[str, float | None, str, datetime, tuple[str, ...]]
"""`(subject, value, coverage, as_of, the content addresses the row was written under)`.

The last field is what makes the check in `_declared_rows` possible: a stored observation carries
the `factor_id`, `transform_id` and `neutralization_id` of the specs that produced it, and the
loaders narrow by *partition* rather than by those addresses -- `load_factor_observations`' own
docstring says so ("The factor is the **dataset**, not a filter"). A build written by a redefined
factor that kept its version therefore lands in the partition this read opens.
"""


_PANEL_FAULTS: Final[tuple[type[Exception], ...]] = (
    FactorEngineError,
    NeutralizationEngineError,
    PanelStorageError,
    PanelBatchError,
    FactorError,
    StockUniverseError,
    TradingCalendarError,
)
"""Every refusal the panel plane raises through the five loaders this module calls.

`shortlist_view._PANEL_FAULTS`' arrangement: a tuple rather than a chain of `except` clauses, so
the translation into this module's vocabulary is written once, and a loader that starts raising a
kind not named here goes uncaught rather than being reported as something it is not.
"""


def _read(thunk: Callable[[], _T], *, what: str) -> _T:
    """Run one panel read, turning its refusal into this module's own.

    The message is kept verbatim rather than summarised, for `shortlist_view._read`'s reason: a
    panel refusal names the dataset, the instant and the issue codes, and a caller told only
    `unreadable` cannot act on it.
    """
    try:
        return thunk()
    except _PANEL_FAULTS as error:
        raise FeatureMatrixUnreadableError(f"{what} could not be read: {error}") from error


def _rows_for(
    store: PanelStore, column: FeatureColumn, *, years: Sequence[int], as_of: datetime
) -> tuple[_StoredRow, ...]:
    """One column's stored rows on its declared tier, projected to a common shape.

    `shortlist_view._rows_for`'s arrangement -- the three tier contracts walked once each, in the
    one function that knows which loader answers for which tier -- with each row's own content
    addresses carried along, because this plane checks them and that one had no reason to.
    """
    if column.tier == "raw":
        return tuple(
            (row.subject, row.value, row.coverage, row.as_of, (row.factor_id,))
            for row in _read(
                lambda: load_factor_observations(
                    store, column.definition, years=years, as_of=as_of
                ),
                what=f"the raw {column.definition.qualified_key} observations",
            )
        )
    transform = column.declared_transform
    if column.tier == "processed":
        return tuple(
            (
                row.subject,
                row.value,
                row.coverage,
                row.as_of,
                (row.source_factor_id, row.transform_id),
            )
            for row in _read(
                lambda: load_processed_factor_observations(
                    store, column.definition, transform, years=years, as_of=as_of
                ),
                what=f"the {transform.qualified_key} rows of {column.definition.qualified_key}",
            )
        )
    neutralization = column.declared_neutralization
    return tuple(
        (
            row.subject,
            row.value,
            row.coverage,
            row.as_of,
            (row.source_factor_id, row.source_transform_id, row.neutralization_id),
        )
        for row in _read(
            lambda: load_neutralized_factor_observations(
                store, column.definition, neutralization, years=years, as_of=as_of
            ),
            what=(
                f"the {neutralization.qualified_key} residuals of {column.definition.qualified_key}"
            ),
        )
    )


def _declared_addresses(column: FeatureColumn) -> tuple[str, ...]:
    """The content addresses a stored row of this column must carry, in the read's own order.

    Read off `FeatureColumnRef` rather than off the definitions again, so what this check
    compares against is literally what `feature_version` was taken over. Two derivations of
    "which specs is this column" is the shape that lets a matrix pass a check against one recipe
    and be addressed under another.
    """
    ref = column.ref
    return tuple(
        address
        for address in (ref.factor_id, ref.transform_id, ref.neutralization_id)
        if address is not None
    )


def _resolve_instant(by_column: Mapping[str, Sequence[_StoredRow]], *, as_of: datetime) -> datetime:
    """The one instant this cross section is at: the newest every declared column shares.

    `factor_view`'s `the_three_tiers_must_have_been_built_at_the_same_instants` and
    `shortlist_view._resolve_instant`, applied across the columns of a feature matrix, and the
    argument is theirs rather than a third one: a row assembled from one factor's Friday value
    and another's Monday value is a row about two markets, and a model fitted on it cannot
    attribute what it learned to either.

    Refused rather than reconciled, in both directions. Taking each column's own newest instant
    is the mixed row above; taking the newest instant every column *happens* to share would
    answer a matrix whose first column is a week stale with nothing to say so.
    """
    newest = {
        feature_id: max((instant for _s, _v, _c, instant, _a in rows), default=None)
        for feature_id, rows in by_column.items()
    }
    empty = sorted(feature_id for feature_id, instant in newest.items() if instant is None)
    if empty:
        raise FeatureMatrixBlockedError(
            f"no stored cross section of {empty} is visible at {as_of.isoformat()}. A feature "
            "matrix with a column of nothing is a model fitted on a constant -- build the tier "
            "first, or ask at an as_of a stored build had reached"
        )
    shared = {feature_id: instant for feature_id, instant in newest.items() if instant is not None}
    distinct = set(shared.values())
    if len(distinct) > 1:
        described = ", ".join(
            f"{feature_id} at {instant.isoformat()}"
            for feature_id, instant in sorted(shared.items())
        )
        raise FeatureMatrixBlockedError(
            f"the declared columns' newest stored cross sections visible at {as_of.isoformat()} "
            f"are {described}; they are not one cross section, and a feature row assembled from "
            "two of them is a row about two markets. Build every declared column at the same "
            "instant, or declare the subset that already shares one"
        )
    return distinct.pop()


def _admitted_cells(
    column: FeatureColumn, rows: Sequence[_StoredRow], *, instant: datetime
) -> dict[str, float]:
    """One column's usable numbers at `instant`, keyed by security.

    Two narrowings, and each is a different question. **The instant** is
    `shortlist_view._component_cross_section`'s: a year partition visible at an `as_of` holds
    every build up to it, so "the cross section at `as_of`" is not "the rows that came back".
    **The address** is this module's own: the loaders narrow by partition and not by content
    address -- `load_factor_observations` says so in its own docstring, "The factor is the
    **dataset**, not a filter" -- and `factor_obs_<key>_v<n>` is keyed by the factor's *handle*,
    so a redefinition that kept its version writes its builds into the partition this read
    opens. A row carrying somebody else's `factor_id` is refused rather than scored, which is
    what makes `feature_version` a claim about the numbers and not only about the declaration.

    A cell is kept when its coverage is **admitted**, which is `backtest/factor_ic.py`'s
    `TIER_ADMITTED_CODES` and not a copy of it. That table differs from "carries a number" in
    exactly one cell -- the processed tier's `imputed` -- and the consequence is written down in
    `a_processed_value_the_transform_imputed_is_read_as_missing`.
    """
    declared = _declared_addresses(column)
    admitted = TIER_ADMITTED_CODES[column.tier]
    cells: dict[str, float] = {}
    for subject, value, coverage, row_as_of, addresses in rows:
        if row_as_of != instant:
            continue
        if addresses != declared:
            raise FeatureMatrixBlockedError(
                f"{column.feature_id} is declared as {list(declared)} and the stored row for "
                f"{subject} at {instant.isoformat()} was written under {list(addresses)}; the "
                "partition is keyed by the factor's handle rather than by its content address, "
                "so a redefinition that kept its version files its builds here too. Bump the "
                "definition's version, or declare the definition these values came from"
            )
        if coverage in admitted and value is not None:
            cells[subject] = value
    return cells


def _session_for(instant: datetime, *, calendar: TradingCalendar) -> date:
    """The session a stored build is about: the newest one that had published at `instant`.

    `panel_ingest.newest_published_session`, which is `V2-P4-077`'s rule with one implementation
    -- a session becomes knowable at 16:30, so a build stamped 00:30 on a Friday is about
    Thursday's close, and a matrix cut from Friday's registry would be cut from a market its own
    values never saw. `shortlist_view._pricing_session` is the other caller; neither imports the
    other, and the refusal each one raises is its own plane's.
    """
    try:
        return newest_published_session(calendar, as_of=instant, date_timezone=FEATURE_DATE_ZONE)
    except TradingCalendarError as error:
        raise FeatureMatrixBlockedError(
            f"the stored cross section is at {instant.isoformat()}, and the exchange calendar "
            f"cannot say which session that instant belongs to: {error}. Extend the calendar "
            "over that year, or ask at an as_of inside the one it covers"
        ) from error


def _universe_for(registry: StockUniverse, *, session: date, instant: datetime) -> tuple[str, ...]:
    """Who the registry lists on the session this cross section is about, ascending.

    Ascending because `StockUniverse.listed_on` says so -- *"Every `ts_code` listed on `day`,
    **ascending**"*, over a `securities` tuple its own class docstring declares ascending by
    `ts_code`. This function wrote `sorted(...)` around that call and a mutation that removed it
    turned nothing red, which is the right answer to the question rather than a gap in the tests:
    the ordering is the registry's guarantee, and a second sort here is one guarantee stated
    twice with this copy the one that goes stale. `V2-P4-011` closed its only surviving mutant
    the same way, on the same ground.
    """
    try:
        listed = tuple(registry.listed_on(session))
    except StockUniverseError as error:
        raise FeatureMatrixBlockedError(
            f"the security registry cannot answer for {session.isoformat()}, which is the "
            f"session the stored cross section at {instant.isoformat()} is about: {error}"
        ) from error
    if not listed:
        raise FeatureMatrixBlockedError(
            f"the registry lists no security on {session.isoformat()}, which is the session the "
            f"stored cross section at {instant.isoformat()} is about. A feature matrix over an "
            "empty market has no row to carry; build the stock_basic partition over that year"
        )
    return listed


def _rows_after_preprocessing(
    *,
    universe: Sequence[str],
    columns: Sequence[FeatureColumn],
    cells: Mapping[str, Mapping[str, float]],
    missing: FeatureMissingPolicy,
    instant: datetime,
) -> tuple[FeatureRow, ...]:
    """The declared policy, applied to the universe's rows. See this module's docstring.

    One function for the three policies rather than three, because the census they share -- one
    row per listed security, aligned to the sorted columns -- is the part that must not drift
    between them, and a policy that built its own census could quietly answer about a different
    market.
    """
    grid: list[list[float | None]] = [
        [cells[column.feature_id].get(ts_code) for column in columns] for ts_code in universe
    ]
    if missing == "cross_section_median":
        for index, column in enumerate(columns):
            sample = [value for row in grid if (value := row[index]) is not None]
            if not sample:
                raise FeatureMatrixBlockedError(
                    f"{column.feature_id} has no admitted value anywhere in the market at "
                    f"{instant.isoformat()}, so `cross_section_median` has no median to take. A "
                    "column that is missing everywhere is one this instant cannot answer for"
                )
            fill = statistics.median(sample)
            for row in grid:
                if row[index] is None:
                    row[index] = fill
    kept = [
        (ts_code, values)
        for ts_code, values in zip(universe, grid, strict=True)
        if missing != "drop_security" or None not in values
    ]
    if not kept:
        raise FeatureMatrixBlockedError(
            f"`drop_security` left no security at {instant.isoformat()}: every one of the "
            f"{len(universe)} the registry lists is missing at least one declared feature. A "
            "matrix of no rows is an empty success; declare fewer columns, or take `abstain`, "
            "which hands the model a None and lets it say so"
        )
    return tuple(FeatureRow(ts_code=ts_code, values=tuple(values)) for ts_code, values in kept)


def stored_cross_section_instants(
    store: PanelStore,
    *,
    columns: Sequence[FeatureColumn],
    years: Sequence[int],
    as_of: datetime,
) -> tuple[datetime, ...]:
    """Every instant **every** declared column has a stored build at, visible at `as_of`.

    `V2-P4-021`'s addition and its caller is the only one: a walk-forward is intrinsically over
    many prediction days, so a face that made a caller name each instant would be a face nobody
    runs a schedule through. This is what lets `model evaluate` take a range of days and resolve
    it against what the store actually holds.

    **The intersection, not the union**, and that is `_resolve_instant`'s rule read forward rather
    than a second decision: an instant one column has a build at and another does not is one this
    module refuses to assemble a row from, so offering it as a candidate would only move the
    refusal later, past a labelling read that costs a partition per session.

    It answers `()` rather than raising when nothing is stored. An empty range is a statement
    about a *request* -- which days were asked for -- and this function is not told what the
    request was; the caller who knows compares the result against its own range and refuses with
    both endpoints in the message. `_resolve_instant` is where a column with nothing in it is
    refused, and it stays there.
    """
    if not columns:
        raise FeatureSpecError(
            "no column was declared, so there is no cross section for stored instants to be "
            "shared across; a matrix with no column is a model fitted on nothing"
        )
    per_column = [
        {
            row_as_of
            for _subject, _value, _coverage, row_as_of, _addresses in _rows_for(
                store, column, years=years, as_of=as_of
            )
        }
        for column in columns
    ]
    return tuple(sorted(set.intersection(*per_column)))


def load_feature_cross_section(
    store: PanelStore, request: FeatureMatrixRequest, *, as_of: datetime
) -> FeatureMatrixSection:
    """One instant of the matrix: the stored tiers, the market they were computed for, and both.

    The order of the reads is the look-ahead argument and is not an implementation detail:

    1. every declared column's stored rows, **at the requested `as_of`** -- `read_visible_at`
       filters out a build stamped after it, one layer down;
    2. the instant every column shares, which is at or before the requested one;
    3. the calendar and the registry, **at that instant** -- what the values themselves saw.

    Step 3 is strictly more conservative than step 1 and never less, which is the asymmetry
    `load_shortlist_cross_section` documents and the reason a fortnight-old cross section is
    honest rather than merely old.
    """
    by_column = {
        column.feature_id: _rows_for(store, column, years=request.years, as_of=as_of)
        for column in request.columns
    }
    instant = _resolve_instant(by_column, as_of=as_of)
    calendar = _read(
        lambda: load_trading_calendar(
            store, exchange=request.exchange, years=request.years, as_of=instant
        ),
        what=f"the {request.exchange} trading calendar",
    )
    session = _session_for(instant, calendar=calendar)
    registry = _read(
        lambda: load_stock_universe(store, years=request.years, as_of=instant, max_staleness=None),
        what="the security registry",
    )
    universe = _universe_for(registry, session=session, instant=instant)
    ordered = sorted(request.columns, key=lambda column: column.feature_id)
    cells = {
        column.feature_id: _admitted_cells(column, by_column[column.feature_id], instant=instant)
        for column in ordered
    }
    rows = _rows_after_preprocessing(
        universe=universe,
        columns=ordered,
        cells=cells,
        missing=request.missing,
        instant=instant,
    )
    return FeatureMatrixSection(
        as_of=instant,
        session=session,
        universe=universe,
        universe_version=set_digest(universe),
        cross_section=FeatureCrossSection(
            as_of=instant, feature_ids=request.spec.feature_ids, rows=rows
        ),
    )


def build_feature_matrix(store: PanelStore, request: FeatureMatrixRequest) -> FeatureMatrix:
    """Every requested instant's cross section, under one recipe.

    The requested instants are strictly increasing (`FeatureMatrixRequest.__post_init__`) and
    what they **resolve** to need not be distinct at all: two questions asked between one build
    and the next resolve to the same stored cross section, and two builds stamped either side of
    a session's 16:30 resolve to two instants about one session's market.

    Both are refused, by **session** rather than by instant, and the wider rule is the one worth
    having: same instant implies same session, so keying on the session catches the narrow case
    too, and the case only it catches is the one that matters -- two cross sections about one
    day's market are two observations of one day. `V2-P4-013` splits this matrix in time and
    cannot see a duplicate, so the two copies would land in two folds and the second would be an
    out-of-sample evaluation on a market the first had already fitted. It is also what lets
    `FeatureMatrix.universe_version` be a `set_digest` over `(session, universe)` pairs without a
    duplicate silently collapsing into one.
    """
    sections = tuple(
        load_feature_cross_section(store, request, as_of=as_of) for as_of in request.as_ofs
    )
    seen: dict[date, tuple[datetime, datetime]] = {}
    for section, asked in zip(sections, request.as_ofs, strict=True):
        if section.session in seen:
            first_asked, first_instant = seen[section.session]
            raise FeatureMatrixBlockedError(
                f"{first_asked.isoformat()} and {asked.isoformat()} resolve to the stored cross "
                f"sections at {first_instant.isoformat()} and {section.as_of.isoformat()}, and "
                f"both of those are about {section.session.isoformat()}; a matrix carrying one "
                "session's market twice offers one observation to two walk-forward folds. Ask "
                "at instants a session stands between, or ask once"
            )
        seen[section.session] = (asked, section.as_of)
    return FeatureMatrix(spec=request.spec, sections=sections)
