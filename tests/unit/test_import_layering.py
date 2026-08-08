"""Enforce ADR-0001's layering guardrail with import-linter, and pin the shrink-only baseline.

ADR-0001 (`docs/architecture/ADR-0001-local-first-runtime.md`) declares that domain and
provider contracts must not import SQLite or DuckDB implementation types. This module
proves the gate that enforces it is real (rejects a freshly introduced violation), proves
the pre-existing baseline of measured violations
(`docs/architecture/import-layering-baseline.toml`) can only shrink, and proves the legal
downward dependency of `runtime`/`backtest` on `storage` is never mis-flagged.

The baseline started at 7: V2-P0B-011 fixed its 2 `providers`/`models` entries
(`providers.file -> duckdb`, `models.governance -> sqlite3`), leaving 5
`storage-no-upward-deps` entries tracked as V2-P0B-012. V2-P0B-012 fixed all 5: each of
`storage/memory.py`, `storage/batch.py`, `storage/portfolio.py`, `storage/recovery.py`, and
`storage/product.py` imported a data contract from `agents`/`runtime`/`product`/`backtest`
purely to serialize/deserialize it; those contracts moved to `openalpha_cn.domain` (four of
them) or, for the one judged not to be a domain concept
(`BatchResearchTask`/`BatchProgressEvent` and their siblings -- durable *orchestration*
state, not a research-domain value), a new neutral top-level module,
`openalpha_cn.batch_contracts`. `storage/*.py` now imports every one of these five
contracts from below it, not above it -- the baseline is pinned at 0 and
`storage-no-upward-deps` carries no `ignore_imports` at all.
See `tests/unit/test_storage_contract_relocation.py` for the relocation's own proofs
(identity preserved from every old import path, new homes carry no edge back into
`agents`/`product`/`backtest`/`storage`).

V2-P0B-012's first version of `storage-no-upward-deps` also carried
`allow_indirect_imports = true`, added to tolerate a real two-hop chain,
`storage.batch -> batch_contracts -> runtime.contracts`, that `batch_contracts.py` needed
for `ResearchRunRequest`. A Critical review rejected that fix: `allow_indirect_imports`
scopes the contract to a direct-edges-only check instead of import-linter's default full
transitive reachability, and a probe proved the gap was real -- a neutral top-level module
importing a behavioural `product` class, reached in turn from a module under `storage/`,
passed the relaxed contract (`lint-imports` reported it KEPT) while `grimp`'s full
reachability check saw the chain plainly.
`test_storage_no_upward_deps_contract_rejects_indirect_leak_via_neutral_module`
below reproduces that exact probe against the current configuration and proves the gate now
rejects it. The actual fix was not to widen the contract but to remove the chain:
`ResearchRunRequest` moved into `openalpha_cn.domain.run_request` (see that module's
docstring), so `openalpha_cn.batch_contracts` now depends only on `openalpha_cn.domain.*`,
`runtime/contracts.py` re-exports the class unchanged, and `storage-no-upward-deps` carries
no `allow_indirect_imports` key -- it runs the default full-transitive-reachability check.

It also proves the domain-purity rule independently of import-linter's static
`forbidden_modules` enumeration in `pyproject.toml`: siblings of `domain` are discovered
from the real directory structure at runtime, so a subpackage added after the enumeration
was written (e.g. a future `panel/`, `factors/`, or alpha-model package) is covered
automatically instead of silently falling outside the gate.

Finally (V2-P0B-001), it asserts the dependency-direction outcome of splitting
`runtime/contracts.py` out of `runtime/engine.py`: the pydantic-only request/result
contracts must not pull in `ResearchEngine`'s SQLite storage dependency, and the four
modules that only ever needed the contracts (not `ResearchEngine`) must route through
`runtime.contracts` instead of `runtime.engine`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import grimp
from importlinter import api as importlinter_api
from importlinter.cli import lint_imports

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "docs" / "architecture" / "import-layering-baseline.toml"
PINNED_BASELINE_COUNT = 0
ISSUE_PATTERN = re.compile(r"^V2-P0B-\d{3}$")

# The five upward edges the V2-P0B-012 baseline used to exempt, keyed by the storage module
# that carried each one. Kept here (rather than only in the baseline's own git history) so
# this test can assert, module by module, that every one of them is gone -- not just that
# the aggregate package-level check below (`test_storage_has_zero_direct_edges_into_*`)
# passes, which could in principle pass even if one edge moved rather than vanished.
FORMER_BASELINE_EDGES = (
    ("openalpha_cn.storage.memory", "openalpha_cn.runtime.memory"),
    ("openalpha_cn.storage.batch", "openalpha_cn.runtime.batch"),
    ("openalpha_cn.storage.portfolio", "openalpha_cn.backtest.portfolio"),
    ("openalpha_cn.storage.recovery", "openalpha_cn.agents.base"),
    ("openalpha_cn.storage.product", "openalpha_cn.product.research"),
)


def _load_baseline() -> list[dict[str, str]]:
    data = tomllib.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    violations = data.get("violation", [])
    assert isinstance(violations, list)
    return violations


def _ignore_import_pairs_from_pyproject() -> set[tuple[str, str]]:
    """Every `ignore_imports` entry across every import-linter contract, as (importer, imported)."""
    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    pairs: set[tuple[str, str]] = set()
    for contract in config["contracts_options"]:
        for expression in contract.get("ignore_imports", []):
            importer, _, imported = expression.partition("->")
            pairs.add((importer.strip(), imported.strip()))
    return pairs


def test_current_source_tree_satisfies_storage_providers_and_models_contracts_via_baseline() -> (
    None
):
    """The real repository, with the baseline's 5 exemptions applied, is fully compliant."""
    exit_code = lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
    )
    assert exit_code == 0


