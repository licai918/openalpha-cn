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
  An evidence payload whose `quality_flags` names a string outside the declared risk-flag
  vocabulary is refused `422` with a **field-error list** — the same body shape pydantic writes
  for `signal.risk_flags` on `/api/v1/research/deliberate`, with the offending `input` echoed,
  every declared flag in `msg`, and a `loc` addressing the exact item and position, e.g.
  `["body", "evidence", 1, "payload", "quality_flags", 1]` (`V2-P4-101`). It answered
  `500 text/plain` until that issue: `EvidenceSnapshot.payload` is an unschema'd `JsonValue`, so
  pydantic cannot check the flag and the refusal is raised further in, by `agents/baseline.py`.
- `POST /api/v1/research/batches` queues bounded concurrent research;
  `GET /api/v1/research/batches/{batch_id}` and `/events` expose durable state
  and progress; `/cancel` and `/retry` are explicit control operations. A failed item's
  `error_type` is the specific exception class; the whole reason, where it is disclosable,
  is on the `item_failed` progress event's `detail` under `/events` (`V2-P4-102`).
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
  active return, turnover, capacity, and exposure attribution. **Each `steps[]` entry is one
  trading session, not one order** (`V2-P5-003`): it carries `trade_date`, the session's `bars`
  (one per subject, all on that date), the `orders` placed into them (possibly none), and one
  `benchmark_close`. The book is marked to the session's closes *before* the session's orders
  run, so a cap is judged on the prices of the day the order was placed. A held name the session
  serves no bar for keeps its price and is reported in `carried_marks`; an order whose subject
  has no bar on its session is a `422` rather than a `rejected` transition inside a `200`.
  **Known gap, stated because it is a regression and not a design**: the *series* refusals
  `V2-P5-003` added -- sessions that are not strictly ascending, a first session before the
  opening state, an opening book with no equity -- are raised by
  `PortfolioBacktestRunner.run` as `ValueError` **after** request validation has passed, and
  this route does not map them, so each comes back as a **`500`** where it should be a `422`.
  The body is correct in every other respect (nothing is written to the ledger: the series is
  checked before the first order runs) and the fix is the three-line `except ValueError as
  error: raise HTTPException(status_code=422, detail=str(error)) from error` this file's
  neighbours already use -- measured to produce `422 multi-day backtest sessions must be
  strictly ascending by date`. It was not applied because `api/app.py` was held by a
  concurrent change while `V2-P5-003` landed.
- `POST /api/v1/backtests/event-study` computes CAR, t-statistic, and a seeded
  Bootstrap confidence interval.
- `POST /api/v1/backtests/validate` accepts a previously returned research result and a future outcome observation, verifies content-derived IDs, and returns reconciled attribution.

## Batch research: two ceilings, one listing, and how each refusal reads

### `max_concurrency` is capped at 8, and was 32 (`V2-P4-042`)

`POST /api/v1/research/batches` takes `max_concurrency` between `1` and **`8`**; `9` or more is
refused `422` with pydantic's own `Input should be less than or equal to 8`. **It was `32` until
`V2-P4-019`**, so a request that worked before that release now fails, and this paragraph is the
place a caller who met that `422` can find out why.

The ceiling was lowered because the parallelism above it was advertised and not delivered. Every
item transition is persisted and every store call in `BatchResearchService` happens under one
process-wide lock, so past a handful of workers the extra threads queue rather than write.
Measured with a runner that sleeps 10 ms to stand in for a model-provider call, N=600:

| `max_concurrency` | 1 | 2 | 4 | 8 | 16 | 32 |
|---|---|---|---|---|---|---|
| items/sec | 57 | 114 | 211 | 184 | 201 | 216 |

Near-linear to 4, then flat inside the noise band all the way to 32. With the real research
engine the plateau arrives at 2, because that work is CPU-bound. `8` is one doubling above the
highest measured plateau, so a slower real-network runner has room while the number stops
claiming a 32x that does not exist. Raising it back is not a supported knob: it is a property of
how batch state is persisted, not a throttle.

A batch may carry up to **10,000** items (`MAX_BATCH_ITEMS`), which is above `V2-P4-004`'s
measured A-share market of 5,545 listed names on purpose — see the request-size section below,
because the two numbers have to be read together.

### `GET /api/v1/research/batches` answers summaries, not items (`V2-P4-040`)

The listing returns a page of **summaries**:

```json
{
  "batches": [
    {
      "batch_id": "whole-market-2026-08-14",
      "status": "partial",
      "max_concurrency": 8,
      "cancellation_requested": false,
      "created_at": "2026-08-14T01:30:00Z",
      "updated_at": "2026-08-14T02:11:04Z",
      "item_count": 5545,
      "items_by_status": {
        "queued": 0, "running": 0, "succeeded": 5502, "failed": 43, "cancelled": 0
      }
    }
  ],
  "total": 20,
  "limit": 50,
  "offset": 0
}
```

`limit` defaults to `50` and may not exceed `500`; `offset` defaults to `0`. `total` is the whole
shelf, not the window. `items_by_status` is **total** over the five item states, so a zero means
zero and never "not counted".

**The items are one route away**: `GET /api/v1/research/batches/{batch_id}` returns the full
`BatchResearchTask` with every item, unchanged.

This route previously inlined every item of every batch. Twenty whole-market batches — roughly a
trading month — measured `items: 115,355, bytes: 36,857,096` (36.9 MB) in 2.35 s, and three
batches already exceeded the request ceiling this same service enforces on the way *in*. If you
were reading `items` off this route, read it off `/batches/{batch_id}` instead.

### Request size: `OPENALPHA_MAX_REQUEST_BYTES` (`V2-P4-043`, `V2-P5-012`)

A request body larger than the configured ceiling is refused `413`. There are two gates and the
refusal says which one answered.

**A body that declares a `Content-Length` above the ceiling is refused before it is read at
all** — nothing is allocated, and the routes are never called:

```json
{"detail": {
  "reason": "request_too_large",
  "message": "the request declared 41000000 bytes against a configured ceiling of 33554432. Raise OPENALPHA_MAX_REQUEST_BYTES on the server, or send fewer items per request.",
  "declared_bytes": 41000000,
  "measured_bytes": null,
  "limit_bytes": 33554432
}}
```

**A body that declares no length — `Transfer-Encoding: chunked` — is metered as it arrives**
(`V2-P5-012`). Until that row, this ceiling was read off `Content-Length` and nothing else, so a
chunked request bypassed it entirely: measured against a 1,024-byte ceiling, a 36,000,030-byte
chunked body was read in full and answered `422` by the JSON parser, with a `tracemalloc` peak of
108,346,472 bytes. It is now counted chunk by chunk and reading **stops** at the ceiling:

