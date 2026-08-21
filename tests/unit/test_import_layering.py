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

import ast
import itertools
import logging
import re
import tomllib
from pathlib import Path
from typing import Final

import grimp
import pytest
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
`source_modules + these` against the real directory in both directions, so a twelfth
`backtest/*.py` is red until somebody either puts it in the contract or argues for it here.
`V2-P4-004`'s `cross_section.py` was the tenth and `V2-P4-005`'s `candidate_ranking.py` the
eleventh, and this test is what made each of them join both.

**That test is the gate for a new file, and `lint-imports` alone is not**, which `V2-P4-093`
measured rather than inferred: a probe module under `backtest/` importing `numpy` and
`openalpha_cn.storage` gives `8 kept, 0 broken`, because neither target is on the whole-package
contract and a new file is on neither enumerated list.
`test_lint_imports_alone_does_not_stop_a_new_backtest_module_reaching_numpy_or_a_store` drives
both halves in one place, so the distinction is a run rather than a sentence.
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
passes, or making `openalpha_cn.runtime` legal for all eleven remaining study modules on the
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
    is no "package except these" form -- and it is also the thing a new `backtest/*.py` would
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


def test_lint_imports_alone_does_not_stop_a_new_backtest_module_reaching_numpy_or_a_store() -> None:
    """`V2-P4-093`: which half of the gate actually catches a *new* file, measured.

    The whole-package contract has `openalpha_cn.backtest` as its source, so it covers a new
    module on arrival -- but only for what *it* forbids: `duckdb`, `pandas`, `scipy`, `sklearn`,
    `openalpha_cn.panel` and the faces. `numpy` and `openalpha_cn.storage` are deliberately not
    on that list, because `backtest/replay.py` composes a real store and reaches
    `runtime/seeding.py`'s guarded `numpy` hook. They are forbidden by the two per-module
    contracts instead, and those enumerate their sources -- which a new file is not one of.

    So a probe module importing both passes `lint-imports` at **8 kept, 0 broken**, and what
    goes red is the pytest assertion above it. That is a true statement about the CI pipeline
    and a false one about `lint-imports` in isolation, and several docstrings said the strong
    form. This is the measurement they now point at.

    The other direction is asserted in the same breath -- the whole-package contract really does
    reject the probe `test_the_backtest_gate_rejects_a_probe_that_reaches_duckdb_and_the_panel_
    store` writes -- so "the contracts catch nothing new" is not what is being claimed either.
    """
    assert not _BACKTEST_PROBE_PATH.exists(), "probe file must not already exist"
    _BACKTEST_PROBE_PATH.write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        "import numpy\n\n"
        "from openalpha_cn.storage.predictions import FilePredictionStore\n\n"
        '__all__ = ["FilePredictionStore", "numpy"]\n',
        encoding="utf-8",
    )
    try:
        assert _lint_imports(config_filename=str(ROOT / "pyproject.toml"), no_cache=True) == 0, (
            "if this now fails, a contract has been widened to cover a module it does not "
            "name, and the sentences pointing at this test are stale in the good direction"
        )
        with pytest.raises(AssertionError, match="does not cover"):
            test_the_two_backtest_study_contracts_cover_every_module_in_the_package()
    finally:
        _BACKTEST_PROBE_PATH.unlink()

    assert _lint_imports(config_filename=str(ROOT / "pyproject.toml"), no_cache=True) == 0


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
        "twelve study modules instead, which is where ADR-0003's decision actually bites"
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


def test_ci_runs_the_import_linter_through_the_form_that_is_not_a_silent_no_op() -> None:
    """`V2-P4-047`. `python -m importlinter.cli lint-imports` exits 0 and prints nothing.

    Measured on this install: that invocation returns exit code 0 with no output **even on a
    flagrant break**, because `importlinter.cli` is a `click` group with no `__main__` guard, so
    `-m` loads the module and runs nothing. It looks exactly like a clean run. Every contract in
    this file could be broken and a pipeline using that form would stay green.

    Nothing in the repository depends on it today -- `.github/workflows/quality.yml` uses the
    console script -- and this test is what keeps that true, because the two spellings are
    indistinguishable by their output and a "portability" edit from one to the other would look
    harmless in review.
    """
    workflow = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    invocations = [line.strip() for line in workflow.splitlines() if "lint-imports" in line]

    assert invocations, (
        "no step in .github/workflows/quality.yml runs lint-imports at all, so every contract in "
        "pyproject.toml is enforced only on developer machines"
    )
    for line in invocations:
        assert "python -m importlinter" not in line and "-m importlinter.cli" not in line, (
            f"{line!r} runs the import linter through `python -m importlinter.cli`, which on this "
            "install exits 0 with no output even when a contract is broken -- it is a silent "
            "no-op, not a check. Use the `lint-imports` console script"
        )
        assert "lint-imports" in line, line


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


