"""Layering proofs for `V2-P1-002`'s two new modules, and for `V2-P1-012`'s one.

`openalpha_cn.domain.panel_batch` is a *contract* both sides of the panel seam need:
`providers/` produces one, `openalpha_cn.panel_ingest` writes one. That is why it is in
`domain/` -- putting it in either `providers/` or `panel/` would create an edge between two
peers -- and `test_domain_purity_holds_against_every_dynamically_discovered_sibling_subpackage`
in `test_import_layering.py` already covers `domain` as a whole. What that file does not
cover, because neither module existed when it was written, is the new top-level
`openalpha_cn.panel_ingest`: its whole reason to exist is that it may import both `domain`
and `panel`, so its dependency set is worth pinning explicitly, exactly as
`test_batch_contracts_import_isolation.py` pins `openalpha_cn.batch_contracts`'s.

`panel_ingest` is a top-level module rather than `panel/ingest.py` because `V2-P1-001` pinned
`openalpha_cn.panel` as importing no sibling subpackage at all; see that module's docstring
for the full reasoning and the `batch_contracts.py` precedent it follows.

`openalpha_cn.panel_doctor` (`V2-P1-012`) is the same shape one layer further up: it reads the
catalog through `panel`, the dataset contracts through `domain`, and the requirement builders
and loaders through `panel_ingest`. Its dependency set is pinned below for the reason
`panel_ingest`'s is -- a top-level module's whole justification is *which* packages it is
allowed to join, and a justification that is not asserted is a comment.

`openalpha_cn.panel_gate` (`V2-P1-013`) is the third and the narrowest: it consumes the health
report and adds no reader of its own, so it reaches `panel_doctor`, `panel` (for `PanelStore`
and the catalog's timezone default) and `domain` (for `TradingCalendar`) -- and, notably, *not*
`panel_ingest`. That absence is worth pinning: a gate that built its own `ReadinessRequirement`
could ask a dataset a different question from the one its own reader asks, and the two verdicts
would drift.

`openalpha_cn.panel_view` (`V2-P1-016`) is the fourth and the widest, and the width is the
point rather than a slip: it is the shared face the CLI, the HTTP app and the SDK render their
answers through, so it joins all four of the others. What matters for this file is the
direction. The edges run `panel_view -> {panel_gate, panel_doctor, panel_ingest, panel,
domain}` and never back, so none of the three modules above gains a dependency by its
existence, and `openalpha_cn.panel`'s import closure is again unchanged. The absence worth
pinning here is the other one: `panel_view` must not reach `storage`, `runtime`, `providers`,
`api` or `product`. A rendering that could see a composition root or a credential would make
the answer depend on how the process was wired, and would put a provider token one exception
message away from a response body.

## Four hand-named modules is where an enumeration stops being safe

Each of the four tests above names one module, which was fine for one and is not fine for
four: `panel_*` is now an established pattern, none of `pyproject.toml`'s `lint-imports`
contracts makes a `panel_*` module a *source* (the three added for `backtest` name them only as
forbidden targets), and the architecture baseline covers `storage`,
`providers` and `models` -- so a *fifth* one could import `storage` or `providers` and nothing
in this repository would go red. `V2-P1-016`'s review found that gap. The last two tests in
this file close it the way `test_import_layering.py` closed the same gap for `domain`'s
sibling packages: discover the modules from the real directory structure, require each to
have a row in `PANEL_MODULE_DEPENDENCIES`, and check the live graph rather than a list
somebody has to remember to update.

## Package granularity is a review, not a detector

Everything above answers one question -- *which packages* may a top-level `panel_*` module join --
and `V2-P3-004` bought one reviewed row with it. What it cannot see is the thing those rows are
about. Measured against the tree as `P3` closed:

- `openalpha_cn.panel_ingest` is inside `_ALLOWED_FACTOR_DEPENDENCIES`, `panel_factors` reads
  five datasets, and adding `load_daily_bars` -- or any number of further loaders -- to that
  module leaves every test above green.
- `panel_neutralization` names **no** dataset anywhere in its own source. Its entire dataset
  reach is the two foreign ones it takes across the seam, `daily_basic` through
  `load_daily_valuations` and `index_member_all` through `load_industry_cross_section`
  (`load_industry_histories` until `V2-P4-028`), which are
  exactly the two `V2-P3-004` split the file in order to make visible. A package-granular table
  cannot see either of them, before or after the split.

The second half of this file is the standing instrument the first half is not, and it is two
instruments rather than one because a widening arrives by two different doors.

**`RESEARCH_PLANE_SEAM_IMPORTS` is the first door, at name granularity.** It records the exact
names each module takes from a sibling `panel_*` module, so a new `load_*` is a row somebody
edits rather than a package that was already allowed. This is the door the two acceptance
mutations come through, and it is the only one that sees a loader for a dataset the module
already reads.

**`RESEARCH_PLANE_DATASETS` is the second, at dataset granularity.** It records which of the
panel's fifteen upstream datasets each module can *name*, and which it can *reach* once the
seam is followed. This is the door a widening comes through when it does not touch the seam at
all -- a factor declaring `FactorField(dataset=ADJ_FACTOR_DATASET, ...)`, a `ReadinessRequirement`
built in place -- which is what `P4`'s walk-forward is most likely to do.

## Where the dataset instrument reads its answer, and what it cannot see

The candidates were: scan the `panel_ingest.load_*` calls; scan the `FactorField(dataset=...)`
literals; scan the `*_DATASET` constants a module references; or make each module declare a list.
The last was rejected because **the information is already in the tree and a declaration nobody
recomputes is the drift this repository keeps finding** -- `domain/` binds fifteen `*_DATASET`
scalars, the factor registry declares `FactorField(dataset=...)`, and the imports are written
down, so no `src/` module gains a manifest for this audit and the only `src/` edit is a docstring
in `panel_neutralization.py` that had said this instrument did not exist. The first two candidates
are each half an answer:
`panel_factors` does not call a loader at all (`compute_factor` takes each `ReadinessRequirement`
from its caller and reads through `PanelStore` directly), and `panel_neutralization` declares no
`FactorField` at all. Either scan alone reports one of the two modules as reading nothing.

So the reading is: **a module names a dataset when its source references a `domain/` constant
that resolves to that dataset's name, or contains a string literal equal to it; and it reaches a
dataset when it names it, or when a name it takes across the seam does, transitively.** Both
halves are computed from the tree, and `_dataset_naming_constants` resolves
`PERIOD_INDEXED_DATASETS` through `FINANCIAL_STATEMENT_DATASETS` down to the four scalars the way
a reader would.

Three blind spots, stated rather than discovered later:

1. **A dataset name that is computed is invisible.** The factor planes' own dataset names are
   `FACTOR_OBSERVATION_DATASET_PREFIX + key` and friends, which is why this audit is scoped to
   the fifteen **upstream** datasets and says so in `UPSTREAM_PANEL_DATASETS`. A module that
   built `"adj" + "_factor"` would defeat it.
2. **Naming is not reading, so the instrument over-approximates.** `panel_gate` names `daily`
   once, in `health.freshness.cadence == "daily"` -- a cadence, not a dataset. Counting every
   literal that equals a dataset name is the deliberate choice: the narrower rule (count a
   literal only in a `dataset=` keyword position) is blind to `panel_doctor`'s `DATASET_CADENCE`,
   whose **keys** are real dataset names, and a false red is a conversation where a false green
   is the failure this section exists to remove.
3. **A dataset-parametric reader contributes nothing to the closure.** `load_statement_histories`
   and `financial_statement_requirement` take `dataset` as an argument, so following either of
   them across the seam adds no dataset at all -- the identity is decided by the caller, and
   `panel_factors` and `panel_doctor` each name their statement datasets in their own source.
   That attribution is the right one, since the module that decides is the module to review, but
   it means the seam half of the instrument is silent about the four statement endpoints, and a
   caller that took `load_statement_histories` and named its dataset only through a value
   computed at run time would show an empty row.

## The drift this instrument could itself become

`Task 38`'s lesson is that a key added to a table without an implementation behind it is `exit 0`
and an empty result. Both tables here are asserted as **equalities** by
`_seam_import_violations` and `_dataset_reach_violations`, which return the differences in both
directions rather than raising, so a declared dataset a module cannot name is as red as an
undeclared one it can -- and both directions are driven, on mutated copies of the real sources,
by `test_a_dataset_declared_for_a_module_that_cannot_name_it_turns_this_audit_red` and
`test_a_seam_name_declared_that_nobody_imports_turns_this_audit_red`. The separating question --
can it tell a module that reads six datasets from one that reads eight -- is
`test_the_dataset_instrument_separates_six_datasets_from_eight`, which adds two loaders for two
datasets the factor engine does not read and reads the answer back.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import grimp

from openalpha_cn.panel_factors import FACTOR_DEFINITIONS

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src" / "openalpha_cn"

_ALLOWED_INTERNAL_DEPENDENCIES = {"openalpha_cn.domain", "openalpha_cn.panel"}
_ALLOWED_DOCTOR_DEPENDENCIES = _ALLOWED_INTERNAL_DEPENDENCIES | {"openalpha_cn.panel_ingest"}
_ALLOWED_GATE_DEPENDENCIES = _ALLOWED_INTERNAL_DEPENDENCIES | {"openalpha_cn.panel_doctor"}
_ALLOWED_VIEW_DEPENDENCIES = _ALLOWED_INTERNAL_DEPENDENCIES | {
    "openalpha_cn.panel_ingest",
    "openalpha_cn.panel_doctor",
    "openalpha_cn.panel_gate",
}
"""Stated in full rather than derived from `_ALLOWED_DOCTOR_DEPENDENCIES`.

The two sets share three entries today and that is a coincidence of the current design, not a
relationship: `panel_doctor` may reach `panel_ingest` because it reuses its requirement
builders, and `panel_view` may because it loads a calendar. Deriving one from the other means
a later issue that widened the doctor's allowlist would widen this module's too, silently and
without anyone having argued for it -- and this is the module that must reach the *fewest*
things it does not render, since it is the one the HTTP app imports.
"""

_ALLOWED_FACTOR_DEPENDENCIES = _ALLOWED_INTERNAL_DEPENDENCIES | {"openalpha_cn.panel_ingest"}
"""`openalpha_cn.panel_factors` (`V2-P3-002`), stated in full for the reason above.

It shares all three with `panel_doctor` and shares none of that module's reasons. It reaches
`domain` for the factor contracts and the columnar batch, `panel` for `PanelStore` and the
readiness vocabulary, and `panel_ingest` for the three writer helpers that turn a batch into a
partition (`write_panel_batch`, `merge_panel_batches`, `split_panel_batch_by_year`) -- not for a
requirement builder, which is the edge it deliberately does not use: `compute_factor` takes each
input dataset's `ReadinessRequirement` from its caller, so the question the engine puts to
`daily` is the one `daily_requirement` puts, and an engine that built its own could ask
something weaker. That is `panel_gate`'s argument, and the difference is that the gate can avoid
the import entirely while this module needs the same package for its writers.
"""

_ALLOWED_NEUTRALIZATION_DEPENDENCIES = _ALLOWED_INTERNAL_DEPENDENCIES | {
    "openalpha_cn.panel_ingest",
    "openalpha_cn.panel_factors",
}
"""`openalpha_cn.panel_neutralization` (`V2-P3-004`), stated in full for the reason above.

