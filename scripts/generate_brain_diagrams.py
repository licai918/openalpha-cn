"""Generate the five source-grounded OpenAlpha CN research-system diagrams."""

# SVG copy intentionally uses Chinese full-width punctuation.
# ruff: noqa: RUF001, E501

from __future__ import annotations

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "assets" / "diagrams"

BG = "#07111E"
PANEL = "#0C1928"
PANEL_ALT = "#102033"
PANEL_SOFT = "#13243A"
LINE = "#263A52"
TEXT = "#F4F7FB"
MUTED = "#96A8BE"
CYAN = "#2DD4BF"
BLUE = "#38BDF8"
VIOLET = "#A78BFA"
AMBER = "#FBBF24"
CORAL = "#FB7185"
LIME = "#A3E635"

NAVIGATION = (
    ("01", "系统总览"),
    ("02", "证据平面"),
    ("03", "研究编排"),
    ("04", "决策约束"),
    ("05", "验证反馈"),
)


class Svg:
    """Small deterministic builder for a cohesive five-diagram SVG system."""

    def __init__(
        self,
        *,
        index: int,
        eyebrow: str,
        title: str,
        subtitle: str,
        accent: str,
    ) -> None:
        self.index = index
        self.accent = accent
        self.parts = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900" role="img" aria-labelledby="title desc">',
            f'  <title id="title">{escape(title)}</title>',
            f'  <desc id="desc">{escape(subtitle)}</desc>',
            """  <defs>
    <linearGradient id="bgGradient" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#091727" />
      <stop offset=".48" stop-color="#07111E" />
      <stop offset="1" stop-color="#050C16" />
    </linearGradient>
    <radialGradient id="ambient" cx="0" cy="0" r="1" gradientTransform="translate(1160 40) rotate(135) scale(600 420)">
      <stop offset="0" stop-color="ACCENT" stop-opacity=".16" />
      <stop offset=".62" stop-color="ACCENT" stop-opacity=".025" />
      <stop offset="1" stop-color="ACCENT" stop-opacity="0" />
    </radialGradient>
    <linearGradient id="activeNav" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="ACCENT" stop-opacity=".30" />
      <stop offset="1" stop-color="ACCENT" stop-opacity=".08" />
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="150%" height="170%">
      <feDropShadow dx="0" dy="16" stdDeviation="20" flood-color="#000000" flood-opacity=".34" />
    </filter>
    <filter id="softGlow" x="-100%" y="-100%" width="300%" height="300%">
      <feGaussianBlur stdDeviation="7" result="blur" />
      <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
    </filter>
    <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="#6D87A3" stroke-width=".55" opacity=".12" />
    </pattern>
    <marker id="arrow" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto" markerUnits="strokeWidth">
      <path d="M0 0L11 5.5L0 11Z" fill="context-stroke" />
    </marker>
    <style>
      text { font-family: Geist, "Plus Jakarta Sans", "Noto Sans SC", "Microsoft YaHei", sans-serif; }
      .eyebrow { font-size: 13px; font-weight: 760; letter-spacing: 1.8px; }
      .title { font-size: 34px; font-weight: 760; letter-spacing: -.5px; }
      .subtitle { font-size: 16px; font-weight: 480; }
      .micro { font-size: 11px; font-weight: 760; letter-spacing: 1.25px; }
      .cardTitle { font-size: 20px; font-weight: 720; }
      .body { font-size: 14px; font-weight: 490; }
      .small { font-size: 12px; font-weight: 520; }
      .mono { font-size: 12px; font-weight: 680; font-family: Geist Mono, "Noto Sans Mono CJK SC", monospace; }
      .metric { font-size: 28px; font-weight: 780; letter-spacing: -.5px; }
      .nav { font-size: 13px; font-weight: 680; }
      .navNum { font-size: 12px; font-weight: 800; }
    </style>
  </defs>""".replace("ACCENT", accent),
            f'  <rect width="1440" height="900" fill="{BG}" />',
            '  <rect width="1440" height="900" fill="url(#bgGradient)" />',
            '  <rect width="1440" height="900" fill="url(#ambient)" />',
            '  <rect width="1440" height="900" fill="url(#grid)" />',
            f'  <rect x="64" y="42" width="42" height="4" rx="2" fill="{accent}" filter="url(#softGlow)" />',
            f'  <text x="64" y="72" class="eyebrow" fill="{accent}">OPENALPHA CN · RESEARCH OS / {index:02d}</text>',
            f'  <text x="64" y="115" class="title" fill="{TEXT}">{escape(eyebrow)}｜{escape(title)}</text>',
            f'  <text x="64" y="148" class="subtitle" fill="{MUTED}">{escape(subtitle)}</text>',
            '  <g transform="translate(1210 50)" opacity=".92">',
            f'    <circle cx="44" cy="30" r="28" fill="{accent}" opacity=".09" />',
            f'    <path d="M18 42L40 20L56 32L75 11" fill="none" stroke="{accent}" stroke-width="2.2" />',
            f'    <circle cx="18" cy="42" r="4" fill="{accent}" /><circle cx="40" cy="20" r="4" fill="{accent}" />',
            f'    <circle cx="56" cy="32" r="4" fill="{accent}" /><circle cx="75" cy="11" r="4" fill="{accent}" />',
            "  </g>",
        ]

    def raw(self, value: str) -> None:
        self.parts.append(value)

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        css: str = "body",
        color: str = TEXT,
        anchor: str = "start",
        opacity: float = 1,
    ) -> None:
        self.raw(
            f'  <text x="{x}" y="{y}" class="{css}" fill="{color}" text-anchor="{anchor}" opacity="{opacity}">{escape(value)}</text>'
        )

    def lines(
        self,
        x: float,
        y: float,
        values: tuple[str, ...],
        *,
        gap: int = 25,
        css: str = "body",
        color: str = MUTED,
    ) -> None:
        for offset, value in enumerate(values):
            self.text(x, y + offset * gap, value, css=css, color=color)

    def pill(
        self,
        x: float,
        y: float,
        width: float,
        value: str,
        *,
        color: str,
        fill_opacity: float = 0.12,
        text_color: str | None = None,
    ) -> None:
        self.raw(
            f'  <rect x="{x}" y="{y}" width="{width}" height="28" rx="14" fill="{color}" fill-opacity="{fill_opacity}" stroke="{color}" stroke-opacity=".38" />'
        )
        self.text(
            x + width / 2,
            y + 19,
            value,
            css="small",
            color=text_color or color,
            anchor="middle",
        )

    def panel(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        title: str,
        label: str,
        color: str,
        fill: str = PANEL,
        lines: tuple[str, ...] = (),
        title_size: str = "cardTitle",
    ) -> None:
        self.raw(
            f'  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="22" fill="{fill}" stroke="{LINE}" filter="url(#shadow)" />'
        )
        self.raw(
            f'  <rect x="{x + 1}" y="{y + 1}" width="{width - 2}" height="{height - 2}" rx="21" fill="none" stroke="#FFFFFF" stroke-opacity=".035" />'
        )
        self.raw(
            f'  <rect x="{x}" y="{y + 22}" width="3" height="{height - 44}" rx="1.5" fill="{color}" />'
        )
        self.text(x + 24, y + 29, label.upper(), css="micro", color=color)
        self.text(x + 24, y + 62, title, css=title_size, color=TEXT)
        self.lines(x + 24, y + 91, lines, gap=24, css="body", color=MUTED)

    def arrow(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str,
        dashed: bool = False,
        label: str | None = None,
        label_x: float | None = None,
        label_y: float | None = None,
    ) -> None:
        dash = ' stroke-dasharray="8 8"' if dashed else ""
        self.raw(
            f'  <path d="M{x1} {y1}L{x2} {y2}" fill="none" stroke="{color}" stroke-width="2.2"{dash} marker-end="url(#arrow)" />'
        )
        if label:
            self.text(
                label_x if label_x is not None else (x1 + x2) / 2,
                label_y if label_y is not None else (y1 + y2) / 2 - 9,
                label,
                css="small",
                color=color,
                anchor="middle",
            )

    def path(
        self,
        d: str,
        *,
        color: str,
        dashed: bool = False,
        arrow: bool = True,
        opacity: float = 1,
    ) -> None:
        dash = ' stroke-dasharray="8 8"' if dashed else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        self.raw(
            f'  <path d="{d}" fill="none" stroke="{color}" stroke-width="2.2"{dash}{marker} opacity="{opacity}" />'
        )

    def section_label(self, x: float, y: float, number: str, value: str, color: str) -> None:
        self.raw(
            f'  <circle cx="{x}" cy="{y - 4}" r="14" fill="{color}" fill-opacity=".16" stroke="{color}" stroke-opacity=".45" />'
        )
        self.text(x, y, number, css="navNum", color=color, anchor="middle")
        self.text(x + 24, y, value, css="micro", color=color)

    def legend(self) -> None:
        self.raw(
            f'  <line x1="898" y1="171" x2="938" y2="171" stroke="{CYAN}" stroke-width="2.2" marker-end="url(#arrow)" />'
        )
        self.text(948, 175, "自动执行 / 持久化", css="small", color=MUTED)
        self.raw(
            f'  <line x1="1108" y1="171" x2="1148" y2="171" stroke="{AMBER}" stroke-width="2.2" stroke-dasharray="7 7" marker-end="url(#arrow)" />'
        )
        self.text(1158, 175, "显式组合 / 人工反馈", css="small", color=MUTED)

    def navigation(self) -> None:
        self.text(64, 785, "研究闭环导航", css="micro", color=MUTED)
        self.text(
            200,
            785,
            "每一脑区交付一个可验证产物，最终回到下一轮证据与规则",
            css="small",
            color="#6F829A",
        )
        start_x = 64
        width = 240
        gap = 16
        y = 806
        for position, (number, label) in enumerate(NAVIGATION, start=1):
            x = start_x + (position - 1) * (width + gap)
            if position == self.index:
                self.raw(
                    f'  <rect x="{x}" y="{y}" width="{width}" height="52" rx="16" fill="url(#activeNav)" stroke="{self.accent}" stroke-opacity=".64" />'
                )
                number_color = self.accent
                label_color = TEXT
            else:
                self.raw(
                    f'  <rect x="{x}" y="{y}" width="{width}" height="52" rx="16" fill="#0B1726" stroke="{LINE}" />'
                )
                number_color = "#647890"
                label_color = "#B7C4D4"
            self.text(x + 22, y + 32, number, css="navNum", color=number_color)
            self.text(x + 55, y + 32, label, css="nav", color=label_color)
            if position < len(NAVIGATION):
                self.raw(
                    f'  <path d="M{x + width + 5} {y + 26}H{x + width + gap - 5}" stroke="{self.accent if position == self.index else LINE}" stroke-width="1.7" marker-end="url(#arrow)" />'
                )

    def finish(self, filename: str) -> None:
        self.navigation()
        self.raw("</svg>")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / filename).write_text("\n".join(self.parts) + "\n", encoding="utf-8")


