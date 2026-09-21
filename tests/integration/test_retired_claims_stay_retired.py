"""Claims the code refutes may not come back into the four user-facing documents.

`D13` found most of these in `README.md`, `README.en.md`, `docs/why-openalpha-cn.zh-CN.md`, the
marketing pack or the API diagrams' generator at `d4ef5e4`, checked each against the code, and
rewrote it. The final review had verified the first classes itself (its I4); the rest came from
its sub-audits and were checked against the code before anything was rewritten. The classes its
rebase round added came from the independent review of `D13` and the rebase brief, checked the
same way; five of the wordings that round retired were never in `d4ef5e4`, because `D13` had
written them itself, on its branch before the rebase (`16db458`). The review of that round
found one more clause of two of those classes in the marketing pack's §032; the risk gate's
pattern, extended to it, found a second clause in §041, and `D13`'s final round retired both. The
same round retired three classes its own report had named, in six clauses of the marketing pack:
a committee every result passes through, a committee outcome written to the DecisionLedger, and
a risk gate that constrains the portfolio. The final review of the whole `D13` batch found those
three reworded in five more sections that none of their patterns read; `D14` broadened them into
families of words that occur together, added the classes that review and `D14`'s own sweep of
the four documents and both generators named, and retired every wording that sweep found. The
review of `D14` found more wordings of those families, in the documents and in the generators, and
one class none of them held, a custom agent's research replayed; `D14`'s fix round added that
class and retired the wordings whose family a pattern can hold without catching a true sentence.
Nine of the wordings that review named are not pinned and no pattern stops them -- §011, §015,
§052's portfolio clause, §093's hook, brain:857's provenance line, §077's schema clause,
README:1106, api:464 and brain:360 -- because a pattern wide enough for them would catch
ordinary true sentences about trading rules and optional calls; all nine read correctly today,
and holding them is the census's job rather than a pattern's. The census of the four documents
and both generators at `29e26f3` named
eighty-eight passages no pattern read; four of its families are entries here, each added with a
premise and each red on the documents before they were rewritten -- a historical read that sees
only the version knowable at the time, a multi-day report that measures capacity and attributes
exposure, Tool/Risk/Validator as versioned extension contracts, and every agent signal citing
evidence -- and two older entries were extended by one branch each, for a batch item that keeps
a structured signal and a risk decision, and for models sharing one replay contract. An entry of
`RETIRED_CLAIMS` holds:

- `pattern`: the family of wordings that was retired, searched in every clause of the four
  documents as `tests/prose_clauses.py` reads them;
- `refuted_by`: the code fact that makes those wordings false, with file:line at the revision it
  was checked at: `d4ef5e4`, `c99b46b` for the classes the rebase round added, `07f5c80` for the
  three the final round added, `20fec55` for what `D14`'s first two commits added, or `43b40a7`
  for what its later commits, its fix round and the census round added -- between the two, only
  `cli.py` from :4914 on and a docstring in `backtest/replay.py` moved, and `src/` is unchanged
  from `43b40a7` through `1be62ce`, where the census round's four were read;
- `retired`: what it retired, verbatim -- the clause, or the part of it the claim sits in -- as it
  stood at `d4ef5e4`, on `16db458` for `D13`'s own five, or at `20fec55` for what `D14` retired,
  and at `29e26f3` for the four wordings its fix round retired that `D14` had written itself;
  every wording the census round retired stood at `20fec55` and at `29e26f3`, and all but two of
  them at `1be62ce` as well -- the two the fix round had already rewritten there;
  the pattern must still match each of them, so a pattern cannot be loosened into matching
  nothing;
- `premise`, where the fact is cheap to read off the code: a check that returns a message the day
  the code starts to support the claim, so a claim that has become true is reported as true
  instead of being blocked;
- `paraphrase`: the same claim in words the pattern does not match.

**What it cannot see.** It catches these wordings and nothing else. A pattern is a family of the
phrasings that were retired, not a detector of the claim: the same claim in other words --
another verb, another order, or its halves in two clauses -- passes, and
`test_the_retired_claims_blind_spot_is_real` holds one such paraphrase per entry. The review of
`D14` wrote 36 natural rewrites of these claims and 30 of them passed, and the census of the four
documents and both generators at `29e26f3`, where every clause and every string literal passed
every pattern, named eighty-eight more false or overstated passages -- sentences, command lines
and drawings. A reworded claim is
therefore left to review and to the census of the documents, not to a pattern: a pattern is not
widened to catch a paraphrase whose words a true sentence shares, and when a pattern catches a
true sentence the pattern is narrowed. Most spans of the entries `D14` added or broadened stop
at 不, 未, 没 or 无 (`_NO_DENIAL`, `_NO_COMMA_OR_DENIAL`), the census round's four families
read the whole clause for one (`_NO_EARLIER_DENIAL`, `_NO_DENIAL_IN_CLAUSE`), and some
patterns look behind for a denial just before their first word; `D13`'s older entries do so
only where a true sentence was caught. Elsewhere a denial reads as the claim it denies
("不能分别启停"), and a claim whose span holds 不可变 or 无 in another sense is missed by the
entries that stop at a denial. A premise reads one fact, not the whole of `refuted_by`, so a
premise that stays quiet does not prove the claim still false. The ledger's premise sees a
call, a bound alias and a `getattr` with the literal name of `append_decision`, never a name
built at run time; the portfolio premise reads
the functions that drive the simulator, not a helper one of them calls. The diagrams' two
generators are read too, one string literal at a time (`tests/diagram_text.py`'s
`diagram_strings`): a claim split across two literals, computed at run time, or drawn as a line
between two boxes, is not read.

**The other direction.** A pattern can also catch a true sentence that shares its words: "REST
clients" held "cli" until SDK and CLI were matched as words, and 移动平均 held 移动. There is no
allowlist, so such a pattern is narrowed, never pinned, and its `retired` wordings must still
match; `test_the_retired_patterns_pass_the_true_sentences_that_share_their_words` holds the
true sentences the reviews of `D13` and `D14` and the census measured being caught, and a probe
for each narrowing since.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, get_args

import pytest
from diagram_text import diagram_strings
from prose_clauses import clauses

from openalpha_cn.agents.committee import DeliberationCommittee, RiskVote
from openalpha_cn.backtest.multi_day import PortfolioBacktestReport, SubjectAttribution
from openalpha_cn.backtest.replay import ReplayRunner
from openalpha_cn.batch_contracts import BatchResultRef
from openalpha_cn.decisions.risk import RiskGate
from openalpha_cn.domain.portfolio import PortfolioOrder, PortfolioTransition
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.domain.signal import SignalFrame
from openalpha_cn.model_view import KNOWN_MODEL_VIEW_LIMITATIONS
from openalpha_cn.runtime.batch import BatchResearchService
from openalpha_cn.runtime.engine import ResearchEngine
from openalpha_cn.sdk import OpenAlphaSDK

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SRC: Final[Path] = ROOT / "src" / "openalpha_cn"

README: Final[Path] = ROOT / "README.md"
README_EN: Final[Path] = ROOT / "README.en.md"
WHY_OPENALPHA: Final[Path] = ROOT / "docs" / "why-openalpha-cn.zh-CN.md"
MARKETING: Final[Path] = ROOT / "docs" / "marketing" / "openalpha-cn-100-promotion-plans.zh-CN.md"

GUARDED_FILES: Final[tuple[Path, ...]] = (README, README_EN, WHY_OPENALPHA, MARKETING)

_NOT_END: Final[str] = r"[^。;\N{FULLWIDTH SEMICOLON}]"
"""One character that ends no sentence: what a pattern may span between its words."""

_FACE: Final[str] = r"(?<![A-Za-z])(?:SDK|CLI)(?![A-Za-z])"
"""SDK or CLI as a word of its own, so client, clients and Click are not read as CLI."""

_NO_COMMA: Final[str] = r"[^。;\N{FULLWIDTH SEMICOLON}，,]"
"""One character that ends no sentence and no phrase: what a pattern may span inside one phrase."""

_NO_DENIAL: Final[str] = r"[^。;\N{FULLWIDTH SEMICOLON}不未没无]"
"""One character that ends no sentence and denies nothing: a span stops at 不, 未, 没 or 无."""

_NO_COMMA_OR_DENIAL: Final[str] = r"[^。;\N{FULLWIDTH SEMICOLON}，,不未没无]"
"""The same inside one phrase: a span that stops at a comma too."""

_NO_EARLIER_DENIAL: Final[str] = r"\A[^不未没无]*?"
"""Nothing denied before the claim: a prefix that reaches the claim only across no 不, 未, 没, 无.

A span class stops a pattern *inside* its own words. It cannot see 「PIT 查询**不会**只返回当时可
知的版本」, where the denial sits before the first word a pattern reads. Anchoring at the clause's
start and crossing no denial to get there is what sees it, and it is the weakest guard that does:
a family whose own retired wordings open with a denial (「不受后来修订干扰」) still matches, because
the prefix may cross nothing at all.
"""

_NO_DENIAL_IN_CLAUSE: Final[str] = r"\A(?![\s\S]*[不未没无])[\s\S]*?"
"""Nothing denied anywhere in the clause: for families whose retired wordings hold no denial.

「换手、容量这两个词在报告里都**找不到**。」 denies the claim after the words a pattern reads, so no
prefix can see it. These three families -- the multi-day report, the versioned extension
contracts, and every signal citing evidence -- retired twenty wordings between them and not one
holds 不, 未, 没 or 无, so a clause that holds one is not a wording of theirs.
"""


# --- Reading the code facts -------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    """Every absolute module `path` imports, and every `module.name` it imports from one."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _imports_under(path: Path, prefixes: tuple[str, ...]) -> list[str]:
    """What `path` imports from any of `prefixes`, a package and everything under it."""
    return sorted(
        module
        for module in _imported_modules(path)
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)
    )


def _class_field_names(path: Path, name: str) -> list[str]:
    """The annotated fields of class `name` in `path`, read from its syntax tree, not imported."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return [
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            ]
    raise AssertionError(f"{name} is not defined in {path.relative_to(ROOT)}")


def _the_committee_still_takes_no_switch() -> str | None:
    review = [*inspect.signature(DeliberationCommittee.review).parameters]
    deliberate = [*inspect.signature(OpenAlphaSDK.deliberate).parameters]
    route = _class_field_names(SRC / "api" / "app.py", "DeliberationApiRequest")
    if (review, deliberate, route) == (
        ["self", "signal", "results"],
        ["self", "signal", "agent_results"],
        ["signal", "agent_results"],
    ):
        return None
    return (
        f"DeliberationCommittee.review{review}, OpenAlphaSDK.deliberate{deliberate} or the "
        f"deliberate route's body {route} now takes more than a signal and agent results"
    )


HTTP_OR_THE_REST_APP: Final[tuple[str, ...]] = (
    "fastapi",
    "starlette",
    "httpx",
    "requests",
    "urllib.request",
    "http.client",
    "openalpha_cn.api",
)


def _the_sdk_and_the_cli_still_call_in_process() -> str | None:
    reached = {
        name: _imports_under(SRC / name, HTTP_OR_THE_REST_APP) for name in ("sdk.py", "cli.py")
    }
    if not any(reached.values()):
        return None
    return f"the SDK or the CLI now imports an HTTP client or the REST app: {reached}"


PORTFOLIO_EXECUTION: Final[tuple[str, ...]] = (
    "openalpha_cn.backtest.execution",
    "openalpha_cn.backtest.portfolio",
    "openalpha_cn.backtest.multi_day",
    "openalpha_cn.domain.portfolio",
)


def _replay_still_executes_no_order() -> str | None:
    reached = _imports_under(SRC / "backtest" / "replay.py", PORTFOLIO_EXECUTION)
    return None if not reached else f"backtest/replay.py now imports {reached}"


def _a_rejection_still_names_no_decision() -> str | None:
    fields = sorted({*PortfolioOrder.model_fields, *PortfolioTransition.model_fields})
    linked = [field for field in fields if "decision" in field]
    return None if not linked else f"a portfolio order or transition now carries {linked}"


REPORT_FIELDS_AT_D13: Final[frozenset[str]] = frozenset(
    {
        "run_id",
        "subject",
        "created_at",
        "title",
        "summary",
        "decision_id",
        "signal_id",
        "final_action",
        "evidence_ids",
        "risk_flags",
    }
)


def _a_report_still_holds_one_research_run() -> str | None:
    added = sorted(set(ResearchReport.model_fields) - REPORT_FIELDS_AT_D13)
    return None if not added else f"ResearchReport now carries {added}: re-read what it holds"


def _playwright_still_drives_desktop_only() -> str | None:
    config = (ROOT / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    devices = set(re.findall(r'devices\["([^"]+)"\]', config))
    if devices == {"Desktop Chrome"}:
        return None
    return f"web/playwright.config.ts now drives {sorted(devices)}"


ROUTES_THE_WORKBENCH_DOES_NOT_CALL: Final[tuple[str, ...]] = (
    "research/batches",
    "/screen",
    "/watchlist",
    "/reports",
)


def _the_workbench_still_skips_the_product_routes() -> str | None:
    called = sorted(
        {
            route
            for path in (ROOT / "web" / "src").rglob("*.ts*")
            for route in ROUTES_THE_WORKBENCH_DOES_NOT_CALL
            if route in path.read_text(encoding="utf-8")
        }
    )
    return None if not called else f"the React workbench now names {called}"


CHECK_COMPOSE_VERBS: Final[tuple[str, ...]] = ("up", "restart")
"""The compose verbs `verify_compose_recovery.py::main` runs before its check today: the stack
brought up once, then restarted."""


def _compose_calls_before_teardown(source: str) -> list[tuple[str, ...]]:
    """The string arguments of each `_compose` call `main()` makes outside a `finally`, in source
    order. Its first three arguments -- the compose command, the project and the environment --
    are names, not strings, and are left out."""
    tree = ast.parse(source, filename="scripts/verify_compose_recovery.py")
    main = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    teardown = {
        id(inner)
        for node in ast.walk(main)
        if isinstance(node, ast.Try)
        for statement in node.finalbody
        for inner in ast.walk(statement)
    }
    calls = sorted(
        (
            node
            for node in ast.walk(main)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_compose"
            and id(node) not in teardown
        ),
        key=lambda call: (call.lineno, call.col_offset),
    )
    return [
        tuple(
            argument.value
            for argument in call.args[3:]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        )
        for call in calls
    ]


def _compose_recovery_still_only_restarts() -> str | None:
    """Outside its `finally`, `main()` brings the stack up once and restarts it, and runs no other
    compose verb. Read from the syntax tree: the `compose down` in `finally` is the teardown after
    the check, so the words down or up anywhere in the file say nothing about the check."""
    source = (ROOT / "scripts" / "verify_compose_recovery.py").read_text(encoding="utf-8")
    calls = _compose_calls_before_teardown(source)
    verbs = tuple(call[0] for call in calls if call)
    if verbs == CHECK_COMPOSE_VERBS:
        return None
    return (
        f"scripts/verify_compose_recovery.py's main() now runs compose {calls} before its check: "
        "re-read whether it deletes and recreates the container"
    )


def _the_batch_service_still_ships() -> str | None:
    missing = [
        name for name in ("submit", "run", "cancel") if not hasattr(BatchResearchService, name)
    ]
    return None if not missing else f"BatchResearchService no longer has {missing}"


def _the_upstream_counts_are_still_unsourced() -> str | None:
    audit = ROOT / "docs" / "audits" / "three-upstream-source-audit-20260724.md"
    if "257" not in audit.read_text(encoding="utf-8"):
        return None
    return f"{audit.relative_to(ROOT)} now states 257: the count may have a source"


def _the_sdk_still_takes_no_provider() -> str | None:
    parameters = [*inspect.signature(OpenAlphaSDK.__init__).parameters]
    taken = [name for name in parameters if "provider" in name or "model" in name]
    return None if not taken else f"OpenAlphaSDK now takes {taken}"


def _the_cli_still_reaches_tushare_over_http() -> str | None:
    imported = _imports_under(SRC / "cli.py", ("openalpha_cn.providers.tushare",))
    if "openalpha_cn.providers.tushare.UrllibTushareTransport" in imported:
        return None
    return (
        "cli.py no longer imports UrllibTushareTransport: re-read whether any CLI command still "
        "makes an HTTP request"
    )


def _the_engine_still_takes_its_agents() -> str | None:
    if "agents" in inspect.signature(ResearchEngine.__init__).parameters:
        return None
    return "ResearchEngine no longer takes agents: a model may now reach a run only through the SDK"


def _the_http_contract_still_names_few_boundaries() -> str | None:
    http = (ROOT / "docs" / "api" / "http.md").read_text(encoding="utf-8")
    if any(item.code not in http for item in KNOWN_MODEL_VIEW_LIMITATIONS):
        return None
    return (
        "docs/api/http.md now names every KNOWN_MODEL_VIEW_LIMITATIONS code: the pointer to it "
        "may hold"
    )


def _the_portfolio_backtest_still_skips_run_cycle() -> str | None:
    multi_day = SRC / "backtest" / "multi_day.py"
    reached = _imports_under(multi_day, ("openalpha_cn.runtime",))
    if "run_cycle" not in multi_day.read_text(encoding="utf-8") and not reached:
        return None
    return f"backtest/multi_day.py now reaches run_cycle ({reached}): re-read what it validates"


def _a_batch_result_still_names_only_a_decision() -> str | None:
    fields = sorted(BatchResultRef.model_fields)
    if fields == ["decision_id", "final_action", "signal_id"]:
        return None
    return f"BatchResultRef now carries {fields}: re-read what a batch item runs"


def _a_portfolio_transition_still_names_no_batch() -> str | None:
    fields = sorted({*PortfolioOrder.model_fields, *PortfolioTransition.model_fields})
    linked = [field for field in fields if "batch" in field or "task" in field]
    return None if not linked else f"a portfolio order or transition now carries {linked}"


def _vote_expressions() -> dict[str, tuple[str, str]]:
    """Each RiskVote the committee casts, by perspective: its decision and its reasons, as
    expressions read from agents/committee.py's syntax tree."""
    source = (SRC / "agents" / "committee.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="agents/committee.py")
    found: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RiskVote"
        ):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        perspective = keywords.get("perspective")
        if isinstance(perspective, ast.Constant) and isinstance(perspective.value, str):
            decision, reasons = (
                ast.dump(keywords[name]) if name in keywords else ""
                for name in ("decision", "reasons")
            )
            found[perspective.value] = (decision, reasons)
    return found


def _the_neutral_and_conservative_votes_still_agree() -> str | None:
    votes = _vote_expressions()
    if "neutral" in votes and votes["neutral"] == votes.get("conservative"):
        return None
    return (
        "the neutral and conservative votes are no longer one expression: re-read what each "
        "one weighs"
    )


