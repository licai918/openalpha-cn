# Shared Research Cycle

OpenAlpha CN uses one `ResearchEngine.run_cycle` path for live, replay, and
backtest modes. The mode changes clocks and data adapters, not the decision core.

```text
point-in-time EvidenceSnapshot[]
  -> AgentRouter
  -> market/theme/capital agents
  -> validated SignalFrame[]
  -> deterministic aggregate SignalFrame
  -> RiskGate
  -> DecisionLedger + RunManifest
  -> append-only SQLite repository + research memory
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

## Idempotency

A completed `run_id` may be replayed only with identical immutable inputs. The
engine compares the newly computed manifest and decision with stored records:

- identical result: return without duplicate rows or memory;
- different result under the same `run_id`: raise `RunConflictError`.
