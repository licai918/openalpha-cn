"""Generate the five source-grounded public API relationship diagrams."""

# SVG copy intentionally uses Chinese full-width punctuation, and some serialized
# element strings are clearer when kept as one line.
# ruff: noqa: RUF001, E501

from __future__ import annotations

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "assets" / "diagrams"

COLORS = {
    "indigo": "#5968F2",
    "teal": "#0F9F8F",
    "purple": "#7C5CE7",
    "orange": "#E89422",
    "blue": "#1688D4",
    "red": "#D95D67",
    "slate": "#64748B",
    "ink": "#0F172A",
}

NAVIGATION = (
    ("01", "API 全景"),
    ("02", "证据数据"),
    ("03", "研究编排"),
    ("04", "决策产品"),
    ("05", "验证闭环"),
)


class Svg:
    """Small deterministic SVG builder for repository-owned architecture assets."""

    def __init__(self, *, index: int, title: str, subtitle: str) -> None:
        self.index = index
        self.parts = [
            (
                '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" '
                'viewBox="0 0 1440 900" role="img" aria-labelledby="title desc">'
            ),
            f'  <title id="title">{escape(title)}</title>',
            f'  <desc id="desc">{escape(subtitle)}</desc>',
            """  <defs>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">
      <feDropShadow dx="0" dy="7" stdDeviation="9" flood-color="#27324A" flood-opacity=".10" />
    </filter>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L10,5 L0,10 z" fill="context-stroke" />
    </marker>
    <pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.5" fill="#D8DEEB" />
    </pattern>
    <style>
      text { font-family: Inter, "Noto Sans SC", "Microsoft YaHei", Arial, sans-serif; }
      .title { font-size: 36px; font-weight: 760; fill: #0F172A; letter-spacing: -.4px; }
      .subtitle { font-size: 18px; font-weight: 450; fill: #64748B; }
      .section { font-size: 14px; font-weight: 800; letter-spacing: 1.1px; }
      .cardTitle { font-size: 21px; font-weight: 760; fill: #0F172A; }
      .body { font-size: 16px; font-weight: 500; fill: #334155; }
      .small { font-size: 14px; font-weight: 520; fill: #64748B; }
      .endpoint { font-size: 14px; font-weight: 720; fill: #26324A; }
      .pill { font-size: 14px; font-weight: 780; letter-spacing: .35px; }
      .arrowLabel { font-size: 13px; font-weight: 700; fill: #64748B; }
      .navCaption { font-size: 15px; font-weight: 650; fill: #64748B; letter-spacing: .4px; }
      .navNumber { font-size: 15px; font-weight: 800; }
      .navLabel { font-size: 17px; font-weight: 720; }
    </style>
  </defs>""",
            '  <rect width="1440" height="900" fill="#F7F8FC" />',
            '  <rect width="1440" height="900" fill="url(#dots)" opacity=".36" />',
            (f'  <rect x="64" y="42" width="188" height="34" rx="17" fill="{COLORS["indigo"]}" />'),
            (
                f'  <text x="158" y="65" class="pill" text-anchor="middle" '
                f'fill="#FFFFFF">OPENALPHA · API {index:02d}</text>'
            ),
            f'  <text x="64" y="112" class="title">{escape(title)}</text>',
            f'  <text x="64" y="148" class="subtitle">{escape(subtitle)}</text>',
        ]

    def raw(self, value: str) -> None:
        self.parts.append(value)

    def section(self, *, x: int, y: int, text: str, color: str) -> None:
        self.raw(f'  <text x="{x}" y="{y}" class="section" fill="{color}">{escape(text)}</text>')

    def card(
        self,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        title: str,
        lines: tuple[str, ...],
        color: str,
        endpoint: str | None = None,
        fill: str = "#FFFFFF",
    ) -> None:
        self.raw(
            f'  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="18" '
            f'fill="{fill}" stroke="#D9DFEA" filter="url(#shadow)" />'
        )
        self.raw(f'  <rect x="{x}" y="{y}" width="7" height="{height}" rx="3.5" fill="{color}" />')
        self.raw(f'  <text x="{x + 24}" y="{y + 32}" class="cardTitle">{escape(title)}</text>')
        cursor = y + 60
        if endpoint:
            self.raw(
                f'  <rect x="{x + 22}" y="{cursor - 18}" width="{width - 42}" '
                f'height="27" rx="9" fill="{color}" opacity=".10" />'
            )
            self.raw(
                f'  <text x="{x + 32}" y="{cursor + 1}" class="endpoint">{escape(endpoint)}</text>'
            )
            cursor += 36
        for line in lines:
            self.raw(f'  <text x="{x + 24}" y="{cursor}" class="body">{escape(line)}</text>')
            cursor += 25

    def pill(
        self,
        *,
        x: int,
        y: int,
        width: int,
        text: str,
        color: str,
        fill: str = "#FFFFFF",
    ) -> None:
        self.raw(
            f'  <rect x="{x}" y="{y}" width="{width}" height="36" rx="18" '
            f'fill="{fill}" stroke="{color}" />'
        )
        self.raw(
            f'  <text x="{x + width / 2:g}" y="{y + 24}" class="pill" '
            f'text-anchor="middle" fill="{color}">{escape(text)}</text>'
        )

    def arrow(
        self,
        *,
        path: str,
        color: str = COLORS["indigo"],
        dashed: bool = False,
        label: str | None = None,
        label_x: int = 0,
        label_y: int = 0,
    ) -> None:
        dash = ' stroke-dasharray="8 7"' if dashed else ""
        self.raw(
            f'  <path d="{path}" fill="none" stroke="{color}" stroke-width="3"'
            f'{dash} marker-end="url(#arrow)" />'
        )
        if label:
            self.raw(
                f'  <text x="{label_x}" y="{label_y}" class="arrowLabel" '
                f'text-anchor="middle">{escape(label)}</text>'
            )

    def legend(self, *, y: int = 732) -> None:
        self.raw(
            f'  <path d="M 72 {y} L 126 {y}" fill="none" stroke="{COLORS["indigo"]}" '
            'stroke-width="3" marker-end="url(#arrow)" />'
        )
        self.raw(f'  <text x="140" y="{y + 5}" class="small">服务端自动调用 / 持久化</text>')
        self.raw(
            f'  <path d="M 380 {y} L 434 {y}" fill="none" stroke="{COLORS["orange"]}" '
            'stroke-width="3" stroke-dasharray="8 7" marker-end="url(#arrow)" />'
        )
        self.raw(f'  <text x="448" y="{y + 5}" class="small">调用方显式组合 / 反馈</text>')
        self.raw(
            f'  <rect x="746" y="{y - 15}" width="16" height="16" rx="4" '
            f'fill="{COLORS["slate"]}" opacity=".16" stroke="{COLORS["slate"]}" />'
        )
        self.raw(f'  <text x="774" y="{y + 5}" class="small">SQLite / Parquet 持久状态</text>')
        self.raw(f'  <text x="1060" y="{y + 5}" class="small">边界：不连接实盘券商</text>')

    def finish(self) -> str:
        self.raw(
            '  <text x="64" y="780" class="navCaption">API 关系导航｜'
            "从数据合同到验证反馈的五层公开能力</text>"
        )
        start_x = 64
        width = 216
        gap = 48
        for position, (number, label) in enumerate(NAVIGATION, start=1):
            x = start_x + (position - 1) * (width + gap)
            active = position == self.index
            fill = COLORS["indigo"] if active else "#FFFFFF"
            stroke = COLORS["indigo"] if active else "#D7DDE8"
            number_color = "#FFFFFF" if active else COLORS["slate"]
            label_color = "#FFFFFF" if active else COLORS["ink"]
            shadow = ' filter="url(#shadow)"' if active else ""
            self.raw(
                f'  <rect x="{x}" y="802" width="{width}" height="58" rx="18" '
                f'fill="{fill}" stroke="{stroke}"{shadow} />'
            )
            self.raw(
                f'  <text x="{x + 34}" y="838" class="navNumber" text-anchor="middle" '
                f'fill="{number_color}">{number}</text>'
            )
            self.raw(
                f'  <text x="{x + 70}" y="837" class="navLabel" '
                f'fill="{label_color}">{escape(label)}</text>'
            )
            if position < len(NAVIGATION):
                self.arrow(
                    path=f"M {x + width + 6} 831 L {x + width + gap - 12} 831",
                    color=COLORS["indigo"] if active else "#C5CDDA",
                )
        self.raw("</svg>")
        return "\n".join(self.parts) + "\n"