def _the_engine_still_calls_no_committee() -> str | None:
    reached = _imports_under(SRC / "runtime" / "engine.py", ("openalpha_cn.agents.committee",))
    return None if not reached else f"runtime/engine.py now imports {reached}: re-read the order"


def _append_decision_readers(tree: ast.AST) -> bool:
    """Whether `tree` reads `append_decision`: a call, a bound alias, or `getattr` with the literal.

    Any load of the attribute counts, so `write = store.append_decision` then `write(entry)` is
    seen at the binding. `getattr(store, "append_decision")` is seen because the name is a
    literal; a name built at run time (`getattr(store, "append_" + kind)`) is not.
    """
    return any(
        (
            isinstance(node, ast.Attribute)
            and node.attr == "append_decision"
            and isinstance(node.ctx, ast.Load)
        )
        or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "append_decision"
        )
        for node in ast.walk(tree)
    )


def _only_the_engine_appends_a_decision() -> str | None:
    callers: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "append_decision" in text and _append_decision_readers(
            ast.parse(text, filename=str(path))
        ):
            callers.append(path.relative_to(SRC).as_posix())
    if callers == ["runtime/engine.py"]:
        return None
    return f"append_decision is now read in {callers}: re-read who writes a DecisionLedger"


PORTFOLIO_MODULES: Final[tuple[str, ...]] = ("backtest/portfolio.py", "backtest/execution.py")
"""The simulator and the execution policy it applies, read whole."""

GATE_NAMES: Final[frozenset[str]] = frozenset({"RiskGate", "risk_decision", "final_action"})
"""The risk gate, and the two fields of a run its verdict reaches."""


PORTFOLIO_MODULE: Final[str] = "openalpha_cn.backtest.portfolio"
"""The module whose simulator applies the A-share rules to an order."""


def _portfolio_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """What a module binds from the portfolio module: the names it imports from it, and the
    names it gives the module itself."""
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == PORTFOLIO_MODULE:
                names.update(alias.asname or alias.name for alias in node.names)
            elif node.module == "openalpha_cn.backtest":
                for alias in node.names:
                    if alias.name == "portfolio":
                        modules.add(alias.asname or alias.name)
                    elif alias.name == "PortfolioSimulator":
                        names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname
                for alias in node.names
                if alias.name == PORTFOLIO_MODULE and alias.asname
            )
    return names, modules


def _drives_the_portfolio(node: ast.AST, names: set[str], modules: set[str]) -> bool:
    """Whether `node` names the simulator, a name bound from its module, or that module."""
    if isinstance(node, ast.Name):
        return node.id in names or node.id == "PortfolioSimulator"
    if isinstance(node, ast.Attribute):
        return node.attr == "PortfolioSimulator" or (
            isinstance(node.value, ast.Name) and node.value.id in modules
        )
    return False


def _own_nodes(function: ast.AST) -> Iterator[ast.AST]:
    """The nodes of `function`, outside any function, lambda or class nested in it."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _portfolio_scopes_of(where: str, tree: ast.AST) -> dict[str, ast.AST]:
    """The scopes of one module the portfolio premise reads; `_portfolio_scopes` has which."""
    names, modules = _portfolio_bindings(tree)
    if where in PORTFOLIO_MODULES or (
        where.startswith("backtest/")
        and (
            names
            or modules
            or any(_drives_the_portfolio(node, names, modules) for node in ast.walk(tree))
        )
    ):
        return {where: tree}
    return {
        f"{where}::{node.name}": node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(_drives_the_portfolio(inner, names, modules) for inner in _own_nodes(node))
    }


def _portfolio_scopes() -> dict[str, ast.AST]:
    """Every place that drives the portfolio simulator, keyed by where it is.

    `PORTFOLIO_MODULES` whole, and whole any other module under `backtest/` whose syntax tree
    imports the portfolio module or a name from it, or names `PortfolioSimulator` as a name or an
    attribute: `multi_day.py`, `paper.py`, `portfolio_policy.py` and the package's `__init__.py`.
    In a module outside `backtest/` -- `sdk.py`, `api/app.py`, `cli.py` -- the innermost functions
    that name one of those themselves, because those modules also serve routes and commands that
    read a run's `final_action` on purpose, and `api/app.py` nests every route in `create_app`. A
    helper such a function calls is not read.
    """
    return {
        key: scope
        for path in sorted(SRC.rglob("*.py"))
        for key, scope in _portfolio_scopes_of(
            path.relative_to(SRC).as_posix(),
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        ).items()
    }


def _no_portfolio_module_reads_the_risk_gate() -> str | None:
    reading = sorted(
        where
        for where, scope in _portfolio_scopes().items()
        if any(
            (isinstance(node, ast.Name) and node.id in GATE_NAMES)
            or (isinstance(node, ast.Attribute) and node.attr in GATE_NAMES)
            for node in ast.walk(scope)
        )
    )
    return None if not reading else f"{reading} now read the risk gate's decision: re-read them"


def _the_gate_still_reads_only_the_flags() -> str | None:
    parameters = list(inspect.signature(RiskGate.evaluate).parameters)
    reasons = [
        field
        for field in _class_field_names(SRC / "domain" / "decision.py", "DecisionLedger")
        if "reason" in field
    ]
    if parameters == ["self", "signal"] and not reasons:
        return None
    return f"RiskGate.evaluate takes {parameters} and DecisionLedger holds {reasons}: re-read them"


def _chainlin_is_still_built_only_for_doctor() -> str | None:
    builders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "ChainLinDataProvider" in text and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            == "ChainLinDataProvider"
            for node in ast.walk(ast.parse(text, filename=str(path)))
        ):
            builders.append(path.relative_to(SRC).as_posix())
    return None if builders == ["cli.py"] else f"ChainLinDataProvider is now built in {builders}"


def _the_manifest_still_records_no_prompt() -> str | None:
    recorded: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "RunManifest(" not in text:
            continue
        for node in ast.walk(ast.parse(text, filename=str(path))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "RunManifest"
            ):
                continue
            recorded.extend(
                f"{path.relative_to(SRC).as_posix()}:{node.lineno}"
                for keyword in node.keywords
                if keyword.arg == "prompt_versions"
                and not (isinstance(keyword.value, ast.Tuple) and not keyword.value.elts)
            )
    return None if not recorded else f"a RunManifest now records prompt versions at {recorded}"


def _validation_still_applies_no_trading_rule() -> str | None:
    reached = _imports_under(SRC / "backtest" / "validation.py", PORTFOLIO_EXECUTION)
    return None if not reached else f"backtest/validation.py now imports {reached}"


def _a_rejected_order_still_makes_a_transition() -> str | None:
    statuses = get_args(PortfolioTransition.model_fields["status"].annotation)
    return None if "rejected" in statuses else f"a PortfolioTransition's status is now {statuses}"


def _the_prediction_listing_still_carries_no_limitations() -> str | None:
    tree = ast.parse((SRC / "model_view.py").read_text(encoding="utf-8"))
    listing = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"prediction_index_view", "_prediction_index_entry"}
    }
    carrying = sorted(
        name
        for name, node in listing.items()
        if any(
            isinstance(inner, ast.Constant) and inner.value == "limitations"
            for inner in ast.walk(node)
        )
    )
    if len(listing) == 2 and not carrying:
        return None
    return f"the prediction listing is {sorted(listing)} and {carrying} carry limitations now"


FEATURE_LEDGER: Final[Path] = ROOT / "artifacts" / "openalpha-v1-feature-coverage" / "features.csv"
"""The feature ledger, whose header says what evidence each feature carries."""


def _the_ledger_still_has_no_documentation_column() -> str | None:
    header = FEATURE_LEDGER.read_text(encoding="utf-8").splitlines()[0]
    documented = [column for column in header.split(",") if "doc" in column.lower()]
    return None if not documented else f"features.csv now has the columns {documented}"


def _a_missing_chainlin_key_is_still_authentication() -> str | None:
    tree = ast.parse((SRC / "providers" / "chainlin.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and any(
                keyword.arg == "category"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "authentication"
                for keyword in node.keywords
            )
            and any(
                keyword.arg == "message" and "missing" in ast.unparse(keyword.value)
                for keyword in node.keywords
            )
        ):
            return None
    return "a missing ChainLin key no longer raises authentication: re-read doctor --probe"


def _callee(node: ast.Call) -> str:
    """The name a call calls: `f` for `f(...)`, `m` for `x.m(...)`, and "" for anything else."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


RUNNING_CALLS: Final[frozenset[str]] = frozenset(
    {"run", "run_cycle", "runner", "retry", "submit", "execute", "start"}
)
"""What a restart would call to run the work it requeues."""


def _a_restart_still_only_requeues() -> str | None:
    tree = ast.parse((SRC / "storage" / "batch.py").read_text(encoding="utf-8"))
    recover = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "recover_interrupted"
        ),
        None,
    )
    if recover is None:
        return "storage/batch.py no longer defines recover_interrupted: re-read the restart path"
    requeues = any(
        isinstance(node, ast.Constant) and node.value == "queued" for node in ast.walk(recover)
    )
    runs = sorted(
        {
            _callee(node)
            for node in ast.walk(recover)
            if isinstance(node, ast.Call) and _callee(node) in RUNNING_CALLS
        }
    )
    if requeues and not runs:
        return None
    return f"recover_interrupted requeues: {requeues}, and now calls {runs}"


PRODUCT_COMMANDS: Final[frozenset[str]] = frozenset({"screen", "watchlist", "batch", "batches"})
"""CLI command or group names that would reach the screening, watchlist or batch services."""


def _cli_command_names() -> set[str]:
    """What cli.py registers: each add_typer name, each command name, and the function name of a
    bare `@app.command()` with its underscores as hyphens, the name Typer gives it."""
    tree = ast.parse((SRC / "cli.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) == "add_typer":
            names.update(
                str(keyword.value.value)
                for keyword in node.keywords
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
            )
        elif isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and _callee(decorator) == "command":
                    first = decorator.args[0] if decorator.args else None
                    names.add(
                        str(first.value)
                        if isinstance(first, ast.Constant)
                        else node.name.replace("_", "-")
                    )
    return names


def _the_cli_still_has_no_product_command() -> str | None:
    reached = sorted(_cli_command_names() & PRODUCT_COMMANDS)
    return None if not reached else f"the CLI now has the commands {reached}"


def _usage_is_still_recorded_once_per_call() -> str | None:
    tree = ast.parse((SRC / "models" / "openai_compatible.py").read_text(encoding="utf-8"))
    records = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) == "_record_usage"
    ]
    looped = [
        loop.lineno
        for loop in ast.walk(tree)
        if isinstance(loop, ast.While | ast.For)
        and any(inner in records for inner in ast.walk(loop))
    ]
    if len(records) == 1 and not looped:
        return None
    return f"_record_usage is now called {len(records)} times, inside the loops at {looped}"


LINK_WORDS: Final[tuple[str, ...]] = ("evidence", "report", "run", "decision", "signal", "screen")
"""Words a field that linked a watchlist entry to the research record would hold."""


def _a_watchlist_entry_still_links_nothing() -> str | None:
    fields = _class_field_names(SRC / "domain" / "watchlist.py", "WatchlistEntry")
    linked = [field for field in fields if any(word in field for word in LINK_WORDS)]
    return None if not linked else f"WatchlistEntry now holds {linked}"


