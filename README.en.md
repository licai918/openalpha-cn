# OpenAlpha CN

Evidence-traceable, point-in-time, multi-agent research for China A shares.

[中文](README.md) · [Deployment](docs/deployment/production.zh-CN.md) · [Data API](docs/api/data-interface.zh-CN.md) · [Three-upstream source audit](docs/audits/three-upstream-source-audit-20260724.md) · [Feature ledger](docs/release/openalpha-v1-feature-ledger.md)

## Self-host

The recommended path is Docker Compose:

```bash
git clone https://github.com/ss8875/openalpha-cn.git
cd openalpha-cn
docker compose -f deploy/compose.yml up -d --build
```

Open `http://127.0.0.1:8000`. Runtime evidence and ledgers live in a dedicated persistent volume.

OpenAlpha CN provides four-clock point-in-time evidence, A-share event semantics,
deterministic baseline agents, a secure OpenAI-compatible BYOK model boundary,
typed signals and decisions, durable per-agent resume and research memory,
A-share execution and portfolio constraints, same-path replay, reconciled
attribution, durable batch research, a ChainLin contract Provider, screening,
watchlists, reports, REST, Python SDK, CLI, and a responsive research workbench.

## Prefer a ready-to-use Windows application?

[Download ChainLin Limit-Up Review 1.0.9 for Windows x64](https://github.com/ss8875/openalpha-cn/releases/download/chainlin-desktop-v1.0.9/Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe)

- Size: `144,902,921 bytes` (`138.19 MiB`)
- SHA-256: `0DDD3AF69C671C3AF0F7AEC90D57B77363705E38E871B49D640C7A2D0D05838B`
- The installer is currently unsigned and Windows may show a SmartScreen warning.
- The separately distributed desktop product is not automatically covered by this repository's MIT license.

## Why it is different

OpenAlpha CN competes on verifiability rather than the number of agent personas:

- A-share-native limit-up, broken-board, consecutive-board, theme, catalyst, disclosure, and capital evidence;
- separate event, availability, ingestion, and revision clocks;
- content-addressed evidence and strict anti-look-ahead rules;
- deterministic operation without an LLM, plus schema validation and bounded retries when a model is used;
- durable node checkpoints that reject changed requests or graph signatures;
- bounded concurrent batches with progress, cancellation, retry, and restart recovery;
- classified model retry plus persistent token and configured-cost accounting;
- one research core shared by the live, replay, backtest, paper and daily modes;
- A-share T+1, board lot, suspension, limit-lock, and transaction-cost constraints;
- immutable cash/lot/mark/fee/PnL portfolio transitions with exposure clamps;
- multi-day portfolio reports and event-study significance inference;
- ablatable bull/bear and three-perspective risk committee;
- evidence-linked decisions and reconciled rule/factor/agent/model attribution with an
  explicit unexplained residual.

The project accepts user-owned CSV, JSON, JSONL, and Parquet data, BYOT Tushare, and an optional constrained AKShare adapter. It does not redistribute commercial raw datasets or expose a hosted data resale proxy.

## The factor plane

Above the point-in-time panel sits a factor library — 19 declared factors (5 momentum/reversal, 4 volatility/liquidity, 3 value, 4 quality, 3 growth), one cross-sectional transform, one industry-and-size neutralisation — and a three-tier experiment that scores all three tiers and seals the result into an immutable, content-addressed record. Four commands, in the order you meet them:

```bash
uv run openalpha factor list                              # what this build declares
uv run openalpha factor describe --factor return_vol_60/v1  # one declaration, whole, with its note
uv run openalpha factor build --factor reversal_1d/v1 --tier processed \
  --transform cross_section_standard/v1 \
  --as-of 2026-01-08T09:00:00+00:00 --as-of 2026-01-09T09:00:00+00:00 \
  --year 2026 --max-staleness-days 30                     # compute and store the tiers
uv run openalpha factor run --factor reversal_1d/v1 --start 2026-01-08 --end 2026-01-09 ...
```

Three faces answer the same questions: `openalpha factor *`, `GET /api/v1/factors` plus
`POST /api/v1/factors/run`, and `OpenAlphaSDK.factor_catalog()` /
`.run_factor_experiment()`. `factor build` is on the command line and in the SDK only,
matching `panel build`: it writes panel partitions and the service ships with no
authentication of its own.

`factor run` prints three tier rows and a six-cell attribution grid. **The six cells are
not equals**: `processed->neutralized` is the step the acceptance criterion is read off —
a statistic that vanishes there was the industry and size exposure. The six verdicts are
`survives`, `removed`, `reversed`, `amplified`, `no_baseline` and `not_measured`;
`openalpha factor list` prints what each one means.

**Exit `0` covers a grid that is `removed` everywhere *and* one that is `not_measured`
everywhere, and the second is the dangerous one** — it is no finding at all, because one
tier in every cell computed nothing, yet it looks like a clean pass to anyone grepping for
`removed`. `factor run` prints a named warning on stderr when that happens, and
`document.artifact.tiers[].ic.coverage` is the per-tier truth. See
[the HTTP contract](docs/api/http.md) for the full argument and for the named boundaries —
including `V2-P4-026`, which is why the neutralised tier cannot be built at a mid-year
prediction instant.

## The model plane

Above the factor tiers sits the model chain: a versioned feature matrix, a walk-forward split
with purge and embargo, two stdlib baselines (a cross-sectional rank model and gradient-boosted
rank trees, no numerical dependency), a content-addressed artifact, and a store that holds a
prediction **before its outcome is known**. Two commands:

```bash
uv run openalpha model evaluate --feature reversal_1d/v1@raw \
  --name reversal-rank --family cross_sectional_rank --horizon 5d --seed 7 \
  --start 2026-01-06 --end 2026-01-14 --year 2026 \
  --folds 2 --test-days-per-fold 2 --embargo-sessions 0 \
  --min-scored-ratio 0.5 --as-of 2026-01-20T04:00:00+00:00

uv run openalpha model daily-run --feature reversal_1d/v1@raw \
  --name reversal-rank --family cross_sectional_rank --horizon 5d --seed 7 \
  --start 2026-01-06 --end 2026-01-14 --year 2026 \
  --predict-at 2026-01-16T09:00:00+00:00 --min-scored-ratio 0.5

uv run openalpha model predictions          # every registered address
uv run openalpha model prediction prd_…     # one of them, as it was registered
```

Three faces again: `openalpha model *`, `POST /api/v1/models/{evaluate,daily-run}` plus
`GET /api/v1/predictions[/{record_id}]`, and `OpenAlphaSDK.evaluate_model()` /
`.run_daily_model()`. All three resolve and run through `model_view`, so they cannot fit three
models from one declaration.

**These commands need `adj_factor` and the shortlist does not.** A label is a return *between two
sessions*, so the labeller requires an adjustment series; conversely the shortlist needs
`namechange` for every bar's risk-warning flag and these commands never build a bar. A panel built
for one face is short for the other, in both directions, and each refusal names the `panel build`
line that repairs it.

**`--min-scored-ratio` has no default, and refused is not empty.** It is the floor under
`scored / offered`, and it exists because abstaining on the hard names is otherwise a free way to
win. Above it: exit `0` / `200` with `admitted` carrying what the run stands behind. Below it:
exit `1` / `409` with `"admitted": null` and both sides of the bar under `blocks` — while the
`measurement` object is byte-identical across the pair. It is a coverage verdict and never a
quality one.

**A refused `daily-run` still registered its prediction.** Story S32 is about a prediction being
persisted before its outcome is known, which is unconditional; the floor is about whether the
answer may be acted on, which is not.

**`--shelf-life-days` makes a stale model abstain out loud.** How many days past its training
cutoff a fit may still be asked; beyond it every security abstains with a stated reason instead of
being scored, and the answer says which span was declared (`declaration.shelf_life_days`, `null`
when none was). It is wall time, not sessions — a horizon counts open sessions and this
repository refuses to convert the one into the other. And it refuses nothing on its own: an
expired run reads `scored_ratio: 0.0`, which is `--min-scored-ratio`'s to reject, so the two flags
are one mechanism.

**What a `forward` standing proves, and what it does not.** It means this store held the bytes
before the instant the outcome became knowable. It does **not** mean the batch was produced when
it says it was: `predicted_at` is whatever the caller passed to `predict`, nothing here can check
it, and nothing here defends against whoever owns the disk. Every rendered prediction carries both
sentences in the body, because a one-word badge reads as an attestation this repository cannot
make. See [the HTTP contract](docs/api/http.md) for the three standings and the nine named
boundaries.

## Development gates

```bash
uv sync --locked --all-extras --dev
uv run pytest --cov=openalpha_cn --cov-fail-under=80
uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
uv build
uv run python scripts/build_feature_coverage.py --check
uv run python scripts/verify_publication.py
```

The web workspace uses locked pnpm dependencies, Vitest, and Playwright. See the [production deployment guide](docs/deployment/production.zh-CN.md), [API contracts](docs/api/contracts.md), and [v1 feature ledger](docs/release/openalpha-v1-feature-ledger.md).

## License

OpenAlpha CN source is released under the [MIT License](LICENSE). Third-party data, models, services, brand assets, and the ChainLin installer retain their own licensing boundaries. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
