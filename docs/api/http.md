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
  A `session` sent here is **discarded**, not honoured and not refused: it is not a
  declared parameter of this endpoint, so nothing in it can see the value in order to
  object. A caller who copies a `/panel/health` query onto this path is answered a
  narrower question than the one they typed.
- `GET /api/v1/panel/health` returns the whole health report: per-dataset readiness and
  freshness, the cross-dataset checks with a record of which of them actually ran, and
  the inherent limitations kept separate from this fetch's defects. Distinct
  from `GET /health`, which is the dependency-free liveness probe.

  `limitations` carries two kinds of entry and they are told apart by `datasets`. An
  entry that **names** datasets is a boundary of those datasets — what `trade_cal` or
  `adj_factor` structurally cannot answer. An entry that names **none** is a boundary of
  the storage plane itself, true of every dataset alike: today, that
  `PanelStore.query()` passes no point-in-time gate, and that an edit changing values in
  place leaves the catalog's census intact. A report scoped to one dataset carries that
  dataset's entries and all of the plane's.
- `GET /api/v1/panel/gate` runs the fail-closed dependency gate.

`exchange` is required on all three, and when `calendar=false` it reaches nothing:
no calendar is loaded, so two well-formed exchange names produce byte-identical
responses and a misspelling cannot be detected — there is nothing to compare it
against, which is exactly what `calendar=false` asserts. An empty or whitespace-padded
name is refused on both settings, because no store could ever hold one. With
`calendar=true` a name the store has no calendar for is a `409`.

Status codes are a five-entry table (`api/app.py#PANEL_HTTP_STATUS`):

| Situation | Code |
|---|---|
| the endpoint answered | `200` |
| the gate refused this request | `409` |
| the exchange calendar this request names is not stored | `409` |
| the request cannot be put at all (unknown dataset, no dataset, naive `as_of`, malformed `exchange`) | `422` |
| the endpoint itself broke; nothing was judged | `500` |

`/panel/readiness` and `/panel/health` always answer `200` when the request could be
put — they are reports and grant nothing, so the verdict is `all_ready` / `is_clean` in
the body. Only `/panel/gate`'s `200` is a permission, which is why a refusal there is
`409` and never `200`. A `409` still carries the full body: every block with its code,
category, severity and detail, the notices, the unverified checks, and the health report
the verdict rests on. A `notice` never produces a non-2xx response.

### `panel doctor`'s exit 1 has no status code here

The CLI's `PanelExit` is this table's sibling, and **one row does not correspond**.
`openalpha panel doctor` exits `1` when the report is not `is_clean`; `GET
/api/v1/panel/health` answers `200` about that same panel, and so does
`/api/v1/panel/readiness` about a blocked dataset. No status code in the table above
means what that exit code means. A monitor that watches only the status code of
`/api/v1/panel/health` will therefore **never fire on a sick panel** — and the endpoint
being named `/health` in a service that also serves `GET /health` as a real liveness
probe makes that an easy rule to write. The HTTP equivalents are, in the body,
`is_clean == false` / `all_ready == false` / a non-zero `counts_by_severity`, or, as a
status code, a `409` from `/api/v1/panel/gate` — which answers a *different question*
("may this request read it") and will also refuse some panels the doctor calls healthy.

### `409` carries two body schemas; switch on `detail.reason`

`blocked` and `panel_unreadable` share `409` deliberately, but not a body. A client that
switched on the status code alone and read `json()["blocks"]` works on the first and
raises `KeyError` on the second.

| Body | Shape | Discriminator |
|---|---|---|
| gate verdict (`200` or `409`) | flat clearance: `is_blocked`, `blocks`, `cleared`, `report`, … | no `detail` key |
| panel refusal (`409`/`422`) | `{"detail": {"reason": …, "message": …}}` | `detail.reason` is the table row above (`panel_unreadable`, `bad_request`) |
| parameter validation (`422`) | FastAPI's own: `detail` is a **list** of error objects | `isinstance(detail, dict)` is false |

The two panel bodies share no key at all. `detail.message` is a disclosable text: it
names the exchange, the codes that stood in the way and the remedy, and never this
service's filesystem layout.

The portfolio endpoint is intentionally stateless: callers submit the immutable
`PortfolioState`, `PortfolioOrder`, `MarketBar`, and optional `PortfolioLimits`,
then persist the returned `PortfolioTransition` in their own workflow. It is a
research/backtest accounting surface, not a live-broker order endpoint.

The default declared request limit is 8 MiB. Configure it with
`OPENALPHA_MAX_REQUEST_BYTES`. The service is local-first and has no public
multi-tenant authentication; use a TLS/authentication gateway before any
network exposure.