def test_baseline_exemption_count_is_pinned_at_zero() -> None:
    """The baseline is shrink-only: exactly 0 entries today, never more.

    Shrunk from 7 to 5 by V2-P0B-011 (`providers-no-infra-imports` and
    `models-no-infra-imports`), then from 5 to 0 by V2-P0B-012, which fixed every remaining
    `storage-no-upward-deps` entry -- `storage/*.py` no longer imports anything from
    `agents`/`runtime`/`product`/`backtest` at all. Should a future change ever need a new
    exemption, this pin forces it to be a deliberate, reviewed edit, not a silent add.
    """
    violations = _load_baseline()
    assert len(violations) == PINNED_BASELINE_COUNT


def test_every_baseline_exemption_declares_the_issue_that_will_close_it() -> None:
    """Every registered exemption must name the P0.B issue that will remove it.

    The baseline is empty today (V2-P0B-012 closed the last 5 entries), so this loop runs
    zero times and the test passes vacuously -- kept as defense in depth: if a baseline
    entry is ever reintroduced, it is still required to carry a valid issue annotation.
    """
    violations = _load_baseline()
    for violation in violations:
        issue = violation.get("issue", "")
        assert ISSUE_PATTERN.match(issue), (
            f"{violation.get('importer')} -> {violation.get('imported')} has no valid "
            f"issue annotation (got {issue!r})"
        )


def test_storage_no_upward_deps_contract_has_no_ignore_imports_key() -> None:
    """`ignore_imports` must be removed entirely from `storage-no-upward-deps`, not left as
    an empty list. An empty list is functionally identical to no exemptions, but leaving the
    key behind would keep inviting a future entry to be appended to "the exemptions list"
    instead of treated as a fresh violation that must be fixed -- see the objective success
    criterion in this task's brief.
    """
    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    contract = next(
        contract
        for contract in config["contracts_options"]
        if contract.get("id") == "storage-no-upward-deps"
    )
    assert "ignore_imports" not in contract, (
        "storage-no-upward-deps still declares an ignore_imports key "
        f"(value: {contract.get('ignore_imports')!r}); it must be removed entirely now that "
        "the baseline is empty, not left as []"
    )