# --------------------------------------------------------------------------------------------
# V2-P4-035: the order contract's claim, held to its enforcement
# --------------------------------------------------------------------------------------------

ORDER_CONTRACT_ID = "ranking-creates-no-portfolio-order"
ORDER_INTENT_MARKER = "order intent"

ORDER_GUARD_POINTER: Final[str] = "tests/unit/backtest/test_ranking_sources_fill_no_order.py"
"""The one guard `ranking-creates-no-portfolio-order`'s comment promises, named by path.

The contract cannot forbid `openalpha_cn.backtest.execution` -- `backtest/cross_section.py`
imports the fill policy for `V2-P4-004`'s tradeability filter -- so its comment discloses the
residual gap and points at this file as what guards it behaviourally instead. That sentence is
the whole of the reader's assurance, so the path in it is bound here rather than left to a
pattern that any test file would satisfy.

`V2-P4-057`: the check that was supposed to enforce this collected *any* `tests/**/test_*.py`
substring from the block and asserted each resolved. Three other pointers live in the same
block, so the assertion stayed green with this sentence gutted and this file deleted -- measured
at 31 passed, `lint-imports` 8 kept / 0 broken, with `ls tests/unit/backtest/ | grep -c
ranking_sources` reporting `0`.
"""

# Every definition under `src/` whose own docstring calls itself an order intent **in those two
# English words**. Discovered off the AST by `_order_intent_declarations` and asserted *equal* to
# this table rather than merely covered by it, so a renamed declaration cannot go missing.
#
# `V2-P4-035` is the reason this table exists. `ranking-creates-no-portfolio-order` was named
# "the candidate ranking contracts reach no module that declares or simulates an order" and its
# comment asserted that its three `forbidden_modules` "are the whole of where an order intent is
# declared or simulated in this repository". Both sentences were false and neither was checked:
# `backtest/execution.py` declares `ExecutionRequest`, "a simplified cash-equity order intent",
# and simulates a fill through `AShareExecutionPolicy.execute`, and **both contract sources
# reach it** -- `shortlist_gate -> candidate_ranking -> cross_section -> execution`. A probe
# placed in `candidate_ranking.py` filled an order (`status=filled qty=100 filled_price=10.20
# total_cost=5.01`) while the import linter reported 8 kept / 0 broken.
#
# `V2-P4-048` made this a set of (module, qualified name) pairs rather than a `dict` keyed by
# module. Keyed by module, a second intent in an already-listed file **overwrote or was
# overwritten depending on where it sat in the source**: the identical class inserted above
# `ExecutionRequest` passed the audit and the same class appended below it failed. A table whose
# verdict depends on line order is not a census.
ORDER_INTENT_DECLARATIONS = {
    ("openalpha_cn.domain.portfolio", "PortfolioOrder"),
    ("openalpha_cn.backtest.execution", "ExecutionRequest"),
}

ORDER_INTENT_SUFFIXES = ("*.py", "*.pyi")
"""Both Python source forms. `V2-P4-048`: `*.py` alone could not see a `.pyi` declaration."""


def _marker_in(docstring: str) -> bool:
    """Whether the docstring uses the two words `ORDER_INTENT_MARKER`, however it is laid out.

    Runs of whitespace are collapsed to one space before matching, so the words still read as
    the marker when a line break or a double space falls between them. `V2-P4-056`: this is a
    **layout** normalisation and not a vocabulary widening -- it still matches exactly the two
    ASCII words `order intent` and nothing else. The reason it has to exist is that the author
    does not control where the break lands: this repository's `line-length` is 100 and the
    formatter reflows prose to it, so a docstring whose words the author typed adjacent can be
    split by a later unrelated edit. Without this, the naming convention the census enforces
    would really be "type these two words and hope they do not get wrapped", which is not a
    convention anybody can follow -- and the `V2-P4-056` probe was a class docstring wrapped at
    exactly the repository's own limit, which the census read as clean.

    Measured on the clean tree: collapsing whitespace finds the same **two** declarations the
    unnormalised match finds, so it cost zero false positives.
    """
    return ORDER_INTENT_MARKER in re.sub(r"\s+", " ", docstring).lower()