def _class_field_annotation(path: Path, name: str, field: str) -> str:
    """The annotation of `field` on class `name` in `path`, as source text."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            for statement in node.body:
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == field
                ):
                    return ast.unparse(statement.annotation)
    raise AssertionError(f"{name}.{field} is not defined in {path.relative_to(ROOT)}")


def _the_routing_path_still_holds_only_ids() -> str | None:
    annotation = _class_field_annotation(
        SRC / "domain" / "decision.py", "DecisionLedger", "routing_path"
    )
    return None if annotation == "tuple[str, ...]" else f"routing_path is now {annotation}"


def _a_risk_vote_still_has_no_abstention() -> str | None:
    perspectives = get_args(RiskVote.model_fields["perspective"].annotation)
    decisions = get_args(RiskVote.model_fields["decision"].annotation)
    if perspectives == ("aggressive", "neutral", "conservative") and decisions == (
        "pass",
        "reduce",
        "block",
    ):
        return None
    return f"a RiskVote is now {perspectives} by {decisions}: re-read the risk panel"


def _nothing_is_called_portfolio_compose() -> str | None:
    routes = [
        node.value
        for node in ast.walk(ast.parse((SRC / "api" / "app.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/api/")
        and "compose" in node.value
    ]
    found = sorted(
        {
            *(name for name in _cli_command_names() if "compose" in name),
            *routes,
            *(name for name in dir(OpenAlphaSDK) if "compose" in name),
        }
    )
    return None if not found else f"something is called compose now: {found}"


def _replay_still_runs_only_the_built_in_agents() -> str | None:
    built = [*inspect.signature(ReplayRunner.__init__).parameters]
    run = [*inspect.signature(ReplayRunner.run).parameters]
    if built == ["self", "code_commit", "config_digest", "random_seed"] and run == [
        "self",
        "corpus",
        "state_path",
        "validation_store",
        "clock",
    ]:
        return None
    return f"ReplayRunner now takes {built} and runs with {run}: re-read which agents it runs"


def _visibility_still_reads_only_the_availability_clock() -> str | None:
    """`is_visible_at` reads `available_time` alone, and the evidence store's query filters on
    it alone -- read from `domain/time.py`'s syntax tree and from the query's WHERE clauses."""
    tree = ast.parse((SRC / "domain" / "time.py").read_text(encoding="utf-8"))
    visible = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "is_visible_at"
    )
    read = sorted(
        {
            node.attr
            for node in ast.walk(visible)
            if isinstance(node, ast.Attribute) and node.attr.endswith("_time")
        }
    )
    query = (SRC / "storage" / "parquet.py").read_text(encoding="utf-8")
    filtered = sorted(
        {
            clock
            for where in re.findall(r"WHERE(.*?)ORDER BY", query, re.DOTALL)
            for clock in re.findall(r"(\w+_time)\s*<=", where)
        }
    )
    if read == ["available_time"] and filtered == ["available_time"]:
        return None
    return (
        f"is_visible_at reads {read} and the evidence query filters on {filtered}: re-read "
        "whether a record revised after as_of is still visible"
    )


def _the_multi_day_report_still_estimates_no_capacity() -> str | None:
    fields = sorted(PortfolioBacktestReport.model_fields)
    capacity = [field for field in fields if "capacity" in field or "exposure_" in field]
    attribution = sorted(SubjectAttribution.model_fields)
    if not capacity and attribution == ["pnl", "subject"]:
        return None
    return (
        f"PortfolioBacktestReport now holds {capacity} and SubjectAttribution {attribution}: "
        "re-read what the multi-day report measures"
    )


def _the_sdk_still_takes_no_tool_risk_or_validator() -> str | None:
    parameters = [*inspect.signature(OpenAlphaSDK.__init__).parameters]
    taken = [name for name in parameters if any(word in name for word in ("tool", "risk", "valid"))]
    return None if not taken else f"OpenAlphaSDK now takes {taken}"


def _an_abstention_still_cites_no_evidence() -> str | None:
    try:
        SignalFrame(
            subject="000001.SZ",
            as_of=datetime(2026, 1, 16, 9, tzinfo=UTC),
            direction="abstain",
            strength=0,
            confidence=0,
            horizon="5d",
            abstention_reason="the evidence supports no direction",
        )
    except ValueError as error:
        return f"an abstaining SignalFrame that cites no evidence is now refused: {error}"
    return None


# --- The retired claims -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RetiredClaim:
    """A family of wordings the code refutes, and why."""

    name: str
    pattern: re.Pattern[str]
    refuted_by: str
    retired: tuple[str, ...]
    paraphrase: str
    premise: Callable[[], str | None] | None = None


RETIRED_CLAIMS: Final[tuple[RetiredClaim, ...]] = (
    RetiredClaim(
        name="the committee's parts switch on and off one by one",
        pattern=re.compile(
            r"(?<!不能)(?<!不可)(?<!不可以)(?<!无法)(?<!没法)(?<!不会)"
            r"(?:独立|分别|单独)(?:启停|开关|关闭)|(?<![不未没无])能单独做对照"
            r"|(?<![A-Za-z])(?:independently|separately|individually)\s+(?:toggl|enabl|disabl|switch)"
            r"|可消融\s*Bull\s*/\s*Bear|ablatable\s+bull\s*/\s*bear"
            r"|(?<!不是)(?<!并非)可消融\s*(?:三态)?风险委员会"
            rf"|(?:与|和)\s*完整委员会{_NO_COMMA_OR_DENIAL}{{0,8}}消融"
            rf"|(?:Bull\s*/\s*Bear|辩论){_NO_COMMA}{{0,4}}(?:与|和)\s*风险委员会"
            rf"(?:(?!整体){_NO_COMMA_OR_DENIAL}){{0,4}}可消融",
            re.IGNORECASE,
        ),
        refuted_by=(
            "DeliberationCommittee.review(*, signal, results) takes no switch and runs the "
            "bull/bear debate and the three risk votes in one call (agents/committee.py:71-183); "
            "OpenAlphaSDK.deliberate passes only those two (sdk.py:236-243), and POST "
            "/api/v1/research/deliberate accepts only signal and agent_results "
            "(api/app.py:314-320). AblationResult(enabled=True) is written, never computed "
            "(agents/committee.py:176-177). Only the whole committee, per call, is optional, and "
            "its ablation compares the signal before and after all of it (:178-181)."
        ),
        retired=(
            "**双委员会研判**：Bull/Bear 研究辩论与激进/中性/保守风险委员会均可独立启停，"
            "既保留观点碰撞，也能做消融对照。",
            "Bull/Bear 辩论与激进、中性、保守风险视角都能独立启停，方便做消融对照"
            "\N{FULLWIDTH SEMICOLON}",
            "每个委员会可以独立启停，用消融对照判断它是否真的贡献增量。",
            "辩论模块可独立关闭，与单 Agent 基线进行消融比较，避免把更长的对话误认为更好的结论。",
            "**开场钩子：** 太会设计了，激进、中性、保守三个角色不是换语气，而是能单独做对照实验。",
            "三个视角基于同一证据合同输出结构化意见，可以分别启停，再与基线结果比较。",
            "OpenAlpha CN 为 Bull/Bear 研究辩论与激进、中性、保守风险委员会提供独立启停，"
            "让研究者比较单 Agent、辩论版和委员会版结果。",
            "真正可比的是整条链路：Bull/Bear 和三态风险委员会可独立关闭，"
            "结合事件统计和多日组合报告比较增量。",
            "可消融 Bull/Bear 研究辩论",
            "Bull/Bear 与风险委员会也可消融比较",
            "确定性基线、单 Agent 与完整委员会可以做消融对照",
            "ablatable bull/bear and three-perspective risk committee",
            "把两类优势结合成可消融风险委员会",
        ),
        paraphrase="委员会里的多空辩论和风险视角可以各自打开或关上。",
        premise=_the_committee_still_takes_no_switch,
    ),
    RetiredClaim(
        name="the SDK and the CLI pass through the FastAPI boundary",
        pattern=re.compile(
            rf"{_FACE}{_NO_COMMA_OR_DENIAL}*(?:最终)?(?:进入|经过|通过|走)同一\s*FastAPI"
            rf"|四类入口{_NO_COMMA}*(?:最终)?(?:进入|经过|通过|走)同一\s*FastAPI"
            r"|(?<!nor\s)(?<!nor\sthe\s)"
            rf"{_FACE}(?:(?!\b(?:never|not|no|neither|nor|without)\b)[^.;,])*"
            r"(?:through|via|behind)\s+(?:the\s+)?(?:same\s+)?FastAPI",
            re.IGNORECASE,
        ),
        refuted_by=(
            "sdk.py imports no HTTP client and no part of openalpha_cn.api: every SDK method calls "
            "a service in process (sdk.py:3-125; run_research at :196-206). cli.py names the REST "
            "app only as the string `openalpha serve` hands to uvicorn (cli.py:1708-1709). Only "
            "REST callers, the React workbench among them, reach the request-size limit and the "
            "security headers create_app installs (api/app.py)."
        ),
        retired=(
            "REST、Python SDK、Typer CLI 和 React 工作台最终进入同一 FastAPI 公共边界。",
            "REST、SDK、CLI 与 React 工作台通过同一 FastAPI 合同进入证据、研究、产品、"
            "组合与验证能力。",
        ),
        paraphrase="SDK 与 CLI 的请求同样要过 FastAPI 那道边界。",
        premise=_the_sdk_and_the_cli_still_call_in_process,
    ),
    RetiredClaim(
        name="replay executes the A-share trading rules",
        pattern=re.compile(
            rf"(?:T\+1|整手|停牌|涨跌停){_NOT_END}{{0,30}}(?:也在|进入同一路径)回放"
            rf"|回放{_NO_DENIAL}{{0,12}}(?:继续|也)?执行{_NOT_END}{{0,8}}T\+1"
            rf"|(?<!不在)(?<!不在\s)(?<!不会在)(?<!不会在\s)run_cycle`?{_NO_DENIAL}{{0,12}}"
            rf"(?:再叠加|并执行){_NOT_END}{{0,8}}T\+1"
            rf"|无论哪条路径{_NOT_END}{{0,6}}都要经过"
            rf"|回放{_NO_DENIAL}{{0,8}}(?:加入|叠加)\s*T\+1"
            r"|结果还会经过\s*T\+1"
        ),
        refuted_by=(
            "backtest/replay.py imports no execution or portfolio module (replay.py:3-27): a "
            "replay runs ResearchEngine.run_cycle and outcome validation over the frozen corpus "
            "and executes no order. T+1, board lot, suspension, limit-lock and fees are "
            "PortfolioSimulator's (backtest/portfolio.py), applied when a caller sends orders: "
            "POST /api/v1/portfolio/execute, or the multi-day portfolio backtest, which drives "
            "the simulator (backtest/multi_day.py:58-66, :204)."
        ),
        retired=(
            "T+1、整手、停牌、涨跌停与费用进入同一路径回放。",
            "回放与实时研究共用同一个 `run_cycle`，再叠加 T+1、停牌、涨跌停和费用约束，减少"
            "\N{LEFT DOUBLE QUOTATION MARK}线上一套、回测一套"
            "\N{RIGHT DOUBLE QUOTATION MARK}的漂移。",
            "结果还会经过 T+1、停牌、涨跌停、整手与费用约束。",
            "无论哪条路径，都要经过 A 股 T+1、整手、停牌、涨跌停与成本约束，"
            "并输出相同结构的验证结果。",
            "所有验证仍使用四时钟证据和同一个 `run_cycle`，并执行 T+1、涨跌停与费用约束。",
            "A 股 T+1、整手、停牌、涨跌停与成本也在回放中执行，组合状态跨日演进。",
            "历史回放继续执行 A 股 T+1、整手、停牌、涨跌停和费用约束。",
            "| 回放执行 | 实时/回放同核心，加入 T+1、整手、停牌、涨跌停锁单 | "
            "回测路径更接近 A 股现实 |",
        ),
        paraphrase="回放时同样会检查 T+1 与涨跌停。",
        premise=_replay_still_executes_no_order,
    ),
    RetiredClaim(
        name="a rejected order is linked to its decision",
        pattern=re.compile(
            rf"拒单(?:也)?关联决策|拒单[，,]\s*记录{_NOT_END}{{0,12}}关联决策"
            rf"|(?:执行失败|拒单|成交){_NO_COMMA_OR_DENIAL}{{0,6}}纳入证据链"
            rf"|(?<!并非)(?<!不是)(?:任何|所有|全部|每个)(?:异常|故障|错误|失败)"
            rf"{_NO_COMMA_OR_DENIAL}{{0,6}}沿\s*(?:运行\s*|任务\s*)?ID"
            rf"|沿\s*(?:(?:运行|任务|订单)\s*(?:或|与|和|/)?\s*){{0,3}}ID"
            rf"{_NO_COMMA_OR_DENIAL}{{0,6}}找到{_NO_COMMA}{{0,4}}(?:故障|错误)"
            rf"|组合账本{_NO_COMMA_OR_DENIAL}{{0,10}}稳定\s*ID\s*关联"
            rf"|组合执行{_NO_COMMA_OR_DENIAL}{{0,6}}关联链?"
            rf"{_NO_COMMA_OR_DENIAL}{{0,4}}(?:回溯|追溯)"
            r"|reject\w*(?:\s+\w+){0,3}\s+(?:linked|tied)\s+to\s+(?:\w+\s+)?decision",
            re.IGNORECASE,
        ),
        refuted_by=(
            "PortfolioOrder carries order_id, subject, side, quantity and target_weight "
            "(domain/portfolio.py:132-162), and PortfolioTransition adds the status, the states "
            "before and after, execution, reason and realized_pnl_delta (:165-176). A rejection "
            "records its reason and its states' date, and no decision."
        ),
        retired=(
            "停牌、涨跌停锁单、整手、T+1、现金不足或敞口超限都会生成明确拒单，记录原因、时间和关联决策"
            "\N{FULLWIDTH SEMICOLON}",
            "组合层的拒单也关联决策与 A 股规则。",
            "最终你可以沿运行 ID 找到故障发生在哪一层",
            "最终你可以沿运行或订单 ID 找到故障发生在哪一层",
            "OpenAlpha CN 则把 A 股执行失败纳入证据链",
            "任何异常都能沿 ID 返回具体节点",
            "证据、风险决定和组合账本都有稳定 ID 关联",
            "四时钟、模型与 Prompt 版本、双委员会、风险和组合执行都能沿关联链回溯",
        ),
        paraphrase="每一笔拒单都能追溯到产生它的那个决策。",
        premise=_a_rejection_still_names_no_decision,
    ),
    RetiredClaim(
        name="the report center holds committee, portfolio and statistical results",
        pattern=re.compile(
            rf"(?:组合执行|统计结果){_NO_DENIAL}{{0,20}}写入报告中心"
            rf"|(?:多日组合|事件统计){_NO_DENIAL}{{0,12}}反馈回报告中心"
            rf"|(?:委员会|风险门|组合){_NOT_END}{{0,16}}结果都能进入报告"
            rf"|(?:委员会|组合验证){_NO_DENIAL}{{0,16}}新结果{_NO_DENIAL}{{0,8}}报告中心"
            rf"|(?<![不未没无])(?<!不会)继续进入{_NO_COMMA_OR_DENIAL}{{0,20}}"
            rf"(?:统计|回放|组合){_NO_COMMA_OR_DENIAL}{{0,6}}报告中心"
            rf"|报告{_NO_COMMA_OR_DENIAL}{{0,6}}展示{_NO_COMMA}{{0,8}}实际结果"
            rf"|统计结果{_NO_COMMA_OR_DENIAL}{{0,4}}都写进{_NO_COMMA}{{0,6}}记录"
            rf"|(?<![不未没无])便于对比{_NO_COMMA}{{0,4}}Agent\s*或委员会的增量"
        ),
        refuted_by=(
            "ResearchReport holds one research run: run_id, subject, created_at, title, summary, "
            "decision_id, signal_id, final_action, evidence_ids and risk_flags "
            "(domain/report.py:25-37), built by ResearchReportFactory.build from a "
            "ResearchRunResult (product/reporting.py:52-55). No committee outcome, portfolio "
            "transition or statistic is a field of it. EventStudyReport and "
            "PortfolioBacktestReport are handed back to the caller and stored nowhere: no storage "
            "module names them (backtest/event_study.py:40, backtest/multi_day.py:176). The "
            "committee's before-and-after delta is only in the AblationResult its own call "
            "returns (agents/committee.py:176-182)."
        ),
        retired=(
            "研究结论、风险决定、组合执行和统计结果共同写入报告中心，"
            "便于对比不同 Agent 或委员会的增量。",
            "最后，多日组合与事件统计把结果反馈回报告中心。",
            "Agent、双委员会、风险门、组合与统计结果都能进入报告。",
            "Agent、Bull/Bear、风险委员会与组合验证产生的新结果，都能通过报告中心沉淀。",
            "自定义结果继续进入风险门、账本、回放、统计和报告中心",
            "后续报告与验证再展示这一判断的实际结果",
            "最终 Agent 输出、风险决策、组合成交和统计结果都写进可追踪记录",
            "便于对比不同 Agent 或委员会的增量",
        ),
        paraphrase="报告中心还会收录组合成交与统计检验。",
        premise=_a_report_still_holds_one_research_run,
    ),
    RetiredClaim(
        name="browser flows are tested at mobile width",
        pattern=re.compile(
            r"(?<!移除)(?<!删除)(?<!去掉)(?<!移除了)(?<!删除了)(?<!不再有)(?<!没有)"
            rf"移动(?:浏览器|视口|端|设备){_NO_DENIAL}{{0,12}}流程"
            r"|(?:桌面|desktop)\s*(?:/|和|与|and)\s*(?:移动|mobile)",
            re.IGNORECASE,
        ),
        refuted_by=(
            "V2-P5-014 removed the mobile-chromium project (web/playwright.config.ts:36-47): the "
            "two projects left, chromium and production, both run Desktop Chrome, and the PRD "
            "scopes mobile-width flows out."
        ),
        retired=(
            "OpenAlpha CN 在 GitHub Actions 中覆盖 Python 3.11、3.12 的 Windows 与 Ubuntu，"
            "Web 端执行 lint、单测、构建和桌面/移动浏览器流程，容器还测试持久化恢复。",
            "桌面和移动视口的关键流程通过 Playwright 自动测试。",
        ),
        paraphrase="Playwright 也在手机尺寸上跑关键流程。",
        premise=_playwright_still_drives_desktop_only,
    ),
    RetiredClaim(
        name="the four faces offer one and the same set of capabilities",
        pattern=re.compile(
            r"同一能力通过|工作台共享(?:同一后端能力|证据)|真走同一条链|入口用的是同一套能力"
            r"|四类入口共享五条"
            rf"|(?<![A-Za-z])(?:API|SDK|CLI|Web)(?![A-Za-z]){_NO_DENIAL}{{0,12}}共享同一合同"
            r"|(?:同一核心路径|同一路径)贯通\s*(?:API|REST)"
            r"|(?:API|REST)\s*/\s*SDK\s*/\s*CLI\s*/\s*Web\s*同(?:一)?契约"
            r"|三个面等价\s*[：:]\s*`?openalpha\s+factor\s+\*"
            r"|Three faces answer the same questions:\s*`?openalpha\s+factor\s+\*"
            rf"|两个面{_NOT_END}{{0,4}}与\s*`?panel build`?\s*一致"
            r"|in the SDK only,?\s+matching\s+`?panel build"
        ),
        refuted_by=(
            "tests/unit/test_surface_parity.py::PARITY maps 48 routes: 28 have no CLI command "
            "and 11 no SDK method (measured at d4ef5e4), each gap named there; and the React "
            "workbench calls none of the batch, screening, watchlist or report routes (web/src). "
            "The faces share the services each of them calls, not one set of capabilities. "
            "`factor describe` and `panel build` are CLI_ONLY, and no SDK method builds a panel: "
            "`describe_factor` is the one's SDK twin, and the other has none "
            "(tests/unit/test_surface_parity.py:186, :218, :225)."
        ),
        retired=(
            "**多入口一致**：同一能力通过 REST API、Python SDK、CLI 和响应式 React "
            "研究工作台开放。",
            "REST、Python SDK、CLI 与 React 工作台共享同一后端能力。",
            "**开场钩子：** 最怕 CLI 能用、网页却是演示\N{FULLWIDTH SEMICOLON}"
            "这个项目的 API、SDK、CLI、Web 真走同一条链。",
            "OpenAlpha CN 的 REST API、Python SDK、Typer CLI 和 React 工作台共享证据、研究、回放、"
            "批量、组合与产品服务。",
            "### 075\N{FULLWIDTH VERTICAL LINE}四个入口用的是同一套能力",
            "### API 关系图 01\N{FULLWIDTH VERTICAL LINE}四类入口共享五条功能链",
            "API 全景\N{FULLWIDTH VERTICAL LINE}四类入口共享五条功能链",
            "API、SDK、CLI、Web 共享同一合同",
            "同一核心路径贯通 API、SDK、CLI、Web 与回放",
            "API / SDK / CLI / Web 同契约",
            "三个面等价：`openalpha factor *`",
            "`factor build` 只有命令行与 SDK 两个面，与 `panel build` 一致",
            "Three faces answer the same questions: `openalpha factor *`",
            "`factor build` is on the command line and in the SDK only, matching `panel build`",
        ),
        paraphrase="四个入口能做的事一模一样。",
        premise=_the_workbench_still_skips_the_product_routes,
    ),
    RetiredClaim(
        name="the container recovery check deletes and recreates the container",
        pattern=re.compile(
            r"(?<!不会把)(?<!不把)(?<!没有把)(?<!未把)容器删除、重建"
            r"|(?<!没有)(?<![不未非无])"
            r"(?<!不会把容器)(?<!不把容器)(?<!没有把容器)(?<!未把容器)删除、重建后"
            r"|验证关键状态真的能恢复"
            r"|(?:deleted|removed) and recreated",
            re.IGNORECASE,
        ),
        refuted_by=(
            "scripts/verify_compose_recovery.py::main (:481-528) brings the stack up, builds one "
            "evidence batch over REST, runs `compose restart openalpha` and checks that the one "
            "evidence_id still comes back. Between those steps it neither removes nor recreates "
            "the container (its `compose down` is the teardown in `finally`, after the check), "
            "and it reads no batch, decision or checkpoint."
        ),
        retired=(
            "很多开源项目提供 Dockerfile，却没有验证容器删除、重建后任务和状态是否还在。",
            "它不是\N{LEFT DOUBLE QUOTATION MARK}能打包成镜像\N{RIGHT DOUBLE QUOTATION MARK}"
            "就算完成，而是验证关键状态真的能恢复。",
        ),
        paraphrase="CI 会把容器整个删掉再拉起来，确认所有状态都还在。",
        premise=_compose_recovery_still_only_restarts,
    ),
    RetiredClaim(
        name="interrupted batch work continues by itself after a restart",
        pattern=re.compile(
            rf"启动时{_NO_DENIAL}{{0,12}}(?:并|自动)继续(?:处理|执行)"
            r"|(?:重试|取消|并发|进度|上限|状态)\s*(?:和|与|、)\s*(?:进程)?重启恢复"
            r"|(?:Checkpoint|WAL)\s*\N{MIDDLE DOT}\s*(?:宕机|灾难)恢复"
            r"|(?:进程)?重启后还能恢复|批量任务中断(?:后还能|也能|后可)恢复"
            rf"|中断重启(?:会|就)?{_NO_COMMA_OR_DENIAL}{{0,6}}继续"
            r"|(?:retry|cancellation),?\s+and\s+restart\s+recovery",
            re.IGNORECASE,
        ),
        refuted_by=(
            "Every start requeues the interrupted items -- build_storage calls "
            "recover_interrupted (runtime/composition.py:255), which sets each running item back "
            "to queued (storage/batch.py:391-413) -- and nothing runs them until a caller posts "
            "POST /api/v1/research/batches/{batch_id}/retry (api/app.py:2105-2112), a route "
            "with no SDK method and no CLI command (tests/unit/test_surface_parity.py:75). A run "
            "resumes from its next agent only when it is run again under the same run_id "
            "(runtime/engine.py:288-298, :351)."
        ),
        retired=(
            "OpenAlpha CN 将批次、项目、进度和终态保存到 SQLite，启动时识别被中断任务并继续处理"
            "\N{FULLWIDTH SEMICOLON}",
            "1\N{EN DASH}8 并发、逐项进度、协作式取消、重试与重启恢复",
            "持久任务队列支持 1\N{EN DASH}8 并发、逐项进度、协作式取消、失败重试和进程重启恢复",
            "任务、并发上限、逐项进度、取消、重试和重启恢复均持久化",
            "SQLite 批量队列支持并发、进度、取消、重试和重启恢复",
            "持久批量队列限制 1\N{EN DASH}8 并发，支持进度、取消、重试和重启恢复",
            "批量任务中断后还能恢复",
            "中断重启会从下一节点继续",
            "支持合作式取消、失败重试与重启恢复",
            "批量任务中断也能恢复",
            "支持 1\N{EN DASH}8 并发、逐项进度、合作式取消、失败重试和进程重启恢复",
            "进程重启后还能恢复",
            "系统同时支持失败重试、并发上限和重启恢复",
            "bounded concurrent batches with progress, cancellation, retry, and restart recovery",
            "SQLite 状态与重启恢复",
            "Checkpoint \N{MIDDLE DOT} 宕机恢复",
            "Checkpoint \N{MIDDLE DOT} SQLite WAL \N{MIDDLE DOT} 灾难恢复",
        ),
        paraphrase="进程重启后，没跑完的批量任务会自己接着跑。",
        premise=_a_restart_still_only_requeues,
    ),
    RetiredClaim(
        name="the batch task center is still missing or deferred",
        pattern=re.compile(
            r"(?:仍缺少|缺少)大规模批量任务中心"
            rf"|大规模批量任务中心{_NO_COMMA_OR_DENIAL}{{0,20}}(?:延后|缺少|缺失)"
        ),
        refuted_by=(
            "The batch task center shipped: BatchResearchService (runtime/batch.py) behind POST "
            "/api/v1/research/batches and its progress, cancel and retry routes (api/app.py), up "
            "to MAX_BATCH_ITEMS requests at 1 to MAX_BATCH_WORKERS workers (batch_contracts.py). "
            "The one DEFERRED ledger row is the graphical agent-flow builder (OA-BOUND-005)."
        ),
        retired=("大规模批量任务中心和图形化 Agent 编排明确延后。",),
        paraphrase="批量任务中心还没有做。",
        premise=_the_batch_service_still_ships,
    ),
    RetiredClaim(
        name="upstream feature counts no document in the repository gives",
        pattern=re.compile(
            r"(?<![不没无]到\s)(?<![不没无]到)(?<!没有\s)(?<!没有)257\s*项上游功能|51\.36\s*%"
            r"|(?<![不没非])(?<!没有)(?<!不是)每(?:个|项)上游(?:功能|能力)"
            rf"{_NO_COMMA_OR_DENIAL}{{0,12}}(?:去向|台账)"
            rf"|源码审计{_NO_COMMA_OR_DENIAL}{{0,4}}逐项对账"
        ),
        refuted_by=(
            "Nothing in the repository gives these counts: "
            "docs/audits/three-upstream-source-audit-20260724.md, the audit the note cites, states "
            "no total, and the feature ledger counts OpenAlpha's own rows "
            "(artifacts/openalpha-v1-feature-coverage/summary.json), not upstream features: "
            "features.csv holds 185 rows and every feature_id is an OA- id. The audit reconciles "
            "the upstreams in eight capability-domain rows (its :26-37), not feature by feature."
        ),
        retired=(
            "共识别 257 项上游功能，原规划真实覆盖 132 项（51.36%），未审计和未知均为 0。",
            "表示每个上游功能都有明确去向",
            "每项上游能力最终都进入唯一 ID 台账",
            "TradingAgents 和 AI Hedge Fund 的能力边界通过源码审计被逐项对账",
        ),
        paraphrase="上游一共有两百多项功能，原计划覆盖了一半左右。",
        premise=_the_upstream_counts_are_still_unsourced,
    ),
    RetiredClaim(
        name="every diagram matches the current source",
        pattern=re.compile(r"每张图都对应当前源码(?![里中]?的?生成器)"),
        refuted_by=(
            "tests/unit/test_repository_assets.py::"
            "test_the_committed_diagrams_are_what_their_generators_write holds each SVG equal to "
            "what its generator writes: it checks a drawing against its generator, not against "
            "the code it describes, and the final review found api-05 drawing factor and agent "
            "attribution the code never produces (its I1)."
        ),
        retired=("每张图都对应当前源码与测试，不把规划功能画成已完成。",),
        paraphrase="五张图画的都是当前代码的样子。",
    ),
    RetiredClaim(
        name="the product supplies a permission boundary",
        pattern=re.compile(
            rf"还要自己补{_NO_COMMA}{{0,12}}权限边界"
            rf"|(?:OpenAlpha(?:\s*CN)?|本服务|本项目){_NO_COMMA_OR_DENIAL}{{0,8}}"
            rf"(?:实现|提供|具备|补齐|补上)了?{_NO_COMMA_OR_DENIAL}{{0,6}}权限边界"
        ),
        refuted_by=(
            "The REST service authenticates nobody -- 'this service has no authentication of any "
            "kind' (api/app.py, the GET /api/v1/jobs docstring) -- and README.md asks a deployer "
            "to put HTTPS, authentication, access control and rate limiting in a gateway."
        ),
        retired=(
            "团队使用 TradingAgents 或 AI Hedge Fund 思路时，往往还要自己补任务队列、权限边界、"
            "数据时间、结果持久化和 A 股执行规则。",
        ),
        paraphrase="OpenAlpha CN 已经把权限控制做好了。",
    ),
    RetiredClaim(
        name="wiring a model provider in brings schema validation with it",
        pattern=re.compile(
            r"wired in through the SDK|在\s*SDK\s*代码中接入模型\s*Provider"
            r"|Schema\s*与重试由治理层"
            rf"|接入{_NOT_END}{{0,30}}模型后[，,]?\s*才有\s*Schema\s*校验"
            rf"|接入\s*(?:模型|LLM){_NO_COMMA_OR_DENIAL}{{0,6}}"
            r"(?:其输出)?(?:才)?强制\s*(?:Schema|结构化输出)",
            re.IGNORECASE,
        ),
        refuted_by=(
            "OpenAlphaSDK.__init__ takes runtime_dir, clock, agents and features, and no provider "
            "(sdk.py:131-137): a model reaches a run only inside an agent, which the SDK takes "
            "as agents=. Schema validation is StructuredSignalAgent.analyze's "
            "StructuredAgentPayload.model_validate (agents/model.py:85-111); the provider sends "
            "the schema as its response_format (models/openai_compatible.py:164-177) and only "
            "checks that the reply is a JSON object (:209-214), and models/governance.py holds "
            "the retry policy, not schema validation (:37-47)."
        ),
        retired=(
            "deterministic operation without an LLM: no shipped path calls a model, and a model "
            "provider wired in through the SDK gets schema validation and bounded retries;",
            "**模型可插拔**：无 LLM 时可确定性运行，出厂路径不调用模型，"
            "在 SDK 代码中接入模型 Provider 后才强制结构化输出、Schema 校验和有界重试。",
            "模型可以在 SDK 代码中通过 OpenAI-compatible BYOK 接入，Schema 与重试由治理层处理，"
            "出厂路径不调用模型。",
            "在代码中接入 OpenAI-compatible 模型后，才有 Schema 校验和有界重试，出厂路径不调用模型",
            "在代码中接入模型时其输出强制 Schema",
            "在代码中接入 LLM 时才强制结构化输出和有界重试",
        ),
        paraphrase="通过 SDK 挂上的模型 Provider 自带 Schema 校验。",
        premise=_the_sdk_still_takes_no_provider,
    ),
    RetiredClaim(
        name="the SDK and the CLI make no HTTP request",
        pattern=re.compile(
            rf"{_FACE}{_NOT_END}{{0,12}}不走\s*HTTP(?!\s*边界)"
            rf"|{_FACE}[^.;]{{0,30}}(?:makes?|sends?)\s+no\s+HTTP",
            re.IGNORECASE,
        ),
        refuted_by=(
            "`openalpha doctor` (cli.py:720) runs `_probe_report` (:669) under --probe, which "
            "fetches through `_probe_once` (:625) from every provider it probes; `openalpha panel "
            "build` reads Tushare through `_panel_transport()`, a UrllibTushareTransport "
            "(cli.py:2145-2153) that calls urllib.request.urlopen (providers/tushare.py:460-474). "
            "What the SDK and the CLI commands skip is the REST boundary: neither imports the "
            "FastAPI app."
        ),
        retired=("Python SDK 与 CLI 命令不走 HTTP",),
        paraphrase="SDK 和 CLI 从来不发网络请求。",
        premise=_the_cli_still_reaches_tushare_over_http,
    ),
    RetiredClaim(
        name="a model reaches a run only through the SDK",
        pattern=re.compile(
            rf"只能经由{_NOT_END}{{0,30}}交给\s*SDK"
            rf"|包进{_NO_COMMA}{{0,30}}再交给\s*SDK"
            rf"|(?<!不)只在{_NO_COMMA}{{0,8}}SDK\s*代码(?:里|中)接入"
            r"|only as an agent[^.;]{0,40}pass(?:ed)? to the SDK",
            re.IGNORECASE,
        ),
        refuted_by=(
            "ResearchEngine takes its agents itself (runtime/engine.py:56-73); the SDK is one "
            "caller that builds an engine with them (sdk.py:196-206), the REST batch runner "
            "builds its own (api/app.py:1831-1843), and BatchResearchService runs whatever "
            "runner it is given (runtime/batch.py:290). A model reaches a run inside an agent, "
            "whichever of these composes it."
        ),
        retired=(
            "模型只能经由你在代码里构造、以 `agents=` 交给 SDK 的 Agent 进入研究",
            "a model reaches a run only as an agent you build in your own code and pass to the SDK "
            "as `agents=`",
            "模型要包进 `StructuredSignalAgent` 再交给 SDK",
            "OpenAI-compatible 模型端点只在你自己的 SDK 代码里接入",
        ),
        paraphrase="只有 SDK 能让模型参与研究。",
        premise=_the_engine_still_takes_its_agents,
    ),
    RetiredClaim(
        name="docs/api/http.md lists the named boundaries a model answer carries",
        pattern=re.compile(
            r"http\.md\)?[^.;]{0,60}(?:nine|sixteen|[0-9]+)\s+named\s+boundar", re.IGNORECASE
        ),
        refuted_by=(
            "docs/api/http.md names 3 of the 16 codes of KNOWN_MODEL_VIEW_LIMITATIONS "
            "(model_view.py:492); each single model answer -- an evaluation, a held prediction, a "
            "daily run -- carries the whole list as its limitations (model_view.py:2436, :2585, "
            ":2759), and the prediction listing carries none (:2625-2669)."
        ),
        retired=(
            "See [the HTTP contract](docs/api/http.md) for the three standings and the nine named "
            "boundaries.",
            "See [the HTTP contract](docs/api/http.md) for the three standings and the sixteen "
            "named boundaries.",
        ),
        paraphrase="The HTTP document is where every named boundary is spelled out.",
        premise=_the_http_contract_still_names_few_boundaries,
    ),
    RetiredClaim(
        name="every validation runs through run_cycle",
        pattern=re.compile(
            rf"(?<!并非)(?<!不是)所有验证{_NO_COMMA_OR_DENIAL}{{0,12}}同一个\s*`?run_cycle"
            r"|(?:验证|回测|daily|paper)\s*共用\s*`?run_cycle`?(?!\s*的说法)"
            r"|research core shared by(?:(?!\b(?:not|never|neither|nor)\b)[^.;]){0,40}"
            r"(?:backtest|paper|daily)"
            rf"|backtest{_NO_DENIAL}{{0,20}}(?:都经过|共用|共享)同一|用同一路径回答"
            rf"|(?:统计|组合){_NO_COMMA_OR_DENIAL}{{0,12}}只消费{_NO_COMMA}{{0,4}}(?:可复核)?账本"
        ),
        refuted_by=(
            "In backtest/ only replay.py calls run_cycle (backtest/replay.py:263-264); the "
            "multi-day portfolio backtest drives PortfolioSimulator (backtest/multi_day.py:58-66, "
            ":204) and never calls it, and the event study is EventStudy().analyze "
            "(sdk.py:245-247). `model daily-run` builds its RunManifest(mode=daily) itself "
            "(model_view.py:2261-2274), and paper and daily have no runtime behaviour "
            "(domain/run_mode.py:41-43). The event study takes the caller's return windows "
            "(backtest/event_study.py:10-31) and the multi-day backtest the caller's initial "
            "state and steps (backtest/multi_day.py:87-121); neither reads a ledger."
        ),
        retired=(
            "所有验证仍使用四时钟证据和同一个 `run_cycle`",
            "**同一研究内核**：实时研究、历史回放与验证共用 `run_cycle`",
            "one research core shared by the live, replay, backtest, paper and daily modes",
            "无论 live、replay 还是 backtest，研究请求都经过同一证据路由、"
            "Agent 聚合、风险门和持久化路径",
            "用同一路径回答：是否有效、为何有效、下一轮改什么",
            "统计、组合与产物层只消费可复核账本",
        ),
        paraphrase="每一种回测都走同一个研究循环。",
        premise=_the_portfolio_backtest_still_skips_run_cycle,
    ),
    RetiredClaim(
        name="a batch item runs the whole research chain",
        pattern=re.compile(
            rf"每个任务仍(?:执行完整|保留){_NO_COMMA_OR_DENIAL}{{0,24}}"
            r"(?:双委员会|组合|结构化信号|风险决定)"
            rf"|研究结论{_NO_COMMA_OR_DENIAL}{{0,6}}受到{_NO_COMMA}{{0,16}}交易规则"
        ),
        refuted_by=(
            "A batch item runs runner(item.request) (runtime/batch.py:290), and the shipped "
            "runners run ResearchEngine.run_cycle alone (api/app.py:1831-1843; sdk.py:216-234 "
            "through run_research): agents over the request's evidence, the risk gate and the "
            "decision ledger (runtime/engine.py:87-200). The committee, the portfolio and the "
            "validations are calls of their own, and an item's result is a decision_id, a "
            "signal_id and a final_action (batch_contracts.py:132-139). No research conclusion "
            "passes a trading rule on the way: T+1, board lot, suspension and limit rules are "
            "PortfolioSimulator's, applied to an order a caller executes."
        ),
        retired=(
            "每个任务仍执行完整证据、Agent、双委员会、风险、组合与验证链",
            "每个任务仍保留四时钟证据、结构化信号、风险与组合记录",
            "研究结论继续受到四时钟、风险门和交易规则约束",
            "每个任务仍保留四时钟证据、结构化信号与风险决定",
        ),
        paraphrase="批量里的每一项都会跑完委员会和组合核算。",
        premise=_a_batch_result_still_names_only_a_decision,
    ),
    RetiredClaim(
        name="portfolio records can be looked up by a batch's task ID",
        pattern=re.compile(rf"组合记录{_NO_DENIAL}{{0,4}}沿任务\s*ID"),
        refuted_by=(
            "GET /api/v1/research/batches/{batch_id} returns the batch's items, each with its "
            "request, status and result reference -- decision_id, signal_id, final_action "
            "(api/app.py:2082-2088; batch_contracts.py:132-150, :177-188). The portfolio ledger "
            "keys its rows on order_id and subject and holds the transition as JSON "
            "(storage/portfolio.py:22-27): no batch, task or decision ID."
        ),
        retired=("证据快照、Agent 输出、风险与组合记录都能沿任务 ID 查询。",),
        paraphrase="每笔持仓变化都能按批次查到。",
        premise=_a_portfolio_transition_still_names_no_batch,
    ),
    RetiredClaim(
        name="the three risk views weigh different things",
        pattern=re.compile(
            rf"保守视角优先{_NOT_END}{{0,8}}(?:回撤|流动性)|中性视角平衡"
            rf"|风险视角再讨论{_NOT_END}{{0,6}}(?:敞口|流动性)|从不同风险偏好"
            rf"|(?:反例|失效){_NO_COMMA_OR_DENIAL}{{0,8}}流动性"
        ),
        refuted_by=(
            "The three votes read the same flags (agents/committee.py:137-153): the aggressive "
            "vote reduces only on a severe flag, and the neutral and conservative votes are one "
            "expression -- block on a severe flag, reduce on any flag -- with the same reasons "
            "(:143-152). No vote reads drawdown, liquidity or return. The debate's two cases "
            "hold side, agent_ids, evidence_ids and weighted_score (:30-36), and nothing else."
        ),
        retired=(
            "中性视角平衡收益风险",
            "保守视角优先回撤与流动性",
            "激进、中性、保守风险视角再讨论敞口与流动性。",
            "激进、中性、保守风险委员会再从不同风险偏好评审",
            "反例 \N{MIDDLE DOT} 失效 \N{MIDDLE DOT} 流动性",
        ),
        paraphrase="保守的那一票更看重回撤。",
        premise=_the_neutral_and_conservative_votes_still_agree,
    ),
    RetiredClaim(
        name="the risk gate runs after the committee",
        pattern=re.compile(rf"风险门随后执行|委员会{_NOT_END}{{0,16}}风险门给出"),
        refuted_by=(
            "The risk gate runs inside ResearchEngine.run_cycle (runtime/engine.py:118), which "
            "calls no committee; the committee is a later, optional call (sdk.py:236-243, POST "
            "/api/v1/research/deliberate), and its pass, reduce or block is its own majority, "
            "DeliberationOutcome.risk_decision (agents/committee.py:154-161)."
        ),
        retired=(
            "风险门随后执行 pass、reduce、block，"
            "A 股组合层继续检查 T+1、整手、停牌、涨跌停、现金和敞口。",
            "之后，激进、中性、保守风险委员会再从不同风险偏好评审，"
            "风险门给出 pass、reduce 或 block",
            "Bull/Bear 和三态风险委员会先评审证据，风险门给出通过、降级或阻断",
        ),
        paraphrase="委员会投完票，风险门接着把关。",
        premise=_the_engine_still_calls_no_committee,
    ),
    RetiredClaim(
        name="every result passes through the committee",
        pattern=re.compile(
            rf"(?<![不无未没])(?:都要|必须|还要|须|需要?)经过{_NO_COMMA}{{0,20}}委员会"
            rf"|所有输出{_NO_COMMA_OR_DENIAL}{{0,12}}进入{_NO_COMMA}{{0,20}}委员会"
            r"|(?<![不无未没])经过双委员会"
            rf"|委员会{_NO_COMMA}{{0,2}}(?:与|和)\s*风险门{_NO_COMMA_OR_DENIAL}{{0,4}}"
            r"(?:审查|负责把|共同形成|共同给出)"
            rf"|委员会{_NO_COMMA_OR_DENIAL}{{0,6}}给出上游判断|回放{_NOT_END}{{0,30}}委员会则给出"
            r"|ResearchRunResult\s*\N{RIGHTWARDS ARROW}\s*DeliberationOutcome"
            rf"{_NOT_END}{{0,30}}"
            r"\N{RIGHTWARDS ARROW}\s*ValidationResult"
        ),
        refuted_by=(
            "ResearchEngine.run_cycle routes, runs the agents, aggregates their signals and runs "
            "the risk gate (runtime/engine.py:101-118), and calls no committee. The Bull/Bear "
            "debate and the three risk votes are one later, optional call a caller makes with "
            "the run's signal and agent results -- OpenAlphaSDK.deliberate (sdk.py:236-243) or "
            "POST /api/v1/research/deliberate (api/app.py:1946-1952); the CLI has no command for "
            "it -- and the A-share trading rules apply only when a caller executes an order "
            "(sdk.py:1195-1210). OutcomeValidator.validate takes a ResearchRunResult and an "
            "observation (backtest/validation.py:235-238), and nothing in src/ takes a "
            "DeliberationOutcome."
        ),
        retired=(
            "最终还要经过 Bull/Bear 辩论、三态风险委员会和 A 股成交约束",
            "输出还要经过风险委员会与成交模型",
            "所有输出再进入 Bull/Bear、风险委员会与风险门",
            "经过双委员会和风险门，再保存决策与报告",
            "双委员会与风险门审查结论",
            "双委员会与风险门继续审查风险",
            "四时钟证据、Bull/Bear 与风险委员会共同给出上游判断",
            "数据端的四时钟确保回放只看到当时可知的涨停与公告证据，"
            "Agent 与风险委员会则给出可追踪判断",
            "双委员会与风险门负责把分歧压缩成可审计动作",
            "双委员会和风险门共同形成可审计结果",
            "市场事实 \N{RIGHTWARDS ARROW} EvidenceSnapshot \N{RIGHTWARDS ARROW} "
            "ResearchRunResult \N{RIGHTWARDS ARROW} DeliberationOutcome / PortfolioTransition "
            "\N{RIGHTWARDS ARROW} ValidationResult",
        ),
        paraphrase="每个研究结果都要过一遍委员会。",
        premise=_the_engine_still_calls_no_committee,
    ),
    RetiredClaim(
        name="the committee's outcome is written to the DecisionLedger",
        pattern=re.compile(
            rf"(?:委员会|讨论|辩论|投票){_NO_COMMA_OR_DENIAL}{{0,24}}"
            rf"(?:写入|写进|存进|存入|进入|记入|保存)\s*{_NO_COMMA_OR_DENIAL}{{0,6}}"
            r"(?:DecisionLedger|决策账本|决策记录|不可变(?:决策)?记录|账本)"
            rf"|委员会{_NOT_END}{{0,24}}(?:最终|随后|再)写入\s*DecisionLedger"
            rf"|委员会{_NOT_END}{{0,24}}DecisionLedger{_NOT_END}{{0,20}}再把"
            rf"|(?<!并非)(?<!不是)所有结果{_NO_COMMA_OR_DENIAL}{{0,6}}进入{_NO_COMMA}{{0,8}}账本"
            rf"|委员会{_NO_COMMA_OR_DENIAL}{{0,24}}(?:回溯|追溯)"
            rf"|委员会{_NO_COMMA_OR_DENIAL}{{0,12}}(?:全部|都)留下{_NO_COMMA_OR_DENIAL}{{0,4}}证据引用"
            rf"|委员会结果{_NOT_END}{{0,16}}要到各自的接口(?:查看|查询)"
        ),
        refuted_by=(
            "A DecisionLedger is built only in ResearchEngine.run_cycle "
            "(runtime/engine.py:144-171), whose routing_path is the agents and then 'risk-gate' "
            "(:163), and it is stored by the one append_decision call there is (:400). "
            "OpenAlphaSDK.deliberate and POST /api/v1/research/deliberate hand the "
            "DeliberationOutcome back to the caller and store nothing (sdk.py:236-243, "
            "api/app.py:1946-1952), and no route, SDK method or command reads one back. A "
            "RiskVote holds no evidence field (agents/committee.py:39-44), and a ledger's "
            "AgentDecision holds an agent's signal_id and no evidence (domain/decision.py:14-22)."
        ),
        retired=(
            "Bull/Bear 和风险委员会也输出结构化结果，最终写入 DecisionLedger",
            "每次讨论、路由和结论都进入不可变决策记录",
            "Bull/Bear 和风险委员会负责研究分歧，DecisionLedger 与 PortfolioLedger 再把决定和执行"
            "分别保存",
            "最后，所有结果进入决策与报告账本",
            "四时钟、模型与 Prompt 版本、双委员会、风险和组合执行都能沿关联链回溯",
            "Agent、Bull/Bear、风险委员会与最终决策全部留下证据引用",
            "委员会结果与组合执行不写进报告，要到各自的接口查看",
        ),
        paraphrase="决策账本里也能查到委员会的每一票。",
        premise=_only_the_engine_appends_a_decision,
    ),
    RetiredClaim(
        name="the risk gate constrains the portfolio",
        pattern=re.compile(
            rf"(?:风险门|风险判断|风控|RiskGate){_NO_COMMA_OR_DENIAL}{{0,8}}"
            rf"(?:限制|约束|改变|改写|决定|影响){_NO_COMMA_OR_DENIAL}{{0,6}}"
            r"(?:组合|仓位|订单|持仓)"
            rf"|(?:组合|仓位|订单|持仓){_NO_COMMA_OR_DENIAL}{{0,6}}受"
            rf"{_NO_COMMA}{{0,4}}(?:风险门|风险判断){_NO_COMMA}{{0,4}}(?:约束|限制|控制)"
            rf"|风险门{_NO_COMMA_OR_DENIAL}{{0,6}}贯穿(?:全链|整条链)"
            rf"|风险(?:层)?(?:和|与)组合层{_NO_COMMA_OR_DENIAL}{{0,4}}应用"
            rf"{_NO_COMMA}{{0,6}}交易规则"
        ),
        refuted_by=(
            "RiskGate.evaluate answers pass, reduce or block about a signal "
            "(decisions/risk.py:45-52) inside run_cycle (runtime/engine.py:118); what it decides "
            "is the run's final_action -- block makes it abstain (:486-497) -- which the ledger, "
            "screening, reports and validation read. No portfolio or execution module reads "
            "RiskGate, risk_decision or final_action. The A-share rules are "
            "AShareExecutionPolicy's (backtest/execution.py:268-274), which PortfolioSimulator "
            "applies (backtest/portfolio.py:90-99) when a caller executes an order "
            "(sdk.py:1195-1210, api/app.py:2195-2219)."
        ),
        retired=(
            "Bull/Bear 与三态风控讨论分歧，风险门和 A 股规则约束组合",
            "风险判断真正改变订单结果",
            "打开 GitHub 下载，就能看到风险门如何贯穿全链",
            "风险和组合层再应用本地交易规则",
        ),
        paraphrase="组合仓位由风险门说了算。",
        premise=_no_portfolio_module_reads_the_risk_gate,
    ),
    RetiredClaim(
        name="the risk gate reads the committee and records its reasons",
        pattern=re.compile(
            rf"(?:RiskGate|风险门){_NO_COMMA_OR_DENIAL}{{0,20}}"
            rf"(?:根据|依据|参考|读取|结合|综合){_NO_COMMA}{{0,12}}委员会"
            rf"|原因{_NO_COMMA_OR_DENIAL}{{0,4}}写入{_NO_COMMA}{{0,4}}(?:决策账本|DecisionLedger)"
            rf"|(?:RiskGate|风险门){_NO_COMMA_OR_DENIAL}{{0,12}}记录{_NO_COMMA}{{0,24}}原因"
        ),
        refuted_by=(
            "RiskGate.evaluate(signal) reads the signal's risk_flags and nothing else "
            "(decisions/risk.py:45-52), inside run_cycle, which calls no committee "
            "(runtime/engine.py:101-118). The DecisionLedger it feeds stores risk_decision and "
            "final_action and has no reason field (domain/decision.py:54-62)."
        ),
        retired=(
            "OpenAlpha CN 将这些思路落成结构化 RiskGate：根据证据与委员会意见给出 pass、reduce 或 "
            "block",
            "并把原因写入决策账本",
            "RiskGate 记录 pass、reduce、block 及原因",
        ),
        paraphrase="风险门会参考辩论的结论。",
        premise=_the_gate_still_reads_only_the_flags,
    ),
    RetiredClaim(
        name="ChainLin errors reach the research or risk path",
        pattern=re.compile(
            rf"链邻\s*Provider\s*的{_NO_COMMA}{{0,12}}(?:错误|修订){_NO_COMMA}{{0,10}}"
            r"(?:不会被当(?:成|作)(?!空结果)|无风险|不会被隐藏)"
        ),
        refuted_by=(
            "The shipped research path never calls ChainLin: ChainLinDataProvider is constructed "
            "in one place, cli.py's _default_providers (cli.py:542-547), which `openalpha doctor` "
            "uses to report credentials and probe. Evidence build reads user files, panel build "
            "reads Tushare, and run_cycle reads the evidence a request carries."
        ),
        retired=(
            "链邻 Provider 的数据错误不会被当成",
            "链邻 Provider 的认证或服务错误也不会被当作市场无风险",
            "链邻 Provider 的修订和错误状态不会被隐藏",
        ),
        paraphrase="链邻接口出错时研究会自动降级。",
        premise=_chainlin_is_still_built_only_for_doctor,
    ),
    RetiredClaim(
        name="a run records its prompt versions",
        pattern=re.compile(
            rf"RunManifest{_NO_COMMA_OR_DENIAL}{{0,4}}(?:记录|保存|写入|包含)"
            rf"{_NO_COMMA_OR_DENIAL}{{0,36}}Prompt"
            rf"|Prompt{_NO_DENIAL}{{0,40}}(?:进入|写入)\s*RunManifest"
            rf"|每次运行保存{_NO_DENIAL}{{0,40}}Prompt"
            r"|版本化模型与\s*Prompt|模型与\s*Prompt\s*版本"
        ),
        refuted_by=(
            "RunManifest's one construction in a research run puts prompt_versions=() -- a "
            "literal empty tuple -- on every run (runtime/engine.py:136), and the DecisionLedger "
            "copies it (:170); nothing in src/ assigns it otherwise. The repository's own prompts "
            "are string literals pinned by code_commit (model_view.py:2247-2250); a prompt a "
            "caller's own code sends is recorded nowhere."
        ),
        retired=(
            "`RunManifest` 记录代码、配置、Provider、模型、Prompt、随机种子和环境版本",
            "RunManifest 记录代码、配置、Provider、模型、Prompt、随机种子和环境",
            "模型、Prompt、代码、配置、随机种子和证据哈希都进入 RunManifest",
            "每次运行保存代码提交、配置摘要、Provider 载荷哈希、模型和 Prompt 版本、随机种子与环境",
            "用冻结 Provider Payload、固定随机种子、版本化模型与 Prompt、"
            "配置摘要和内容哈希建立复现清单",
            "四时钟、模型与 Prompt 版本、双委员会、风险和组合执行都能沿关联链回溯",
            "RunManifest 保存模型、Prompt、代码与配置版本",
        ),
        paraphrase="每次运行都会留下提示词的版本。",
        premise=_the_manifest_still_records_no_prompt,
    ),
    RetiredClaim(
        name="README counts ten key capability classes",
        pattern=re.compile(r"十类增强能力"),
        refuted_by=(
            "The capability table that sentence introduces has seven rows (README.md:13-21 at "
            "20fec55), and no list of ten exists anywhere in the repository."
        ),
        retired=("本轮已经把与高星项目对账后最关键的十类增强能力落到源码和测试中",),
        paraphrase="下表列出了十项关键增强。",
    ),
    RetiredClaim(
        name="outcome validation applies the A-share trading rules",
        pattern=re.compile(
            rf"(?:结果|验证){_NO_COMMA_OR_DENIAL}{{0,8}}(?:统一)?计入\s*A\s*股交易约束"
        ),
        refuted_by=(
            "OutcomeValidator.validate (backtest/validation.py:224-284) attributes the caller's "
            "transaction_cost and the forgone benchmark and nothing else (:313-346), and "
            "validation.py imports no execution or portfolio module: T+1, board lot, suspension "
            "and limit rules are PortfolioSimulator's, applied when a caller executes an order."
        ),
        retired=("**结果可解释**：统一计入 A 股交易约束与成本",),
        paraphrase="结果验证会按 A 股规则重算成交。",
        premise=_validation_still_applies_no_trading_rule,
    ),
    RetiredClaim(
        name="only an order that passes the checks produces a transition",
        pattern=re.compile(rf"检查后{_NOT_END}{{0,4}}才产生{_NOT_END}{{0,8}}PortfolioTransition"),
        refuted_by=(
            "PortfolioSimulator.execute_order returns a PortfolioTransition with "
            "status='rejected' and the reason when a check fails (backtest/portfolio.py:310-322), "
            "and the SDK and REST faces append every transition they get (sdk.py:1209, "
            "api/app.py:2219-2225)."
        ),
        retired=(
            "通过 T+1、整手、停牌、涨跌停、费用与敞口检查后，才产生不可变 `PortfolioTransition`",
        ),
        paraphrase="被拒的订单不会留下任何转移记录。",
        premise=_a_rejected_order_still_makes_a_transition,
    ),
    RetiredClaim(
        name="every model answer carries the named limitations",
        pattern=re.compile(r"(?<!not )every\s+model\s+answer\s+carries", re.IGNORECASE),
        refuted_by=(
            "evaluation_view, held_prediction_view and daily_view put the whole list under "
            "'limitations' (model_view.py:2436, :2585, :2759); prediction_index_view and "
            "_prediction_index_entry, the listing, carry none (:2625-2669)."
        ),
        retired=("the sixteen named boundaries are the `limitations` every model answer carries",),
        paraphrase="Each response from the model plane lists the boundaries.",
        premise=_the_prediction_listing_still_carries_no_limitations,
    ),
    RetiredClaim(
        name="the feature ledger holds documentation evidence",
        pattern=re.compile(rf"(?:源码|测试)(?:证据)?{_NO_COMMA_OR_DENIAL}{{0,8}}文档证据"),
        refuted_by=(
            "artifacts/openalpha-v1-feature-coverage/features.csv has local_source_evidence, "
            "test_evidence and entrypoint columns and no documentation column (its header)."
        ),
        retired=("每项功能的源码、入口、测试和文档证据",),
        paraphrase="台账也列出每项功能对应的说明文档。",
        premise=_the_ledger_still_has_no_documentation_column,
    ),
    RetiredClaim(
        name="doctor reports authentication only when an endpoint rejects a credential",
        pattern=re.compile(
            r"凭证被端点拒绝\s*[（(]\s*`?authentication"
            r"|(?<!不)会对\s*\**每一个\**\s*已声明的数据集"
            r"|doctor\s+--probe`?\s*在凭证齐全时对\s*\**每一个\**\s*已声明的数据集"
        ),
        refuted_by=(
            "ChainLinDataProvider.fetch raises category='authentication' when its key is "
            "missing, before any transport call (providers/chainlin.py:155-162), and "
            "_probe_report fetches every dataset of a provider whose base URL is set "
            "(cli.py:669-696). With a ChainLin base URL and no key, doctor --probe sends no "
            "request, reports authentication for every dataset, and exits non-zero "
            "(PROBE_FAILURE_STATES, cli.py:634). A provider that is not set up sends none "
            "either: ChainLin without a base URL reports not_configured for every dataset "
            "(cli.py:685-686), and AKShare without its optional extra fails configuration "
            "before a request (providers/akshare.py:146-157)."
        ),
        retired=(
            "凭证被端点拒绝（`authentication`）时命令**非零退出**",
            "`openalpha doctor --probe` 会对**每一个**已声明的数据集发一次最小请求",
            "`openalpha doctor --probe` 在凭证齐全时对**每一个**已声明的数据集发一次最小请求",
        ),
        paraphrase="doctor 报 authentication 就说明服务器拒绝了你的 key。",
        premise=_a_missing_chainlin_key_is_still_authentication,
    ),
    RetiredClaim(
        name="REST, the SDK and the CLI reach the same product services",
        pattern=re.compile(
            r"(?<![A-Za-z])(?:REST|API)(?:\s*API)?\s*、\s*(?:Python\s*)?SDK\s*(?:与|和)\s*"
            r"(?:Typer\s*)?CLI\s*调用同一批(?:后端)?服务(?!\s*[，,]\s*但)"
        ),
        refuted_by=(
            "tests/unit/test_surface_parity.py::PARITY gives the six batch routes (:70-75), "
            "POST /api/v1/screen (:116) and the three watchlist routes (:117-119) no CLI "
            "command, and cli.py registers no screen, watchlist or batch command; of the report "
            "routes the CLI has create and export alone (:124, :129). The three faces call one "
            "set of services only where each of them reaches it."
        ),
        retired=(
            "REST、Python SDK 与 CLI 调用同一批后端服务",
            "API、SDK 与 CLI 调用同一批服务，Web 只接入其中一部分",
        ),
        paraphrase="命令行能做的事和 REST 一样多。",
        premise=_the_cli_still_has_no_product_command,
    ),
    RetiredClaim(
        name="every attempt of a model call is written to the usage ledger",
        pattern=re.compile(
            rf"(?<!不是)(?<!并非)每(?:一)?次尝试{_NO_COMMA_OR_DENIAL}{{0,40}}"
            r"(?:账本|入账|记账|记录|写入)"
        ),
        refuted_by=(
            "OpenAICompatibleProvider.generate_json retries inside one while loop and calls "
            "_record_usage once, after a response has decoded (models/openai_compatible.py:"
            "186-215); the one ModelUsageRecord it appends carries the attempt count "
            "(:237-248, models/governance.py:61). A failed attempt writes nothing."
        ),
        retired=(
            "每次尝试、Token 和估算成本另有账本",
            "每次尝试与 Token 成本要接自带用量追踪的 Provider 才会入账",
        ),
        paraphrase="模型每重试一回，账本里就多一行。",
        premise=_usage_is_still_recorded_once_per_call,
    ),
    RetiredClaim(
        name="the watchlist links its subjects to evidence and reports",
        pattern=re.compile(
            rf"观察池{_NOT_END}{{0,16}}(?<!不能)(?<![不没])接住{_NO_COMMA}{{0,6}}(?:证据|报告)"
            rf"|观察池{_NOT_END}{{0,6}}并(?:把它)?(?:连接|关联|接入)到?"
            rf"{_NO_COMMA}{{0,4}}(?:证据|报告)"
            rf"|观察池{_NOT_END}{{0,4}}再由报告中心"
            rf"|观察池后{_NOT_END}{{0,12}}生成{_NO_COMMA}{{0,8}}报告"
            r"|筛选\s*\N{RIGHTWARDS ARROW}\s*观察池\s*\N{RIGHTWARDS ARROW}"
            rf"(?!{_NO_COMMA}{{0,8}}(?:并不|并非|不是))"
            rf"|研究结果{_NO_COMMA_OR_DENIAL}{{0,12}}送入{_NO_COMMA_OR_DENIAL}{{0,16}}观察池"
        ),
        refuted_by=(
            "WatchlistEntry holds subject, tags, note, created_at and updated_at "
            "(domain/watchlist.py:23-30), and put, list and remove are the whole surface of its "
            "store (storage/product.py:34-50): no evidence, report, run or screening id. A "
            "report is built from a ResearchRunResult (product/reporting.py:55), never from a "
            "watchlist entry, and POST /api/v1/watchlist takes a WatchlistEntry, not a research "
            "result (api/app.py:1972-1976)."
        ),
        retired=(
            "这个项目的观察池不只是记住代码，还能接住后续证据和报告",
            "OpenAlpha CN 独立实现 SQLite 持久观察池，并把它连接到证据和报告链",
            "筛选结果可以进入持久观察池，再由报告中心固化",
            "筛选结果进入观察池后，还可以继续生成新的版本化报告",
            "筛选 \N{RIGHTWARDS ARROW} 观察池 \N{RIGHTWARDS ARROW} 不可变报告",
            "研究结果由调用方显式送入委员会、筛选、报告、观察池或组合核算",
        ),
        paraphrase="加进观察池的股票会自动带上它的证据和报告。",
        premise=_a_watchlist_entry_still_links_nothing,
    ),
    RetiredClaim(
        name="the routing path records why a role was chosen",
        pattern=re.compile(
            rf"(?:路由路径|routing_path|路由){_NO_DENIAL}{{0,24}}(?:为何|为什么)(?:选择|选中)"
        ),
        refuted_by=(
            "DecisionLedger.routing_path is a tuple of agent ids, 'risk-gate' last "
            "(domain/decision.py:55, runtime/engine.py:163), and AgentRouter.route hands back "
            "the selected agents alone (runtime/router.py:281): no reason for a selection is "
            "kept."
        ),
        retired=("路由路径被写进决策记录，后续可以检查为何选择某个角色",),
        paraphrase="决策记录里写着每个角色被叫来的理由。",
        premise=_the_routing_path_still_holds_only_ids,
    ),
    RetiredClaim(
        name="the risk committee casts an abstaining vote",
        pattern=re.compile(r"(?:激进|中性|保守)(?:\s*/\s*(?:激进|中性|保守))*\s*/\s*弃权"),
        refuted_by=(
            "A RiskVote's perspective is aggressive, neutral or conservative and its decision is "
            "pass, reduce or block (agents/committee.py:39-44), and review casts one vote from "
            "each perspective (:141-153). An abstention reaches the committee only as the input "
            "signal's own direction, which it carries through (:121-136)."
        ),
        retired=("保守 / 弃权",),
        paraphrase="风险委员会里还有一票可以选择不表态。",
        premise=_a_risk_vote_still_has_no_abstention,
    ),
    RetiredClaim(
        name="a portfolio compose command or route exists",
        pattern=re.compile(
            r"(?<!没有叫\s)(?<!没有叫)(?<!没有\s)(?<!没有)(?<!不叫\s)(?<!并无\s)portfolio\s+compose",
            re.IGNORECASE,
        ),
        refuted_by=(
            "Nothing is called compose: the CLI's portfolio group holds construct and "
            "turnover-variants (cli.py:6718, :7816), and a transition is made by POST "
            "/api/v1/portfolio/execute (api/app.py:2196) or OpenAlphaSDK.execute_portfolio_order."
        ),
        retired=("组合会计 \N{MIDDLE DOT} 显式 portfolio compose",),
        paraphrase="组合要用 compose 命令拼出来。",
        premise=_nothing_is_called_portfolio_compose,
    ),
    RetiredClaim(
        name="the validation reports answer whether the evidence was knowable then",
        pattern=re.compile(rf"共同回答{_NOT_END}{{0,4}}当时是否可知"),
        refuted_by=(
            "Knowability is decided when a replay corpus loads: a case whose evidence is not "
            "visible at its as_of raises LookAheadViolationError (backtest/replay.py:59-63). "
            "ReplayReport.look_ahead_violations counts cases whose run raised it, which no "
            "validated corpus can reach (:103-115); PortfolioBacktestReport, EventStudyReport and "
            "ValidationResult hold no such answer."
        ),
        retired=("共同回答：当时是否可知\N{FULLWIDTH QUESTION MARK}",),
        paraphrase="四份报告一起告诉你证据在决策时是不是看得到。",
    ),
    RetiredClaim(
        name="a custom agent's research is replayed",
        pattern=re.compile(
            rf"自定义\s*(?:结果|Agent){_NO_COMMA_OR_DENIAL}{{0,16}}(?:并)?可回放"
            rf"|输出仍会进入{_NO_COMMA_OR_DENIAL}{{0,16}}回放链"
            rf"|所有模型{_NO_COMMA_OR_DENIAL}{{0,48}}回放合同"
        ),
        refuted_by=(
            "ReplayRunner takes code_commit, config_digest and random_seed and no agents "
            "(backtest/replay.py:133-142), and builds each case's engine as "
            "partial(ResearchEngine, clock=...) (:248-253), so a replay runs the built-in "
            "baseline agents (runtime/engine.py:71). OpenAlphaSDK.replay, POST "
            "/api/v1/backtests/replay and `openalpha replay run` pass none either "
            "(sdk.py:1250-1259, api/app.py:2183-2193, cli.py:1249-1254)."
        ),
        retired=(
            "自定义结果同样经过风险门、写进账本并可回放",
            "输出仍会进入统一证据、风险、账本和回放链",
            "让你在代码中接入的所有模型共享同一 A 股 EvidenceSnapshot、SignalFrame、风险和回放合同",
        ),
        paraphrase="你写的 Agent 也能拿冻结语料重放一遍。",
        premise=_replay_still_runs_only_the_built_in_agents,
    ),
    RetiredClaim(
        name="a historical read sees only the version knowable at the time",
        pattern=re.compile(
            rf"{_NO_EARLIER_DENIAL}(?:"
            rf"(?:只|仅)能?(?:读到|读取|返回|接收|收){_NO_COMMA_OR_DENIAL}{{0,6}}"
            r"(?:当时|决策时刻)(?:已经)?(?:可知|可见)的?\s*(?:版本|evidence_id)"
            rf"|选择{_NO_COMMA_OR_DENIAL}{{0,4}}可见版本"
            rf"|不受{_NO_COMMA}{{0,6}}修订{_NO_COMMA}{{0,2}}干扰"
            rf"|四时钟{_NO_COMMA_OR_DENIAL}{{0,4}}(?:避免|阻止|防止)把?{_NO_COMMA}{{0,4}}修订"
            rf"|修订语义{_NO_COMMA_OR_DENIAL}{{0,4}}(?:保证|确保){_NO_COMMA}{{0,10}}不偷看"
            rf"|四时钟{_NO_COMMA_OR_DENIAL}{{0,6}}(?:约束|保证|确保|阻止){_NO_COMMA}{{0,12}}"
            r"(?:可见|可知|只读|只看到|未来信息)"
            r"|不会偷看后来才(?:知道|可知)|恢复当时的?信息边界"
            r"|(?<!no\s)(?<!not\s)strict\s+anti-look-ahead)",
            re.IGNORECASE,
        ),
        refuted_by=(
            "Visibility is available_time <= as_of and nothing else: is_visible_at "
            "(domain/time.py:34-36), which requests, replay corpora and provider batches call, and "
            "the evidence store's WHERE available_time <= ? (storage/parquet.py:104). A record "
            "revised after as_of is therefore still visible. The builder only marks one whose "
            "revision_time is later than its available_time as revised_after_initial_availability "
            "(evidence/builder.py:134-135), a reduced flag (domain/risk_flag.py:167) the risk gate "
            "answers with reduce (decisions/risk.py:41-51), and reduce leaves final_action as it "
            "was (runtime/engine.py:486-497)."
        ),
        retired=(
            "历史回放只能读取当时已经可知的版本",
            "历史查询只能读到当时可知的版本",
            "A 股数据进入 EvidenceSnapshot 后，历史研究只读取当时可见版本",
            "PIT 查询只返回决策时刻已经可知的版本",
            "下游只接收决策时刻已经可知的 evidence_id",
            "历史查询按决策时刻选择可见版本",
            "回放也能验证当时版本，不受后来修订干扰",
            "四时钟避免把修订结果提前放进历史研究",
            "PIT 与修订语义保证历史研究不偷看未来",
            "四时钟约束历史可见性",
            "四时钟保证 Agent 只读当时可见信息",
            "数据端的四时钟确保回放只看到当时可知的涨停与公告证据",
            "四时钟阻止未来信息进入样本",
            "历史研究不会偷看后来才知道的信息",
            "OpenAlpha CN 通过四时钟和 PIT 查询恢复当时信息边界",
            "content-addressed evidence and strict anti-look-ahead rules",
        ),
        paraphrase="回测里拿不到之后才改过的数字。",
        premise=_visibility_still_reads_only_the_availability_clock,
    ),
    RetiredClaim(
        name="the multi-day report measures capacity and attributes exposure",
        pattern=re.compile(
            rf"{_NO_DENIAL_IN_CLAUSE}(?:"
            rf"换手{_NO_COMMA}{{0,2}}(?:最大订单)?容量"
            r"|容量\s*(?:与|和|、|\N{MIDDLE DOT})\s*(?:标的)?(?:暴露|归因))"
        ),
        refuted_by=(
            "PortfolioBacktestReport holds the total, benchmark and active returns, turnover, "
            "max_order_notional -- the largest single fill -- max_gross_exposure, and an "
            "attribution that is each traded subject's realized PnL (backtest/multi_day.py:"
            "167-192, :248-253, :283-286). It estimates no capacity, and it attributes no return "
            "to an exposure; a fill is taken whole at the close, whatever its size "
            "(backtest/execution.py:285, :305)."
        ),
        retired=(
            "多日报告同时给出基准、主动收益、换手、容量和暴露归因",
            "多日收益、基准、主动收益、换手、容量与暴露归因",
            "统一输出收益、基准、主动收益、换手、最大订单容量和标的暴露归因",
            "多日组合报告同时给出基准、主动收益、换手、容量与标的暴露归因",
            "容量 \N{MIDDLE DOT} 暴露 \N{MIDDLE DOT} 标的归因",
            "多日组合报告持续展示换手、收益、基准、主动收益、容量和暴露",
            "多日报告再汇总换手、容量、收益、基准和主动收益",
        ),
        paraphrase="多日组合报告还会估算这笔资金最多能做多大。",
        premise=_the_multi_day_report_still_estimates_no_capacity,
    ),
    RetiredClaim(
        name="Tool, Risk and Validator are versioned extension contracts",
        pattern=re.compile(
            rf"{_NO_DENIAL_IN_CLAUSE}"
            r"(?:Provider|ResearchAgent|Agent)\s*[、/]\s*Tool\s*[、/]\s*Risk"
            r"\s*(?:[、/]|和|与)\s*Validator"
        ),
        refuted_by=(
            "OpenAlphaSDK takes runtime_dir, clock, agents and features (sdk.py:131-138), and a "
            "DataProvider is the other interface a caller implements (providers/base.py:170-178). "
            "ResearchTool is a Protocol satisfied only by EvidenceLookupTool "
            "(tools/base.py:54, tools/evidence.py:7), which no shipped path constructs or "
            "calls -- run_cycle reaches an agent and nothing else (runtime/engine.py:87-200) "
            "-- and whose one import under src/ is the package re-export "
            "(tools/__init__.py:3); RiskGate and OutcomeValidator are concrete classes the "
            "engine, the SDK and the routes construct themselves (runtime/engine.py:73, "
            "sdk.py:321, api/app.py:2345). None of the three carries a version: ContractVersions "
            "registers the evidence, signal, decision ledger, run manifest, validation result, "
            "prediction record, provider record and batch, and recovery state documents."
        ),
        retired=(
            "Provider、Tool、Risk 和 Validator 也有明确边界",
            "OpenAlpha CN 用 Provider、ResearchAgent、Tool、Risk、Validator 等版本化合同划分边界",
            "再允许扩展 Provider、Agent、Tool、Risk 与 Validator",
            "稳定、版本化的 Provider / Agent / Tool / Risk / Validator 合同",
        ),
        paraphrase="风险规则和验证器都能像 Agent 一样插进来。",
        premise=_the_sdk_still_takes_no_tool_risk_or_validator,
    ),
    RetiredClaim(
        name="every signal an agent emits cites evidence",
        pattern=re.compile(
            rf"{_NO_DENIAL_IN_CLAUSE}(?:"
            r"(?<!方向性)(?<!方向性\s)SignalFrame\s*必须(?:携带\s*evidence_ids|写明证据引用)"
            r"|(?<!并非)(?<!不是)所有输出必须引用\s*`?evidence_id"
            r"|(?<!并非)(?<!不是)每项输出都引用\s*`?evidence_id)"
        ),
        refuted_by=(
            "SignalFrame.validate_conclusion demands evidence_ids of a directional signal only; an "
            "abstention needs a reason and zero strength and may cite nothing (domain/signal.py:"
            "85-97), and both of run_cycle's abstaining aggregates cite none (runtime/engine.py:"
            "412-450). Confirmation conditions, invalidation conditions and risk flags default "
            "to empty tuples (domain/signal.py:48-51)."
        ),
        retired=(
            "SignalFrame 必须携带 evidence_ids",
            "所有输出必须引用 evidence_id",
            "市场事件、题材催化和资金流智能体经证据感知路由协作，每项输出都引用 `evidence_id`",
            "SignalFrame 必须写明证据引用、确认条件、失效条件、风险标记和弃权原因",
        ),
        paraphrase="智能体给出的每个结论都附带证据编号。",
        premise=_an_abstention_still_cites_no_evidence,
    ),
)
"""Each family of wordings `D13` retired, the code fact that refutes it, and what it retired."""


def _retired_claim_violations(documents: dict[Path, str]) -> list[str]:
    """One message per clause of `documents` that an entry's pattern finds."""
    return [
        f"{path.relative_to(ROOT)}:{clause.line} repeats the retired claim {claim.name!r}: "
        f"{clause.text!r}\n    refuted by: {claim.refuted_by}"
        for path, document in documents.items()
        for clause in clauses(document)
        for claim in RETIRED_CLAIMS
        if claim.pattern.search(clause.text)
    ]


