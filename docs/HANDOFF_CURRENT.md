# 当前交接入口

当前工作区：[`specs/v2/`](specs/v2/) — OpenAlpha CN v2 智能研究与选股平台改造

- 范围、实测基线与决策依据：[`specs/v2/openalpha-cn-v2-prd.md`](specs/v2/openalpha-cn-v2-prd.md)
- 阶段、闸门与任务清单：[`specs/v2/openalpha-cn-v2-roadmap.md`](specs/v2/openalpha-cn-v2-roadmap.md)

当前状态：v2 实现尚未开始。下一步是 P0 的 `T0.1` —— 扩展 `scripts/build_feature_coverage.py`
做 AST 符号校验，并修正 v1 台账中 7 处失效符号引用（PRD §1.3 B3 与 Implementation Decision 29）。

维护者依次读取：

1. 根目录 `AGENTS.md`
2. `specs/v2/openalpha-cn-v2-roadmap.md`（当前阶段与闸门）
3. `specs/v2/openalpha-cn-v2-prd.md`（范围与决策依据）
4. `specs/openalpha-cn-v1-spec.md`（v1 契约基线）
5. `release/openalpha-v1-feature-ledger.md`（能力台账；失效引用待修，见上）
6. `release/handoff-main-20260724-ten-capabilities.md`（v1 历史交接）
7. `audits/three-upstream-source-audit-20260724.md`
8. 与当前任务相关的架构、数据、API 或部署文档

历史发布交接不删除；新版本新建交接文件后只更新本入口。