def _string_literal(node: ast.AST) -> str:
    """A string expression's text in **any** literal form, including an f-string.

    `ast.get_docstring` returns `None` for an f-string docstring, because `JoinedStr` is not
    `Constant`. `V2-P4-048`'s probe used exactly that to hide an order intent in plain sight, so
    the literal parts of a `JoinedStr` are joined and read here too. What is deliberately *not*
    reconstructed is the interpolated part -- a docstring whose marker only appears after
    substitution is not readable in the source either, and chasing it would mean evaluating
    arbitrary expressions at audit time.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def _docstring_of(node: ast.AST) -> str:
    """The node's own docstring: its first statement, when that is a string expression."""
    body = getattr(node, "body", None)
    if not body or not isinstance(body[0], ast.Expr):
        return ""
    return _string_literal(body[0].value)


def _qualified_names(tree: ast.Module) -> list[tuple[str, str]]:
    """Every class and function in the tree, as (qualified name, docstring).

    Walked with a parent trail rather than `ast.walk`, so a nested declaration is reported as
    `Outer.Inner` and cannot silently collide with a top-level one of the same name.
    """
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, trail: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                name = (*trail, child.name)
                found.append((".".join(name), _docstring_of(child)))
                visit(child, name)
            else:
                visit(child, trail)

    visit(tree, ())
    return found


def _assigned_docstrings(tree: ast.Module) -> list[tuple[str, str]]:
    """Every `X.__doc__ = "..."` and every in-scope bare `__doc__ = "..."`, as (target, doc).

    A docstring does not have to be the first statement to be a docstring: assigning `__doc__`
    after the class body produces exactly the same `help()` output and exactly the same intent,
    and `ast.get_docstring` cannot see it. `V2-P4-048`'s probe used this too.

    The **module's own** `__doc__ = "..."` is the one form deliberately skipped, and skipping it
    is `V2-P4-056`'s repair rather than a hole: see `_order_intent_declarations` for the
    measurement. A bare `__doc__` inside a class body is *not* skipped -- there it is that
    class's docstring, and it declares a named thing -- so the exclusion is written against the
    module body specifically rather than against the syntax.
    """
    found: list[tuple[str, str]] = []
    module_level = set(tree.body)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        doc = _string_literal(node.value)
        if not doc:
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == "__doc__":
                owner = target.value.id if isinstance(target.value, ast.Name) else "<expr>"
                found.append((f"{owner}.__doc__", doc))
            elif isinstance(target, ast.Name) and target.id == "__doc__":
                if node in module_level:
                    continue
                found.append(("__doc__", doc))
    return found


def _attribute_docstrings(tree: ast.Module) -> list[tuple[str, str]]:
    """Every PEP-258 attribute docstring, as (qualified attribute name, docstring).

    The form is an assignment immediately followed by a bare string expression in the same
    suite -- `NAME: Final[...] = ...` then `\"\"\"...\"\"\"` -- which is how this repository
    documents most of its constants, and how `tools`, `help()` and every documentation
    generator read them. It is a **closed**, purely syntactic form, which is why covering it is
    a finite change rather than a guess.

    `V2-P4-056` is why it is here. The census enumerated the docstring-form axis as "literal,
    f-string, assigned `__doc__`" and called the axis closed; that enumeration simply omitted
    this one. Measured in the audited tree at the time: **715** attribute docstrings across the
    124 files under `src/openalpha_cn`, none of which the census could read. The probe was
    `PROBE_ATTRIBUTE_TICKET: Final[...] = {...}` followed by `\"\"\"A single-security order
    intent...\"\"\"`, which `grep -n \"order intent\"` finds on one line and the census reported
    as clean.

    Scanned in every suite, not just class and module bodies, so an attribute documented inside
    `if TYPE_CHECKING:` or inside `__init__` is read too. Measured on the clean tree: adding
    this form finds the same **two** declarations, so it cost zero false positives.
    """
    found: list[tuple[str, str]] = []

    def named(node: ast.Assign | ast.AnnAssign) -> str:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                return target.id
            if isinstance(target, ast.Attribute):
                return target.attr
        return "<expr>"

    def scan(scope: ast.AST, trail: tuple[str, ...]) -> None:
        body = getattr(scope, "body", None)
        if not isinstance(body, list):
            return
        for previous, node in itertools.pairwise(body):
            if not isinstance(previous, ast.Assign | ast.AnnAssign):
                continue
            if not isinstance(node, ast.Expr):
                continue
            doc = _string_literal(node.value)
            if doc:
                found.append((".".join((*trail, named(previous))), doc))

    def visit(node: ast.AST, trail: tuple[str, ...]) -> None:
        scan(node, trail)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, (*trail, child.name))
            else:
                visit(child, trail)

    visit(tree, ())
    return found


