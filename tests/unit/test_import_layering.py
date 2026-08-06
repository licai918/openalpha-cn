"""Enforce ADR-0001's layering guardrail with import-linter, and pin the shrink-only baseline.

ADR-0001 (`docs/architecture/ADR-0001-local-first-runtime.md`) declares that domain and
provider contracts must not import SQLite or DuckDB implementation types. This module
proves the gate that enforces it is real (rejects a freshly introduced violation), proves
the pre-existing baseline of 7 measured violations
(`docs/architecture/import-layering-baseline.toml`) can only shrink, and proves the legal
downward dependency of `runtime`/`backtest` on `storage` is never mis-flagged.

It also proves the domain-purity rule independently of import-linter's static
`forbidden_modules` enumeration in `pyproject.toml`: siblings of `domain` are discovered
from the real directory structure at runtime, so a subpackage added after the enumeration
was written (e.g. a future `panel/`, `factors/`, or alpha-model package) is covered
automatically instead of silently falling outside the gate.
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
PINNED_BASELINE_COUNT = 7
ISSUE_PATTERN = re.compile(r"^V2-P0B-\d{3}$")


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
    """The real repository, with the baseline's 7 exemptions applied, is fully compliant."""
    exit_code = lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
    )
    assert exit_code == 0


def test_baseline_exemption_count_is_pinned_at_seven() -> None:
    """The baseline is shrink-only: exactly 7 entries today, never more."""
    violations = _load_baseline()
    assert len(violations) == PINNED_BASELINE_COUNT


def test_every_baseline_exemption_declares_the_issue_that_will_close_it() -> None:
    """Every registered exemption must name the P0.B issue that will remove it."""
    violations = _load_baseline()
    assert violations, "baseline must not be empty for this assertion to be meaningful"
    for violation in violations:
        issue = violation.get("issue", "")
        assert ISSUE_PATTERN.match(issue), (
            f"{violation.get('importer')} -> {violation.get('imported')} has no valid "
            f"issue annotation (got {issue!r})"
        )


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
    violation."""
    graph = grimp.build_graph("openalpha_cn")
    assert graph.direct_import_exists(
        importer="openalpha_cn.runtime.engine", imported="openalpha_cn.storage.recovery"
    )
    assert graph.direct_import_exists(
        importer="openalpha_cn.runtime.engine", imported="openalpha_cn.storage.sqlite"
    )
    assert graph.direct_import_exists(
        importer="openalpha_cn.backtest.multi_day", imported="openalpha_cn.storage.portfolio"
    )
    assert graph.direct_import_exists(
        importer="openalpha_cn.backtest.replay", imported="openalpha_cn.storage.sqlite"
    )

    exit_code = lint_imports(
        config_filename=str(ROOT / "pyproject.toml"),
        no_cache=True,
        limit_to_contracts=("storage-no-upward-deps",),
    )
    assert exit_code == 0