**It is the first row in this table with an edge to another top-level `panel_*` module, and that
edge is the point of the row rather than an awkwardness in it.** A neutralisation consumes a
`ProcessedFactorPanel`, which `panel_factors` owns, and shares that module's `FactorEngineError`,
its `EVENT_TIME_COLUMN`, its `FACTOR_PROVIDER_ID`, its census-column prefix and **both** of its
write-side drop guards -- `_refuse_to_drop_a_stored_build` on the catalog's stored build list and
`_refuse_a_merge_that_lost_a_stored_build` on the merge itself (`V2-P4-073`) -- so the alternative
to the edge was either a second copy of each or keeping the code inside a module that would then
be 4,900 lines.

**What the edge buys the audit is the reason `V2-P3-004` split the file at all.** This module
reaches `panel_ingest` for two things `panel_factors` deliberately does not:
`load_industry_cross_section` and `load_daily_valuations`, the readers of the two **foreign**
datasets
a neutralisation regresses against. Had this code stayed in `panel_factors`, that widening would
have been invisible here --
`openalpha_cn.panel_ingest` is already in `_ALLOWED_FACTOR_DEPENDENCIES` and this table records
dependencies at package granularity, so the factor engine would have silently gained two datasets
it has no business knowing about, with nothing to go red. A separate module made the widening a
row somebody had to approve, which is what this table is for.

**And that is the whole of what *this row* bought -- one review, at one moment.** It is not a
standing detector on datasets, because this row is package-granular in exactly the way the one
above it is: `openalpha_cn.panel_ingest` is now *inside* `_ALLOWED_NEUTRALIZATION_DEPENDENCIES`,
so a later commit that has `panel_neutralization` call `load_daily_bars` or `load_index_weights`
widens its reach with nothing **in this table** going red. This table sees **modules**. The guard
that sees datasets is a different instrument and is now in the second half of this file:
`RESEARCH_PLANE_SEAM_IMPORTS` refuses the new loader by name and `RESEARCH_PLANE_DATASETS`
refuses the dataset it would bring, and
`test_a_loader_added_to_the_neutralisation_turns_both_tables_red` drives exactly the widening
this paragraph used to describe as unpoliced.

The edge runs one way only. `panel_factors` does not import `panel_neutralization` -- its own row
above is an *equality*, so an edge back would fail that assertion rather than this comment.
"""

PANEL_MODULE_DEPENDENCIES: dict[str, set[str]] = {
    "openalpha_cn.panel_ingest": _ALLOWED_INTERNAL_DEPENDENCIES,
    "openalpha_cn.panel_doctor": _ALLOWED_DOCTOR_DEPENDENCIES,
    "openalpha_cn.panel_gate": _ALLOWED_GATE_DEPENDENCIES,
    "openalpha_cn.panel_view": _ALLOWED_VIEW_DEPENDENCIES,
    "openalpha_cn.panel_factors": _ALLOWED_FACTOR_DEPENDENCIES,
    "openalpha_cn.panel_neutralization": _ALLOWED_NEUTRALIZATION_DEPENDENCIES,
}
"""Every top-level `panel_*` module and the sibling packages it may join.

The six of them are 10,000-odd lines that sit *outside* `openalpha_cn/panel/` precisely so the
package can keep its zero-sibling-edge guarantee, which makes "which packages may this one
join" the whole justification for each of them being top-level at all. No `lint-imports`
contract has a `panel_*` module as a *source* -- the three `backtest` contracts name them as
forbidden targets, which constrains `backtest` and says nothing about these six -- so this table
and the tests below are the only thing standing there.

`test_every_top_level_panel_module_is_in_this_table_and_stays_inside_its_row` keeps it from
being the hand-maintained enumeration it looks like: the modules are discovered from the
directory, so a seventh one arrives red rather than unguarded.

What it does **not** do is see a dataset; `RESEARCH_PLANE_SEAM_IMPORTS` and
`RESEARCH_PLANE_DATASETS` below are the two instruments that do, and this module's docstring says
why one table cannot be both.
"""

UPSTREAM_PANEL_DATASETS: frozenset[str] = frozenset(
    {
        "adj_factor",
        "balancesheet",
        "cashflow",
        "daily",
        "daily_basic",
        "fina_indicator",
        "income",
        "index_classify",
        "index_daily",
        "index_member_all",
        "index_weight",
        "namechange",
        "stk_limit",
        "stock_basic",
        "suspend_d",
        "trade_cal",
    }
)
"""The sixteen dataset names the panel ingests from upstream, written out rather than derived.

This is the vocabulary `RESEARCH_PLANE_DATASETS` is written against, and writing it here is what
makes a sixteenth dataset a red rather than a silent widening of four rows at once:
`test_the_written_dataset_vocabulary_is_the_one_domain_declares` holds this set against every
`*_DATASET: Final[str]` `domain/` declares, so a new upstream dataset fails there first and the
rows below have to be re-read before it can pass.

Deliberately *not* including the derived planes. `factor_obs_<key>_v<n>`, `factor_manifest_`,
`factor_neut_` and `factor_neutmn_` are built by concatenation at run time
(`panel_factors.FACTOR_OBSERVATION_DATASET_PREFIX + ...`), so no static reading can enumerate
them, and they are the factor plane's *own* output rather than a reach into somebody else's
data -- which is the thing this audit is about.
"""


class DatasetReach(NamedTuple):
    """One module's dataset surface, split into the part it decides and the part it inherits.

    `named` is what the module's own source can say the name of. `reached` adds what the names
    it takes across the seam can say, transitively -- so `named` is the half a reviewer can
    change by editing this module and `reached` is the half that follows from what it imports.

    Two fields rather than one because they fail differently. `panel_view` has
    `named == frozenset()` and reaches all fifteen through `panel_doctor.dataset_health`: its
    `reached` can never go red, and its `named` goes red the moment that module starts deciding
    a dataset for itself instead of rendering somebody else's answer. `panel_neutralization` is
    the mirror -- `named` is empty and `reached` is exactly the two foreign datasets `V2-P3-004`
    exists to have made visible, so the difference between the two fields *is* that issue's
    claim, measured.
    """

    named: frozenset[str]
    reached: frozenset[str]


RESEARCH_PLANE_SEAM_IMPORTS: dict[str, frozenset[str]] = {
    "openalpha_cn.panel_ingest": frozenset(),
    "openalpha_cn.panel_doctor": frozenset(
        {
            "panel_ingest.adjustment_requirement",
            "panel_ingest.daily_basic_requirement",
            "panel_ingest.daily_requirement",
            "panel_ingest.financial_statement_requirement",
            "panel_ingest.index_price_requirement",
            "panel_ingest.index_weight_requirement",
            "panel_ingest.industry_membership_requirement",
            "panel_ingest.industry_tree_requirement",
            "panel_ingest.load_adjustment_histories",
            "panel_ingest.load_daily_bars",
            "panel_ingest.load_daily_valuations",
            "panel_ingest.load_index_prices",
            "panel_ingest.load_industry_histories",
            "panel_ingest.load_industry_trees",
            "panel_ingest.load_name_histories",
            "panel_ingest.load_statement_histories",
            "panel_ingest.load_stock_universe",
            "panel_ingest.load_suspensions",
            "panel_ingest.name_history_requirement",
            "panel_ingest.price_limit_requirement",
            "panel_ingest.stock_universe_requirement",
            "panel_ingest.suspension_requirement",
            "panel_ingest.trading_calendar_requirement",
        }
    ),
    "openalpha_cn.panel_gate": frozenset(
        {
            "panel_doctor.DatasetHealth",
            "panel_doctor.HEALTH_CODE_CATEGORY",
            "panel_doctor.HealthCategory",
            "panel_doctor.HealthFinding",
            "panel_doctor.HealthSeverity",
            "panel_doctor.PANEL_HEALTH_CODES",
            "panel_doctor.PanelHealthReport",
            "panel_doctor.panel_health_report",
        }
    ),
    "openalpha_cn.panel_view": frozenset(
        {
            "panel_doctor.HEALTH_CATEGORIES",
            "panel_doctor.HEALTH_SEVERITIES",
            "panel_doctor.HealthFinding",
            "panel_doctor.PanelHealthReport",
            "panel_doctor.dataset_health",
            "panel_gate.DependencyClearance",
            "panel_gate.DependencyRequest",
            "panel_ingest.load_trading_calendar",
        }
    ),
    "openalpha_cn.panel_factors": frozenset(
        {
            "panel_ingest.carry_stored_rows_forward",
            "panel_ingest.merge_panel_batches",
            "panel_ingest.split_panel_batch_by_year",
            "panel_ingest.write_panel_batch",
        }
    ),
    "openalpha_cn.panel_neutralization": frozenset(
        {
            "panel_factors.EVENT_TIME_COLUMN",
            "panel_factors.FACTOR_CENSUS_COLUMN_PREFIX",
            "panel_factors.FACTOR_PROVIDER_ID",
            "panel_factors.FactorEngineError",
            "panel_factors.ProcessedFactorPanel",
            "panel_factors._refuse_a_merge_that_lost_a_stored_build",
            "panel_factors._refuse_rows_that_are_not_the_answers_their_manifest_addresses",
            "panel_factors._refuse_to_drop_a_stored_build",
            "panel_factors.appended_to_the_stored_year",
            "panel_ingest.load_daily_valuations",
            "panel_ingest.load_industry_cross_section",
            "panel_ingest.merge_panel_batches",
            "panel_ingest.split_panel_batch_by_year",
            "panel_ingest.write_panel_batch",
        }
    ),
    "openalpha_cn.factor_view": frozenset(
        {
            "panel_factors.FACTOR_DEFINITIONS",
            "panel_factors.FACTOR_TRANSFORMS",
            "panel_factors.FactorEngineError",
            "panel_factors.FactorPanel",
            "panel_factors.ProcessedFactorPanel",
            "panel_factors.apply_factor_transform",
            "panel_factors.compute_factor",
            "panel_factors.factor_observation_dataset",
            "panel_factors.load_factor_observations",
            "panel_factors.load_processed_factor_observations",
            "panel_factors.processed_factor_dataset",
            "panel_factors.write_factor_panels",
            "panel_factors.write_processed_factor_panels",
            "panel_ingest.daily_basic_requirement",
            "panel_ingest.daily_requirement",
            "panel_ingest.financial_statement_requirement",
            "panel_ingest.index_price_requirement",
            "panel_ingest.load_adjustment_histories",
            "panel_ingest.load_daily_bars",
            "panel_ingest.load_name_histories",
            "panel_ingest.load_price_limits",
            "panel_ingest.load_stock_universe",
            "panel_ingest.load_suspensions",
            "panel_ingest.load_trading_calendar",
            "panel_neutralization.FACTOR_NEUTRALIZATIONS",
            "panel_neutralization.NeutralizationEngineError",
            "panel_neutralization.NeutralizedFactorPanel",
            "panel_neutralization.apply_factor_neutralization",
            "panel_neutralization.load_industry_market_cap_cross_section",
            "panel_neutralization.load_neutralized_factor_observations",
            "panel_neutralization.neutralized_factor_dataset",
            "panel_neutralization.write_neutralized_factor_panels",
            "panel_view.PANEL_STORE_PLACEHOLDER",
            "panel_view.panel_store",
        }
    ),
    "openalpha_cn.feature_matrix": frozenset(
        {
            "panel_factors.FactorEngineError",
            "panel_factors.load_factor_observations",
            "panel_factors.load_processed_factor_observations",
            "panel_ingest.load_stock_universe",
            "panel_ingest.load_trading_calendar",
            "panel_ingest.newest_published_session",
            "panel_neutralization.NeutralizationEngineError",
            "panel_neutralization.load_neutralized_factor_observations",
        }
    ),
    "openalpha_cn.model_view": frozenset(
        {
            "feature_matrix.FeatureColumn",
            "feature_matrix.FeatureMatrix",
            "feature_matrix.FeatureMatrixError",
            "feature_matrix.FeatureMatrixRequest",
            "feature_matrix.FeatureMatrixSection",
            "feature_matrix.FeatureMatrixUnreadableError",
            "feature_matrix.FeatureMissingPolicy",
            "feature_matrix.FeatureSpec",
            "feature_matrix.build_feature_matrix",
            "feature_matrix.load_feature_cross_section",
            "feature_matrix.require_declared_features",
            "feature_matrix.stored_cross_section_instants",
            "panel_factors.FACTOR_DEFINITIONS",
            "panel_factors.FACTOR_TRANSFORMS",
            "panel_factors.FactorEngineError",
            "panel_ingest.load_adjustment_histories",
            "panel_ingest.load_daily_bars",
            "panel_ingest.load_price_limits",
            "panel_ingest.load_stock_universe",
            "panel_ingest.load_suspensions",
            "panel_ingest.load_trading_calendar",
            "panel_neutralization.FACTOR_NEUTRALIZATIONS",
            "panel_neutralization.NeutralizationEngineError",
            "panel_view.PANEL_STORE_PLACEHOLDER",
            "panel_view.panel_store",
        }
    ),
    "openalpha_cn.shortlist_compare": frozenset(
        {
            "shortlist_view.SHORTLIST_VIEW_SCHEMA_VERSION",
            "shortlist_view.ShortlistDocumentStore",
            "shortlist_view.ShortlistRequestError",
            "shortlist_view.held_shortlist",
        }
    ),
    "openalpha_cn.shortlist_view": frozenset(
        {
            "panel_factors.FACTOR_DEFINITIONS",
            "panel_factors.FACTOR_TRANSFORMS",
            "panel_factors.FactorEngineError",
            "panel_factors.factor_observation_dataset",
            "panel_factors.load_factor_observations",
            "panel_factors.load_processed_factor_observations",
            "panel_factors.processed_factor_dataset",
            "panel_ingest.load_daily_bars",
            "panel_ingest.load_name_histories",
            "panel_ingest.load_price_limits",
            "panel_ingest.load_stock_universe",
            "panel_ingest.load_suspensions",
            "panel_ingest.load_trading_calendar",
            "panel_ingest.newest_published_session",
            "panel_neutralization.FACTOR_NEUTRALIZATIONS",
            "panel_neutralization.NeutralizationEngineError",
            "panel_neutralization.load_neutralized_factor_observations",
            "panel_neutralization.neutralized_factor_dataset",
            "panel_view.PANEL_STORE_PLACEHOLDER",
            "panel_view.panel_store",
        }
    ),
}
"""`PANEL_MODULE_DEPENDENCIES` at the granularity the rows are actually about.