```json
{"detail": {
  "reason": "request_too_large",
  "message": "the request body reached 33554433 bytes without declaring a Content-Length, against a configured ceiling of 33554432. Reading stopped there, so that number is a floor and not the body's size. Raise OPENALPHA_MAX_REQUEST_BYTES on the server, or send fewer items per request.",
  "declared_bytes": null,
  "measured_bytes": 33554433,
  "limit_bytes": 33554432
}}
```

`reason` and `limit_bytes` are the same from either gate, so a client switches on `reason`
exactly as before. `declared_bytes` and `measured_bytes` are both always present and exactly one
is non-null: `declared_bytes` is what you said, `measured_bytes` is where reading stopped — a
**floor** on the body and never its size, because the rest was never asked for.

One case is deliberately still not metered: a body sent to a route that never reads one (a `404`,
or a `GET` carrying a body). Nothing is accumulated there, because nothing asks for it.

The default is **33554432** bytes (32 MiB), raised from 8 MiB by `V2-P4-043`. 8 MiB was smaller
than the ceilings this service declares elsewhere, so two of its own limits contradicted each
other. Measured, one evidence snapshot per request:

| request | at | bytes | at 8 MiB |
|---|---|---|---|
| `POST /api/v1/research/batches` | 10,000 items | 9,840,054 | `413` |
| `POST /api/v1/screen` | 10,000 names | 14,770,051 | `413` |
| `POST /api/v1/screen` | 5,545 names (the measured market) | 8,190,016 | `200` |

The first row is the one that decided the new default: `MAX_BATCH_ITEMS` is 10,000 *deliberately*
— because the market is a moving number — and a batch at exactly that ceiling could not be
posted at all. The third shows how little headroom the market had left: 198,592 bytes, about 134
more listings. A real caller sends more evidence per request than these measurements do, which is
why the new ceiling is a factor above the measurement rather than fitted to it.

`POST /api/v1/screen` now also states its own item ceiling — the same 10,000 — so one name too
far is a `422` naming the number rather than a `413` about bytes.

### What a browser sees: CORS and the response headers (`V2-P5-011`, `V2-P5-012`)

Every response carries nine hardening headers, and they **replace** rather than add to whatever a
route set:

| header | value |
|---|---|
| `content-security-policy` | `default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'` |
| `x-content-type-options` | `nosniff` |
| `x-frame-options` | `DENY` |
| `referrer-policy` | `no-referrer` |
| `permissions-policy` | `camera=(), microphone=(), geolocation=()` |
| `cross-origin-opener-policy` | `same-origin` |
| `cross-origin-embedder-policy` | `require-corp` |
| `cross-origin-resource-policy` | `same-origin` |
| `strict-transport-security` | `max-age=31536000; includeSubDomains` |

`strict-transport-security` is sent unconditionally and a user agent must ignore it over plain
HTTP (RFC 6797 §7.2), so it does nothing on `http://127.0.0.1:8000` and is the header a
TLS-terminating reverse proxy needs the application to emit. `preload` is deliberately absent —
that is a near-irreversible commitment about a domain, and an operator makes it, not a library.

**CORS** allows the two local Vite dev origins (`http://127.0.0.1:5173`,
`http://localhost:5173`), the header `Content-Type`, no credentials, and the methods
`DELETE, GET, HEAD, PATCH, POST, PUT` — which is exactly what
`Access-Control-Allow-Methods` carries. `OPTIONS` is not among them and does not need to be: the
preflight *is* the `OPTIONS` request, and what is checked against this list is the value of
`Access-Control-Request-Method`. The method list is wider than the routes this
service serves today, on purpose: advertising a method no route serves costs a `405` — this
service's own answer — while omitting one it does serve costs a browser-level refusal the caller
cannot see. Before `V2-P5-011` the list was `GET, POST`, and `HEAD` was already refused despite
four `HEAD` routes existing.

CORS is the **outermost** layer, so a `413` or a `400` decided before any route runs still
carries `Access-Control-Allow-Origin`. A CORS preflight (`OPTIONS` with
`Access-Control-Request-Method`) is answered by that layer and does not carry the hardening
headers; it renders nothing and has no body.

None of this is authentication. This service has none, and CORS restricts browsers only.

### A refused research result names the record and the identifier (`V2-P4-041`)

`POST /api/v1/screen`, `POST /api/v1/reports` and `POST /api/v1/backtests/validate` all rebuild a
returned `ResearchRunResult` and re-derive its three content addresses. A mismatch is `422` with
the `{"reason", "message"}` object the panel refusals use, plus the record's own coordinates:

```json
{"detail": {
  "reason": "decision_id_mismatch",
  "message": "research[17] (subject 000017.SZ) carries decision_id 'dec_…' but its own content derives 'dec_…'. All three identifiers on a research result are content-derived, so send the record back exactly as this service handed it out, or omit the identifier and let it be re-derived.",
  "index": 17,
  "subject": "000017.SZ",
  "field": "research[17].decision.decision_id",
  "claimed": "dec_…",
  "derived": "dec_…"
}}
```

`reason` is one of `signal_id_mismatch`, `decision_id_mismatch`, `run_manifest_id_mismatch`, or
`malformed_research_result` for a body that is not a research result at all. `index` is the
record's position in `research` and is `null` on the two routes that take a single result. The
refusal is on the **first** faulty record, so a 5,545-record body answers with one sentence and
not a wall of them. Before `V2-P4-041` all four causes came back as the single string
`Research result failed integrity validation.`

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
| declared ceiling (`422`) | `{"detail": {"reason": "declared_ceiling_exceeded", "message": …, "field": …, "limit": …, "received": …}}` | `detail.reason == "declared_ceiling_exceeded"` |

The two panel bodies share no key at all. `detail.message` is a disclosable text: it
names the exchange, the codes that stood in the way and the remedy, and never this
service's filesystem layout.

### A `422` never quotes the request back

`V2-P4-040` again, met inside `V2-P4-043`'s own fix. Pydantic's error objects carry
`input` — the value that was refused — so a collection one item over a declared
ceiling used to be answered with the collection. Measured: `POST /api/v1/screen` with
10,001 records (14,771,528 bytes in) replied **13,821,594 bytes**, and
`POST /api/v1/research/batches` with 10,001 requests replied **9,261,138 bytes**. A
misspelled top-level key was worse in proportion — two errors, each echoing the whole
body, **1.87×** the request at only 200 records.

Two rules now bound every `422` this service issues, and neither depends on the size of
the request:

- **A ceiling this service declares** (`max_length`/`min_length` on a collection) answers
  with the object in the table above, naming `field`, `limit` and `received`. It is a
  refusal this service decided, so it is switchable on `detail.reason` exactly as the
  panel refusals and `POST /api/v1/screen`'s malformed-record refusal are. A ceiling
  fault reported *alongside* per-item faults is derivative — a body whose items all fail
  leaves nothing behind and trips `min_length` — so in that case the item faults are the
  answer and the list shape below is what you get.
