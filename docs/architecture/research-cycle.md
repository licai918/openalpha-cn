# Shared Research Cycle

OpenAlpha CN uses one `ResearchEngine.run_cycle` path for live, replay, and
backtest modes. The mode changes clocks and data adapters, not the decision core.

```text
point-in-time EvidenceSnapshot[]
  -> AgentRouter
  -> market/theme/capital agents
  -> durable node checkpoint
  -> validated SignalFrame[]
  -> deterministic aggregate SignalFrame
  -> RiskGate
  -> DecisionLedger + RunManifest
  -> append-only SQLite repository + durable research memory
```

## Built-in agents

- `market-agent`: limit-up, consecutive-board, and broken-board evidence
- `theme-agent`: theme, catalyst, and disclosure evidence
- `capital-agent`: normalized capital-flow evidence

They are deterministic baselines, not profitability claims. Every directional
signal cites visible evidence IDs and carries confirmation, invalidation, and
risk fields. Unsupported or insufficient evidence produces an explicit
abstention.

## Extension boundaries

- `ResearchAgent`: receives `AgentContext`, returns `AgentResult`
- `ResearchTool`: accepts `ToolRequest`, returns explicit success/no-data
- `ModelProvider`: returns JSON validated as `StructuredAgentPayload`
- `ResearchMemory`: append/list contract for decision-linked memory
- `RiskGate`: maps explicit risk flags to pass/reduce/block

Model-backed agents have a bounded retry budget. Outputs with the wrong subject,
clock, schema, or evidence IDs are rejected; exhausting retries raises
`ModelProviderFailure`.

`OpenAICompatibleProvider` is the built-in BYOK transport for compatible chat
completion APIs. It reads credentials only from an explicitly configured
environment variable, requires HTTPS except for explicit localhost endpoints,
requests structured JSON, and never copies the secret value into run metadata.

## Recovery

The recovery store records the immutable request digest, graph signature,
completed agent prefix, next agent index, attempt count, status, and error type
in the same SQLite WAL database as run records:

- every successful agent result is validated before the checkpoint advances;
- a restarted process resumes from the first unfinished agent;
- changing request inputs or the selected graph under the same `run_id` raises
  `RunConflictError`;
- after the decision and memory entry are durable, the recovery state becomes
  `succeeded`.

This is executable resume behavior, not only a checkpoint status record.

## Portfolio transition

Research decisions can be evaluated through the deterministic long-only
`PortfolioSimulator`. It tracks cash, acquisition-date lots, valuation marks,
fees, and realized PnL while reusing the A-share execution policy for board lots,
T+1, suspension, price limits, and transaction costs. Buy orders additionally
enforce single-position and total-exposure limits. The returned transition is
immutable and rejected orders leave the state unchanged.

## Idempotency

A completed `run_id` may be replayed only with identical immutable inputs. The
engine compares the newly computed manifest and decision with stored records:

- identical result: return without duplicate rows or memory;
- different result under the same `run_id`: raise `RunConflictError`.
