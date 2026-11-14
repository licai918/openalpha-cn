# P7 A/B follow-ups -- daily-list annotation, A's parallel book, B's research-window increment (V2-P7-027, V2-P7-028)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Implementers are
> DeepSeek Harness sessions (`deepseek-official/deepseek-flash`, TDD); the controller (Claude) dispatches,
> an Opus reviewer reviews each task, the controller runs the serial gate and merges with `--no-ff`.

**Goal:** finish the development side of plans A and B that does not wait on C: (N) a read-only command that
annotates the daily list with A's veto and B's two factors for the human trader; (A) plan A's veto running as a
parallel, separately registered paper book; (B) an exploratory research-window measurement of B's incremental
rank information over the survivor composite.

**Architecture:** one shared seam first (Task 1: the five-session veto window read moves from research code into
`panel_limit_events.py`). N is one new top-level script that only reads. A threads the veto through the one
`decide_book` caller (`run_evening`) and lets `p7_register.py` write a v2 book outside the 006b chain. B is one new
exploratory script under `scripts/research/` reusing `p7_diagnostics`' engine and `domain/incremental_ic.py`.

**Tech stack:** Python 3.12, pydantic, the panel store, pytest; no new dependency.

**Spec / authority:** `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §4.3, §4.4, §9 (rows of
2026-11-14); `docs/superpowers/plans/2026-11-14-p7-plans-a-b.md` (R-A1, R-A2, R-B1, R-R1);
`docs/superpowers/plans/2026-11-14-p7-forward-ab.md` (R-F1..R-F6). The user, 2026-11-14: plan all of A and B,
DSH implements, Opus reviews; the repo never connects to a broker -- it prints a list the user executes by hand,
and real money vs paper is the user's decision.

## Rulings (controller, 2026-11-14)

- **R-N0 -- R-F1's deferral is lifted by the user.** "A 的并列第二账簿推迟" (R-F1) was the controller's ruling;
  the user's request of 2026-11-14 to plan all of A and B supersedes it. Task 0 records this in §9. The
  `forward_ab_1` registration (`4282533`) is not touched: the parallel book is a product/paper artifact; the
  veto's statistical test stays the frozen `forward_ab_1` test.
- **R-N1 -- the forward read runs from a worktree pinned at `4282533`.** `p7_reading` binds `REGISTERED_PATHS`
  (`src`, `scripts/research`, `pyproject.toml`, `uv.lock`) to the registration's `code_commit` `7c8b912`
  (`registry.py:93`, `:286-294`). Every task here changes those bytes on main, which is allowed: the 2027 read
  is executed on a branch `read/v2-p7-027-028-forward-ab-1` cut from `4282533`, its claims committed there and
  merged back with `--no-ff`. Task 0 writes this into the runbook §7.4.
- **R-N2 -- the annotation never changes the list.** It reads the journal and the panel and prints; it writes
  nothing, sends no request, and is not bound by any registration (a top-level `scripts/` file outside
  `p7_smoke_env.BOUND`). Every cell is labelled "候选参考 / candidate reference".
- **R-N3 -- the veto reads each session at its own 16:30.** The window is the session and its four calendar
  predecessors, newest first; each session is read at `session_publication_instant(s)`, never at the run's
  `as_of`. This is what makes a rerun at a later instant reproduce the same set, which `run_evening`'s
  `unchanged` path requires.
- **R-N3a -- a restatement inside the window refuses, it does not reproduce (review of Task 1).** The panel store
  keeps no old versions of a restated row, so reading a session at its own 16:30 after that session was restated
  refuses (`PanelStorageError`). A v2 book's evening -- first run or rerun -- whose five-session window (or a
  board streak walk) holds a restated session refuses by name rather than silently changing the vetoed set. The
  guarantee is "never a different set", not "always the same set". The base book reads no veto (R-N4).
- **R-N4 -- the base book's bytes do not move.** With `entry_veto == "none"` nothing new is read, filed,
  printed or labelled: no veto read, no new journal key, the same launchd label, the same `EveningResult`
  fields' values. Tests pin it.
- **R-N5 -- a parallel registration is outside the survivor chain.** It names the base registration it runs
  beside (`settings.survivor_parallel_of: {"path", "sha256"}`), is never a chain root or a continuation, and is
  refused if it also names `survivor_continuation`.
- **R-N6 -- B's measurement is exploratory and in-sample.** The research window was P6's discovery and
  composition data and 2021 was already rehearsed; the output carries the `EXPLORATORY, UNREGISTERED -- NOT
  EVIDENCE` banner, no p-value gate, candidate wording. No walk-forward tree fit and no P6 grid/ledger row: the
  literal "grid row" would rerun a spent, failed protocol. A walk-forward model with B features is a later
  forward pre-registration, not in this plan.
- **R-N7 -- A's LLM veto stays gated.** It opens only after C's L6 reading; no task here.

## Global Constraints

- Every branch, commit and PR names its issue: `V2-P7-027` (Tasks 1, 3, 5, 5b), `V2-P7-028` (Task 6), both
  (Tasks 0, 2).
- AGENTS.md hard rules 1-9. Rule 2: `domain/` imports no numpy/pandas/sqlite3/duckdb. Rule 3: no stored
  contract changes shape (`survivor-book-config/v1` bytes and `config_id` unchanged; `openalpha-daily-survivor/v1`
  journals of a no-veto book byte-identical). Rule 5: open the panel through `panel_view.panel_store`, never by
  hand. Rules 7/9: a new or moved test file regenerates `artifacts/openalpha-v1-feature-coverage/features.csv`,
  `summary.json` and the ledger in the same commit (`uv run python scripts/build_feature_coverage.py` then
  `--check`).
- No network, no Tushare/DeepSeek request, no `--live`, no launchd install (print only), no write to
  `~/openalpha-research`, `~/openalpha-smoke` or `~/openalpha-readings`. Tests use generated panels only.
- Do not touch `docs/research/p7-readings/`, `docs/research/p7-c-registration.json`, or the `1289dad` / `edaaa26`
  worktrees.
- Wording: every result and every annotation is "候选 / candidate"; nothing is called evidence or confirmatory.
- TDD: a failing behavioral test before each piece of logic. Implementers run only the test files they touch plus
  `tests/unit/test_source_counts_match_the_tree.py`, `tests/unit/test_panel_ingest_import_isolation.py` and the
  coverage check -- never a directory-wide suite. Worktree runs set `PYTHONPATH=<worktree>/src`.
- A new top-level module under `src/openalpha_cn/` (none is planned) would need rows in
  `tests/unit/test_panel_ingest_import_isolation.py` and an import-linter contract; prefer adding to existing
  modules as specified.

## Review Focus

1. A rerun of a vetoed evening at a later `as_of` (R-N3a): with no restatement -> the `unchanged` path and the
   same vetoed set (Task 3 test `test_a_rerun_without_a_restatement_reproduces_the_vetoed_evening`); after a
   restatement of a session inside the window -> refused by name, naming that session, nothing written
   (Task 3 test `test_a_rerun_after_a_restatement_refuses_rather_than_changing_the_vetoed_evening`).
2. A veto window that crosses a holiday or New Year -> five *sessions*, not five days; the calendar's start ->
   refused by name (Task 1 tests).
3. The annotation run against a runtime dir with no B build for the session -> exit 1 with the exact
   `openalpha factor build ...` line, nothing written (Task 2 test `test_a_missing_b_build_is_refused_with_the_build_command`).
4. A base (no-veto) book after Task 3 -> journal, printed list and launchd plist byte-identical to before (Tasks
   3, 5, 5b tests named `..._base_book_..._unchanged`).
5. A parallel registration that also names a continuation, names a base registration that is not committed, or
   uses `entry_veto: "none"` -> refused by name (Task 5 tests).

---

### Task 0 -- records (controller, docs only; `V2-P7-027`, `V2-P7-028`)

- [ ] Add a §9 row to `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`: R-N0 (user lifts R-F1's deferral;
  `forward_ab_1` untouched), R-N1, R-N7, and the user's product stance (no broker; the list is executed by hand;
  real money vs paper is the user's decision -- the "real-money gate" is reported advice, not a lock).
- [ ] Runbook `docs/runbooks/p7-reading.zh-CN.md` §7.4: the forward read runs on `read/v2-p7-027-028-forward-ab-1`
  cut from `4282533` (R-N1), with the exact `git worktree add` command.
- [ ] Commit `docs(V2-P7-027, V2-P7-028): ...`.

### Task 1 -- the veto window read in the panel plane (`V2-P7-027`)

**Files:** Modify `src/openalpha_cn/panel_limit_events.py`, `src/openalpha_cn/domain/survivor_veto.py`,
`scripts/research/p7_reading.py` (`_vetoed_on`, `_limit_events_reader` delegate). Test:
`tests/integration/panel/test_limit_event_producer.py` (or a new `tests/integration/panel/test_veto_window.py`),
`tests/unit/domain/test_survivor_veto.py`.

**Interfaces -- produces:**
- `panel_limit_events.veto_window(store: PanelStore, *, session: date, calendar: TradingCalendar,
  exchange: str = DEFAULT_EXCHANGE, lookback: int = VETO_LOOKBACK_SESSIONS) -> tuple[tuple[LimitEvent, ...], ...]`
  -- newest first, each session read at its own `session_publication_instant`; raises `LimitEventReadError`
  naming the window when a predecessor is beyond the calendar.
- `panel_limit_events.vetoed_on(store, *, session, calendar, exchange=DEFAULT_EXCHANGE) -> frozenset[str]`
  = `vetoed_codes(veto_window(...))`.
- `domain.survivor_veto.VetoDetail` (frozen dataclass: `ts_code: str`, `event_count: int`, `mean_weight: float`,
  `vetoed: bool`) and `veto_details(events_by_session) -> Mapping[str, VetoDetail]` (only names with an event;
  same lookback refusal as `vetoed_codes`; `{d.ts_code for d in details.values() if d.vetoed} == vetoed_codes(...)`).

- [ ] Tests first: (a) `veto_details` agrees with `vetoed_codes` on the existing veto fixtures (broken_board
  alone vetoed; broken_board + limit_up not vetoed; a sixth session refused); (b) `veto_window` on a generated
  panel spanning a holiday returns five sessions; reads each at its own instant (a row restated after session
  s's 16:30 does not change s's events); a window reaching the calendar start raises `LimitEventReadError`;
  (c) `p7_reading`'s A/B measurement on its existing fixture gives byte-identical output after delegation
  (the existing `tests/unit/scripts/test_p7_ab_reading.py` stays green unchanged).
- [ ] Implement; run the touched test files; commit `feat(V2-P7-027): the veto window read moves into panel_limit_events`.

### Task 2 -- daily-list annotation (`V2-P7-027`, `V2-P7-028`; after Task 1)

**Files:** Create `scripts/survivor_annotate.py`, `tests/unit/scripts/test_survivor_annotate.py`; ledger row
`OA-...` for the command; regenerate coverage artifacts.

**Interfaces -- consumes:** Task 1's `veto_window`, `veto_details`; `daily_selection.read_survivor_journal`,
`survivor_journal_complete`, `survivor_journal_directory`; `strategy_survivor_morning.plan_names`;
`panel_view.panel_store`; `panel_factors.load_factor_observations`, `LIMIT_UP_COUNT_20`, `BROKEN_BOARD_COUNT_20`;
`backtest.factor_ic.TIER_ADMITTED_CODES["raw"]`; `daily_selection.smoke_banner/with_smoke`.

**Produces:**
- `main(argv: Sequence[str] | None = None) -> int`. Args: `--runtime-dir` (required; holds the journal),
  `--panel-runtime-dir` (default = `--runtime-dir`; whose `panel/` is read -- an APFS clone in practice),
  `--config-id` (required, the book's), `--session YYYY-MM-DD` (default: newest complete journal), `--json`.
  Exits: 0 printed; 1 refused (no journal, incomplete journal, epoch or settle-only day, no panel catalog,
  no B build); 2 usage.
- `raw_cross_section(store, definition, *, session: date) -> Mapping[str, float]` -- the earliest raw build at or
  after `session_publication_instant(session)`, admitted codes and finite values only (the `p7_reading._FactorReads`
  rule); raises `MissingFactorBuild(command: str)` whose `command` is
  `openalpha factor build --factor <key> --tier raw --year <yyyy> --as-of <session 16:30 +08:00 ISO> --max-staleness-days <n>`
  (argument names as `factor_view._unbuilt_factor_remedy` prints them).
- `percentile(value: float, cross_section: Sequence[float]) -> float` -- mid-rank / N in [0, 1].
- `annotate(*, journal: Mapping[str, Any], store: PanelStore, calendar: TradingCalendar, exchange: str) -> dict`
  kind `survivor-annotation/v1`: `session`, `config_id`, `rows` (one per name: `role` in
  `ENTRY|EXIT|HOLD|WATCH`, `ts_code`, `veto` {`rule`, `vetoed`, `event_count`, `mean_weight`} or `null` when no
  event, `limit_up_count_20` {`value`, `pct`, `n`} or `null` when the name is not in the build, same for
  `broken_board_count_20`), `wording`.
- `annotation_lines(document) -> list[str]`: header line "候选参考 / candidate reference only: not part of the
  list, the paper book or the journal; nothing was written and no request was sent"; one line per row; the
  journal's survivor wording as footer; the smoke banner first and last when the journal's registration is a
  smoke one.

- [ ] Tests first, on a generated panel + a fabricated complete journal: roles come from `plan_names` and
  `result.paper.decision` (ENTRY/EXIT/HOLD/WATCH each present once in a fixture); a vetoed name shows
  `vetoed: true` with its count and mean; a name with no event shows `null`; percentile mid-rank on ties (all
  zeros -> 0.5); `test_a_missing_b_build_is_refused_with_the_build_command` (exit 1, the exact line on stderr);
  `test_the_annotation_writes_nothing` (hash of the runtime-dir and panel trees before == after);
  an epoch-day and a settle-only journal refused by name; AST test: the script imports no
  `openalpha_cn.backtest.paper*`, no `runtime.composition.build_storage`, no `providers`.
- [ ] Implement; run touched tests + count/isolation/coverage checks; commit
  `feat(V2-P7-027, V2-P7-028): survivor_annotate prints A's veto and B's factors beside the daily list`.

### Task 3 -- the veto in the evening (`V2-P7-027`; after Task 1)

**Files:** Modify `src/openalpha_cn/backtest/paper_evening.py` (`run_evening`, `EveningResult`),
`src/openalpha_cn/strategy_survivor_book.py` (`evening`), `src/openalpha_cn/strategy_survivor_summary.py`
(`_paper_section`, `summary_lines`). Tests: `tests/unit/backtest/test_paper_evening.py`,
`tests/unit/test_strategy_survivor_book.py`, `tests/unit/test_strategy_survivor_summary.py`.

**Interfaces -- produces:**
- `run_evening(..., vetoed: frozenset[str] = frozenset())` passes it to `decide_book(..., vetoed=vetoed)`;
  `EveningResult.vetoed: frozenset[str] = frozenset()` (the set used, both on `created` and `unchanged`).
- `strategy_survivor_book.evening` computes `vetoed = frozenset()` when `registration.book.entry_veto == "none"`
  else `vetoed_on(panel, session=session, calendar=calendar, exchange=registration.adapter.exchange)`, inside the
  existing `_refusals("paper book", ...)` block; `catch_up`, `settle_only`, `catch_up_again` unchanged (they make
  no decision).
- Journal: `_paper_section` adds `"entry_veto": {"rule": VETO_RULE_ID, "vetoed": sorted(result.vetoed & survivors)}`
  **only** when the book's `entry_veto != "none"`; `summary_lines` adds one line
  `entry veto market_bearish_5/v1 blocked: <codes or none>` under the same condition.

- [ ] Tests first: a v2 book whose top-ranked survivor is vetoed buys the next one and journals the veto block;
  a held vetoed name stays held (R-A1); `test_a_rerun_without_a_restatement_reproduces_the_vetoed_evening`;
  `test_a_rerun_after_a_restatement_refuses_rather_than_changing_the_vetoed_evening` (R-N3a);
  `test_the_base_book_evening_and_journal_are_unchanged` (a v1/none book: no veto read -- the panel reader is a
  spy that fails if `limit_events_on` is called -- and journal bytes equal a literal captured on the base commit);
  every existing test in the three files green unchanged.
- [ ] Implement; run the touched files plus `tests/unit/scripts/test_daily_selection_survivor_day.py`,
  `test_daily_selection_survivor.py`, `test_daily_selection_survivor_binding.py`; commit
  `feat(V2-P7-027): a v2 book's evening applies market_bearish_5/v1 to entries and journals it`.

