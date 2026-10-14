# T9: how often would the paper book hold a name when it is delisted?

Status: exploratory, in-sample, research window only, read-only — 2026-10-14. **Not evidence.**
Issue: `V2-P7-009` (the settlement's delisting rule, decision D13 in `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` section 9; plan `docs/superpowers/plans/2026-10-14-p7-step0-adapters-and-ledger.md` Part C, item (11) and the D13 bullet)
Scripts (outside the repository, under `~/openalpha-research/diagnostics/p7/t9/`):
`t9_events.py` (sha256 `7d3b3648…b8afe`, 237 lines), `t9_book_exposure.py` (sha256 `2079b8fb…81a6d4`, 318 lines), `t9_summarise.py` (sha256 `7275d8d0…d7b495`, 58 lines).
Reused, unmodified: `~/openalpha-research/diagnostics/p7/t3_st_newlist.py` (sha256 `6c845517…b80b14`; its loader block, source lines 1–124, is executed verbatim) and `~/openalpha-research/diagnostics/p7/main_board_remeasure.py` (sha256 `7f9dd54e…1f896f`, the T8 script; run with `SECTIONS=none` to build its states, views and draw).
Upstream: `T3-st-and-new-listings.md` (name corpus, registry, ST rules), `T8-main-board-remeasure.md` (universe, harness, expected numbers), `T5-paper-step-builder.md` (halts have no daily row).

> **Use boundary.** Every number here comes from the research window (registry delisting dates 2017-01-01 … 2021-10-31; simulated signal days 2017-01-03 … 2021-09-07), after the fact, not pre-registered. It is **in-sample and exploratory, not evidence**, and nothing in it upgrades any rule from "candidate". The path classes are a heuristic built from name, price and halt signals; the panel has no field that says why a code was delisted. No 2022+ partition was opened.

---

## 0. Conclusions

1. **48 main-board codes were delisted inside the window** (registry `stock_basic` delisting rows; 56 on all boards, the other 8 are ChiNext `300.SZ`): 4 / 5 / 10 / 10 / 19 in 2017 / 2018 / 2019 / 2020 / 2021 (Jan–Oct), 9.94 a year. By the heuristic of section 3: **35 delisting-consolidation path (a), 8 halted-then-gone with an ordinary name and a non-distressed close (b), 5 other (c)** — all five (c) are `*ST` names delisted without a recorded consolidation name. Class (b) is 8 / 8 / 7 / 6 events at a 1 / 2 / 3 / 5 CNY distress threshold (§2.2).
2. **Path (a) does not reach the write-off under the kernel as built.** Every class-(a) and class-(c) event carried a warning name long before its last bar, and each had at least 9 (a) / 46 (c) bars after the first warning whose open was above the down limit; the kernel's forced exit on special treatment sells on the first of them. In the simulation the book sold a later-delisted name 44 times in 2,852 book-years and was **never** left holding a class-(a) or (c) name at its delisting. What path (a) costs is the crash before that sale (mean period return to the sale −31.0%), and D13 does not touch it.
3. **Path (b) is where the write-off bites, and it is rare.** Re-running T8's sticky N=10 book (main board, ST/new-listing filters on, 600 seeds) with causal eligibility and frozen halted holdings, the book reached a registry delisting date still holding the name **20 times in 2,852 book-years = 0.0070 per book-year**, all class (b) at the 2 CNY threshold (18 (b) + 2 (c) at 3 CNY), driven by three targets (`600068.SH` 14, `600270.SH` 4, `600317.SH` 2). The simple upper bound for class (b) is 0.0119 per book-year (1.66 events/yr × 10 / 1,397 survivors). **Candidate range: 0.006–0.012 write-offs per book-year, i.e. one every 80–170 book-years.**
4. **In capital terms** each such write-off is about 10% of the book's equity (10.0–10.4% in the simulation; 35,000–36,255 CNY at 350,000). Expected: **0.06–0.12pp of equity a year, about 210–420 CNY a year at 350,000 / 10 names**; realised: nothing in 98.8–99.7% of years and about 35,000 CNY in the year it happens.
5. **Recovering class (b) at its last close instead of zero** raises the book's equity by +0.07pp/yr on average (control arm +0.06pp/yr). It moves the pre-registered book-minus-control comparison by **−0.01pp/yr on average** (both arms are about equally exposed in this sample), but in the 4.2% of seeds where one arm is hit it moves the 4.75-year comparison by ±2.1pp/yr, i.e. ±10pp of cumulative equity. **Plainly: in expectation the write-off's conservatism does not move the comparison; in the rare realised case it moves it by more than the in-sample effect being compared (+2.8pp/yr) and by about 2.4 annual standard deviations of that comparison.**
6. **Side finding — T8's harness has a forward-bar look-ahead worth about 0.9pp/yr.** T8 (and T1) keep a name only if it has a bar on d1 **and** d2, and drop a holding that is halted on a signal day at no price. That removes the halt-then-delist path entirely (with T8's views this script produced zero write-offs) and also the halt-then-crash path. With eligibility built only from information on or before the signal day, and halted holdings frozen as the kernel does, the book's raw return falls by **−0.91pp/yr (sample se 0.30)**: −0.18pp/yr from the 48 window delistings (of which the write-off itself is −0.07) and −0.73pp/yr from other names (§6). The expected numbers T8 handed to `V2-P7-006` (+6.06 … +6.33%) are optimistic by about this much in this harness.
7. **Decisions for the controller** are in §7: the D13 residual for the class-(b) signature, the re-measurement of the `V2-P7-006` expected numbers, and a hand spot-check of the ten events in §3.3.