`panel_factors`' three entries are the argument its `_ALLOWED_FACTOR_DEPENDENCIES` docstring
already makes -- three writer helpers, and deliberately not a requirement builder -- turned from
prose into a check. That docstring says the engine reaches `panel_ingest` "for the three writer
helpers ... not for a requirement builder, which is the edge it deliberately does not use", and
until this table existed nothing measured the "not". A fourth name in that row is now a diff.

`panel_neutralization`'s twelve are the row `V2-P3-004` argued for, unrolled. Two of them --
`load_daily_valuations` and `load_industry_cross_section` -- are the whole of that issue's
foreign
reach, and seven more are the shared vocabulary that made the split cheaper than a second copy.
The seventh, `_refuse_rows_that_are_not_the_answers_their_manifest_addresses`, is `V2-P3-019`'s
seal check reused rather than re-written on the third tier, and it arrived here as a diff on this
row -- which is what this table is for.

`factor_view`'s row is the one that has already moved twice. `V2-P3-019` gave it
`openalpha factor build`, so eleven of its names -- `compute_factor`,
`apply_factor_transform`, `apply_factor_neutralization`, the three `write_*_panels` writers, the
two panel types, `load_industry_market_cap_cross_section` and the two requirement builders --
are ones a *renderer* would not have. That is a face that now writes the tiers it used to only
read, and it arrived here as thirteen lines on this row rather than as a package edge nothing
measured, which is the whole reason the table is at name granularity.

**`shortlist_compare` is the emptiest row on the panel side and that is its whole claim**
(`V2-P4-007`). It takes four names, all off `openalpha_cn.shortlist_view`, and **not one**
`panel_*` name: a comparison of two *published answers* reads two documents out of a byte
store and touches no partition, no calendar and no registry. `model_view` was the first row
that was not all `panel_*`; this is the first that is none of it, and the row is what makes
"it reads no panel data" a diff rather than a sentence -- a later version of that module that
reached for a loader to re-price something would arrive here before it arrived anywhere else.

The values are `"<sibling module stem>.<name>"` rather than fully qualified, because every
importer and every import target in this table is a top-level `openalpha_cn.panel_*` module by
construction and the prefix would be the same fifty-two times.
"""

RESEARCH_PLANE_DATASETS: dict[str, DatasetReach] = {
    "openalpha_cn.panel_ingest": DatasetReach(
        named=UPSTREAM_PANEL_DATASETS, reached=UPSTREAM_PANEL_DATASETS
    ),
    "openalpha_cn.panel_doctor": DatasetReach(
        named=UPSTREAM_PANEL_DATASETS, reached=UPSTREAM_PANEL_DATASETS
    ),
    "openalpha_cn.panel_gate": DatasetReach(
        named=frozenset({"daily"}), reached=UPSTREAM_PANEL_DATASETS
    ),
    "openalpha_cn.panel_view": DatasetReach(named=frozenset(), reached=UPSTREAM_PANEL_DATASETS),
    "openalpha_cn.panel_factors": DatasetReach(
        named=frozenset(
            {
                "balancesheet",
                "cashflow",
                "daily",
                "daily_basic",
                "fina_indicator",
                "income",
                "index_daily",
            }
        ),
        reached=frozenset(
            {
                "balancesheet",
                "cashflow",
                "daily",
                "daily_basic",
                "fina_indicator",
                "income",
                "index_daily",
            }
        ),
    ),
    "openalpha_cn.panel_neutralization": DatasetReach(
        named=frozenset(),
        reached=frozenset({"daily_basic", "index_member_all"}),
    ),
    "openalpha_cn.factor_view": DatasetReach(
        named=frozenset(
            {
                "adj_factor",
                "balancesheet",
                "cashflow",
                "daily",
                "daily_basic",
                "fina_indicator",
                "income",
                "index_daily",
                "namechange",
                "stock_basic",
            }
        ),
        reached=frozenset(
            {
                "adj_factor",
                "balancesheet",
                "cashflow",
                "daily",
                "daily_basic",
                "fina_indicator",
                "income",
                "index_daily",
                "index_member_all",
                "namechange",
                "stk_limit",
                "stock_basic",
                "suspend_d",
                "trade_cal",
            }
        ),
    ),
    "openalpha_cn.feature_matrix": DatasetReach(
        named=frozenset(),
        reached=frozenset({"stock_basic", "trade_cal"}),
    ),
    "openalpha_cn.model_view": DatasetReach(
        named=frozenset(
            {
                "adj_factor",
                "daily",
                "stk_limit",
                "stock_basic",
                "suspend_d",
                "trade_cal",
            }
        ),
        reached=frozenset(
            {
                "adj_factor",
                "balancesheet",
                "cashflow",
                "daily",
                "daily_basic",
                "fina_indicator",
                "income",
                "index_daily",
                "stk_limit",
                "stock_basic",
                "suspend_d",
                "trade_cal",
            }
        ),
    ),
    "openalpha_cn.shortlist_compare": DatasetReach(
        # V2-P4-007. The only row in this table that is empty in both directions, and the
        # only module in the discovered set that can say so: it reads two rendered answers
        # out of `ShortlistDocumentStore` and follows `held_shortlist`, whose whole reach is
        # `open_shortlist` and `stable_answer_digest` -- no loader, no dataset name, nothing
        # to close over. `panel_view` is empty on `named` and reaches all fifteen; this is
        # empty on both, which is the difference between a face that renders panel data and
        # one that compares two documents somebody else already produced.
        named=frozenset(),
        reached=frozenset(),
    ),
    "openalpha_cn.shortlist_view": DatasetReach(
        named=frozenset(
            {
                "daily",
                "namechange",
                "stk_limit",
                "stock_basic",
                "suspend_d",
                "trade_cal",
            }
        ),
        reached=frozenset(
            {
                "balancesheet",
                "cashflow",
                "daily",
                "daily_basic",
                "fina_indicator",
                "income",
                "index_daily",
                "namechange",
                "stk_limit",
                "stock_basic",
                "suspend_d",
                "trade_cal",
            }
        ),
    ),
}
"""Which of the fifteen upstream datasets each top-level research-plane module can touch.

Read the five rows that are worth reading:

**`panel_factors` names six and the twenty shipped factors now declare all six.** Until
`V2-P3-017` the module's reach was a *proper* superset of its registry's, and that was correct
rather than slack: `required_fields` is what `compute_factor` iterates, so the declared five were
what any *stored* build read, while the sixth -- `fina_indicator` -- was nameable because
`domain/factor.py::PERIOD_INDEXED_DATASETS` is defined as the four statement endpoints and the
module imports it to decide an axis. The gap was `panel_factors.py`'s own "Nothing here reads
`fina_indicator`" made checkable, and
`test_the_twenty_shipped_factors_declare_every_one_of_the_six_datasets_the_engine_can_name`
asserted it exactly so that a factor which started reading that endpoint would go red there
rather than pass silently. `deducted_earnings_yield_ttm` did start reading it, the assertion is
now an equality, and the row below never had to move -- which is what a reach table being a
superset is for.

**`panel_neutralization` names none and reaches two.** No dataset name appears anywhere in that
module's 2,172 lines; `daily_basic` and `index_member_all` arrive entirely through
`load_daily_valuations` and `load_industry_cross_section`. That difference is `V2-P3-004`'s
claim as
a measurement, and it is also why a scan of `FactorField(dataset=...)` literals alone would have
reported this module as reading nothing at all.