def _guarded_documents() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in GUARDED_FILES}


def test_no_user_facing_document_repeats_a_retired_claim() -> None:
    """Every clause of the four documents, searched with every entry's pattern.

    Written against the clauses `D13` rewrote, which each entry's `retired` records. There is no
    allowlist: a clause a pattern finds is a claim to rewrite, or a sign the pattern has grown
    too wide -- narrow it, and its `retired` wordings must still match.
    """
    violations = _retired_claim_violations(_guarded_documents())
    assert not violations, (
        "\n".join(violations) + "\nRewrite the clause to what the code does, or, if the code "
        "now supports the claim, retire the entry."
    )


def test_a_retired_claim_written_back_is_reported() -> None:
    """The check above, fed each entry's first retired wording appended to `README.md`.

    Today's documents hold no retired claim, so a check that stopped reading them, or stopped
    matching, would still pass; this requires each wording to be reported under its entry.
    """
    documents = _guarded_documents()
    unreported = [
        claim.name
        for claim in RETIRED_CLAIMS
        if not any(
            f"retired claim {claim.name!r}" in violation
            for violation in _retired_claim_violations(
                {**documents, README: f"{documents[README]}\n\n{claim.retired[0]}\n"}
            )
        )
    ]
    assert not unreported, f"a retired wording written back went unreported: {unreported}"