- **Everything else** keeps FastAPI's list, because `loc` plus a small echo is the useful
  answer to "you sent a string where a float goes". Each entry's `input` is replaced by
  `"<list of 10001 elided>"` (kind and size) once it exceeds `MAX_ECHOED_INPUT_BYTES`
  (512), and the list itself stops at `MAX_VALIDATION_ERRORS` (20) followed by one final
  entry — `{"loc": [], "type": "errors_elided", "msg": "N further validation error(s)
  were not listed"}` — so a truncated list always says it was truncated. Every entry
  still carries a `loc`, so the shape rule above is unchanged.

## Factor declarations

`GET /api/v1/factors` serves everything this build declares: every factor, transform and
neutralisation, each with its full prose note, plus the three tables a `factor run`
answer cannot be read without — the tier order, what each of the six verdicts means, and
which of the six attribution cells the acceptance criterion is decided on. It reads no
store, so it answers before any panel exists; it is how a caller finds out what to build.

`GET /api/v1/factors?factor=reversal_1d/v1` (or `?transform=…`, or
`?neutralization=…`) serves one declaration instead of all of them. Exactly one of the
three query parameters, because they name three registries rather than three spellings of
one; naming none serves the whole catalog, naming two is `422`. A **query parameter**
rather than a path segment because a handle is `key/vN` and contains a `/`: a
`GET /api/v1/factors/{handle}` route would need a `:path` converter, which would shadow
`/api/v1/factors/run` and `/api/v1/factors/experiments`.

`factor` also accepts a `fct_…` content address; the other two take their qualified key.
The refusal for an unknown handle names the **declared handles**, never the content
addresses — a caller holding an address already has it, and a caller who mistyped a key
needs the keys.

The body is `{"kind", "handle", "identity", "declaration", "note"}` per entry, where
`declaration` is the contract's own model dump and `identity` is its content address over
exactly those fields. Perturb any key of `declaration` and `identity` moves, which is what
makes the whole body auditable key by key.

The twins are `openalpha factor list` / `openalpha factor describe` and
`OpenAlphaSDK.factor_catalog()` / `.describe_factor()`, all three through
`factor_view.factor_catalog` and `factor_view.factor_entry`.

## Building the tiers a run reads

**There is no HTTP route that builds a factor panel, and that is deliberate.**
`openalpha panel build` has no HTTP twin either: building writes panel partitions, a
partition is replaced whole, and this service ships with no authentication of its own, so
a `POST` that replaced a stored partition would hand that to whoever could reach the port.
The builder is `openalpha factor build` and `OpenAlphaSDK.build_factor_panels(...)`.

A store that only `openalpha panel build` ever wrote holds **no factor partition**, so
`POST /api/v1/factors/run` against it answers `409` with `detail.reason =
"panel_unreadable"` and `partition_missing` on the `factor_obs_…` dataset. That is not a
defect in the request: run `openalpha factor build` first.

## Factor experiments

`POST /api/v1/factors/run` runs one factor's three-tier experiment over a closed range
of prediction days, seals it, and stores it. It is the HTTP twin of
`openalpha factor run` and of `OpenAlphaSDK.run_factor_experiment`; all three resolve
through `factor_view.factor_request` and run through `factor_view.run_factor_experiment`.

**Nineteen body fields and not one of them has a default.** `factor`, `transform`,
`neutralization`, `start`, `end`, `as_of`, `exchange`, `horizon`, `ic_method`,
`min_securities`, `min_as_ofs`, `group_count`, `min_securities_per_group`,
`position_capital`, `min_periods`, `participation_cap`, `min_rebalances`,
`redundancy_threshold` and `retention_floor` are each a floor or a policy one of the four
upstream studies refuses to choose for a caller. `code_commit` may be omitted and is then
resolved server-side, for the reason `POST /api/v1/research/run` resolves it: a browser
cannot know the server's own commit. `note` is optional prose and reaches no digest.

`factor` is the **qualified key** (`reversal_1d/v1`) or the opaque `factor_id`
(`fct_…`), told apart by the `/` separator. `start`/`end` bound **prediction days** — the
days the stored cross sections were computed at — and `as_of` is the instant the panel is
read at and the experiment is evaluated at. It must be at or after `end`, because a
forward return is priced on sessions after its prediction day.

`GET /api/v1/factors/experiments` lists every held `experiment_id`;
`GET /api/v1/factors/experiments/{experiment_id}` serves one, reopened through the seal.

The body of an answer is four keys plus the sealed document
(`factor_view.experiment_view`): `schema_version`, `experiment_id`, `content_digest`,
`write` (`created` or `unchanged`) and `document`. The document is byte-for-byte what the
store holds, so a client can recompute its seal.

Status codes are a seven-entry table (`api/app.py#FACTOR_HTTP_STATUS`):

| Situation | Code |
|---|---|
| the experiment was assembled and sealed | `200` |
| the stored tiers cannot answer as asked (no cross section in range; the three tiers were not built at the same instants) | `409` |
| a partition this run needs is missing, damaged, stale, or holds rows that were not knowable at `as_of` | `409` |
| the document store refused a second, different answer under a held `experiment_id` | `409` |
| the request cannot be put at all (unknown factor, backwards range, `as_of` before `end`, floor outside `(0, 1]`) | `422` |
| nothing is held under that `experiment_id` (the `GET` route only) | `404` |
| the endpoint itself broke; nothing was judged | `500` |

**A refused run answers `409`, never `200` with an empty body.** A run whose three stored
tiers were not built at the same instants produces no artifact, and the neutralised row is
the one a three-tier report's verdict is decided on — so an endpoint that answered `200`
with a report whose third row measured nothing would let a client conclude "the factor
survives neutralisation" about a tier that was never built. The three `409` rows share the
code and are told apart by `detail.reason`, exactly as the panel plane's two are.

`openalpha factor run` maps the same names onto exit codes (`cli.py#FACTOR_EXIT`) and
reuses `PanelExit`: `0` answered, `1` for all three `409` rows, `3` for `422`, `5` for an
unhandled defect. **Exit `0` includes an experiment whose grid says `removed` on every
cell** — a `removed` verdict is the report succeeding at its job, and an exit code that
treated it as failure would make every honest three-tier report look like a broken
command.

### Exit `0` and `200` also include a grid that says `not_measured` on every cell

This is the sentence this document was missing, and it is the more dangerous of the two.
A `removed` grid is a finding. A `not_measured` grid is **no finding at all**: one of the
two tiers in every cell carries no statistic, because its own coverage code is not
`measured`. It still assembles, so it still exits `0` and still answers `200` — and to a
reader (or a CI step) that greps the body for `removed`, finds nothing and stops, it looks
exactly like a clean pass. It is not. Two of the three tiers may never have computed a
number.

