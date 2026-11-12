"""`V2-P7-013` R2: the two import-linter contracts the forward-registration path needs.

R1 moves the forward-registration protocol into `openalpha_cn.strategy_forward_session`, R3 gives
the registration step a `research_engine(...)` to assemble, and the daily agent step that will file
through both is the step `V2-P7-013` exists for. Before that step lands, this file binds the two
architecture sentences the audit's section 4 R2 states and the plan's section 1 measured on
`edaaa26`:

* **the forward-registration modules reach no face** -- neither `strategy_registration`, nor the
  `strategy_survivor*` family, nor the new `strategy_forward_session` may reach `agents`, `api`,
  `cli`, `product`, `sdk`, `shortlist_view`, `shortlist_compare`, `runtime.engine` or
  `runtime.composition`. Those modules take their stores injected; reaching a face is the one
  shape a registration path must not have. `runtime.composition` is forbidden by decision D3 in
  `.superpowers/sdd/plan.md` section 0 (registration modules do not open their own storage), and
  `runtime.engine` because a module that can reach the engine can run a cycle rather than file a
  record.
* **the agent layer reaches no paper book** -- `openalpha_cn.agents` may not reach the eight
  paper-book `backtest.*` modules (`paper`, `paper_evening`, `paper_reconciliation`,
  `paper_settlement`, `paper_state`, `paper_step`, `portfolio`, `portfolio_policy`), nor
  `storage.paper_state`, `storage.portfolio`, `domain.portfolio`, `domain.survivor_book`,
  `domain.survivor_orders`, `strategy_survivor_book` or `strategy_survivor_paper_report`. ADR-0005
  decision 2 is that the daily agent family "does not trade", and this is the family
  `V2-P7-013` registers; `strategy_forward_session` is deliberately **not** forbidden, because
  plan C's step files its predictions through it -- it declares a record, it does not trade.

**Why the source lists are pinned against the directory, in both directions.** A `forbidden`
contract enumerates its sources: a thirteenth `strategy_survivor*` module would arrive unguarded
by `lint-imports` alone, so the gate for the day it arrives is the equality assertion here, not
the linter's verdict. That is not a hypothesis -- the probe test
`test_a_thirteenth_strategy_module_would_arrive_unguarded_by_lint_imports_alone` measures it, the
same way `tests/unit/test_import_layering.py::test_lint_imports_alone_does_not_stop_a_new_backtest_
module_reaching_numpy_or_a_store` does for `backtest/`. The probe tests also prove the other
direction: each contract really rejects a planted violation, and the copy the violation is
planted in passes both before it is planted.

**How the configuration is read, and why not the way `test_import_layering.py` reads it.** That
file reads the same table with `importlinter.api.read_configuration`, which is the right reader --
but `IMPORT_LINTER_IMPORTS` next to it is an **equality** census of every `importlinter` import
under `tests/`, and a sixth row is a decision only an edit to that census can make.
`test_the_import_linter_cli_is_reached_from_one_file_in_this_whole_tree` is why a new file cannot
add one silently, and R2 is a new-file-only change (`pyproject.toml` plus this file), so this file
reads the same `[tool.importlinter]` contract tables with the standard library's `tomllib`
instead. For the fields asserted here -- `id`, `name`, `type`, `source_modules`,
`forbidden_modules` -- that is the same data `read_configuration` returns. The import-linter
*surfaces* this file does exercise are `scratch_package.copy_package`/`lint_copy`, which run the
real `lint-imports` console script over a copy in a child process (D13 I-D); no violation is
planted in this checkout, and the CLI is never imported or invoked from here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

from scratch_package import copy_package, lint_copy

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PACKAGE_ROOT: Final[Path] = ROOT / "src" / "openalpha_cn"
CONFIG_PATH: Final[Path] = ROOT / "pyproject.toml"

FORWARD_CONTRACT_ID: Final[str] = "forward-registration-reaches-no-face"
AGENT_CONTRACT_ID: Final[str] = "agent-layer-reaches-no-paper-book"
BACKTEST_CONTRACT_ID: Final[str] = "backtest-no-numeric-stack-or-panel-plane"

FORWARD_FORBIDDEN_FACES: Final[frozenset[str]] = frozenset(
    {
        "openalpha_cn.agents",
        "openalpha_cn.api",
        "openalpha_cn.cli",
        "openalpha_cn.product",
        "openalpha_cn.sdk",
        "openalpha_cn.shortlist_compare",
        "openalpha_cn.shortlist_view",
        "openalpha_cn.runtime.composition",
        "openalpha_cn.runtime.engine",
    }
)
"""Every face the forward registration path may not reach.