def test_storage_has_zero_direct_edges_into_agents_runtime_product_or_backtest() -> None:
    """The package-level guarantee `storage-no-upward-deps` exists to enforce, checked
    directly with `grimp` rather than through import-linter's own (now-unexempted)
    evaluation -- an independent measurement of the same property.
    """
    graph = grimp.build_graph("openalpha_cn")
    for forbidden in (
        "openalpha_cn.agents",
        "openalpha_cn.runtime",
        "openalpha_cn.product",
        "openalpha_cn.backtest",
    ):
        assert not graph.direct_import_exists(
            importer="openalpha_cn.storage", imported=forbidden, as_packages=True
        ), f"openalpha_cn.storage still directly imports {forbidden}"


def test_each_former_baseline_edge_is_individually_gone() -> None:
    """Module-by-module proof that each of the five specific edges the baseline used to
    exempt no longer exists -- not merely that some aggregate package-level check passes,
    which could stay green even if one edge had only moved rather than vanished.
    """
    graph = grimp.build_graph("openalpha_cn")
    for importer, imported in FORMER_BASELINE_EDGES:
        assert not graph.direct_import_exists(importer=importer, imported=imported), (
            f"{importer} still directly imports {imported}"
        )


_LEAKY_HELPER_PATH = ROOT / "src" / "openalpha_cn" / "leaky_helper.py"
_LEAKY_PROBE_PATH = ROOT / "src" / "openalpha_cn" / "storage" / "_leaky_probe.py"


def test_storage_no_upward_deps_contract_does_not_relax_to_direct_edges_only() -> None:
    """`storage-no-upward-deps` must not declare `allow_indirect_imports = true`.

    That flag (present in an earlier version of this contract) scopes import-linter's
    `forbidden` contract type to a direct-edges-only check instead of its default full
    transitive reachability -- see
    `test_storage_no_upward_deps_contract_rejects_indirect_leak_via_neutral_module`
    below for a reproduction of the exact leak that relaxation permitted.
    """
    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    contract = next(
        contract
        for contract in config["contracts_options"]
        if contract.get("id") == "storage-no-upward-deps"
    )
    assert not contract.get("allow_indirect_imports", False), (
        "storage-no-upward-deps declares allow_indirect_imports=true, which relaxes it to a "
        "direct-edges-only check -- see this test's docstring"
    )


def test_storage_no_upward_deps_contract_rejects_indirect_leak_via_neutral_module() -> None:
    """A Critical-review finding on V2-P0B-012 proved that `allow_indirect_imports = true`
    (once present on this contract) let any future contributor reach a behavioural
    upper-layer class through one neutral top-level module without this gate ever noticing.

    The exact probe that proved it: a top-level module (`openalpha_cn.leaky_helper`)
    importing `product.research.ResearchScreener` -- a behavioural class, not a data
    contract -- imported in turn by a module under `storage/`. Under the relaxed contract,
    `lint-imports` reported `storage-no-upward-deps` KEPT (not broken) for that probe, while
    `grimp`'s full-reachability check saw the two-hop chain plainly. This test reproduces
    that exact probe against the current (fixed) configuration and proves the gate now
    rejects it, then removes both probe files so the real source tree is left clean.
    """
    assert not _LEAKY_HELPER_PATH.exists(), "probe file must not already exist"
    assert not _LEAKY_PROBE_PATH.exists(), "probe file must not already exist"
    _LEAKY_HELPER_PATH.write_text(
        '"""Temporary probe module for an import-layering leak test."""\n\n'
        "from openalpha_cn.product.research import ResearchScreener\n\n"
        '__all__ = ["ResearchScreener"]\n'
    )
    _LEAKY_PROBE_PATH.write_text(
        '"""Temporary probe module for an import-layering leak test."""\n\n'
        "from openalpha_cn.leaky_helper import ResearchScreener\n\n"
        '__all__ = ["ResearchScreener"]\n'
    )
    try:
        exit_code = lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("storage-no-upward-deps",),
        )
        assert exit_code == 1, (
            "lint-imports should reject storage/_leaky_probe.py -> leaky_helper -> "
            "product.research.ResearchScreener as an indirect leak through "
            "storage-no-upward-deps -- if this passes, the contract has regressed to a "
            "direct-edges-only check"
        )
    finally:
        _LEAKY_PROBE_PATH.unlink()
        _LEAKY_HELPER_PATH.unlink()

    # Confirm the gate is green again once both probe files are removed.
    exit_code = lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("storage-no-upward-deps",),
    )
    assert exit_code == 0


