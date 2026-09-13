"""Claims the code refutes may not come back into the four user-facing documents.

`D13` found each of these in `README.md`, `README.en.md`, `docs/why-openalpha-cn.zh-CN.md` or the
marketing pack at `d4ef5e4`, checked it against the code, and rewrote it. The final review had
verified the first classes itself (its I4); the rest came from its sub-audits and were checked
against the code before anything was rewritten. An entry of `RETIRED_CLAIMS` holds:

- `pattern`: the family of wordings that was retired, searched in every clause of the four
  documents as `tests/prose_clauses.py` reads them;
- `refuted_by`: the code fact that makes those wordings false, with file:line at `d4ef5e4`;
- `retired`: what it retired, verbatim from `d4ef5e4` -- the clause, or the part of it the claim
  sits in -- each of which the pattern must still match, so a pattern cannot be loosened into
  matching nothing;
- `premise`, where the fact is cheap to read off the code: a check that returns a message the day
  the code starts to support the claim, so a claim that has become true is reported as true
  instead of being blocked;
- `paraphrase`: the same claim in words the pattern does not match.

**What it cannot see.** It catches these wordings and nothing else. A pattern is a family of the
phrasings that were retired, not a detector of the claim: the same claim in other words --
another verb, another order, or its halves in two clauses -- passes, and
`test_the_retired_claims_blind_spot_is_real` holds one such paraphrase per entry. A pattern does
not read negation either: "不能分别启停" is read as the claim it denies. A premise reads one fact,
not the whole of `refuted_by`, so a premise that stays quiet does not prove the claim still
false. The embedded diagrams are not read.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from prose_clauses import clauses

from openalpha_cn.agents.committee import DeliberationCommittee
from openalpha_cn.domain.portfolio import PortfolioOrder, PortfolioTransition
from openalpha_cn.domain.report import ResearchReport
from openalpha_cn.runtime.batch import BatchResearchService
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


def _compose_recovery_still_only_restarts() -> str | None:
    source = (ROOT / "scripts" / "verify_compose_recovery.py").read_text(encoding="utf-8")
    only_restarts = (
        '"restart", "openalpha"' in source and '"rm"' not in source and "recreate" not in source
    )
    if only_restarts:
        return None
    return "scripts/verify_compose_recovery.py no longer only restarts the container"


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
            r"(?:独立|分别|单独)(?:启停|开关|关闭)|能单独做对照"
            r"|(?<![A-Za-z])(?:independently|separately|individually)\s+(?:toggl|enabl|disabl|switch)",
            re.IGNORECASE,
        ),
        refuted_by=(
            "DeliberationCommittee.review(*, signal, results) takes no switch and runs the "
            "bull/bear debate and the three risk votes in one call (agents/committee.py:71-183); "
            "OpenAlphaSDK.deliberate passes only those two (sdk.py:236-243), and POST "
            "/api/v1/research/deliberate accepts only signal and agent_results "
            "(api/app.py:314-320). AblationResult(enabled=True) is written, never computed "
            "(agents/committee.py:176-177). Only the whole committee, per call, is optional."
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
        ),
        paraphrase="委员会里的多空辩论和风险视角可以各自打开或关上。",
        premise=_the_committee_still_takes_no_switch,
    ),
    RetiredClaim(
        name="the SDK and the CLI pass through the FastAPI boundary",
        pattern=re.compile(
            rf"(?:SDK|CLI){_NOT_END}*(?:最终)?(?:进入|经过|通过|走)同一\s*FastAPI"
            rf"|四类入口{_NOT_END}*FastAPI"
            r"|(?:SDK|CLI)[^.;]*(?:through|via|behind)\s+(?:the\s+)?(?:same\s+)?FastAPI",
            re.IGNORECASE,
        ),
        refuted_by=(
            "sdk.py imports no HTTP client and no part of openalpha_cn.api: every SDK method calls "
            "a service in process (sdk.py:3-125; run_research at :204-214). cli.py names the REST "
            "app only as the string `openalpha serve` hands to uvicorn (cli.py:1708-1709). Only "
            "REST callers, the React workbench among them, reach the request-size limit and the "
            "security headers create_app installs (api/app.py)."
        ),
        retired=("REST、Python SDK、Typer CLI 和 React 工作台最终进入同一 FastAPI 公共边界。",),
        paraphrase="SDK 与 CLI 的请求同样要过 FastAPI 那道边界。",
        premise=_the_sdk_and_the_cli_still_call_in_process,
    ),
    RetiredClaim(
        name="replay executes the A-share trading rules",
        pattern=re.compile(
            rf"(?:T\+1|整手|停牌|涨跌停){_NOT_END}{{0,30}}(?:也在|进入同一路径)回放"
            rf"|回放{_NOT_END}{{0,12}}(?:继续|也)?执行{_NOT_END}{{0,8}}T\+1"
            rf"|run_cycle`?{_NOT_END}{{0,12}}(?:再叠加|并执行){_NOT_END}{{0,8}}T\+1"
            rf"|无论哪条路径{_NOT_END}{{0,6}}都要经过"
            rf"|回放{_NOT_END}{{0,8}}(?:加入|叠加)\s*T\+1"
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
            rf"拒单{_NOT_END}*关联决策"
            r"|reject\w*[^.;]*(?:linked|tied)\s+to\s+(?:the\s+|its\s+|a\s+)?decision",
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
        ),
        paraphrase="每一笔拒单都能追溯到产生它的那个决策。",
        premise=_a_rejection_still_names_no_decision,
    ),
    RetiredClaim(
        name="the report center holds committee, portfolio and statistical results",
        pattern=re.compile(
            rf"(?:组合执行|统计结果){_NOT_END}{{0,20}}写入报告中心"
            rf"|(?:多日组合|事件统计){_NOT_END}{{0,12}}反馈回报告中心"
            rf"|(?:委员会|风险门|组合){_NOT_END}{{0,16}}结果都能进入报告"
            rf"|(?:委员会|组合验证){_NOT_END}{{0,16}}新结果{_NOT_END}{{0,8}}报告中心"
        ),
        refuted_by=(
            "ResearchReport holds one research run: run_id, subject, created_at, title, summary, "
            "decision_id, signal_id, final_action, evidence_ids and risk_flags "
            "(domain/report.py:25-37), built by ResearchReportFactory.build from a "
            "ResearchRunResult (product/reporting.py:52-55). No committee outcome, portfolio "
            "transition or statistic is a field of it."
        ),
        retired=(
            "研究结论、风险决定、组合执行和统计结果共同写入报告中心，"
            "便于对比不同 Agent 或委员会的增量。",
            "最后，多日组合与事件统计把结果反馈回报告中心。",
            "Agent、双委员会、风险门、组合与统计结果都能进入报告。",
            "Agent、Bull/Bear、风险委员会与组合验证产生的新结果，都能通过报告中心沉淀。",
        ),
        paraphrase="报告中心还会收录组合成交与统计检验。",
        premise=_a_report_still_holds_one_research_run,
    ),
    RetiredClaim(
        name="browser flows are tested at mobile width",
        pattern=re.compile(
            rf"移动(?:浏览器|视口|端)?{_NOT_END}{{0,12}}(?:流程|Playwright)"
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
        ),
        refuted_by=(
            "tests/unit/test_surface_parity.py::PARITY maps 48 routes: 28 have no CLI command "
            "and 11 no SDK method (measured at d4ef5e4), each gap named there; and the React "
            "workbench calls none of the batch, screening, watchlist or report routes (web/src). "
            "The faces share the services each of them calls, not one set of capabilities."
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
        ),
        paraphrase="四个入口能做的事一模一样。",
        premise=_the_workbench_still_skips_the_product_routes,
    ),
    RetiredClaim(
        name="the container recovery check deletes and recreates the container",
        pattern=re.compile(
            r"容器删除、重建|删除、重建后|验证关键状态真的能恢复|(?:deleted|removed) and recreated",
            re.IGNORECASE,
        ),
        refuted_by=(
            "scripts/verify_compose_recovery.py::main (:481-529) brings the stack up, builds one "
            "evidence batch over REST, runs `compose restart openalpha` and checks that the one "
            "evidence_id still comes back. It neither removes nor recreates the container, and "
            "reads no batch, decision or checkpoint."
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
        pattern=re.compile(rf"启动时{_NOT_END}{{0,12}}(?:并|自动)继续(?:处理|执行)"),
        refuted_by=(
            "Every start requeues the interrupted items -- build_storage calls "
            "recover_interrupted (runtime/composition.py:255), which sets each running item back "
            "to queued (storage/batch.py:391-413) -- and nothing runs them until a caller posts "
            "POST /api/v1/research/batches/{batch_id}/retry (api/app.py:2105-2112)."
        ),
        retired=(
            "OpenAlpha CN 将批次、项目、进度和终态保存到 SQLite，启动时识别被中断任务并继续处理"
            "\N{FULLWIDTH SEMICOLON}",
        ),
        paraphrase="进程重启后，没跑完的批量任务会自己接着跑。",
    ),
    RetiredClaim(
        name="the batch task center is still missing or deferred",
        pattern=re.compile(
            rf"(?:仍缺少|缺少)大规模批量任务中心|大规模批量任务中心{_NOT_END}{{0,20}}(?:延后|缺少|缺失)"
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
        pattern=re.compile(r"257\s*项上游功能|51\.36\s*%"),
        refuted_by=(
            "Nothing in the repository gives these counts: "
            "docs/audits/three-upstream-source-audit-20260724.md, the audit the note cites, states "
            "no total, and the feature ledger counts OpenAlpha's own rows "
            "(artifacts/openalpha-v1-feature-coverage/summary.json), not upstream features."
        ),
        retired=("共识别 257 项上游功能，原规划真实覆盖 132 项（51.36%），未审计和未知均为 0。",),
        paraphrase="上游一共有两百多项功能，原计划覆盖了一半左右。",
        premise=_the_upstream_counts_are_still_unsourced,
    ),
    RetiredClaim(
        name="every diagram matches the current source",
        pattern=re.compile(r"每张图都对应当前源码"),
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
        pattern=re.compile(rf"(?:补|实现|提供|具备){_NOT_END}{{0,10}}权限边界"),
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
        name="a model provider is wired in through the SDK and gets schema validation there",
        pattern=re.compile(
            r"wired in through the SDK|在\s*SDK\s*代码中接入模型\s*Provider"
            r"|Schema\s*与重试由治理层",
            re.IGNORECASE,
        ),
        refuted_by=(
            "OpenAlphaSDK.__init__ takes runtime_dir, clock, agents and features, and no provider "
            "(sdk.py:131-137): a model reaches a run only inside an agent passed as agents=. "
            "Schema validation is StructuredSignalAgent.analyze's "
            "StructuredAgentPayload.model_validate (agents/model.py:85-111); the provider only "
            "checks that the reply is a JSON object (models/openai_compatible.py:209-214), and "
            "models/governance.py holds the retry policy, not schema validation (:37-47)."
        ),
        retired=(
            "deterministic operation without an LLM: no shipped path calls a model, and a model "
            "provider wired in through the SDK gets schema validation and bounded retries;",
            "**模型可插拔**：无 LLM 时可确定性运行，出厂路径不调用模型，"
            "在 SDK 代码中接入模型 Provider 后才强制结构化输出、Schema 校验和有界重试。",
            "模型可以在 SDK 代码中通过 OpenAI-compatible BYOK 接入，Schema 与重试由治理层处理，"
            "出厂路径不调用模型。",
        ),
        paraphrase="通过 SDK 挂上的模型 Provider 自带 Schema 校验。",
        premise=_the_sdk_still_takes_no_provider,
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