`shortlist_compare` is on the list with `shortlist_view` for the same reason: both are read
adapters over a stored candidate list, and a registration module that could import one could
publish or compare a list rather than file a prediction. `runtime.engine` and `runtime.composition`
are named individually rather than as `openalpha_cn.runtime`: `strategy_survivor_book` reaches
`storage` today (measured in plan section 1), and the side-effect-free `runtime` modules are not
what this sentence is about.
"""

AGENT_FORBIDDEN_PAPER_BOOK: Final[frozenset[str]] = frozenset(
    {
        "openalpha_cn.backtest.paper",
        "openalpha_cn.backtest.paper_evening",
        "openalpha_cn.backtest.paper_reconciliation",
        "openalpha_cn.backtest.paper_settlement",
        "openalpha_cn.backtest.paper_state",
        "openalpha_cn.backtest.paper_step",
        "openalpha_cn.backtest.portfolio",
        "openalpha_cn.backtest.portfolio_policy",
        "openalpha_cn.domain.portfolio",
        "openalpha_cn.domain.survivor_book",
        "openalpha_cn.domain.survivor_orders",
        "openalpha_cn.storage.paper_state",
        "openalpha_cn.storage.portfolio",
        "openalpha_cn.strategy_survivor_book",
        "openalpha_cn.strategy_survivor_paper_report",
    }
)
"""Every module where the paper book is declared, stored or iterated.