def test_each_pattern_still_matches_the_wordings_it_retired() -> None:
    """Each entry's pattern must still find every wording it retired, read as clauses."""
    missed = [
        f"{claim.name}: {wording!r}"
        for claim in RETIRED_CLAIMS
        for wording in claim.retired
        if not any(claim.pattern.search(clause.text) for clause in clauses(wording))
    ]
    assert not missed, "a pattern no longer matches what it retired:\n" + "\n".join(missed)


def test_the_code_facts_behind_the_retired_claims_still_hold() -> None:
    """Each premise reads its one fact off the code; a message means the code has moved."""
    moved = [
        f"{claim.name}: {message}"
        for claim in RETIRED_CLAIMS
        if claim.premise is not None and (message := claim.premise()) is not None
    ]
    assert not moved, (
        "\n".join(moved) + "\nThe code may support a retired claim now: re-read it against "
        "refuted_by, and retire or rewrite the entry if the claim has become true."
    )


def test_the_retired_claims_blind_spot_is_real() -> None:
    """Each entry's paraphrase -- the same claim in other words -- passes its pattern.

    A pattern that starts matching its paraphrase has widened: update the paraphrase and the
    module docstring together.
    """
    caught = [claim.name for claim in RETIRED_CLAIMS if claim.pattern.search(claim.paraphrase)]
    assert not caught, f"a paraphrase is now caught, so the stated blind spot moved: {caught}"


