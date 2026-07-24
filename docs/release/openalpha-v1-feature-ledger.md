# OpenAlpha CN v1 100% 功能去向台账

> 100% 表示每项能力都有唯一 ID、证据和终态去向; 真实完成率仅计 `NATIVE_COMPLETE`、`ADAPTER_COMPLETE`、`ENHANCED_REPLACEMENT`。

## 对账结论

- 功能总数: 75
- 当前真实完成: 70 (93.33%)
- `UNREVIEWED=0`
- `UNKNOWN=0`

## 状态分布

| 状态 | 数量 |
|---|---:|
| `DEFERRED` | 1 |
| `ENHANCED_REPLACEMENT` | 16 |
| `EXCLUDED` | 4 |
| `NATIVE_COMPLETE` | 54 |

## 功能明细

| ID | 类别 | 功能 | 状态 | 源码证据 | 测试证据 |
|---|---|---|---|---|---|
| `OA-TIME-001` | temporal | Aware datetime contract | `NATIVE_COMPLETE` | `src/openalpha_cn/domain/time.py#ensure_aware` | `tests/unit/domain/test_time.py` |
| `OA-TIME-002` | temporal | Four-clock timeline | `NATIVE_COMPLETE` | `src/openalpha_cn/domain/time.py#Timeline` | `tests/unit/domain/test_time.py` |
| `OA-TIME-003` | temporal | Point-in-time visibility | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/domain/time.py#is_visible_at` | `tests/unit/domain/test_time.py;tests/unit/domain/test_evidence.py` |
| `OA-EVID-001` | evidence | Immutable evidence snapshot | `NATIVE_COMPLETE` | `src/openalpha_cn/domain/evidence.py#EvidenceSnapshot` | `tests/unit/domain/test_evidence.py` |
| `OA-EVID-002` | evidence | Content-addressed evidence | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/domain/evidence.py#EvidenceSnapshot` | `tests/unit/domain/test_evidence.py` |
| `OA-EVID-003` | evidence | Serialized evidence verification | `NATIVE_COMPLETE` | `src/openalpha_cn/evidence/service.py#parse_serialized_evidence` | `tests/integration/test_evidence_interfaces.py` |
| `OA-EVID-004` | evidence | Limit-up normalization | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/evidence/builder.py#EvidenceBuilder` | `tests/unit/evidence/test_builder.py` |
| `OA-EVID-005` | evidence | Broken-board normalization | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/evidence/builder.py#EvidenceBuilder` | `tests/unit/evidence/test_builder.py` |
| `OA-EVID-006` | evidence | Consecutive-board normalization | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/evidence/builder.py#EvidenceBuilder` | `tests/unit/evidence/test_builder.py` |
| `OA-EVID-007` | evidence | Disclosure normalization | `NATIVE_COMPLETE` | `src/openalpha_cn/evidence/builder.py#EvidenceBuilder` | `tests/unit/evidence/test_builder.py` |
| `OA-EVID-008` | evidence | Theme and catalyst normalization | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/evidence/builder.py#EvidenceBuilder` | `tests/unit/evidence/test_builder.py` |
| `OA-EVID-009` | evidence | Capital observation normalization | `NATIVE_COMPLETE` | `src/openalpha_cn/evidence/builder.py#EvidenceBuilder` | `tests/unit/evidence/test_builder.py` |
| `OA-DATA-001` | storage | Append-only run ledger | `NATIVE_COMPLETE` | `src/openalpha_cn/storage/sqlite.py#SQLiteRunRepository` | `tests/integration/storage/test_sqlite_repository.py` |
| `OA-DATA-002` | storage | Append-only decision ledger | `NATIVE_COMPLETE` | `src/openalpha_cn/storage/sqlite.py#SQLiteRunRepository` | `tests/integration/storage/test_sqlite_repository.py` |
| `OA-DATA-003` | storage | Checkpoint records | `NATIVE_COMPLETE` | `src/openalpha_cn/storage/sqlite.py#SQLiteRunRepository` | `tests/integration/storage/test_sqlite_repository.py` |
| `OA-DATA-004` | storage | Parquet evidence partitions | `NATIVE_COMPLETE` | `src/openalpha_cn/storage/parquet.py#ParquetEvidenceStore` | `tests/integration/storage/test_parquet_evidence_store.py` |
| `OA-DATA-005` | storage | DuckDB PIT query | `NATIVE_COMPLETE` | `src/openalpha_cn/storage/parquet.py#ParquetEvidenceStore` | `tests/integration/storage/test_parquet_evidence_store.py` |
| `OA-PROV-001` | provider | Shared provider contract | `NATIVE_COMPLETE` | `src/openalpha_cn/providers/base.py#DataProvider` | `tests/contract/providers/test_file_provider.py` |
| `OA-PROV-002` | provider | Explicit provider failures | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/providers/base.py#ProviderFailure` | `tests/contract/providers/test_tushare_provider.py;tests/contract/providers/test_akshare_provider.py` |
| `OA-PROV-003` | provider | File provider | `NATIVE_COMPLETE` | `src/openalpha_cn/providers/file.py#FileProvider` | `tests/contract/providers/test_file_provider.py` |
| `OA-PROV-004` | provider | Tushare BYOT adapter | `NATIVE_COMPLETE` | `src/openalpha_cn/providers/tushare.py#TushareProvider` | `tests/contract/providers/test_tushare_provider.py` |
| `OA-PROV-005` | provider | AKShare optional adapter | `NATIVE_COMPLETE` | `src/openalpha_cn/providers/akshare.py#AkShareProvider` | `tests/contract/providers/test_akshare_provider.py` |
| `OA-PROV-006` | provider | Provider policy metadata | `NATIVE_COMPLETE` | `src/openalpha_cn/providers/base.py#ProviderMetadata` | `tests/contract/providers/test_file_provider.py` |
| `OA-AGENT-001` | agent | Agent extension contract | `NATIVE_COMPLETE` | `src/openalpha_cn/agents/base.py#ResearchAgent` | `tests/integration/test_research_cycle.py` |
| `OA-AGENT-002` | agent | Market event agent | `NATIVE_COMPLETE` | `src/openalpha_cn/agents/baseline.py#MarketEventAgent` | `tests/integration/test_research_cycle.py` |
| `OA-AGENT-003` | agent | Theme catalyst agent | `NATIVE_COMPLETE` | `src/openalpha_cn/agents/baseline.py#ThemeCatalystAgent` | `tests/integration/test_research_cycle.py` |
| `OA-AGENT-004` | agent | Capital flow agent | `NATIVE_COMPLETE` | `src/openalpha_cn/agents/baseline.py#CapitalFlowAgent` | `tests/integration/test_research_cycle.py` |
| `OA-AGENT-005` | agent | Evidence-aware router | `NATIVE_COMPLETE` | `src/openalpha_cn/runtime/router.py#AgentRouter` | `tests/integration/test_research_cycle.py` |
| `OA-AGENT-006` | agent | Structured model boundary | `NATIVE_COMPLETE` | `src/openalpha_cn/agents/model.py#StructuredModelAgent` | `tests/unit/agents/test_model_agent.py` |
| `OA-AGENT-007` | agent | Bounded model retry | `NATIVE_COMPLETE` | `src/openalpha_cn/agents/model.py#StructuredModelAgent` | `tests/unit/agents/test_model_agent.py` |
| `OA-AGENT-008` | agent | Read-only evidence tool | `NATIVE_COMPLETE` | `src/openalpha_cn/tools/evidence.py#EvidenceLookupTool` | `tests/unit/tools/test_evidence_lookup.py` |
| `OA-AGENT-009` | agent | Deterministic baseline | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/agents/baseline.py#baseline_agents` | `tests/integration/test_research_cycle.py;tests/replay/test_frozen_corpus.py` |
| `OA-DEC-001` | decision | Typed signal frame | `NATIVE_COMPLETE` | `src/openalpha_cn/domain/signal.py#SignalFrame` | `tests/unit/domain/test_signal.py` |
| `OA-DEC-002` | decision | Typed abstention | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/domain/signal.py#SignalFrame` | `tests/unit/domain/test_signal.py` |
| `OA-DEC-003` | decision | Decision ledger | `NATIVE_COMPLETE` | `src/openalpha_cn/domain/decision.py#DecisionLedger` | `tests/unit/domain/test_records.py` |
| `OA-DEC-004` | decision | Risk gate | `NATIVE_COMPLETE` | `src/openalpha_cn/decisions/risk.py#RiskGate` | `tests/integration/test_research_cycle.py` |
| `OA-DEC-005` | decision | Run manifest | `NATIVE_COMPLETE` | `src/openalpha_cn/domain/run.py#RunManifest` | `tests/unit/domain/test_records.py` |
| `OA-DEC-006` | decision | Idempotent research recovery | `NATIVE_COMPLETE` | `src/openalpha_cn/runtime/engine.py#ResearchEngine` | `tests/integration/test_research_cycle.py` |
| `OA-DEC-007` | decision | Durable research memory | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/storage/memory.py#SQLiteResearchMemory` | `tests/integration/test_recovery_and_memory.py;tests/integration/test_sdk_golden_path.py` |
| `OA-MCR-001` | recovery | Node-level checkpoint resume | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/storage/recovery.py#SQLiteRecoveryStore;src/openalpha_cn/runtime/engine.py#ResearchEngine` | `tests/integration/test_recovery_and_memory.py` |
| `OA-MCR-002` | recovery | Request and graph signature isolation | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/storage/recovery.py#RunRecoveryState;src/openalpha_cn/runtime/engine.py#ResearchEngine` | `tests/integration/test_recovery_and_memory.py` |
| `OA-BT-001` | backtest | Shared run cycle | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/runtime/engine.py#ResearchEngine` | `tests/integration/test_research_cycle.py;tests/replay/test_frozen_corpus.py` |
| `OA-BT-002` | backtest | Frozen replay corpus | `NATIVE_COMPLETE` | `src/openalpha_cn/backtest/replay.py#ReplayCorpus` | `tests/replay/test_frozen_corpus.py` |
| `OA-BT-003` | backtest | Determinism replay | `NATIVE_COMPLETE` | `src/openalpha_cn/backtest/replay.py#ReplayRunner` | `tests/replay/test_frozen_corpus.py` |
| `OA-BT-004` | backtest | Look-ahead guard | `NATIVE_COMPLETE` | `src/openalpha_cn/backtest/replay.py#ReplayCase` | `tests/replay/test_frozen_corpus.py` |
| `OA-BT-005` | backtest | A-share execution constraints | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/backtest/execution.py#AShareExecutionModel` | `tests/unit/backtest/test_execution.py` |
| `OA-BT-006` | backtest | A-share transaction costs | `NATIVE_COMPLETE` | `src/openalpha_cn/backtest/execution.py#AShareCostModel` | `tests/unit/backtest/test_execution.py` |
| `OA-BT-007` | backtest | Outcome validation | `NATIVE_COMPLETE` | `src/openalpha_cn/backtest/validation.py#OutcomeValidator` | `tests/unit/backtest/test_validation.py` |
| `OA-BT-008` | backtest | Rule factor agent attribution | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/backtest/validation.py#OutcomeValidator` | `tests/unit/backtest/test_validation.py` |
| `OA-BT-009` | backtest | Portfolio cash and holdings simulator | `ENHANCED_REPLACEMENT` | `src/openalpha_cn/backtest/portfolio.py#PortfolioSimulator` | `tests/unit/backtest/test_portfolio.py;tests/integration/test_portfolio_interfaces.py` |
| `OA-IFACE-001` | interface | Versioned REST API | `NATIVE_COMPLETE` | `src/openalpha_cn/api/app.py#create_app` | `tests/integration/test_evidence_interfaces.py` |
| `OA-IFACE-002` | interface | Python SDK | `NATIVE_COMPLETE` | `src/openalpha_cn/sdk.py#OpenAlphaSDK` | `tests/integration/test_sdk_golden_path.py` |
| `OA-IFACE-003` | interface | CLI evidence flow | `NATIVE_COMPLETE` | `src/openalpha_cn/cli.py#evidence_build` | `tests/integration/test_evidence_interfaces.py` |
| `OA-IFACE-004` | interface | CLI research flow | `NATIVE_COMPLETE` | `src/openalpha_cn/cli.py#research_run` | `tests/integration/test_cli_research.py` |
| `OA-IFACE-005` | interface | CLI replay flow | `NATIVE_COMPLETE` | `src/openalpha_cn/cli.py#replay_run` | `tests/unit/test_cli.py` |
| `OA-IFACE-006` | interface | Research workbench | `NATIVE_COMPLETE` | `web/src/App.tsx#App` | `web/src/App.test.tsx;web/e2e/golden-flow.spec.ts` |
| `OA-IFACE-007` | interface | Explicit UI states | `NATIVE_COMPLETE` | `web/src/App.tsx#App` | `web/src/App.test.tsx;web/e2e/golden-flow.spec.ts` |
| `OA-IFACE-008` | interface | JSON schema distribution | `NATIVE_COMPLETE` | `src/openalpha_cn/domain/schema.py#export_schemas` | `tests/unit/domain/test_schema_export.py` |
| `OA-MODEL-001` | model | Secure OpenAI-compatible BYOK provider | `NATIVE_COMPLETE` | `src/openalpha_cn/models/openai_compatible.py#OpenAICompatibleProvider` | `tests/unit/models/test_openai_compatible.py;tests/integration/test_sdk_golden_path.py` |
| `OA-OPS-001` | operations | Locked Python environment | `NATIVE_COMPLETE` | `pyproject.toml#project;uv.lock` | `tests/unit/test_repository_assets.py` |
| `OA-OPS-002` | operations | Locked web environment | `NATIVE_COMPLETE` | `web/package.json#packageManager;web/pnpm-lock.yaml` | `web/src/App.test.tsx` |
| `OA-OPS-003` | operations | Cross-platform CI | `NATIVE_COMPLETE` | `github:.github/workflows/quality.yml` | `tests/unit/test_repository_assets.py` |
| `OA-OPS-004` | operations | Multi-stage container | `NATIVE_COMPLETE` | `Dockerfile#runtime` | `tests/unit/test_repository_assets.py` |
| `OA-OPS-005` | operations | Persistent container volume | `NATIVE_COMPLETE` | `deploy/compose.yml#openalpha-runtime` | `scripts/verify_compose_recovery.py` |
| `OA-OPS-006` | operations | Read-only container hardening | `NATIVE_COMPLETE` | `deploy/compose.yml#services` | `tests/unit/test_repository_assets.py` |
| `OA-OPS-007` | operations | Browser security headers | `NATIVE_COMPLETE` | `src/openalpha_cn/api/app.py#SecurityHeadersMiddleware` | `tests/integration/test_evidence_interfaces.py` |
| `OA-OPS-008` | operations | Request size boundary | `NATIVE_COMPLETE` | `src/openalpha_cn/api/app.py#SecurityHeadersMiddleware` | `tests/integration/test_evidence_interfaces.py` |
| `OA-OPS-009` | operations | Secret and artifact publication gate | `NATIVE_COMPLETE` | `scripts/verify_publication.py#main` | `tests/unit/test_repository_assets.py` |
| `OA-OPS-010` | operations | Dependency vulnerability audit | `NATIVE_COMPLETE` | `github:.github/workflows/quality.yml` | `tests/unit/test_repository_assets.py` |
| `OA-OPS-011` | operations | MIT source release | `NATIVE_COMPLETE` | `LICENSE#MIT;THIRD_PARTY_NOTICES.md` | `tests/unit/test_repository_assets.py` |
| `OA-BOUND-001` | boundary | No live broker execution | `EXCLUDED` | `docs/specs/openalpha-cn-v1-spec.md#Non-goals` | `docs/specs/openalpha-cn-v1-spec.md` |
| `OA-BOUND-002` | boundary | No short or cover execution | `EXCLUDED` | `docs/specs/openalpha-cn-v1-spec.md#Non-goals` | `docs/specs/openalpha-cn-v1-spec.md` |
| `OA-BOUND-003` | boundary | No hosted commercial data proxy | `EXCLUDED` | `docs/data/providers.zh-CN.md#Redistribution` | `tests/unit/test_repository_assets.py` |
| `OA-BOUND-004` | boundary | No embedded provider secrets | `EXCLUDED` | `SECURITY.md#Credential-handling` | `tests/unit/test_repository_assets.py` |
| `OA-BOUND-005` | boundary | Graphical agent flow builder | `DEFERRED` | `docs/specs/openalpha-cn-v1-spec.md#Out-of-scope` | `docs/specs/openalpha-cn-v1-spec.md` |

## 边界

- `EXCLUDED` 和 `DEFERRED` 也是已审计终态, 不计入真实完成率。
- 源码 MIT 许可不覆盖第三方数据、品牌素材或链邻桌面安装程序。
- 台账由 `scripts/build_feature_coverage.py` 生成并在 CI 中逐文件复核。