def _documented_declarations(tree: ast.Module) -> list[tuple[str, str]]:
    """Every named declaration in the tree that carries a docstring, in any form this repository
    uses, as (qualified name, docstring).

    The **one** place the three readers are composed. `V2-P4-056` had them composed twice --
    once in `_order_intent_declarations` and once in the test that pins which forms are covered
    -- and a mutation dropping `_attribute_docstrings` from the census survived, because the
    test called the reader directly and never went through the census. Two call sites meant the
    coverage test was testing the readers rather than the audit. Now there is one, so the test
    and the census cannot disagree about what "every docstring form" means.
    """
    return _qualified_names(tree) + _assigned_docstrings(tree) + _attribute_docstrings(tree)


def _order_intent_declarations() -> set[tuple[str, str]]:
    """Every order intent declared under `src/openalpha_cn`, as {(module, qualified name)}.

    A **set of pairs**, not a dict keyed by module: see `ORDER_INTENT_DECLARATIONS`.

    Widened by `V2-P4-048` along every axis that can be widened without guessing -- node kind
    (functions as well as classes, at any nesting depth), docstring form, and file extension
    (`.pyi` as well as `.py`). The marker vocabulary is the one axis that is *not* closed, and
    the claim is narrowed to match rather than the matcher widened to guess -- see
    `test_every_order_intent_is_forbidden_to_the_ranking_or_disclosed_as_reachable`.

    **`V2-P4-056`: the docstring-form axis was called closed while enumerated wrong.** The
    enumeration was "literal, f-string, assigned `__doc__`", and two forms this repository uses
    constantly were outside it. Both were demonstrated in `backtest/cross_section.py`, a module
    both contract sources reach, each carrying the literal ASCII words, and the census reported
    `MATCHES DECLARED TABLE: True` at 31 passed:

    * a class docstring **wrapped at this repository's own 100-column limit**, so the two words
      straddled the line break -- now read by `_marker_in`;
    * a **PEP-258 attribute docstring**, which `grep -n \"order intent\"` finds on a single line
      -- now read by `_attribute_docstrings`, and there are 715 of them under `src/`.

    An isolated matcher probe found five misses in all, every one with both words present.

    **The module docstring is the form deliberately left out, and that is measured too.** The
    census had an inconsistency worth naming: an *assigned* module `__doc__` was read while the
    *literal* module docstring was not. Resolving it by reading module docstrings was tried
    and rejected on the number. On the clean tree that form finds exactly one site beyond the
    declared two -- `openalpha_cn.backtest.candidate_ranking`'s module docstring, which says
    \"Those three are not every order intent in the repository\". That is prose *about* order
    intents, not a module declaring itself one, and it is a **false positive with teeth**:
    `candidate_ranking` is one of this contract's own `source_modules`, so the partition below
    would demand it be disclosed as a deliberately reachable order intent, which is false, and
    a clean tree would go red until somebody wrote that falsehood into `pyproject.toml`. So the
    rule is uniform in the other direction and stated rather than left as an accident: **the
    census reads the docstring of a named definition -- class, function, or attribute -- and
    never a module's own docstring, in either form.** A module docstring declares nothing that
    can be constructed or filled; a class, function or attribute does.

    False positives for the two forms that *were* added, measured on the clean tree rather than
    assumed: **zero**. The census finds the same two declarations it found before.
    """
    package_root = ROOT / "src" / "openalpha_cn"
    found: set[tuple[str, str]] = set()
    for suffix in ORDER_INTENT_SUFFIXES:
        for path in sorted(package_root.rglob(suffix)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parts = path.relative_to(package_root).with_suffix("").parts
            module = ".".join(("openalpha_cn", *parts))
            declarations = _documented_declarations(tree)
            found |= {(module, name) for name, doc in declarations if _marker_in(doc)}
    return found


def _order_contract() -> dict[str, object]:
    config = importlinter_api.read_configuration(str(ROOT / "pyproject.toml"))
    return next(
        contract
        for contract in config["contracts_options"]
        if contract.get("id") == ORDER_CONTRACT_ID
    )


def _order_contract_block() -> str:
    """The contract's own comment lines, and nothing else. `V2-P4-048` narrowed this hard.

    Read off the file rather than the parsed configuration because the disclosure this checks
    for lives in a `#` comment, which `tomllib` throws away.

    Two defects, both of which made `assert module in disclosure` satisfiable without a
    disclosure. First, the slice ended at the next `[[tool.importlinter.contracts]]` -- and this
    is the **last** contract in `pyproject.toml`, so it ran to EOF and swallowed every section
    appended after it. Measured: the entire `V2-P4-035` rationale deleted and the module name
    re-added under a `[tool.probe_unrelated]` section left the audit at 29 passed and
    `lint-imports` at 8 kept / 0 broken. The slice now stops at the next table header of any
    kind, `[` at column zero. Second, it returned the TOML values too, so a module name in
    `forbidden_modules` or `source_modules` counted as prose about it; only `#` lines survive
    now, which is what "the contract's comment names the module" actually meant.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    tail = text[text.index(f'id = "{ORDER_CONTRACT_ID}"') :]
    lines: list[str] = []
    for index, line in enumerate(tail.splitlines()):
        if index and line.startswith("["):
            break
        if line.startswith("#"):
            lines.append(line)
    return "\n".join(lines)


def test_every_order_intent_is_forbidden_to_the_ranking_or_disclosed_as_reachable() -> None:
    """`V2-P4-035`. The contract's claim and its enforcement, forced to agree by measurement.

    The defect this closes was not a missing prohibition -- it was a name and a comment that
    described a prohibition nobody had. The repair could not be "forbid
    `openalpha_cn.backtest.execution`" either: `backtest/cross_section.py` imports
    `AShareExecutionPolicy` to decide tradeability, which is `V2-P4-004`'s hard filter and a
    shipped feature, so that addition would have had to break a feature to become true.

    So the binding is a partition, and both halves are measured off `grimp` rather than
    asserted. Every order intent in the tree is either **forbidden** to both contract sources
    (and then no chain may exist) or **disclosed by name** in the contract's own comment as
    deliberately reachable (and then a chain must exist, from every source, or the disclosure is
    describing a risk that is not there). "Fixing" this by forbidding `execution` fails the
    reachability assertion *and* the tradeability filter, which is the outcome that should be
    hard.

    **`V2-P4-048`: what "order intent" means here, and the claim this test does not make.**
    The previous wording was that "a third order intent added anywhere under `src/` fails the
    equality below". That was false as written. The matcher requires a `ClassDef` with a literal
    first-statement docstring containing the ASCII substring `order intent`, under
    `src/openalpha_cn/**/*.py`; the re-acceptance added **seven** order intents to
    `backtest/cross_section.py` -- a module both sources reach -- as a function, a `.pyi` class,
    an assigned `__doc__`, an f-string docstring, `一个简化的现金股票订单意图`, a "buy
    instruction" and a "trade ticket", and this audit reported `MATCHES THE DECLARED TABLE: True`
    with the whole file at 29 passed.

    The choice was widen the matcher or narrow the claim, and **the axes split cleanly, so this
    does both -- to different axes**:

    * **Widened, because the axis is closed and enumerable.** Node kind (class, function, async
      function, at any nesting depth), docstring form and file extension (`.py`, `.pyi`). Each
      is a finite set defined by the language, so covering it is a change that can be finished
      and checked. Measured on the clean tree, the widened matcher finds **exactly the same
      two** declarations, so this cost no false positives.

      `V2-P4-056` is the correction that this bullet needed and the reason its enumeration is
      no longer written out here. "Closed" was true; the enumeration `V2-P4-048` gave for the
      docstring-form axis -- "literal, f-string, assigned `__doc__`" -- was not the whole of it,
      and calling an axis closed is a claim about the enumeration rather than about the concept.
      Two forms this repository uses everywhere were missing, and a declaration in either was
      invisible: a docstring **wrapped at the repository's own 100-column limit**, and a
      **PEP-258 attribute docstring** (715 of them under `src/`). Both are read now. The form
      the census does **not** read is the module's own docstring, in either form -- which is a
      measured exclusion and not a fourth omission; `_order_intent_declarations` carries the
      number and the reason.
    * **Narrowed, because the axis is open and a wider claim would be unfalsifiable.** The
      *marker vocabulary*. Hunting synonyms -- "buy instruction", "trade ticket", "sell order",
      `订单意图`, `委托` -- across a repository this heavily documented is the trap
      `test_known_limitation_registries.py`'s docstring names: the synonym set cannot be
      enumerated, so a green run would prove nothing, and the false positives would be constant
      (`cross_section.py`'s own module docstring says "Every scored name is offered as a real
      buy"). So the claim is now exactly: **every definition whose docstring uses the two English
      words `order intent`**. That is a naming *convention*, and this test enforces the
      convention rather than pretending to detect the concept.

    What the narrowing gives up is real, and is covered elsewhere rather than waved away: an
    order intent declared under another name is invisible here. What makes it discoverable is
    that it cannot be *used* from either contract source without going red --
    `tests/unit/backtest/test_ranking_sources_fill_no_order.py` wraps the real fill policy during
    a real run and asks each fill which files are on its stack, so it never reads a name at all.
    A class nobody can call an order from is a documentation defect; a class somebody can is a
    contract defect, and the contract defect is the one that is bound.
    """
    declared = _order_intent_declarations()
    assert declared == ORDER_INTENT_DECLARATIONS, (
        f"the set of definitions documenting themselves with the words {ORDER_INTENT_MARKER!r} "
        f"changed: expected {sorted(ORDER_INTENT_DECLARATIONS)}, found {sorted(declared)}. Every "
        f"one of them has to be either in {ORDER_CONTRACT_ID}'s forbidden_modules or disclosed "
        "by name in its comment as deliberately reachable -- update this table and pick the side "
        "in the same commit"
    )

    contract = _order_contract()
    forbidden = set(contract["forbidden_modules"])  # type: ignore[call-overload]
    sources = set(contract["source_modules"])  # type: ignore[call-overload]
    disclosure = _order_contract_block()
    graph = grimp.build_graph("openalpha_cn")

    for module, name in sorted(declared):
        reaching = {
            source
            for source in sources
            if graph.chain_exists(importer=source, imported=module, as_packages=False)
        }
        if module in forbidden:
            assert not reaching, (
                f"{ORDER_CONTRACT_ID} forbids {module}, and {sorted(reaching)} reaches it anyway"
            )
            continue
        assert reaching == sources, (
            f"{module} declares an order intent ({name}) and {ORDER_CONTRACT_ID} does not forbid "
            f"it, so the comment discloses it as deliberately reachable -- but only "
            f"{sorted(reaching)} of {sorted(sources)} actually reaches it. Either the disclosure "
            "is stale or the module could have been forbidden after all"
        )
        assert module in disclosure, (
            f"{module} declares an order intent ({name}), is NOT in "
            f"{ORDER_CONTRACT_ID}'s forbidden_modules, and IS reachable from {sorted(sources)}. "
            "That combination is exactly the V2-P4-035 defect, and it is only honest if the "
            "contract's comment names the module and says why it cannot be forbidden. It does "
            "not."
        )


ORDER_INTENT_DOCSTRING_FORMS: Final[tuple[tuple[str, bool, str], ...]] = (
    (
        "literal class docstring",
        True,
        'class Plain:\n    """A simplified cash-equity order intent."""\n',
    ),
    (
        "literal function docstring",
        True,
        'def build():\n    """Build an order intent."""\n',
    ),
    (
        "f-string docstring",
        True,
        'class Interpolated:\n    f"""A simplified cash-equity order intent for {SUBJECT}."""\n',
    ),
    (
        "assigned __doc__ on a class",
        True,
        'class Assigned:\n    pass\n\n\nAssigned.__doc__ = "A cash-equity order intent."\n',
    ),
    (
        "bare __doc__ inside a class body",
        True,
        'class Inner:\n    __doc__ = "A cash-equity order intent."\n',
    ),
    (
        "class docstring wrapped at the 100-column limit",
        True,
        'class Wrapped:\n    """A simplified cash-equity settlement ticket built as a real order\n'
        '    intent against the session bar.\n    """\n',
    ),
    (
        "docstring separated by a double space",
        True,
        'class Spaced:\n    """A simplified cash-equity order  intent."""\n',
    ),
    (
        "PEP-258 attribute docstring at module level",
        True,
        'TICKET: Final[dict[str, str]] = {"side": "buy"}\n'
        '"""A single-security order intent, held as a plain mapping."""\n',
    ),
    (
        "PEP-258 attribute docstring on a class attribute",
        True,
        'class Holder:\n    """Holder."""\n\n    ticket = {"side": "buy"}\n'
        '    """A single-security order intent."""\n',
    ),
    (
        "PEP-258 attribute docstring inside a function body",
        True,
        'def build():\n    """Build."""\n\n    ticket = {"side": "buy"}\n'
        '    """A single-security order intent."""\n    return ticket\n',
    ),
    (
        "literal module docstring",
        False,
        '"""Those three are not every order intent in the repository."""\n\nX = 1\n',
    ),
    (
        "assigned module __doc__",
        False,
        '__doc__ = "Those three are not every order intent in the repository."\n\nX = 1\n',
    ),
)
"""Each docstring form the census is claimed to read or claimed to skip, and which it is.

`V2-P4-056`'s guard against its own repair. The forms the census covers can only be
demonstrated end-to-end by putting a declaration in `src/`, which no committed test may do, so
the coverage is pinned here against synthetic sources instead: a later simplification of
`_marker_in` back to a plain substring test, or a census that stops calling
`_attribute_docstrings`, goes red here rather than silently narrowing.

Every sample carries the two words `order intent`; the flag is whether it is a **declaration**
of one. The last two are the deliberate exclusion, and they use `candidate_ranking`'s own real
sentence -- the single false positive measured on the clean tree -- so the exclusion is pinned
by the case that motivated it.
"""