def landscape() -> str:
    svg = Svg(
        index=1,
        title="API 全景｜四类入口共享五条功能链",
        subtitle="REST、SDK、CLI 与 React 工作台通过同一 FastAPI 合同进入证据、研究、产品、组合与验证能力。",
    )
    svg.section(x=64, y=194, text="调用入口", color=COLORS["slate"])
    for y, label in (
        (220, "REST / OpenAPI"),
        (284, "Python SDK"),
        (348, "Typer CLI"),
        (412, "React 工作台"),
    ):
        svg.pill(x=64, y=y, width=196, text=label, color=COLORS["indigo"])
    svg.card(
        x=302,
        y=220,
        width=250,
        height=330,
        title="FastAPI 公共边界",
        endpoint="GET /health · /docs · /openapi.json",
        lines=(
            "Pydantic 严格 Schema",
            "8 MiB 默认请求上限",
            "安全响应头",
            "本机 CORS 白名单",
            "HTTP 只接结构化记录",
            "版本前缀 /api/v1",
        ),
        color=COLORS["indigo"],
    )
    svg.arrow(path="M 262 350 L 294 350")
    svg.section(x=606, y=194, text="五条公开功能链", color=COLORS["slate"])
    lanes = (
        (
            210,
            "证据链",
            "POST /api/v1/evidence/build",
            "PIT 快照 → Parquet → 查询",
            COLORS["teal"],
        ),
        (
            302,
            "研究链",
            "POST /api/v1/research/run",
            "路由 → Agent → Signal → 风险门",
            COLORS["purple"],
        ),
        (
            394,
            "研究产品链",
            "POST /api/v1/screen · /reports",
            "筛选 → 观察池 → 不可变报告",
            COLORS["orange"],
        ),
        (
            486,
            "组合链",
            "POST /api/v1/portfolio/execute",
            "A 股约束 → 转移 → 不可变账本",
            COLORS["red"],
        ),
        (
            578,
            "验证链",
            "POST /api/v1/backtests/replay",
            "同路径回放 → 统计 → 归因",
            COLORS["blue"],
        ),
    )
    for y, title, endpoint, line, color in lanes:
        svg.raw(
            f'  <rect x="628" y="{y}" width="674" height="78" rx="18" '
            'fill="#FFFFFF" stroke="#D9DFEA" filter="url(#shadow)" />'
        )
        svg.raw(f'  <rect x="628" y="{y}" width="7" height="78" rx="3.5" fill="{color}" />')
        svg.raw(f'  <text x="652" y="{y + 31}" class="cardTitle">{escape(title)}</text>')
        svg.raw(f'  <text x="836" y="{y + 29}" class="endpoint">{escape(endpoint)}</text>')
        svg.raw(f'  <text x="836" y="{y + 56}" class="body">{escape(line)}</text>')
        svg.arrow(path=f"M 554 385 C 590 385, 584 {y + 39}, 620 {y + 39}", color=color)
    svg.card(
        x=1040,
        y=650,
        width=262,
        height=70,
        title="持久状态",
        lines=("Parquet + SQLite WAL",),
        color=COLORS["slate"],
        fill="#F2F4F8",
    )
    svg.arrow(
        path="M 960 656 C 960 700, 1000 685, 1032 685",
        color=COLORS["slate"],
        label="证据 / 运行 / 账本",
        label_x=930,
        label_y=704,
    )
    svg.legend()
    return svg.finish()


