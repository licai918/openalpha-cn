# OpenAlpha CN

Evidence-traceable, point-in-time, multi-agent research for China A shares.

> Status: `v0.1.0` is under active development. OpenAlpha CN is for research, education, and market review only. It is not investment advice and does not promise returns.

## Two ways to use the project

### Self-host OpenAlpha CN

Developers and researchers can connect their own data and model providers, reproduce historical research, and extend the provider, agent, tool, risk, memory, and validation contracts.

```powershell
git clone https://github.com/ss8875/openalpha-cn.git
Set-Location openalpha-cn
uv sync --all-extras --dev
uv run openalpha doctor
```

### Prefer a ready-to-use Windows application?

The separately licensed ChainLin Limit-Up Review desktop application is intended for users who do not want to configure Python, Node, databases, data providers, and models.

[Download ChainLin Limit-Up Review 1.0.9 after the Release is published](https://github.com/ss8875/openalpha-cn/releases/download/chainlin-desktop-v1.0.9/Lianlin-LimitUp-Review-Setup-1.0.9-x64.exe)

The current installer is unsigned and Windows may display a SmartScreen warning. Its size and SHA-256 will be published and verified in the Release notes. The desktop application is not automatically covered by this repository's MIT license.

## Research path

```text
Point-in-time data
→ Evidence Snapshot
→ Agent research
→ SignalFrame
→ DecisionLedger
→ Replay and validation
→ Attribution and improvement
```

## Data policy

The default legal-safe inputs are user-owned files, synthetic fixtures, and Tushare Pro with a user-supplied token. Optional adapters must declare their credential, cache, redistribution, rate-limit, freshness, and failure boundaries.

No secret, user runtime database, paid-provider raw dataset, or unlicensed scraped dataset belongs in Git.

## Development

```powershell
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

Read the [v1 specification](docs/specs/openalpha-cn-v1-spec.md), [implementation plan](docs/specs/openalpha-cn-v1-implementation-plan.md), and [security policy](SECURITY.md).

## License

OpenAlpha CN source code is released under the [MIT License](LICENSE). Third-party data, models, brand assets, and the ChainLin desktop installer retain their own licensing boundaries. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