**`panel_gate` names exactly one, and it is not a dataset.** `health.freshness.cadence == "daily"`
compares against a *cadence*; the literal happens to equal `DAILY_DATASET`'s value. It is counted
because this instrument counts every literal that equals a dataset name -- see this module's
docstring, blind spot 2, for why the narrower rule is worse -- and it costs nothing here because
the gate's `reached` already covers `daily` through `panel_health_report`.

**`factor_view` names ten and reaches fourteen, and the two it does not reach are the row.**
`index_classify` and `index_weight` are the only upstream datasets left, and the shape of this
row is `V2-P3-019`'s doing: when this table was first written the face named *none* and reached
*eleven*, because it rendered stored tiers and its reach came from the seven `panel_ingest`
loaders a tradeability label needs. `openalpha factor build` made it a builder as well, so it
names the same six `panel_factors` does -- it imports `DAILY_DATASET`, `DAILY_BASIC_DATASET` and
`FINANCIAL_STATEMENT_DATASETS` to state the requirements a build must clear -- and picks up
`fina_indicator` and `index_member_all` in `reached`, the latter through
`load_industry_market_cap_cross_section`, which is the industry corpus the neutralisation
regresses against and which a renderer had no reason to touch.

**The last three all arrived as remedy strings rather than as reads**, and all three name a
dataset the face had already reached. `V2-P4-080` gave `_unnamed_session_refusal` the `openalpha
panel build --dataset namechange` line a caller needs, so the module imports `NAMECHANGE_DATASET`
to spell it; `V2-P4-084` did the same for `ADJ_FACTOR_DATASET` and `STOCK_BASIC_DATASET` in
`_LABEL_CORPUS_REMEDIES`, whose whole content is which partition each refusal is about and what
command repairs it. This table correctly calls that naming the dataset even though the face has
reached all three since this row was written, through `load_name_histories`,
`load_adjustment_histories` and `load_stock_universe`. `named` growing into `reached` is the
benign direction and the instrument does not distinguish it; what it is here to catch is `named`
or `reached` growing past `reached`'s declared set, which none of them did.

**Both counts in the heading above were wrong until `V2-P4-084` measured them.** It said "names
seven and reaches thirteen" against a row of eight and fourteen. Both were true when the row was
written -- at `7c59c1f` it held six and thirteen and the sentence said six and thirteen -- and
each half drifted once: `V2-P3-016` added `index_daily` to `reached` in the same commit that grew
`UPSTREAM_PANEL_DATASETS` from fifteen to sixteen, and `V2-P4-080` added `namechange` to `named`
while writing "seven" for a set of eight. Neither number is asserted anywhere, which is why both
survived: the row itself is checked against the source in both directions, and the sentence
describing the row is not. It is covered here at all because
`V2-P3-015` made `factor_*` a second top-level family and the glob above only knew about the
first; see `RESEARCH_PLANE_PREFIXES`.

**`feature_matrix` names none and reaches two, which is the narrowest row in the table and is
`V2-P4-012`'s whole claim about its own seam.** Every feature value it returns comes out of the
factor plane's *derived* partitions -- `factor_obs_*`, `factor_proc_*`, `factor_neut*` -- which are
built by concatenation at run time and are deliberately not in `UPSTREAM_PANEL_DATASETS` at all,
so the three observation loaders it takes across the seam contribute nothing here. What is left is
exactly what a matrix needs besides its numbers: `trade_cal` to say which session a stored build is
about, and `stock_basic` to say who was listed on it. A row that grew a price or valuation dataset
would mean this module had started deciding tradeability, which
`a_universe_version_says_who_was_listed_and_not_who_was_tradeable` says it does not.

**`panel_ingest`, `panel_doctor`, `panel_gate` and `panel_view` reach all fifteen**, and their
rows say `UPSTREAM_PANEL_DATASETS` rather than repeating it. That is a derived value in a table
that is otherwise written by hand, so it is worth being explicit about what it costs: a sixteenth
upstream dataset would widen those four rows without anybody arguing for it. What stops that is
`test_the_written_dataset_vocabulary_is_the_one_domain_declares`, which fails on the sixteenth
dataset before these rows are ever consulted -- one hop, and a red either way.
"""


def _build_graph() -> grimp.ImportGraph:
    return grimp.build_graph("openalpha_cn")


def _direct_internal_dependencies(module: str, graph: grimp.ImportGraph | None = None) -> set[str]:
    built = _build_graph() if graph is None else graph
    siblings = {
        name
        for name in built.modules
        if name.count(".") == 1 and name.startswith("openalpha_cn.") and name != module
    }
    return {
        sibling
        for sibling in siblings
        if built.direct_import_exists(importer=module, imported=sibling, as_packages=True)
    }


def _top_level_panel_modules() -> list[str]:
    """`src/openalpha_cn/panel_*.py`, discovered from the real directory structure.

    `test_import_layering.py`'s `_sibling_subpackages_of_domain()` for modules instead of
    packages, and for its reason: an enumeration written by hand is exactly the thing a later
    addition does not update, and the guarantee these modules carry is one every one of them
    has to carry individually.
    """
    return sorted(
        f"openalpha_cn.{path.stem}"
        for path in (ROOT / "src" / "openalpha_cn").glob("panel_*.py")
        if not path.stem.startswith("__")
    )


RESEARCH_PLANE_PREFIXES = ("panel_", "factor_", "shortlist_", "feature_", "model_")
"""The five top-level module families the research plane is built out of.

`_top_level_panel_modules()` above globs `panel_*.py` alone, which was the whole plane when it
was written and is not any more: `V2-P3-015` added `factor_view.py`, a *second* top-level family
with the same justification (it may join packages `openalpha_cn.panel` may not) and none of the
same discovery. It is covered today only because `test_factor_view_layering.py` names it by hand
-- so a **second** `factor_*.py`, which is what an issue after `V2-P3-015` would add, would be
guarded by nothing at all. The two instruments below discover from both prefixes, and
`test_every_top_level_module_is_a_declared_leaf_or_a_member_of_a_discovered_family` refuses a
top-level module that belongs to neither family and has not been declared a leaf.

**`shortlist_` is the third family, and it arrived exactly the way that test predicted it would.**
`TOP_LEVEL_MODULES_OUTSIDE_EVERY_PLANE_FAMILY`'s docstring names the shape in advance -- "a third
family -- `signal_view.py`, say -- which is exactly the shape `V2-P3-015` was, one issue earlier"
-- and `V2-P4-032` wrote `shortlist_view.py`, a research-plane module reading the stored factor
tiers back into the two-stage funnel's input. It went red on arrival, with a message saying which
of the two remedies to take, and this is the one it took: it is a research-plane module, so it
joins the discovered set and takes the rows below rather than a sentence excusing it from them.

**`feature_` is the fourth, and it arrived the same way.** `V2-P4-012`'s `feature_matrix.py`
reads the same three stored tiers back into a versioned feature matrix for the model chain, and
`test_every_top_level_module_is_a_declared_leaf_or_a_member_of_a_discovered_family` failed on it
before any of its own tests were written. It takes the two rows below, and it is named
`feature_*` rather than folded into `factor_*` deliberately: a column of that matrix is a
*(factor, tier, transform, neutralisation)* tuple rather than a factor, and renaming a module to
land inside an existing glob is how a family stops being a claim about what a module is.

**`model_` is the fifth, and it arrived red exactly as the test below predicts a fifth would.**
`V2-P4-021`'s `model_view.py` is the face that finally reaches `V2-P4-010`--`V2-P4-017`: it
composes a feature matrix, a walk-forward split, a fitted artifact and a prediction store into
`openalpha model evaluate` and `openalpha model daily-run`. It takes the two rows below rather
than a sentence in `TOP_LEVEL_MODULES_OUTSIDE_EVERY_PLANE_FAMILY`, which is the remedy that
test's own message names first and the one `shortlist_view.py` and `feature_matrix.py` both took.

Its `RESEARCH_PLANE_SEAM_IMPORTS` row is the first that is **not** all `panel_*`: twelve of its
twenty-five names come off `openalpha_cn.feature_matrix`, a research-plane sibling of its own.
That is what `_is_research_plane_stem` was widened for at `factor_view`'s arrival, and it is why
the table is at name granularity -- a face reaching a *producer* is a different edge from a face
reaching a loader, and both are now diffs on one row.
"""


def _top_level_research_plane_modules() -> list[str]:
    """`src/openalpha_cn/{panel,factor}_*.py`, discovered from the real directory structure."""
    return sorted(
        f"openalpha_cn.{path.stem}"
        for prefix in RESEARCH_PLANE_PREFIXES
        for path in SOURCE_ROOT.glob(f"{prefix}*.py")
        if not path.stem.startswith("__")
    )


def _research_plane_sources() -> dict[str, str]:
    """Every top-level research-plane module's source text, keyed the way the graph names it.

    Discovered by `_top_level_research_plane_modules()`, so this file can only disagree with the
    directory about which modules exist if the glob does. Returned as **text** and taken as an
    argument by everything below rather than read inside them, because that is what lets a test
    hand this audit a mutated copy of one module and watch it go red -- the difference between an
    assertion that holds today and one that has been shown to separate two answers.
    """
    return {
        module: (SOURCE_ROOT / f"{module.rpartition('.')[2]}.py").read_text(encoding="utf-8")
        for module in _top_level_research_plane_modules()
    }


def _domain_bindings() -> dict[str, tuple[ast.expr, ...]]:
    """Every name `domain/` binds at module level, and every expression bound to it.

    A tuple of values rather than one, because `domain/` is read as a single flat namespace and
    two modules may bind the same name: `INDUSTRY_LEVELS` is bound in both
    `factor_neutralization.py` and `industry_classification.py` today. Unioning the resolutions
    is the fail-closed reading -- a colliding name that named a dataset in *either* module would
    count -- and it needs no exemption for the one collision that names none.
    """
    bound: dict[str, list[ast.expr]] = {}
    for path in sorted((SOURCE_ROOT / "domain").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                bound.setdefault(node.target.id, []).append(node.value)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound.setdefault(target.id, []).append(node.value)
    return {name: tuple(values) for name, values in bound.items()}


def _dataset_naming_constants() -> dict[str, frozenset[str]]:
    """Each `domain/` constant mapped to the upstream datasets its own definition names.

    Resolved rather than listed, which is the point: `PERIOD_INDEXED_DATASETS` is a `frozenset`
    of `FINANCIAL_STATEMENT_DATASETS`, which is a tuple of four scalars, and a hand-written map
    from constant to dataset would be one more table to drift. Referring to a constant counts as
    naming everything it resolves to, so `panel_factors` importing `PERIOD_INDEXED_DATASETS`
    names the four statement endpoints -- which is why its row is six and not five.

    Memoised without regard to the cycle guard because a module-level constant cannot take part
    in one: a cycle among them would be a `NameError` at import rather than a wrong answer here.
    """
    bindings = _domain_bindings()
    resolved: dict[str, frozenset[str]] = {}

    def resolve(name: str, seen: frozenset[str]) -> frozenset[str]:
        if name in resolved:
            return resolved[name]
        if name in seen or name not in bindings:
            return frozenset()
        onward = seen | {name}
        found: set[str] = set()
        for value in bindings[name]:
            for inner in ast.walk(value):
                if isinstance(inner, ast.Constant) and inner.value in UPSTREAM_PANEL_DATASETS:
                    found.add(inner.value)
                elif isinstance(inner, ast.Name):
                    found |= resolve(inner.id, onward)
                elif isinstance(inner, ast.Attribute):
                    found |= resolve(inner.attr, onward)
        resolved[name] = frozenset(found)
        return resolved[name]

    return {name: resolve(name, frozenset()) for name in bindings}


def _datasets_named_in(node: ast.AST, constants: Mapping[str, frozenset[str]]) -> frozenset[str]:
    """The upstream datasets `node`'s subtree can say the name of.

    A bare string literal counts, and so does any reference to a `domain/` constant that resolves
    to one. See this module's docstring's blind spot 2 for why the literal half is deliberately
    over-inclusive; `panel_gate`'s cadence comparison is the one place it currently over-reports.
    """
    found: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and inner.value in UPSTREAM_PANEL_DATASETS:
            found.add(inner.value)
        elif isinstance(inner, ast.Name):
            found |= constants.get(inner.id, frozenset())
        elif isinstance(inner, ast.Attribute):
            found |= constants.get(inner.attr, frozenset())
    return frozenset(found)


def _is_research_plane_stem(stem: str) -> bool:
    return stem.startswith(RESEARCH_PLANE_PREFIXES)


def _seam_imports(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Local name to `(sibling module, name in it)` for every research-plane sibling import.

    Both families, so `factor_view` taking eighteen names off the panel plane is a row here
    rather than a blind spot. Only the `from ... import <name>` form, which is the only one in
    the tree and the only one a name-granular table can police;
    `test_no_research_plane_module_takes_a_whole_sibling_module_instead_of_names_from_it` is what
    keeps the other two forms from becoming the way around this table.
    """
    taken: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level or node.module is None:
            continue
        parts = node.module.split(".")
        if len(parts) == 2 and parts[0] == "openalpha_cn" and _is_research_plane_stem(parts[1]):
            for alias in node.names:
                taken[alias.asname or alias.name] = (parts[1], alias.name)
    return taken