The shipped configuration reaches this state routinely: `cross_section_standard/v1` and
`industry_and_size/v1` both declare `min_cross_section=100`, so on a market narrower than
that both derived tiers store a coverage code for every name and no value.

How to tell, in order of directness:

- **`document.artifact.tiers[].ic.coverage`** — the per-tier truth. `measured` is the only
  value that means a number exists; anything else (`insufficient_as_ofs`,
  `insufficient_securities`, …) means that tier's cells are `not_measured` by construction.
- **`document.artifact.attributions[].verdict`** — check for the *presence* of a verdict
  you can act on, never for the absence of `removed`.
- **`openalpha factor run`** prints a named `WARNING` line on **stderr** when every cell is
  `not_measured`, in both `--json` and plain modes (stdout stays exactly the sealed
  envelope). `factor_view.everything_is_unmeasured` is the predicate; there is no such
  warning over HTTP, because a response body has no second channel.

This is deliberately **not** a fourth exit code or a non-2xx status. An all-`not_measured`
experiment did assemble, its record is sealed and worth keeping, and each tier already
carries its own four coverage codes — a coarser signal on the envelope would be a fifth
vocabulary for "not enough data", which the artifact contract refuses to add.

### Which grid cell is the answer

The grid has six cells: two statistics (`mean_ic`, `mean_spread`) over three steps
(`raw->processed`, `processed->neutralized`, `raw->neutralized`). They are not equals.
**`processed->neutralized` is the step the acceptance criterion is read off** — a statistic
that vanishes there was the industry and size exposure, and no transform setting recovers
it. `GET /api/v1/factors` flags it in `attribution_cells[].decides_the_acceptance_criterion`
and `openalpha factor run` marks the row inline.

The six verdicts (`survives`, `removed`, `reversed`, `amplified`, `no_baseline`,
`not_measured`) are served with one sentence each in `GET /api/v1/factors` under
`verdicts`, and printed by `openalpha factor list`.

## Shortlists

`POST /api/v1/shortlists/run` cuts the stored panel down to the names worth spending an
evidence run on, joins whatever the evidence plane has already answered about them, and
runs the shortlist gate over the result. It is the HTTP twin of `openalpha shortlist run`
and of `OpenAlphaSDK.run_shortlist`; all three resolve through
`shortlist_view.shortlist_request` and run through `shortlist_view.run_shortlist`.

**What the panel has to hold before this route can answer (`V2-P4-078`).** Six datasets,
written by five `openalpha panel build` targets, and a panel short of any one of them is a
`409 panel_unreadable` rather than a thinner list:

| dataset | `panel build --dataset` | what this route reads it for |
|---|---|---|
| `trade_cal` | `trade_cal` | which day the cross section's session is |
| `stock_basic` | `stock_basic` | who was listed on it |
| `daily` | `price` | the bars stage two prices against |
| `stk_limit` | `stk_limit` | the exchange's own published bands |
| `suspend_d` | `price` | whether a name was halted at the close |
| `namechange` | `namechange` | `is_st`, off the name in effect on that session |

`namechange` is the one that catches people: `openalpha factor build --tier raw` — the command
that writes the partition this route reads, and which has no HTTP twin — neither needs nor
fetches it, so a panel without it serves a green factor build and a red shortlist. The `409` body now names the command — `panel build --dataset namechange --year
<year>` — on `message`, which is the disclosable string and carries no filesystem path.
`adj_factor` is deliberately absent from the table: the factor build may want it, this route
never opens it.

**Which session a cross section is priced on is decided by the `as_of` it was *built* at, and
that is not the day that instant falls on (`V2-P4-077`).** A session's bars publish at 16:30
Asia/Shanghai, so a cross section stamped anywhere between that day's midnight and 16:30 is
priced against the **previous** session — the newest one that had published when its own factor
values were computed. `cross_section.pricing_session` on every answer says which one was used.
Before this the day itself was used, so such a cross section asked for a session that had not
published, was refused for it, and — because the instant is stored on the cross section — was
refused at every later `as_of` too. The look-ahead guard did not move: `panel doctor --session`
still refuses an unpublished session by name, and this route simply no longer asks for one.

`components` is `[{"factor": "<qualified key or fct_ address>", "weight": <number>}]`, and
`tier` is `raw`, `processed` or `neutralized`. A **raw**-tier screen takes no `transform`
and may declare exactly one component — raw values carry each factor's own units, so
summing two of them adds quantities that share no scale. A **processed** screen requires a
`transform`, because that partition holds every transform of the factor and is narrowed by
the one you name. A **neutralized** screen is refused by this route: the ranking it would
produce needs the industry-and-size cross section its scores were neutralised against, and
this face does not load one.

`evidence` maps each researched subject to `{"signal": <SignalFrame>, "run_manifest_id":
"run_…"}` and is **empty by default**, which is the ordinary first answer: the shortlist
says which names are worth an evidence run, and nothing has been researched yet. A signal
may carry its own `signal_id` — the field this service puts on every frame it hands out —
and it is stripped before validation and then verified against the frame's content.
Answers about names the cut did not reach are not an error; they come back under
`evidence_not_shortlisted`.

**The `run_manifest_id` is resolved against the runs this deployment holds (`V2-P4-049`).**
It used to be format-checked and nothing else, and a `SignalFrame` only had to hash to its
own address — so an invented conclusion beside the literal
`run_000000000000000000000000` cleared a `minimum_researched_ratio` of `1.0` and was
published with `researched_ratio: 1.0` and a provenance pointer that resolved to nothing.
An entry whose `run_manifest_id` names no stored run is now **dropped before the ranking is
built**: its subject is `unresearched`, it counts against `researched_ratio` exactly as a
name with no evidence does, and it is named on the answer under
`evidence_without_a_stored_run`. Dropped rather than refused, so a caller looping over a
year of `as_of`s can keep going past it.

**A run that did not finish does not resolve one either (`V2-P4-075`).** A
`RunManifest` stored with `status` `failed` or `interrupted` — or `pending` or
`running` — *is* held by the deployment, and filing evidence against its address used to
clear a `minimum_researched_ratio` of `1.0` while the refusal that floor raises described
the ratio as "a fact about which runs finished". Only a `succeeded` run resolves now. The
two are reported apart, on `evidence_without_a_stored_run` and
`evidence_from_an_unfinished_run`, because the remedies differ: run the research, or go and
find out why the run broke.

What that proves and what it does not: the run is resolved, the **signal** is not. This
repository stores no `SignalFrame`, so there is nothing to resolve one against, and a
caller holding a real `run_manifest_id` can still file an invented conclusion under it. The
property delivered is that a published `run_manifest_id` resolves to a run this deployment
holds — not that the conclusion beside it came out of that run.

