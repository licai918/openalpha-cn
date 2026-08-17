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

V2-P1-001 added `openalpha_cn.panel` (ADR-0002's panel plane), the first sibling
subpackage this file's dynamic `domain-purity` discovery ever actually had to cover
automatically -- the two tests near the end of this module close the matching gap on the
`storage-no-upward-deps` side, which (unlike `domain-purity`) is a static, finite
enumeration that a new sibling does not automatically join.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

import grimp
from importlinter import api as importlinter_api
from importlinter.cli import lint_imports

ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__)
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


def _lint_imports(**kwargs: object) -> int:
    """`importlinter.cli.lint_imports`, with the logging state it silently wrecks put back.

    `lint_imports` calls `importlinter.cli._configure_logging`, which calls
    `logging.config.dictConfig` with a config naming only `importlinter`, `grimp` and
    `_rustgrimp`. `dictConfig` defaults to `disable_existing_loggers=True`, and that default
    sets `.disabled = True` on **every** logger that already exists and is not named in (or a
    child of) the config -- including `openalpha_cn.storage.migrations`, which
    `tests/integration/storage/test_migrations.py` imports at collection time, long before any
    test in this module runs.

    A disabled logger emits nothing, so `caplog` captures nothing, so
    `test_run_migrations_logs_the_backup_path_and_each_applied_migration` and
    `test_run_migrations_logs_failure_without_leaking_the_underlying_exception_message` --
    V2-P0B-007's "the structured log is greppable" acceptance -- both failed on
    `assert 0 == 1`, deterministically, in 0.9 seconds:

        pytest tests/unit/test_import_layering.py tests/integration/storage/test_migrations.py
        -> 2 failed, 33 passed
        pytest tests/integration/storage/test_migrations.py tests/unit/test_import_layering.py
        -> 35 passed

    CI was green only because pytest's default collection order happens to put
    `tests/integration` before `tests/unit`; pytest-randomly, xdist, a directory rename or
    simply naming the two paths explicitly would have flipped it. Neither the root logger nor
    any parent's level, propagate flag or handler list changes -- which is why the first probe
    for this missed it -- and `logging.disable` is untouched too. The mutated state is the
    per-logger `disabled` attribute, and nothing in `logging` restores it.

    Every call in this module goes through here, and
    `test_no_test_in_this_module_calls_lint_imports_without_restoring_logging` keeps it that
    way; `test_running_the_import_linter_leaves_an_existing_logger_enabled` proves the restore
    is real rather than a comment.
    """
    manager = logging.Logger.manager
    before = {
        name: existing.disabled
        for name, existing in manager.loggerDict.items()
        if isinstance(existing, logging.Logger)
    }
    try:
        return lint_imports(**kwargs)  # type: ignore[arg-type]
    finally:
        for name, disabled in before.items():
            restored = manager.loggerDict.get(name)
            if isinstance(restored, logging.Logger):
                restored.disabled = disabled


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
    exit_code = _lint_imports(
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
        exit_code = _lint_imports(
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
    exit_code = _lint_imports(
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
        exit_code = _lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("domain-purity",),
        )
        assert exit_code == 1
    finally:
        probe_path.unlink()

    # Confirm the gate is green again once the probe is removed.
    exit_code = _lint_imports(
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
        exit_code = _lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("domain-purity",),
        )
        assert exit_code == 1
    finally:
        probe_path.unlink()

    exit_code = _lint_imports(
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

    exit_code = _lint_imports(
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


# V2-P1-001: `openalpha_cn.panel` (ADR-0002's panel plane, `src/openalpha_cn/panel/store.py`)
# is a new top-level sibling package, exactly the kind
# `test_domain_purity_holds_against_every_dynamically_discovered_sibling_subpackage` above
# already anticipated ("a future `panel/`... is invisible [to the static enumeration] until a
# human remembers to add an entry"). That test's dynamic, directory-driven discovery means
# `domain -> panel` is already covered with zero edits to this file or to
# `pyproject.toml`'s `domain-purity` contract. `storage-no-upward-deps`'s `forbidden_modules`
# is a *static*, finite enumeration (`agents`/`runtime`/`product`/`backtest` only) that does
# not automatically extend to a new sibling, so the two tests below close the gap directly
# with `grimp`, matching this file's existing "check the live graph, not a hand-maintained
# list" approach.


def test_panel_package_has_zero_direct_edges_into_any_other_openalpha_cn_subpackage() -> None:
    """`openalpha_cn.panel` is written to be fully self-contained (DuckDB + the standard
    library only) -- it has no reason to import `domain`, `storage`, or any other
    subpackage for this task's storage skeleton. Checked against every sibling discovered
    from the real directory structure (`_sibling_subpackages_of_domain()`, which excludes
    only `domain` itself), not a hand-copied list.
    """
    graph = grimp.build_graph("openalpha_cn")
    siblings = [name for name in _sibling_subpackages_of_domain() if name != "panel"]
    assert siblings, "expected at least one non-panel sibling subpackage to check against"
    violations = [
        sibling
        for sibling in siblings
        if graph.direct_import_exists(
            importer="openalpha_cn.panel", imported=f"openalpha_cn.{sibling}", as_packages=True
        )
    ]
    assert not violations, f"openalpha_cn.panel directly imports forbidden sibling(s): {violations}"


def test_storage_and_domain_have_zero_direct_edges_into_the_new_panel_package() -> None:
    """The other half of the same guarantee: `storage/` and `domain/` must never import
    `openalpha_cn.panel` -- V2-P1-001's brief calls this out explicitly, a new sibling
    plane must not become a new upward dependency for the packages underneath it. Proven
    directly with `grimp` rather than added to `storage-no-upward-deps`'s
    `forbidden_modules`, since `panel` currently has zero reason to ever be imported by
    `storage` at all (unlike `agents`/`runtime`/`product`/`backtest`, which `storage`
    legitimately *used* to import before V2-P0B-012's relocation) -- there is no live edge
    this static list needs to keep permanently rejecting, only one to keep proving absent.
    """
    graph = grimp.build_graph("openalpha_cn")
    for importer in ("openalpha_cn.storage", "openalpha_cn.domain"):
        assert not graph.direct_import_exists(
            importer=importer, imported="openalpha_cn.panel", as_packages=True
        ), f"{importer} must not import openalpha_cn.panel"


# --- backtest: a forbidden target in four contracts and a source in none (P3 acceptance) ----
#
# The P3 technical acceptance measured that `openalpha_cn.backtest` appeared in the four
# contracts above only as a forbidden *target*, so eight of P3's nine new modules were in no
# contract's source set at all. Its probe -- a new `backtest/_probe.py` importing `duckdb`,
# `panel.store` and `runtime.composition`, plus a `panel.store` import added to `factor_ic.py` --
# left lint-imports at "4 kept, 0 broken", ruff clean, mypy clean and the layering tests at
# 39 passed. Meanwhile "backtest may not import panel or storage" appears five times in P3
# source, once inside `factor_experiment.py`'s KNOWN_EXPERIMENT_LIMITATIONS entry
# `nothing_in_this_module_stores_an_artifact_or_can_be_made_to`. Load-bearing, and enforced by
# nothing.

BACKTEST_PACKAGE_PATH = ROOT / "src" / "openalpha_cn" / "backtest"
_BACKTEST_PROBE_PATH = BACKTEST_PACKAGE_PATH / "_layering_gate_probe.py"

BACKTEST_MODULES_EXEMPT_FROM_THE_STORE_CONTRACT: dict[str, str] = {
    "openalpha_cn.backtest": (
        "the package `__init__` re-exports `ReplayRunner`, so its own import closure is the "
        "replay harness's. Exempting the `__init__` rather than emptying it is deliberate: the "
        "re-export is this package's public surface and moving it would be an API change made "
        "for a contract's convenience."
    ),
    "openalpha_cn.backtest.replay": (
        "the replay harness composes a real `ResearchEngine` over `SQLiteRunRepository` and "
        "`SQLiteRecoveryStore`, because replaying a recorded run is what it does. It is the one "
        "module under `backtest/` that is a composition root, and `tests/replay/` is its "
        "acceptance."
    ),
}
"""The two `backtest/*.py` modules `backtest-studies-touch-no-store` cannot cover, with reasons.

Written as the *complement* of a directory glob rather than as the source list itself, which is
the whole point: `test_the_two_backtest_study_contracts_cover_every_module_in_the_package` holds
`source_modules + these` against the real directory in both directions, so a tenth
`backtest/*.py` is red until somebody either puts it in the contract or argues for it here.
"""

BACKTEST_MODULES_EXEMPT_FROM_THE_RUNTIME_CONTRACT: dict[str, str] = {
    **BACKTEST_MODULES_EXEMPT_FROM_THE_STORE_CONTRACT,
    "openalpha_cn.backtest.validation": (
        "imports `runtime.contracts.ResearchRunResult`, a pydantic result model, and is already "
        "one of the four `CONTRACT_ONLY_CONSUMERS` this file proves never reach `runtime.engine` "
        "or the storage modules it owns. It passes the store contract; only the composition-root "
        "one has to let it through."
    ),
}
"""One module wider, which is why the two contracts are two and not one.

Folding them together would have meant either exempting `validation.py` from a store contract it
passes, or making `openalpha_cn.runtime` legal for all nine remaining study modules on the
strength of one module's data-contract import.
"""


def _contract_source_modules(contract_id: str) -> set[str]:
    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    contract = next(
        options for options in config["contracts_options"] if options.get("id") == contract_id
    )
    sources = contract["source_modules"]
    assert isinstance(sources, list)
    return set(sources)


def _contract_forbidden_modules(contract_id: str) -> set[str]:
    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    contract = next(
        options for options in config["contracts_options"] if options.get("id") == contract_id
    )
    forbidden = contract["forbidden_modules"]
    assert isinstance(forbidden, list)
    return set(forbidden)


def _backtest_modules_on_disk() -> set[str]:
    """Every module `backtest/` holds, `__init__` included and named as the package itself."""
    return {
        "openalpha_cn.backtest" if path.stem == "__init__" else f"openalpha_cn.backtest.{path.stem}"
        for path in BACKTEST_PACKAGE_PATH.glob("*.py")
    }


def test_the_two_backtest_study_contracts_cover_every_module_in_the_package() -> None:
    """Each contract's source list plus its named exemptions is exactly what is on disk.

    An explicit `source_modules` list is what makes these two contracts possible at all -- there
    is no "package except these" form -- and it is also the thing a tenth `backtest/*.py` would
    walk straight past. So the list is checked against the directory in both directions: a module
    in neither the list nor the exemption table fails, and an exemption for a module that no
    longer exists fails too.
    """
    on_disk = _backtest_modules_on_disk()

    for contract_id, exempt in (
        ("backtest-studies-touch-no-store", BACKTEST_MODULES_EXEMPT_FROM_THE_STORE_CONTRACT),
        (
            "backtest-studies-reach-no-composition-root",
            BACKTEST_MODULES_EXEMPT_FROM_THE_RUNTIME_CONTRACT,
        ),
    ):
        sources = _contract_source_modules(contract_id)
        assert not sources & set(exempt), (
            f"{contract_id} names {sorted(sources & set(exempt))} as both a source and an "
            "exemption; the exemption is dead and would hide the source going missing"
        )
        uncovered = sorted(on_disk - sources - set(exempt))
        assert not uncovered, (
            f"{uncovered} is a module under backtest/ that {contract_id} does not cover and "
            "that nothing exempts. Add it to source_modules in pyproject.toml, or add it to "
            "the exemption table above with the sentence that says why it may reach a store "
            "or a composition root"
        )
        vanished = sorted((sources | set(exempt)) - on_disk)
        assert not vanished, f"{contract_id} names {vanished}, which is not on disk"


def test_the_backtest_contracts_forbid_the_targets_the_acceptance_probe_reached() -> None:
    """The three things the acceptance probe imported must each be forbidden somewhere.

    `duckdb` and `openalpha_cn.panel` by the whole-package contract, `openalpha_cn.storage` and
    `openalpha_cn.runtime` by the two study contracts. Asserted against the parsed configuration
    rather than against the file's text, so reformatting the TOML cannot make this pass while the
    contract says something else.
    """
    whole_package = _contract_forbidden_modules("backtest-no-numeric-stack-or-panel-plane")
    store = _contract_forbidden_modules("backtest-studies-touch-no-store")
    composition = _contract_forbidden_modules("backtest-studies-reach-no-composition-root")

    assert {"duckdb", "pandas", "openalpha_cn.panel"} <= whole_package
    assert "numpy" not in whole_package, (
        "numpy cannot be forbidden package-wide: backtest/replay.py reaches runtime/seeding.py, "
        "whose numpy import is a real guarded optional-determinism hook. It is forbidden for the "
        "ten study modules instead, which is where ADR-0003's decision actually bites"
    )
    assert {"numpy", "openalpha_cn.storage"} <= store
    assert composition == {"openalpha_cn.runtime"}
    assert _contract_source_modules("backtest-no-numeric-stack-or-panel-plane") == {
        "openalpha_cn.backtest"
    }


def test_the_backtest_gate_rejects_a_probe_that_reaches_duckdb_and_the_panel_store() -> None:
    """The acceptance probe, reproduced: a new module under `backtest/` reaching both.

    This is the exact file the P3 technical acceptance created to prove the gap, and the whole
    package is the contract's source precisely so a *new* module is covered on arrival rather
    than once somebody adds it to a list.
    """
    assert not _BACKTEST_PROBE_PATH.exists(), "probe file must not already exist"
    _BACKTEST_PROBE_PATH.write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        "import duckdb\n\n"
        "from openalpha_cn.panel.store import PanelStore\n\n"
        '__all__ = ["PanelStore", "duckdb"]\n',
        encoding="utf-8",
    )
    try:
        exit_code = _lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("backtest-no-numeric-stack-or-panel-plane",),
        )
        assert exit_code == 1, (
            "lint-imports should reject backtest/_layering_gate_probe.py -> duckdb and -> "
            "openalpha_cn.panel.store; if this passes, backtest is a forbidden target again "
            "and a source of nothing"
        )
    finally:
        _BACKTEST_PROBE_PATH.unlink()

    exit_code = _lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("backtest-no-numeric-stack-or-panel-plane",),
    )
    assert exit_code == 0


def test_the_experiment_module_can_no_longer_reach_the_document_store_it_says_it_cannot() -> None:
    """`nothing_in_this_module_stores_an_artifact_or_can_be_made_to`, made true.

    That is a `KNOWN_EXPERIMENT_LIMITATIONS` code in `backtest/factor_experiment.py`, and the P3
    acceptance imported `storage.factor_experiments` into that module and watched 121 tests pass.
    The import is added here to the real file and removed in `finally`, rather than to a fresh
    probe module, because `backtest-studies-touch-no-store` lists its sources explicitly -- a new
    file would not be one, so a probe module would prove nothing about this claim.

    `factor_view.ExperimentDocumentStore` is a `Protocol` declared beside the consumer exactly so
    `storage/factor_experiments.py` satisfies it with no import in either direction; this is what
    keeps that design from being abandoned quietly.
    """
    module_path = BACKTEST_PACKAGE_PATH / "factor_experiment.py"
    original = module_path.read_text(encoding="utf-8")
    module_path.write_text(
        original + "\n\nfrom openalpha_cn.storage.factor_experiments import FileExperimentStore\n\n"
        '__all__ = ["FileExperimentStore"]\n',
        encoding="utf-8",
    )
    try:
        exit_code = _lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("backtest-studies-touch-no-store",),
        )
        assert exit_code == 1, (
            "backtest/factor_experiment.py importing storage.factor_experiments must break "
            "backtest-studies-touch-no-store -- otherwise its own "
            "nothing_in_this_module_stores_an_artifact_or_can_be_made_to is prose"
        )
    finally:
        module_path.write_text(original, encoding="utf-8")

    exit_code = _lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("backtest-studies-touch-no-store",),
    )
    assert exit_code == 0