def test_baseline_exemptions_match_import_linter_ignore_imports_configuration_exactly() -> None:
    """The hand-written baseline and the live enforcement config must never drift apart."""
    baseline_pairs = {
        (violation["importer"], violation["imported"]) for violation in _load_baseline()
    }
    configured_pairs = _ignore_import_pairs_from_pyproject()
    assert baseline_pairs == configured_pairs


def test_domain_layer_gate_rejects_a_newly_introduced_forbidden_stdlib_import() -> None:
    """The gate is live: a fresh `import sqlite3` inside `domain/` must fail the check."""
    probe_path = ROOT / "src" / "openalpha_cn" / "domain" / "_layering_gate_probe.py"
    assert not probe_path.exists(), "probe file must not already exist in the real source tree"
    probe_path.write_text('"""Temporary probe module for a layering test."""\n\nimport sqlite3\n')
    try:
        exit_code = lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("domain-purity",),
        )
        assert exit_code == 1
    finally:
        probe_path.unlink()

    # Confirm the gate is green again once the probe is removed.
    exit_code = lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("domain-purity",),
    )
    assert exit_code == 0


def test_domain_layer_gate_rejects_a_newly_introduced_cross_subpackage_import() -> None:
    """The gate is live: `domain/` importing any sibling subpackage must fail the check."""
    probe_path = ROOT / "src" / "openalpha_cn" / "domain" / "_layering_gate_probe.py"
    assert not probe_path.exists(), "probe file must not already exist in the real source tree"
    probe_path.write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        "from openalpha_cn.providers.base import ProviderMetadata\n\n"
        '__all__ = ["ProviderMetadata"]\n'
    )
    try:
        exit_code = lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("domain-purity",),
        )
        assert exit_code == 1
    finally:
        probe_path.unlink()

    exit_code = lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("domain-purity",),
    )
    assert exit_code == 0


def _sibling_subpackages_of_domain() -> list[str]:
    """Subpackage directories under `src/openalpha_cn/`, excluding `domain` itself.

    Discovered from the real directory structure at runtime rather than hand-copied from
    `pyproject.toml`'s `forbidden_modules`, so a subpackage added later is picked up with
    no config edit and no human remembering to update an enumeration.
    """
    src_root = ROOT / "src" / "openalpha_cn"
    return sorted(
        entry.name
        for entry in src_root.iterdir()
        if entry.is_dir()
        and entry.name != "domain"
        and not entry.name.startswith("__")
        and (entry / "__init__.py").exists()
    )


