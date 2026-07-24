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
