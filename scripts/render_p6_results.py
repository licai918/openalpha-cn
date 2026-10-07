"""Render the P6 results tables from the research ledgers (V2-P6-010; no number is typed by hand).

usage: render_p6_results.py RUN OUT.md   (RUN = r3, the protocol run under amendment #4)

Reads `~/openalpha-research/research/RUN/*` (the protocol run), `research/r2/*` (superseded: the
statement-factor read defect, its holdout refused) and `research/p6-ledger.jsonl` with
`research/p6-survivors.json` (the first run, defective benchmark), and prints every stage of each
in full. Every figure comes from those files; `docs/research/p6-results.md` quotes this output
section by section. A ledger row the stage's FDR table does not name is refused, not shown as "not
rejected".
"""

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESEARCH_SCRIPTS = Path(__file__).resolve().parent / "research"
if str(RESEARCH_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RESEARCH_SCRIPTS))

import grid  # noqa: E402  (scripts/research/grid.py: the ledger's canonical JSON)

ROOT = Path.home() / "openalpha-research" / "research"
R2 = ROOT / "r2"

Row = dict[str, Any]
Label = Callable[[Row], str]


def rows(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def artifact(run_dir: Path, name: str) -> Row | None:
    path = run_dir / name
    return json.loads(path.read_text()) if path.exists() else None


def require_digest(ledger: list[Row], stage: str, made: Row, name: str) -> None:
    """Refuse an artifact that was not made from these ledger rows: `stage_rows_sha256` is
    `p6._rows_digest` over the stage's measurement rows in ledger order."""
    chosen = [[r["config_id"], r["result"]] for r in measurements(ledger, stage)]
    digest = hashlib.sha256(grid.canonical_json(chosen).encode("utf-8")).hexdigest()
    if digest != made["stage_rows_sha256"]:
        raise SystemExit(f"{name} was not made from the ledger's {stage} rows")


def f(value: object, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{float(str(value)):.{digits}f}"


def p(value: object) -> str:
    if value is None:
        return "—"
    v = float(str(value))
    return f"{v:.2e}" if v < 1e-3 else f"{v:.4f}"


def source(config: Row) -> str:
    if "walk_forward" in config:
        h = config["walk_forward"]["hyperparameters"]
        return (
            f"walk-forward trees={h['tree_count']} depth={h['max_depth']} "
            f"lr={h['learning_rate']} min_leaf={h['min_leaf_securities']}"
        )
    if "trailing_ic" in config:
        return f"trailing IC ({config['trailing_ic']['negative_ic']})"
    comps = config["components"]
    if len(comps) == 1:
        return f"{comps[0][0]} @ {comps[0][1]}"
    return f"static zscore_sum, {len(comps)} components"


def component(row: Row) -> str:
    return source(row["config"])


def strategy(row: Row) -> str:
    c = row["config"]
    return (
        f"hold {c['holding_count']}, every {c['rebalance_every_sessions']}, "
        f"buffer {c['buffer_rank'] or '—'}, cap {c['max_industry_weight'] or '—'}"
    )


def fdr_lookup(table: Row) -> dict[str, Row]:
    return {v["hypothesis_id"]: v for v in table["verdicts"]}


def verdict_of(fdr: dict[str, Row], config_id: str) -> Row:
    if config_id not in fdr:
        raise SystemExit(
            f"the FDR table does not name {config_id}; the artifacts and ledger differ"
        )
    return fdr[config_id]


def stage_table(
    stage_rows: list[Row], fdr: dict[str, Row], ic_fdr: dict[str, Row] | None, label: Label
) -> list[str]:
    head = (
        "| 配置 | 信息比率 | 年化净超额 | p_excess | BY q | BY 拒绝 | 平均换手 "
        "| 中证500 IR | 中证500 复合年化相对 |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|"
    if ic_fdr is not None:
        head += " 平均 IC | p_ic | IC BY 拒绝 |"
        sep += "---|---|---|"
    out = [head, sep]
    for row in stage_rows:
        res = row["result"]
        if "error" in res:
            # A refused row has no p-value: the FDR table counts it as withheld (in the family,
            # never rejected) and names only the reported hypotheses.
            reported = fdr.get(row["config_id"])
            q = "withheld" if reported is None else f(reported.get("q_value"), 4)
            cells = [label(row), "拒绝", "—", "—", q, "否", "—", "—", "—"]
            if ic_fdr is not None:
                cells += ["—", "—", "—"]
            out.append("| " + " | ".join(cells) + " |")
            continue
        verdict = verdict_of(fdr, row["config_id"])
        cells = [
            label(row),
            f(res["information_ratio"]),
            f(res["annualized_mean_net_excess"], 4),
            p(res["p_excess"]),
            f(verdict.get("q_value"), 4),
            "是" if verdict["rejected"] else "否",
            f(res["mean_turnover"]),
            f(res.get("reported_information_ratio")),
            f(res.get("reported_compounded_annual_relative_return"), 4),
        ]
        if ic_fdr is not None:
            if res.get("mean_ic") is None:
                cells += ["—", "—", "—"]
            else:
                iv = verdict_of(ic_fdr, row["config_id"])
                cells += [
                    f(res["mean_ic"], 4),
                    p(res.get("p_ic")),
                    "是" if iv["rejected"] else "否",
                ]
        out.append("| " + " | ".join(cells) + " |")
    return out


def result_table(results: list[Row], label: Label) -> list[str]:
    out = [
        "| 配置 | 信息比率 | 年化净超额 | 复合年化相对收益 | 最大相对回撤 | p_excess | 平均换手 "
        "| 中证500 IR | 中证500 复合年化相对 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for res in results:
        if "error" in res:
            out.append(f"| {label(res)} | 拒绝 | — | — | — | — | — | — | — |")
            continue
        cells = [
            label(res),
            f(res["information_ratio"]),
            f(res["annualized_mean_net_excess"], 4),
            f(res.get("compounded_annual_relative_return"), 4),
            f(res.get("max_relative_drawdown"), 4),
            p(res["p_excess"]),
            f(res["mean_turnover"]),
            f(res.get("reported_information_ratio")),
            f(res.get("reported_compounded_annual_relative_return"), 4),
        ]
        out.append("| " + " | ".join(cells) + " |")
    return out


def measurements(ledger: list[Row], stage: str) -> list[Row]:
    return [r for r in ledger if r["stage"] == stage and r["kind"] == "measurement"]


def discovery_summary(ledger: list[Row]) -> str:
    disc = measurements(ledger, "discovery")
    ok = sorted(r["result"]["information_ratio"] for r in disc if "error" not in r["result"])
    refused = len(disc) - len(ok)
    pos = sum(1 for x in ok if x > 0)
    return (
        f"{len(disc)} 行（{refused} 行拒绝），可测 {len(ok)}，IR>0 {pos} 个，"
        f"最高 {f(ok[-1])}，中位 {f(ok[len(ok) // 2])}"
    )


def commits_of(ledger: list[Row]) -> str:
    found = {r["result"]["code_commit"][:7] for r in ledger if "code_commit" in r.get("result", {})}
    return ", ".join(sorted(found))


def holdout_lines(ledger: list[Row], verdict: Row) -> list[str]:
    v = verdict
    out = [
        f"登记提交 `{v['registration_commit'][:7]}`，"
        f"登记 sha256 `{v['registration_sha256'][:12]}…`，"
        f"保留期配置 `{v['holdout_config_id'][:12]}`，认领时刻（UTC）"
        f"{v['statement'].removeprefix('measured once, claimed at ')}，只测一次。",
        "",
        "| 判据 | 值 | 阈值 | 通过 |",
        "|---|---|---|---|",
    ]
    for name, c in v["criteria"].items():
        passed = "是" if c["passed"] else "否"
        out.append(f"| {name} | {f(c['value'], 6)} | {f(c['threshold'], 6)} | {passed} |")
    measured = measurements(ledger, "holdout")
    if len(measured) != 1:
        raise SystemExit(f"the ledger holds {len(measured)} holdout measurements, not one")
    m = measured[0]["result"]
    if "error" not in m:
        complete = m["period_count"] - m["excluded_incomplete_periods"]
        out += [
            "",
            f"测量（{complete} 个完整期，另有 {m['excluded_incomplete_periods']} 个"
            f"不完整的末期按协议第 7 节不进入检验，主基准 `{m['excess_benchmark']}`）：信息比率 "
            f"{f(m['information_ratio'])}，年化净超额 {f(m['annualized_mean_net_excess'], 4)}，"
            f"双侧 p {p(m['p_excess'])}，平均换手 {f(m['mean_turnover'])}。"
            f"并列报告的中证 500 `{m['reported_benchmark']}`：信息比率 "
            f"{f(m['reported_information_ratio'])}，复合年化相对收益 "
            f"{f(m['reported_compounded_annual_relative_return'], 6)}。",
        ]
    out += ["", f"判定：**{v['verdict']}**（措辞：{v['wording']}）。"]
    if v.get("error"):
        out += ["", f"测量被拒，无任何绩效数字：`{v['error']}`"]
    out.append("")
    return out


def run_sections(ledger: list[Row], run_dir: Path, survivors: Row, level: str) -> list[str]:
    """Every stage the run reached, each table in full. `level` is the heading prefix."""
    by_id = {r["config_id"]: r for r in ledger if "config" in r}
    require_digest(ledger, "discovery", survivors, "p6-survivors.json")
    t = survivors["fdr_table"]
    lines = [
        f"{level} 阶段 1",
        "",
        f"家族 {t['family_size']}，报告 {t['reported_hypotheses']}，"
        f"withheld {t['withheld_hypotheses']}，"
        f"BY（任意相依，q={t['false_discovery_rate']}）拒绝 {t['discoveries']}，"
        f"次家族拒绝 {survivors['ic_fdr_table']['discoveries']}，"
        f"幸存 {len(survivors['survivors'])}，回退 {survivors['fallback']}。"
        f"{discovery_summary(ledger)}。",
        "",
    ]
    disc = measurements(ledger, "discovery")
    for h in (1, 5, 20):
        hs = [r for r in disc if r["config"]["rebalance_every_sessions"] == h]
        lines += [f"{level}# 预测期 h={h}", ""]
        lines += stage_table(hs, fdr_lookup(t), fdr_lookup(survivors["ic_fdr_table"]), component)
        lines.append("")
    comp = measurements(ledger, "composition")
    fin = artifact(run_dir, "p6-finalists.json")
    if comp and fin is not None:
        require_digest(ledger, "composition", fin, "p6-finalists.json")
        fdr = fdr_lookup(fin["fdr_table"])
        sources = [
            r
            for r in comp
            if r["config"]["holding_count"] == 50
            and r["config"]["rebalance_every_sessions"] == 20
            and r["config"]["buffer_rank"] is None
            and r["config"]["max_industry_weight"] is None
        ]
        strategies = [r for r in comp if r not in sources]
        used = {source(r["config"]) for r in strategies}
        if len(used) != 1:
            raise SystemExit(f"2b rows use {len(used)} scoring sources, not the one 2a chose")
        lines += [f"{level} 阶段 2", "", f"{level}# 2a 打分源", ""]
        lines += stage_table(sources, fdr, None, component)
        lines += ["", f"{level}# 2b 策略参数（打分源：{used.pop()}）", ""]
        lines += stage_table(strategies, fdr, None, strategy)
        none_passed = "是" if fin["no_configuration_passed_multiple_testing"] else "否"
        lines += [
            "",
            f"{level}# 入围",
            "",
            f"家族 {fin['family_size']}，BY 拒绝 {fin['fdr_table']['discoveries']}，"
            f"无配置通过多重检验：{none_passed}。"
            "以下为阶段 2 的 config_id，验证期把同一配置换成验证期窗口，config_id 随之改变。",
            "",
        ]
        lines += [f"- {strategy(by_id[c])} `{c[:12]}`" for c in fin["finalists"]]
        lines.append("")
    val = artifact(run_dir, "p6-validation.json")
    if val is not None:
        require_digest(ledger, "validation", val, "p6-validation.json")
        lines += [f"{level} 阶段 3：验证期", ""]
        lines += result_table(
            val["results"],
            lambda res: f"{strategy(by_id[res['config_id']])} `{res['config_id'][:12]}`",
        )
        lines += ["", f"选定：`{val['chosen'][:12]}`（按 IR → 换手 → config_id）。", ""]
    verdict = artifact(run_dir, "p6-holdout-verdict.json")
    if verdict is not None:
        lines += [f"{level} 保留期（一次性）", "", *holdout_lines(ledger, verdict)]
    return lines


def run_block(
    name: str, ledger_path: Path, run_dir: Path, survivors: Path, level: str
) -> list[str]:
    ledger = rows(ledger_path)
    return [
        f"账本 `research/{name}`：{len(ledger)} 行，提交 {commits_of(ledger)}。",
        "",
        *run_sections(ledger, run_dir, json.loads(survivors.read_text()), level),
    ]


def main() -> None:
    run = sys.argv[1]
    out_path = Path(sys.argv[2])
    run_dir = ROOT / run
    lines = run_block(
        f"{run}/p6-ledger.jsonl",
        run_dir / "p6-ledger.jsonl",
        run_dir,
        run_dir / "p6-survivors.json",
        "##",
    )
    lines += ["## 附：被取代的 r2 运行", ""]
    lines += run_block(
        "r2/p6-ledger.jsonl", R2 / "p6-ledger.jsonl", R2, R2 / "p6-survivors.json", "###"
    )
    lines += ["## 附：第一轮（基准有缺陷）", ""]
    lines += run_block(
        "p6-ledger.jsonl", ROOT / "p6-ledger.jsonl", ROOT, ROOT / "p6-survivors.json", "###"
    )
    out_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {out_path}: {len(lines)} lines")


if __name__ == "__main__":
    main()