def evidence_dataflow() -> str:
    svg = Svg(
        index=2,
        title="证据数据链｜Provider 数据如何变成可研究事实",
        subtitle="先在调用侧取得合法数据，再以 ProviderMetadata + ProviderBatch 进入统一构建、四时钟校验与内容寻址。",
    )
    svg.section(x=64, y=194, text="调用方 / Provider 侧", color=COLORS["teal"])
    for y, label in (
        (220, "链邻 API · 用户授权"),
        (270, "CSV / JSONL / Parquet"),
        (320, "Tushare · 用户 Token"),
        (370, "AKShare · 可选研究"),
    ):
        svg.pill(x=64, y=y, width=246, text=label, color=COLORS["teal"])
    svg.card(
        x=352,
        y=220,
        width=252,
        height=220,
        title="统一 Provider 合同",
        lines=(
            "ProviderMetadata",
            "ProviderBatch",
            "来源 / 许可 / 修订",
            "显式失败 / no_data_reason",
            "服务端不自动抓取数据",
        ),
        color=COLORS["teal"],
    )
    svg.arrow(path="M 312 330 L 344 330", color=COLORS["teal"])
    svg.raw(
        '  <path d="M 628 188 L 628 590" fill="none" stroke="#B9C2D0" '
        'stroke-width="2" stroke-dasharray="6 7" />'
    )
    svg.raw('  <text x="640" y="208" class="small">OpenAlpha HTTP 服务端边界</text>')
    svg.card(
        x=670,
        y=220,
        width=300,
        height=220,
        title="构建与校验",
        endpoint="POST /api/v1/evidence/build",
        lines=(
            "Schema / aware datetime",
            "四时钟 PIT",
            "涨停 / 炸板 / 连板规范化",
            "题材 / 催化 / 公告 / 资金",
        ),
        color=COLORS["indigo"],
    )
    svg.arrow(
        path="M 606 330 L 662 330",
        color=COLORS["indigo"],
    )
    svg.card(
        x=1012,
        y=220,
        width=352,
        height=220,
        title="不可变 EvidenceSnapshot",
        lines=(
            "evidence_id + content_hash",
            "event / available / ingested / revision",
            "source_id / URI / license",
            "只保留 as_of 时刻可见事实",
        ),
        color=COLORS["purple"],
    )
    svg.arrow(path="M 972 330 L 1004 330", color=COLORS["purple"])
    svg.card(
        x=352,
        y=500,
        width=252,
        height=162,
        title="查询视图",
        lines=(
            "GET /api/v1/evidence",
            "GET /api/v1/market/events",
            "GET /api/v1/themes",
        ),
        color=COLORS["blue"],
    )
    svg.card(
        x=670,
        y=500,
        width=300,
        height=162,
        title="PIT 证据存储",
        lines=("ParquetEvidenceStore", "DuckDB as_of 查询", "追加写入 · 防前视"),
        color=COLORS["slate"],
        fill="#F2F4F8",
    )
    svg.arrow(path="M 1188 446 C 1188 476, 820 470, 820 492", color=COLORS["slate"])
    svg.arrow(path="M 662 580 L 612 580", color=COLORS["blue"])
    svg.card(
        x=1012,
        y=500,
        width=352,
        height=162,
        title="下游研究输入",
        lines=(
            "ResearchRunRequest.evidence",
            "subject / as_of 一致性",
            "Evidence ID 全链引用",
        ),
        color=COLORS["purple"],
    )
    svg.arrow(
        path="M 972 580 L 1004 580",
        color=COLORS["orange"],
        dashed=True,
    )
    svg.legend()
    return svg.finish()


