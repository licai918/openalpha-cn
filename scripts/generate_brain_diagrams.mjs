import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const OUTPUT_DIR = path.join(ROOT, "assets", "diagrams");

const palette = {
  ink: "#0F172A",
  muted: "#64748B",
  line: "#CBD5E1",
  panel: "#FFFFFF",
  canvas: "#F7F8FC",
  indigo: "#5968F2",
  teal: "#0F9F8F",
  violet: "#7C5CE7",
  amber: "#E89422",
  blue: "#1688D4",
  green: "#22A06B",
  red: "#DC5A54",
};

const stages = [
  ["01", "全脑总览"],
  ["02", "证据感知"],
  ["03", "智能体研判"],
  ["04", "风险决策"],
  ["05", "回放与开放"],
];

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function text(x, y, value, className = "body", anchor = "start", fill = "") {
  const fillAttribute = fill ? ` fill="${fill}"` : "";
  return `<text x="${x}" y="${y}" class="${className}" text-anchor="${anchor}"${fillAttribute}>${escapeXml(value)}</text>`;
}

function textLines(x, y, values, className = "body", gap = 26, anchor = "start") {
  return values
    .map((value, index) => text(x, y + index * gap, value, className, anchor))
    .join("");
}

function roundedRect(x, y, width, height, fill, stroke = palette.line, radius = 20, extra = "") {
  return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${radius}" fill="${fill}" stroke="${stroke}" ${extra}/>`;
}

function pill(x, y, width, label, fill, color = "#FFFFFF") {
  return [
    roundedRect(x, y, width, 34, fill, fill, 17),
    text(x + width / 2, y + 23, label, "pill", "middle", color),
  ].join("");
}

function arrow(x1, y1, x2, y2, color = palette.line, dashed = false) {
  const dash = dashed ? ' stroke-dasharray="8 8"' : "";
  return `<path d="M ${x1} ${y1} L ${x2} ${y2}" fill="none" stroke="${color}" stroke-width="3"${dash} marker-end="url(#arrow)"/>`;
}

function pathArrow(d, color = palette.line, dashed = false) {
  const dash = dashed ? ' stroke-dasharray="8 8"' : "";
  return `<path d="${d}" fill="none" stroke="${color}" stroke-width="3"${dash} marker-end="url(#arrow)"/>`;
}

function card({ x, y, width, height, title, kicker = "", lines = [], accent, badge = "" }) {
  const output = [
    roundedRect(x, y, width, height, palette.panel, "#D9DFEA", 22, 'filter="url(#shadow)"'),
    `<rect x="${x}" y="${y}" width="8" height="${height}" rx="4" fill="${accent}"/>`,
  ];
  if (badge) {
    output.push(pill(x + 24, y + 22, 54, badge, accent));
  }
  const titleX = badge ? x + 94 : x + 28;
  if (kicker) {
    output.push(text(titleX, y + 35, kicker, "kicker", "start", accent));
    output.push(text(x + 28, y + 72, title, "cardTitle"));
  } else {
    output.push(text(titleX, y + 45, title, "cardTitle"));
  }
  const startY = kicker ? y + 108 : y + 84;
  output.push(textLines(x + 28, startY, lines, "body", 30));
  return output.join("");
}

function header(stage, title, subtitle, accent) {
  return [
    pill(64, 46, 158, `OPENALPHA · ${stage}`, accent),
    text(64, 116, title, "title"),
    text(64, 154, subtitle, "subtitle"),
    `<g transform="translate(1270 42)">
      <path d="M48 10c-19 0-34 14-34 32 0 9 4 17 10 23-2 4-3 9-2 14 2 12 13 21 26 21h29c15 0 27-11 27-25 0-7-3-13-8-18 2-5 2-11 0-17-4-12-16-20-29-19-5-7-11-11-19-11Z" fill="${accent}" opacity=".12"/>
      <circle cx="42" cy="44" r="7" fill="${accent}"/>
      <circle cx="70" cy="33" r="7" fill="${accent}" opacity=".8"/>
      <circle cx="82" cy="65" r="7" fill="${accent}" opacity=".65"/>
      <path d="M48 43 64 35M47 49 76 62M72 39 80 58" stroke="${accent}" stroke-width="3" stroke-linecap="round"/>
    </g>`,
  ].join("");
}

