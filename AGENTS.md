# OpenAlpha CN Repository Instructions

## Current work pointer

The active direction is **v2**. The workspace is `docs/specs/v2/`:

- `openalpha-cn-v2-prd.md` — scope, measured baseline, decisions. Read this to know what is in and out.
- `openalpha-cn-v2-roadmap.md` — the work breakdown: 253 issues across P0.A–P7, each with dependencies, evidence, test seam and PRD mapping. **Take work from here.**
- `openalpha-cn-v2-p7-decision-chain.md` — the **current phase (P7)**: measured facts about the two decision chains, the exploratory findings (labelled as not evidence), the decisions, the order of work and its gates, and the revision log. Read it before touching P7 work; change the order or a gate only by adding a row to its section 9. The route as adjusted on 2026-11-01 (real-data smoke first, `006` split into `006a`/`006b`, G-0 rewritten, issues `V2-P7-019`…`024`) is `docs/superpowers/plans/2026-11-01-p7-route-adjustment.md`. As of 2026-11-02, development batch 1 (`V2-P7-004`, `009`, `018`, `019`, `021`, plus the new `V2-P7-025`, the registry's New Year gap, a hard gate before 2027-01-04) is on the integration branch, not pushed and not run against real data: its record, the decisions still the user's and the prerequisites of the real validation read and the real smoke are in `docs/superpowers/plans/2026-11-02-p7-dev-batch-1-closeout.md`. As of 2026-11-07, development batch 2 (`V2-P7-025`'s code and the four `V2-P7-024` prerequisites -- the runtime-directory refusal, the calendar-exact `minimum_periods` before the claim, the validation-before-`second_use` order, and `as_of_reason`; `V2-P7-025`'s value is provisional, the user's to confirm) is on `feat/v2-p7-025-batch-2`, not pushed and not run against real data; the code was frozen at `4b4ec94` for the first serial gate, and after it came a documentation wave and then the final-review fix wave, which changed executable bytes (`scripts/research/p7_reading.py` -- the preflight now names an unplannable calendar and the knowledge line marks the registrant's words; `src/openalpha_cn/domain/stock_universe.py` -- `universe_year_may_be_empty` bounds the day to its year), so the serial gate that counts is the one on the final tip; its record is `docs/superpowers/plans/2026-11-07-p7-dev-batch-2-closeout.md`. No gate or order changed and the issue count stays 253.
- `openalpha-cn-v2-seam-audit.md` — 103 findings with `file:line` evidence, each mapped to a closing issue. Read the relevant section before touching a subsystem.

`docs/specs/openalpha-cn-v1-spec.md` remains the **contract baseline** — the v1 domain contracts it describes are still authoritative. It is no longer the work pointer.

Every change must name its issue ID (`V2-P0A-001` … `V2-P7-025`) in the branch, commit and pull request.

## Working agreement

- Read `docs/HANDOFF_CURRENT.md`, then the roadmap phase you are working in.
- Implement one issue at a time and keep the repository runnable.
- Write a failing behavioral test before implementing new logic.
- Preserve evidence source, time, revision, content hash, and license metadata.
- Never commit credentials, runtime databases, user output, or unlicensed raw datasets.
- Never count a stub, mock, file name, or UI control as a completed feature.
- Do not publish or force-push until the local release gate passes.

## v2 hard rules

These come from the four-seam audit. Violating one costs more to undo than to obey.

1. **The two data planes stay separate.** Panel data (prices, fundamentals, calendar, universe, industry, adjustment factors) goes to the partitioned panel store. It must never enter `ParquetEvidenceStore`, whose per-row rebuild-and-rehash is a correctness feature for discrete evidence and fatal at factor scale. Audit F71–F75.
2. **`domain/` imports no numerical or infrastructure library.** No numpy, pandas, sqlite3 or duckdb. `DataFrame`/`ndarray` live only in `panel/`, `factors/`, `models/`. A lint gate enforces this (`V2-P0A-005`); do not weaken it to make a type check pass.
3. **No breaking contract change without a migration.** Rows are opaque JSON with `extra="forbid"` and `Literal[".../v1"]`, so adding one field makes old and new rows mutually unreadable. The migration mechanism (`V2-P0B-004`) lands first, and all three breaking changes ship together in the single `V2-P4-001` window. Audit F65–F66.
4. **Do not skip the P2 PIT red-team gate.** Factor work on look-ahead-contaminated data produces confident wrong answers. P2 must be green before any P3 issue starts.
5. **One composition root.** After `V2-P0B-002` a single place wires the stores. Do not reintroduce a second; `api/app.py` and `sdk.py` previously duplicated eight store constructions by hand, and the drift already caused identical payloads to succeed on one route and fail on another.
6. **Panel fixtures are generated at test time.** `.parquet`, `.duckdb`, `.sqlite3` and `.db` are publication-blocked suffixes in `scripts/verify_publication.py`; a checked-in fixture fails CI.
7. **Ledger references must resolve.** Every `file#symbol` in the capability ledger is AST-verified (`V2-P0A-001`) and every `acceptance_test` binds to a real pytest node id (`V2-P0A-003`). A file that merely exists is not evidence.
8. **Reproducibility claims must be real.** `code_commit` comes from git, `config_digest` is computed, and `random_seed` is threaded to every stochastic component including BLAS thread counts. All three feed `decision_id`. Audit F87–F91.
9. **Moving a test file is a three-artifact change.** `artifacts/openalpha-v1-feature-coverage/features.csv` names every test file; regenerate it, `summary.json` and the ledger in the same commit or two CI jobs go red.

## Research honesty

Engineering completion never implies research validity. Pre-registered out-of-sample metrics, net-of-cost incremental value, multiple-testing control, and predictions persisted before outcomes are known are the only evidence that a signal is real. Do not upgrade user-visible wording from "candidate" to anything stronger on the strength of a passing test suite.

<!-- CODEGRAPH_START -->
## CodeGraph

When a `.codegraph/` directory exists, use CodeGraph before grep/find or direct file reads when locating or understanding code:

- `codegraph explore "<question or symbols>"`
- `codegraph node <symbol-or-file>`

If `.codegraph/` does not exist, do not create or rebuild the index unless explicitly requested.
<!-- CODEGRAPH_END -->

## Stable commands

```powershell
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```
