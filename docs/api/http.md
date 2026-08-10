# OpenAlpha CN HTTP API

Start the local API:

```powershell
uv run openalpha serve --host 127.0.0.1 --port 8000
```

Useful local URLs:

- Health: `GET http://127.0.0.1:8000/health`
- OpenAPI: `GET http://127.0.0.1:8000/openapi.json`
- Interactive docs: `http://127.0.0.1:8000/docs`

## Build evidence

`POST /api/v1/evidence/build` accepts a validated `ProviderMetadata` and
`ProviderBatch`. It returns the same `EvidenceBuildResponse` used by:

```powershell
uv run openalpha evidence build .\events.json `
  --as-of 2026-07-24T10:30:00+00:00 `
  --source-id user.file `
  --source-license user-supplied `
  --redistribution restricted
```

The HTTP endpoint intentionally accepts structured records rather than a server
filesystem path. Local file access remains a CLI responsibility.

## Research, replay, and attribution

- `POST /api/v1/research/run` executes the shared research core from verified evidence.
- `POST /api/v1/research/batches` queues bounded concurrent research;
  `GET /api/v1/research/batches/{batch_id}` and `/events` expose durable state
  and progress; `/cancel` and `/retry` are explicit control operations.
- `POST /api/v1/research/deliberate` returns evidence-linked bull/bear cases,
  three risk perspectives, and an ablation delta.
- `POST /api/v1/screen` filters verified research results; `GET/POST
  /api/v1/watchlist` manages the local observation pool; `GET/POST
  /api/v1/reports` manages immutable generated reports.
- `GET /api/v1/memory/{subject}` returns durable decision-linked research memory.
- `GET /api/v1/runs/{run_id}/recovery` exposes the durable node checkpoint used
  to resume an interrupted run; an unknown run returns `404`.
- `POST /api/v1/backtests/replay` executes a supplied versioned frozen corpus.
- `POST /api/v1/portfolio/execute` applies one deterministic A-share portfolio
  transition, including cash, T+1, board-lot, suspension, price-limit, fee, FIFO,
  single-position, and total-exposure checks.
- `GET /api/v1/portfolio/ledger` lists immutable accepted/rejected transitions.
- `POST /api/v1/backtests/portfolio` returns multi-day return, benchmark,
  active return, turnover, capacity, and exposure attribution.
- `POST /api/v1/backtests/event-study` computes CAR, t-statistic, and a seeded
  Bootstrap confidence interval.
- `POST /api/v1/backtests/validate` accepts a previously returned research result and a future outcome observation, verifies content-derived IDs, and returns reconciled attribution.

## Panel readiness, health, and the dependency gate

Three read-side endpoints over the point-in-time panel plane at `runtime_dir/panel`,
paired one-for-one with `OpenAlphaSDK.panel_readiness` / `panel_health` /
`panel_clearance` and asserted equivalent to them
(`tests/integration/test_panel_interfaces.py`). All three take the same query
parameters: repeated `dataset` and `year` (both required — nothing is inferred),
required `as_of` (ISO-8601, **timezone-aware**), required `exchange`, required
`calendar` (`true`/`false`), plus repeated optional `session` and `index_code`.
`/panel/readiness` takes no `session`.

- `GET /api/v1/panel/readiness` returns each named dataset's own readiness verdict —
  `state`, `issues`, and `checks_waived`, which says which questions were never put.
- `GET /api/v1/panel/health` returns the whole health report: per-dataset readiness and
  freshness, the cross-dataset checks with a record of which of them actually ran, and
  the datasets' inherent limitations kept separate from this fetch's defects. Distinct
  from `GET /health`, which is the dependency-free liveness probe.
- `GET /api/v1/panel/gate` runs the fail-closed dependency gate.

Status codes are a four-entry table (`api/app.py#PANEL_HTTP_STATUS`):

| Situation | Code |
|---|---|
| the endpoint answered | `200` |
| the gate refused this request | `409` |
| the exchange calendar this request names is not stored | `409` |
| the request cannot be put at all (unknown dataset, no dataset, naive `as_of`) | `422` |

`/panel/readiness` and `/panel/health` always answer `200` when the request could be
put — they are reports and grant nothing, so the verdict is `all_ready` / `is_clean` in
the body. Only `/panel/gate`'s `200` is a permission, which is why a refusal there is
`409` and never `200`. A `409` still carries the full body: every block with its code,
category, severity and detail, the notices, the unverified checks, and the health report
the verdict rests on. A `notice` never produces a non-2xx response.

The portfolio endpoint is intentionally stateless: callers submit the immutable
`PortfolioState`, `PortfolioOrder`, `MarketBar`, and optional `PortfolioLimits`,
then persist the returned `PortfolioTransition` in their own workflow. It is a
research/backtest accounting surface, not a live-broker order endpoint.

The default declared request limit is 8 MiB. Configure it with
`OPENALPHA_MAX_REQUEST_BYTES`. The service is local-first and has no public
multi-tenant authentication; use a TLS/authentication gateway before any
network exposure.