def _whole_sibling_imports(tree: ast.Module) -> set[str]:
    """The two import forms that take a sibling's entire namespace instead of names from it.

    `import openalpha_cn.panel_ingest` and `from openalpha_cn import panel_ingest` both leave
    every loader one attribute access away, which would make `RESEARCH_PLANE_SEAM_IMPORTS` a table
    of nothing. Neither appears in the tree today and neither may.
    """
    taken: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            taken.update(
                stem
                for alias in node.names
                for stem in [alias.name.rpartition(".")[2]]
                if alias.name.startswith("openalpha_cn.") and _is_research_plane_stem(stem)
            )
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module == "openalpha_cn":
            taken.update(alias.name for alias in node.names if _is_research_plane_stem(alias.name))
    return taken


def _module_level_bindings(tree: ast.Module) -> dict[str, ast.stmt]:
    """Each name a module binds at top level, and the statement that binds it."""
    bound: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound[node.name] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound[node.target.id] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node
    return bound


def _research_plane_dataset_reach(sources: Mapping[str, str]) -> dict[str, DatasetReach]:
    """What each module in `sources` can name, and what it can reach once the seam is followed.

    Per symbol rather than per module on the far side of the seam, which is the difference
    between a useful answer and a vacuous one: `panel_neutralization` imports six names from
    `panel_factors`, and closing at module granularity would hand it that module's whole
    six-dataset reach for the sake of an error class and a column prefix. At symbol granularity
    those six contribute nothing and its row stays at the two datasets it really takes.

    Symbol-to-symbol edges are resolved by name -- a local variable that shadows a top-level
    function is followed as if it were that function -- so the closure over-approximates in the
    fail-closed direction. It is computed as a fixpoint rather than by recursion because two
    functions in `panel_ingest` may call each other and a depth-first walk would have to carry
    a cycle guard that silently truncates the answer; the loop reaches its fixpoint in three
    passes over the current tree.
    """
    constants = _dataset_naming_constants()
    trees = {module: ast.parse(text, filename=module) for module, text in sources.items()}
    seams = {module: _seam_imports(tree) for module, tree in trees.items()}
    bindings = {module: _module_level_bindings(tree) for module, tree in trees.items()}

    onward: dict[tuple[str, str], frozenset[tuple[str, str]]] = {}
    reach: dict[tuple[str, str], frozenset[str]] = {}
    for module, bound in bindings.items():
        stem = module.rpartition(".")[2]
        for symbol, node in bound.items():
            edges: set[tuple[str, str]] = set()
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Name) or inner.id == symbol:
                    continue
                if inner.id in bound:
                    edges.add((stem, inner.id))
                elif inner.id in seams[module]:
                    edges.add(seams[module][inner.id])
            reach[(stem, symbol)] = _datasets_named_in(node, constants)
            onward[(stem, symbol)] = frozenset(edges)

    widening = True
    while widening:
        widening = False
        for symbol, edges in onward.items():
            widened = set(reach[symbol])
            for target in edges:
                widened |= reach.get(target, frozenset())
            if widened != reach[symbol]:
                reach[symbol] = frozenset(widened)
                widening = True

    measured: dict[str, DatasetReach] = {}
    for module, tree in trees.items():
        named = _datasets_named_in(tree, constants)
        reached = set(named)
        for target in seams[module].values():
            reached |= reach.get(target, frozenset())
        measured[module] = DatasetReach(named=named, reached=frozenset(reached))
    return measured


def _row_shape_violations(table: Mapping[str, object], sources: Mapping[str, str]) -> list[str]:
    """The two ways a table stops covering the modules that exist, in one place for both tables.

    A seventh `panel_*.py` with no row is the gap
    `test_every_top_level_panel_module_is_in_this_table_and_stays_inside_its_row` closed for
    `PANEL_MODULE_DEPENDENCIES`; both tables below have to close it too, and neither may keep a
    row for a module that has been deleted.
    """
    return [
        f"{module} is a top-level panel module with no row in this table"
        for module in sorted(set(sources) - set(table))
    ] + [
        f"{module} has a row in this table and no longer exists"
        for module in sorted(set(table) - set(sources))
    ]


def _seam_import_violations(
    table: Mapping[str, frozenset[str]], sources: Mapping[str, str]
) -> list[str]:
    """Every disagreement between `table` and the sources, in both directions.

    Returned rather than asserted so that both directions can be driven on mutated sources: a
    name taken across the seam that no row declares, and a declared name nobody imports, are two
    different findings and `Task 38`'s lesson is that only the second one goes unnoticed.
    """
    found = _row_shape_violations(table, sources)
    for module, text in sorted(sources.items()):
        tree = ast.parse(text, filename=module)
        whole = sorted(_whole_sibling_imports(tree))
        if whole:
            found.append(
                f"{module} imports {whole} whole, so the names it takes from them cannot be "
                "seen here; import the names instead"
            )
        declared = table.get(module)
        if declared is None:
            continue
        observed = frozenset(f"{sibling}.{name}" for sibling, name in _seam_imports(tree).values())
        found.extend(
            f"{module} takes {taken} across the seam and its row does not say so"
            for taken in sorted(observed - declared)
        )
        found.extend(
            f"{module}'s row claims {claimed}, which it does not import"
            for claimed in sorted(declared - observed)
        )
    return found


def _dataset_reach_violations(
    table: Mapping[str, DatasetReach], sources: Mapping[str, str]
) -> list[str]:
    """Every disagreement between `table` and the datasets the sources can touch, both ways."""
    found = _row_shape_violations(table, sources)
    for module, observed in sorted(_research_plane_dataset_reach(sources).items()):
        declared = table.get(module)
        if declared is None:
            continue
        found.extend(
            f"{module} can name {dataset!r} and its row does not say so"
            for dataset in sorted(observed.named - declared.named)
        )
        found.extend(
            f"{module}'s row claims it names {dataset!r}, which it does not"
            for dataset in sorted(declared.named - observed.named)
        )
        found.extend(
            f"{module} can reach {dataset!r} and its row does not say so"
            for dataset in sorted(observed.reached - declared.reached)
        )
        found.extend(
            f"{module}'s row claims it reaches {dataset!r}, which it cannot"
            for dataset in sorted(declared.reached - observed.reached)
        )
    return found


_ONE_MORE_LOADER = """

from openalpha_cn.panel_ingest import {loader}


def _a_widening_this_audit_has_to_see_{loader}(store: object) -> object:
    return {loader}(store)
"""
"""The mutation the two acceptance tests apply: one more reader, imported and called.

Appended to a real module's source rather than written to `src/`, so the mutation runs on every
`pytest` invocation instead of on the one afternoon somebody remembered to try it by hand. Both
halves matter -- an import alone would be caught by `ruff` as unused, and a call alone would not
compile -- so this is the shape the widening would really arrive in.

The call site is named after its loader so that adding two of them adds two functions rather than
rebinding one; a mutation whose second half overwrote its first would be a weaker mutation than
the test claims to be applying.
"""


def _with_one_more_loader(module: str, *loaders: str) -> dict[str, str]:
    """The real sources, with `loaders` imported and called in `module`."""
    sources = _research_plane_sources()
    sources[module] += "".join(_ONE_MORE_LOADER.format(loader=loader) for loader in loaders)
    return sources


def test_panel_ingest_depends_only_on_domain_and_panel() -> None:
    dependencies = _direct_internal_dependencies("openalpha_cn.panel_ingest")

    assert dependencies <= _ALLOWED_INTERNAL_DEPENDENCIES, (
        f"openalpha_cn.panel_ingest may only import {sorted(_ALLOWED_INTERNAL_DEPENDENCIES)}, "
        f"found {sorted(dependencies)}"
    )
    assert dependencies == _ALLOWED_INTERNAL_DEPENDENCIES, (
        "panel_ingest exists precisely to join these two packages; if it stops importing "
        f"one of them this module has lost its reason to be top-level (found {dependencies})"
    )


def test_panel_doctor_joins_domain_panel_and_panel_ingest_and_nothing_else() -> None:
    """The health report is allowed exactly the three it aggregates. In particular it must not
    reach `storage`, `runtime`, `api` or `product`: a doctor that could see the evidence plane
    or a composition root would be one whose verdict depended on how the process was wired
    rather than on what is in the panel store."""
    dependencies = _direct_internal_dependencies("openalpha_cn.panel_doctor")

    assert dependencies == _ALLOWED_DOCTOR_DEPENDENCIES, (
        f"openalpha_cn.panel_doctor may import exactly "
        f"{sorted(_ALLOWED_DOCTOR_DEPENDENCIES)}, found {sorted(dependencies)}"
    )