def test_domain_purity_holds_against_every_dynamically_discovered_sibling_subpackage() -> None:
    """`domain-purity`'s `forbidden_modules` in `pyproject.toml` is a static enumeration of
    the subpackages that exist today. A subpackage added later (e.g. a future `panel/`,
    `factors/`, or alpha-model package for v2) is invisible to that list until a human
    remembers to add an entry — `lint-imports` reports `0 broken` even if `domain/` imports
    it. This test is independent of that static config: it enumerates sibling subpackages
    from the real directory structure and checks the actual import graph with `grimp`
    directly, so a newly added package is covered automatically.
    """
    siblings = _sibling_subpackages_of_domain()
    assert siblings, "expected at least one sibling subpackage under src/openalpha_cn/"

    graph = grimp.build_graph("openalpha_cn")
    violations = [
        sibling
        for sibling in siblings
        if graph.direct_import_exists(
            importer="openalpha_cn.domain",
            imported=f"openalpha_cn.{sibling}",
            as_packages=True,
        )
    ]
    assert not violations, (
        f"openalpha_cn.domain directly imports forbidden sibling subpackage(s): {violations}"
    )


def test_legal_downward_imports_from_runtime_and_backtest_into_storage_are_not_flagged() -> None:
    """`runtime`/`backtest` depending on `storage` is a legal downward dependency, not a
    violation -- for the composition roots that still construct concrete stores, and for
    the plain data models (`RunRecoveryState`) a Protocol's method signatures reference.

    V2-P0B-003 introduced `runtime.repository.RunRepository` and
    `runtime.recovery.RecoveryStore` Protocols and retyped `ResearchEngine.__init__`
    against them (`runtime/engine.py:31,34`), and `backtest.multi_day.PortfolioLedger`
    similarly (`backtest/multi_day.py:83`). That is precisely why `runtime.engine` no
    longer has any remaining reason to import `openalpha_cn.storage.sqlite` (only the
    `RunRepository` Protocol, satisfied structurally, is needed there now), and why
    `backtest.multi_day` no longer imports `openalpha_cn.storage.portfolio` at all (its
    `PortfolioLedger` Protocol only needs `PortfolioTransition`, which already lives in
    `backtest.portfolio`). `runtime.engine -> storage.recovery` survives unchanged: engine
    still constructs/reconstructs `RunRecoveryState` values directly, and that plain
    pydantic model is out of this task's scope to relocate. The composition roots
    (`backtest.replay`, and `sdk.py`/`api/app.py` elsewhere) still legitimately construct
    concrete stores to inject into the Protocol-typed parameters -- `backtest.replay` now
    also constructs a `SQLiteRecoveryStore` (V2-P0B-003 made `recovery_store` a required,
    caller-injected parameter, removing `ResearchEngine`'s prior self-construction from
    `repository.path`), so its dependency on `storage.recovery` is new and legitimate too.
    """
    graph = grimp.build_graph("openalpha_cn")
    assert graph.direct_import_exists(
        importer="openalpha_cn.runtime.engine", imported="openalpha_cn.storage.recovery"
    )
    assert not graph.direct_import_exists(
        importer="openalpha_cn.runtime.engine", imported="openalpha_cn.storage.sqlite"
    ), "runtime.engine should be Protocol-typed only; it no longer needs SQLiteRunRepository"
    assert not graph.direct_import_exists(
        importer="openalpha_cn.backtest.multi_day", imported="openalpha_cn.storage.portfolio"
    ), "backtest.multi_day should be Protocol-typed only; it no longer needs SQLitePortfolioLedger"
    assert graph.direct_import_exists(
        importer="openalpha_cn.backtest.replay", imported="openalpha_cn.storage.sqlite"
    )
    assert graph.direct_import_exists(
        importer="openalpha_cn.backtest.replay", imported="openalpha_cn.storage.recovery"
    )

    exit_code = lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("storage-no-upward-deps",),
    )
    assert exit_code == 0


# V2-P0B-001: `runtime/contracts.py` was split out of `runtime/engine.py` so that modules
# wanting only `ResearchRunRequest`/`ResearchRunResult`/`RunConflictError` stop transitively
# depending on `ResearchEngine`'s SQLite storage. These four modules only ever needed the
# contracts (see task-8-brief.md's dependency table, measured at HEAD); `sdk.py`,
# `backtest/replay.py`, and `api/app.py` legitimately need the full `ResearchEngine` and are
# intentionally excluded.
CONTRACT_ONLY_CONSUMERS = (
    "openalpha_cn.cli",
    "openalpha_cn.runtime.batch",
    "openalpha_cn.product.research",
    "openalpha_cn.backtest.validation",
)