`neutralization` addresses the neutralized partition and **is refused on any other tier**,
exactly as `transform` is refused on `raw`: a flag that would move no security and no value
in the answer is a question this route cannot honour, and accepting it silently returned a
raw screen to a caller who asked for a neutralised one.

`position_capital` is a notional **per name** and not a fund size — nothing here allocates.
It must be below `10**26`, which is not a policy limit but the first budget whose own fill
this build cannot price: stage two quantizes a notional to cents, and a larger one needs
more significant digits than `decimal`'s context carries.

### What the answer records about itself

`declaration` carries the whole resolved question — `tier`, `transform`, `neutralization`,
`exchange`, `years` and each component's `factor_id`, qualified key and weight. Without it a
published answer said `tier: "processed"` and never which transform chose the numbers, so
two runs of one factor under two transforms were indistinguishable after the fact.

`cross_section.components[]` reports `row_count` and, beside it, `admitted_count` — how many
of those rows carried a value this tier admits — and `stored_coverage`, the panel's own
coverage codes with their counts. `funnel.excluded_by_coverage` is the other half: how many
securities stage one dropped, keyed by `incomplete_components`, `not_admissible` and
`not_valued`. Together they separate "the rows carried no value" from "the components did
not overlap", which a `row_count` beside a `scored_count` of zero could not.

`funnel.excluded_by_coverage` explains stage one only, and stage two used to have nothing
(`V2-P4-066`). `scored -> tradeable` was a subtraction with no explanation beside it, on the
very ratio `minimum_tradable_ratio` gates — a whole-market acceptance read `5542 scored ->
5533 tradeable` and could not learn which nine names went or under which rule. Four keys
now say so:

- `funnel.refused_by_verdict` — how many scored securities stage two refused, keyed by
  `unbarred` (no bar for the pricing session), `unbanded` (a bar with no published band),
  `below_board_minimum` (`position_capital` does not buy one board lot) and `rejected` (the
  execution policy refused the buy). **All four cells always**, occurred or not, so "nobody
  was `below_board_minimum`" and "nothing looked" stay different claims.
- `funnel.rejection_reasons` — the execution policy's own sentences with their counts, for
  the `rejected` cell only. `{}` when it refused nobody.
- `funnel.untradeable` — every refused security **by name**, as
  `{"subject", "verdict", "reason"}`, grouped in the verdict order above and then by
  subject. `reason` is the policy's sentence for exactly `rejected` and `null` for the other
  three, which are decided before the policy is called.
- `funnel.untradeable_not_named` — how many refused securities the list above left out. The
  named list is capped so a body cannot scale with the market; the two count objects above
  are exact at any size.

The `tradable_ratio_below_floor` refusal names the rules and the first few securities under
each, and points at `funnel.untradeable` for the rest.

The factor tier is read at your `as_of`. **Everything the screen prices with — the
calendar, the registry, the bars, the published bands, the halts and the name histories —
is read at the resolved cross section's own instant**, which is at or before it. So a
fortnight-old cross section is offered to the market of *its* session and never to a later
one its factor values never saw. `cross_section.as_of` and `cross_section.pricing_session`
are on every answer, because the cross section may legitimately be older than the `as_of`
you asked about.

**That sentence did not hold until `V2-P4-076`, and it is worth knowing why if you are
reading answers this service gave before it.** Six datasets are read at the cross section's
own instant, and until `V2-P4-061` five of them went through a whole-partition gate that
decides "not yet knowable" from the newest row *anywhere in the calendar year*. So a panel
that had advanced by one session refused every **earlier** cross section in that year —
`daily cannot be read at …: ['not_yet_knowable']` — and only the newest one could be
screened at all. Two days' shortlists could not be compared, yesterday's could not be
re-run, and a published list could not be audited after the fact.

`V2-P4-061` moved the bars and the published bands onto the same as-of-sensitive session
read the valuation panel already used. **On a real panel all three of those sentences were
still true afterwards**, because the registry, the halts and the name histories had not
moved and each of them walls off a cross section on its own: measured 2026-08-19, the
registry became knowable at that day's midnight and the halt corpus at its 16:30, so a
cross section about the previous session was refused before a single bar was read. Those
three now go through a per-event-date read that reconciles what it can see against the
partition's own row census, date by date. The exchange calendar is the one read left on the
whole-partition door, and that is measured rather than an omission: every row of a calendar
year is dated knowable at 1 January of that year, so the gate has no way to fire inside it.

Nothing about the look-ahead guard moved with them, and each refusal still has its own
name. A session whose own 16:30 Asia/Shanghai has not arrived at the read's instant is
refused before the partition is touched, because answering it with an empty cross section
would be a look-ahead dressed as thin data. A session the store holds and the instant
cannot see is refused rather than answered empty. A session the panel never stored is
refused as a `date_gap` against the exchange calendar's own census. And a session whose
stored rows do not all share one availability instant — the property that makes a session
read sound at all — is refused outright rather than returned short.

The three whole-year reads keep their own two refusals, for the same reason. A partition
that holds fewer rows on an event date than its own census counts there is refused by name,
because a corpus short by a withheld row is indistinguishable from one where the row does
not exist — and on the halt corpus those two are the same answer, "not halted". A partition
that answers with a row visible *before* its own event is refused separately and first,
because that is a look-ahead rather than a shortfall, and one message about two totals is
what let a compensating pair through once already.

### Keeping an answer, and fetching it back

Every answer carries **`shortlist_id`**, and that is the address the run is stored under.
`GET /api/v1/shortlists/{shortlist_id}` returns it; `GET /api/v1/shortlists` lists every
address this runtime directory holds. `openalpha shortlist get`/`list` and
`OpenAlphaSDK.held_shortlist`/`list_shortlists` are the other two faces, and all three
serve one document.

Before `V2-P4-062` the answer carried three content addresses — `gate_manifest_id`,
`ranking_manifest_id`, `ranking_content_digest` — and nothing held anything under any of
them: `runtime/` had no shortlist artifact and this API had no `GET`. Two runs of one
command produced byte-identical addresses, so the identities were sound; they simply had
nothing behind them, and a caller who wanted to compare today's list against yesterday's
had to have saved `--json` themselves.

None of the three could be the key, and each fails a case this repository's own fixtures
reach. `ranking_manifest_id` addresses the **question** — as-of, horizon, universe, scoring
policy, code commit, config digest — so two runs under two different gate bars share it.
`gate_manifest_id` addresses the question *and* the bars, and the **evidence** is in
neither, so the same run with and without a supplied signal shares it and produces two
different `admitted` lists. `ranking_content_digest` addresses `(subject, rank, score,
signal_id, run_manifest_id)` per **candidate**, so a first run with no evidence has zero
candidates and two entirely different shortlists share one digest.

