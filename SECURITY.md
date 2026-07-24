# Security Policy

## Supported versions

Security fixes are provided for the latest minor release. Before `v1.0.0`, only the latest published prerelease is supported.

## Reporting a vulnerability

Do not publish credentials, private market data, exploit details, or personally identifiable information in a public issue.

Use GitHub private vulnerability reporting when it is enabled for this repository. If private reporting is unavailable, open a public issue containing only a request for a private contact channel.

Include:

- affected version and commit;
- operating system and deployment mode;
- minimal reproduction without secrets or private data;
- expected impact;
- any known workaround.

## Credential handling

OpenAlpha CN never requires credentials to be committed. Provider and model credentials belong in local environment variables or an operating-system secret store. Logs, run manifests, exported reports, support bundles, and test fixtures must redact them.

## Investment and data boundary

OpenAlpha CN is a research tool, not an execution or custody service. A security report must not contain broker passwords, order credentials, production tokens, customer datasets, or third-party data that the reporter cannot lawfully share.

