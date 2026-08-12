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
four: `panel_*` is now an established pattern, `pyproject.toml`'s four `lint-imports`
contracts do not mention `panel*` at all, and the architecture baseline covers `storage`,
`providers` and `models` -- so a *fifth* one could import `storage` or `providers` and nothing
in this repository would go red. `V2-P1-016`'s review found that gap. The last two tests in
this file close it the way `test_import_layering.py` closed the same gap for `domain`'s
sibling packages: discover the modules from the real directory structure, require each to
have a row in `PANEL_MODULE_DEPENDENCIES`, and check the live graph rather than a list
somebody has to remember to update.
"""

from __future__ import annotations

from pathlib import Path

import grimp

ROOT = Path(__file__).resolve().parents[2]

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
its `EVENT_TIME_COLUMN`, its `FACTOR_PROVIDER_ID`, its census-column prefix and its
`_refuse_to_drop_a_stored_build` -- so the alternative to the edge was either a second copy of
each or keeping the code inside a module that would then be 4,900 lines.

**What the edge buys the audit is the reason `V2-P3-004` split the file at all.** This module
reaches `panel_ingest` for two things `panel_factors` deliberately does not:
`load_industry_histories` and `load_daily_valuations`, the readers of the two **foreign** datasets
a neutralisation regresses against. Had this code stayed in `panel_factors`, that widening would
have been invisible here --
`openalpha_cn.panel_ingest` is already in `_ALLOWED_FACTOR_DEPENDENCIES` and this table records
dependencies at package granularity, so the factor engine would have silently gained two datasets
it has no business knowing about, with nothing to go red. A separate module makes the widening a
row somebody has to approve, which is what this table is for.

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
join" the whole justification for each of them being top-level at all. None of
`pyproject.toml`'s four `lint-imports` contracts mentions `panel*`, so this table and the tests
below are the only thing standing there.

`test_every_top_level_panel_module_is_in_this_table_and_stays_inside_its_row` keeps it from
being the hand-maintained enumeration it looks like: the modules are discovered from the
directory, so a seventh one arrives red rather than unguarded.
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
    in this repository would go red. `pyproject.toml`'s four `lint-imports` contracts do not
    mention `panel*` at all, and the architecture baseline is about `storage`/`providers`/
    `models`.

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
