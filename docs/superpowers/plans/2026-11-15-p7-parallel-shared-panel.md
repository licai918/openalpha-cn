# P7 parallel book on the central panel -- implementation plan (`V2-P7-027`)

**Goal:** A's parallel book (a registration carrying `settings.survivor_parallel_of`) runs from its own
runtime directory but **reads the base book's panel and never writes it**: zero Tushare requests, no
second panel, no `cp -c` clone.

**Why (user, 2026-11-15):** one central panel; save disk and the doubled nightly requests; no loss of
quality. Measured on 8d991bc (read-only investigation):

- A runtime dir holds exactly one paper book: `<rt>/paper/registration.json` is a write-once marker and a
  second registration is refused at step 5 (`strategy_survivor_book.py:553-603`); `paper/snapshots`,
  `paper/writer.lock`, `state.sqlite3` are single. **So the parallel book keeps its own runtime dir.**
- Step 2 always runs `panel build --incremental`, which re-fetches the last stored session
  (`daily_selection.py:2299-2310,2766-2830`, `cli.py:3436-3475`): every run makes requests and rewrites
  partitions. Step 4 skips built instants (`build_factors`, `:3182-3270`) but would build any that are due.
- The morning recheck fetches `namechange` once into the panel (`_fetch_names`, `:5985`).
- The catalog lock blocks a reader up to 600 s, then `PanelCatalogBusyError` (`panel/store.py:322-345,672`).
- `scripts/survivor_annotate.py` already has the shape we want: `--runtime-dir` for the book,
  `--panel-runtime-dir` for the panel, refusing to open a panel dir without `panel/catalog.duckdb`.

## Rulings (controller)

- **R-S1.** Read-only is a property of the **registration**, not a flag: a registration with
  `survivor_parallel_of` always runs read-only; a base registration never does. No `--panel-readonly`
  switch that could be forgotten or misapplied to the base book.
- **R-S2.** The panel location is a new option `--panel-runtime-dir PATH` on `scripts/daily_selection.py`
  (evening, morning, `--show-book` where it reads the panel). A parallel registration **requires** it and
  refuses without it (exit 2, by name); a base registration **refuses** it (exit 2) -- the base book's
  command line and bytes stay exactly as today (R-N4). It must not equal `--runtime-dir`.
- **R-S3.** In read-only mode the evening **does not run** the panel update (step 2), the panel doctor's
  write paths, the calendar fetch in `_survivor_session`, or factor builds (step 4). Each of those steps is
  replaced by a **readiness check against the stored panel** at the run's pinned `as_of`: every dataset
  the evening reads covers the session, the calendar covers what the outcome window needs, every factor
  instant the configuration reads at the session's 16:30 is built. Anything missing -> refuse by name,
  exit 1, write nothing in the parallel runtime dir, and say: the base book's evening for that session
  has not completed (name the missing dataset/instant and the base runtime dir). Never fall back to
  fetching.
- **R-S4.** The morning recheck in read-only mode does not fetch `namechange`; it requires the stored
  `namechange` partition for the session's year to carry the base morning's fetch for today (a coverage
  `fetched_at` at or after today's morning window start -- implementer finds the exact field and
  documents it), else refuses by name ("the base book's morning recheck has not run"). The parallel
  morning job fires 15 minutes after the base morning (08:30 -> 08:45, a named constant
  `LAUNCHD_PARALLEL_MORNING_LAG_MINUTES = 15`): the evening's 60-minute lag would reach 09:30, past the
  09:15 auction.
- **R-S5.** Journals record the mode: the evening/morning journal of a read-only run carries the panel
  directory used and `"panel_mode": "read_only"` **only for parallel registrations** (base journal bytes
  unchanged). Request accounting shows 0 for steps 2/4.
- **R-S6.** Smoke: the smoke root has a single book, so read-only is exercised by tests with generated
  panels, not by the real-data smoke. The real-data smoke with `--entry-veto` (already shipped) still
  exercises the veto window on real data. Runbook says so.
- **R-S7.** No change to `src/openalpha_cn/domain/`, contracts, or the registration body: the panel
  location is an operational argument, not registered content (like `--runtime-dir`).

## Tasks (one DSH implementer, sequential, TDD)

### Task 1 -- `--panel-runtime-dir` and the read-only evening
- `scripts/daily_selection.py`: option, R-S2 refusals, R-S3 readiness checks replacing steps 2 and 4 (and
  the calendar fetch) for parallel registrations; the panel store opened through the existing composition
  root on the panel dir (hard rule 5: no second store construction outside it); paper book, journals,
  `state.sqlite3` stay in `--runtime-dir`.
- Tests (behavioural, generated panels, no network): (a) base registration + `--panel-runtime-dir` ->
  exit 2, nothing written; (b) parallel without it -> exit 2; (c) equal to `--runtime-dir` -> exit 2;
  (d) parallel against a panel the "base" has completed for the session -> runs, zero provider calls (a
  tripwire provider that raises on any call), panel directory byte-for-byte unchanged (hash the tree
  before/after), paper book written in its own dir; (e) a panel missing tonight's session or a factor
  instant -> exit 1, names what is missing, nothing written anywhere; (f) base evening behaviour and
  printed bytes unchanged (existing pinned tests pass untouched).

### Task 2 -- read-only morning, `--show-book`, launchd, runbook
- Morning per R-S4; `--show-book` accepts `--panel-runtime-dir` only if it reads the panel (implementer
  checks; if it does not read the panel, it neither needs nor accepts it).
- `launchd_plist` / `launchd_morning_plist` for a parallel registration add
  `--panel-runtime-dir <base runtime dir>` (new required printer input for parallel; base plist bytes
  unchanged -- existing literal tests untouched) and the morning lag of R-S4.
- Runbook `docs/runbooks/daily-selection.zh-CN.md` section 并列账簿: replace the `cp -c` step with "own
  runtime dir + `--panel-runtime-dir <基础运行目录>`"; state zero requests every night, that the
  parallel book needs the base book's evening/morning to have finished, and what its refusal looks like.
  Keep the "not A's test" and candidate wording. Documented command lines must be runnable or in prose
  (`tests/integration/test_documented_command_lines.py` runs fenced `openalpha` lines verbatim).
- Tests: morning read-only refuses without today's base `namechange` fetch, runs with it, zero calls;
  printers include the flag and times for parallel; base printers byte-identical.

## Acceptance (controller verifies)
1. Every refusal above tested by behaviour; tripwire proves zero provider calls; tree hash proves no panel
   write in read-only mode.
2. Base book: no pre-existing literal/SHA/plist expectation edited (`git diff` of existing tests shows only
   additions); journal bytes unchanged.
3. Static checks green; controller's serial gate (`tests/unit tests/integration`) green on the final tip.