`shortlist_id` is the digest of the whole rendered answer, less
`measurement.ranking_age_days` — which is `built_at - as_of` and therefore a wall clock, so
addressing it would mint a new document every day the same shortlist was re-run. So the
store is purely content-addressed: two runs that produce one answer produce one document
and the second write is a no-op, and two answers that differ have two addresses. What it
therefore cannot tell you is **how many times** or **when** an answer was reached; that is
the `RunManifest` plane's question, not this one's.

Retrieval has two refusals and they are deliberately different. A token that is not an
address (`sla_` and 24 lowercase hex characters) is **`422`** with
`{"detail": {"reason": "bad_request", …}}` — checked before the store is asked. A
well-formed address this runtime directory holds nothing under, or holds a document whose
answer no longer hashes to it, is **`404`** with `reason: "not_held"`. One code covering
both would tell a caller who mistyped an address that their answer had been lost.

### A refused list and an empty one are two different answers

This is the property the route exists for.

| Situation | Code | `is_blocked` | `admitted` |
|---|---|---|---|
| the gate ran and admitted the list | `200` | `false` | an array, possibly `[]` |
| the gate ran and **refused** it | `409` | `true` | `null` |
| no component has a stored cross section at or before the `as_of`, or the declared components disagree about which instant they share | `409` | — | — |
| a declared component's stored cross section admits no value at all — for instance a processed tier whose rows all read `insufficient_cross_section` | `409` | — | — |
| a partition this screen needs is missing, damaged, stale, or holds rows that were not knowable at `as_of`, or the stored registry's own shape refuses the read (a delisting row whose listing year was never written, a duplicated code, a skipped lifecycle year) | `409` | — | — |
| the request cannot be put at all (unknown factor, non-positive weight, processed tier with no `transform`, a `neutralization` on a tier that has none, a `position_capital` at or above `10**26`, naive `as_of`, or a retrieval address that is not one) | `422` | — | — |
| a retrieval address nothing is held under, or a held document that no longer hashes to it | `404` | — | — |
| the endpoint itself broke; nothing was judged | `500` | — | — |

`admitted: []` means the gate cleared a list every name of which came back unresearched,
under a `minimum_researched_ratio` the caller declared they could live with. `admitted:
null` with `409` means the gate refused, and `blocks[]` carries each bar with its `code`,
its `detail`, the value `measured` and the value `required`. `measurement` — the universe,
scored, tradeable, shortlist and candidate counts, both ratios and the ranking's age — is
on **both** verdicts, so a list that scraped over a bar is distinguishable from one that
sailed over it.

The five refusal rows carry `{"detail": {"reason", "message"}}` and no `is_blocked`; the
two verdict rows carry `is_blocked` and no `detail`. The `500` row is neither — see below.
`api/app.py#SHORTLIST_HTTP_STATUS` is the table.

#### Telling the body shapes apart

**Branch on the *shape* of `detail`, not on whether the key is present.** `422` carries two
different bodies and both have a `detail` key, so `"detail" in body` selects the wrong one
half the time — which is what this reference used to say, and a client that followed it
raised `TypeError: list indices must be integers` on an unparseable `as_of`, a misspelled
field, a non-numeric `position_capital`, a wrong `Content-Type` and malformed JSON.

| `body["detail"]` | who wrote it | what it holds |
|---|---|---|
| absent | this route | a verdict — read `is_blocked` and `admitted` |
| **an object** | this route | `{"reason", "message"}` — `reason` is the row of `SHORTLIST_HTTP_STATUS` |
| **a list** | FastAPI's request validation | one entry per offending field, each with `loc`, `msg` and `type` |

```python
detail = body.get("detail")
if detail is None:
    ...  # a verdict: body["is_blocked"] says which
elif isinstance(detail, dict):
    ...  # this service refused: detail["reason"], detail["message"]
else:
    ...  # the request never parsed: detail is a list of field errors
```

The two `422`s are genuinely different findings and are deliberately not merged. The object
is *this module's* refusal — the request parsed, and `shortlist_view.shortlist_request`
judged it unanswerable. The list is FastAPI's own report that the body never became a
request at all, and it names the offending field, which a flattened message would lose.

The `500` row has **no `detail` of either shape**: an exception no branch anticipated is
handled by Starlette rather than by this route, so it arrives as `text/plain` `Internal
Server Error`. Nothing a caller can put in the body should produce one — a caller-supplied
`position_capital` used to, and now does not — so a `500` here is a defect to report, not a
request to change.

**Nor should anything in the store produce one — and four times something did.** This
sentence first read "and until `V2-P4-070` something did", which asserted the list was
closed; it was written in `V2-P4-070`'s own commit and measured false eleven commits later,
and false again two commits after it became a list. It is a list, the list is not closed,
this is its fourth entry, and every entry so far has been the same shape: a domain refusal
that is a verdict about stored data, raised somewhere no `except` on the route's path
anticipated.

The first was a registry whose lifecycle backfill was interrupted — a security's delisting
row stored and its listing row in a year partition that was never written — which made this
route answer `500` `text/plain` while `openalpha factor build` on the same store answered
`1` and named the security (`V2-P4-070`).

The second was an ordinary rename (`V2-P4-080`). `MarketBar.is_st` is read off the stored
`namechange` corpus, `load_name_histories` is scoped to the announcement years the request
asks for, and `NameHistory.record_on` refuses a session before a security's earliest record
in them rather than answering the earliest name on file. A security whose only rename in the
requested year was announced before the priced session and takes effect after it therefore
has no name on that session — the two-clock rename this repository models on purpose, met one
year at a time — and the refusal escaped both faces: `500` `text/plain` here, `exit 5` from
`shortlist run` and `exit 5` from `factor run`, with the sentence naming the security withheld
each time. It needs no damaged partition: the corpus is well formed and simply does not reach
back past the session.

The third was three refusals at one seam (`V2-P4-084`), and it is `POST /api/v1/factors/run`
rather than the shortlist route. `factor_view._PanelInputs.label` wrapped `label_outcome` in
`except LabelError` alone, and `label_outcome` asks three other modules questions that answer
in their own vocabulary — all four are independent `ValueError` subclasses, so three of them
went straight past:

| what the run met | what was raised | what the caller got |
|---|---|---|
| a priced security the registry has no row for | `StockUniverseError` | `500` `text/plain` |
| a factor series ending before the label window | `AdjustmentHorizonError` | `500` `text/plain` |
| `daily` and `adj_factor` disagreeing about a session | `PriceDataError` | `500` `text/plain` |

