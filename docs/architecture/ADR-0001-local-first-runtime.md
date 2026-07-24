# ADR-0001: Local-first single-node runtime

Date: 2026-07-24  
Status: Accepted

## Context

OpenAlpha CN must be easy to self-host on Windows and Linux, preserve point-in-time datasets, run deterministic replay, and avoid requiring a distributed operations stack for the first release.

## Decision

The v1 runtime uses:

- FastAPI for the HTTP boundary;
- DuckDB and Parquet for analytical event data;
- SQLite WAL for run metadata, durable jobs, decisions, and checkpoints;
- filesystem volumes for local persistence;
- Docker Compose for the packaged deployment;
- a storage interface that permits a later PostgreSQL implementation.

The runtime does not require Redis, Kafka, Kubernetes, or a public cloud service in v1.

## Consequences

Positive:

- One command can start the system.
- Windows users can run without managing a database service.
- Frozen Parquet inputs are easy to hash, archive, and replay.
- The operational surface stays small enough for strong testing.

Negative:

- v1 is not intended for multi-node concurrent workloads.
- SQLite write concurrency is bounded.
- A hosted multi-tenant service will require a future storage and job-runner adapter.

## Guardrail

Domain and provider contracts must not import SQLite or DuckDB implementation types. Storage replacement must not change `EvidenceSnapshot`, `SignalFrame`, `DecisionLedger`, `RunManifest`, or `ValidationResult`.

