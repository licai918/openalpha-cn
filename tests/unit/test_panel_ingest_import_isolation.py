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
"""

from __future__ import annotations

import grimp

_ALLOWED_INTERNAL_DEPENDENCIES = {"openalpha_cn.domain", "openalpha_cn.panel"}
_ALLOWED_DOCTOR_DEPENDENCIES = _ALLOWED_INTERNAL_DEPENDENCIES | {"openalpha_cn.panel_ingest"}
_ALLOWED_GATE_DEPENDENCIES = _ALLOWED_INTERNAL_DEPENDENCIES | {"openalpha_cn.panel_doctor"}


def _direct_internal_dependencies(module: str) -> set[str]:
    graph = grimp.build_graph("openalpha_cn")
    siblings = {
        name
        for name in graph.modules
        if name.count(".") == 1 and name.startswith("openalpha_cn.") and name != module
    }
    return {
        sibling
        for sibling in siblings
        if graph.direct_import_exists(importer=module, imported=sibling, as_packages=True)
    }


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