---

## 1. Setup

### 1.1 Window and guards

- Event window: registry delisting dates in [2017-01-01, 2021-10-31]. Every table is read with `<= 2021-10-31` (the T3 loader's own bound); partitions opened: `trade_cal` 2013–2021, `daily` 2013–2021, `stk_limit` 2013–2021, `adj_factor` 2013–2021, `suspend_d` 2013–2021, `stock_basic` 1990–2021, `namechange` 2013–2021. In-memory DuckDB over parquet; no write, no network, no `.env`, no `PanelStore`.
- Simulation: T8's 59 signal days minus the last one (2021-10-14, whose forward leg ends 2021-11-12), so **58 periods, 2017-01-03 … 2021-09-07, last forward leg ending 2021-10-15**; the price matrix is cut at 2021-10-31. T8's own loader (its SQL, unmodified) reads its 2021 partitions of `daily`, `adj_factor` and `stk_limit` whole, Nov–Dec rows included, plus the four `processed` factor partitions 2017–2021 on the signal days; no number in this report uses a row dated after 2021-10-31.
- Imported from the repository: only `openalpha_cn.domain.name_history`, through the T3 / T8 loaders (the special-treatment grammar).

### 1.2 Definitions

| term | meaning |
|---|---|
| main board | SH `600/601/603/605`, SZ `000/001/002/003` (the whitelist) |
| delisting date D | the registry's `lifecycle_event = 'delisting'` date; the security is not listed on D (`delisted_on` is exclusive) |
| last bar | the last `daily` row with `close > 0` on or before 2021-10-31 |
| halted in 60 before the last bar | open SSE sessions without a bar (after listing) among the 60 sessions before the last bar; cross-checked against `suspend_d` rows of type `S` (identical for all eight class-(b) events) |
| listed sessions after the last bar | open sessions strictly between the last bar and D: the security is listed and has no bar — the kernel freezes such a holding |
| name state | T3's `name_state` (announced-by view): `st` = ST / `*ST` / `PT`; `delisting` = `RiskWarning.delisting_process` (the consolidation names such as `华泽退`, `退市海润`); a pending ST counts as a warning. Read on every open session of the 500 before D; "500 (cap)" means warned on every session read |
| first warning | the first of those sessions whose state is `st` or `delisting` or pending ST |
| sellable bar | a bar whose open is above the published down limit (+0.0051): a sell at the open could fill |

### 1.3 Classification (a heuristic)

- **(a) delisting-consolidation path**: a `delisting_process` name state occurred in the 500 sessions before D.
- **(b) halted-then-gone, ordinary name, not distressed**: no warning state at any point of that path, and a raw last close **≥ 2 CNY** (§2.2 shows 1 / 3 / 5 / 8 CNY and a drawdown rule).
- **(c) other**: (c1) an ST path without a recorded consolidation name; (c2) an ordinary name with a distressed close.

Why 2 CNY: every class-(a) last close is at most 1.70 CNY (median 0.26), and every ordinary-name event's last adjusted close is at least 0.649 of its 250-session high (class (a) at most 0.464), so the line falls in an empty gap on both scales. **All eight class-(b) events have no `namechange` row in 2013–2021**: their "ordinary name" is the corpus saying nothing, i.e. `UNKNOWN` treated as ordinary — the same rule the adapter applies.

---

## 2. The event table (step 1)

### 2.1 Per year (main board, 2 CNY threshold)

| year of D | events | (a) consolidation | (b) halted-then-gone | (c) other |
|---|---:|---:|---:|---:|
| 2017 | 4 | 1 | 3 | 0 |
| 2018 | 5 | 4 | 1 | 0 |
| 2019 | 10 | 8 | 1 | 1 |
| 2020 | 10 | 10 | 0 | 0 |
| 2021 (Jan–Oct) | 19 | 12 | 3 | 4 |
| **total (4.83 years)** | **48** | **35** | **8** | **5** |

Registry sanity: 146 delisting rows on or before 2021-10-31, one per code (no code has two delisting dates). By prefix inside the window: `600.SH` 24, `002.SZ` 12, `000.SZ` 11, `300.SZ` 8 (ChiNext, excluded), `601.SH` 1.

### 2.2 Sensitivity of class (b) to the distress threshold

| raw last close ≥ | 0 | 1 | 2 | 3 | 5 | 8 CNY |
|---|---:|---:|---:|---:|---:|---:|
| class (b) events | 8 | 8 | **8** | 7 | 6 | 6 |

At 3 CNY `600317.SH` (last close 2.60) moves to (c2); at 5 CNY `600005.SH` (3.71) follows. A drawdown rule (last adjusted close ≥ 0.5 × its 250-session high) gives the same eight as the 2 CNY line. No class-(a) assignment depends on the threshold; raising it only moves (b) events into (c2).

### 2.3 Profile by class (median [min, max])

| | (a) n=35 | (b) n=8 | (c) n=5 |
|---|---|---|---|
| raw last close, CNY | 0.26 [0.15, 1.70] | 9.43 [2.60, 57.39] | 1.39 [0.52, 7.69] |
| last adjusted close / 250-session high | 0.16 [0.05, 0.46] | 0.85 [0.65, 1.00] | 0.18 [0.10, 0.47] |
| return over the 60 sessions before the last bar | −72% [−94%, −24%] | +7% [−4%, +41%] | −56% [−58%, −19%] |
| halted sessions in the 60 before the last bar | 31 [11, 31] | 2 [1, 7] | 0 [0, 0] |
| listed sessions after the last bar | 0 [0, 4] | 12.5 [7, 55] | 213 [17, 269] |
| sessions under a warning name (of the 500 read) | 500 cap [30, 500 cap]; 20 of 35 warned throughout | 0 | 500 cap [215, 500 cap] |
| sessions under the consolidation name | 30 [30, 34] | 0 | 0 |
| sellable bars after the first warning | 148 [9, 452] | — | 174 [46, 457] |
| name state on the last bar | consolidation name 35/35 | no record 8/8 | `*ST` 5/5 |

Readings. (a) The typical shape is a long `*ST` period, a long halt (31 of the 60 sessions before the last bar), then 30 trading sessions under the consolidation name, then delisting 0–4 sessions after the last bar. Two of the 35 (`000979.SZ` 中弘退, `002509.SZ` 天茂退) had no ST name before the consolidation name in the corpus; their first warning was the consolidation name itself, with 22 and 18 sellable bars after it. (b) Trades normally to the last bar (up to +41% over 60 sessions, within 0.65–1.00 of its one-year high), then 7–55 listed sessions with no bar, then delisting: the signature the brief describes for an absorption. (c) Trades under `*ST` to the last bar, then a long halt without consolidation trading (3 of 5 at 213–269 sessions).

The full 48-row table is Appendix A.

---

## 3. Path classification (step 2)

### 3.1 Class (a): 35 events

All 35 trade their last bar under a consolidation name. For the book the relevant number is the last row of §2.3: a holder who is told "special treatment" on the first warning day has at least 9 and typically about 150 sessions on which a sell at the open could fill before the last bar. The kernel's forced exit (`EXIT_ELIGIBILITY_LOST`, a sell that never expires) takes the first one.

### 3.2 Class (c): 5 events, all (c1)

`600247.SH` `*ST成城`, `600485.SH` `*ST信威`, `600677.SH` `*ST航通`, `600680.SH` `*ST上普`, `600978.SH` `*ST宜生`. All carried an ST name for 215–500+ sessions before D, with 46–457 sellable bars after the first warning. The ST filter keeps the book from buying them, and the forced exit sells one that turns ST while held. No (c2) event exists at the 2 CNY threshold.

### 3.3 Class (b): the eight events, plus two borderline ones, for a hand spot-check

The panel cannot say why these codes were delisted. The last column is the author's **unverified recollection, not panel data**, offered only to speed up the check: each looks like an absorption by share exchange into a listed acquirer. The brief asked for ten class-(b) events; the window holds eight, so the two (c1) events with the highest last close are added as borderline cases.

| # | code | delisted | last bar | last close | listed sessions after last bar | halted in 60 before | class | to check (unverified recollection) |
|---:|---|---|---|---:|---:|---:|---|---|
| 1 | `000748.SZ` | 2017-01-18 | 2016-10-31 | 20.31 | 55 | 5 | b | absorbed by 中国长城 (`000066.SZ`) |
| 2 | `600005.SH` | 2017-02-14 | 2017-01-23 | 3.71 | 10 | 5 | b | absorbed by 宝钢股份 (`600019.SH`) |
| 3 | `000916.SZ` | 2017-12-25 | 2017-12-04 | 8.82 | 14 | 7 | b | absorbed by 招商公路 (`001965.SZ`) |
| 4 | `600270.SH` | 2018-12-28 | 2018-12-12 | 20.99 | 11 | 3 | b | absorbed by 中国外运 (`601598.SH`) |
| 5 | `000418.SZ` | 2019-06-21 | 2019-05-07 | 57.39 | 31 | 1 | b | absorbed by 美的集团 (`000333.SZ`) |
| 6 | `600317.SH` | 2021-01-29 | 2021-01-14 | 2.60 | 10 | 1 | b (c2 at ≥ 3 CNY) | absorbed by 大连港 (`601880.SH`) |
| 7 | `600068.SH` | 2021-09-13 | 2021-09-01 | 9.93 | 7 | 1 | b | absorbed by 中国能建 (`601868.SH`) |
| 8 | `600723.SH` | 2021-10-22 | 2021-09-15 | 8.93 | 19 | 1 | b | absorbed by 王府井 (`600859.SH`) |
| 9 | `600680.SH` | 2019-05-23 | 2018-04-27 | 7.69 | 258 | 0 | c1 (`*ST上普`) | borderline: high close, ST path, no consolidation |
| 10 | `600677.SH` | 2021-03-18 | 2020-04-29 | 3.01 | 213 | 0 | c1 (`*ST航通`) | borderline: close ≥ 3 CNY, ST path, no consolidation |

If the recollection holds, **the window contains no cash tender offer and no voluntary delisting with a cash exit among the class-(b) events**; all eight are share exchanges, where a real holder receives the acquirer's shares (and, in some deals, a cash option). The signals above cannot tell those three cases apart.

---

## 4. Exposure of the book (step 3)

### 4.1 (i) The simple upper bound

events per year × 10 / the typical eligible main-board set (the causal views of §4.2 average 1,397 survivors and 1,746 investable names; T8's report has 1,390 / 1,737, which gives the same numbers to three decimals):

| class | events / yr | × 10 / survivors | × 10 / investable |
|---|---:|---:|---:|
| all | 9.94 | 0.0712 | 0.0569 |
| (a) | 7.25 | 0.0519 | 0.0415 |
| (b) | 1.66 | **0.0119** | 0.0095 |
| (c) | 1.04 | 0.0074 | 0.0059 |

Per book-year. This bound assumes a name is as likely to be held at its last bar as any eligible name. For (a) and (c) it ignores the ST filter and the forced exit, so it is far above what the kernel can incur (§4.2). For (b) it is the right shape: the targets are ordinary, liquid names, mostly eligible up to their last bar (§4.2, last table).

### 4.2 (ii) Re-running T8's simulation with delistings handled

Feasible and cheap: about 15 s of simulation per arm at 600 seeds (about 80 s for the whole script including T8's loader). `t9_book_exposure.py` runs T8's script with `SECTIONS=none` and reuses its per-signal-day states, `make_view` and the session-salted draw (`sha256(f"{seed}|{session}|{code}")`), with these changes:

| T8 harness | this harness |
|---|---|
| a name needs a bar on d1 **and** d2 to be eligible (a look-ahead) | eligibility uses only information on or before the signal day d (T8's `make_view` with the forward-bar requirement switched off) |
| a holding with no bar on d leaves the book, at no price | it is **frozen** (kept), as the kernel's `FREEZE_COMPATIBLE_REASONS` do |
| an exit is sold at the d1 close | it fills at the first bar on or after d1 (for a later-delisted code: the first sellable bar); until then it stays in the book |
| ST onset is seen at the next 20-session signal day | for a later-delisted code, the first warning triggers the daily kernel's forced exit at the first sellable bar after it |
| — | a name still held when its registry delisting date arrives is **written off** (value 0, D13); a parallel return series recovers it at its last close instead; the holdings are identical in both series |

Arms: **book** = T8's main board, filters on, liquidity population A, N=10, entry floor and survivor rule unchanged; **control** = the same holding policy and draw with no survivor filter (held while investable; entrants = all investable names) — this script's reading of `V2-P7-002`. 600 seeds × 58 periods = 4.75 book-years per seed, **2,852 book-years per arm**.

| | book | control |
|---|---:|---:|
| net excess vs investable equal weight, write-off / recovery (%/yr; MC se) | +5.89 / +5.96 (0.21) | +3.12 / +3.18 (0.24) |
| held at the registry delisting date (write-offs) | **20** | **17** |
| … per book-year | **0.0070** | **0.0060** |
| by class at 2 CNY: (a) / (b) / (c) | 0 / 20 / 0 | 0 / 17 / 0 |
| by class at 3 or 5 CNY | 0 / 18 / 2 | 0 / 17 / 0 |
| by code | `600068.SH` 14, `600270.SH` 4, `600317.SH` 2 | `600068.SH` 15, `600270.SH` 2 |
| seeds with one write-off / with two or more | 20 / 0 | 17 / 0 |
| value written off per event, share of book equity at the period start | 10.0% [10.0, 10.4] | 10.0% [10.0, 10.0] |
| later-delisted names sold before their delisting (sales) | 44, all (a) | 83: 70 (a), 13 (c) |
| … mean period return up to the sale | −31.0% (min −44.6%) | (a) −25.6%, (c) −43.3% |
| held at the window's end with no bar in the last 60 sessions and no registry delisting | none | none |

So the kernel's exposure to the write-off is entirely the class-(b) signature, and the book's propensity to be holding an absorbed target (0.0070) is about the control's (0.0060): the survivor composite does not, in this sample, steer the book towards absorbed targets. The count is driven by few events: `600068.SH` was an entrant on each of the five signal days before its last bar.

Where each class-(b) target sat on the last five signal days on or before its last bar (causal view; E entrant, I investable but in the worst two composite deciles, - not investable; "T8 drops" = absent from T8's forward-bar investable set that day):

| code | signal days (oldest → newest) |
|---|---|
| `000418.SZ` | 2018-12-20 -, 2019-01-21 -, 2019-02-25 E, 2019-03-25 E, 2019-04-23 E (T8 drops) |
| `000748.SZ` | last bar 2016-10-31, before the first signal day: never holdable |
| `000916.SZ` | 2017-08-01 -, 2017-08-29 -, 2017-09-26 I, 2017-10-31 I, 2017-11-28 I (T8 drops) |
| `600005.SH` | 2017-01-03 - (the only signal day before its last bar) |
| `600068.SH` | 2021-04-14 E, 2021-05-17 E, 2021-06-15 E, 2021-07-13 E (T8 drops), 2021-08-10 E (T8 drops) |
| `600270.SH` | 2018-07-25 E, 2018-08-22 E, 2018-09-19 E, 2018-10-25 E, 2018-11-22 E (T8 drops) |
| `600317.SH` | 2020-09-11 -, 2020-10-19 -, 2020-11-16 -, 2020-12-14 E, 2021-01-12 E (T8 drops) |
| `600723.SH` | 2021-05-17 -, 2021-06-15 E, 2021-07-13 -, 2021-08-10 E, 2021-09-07 E (T8 drops) |

Every class-(b) target that was investable on its last signal day was **dropped by T8's forward-bar rule on that day** — the look-ahead removes exactly the path this task is about. With T8's views this harness produced zero write-offs.

### 4.3 (iii) Expected total-loss events and their value, by class (350,000 CNY, 10 names, 35,000 per name)

| class | write-offs per book-year (candidate range) | expected value per year | realised |
|---|---|---|---|
| (a) | ~0 (simulation 0 in 2,852 book-years; a write-off would need the forced sale to fail on each of ≥ 9 sellable bars) | ~0 | — |
| (b) | **0.006 – 0.012** (simulation 0.0063–0.0070 depending on the threshold; upper bound 0.0119) | **0.06–0.12pp of equity, about 210–420 CNY** | 0 in 98.8–99.7% of years; about 35,000 CNY (≈10% of equity) otherwise |
| (c) | ~0 (simulation 0 at 2 CNY; the 2 at 3 CNY are `600317.SH`, reclassified; ≥ 46 sellable bars after the first warning) | ~0 | — |

The probability of at least one class-(b) write-off in a year: 0.28% in the simulated book (0.14% in the control), 1 − e^(−0.0119) = 1.2% at the upper bound.

---

## 5. Sensitivity of the conclusion (step 4)

**Recovery at the last close instead of zero, for the class-(b) signature:**

| | book | control |
|---|---:|---:|
| mean equity difference, recovery minus write-off | **+0.07pp/yr** (upper bound +0.12) | +0.06pp/yr |
| share of seeds with any difference over 4.75 years | 3.3% | 2.8% |
| one-year windows (12 periods) with any difference | 0.28% | 0.14% |
| size of the difference when it occurs | 10.0–10.4pp of equity | 10.0pp |
| in CNY at 350,000 | ≈ 245 (up to 420) a year on average; 35,000–36,255 in the year it occurs | ≈ 210 a year; 35,000 |

**The pre-registered comparison (book minus control, same seeds and periods):** +2.769pp/yr with the write-off (MC se 0.258), +2.780pp/yr with recovery: the rule moves it by **−0.011pp/yr on average**. Per seed it moves it by −2.18 … +2.10pp/yr over the 4.75 years, and at all in 4.2% of seeds. The comparison's own time-series standard error over the 58 periods is 1.94pp/yr, which is about 4.2pp for a single year.

**Plain statement.** The write-off's conservatism **cannot move the comparison in expectation**: both arms hold absorbed targets at about the same rate, so the expected bias is −0.01pp/yr against an in-sample difference of +2.8pp/yr. **It can move a realised comparison decisively**: a single write-off takes about 10pp from one arm. In a one-to-two-year forward comparison that is larger than the effect being tested and about 2.4 annual standard deviations of the difference. The chance that either arm is hit in a given year is about 0.4% (simulation) to 2.4% (upper bound, both arms).

Two qualifications. "Recovery at the last close" is itself only an approximation: a share-exchange holder receives the acquirer's shares at a fixed ratio, priced when the exchange completes, 7–55 sessions after the target's last bar, or takes a cash option where one exists. This report does not measure that value; the panel has no acquirer or ratio field. And the paper book's settlement would not see the acquirer's shares at all: the D13 write-off is a zero where a real holder's value is roughly the last close moved by the acquirer's price over the gap.

---

## 6. Side finding: T8's forward-bar requirement is worth about 0.9pp/yr in this harness

Book arm, write-off series, same 600 seeds and 58 periods:

| step | net excess %/yr | raw book return %/yr | benchmark %/yr |
|---|---:|---:|---:|
| T8's own `simulate` on T8's views (reference) | +6.89 (MC se 0.20; sample se 2.87) | — | — |
| this harness on T8's forward-bar views | +6.80 | +7.60 | +0.61 |
| this harness on causal views | **+5.89** | **+6.69** | +0.62 |
| … with the 48 window delistings made ineligible | +6.07 | — | — |

The paired difference, causal minus forward-bar views, is **−0.91pp/yr** (net; sample se 0.28; raw −0.91, se 0.30). The benchmark does not move. Attribution: the window delistings account for **−0.18pp/yr** (se 0.11; the write-off itself −0.07, the rest the class-(a) crashes before the forced sale) and **every other name the forward-bar rule drops** — names that halt during or at the end of a period and trade again later — for **−0.73pp/yr** (se 0.27). T8 §4.7's `FILL=1` sensitivity found no effect because it kept two of the three conventions: a name still needed a bar somewhere in [d1, d2], and a holding halted on a signal day still left the book at no price. This is in-sample and the 20-session cadence can move it either way (§8), but it is about 3 standard errors and the same order as the differences `V2-P7-006` will be asked to register.

---

## 7. What the controller must decide

1. **The D13 residual for the class-(b) signature (a user decision; D13 was the user's).** The data say the expected cost of the write-off is small (0.06–0.12pp/yr) and that it does not tilt book-minus-control, but that a realised event is a 10pp jump in one arm. Options: (A) keep D13 and pre-register in `V2-P7-006` that every write-off is reported next to the comparison with a recovery-at-last-close line, and that the signature (last bar under an ordinary or unknown name, last close ≥ 2 CNY, no consolidation name) is flagged as "probable absorption"; (B) for that signature only, settle at the last close and flag it; this is not a fill, so it needs the same "labelled transfer, not a trade" treatment D13 already requires; (C) model the exchange, which needs an acquirer-and-ratio source the panel does not have. Paths (a) and (c) need no change: the forced exit already handles them.
2. **Re-measure the `V2-P7-006` expected numbers without the forward-bar look-ahead** (§6): in this harness the book's in-sample net excess is +5.89%/yr on 58 periods against +6.89% for T8's harness on the same periods.
3. **Spot-check the ten events of §3.3 by hand**, in particular whether any is a cash tender or a voluntary delisting with a cash exit; the recovery value differs by case.
4. For the record, no action implied: four main-board codes stopped trading inside the window without a registry delisting row by 2021-10-31 — `000022.SZ` (last bar 2018-12-20) and `000043.SZ` (2019-12-13), the code migrations of T3 §3.3 with no listing row, and `000670.SZ` (2020-03-19) and `002260.SZ` (2019-04-26), both `*ST` and halted. None was held at the window's end in either arm, and the first two are ineligible under `NO_LISTING_ROW`. A held code that the registry does not know would, under the adapter's `held_status`, refuse the evening (`not_in_registry`) rather than be written off.

---

## 8. What this does not show

- **Why a code was delisted.** The classes are signatures in name, price and halt data. Class (b) is "ordinary or unknown name, non-distressed close, halted then gone", not "merger"; cash tenders, voluntary delistings and absorptions cannot be told apart here, and the recollection column of §3.3 is not evidence.
- **"Ordinary" for class (b) means "no record"**: none of the eight has a `namechange` row in 2013–2021. A rename before 2013 is not read (T3's left-censoring).
- **The future rate.** Eight class-(b) events in 4.83 years, one per year on average, three of them in 2021; the simulated count is driven by three targets and one of them supplies 14 of 20 hits. A wave of state-owned consolidations would scale the rate; this window cannot say whether one is coming.
- **The daily kernel exactly.** The harness keeps T8's 20-session cadence: entries and survivor-lost exits only on signal days; no 2-a-day cap, cool-down, T+1, lots, limit-up buys or cash; equal weight each period; an entrant needs a bar on d1 (a one-session look-ahead on entry only). Only the forced exit of later-delisted codes and the fill of decided sales run at daily resolution. "Sellable" is an open above the down limit, not a volume check; a 35,000 CNY order is small.
- **The real recovery value** of an absorbed holding (acquirer shares at the exchange ratio, priced at completion; any cash option). "Recovery at the last close" is a stand-in.
- **The benchmark is not the point.** Net excess is against the arm's own investable equal weight, as in T8; the control arm's +3.1%/yr against that benchmark is not explained here and is not a claim.
- **Delistings after 2021-10-31** of names halted inside the window are not counted; no simulated book held such a name at the window's end.
- **The look-ahead finding of §6** is one harness on one window; its split into delisting and halt parts depends on the cadence (a resumed name is held to the next signal day here, while the daily kernel would sell sooner if it no longer qualifies).
- Everything is in-sample; the window has been used by T1, T3, T8 and this report. None of it is evidence and none of it selects a rule.

---

## 9. Reproduction

Read-only; in-memory DuckDB over parquet; no network; no `.env`. The interpreter is the repository venv. Because the checkout path contains a space, a symlink named `.venv` at the worktree root points to it (as T8 does; removed afterwards), and `PYTHONPATH` points at the worktree's `src` (its `domain/name_history.py` is byte-identical to the main checkout's). Run from the worktree root:

```bash
# step 1-2: the event table (about 2 s; writes ~/openalpha-research/diagnostics/p7/t9/events.tsv)   -> sections 2, 3, Appendix A
PYTHONHASHSEED=0 PYTHONPATH="$PWD/src" .venv/bin/python ~/openalpha-research/diagnostics/p7/t9/t9_events.py

# per-year / per-class summaries at several thresholds (stdlib only)                                 -> sections 2.1-2.3
.venv/bin/python ~/openalpha-research/diagnostics/p7/t9/t9_summarise.py

# steps 3-4: the book and the control, 600 seeds (about 80 s including T8's loader)                -> sections 4-6
PYTHONHASHSEED=0 PYTHONPATH="$PWD/src" T9_SEEDS=600 .venv/bin/python ~/openalpha-research/diagnostics/p7/t9/t9_book_exposure.py
```

`t9_book_exposure.py` reads `events.tsv`, so the event script runs first. The event script's output is byte-identical across two runs, and the exposure script's output (60 seeds) is identical under `PYTHONHASHSEED=0` and `7` apart from timing lines. The outputs used here are kept beside the scripts: `events.out`, `events.tsv` (sha256 `ff34118b…05cf9`) and `exposure_s600.out`.

---

## Appendix A: the 48 main-board delistings in the window

Columns: last close in CNY (raw); close / 250-session high uses adjusted closes; warning sessions are counted over the 500 open sessions before D ("500 (cap)" = warned on all of them); sellable bars are counted after the first warning.

| # | code | delisted | last bar | last close | close / 250-session high | name at last bar | first warning | warning sessions (of 500) | consolidation-name sessions | halted in 60 before last bar | listed sessions after last bar | sellable bars after warning | class (2 CNY) |
|---:|---|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | `000748.SZ` | 2017-01-18 | 2016-10-31 | 20.31 | 0.65 | (no record) | — | 0 | 0 | 5 | 55 | — | b |
| 2 | `600005.SH` | 2017-02-14 | 2017-01-23 | 3.71 | 0.98 | (no record) | — | 0 | 0 | 5 | 10 | — | b |
| 3 | `000033.SZ` | 2017-07-07 | 2017-07-06 | 1.70 | 0.24 | 新都退 | 2015-06-18 | 500 (cap) | 30 | 31 | 0 | 12 | a |
| 4 | `000916.SZ` | 2017-12-25 | 2017-12-04 | 8.82 | 0.80 | (no record) | — | 0 | 0 | 7 | 14 | — | b |
| 5 | `600432.SH` | 2018-07-13 | 2018-07-11 | 1.38 | 0.23 | 退市吉恩 | 2016-06-27 | 500 (cap) | 31 | 31 | 1 | 148 | a |
| 6 | `600806.SH` | 2018-07-13 | 2018-07-11 | 1.47 | 0.25 | 退市昆机 | 2016-06-27 | 500 (cap) | 31 | 31 | 1 | 207 | a |
| 7 | `000511.SZ` | 2018-07-18 | 2018-07-17 | 0.61 | 0.13 | 烯碳退 | 2016-06-30 | 500 (cap) | 30 | 31 | 0 | 110 | a |
| 8 | `000979.SZ` | 2018-12-28 | 2018-12-27 | 0.22 | 0.12 | 中弘退 | 2018-11-16 | 30 | 30 | 20 | 0 | 22 | a |
| 9 | `600270.SH` | 2018-12-28 | 2018-12-12 | 20.99 | 0.93 | (no record) | — | 0 | 0 | 3 | 11 | — | b |
| 10 | `600680.SH` | 2019-05-23 | 2018-04-27 | 7.69 | 0.47 | *ST上普 | 2017-05-04 | 500 (cap) | 0 | 0 | 258 | 230 | c |
| 11 | `000418.SZ` | 2019-06-21 | 2019-05-07 | 57.39 | 0.80 | (no record) | — | 0 | 0 | 1 | 31 | — | b |
| 12 | `000693.SZ` | 2019-07-09 | 2019-07-08 | 0.37 | 0.12 | 华泽退 | 2017-06-21 | 500 (cap) | 30 | 31 | 0 | 9 | a |
| 13 | `002070.SZ` | 2019-07-09 | 2019-07-08 | 0.71 | 0.20 | 众和退 | 2017-06-21 | 500 (cap) | 30 | 31 | 0 | 83 | a |
| 14 | `600401.SH` | 2019-07-12 | 2019-07-08 | 0.15 | 0.19 | 退市海润 | 2017-06-26 | 500 (cap) | 33 | 31 | 3 | 153 | a |
| 15 | `002477.SZ` | 2019-10-16 | 2019-10-15 | 0.17 | 0.05 | 雏鹰退 | 2019-04-25 | 114 | 30 | 17 | 0 | 60 | a |
| 16 | `002018.SZ` | 2019-11-01 | 2019-10-31 | 0.26 | 0.18 | 华信退 | 2018-04-27 | 368 | 30 | 18 | 0 | 307 | a |
| 17 | `002680.SZ` | 2019-11-27 | 2019-11-26 | 0.77 | 0.20 | 长生退 | 2018-07-25 | 326 | 30 | 31 | 0 | 34 | a |
| 18 | `002143.SZ` | 2019-11-29 | 2019-11-28 | 0.25 | 0.05 | 印纪退 | 2019-04-04 | 160 | 30 | 20 | 0 | 110 | a |
| 19 | `600747.SH` | 2019-12-12 | 2019-12-06 | 0.26 | 0.14 | 退市大控 | 2017-11-23 | 500 (cap) | 33 | 21 | 3 | 449 | a |
| 20 | `000018.SZ` | 2020-01-07 | 2020-01-06 | 0.27 | 0.09 | 神城A退 | 2019-04-30 | 169 | 30 | 17 | 0 | 123 | a |
| 21 | `600240.SH` | 2020-02-05 | 2020-01-23 | 0.41 | 0.10 | 退市华业 | 2019-04-29 | 185 | 32 | 21 | 2 | 133 | a |
| 22 | `600074.SH` | 2020-06-02 | 2020-05-26 | 0.17 | 0.18 | 退市保千 | 2018-05-14 | 500 (cap) | 34 | 31 | 4 | 242 | a |
| 23 | `601558.SH` | 2020-07-02 | 2020-06-23 | 0.25 | 0.19 | 退市锐电 | 2018-06-11 | 500 (cap) | 34 | 18 | 4 | 452 | a |
| 24 | `002604.SZ` | 2020-07-15 | 2020-07-14 | 0.25 | 0.14 | 龙力退 | 2018-06-25 | 500 (cap) | 30 | 31 | 0 | 213 | a |
| 25 | `002509.SZ` | 2020-07-20 | 2020-07-17 | 0.15 | 0.08 | 天茂退 | 2020-06-04 | 30 | 30 | 17 | 0 | 18 | a |
| 26 | `002220.SZ` | 2020-08-11 | 2020-08-10 | 0.26 | 0.16 | 天宝退 | 2019-05-27 | 296 | 30 | 18 | 0 | 251 | a |
| 27 | `600175.SH` | 2020-08-14 | 2020-08-07 | 0.21 | 0.09 | 退市美都 | 2020-04-30 | 71 | 34 | 20 | 4 | 25 | a |
| 28 | `600069.SH` | 2020-08-27 | 2020-08-20 | 0.28 | 0.09 | 退市银鸽 | 2020-04-29 | 81 | 34 | 20 | 4 | 28 | a |
| 29 | `000939.SZ` | 2020-12-17 | 2020-12-16 | 0.15 | 0.16 | 凯迪退 | 2018-11-27 | 500 (cap) | 30 | 31 | 0 | 110 | a |
| 30 | `600317.SH` | 2021-01-29 | 2021-01-14 | 2.60 | 0.90 | (no record) | — | 0 | 0 | 1 | 10 | — | b |
| 31 | `600687.SH` | 2021-03-03 | 2021-02-24 | 0.30 | 0.21 | 退市刚泰 | 2019-04-11 | 459 | 34 | 11 | 4 | 403 | a |
| 32 | `600086.SH` | 2021-03-17 | 2021-03-10 | 0.16 | 0.07 | 退市金钰 | 2020-06-23 | 177 | 34 | 20 | 4 | 129 | a |
| 33 | `600677.SH` | 2021-03-18 | 2020-04-29 | 3.01 | 0.15 | *ST航通 | 2020-01-21 | 278 | 0 | 0 | 213 | 46 | c |
| 34 | `600247.SH` | 2021-03-22 | 2021-02-24 | 0.65 | 0.17 | *ST成城 | 2019-03-01 | 500 (cap) | 0 | 0 | 17 | 457 | c |
| 35 | `600978.SH` | 2021-03-22 | 2021-02-22 | 0.52 | 0.19 | *ST宜生 | 2020-04-30 | 215 | 0 | 0 | 19 | 174 | c |
| 36 | `000662.SZ` | 2021-04-12 | 2021-04-09 | 0.25 | 0.05 | 天夏退 | 2020-06-30 | 191 | 30 | 26 | 0 | 136 | a |
| 37 | `600701.SH` | 2021-04-30 | 2021-04-26 | 0.45 | 0.37 | 退市工新 | 2019-04-11 | 500 (cap) | 33 | 31 | 3 | 263 | a |
| 38 | `002071.SZ` | 2021-05-07 | 2021-05-06 | 0.31 | 0.09 | 长城退 | 2020-06-19 | 212 | 30 | 31 | 0 | 152 | a |
| 39 | `600891.SH` | 2021-05-12 | 2021-04-30 | 0.38 | 0.35 | 退市秋林 | 2019-04-18 | 500 (cap) | 34 | 31 | 4 | 215 | a |
| 40 | `002450.SZ` | 2021-05-31 | 2021-05-28 | 0.20 | 0.06 | 康得退 | 2019-05-10 | 500 (cap) | 30 | 31 | 0 | 37 | a |
| 41 | `600485.SH` | 2021-06-01 | 2020-04-20 | 1.39 | 0.10 | *ST信威 | 2019-05-13 | 500 (cap) | 0 | 0 | 269 | 131 | c |
| 42 | `002711.SZ` | 2021-07-15 | 2021-07-14 | 0.32 | 0.46 | 欧浦退 | 2019-06-26 | 500 (cap) | 30 | 31 | 0 | 212 | a |
| 43 | `600614.SH` | 2021-07-21 | 2021-07-14 | 0.26 | 0.39 | 退市鹏起 | 2019-07-02 | 500 (cap) | 34 | 31 | 4 | 233 | a |
| 44 | `600634.SH` | 2021-07-21 | 2021-07-14 | 0.28 | 0.21 | 退市富控 | 2019-07-02 | 500 (cap) | 34 | 31 | 4 | 337 | a |
| 45 | `000760.SZ` | 2021-07-23 | 2021-07-22 | 0.26 | 0.20 | 斯太退 | 2019-07-04 | 500 (cap) | 30 | 31 | 0 | 226 | a |
| 46 | `002359.SZ` | 2021-07-23 | 2021-07-22 | 0.35 | 0.32 | 北讯退 | 2019-07-04 | 500 (cap) | 30 | 31 | 0 | 240 | a |
| 47 | `600068.SH` | 2021-09-13 | 2021-09-01 | 9.93 | 1.00 | (no record) | — | 0 | 0 | 1 | 7 | — | b |
| 48 | `600723.SH` | 2021-10-22 | 2021-09-15 | 8.93 | 0.72 | (no record) | — | 0 | 0 | 1 | 19 | — | b |