def test_the_study_contracts_reject_numpy_and_a_composition_root_added_to_a_real_study() -> None:
    """ADR-0003's decision and the composition-root rule, each driven on a listed source module.

    Two separate probes on `factor_ic.py`, and each is checked against *both* study contracts so
    that a contract going red for the wrong reason cannot pass for the right one: `import numpy`
    breaks the store contract and leaves the composition-root one alone, and a
    `runtime.contracts` import does the reverse.
    """
    module_path = BACKTEST_PACKAGE_PATH / "factor_ic.py"
    original = module_path.read_text(encoding="utf-8")

    for addition, broken, intact in (
        (
            '\n\nimport numpy\n\n__all__ = ["numpy"]\n',
            "backtest-studies-touch-no-store",
            "backtest-studies-reach-no-composition-root",
        ),
        (
            "\n\nfrom openalpha_cn.runtime.contracts import ResearchRunResult\n\n"
            '__all__ = ["ResearchRunResult"]\n',
            "backtest-studies-reach-no-composition-root",
            "backtest-studies-touch-no-store",
        ),
    ):
        module_path.write_text(original + addition, encoding="utf-8")
        try:
            assert (
                _lint_imports(
                    config_filename=str(ROOT / "pyproject.toml"),
                    no_cache=True,
                    limit_to_contracts=(broken,),
                )
                == 1
            ), f"{broken} should have rejected backtest/factor_ic.py{addition!r}"
            assert (
                _lint_imports(
                    config_filename=str(ROOT / "pyproject.toml"),
                    no_cache=True,
                    limit_to_contracts=(intact,),
                )
                == 0
            ), f"{intact} has nothing to say about {addition!r} and must stay green"
        finally:
            module_path.write_text(original, encoding="utf-8")

    assert _lint_imports(config_filename=str(ROOT / "pyproject.toml"), no_cache=True) == 0, (
        "every contract must be green again once both probes are removed"
    )