TRUE_SENTENCES_THAT_SHARE_THE_WORDS: Final[tuple[str, ...]] = (
    "REST clients reach the service through the FastAPI boundary.",
    "A browser client goes via the same FastAPI app as curl.",
    "HTTP client 经过同一 FastAPI 边界。",
    "移动平均因子的计算流程见 factor list。",
    "资金在板块间移动的流程可以回放。",
    "拒单只记录原因，报告单独关联决策。",
    "The argument is in [the HTTP contract](docs/api/http.md), and `factor list --json` prints "
    "the named boundaries.",
    "三类数据 Provider 共享同一合同，失败语义一致。",
    "四类入口共享的是同一批领域模型，覆盖面各不相同。",
    "四类入口共享同一批服务，覆盖面各不相同。",
    "FastAPI 路由与 OpenAPI 文档共享同一合同。",
    "run_cycle 先路由、再生成信号，之后风险门给出 pass、reduce 或 block。",
    "信号还要经过风险门，委员会是之后可选的一次调用。",
    "所有输出进入决策记录，委员会的结果交还调用方。",
    "委员会的结果交还调用方，只有 run_cycle 写入 DecisionLedger。",
    "风险门之后，交易规则约束组合。",
    "每个信号都要经过风险门，委员会可以不调用。",
    "路由与风险门的结论进入不可变决策记录，委员会的讨论与投票只交还调用方。",
    "辩论与投票写进返回的 DeliberationOutcome，不进 DecisionLedger。",
    "风险门决定研究动作，组合由下单时的交易规则约束。",
    "风险门只读信号的风险标记，账本只记结论。",
    "组合账本按订单 ID 查询每一次成交与拒单。",
    "研究异常可沿运行与决策 ID 回到具体节点，订单则按订单 ID 查询。",
    "链邻 Provider 的错误分类只在 doctor 探测时用到。",
    "RunManifest 记录模型版本\N{FULLWIDTH SEMICOLON}Prompt 版本字段目前恒为空。",
    "实时研究与历史回放共用 run_cycle，验证与多日组合回测走各自的路径。",
    "The research core shared by live research and replay is run_cycle.",
    "在代码中把模型包进 StructuredSignalAgent 时，输出按 Schema 校验并有界重试。",
    "模型要包进 StructuredSignalAgent 再以 agents= 交给引擎。",
    "doctor --probe 对已配置的 provider、在凭证齐全时对它声明的每一个数据集发一次最小请求。",
    "Each single model answer carries the limitations list; the prediction listing carries none.",
    "组合执行计入 A 股交易约束与成本。",
    "台账的每项功能都有源码、入口和测试证据。",
    "检查后产生不可变 PortfolioTransition，成交与拒单都有。",
    "实时研究与回放贯通同一核心 run_cycle。",
    "下表列出七类增强能力。",
    "RunManifest 不记录 Prompt 版本。",
    "Prompt 版本目前不写入 RunManifest。",
    "RunManifest 的 prompt_versions 字段目前恒为空。",
    "结果验证不计入 A 股交易约束，只计入调用方给出的交易成本。",
    "Not every model answer carries the limitations: the prediction listing carries none.",
    "台账只有源码、入口和测试证据而没有文档证据。",
    "链邻的凭证被端点拒绝时报 authentication，缺 key 时也报。",
    "链邻缺 key 时 doctor --probe 不会对每一个已声明的数据集发请求。",
    "接入模型时不强制 Schema，包进 StructuredSignalAgent 才校验。",
    "模型端点不只在 SDK 代码里接入，引擎也直接接收 Agent。",
    "批量任务重启后，被中断的项重新排队，调用方再发一次重试才继续。",
    "Compose 重启恢复测试只重启容器，再读回证据。",
    "中断后再运行同一个 run_id，会从下一节点继续。",
    "REST API、Python SDK 与 CLI 调用同一批服务，但覆盖的面不同。",
    "用量账本在调用成功后记一行，行里带着尝试次数。",
    "不是每次尝试都入账，成功后才记一行。",
    "观察池只存标的、标签和备注，报告由研究运行结果生成。",
    "观察池里的标的再跑一次研究，就能生成一份新的报告。",
    "持久观察池、内容寻址且关联证据的报告中心。",
    "路由路径写进决策记录，可以查到选中了哪些角色。",
    "路由路径不记录为何选择某个角色。",
    "研究结论受到可得时间和风险门约束，订单再受交易规则约束。",
    "委员会整体可消融，Bull/Bear 与三视角风险投票在一次调用里完成。",
    "双委员会可消融。",
    "激进 \N{MIDDLE DOT} 中性 \N{MIDDLE DOT} 保守三票，"
    "每票 pass、reduce 或 block，弃权只来自输入信号。",
    "组合由 portfolio execute 执行，portfolio construct 负责构建。",
    "当时是否可知，由加载语料时的前视检查回答。",
    "backtest 走自己的路径，不经过同一 run_cycle。",
    "筛选 \N{MIDDLE DOT} 观察池 \N{MIDDLE DOT} 不可变报告",
    "SQLite 状态 \N{MIDDLE DOT} 重启后重新排队",
    "REST 经 FastAPI \N{MIDDLE DOT} SDK / CLI 进程内",
    "三重验证回答：是否显著、组合表现如何、记录是否一致",
    "SDK 与 CLI 在进程内调用服务，只有 REST 调用方经过同一 FastAPI 边界。",
    "The SDK and the CLI call services in process, and only REST callers go through the "
    "FastAPI boundary.",
    "四类入口里只有 REST 与 Web 经过 FastAPI。",
    "V2-P5-014 已移除移动端 Playwright 项目。",
    "V2-P5-014 移除了移动端 Playwright 项目。",
    "回放不执行 T+1，组合执行才会。",
    "回放不叠加 T+1 等交易规则。",
    "拒单不纳入证据链，只写进组合账本。",
    "组合账本不按稳定 ID 关联决策。",
    "组合执行无法回溯到决策。",
    "组合执行与统计结果不写入报告中心。",
    "多日组合报告不会反馈回报告中心。",
    "委员会的新结果不会进入报告中心。",
    "报告不展示这一判断的实际结果。",
    "CI 只重启容器，没有删除、重建后再检查。",
    "大规模批量任务中心已经出厂，图形化 Agent 编排延后。",
    "每张图都对应当前源码里的生成器，由同步测试钉住。",
    "组合记录不能沿任务 ID 查询。",
    "SDK 与 CLI 不经过同一 FastAPI 边界，而是在进程内调用服务。",
    "The SDK and the CLI never go through the FastAPI boundary.",
    "Neither the SDK nor the CLI goes through FastAPI.",
    "V2-P5-014 已把移动端 Playwright 项目移除。",
    "移动端的 Playwright 项目已被移除。",
    "仓库不再有移动端 Playwright 流程。",
    "风险门不影响组合。",
    "风险门不会限制仓位，仓位由下单时的交易规则约束。",
    "组合不受风险门约束。",
    "RiskGate 从不改变订单。",
    "委员会的结论不写入 DecisionLedger。",
    "委员会的投票结果不会存进决策账本。",
    "委员会的讨论不会回溯到决策账本。",
    "信号不需要经过委员会。",
    "研究结果无须经过委员会。",
    "风险门不读取委员会的意见。",
    "风险门并不参考委员会意见。",
    "RiskGate 不记录原因。",
    "RunManifest 预留了 Prompt 版本字段，目前恒为空。",
    "每次尝试失败都不会写入账本。",
    "组合执行可按订单 ID 追溯。",
    "研究异常沿运行 ID 回到具体节点。",
    "研究运行的结果可以继续进入报告中心，生成不可变报告。",
    "链邻 Provider 的认证错误不会被当成空结果成功。",
    "Bull/Bear 与风险委员会整体可消融，不能只开一方。",
    "每次调用都运行完整委员会：辩论与三票一起算。",
    "部署者需要在网关里自己实现权限边界。",
    "本服务不提供权限边界，请在网关补上鉴权。",
    "OpenAlpha CN 不具备权限边界，部署时要在前面加网关。",
    "进程宕机恢复后，被中断的项重新排队。",
    "SDK 和 CLI 都在进程内调用服务，从来不经过 FastAPI。",
    "只有 REST 调用方经过同一 FastAPI 边界。",
    "批量任务在进程重启后不会自动恢复运行，要调用方重试。",
    "报告只展示研究运行的结论，不展示实际结果。",
    "决策账本不记录委员会的投票。",
    "拒单不关联决策。",
    "被拒订单沿订单 ID 就能找到原因。",
    "回放无需执行 T+1。",
    "并非删除、重建后再检查，CI 只重启容器。",
    "并非任何异常都能沿 ID 回到具体节点。",
    "沿运行 ID 并不能找到故障发生在哪一层。",
    "统计结果不都写进记录，事件研究报告只交还调用方。",
    "自定义结果不会继续进入统计和报告中心。",
    "研究结论不受到交易规则约束，订单才受。",
    "所有输出都不必进入委员会。",
    "研究结果不经过双委员会。",
    "委员会不给出上游判断，它在研究之后才被调用。",
    "委员会的投票进入的不是决策账本，而是返回给调用方的结果。",
    "并非所有结果都进入账本，委员会的结果只交还调用方。",
    "风险门改变的不是组合，而是研究动作。",
    "风险门并不贯穿全链，只在 run_cycle 里执行。",
    "风险层与组合层不会应用同一套交易规则。",
    "原因不写入决策账本，账本只有风险结论。",
    "观察池并不能接住证据和报告。",
    "One research core shared by live research and replay, not by the backtest or daily runs.",
    "中断重启不会自己继续，要调用方再次运行。",
    "委员会与风险门并不负责把分歧压缩成动作。",
    "委员会与风险门不审查组合，组合由下单时的交易规则检查。",
    "并不是每个上游功能都有去向，台账数的是自有能力。",
    "研究结果不能直接送入观察池，观察池只收标的。",
    "The committee is one optional call whose ablation compares the signal before and after it.",
    "SQLite WAL \N{MIDDLE DOT} 同一 runtime_dir",
    "事件统计与多日组合用调用方提供的收益与订单。",
    "回测共用 run_cycle 的说法不成立，多日组合回测直接驱动组合模拟器。",
    "所有验证都不走同一个 run_cycle。",
    "backtest 与 replay 不共享同一套执行路径。",
    "每个任务仍保留四时钟证据，但不跑双委员会和组合。",
    "每个任务仍保留请求里的四时钟证据，组合与委员会另行调用。",
    "回放不在 run_cycle 之后再叠加 T+1。",
    "启动时不会自动继续执行被中断的项，只把它们重新排队。",
    "CI 不会把容器删除、重建后再验证，只重启一次再读回证据。",
    "大规模批量任务中心不再缺失，图形化 Agent 编排仍延后。",
    "并非所有验证都经过同一个 `run_cycle`：只有回放会先跑它。",
    "CLI 与 Web 并不共享同一合同：CLI 没有批量命令，Web 不调用产品路由。",
    "台账里找不到 257 项上游功能这类数字。",
    "筛选 \N{RIGHTWARDS ARROW} 观察池 \N{RIGHTWARDS ARROW} 报告并不是一条自动的链。",
    "委员会不能单独关闭 Bull/Bear，只能整体不调用。",
    "三个风险视角不能分别启停。",
    "没有叫 portfolio compose 的命令或路由。",
    "出厂路径没有 portfolio compose，组合执行走 /portfolio/execute。",
    "委员会的输出并不都留下证据引用，三票没有证据字段。",
    "委员会结果只在调用时交还、不落库，组合执行记在组合账本、可按标的查询。",
    "ResearchRunResult \N{RIGHTWARDS ARROW} ValidationResult 由调用方发起，"
    "ResearchRunResult \N{RIGHTWARDS ARROW} DeliberationOutcome 是另一次可选调用。",
    "factor list 与 factor run 三面等价，"
    "factor describe 只有命令行与 SDK，panel build 只有命令行。",
    "`factor build` is on the command line and in the SDK only; `panel build` is on the command "
    "line alone.",
    "源码审计不是逐项对账，而是按八个能力域对账。",
    "报告不便于对比不同 Agent 或委员会的增量，委员会调用前后的差值只在它自己的消融输出里。",
    "反例一方不看流动性，只有 Agent、证据与加权得分。",
    "出厂回放只跑内置的三个基线 Agent，不接收自定义 Agent。",
    "自定义 Agent 可以经 SDK 的 agents= 进入 run_cycle，但出厂回放只跑内置基线。",
    "自定义 Agent 不会进入出厂回放，回放只跑内置基线。",
    "自定义 Agent 的结果不可回放，出厂回放只跑内置基线。",
    "辩论与三票不能单独做对照，委员会只能整体与基线比较。",
    "回放的 run_cycle 之后不会再叠加 T+1。",
    "自定义 Agent 的输出仍会进入统一的证据链，但不会进入回放链。",
    "这不是可消融风险委员会，三票不进消融差值。",
    "链邻 Provider 负责认证、限流和错误分类，可得时间保证 Agent 只读决策时刻已可得的证据。",
    "回放会拒收当时还不可得的证据，但不会把修订过的记录挡在门外。",
    "四时钟都会记录，只有可得时间决定证据在某一时刻是否可见。",
    "修订时间晚于决策时刻的记录照样可见，只多一个 revised_after_initial_availability 标记，"
    "风险门据此降级。",
    "历史查询按可得时间筛选，不按修订时间挑版本。",
    "回放只检查可得时间，修订时间不参与可见性判断。",
    "修订前后载荷不同，evidence_id 也不同。",
    "多日报告没有容量模型，也没有暴露归因。",
    "多日报告给出基准收益、主动收益、换手、最大单笔成交额与按标的已实现盈亏。",
    "只有方向性 SignalFrame 必须携带 evidence_ids，弃权信号可以一条都不带。",
    "确认条件、失效条件和风险标记是 SignalFrame 的可选字段。",
    "实时研究与历史回放共用 run_cycle\N{FULLWIDTH SEMICOLON}"
    "回放只跑内置基线 Agent，状态写进独立的回放库。",
    "多日组合回测不经过 run_cycle，只执行调用方给的订单。",
    "风险门只读信号的风险标记，不读证据 ID。",
    "面板平面按批次存列，不给每一行生成 evidence_id。",
    "EvidenceBuilder 只规范化七类事件，daily、quote 这类行情记录会被拒收。",
    "事件研究只收调用方给的收益窗口，不读研究结果。",
    "批量里的每一项只跑 run_cycle，结果只有 decision_id、signal_id 与 final_action。",
    "委员会的讨论与投票不写进决策账本，只交还调用方。",
    "风险门的结论不会改变订单，组合层只按交易规则检查。",
    "RunManifest 只记录模型版本，Prompt 版本字段为空。",
    "历史研究只收首次可知时间不晚于 as_of 的证据，修订时钟不参与这一判断。",
    "可见性只看首次可知时间，修订时钟不参与判断。",
    "出厂 Agent 只读取请求携带、且在 as_of 时刻可见的证据。",
    "决策与报告只引用证据 ID，Agent 读取请求携带的完整快照。",
    "后续结果验证把实际观察、基准和成本接回原决策 ID。",
    "回放把冻结语料里的结果观察接到它重算出的决策上，接不回原来的实时决策。",
    "验证结果按决策 ID 查询，回放产生的验证挂在回放自己的决策上。",
    "每次运行只看请求携带的证据，研究记忆不会喂回下一次研究。",
    "研究记忆只写不读：引擎每次运行只追加一条摘要，Agent 看不到它。",
    "多日组合报告按标的归集已实现盈亏，敞口只有整本账户的最高值。",
    "用量账本只在端点同时返回请求 ID 与用量时记一行。",
    "中断后用同一个 run_id 再跑，已完成的 Agent 节点不会重跑。",
    "Web 工作台每次研究都生成新的 run_id，所以从 Web 重跑不会续跑。",
    "委员会的结果只交还调用方，不写入 DecisionLedger。",
    "决策账本记下路由和风险门结论，委员会的讨论与投票不进账本。",
    "RiskGate 只给研究动作定级，下单后的约束来自 PortfolioSimulator。",
    "组合层要等调用方下单才检查 T+1、整手、停牌、涨跌停、现金和敞口。",
    "拒单也会生成 PortfolioTransition，状态为 rejected。",
    "PortfolioTransition 不带决策 ID，也不带运行 ID。",
    "SDK 与 REST 的多日组合回测都把每笔成交与拒单写进组合账本。",
    "激进一票只在严重标记时减仓，中性与保守规则相同。",
    "可按运行 ID 读到恢复状态里失败的 Agent 与异常类型。",
    "Provider 失败分认证、限流、配置、响应无效和上游错误，从不变成空结果。",
    "回放验证复用四时钟证据和同一个 `run_cycle`\N{FULLWIDTH SEMICOLON}"
    "T+1、涨跌停与费用约束由多日组合回测按日施加。",
    "A 股 T+1、整手、停牌、涨跌停与成本不在回放里执行，而由多日组合回测按日推演组合状态时施加。",
    "每个任务只跑 `run_cycle`（证据、Agent 与风险门），委员会、组合与验证另行调用。",
    "进程重启后被中断的项重新排队、再发一次重试就能继续。",
    "启动时把被中断的项重新排队，调用方再发一次重试就从节点 Checkpoint 继续。",
    "证据与风险决定按稳定 ID 关联，组合账本按订单 ID 记录。",
    "每一项的请求与证据、状态、决策 ID 与最终动作都能沿任务 ID 查询。",
    "含前视证据的语料在加载时就被整体拒绝。",
    "它不是\N{LEFT DOUBLE QUOTATION MARK}能打包成镜像\N{RIGHT DOUBLE QUOTATION MARK}"
    "就算完成，而是验证写入的证据在容器重启后仍能读回。",
    "筛选、观察池与报告的接口 REST 全都提供，Python SDK 提供大部分，CLI 只有报告的创建与导出，"
    "Web 工作台暂未接入。",
    "委员会作为一次可选调用，也输出调用前后的对照。",
    "归因只认领交易成本与空仓机会成本两项，因子、智能体与模型份额结构性不产生，其余记为显式残差。",
    "重启应用后观察池仍在，同一标的再次加入会更新原条目。",
    "图形化任意 Agent Flow Builder 仍明确标记为 Deferred。",
    "组合执行和统计结果由各自的接口给出，不写入报告中心。",
    "T+1、整手、停牌与涨跌停都不在回放里执行。",
    "验证不共用 run_cycle。",
    "拒单不关联决策，只记原因。",
    "Compose 恢复检查不删除、重建容器，只重启后读回一条证据。",
    "批量任务中断后不会自己恢复，要调用方再发一次重试。",
    "移动端宽度不在 Playwright 的测试范围内。",
    "移动端 Playwright 项目已在 V2-P5-014 移除。",
    "风险门不约束组合\N{FULLWIDTH SEMICOLON}A 股规则只在调用方下单时施加。",
    "本服务不提供权限边界，跨机器开放要在前置网关补上认证。",
    "evidence build 打印的载荷可以直接交给 research run。",
    "`openalpha replay run` 读取一份冻结语料，与刚跑过的研究无关。",
    "CLI 能做证据构建、研究运行与回放，委员会、批量、筛选和观察池只有 SDK 或 REST。",
    "先固定 EvidenceSnapshot、SignalFrame、DecisionLedger、RunManifest 和 ValidationResult "
    "等核心合同。",
    "ResearchAgent 与 ResearchTool 没有版本号\N{FULLWIDTH SEMICOLON}"
    "RiskGate 和 OutcomeValidator 是具体类，SDK 不接收它们。",
    "Tool 合同已声明，只有 EvidenceLookupTool 实现它，研究引擎从不调用任何 Tool。",
    "风险门可在直接构造 ResearchEngine 时换成子类，SDK、REST 与 CLI 都不开放这一项。",
    "Agent 要声明 agent_id、evidence_families、feature_dependencies 与 provenance，"
    "并返回带理由的 AgentResult。",
    "源码环境只需 Python 与 uv，SQLite 与 DuckDB 随依赖装好，不需要另起数据库服务。",
    "台账里唯一未完成的一行是延后的 Flow Builder。",
    "审计文档按八个能力域对账三套上游，锁定了各自的 Commit。",
    "功能台账中的\N{LEFT DOUBLE QUOTATION MARK}已完成\N{RIGHT DOUBLE QUOTATION MARK}"
    "都同时写有源码证据与测试证据。",
    "桌面视口的关键流程通过 Playwright 自动测试。",
    "双委员会没有 CLI 命令，要经 SDK 或 REST 另行调用。",
    "委员会是一次可选调用，结果只交还调用方，不写入 `DecisionLedger`\N{FULLWIDTH SEMICOLON}"
    "`run_cycle` 的 `routing_path` 以 `risk-gate` 结尾，不含委员会。",
    "风险门只读 `SignalFrame.risk_flags`，不读委员会意见，也不改订单\N{FULLWIDTH SEMICOLON}"
    "A 股交易规则只在 `execute_portfolio_order` 与 `/portfolio/execute` 施加。",
    "批量里的每一项都由同一个 `run_cycle` 执行（REST 经 `run_one`，SDK 经 `run_research`），"
    "单批最多 10,000 项，并发 1\N{EN DASH}8。",
    "进程重启时只把运行中的项改回排队，不会自动接着跑，要调用方再发一次 retry。",
    "`RunManifest` 预留了 `prompt_versions`，`run_cycle` 把它写成空元组。",
    "出厂路径里只有 `openalpha doctor` 构造链邻与 AKShare Provider\N{FULLWIDTH SEMICOLON}"
    "`evidence build`、`panel build` 与 REST 证据路由都不调用它们。",
    "`POST /api/v1/research/run` 不读证据库，证据随请求体一起提交。",
    "回放每个案例跑两遍，第二遍用新引擎和空存储从头重算\N{FULLWIDTH SEMICOLON}"
    "前视语料在加载时就被整体拒绝，不进入回放。",
    "冻结回放语料放在 `tests/fixtures/replay/`，`openalpha replay run` 要调用方传入语料路径。",
    "`--resume` 对 `index_weight` 与三张财报只看分区是否已登记，不核对其中有哪些证券。",
    "`panel build` 只有命令行一个面，没有 SDK 方法，也没有路由。",
    "`factor describe` 与 `factor build` 都有命令行与 SDK 两个面，都没有路由。",
    "Web 工作台调用研究与回放接口，但不调用批量、筛选、观察池与报告接口。",
    "候选榜、预测记录与因子实验存成运行目录下的 JSON 文档，而不是 `state.sqlite3` 里的表。",
    "候选榜闸口只把 `run_manifest_id` 对到已完成的运行，"
    "不拿提交信号的 `signal_id` 去比对 `decisions.signal_ids`。",
    "`model daily-run` 直接写一条 `mode=daily` 的 `RunManifest`，"
    "不经过 `run_cycle`\N{FULLWIDTH SEMICOLON}"
    "多日组合回测、事件研究与结果验证也都不经过 `run_cycle`。",
    "`doctor --probe` 的探测结果里，只有 `authentication` 会让命令非零退出\N{FULLWIDTH SEMICOLON}"
    "`upstream` 与 `rate_limit` 按数据集上报，不影响退出码。",
    "`--waive-max-staleness` 仍是 `factor build` 接受的旗标，"
    "`V2-P4-100` 实测它以 exit 1 被引擎按名拒绝。",
    "整个委员会是一次可选的显式调用（`POST /api/v1/research/deliberate`、"
    "`OpenAlphaSDK.deliberate`），不能只开其中一方。",
    "实时研究与历史回放共用 `run_cycle`，避免线上逻辑和回放逻辑各走一套\N{FULLWIDTH SEMICOLON}"
    "结果验证、多日组合回测与 `model daily-run` 走各自的路径。",
    "React 研究工作台只接入其中一部分 REST 接口，不含批量、筛选、观察池与报告。",
    "可选、受限的 AKShare Adapter 已实现，但出厂路径只有 `openalpha doctor` 构造它。",
    "通过的是成交，未通过的是写明原因的拒单，二者都写进账本\N{FULLWIDTH SEMICOLON}"
    "它不会根据研究结论自动下单，也不连接实盘券商。",
    "bounded concurrent batches with progress, cancellation and retry, whose interrupted items "
    "are requeued after a restart",
    "no shipped path calls a model, and a model reaches a run only inside an agent you build in "
    "your own code",
    "a token and configured-cost usage ledger that no shipped path writes to \N{EM DASH} only a "
    "provider built with a usage store records into it",
    "one research core, `run_cycle`, shared by live research and replay (validation, the "
    "multi-day portfolio backtest and `model daily-run` take their own paths)",
    "durable node checkpoints that reject changed requests or graph signatures",
    "an optional, constrained AKShare adapter is implemented, but only `openalpha doctor` "
    "constructs it",
    "`factor build` is on the command line and in the SDK only: it writes panel partitions and "
    "the service ships with no authentication of its own.",
    "the sixteen named boundaries are the `limitations` each single model answer carries (an "
    "evaluation, a held prediction, a daily run; the prediction listing carries none)",
    "`openalpha validation statistics` and `validation segmented` are `CLI_ONLY`: their SDK "
    "twins exist and no route does yet",
    "实时与回放共用同一研究内核\N{FULLWIDTH SEMICOLON}"
    "T+1、整手、停牌、涨跌停锁单由组合执行与多日组合回测施加",
    "持久批量任务中心已经出厂，不在延后之列",
    "不写入 DecisionLedger",
    "输入的显式弃权原样保留",
    "客户端合同 \N{MIDDLE DOT} 仅 doctor 使用",
    "证据与面板构建不调用它",
    "REST 全部 \N{MIDDLE DOT} SDK 大部分 \N{MIDDLE DOT} CLI 仅报告",
    "RiskGate 已执行",
    "研究结果不会自动下单",
    "每次组合由调用方发起",
    "能否复现\N{FULLWIDTH QUESTION MARK}",
    "历史回放只接受可得时间不晚于决策时刻的证据，事后修订过的记录只被标记降级、不会被剔除。",
    "PIT 查询只返回可得时间不晚于决策时刻的证据，事后修订过的记录带着降级标记。",
    "四时钟单独记下修订时间，修订过的证据会被风险门降级。",
    "下游只接收可得时间不晚于决策时刻的 evidence_id",
    "四时钟分别记录在案，其中可得时间约束历史可见性。",
    "四时钟并不阻止修订过的记录进入历史研究，只给它降级标记。",
    "PIT 查询并不只返回当时可知的版本，修订过的记录也会返回。",
    "修订语义并不保证历史研究不偷看未来，只保证修订被标记。",
    "A record revised after as_of stays visible and carries revised_after_initial_availability; "
    "there is no strict anti-look-ahead on revisions.",
    "因子实验算周转与容量、冗余，六格归因。",
    "最大单笔成交额 \N{MIDDLE DOT} 最大总敞口 \N{MIDDLE DOT} 标的已实现盈亏",
    "Tool 合同尚无调用方，SDK 也不开放风险门与验证器的替换。",
    "方向性 SignalFrame 必须携带 evidence_ids，弃权须写明原因。",
    "并非所有输出必须引用 evidence_id，弃权可以一条都不带。",
    "不是每项输出都引用 evidence_id，弃权不带证据。",
    "每项方向性输出都引用 evidence_id，弃权写明原因。",
    "Python SDK 的调用从不走 HTTP 边界。",
    "多日报告给出换手，没有最大订单容量。",
    "多日报告不给最大订单容量，也不给标的暴露归因。",
    "换手、容量这两个词在报告里都找不到。",
    "报告里既没有容量与暴露归因，也没有容量模型。",
    "Agent、Tool、Risk 都不是版本化合同。",
    "ResearchAgent/Tool/Risk 没有版本号。",
    "Provider、Tool、Risk 三个合同里只有 Provider 可以扩展。",
    "稳定、版本化的 Provider / Agent / Tool / Risk / Validator 合同并不存在。",
    "并非 SignalFrame 必须携带 evidence_ids，弃权可以不带。",
    "所有输出必须引用 evidence_id 的说法不成立。",
    "PIT 查询不会只返回当时可知的版本，修订过的记录照样返回。",
    "历史研究并不能只读到当时可知的版本。",
    "该仓库没有 strict anti-look-ahead 规则。",
    "系统不会恢复当时的信息边界，只恢复可得边界。",
)
"""True or unrelated sentences that share a retired pattern's words. The review of `D13` measured
the first six being caught (its M1): client holds cli, 移动平均 holds 移动, and a rejection and a
report's decision can sit in one clause. The seventh puts http.md and a factor run's named
boundaries in one clause with no count, as README.en.md:100 did; the first draft of the
boundaries pattern caught that pointer. The pointer itself was wrong -- the boundaries are
`run_limitations`, which http.md does not hold -- and `D13`'s final round rewrote it, so the
sentence here is a true one in the same words. The review of the rebase round measured the next
two being caught by the four faces' entry
(its m4): a contract three providers share is not the faces', and faces that share domain models
need not cover the same routes. The two after them are what that review's suggested narrowing
would still catch: 四类入口共享同一 holds the true 同一批服务, and API read as a substring
holds OpenAPI and FastAPI. The next is why the risk gate's branch for §032 is anchored on 委员会
rather than on that review's suggested 之后: run_cycle's own order puts 之后 before 风险门给出,
and that order is true. The two after it are what the entry for a committee every result passes
through must not catch: a comma closes the phrase a 还要经过 or a 所有输出…进入 is about, and past
it these two say the committee is optional. The final review of the whole `D13` batch measured
the next two being caught (its I-A): a comma separates a committee's returned result from
run_cycle's write, and a gate from the trading rules that act after it. The rest are sentences
`D14` wrote while fixing that finding, each sharing a family's words: a gate every signal
passes before an optional committee, run_cycle's ledger beside the committee's returned debate,
a gate that decides research actions and reads only flags, the order ids a portfolio ledger is
keyed on, run and decision ids that lead back to a research node, and a ChainLin error
classification that only doctor reads. After them, one sentence for each family `D14`'s README,
why and prompt fixes added or broadened, each the true wording its own rewrite used or the
nearest true use of its words: a manifest whose prompt field is stated empty after a clause end,
run_cycle shared by live research and replay only, a model wrapped in StructuredSignalAgent and
handed to an engine, a probe conditioned on credentials, limitations on single answers, trading
constraints that portfolio execution counts, a ledger with source, entry and test evidence, a
transition for every order, and a table of seven. The ten after them are `D14`'s probes of the
entries its second commit added, each the most natural true sentence in their words; all but
the one in lower case were caught before the patterns were narrowed: five hold a denial inside
the span a pattern crosses, three hold one just before a pattern's first word, one names a
rejected credential without equating it with authentication, and one holds the manifest's field
name in lower case. The next fourteen probe what its third commit added or broadened: the true
wording of each rewrite, and for each narrowing the sentence it is narrowed against -- 重启恢复
with no list word before it, a hedge after 调用同一批服务, 不是 before 每次尝试, 关联 with no 并
before it, a denial inside a routing span, and a comma between a conclusion and the trading
rules. The next eight probe the diagram texts its fourth commit retired, each the wording the
generator now draws or the nearest true use of the retired words: three votes that never
abstain, a portfolio verb that exists, knowability answered at load, a backtest on a path of its
own, three products drawn apart, a restart that requeues, the faces' transport, and three
validations. The next eighteen are its fifth commit's probes of the older entries, each a
natural true sentence in their words that its pattern caught until the pattern was narrowed:
the review's two (an in-process SDK and CLI, a removed mobile project) in three and two
wordings, then denials inside a replay, rejection, report, container or portfolio-record span,
a comma after a batch center that shipped, and a diagram tied to its generator. The next
fifty-six come from the review of `D14` (its m-2): its thirty-nine true sentences, verbatim, of
which it measured thirty-one being caught, and seventeen natural denials `D14`'s fix round wrote
for the spans those thirty-nine did not reach. All of them but the review's eight that passed
were caught until their patterns were narrowed. The next seven probe what the fix round broadened
for that review's I-1 and I-2, each a true wording beside a retired one; one of them, 委员会与风险门
不审查组合, was caught by the older 审查 branch until that branch took the new one's span, which
stops at a denial. The next seventeen are the census's, at `29e26f3`: true sentences its six
reports wrote in the families' words, which the patterns caught until each was narrowed. The next
sixteen probe what the fix round added or narrowed beyond those: each is the wording a rewrite now
uses, a true sentence holding both of a branch's words, or a denial a span or a lookbehind must
stop at. The last hundred and forty-one come from the census round: first the census's own true
sentences, verbatim -- every one its six reports wrote in their second parts that is not pinned
above, a hundred and twenty-five, which the patterns passed before and after that round's four
families were added -- then sixteen probes of those families, each a rewrite's wording or a
denial; one of them, a denial before strict anti-look-ahead, was caught until that branch looked
behind for no and not. The last one is the fix round's review (its third minor): an SDK call
that skips the HTTP boundary, true with 边界 and false without it, which entry 15 -- a `D13`
entry, unchanged since -- caught until it looked ahead for that word. The last fourteen are
the census round's review (its I-1): the sentences a reader writes to record what a report
does *not* do, which the census round's four families caught until two clause-level guards
were added -- no denial before the claim, and, for the three families whose retired wordings
hold no denial at all, none anywhere in the clause."""


