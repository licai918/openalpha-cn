"""V2-P0B-012: proofs for the five contracts relocated out of storage's upward imports.

`storage/memory.py`, `storage/batch.py`, `storage/portfolio.py`, `storage/recovery.py`, and
`storage/product.py` each imported one data contract from `agents`/`runtime`/`product`/
`backtest` purely to serialize/deserialize it (see the former `ignore_imports` entries this
task removed from `pyproject.toml`, and the now-empty
`docs/architecture/import-layering-baseline.toml`). This module proves the relocation did
not merely hide the edge (the Task 19 mistake this task's brief explicitly warns against):

1. Every relocated contract is the *same class object* whether imported from its new home
   or its original module -- the original modules (`agents.base`, `runtime.memory`,
   `runtime.batch`, `backtest.portfolio`, `product.research`) re-export it, so every
   pre-existing caller of those modules keeps working unchanged.
2. Each new home carries no import edge back into `agents`, `product`, `backtest`, or
   `storage` -- moving the data shape somewhere storage can reach only helps if that new
   location does not itself reintroduce an upward or circular dependency.

Four of the five contracts (`AgentResult`, `MemoryEntry`, `PortfolioTransition`,
`WatchlistEntry`/`ResearchReport`) moved into `openalpha_cn.domain`: each is a plain data
value (no orchestration state, no behavior) whose own field types were already domain-pure
or trivially made so (`PortfolioTransition` needed `ExecutionResult` extracted alongside
it, from `backtest/execution.py` into `domain/execution.py`, since a transition embeds one).

The fifth, `BatchResearchTask`/`BatchProgressEvent` (plus `BatchTaskItem`/`BatchResultRef`,
which `BatchResearchTask` embeds), moved to a new top-level module,
`openalpha_cn.batch_contracts`, instead. It is durable *batch-orchestration* state (queued/
running/succeeded/failed/cancelled, max_concurrency, cancellation_requested) rather than a
research-domain concept, and -- decisively -- `BatchTaskItem.request` is typed
`runtime.contracts.ResearchRunRequest`, so it structurally cannot move into `domain`
without pulling `openalpha_cn.runtime` into `openalpha_cn.domain` too, which would violate
`domain-purity` for every other domain contract along with it. See
`openalpha_cn/batch_contracts.py`'s own module docstring for the same reasoning in place.
"""

from __future__ import annotations

import grimp

from openalpha_cn.agents.base import AgentResult as AgentResultViaAgents
from openalpha_cn.backtest.portfolio import PortfolioTransition as PortfolioTransitionViaBacktest
from openalpha_cn.batch_contracts import BatchProgressEvent, BatchResearchTask
from openalpha_cn.domain.agent_result import AgentResult
from openalpha_cn.domain.memory import MemoryEntry
from openalpha_cn.domain.portfolio import PortfolioTransition
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.domain.watchlist import WatchlistEntry
from openalpha_cn.product.research import ResearchReport as ResearchReportViaProduct
from openalpha_cn.product.research import WatchlistEntry as WatchlistEntryViaProduct
from openalpha_cn.runtime.batch import BatchProgressEvent as BatchProgressEventViaRuntime
from openalpha_cn.runtime.batch import BatchResearchTask as BatchResearchTaskViaRuntime
from openalpha_cn.runtime.memory import MemoryEntry as MemoryEntryViaRuntime

# Every module a relocated contract's new home is allowed to reach. `openalpha_cn.domain`
# submodules may only reach other `domain` submodules (`domain-purity`, enforced elsewhere).
# `openalpha_cn.batch_contracts` is the one deliberate exception: it embeds
# `runtime.contracts.ResearchRunRequest` (see module docstring above), so `openalpha_cn.
# runtime` is excluded from its forbidden set even though it is forbidden for the four
# `domain` homes.
_FORBIDDEN_FOR_DOMAIN_HOMES = (
    "openalpha_cn.agents",
    "openalpha_cn.runtime",
    "openalpha_cn.product",
    "openalpha_cn.backtest",
    "openalpha_cn.storage",
)
_FORBIDDEN_FOR_BATCH_CONTRACTS = (
    "openalpha_cn.agents",
    "openalpha_cn.product",
    "openalpha_cn.backtest",
    "openalpha_cn.storage",
)


def _touches_any(upstream: set[str], forbidden_prefixes: tuple[str, ...]) -> set[str]:
    return {
        module
        for module in upstream
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    }


def test_relocated_contracts_are_the_same_object_from_every_import_path() -> None:
    """Re-exports must be the identical class/object, not a lookalike duplicate -- a
    duplicate would silently break `isinstance` checks and pydantic validation for any
    caller still using the pre-relocation import path.
    """
    assert AgentResult is AgentResultViaAgents
    assert MemoryEntry is MemoryEntryViaRuntime
    assert PortfolioTransition is PortfolioTransitionViaBacktest
    assert WatchlistEntry is WatchlistEntryViaProduct
    assert ResearchReport is ResearchReportViaProduct
    assert BatchResearchTask is BatchResearchTaskViaRuntime
    assert BatchProgressEvent is BatchProgressEventViaRuntime


def test_domain_contract_homes_carry_no_edge_back_into_upper_layers_or_storage() -> None:
    """The four contracts moved into `domain/` must not, themselves, transitively reach
    back into `agents`/`runtime`/`product`/`backtest`/`storage` -- otherwise relocating them
    would just move the violation one file over instead of removing it.
    """
    graph = grimp.build_graph("openalpha_cn")
    for module in (
        "openalpha_cn.domain.agent_result",
        "openalpha_cn.domain.memory",
        "openalpha_cn.domain.execution",
        "openalpha_cn.domain.portfolio",
        "openalpha_cn.domain.watchlist",
        "openalpha_cn.domain.report",
    ):
        upstream = graph.find_upstream_modules(module)
        bad = _touches_any(upstream, _FORBIDDEN_FOR_DOMAIN_HOMES)
        assert not bad, f"{module} transitively depends on {sorted(bad)}"


def test_batch_contracts_module_does_not_depend_on_agents_product_backtest_or_storage() -> None:
    """`openalpha_cn.batch_contracts` legitimately depends on `runtime.contracts`
    (`BatchTaskItem.request: ResearchRunRequest`) -- that dependency is real and intentional,
    not the thing under test here. What must never happen is a dependency on `agents`,
    `product`, `backtest`, or (the one that would reintroduce a cycle) `storage` itself.
    """
    graph = grimp.build_graph("openalpha_cn")
    upstream = graph.find_upstream_modules("openalpha_cn.batch_contracts")
    bad = _touches_any(upstream, _FORBIDDEN_FOR_BATCH_CONTRACTS)
    assert not bad, f"openalpha_cn.batch_contracts transitively depends on {sorted(bad)}"
    assert "openalpha_cn.runtime.contracts" in upstream, (
        "openalpha_cn.batch_contracts should still depend on runtime.contracts for "
        "ResearchRunRequest -- if this ever goes false, BatchTaskItem's shape changed "
        "and this test's forbidden-set reasoning should be re-checked"
    )