def test_the_replay_harness_and_the_outcome_validator_keep_the_imports_they_exist_for() -> None:
    """The positive half: the two exempted modules' real edges are still there.

    A contract that passed because the design it permits had quietly gone away would be a gate
    measuring nothing, and these two exemptions are the only reason the study contracts cannot be
    stated over the whole package. `replay.py` composing a SQLite-backed engine and
    `validation.py` taking one pydantic result model are what the exemptions buy.
    """
    graph = grimp.build_graph("openalpha_cn")

    for imported in (
        "openalpha_cn.runtime.engine",
        "openalpha_cn.storage.sqlite",
        "openalpha_cn.storage.recovery",
        "openalpha_cn.storage.migrations",
    ):
        assert graph.direct_import_exists(
            importer="openalpha_cn.backtest.replay", imported=imported
        ), f"sanity check: the replay harness is supposed to import {imported}"
    assert graph.direct_import_exists(
        importer="openalpha_cn.backtest.validation", imported="openalpha_cn.runtime.contracts"
    ), "sanity check: the outcome validator is supposed to take ResearchRunResult"


# --- the logging state `lint_imports` wrecks, and the guard that puts it back (P1 review) ---
#
# `importlinter.cli.lint_imports` reconfigures logging as a side effect, and its
# `dictConfig` call disables every pre-existing logger in the process. That made this
# module's tests silently break `tests/integration/storage/test_migrations.py`'s two
# `caplog` acceptances whenever pytest ran this file first. `_lint_imports` above is the
# containment; the two tests below are what keeps the containment honest.