def research_orchestration() -> str:
    svg = Svg(
        index=3,
        title="研究编排链｜单次与批量 API 汇入同一 run_cycle",
        subtitle="无论 live、replay 还是 backtest，研究请求都经过同一证据路由、Agent 聚合、风险门和持久化路径。",
    )
    svg.card(
        x=64,
        y=214,
        width=290,
        height=160,
        title="单次研究",
        endpoint="POST /api/v1/research/run",
        lines=("ResearchApiRequest", "验证序列化 evidence_id", "同步返回完整结果"),
        color=COLORS["purple"],
    )
    svg.card(
        x=64,
        y=414,
        width=290,
        height=220,
        title="持久批量研究",
        endpoint="POST /api/v1/research/batches",
        lines=(
            "1–1000 个不可变请求",
            "1–32 并发",
            "events / cancel / retry",
            "SQLite 状态与重启恢复",
            "逐项复用同一 runner",
        ),
        color=COLORS["indigo"],
    )
    svg.card(
        x=410,
        y=246,
        width=300,
        height=352,
        title="ResearchEngine.run_cycle",
        lines=(
            "1  校验 subject / as_of / PIT",
            "2  AgentRouter 按证据族路由",
            "3  节点级 checkpoint / resume",
            "4  聚合结构化 SignalFrame",
            "5  RiskGate：pass / reduce / block",
            "6  final_action：watch / avoid / abstain",
        ),
        color=COLORS["purple"],
    )
    svg.arrow(path="M 356 294 L 402 294", color=COLORS["purple"])
    svg.arrow(
        path="M 356 520 C 386 520, 380 470, 402 470",
        color=COLORS["indigo"],
    )
    for y, title, lines, color in (
        (
            206,
            "证据感知 Agent",
            ("市场事件", "题材催化", "资金观察"),
            COLORS["teal"],
        ),
        (
            382,
            "结果聚合与风险",
            ("SignalFrame", "显式弃权", "RiskGate"),
            COLORS["orange"],
        ),
        (
            558,
            "ResearchRunResult",
            ("signal + decision", "manifest + agent_results"),
            COLORS["blue"],
        ),
    ):
        svg.card(
            x=770,
            y=y,
            width=280,
            height=140,
            title=title,
            lines=lines,
            color=color,
        )
    svg.arrow(path="M 712 330 L 762 276", color=COLORS["teal"])
    svg.arrow(path="M 910 348 L 910 374", color=COLORS["purple"])
    svg.arrow(path="M 910 524 L 910 550", color=COLORS["blue"])
    svg.card(
        x=1098,
        y=206,
        width=266,
        height=316,
        title="自动持久化",
        lines=(
            "RunManifest",
            "DecisionLedger",
            "ResearchMemory",
            "RunRecoveryState",
            "请求摘要 / 图签名隔离",
            "SQLite WAL",
        ),
        color=COLORS["slate"],
        fill="#F2F4F8",
    )
    svg.arrow(path="M 1052 610 C 1076 610, 1072 510, 1090 510", color=COLORS["slate"])
    svg.card(
        x=1098,
        y=562,
        width=266,
        height=100,
        title="可选委员会 API",
        endpoint="POST /api/v1/research/deliberate",
        lines=("调用方显式传入 signal + agent_results",),
        color=COLORS["orange"],
    )
    svg.arrow(
        path="M 1052 650 L 1090 650",
        color=COLORS["orange"],
        dashed=True,
    )
    svg.legend()
    return svg.finish()


