# OpenAlpha CN

Evidence-traceable, point-in-time, multi-agent research for China A shares.

> Current release: `v1.0.0`. Research, education, and review only. No investment advice, return promise, or live broker execution.

[中文](README.md) · [Deployment](docs/deployment/production.zh-CN.md) · [Data API](docs/api/data-interface.zh-CN.md) · [Feature ledger](docs/release/openalpha-v1-feature-ledger.md)

## Self-host

The recommended path is Docker Compose:

```bash
git clone https://github.com/ss8875/openalpha-cn.git
cd openalpha-cn
docker compose -f deploy/compose.yml up -d --build
```

Open `http://127.0.0.1:8000`. Runtime evidence and ledgers live in a dedicated persistent volume.

OpenAlpha CN provides four-clock point-in-time evidence, A-share event semantics, deterministic baseline agents, a structured model boundary, typed signals and decisions, A-share execution constraints, same-path replay, reconciled attribution, REST, Python SDK, CLI, and a responsive research workbench.

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
- one research core shared by live, replay, and backtest modes;
- A-share T+1, board lot, suspension, limit-lock, and transaction-cost constraints;
- evidence-linked decisions and reconciled rule/factor/agent attribution.

The project accepts user-owned CSV, JSON, JSONL, and Parquet data, BYOT Tushare, and an optional constrained AKShare adapter. It does not redistribute commercial raw datasets or expose a hosted data resale proxy.

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