None needs a damaged partition either. The first is measured on the live feed — 300114.SZ has
a real bar on 2024-06-28 and no `stock_basic` row under any `list_status` — and the systematic
version is a fetch parameter: `list_status='L'` serves 5,539 rows where `'L,D'` serves 5,878.

All three are `409` `panel_unreadable` now, with the refusal's own sentence in
`detail.message` naming the security, the window, the partition it is about and the repair —
which for the third is re-fetching the session rather than rebuilding either dataset, because
which of the two is wrong is exactly what the disagreement does not say.
`tests/integration/test_partial_registry_faces.py`,
`tests/integration/test_unnamed_session_faces.py` and
`tests/integration/test_unlabelled_corpus_faces.py` drive stores at this route, at the
`/shortlists/run` route, at `shortlist run` and at `factor run`.

The fourth is on a different route — `POST /api/v1/models/daily-run` — and it is the first
that turns on the *date asked about* rather than on anything in the corpus (`V2-P4-088`). The
second and third need no damaged partition either, but each still needs a particular thing to
be true of the stored rows; this one needs a well-formed calendar and a prediction day near
the end of it. A prediction is sealed against the instant
its outcome becomes knowable, which the store derives from the calendar; a prediction day in
the last `horizon.sessions + 1` sessions of a year-keyed calendar has no such instant, because
the exchange has not published that far. `model_view.run_daily` handed the batch to the store
**after** its only `try` block had closed, so `CalendarHorizonError` — whose own docstring calls
it "the one failure that is *not* a caller mistake" — arrived here as `500` `text/plain` and at
`OpenAlphaSDK.run_daily_model` as a bare `ValueError` subclass. `daily_request` requires
`predict_at`'s date to be strictly after `end`, so the prediction day is always later than every
training day and the guarded path could never fire for it: a routine late-December daily run met
this every time. It is a `409` `blocked` on all three faces now, naming the session it could not
place and the repair — `openalpha panel build --dataset trade_cal --year <next>`, declared with
`--year`. `tests/integration/test_year_end_daily_run.py` drives a whole year of 2026 at it.

None was a defect in this service, and none is a reason to widen this row: what makes a `500`
here still mean "report a bug" is that each such refusal is anticipated where it is raised —
at the read for the first, at the question asked of what a read returned for the next two, and
at the hand-off to the store for the fourth.

`openalpha shortlist run` maps the same names onto exit codes
(`cli.py#SHORTLIST_EXIT`) and reuses `PanelExit`: `0` admitted, `1` for every `409` row —
**including a refused list** — and for the `404` one, `3` for `422`, `5` for an unhandled
defect, and `2` for Click's own usage errors (a missing or misspelled flag), which this
table does not own. A scheduled job that cut a shortlist, had it refused and exited `0`
would be no gate at all. `openalpha shortlist get` uses the same two: `1` when nothing is
held under the address, `3` when the token is not an address.

## Models

`POST /api/v1/models/evaluate` fits one model declaration once per walk-forward fold and
reports what it ordered; `POST /api/v1/models/daily-run` fits on the outcomes that have
already closed, scores one stored cross section, and **registers the prediction before its
outcome is known**. They are the HTTP twins of `openalpha model evaluate` and `openalpha
model daily-run` and of `OpenAlphaSDK.evaluate_model` / `.run_daily_model`; all three faces
resolve through `model_view.model_evaluation_request` / `.daily_request` and run through
`model_view.evaluate_model` / `.run_daily`, so they cannot come to fit three models from one
declaration (`V2-P4-021`).

**What the panel has to hold before either route can answer.** Six datasets, written by five
`openalpha panel build` targets. They are **not** the shortlist's six:

| dataset | `panel build --dataset` | what these routes read it for |
|---|---|---|
| `trade_cal` | `trade_cal` | the label windows and the embargo's sessions |
| `stock_basic` | `stock_basic` | who was listed on each prediction day |
| `daily` | `price` | the closes every outcome is measured between |
| `suspend_d` | `price` | the halts that refuse a window |
| `stk_limit` | `stk_limit` | the bands that refuse a window locked at a limit |
| `adj_factor` | `adj_factor` | the corporate actions the return is adjusted for |

`adj_factor` is the one `shortlist run` does not need and these do: a label is a *return
between two sessions*, so `label_outcome` requires an adjustment series and `window_return`
refuses one that does not reach the window. `namechange` is the reverse — the shortlist needs
it for every `MarketBar`'s `is_st`, and nothing on these routes builds a `MarketBar`. A panel
built for one of the two faces is short for the other, in **both** directions, and the `409`
body names the command that repairs it.

### The two clocks, and which one a request supplies

`as_of` is when the **labels** are read, and it must be at or after `end`. Every panel read
in a run is made at it. That is not a weakening of the point-in-time guarantee: each cross
section is still the stored build visible at *its own* prediction instant, filtered by
`read_visible_at` one layer down. What is read at the single `as_of` is the corpus's
**shape** — which securities the registry lists, which sessions the calendar holds, which
factors the adjustment series carries. An outcome is by definition not knowable at the
instant it is predicted about, so a run that read the labels at each prediction instant would
find no closed window at all. `model_view`'s
`the_evaluation_reads_its_labels_at_one_as_of_and_that_is_not_a_point_in_time_fit` states the
residual.

`predict_at` (daily runs only) is the instant the prediction is **about** — the stored cross
section it scores — and must be strictly after `end`. It is *not* the instant the batch was
produced at: that is the service's own clock, and no request field carries it, because the
store's reading of that same clock is the entire mechanism behind `standing` below.

### Which prediction days a run is about

`start` and `end` name the first and last prediction day in `Asia/Shanghai`, and the instants
come from the builds every declared column actually shares, visible at `as_of`. One
prediction day keeps its **newest** build, because two builds on one day are two answers to
one question. A day is `instant.astimezone(Asia/Shanghai).date()` — `build_label_window`'s
own first step — and deliberately not the *pricing session* the values were computed from;
the two come apart for a build stamped between midnight and the 16:30 close, and two
prediction days whose builds price one session are a `409 blocked` in `build_feature_matrix`'s
own words.

### `feature_version`, and what omitting it costs

Omitted, it is resolved from the columns the request declares — `code_commit`'s arrangement,
because nobody can type a `feat_` digest by hand. Supplied, it is checked by
`feature_matrix.require_declared_features` and a mismatch is a `422`. The answer records
which of the two happened, under `declaration.feature_version_source` (`resolved` or
`declared`), because a resolved recipe proves only that the artifact records what it was
fitted on — a mistyped `features` entry yields a different, self-consistent digest rather than
a refusal.

### Refused is not empty

`minimum_scored_ratio` has no default on any face. It is the floor under `scored / offered`,
and it exists because `FoldEvaluation.scored_ratio` does: abstaining on the hard names is
otherwise a free way to win, so a headline statistic is only comparable beside the fraction of
the market it was taken over.