### Task 5b -- a launchd label per parallel book (`V2-P7-027`; after Task 5)

**Files:** Modify `scripts/daily_selection.py` (`_launchd_plist` call sites for `LAUNCHD_LABEL` and
`LAUNCHD_MORNING_LABEL`). Test: the existing launchd tests in `tests/unit/scripts/test_daily_selection_survivor*.py`.

**Produces:** the printed plist's label is `f"{LAUNCHD_LABEL}.{config_id[:16]}"` (and the morning label likewise)
**only** when the registration carries `settings.survivor_parallel_of`; otherwise unchanged. Still print-only.

- [ ] Tests first: a parallel registration prints the suffixed labels; `test_the_base_book_plist_is_unchanged`
  (byte-equal to a literal from the base commit). Implement; commit
  `feat(V2-P7-027): a parallel book prints its own launchd labels`.

### Task 5 -- registering the parallel book (`V2-P7-027`; after Task 3)

**Files:** Modify `scripts/p7_register.py` (`registration_body`, `_write`, `chain_problems`,
`committed_survivor_registrations` or its filter), `scripts/p7_smoke_env.py` (`smoke_configuration` already takes
`book=`), `tests/unit/scripts/test_p7_register.py`, `tests/unit/test_p7_registration_chain.py`; runbook
`docs/runbooks/daily-selection.zh-CN.md` (a "并列账簿" section).