def test_the_census_reads_every_docstring_form_it_claims_and_skips_the_one_it_excludes() -> None:
    """The docstring-form axis, held to its enumeration instead of to its adjective.

    `V2-P4-048` called this axis closed and enumerated it "literal, f-string, assigned
    `__doc__`". Calling an axis closed is a claim about the enumeration, and that one was two
    forms short: `V2-P4-056` added a class docstring wrapped at this repository's own 100-column
    limit and a PEP-258 attribute docstring to `backtest/cross_section.py`, both carrying the
    literal ASCII words, and the census reported `MATCHES DECLARED TABLE: True` at 31 passed.

    So the enumeration is a table now, checked in both directions, because a matcher that reads
    everything is as useless as one that reads nothing: the two skipped forms are asserted
    **skipped**, and they are the module docstring, whose exclusion
    `_order_intent_declarations` measures rather than assumes.
    """
    for label, is_declaration, source in ORDER_INTENT_DOCSTRING_FORMS:
        tree = ast.parse(source)
        seen = {name for name, doc in _documented_declarations(tree) if _marker_in(doc)}

        assert bool(seen) is is_declaration, (
            f"{label}: the census {'missed' if is_declaration else 'read'} it, and the two words "
            f"{ORDER_INTENT_MARKER!r} are present either way. Every form here is either a "
            "declaration the census must read or a module docstring it must skip -- if this "
            "table is what changed, the measurement in _order_intent_declarations has to change "
            "with it"
        )