def test_the_retired_patterns_pass_the_true_sentences_that_share_their_words() -> None:
    """The other direction from the blind spot: a pattern must not catch a true sentence.

    There is no allowlist, so a pattern that catches one of these is narrowed, not pinned; its
    `retired` wordings must still match.
    """
    caught = [
        f"{claim.name}: {sentence!r}"
        for sentence in TRUE_SENTENCES_THAT_SHARE_THE_WORDS
        for clause in clauses(sentence)
        for claim in RETIRED_CLAIMS
        if claim.pattern.search(clause.text)
    ]
    assert not caught, "a retired pattern caught a true sentence:\n" + "\n".join(caught)


COMPOSE_RESTART: Final[str] = (
    '        _compose(compose_command, project, env, "restart", "openalpha")\n'
)
"""The one compose call `verify_compose_recovery.py::main` makes between building the evidence and
reading it back."""

REAL_RECREATES: Final[dict[str, str]] = {
    "compose down, then up": (
        '        _compose(compose_command, project, env, "down")\n'
        '        _compose(compose_command, project, env, "up", "--detach", "--wait")\n'
    ),
    "up with --force-recreate": (
        '        _compose(compose_command, project, env, "up", "--detach", '
        '"--force-recreate", "--wait")\n'
    ),
}
"""The natural ways to make the check a real delete-and-recreate, each added after the restart."""