**Produces:**
- `registration_body(epoch: date, *, book: SurvivorBookConfig | None = None, parallel_of: ParallelOf | None = None) -> dict`;
  `ParallelOf` = `{"path": str, "sha256": str}` written to `settings.survivor_parallel_of`.
- CLI: `p7_register.py ... --entry-veto market_bearish_5/v1 --parallel-of <committed base registration path>`
  builds the base book's parameters as `survivor-book-config/v2` with that `entry_veto`; `--entry-veto` without
  `--parallel-of` and vice versa -> exit 2.
- Chain: a registration with `survivor_parallel_of` is excluded from chain-root counting and from
  `admit_predecessors`; `test_p7_registration_chain.py` asserts the 006b chain still has at most one root and that
  every parallel registration names a committed base whose sha256 matches.

- [ ] Tests first: the written v2 registration round-trips through `read_survivor_registration` and its
  `config_id` differs from the base's; refusals: names `survivor_continuation` too; base path not committed; base
  sha256 mismatch; `entry_veto` `none`; a second parallel registration of the same config. Implement; update the
  runbook with the operating sequence (after the 006b registration; clone the base runtime's panel with `cp -c`
  after the base evening so the parallel book's step 2 sends zero requests; its own smoke at the new commit before
  registering; all of it the user's go). Commit
  `feat(V2-P7-027): p7_register writes A's veto book as a parallel registration outside the 006b chain`.

### Task 6 -- B's incremental information, exploratory (`V2-P7-028`; independent of Tasks 1-5)

**Files:** Create `scripts/research/p7_increment.py`, `tests/unit/scripts/test_p7_increment.py`; runbook
`docs/runbooks/p7-reading.zh-CN.md` gets a short "B 的增量（探索性）" section.

**Interfaces -- consumes:** `p7_windows.RESEARCH`; `p7_diagnostics`' window-capped store, `plan_signal_days`
(or the shared `sampled_sessions`), panel label source, `EXPLORATORY_KIND` banner and `config_digest`;
`domain.incremental_ic.partial_rank_ic_with_reason`, `non_overlapping`.

**Produces:**
- `increment_readings(*, sessions: Sequence[date], composite: Callable[[date], Mapping[str, float]],
  extra: Callable[[date], Mapping[str, float]], labels: Callable[[date], Mapping[str, float]]) -> tuple[SessionIncrement, ...]`
  -- per sampled session, on the default book's eligible names that have all three values: the partial rank IC of
  the oriented extra factor given the composite, with the reason when not computable; counted gaps per session.
- `increment_document(...) -> dict` kind `p7-increment/v1`: window, stride 20, horizon 20, both B factors, per-session
  rows, mean, sd, n, t (descriptive), the banner, candidate wording, `config_digest`; no p-value field.
- CLI `explore --runtime-dir --as-of --first --last [--out]`, refusing any day outside `RESEARCH`.

- [ ] Tests first on a generated panel: a planted extra factor correlated with the label beyond the composite gives
  a positive mean partial IC; a copy of the composite gives ~0; a window touching 2022 refused by name; the document
  has no p-value key and carries the banner; oriented by declared direction (`lower_is_better` negated).
- [ ] Implement; commit `feat(V2-P7-028): p7_increment measures B's partial rank IC over the composite (exploratory)`.

## After merge -- execution, each step the user's explicit go

1. B: raw builds of both factors for 2015-2021 on a clone of `~/openalpha-research/panel` (zero Tushare requests;
   check `stk_limit` 2014-2021 partitions first), then one `p7_increment explore` run; report as exploratory.
2. N: use `survivor_annotate.py` against a smoke/006b runtime dir with a cloned panel that holds the B raw build
   for the session (the command prints the build line when it does not).
3. A: after the 006b registration -- the parallel book's own smoke at the new commit, then its registration and a
   pinned worktree for it, never the 006b one.
4. The `forward_ab_1` read in 2027 per R-N1.