def overview() -> None:
    svg = Svg(
        index=1,
        eyebrow="研究操作系统总览",
        title="从 A 股事实到可复核结论，闭合整条研究链",
        subtitle="不是角色堆叠，而是以证据 ID、显式边界和不可变账本连接数据、研究、约束与验证。",
        accent=BLUE,
    )
    svg.legend()
    svg.section_label(76, 214, "A", "数据与治理底座", CYAN)
    svg.panel(
        64,
        232,
        275,
        438,
        title="A 股证据入口",
        label="DATA PLANE",
        color=CYAN,
        lines=(
            "链邻数据接口 API",
            "已实现 · 统一替代入口",
            "用户授权实时 A 股数据",
            "用户自有 CSV / JSONL / Parquet",
            "Provider 合同 · 鉴权 · 限流",
            "来源 / 许可 / 修订语义",
        ),
    )
    svg.pill(88, 624, 220, "输入不等于证据", color=CYAN)

    svg.section_label(385, 214, "B", "五级专业流水线", VIOLET)
    stages = [
        (
            374,
            244,
            322,
            174,
            "01",
            "证据成像",
            "EvidenceSnapshot",
            ("四时钟 PIT", "内容寻址 · 来源与许可"),
            CYAN,
        ),
        (
            716,
            244,
            322,
            174,
            "02",
            "研究编排",
            "ResearchRunResult",
            ("持久批量队列 · 1–32 并发", "ResearchEngine.run_cycle"),
            VIOLET,
        ),
        (
            374,
            440,
            322,
            174,
            "03",
            "决策约束",
            "DeliberationOutcome",
            ("Bull / Bear · 三态风控", "显式弃权 · A 股交易约束"),
            AMBER,
        ),
        (
            716,
            440,
            322,
            174,
            "04",
            "验证归因",
            "ValidationResult",
            ("同路径回放 · 事件统计", "组合报告 · 结构化筛选"),
            BLUE,
        ),
    ]
    for x, y, width, height, number, label, title, lines, color in stages:
        svg.panel(
            x, y, width, height, title=title, label=f"{number} / {label}", color=color, lines=lines
        )
    svg.arrow(696, 331, 716, 331, color=VIOLET)
    svg.path("M877 418V430H535V440", color=AMBER)
    svg.arrow(696, 527, 716, 527, color=BLUE)
    svg.path("M877 614V650H535V625", color=AMBER, dashed=True)
    svg.pill(566, 636, 312, "统计证据反哺规则 · 不自动训练模型", color=AMBER)
    svg.arrow(339, 451, 374, 451, color=CYAN, label="规范化", label_y=435)

    svg.section_label(1081, 214, "C", "系统级保证", CORAL)
    svg.panel(
        1070,
        232,
        306,
        274,
        title="可复现与可审计",
        label="CONTROL PLANE",
        color=CORAL,
        lines=(
            "RunManifest / DecisionLedger",
            "Checkpoint · SQLite WAL",
            "Retry / Recovery · 成本账本",
            "证据不足时显式 abstain",
            "API / SDK / CLI / Web 同契约",
        ),
    )
    svg.panel(
        1070,
        526,
        306,
        144,
        title="清晰的责任边界",
        label="GOVERNANCE",
        color=AMBER,
        lines=("研究系统，不自动下单", "调用方显式组合委员会与组合账本"),
    )
    svg.finish("openalpha-brain-01-overview.svg")