REWRITES_THE_FINAL_REVIEW_MEASURED: Final[tuple[tuple[str, str], ...]] = (
    ("最终都要经过风险委员会审议", "every result passes through the committee"),
    ("每个信号必须经过委员会", "every result passes through the committee"),
    (
        "委员会的结论会存进 DecisionLedger",
        "the committee's outcome is written to the DecisionLedger",
    ),
    ("风险门限制组合仓位", "the risk gate constrains the portfolio"),
    ("组合受风险门约束", "the risk gate constrains the portfolio"),
)
"""Five natural rewrites of `D13`'s three newest classes, and the entry each belongs to. The final
review of the whole `D13` batch fed them to all 25 patterns at `20fec55` and none was caught (its
I-A); `D14` broadened those three entries until each is."""


def test_the_rewrites_the_final_review_measured_are_caught_by_their_entry() -> None:
    by_name = {claim.name: claim for claim in RETIRED_CLAIMS}
    missed = [
        f"{name}: {wording!r}"
        for wording, name in REWRITES_THE_FINAL_REVIEW_MEASURED
        if not by_name[name].pattern.search(wording)
    ]
    assert not missed, "a rewrite the final review measured is not caught:\n" + "\n".join(missed)


def test_the_ledger_premise_sees_an_alias_and_a_literal_getattr() -> None:
    """The final review found the ledger premise read only `x.append_decision(...)` (its m-11).

    It now sees the attribute wherever it is loaded, and `getattr` with the literal name; a name
    built at run time is the blind spot the module docstring states, and stays one.
    """
    planted = {
        "a call": "store.append_decision(entry)",
        "a bound alias": "write = store.append_decision\nwrite(entry)",
        "a literal getattr": 'getattr(store, "append_decision")(entry)',
    }
    missed = [
        name for name, source in planted.items() if not _append_decision_readers(ast.parse(source))
    ]
    assert not missed, f"the ledger premise does not see {missed}"
    assert not _append_decision_readers(ast.parse('getattr(store, "append_" + kind)(entry)'))


def test_the_portfolio_premise_reads_every_place_that_drives_the_simulator() -> None:
    """The final review found the old premise chose modules by file name and missed these three."""
    scopes = set(_portfolio_scopes())
    expected = {
        "backtest/multi_day.py",
        "sdk.py::execute_portfolio_order",
        "api/app.py::portfolio_execute",
    }
    assert expected <= scopes, f"the portfolio premise no longer reads {sorted(expected - scopes)}"


def test_the_portfolio_premise_reads_importers_and_only_the_routes_that_drive_it() -> None:
    """The review of `D14` (its m-3) measured the premise skipping `paper.py` and
    `portfolio_policy.py`, which import from the portfolio module without naming
    `PortfolioSimulator`; reading the whole of `create_app`, whose other routes read
    `final_action` on purpose; and missing a construction through the module."""
    scopes = set(_portfolio_scopes())
    assert {"backtest/paper.py", "backtest/portfolio_policy.py"} <= scopes, sorted(scopes)
    assert "api/app.py::create_app" not in scopes, "the premise reads every route of create_app"
    planted = _portfolio_scopes_of(
        "backtest/planted.py",
        ast.parse("from openalpha_cn.backtest import portfolio as p\np.PortfolioSimulator()\n"),
    )
    assert "backtest/planted.py" in planted, "a construction through the module went unread"
    through_the_package = _portfolio_scopes_of(
        "sdk.py",
        ast.parse(
            "import openalpha_cn.backtest.portfolio\n"
            "def run():\n"
            "    return openalpha_cn.backtest.portfolio.PortfolioSimulator()\n"
        ),
    )
    assert set(through_the_package) == {"sdk.py::run"}, sorted(through_the_package)
    nested = _portfolio_scopes_of(
        "api/app.py",
        ast.parse(
            "from openalpha_cn.backtest.portfolio import PortfolioSimulator\n"
            "def create_app():\n"
            "    def portfolio_execute():\n"
            "        return PortfolioSimulator()\n"
            "    def research_route():\n"
            "        return final_action\n"
        ),
    )
    assert set(nested) == {"api/app.py::portfolio_execute"}, sorted(nested)


@pytest.mark.parametrize("name", REAL_RECREATES)
def test_the_compose_premise_reports_a_real_delete_and_recreate(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day the check deletes and recreates the container the claim is true, and the premise
    must say so. `main()` already runs `compose down` as its teardown in `finally`, so the words
    alone cannot tell a real recreate from the teardown. The script is rewritten into `tmp_path`,
    never in the checkout."""
    source = (ROOT / "scripts" / "verify_compose_recovery.py").read_text(encoding="utf-8")
    assert source.count(COMPOSE_RESTART) == 1, "the restart call moved: update COMPOSE_RESTART"
    script = tmp_path / "scripts" / "verify_compose_recovery.py"
    script.parent.mkdir()
    recreated = source.replace(COMPOSE_RESTART, COMPOSE_RESTART + REAL_RECREATES[name])
    script.write_text(recreated, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert _compose_recovery_still_only_restarts() is not None, f"the premise missed {name}"


GENERATORS: Final[tuple[Path, ...]] = (
    ROOT / "scripts" / "generate_brain_diagrams.py",
    ROOT / "scripts" / "generate_api_relationship_diagrams.py",
)
"""The generators of the diagrams `README.md` embeds. Each SVG is held equal to what its generator
writes, so a generator's string literals are the words the diagrams draw."""


def _retired_claim_drawings(sources: dict[str, str]) -> list[str]:
    """One message per string literal of the generator `sources` that an entry's pattern finds,
    each generator read with its path as `filename`."""
    return [
        f"{label}:{string.line} draws the retired claim {claim.name!r}: {string.text!r}"
        for label, source in sources.items()
        for string in diagram_strings(source, filename=label)
        for claim in RETIRED_CLAIMS
        if claim.pattern.search(string.text)
    ]


def _generator_sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8") for path in GENERATORS
    }


def test_no_diagram_draws_a_retired_claim() -> None:
    """Every string literal the two generators hold, searched with every entry's pattern.

    Written against api-01, whose subtitle sent the SDK and the CLI through the FastAPI contract
    and whose title had the four entrances share the five chains. Fix the generator, then
    regenerate the SVG with the generator itself.
    """
    drawings = _retired_claim_drawings(_generator_sources())
    assert not drawings, "\n".join(drawings)


def test_a_retired_claim_drawn_into_a_generator_is_reported() -> None:
    """Each entry's first retired wording, appended to a generator as a string literal, must be
    reported under that entry, so a drawing check that stopped reading cannot pass."""
    sources = _generator_sources()
    label = GENERATORS[1].relative_to(ROOT).as_posix()
    unreported = [
        claim.name
        for claim in RETIRED_CLAIMS
        if not any(
            f"retired claim {claim.name!r}" in drawing
            for drawing in _retired_claim_drawings(
                {**sources, label: f"{sources[label]}\nPLANTED = {claim.retired[0]!r}\n"}
            )
        )
    ]
    assert not unreported, f"a retired wording drawn into a generator went unreported: {unreported}"