def test_the_health_report_adds_no_edge_into_the_panel_package() -> None:
    """The layering question a new top-level module has to answer: is it a pattern or an
    evasion? An evasion leaves the guarded package's real dependency set untouched and only
    moves it out of the metric's sight. This one cannot be that, because the edge runs
    `panel_doctor -> panel` and never back -- `openalpha_cn.panel`'s import closure is
    byte-for-byte what it was before this module existed, and nothing moved out of `panel/`
    to make room for it."""
    graph = grimp.build_graph("openalpha_cn")

    for importer in ("openalpha_cn.panel", "openalpha_cn.domain", "openalpha_cn.storage"):
        assert not graph.direct_import_exists(
            importer=importer, imported="openalpha_cn.panel_doctor", as_packages=True
        ), f"{importer} must not import openalpha_cn.panel_doctor"
    assert graph.direct_import_exists(
        importer="openalpha_cn.panel_doctor", imported="openalpha_cn.panel", as_packages=True
    ), "sanity check: the health report is supposed to read the catalog"


def test_the_dependency_gate_consumes_the_report_and_builds_no_requirement_of_its_own() -> None:
    """`panel_gate` may join exactly three, and `panel_ingest` is deliberately not among them.

    The gate's whole contract is that it decides on `panel_doctor`'s evidence: the report asks
    each dataset the question its own reader asks (`_requirement_for` reuses `panel_ingest`'s
    builders), and a gate with a direct edge to those builders could put a different question
    and reach a verdict the loader disagrees with.
    """
    dependencies = _direct_internal_dependencies("openalpha_cn.panel_gate")

    assert dependencies == _ALLOWED_GATE_DEPENDENCIES, (
        f"openalpha_cn.panel_gate may import exactly {sorted(_ALLOWED_GATE_DEPENDENCIES)}, "
        f"found {sorted(dependencies)}"
    )


def test_the_dependency_gate_adds_no_edge_into_the_panel_package_either() -> None:
    """The same pattern-or-evasion question `panel_doctor` had to answer, asked again because
    the answer is not inherited: a neutral top-level module is an evasion when the guarded
    package's real dependency set is untouched and only moves out of the metric's sight. Here
    the edges run `panel_gate -> panel_doctor -> panel` and never back, so `openalpha_cn.panel`'s
    import closure is what it was before this module existed."""
    graph = grimp.build_graph("openalpha_cn")

    for importer in (
        "openalpha_cn.panel",
        "openalpha_cn.domain",
        "openalpha_cn.storage",
        "openalpha_cn.panel_ingest",
        "openalpha_cn.panel_doctor",
    ):
        assert not graph.direct_import_exists(
            importer=importer, imported="openalpha_cn.panel_gate", as_packages=True
        ), f"{importer} must not import openalpha_cn.panel_gate"
    assert graph.direct_import_exists(
        importer="openalpha_cn.panel_gate", imported="openalpha_cn.panel_doctor", as_packages=True
    ), "sanity check: the gate is supposed to consume the health report"


def test_the_shared_face_joins_the_panel_plane_and_reaches_nothing_above_it() -> None:
    """`panel_view` may import all four panel-plane modules and nothing else.

    The four are its reason to exist -- one rendering for three faces has to be able to see
    everything the plane produces. What it must not see is `storage`, `runtime`, `providers`,
    `api` or `product`: a rendering that could reach a composition root would make its answer
    depend on how the process was wired rather than on what is in the panel store, and one that
    could reach `providers` would put a credential inside the module that builds response
    bodies.
    """
    dependencies = _direct_internal_dependencies("openalpha_cn.panel_view")

    assert dependencies == _ALLOWED_VIEW_DEPENDENCIES, (
        f"openalpha_cn.panel_view may import exactly {sorted(_ALLOWED_VIEW_DEPENDENCIES)}, "
        f"found {sorted(dependencies)}"
    )


def test_the_factor_engine_joins_the_plane_below_it_and_nothing_above() -> None:
    """`panel_factors` (`V2-P3-002`) may import exactly the three the plane is made of.

    The absence that matters here is not `runtime` or `api` -- `test_no_top_level_panel_module_
    reaches_a_composition_root_or_a_credential` covers those for every module at once -- it is
    `openalpha_cn.storage`, where `ParquetEvidenceStore` lives, and `openalpha_cn.evidence`,
    where the normaliser that feeds it does. `V2-P3-002` forbids factor observations from the
    evidence plane, and an import graph with no edge is what makes that a structural obstacle
    rather than a convention somebody has to remember. Asserted from this module's own row as
    an equality, so an edge added later fails here as well as in
    `tests/unit/panel/test_visible_read_callers.py`, which asks the same question from the
    factor side.
    """
    dependencies = _direct_internal_dependencies("openalpha_cn.panel_factors")

    assert dependencies == _ALLOWED_FACTOR_DEPENDENCIES, (
        f"openalpha_cn.panel_factors may import exactly "
        f"{sorted(_ALLOWED_FACTOR_DEPENDENCIES)}, found {sorted(dependencies)}"
    )
    assert "openalpha_cn.storage" not in dependencies
    assert "openalpha_cn.evidence" not in dependencies


def test_the_shared_face_adds_no_edge_into_anything_it_renders() -> None:
    """The pattern-or-evasion question, asked a third time and not inherited. An evasion leaves
    the guarded package's real dependency set untouched and only moves it out of the metric's
    sight; here every edge runs into `panel_view` from the three faces above it and out of it
    into the plane below, never back."""
    graph = grimp.build_graph("openalpha_cn")

    for importer in (
        "openalpha_cn.panel",
        "openalpha_cn.domain",
        "openalpha_cn.storage",
        "openalpha_cn.panel_ingest",
        "openalpha_cn.panel_doctor",
        "openalpha_cn.panel_gate",
    ):
        assert not graph.direct_import_exists(
            importer=importer, imported="openalpha_cn.panel_view", as_packages=True
        ), f"{importer} must not import openalpha_cn.panel_view"
    for face in ("openalpha_cn.cli", "openalpha_cn.api", "openalpha_cn.sdk"):
        assert graph.direct_import_exists(
            importer=face, imported="openalpha_cn.panel_view", as_packages=True
        ), f"sanity check: {face} is supposed to render through the shared face"


def test_every_top_level_panel_module_is_in_this_table_and_stays_inside_its_row() -> None:
    """The gap this issue's review found: the four `panel_*` modules' layering is guarded
    entirely by the four tests above, each of which names one module by hand. A **fifth**
    top-level `panel_*.py` -- the obvious next step, since this is now an established pattern
    with four instances -- could import `storage`, `runtime`, `providers` or `api` and no gate
    in this repository would go red. No `lint-imports` contract makes a `panel_*` module a
    source, and the architecture baseline is about `storage`/`providers`/`models`.

    So the modules are discovered from the directory and each is required to have a row here,
    which is `test_import_layering.py`'s own approach ("check the live graph, not a
    hand-maintained list") applied to the thing that grew four instances since that file was
    written. The row is then asserted as an equality, not a subset: a module that stops
    importing one of the packages it exists to join has lost its reason to be top-level, which
    is the argument `test_panel_ingest_depends_only_on_domain_and_panel` already makes for the
    first of them.

    One graph, built once and shared: `grimp.build_graph` walks the whole package, and this
    test asks four questions of it.
    """
    discovered = _top_level_panel_modules()

    assert len(discovered) >= 4, f"expected the four known panel_* modules, found {discovered}"
    undeclared = sorted(set(discovered) - set(PANEL_MODULE_DEPENDENCIES))
    assert not undeclared, (
        f"{undeclared} is a top-level panel module with no row in "
        "PANEL_MODULE_DEPENDENCIES. Every one of these sits outside openalpha_cn/panel/ so "
        "that package can keep its zero-sibling-edge guarantee, which makes the set of "
        "packages it may join its entire justification for existing -- add the row, and "
        "argue for it in the module's own docstring"
    )
    vanished = sorted(set(PANEL_MODULE_DEPENDENCIES) - set(discovered))
    assert not vanished, f"PANEL_MODULE_DEPENDENCIES names {vanished}, which no longer exists"

    graph = _build_graph()
    observed = {module: _direct_internal_dependencies(module, graph) for module in discovered}

    assert observed == {module: PANEL_MODULE_DEPENDENCIES[module] for module in discovered}


def test_no_top_level_panel_module_reaches_a_composition_root_or_a_credential() -> None:
    """The other half, stated once for all of them rather than four times.

    Whatever a `panel_*` module joins below, none of them may reach `providers` (a credential
    inside the module that builds response bodies), `storage`/`runtime` (a verdict that
    depended on how the process was wired rather than on what is in the store), or `api`/
    `product`/`agents`/`backtest` (an inversion of the direction the whole plane runs in).
    Discovered the same way, so the fifth module is covered by this too.
    """
    forbidden = {
        "openalpha_cn.providers",
        "openalpha_cn.storage",
        "openalpha_cn.runtime",
        "openalpha_cn.api",
        "openalpha_cn.product",
        "openalpha_cn.agents",
        "openalpha_cn.backtest",
        "openalpha_cn.decisions",
        "openalpha_cn.evidence",
        "openalpha_cn.models",
    }
    graph = _build_graph()

    leaked = {
        module: sorted(_direct_internal_dependencies(module, graph) & forbidden)
        for module in _top_level_panel_modules()
    }

    assert leaked == {module: [] for module in leaked}


def test_the_columnar_contract_reaches_no_infrastructure_library() -> None:
    """`domain/panel_batch.py` is where a columnar contract is most tempted to acquire
    numpy/pandas or a DuckDB type vocabulary. It has neither: the DuckDB translation table
    lives in `panel_ingest.py`, on the far side of the seam (ADR-0003)."""
    graph = grimp.build_graph("openalpha_cn", include_external_packages=True)
    forbidden = {"numpy", "pandas", "polars", "pyarrow", "duckdb", "sqlite3"}

    reachable = graph.find_downstream_modules("openalpha_cn.domain.panel_batch")
    assert "openalpha_cn.panel_ingest" in reachable, (
        "sanity check: panel_ingest must be a consumer of the contract, otherwise the "
        "assertion below is checking an unused module"
    )
    # A library absent from the graph is trivially not imported; `direct_import_exists`
    # raises rather than returning False for an unknown module, so filter first.
    leaked = {
        name
        for name in forbidden & graph.modules
        if graph.direct_import_exists(
            importer="openalpha_cn.domain.panel_batch", imported=name, as_packages=True
        )
    }
    assert not leaked, f"openalpha_cn.domain.panel_batch must not import {sorted(leaked)}"


def test_providers_gain_the_panel_protocol_without_gaining_an_infrastructure_import() -> None:
    """`providers/base.py` now imports the columnar contract for its `PanelDataProvider`
    protocol. The `providers-no-infra-imports` contract checks full transitive reachability,
    so this would have broken the moment the contract carried a DuckDB dependency -- which
    is the concrete reason the translation table is not in `domain/`."""
    graph = grimp.build_graph("openalpha_cn", include_external_packages=True)

    for infrastructure in ("duckdb", "sqlite3"):
        assert not graph.direct_import_exists(
            importer="openalpha_cn.providers", imported=infrastructure, as_packages=True
        ), f"openalpha_cn.providers must not reach {infrastructure}"
        assert not graph.direct_import_exists(
            importer="openalpha_cn.domain", imported=infrastructure, as_packages=True
        ), f"openalpha_cn.domain must not reach {infrastructure}"