def test_the_disclosure_is_a_rationale_and_a_live_pointer_not_a_bare_name() -> None:
    """`V2-P4-048`. What `assert module in disclosure` above cannot tell on its own.

    A substring test is satisfied by the module name on a line by itself, so the check it
    performs is "somebody typed this string", not "somebody explained this". The three facts
    below are what make the disclosure *actionable*, and each is checked as a thing that exists
    rather than as prose:

    1. **Why it cannot be forbidden.** The disclosure must name `backtest/cross_section.py`, the
       module whose import of the fill policy is the reason -- and the reason is measured, not
       recited: adding `openalpha_cn.backtest.execution` to `forbidden_modules` yields
       7 kept / 1 broken, breaking `V2-P4-004`'s tradeability filter.
    2. **What guards the gap instead.** The disclosure must point at `ORDER_GUARD_POINTER` **by
       that path**, and the file must exist on disk. A disclosure whose named guard was deleted
       or renamed is worse than none: it tells a reader a risk is covered when it is not.
    3. **That the gap is stated at all.** The disclosure must say the residual is a single-
       security order intent, in the contract's own vocabulary.

    **`V2-P4-057`: point 2 did not bind its own pointer, and the docstring claimed it did.** The
    check collected *any* `tests/**/test_*.py` substring and required each to resolve. The block
    already carries three other pointers, so the guard's own was redundant to the assertion and
    the sentence about it was free to become false. Measured: one line of `pyproject.toml`
    edited so the sentence no longer names the guard, **and the guard file moved out of the tree
    entirely** -- `ls tests/unit/backtest/ | grep -c ranking_sources` at `0` -- left this test at
    31 passed and `lint-imports` at 8 kept / 0 broken. The old wording said it "is the assertion
    that would have caught `V2-P4-035`'s pin being replaced without the comment following"; it
    would not have, and that sentence is gone rather than reworded.

    The named pointer is asserted **and** the sweep over every other pointer is kept, because
    they fail differently: the sweep catches the three unrelated pointers going stale, and the
    named assertion catches the one the sentence promises. Neither implies the other -- that was
    the defect.
    """
    disclosure = _order_contract_block()

    assert "cross_section" in disclosure, (
        f"{ORDER_CONTRACT_ID}'s comment discloses openalpha_cn.backtest.execution as reachable "
        "but no longer says why it cannot be forbidden. The reason is backtest/cross_section.py, "
        "which imports the execution policy for V2-P4-004's tradeability filter -- measured at "
        "7 kept / 1 broken when the module is forbidden. Without that, the disclosure reads as "
        "an unexplained exemption"
    )
    assert "ExecutionRequest" in disclosure, (
        f"{ORDER_CONTRACT_ID}'s comment no longer names the order intent it exempts. The "
        "residual gap this contract discloses is that a source could build and fill an "
        "ExecutionRequest -- a single-security order intent -- and no contract would refuse it"
    )

    pointers = {
        match
        for match in re.findall(r"tests/[\w/\n#\s.]*?\.py", disclosure.replace("#", ""))
        if "test_" in match
    }
    # Joined before comparing: a pointer long enough to wrap carries the line break and the
    # continuation `#` inside the match, and `test_shortlist_gate.py`'s does today. Comparing
    # raw would make this assertion a check on where the comment happens to wrap.
    named = {"".join(pointer.split()) for pointer in pointers}
    assert named, (
        f"{ORDER_CONTRACT_ID}'s comment names no test file. Its own text says the residual gap "
        "is guarded by a file-scoped test rather than by the contract, so deleting the pointer "
        "leaves a reader no way to check whether anything guards it at all"
    )

    # Partitioned, so neither half is implied by the other. The guard's own pointer is asserted
    # named and asserted to exist; the sweep below covers every *other* pointer. Written as
    # `named - {guard}` rather than as a sweep over all of them because a sweep over all of them
    # makes the guard's existence check unfalsifiable -- which is the shape of the V2-P4-057
    # defect this test is being repaired for, and a mutation run caught it here too.
    assert ORDER_GUARD_POINTER in named, (
        f"{ORDER_CONTRACT_ID}'s comment no longer names {ORDER_GUARD_POINTER} as what guards the "
        f"residual gap; it points at {sorted(named)}. The three other pointers in the block "
        "are about the census and the probe that drives the contract, not about the fill -- so "
        "'some test file is named and every named file exists' stays true with the one guard "
        "that matters unnamed, which is V2-P4-057. Name the guard, or change this constant in "
        "the same commit as whatever replaced it"
    )
    assert (ROOT / ORDER_GUARD_POINTER).is_file(), (
        f"{ORDER_CONTRACT_ID}'s comment names {ORDER_GUARD_POINTER} as the behavioural guard on "
        "the one gap this contract cannot close, and there is no such file. This is the "
        "assertion the disclosure's own sentence is worth: the sentence and the file have to be "
        "deleted or renamed together"
    )

    others = sorted(named - {ORDER_GUARD_POINTER})
    missing = sorted(pointer for pointer in others if not (ROOT / pointer).is_file())
    assert not missing, (
        f"{ORDER_CONTRACT_ID}'s comment points at {missing}, which does not exist. A disclosure "
        "naming a guard that was deleted or renamed says a risk is covered when it is not, "
        "which is strictly worse than disclosing nothing"
    )