def evidence() -> None:
    svg = Svg(
        index=2,
        eyebrow="证据平面",
        title="先证明“当时可知”，再讨论模型是否聪明",
        subtitle="所有研究结论先经过授权、限流、四时钟与内容寻址，得到可追溯、可回放的 EvidenceSnapshot。",
        accent=CYAN,
    )
    svg.legend()
    svg.section_label(76, 214, "A", "授权输入", CYAN)
    svg.panel(
        64,
        232,
        250,
        190,
        title="链邻数据接口 API",
        label="LICENSED DATA",
        color=CYAN,
        lines=("已实现 · 统一替代入口", "Bearer 鉴权 · 客户端限流", "时效 / 精度以链邻服务为准"),
    )
    svg.panel(
        64,
        440,
        250,
        150,
        title="用户自有文件",
        label="OWNED DATA",
        color=BLUE,
        lines=("CSV · JSON · JSONL · Parquet", "来源与许可元数据随批次进入"),
    )
    svg.pill(81, 612, 216, "失败显式 · 禁止空成功", color=CORAL)

    svg.section_label(359, 214, "B", "数据入口闸门", VIOLET)
    svg.panel(
        348,
        232,
        274,
        380,
        title="Provider Contract",
        label="NORMALIZE & GOVERN",
        color=VIOLET,
        lines=(
            "ProviderMetadata / ProviderBatch",
            "认证 · 限流 · 新鲜度",
            "错误分类与 Retry-After",
            "PIT 可见性与修订语义",
            "涨停 / 炸板 / 连板 / 题材",
            "催化 / 公告 / 资金语义",
            "统一 schema 后才可进入研究",
        ),
    )
    svg.arrow(314, 327, 348, 327, color=CYAN)
    svg.arrow(314, 514, 348, 514, color=BLUE)

    svg.section_label(670, 214, "C", "四时钟 PIT", CYAN)
    clocks = [
        (658, "事件发生", "event_time"),
        (837, "首次可知", "available_time"),
        (1016, "系统入库", "ingested_time"),
        (1195, "数据修订", "revision_time"),
    ]
    for x, title, code in clocks:
        svg.raw(
            f'  <rect x="{x}" y="232" width="160" height="92" rx="18" fill="{PANEL_SOFT}" stroke="{CYAN}" stroke-opacity=".28" />'
        )
        svg.text(x + 80, 270, title, css="cardTitle", color=TEXT, anchor="middle")
        svg.text(x + 80, 298, code, css="mono", color=CYAN, anchor="middle")

    svg.panel(
        658,
        350,
        454,
        200,
        title="EvidenceSnapshot",
        label="CONTENT-ADDRESSED ARTIFACT",
        color=CYAN,
        lines=(
            "evidence_id = hash(source_uri + content_hash)",
            "可见时点 · 哈希 · 来源 · 许可 · 修订",
            "A 股事件语义与原始载荷建立绑定",
            "下游只接收决策时刻已经可知的 evidence_id",
        ),
    )
    svg.arrow(622, 421, 658, 421, color=CYAN, label="固化", label_y=405)
    svg.panel(
        1142,
        350,
        214,
        200,
        title="证据存储",
        label="IMMUTABLE STORE",
        color=BLUE,
        lines=("Parquet 分区", "DuckDB PIT 查询", "只读证据工具", "内容哈希复核"),
    )
    svg.arrow(1112, 450, 1142, 450, color=BLUE)

    svg.raw(
        f'  <rect x="658" y="578" width="698" height="92" rx="20" fill="{PANEL_ALT}" stroke="{LINE}" />'
    )
    svg.text(684, 608, "QUERY CONTRACT", css="micro", color=BLUE)
    svg.text(
        684,
        646,
        "as_of + symbol / event_type → 可见证据集合 → EvidenceSnapshot",
        css="cardTitle",
        color=TEXT,
    )
    svg.pill(1163, 588, 168, "交付研判脑区 →", color=CYAN)
    svg.finish("openalpha-brain-02-evidence.svg")


