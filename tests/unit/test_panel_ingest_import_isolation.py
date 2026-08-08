"""Layering proofs for `V2-P1-002`'s two new modules.

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
"""

from __future__ import annotations

import grimp

_ALLOWED_INTERNAL_DEPENDENCIES = {"openalpha_cn.domain", "openalpha_cn.panel"}


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
