# P7 A/B forward reading + the as_of gap (V2-P7-024, V2-P7-027, V2-P7-028)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Implementer is a
> DeepSeek Harness session; the controller (Claude) reviews, runs the serial gate, merges and registers.

**Goal:** turn A's and B's exploratory candidates (second_use read `f9c85cd`) into a **forward**
test: one pre-registered, read-once reading of a window that starts *after* its registration is
committed, using exactly the frozen R-R1 tests; and close the `as_of` gap that cost the second_use
read 9 of 33 signal days, before any further read is claimed.

**Authority:** the user's "按推荐，继续推进" of 2026-11-14 on the recommendation "B 先走 forward; A
后走". Controller rulings below (also appended to decision chain §9 by the docs commit).

**Spec:** `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §4.3, §4.4, §7, §9;
`docs/superpowers/plans/2026-11-14-p7-plans-a-b.md` (R-A1, R-A2, R-B1, R-R1 are unchanged).

## Rulings (controller, 2026-11-14)

- **R-F1 one forward window covers A and B.** Test 1 (vetoed survivors' h20 return minus all
  survivors') measures A from the panel alone; it needs no second paper book. The "A as a parallel
  second book" step is **deferred**: a book adds a portfolio-level reading that has far less power
  than the prediction-level test, and it would need its own daily runtime. Cost if wrong: A's
  portfolio effect is learned later; nothing is lost from the forward record.
- **R-F2 the window.** Name `forward_ab_1`; first `2026-11-16` (the first session after the
  registration commit of 2026-11-14), last `2027-06-30`, year ceiling 2027. Same R-R1 tests, same
  thresholds, same h20 / stride 20 / BH q=0.10 family of 3 / ≥60 vetoed observations; the section
  schema stays `p7-ab-tests/v1` with `window: "forward_ab_1"`. Roughly 150 sessions → about 6–7
  non-overlapping h20 periods (推算). At B's historical spread (sd ≈ 0.13 per period, 推算) a true IC
  of ≈ 0.10 gives t ≈ 1.9; this is a planned read date, **not** a promise of significance.
- **R-F3 forward property.** A `forward_ab_1` registration is refused at `register` time if the clock
  is at or after the window's first session open (`2026-11-16 09:30 Asia/Shanghai`), and refused at
  `read` time unless the registration's commit (committer time, from git) is before that instant.
  This is a record, not a proof (commit times are self-reported); the result text says so.
- **R-F4 order.** A `forward_ab_1` read requires the `second_use` window's claim to exist (it is the
  read the candidates came from). G-0's secondary diagnostics run in the same read as in second_use
  (role secondary, no stop rule).
- **R-F5 the as_of gap (fix before any new claim).** The read's preflight -- before any claim file is
  written -- refuses when the registered `as_of` falls on a Shanghai session later than the last
  session the runtime panel's `daily` dataset holds: it names that last session and says the `as_of`
  must be at or before that session's last write. A window's own last day still must be published
  before `as_of` (existing check). Cost if wrong: a refusal the operator overrides by re-registering
  before any claim, never a lost window.
- **R-F6 the window rule's wording.** `p7_windows`'s "nothing after 2026-09-24" rule gains exactly one
  exception, `forward_ab_1`, because it is registered before its first session; the docstring says so.

## Global constraints

- Branch `feat/v2-p7-forward-ab`; every commit names `V2-P7-024` (R-F5) or `V2-P7-027, V2-P7-028`
  (R-F1..R-F4, R-F6).
- AGENTS.md hard rules 1–9; wording stays candidate / 候选; no confirmatory wording anywhere.
- No network, no Tushare/DeepSeek request, no `--live`; do not read or write `~/openalpha-research`;
  fixtures are generated at test time.
- TDD: a failing behavioral test before each piece of logic. Run only the touched test files plus
  `tests/unit/test_source_counts_match_the_tree.py`, `tests/unit/test_feature_ledger_test_tree.py` and
  `scripts/build_feature_coverage.py --check`; never a directory-wide suite (the controller runs it).
- Do not write `docs/research/p7-readings/*` (the controller registers after merge).

## Tasks

### F1 -- `V2-P7-024` as_of gap guard
`scripts/research/p7_reading.py` (`_panel_preflight` or a step it calls), tests in
`tests/unit/scripts/test_p7_reading*.py`. A generated panel whose `daily` ends on session D: a
registration with `as_of` on a session after D is refused by `read_once` with
`ReadingPreflightError` naming D, and **no** window-claim / claim / result file exists afterwards;
`as_of` at D 19:00 passes this check. Runbook `docs/runbooks/p7-reading.zh-CN.md` gains one line.

### F2 -- `V2-P7-027, V2-P7-028` the forward window
`scripts/research/p7_windows.py` (`FORWARD_AB_1` in `WINDOWS`, docstring per R-F6),
`scripts/research/p7_reading.py` (`_ab_tests_of` accepts `second_use` or `forward_ab_1`;
`adopted_ab_tests(window=...)`; register-time and read-time checks per R-F3; order per R-F4; the
window-opening list `"validation" or "second_use"` gains `forward_ab_1`), `scripts/research/p7_read.py`
(`--window forward_ab_1` help text), runbook §7. Tests: the adopted section on `forward_ab_1` round-trips
and carries R-R1's values unchanged; a hand-written section naming another window is refused; register
at/after `2026-11-16 09:30+08:00` refused; read refused when the registration commit is after that
instant; read refused when `second_use` has no claim; an existing second_use test stays green unchanged.

### F3 -- controller
Review, serial gate, `--no-ff` merge, decision chain §9 row, then `p7_read.py register --window
forward_ab_1 --ab-tests` with `--as-of 2027-07-30T22:00:00+08:00` (about 21 sessions after the last signal day, so its h20
labels are published; R-F5 refuses the read until the panel reaches it; dry-run first), commit the
registration **before 2026-11-16 09:30 Beijing (2026-11-15 20:30 Toronto)**. The read itself waits for a
panel that holds 2026-11-16..2027-06-30 plus h20 labels; it is the user's go.