The eight `backtest.*` modules, not `openalpha_cn.backtest` whole (review 1, finding 1). A
package-wide ban would forbid the very seam this contract says it lets through:
`strategy_forward_session` reaches ten non-paper `backtest.*` modules through
`strategy_registration -> strategy_view` (measured with grimp on `39baeb5`), so an `agent_*`
module importing it would break the package-wide ban while the claim "the agent family files
through `strategy_forward_session`" is still the plan's reason that module is not on this list.
These eight are exactly where a paper book is declared, stored, stepped, settled, reported or
iterated -- none of them is reached by `strategy_forward_session` today, also measured. The two
`domain` modules are the data contracts behind the survivor book and its orders; the two
`storage` modules are where a paper portfolio is persisted; and the two `strategy_survivor*`
modules are the strategies that read or report a paper book. `strategy_forward_session` is absent
on purpose -- plan C's registration step files through it.
"""


def _config_contracts() -> list[dict[str, object]]:
    """The `[[tool.importlinter.contracts]]` tables of `pyproject.toml`, in file order.

    Standard-library `tomllib` rather than `importlinter.api.read_configuration`, and the module
    docstring carries the measured reason: that API is itself an `importlinter` import, and
    `tests/unit/test_import_layering.py::IMPORT_LINTER_IMPORTS` is an equality census over every
    such import under `tests/`. The values asserted here are plain strings and lists, identical
    under both readers.
    """
    data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    contracts = data["tool"]["importlinter"]["contracts"]
    assert isinstance(contracts, list)
    return contracts


def _contract(contract_id: str) -> dict[str, object]:
    contract = next(
        (options for options in _config_contracts() if options.get("id") == contract_id), None
    )
    assert contract is not None, (
        f"pyproject.toml declares no import-linter contract with id {contract_id!r}. R2 adds it "
        "after `ranking-creates-no-portfolio-order` (V2-P7-013); the two contracts are what binds "
        "the registration seam R1 creates"
    )
    return contract


def _source_modules(contract_id: str) -> set[str]:
    sources = _contract(contract_id).get("source_modules")
    assert isinstance(sources, list)
    return set(sources)


def _forbidden_modules(contract_id: str) -> set[str]:
    forbidden = _contract(contract_id).get("forbidden_modules")
    assert isinstance(forbidden, list)
    return set(forbidden)


def _strategy_registration_modules_on_disk(package_root: Path) -> set[str]:
    """The forward contract's source set, read off the directory.

    Exactly the set plan section 4 T2.1 defines, widened by plan C task 2.4's
    `strategy_agent_forward` (and task 2.7's `strategy_agent_evaluation`): the registration
    protocol, its two consumers, and every `strategy_survivor*.py` beside them.
    """
    names = {
        "strategy_registration",
        "strategy_forward_session",
        "strategy_agent_forward",
        "strategy_agent_evaluation",
    }
    names |= {path.stem for path in package_root.glob("strategy_survivor*.py")}
    return {f"openalpha_cn.{name}" for name in names}


def _strategy_star_modules_on_disk(package_root: Path) -> set[str]:
    """Every top-level `strategy_*` module, which is what the backtest contract must forbid."""
    return {f"openalpha_cn.{path.stem}" for path in package_root.glob("strategy_*.py")}


def _agent_modules_on_disk(package_root: Path) -> set[str]:
    """The `agent_*` reservation: top-level modules and packages, distinct from `agents`.

    Empty today; the reservation is the point. The agent contract's source list has to name the
    package `openalpha_cn.agents` today and every `agent_*` sibling the day one arrives, so the
    sources are checked as a superset of this set rather than only of `openalpha_cn.agents`.
    """
    modules = {f"openalpha_cn.{path.stem}" for path in package_root.glob("agent_*.py")}
    modules |= {f"openalpha_cn.{path.name}" for path in package_root.glob("agent_*/")}
    return modules


def _assert_the_forward_sources_match_the_directory(package_root: Path) -> None:
    on_disk = _strategy_registration_modules_on_disk(package_root)
    sources = _source_modules(FORWARD_CONTRACT_ID)
    assert sources == on_disk, (
        f"{FORWARD_CONTRACT_ID} and the files under {package_root} disagree: unlisted on disk = "
        f"{sorted(on_disk - sources)}, listed but absent = {sorted(sources - on_disk)}. The "
        "source list is an enumeration, so a new strategy_survivor* module is invisible to "
        "lint-imports until this list names it -- update the list in pyproject.toml and this test "
        "is what forces it"
    )


def test_the_forward_contract_covers_exactly_the_registration_modules_on_disk() -> None:
    """The contract exists, is `forbidden`, adds no exemption, and its sources are the directory.

    The equality is the gate for a thirteenth survivor module: `strategy_survivor*` in
    `src/openalpha_cn/` and the contract's explicit `source_modules` must agree in **both**
    directions, so a module that arrives unlisted is red here rather than silently unguarded.
    """
    contract = _contract(FORWARD_CONTRACT_ID)
    assert contract.get("type") == "forbidden"
    assert "ignore_imports" not in contract, (
        f"{FORWARD_CONTRACT_ID} must carry no ignore_imports: a violation of this sentence is a "
        "design defect to fix, not to exempt"
    )
    assert "allow_indirect_imports" not in contract, (
        f"{FORWARD_CONTRACT_ID} must run import-linter's default full transitive reachability; "
        "allow_indirect_imports would narrow it to direct edges and let a two-hop leak pass"
    )
    _assert_the_forward_sources_match_the_directory(PACKAGE_ROOT)


def test_the_forward_contract_forbids_every_face_the_registration_path_must_not_reach() -> None:
    """A registration module may not reach a face, nor the engine, nor the composition root."""
    forbidden = _forbidden_modules(FORWARD_CONTRACT_ID)
    missing = sorted(FORWARD_FORBIDDEN_FACES - forbidden)
    assert not missing, (
        f"{FORWARD_CONTRACT_ID} does not forbid {missing}. Each of those is where a registration "
        "module could turn a filed record into a published list, a served payload or a run -- the "
        "direction the contract exists to close; see this module's docstring for D3 and for why "
        "runtime.engine/composition are named individually"
    )


def test_the_forward_contract_name_does_not_promise_more_than_it_enforces() -> None:
    """The `name` says `shortlist view`, not `view`, and names the runtime composition.

    The contract forbids `shortlist_view`/`shortlist_compare` among the views -- it does **not**
    forbid `strategy_view`, `model_view` or `panel_view`, which eight of its thirteen sources
    reach (measured with grimp on `39baeb5`), nor the non-paper `backtest.*` modules all thirteen
    reach. A name saying "no view" would be the same overclaim `V2-P4-035` fixed for
    `ranking-creates-no-portfolio-order`; this pins the narrower wording review 1 asked for.
    """
    name = _contract(FORWARD_CONTRACT_ID).get("name")
    assert isinstance(name, str)
    lowered = name.lower()
    assert "face" in lowered, f"{FORWARD_CONTRACT_ID}'s name is {name!r}; it has to name the faces"
    assert "shortlist view" in lowered, (
        f"{FORWARD_CONTRACT_ID}'s name is {name!r}; the two views it forbids are the shortlist "
        "ones, so the name has to say so rather than claim every view"
    )
    assert "runtime composition" in lowered, (
        f"{FORWARD_CONTRACT_ID}'s name is {name!r}; decision D3's forbidden "
        "`openalpha_cn.runtime.composition` is part of what the contract enforces"
    )
    assert re.search(r"\bno view\b", name, flags=re.IGNORECASE) is None, (
        f"{FORWARD_CONTRACT_ID}'s name is {name!r} and claims 'no view'. Eight of the thirteen "
        "sources reach `strategy_view`, `model_view` and `panel_view`; only `shortlist_view` and "
        "`shortlist_compare` are forbidden"
    )


def test_the_backtest_contract_forbids_every_strategy_module_on_disk() -> None:
    """R2 also closes the four names the current list is missing, plus the new module.

    The whole-package `backtest` contract enumerates what a backtest study may not reach.
    `strategy_registration`, `strategy_survivor_continuation`, `strategy_survivor_paper_report` and
    `strategy_survivor_summary` were missing, and R1's `strategy_forward_session` joins them; this
    asserts the forbidden list is a superset of every `strategy_*` module on disk, so a thirteenth
    one cannot slip in as a reachable target either.
    """
    on_disk = _strategy_star_modules_on_disk(PACKAGE_ROOT)
    assert "openalpha_cn.strategy_forward_session" in on_disk, (
        "R1's strategy_forward_session is on disk by the time this gate runs; if it is not, the "
        "module was moved or renamed and the plan's D2 decision needs revisiting"
    )
    missing = sorted(on_disk - _forbidden_modules(BACKTEST_CONTRACT_ID))
    assert not missing, (
        f"{BACKTEST_CONTRACT_ID} does not forbid {missing}. A backtest study may reach no "
        "strategy_* module: the survivor machinery is the thing a study must be ignorant of to be "
        "a study rather than a replay of the strategy it is evaluating"
    )


def test_the_agent_contract_covers_the_agent_layer_and_reserves_the_agent_namespace() -> None:
    """The contract exists, is `forbidden`, adds no exemption, and its sources are a superset.

    A superset and not an equality, on purpose: `openalpha_cn.agents` must always be a source, and
    every `agent_*` top-level module or package on disk must join it the day it arrives. Nothing
    writes one today, so the second half is vacuous -- which is exactly why it is asserted now: the
    red run the day an `agent_*` module arrives unlisted is this assertion, not a reviewer's
    memory.
    """
    contract = _contract(AGENT_CONTRACT_ID)
    assert contract.get("type") == "forbidden"
    assert "ignore_imports" not in contract, (
        f'{AGENT_CONTRACT_ID} must carry no ignore_imports: "the agent family does not trade" '
        "(ADR-0005 decision 2) is a property to keep, not to exempt"
    )
    assert "allow_indirect_imports" not in contract, (
        f"{AGENT_CONTRACT_ID} must run import-linter's default full transitive reachability; a "
        "one-hop-from-the-paper-book agent would pass a direct-edges-only check"
    )
    required = {"openalpha_cn.agents"} | _agent_modules_on_disk(PACKAGE_ROOT)
    missing = sorted(required - _source_modules(AGENT_CONTRACT_ID))
    assert not missing, (
        f"{AGENT_CONTRACT_ID} has no source for {missing}. Every `agent_*` module this repository "
        "grows is part of the layer that must not trade, so it has to be named in source_modules "
        "in the same commit that creates it (this is the reservation the plan's T2.1-3 describes)"
    )


def test_the_agent_contract_forbids_the_paper_book_and_lets_the_forward_session_through(
    tmp_path: Path,
) -> None:
    """The forbidden set is the paper-book half, and the forward session really passes.

    The membership assertions are the first half. The second half is a live probe and the
    load-bearing one (review 1, finding 1): plan C's step files predictions **through**
    `strategy_forward_session`, so an `agent_*` module importing it has to stay KEPT. A
    membership check alone cannot see the difference -- `strategy_forward_session` reaches ten
    non-paper `backtest.*` modules, so a package-wide `openalpha_cn.backtest` ban would break
    this probe while every list assertion stayed green. The probe is planted in a copy, never in
    the checkout, and the same copy is asserted to pass before it is planted.
    """
    forbidden = _forbidden_modules(AGENT_CONTRACT_ID)
    missing = sorted(AGENT_FORBIDDEN_PAPER_BOOK - forbidden)
    assert not missing, (
        f"{AGENT_CONTRACT_ID} does not forbid {missing}. Each is a place the paper book is "
        "declared, stored or iterated; an agent layer that can reach one can trade"
    )
    assert "openalpha_cn.strategy_forward_session" not in forbidden, (
        f"{AGENT_CONTRACT_ID} forbids openalpha_cn.strategy_forward_session, but plan C's "
        "registration step files its predictions through that module. It declares a record; it "
        "does not trade, and the forward contract is what keeps it off the faces"
    )

    package = copy_package(tmp_path)
    kept = lint_copy(package, AGENT_CONTRACT_ID)
    assert kept.exit_code == 0, (
        f"the unmodified copy must pass {AGENT_CONTRACT_ID} before the forward probe can mean "
        f"anything: {kept.report}"
    )

    (package / "agents" / "_forward_probe.py").write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        "from openalpha_cn import strategy_forward_session\n\n"
        '__all__ = ["strategy_forward_session"]\n',
        encoding="utf-8",
    )
    allowed = lint_copy(package, AGENT_CONTRACT_ID)

    assert allowed.exit_code == 0, (
        f"an agent module importing openalpha_cn.strategy_forward_session must be allowed by "
        f"{AGENT_CONTRACT_ID} -- plan C's step files through it. Refused: {allowed.report}"
    )
    assert "openalpha_cn.agents._forward_probe -> openalpha_cn.strategy_forward_session" not in (
        allowed.report
    ), allowed.report


def test_the_agent_contract_name_does_not_promise_more_than_it_enforces() -> None:
    """The `name` says `paper book` and never the bare word `order`.

    That discipline is `V2-P4-035`'s, applied to this contract on arrival: a name that says "an
    order" is a claim wider than the list it labels. The order intents live in the two modules
    this contract does forbid, so the name does not need the word and must not imply a
    single-security order it does not check.
    """
    name = _contract(AGENT_CONTRACT_ID).get("name")
    assert isinstance(name, str)
    assert "paper book" in name.lower(), (
        f"{AGENT_CONTRACT_ID}'s name is {name!r}; it has to say what it forbids, which is the "
        "paper book"
    )
    assert re.search(r"\border\b", name, flags=re.IGNORECASE) is None, (
        f"{AGENT_CONTRACT_ID}'s name is {name!r} and uses the bare word 'order'. The contract "
        "forbids the modules that declare or persist portfolio and survivor orders, not every "
        "order intent in the tree (that is ranking-creates-no-portfolio-order's subject, and its "
        "name says 'portfolio order' for the same reason)"
    )


def test_the_agent_contract_rejects_a_probe_in_the_agents_package_reaching_the_paper_book(
    tmp_path: Path,
) -> None:
    """The unmodified copy passes; one `backtest.paper_step` import inside `agents/` turns it red.

    `agents` is a package source, so a new module under it is covered on arrival -- this is the
    probe shape that works for an enumerated *package* source. The refusal has to name the probe's
    own edge, and the same copy has to pass before the probe is planted, so the difference is the
    probe and not a contract that was already broken.
    """
    package = copy_package(tmp_path)

    kept = lint_copy(package, AGENT_CONTRACT_ID)
    assert kept.exit_code == 0, (
        f"the unmodified copy must pass {AGENT_CONTRACT_ID} before the probe can mean anything. "
        f"Measured on arrival in plan section 1, and kept on arrival here: {kept.report}"
    )

    (package / "agents" / "_probe.py").write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        "from openalpha_cn.backtest.paper_step import PaperStepPlan\n\n"
        '__all__ = ["PaperStepPlan"]\n',
        encoding="utf-8",
    )
    refused = lint_copy(package, AGENT_CONTRACT_ID)

    assert refused.exit_code == 1, (
        f"lint-imports should reject agents/_probe.py -> openalpha_cn.backtest.paper_step under "
        f"{AGENT_CONTRACT_ID}; if it does not, an agent can reach the paper book: {refused.report}"
    )
    assert "openalpha_cn.agents._probe" in refused.report, refused.report
    assert "openalpha_cn.backtest.paper_step" in refused.report, refused.report


def test_the_forward_contract_rejects_a_planted_edge_from_a_listed_registration_module(
    tmp_path: Path,
) -> None:
    """The unmodified copy passes; one `openalpha_cn.agents` import in a listed source turns it red.

    The contract enumerates its sources, so the probe is planted in a real one --
    `strategy_survivor.py` -- rather than in a new file (see the next test for the measurement of
    why a new file is outside what `lint-imports` can see). That is also the shape the contract
    actually guards: the module has to be one of the sources. Both halves are asserted on the same
    copy, and the refusal has to name the planted edge.
    """
    package = copy_package(tmp_path)

    kept = lint_copy(package, FORWARD_CONTRACT_ID)
    assert kept.exit_code == 0, (
        f"the unmodified copy must pass {FORWARD_CONTRACT_ID} before the probe can mean anything. "
        f"Measured on arrival in plan section 1, and kept on arrival here: {kept.report}"
    )

    module = package / "strategy_survivor.py"
    module.write_text(
        module.read_text(encoding="utf-8") + "\n\nimport openalpha_cn.agents\n",
        encoding="utf-8",
    )
    refused = lint_copy(package, FORWARD_CONTRACT_ID)

    assert refused.exit_code == 1, (
        f"lint-imports should reject strategy_survivor.py -> openalpha_cn.agents under "
        f"{FORWARD_CONTRACT_ID}; if it does not, a registration module can reach a face, and the "
        f"contract is prose: {refused.report}"
    )
    assert "openalpha_cn.strategy_survivor -> openalpha_cn.agents" in refused.report, refused.report


def test_a_thirteenth_strategy_module_would_arrive_unguarded_by_lint_imports_alone(
    tmp_path: Path,
) -> None:
    """Measured, not inferred: an unlisted new `strategy_survivor_probe.py` is not caught.

    This is the same distinction
    `tests/unit/test_import_layering.py::test_lint_imports_alone_does_not_stop_a_new_backtest_module
    _reaching_numpy_or_a_store` draws for `backtest/`, and the plan's T2.1-1 sentence "so a 13th
    survivor module cannot arrive unguarded" rests on it: a `forbidden` contract enumerates whole
    module names -- `strategy_survivor*` is not a syntax import-linter accepts -- so a module no
    contract names is invisible to `lint-imports` however forbidden its imports are. The gate for
    that day is `_assert_the_forward_sources_match_the_directory`, which reads the directory in
    both directions; this test drives both halves so the claim is a run rather than a comment.
    """

    package = copy_package(tmp_path)
    (package / "strategy_survivor_probe.py").write_text(
        '"""Temporary probe module for a layering test."""\n\n'
        "from openalpha_cn.agents.base import ResearchAgent\n\n"
        '__all__ = ["ResearchAgent"]\n',
        encoding="utf-8",
    )

    kept = lint_copy(package, FORWARD_CONTRACT_ID)

    assert kept.exit_code == 0, (
        "if this now fails, a contract has grown a way to cover a module it does not name, and "
        "the sentences pointing at this test are stale in the good direction: "
        f"{kept.report}"
    )
    try:
        _assert_the_forward_sources_match_the_directory(package)
    except AssertionError as error:
        assert "openalpha_cn.strategy_survivor_probe" in str(error), error
    else:  # pragma: no cover - the assertion above is the whole point of this test
        raise AssertionError(
            "a new strategy_survivor_probe.py is under the package the contract is about and the "
            "source-list check did not report it; the gate for a thirteenth strategy module has "
            "gone missing"
        )