def agents() -> None:
    svg = Svg(
        index=3,
        eyebrow="研究编排",
        title="把证据规模化为可反驳、可审计的结构化观点",
        subtitle="单次与批量任务复用同一研究内核；角色由证据类型路由，模型只在治理边界内增强，不替代确定性基线。",
        accent=VIOLET,
    )
    svg.legend()
    svg.section_label(76, 214, "A", "同一输入契约", CYAN)
    svg.panel(
        64,
        232,
        244,
        164,
        title="EvidenceSnapshot",
        label="READ-ONLY INPUT",
        color=CYAN,
        lines=("PIT 可见证据", "稳定 evidence_id", "来源 / 时间 / 哈希"),
    )
    svg.panel(
        64,
        420,
        244,
        198,
        title="持久批量任务中心",
        label="1–32 CONCURRENCY",
        color=VIOLET,
        lines=(
            "进度事件 · 取消 · 重试",
            "Checkpoint · 宕机恢复",
            "每个标的复用同一 runner",
            "无旁路分析逻辑",
        ),
    )
    svg.pill(85, 640, 202, "单次 / 批量同源", color=VIOLET)

    svg.section_label(357, 214, "B", "确定性研究内核", VIOLET)
    svg.panel(
        346,
        232,
        286,
        386,
        title="ResearchEngine.run_cycle",
        label="ORCHESTRATION KERNEL",
        color=VIOLET,
        lines=(
            "EvidenceLookupTool 只读查询",
            "AgentRouter 记录 routing_path",
            "按证据类型选择专业角色",
            "汇总 SignalFrame",
            "RiskGate：pass / reduce / block",
            "证据不足 → 显式弃权",
            "写入 RunManifest / DecisionLedger",
        ),
    )
    svg.arrow(308, 314, 346, 314, color=CYAN)
    svg.arrow(308, 518, 346, 518, color=VIOLET)

    svg.section_label(681, 214, "C", "专业角色协作", VIOLET)
    roles = [
        (670, 232, "A1", "市场事件智能体", "涨停 · 炸板 · 连板", CYAN),
        (670, 362, "A2", "题材催化智能体", "题材 · 公告 · 催化", VIOLET),
        (670, 492, "A3", "资金流智能体", "资金观察 · 确认条件", BLUE),
    ]
    for x, y, code, title, detail, color in roles:
        svg.raw(
            f'  <rect x="{x}" y="{y}" width="292" height="110" rx="20" fill="{PANEL}" stroke="{LINE}" />'
        )
        svg.pill(x + 18, y + 17, 54, code, color=color, fill_opacity=0.2)
        svg.text(x + 88, y + 43, title, css="cardTitle", color=TEXT)
        svg.text(x + 24, y + 79, detail, css="body", color=MUTED)
    svg.path("M632 330H650V287H670", color=CYAN)
    svg.path("M632 400H670", color=VIOLET)
    svg.path("M632 470H650V547H670", color=BLUE)
    svg.raw(
        f'  <rect x="670" y="624" width="292" height="46" rx="16" fill="{PANEL_ALT}" stroke="{VIOLET}" stroke-opacity=".36" />'
    )
    svg.text(816, 653, "确定性基线｜无 LLM 也可运行", css="small", color=VIOLET, anchor="middle")

    svg.section_label(1011, 214, "D", "结构化研究产物", AMBER)
    svg.panel(
        1000,
        232,
        376,
        278,
        title="ResearchRunResult",
        label="AUDITABLE OUTPUT",
        color=AMBER,
        lines=(
            "SignalFrame：方向 / 强度 / 置信度 / 周期",
            "evidence_ids / signal_ids / risk_flags",
            "确认条件 / 失效条件",
            "watch / avoid / abstain",
            "agent_outputs / routing_path / 成本",
        ),
    )
    svg.path("M962 287H980V340H1000", color=AMBER)
    svg.path("M962 417H1000", color=AMBER)
    svg.path("M962 547H980V470H1000", color=AMBER)
    svg.panel(
        1000,
        532,
        376,
        138,
        title="模型治理边界",
        label="OPTIONAL MODEL ENHANCEMENT",
        color=CORAL,
        lines=("能力注册 · Schema 校验 · 408/429/5xx 重试", "Token / 尝试次数 / 估算成本持久化"),
    )
    svg.finish("openalpha-brain-03-agents.svg")