def test_the_order_contracts_name_claims_only_what_its_forbidden_modules_enforce() -> None:
    """The other half of `V2-P4-035`: the sentence a reader sees when the linter passes.

    `lint-imports` prints the contract's `name`, not its `id` or its `forbidden_modules`, so the
    name is the claim almost everybody reads. Its three forbidden modules are the three where a
    **portfolio** order is declared or simulated, which is exactly D16's `绝不直接创建组合订单`;
    the unqualified word "order" covers `ExecutionRequest` too and was therefore false.

    Checked as "every occurrence of `order` in the name is part of `portfolio order`" rather than
    against a fixed string, so the name may still be reworded -- it may not be rewidened. The
    first assertion stops the cheap way out of the second, which is to delete the word.
    """
    name = str(_order_contract()["name"]).lower()

    assert "portfolio order" in name, (
        f"{ORDER_CONTRACT_ID}'s name no longer says what it forbids: {name!r}. It must still "
        "claim the portfolio-order ban -- narrowing the claim to nothing is not narrowing it"
    )
    unqualified = re.sub(r"portfolio orders?", "", name)
    assert "order" not in unqualified, (
        f"{ORDER_CONTRACT_ID}'s name claims more than its forbidden_modules enforce: {name!r}. "
        "An unqualified 'order' covers openalpha_cn.backtest.execution's ExecutionRequest -- "
        "'a simplified cash-equity order intent' -- which this contract does not forbid and "
        "cannot forbid, because backtest/cross_section.py imports the execution policy for "
        "V2-P4-004's tradeability filter. Say 'portfolio order', which is what is enforced"
    )