# The storage submodules `runtime.engine`'s own dependency closure could plausibly reach.
# Checking transitive reachability against these two specifically (rather than all of
# `openalpha_cn.storage`) avoids false failures from unrelated, pre-existing storage
# touchpoints outside this task's scope. `storage.recovery` remains a real, direct
# `runtime.engine` import (for the plain `RunRecoveryState` data model); `storage.sqlite` no
# longer is (V2-P0B-003 retyped `repository` against the `RunRepository` Protocol), but is
# kept here for defense in depth against a future regression back to the concrete type.
ENGINE_OWNED_STORAGE_MODULES = ("openalpha_cn.storage.recovery", "openalpha_cn.storage.sqlite")


def test_runtime_contracts_module_does_not_import_runtime_engine() -> None:
    """`runtime.contracts` must not import `runtime.engine` -- that edge is exactly the
    coupling this split exists to remove; if it reappears, the split is pointless."""
    graph = grimp.build_graph("openalpha_cn")
    assert not graph.direct_import_exists(
        importer="openalpha_cn.runtime.contracts", imported="openalpha_cn.runtime.engine"
    )


def test_runtime_contracts_module_does_not_transitively_depend_on_storage() -> None:
    """`runtime.contracts` holds only pydantic request/result models; its own import
    closure must never reach `openalpha_cn.storage` (directly or transitively)."""
    graph = grimp.build_graph("openalpha_cn")
    upstream = graph.find_upstream_modules("openalpha_cn.runtime.contracts")
    storage_deps = {
        module
        for module in upstream
        if module == "openalpha_cn.storage" or module.startswith("openalpha_cn.storage.")
    }
    assert not storage_deps, f"runtime.contracts transitively depends on storage: {storage_deps}"


def test_contract_only_consumers_do_not_import_runtime_engine_directly() -> None:
    """Every module that only ever needed the request/result contracts must import them
    from `runtime.contracts`, not `runtime.engine`. This is the literal mutation-testing
    target: reverting any one of these four modules' import line back to `runtime.engine`
    must flip this edge back on and fail this test."""
    graph = grimp.build_graph("openalpha_cn")
    for module in CONTRACT_ONLY_CONSUMERS:
        assert not graph.direct_import_exists(
            importer=module, imported="openalpha_cn.runtime.engine"
        ), f"{module} still imports openalpha_cn.runtime.engine directly"


def test_contract_only_consumers_do_not_transitively_reach_engine_owned_storage_modules() -> None:
    """`runtime.batch`, `product.research`, and `backtest.validation` had no other reason to
    reach `storage.recovery`/`storage.sqlite` -- their only prior path was through
    `runtime.engine`'s contract classes, so it must vanish once they import from
    `runtime.contracts` instead.

    `cli.py` is deliberately excluded from this transitive check: it separately imports
    `backtest.replay.ReplayCorpus` for its `replay` subcommand, which legitimately needs the
    full `ResearchEngine` (and therefore `storage.sqlite`). That dependency is real,
    pre-existing, and out of this task's scope; `cli.py` is still covered by the direct-edge
    test above, which is the exact edge this task changes.
    """
    graph = grimp.build_graph("openalpha_cn")
    storage_targets = set(ENGINE_OWNED_STORAGE_MODULES)
    for module in (
        "openalpha_cn.runtime.batch",
        "openalpha_cn.product.research",
        "openalpha_cn.backtest.validation",
    ):
        upstream = graph.find_upstream_modules(module)
        touched = upstream & storage_targets
        assert not touched, f"{module} still transitively reaches {touched}"