def decision() -> None:
    svg = Svg(
        index=4,
        eyebrow="决策约束",
        title="把分歧压缩成可解释结论，把结论约束成可审计状态变化",
        subtitle="研究委员会与组合会计均由调用方显式组合；主研究链不会把观点偷偷变成订单，更不会连接实盘券商。",
        accent=AMBER,
    )
    svg.legend()
    svg.section_label(76, 214, "A", "研究交接", VIOLET)
    svg.panel(
        64,
        232,
        244,
        236,
        title="ResearchRunResult",
        label="STRUCTURED SIGNALS",
        color=VIOLET,
        lines=(
            "市场 / 题材 / 资金观点",
            "evidence_ids / risk_flags",
            "置信度 / 周期 / 失效条件",
            "RiskGate 已执行",
        ),
    )
    svg.panel(
        64,
        490,
        244,
        180,
        title="责任边界",
        label="NO HIDDEN EXECUTION",
        color=CORAL,
        lines=("不自动下单", "不连接券商", "研究 ≠ 投资指令", "每次组合由调用方发起"),
    )

    svg.section_label(357, 214, "B", "研究委员会 · 显式 deliberate", AMBER)
    svg.raw(
        f'  <rect x="346" y="232" width="650" height="250" rx="22" fill="{PANEL}" stroke="{AMBER}" stroke-opacity=".28" />'
    )
    svg.text(671, 250, "Bull / Bear 辩论", css="small", color=AMBER, anchor="middle")
    svg.raw(
        f'  <rect x="370" y="260" width="208" height="120" rx="18" fill="{PANEL_SOFT}" stroke="{LIME}" stroke-opacity=".34" />'
    )
    svg.text(394, 289, "BULL / 正方", css="micro", color=LIME)
    svg.text(394, 322, "收益证据链", css="cardTitle", color=TEXT)
    svg.text(394, 352, "机会 · 催化 · 确认条件", css="body", color=MUTED)
    svg.raw(
        f'  <rect x="596" y="260" width="208" height="120" rx="18" fill="{PANEL_SOFT}" stroke="{CORAL}" stroke-opacity=".34" />'
    )
    svg.text(620, 289, "BEAR / 反方", css="micro", color=CORAL)
    svg.text(620, 322, "风险证据链", css="cardTitle", color=TEXT)
    svg.text(620, 352, "反例 · 失效 · 流动性", css="body", color=MUTED)
    svg.raw(
        f'  <rect x="822" y="260" width="150" height="120" rx="18" fill="{PANEL_SOFT}" stroke="{AMBER}" stroke-opacity=".34" />'
    )
    svg.text(846, 289, "RISK PANEL", css="micro", color=AMBER)
    svg.text(846, 320, "激进 / 中性", css="body", color=TEXT)
    svg.text(846, 347, "保守 / 弃权", css="body", color=TEXT)
    svg.path("M308 340H346", color=AMBER, dashed=True)
    svg.arrow(578, 320, 596, 320, color=AMBER, dashed=True)
    svg.arrow(804, 320, 822, 320, color=AMBER, dashed=True)
    svg.raw(
        f'  <rect x="370" y="404" width="602" height="50" rx="16" fill="{AMBER}" fill-opacity=".10" stroke="{AMBER}" stroke-opacity=".35" />'
    )
    svg.text(
        671,
        435,
        "DeliberationOutcome · 分歧摘要 / 风险调整信号 / 消融对照",
        css="small",
        color=AMBER,
        anchor="middle",
    )

    svg.section_label(1045, 214, "C", "最终研究裁决", CORAL)
    svg.panel(
        1034,
        232,
        342,
        250,
        title="RiskGate / DecisionLedger",
        label="POLICY DECISION",
        color=CORAL,
        lines=(
            "pass · reduce · block",
            "watch · avoid · abstain",
            "风险信号保留，不被模型文案覆盖",
            "证据 / 路由 / 信号 / 裁决全链可追溯",
            "显式弃权是合法的一等结果",
        ),
    )
    svg.path("M996 357H1015V340H1034", color=CORAL, dashed=True)

    svg.section_label(357, 524, "D", "组合会计 · 显式 portfolio compose", BLUE)
    svg.raw(
        f'  <rect x="346" y="542" width="1030" height="128" rx="22" fill="{PANEL}" stroke="{BLUE}" stroke-opacity=".30" />'
    )
    columns = [
        (370, 260, "输入状态", ("PortfolioState", "Order / MarketBar / Limits"), VIOLET),
        (650, 310, "A 股约束", ("T+1 · 100 股整手", "停牌 · 涨跌停 · 费用"), AMBER),
        (
            980,
            370,
            "PortfolioTransition",
            ("订单 → 成交 / 拒单", "持仓 / 现金 / FIFO / 暴露"),
            BLUE,
        ),
    ]
    for x, width, title, lines, color in columns:
        svg.text(x, 574, title, css="micro", color=color)
        svg.text(x, 603, lines[0], css="body", color=TEXT)
        svg.text(x, 631, lines[1], css="body", color=MUTED)
        if x < 900:
            svg.arrow(x + width, 606, x + width + 34, 606, color=color, dashed=True)
    svg.text(1352, 650, "不可变组合转移账本", css="small", color=BLUE, anchor="end")
    svg.finish("openalpha-brain-04-decision.svg")