def test_running_the_import_linter_leaves_an_existing_logger_enabled() -> None:
    """The pollution itself, reproduced and then shown closed.

    `openalpha_cn.storage.migrations` is the logger that actually got disabled -- it exists
    by the time this runs because pytest imports every test module during collection, and
    `tests/integration/storage/test_migrations.py` creates it at import time. Asserted here
    against a bare `lint_imports` first, so this test fails if `dictConfig`'s
    `disable_existing_loggers` default ever stops being the cause, and then against
    `_lint_imports`, which is the fix.
    """
    logger = logging.getLogger("openalpha_cn.storage.migrations")
    assert not logger.disabled, "precondition: the logger under test starts enabled"

    try:
        lint_imports(
            config_filename=str(ROOT / "pyproject.toml"),
            no_cache=True,
            limit_to_contracts=("domain-purity",),
        )
        assert logger.disabled, (
            "the raw importlinter CLI is expected to disable existing loggers via "
            "dictConfig(disable_existing_loggers=True); if it no longer does, _lint_imports "
            "can be simplified -- but check the new mechanism before deleting it"
        )
    finally:
        logger.disabled = False

    exit_code = _lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("domain-purity",),
    )

    assert exit_code == 0
    assert not logger.disabled


def test_no_test_in_this_module_calls_lint_imports_without_restoring_logging() -> None:
    """One bare `lint_imports(` added later would reintroduce the order dependence for the
    whole suite, and it would show up as two unrelated tests failing in another directory --
    the least findable failure shape there is. So the rule is checked on this file's own
    source: the only call to the raw CLI is the one inside `_lint_imports`, plus the single
    deliberate one in the test above that proves the pollution is still real.
    """
    source = THIS_FILE.read_text(encoding="utf-8")
    # A backtick before the name means this file is *talking about* the call, not making it.
    bare_calls = re.findall(r"(?<![_`])lint_imports\(", source)

    assert len(bare_calls) == 2, (
        f"expected exactly 2 bare `lint_imports(` calls (one inside _lint_imports, one in "
        f"test_running_the_import_linter_leaves_an_existing_logger_enabled); found "
        f"{len(bare_calls)}. Every other call site must go through `_lint_imports`"
    )