function navigation(activeIndex, accent) {
  const xPositions = [64, 328, 592, 856, 1120];
  const labels = stages.map(([number, label], index) => {
    const active = index === activeIndex;
    const fill = active ? accent : "#FFFFFF";
    const stroke = active ? accent : "#D7DDE8";
    const numberFill = active ? "#FFFFFF" : palette.muted;
    const labelFill = active ? "#FFFFFF" : palette.ink;
    const x = xPositions[index];
    const block = [
      roundedRect(x, 792, 216, 58, fill, stroke, 18, active ? 'filter="url(#shadow)"' : ""),
      text(x + 34, 828, number, "navNumber", "middle", numberFill),
      text(x + 70, 827, label, "navLabel", "start", labelFill),
    ];
    if (index < stages.length - 1) {
      block.push(arrow(x + 222, 821, x + 252, 821, active ? accent : "#C5CDDA"));
    }
    return block.join("");
  });
  return [
    text(64, 766, "五脑区导航｜从数据感知到验证反馈的完整认知链", "navCaption"),
    ...labels,
  ].join("");
}

function frame({ stage, title, subtitle, accent, activeIndex, body }) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900" role="img" aria-labelledby="title desc">
  <title id="title">${escapeXml(title)}</title>
  <desc id="desc">${escapeXml(subtitle)}</desc>
  <defs>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">
      <feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#27324A" flood-opacity=".10"/>
    </filter>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/>
    </marker>
    <pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.5" fill="#D8DEEB"/>
    </pattern>
    <style>
      text { font-family: Inter, "Noto Sans SC", "Microsoft YaHei", Arial, sans-serif; }
      .title { font-size: 38px; font-weight: 760; fill: ${palette.ink}; letter-spacing: -.5px; }
      .subtitle { font-size: 19px; font-weight: 450; fill: ${palette.muted}; }
      .cardTitle { font-size: 23px; font-weight: 720; fill: ${palette.ink}; }
      .kicker { font-size: 14px; font-weight: 760; letter-spacing: 1.2px; }
      .body { font-size: 17px; font-weight: 470; fill: #334155; }
      .small { font-size: 15px; font-weight: 500; fill: ${palette.muted}; }
      .pill { font-size: 14px; font-weight: 760; letter-spacing: .4px; }
      .metric { font-size: 28px; font-weight: 780; fill: ${palette.ink}; }
      .navCaption { font-size: 15px; font-weight: 650; fill: ${palette.muted}; letter-spacing: .4px; }
      .navNumber { font-size: 15px; font-weight: 800; }
      .navLabel { font-size: 17px; font-weight: 720; }
    </style>
  </defs>
  <rect width="1440" height="900" fill="${palette.canvas}"/>
  <rect width="1440" height="900" fill="url(#dots)" opacity=".36"/>
  ${header(stage, title, subtitle, accent)}
  ${body}
  ${navigation(activeIndex, accent)}
</svg>`;
}

function overview() {
  const accent = palette.indigo;
  const cards = [
    {
      x: 52,
      title: "数据感知",
      color: palette.teal,
      lines: ["自有文件 / Tushare", "可选 AKShare", "来源与许可元数据"],
    },
    {
      x: 326,
      title: "证据成像",
      color: "#1AA7A1",
      lines: ["四时钟时间线", "EvidenceSnapshot", "PIT 防前视查询"],
    },
    {
      x: 600,
      title: "智能体研判",
      color: palette.violet,
      lines: ["市场 / 题材 / 资金", "证据感知路由", "结构化模型边界"],
    },
    {
      x: 874,
      title: "风险决策",
      color: palette.amber,
      lines: ["SignalFrame", "pass / reduce / block", "watch / avoid / abstain"],
    },
    {
      x: 1148,
      title: "回放开放",
      color: palette.blue,
      lines: ["同一 run_cycle", "验证与三层归因", "API / SDK / CLI / Web"],
    },
  ];
  const body = [
    text(720, 206, "OPENALPHA CN · A 股研究大脑", "metric", "middle"),
    text(720, 238, "感知 → 证据 → 研判 → 决策 → 验证，再把结果反馈给下一轮研究", "small", "middle"),
    ...cards.map((item, index) =>
      card({
        x: item.x,
        y: 282,
        width: 240,
        height: 278,
        title: item.title,
        lines: item.lines,
        accent: item.color,
        badge: `0${index + 1}`,
      }),
    ),
    ...cards.slice(0, -1).map((item, index) => arrow(item.x + 242, 420, cards[index + 1].x - 8, 420, accent)),
    roundedRect(202, 608, 1036, 68, "#EEF1FF", "#C9D0FF", 22),
    text(720, 638, "闭环反馈：回放结果与归因用于校验证据质量、路由规则和风险条件", "cardTitle", "middle"),
    text(720, 663, "不是直接下单的大脑，而是把每个研究结论变成可验证记录的研究系统", "small", "middle"),
    pathArrow("M 1210 590 C 1210 708, 230 708, 230 590", accent, true),
  ].join("");
  return frame({
    stage: "01",
    title: "全脑总览｜五个脑区组成一条 A 股研究闭环",
    subtitle: "吸收多角色协作与风险决策的表达优势，用证据、时间与回放把整条链路变得可验证。",
    accent,
    activeIndex: 0,
    body,
  });
}

function evidence() {
  const accent = palette.teal;
  const body = [
    text(64, 205, "合法数据输入", "kicker", "start", accent),
    card({
      x: 52,
      y: 228,
      width: 262,
      height: 112,
      title: "用户自有文件",
      lines: ["CSV · JSON · JSONL · Parquet"],
      accent,
      badge: "A",
    }),
    card({
      x: 52,
      y: 365,
      width: 262,
      height: 112,
      title: "Tushare Pro",
      lines: ["用户自带 Token · 不捆绑凭据"],
      accent,
      badge: "B",
    }),
    card({
      x: 52,
      y: 502,
      width: 262,
      height: 112,
      title: "AKShare",
      lines: ["可选 Adapter · 默认关闭"],
      accent,
      badge: "C",
    }),
    card({
      x: 368,
      y: 276,
      width: 252,
      height: 286,
      title: "Provider 合同",
      kicker: "数据入口闸门",
      lines: ["来源与许可", "凭据与限流", "新鲜度与缓存", "失败必须显式", "禁止空结果伪成功"],
      accent,
    }),
    text(748, 205, "四时钟时间线", "kicker", "start", accent),
    ...[
      ["事件发生", "event_time"],
      ["首次可知", "available_time"],
      ["系统入库", "ingested_time"],
      ["数据修订", "revision_time"],
    ].map(([label, field], index) => {
      const x = 674 + index * 176;
      return [
        roundedRect(x, 228, 154, 86, index === 1 ? "#DDF7F2" : "#FFFFFF", index === 1 ? accent : "#D7DDE8", 18),
        text(x + 77, 261, label, "cardTitle", "middle"),
        text(x + 77, 288, field, "small", "middle"),
      ].join("");
    }),
    card({
      x: 742,
      y: 350,
      width: 510,
      height: 172,
      title: "EvidenceSnapshot",
      kicker: "不可变 · 内容寻址",
      lines: ["evidence_id + source_uri + content_hash", "A 股语义：涨停 / 炸板 / 连板 / 题材 / 催化 / 公告 / 资金"],
      accent,
    }),
    roundedRect(678, 570, 648, 74, "#E9F8F5", "#B7E8DE", 20),
    text(1002, 600, "Parquet 分区  →  DuckDB PIT 查询  →  只读证据工具", "cardTitle", "middle"),
    text(1002, 627, "只向下一脑区交付决策时刻已经可见的 evidence_id", "small", "middle"),
    pathArrow("M 314 284 C 344 284, 340 330, 358 330", accent),
    arrow(314, 421, 358, 421, accent),
    pathArrow("M 314 558 C 344 558, 340 510, 358 510", accent),
    arrow(620, 421, 732, 421, accent),
    arrow(995, 522, 995, 562, accent),
    pill(1244, 680, 132, "→ 研判脑区", accent),
  ].join("");
  return frame({
    stage: "02",
    title: "证据感知｜先回答“当时能不能知道”",
    subtitle: "数据优势不等于多接几个接口；真正的优势是来源、时间、修订和内容哈希都可追溯。",
    accent,
    activeIndex: 1,
    body,
  });
}

function agents() {
  const accent = palette.violet;
  const body = [
    card({
      x: 54,
      y: 244,
      width: 260,
      height: 214,
      title: "EvidenceSnapshot",
      kicker: "只读证据输入",
      lines: ["PIT 可见证据", "稳定 evidence_id", "来源 / 时间 / 哈希"],
      accent: palette.teal,
    }),
    roundedRect(76, 492, 216, 92, "#F0EDFF", "#D8D0FF", 18),
    text(184, 525, "EvidenceLookupTool", "cardTitle", "middle"),
    text(184, 553, "智能体只能查询证据", "small", "middle"),
    card({
      x: 372,
      y: 308,
      width: 212,
      height: 164,
      title: "AgentRouter",
      kicker: "证据感知路由",
      lines: ["按证据类型选择角色", "记录 routing_path"],
      accent,
    }),
    ...[
      ["市场事件智能体", "涨停 · 炸板 · 连板", 198],
      ["题材催化智能体", "题材 · 公告 · 催化", 340],
      ["资金流智能体", "资金观察 · 确认条件", 482],
    ].map(([titleValue, detail, y], index) =>
      card({
        x: 642,
        y,
        width: 286,
        height: 116,
        title: titleValue,
        lines: [detail],
        accent: index === 0 ? "#6D5CE8" : index === 1 ? "#8B67E8" : "#A074E2",
        badge: `A${index + 1}`,
      }),
    ),
    roundedRect(646, 622, 278, 58, "#F0EDFF", "#D8D0FF", 18),
    text(785, 648, "确定性基线｜无 LLM 也可运行", "body", "middle"),
    card({
      x: 1000,
      y: 244,
      width: 366,
      height: 290,
      title: "SignalFrame",
      kicker: "统一结构化输出",
      lines: [
        "方向 · 强度 · 置信度 · 周期",
        "evidence_ids",
        "确认条件 / 失效条件",
        "risk_flags",
        "证据不足时显式 abstain",
      ],
      accent,
    }),
    roundedRect(1000, 562, 366, 102, "#F7F5FF", "#D8D0FF", 18),
    text(1026, 595, "可选 StructuredModelAgent", "cardTitle"),
    text(1026, 625, "Schema 校验 · 有界重试 · 不覆盖证据边界", "small"),
    arrow(314, 350, 362, 350, accent),
    pathArrow("M 292 538 C 338 538, 330 452, 362 452", accent, true),
    ...[256, 398, 540].map((y) => pathArrow(`M 584 390 C 614 390, 614 ${y}, 634 ${y}`, accent)),
    ...[256, 398, 540].map((y) => pathArrow(`M 928 ${y} C 960 ${y}, 960 390, 990 390`, accent)),
    pill(1230, 690, 146, "→ 决策脑区", accent),
  ].join("");
  return frame({
    stage: "03",
    title: "智能体研判｜角色围绕证据协作，而不是围绕人设表演",
    subtitle: "借鉴专业角色分工，但让每个结论都落入同一 SignalFrame，并完整引用证据。",
    accent,
    activeIndex: 2,
    body,
  });
}

function decision() {
  const accent = palette.amber;
  const body = [
    text(66, 206, "结构化信号", "kicker", "start", accent),
    ...[
      ["市场事件", "bullish / bearish"],
      ["题材催化", "confidence / horizon"],
      ["资金观察", "risk_flags"],
    ].map(([titleValue, detail], index) =>
      card({
        x: 52,
        y: 232 + index * 128,
        width: 260,
        height: 100,
        title: titleValue,
        lines: [detail],
        accent: palette.violet,
        badge: `S${index + 1}`,
      }),
    ),
    card({
      x: 384,
      y: 284,
      width: 244,
      height: 238,
      title: "RiskGate",
      kicker: "风险门",
      lines: ["pass · 正常通过", "reduce · 降级观察", "block · 阻断结论", "风险信号不会被隐藏"],
      accent,
    }),
    text(700, 206, "A 股约束校验", "kicker", "start", accent),
    ...[
      ["T+1", "当日买入不可卖"],
      ["整手", "主板 100 股"],
      ["停牌", "显式拒绝"],
      ["涨跌停", "一字板锁单"],
      ["成本", "佣金 / 过户 / 印花税"],
    ].map(([label, detail], index) => {
      const x = 686 + (index % 2) * 230;
      const y = 232 + Math.floor(index / 2) * 100;
      return [
        roundedRect(x, y, 210, 78, "#FFF8E9", "#F1D49D", 16),
        text(x + 22, y + 30, label, "cardTitle"),
        text(x + 22, y + 57, detail, "small"),
      ].join("");
    }),
    card({
      x: 1142,
      y: 232,
      width: 246,
      height: 334,
      title: "DecisionLedger",
      kicker: "不可变决策记录",
      lines: [
        "watch · 继续观察",
        "avoid · 明确回避",
        "abstain · 证据不足",
        "agent_outputs",
        "routing_path",
        "evidence_ids / signal_ids",
      ],
      accent,
    }),
    roundedRect(688, 548, 438, 104, "#FFF4E2", "#F0C77C", 20),
    text(907, 584, "研究决策 ≠ 实盘订单", "cardTitle", "middle"),
    text(907, 615, "系统记录观察、回避或弃权，不连接券商下单", "body", "middle"),
    ...[282, 410, 538].map((y) => pathArrow(`M 312 ${y} C 346 ${y}, 346 402, 374 402`, accent)),
    arrow(628, 402, 676, 402, accent),
    arrow(1126, 402, 1132, 402, accent),
    pill(1222, 688, 154, "→ 验证脑区", accent),
  ].join("");
  return frame({
    stage: "04",
    title: "风险决策｜把分歧压缩成可审计的研究动作",
    subtitle: "保留多角色信号与风险管理的优点，同时用 A 股规则、显式弃权和不可变账本约束结论。",
    accent,
    activeIndex: 3,
    body,
  });
}

function replayInterfaces() {
  const accent = palette.blue;
  const body = [
    roundedRect(52, 224, 286, 172, "#EAF5FC", "#B9DDF2", 22, 'filter="url(#shadow)"'),
    text(195, 264, "实时研究", "cardTitle", "middle"),
    text(195, 296, "API / SDK / CLI / Web", "body", "middle"),
    text(195, 330, "同一输入合同", "small", "middle"),
    roundedRect(52, 434, 286, 172, "#EAF5FC", "#B9DDF2", 22, 'filter="url(#shadow)"'),
    text(195, 474, "历史回放", "cardTitle", "middle"),
    text(195, 506, "60 交易日 · 300 事件", "body", "middle"),
    text(195, 540, "冻结 Provider Payload", "small", "middle"),
    card({
      x: 406,
      y: 294,
      width: 258,
      height: 238,
      title: "run_cycle",
      kicker: "同一研究内核",
      lines: ["同一 AgentRouter", "同一 RiskGate", "同一 DecisionLedger", "避免线上 / 回放漂移"],
      accent,
    }),
    roundedRect(724, 222, 286, 118, "#FFFFFF", "#D7DDE8", 18, 'filter="url(#shadow)"'),
    text(867, 258, "Checkpoint + Memory", "cardTitle", "middle"),
    text(867, 291, "SQLite WAL · 幂等恢复", "body", "middle"),
    roundedRect(724, 370, 286, 118, "#FFFFFF", "#D7DDE8", 18, 'filter="url(#shadow)"'),
    text(867, 406, "确定性与防前视", "cardTitle", "middle"),
    text(867, 439, "固定输入复现 · PIT 守卫", "body", "middle"),
    roundedRect(724, 518, 286, 118, "#FFFFFF", "#D7DDE8", 18, 'filter="url(#shadow)"'),
    text(867, 554, "ValidationResult", "cardTitle", "middle"),
    text(867, 587, "规则 · 因子 · 智能体归因", "body", "middle"),
    card({
      x: 1074,
      y: 244,
      width: 310,
      height: 336,
      title: "开放使用与交付",
      kicker: "研究结果出口",
      lines: [
        "REST API · OpenAPI",
        "Python SDK",
        "CLI 证据 / 研究 / 回放",
        "React 研究工作台",
        "Docker 持久卷与恢复",
        "不部署：链邻桌面软件",
      ],
      accent,
    }),
    arrow(338, 310, 396, 368, accent),
    arrow(338, 520, 396, 458, accent),
    pathArrow("M 664 350 C 694 350, 690 280, 714 280", accent),
    arrow(664, 412, 714, 412, accent),
    pathArrow("M 664 474 C 694 474, 690 577, 714 577", accent),
    pathArrow("M 1010 280 C 1040 280, 1036 350, 1064 350", accent),
    arrow(1010, 430, 1064, 430, accent),
    pathArrow("M 1010 577 C 1040 577, 1036 510, 1064 510", accent),
    pathArrow("M 1228 596 C 1228 710, 158 710, 158 628", palette.teal, true),
    pill(74, 660, 316, "验证反馈 → 下一轮证据与规则", palette.teal),
  ].join("");
  return frame({
    stage: "05",
    title: "回放与开放｜让研究结果进入记忆，再服务下一轮判断",
    subtitle: "实时与历史共用同一内核；验证、归因、恢复和多端接口共同构成可持续的研究闭环。",
    accent,
    activeIndex: 4,
    body,
  });
}

const diagrams = [
  ["openalpha-brain-01-overview.svg", overview()],
  ["openalpha-brain-02-evidence.svg", evidence()],
  ["openalpha-brain-03-agents.svg", agents()],
  ["openalpha-brain-04-decision.svg", decision()],
  ["openalpha-brain-05-replay-interfaces.svg", replayInterfaces()],
];

await mkdir(OUTPUT_DIR, { recursive: true });
for (const [name, content] of diagrams) {
  await writeFile(path.join(OUTPUT_DIR, name), content, "utf8");
}

console.log(`Generated ${diagrams.length} SVG diagrams in ${OUTPUT_DIR}`);