def validation() -> None:
    svg = Svg(
        index=5,
        eyebrow="验证与反馈",
        title="用同一路径回答：是否有效、为何有效、下一轮改什么",
        subtitle="实时研究与历史回放共享 run_cycle；统计、组合与产物层只消费可复核账本，反馈必须经人工审阅后进入规则。",
        accent=BLUE,
    )
    svg.legend()
    svg.section_label(76, 214, "A", "双场景同内核", CYAN)
    svg.panel(
        64,
        232,
        248,
        160,
        title="实时研究",
        label="LIVE RESEARCH",
        color=CYAN,
        lines=("API / SDK / CLI / Web", "同一输入与输出契约", "授权 Provider 载荷"),
    )
    svg.panel(
        64,
        414,
        248,
        160,
        title="历史回放",
        label="HISTORICAL REPLAY",
        color=BLUE,
        lines=("60 交易日 · 300 事件", "冻结 Provider Payload", "避免线上 / 回放漂移"),
    )
    svg.panel(
        348,
        286,
        258,
        230,
        title="ResearchEngine.run_cycle",
        label="SAME EXECUTION PATH",
        color=VIOLET,
        lines=(
            "同一 AgentRouter",
            "同一 RiskGate",
            "同一 DecisionLedger",
            "同一成本与恢复语义",
            "输出 ResearchRunResult",
        ),
    )
    svg.arrow(312, 312, 348, 344, color=CYAN)
    svg.arrow(312, 494, 348, 458, color=BLUE)

    svg.section_label(655, 214, "B", "三重验证", BLUE)
    validation_cards = [
        (644, 232, "事件统计检验", "CAR · t 统计量", "确定性 Bootstrap 置信区间", CYAN),
        (
            644,
            354,
            "多日组合报告",
            "收益 · 基准 · 主动收益 · 换手",
            "容量 · 暴露 · 标的归因",
            VIOLET,
        ),
        (644, 476, "内容一致性验证", "ID 重算 · 账本完整性", "结果属性与失败原因归因", CORAL),
    ]
    for x, y, title, headline, detail, color in validation_cards:
        svg.raw(
            f'  <rect x="{x}" y="{y}" width="338" height="104" rx="19" fill="{PANEL}" stroke="{LINE}" />'
        )
        svg.raw(f'  <rect x="{x}" y="{y + 20}" width="3" height="64" rx="1.5" fill="{color}" />')
        svg.text(x + 22, y + 29, title.upper(), css="micro", color=color)
        svg.text(x + 22, y + 58, headline, css="cardTitle", color=TEXT)
        svg.text(x + 22, y + 84, detail, css="small", color=MUTED)
    svg.path("M606 360H625V284H644", color=CYAN)
    svg.arrow(606, 401, 644, 406, color=VIOLET)
    svg.path("M606 442H625V528H644", color=CORAL)

    svg.section_label(1031, 214, "C", "研究产品与交付", AMBER)
    svg.panel(
        1020,
        232,
        356,
        348,
        title="ValidationResult",
        label="RESEARCH PRODUCTS",
        color=AMBER,
        lines=(
            "结构化研究筛选",
            "SQLite 持久观察池",
            "内容寻址不可变报告中心",
            "REST / SDK / CLI / Web",
            "Checkpoint · SQLite WAL · 灾难恢复",
            "报告 / 指标 / 归因保留 provenance",
        ),
    )
    svg.path("M982 284H1002V330H1020", color=AMBER)
    svg.arrow(982, 406, 1020, 406, color=AMBER)
    svg.path("M982 528H1002V482H1020", color=AMBER)

    svg.raw(
        f'  <rect x="348" y="608" width="1028" height="62" rx="19" fill="{AMBER}" fill-opacity=".075" stroke="{AMBER}" stroke-opacity=".30" />'
    )
    svg.text(374, 634, "HUMAN-IN-THE-LOOP FEEDBACK", css="micro", color=AMBER)
    svg.text(
        374,
        658,
        "人工复核归因 → 调整数据质量规则 / AgentRouter / RiskGate 阈值 → 进入下一轮证据与配置；不自动训练模型",
        css="body",
        color=TEXT,
    )
    svg.path("M1020 638H620", color=AMBER, dashed=True, arrow=False)
    svg.path("M348 638H328V654H188V574", color=AMBER, dashed=True)
    svg.finish("openalpha-brain-05-replay-interfaces.svg")


def main() -> None:
    overview()
    evidence()
    agents()
    decision()
    validation()


if __name__ == "__main__":
    main()