def decision_products() -> str:
    svg = Svg(
        index=4,
        title="决策产品链｜研究结果如何转化为可复用资产",
        subtitle="ResearchRunResult 是公共交汇点；委员会、筛选、报告、观察池和组合 API 由调用方按业务目的显式组合。",
    )
    svg.card(
        x=64,
        y=290,
        width=274,
        height=212,
        title="可信研究结果",
        lines=(
            "SignalFrame",
            "DecisionLedger",
            "RunManifest",
            "AgentResult[]",
            "内容派生 ID 可复核",
        ),
        color=COLORS["purple"],
    )
    svg.card(
        x=392,
        y=190,
        width=302,
        height=180,
        title="双委员会",
        endpoint="POST /api/v1/research/deliberate",
        lines=("Bull / Bear 证据案例", "激进 / 中性 / 保守票决", "adjusted_signal + ablation"),
        color=COLORS["orange"],
    )
    svg.card(
        x=392,
        y=420,
        width=302,
        height=180,
        title="结构化筛选",
        endpoint="POST /api/v1/screen",
        lines=("先重建并核验研究结果", "显式 ScreeningCriteria", "返回排序与排除原因"),
        color=COLORS["blue"],
    )
    svg.arrow(path="M 340 370 C 366 370, 360 280, 384 280", color=COLORS["orange"], dashed=True)
    svg.arrow(path="M 340 422 C 366 422, 360 510, 384 510", color=COLORS["blue"], dashed=True)
    svg.card(
        x=748,
        y=190,
        width=286,
        height=180,
        title="不可变报告",
        endpoint="POST /api/v1/reports",
        lines=("完整性校验", "证据 ID 关联", "content-derived report_id"),
        color=COLORS["teal"],
    )
    svg.card(
        x=748,
        y=420,
        width=286,
        height=180,
        title="持久观察池",
        endpoint="POST /api/v1/watchlist",
        lines=("调用方选择入池标的", "GET 列表 / remove", "SQLite 本地持久化"),
        color=COLORS["teal"],
    )
    svg.arrow(
        path="M 340 320 C 360 180, 650 170, 720 170 L 720 280 L 740 280",
        color=COLORS["teal"],
        dashed=True,
    )
    svg.arrow(
        path="M 696 510 L 740 510",
        color=COLORS["teal"],
        dashed=True,
        label="显式选择入池",
        label_x=718,
        label_y=492,
    )
    svg.card(
        x=1088,
        y=190,
        width=276,
        height=180,
        title="组合执行输入",
        lines=("PortfolioState", "PortfolioOrder", "MarketBar + Limits", "研究结果不会自动下单"),
        color=COLORS["red"],
    )
    svg.card(
        x=1088,
        y=420,
        width=276,
        height=180,
        title="A 股组合转移",
        endpoint="POST /api/v1/portfolio/execute",
        lines=(
            "T+1 / 整手 / 停牌 / 涨跌停",
            "费用 / FIFO / 敞口限制",
            "不可变 PortfolioTransition",
        ),
        color=COLORS["red"],
    )
    svg.arrow(
        path="M 340 300 C 350 156, 1048 156, 1080 260",
        color=COLORS["orange"],
        dashed=True,
        label="调用方基于研究结果构造订单",
        label_x=804,
        label_y=172,
    )
    svg.arrow(path="M 1226 372 L 1226 412", color=COLORS["red"])
    svg.card(
        x=392,
        y=642,
        width=972,
        height=66,
        title="持久研究资产",
        lines=("GET /reports · GET /watchlist · GET /portfolio/ledger · GET /memory/{subject}",),
        color=COLORS["slate"],
        fill="#F2F4F8",
    )
    svg.arrow(path="M 890 602 L 890 634", color=COLORS["slate"])
    svg.arrow(path="M 1226 602 L 1226 634", color=COLORS["slate"])
    svg.legend(y=746)
    return svg.finish()