# --- the dataset-granularity instrument ------------------------------------------------------


TOP_LEVEL_MODULES_OUTSIDE_EVERY_PLANE_FAMILY: dict[str, str] = {
    "openalpha_cn._build_commit": (
        "one generated string. It has no imports at all, so there is no dependency set to argue "
        "about; `test_repository_assets.py` owns how it is produced."
    ),
    "openalpha_cn.batch_contracts": (
        "the durable-orchestration contract `V2-P0B-012` created so `storage.batch` would stop "
        "importing `runtime.batch`. Its whole reason to exist is that it depends on `domain` "
        "only, which is what `storage-no-upward-deps` measures transitively through it -- see "
        "`tests/unit/test_batch_contracts_import_isolation.py`, which pins its dependency set "
        "the way this file pins the panel plane's."
    ),
    "openalpha_cn.cli": "a face. It may reach anything it renders; that is what a face is.",
    "openalpha_cn.job_contracts": (
        "the durable-scheduling contract `V2-P5-010` created, and `batch_contracts`'s reason "
        "exactly: `storage.jobs` must persist a `ScheduledJob` without reaching upward, so this "
        "depends on `domain` only. It is deliberately not a research-plane module -- it holds no "
        "dataset and reads no panel, and the one thing a trading-day scheduler *does* need from "
        "the plane, the 16:30 publication rule, it is forbidden from restating: "
        "`openalpha_cn.scheduler` asks `panel_ingest` for it instead (`V2-P4-063`, `V2-P4-114`)."
    ),
    "openalpha_cn.config": (
        "settings. `test_repository_assets.py` owns the dotenv precedence rules, and a "
        "configuration module that could reach the planes would invert the wiring direction."
    ),
    "openalpha_cn.logging_setup": "process-wide logging configuration, imported by the faces.",
    "openalpha_cn.scheduler": (
        "orchestration, not a plane. It reaches `panel_ingest` for exactly two names -- "
        "`newest_published_session` and `session_publication_instant` -- and that edge is the "
        "point of the module rather than a leak: a scheduler that computed 16:30 for itself "
        "would be the fifth restatement of the rule `V2-P4-063` found stated three times and "
        "`V2-P4-114` found stated a fourth. It reads no dataset and holds no panel, so it has "
        "nothing to put in `RESEARCH_PLANE_DATASETS`."
    ),
    "openalpha_cn.schema_export": "a build script's entry point, not a research module.",
    "openalpha_cn.sdk": "a face, for `openalpha_cn.cli`'s reason.",
}
"""Every top-level module that is neither `panel_*` nor `factor_*`, and why it needs no row.

The discovery gap this closes is one level up from the one `RESEARCH_PLANE_PREFIXES` closes.
Widening the glob to two prefixes makes a second `factor_*.py` arrive red; it does nothing at all
for a **third family** -- `signal_view.py`, say -- which is exactly the shape `V2-P3-015` was, one
issue earlier, when `factor_view.py` was the second. So the check below discovers every top-level
`.py` in the package and requires each one to be either in a discovered family or named here with
a sentence. An enumeration is unavoidable at the bottom of this recursion; what makes this one
safe is that it is the *complement* of a discovered set rather than the set itself, so a new
module defaults to red instead of to unguarded.
"""


def test_every_top_level_module_is_a_declared_leaf_or_a_member_of_a_discovered_family() -> None:
    """The gap `V2-P3-015` opened and nothing closed: a top-level family this file cannot see.

    `_top_level_panel_modules()` globs `panel_*.py`, which was the whole plane when it was
    written. `factor_view.py` arrived afterwards with the same justification -- a module outside
    `openalpha_cn/panel/` precisely so that package keeps its zero-sibling-edge guarantee -- and
    is guarded only because `test_factor_view_layering.py` names it by hand. Measured: a second
    `factor_*.py` would have had a row in no table in this repository.

    Two prefixes fixes that one instance and not the shape of it, so this test is the general
    form: every `src/openalpha_cn/*.py` is either discovered by a family glob or carries a reason
    in `TOP_LEVEL_MODULES_OUTSIDE_EVERY_PLANE_FAMILY`. Both directions, because a reason left
    behind for a module that no longer exists is the same drift in the other direction.
    """
    discovered = {
        f"openalpha_cn.{path.stem}"
        for path in SOURCE_ROOT.glob("*.py")
        if not path.stem.startswith("__")
    }
    family = set(_top_level_research_plane_modules())
    declared = set(TOP_LEVEL_MODULES_OUTSIDE_EVERY_PLANE_FAMILY)

    assert family <= discovered, f"the family glob found {sorted(family - discovered)} on no disk"
    unguarded = sorted(discovered - family - declared)
    assert not unguarded, (
        f"{unguarded} is a top-level module in neither a discovered research-plane family "
        f"({', '.join(RESEARCH_PLANE_PREFIXES)}) nor "
        "TOP_LEVEL_MODULES_OUTSIDE_EVERY_PLANE_FAMILY. Either name it so the family glob finds "
        "it -- which gives it a row in RESEARCH_PLANE_SEAM_IMPORTS and RESEARCH_PLANE_DATASETS "
        "and a layering argument it has to pass -- or add it here with the sentence that says "
        "why a top-level module needs neither"
    )
    vanished = sorted(declared - discovered)
    assert not vanished, f"{vanished} carries a reason here and no longer exists"
    overlapping = sorted(declared & family)
    assert not overlapping, (
        f"{overlapping} is both declared exempt and discovered by a family glob; the exemption "
        "is dead and would hide the module's row going missing"
    )


def test_the_written_dataset_vocabulary_is_the_one_domain_declares() -> None:
    """`UPSTREAM_PANEL_DATASETS` against every `*_DATASET` scalar `domain/` binds.

    This is the sentinel under the four rows that say `UPSTREAM_PANEL_DATASETS` rather than
    listing fifteen names. Those rows would widen silently when a sixteenth upstream dataset
    arrived; this test makes that arrival red one hop earlier, before `RESEARCH_PLANE_DATASETS`
    is consulted at all, and its message says which rows have to be re-read.

    Scalars only. `FINANCIAL_STATEMENT_DATASETS` and `PERIOD_INDEXED_DATASETS` are groupings of
    names declared elsewhere, so counting them would add nothing and would make a *renamed*
    grouping look like a new dataset.
    """
    declared = {
        constant: values
        for constant, values in _dataset_naming_constants().items()
        if constant.endswith("_DATASET")
    }
    plural = sorted(constant for constant, values in declared.items() if len(values) != 1)

    assert not plural, (
        f"{plural} is named like a single dataset and resolves to several; either it is a "
        "grouping and should be named _DATASETS, or this scan is reading it wrong"
    )
    named = frozenset().union(*declared.values())
    assert named == UPSTREAM_PANEL_DATASETS, (
        f"domain/ declares {sorted(named)} and UPSTREAM_PANEL_DATASETS says "
        f"{sorted(UPSTREAM_PANEL_DATASETS)}. Add the difference here, and then re-read the four "
        "rows of RESEARCH_PLANE_DATASETS that say UPSTREAM_PANEL_DATASETS instead of listing "
        "their datasets -- a new upstream dataset widens all four of them at once"
    )


def test_no_research_plane_module_takes_a_whole_sibling_module_instead_of_names_from_it() -> None:
    """`import openalpha_cn.panel_ingest` would make the table below a table of nothing.

    The obvious way around a name-granular allowlist is not to add a name to it: bind the whole
    sibling once and reach every loader through an attribute. Neither of the two forms that does
    that appears in the tree, and the check is here rather than left implicit because a table
    whose evasion is one line long is not a detector.
    """
    taken = {
        module: sorted(_whole_sibling_imports(ast.parse(text, filename=module)))
        for module, text in _research_plane_sources().items()
    }

    assert taken == {module: [] for module in taken}, (
        "a top-level panel module binds a sibling module whole; import the names it actually "
        f"uses so RESEARCH_PLANE_SEAM_IMPORTS can see them -- {taken}"
    )


def test_every_name_each_research_plane_module_takes_across_the_seam_is_declared() -> None:
    """`RESEARCH_PLANE_SEAM_IMPORTS` as an equality against the real imports.

    The first of the two doors a dataset widening comes through, and the only one that sees a
    loader for a dataset the module already reads -- which is exactly the case
    `PANEL_MODULE_DEPENDENCIES` cannot see, since `openalpha_cn.panel_ingest` is already inside
    `_ALLOWED_FACTOR_DEPENDENCIES` and inside `_ALLOWED_NEUTRALIZATION_DEPENDENCIES`.

    Modules are discovered rather than listed, so a seventh `panel_*.py` arrives with no row and
    fails here as well as in
    `test_every_top_level_panel_module_is_in_this_table_and_stays_inside_its_row`.
    """
    sources = _research_plane_sources()

    assert _seam_import_violations(RESEARCH_PLANE_SEAM_IMPORTS, sources) == []


def test_every_upstream_dataset_each_research_plane_module_can_reach_is_declared() -> None:
    """`RESEARCH_PLANE_DATASETS` as an equality, on both of its fields.

    The second door: a widening that adds no import at all. A factor declaring
    `FactorField(dataset=ADJ_FACTOR_DATASET, ...)`, or a `ReadinessRequirement` built in place
    for a dataset the module has never read, changes `named` without touching the seam -- and
    `V2-P4`'s walk-forward is far likelier to arrive that way than through a new loader.
    """
    sources = _research_plane_sources()

    assert _dataset_reach_violations(RESEARCH_PLANE_DATASETS, sources) == []


def test_the_neutralisation_reaches_its_two_foreign_datasets_only_across_the_seam() -> None:
    """`V2-P3-004`'s claim as a measurement rather than as an argument in a docstring.

    That issue split `panel_neutralization` out of `panel_factors` because the neutralisation
    reads two datasets the factor engine has no business knowing about. Measured here: the
    module names **no** dataset anywhere in its own source, and `daily_basic` and
    `index_member_all` arrive entirely through two names it takes across the seam. The gap
    between `named` and `reached` is that issue's whole subject, and it is also the reason a
    scan of `FactorField(dataset=...)` literals -- the obvious way to measure a factor plane's
    dataset reach -- would have reported this module as reading nothing at all.

    `index_classify` is deliberately absent: the neutralisation takes
    `load_industry_cross_section` and not `load_industry_trees`, so it reads assignments and never
    the taxonomy tree.

    **The industry name in that pair moved and the dataset behind it did not**, which is
    `V2-P4-028` and is the case this instrument is built to be quiet about: the loader changed
    from `load_industry_histories` to `load_industry_cross_section`, both of which reach
    `index_member_all` and nothing else, so `reached` is unmoved while the seam row is a diff a
    reviewer reads.
    """
    measured = _research_plane_dataset_reach(_research_plane_sources())
    neutralisation = measured["openalpha_cn.panel_neutralization"]

    assert neutralisation.named == frozenset()
    assert neutralisation.reached == frozenset({"daily_basic", "index_member_all"})
    assert "index_classify" not in neutralisation.reached
    assert RESEARCH_PLANE_SEAM_IMPORTS["openalpha_cn.panel_neutralization"] >= {
        "panel_ingest.load_daily_valuations",
        "panel_ingest.load_industry_cross_section",
    }