- Above the floor: **`200`**, `"is_blocked": false`, and `admitted` carries the artifact
  addresses (evaluate) or the scored securities (daily run).
- Below it: **`409`**, `"is_blocked": true`, `"admitted": null`, and every bar missed under
  `blocks` with `measured`, `required` and a `detail` naming both counts.

`null` and a list are two different answers on these routes, and the `measurement` object is
byte-identical across the pair — which is what makes a client able to see that only the
declared bar moved. It is a **coverage** verdict and never a quality one.

**A refused daily run still registered its prediction**, and its `record_id` is on the `409`
body. Story S32 is about a prediction being persisted before its outcome is known, which is
unconditional; the floor is about whether the answer may be acted on, which is not.

### `shelf_life_days`, and what a stale model answers

Optional, and its absence is recorded rather than resolved. Supplied, it is how many days past
its training cutoff a fit may still be asked about a cross section; beyond that every security
in the batch abstains with a stated reason instead of being scored, which is Story S35's
`stale 模型显式弃权`. The answer renders it under `declaration.shelf_life_days`, `null` when the
request declared none — `feature_version_source`'s arrangement, because a reader has to be able
to see on the answer which of the two happened.

It is **wall time and not sessions**. A horizon in this repository counts open sessions, and
`domain/horizon.py` refuses to convert a session count into a calendar span, so `5` here is five
calendar days and a caller who means five sessions widens it for weekends.

An expired run is not refused by this field. It drives `scored_ratio` to `0.0`, and it is
`minimum_scored_ratio` above that turns that into the `409`, so a request declaring a floor of
`0.0` reads an all-abstaining model as a `200`. The two are one mechanism and
`an_expired_run_is_refused_only_by_the_coverage_floor_the_caller_declared` is the entry in
`limitations` that says so.

### `standing`, and exactly what it proves

Every rendered prediction carries `standing` plus `standing_proves` and
`standing_does_not_prove`, and the second is not decoration. `V2-P4-017`'s three standings
are three different facts:

- **`forward`** — the batch says it was produced before the outcome became knowable **and**
  this store held the bytes before then. It does **not** prove the batch was produced when it
  says it was: `predicted_at` is whatever the caller passed to `predict` and nothing in this
  repository can check it, and nothing here defends against whoever owns the disk. A claim a
  third party could check would need a timestamp somebody else controls, and this repository
  has none.
- **`unwitnessed`** — stamped in time, received late. Which may be a slow disk and may be a
  backdated `predicted_at`, and the record cannot tell you which.
- **`backfill`** — produced at or after the deadline, stated as a recomputation. A backfill
  may not replace an original.

`GET /api/v1/predictions` lists the register **in the order this store took custody**, oldest
first: `record_ids` keeps its name and its place and now carries that order, and `predictions`
is the same list with a row each — the cross section it is about, its standing with both of the
sentences above, the horizon, the model name and how much of the market it scored. The order was
a sort over content digests until `V2-P4-098`, which is uncorrelated with time and so answered
the one question a register is read for with a shuffle; it is `recorded_at` and not
`predicted_at`, because the custody stamp is the one instant a caller does not set.

`GET /api/v1/predictions/{record_id}` returns one. The body is **what was registered**, not a
re-run: the store re-derives the address from the content before handing it over, so a document
edited on disk is a `404` rather than scores somebody trades on. A malformed address is a `422`
and never a `404`, so "that is not an address" and "nothing is filed under that address" stay
two answers. It carries `model` — the whole fitted artifact the record holds by value, so a
prediction read a year later resolves to its declaration with no lookup — and `limitations`,
because this route and `openalpha model prediction` hand out a record with no run's answer
beside it. What no stored record carries is the training range and the instant the fit read the
panel at: a `forward` standing does not bound the latter, and
`a_forward_standing_does_not_bound_the_instant_the_fit_read_the_panel` is the entry that says
so and says why the instant is not added to the document.

### The manifest slot these routes fill

`POST /api/v1/models/daily-run` files a `RunManifest` with `mode: "daily"` and
`alpha_model_versions` naming the one artifact it consumed — the slot `V2-P4-010` declared,
`V2-P4-016` measured it could not fill (`run_cycle` has no `AlphaModel` on its path) and
`V2-P4-017` left open. `run_id` is derived from the prediction's own content address, so a re-run
that reproduces the prediction reports `unchanged` on both stores instead of a duplicate on one
— and **this route cannot reproduce one**: `predicted_at` is the service's own clock reading and
it reaches that address, so a client retrying after a transient failure files a second record for
one prediction day. `a_re_run_of_one_day_files_a_second_record_because_predicted_at_reaches_the_
address` carries the argument against each of the two repairs that look obvious.
`POST /api/v1/models/evaluate` writes **no** manifest and **no** prediction: it fits one
artifact per fold and acts on none of them, and every record an evaluation could register
would stand `unwitnessed`, because a simulated prediction is dated at the instant it
simulates.

### Status codes

`model_view`'s four faults map through `api/app.py#MODEL_HTTP_STATUS`: `bad_request` → `422`,
`blocked` and `panel_unreadable` → `409`, `not_held` → `404`, plus `answered` → `200` and
`refused` → `409` for the two verdicts, which are not faults. `409` therefore carries two body
schemas and `detail` is the discriminator — a verdict body has `is_blocked` and no `detail`
key. `422` carries two as well: this module's refusal is a `{"reason", "message"}` **object**
while a body FastAPI itself rejected is a **list** of field errors, so a client branches on
`isinstance(detail, dict)`.

`openalpha model evaluate` / `daily-run` map the same names onto exit codes
(`cli.py#MODEL_EXIT`) and reuse `PanelExit`: `0` admitted, `1` for every `409` row —
**including a refused run** — and for the `404` one, `3` for `422`, `5` for an unhandled
defect, and `2` for Click's own usage errors. `openalpha model prediction` uses the same two:
`1` when nothing is held under the address, `3` when the token is not an address.

The portfolio endpoint is intentionally stateless: callers submit the immutable
`PortfolioState`, `PortfolioOrder`, `MarketBar`, and optional `PortfolioLimits`,
then persist the returned `PortfolioTransition` in their own workflow. It is a
research/backtest accounting surface, not a live-broker order endpoint.

The declared request limit, its default, and the two gates that enforce it are stated once
under [Request size](#request-size-openalpha_max_request_bytes-v2-p4-043-v2-p5-012); this
paragraph deliberately no longer restates the number, because it restated a stale one for
as long as `V2-P4-043` has been merged. Configure it with
`OPENALPHA_MAX_REQUEST_BYTES`. The service is local-first and has no public
multi-tenant authentication; use a TLS/authentication gateway before any
network exposure.