def validation_loop() -> str:
    svg = Svg(
        index=5,
        title="验证闭环｜回放、统计与归因怎样反哺下一轮研究",
        subtitle="四类验证 API 各自回答可复现性、组合表现、事件显著性和结果归因；反馈由研究者显式采纳，不自动训练模型。",
    )
    columns = (
        (
            64,
            "同路径回放",
            "POST /api/v1/backtests/replay",
            ("冻结 ReplayCorpus", "ResearchEngine.run_cycle", "确定性 / 防前视报告"),
            COLORS["purple"],
        ),
        (
            394,
            "多日组合",
            "POST /api/v1/backtests/portfolio",
            ("initial + ordered steps", "A 股执行 + 不可变账本", "收益 / 基准 / 换手 / 暴露"),
            COLORS["red"],
        ),
        (
            724,
            "事件显著性",
            "POST /api/v1/backtests/event-study",
            ("事件与基准收益", "CAR · t 统计量", "seeded Bootstrap CI"),
            COLORS["blue"],
        ),
        (
            1054,
            "结果归因",
            "POST /api/v1/backtests/validate",
            ("研究结果 + 未来观察", "重算 signal / decision ID", "规则 / 因子 / Agent 归因"),
            COLORS["teal"],
        ),
    )
    for x, title, endpoint, lines, color in columns:
        svg.card(
            x=x,
            y=220,
            width=286,
            height=252,
            title=title,
            endpoint=endpoint,
            lines=lines,
            color=color,
        )
    svg.card(
        x=128,
        y=540,
        width=1184,
        height=116,
        title="验证结果汇总层",
        lines=(
            "ReplayReport + PortfolioBacktestReport + EventStudyReport + ValidationResult",
            "共同回答：当时是否可知？是否可成交？是否显著？哪条规则、因子或 Agent 贡献了结果？",
        ),
        color=COLORS["indigo"],
        fill="#EEF1FF",
    )
    for x, _, _, _, color in columns:
        svg.arrow(path=f"M {x + 143} 474 L {x + 143} 532", color=color)
    svg.card(
        x=394,
        y=670,
        width=652,
        height=62,
        title="下一轮显式改进",
        lines=("人工调整数据质量、路由、风险阈值和筛选；不自动训练模型",),
        color=COLORS["orange"],
    )
    svg.arrow(
        path="M 720 658 L 720 662",
        color=COLORS["orange"],
        dashed=True,
    )
    svg.legend(y=754)
    return svg.finish()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    diagrams = {
        "openalpha-api-01-landscape.svg": landscape(),
        "openalpha-api-02-evidence-dataflow.svg": evidence_dataflow(),
        "openalpha-api-03-research-orchestration.svg": research_orchestration(),
        "openalpha-api-04-decision-products.svg": decision_products(),
        "openalpha-api-05-validation-loop.svg": validation_loop(),
    }
    for name, content in diagrams.items():
        (OUTPUT_DIR / name).write_text(content, encoding="utf-8")
    print(f"generated {len(diagrams)} API relationship diagrams in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