def test_a_loader_added_to_the_factor_engine_turns_the_seam_table_red() -> None:
    """The first acceptance mutation, run on every invocation instead of by hand once.

    `panel_factors` already reads `daily`, so `load_daily_bars` widens no dataset and
    `RESEARCH_PLANE_DATASETS` stays green -- which is the case that makes the seam table
    necessary rather than redundant, and the case a purely dataset-level guard would miss. The
    dataset half is asserted to stay quiet on purpose: an audit that went red for the wrong
    reason would pass this test while measuring nothing.
    """
    widened = _with_one_more_loader("openalpha_cn.panel_factors", "load_daily_bars")

    seam = _seam_import_violations(RESEARCH_PLANE_SEAM_IMPORTS, widened)
    assert seam == [
        "openalpha_cn.panel_factors takes panel_ingest.load_daily_bars across the seam and its "
        "row does not say so"
    ]
    assert _dataset_reach_violations(RESEARCH_PLANE_DATASETS, widened) == [], (
        "sanity check: load_daily_bars reads a dataset panel_factors already reads, so the "
        "dataset table has nothing to say and the seam table is the whole detector here"
    )


def test_a_loader_added_to_the_neutralisation_turns_both_tables_red() -> None:
    """The second acceptance mutation, and the one both instruments answer.

    `load_daily_bars` brings `daily`, which `panel_neutralization` does not read, so the seam
    table sees the name and the dataset table sees the dataset. This is the widening
    `_ALLOWED_NEUTRALIZATION_DEPENDENCIES`' own docstring described as going unpoliced.
    """
    widened = _with_one_more_loader("openalpha_cn.panel_neutralization", "load_daily_bars")

    assert _seam_import_violations(RESEARCH_PLANE_SEAM_IMPORTS, widened) == [
        "openalpha_cn.panel_neutralization takes panel_ingest.load_daily_bars across the seam "
        "and its row does not say so"
    ]
    assert _dataset_reach_violations(RESEARCH_PLANE_DATASETS, widened) == [
        "openalpha_cn.panel_neutralization can reach 'daily' and its row does not say so"
    ]


def test_the_dataset_instrument_separates_seven_datasets_from_nine() -> None:
    """The separating question, asked directly: can it tell seven datasets from nine?

    An assertion that holds on the tree as it stands has not been shown to distinguish two
    answers. So two loaders for two datasets the factor engine does not read are added to it,
    and the measured reach is read back rather than only the verdict -- the count moves from
    seven to nine and names which two arrived.

    `load_index_membership` and `load_price_limits` are chosen because their datasets
    (`index_weight`, `stk_limit`) are in no factor's `required_fields` and in no other name
    `panel_factors` imports, so the two new entries can only have come from the mutation.
    """
    widened = _with_one_more_loader(
        "openalpha_cn.panel_factors", "load_index_membership", "load_price_limits"
    )

    before = _research_plane_dataset_reach(_research_plane_sources())["openalpha_cn.panel_factors"]
    after = _research_plane_dataset_reach(widened)["openalpha_cn.panel_factors"]

    assert len(before.reached) == 7
    assert len(after.reached) == 9
    assert after.reached - before.reached == {"index_weight", "stk_limit"}
    assert after.named == before.named, (
        "the two loaders are imported, not inlined, so what widened is the reach across the "
        "seam and not what this module names for itself"
    )
    assert sorted(_dataset_reach_violations(RESEARCH_PLANE_DATASETS, widened)) == [
        "openalpha_cn.panel_factors can reach 'index_weight' and its row does not say so",
        "openalpha_cn.panel_factors can reach 'stk_limit' and its row does not say so",
    ]


def test_a_dataset_declared_for_a_module_that_cannot_name_it_turns_this_audit_red() -> None:
    """The other direction, which is the one `Task 38` proved goes unnoticed.

    That task added a key to a target table without adding the branch behind it and got `exit 0`
    with an empty result, because two existing tests both checked the implementation against the
    table and neither checked the table against the implementation. Both fields of this row are
    equalities for that reason, and both are driven here: a dataset declared that the module
    cannot name, and one it can name that the row omits.

    The two datasets it mutates with are checked to be respectively absent from and present in
    the row before they are used. A mutation that changed nothing would make this test pass while
    measuring nothing, and it would arrive quietly on the day `panel_factors` legitimately gained
    `adj_factor` or lost `income` -- which is the same failure mode the test is about.
    """
    sources = _research_plane_sources()
    row = RESEARCH_PLANE_DATASETS["openalpha_cn.panel_factors"]

    assert "adj_factor" not in row.named, (
        "this test mutates the row by adding adj_factor and needs it to be absent; the factor "
        "engine now declares it, so pick a dataset it does not"
    )
    assert "income" in row.named and "income" in row.reached, (
        "this test mutates the row by removing income and needs it to be present; the factor "
        "engine no longer names it, so pick a dataset it does"
    )

    overclaimed = dict(RESEARCH_PLANE_DATASETS)
    overclaimed["openalpha_cn.panel_factors"] = DatasetReach(
        named=row.named | {"adj_factor"}, reached=row.reached | {"adj_factor"}
    )
    assert _dataset_reach_violations(overclaimed, sources) == [
        "openalpha_cn.panel_factors's row claims it names 'adj_factor', which it does not",
        "openalpha_cn.panel_factors's row claims it reaches 'adj_factor', which it cannot",
    ]

    underclaimed = dict(RESEARCH_PLANE_DATASETS)
    underclaimed["openalpha_cn.panel_factors"] = DatasetReach(
        named=row.named - {"income"}, reached=row.reached - {"income"}
    )
    assert _dataset_reach_violations(underclaimed, sources) == [
        "openalpha_cn.panel_factors can name 'income' and its row does not say so",
        "openalpha_cn.panel_factors can reach 'income' and its row does not say so",
    ]

    dropped = {
        module: reach
        for module, reach in RESEARCH_PLANE_DATASETS.items()
        if module != "openalpha_cn.panel_neutralization"
    }
    assert _dataset_reach_violations(dropped, sources) == [
        "openalpha_cn.panel_neutralization is a top-level panel module with no row in this table"
    ]
    assert _dataset_reach_violations(
        {**RESEARCH_PLANE_DATASETS, "openalpha_cn.panel_ghost": row}, sources
    ) == ["openalpha_cn.panel_ghost has a row in this table and no longer exists"]


def test_a_seam_name_declared_that_nobody_imports_turns_this_audit_red() -> None:
    """The same two directions for the seam table, driven the same way.

    The overclaim is the one worth naming: a row that keeps `panel_ingest.load_daily_bars` after
    the call site is deleted is a row that would then accept the loader coming back without
    review, which is a table drifting into permission rather than into error.

    Both mutations are checked to be real mutations first, for the reason the test above states.
    """
    sources = _research_plane_sources()
    row = RESEARCH_PLANE_SEAM_IMPORTS["openalpha_cn.panel_neutralization"]

    assert "panel_ingest.load_daily_bars" not in row, (
        "this test mutates the row by adding panel_ingest.load_daily_bars and needs it to be "
        "absent; the neutralisation now imports it, so pick a name it does not"
    )
    assert "panel_ingest.load_industry_cross_section" in row, (
        "this test mutates the row by removing panel_ingest.load_industry_cross_section and "
        "needs it to be present; the neutralisation no longer imports it, so pick a name it does"
    )

    overclaimed = dict(RESEARCH_PLANE_SEAM_IMPORTS)
    overclaimed["openalpha_cn.panel_neutralization"] = row | {"panel_ingest.load_daily_bars"}
    assert _seam_import_violations(overclaimed, sources) == [
        "openalpha_cn.panel_neutralization's row claims panel_ingest.load_daily_bars, which it "
        "does not import"
    ]

    underclaimed = dict(RESEARCH_PLANE_SEAM_IMPORTS)
    underclaimed["openalpha_cn.panel_neutralization"] = row - {
        "panel_ingest.load_industry_cross_section"
    }
    assert _seam_import_violations(underclaimed, sources) == [
        "openalpha_cn.panel_neutralization takes panel_ingest.load_industry_cross_section "
        "across the seam and its row does not say so"
    ]

    dropped = {
        module: names
        for module, names in RESEARCH_PLANE_SEAM_IMPORTS.items()
        if module != "openalpha_cn.panel_factors"
    }
    assert _seam_import_violations(dropped, sources) == [
        "openalpha_cn.panel_factors is a top-level panel module with no row in this table"
    ]


def test_the_shipped_factors_declare_every_dataset_the_engine_can_name() -> None:
    """The relationship between what the registry declares and what the module could read.

    They were not equal and are now, and **the change of verdict is the finding this test was
    written to produce**. `required_fields` is what `compute_factor` iterates, so the declared
    set is what every stored build reads; the module could always *name* a sixth dataset because
    it imports `PERIOD_INDEXED_DATASETS` to decide which axis a dataset sits on and that set is
    the four statement endpoints. Until `V2-P3-017` no shipped factor read `fina_indicator`, so
    the difference was exactly `{"fina_indicator"}` and this test asserted it -- with its own
    docstring saying "a factor that started reading `fina_indicator` would fail here".

    One did: `deducted_earnings_yield_ttm` reads `fina_indicator.profit_dedt`, because that is
    the only endpoint of the four that serves the deducted profit at all. So the assertion is
    inverted rather than relaxed -- equality now, and still exact -- and the sentence it was
    holding `panel_factors.py` to has been narrowed there in the same change, from "Nothing here
    reads `fina_indicator`" to the quality family's own claim about itself.

    `V2-P3-016` is the second occasion and it moved the row rather than only the count.
    `residual_vol_60` reads `index_daily.close` and `index_daily.pre_close`, so the declared set
    is seven and the module's own `named` is seven -- the equality is the same assertion, over a
    wider set. The count is no longer in this test's name for the reason `V2-P3-017` gave for
    inverting the set assertion: a name that says "twenty" is a name that has to be edited every
    time a factor ships, which makes the edit routine, and the routine edit is how a table stops
    being read. The number is still asserted below, where a diff shows it.
    """
    declared = frozenset(
        field.dataset
        for definition in FACTOR_DEFINITIONS.definitions
        for field in definition.required_fields
    )
    engine = RESEARCH_PLANE_DATASETS["openalpha_cn.panel_factors"]

    assert len(FACTOR_DEFINITIONS.definitions) == 21
    assert declared == {
        "balancesheet",
        "cashflow",
        "daily",
        "daily_basic",
        "fina_indicator",
        "income",
        "index_daily",
    }
    assert declared == engine.named, "the module names exactly what its factors declare"
    assert declared <= UPSTREAM_PANEL_DATASETS
    assert engine.named == engine.reached, (
        "the factor engine takes three writer helpers across the seam and no reader, so "
        "following the seam must add nothing to what it can already name"
    )
