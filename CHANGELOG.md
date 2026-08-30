# Changelog

All notable changes follow Keep a Changelog and Semantic Versioning.

## [Unreleased]

### Fixed

- **An e2e test titled "byte-for-byte restoration, checked against the catalog's own content
  hash" never touched a content hash, and its docstring contradicted the module it tests**
  (`V2-P5-068`). Found by re-running the 60 paid e2e tests against current HEAD — 59 passed, 1
  legitimate skip, 38:59, reusing the stored panel so no rebuild was paid for.
  - `test_the_defect_free_panel_still_answers_after_a_partition_was_moved_and_put_back` asserted
    `path.exists()` and `count(*) > 0`. A partition missing a million rows satisfies both.
    Demonstrated: `stk_limit/2026` rewritten one row short — 1,190,637 against the catalog's
    1,190,638, magic and format intact — and the test passed unchanged.
  - Its second claim, that `content_hash` "is recomputed from the file on every readiness
    assessment", is also false: `_content_hash` has exactly one call site, inside
    `write_partition`, over the rows in memory. `store.py` says the opposite in as many words —
    "nothing in the catalog changes, so no hash comparison can see it".
  - **No product defect.** The store declares exactly what it guarantees (Parquet magic at both
    ends, and the footer's row count reconciled against `panel_partitions`) and exactly what is
    out of reach (an in-place value edit, caught a plane up by `return_path_disagreement` and
    `close_disagreement`) — disclosed rather than argued. The test now asserts the guarantee that
    exists, through a new `catalogued_row_count` mirroring `catalogued_path`.
  - Written test-first with the order deliberately inverted, because a guard's RED cannot be
    "it passes today": the panel was corrupted first and the old test watched to pass — that is
    this gap's red — then the assertion written and watched to fail, then the panel restored
    byte-for-byte and watched to pass. Every Parquet partition is byte-identical to what it was
    before the run.

- **The one deliberately-open row now has a ratchet: the unverified part of the feature ledger
  can shrink and cannot grow** (`V2-P5-038`, step 1 of its plan). 85 of 185 rows carry
  `acceptance_kind="legacy-prose"`, and only `pytest` rows are tied to a *named test function*
  that `_validate_pytest_acceptance` verifies by AST — so a swap inside one directory leaves the
  per-directory counts unchanged. That row had weighed two closes and found both dearer than the
  defect: migrating 85 rows is a content decision each, and a 185-item path-set literal would go
  red on every legitimate ledger edit — the shape already paid for twice, in `V2-P5-053` (a
  version literal that blocked its own security fix) and `V2-P5-060` (a hand-kept list that went
  stale).
  - The third way is a ceiling. `summary.json` already records `legacy_acceptance_rows` and
    `--check` pins it byte-for-byte, but **a pin permits an increase as easily as a decrease** —
    update the artifact and it passes. `test_the_unvalidated_acceptance_rows_never_grow` does
    not: new work must name a test, and the debt can only be paid down, in the same commit that
    earns it.
  - **The floor is not zero, and the docstring says so.** `OA-IFACE-006`, `OA-IFACE-007` and
    `OA-OPS-002` name `web/src/App.test.tsx` and `web/e2e/golden-flow.spec.ts`, which no `pytest`
    node id can address; they need a fourth acceptance kind or they stay prose. The ceiling does
    not prejudge that.
  - One clarification the original row overstated: `build_feature_coverage._load` already checks
    every evidence path on **every** row whatever its acceptance kind, so **all 85** prose rows
    have their paths verified today, the three `web/` ones included — measured by pointing
    `OA-IFACE-006` at a nonexistent `App.test.NOPE.tsx` and watching `--check` raise. What is
    unchecked is narrower than "89 rows outside AST coverage": it is that their evidence names a
    file rather than a test.
  - **Three corrections an independent acceptance pass returned, all applied.** The coverage
    figure above was first written as 82 of 85 — that number counts how many prose rows name a
    `tests/*.py` path, which is not the same question. The commit message claimed the failure
    message names the offending row; it does not, and the test's own docstring says that is
    deliberate. And the guard was first written as `<=`, which loosens itself: pay ten rows down
    and the count is 75 against a ceiling of 85, so ten can be added back with nothing going red.
    A guard that weakens every time somebody does the right thing is the shape this release has
    spent its length removing, so it is now `==` — growth fails, and so does payment until the
    number is lowered in the same commit. What it still cannot catch is written into the
    docstring rather than left to be found: a commit that migrates one row and adds another nets
    zero, and the number itself can be raised by anyone willing to write the diff.

- **An ordinary read crashed if it overlapped a write: the panel catalog's concurrency design
  assumed a read-only and a read-write DuckDB connection can coexist in one process, and DuckDB
  refuses that** (`V2-P5-067`). This family had been reported as "needs a Windows machine to
  diagnose". That was wrong, and it was wrong because the complete error had not been read.
  Read: `Can't open a connection to same database file with a different configuration than
  existing connections`. DuckDB keeps one database instance per file per process; `store.py`
  opens the catalog `read_only=True` at seven sites and read-write at two.
  - **Reproduced on macOS against `duckdb.connect` directly**, so it is not a platform property.
    Windows only scheduled it reliably, which is how it surfaced. The new
    `test_a_read_running_while_a_write_holds_the_catalog_does_not_crash` failed **28 of 64
    operations** before the fix and passes 3 runs out of 3 after it.
  - The blast radius is wider than the test that found it: `query()` is read-only and
    `write_partition()` is read-write, so a read request landing during a panel build raised
    `ConnectionException` — in the API server, a read failing because a build was in flight.
    The module docstring's promise that "any number of concurrent readers … can run in parallel"
    is true among readers and silent about the case that breaks.
  - Fixed with a readers-writer lock rather than one lock around everything: serialising reads
    would have traded a crash for a regression
    (`test_profile_query_survives_eight_concurrent_threads_against_the_same_partition`). What
    DuckDB forbids is exactly reader-with-writer, and that is all this excludes. Both re-entrancy
    cases are answered in code rather than documented as hazards — a deadlock left in a storage
    layer as a comment is a deadlock — and waiting writers are counted so a stream of readers
    cannot starve a build.
  - Two test defects in the same family. The truncation fixture ran
    `COPY (SELECT … read_parquet(?)) TO ?` with **both parameters bound to the same file**;
    DuckDB writes to a temp path and moves it onto the target, which is the file still being
    scanned — POSIX renames over an open file, Windows answers `Access is denied`. Giving the
    two ends different paths then revealed the second defect: DuckDB binds the `TO ?`
    destination *before* `read_parquet(?)`, which the original could not show because both
    parameters were the same string. It is now one parameter per statement.

- **The only job that builds the shipped artifact was gated behind a matrix whose individual
  legs `needs` cannot name, and had therefore never run** (`V2-P5-066`). `container` is where the
  `Dockerfile`'s image is built, `deploy/compose.yml`'s stack is started and evidence is proved
  to survive a restart — the whole of this workflow's production verification. It declared
  `needs: [python, web, security]`, and `python` is a matrix over `ubuntu-latest` *and*
  `windows-latest`. `needs` waits on a job, not on a leg, so a red Windows leg skipped the
  container job outright — and it was `skipped` on all four dispatches this repository has made.
  Every "green" reported for this work, ubuntu 3.11 and 3.12 included, had therefore not touched
  the thing actually being shipped. The dependency was a cost heuristic — don't spend build
  minutes on obviously broken code — and what it was silently doing instead was withholding the
  only evidence that the artifact works. `web` and `security` still gate it, over the same tree.
  Splitting the matrix into two jobs was rejected: GitHub Actions has no YAML anchors, so it
  would mean copying ten steps, and this release already carries `V2-P5-065` on what copies
  cost.

- **Four modules held a byte-identical copy of the store-path substitution, and three of them
  said so in their docstrings and kept the copy anyway** (`V2-P5-065`). `V2-P5-064` added
  separator normalisation to `panel_view`'s implementation, and Windows went on emitting
  `this service's panel store\adj_factor\2026\data.parquet` — because the fix reached one
  caller in four. `factor_view`, `model_view` and `shortlist_view` each carried the same loop,
  each under a docstring naming `panel_view._without_store_path` as "the rule and its measured
  reason". Knowing you are a copy and saying so is not the same as not being one.
  - Found through Windows, but the defect is platform-independent: any change to this logic
    would have landed on a quarter of it.
  - Fixed the way this repository already fixes copied helpers: one implementation,
    `panel_view.without_store_path`, three one-line delegations, and an AST audit asserting that
    exactly one module in `src/` calls `x.replace(..., PANEL_STORE_PLACEHOLDER)` — proved able to
    fail by restoring the copy in `factor_view`.
  - The new cross-module name was caught immediately by the seam table
    (`test_panel_ingest_import_isolation`), which requires every module to declare each name it
    takes across the seam. Three rows updated.

- **The SPA fallback took a path's first segment by splitting on `/`, and `StaticFiles.get_path`
  hands it "OS specific path separators" — so on Windows the owner check was inoperative and
  every request was served the HTML shell** (`V2-P5-064`). With `V2-P5-059`/`060`/`061`/`063`
  landed, Windows went from 194 failed and 111 errors to **31 failed, 5566 passed** — and 26 of
  the 31 were in one file, from one line: `raw = path.split("/", 1)[0]`.
  - Its docstring correctly noted that `StaticFiles.get_path` had already normalised the path.
    Starlette's docstring for that method says what the normalisation *does*: it returns the
    path "with OS specific path separators", via
    `os.path.normpath(os.path.join(*route_path.split("/")))`. On Windows `GET /api/v1/nope`
    arrives as `api\v1\nope`, nothing splits, the whole string is compared against the owner
    sets and matches neither — so both of the owners this class derives so carefully, the API's
    route table and the build's own directories, were dead at once. That is the sentence
    `V2-P5-027` and `V2-P5-030` exist to prevent, arriving through the separator nobody checked:
    a missing `/assets/*.js` answering `200 text/html` to a `<script>` tag.
  - The separators are read from `os` rather than written as `"/\"`, so the code says "whatever
    this interpreter splits paths on" instead of naming the two platforms somebody thought of;
    on POSIX `os.altsep` is `None` and the behaviour is byte-for-byte what it was. The split is
    a function taking the separators as a parameter so the Windows reading is exercised from a
    POSIX machine — mutation-checked: `PATH_SEPARATORS` as `""` or `"\"` fails 28 tests, and
    splitting on `/` alone fails the Windows-separator test.
  - Three more from the same run. `_without_store_path` now spells what survives the placeholder
    with `/` on every platform: that string is `disclosable`, it crosses a process boundary, and
    `this service's panel store\adj_factor\2026\data.parquet` is neither a usable path nor an
    identifier two deployments would agree on. `Counter(str(Path(p).parent) ...)` had keyed a
    whole ledger by `tests\e2e`; `V2-P5-060`'s audit knew `relative_to` and not `parent`, and is
    widened — with `resolve`/`absolute` deliberately excluded, because they answer "where is this
    on *this* machine", which is the one question whose answer must carry the platform's
    separator. And `find_library("c")` returns `None` on Windows, so `ctypes.dlopen` is provoked
    through `msvcrt` there rather than subtracted out of the guard's coverage.

- **Three tests treated "this machine" as an invariant: a hardcoded POSIX `PATH`, a hardcoded
  exception class name, and a wall clock calibrated on one laptop** (`V2-P5-063`). All three were
  found by the first Windows run and none of them is a Windows problem.
  - `test_import_time_filesystem` *replaces* the child's environment so no developer's
    `OPENALPHA_*` can reach it, and spelled that as `{"PATH": "/usr/bin:/bin", ...}`. Windows'
    `python.exe` does not start without `SYSTEMROOT`, so the child exited 1 before importing
    anything — and `check=True` captured the child's stderr and discarded it, so the failure said
    only "returned non-zero exit status 1". Fixed by making the failure say why first, then
    giving the child the platform's bootstrap minimum; the isolation is kept by asserting the
    child carries exactly one `OPENALPHA_` variable.
  - `test_a_command_that_breaks_is_not_reported_as_an_unhealthy_panel` asserted
    `NotADirectoryError`, which is what POSIX raises for a file standing where a directory
    belongs. Windows raises `FileExistsError`. The test now provokes the same operation and reads
    the name off the running platform — and fails loudly if it stops raising at all, which a
    two-name `or` would have quietly turned into a test of nothing.
  - `test_whole_market_batch_completes` asserted `elapsed < 60.0`, under a comment saying it is a
    tripwire for an O(N²) regression rather than a benchmark. Sixty seconds is a property of the
    machine that wrote it: the same run is 14s here and **602s on the Windows runner**, so the
    tripwire fired on a slow machine — and on a fast enough machine it would equally have gone on
    *missing* a regression, which is the half nobody would notice. It now compares two sizes:
    quadratic growth over this span predicts ~123×, linear ~11×, and no clock speed moves the
    quotient. Measured at 10.4× locally, ceiling 30×, proved able to fire by lowering it to 5×.

- **`_pearson` summed with `sum` and not `math.fsum`, so the same cross section correlated
  differently on the two interpreters this repository supports — and produced different content
  addresses** (`V2-P5-062`). CPython 3.12 gave float `sum()` Neumaier compensation, and this was
  the first run of the suite on 3.12 (until `V2-P5-055` it stopped at the type check), so six
  tests went red there and nowhere else. Three lines of `backtest/factor_ic.py` were the cause:
  the covariance and both dispersions used `sum`, in a module that uses `fsum` everywhere around
  them.
  - **The sharpest evidence was not in a test's tolerance.** In
    `test_the_register_lists_what_it_holds_in_custody_order_and_not_by_content_hash`, the five
    records' `prd_*` content addresses *differed between the two interpreters*. After the change
    they are byte-identical on both. A content address is not supposed to be a property of the
    interpreter that computed it.
  - **This repository had already written the sentence.** `alpha_baseline.rankable`'s docstring
    says the sort is not tidiness, because "`_pearson` sums products in the argument's own order
    … a fit that silently moved in the last place would still break every content address
    `V2-P4-016` builds on it", with 190/400 at six names and 347/400 at sixty measured to prove
    it. The answer chosen then was to make callers sort. The same measurement is now **0/400 at
    every size on both interpreters**, because the arithmetic no longer has an order to depend
    on. The sort stays — two callers must draw the same population — but its stated reason has
    been corrected to what is now true.
  - Every measurement the change moved was re-measured rather than relaxed: `149 → 143` and
    `153 → 150` (in `factor_redundancy`'s prose *and* its test), `point.ic 0.9999999999999998 →
    1.0`, the digest-sort indices `(2, 1) → (4, 0)`, and `raw_correlation == 0.0` to
    `approx(0.0, abs=1e-15)` — that last because the test's own docstring said "near zero" and
    the exact zero was an artifact of accumulation order.
  - Two adversarial fixtures had been *eaten* by 3.12's compensation and no longer separated
    `sum` from `fsum` at all; both were rebuilt and proved falsifiable by mutation. Over 200
    random mixed-magnitude cross sections built for the purpose, a `sum`-based `_pearson` was
    order-invariant on **200** of 200 on 3.12 against **77** of 200 on 3.11 — a section that can
    tell the two summations apart on 3.12 has to be searched for. Mutating each `fsum` in turn:
    the covariance is caught on both interpreters, the two dispersions **only on 3.12**. That
    table is in the fixture's docstring rather than rounded up to "both".

- **Windows ran the suite for the first time and returned 194 failures and 111 errors; 281 of
  those refusals were the offline guard refusing the interpreter's own plumbing**
  (`V2-P5-059`, `V2-P5-060`, `V2-P5-061`). `windows-latest` has been in `quality.yml`'s matrix,
  and asserted by a test, for as long as the matrix has existed. It had never run.
  - **`V2-P5-059` — the offline guard meant something different on Windows than it means on
    POSIX.** The stack was `TestClient.__enter__` → anyio's blocking portal → asyncio's proactor
    loop → `_make_self_pipe` → `socket.socketpair()`, which Windows has no syscall for and
    CPython therefore emulates by binding a listener on `127.0.0.1` and connecting to it. Not one
    of those tests was reaching the network. The 281 red tests were the symptom; the defect was
    the guard. The exemption is keyed on `socket.socketpair`'s code object and walks the stack —
    the audit event is raised from inside the C method, with the emulation's frame above it — and
    deliberately not on the address: `V2-P4-039` made this guard address-blind on purpose, and an
    exemption reading `if destination == ("127.0.0.1", …)` would have repealed that to paper over
    a platform quirk. A first draft of the fix claimed `socket.socketpair` was a builtin on POSIX
    with no `__code__`, making the branch dead there; measured, that is wrong — `socket.py` wraps
    it on both platforms so callers get `socket.socket` objects. What differs is what the body
    does, not whether it has one.
  - **`V2-P5-060` — nine sites spelled a repository path in the running platform's own
    separator and compared it with a literal written using `/`.** On Windows every one of them
    reported its whole set as unlisted. Seven neighbouring sites already used `.as_posix()`: the
    tree was half converted. An AST audit now holds it converted — and its own first draft was a
    regex over source that flagged its own docstring, which is the mistake
    `test_known_limitation_registries.py` exists to stop counting.
  - **`V2-P5-061` — two "every entry is provoked" equalities cannot hold where the platform has
    no `AF_UNIX`, no `sendmsg` and no `os.posix_spawn`.** Skipping the tests would have left
    `socket.__new__`, `subprocess.Popen`, `os.exec` and `ctypes.dlopen` unmeasured on Windows
    alongside them, so what the platform cannot fire is now named and subtracted instead.
    `socket.sendmsg` stays in `GUARDED_AUDIT_EVENTS` regardless: the guard names the events
    CPython can raise, not the ones a given runner can provoke, and letting the guarded set drift
    with the environment is the defect `V2-P5-059` had just finished removing.

- **The first full suite run CI has ever done found two tests that pass locally and prove
  nothing there** (`V2-P5-057`, `V2-P5-058`). 6 failed, 5580 passed. Neither failure is a defect
  in `src/`; both are assertions that could not separate the two answers on the platform that
  matters.
  - **`V2-P5-057` — six modules each wrote their own undoing of Rich's rendering and not one of
    them removed ANSI.** On a terminal, `--help` is coloured, and the escape sequences land
    *inside* the sentence, so a substring that reads plainly to a human stops matching. The six
    spellings: two collapsed whitespace, three also replaced the option table's `│` (a wrapped
    option help carries the box rule between its lines), one widened `COLUMNS`, and one joined
    with no separator because a limitation code is one token Rich breaks across lines. Locally
    nothing colours the output, so all six passed; CI sets `FORCE_COLOR`. Reproduced locally with
    `FORCE_COLOR=1`. Fixed the way this repository already fixes copied helpers — one
    `tests/cli_help.py` doing only what is about *presentation* (strip ANSI, drop the box rule,
    widen `COLUMNS`, assert `--help` exited 0), plus an AST audit holding the `"--help"` literal
    to that one module. The join is deliberately left to callers: `"".join` and `" ".join` are
    claims about what is being matched, and a helper that picked one would have broken the other.
  - **`V2-P5-058` — `ctypes.dlopen` had never been provoked on Linux; another event was passing
    in its place.** The test exists to hold `OUTWARD_AUDIT_EVENTS` equal to the set it actually
    fires, on the argument that an event nothing raises is an event whose guard is unmeasured.
    What it raised was `subprocess.Popen`: `find_library("c")` was called *inside* the guarded
    block, and on Linux it shells out to `gcc`/`objdump`, so the guard fired before `ctypes.CDLL`
    ran at all. macOS resolves through dyld with no child process, so the test passed locally —
    meaning the `ctypes.dlopen` guard was unmeasured on exactly the platform the `Dockerfile`
    ships. `find_library` now resolves outside the block, and its result is asserted non-`None`
    so that `CDLL(None)` cannot pass in its place.

- **CI had never run on this branch, and its first run found two defects that five local gates
  are structurally unable to see** (`V2-P5-054`, `V2-P5-055`, `V2-P5-056`). `quality.yml` fires
  on pushes to `main`, on pull requests, and on `workflow_dispatch`. The working branch is 356
  commits ahead of `origin/main` with no PR, so none of those had happened: `gh run list` for
  the push target returns empty. Every "green" claimed for this work had been measured locally.
  Dispatching the workflow manually turned two of those local greens red.
  - **`V2-P5-055` — the mypy exemption named the package and missed the only file that package
    types itself with.** Both Python 3.12 jobs exited `2` at *Type check*, on
    `numpy/__init__.pyi:737: error: Type statement is only supported in Python 3.12 and greater`,
    with "errors prevented further checking" — so none of our own code was checked at all.
    `python_version` is pinned to `"3.11"` (correct: the repository supports 3.11), while 3.12
    resolves `numpy==2.4.6`, whose bundled stub spells a PEP 695 `type` statement. The override
    for this already existed — `numpy`, `numpy.*`, `follow_imports = "skip"`, under a comment
    saying third-party stubs may use the executing interpreter's syntax. It had the right intent
    and the wrong surface: `follow_imports` is not honoured for `.pyi` files unless
    `follow_imports_for_stubs` says so. Invisible on 3.11, which resolves an older stub, and
    invisible locally, where numpy is not installed at all and `test_seeding.py` merely skips.
  - **`V2-P5-056` — the `web` job never installed `uv`, and the `production` Playwright project
    starts the real ASGI server.** `pnpm test:e2e` died at `/bin/sh: 1: uv: not found`, exit
    `127`, before a browser opened; the audit, lint, unit-test and build steps were all green.
    That webServer is `uv run uvicorn openalpha_cn.api.app:app` deliberately — `V2-P5-027` fixed
    a URL that was bookmarkable only under the dev server, and only the shipped server can show
    it. The job installed Node, pnpm and Chromium and no Python at all. Locally `uv` is always
    on `PATH`, so the suite passed for every developer and had never once run here.
  - A measurement error worth recording with them: `gh run list` without `-R` resolved to
    `upstream` (`ss8875/…`, whose push URL is deliberately disabled) and listed *that*
    repository's dependabot history. Read straight, it says CI has been running. `gh` picks a
    remote when no default repository is set, and the branch's real CI history was empty.

- **The `pnpm` override written to close CI's dependency-audit gate was what had been holding
  it open, and the test pinning that override's literal version would have refused the fix**
  (`V2-P5-053`). `pnpm audit --audit-level high` — the `web` job's own gate — exited `1` on an
  untouched checkout, on three transitive *dev* advisories that reach no production bundle:
  `undici` through `vitest > jsdom`, `brace-expansion` through `eslint > minimatch`, `nanoid`
  through `vite > postcss`. Two independent acceptance passes had handed this back as "needs an
  exemption decision with an expiry date." It needed no decision. `brace-expansion` was already
  pinned in `web/pnpm-workspace.yaml` at `5.0.8` — which was the *patched* version when the pin
  was written. The advisory later moved to `>=5.0.9`, so the override added to close the gate
  became the thing pinning the vulnerable version in place. A version is a claim about a moment,
  and nothing re-reads it.
  `test_quality_workflow_covers_supported_platforms_and_locked_dependencies` had made that
  moment an invariant — `assert "brace-expansion: 5.0.8" in pnpm_workspace` — so raising the
  pin failed the suite: the test blocked the security fix it existed to guarantee. Fixed by
  moving all three overrides to their patched versions and rewriting the assertion to read the
  *mechanism* (these three packages are pinned) plus the gate that can actually see a new
  advisory (CI still runs `pnpm audit --audit-level high`). That gate is not run from a unit
  test: it needs the network, which `tests/conftest.py` refuses outside `tests/e2e`. The new
  assertion was proved falsifiable by deleting one override. Measured: 8 advisories (5 moderate,
  3 high) to 1 moderate, `--audit-level high` exit `0`; 459 web tests, `lint`, `tsc -b`, `build`
  and `pnpm install --frozen-lockfile` all green; `src/` unchanged.

- **Three e2e tests the first paid Tushare run falsified, each rewritten to assert what the
  market actually did** (`V2-P5-050`, `V2-P5-051`, `V2-P5-052`). All three were tests whose
  premise real rows disproved; none of them was a defect in `src/`, and the whole of `src/` is
  unchanged by this entry.
  - **`V2-P5-050` — the registry wall this repository had been waiting to re-measure is down,
    and the answer to "which dataset now answers first" is *none of them*.**
    `test_shortlist_workflow_online.py`'s own docstring predicted this failure and asked for
    exactly this measurement: its reading that "the registry is what refuses" predated
    `V2-P4-076`, the test would *skip* rather than fail on a post-`076` panel, and "re-running
    this suite against a fresh panel is what would settle which dataset now answers first."
    It did not skip. At 2026-08-24T17:30+08:00 — earlier than `stock_basic`'s own partition
    availability of 2026-08-25T00:00+08:00 — `factor build` exits `0` over 60 subjects, and
    **the cross section is point-in-time sound**: the security that *sets* that availability
    instant (listed 2026-08-25) is absent from the earlier read and present in the later one,
    `listed_on` raises `UniverseHorizonError` for any day past its snapshot rather than
    answering, and all sixty stored observations equal the two prior sessions' close ratio under
    floating-point equality. Sampled across the stored year — the newest six sessions and a spread
    back to the year's second, 11 of 157 — **all eleven build**, 8 of the 11 screen end to end,
    and each answer prices its own session with a listed universe growing monotonically with
    the calendar. The other 146 were not swept, so the claim is "no wall stands anywhere in the
    sampled span", not "every session is screenable". The three that do not screen fail on the
    `namechange` corpus's within-year scope, not on any availability wall. `adj_factor` — the one plane
    `076` deliberately left on `read_if_ready` — does still refuse there, and is now asserted
    as the one wall still standing; it does not stop the build because neither
    `factor build --tier raw` nor `load_shortlist_cross_section` reaches it on this path.
    The rewritten test asserts the withholding is *sound* rather than merely permitted, and
    each of its assertions was checked against an input that models the failure it catches.
  - **`V2-P5-051` — a refusal fired, but the test named a rule that can no longer reach the
    dataset.** `test_a_correction_published_after_the_read_serves_neither_version` expected
    `not_yet_knowable`; `V2-P4-034`'s per-event-date census answered instead. The census is
    **correct here, and nothing is being masked behind it**: `V2-P4-076` moved
    `load_suspensions` off `read_if_ready` entirely, measured before any injection —
    `load_suspensions` answers at an instant two days *earlier* than the `suspend_d` partition's
    own `max_available_time`, which under `read_if_ready` is textbook `not_yet_knowable` and
    does not fire. And the census is the right answer for this injection rather than an
    accident of ordering: `suspend_d` is `ClockStrategy.daily_close`, so a halt dated
    2026-04-28 cannot legitimately become available four months later, and the census refuses
    a partition claiming 32 rows on that event date whose visible slice carries 31. The test's
    other two assertions — the later clock's `SuspensionError` and `panel doctor`'s
    `domain_rebuild_refused` naming the security — hold unchanged, which is what localises the
    failure to the rule name. The rewrite pins the census sentence, the injected row's own
    event date, and the withheld count that this injection (not this panel) determines, and
    keeps an `assert "not_yet_knowable" not in refusal` so that putting the door back on
    `read_if_ready` fails here rather than quietly rebuilding the wall `076` removed.
  - **`V2-P5-052` — not a face divergence: a test comparing two documents that were never one
    object.** The field-by-field diff is **16 items identical, one extra key on the left
    (`limitations`), and zero items differing**; `over_the_command_line == over_http` passes.
    `model_view.held_prediction_view` is `prediction_view` plus `KNOWN_MODEL_VIEW_LIMITATIONS`
    by design — `daily_view` embeds the *un*-suffixed `prediction_view` under a body that
    already carries the registry once, so `fitted.first["prediction"]` and the retrieved
    document differ by exactly that key. The sibling test in the same file already asserts the
    correct merge and names `V2-P4-098`; that fix updated the sibling and left this line
    behind, and `bcddd0d`'s own commit message records that no suite ran on it. The merge is
    now spelled exactly as the sibling spells it, because one rule in two spellings is how the
    faces came apart in the first place. **The third face is now actually compared**:
    `sdk.held_prediction` returns a `PredictionRecord` rather than a rendering, so the old
    `isinstance` plus one field would have passed on an SDK reading a different record; its
    record is now put through the same `held_prediction_view` the other two faces use.

- **Two web-surface defects the product acceptance found, both measured against a running
  server rather than against the report** (`V2-P5-041`, `V2-P5-042`).
  - **`V2-P5-041` — all four pages rendered an API refusal as a raw JSON blob.**
    `web/src/api/client.ts` threw `new Error(await response.text())` and every page renders
    `error.message` verbatim, so the server's own sentence — the good part, the one that
    names the command that fixes the problem — reached users buried in punctuation. Now
    unwrapped in `web/src/api/refusal.ts`, the one module that knows the wire, rather than
    in each of the four pages. All four documented body shapes are handled: the
    `{reason, message}` object (including the `declared_ceiling_exceeded` variant),
    pydantic's field-error **list**, a bare-string `detail`, and a body that is not JSON at
    all. **The list is not flattened** — `loc` is the only thing that says which field
    failed, so each entry renders as `body.evidence[1].payload.quality_flags[1]：<msg>` and
    the `errors_elided` sentinel (empty `loc`) renders as its bare sentence.
    **The status code is not the discriminator**, and that corrects the report: the panel
    plane's `panel_unreadable` is a **`409`**, not a `422`, and the same `409` also carries
    the flat gate verdict, which has no `detail` key. Measured with `curl`, not inferred.
    One assertion in the new tests was caught being green by construction: written as
    `rejects.toThrow(<the sentence>)` it passed against the *unfixed* client, because the
    raw blob contains the sentence as a substring — only an equality can see this bug.
  - **`V2-P5-042` — `所需字段：[object Object]` on every factor-experiment page.**
    `types.ts` declared `required_fields: string[]`; every server answers
    `[{"dataset": "daily", "column": "close"}]`, read off a live
    `GET /api/v1/factors/experiments/{id}`. The type was the defect, not the render. Three
    guards were blind to it at once: the drift check exempts `FactorExperimentEnvelope` by
    *type*, so every field inside it was exempt; the panel's own assertion passed because
    both fixtures were hand-written to match the wrong type; and the CLI was right the whole
    time, so the divergence was invisible from Python. A checked-in JSON schema was
    considered and rejected — the route declares no `response_model`, so a hand-written
    schema would be a *second* hand-maintained mirror free to drift the same way. Instead
    `tests/unit/test_web_factor_definition_mirror.py` compares the mirror field by field
    against `FactorDefinition.model_json_schema()`, which pydantic **generates**; it follows
    `test_spa_addressability.py`'s precedent, runs in 0.02s, and keeps `pnpm test` offline.
    The exemption is narrowed to name what is and is not covered, and a new guard fails if a
    file an exemption points at stops existing. **A third defect of the same family turned up
    on the way**: `family` was mirrored as `string`, so both fixtures carried
    `price_momentum` / `price_reversal` — values no server can send. Mirrored as the real
    five-value union, `tsc` now refuses an invented one.

### Added

- **`V2-P5-047` — the CLI got the two writers its own readers had no input without.**
  `openalpha validation` shipped `statistics` and `segmented` and no writer; `openalpha report`
  shipped `export` and no writer. Both readers therefore answered a CLI-only operator about a
  store that operator had no way to fill — `validation statistics` refuses a signal with nothing
  stored *by name*, correctly and, with no reachable writer, always — while both READMEs
  asserted 「三个面等价」 over the flow containing them. **The choice between building the
  writers and correcting the claim was made by measurement, not by preference**:
  `OutcomeApiRequest.research` and `ReportApiRequest.research` are both `dict[str, Any]`, so
  neither writer needs the in-process object that is `SDK_ONLY`'s stated reason for the one
  capability that genuinely cannot have a second face — and `openalpha research run` already
  prints exactly those bytes, so the loop
  `research run > run.json → validation record → validation statistics` closes entirely inside
  a terminal. New: `openalpha validation record --research … --observation …` and
  `openalpha report create --research …`, both taking their input as files for
  `validation segmented --plan`'s reason (an observation's window, prices, benchmark and cost
  only mean anything together, and every default would be a claim about a market).
  `parse_research_result` and the refusal text moved to a new neutral top-level module,
  `openalpha_cn.research_result_io`, because `cli.py` has no import edge to `openalpha_cn.api`
  at all and reaching the parser through the route module would have put FastAPI behind every
  `openalpha version`; one function now authors the refusal for all three faces.
  `tests/integration/test_validation_and_report_writer_faces.py` holds CLI `--json` stdout
  byte-equal to each route's `200` body and CLI stderr byte-equal to `detail.message` for both
  commands. `tests/unit/test_surface_parity.py` went red naming `cli_commands` 33 → 35 and is
  synced by measurement: two `None` CLI halves of rows that already carried an SDK method
  became commands, so no route, reason or gap-table entry moved.

- **`V2-P5-048` — every command line the READMEs and `docs/` print is now executed.**
  `V2-P4-094` built this shape one channel over, for `openalpha model --help`, on the argument
  that "a `--help` example that has not been run is a claim like any other". The documents were
  the same kind of claim and had never been run.
  `tests/integration/test_documented_command_lines.py` parses every `openalpha …` line out of
  `README.md`, `README.en.md` and `docs/**/*.md`, checks all 50 against the live parameter list,
  and executes the reachable subset verbatim against a generated panel. **Four defects, and
  three of them were the same failure: a fix applied to an example's source and not to its
  copies.**
  - **The factor workflow's steps 3 and 4 are printed as consecutive and are not.** `--tier`
    names the tier that is *written*, not the tier that is reached: step 3 (`--tier processed`)
    exits `0`, and step 4 (`--neutralization industry_and_size/v1`) then exits `1` with
    `No neutralized partition of this factor is registered in this panel at all`. Step 3 has to
    run **twice**, once per tier, which nothing said. Step 4 also named no `--as-of`, so it read
    the panel at the wall clock and its success depended on the day it was run.
  - **The refusal above printed a remedy that does not run either, for any tier** — found only
    by executing it, and **three separate missing flags found one at a time**. `openalpha factor
    build --factor … --tier <tier> --year <year>` exits `2` on `Missing option '--as-of'`; then
    `3` on `--max-staleness-days`, which is refused-if-absent rather than Click-required and so
    is invisible to any check reading `Parameter.required`; then `3` again on `--transform` /
    `--neutralization`. `FACTOR_TIER_BUILD_ARGUMENTS` now carries each tier's required shape as
    angle-bracket placeholders (an instant and a neutralisation are declarations about a study
    that a message must not invent), restated in `factor_view` and `shortlist_view` for
    `FACTOR_TIER_DATASETS`' reason and held equal by a new test.
  - **`model evaluate` and `daily-run` carried `V2-P4-094`'s own broken examples** in three
    places — `README.md`, `README.en.md`, `docs/HANDOFF_CURRENT.md` — because that row changed
    the two printed by `--help` and left the copies. `--horizon 5d` purges the first fold's
    training set to nothing over those seven prediction days; `--as-of
    2026-01-20T04:00:00+00:00` is refused by the partition gate; `daily-run` named no `--as-of`.
  - **`docs/HANDOFF_CURRENT.md` still carried `--waive-max-staleness`,** which `V2-P4-100`
    removed from `factor build --help` after measuring that it exits `1`.

  **What the new test cannot see is declared per line** in `NOT_EXECUTED`: the paid provider
  (`panel build`), a server (`serve`), this machine's credentials (`doctor`), lines whose
  arguments are illustrative placeholders, and lines that answer about the wall clock so that an
  exit code would measure the fixture's build window rather than the line. It also cannot see
  prose — a command that runs while the paragraph beside it says something else is invisible to
  it, which is exactly the shape `V2-P5-047` corrected.

- **`V2-P5-049` — `openalpha --version` exited `2` with `No such option: --version`,** on a
  build whose `openalpha version` printed the number perfectly well. It is the first thing a
  person types. Now an eager root callback that shares `version`'s bytes (asserted byte-equal,
  because two spellings of one build number is what a second spelling invites). The callback is
  named `_root_options` and not Typer's conventional `main`, which this module already binds to
  its `console_scripts` entry point — defining both left the entry point rebinding over the
  callback, working only because the decorator had already captured the function object, with
  `ruff` reporting `F811` throughout. **`is_eager=True` is a reported surviving mutant, and the
  claim it replaces was this row's own**: the first version of these notes called it
  load-bearing, reasoning that `no_args_is_help=True` would otherwise make a non-eager flag
  parse, find no command and exit `2` for a second reason. Deleting it left the whole CLI suite
  green and the installed binary still printed the version and exited `0` — a root callback's
  parameters are processed before the group looks for a subcommand, so there is nothing here
  for eagerness to get ahead of. The flag stays as Click's documented idiom, and the sentence
  claiming a test pinned it does not.

- **A 12-mutant sweep over the source the three rows above changed**, on a proven-green
  baseline with a per-mutant timeout, a backup and restore-on-signal. 9/12 killed on the first
  pass, 11/12 after the survivors were closed. Besides `is_eager`, two were real gaps:
  `--research` accepting a JSON document that is **not** an object (every byte-equality test
  hands the command a well-formed result, so nothing exercised a readable file that is a list —
  without the guard it reaches `parse_research_result`, gets subscripted with `"signal"`, and is
  re-reported as `malformed_research_result`, which is true and useless), and a pretty-printed
  `--json` (comparing `json.loads(...)` cannot see whitespace, and `--json` is piped, so it is
  one line — `report export`'s existing rule, now asserted).

- **A guard that covers the instance and not the class, closed nine times**
  (`V2-P5-030` … `V2-P5-040`, new rows filed by this work). Every item below has the same
  shape: the property was real, the code was often already correct, and the thing that
  measured it could only see one spelling of it. Each was reproduced first and watched
  staying green, then closed and watched going red.
  - **`V2-P5-030` — unknown API paths answered `405` for every non-`GET` method, and the two
    owner sets were compared case-sensitively.** Measured through raw ASGI against a
    `create_app(web_dir=None)` baseline in the same process: `POST`/`PUT`/`PATCH`/`DELETE`/
    `OPTIONS /api/v1/nope` were `405 application/json` with the build mounted and `404`
    without it, and `GET /API/v1/nope`, `/api./v1/nope`, `/api /v1/nope`, `/HEALTH/x`,
    `/Docs/x`, `/OpenAPI.json/x`, `/Assets/missing.js` were all `200 text/html`. Both are the
    sentence the class docstring says it exists to prevent — "a client-side typo, a stale
    caller … comes back as an HTML `200`, and a caller that branches on `response.ok` reads a
    page as a payload" — and `405` additionally asserts the path exists, which for a
    misspelled or retired route is false in both halves. `/Assets/` is the one that matters
    most and shows least: macOS is case-insensitive, so it serves the real JS locally while
    production Linux degrades it to shell HTML for a `<script>` tag. `StaticFiles` checks the
    method *before* the lookup, so a `405` for a non-client location now replays the request
    as a `GET` — in starlette's own words rather than a second copy of the lookup rules — and
    answers `404` when the build holds nothing. `POST /portfolio` stays `405` (the page is
    there, the verb is wrong) and so does `POST /assets/index-abc123.js` (the file is there).
    Ownership is decided on a normalised segment (casefold, trailing dots and spaces
    stripped), deliberately broader than HTTP and asymmetric on purpose: a client area
    colliding with an owner is caught loudly by an existing test, a reserved namespace
    answering as a page is caught by nobody. **The audit's `//api/v1/nope` finding is
    falsified**: raw ASGI shows the server answering `404 application/json`; `httpx` collapses
    the leading `//` and sends `/v1/nope`, so what was measured was the client. Also deletes
    the duplicated `job_store = storage.job_store` at `api/app.py:1792-1793`, a merge remnant
    that had passed ruff, mypy and 3235 unit tests.
    `tests/unit/test_spa_addressability.py` 17 → 36.
  - **`V2-P5-031` — the offline guard was inert outside a test function body.** `_depth` was
    raised by a function-scoped autouse fixture, so module-import time and the setup of every
    session-, module- and class-scoped fixture ran unguarded — measured with a probe in the
    real test tree, four phases `NOT REFUSED, connection completed` against one
    `OfflineSuiteViolation` inside the body. "Fetch it once and reuse it" is the sentence a
    session-scoped fixture exists to make, and `tests/conftest.py` declared four limits with
    this one not among them, which is worse than declaring it. The scope was **raised** rather
    than the limit declared, and that was decided by measurement: no fixture outside
    `tests/e2e/` is session-scoped at all, all 37 module-scoped ones are offline, and the
    whole tree collects clean under the guard. It is a `pytest_runtest_protocol` wrapper plus
    a `pytest_collection` wrapper, not a wider fixture, because broad-scoped fixtures are
    instantiated before any function-scoped autouse one — a session-scoped guard would hold
    `_depth` at one while `tests/e2e/`'s own `built_panel` fetched a real panel. The one phase
    still outside (a session-scoped finaliser) is declared *and* held unreachable by an AST
    assertion. Driven in a child pytest that loads this repository's own conftest as a plugin:
    five phases `REFUSED` with it, five `OPEN` without.
  - **`V2-P5-032` — `tests/unit/test_offline_suite.py` claimed three switches and tested two.**
    Its sections ran "switch 1" then "switch 3", and outside `tests/e2e/` the string
    `OPENALPHA_E2E` appeared in exactly two places: a comment in `pyproject.toml` and the
    docstring sentence claiming it was covered. `require_opt_in` is now driven in a child
    interpreter with the variable cleared.
  - **`V2-P5-033` — a ctypes handle loaded before a paper session opens a brand-new socket.**
    `libc.socket`, `libc.connect` and `libc.send` all happen *inside*
    `refusing_outward_calls()`, raise no audit event, and deliver — measured, twelve bytes
    over loopback. Strictly wider than the two "started beforehand" limitations already
    declared: it needs no connected descriptor and no child process. Putting `ctypes.dlopen`
    in `OUTWARD_AUDIT_EVENTS` created the impression ctypes was covered; only loading is.
    Declared as `KNOWN_PAPER_LIMITATIONS`' eighth entry rather than closed, because an audit
    hook sees what CPython publishes and a raw syscall through a loaded library publishes
    nothing. The test asserts **the bytes arrive** — asserting a refusal would describe a
    guarantee this module does not have.
  - **`V2-P5-034` — the last hand-written limitation registry with no set-literal equality.**
    `test_known_limitation_registries.py` had already measured and written down that
    `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS` was one of the two without one; its own test used six
    `in codes` membership assertions over nine entries, leaving three codes named nowhere in
    the suite. Now an equality, like every other. **The audit's "exchanging one code between
    two registries → 7 passed" does not reproduce here**: the exchange dies, but by an
    unrelated test that happens to index a detail by code, not by a systematic guard — which
    is the difference this row converts.
  - **`V2-P5-035` — surface parity checked that names exist, not that they correspond, and was
    blind to WebSocket routes and Mounts.** Swapping `POST /api/v1/screen`'s SDK method with
    `GET /api/v1/watchlist`'s left `5 passed`; adding an `@application.websocket(...)` left
    `5 passed`. Neither a name rule nor a call graph can close the first — the two faces name
    things by opposite conventions (route endpoint name equals SDK method name in 6 of 48
    rows) and the SDK is in-process, so there is no edge to follow. What a swap breaks is the
    **answer's type**: 20 of the 37 paired rows have both sides declaring one, and the other
    17 are counted rather than skipped. The surviving swap is reported rather than hidden —
    two methods on one path answering the same type cannot be told apart. `_surface_of` now
    keys `WEBSOCKET <path>` and `MOUNT <path>`, and is shared with its test rather than
    re-implemented, because the branch is unreachable through the shipped application. A new
    test **falsified this work's own first fix**: `web_dir=None` is not enough, because
    `create_app` still reads `OPENALPHA_WEB_DIR` when `web_dir` is `None`.
  - **`V2-P5-036` — the conflict-marker gate matched a Markdown heading and missed three real
    markers.** `Summary` + `=======` (a seven-character setext H1 underline) was **detected**;
    nine `=` was missed; `||||||| merged common ancestors` was missed; and bare `<<<<<<<` and
    `>>>>>>>` — which `git` writes when it has no label — were both missed for want of a
    trailing space. `=======` now counts only in a file that also carries an unambiguous
    marker, because `git` never writes a separator alone and a setext underline always is
    alone. Twelve shapes driven, including `cat <<EOF` and six- and eight-character runs.
  - **`V2-P5-037` — `testDiscipline.test.ts` stat'd filenames, and its floor sat under the
    floor.** A new `probeDrift.ts` went red and named itself; a `probeDrift.test.ts` holding
    one `it("works", () => {})` that imported nothing and asserted nothing turned it green at
    `6 passed`. A co-located test must now import its subject and contain an `expect(` — two
    static properties, neither a proof, which remove the *empty* file a filename check
    invites; all 24 already satisfied it. Its threshold floor read `≥90/84/89/91` while
    `vite.config.ts` shipped `93/87/94/94`, so "only ever up" could be walked back by up to
    five points with the test green. Now the shipped numbers.
  - **`V2-P5-039` — `V2-P5-007`'s family-size guard could not separate the two answers.** The
    code was correct, but all 8 fixtures reaching `report_segmented_outcomes` satisfied
    `declared_family_size == len(cohorts)`, so recomputing the family from `len(cohorts)`
    survived at 28 + 13 passed. The same mutant dies 7 times in `multiple_testing.py`. One
    fixture declaring 14 against seven buckets closes it at both sites: `most_permissive`
    halves below a four-observation bucket's floor, so the two buckets that sit exactly on the
    line become incapable and `hypotheses_that_could_ever_reject` goes 4 → 0.
  - **`V2-P5-040` — the citation audit read 60-odd path-qualified citations and ignored 109
    bare ones.** Found while correcting `segmented_reporting.py:43`, which cited a test name
    that has never existed. A bare backticked `test_*` is now held against every name declared
    under `tests/`, minus the names `src/` declares itself — `test_day_count` and `test_set`
    are *fields* of shipped models, and prose naming a field is naming a field. Both sides are
    derived from the tree. It found two more stale citations on the day it was written, in
    `panel_factors.py` and `storage/migrations.py`. A planned de-duplication step was
    **measured to remove zero occurrences** and deleted rather than kept as a no-op, and the
    test that first justified it asserted a per-name partition that does not exist.
  - **`V2-P5-038` is filed rather than fixed.** `test_feature_ledger_test_tree.py` cannot see a
    same-directory row swap; the module already declares that residue. This work measured its
    size — 89 of 185 rows are outside the AST check (`legacy-prose` 85, `ci-job` 1,
    `not-applicable` 3) — and judged both closures more expensive than the defect: migrating
    85 rows is a content change, and a 185-entry path literal goes red on every legitimate
    ledger edit. Recorded as a row rather than left in prose.

- **每个可收藏的 URL 现在在真服务器下也是地址，且未知 `/api/` 路径仍是 JSON 404**
  (`V2-P5-027`, new row filed by this work). `api/app.py` mounted `StaticFiles(html=True)`, whose
  fallback covers only *directory* requests, so **every deep link pages ① through ④ added worked
  under `vite dev` and 404'd under the shipped server**. Measured a third time on `12532e3` with a
  `TestClient` over the real `create_app` and a real `pnpm build`, after two earlier agents
  measured it independently: `/` was `200`; `/data-health`, `/shortlists`, `/shortlists/sl_abc`,
  `/factor-lab`, `/factor-lab/fxp_abc` and `/portfolio` were **all `404`**. The e2e suite could not
  see it -- the thing answering was the dev server, not the application. `SinglePageFallbackFiles`
  serves the shell for any `GET`/`HEAD` the build cannot answer **unless the first path segment
  belongs to somebody else**, and both owners are *derived* rather than written down a second
  time: the live route table's own first segments (`api`, `health`, `docs`, `redoc`,
  `openapi.json`) and the build's own directories (`assets`). The first is the load-bearing half
  -- an unknown `/api/v1/...` stays a JSON `404`, so a client-side typo cannot become a page a
  caller reads as a payload. The second turns a missing subresource back into a clean `404`
  instead of `text/html` handed to a `<script>`.
  **Unknown non-API paths get the shell and the router names them**, and the alternative is
  refused for a stated reason: 404-ing at the server needs Python to hold a second copy of the
  client's route table, which is the defect `V2-P5-011` measured on `CORS_ALLOWED_METHODS` -- one
  fact stated twice, nothing keeping the two equal, the copy already behind the original.
  **Three assumptions about `vite dev` were measured and two were false** (vite 8.1.5, probed with
  `curl`): it has **no** extension rule at all (`/no-such.page` and `/stocks/000001.SZ` are both
  `200` shell), it serves the shell for `/assets/missing.js` too, and its protection of `/api`
  comes from `vite.config.ts`'s hand-written proxy list -- already missing `/docs` and `/redoc` --
  rather than from the fallback. Development is therefore deliberately *not* copied on those two
  points. Nothing keys on `Accept` either: a rule that answers `curl` differently from a browser
  makes an incident unreproducible from a terminal.
  **The proof runs against the real server.** `web/e2e/production-routing.spec.ts` drives
  `uvicorn openalpha_cn.api.app:app` -- the `Dockerfile`'s entry point -- over a real `pnpm build`,
  and **not** `openalpha serve`, because `cli.main()` merges `.env` into the environment and a test
  harness must not read a developer's real one; `OPENALPHA_RUNTIME_DIR` is a fresh `mktemp -d`, so
  no run touches the repository's `runtime/`. Reverting the fallback leaves that file **8 red / 3
  green**, the red being the six deep links plus reload-holds-the-address plus the in-app 404.
  `tests/unit/test_spa_addressability.py` is 17 further cases over a synthetic build, including the
  correspondence that a client area landing on a reserved segment goes red naming the segment.
  `test_surface_parity.py`'s counts are **unmoved**: the fallback is a `StaticFiles` subclass, not
  a route, because it is *how the build is served* rather than a capability of this API.

- **报告导出：许可过滤后的可分享制品，三张脸一次交付** (`V2-P5-022`).
  `GET /api/v1/reports/{report_id}/export`, `OpenAlphaSDK.export_report` and
  `openalpha report export`, all over one implementation in `product/export.py`.
  PRD `S72`/`S81` and Implementation Decision 27 ask for exactly one thing -- 不导出 Tushare 原始
  payload -- and **the obstacle recorded against this row, that no licence field reaches pages ③
  or ④, is true and does not block it**: the row is about *reports*, and a report's evidence is
  the one place a per-row licence actually travels (`EvidenceSnapshot.source_license` and
  `redistribution`, written straight off `ProviderMetadata`; all three shipped providers declare
  `restricted`). No contract change was needed.
  **What is withheld was measured on the real adapter, not assumed.** `providers/tushare.py:4038`
  builds `payload=cast(JsonValue, row)` -- the upstream response row verbatim, which is the "原始
  payload" the decision names. The same constructor builds `summary` as
  `f"Tushare {kind} record for {subject} on {date}."`, a template written in this repository
  carrying a dataset name, a subject and a date and **no provider field values**, so the summary is
  kept and the module says so out loud rather than deciding it quietly.
  **Withholding is a tag, never an absence.** `ExportedEvidence.body` is a discriminated union
  rather than a nullable `payload`, because `JsonValue` admits `null`: an unrestricted evidence
  whose payload *is* `null` and a restricted one whose payload was taken away are byte-identical
  under any design that signals withholding by absence, and they are different facts. The fixture
  that separates them is its own test. `unknown` is treated exactly as `restricted` -- not knowing
  a licence is not a licence -- and a citation the store cannot resolve is reported under
  `evidence_not_recovered` rather than shortening the list.
  Measured: 10 unit cases plus 5 integration cases that all start at a `CliRunner`, a `TestClient`
  over a real `create_app(runtime_dir=…)` or an `OpenAlphaSDK`, over a runtime directory built by
  real `openalpha evidence build` / `openalpha research run` / `POST /api/v1/reports` calls. The
  three faces render the export **byte-for-byte identically**, and a marker planted in the
  restricted provider's row appears in **none** of the three outputs. `test_surface_parity.py`
  moves 47/54/32 → **48/55/33** with `without_sdk` and `rest_only` unchanged: one capability on
  three faces, not one face and two gap-table entries.

- **Playwright page objects, four-page money flows, and the assertion `F64` needed** (`V2-P5-021`).
  Baseline measured: **6 tests / 1 project / 2 spec files / zero page objects**; delivered **25
  tests / 2 projects / 4 spec files / 6 page-object modules**. `F64`'s two claims are both true --
  no page object, and `/api/v1/backtests/validate` unstubbed so **the golden flow never reached
  attribution**. The root cause is not a missing stub line: **no assertion in that suite was ever
  about a request happening**, so `toBeVisible()` passed whether or not a panel had spoken to the
  server. `e2e/stubs.ts` therefore counts every `page.route` hit, and `expectRequested("validate")`
  is the assertion F64's finding actually needed. Stub payloads now come from
  `src/test/fixtures.ts`, whose own docstring already claimed the e2e specs stubbed the same
  shapes -- true by intention and false by construction until now.
  **A measured surprise, kept rather than smoothed over**: the first version asserted each request
  happened exactly once and went red on three flows at once with `Expected: 1, Received: 2`.
  `main.tsx` wraps the tree in `<StrictMode>`, which deliberately double-invokes effects in
  development. Rather than loosening the bound, the two triggers are now distinguished:
  `expectRequested` (a click; **exactly once**, so a double-submitting button still goes red) and
  `expectRequestedOnMount` (**≥1 and ≤2**, two-sided, so a fetch loop still goes red).
  Each page's flow pins its own contract: ① and ④ start `idle` because their endpoints have
  required parameters the app must not invent, and assert that **nothing was requested** before the
  user asked; ② and ③ fetch on mount because theirs take none. One case is only expressible in a
  browser: failing `/api/v1/predictions` must leave the factor-experiment index on screen, which is
  the claim `FactorLabPage.tsx` makes by *not* using `Promise.all` -- about two in-flight requests
  rather than two rendered states.
  Also closed: **`tsc -b` had never read `e2e/`** -- the directory was in none of the three
  tsconfigs -- so `tsconfig.e2e.json` was added and referenced.

- **页面③因子与模型实验室与页面④组合与验证，含五处具名缺口** (`V2-P5-017`, `V2-P5-018`). The last
  two of PRD Decision 24's four route areas, at `/factor-lab` (+ `/factor-lab/:experimentId`) and
  `/portfolio`. **296 -> 438 tests, 22 -> 30 files**, and the ratchet rose on all four metrics:
  **93 / 87 / 94 / 94** (93.19% / 87.52% / 94.41% / 94.36%), against the merged tree's 91 / 85 /
  92 / 93. No dependency was added; `pnpm audit --audit-level high` is **byte-identical** to the
  baseline (still exit 1 through dev-only `jsdom`/`eslint`/`vite->postcss`, unchanged by this row).
  **The headline is a defect the backend names and only the browser is exposed to.**
  `factor_view.everything_is_unmeasured` calls a grid whose six cells are all `not_measured` "the
  quietest bad answer", and records that the acceptance review "named it the most dangerous thing
  on this face": such a run exits `0`, answers `200`, and "a reader that greps for `removed`, finds
  nothing and stops has concluded 'this factor survived neutralisation' about two tiers that never
  computed a number." The CLI prints a named line for it and `--json` prints it on stderr -- but the
  property is deliberately **not a field on the envelope**, so an HTTP client is told nothing at
  all. `factorExperimentStateFrom` recomputes it, and refuses the near miss too: a grid where five
  cells `survive` while the `ACCEPTANCE_STEP` (`processed`->`neutralized`) alone is unmeasured is
  `degraded`, because that is the one step the acceptance criterion is decided on. `removed` is
  emphatically **not** degraded -- a measured step that destroyed the statistic is a report doing
  its job, and folding it in with "we could not measure" would delete the distinction.
  **All three classifiers were written naive-first and measured: 9 of 19 cases failed**, including
  the all-unmeasured grid answering `succeeded`, a `backfill` prediction answering `ready`, and --
  the one worth naming -- `if (view.unallocated_weight)` degrading *every* clean construction,
  because the wire value is the **string** `"0"` and every non-empty string is truthy.
  **Page ④'s weights never touch a float.** `construction_view` renders every Decimal as a string
  so that "`sum(weights) == invested_weight`" stays exactly true, and the panel renders
  `invested_weight` from the field rather than recomputing it; the request sends decimals as
  strings too, since pydantic parses `"0.1"` exactly while the JSON number `0.1` is a float first.
  The fixture that proves it was **corrected on measurement**: the first weight triple (0.4/0.35/
  0.25) sums to exactly `1` in IEEE-754, so the assertion existed and could not fail. It is now
  0.7/0.2/0.1, which sums to `0.9999999999999999` in the order the contract emits, and a guard test
  keeps that property true. **Five named absences, contracts read rather than invented.** Page ③:
  **衰减** -- `ICDecayCurve`/`ICDecayRung`/`FactorICStudy.decay` are complete contracts with **zero
  callers anywhere in `src/`**; they reach no artifact, no view and no route, and cannot be rebuilt
  client-side either, because a `TierReport`'s validator forces one `horizon_sessions` per
  experiment and `ICDecayCurve` requires its rungs over one sample. **相关性** -- the only
  correlation on any HTTP body is `tiers[].survival`, which is the *same factor* at raw vs this
  tier (`left_key == right_key`); cross-factor correlation has no field and `ICSeriesCorrelation`
  is another zero-caller contract, so the heading says 档位存活相关性 rather than 因子相关性.
  Page ④: **容量**, **Paper 净值** and **分段** have no browser-reachable route at all --
  `api/app.py` imports nine `backtest` modules and `paper`, `outcome_statistics`,
  `multiple_testing` and `segmented_reporting` are **not** among them; `nav`/`net_value` appear
  nowhere in `src/openalpha_cn/`; and the construction face ships its own denial as
  `no_capacity_liquidity_or_cost_term_enters_a_weight`. 暴露 is delivered only in its total-exposure
  form, because `targets[].industry_code` is structurally `null` on the shipped shortlist path
  (`an_industry_cap_is_unenforceable_on_the_shipped_shortlist_face`). All five are rendered on the
  page as codes with reasons, and asserted both ways -- the gap named, and the chart absent.
  **模型样本外指标** is served by the prediction register rather than by an evaluation, because
  `POST /api/v1/models/evaluate` stores nothing and has no listing or retrieval route; the panel
  renders `standing_proves`/`standing_does_not_prove` per row, which the serialiser added because a
  face printing `standing` and stopping "turns a local-first bookkeeping fact into what reads like
  an attestation, and a column in a table does that at least as fast as a field in a document".

- **A frontend coverage gate that measures the source tree instead of the import graph, and
  a guard that can name the file** (`V2-P5-020`). The row's own text was stale in three
  places and none of them was the defect. "No component is rendered in isolation" and
  "`vite.config.ts` has no coverage key" were both already false -- `V2-P5-019` had four
  panels rendering all nine `PanelState` kinds through a shared contract suite, and the
  coverage gate had been live since 2026-08-07. **The real hole was the gate's range**:
  without `coverage.include`, vitest's v8 provider measures only files some test happened
  to import, so the denominator is chosen by the test suite rather than by the source tree.
  Measured -- a module dropped into `src/` with two exported functions and no test left
  every number byte-identical (370/405 statements, 246/294 branches, 80/89 functions,
  348/376 lines); with `include` the same probe drove all four thresholds red. Two things
  also left the old denominator's shape: `src/main.tsx` was never counted at all, and
  `src/test/**` *was* counted -- 52 statements at 50 covered, scoring the tests with the
  tests. **What no percentage floor can catch, measured**: deleting `ReplayPanel.test.tsx`
  outright (16 tests, 150 -> 134) moved statements, functions and lines by **exactly zero**
  (320/356, 64/73, 299/328 unchanged); only branches moved, by 1.06pp. `App.test.tsx`
  mounts the whole tree, so every panel's lines execute on the way past whether or not
  anyone asserts anything -- coverage sees execution, not assertion. The converse held too:
  this row's 31 new tests bought 1 statement, 4 branches, 1 function and 1 line. So the
  ratchet is set at the rounded-down measured value, **90 / 84 / 89 / 91** (90.16% /
  84.85% / 89.04% / 91.46%), whose margin in whole units is 0 statements, 2 branches, 0
  functions, 1 line -- two of four at zero. Re-measured under the *previous* scope for
  comparability, the same suite reads 91.60 / 85.03 / 91.01 / 92.81, up on all four against
  `V2-P5-019`'s 91.35 / 83.67 / 89.88 / 92.55: the printed drop is the denominator, not the
  tests. **The naming half is `web/src/testDiscipline.test.ts`**, because vitest 4.1.10
  measurably cannot express an aggregate floor and a per-file floor at once --
  `thresholds.perFile` is global-only, a glob entry's type is `Pick<Thresholds, 100 |
  "statements" | "functions" | "branches" | "lines">` with no `perFile`, and a glob
  aggregates rather than checking each file (`"src/components/**": { functions: 60 }` stayed
  green with `ReplayPanel.tsx` at 50%). That test is a two-way equality: every production
  module has a co-located test or a stated reason, and an exemption that gains a test or
  names a vanished module fails just as loudly. It also pins `include`, the exclusion list
  and the threshold floor, since widening any of them is the cheapest way to turn a red run
  green. **Three modules that had no co-located test now have one**: `PanelNotice` -- the
  only place in the repository that emits `role="alert"`, at 100% coverage purely because
  four panels render it on the way past, with not one assertion of its own; `StatusBar`,
  the one panel-level component the contract suite cannot reach, whose untested branch was
  a reachable backend reporting `status="error"`; and `api/client.ts`, pinning task 17's
  finding that the browser must **not** send `code_commit`/`config_digest` and fabricate
  provenance. **What is still uncovered, itemised**: all 8 uncovered functions are event
  handlers (four `AttributionPanel` `onChange`, `EvidencePanel`'s `as_of` `onChange`,
  `ReplayPanel`'s `onFile`, `App`'s health `.catch` and `startReplay`), and
  `schemaDrift.ts`'s 17 uncovered statements are almost all defensive `throw`s. Rendering
  is proven; interaction is untested, which is an honest next row rather than something to
  bolt on here. No file under `web/src/**` was modified -- only new test files and
  `vite.config.ts`. `pnpm test` **181 passed / 13 files**; `pnpm lint` and `tsc -b` clean.

- **The feature ledger's correspondence with the test tree made executable, and two totals
  that were never measurements** (`V2-P5-023`). `features.csv` names test files by path, so
  a test-tree reorganisation is a three-artifact change -- and only two of the three edges
  were enforced. `--check` holds `summary.json` and the ledger Markdown byte-for-byte
  against the CSV, but the CSV -> disk edge sat inside `if status in TRUE_COMPLETE`, so the
  five `EXCLUDED`/`DEFERRED` rows were exempt from every path check, and three paths are
  reachable only through them. Measured: deleting `SECURITY.md` outright left `--check`
  printing `{"unknown": 0, ...}` and **exiting 0**; with the check moved out of that branch
  the same deletion fails naming the row -- `OA-BOUND-004 references missing evidence:
  ['SECURITY.md']`. **`unknown` and `unreviewed` were literal zeros** in `_summary`, ranging
  over nothing since the ledger's first commit, printed by `--check` as findings and set
  into the released document by `_markdown`. They are computed from the rows now, off the
  same `TERMINAL_STATUSES` the validator enforces, so loosening that set moves them; both
  are still 0, the difference being that this is now a measurement. `summary.json` and the
  ledger Markdown are byte-identical, which is the proof that only the provenance changed.
  **The row's own counts were stale**: `features.csv` names **182** distinct test files
  (180 Python plus `web/src/App.test.tsx` and `web/e2e/golden-flow.spec.ts`), not "34+2" --
  the "2 web" is right and the "34 Python" is off by 5.3x -- and it does not name "all" of
  them: 283 test files exist on disk and 106 are named nowhere. That gap is deliberately
  *not* made an equality: this is the **v1** ledger, and v2's P0-P5 tests do not belong in
  it. **`tests/unit/test_feature_ledger_test_tree.py`** follows `REGISTRY_ENTRY_COUNTS`:
  exact per-directory counts (20 directories, 182 total) as an equality rather than a floor
  or a membership test, plus an existence assertion over every row, field and status. Both
  kinds of move were watched go red -- moving a file without touching the CSV fails naming
  `OA-PANEL-006 -> tests/unit/domain/test_adjustment.py`, and moving it *with* the CSV
  updated fails on the counts naming both ends (`tests/unit/domain: 23 != 24`,
  `tests/unit: 30 != 29`) while the total of 182 stays put, which is what makes the
  per-directory counts the load-bearing half. What it does not catch is stated in the
  module docstring rather than left for the next person: a swap inside one directory moves
  neither count, and is covered for `acceptance_kind="pytest"` rows by
  `_validate_pytest_acceptance`'s AST check. `pytest tests/unit -q` **3146 passed, 1
  skipped**; `ruff check`, `ruff format --check` and `mypy src scripts` clean.

- **前端路由与数据层，以及页面 ① 数据体检、页面 ② 候选清单 + 个股详情**（`V2-P5-014`、`V2-P5-015`、
  `V2-P5-016`；三行同落，因为分开落不成立 —— 见下）。新增 `web/src/routes.ts`（全部地址的唯一出处）、
  `web/src/AppRouter.tsx`（`<Routes>` + 导航 + 404 页）、`DataHealthPanel`、`ShortlistIndexPanel`、
  `ShortlistDetailPanel` 三个纯组件与三个路由容器，`contractState.ts` 增加 `panelHealthStateFrom`
  与 `shortlistStateFrom`，`api/client.ts` 增加 `getPanelHealth`/`listShortlists`/`getShortlist`。
  测试 **150 → 261**，覆盖率四项**全部上升**（语句 91.35→92.53、分支 83.67→84.31、函数 89.88→92.30、
  行 92.55→93.90），`vite.config.ts` 的棘轮按其"只升不降"规则同步抬到 92/84/92/93。
  - **`014` 留下的两个悬案分别作答，依据是实测而非口味。** ① **React Router 取用**：PRD 决策 24
    明文要求"Web 应用演进为 4 个路由区域"，这是产品需求不是偏好；且 `019` 所说"单页应用里路由测试
    分不开『路由生效』与『应用渲染了』"**已随本次三行同落而失效** —— 三个页面在场时，"只渲染地址所指
    的那一个"是可断言的，实测**一个不做路由的 shell 在 9 条路由测试中失败 7 条**（含全部三条
    "renders only"）。代价实测：包体 205.74 kB → 245.87 kB（gzip 64.93 → 78.58），传递依赖仅
    `cookie-es` 一个，`pnpm audit` 高危条数**不变**（三条全部经 `jsdom`/`eslint`/`vite→postcss`
    的开发依赖进入，与本依赖无关）。② **TanStack Query 不取**，四项实测：**(a) 去重价值为零** ——
    `client.ts` 六个函数各**恰好一个**调用点，无任何端点被两个组件取用，没有可合并的并发请求；
    **(b) 状态模型只覆盖 9 分之 3** —— 它能给出 `loading`/`ready`/`failed`，而 `empty`/`degraded`/
    `stale`/`blocked` 由 `contractState.ts` 从**契约字段**导出，取数库看不见；`succeeded` 需要
    `useMutation`（第二套模型）；v5 已删除 `idle` 状态，恰好抹掉 `panelState.ts` 特意保留的
    "还没问"与"正在问"之别；**(c) `stale` 一词语义相反** —— 它的 `isStale` 是"缓存过期，去重取"
    且**期间照常把 `data` 当成功渲染**，而本仓的 `stale` 是"这答的是旧问题，必须标注"；
    **(d) 它要加的缓存服务端已有且是内容寻址的** —— `POST /api/v1/shortlists/run` 把答案按
    `shortlist_id` 存下，`GET /api/v1/shortlists/{id}` 原样奉还（"the body is the stored answer
    and not a re-run"），客户端缓存只是这份内容寻址存储的一个更弱的副本。包体代价 +33.19 kB
    （gzip +9.61）。
  - **两个分类器各有一个"分得开两个答案"的固定夹具**，沿用 `replayStateFrom` 的先例。
    `panelHealthStateFrom`：一份 `is_clean: true`、三项 severity 计数**全为 0**、数据集
    `state: "ready"` 的报告，**只因为 `checks_waived` 里有一项而判 `degraded`** —— 序列化器自己
    说空元组才是"更强的主张"。`shortlistStateFrom`：`is_blocked: true` 且 `funnel.shortlist`
    **已经算出两个名字**时判 `blocked` 且不渲染任何数据；`admitted: null` 与 `admitted: []`
    保持为**两个**答案（`blocked` 对 `empty`），即服务端专门重建过的那个区别。实测**一份朴素实现
    （读 `is_clean` / 读 `funnel.shortlist`）在 15 条中失败 12 条**，两条头号用例都在其中。
  - 新增 `web/e2e/routing.spec.ts`（深链、地址栏、浏览器后退、未知地址）。**e2e 由 4 条（2×2 项目）
    改为 6 条（单 chromium 项目）且更快**（2.6s → 2.1s）。
  - `types.ts` 新增四个镜像（`ReadinessState`、`PanelHealthReport`、`ShortlistIndex`、
    `ShortlistAnswer`），**均为有意的子集**并按 `ReplayReport` 的既有先例登记进
    `INTENTIONALLY_UNMAPPED_TYPES`，每条点名其 Python 序列化器 —— `docs/api/schemas/` 的五份
    契约里没有面板体检报告，也没有候选清单答案，无从对照。守卫先红后绿：四个类型全部被点名。

### Changed

- **删除 `playwright.config.ts` 的 `mobile-chromium`（Pixel 5）项目**（`V2-P5-014`）。它同时与
  PRD 的**两处**规定冲突：实施决策 15 把本套件限定为"覆盖桌面 golden 流程 …… 移动端宽度流程移出
  范围"，决策 24 就 Web 应用整体重申"移动端宽度移出范围"，§5.10 的场景 S82 标为 **OUT**。把整条
  桌面 golden 流程在 393×851 上重放一遍**就是**一条移动端宽度流程，代价是本套件运行时间翻倍。
  其中值得留下的那半条留下了：`workbench stays within a mobile viewport` 更名为
  `workbench never scrolls horizontally at the desktop viewport`，断言一字未动 —— 横向溢出在
  任何宽度上都是真缺陷，旧名字声称的却是一个 PRD 已经移出的范围。

### Fixed

- **Four refusals on `evidence build` and `research run` arrived as raw Python tracebacks,
  and none named a way out** (`V2-P5-043`, new row filed by this work). Every other command
  the final product acceptance drove produced a clean one-line refusal; these four printed a
  full rich traceback of `openalpha_cn` frames with the real message only on the last line,
  which is the presentation `create_app`'s own docstring rules out for this repository --
  "naming the specific variable, never a bare traceback". Reproduced on `94a0af2` through the
  installed `openalpha` binary, not an internal import, because for three of the four the
  message was already right and only its delivery was a stack trace.
  - A CSV missing a column gave `ProviderFailure: Cannot read ev_bad.csv: 'summary'` -- a
    `KeyError` repr, which names **one** absent column and not the contract, so fixing
    `summary` only buys a refusal about `event_time`. `providers/file.py::REQUIRED_COLUMNS`
    now states the eight-column contract, both spellings of the payload column, which column
    is optional, and **which columns this row actually carries** -- the last of those is what
    turns a header typo into a diff a caller can read off one line. `MissingColumnsError` is a
    `LookupError` subclass rather than a `KeyError` precisely because `KeyError.__str__` is
    `repr()`, which is where those quotes came from; `fetch()`'s clause widened from `KeyError`
    to its base class, so every fault it already translated still translates.
  - `kind=filing` gave `ValueError: unsupported evidence kind: filing`, naming none of the
    seven supported kinds. The message now reads them out of `_NORMALIZERS` itself, so an
    eighth kind reaches the refusal in the same commit that declares it.
  - `research run` handed a CSV gave a traceback out of `json.loads`: a `JSONDecodeError`
    about column 1, which describes the file rather than the mistake -- and the mistake is a
    natural one, since both commands take one path and only one of them takes a CSV. The
    refusal names the format this argument takes and the command that produces one.
  - Multi-subject evidence gave a three-line pydantic report in which `--subject` never
    appeared. `validate_evidence` now names the other subjects the payload carries; the
    sentence before the semicolon is unchanged because `sdk.py::export_report` cites it
    verbatim as the invariant its narrowing rests on.

  The catches are per-statement rather than one wide `try`, for the reason `research_run`'s
  docstring already gives: a single clause would let any one of them answer for the others.
  One fault the acceptance did not measure was found on the same statement and fixed with
  them -- `--as-of` defaults to `""`, so omitting it reached `datetime.fromisoformat("")`.
  Exit codes are unchanged at `1` throughout.

- **`research run --run-id` defaults to the literal `local-run`, so every subject after the
  first collides** (`V2-P5-044`, new row filed by this work). Researching four names -- the
  minimum the shortlist gate admits -- gave `rc=0` and then `rc=1` three times with
  `RunConflictError: run_id conflicts with an immutable request: local-run`, behind a
  traceback that never named `--run-id`. **Hitting this is the normal path**, because the
  shortlist gate admits a shortlist only when enough of its names have been researched.
  **The default stays fixed** and the argument is on the record in `RUN_ID_DEFAULT`: a
  `run_id` is not a label on a run but part of its identity -- `RunManifest` stores it,
  `request_digest` is computed over it, and `refuse_a_restated_request` compares the two so
  one id can never mean two requests. A default that differed every invocation would make the
  same command run twice produce two runs, so a rerun could not be recognised as a rerun and
  therefore could not be reproduced. What changes is the refusal, which now names `--run-id`
  and says why the default is what it is, plus a `--help` string on the option itself.
  `RUN_ID_REMEDY` is deliberately **not** shared across faces the way `NO_CALENDAR_REMEDY` is:
  neither the SDK nor HTTP has a default `run_id` at all, so this collision is reachable only
  from the command line and only the command line has a flag to name.

- **A `date_gap` refusal named no flag, and the flag that fixes it is `--as-of`**
  (`V2-P5-045`, new row filed by this work). `--as-of` correctly defaults to "now", so a panel
  built for January fails the moment somebody runs the command in August: `157 required date(s)
  are absent from daily, starting at 2026-01-19`. That sentence is accurate and useless -- it
  describes the store, and the store is fine; what is dated wrong is the question. Passing
  `--as-of` made the identical command exit `0`. `DATE_GAP_REMEDY` now names **both** ways out,
  because both are real and they are not interchangeable: re-date the question when the panel
  is deliberately historical, fetch the missing sessions when it has fallen behind. Naming only
  the second would send somebody replaying January to a paid provider for nothing -- which is
  exactly the mistake `V2-P5-046` below records. Spelled for each face, `NO_CALENDAR_REMEDY`'s
  rule, because this detail is serialised verbatim by the HTTP app, the SDK and `panel doctor`
  alike. **One measurement correction**: the acceptance described this as a stderr refusal; it
  is a `BLOCKING daily date_gap: …` line on `panel doctor`'s **stdout**, and that command's
  `--json` was already emitting 21,382 bytes on this path. The missing thing is the same, its
  channel is not.

- **`subject_missing` printed a count, never the subject, and both remedies it offered were
  wrong** (`V2-P5-046`, new row filed by this work). `openalpha panel doctor --dataset daily
  --year 2026 --as-of 2026-01-17T12:00:00+08:00` refused with `1 required subject(s) are absent
  from trade_cal`, then offered a rebuild or `--no-calendar`. But `trade_cal` **was** built and
  healthy -- it held `SZSE`. Rebuilding would fetch SSE (paid, slow) and `--no-calendar`
  discards the check; the fix was `--exchange SZSE`, which the same command accepts and which
  returns `rc=0 READY daily`. `missing_items` had carried the answer server-side the whole time
  and the human output printed only its length. Two changes: `subject_gap_issue` now names the
  absent subjects (capped at `SAMPLE_LIMIT` then counted, because `required_subjects` on an
  index-membership or universe read is a cross section and an unbounded list would put
  thousands of codes on one terminal line), and `panel_view._calendar_remedy` offers the
  narrower way out **only when the store can support it** -- the cause is a `subject_missing`
  and the census names some other exchange. A genuinely absent partition, a damaged catalog, or
  a store holding only the exchange already asked for keeps `NO_CALENDAR_REMEDY` unchanged,
  because building really is the answer there and naming `--exchange` would point at a flag
  with nothing to put after it. The census crosses the seam as
  `panel_ingest.stored_calendar_exchanges` rather than being read in `panel_view`, whose row in
  `RESEARCH_PLANE_DATASETS` says it reaches fifteen datasets and names none.

- **`--json` emitted nothing at all on the refusal path** (`V2-P5-047`, new row filed by this
  work). The acceptance measured it on one command. Enumerations in this repository have been
  short before, so the whole surface was measured first: of **33 leaf commands in the live
  Typer tree, 22 accept `--json`**, and when each was driven into a genuine refusal (not a
  usage error) **15 exited non-zero having written zero bytes to stdout** -- `data-check`,
  `factor build`, `factor run`, `jobs due`, `jobs run`, `model daily-run`, `model evaluate`,
  `panel build`, `panel doctor`, `portfolio construct`, `portfolio turnover-variants`,
  `shortlist compare`, `shortlist run`, `validation segmented`, `validation statistics`. The
  other seven never reached a refusal in that sweep, so 15 is a floor on the fault rather than
  a count of the healthy. `_panel_fail`'s own docstring had stated the rule since the day it
  was written -- "`--json` output has to stay parseable on stdout even when the command is on
  its way to a non-zero exit, which is precisely when a caller most needs the structured
  reasons" -- and wrote that sentence to stderr with nothing on stdout. The fix is at that one
  funnel: `_panel_command` sets a `ContextVar` and `_panel_fail` reads it, so all 20 commands
  that route refusals through it are covered at once without threading a flag through eighty
  call sites. The human sentence stays on stderr unchanged; stdout gains one document carrying
  `status: refused`, the exit code, and **the same sentence** rather than a second wording of
  it. The guard walks the live Typer tree with a two-entry exemption list that states why
  (`doctor` and `migrate status` route no refusals through `_panel_fail` and already print
  their whole payload). Two earlier versions of that guard read the option declarations from
  the wrong place and returned an empty set for all 33 commands while passing; the floor
  assertion in `test_the_json_walk_really_finds_the_measured_surface` is what caught it, and
  the reading now comes off the built click tree.

- **One integration test wrote a real evidence part and a migration backup into the
  developer's own `runtime/` on every run of the suite** (`V2-P5-048`, new row filed by this
  work). Found by tripping it: running `tests/integration` grew a fresh worktree's `runtime/`
  from nothing to four files. Bisected by pointing `OPENALPHA_RUNTIME_DIR` at a probe
  directory, one file at a time, down to `test_cli_and_api_return_the_same_evidence_snapshot`
  in `tests/integration/test_evidence_interfaces.py`, whose `runner.invoke(app, ["evidence",
  "build", …])` passed no `--runtime-dir`. Eight lines below it, the same test's `create_app(…)`
  carries a comment saying that default "is the repository's own `runtime/`, so this line
  initialised real storage and took a migration backup on **every run of the suite**" and calls
  itself "the last executable `create_app()` in `tests/` with no runtime directory". It was --
  through *that* face. The `runner.invoke` above it became a writer later, when `V2-P5-013`
  made `evidence build` persist what it prints. The printed payload is unaffected, so this is
  pure containment. The class was then swept: of 108 literal `runner.invoke(app, [...])` calls
  naming one of the 28 commands that take `--runtime-dir`, 11 omitted it, and per-file
  measurement showed only this one actually writes -- the rest refuse before any store is
  built, or are `test_cli_runtime_dir_env.py`'s four deliberate omissions, which exist to test
  the `OPENALPHA_RUNTIME_DIR` fallback. **The files already written are not deleted**, which is
  `V2-P4-111`'s own rule for the same directory: they are the user's data.

  **One false positive is recorded here so it is not chased twice.** Re-running the whole
  integration suite with `OPENALPHA_RUNTIME_DIR` pointed at a probe directory still showed two
  files landing in it, from `test_transport_hardening.py::test_openalpha_serve_does_not_announce
  _its_server_software` -- `openalpha serve` takes no `--runtime-dir` and does open the store.
  That test is **not** a leak: it `monkeypatch.chdir(tmp_path)` first, so the relative `./runtime`
  default resolves inside its own sandbox. It reached the probe directory only because an
  exported absolute `OPENALPHA_RUNTIME_DIR` correctly beats a relative default, which is
  `config.py`'s documented precedence rather than a defect. Verified by running that file with
  no such variable set and diffing the real `runtime/` by name, size and mtime: identical.

- **Reconciliation only ever checked half of what it claimed to, and the other half was a
  dropped table reported as a healthy database** (`V2-P5-029`, new row filed by this work).
  `V2-P5-026` says it reconciles "by inspecting the schema, never trusting either counter".
  Measured, it trusted one of them completely: `_unrecorded()` selects on
  `migration.name not in (recorded | repaired)`, so `effect_present` was only ever consulted
  for migrations the audit trail had **never heard of**. The complementary class -- the trail
  names it, the schema has lost it -- was checked by nothing, which meant that on the one
  question the trail and the schema can actually disagree about, **the trail always won**.
  **Measured** on a database at head, dropping `validation_results` and leaving its
  `schema_migrations` row in place -- which is what `DROP TABLE` really does: after five
  `migrate run` restarts, `user_version=8`, `recorded=[1..8]`, `pending=[]`, no
  `schema_repairs` table and no `validation_results` table. `migrate run` said "schema version
  8 is up to date; nothing to do" every time; `migrate status` printed eight `applied` lines
  and nothing else. The table was gone permanently and every operator surface called the
  database healthy -- the same silent stall `V2-P5-026` exists to end, entered by the other
  door.
  **The existing fixture was built so the guard had to fire.** Both repair cases in
  `tests/integration/test_cli_migrate.py` do `DROP TABLE validation_results` **and**
  `DELETE FROM schema_migrations WHERE version = 2` together, and one documents that pair as
  "what a dropped table looks like from the engine's side". It is not -- `DROP TABLE` does not
  touch `schema_migrations`, and deleting the audit row is precisely the condition
  `_unrecorded` keys on. The half where history lies had never been tested.
  **The fix.** `_damaged()` finds migrations below the watermark that the trail *does* name,
  that carry a predicate, and whose predicate answers no. `_repairable()` merges it with
  `_unrecorded()` **in version order** rather than concatenating -- concatenation would put
  every damaged migration behind every undecidable gap regardless of version, so a damaged
  version 2 would be stranded behind an undecidable version 5 three versions in front of it.
  `run_migrations`' early return gained `and not damaged`, which it must: everything past that
  return begins with `_take_backup`, and a repair changes schema. `MigrationStatus.damaged` and
  a `damaged` section in `migrate status` (text and `--json` alike) report it.
  **Two deliberate boundaries.** `_damaged` does **not** exclude names already in
  `schema_repairs`: a table dropped again after being repaired once is damaged again, and a
  ledger lookup there would make the second loss invisible forever -- the very defect being
  closed. What stops the second pass is the predicate answering `True`, a fact about the
  database rather than about our own bookkeeping. And the three data rewrites
  (`effect_present is None`) never appear in `damaged` at all: it means "the schema was asked
  and answered no", and a migration that cannot be asked can be called neither intact nor
  damaged -- the same refusal to guess `V2-P5-026` already makes in the other direction.
  **A hazard this created, found and closed.** `schema_repairs`' primary key is
  `(version, name)`. Under the old design a migration could never be repaired twice, because a
  repaired name was excluded from `_unrecorded` forever; checking the effect regardless of the
  ledger makes it reachable, and a raw `INSERT` would have surfaced `sqlite3.IntegrityError` as
  `MigrationFailedError` against a database merely damaged twice. Now
  `ON CONFLICT (version, name) DO UPDATE`, pinned by
  `test_the_same_migration_can_be_repaired_twice`.
  **Two existing tests went red and were updated to the new correct answer, not reverted.**
  `test_a_projection_missing_only_its_index_reports_its_effect_absent` and
  `test_a_partly_indexed_query_path_migration_reports_its_effect_absent` write
  `schema_migrations` rows for migrations they never apply, over a v1-shaped schema -- which is
  exactly the damage class now inspected for, so the additional repairs they now report are
  correct.
  **Red first.** The nine assertions that existed while the engine was still unmodified
  were all red, each for its predicted reason. Four more were added afterwards -- against
  the `--dry-run` preview, the repair line's wording, and the `<=` boundary -- and rather
  than a second red run each is pinned by a mutant that reverts exactly the behaviour it
  tests, all killed. Mutation sweep over everything this work changed in `cli.py` and
  `storage/migrations.py`: **23 mutants, 23 killed, 0 survived**, on a
  baseline proved green first, each mutant under a 120-second hard timeout with the sources
  restored from a `finally`, an `atexit` hook and SIGINT/SIGTERM handlers, and both files
  byte-compared against pre-sweep copies afterwards. The `<=` boundary case exists *because*
  of that sweep: `<` survived the first pass, since every other case damages version 2 on a
  database at version 8 and none of them stood on the watermark -- and `<` would have missed
  the head migration of every database, which is the most ordinary loss there is.
  `pytest tests/unit` 3246 passed, 1 skipped; `tests/integration` green; `ruff`/`mypy` clean;
  the repair path re-verified through the installed `openalpha` binary as well as `CliRunner`.

- **Two sentences `V2-P5-026` states about its own behaviour did not survive measurement**
  (`V2-P5-029`; both corrected in place in that row rather than restated here).
  **"Stops and reports, leaving it to a human" stops the reconciliation loop, not the
  process.** With version 5 (`rewrite_contract_identities`, no predicate) unrecorded,
  `build_storage` succeeds, `create_app` succeeds and `GET /health` returns
  `200 {"status":"ok"}`; the only signals are one WARNING log line
  (`migration_repair_undecidable`) and one line in `status.unrecorded`. **And it blocks every
  repair behind it, permanently**: unrecording version 6 (`add_runs_mode_projection`, which has
  a predicate and whose effect really was missing) alongside version 5 left `unrecorded` at
  `[(5,…),(6,…)]` and `repaired` empty after five `migrate run` calls. The ordering discipline
  is kept -- repairs are a chain in version order, exactly like the pending loop -- but one
  false sentence it produced is fixed: `migrate status` used to print "its effect cannot be
  established by inspecting the schema" for *every* unrecorded entry, which for version 6 was a
  result reported for an inspection that never ran. The three situations are now worded
  separately, and an entry that was never examined says which migration is blocking it.

- **`OPENALPHA_RUNTIME_DIR` now reaches the command line, and it was 28 commands that ignored
  it, not eight** (`V2-P5-028`, new row filed by this work). Every CLI command naming a runtime
  directory declared `runtime_dir: Annotated[Path, typer.Option("--runtime-dir")] =
  Path("./runtime")` -- the path was a **Typer option default**, so the option was never
  "omitted" as far as the body could tell, and `load_config()` was never consulted at all. An
  exported environment variable lost to a compiled-in default, the exact inversion
  `config.py`'s module docstring rules out ("an already-exported real environment variable
  always wins over ... a field's compiled-in default").
  **The production shape.** `Dockerfile:48` sets `ENV OPENALPHA_RUNTIME_DIR=/data` beside
  `WORKDIR /data` and `VOLUME ["/data"]`. `uvicorn openalpha_cn.api.app:app` therefore serves
  `/data/state.sqlite3`, while `docker exec … openalpha migrate status` resolved `./runtime`
  against the working directory and reported on `/data/runtime/state.sqlite3` -- a file that
  does not exist. The operator was told "schema version 0, 8 pending" about a database nobody
  serves, **a decoy appeared on the mounted volume as a side effect**, and `migrate run` would
  then have migrated the decoy. It also disabled the entire operator surface of `V2-P5-026`:
  the `repaired`/`unrecorded` sections of `migrate status`, and what `migrate run` reports it
  repaired, ship only through this command group.
  **The enumeration this work was handed named eight and was wrong; measured, it is 28** --
  every command in the file that takes `--runtime-dir`, with no exceptions. The twenty it
  missed include `report export`, `evidence build`, all four `shortlist` commands, all four
  `model` commands, all four `jobs` commands, both `factor` commands, both `portfolio` and both
  `validation` commands. Measured in a scratch working directory with the variable exported,
  **`openalpha jobs list` -- a command that only reads -- created a decoy database and then
  migrated it**, logging `migration_applied` for `baseline` and `create_validation_results`
  against a file nothing serves. So the guard filed here is structural rather than a list of
  names: `test_no_command_hardcodes_the_runtime_directory_as_an_option_default` walks the live
  Typer tree and fails on any `--runtime-dir` whose default is a `Path`, which is what a
  hand-maintained enumeration could not do -- it was made once, by hand, and was 20 short.
  **The fix.** Every one of the 28 declares `Path | None = None` and opens with
  `runtime_dir = _resolved_runtime_dir(runtime_dir)`. `None` is the only default that can tell
  "the caller omitted this" from "the caller asked for `./runtime`", and only the first may
  fall back to `load_config().runtime_dir`. The helper is **lazy**, mirroring
  `_resolved_config_digest` directly above it and for that function's stated reason (P0.B
  Finding 2): `load_config()` validates every `OPENALPHA_*` field atomically, so a caller who
  passes `--runtime-dir` never touches config validation and an unrelated invalid field -- a
  non-numeric `OPENALPHA_MAX_REQUEST_BYTES`, say -- can never block them. A caller who omits it
  does get that validation, and a named `ConfigError` on stderr with exit 1, which is the right
  trade when the alternative is silently operating on the wrong database. Both halves are
  asserted against the same broken environment so they cannot drift apart.
  **Three of the six behavioural cases were dropped for being unable to separate the two
  answers**, which is the failure mode this repository keeps finding in its own fixtures.
  `shortlist list`, `model predictions` and `migrate prune-backups` touch an empty runtime
  directory and create nothing in *either* place, so "no decoy in the cwd" passes on the broken
  tree too. The parametrized behavioural test therefore covers only the three that genuinely
  open the database (`migrate status`, `migrate run`, `jobs list` -- measured leaving a decoy
  before the fix and none after), and `shortlist list` is covered instead by a test that seeds a
  well-formed content address into the *exported* directory only, making the two answers differ
  in the output itself. Verified: all seven new assertions fail on the unmodified `cli.py` and
  the three that pin unchanged behaviour (explicit flag wins; no variable still means
  `./runtime`) pass on both. `pytest tests/unit` 3246 passed, 1 skipped;
  `tests/integration/test_cli_migrate.py` 8 passed; `ruff`/`mypy` clean. Reproduced and
  re-verified through the installed `openalpha` binary, not only `CliRunner`.
  **The operator half.** All 28 options now carry the same help string and it states the
  precedence. Eight had no help at all and none named `OPENALPHA_RUNTIME_DIR` -- accurate
  while they ignored it, a silent omission now that they do not, and
  `docker exec … openalpha migrate status --help` is the only place the operator inside the
  container can find out. The guard for it went red on `panel doctor` alone, and the cause
  was the renderer rather than the text: that command's option names are long enough that at
  the 80-column default Rich hard-wraps `OPENALPHA_RUNTIME_DIR` *inside the token*. Fixed in
  the fixture, not the source -- the other way round would have been fixing a defect that
  did not exist. All 28 were then checked structurally: default `None`, the resolver as the
  first statement of the body, and `--help` rendering without error.

- **`V2-P5-020` 的行文与实测不符，已改**（`V2-P5-014` 顺手）。该行称"当前**无任何组件被隔离渲染**"
  且 `web/vite.config.ts` **无 coverage 键** —— 两条在 `V2-P5-019` 交付后即为假：`vite.config.ts`
  自 2026-08-07 起就有 `coverage.thresholds`，四个面板自 `019` 起各有隔离渲染的 `*.test.tsx`。
- **Segmenting a cohort multiplies the hypotheses, and one segmented report is still one family**
  (`V2-P5-009`). `backtest/segmented_reporting.py` cuts one set of validated outcomes by
  industry, market capitalisation, liquidity and market regime and tests **every bucket of every
  axis, plus every baseline, in a single `MultipleTestingReport`**. Reporting each axis as its own
  family is the defect the module exists to prevent: three cuts of one cohort is however many
  buckets result, and three separate corrections give three chances to look skilful at the price
  of one. `declared_family_size` is refused when it is below the buckets actually tested, and the
  refusal names both numbers.
  **All four axes are declared inputs, not just the regime.** A `ValidationResult` carries a
  `signal_id` and no security identifier at all, so an industry or a market capitalisation cannot
  be looked up for one however much of `domain/daily_prices.py` is populated -- there is no key to
  join on. `SegmentLabelling` therefore requires a `definition` and a `source` beside the labels,
  and a signal with no label on a declared axis is refused by name rather than swept into an
  `unknown` bucket. There is no default regime classifier, no default size break and no default
  liquidity screen anywhere in the module.
  **The new column is `can_ever_reject`, and it separates two things a q-value table conflates.**
  An exact sign-flip test over `n` observations cannot return a p-value below `2**(1 - n)`; the
  most permissive line anywhere in a family is `reported * rate / (family_size * penalty)`. A
  bucket whose floor is above that line could not have been a discovery **on any data whatsoever**,
  so its large q-value measures the study's resolution rather than the segment's skill. The floor
  is measured, not asserted from the algebra: the shipped `sign_flip_test` was run over twelve
  thousand random samples at three sample sizes and no p-value fell below it. Segmenting pushes
  both sides of that inequality at once -- smaller buckets, larger family -- which is exactly why
  the column earns its place.
  Regime coverage is measured from the evidence rather than the intention: `spans_multiple_regimes`
  counts regimes with a *testable* bucket, so a walk-forward whose out-of-sample evidence lies in
  one regime says so however many folds produced it. A baseline is paired on the multiset of
  observation windows and yields a paired difference cohort in the same family when it pairs, and
  a named absence when it does not -- the difference of two unpaired means is not the mean of the
  differences. `openalpha validation segmented` and `OpenAlphaSDK.segmented_outcomes` are the two
  faces, both driven end to end over a real store.

- **A turnover band's saving and its price are the same number, and the report says so**
  (`V2-P5-024`). `backtest/turnover_variants.py` reports a buffered book **beside** the unbuffered
  one, and `TurnoverVariantReport` carries both arms as required fields -- a one-armed report is
  unrepresentable rather than discouraged, because *默认并列出报* is the row and a caller who can
  ask for the flattering half eventually will. The band is a no-trade band and is **not**
  `V2-P5-001`'s `turnover_budget`: a budget damps every move proportionally, a band leaves each
  small move untraded and takes each large one whole, and a policy declaring both gets the budget
  first and the band second.
  **A measurement falsified this module's own first design and the correction is the headline.**
  It shipped a `tracking_deviation` column beside `turnover_reduction` as "the price beside the
  saving". They are provably equal -- a banded weight is either the target or the previous weight,
  so a traded name contributes zero to both sums and a suppressed name contributes its whole move
  to both -- and a search over 200,000 random book pairs found no counterexample. A derived column
  cannot disagree with its parents and therefore cannot detect anything (`V2-P5-005`'s rule), so
  the column is gone, `deviation_from_intended_book` is a `property`, and the identity is reported
  as the conclusion: **every unit of turnover a band saves is a unit of distance from the book the
  ranking asked for, one for one.** What is genuinely not derivable from turnover is stored --
  `retained_positions` names each position a buffered run still holds that its own ranking no
  longer admits, and `position_caps_breached` names each limit the suppressed trade would have
  brought back inside, reported and never repaired. `cost_per_unit_turnover` has no default: with
  no declared rate the saving is reported in turnover and `cost_absence_reason` says why, because
  an invented rate would be multiplied by every turnover figure in the report.
  `openalpha portfolio turnover-variants` and `OpenAlphaSDK.turnover_variants` are the two faces;
  the row's own declared seam is measured on the command line over a really stored shortlist, where
  the buffered arm trades `0.100000` against the unbuffered arm's `0.155000`.

  `KNOWN_SEGMENTED_REPORTING_LIMITATIONS` (7 entries) and `KNOWN_TURNOVER_VARIANT_LIMITATIONS`
  (6 entries) are the fortieth and forty-first registries (`REGISTRY_ENTRY_COUNTS` 38 -> 40 rows
  and 284 -> 297 entries, `DOCSTRING_TOTALS` 39 -> 41 registries and 354 -> 367 entries);
  `tests/unit/test_surface_parity.py` moves 50 -> 54 SDK methods and 30 -> 32 CLI commands.
  Unit suite 3135 -> 3196 passing. Mutation sweeps: 12 mutants / 11 killed over
  `segmented_reporting` (the twelfth pattern was removed as provably dead code) and 13 / 13 over
  `turnover_variants`. Runtime dependencies remain **nine**, and `lint-imports` remains
  **8 kept / 0 broken** -- both new modules join the two `backtest-studies-*` source lists rather
  than relaxing anything.

- **`CHANGELOG.md`'s committed merge-conflict markers are resolved** (housekeeping, found while
  closing `V2-P5-009`). `3d3f8c6` carried `<<<<<<< HEAD`, `=======` and `>>>>>>>` in the
  `[Unreleased] / Added` section with a clean `git status`, so they were in the tree rather than in
  a live merge. Both sides were additive bullet lists for different rows (`V2-P5-004` against
  `V2-P5-007`), so both are kept and only the three marker lines are gone; no bullet was dropped.

- **`backtest/paper.py`: a Paper Portfolio whose inability to reach a broker is enforced at
  run time, not asserted in a comment** (`V2-P5-004`, the thirteenth pure-stdlib `backtest/`
  leaf). `PaperPortfolio.advance` lives one observed session forward through
  `PortfolioBacktestRunner` -- so cash, T+1, FIFO, fees and every clamp are the same code a
  backtest runs -- and does the whole of it inside a CPython audit hook (PEP 578) that refuses
  eight events: `socket.__new__`, `socket.connect`/`sendto`/`sendmsg`, `subprocess.Popen`,
  `os.posix_spawn`, `os.exec` and `ctypes.dlopen`. Refusing socket *creation* is what does most
  of the work -- measured, it covers the three escapes `V2-P4-105` had to reach below the class
  graph for, including re-wrapping a detached descriptor with `_socket.socket(fileno=fd)`.
  **The static mechanism was measured and rejected rather than skipped**: `openalpha_cn
  .backtest`'s own `__init__` reaches `replay -> runtime -> agents -> models ->
  models/openai_compatible.py`, whose line 11 is `from urllib.request import Request, urlopen`,
  so importing *any* `backtest/` module already leaves `_socket`, `ssl`, `http.client` and
  `urllib.request` in `sys.modules` -- a claim about this module's import closure would have
  been false, and `lint-imports` cannot see a reach made by an object handed in at run time
  anyway. **That is the case the guard is for**: a caller-supplied `PortfolioLedger` is a
  `Protocol`, so anything with `append` satisfies it, and a ledger that opens a socket during
  `advance` is refused *inside its own `append`*. The depth is **thread-local**, unlike
  `tests/offline_guard.py`'s: a process-wide flag would let a paper session in one FastAPI
  request refuse a provider fetch in another. The ban costs the legitimate path nothing --
  measured, a session over a real `SQLitePortfolioLedger` raises only `sqlite3.connect` and
  `sqlite3.connect/handle`. A paper book **requires** a ledger where a backtest's is optional (a
  paper trade nobody recorded is not a paper trade), refuses a session that does not move it
  forward, and refuses one dated after the caller's declared `observed_on`. Every result carries
  `execution_venue` as a `Literal`, `V2-P5-001`'s idiom, so a record that stopped saying no
  broker was contacted would not validate. `KNOWN_PAPER_LIMITATIONS` is the thirty-seventh
  registry (7 entries; `REGISTRY_ENTRY_COUNTS` 35 → 36 rows and 264 → 271 entries,
  `DOCSTRING_TOTALS` 36 → 37 registries and 334 → 341 entries), runtime dependencies remain
  **nine**, and `lint-imports` remains **8 kept / 0 broken** -- `paper.py` joined both per-module
  `backtest-studies-*` source lists on arrival, which is the property that audit exists to
  create. **It has no command or route of its own yet**: `cli.py`, `sdk.py` and `api/app.py` were
  held by a sibling, so the exact edit is reported instead, and what is proved here is that a
  paper book handed `OpenAlphaSDK.portfolio_ledger` itself reads back on
  `OpenAlphaSDK.list_portfolio_transitions()` and on `GET /api/v1/portfolio/ledger`.

### Changed

- **`PortfolioBacktestStep` holds one trading session's whole book, not one order**
  (`V2-P5-003`). It carried a single `order` and a single `market`, so a K-name book cost K
  steps -- and the verbosity was the least of it. Three defects were reproduced on the old
  runner with a two-name book before the change: the equity curve carried **two points dated
  `2026-07-24`**, the first mid-session; a book **could not be told what a name it holds closed
  at unless it traded that name**, reporting a market value of `21000.00` against a true
  `31000`; and the exposure clamp therefore judged buys on **yesterday's** prices, reading
  `max_gross_exposure` `0.210032` where the truth was `~0.31`. A step is now
  `trade_date` + `bars` + `orders` + one `benchmark_close`, and the runner **marks the book to
  the session's closes before it executes that session's orders** -- an ordering that is two
  different answers on one fixture, refused when marked first and *filled* when marked after.
  `orders` may be empty, so a session the book merely holds through is finally representable.
  A held name with **no bar** on a session keeps its price (an A-share halt serves no daily row,
  so refusing it would make a halt unrepresentable) and is named for keeping it in the new
  `PortfolioBacktestReport.carried_marks`, with the session and how many consecutive sessions
  it has now been carried. A mis-ordered series is refused up front instead of arriving as a
  report whose curve ran backwards while every transition on it read `rejected`.
  **`max_industry_weight` is unmoved and still a named refusal**: K `MarketBar`s carry no more
  industry than one does, so this step supplies no exposures and enforces no industry cap.
  **Both faces changed shape without either file changing a byte** -- `POST
  /api/v1/backtests/portfolio` and `OpenAlphaSDK.run_portfolio_backtest` are pass-throughs of
  this model -- and both of them, listed in the seam audit's `F38` among the 22 routes nothing
  consumes, had **no test at all** until this row; they now have four, driven through
  `TestClient` and `OpenAlphaSDK`. A latent crash went with it: an opening book with no cash
  and no positions is constructible (`PortfolioState.cash` is only `ge=0`) and reached
  `total_return`'s division, returning `decimal.DivisionByZero` from inside a report builder;
  it is now refused by name. **Mutation sweep** over `multi_day.py` and `paper.py`, baseline
  proven green first (`tests/unit` 3071 passed / 1 skipped): **66 mutants, 64 killed**. Both
  survivors are the turnover zero-guard and both are **equivalent, measured rather than
  labelled** -- the refusal above makes `average_equity == 0` unreachable, and flipping it to
  `== 1` needs a book that filled something while averaging one yuan of equity, where the
  smallest fillable order is a hundred-share board lot. **One gap is left open and stated
  rather than hidden**: those series refusals reach `POST /api/v1/backtests/portfolio` as an
  unmapped `ValueError`, so a mis-ordered series comes back `500` where it should be `422`.
  Nothing is written when it happens -- the series is checked before the first order runs --
  and the fix is the three-line `except ValueError -> HTTPException(422)` the neighbouring
  routes already use, measured working; it was not applied because `api/app.py` was held by a
  concurrent change. `docs/api/http.md` carries the note where a caller looks.

### Added
- **The three product faces, measured against each other instead of described** (`V2-P5-013`,
  closing `F31` and the caller half of `F98`, and re-measuring `F29`/`F30`/`F35`). The row's own
  text was checked before it was believed and **two of its four claims were false on `2746663`**:
  `OutcomeValidator` is *not* "completely absent from the SDK" -- `validate_outcome`,
  `list_validations_by_decision` and `list_validations_by_signal` have all shipped -- and the CLI
  does not cover "4 of 20 capability domains"; 25 commands reached the capability of **14**
  of the 44 shipping routes, and 17 of 47 now. The two claims that held were `F30` (whose list is **nine** routes, not the
  "7" it says) and `F31`, both addressed below. Measured surfaces: **44 routes / 48 SDK methods / 25
  CLI commands** before, **47 / 48 / 29** after.
- **`POST /api/v1/portfolio/construct`** (`V2-P5-013`). `V2-P5-001` shipped `openalpha portfolio
  construct` and `OpenAlphaSDK.construct_portfolio` with no REST route. This is the third face and
  it is three lines of body -- `held_shortlist`, `construct_portfolio`, `construction_view`, the
  same three the other two call. The request body's `policy` field is
  `PortfolioConstructionPolicy` **itself** rather than loose numbers the route re-assembles, so the
  tier-weight validator a caller meets is pydantic's own, once, on all three faces. Held to
  `V2-P4-101`'s standard rather than to a substring check: the `200` body is asserted **byte-equal**
  to `openalpha portfolio construct --json`, and both refusals -- a gate-refused shortlist and a
  declared industry cap over candidates that carry no industry -- are asserted **equal** to the
  sentence the CLI prints on stderr. `/portfolio/` and not `/portfolios/`, because
  `portfolio/execute` and `portfolio/ledger` already spell the noun singular, and a second spelling
  of one noun on one API is the drift this row exists to close rather than to add to.
- **`openalpha jobs`: the scheduling primitive gets an operator** (`V2-P5-013`, the caller half of
  `F98`). `V2-P5-010` shipped `job_contracts.py`, `storage/jobs.py` and `scheduler.py` and recorded
  in its own row that nothing in the shipping product called them -- no CLI command, no route, not
  in `build_storage`. All three are now closed: `SQLiteJobStore` is the **thirteenth** store in the
  composition root, `openalpha jobs register|list|due|run` is the face, and `GET /api/v1/jobs` /
  `GET /api/v1/jobs/{job_id}` are the read half. The API face is read-only **deliberately** -- this
  service still has no authentication of any kind (audit `F101`'s second sentence, unclosed), so
  declaring a schedule and taking a lease stay on the machine holding the runtime directory.

  `jobs run` claims the lease and, for each owed session, performs a point-in-time panel health
  report **at that session's own publication instant**, recording `succeeded` or `failed` under the
  per-trading-day primary key. One job body rather than a vocabulary of them, and that is a
  measurement rather than an ambition: every other per-session action in this build takes between
  eight and twenty declared parameters and `scheduled_jobs` has no column that could hold them --
  adding one would be a change to a stored contract, which AGENTS.md rule 3 confines to the closed
  `V2-P4-001` window. It is also the only per-session action that reaches no network, which a job
  running on a timer had better be.

  **`due` still reads no stored fire time, and the CLI is now where that is provable**: a test
  writes a `next_fire_time` a year into the future onto the row and asserts the command owes exactly
  the same sessions, because the answer comes from `newest_published_session` and
  `last_fired_session` and never from that column.

  **The catch-up stops at the first failed session.** `finish_session` does not advance
  `last_fired_session` past a failure, but a later success in the same loop would move the watermark
  over it -- a daily ingest that failed on Monday and succeeded on Wednesday would report itself
  complete through Wednesday with Monday's hole still open, which is exactly the silent gap a
  point-in-time panel must not acquire.

### Fixed

- **Two portfolio routes answered `500 text/plain` for a caller's mistake** (`V2-P5-013`).
  `SQLitePortfolioLedger.append` raises a bare `ValueError` when an `order_id` is reused with
  different content, and neither `POST /api/v1/portfolio/execute` nor `POST
  /api/v1/backtests/portfolio` caught it -- so resubmitting an order, or a backtest whose orders
  keep their ids, told the caller that this repository has a defect. Both now answer `422` with
  the ledger's own sentence, and the two are asserted **equal**: one fault must not depend on
  which door the caller came through.

  **The diagnosis moved under measurement.** This arrived reported as a regression `V2-P5-003`
  introduced with a new strictly-ascending-session check. Driven on `2746663`, before any of that
  row exists, the `500` is already there by the route above; `V2-P5-003` adds a third road to a
  fault that already had one, and its check lands in the same `except` when it merges. What let
  it sit unnoticed is separate and worth naming: `grep -rn "backtests/portfolio" tests/` returned
  **one** line before this change and it was a row in a route table -- the runner had a
  library-level test and the route had none.

  **The catch is narrow by type and by placement.** `PortfolioSimulator` *returns* every
  disagreement with the market as a `200` carrying `status: "rejected"` and a `reason`, and those
  are still recorded; only the ledger write is wrapped on the single-order route. A control test
  drives a sell of stock the book does not hold and requires it to stay a `200`, so a later edit
  cannot widen the catch into "every unhappy answer is a bad request".

- **`openalpha evidence build` printed its snapshots and threw them away** (`V2-P5-013`, closing
  audit `F31`). `OpenAlphaSDK.build_file_evidence` and `POST /api/v1/evidence/build` both appended
  to the evidence store; the command line did not. Two faces of three agreed and the terminal was
  the odd one out, so a caller who built evidence from the command line and then queried it found
  nothing -- and could not tell "the file produced no events" from "the build discarded them". It
  now takes `--runtime-dir` (default `./runtime`, the same directory every other command in this
  CLI means by it) and appends through the composition root. The printed payload is unchanged, so
  a caller piping it into `jq` keeps working. **The test reads the evidence back through a second
  face** rather than asserting on stdout, because the old command already printed the right
  snapshots and a stdout assertion was green before the fix and after it.
- **`SQLiteJobStore.retry_session` existed only in a docstring, and its absence made a failed
  session permanently unreachable** (`V2-P5-013`). `finish_session`'s own prose has always said "a
  retry is an explicit `retry_session`"; there was no such method. A failed run leaves
  `last_fired_session` where it was, so `due()` keeps owing that session, while its row holds the
  `PRIMARY KEY`, so `start_session` answers `JobAlreadyRanError` for it **for ever** -- and
  therefore for every session after it, because `due()` counts forward from `last_fired_session`.
  Nothing had met it because nothing outside `tests/unit/` had ever called this store; `openalpha
  jobs run` meets it on an ordinary Tuesday, since a point-in-time health report legitimately fails
  on a session whose data has not landed. The method reopens a **terminal** run in place -- never a
  `running` one, which would let two processes hold one trading session at once, and never by
  delete-and-reinsert, which would vacate the primary key mid-retry. Stated by the operator
  (`--retry-failed`) rather than taken automatically, because a session that fails for a reason time
  does not fix would otherwise be retried on every wake-up, for ever.

### Changed

- **`tests/unit/test_surface_parity.py` replaces the prose about which face is missing what**
  (`V2-P5-013`). Every shipping route is named with the SDK method and CLI command that reach the
  same capability, or with `None` and a reason; `SDK_ONLY` and `CLI_ONLY` do the same in the other
  two directions; and all three are **equalities** against the live app, `OpenAlphaSDK` and the
  Typer tree. A route added on one face and nowhere else is red and names the route. A row naming a
  method that does not exist is red and names it. A gap that closes without its reason being deleted
  is red. The five counts are pinned the way `REGISTRY_ENTRY_COUNTS` is, so a surface cannot move
  without passing through this file. This is the actual lesson of `V2-P5-013`: **the row's claims
  went stale silently because nothing was checking them**, and the remedy for that is a test rather
  than a better paragraph.
- **One panel-state vocabulary across all four web panels, so a refusal can no longer be
  rendered as an empty success** (`V2-P5-019`). `web/src/panelState.ts` declares a nine-member
  discriminated union and `web/src/components/PanelNotice/` is the single place that emits
  `role="alert"` -- one implementation rather than four copies, because the defect this row
  names *is* that the four panels had diverged in how they express failure. Three of them
  carried `loading: boolean` + `error: string | null` + `result: T | null`, which makes
  `{loading: false, error: null, result: null}` representable: a request that failed and lost
  its message rendered exactly like one that never started. **The row's own text is wrong in
  two places and both corrections are measured.** `EvidencePanel.tsx:9` held a **five**-state
  union, not eight, and an eight-state union had never existed anywhere in `web/src`; the eight
  names come from PRD Decision 14 and are **not** a superset of those five (`idle` and `error`
  are absent from them). What shipped is therefore **nine**: PRD's eight, plus `idle` kept
  rather than folded into `empty` (they are different answers -- "you have not asked" against
  "we asked and there is nothing visible at that clock"), with `error` renamed `failed`. And
  the row's declared dependency on `V2-P5-014` is **false**: this landed with no React Router
  and no TanStack Query, and `web/package.json`'s runtime dependencies are still `react` +
  `react-dom`. **`degraded`/`stale`/`blocked` are constructed from real contract fields, not
  decoration** -- a state only ever built inside a component test is the "branch no test has
  rendered" defect one level up. `blocked` comes from `decision.risk_decision === "block"` and
  from `look_ahead_violations > 0` (PRD Decisions 8 and 19 make look-ahead fail-closed; a
  report's success counters can all read perfect while the violation count is non-zero, so
  that is read first, and the fixture pinning it is exactly `succeeded === total_cases &&
  success_rate === 1 && look_ahead_violations === 1`). `degraded` comes from
  `redistribution !== "allowed"` (`unknown` treated as restrictively as `restricted` -- not
  knowing a licence is not a licence), from an abstaining signal, from carried `risk_flags`,
  from `failures[]`, and from an attribution with **zero named terms** -- chosen because it
  needs no threshold, since no cut-off separating an acceptable residual from an unacceptable
  one has been measured anywhere in this repository and inventing one would be a number with
  no evidence behind it. **A real defect is fixed in passing**: `loadEvidence` cleared the
  downstream results and `importBatch` did not, so importing a batch left the previous run's
  verdict on screen, unqualified, describing evidence it had never seen; it is now `stale`
  with a reason, kept rather than cleared because the run was real and is merely about a
  different input set. **`V2-P5-020`'s finding that all four panels have a `role="alert"`
  branch no test has ever rendered is closed here**: `src/test/panelStateContract.tsx` runs
  the *same* contract suite against each of the four panels -- hand-writing four suites is how
  they diverged in the first place -- rendering all nine kinds and asserting that an alert
  kind carries non-empty text and **no payload at all**. Mutation-checked both ways: forcing
  the two staleness computations to `false` reddens exactly the two new tests and leaves the
  other three green, and deleting a member from `PANEL_STATE_KINDS` fails `tsc -b` at
  `panelState.ts:81`. Measured gates: `pnpm test` **150 passed / 9 files** (baseline 53 / 4);
  coverage statements 80.9 -> **91.35%**, branches 73.47 -> **83.67%**, functions 77.58 ->
  **89.88%**, lines 82.12 -> **92.55%**, with `vite.config.ts`'s ratchet raised to 91/83/89/92
  per its own "only ever up" rule; `pnpm lint` and `tsc -b` clean; `pnpm build` succeeds;
  `pnpm test:e2e` **4 passed** offline.
- **Benjamini-Hochberg false-discovery control, and the family size it was computed against**
  (`V2-P5-007`). `backtest/multiple_testing.py` is a pure-stdlib `backtest/` leaf --
  `math`, `dataclasses` and pydantic, no `openalpha_cn` import at all -- and it does the half of
  the row that is arithmetic in a sort and two comparisons, which is why it fits inside ADR-0003's
  nine runtime dependencies where a t-distribution quantile would not. **The half that gets
  forgotten is the one the contract enforces**: `family_size` is a required, stored field on both
  the request and the report and is *never* inferred from `len(tests)`, because a q-value without
  the family it was computed against is not reproducible -- the same two p-values `(0.0625, 0.375)`
  are `(0.125, 0.375)` and two discoveries under a declared family of two, and `(0.5, 1.0)` and one
  under a declared family of eight, with nothing about the data moving. Only one direction of a
  declaration is checkable and it is checked on the contract (`family_size` below the rows handed
  over is refused, naming both numbers); the anti-conservative direction is not, and
  `the_family_size_is_declared_and_no_check_can_confirm_it` says so instead of implying a check that
  does not exist. **The dependence assumption is an input rather than a label**: `dependence` is
  required with no default, `independent-or-positively-dependent` is BH and `arbitrary` divides
  every line by `H_m`, and on one family at one rate the two give different rejection sets.
  `KNOWN_MULTIPLE_TESTING_LIMITATIONS` is the thirty-seventh registry (6 entries).
- **`openalpha validation statistics` and `OpenAlphaSDK.outcome_statistics`: gross beside net, cost
  drag in its own column, intervals that say what they assumed, and sample counts**
  (`V2-P5-008`). `backtest/outcome_statistics.py` aggregates stored `ValidationResult` rows into
  cohorts -- one signal, one cohort, one hypothesis -- and is the caller `V2-P5-007` needs, since
  that module computes no p-value and refuses to. Five columns per cohort, each its own
  `math.fsum` mean and **none derived from the others**, because a derived column cannot disagree
  with its parents and therefore cannot detect anything: that is the free variable `V2-P5-005` took
  out of the attribution, kept out of the aggregate. The fifth column is `unexplained_return` and
  is not in the row -- it is there because dropping it would repeat `V2-P5-006`'s defect one level
  up, a residual computed and then lost on the way to a product surface.
  **The interval declares its model.** ADR-0003 rules out a t-quantile, so what ships is a
  percentile bootstrap carrying `method`, `confidence_level`, `bootstrap_samples`, `random_seed`
  and `distinct_bootstrap_means` -- the resolution the resampling actually achieved -- and it uses
  `backtest/event_study.py`'s percentile convention verbatim, held to it by a test that requires
  the two faces' endpoints to agree bit for bit. **Below two observations there is no interval and
  no p-value at all**: every resample of a single observation is that observation, so `lower ==
  upper` at any confidence level, and publishing that would be the statistical form of the invented
  20/30/50 split. The cohort keeps its five columns and its sample count, `absence_reason` says why
  in a sentence a report prints, and it stays **outside** the controlled family, because a
  hypothesis nobody tested is not a hypothesis that failed to reject. The p-value is a sign-flip
  randomization test against a null carried on every row, enumerated exactly over all `2**n` sign
  patterns at `n <= 12` and sampled with Phipson and Smyth's `(1 + hits) / (1 + draws)` above it.
  `KNOWN_OUTCOME_STATISTICS_LIMITATIONS` is the thirty-eighth registry (7 entries); the two
  registries move `REGISTRY_ENTRY_COUNTS` and `DOCSTRING_TOTALS` from 36 / 334 / 35 / 264 to
  38 / 347 / 37 / 277. Runtime dependencies stay at **nine**; `lint-imports` stays at
  **8 kept / 0 broken** -- both new modules join the two `backtest-studies-*` source lists rather
  than relaxing anything.

- **`openalpha portfolio construct` and `OpenAlphaSDK.construct_portfolio`: heuristic target
  weights over one admitted shortlist** (`V2-P5-001`, the first module of P5). A twelfth
  pure-stdlib `backtest/` leaf, `backtest/portfolio_policy.py`, turns one `as_of`'s ranked list
  into weights by three declared arithmetic steps -- a contiguous cut on rank into tiers that each
  split their share **equally**, a bounded clamp/redistribute/clamp pass against the caps, and a
  proportional move toward the target bounded by a turnover budget -- and labels the answer
  `heuristic, not optimized` on `PortfolioConstruction.method` (a `Literal`, so a build that
  stopped saying it would not validate) and on every rendered body, terminal and `--json` alike.
  There is no optimiser and that is ADR-0003's decision rather than a shortfall: nine runtime
  dependencies, no numerical stack, so a covariance estimate and a solver are not shippable here.
  **Nothing is pushed onto a last name to make a column add up** -- weight the caps will not take
  becomes cash and is reported as `unallocated_weight`, which is the residual-absorption trick
  `V2-P5-005` exists to delete out of `backtest/validation.py`, not reintroduced one phase earlier.
  **A shortlist the gate refused cannot be turned into weights on either face**: `admitted` is
  `null` for a refusal and `[]` for an admitted empty list, two answers `V2-P4-032` separated on
  purpose, and building a portfolio out of the first would launder the refusal into a set of
  numbers. Driven end to end from a `CliRunner` and an `OpenAlphaSDK` over a real generated panel,
  a real `openalpha factor build` and a real `openalpha shortlist run`, because a policy nobody can
  invoke is not delivered.
- **`PortfolioOrder.target_weight`; `PortfolioLimits` from two fields to five** (`V2-P5-002`).
  The order carries the share of equity it was *meant* to reach, so a stored transition says which
  plan produced it; `PortfolioSimulator` refuses a buy whose **declared** target already exceeds
  `max_position_weight` and still checks the **realised** weight after the fill, which is a
  different fact and the one that differs on a drifted book. `PortfolioLimits` gains
  `max_industry_weight`, `turnover_budget` and `min_cash_weight`, and **which consumer reads which
  field is written down rather than discovered**: `LIMITS_ENFORCED_BY_THE_SIMULATOR` and
  `LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY` are held *covering* against
  `PortfolioLimits.model_fields`, so a limit the contract declares and nobody enforces is red --
  the fail-open shape `V2-P4-030` found four instances of in the risk gate. The two the simulator
  omits are omitted structurally: `MarketBar` carries no industry, and one order carries no book
  history.

### Measured, and it falsifies two premises this work started from

- **A closed-form control that is entirely dyadic cannot see a derived column
  (`V2-P5-008`).** Every figure in the corpus is exact, and on exact inputs `fmean(gross) + fmean(drag)` and
  `fmean(net)` are bit-identical -- so an implementation that *derived* the net column passed the
  whole file. A mutation sweep found it. The fix is one deliberately **non**-dyadic arm
  (`unrounded`), four ordinary decimal returns whose three roundings do not cancel: derived reads
  `-0.1282` and measured reads `-0.12819999999999998`, one unit in the last place apart, and the
  assertion now separates them. The first attempt at that mutant was itself equivalent and is
  recorded as such: `a - b - c` and `(a - b) + (-c)` are bit-identical *per element*, so only the
  mean-level derivation is a real defect.
- **The same corpus cannot see the percentile index either.** `int(0.025 * 1000)` and
  `int(0.025 * 999)` are 25 and 24, and the alpha cohort's three net returns are an arithmetic
  progression whose thousand resample means collapse onto **seven** distinct values -- so
  `means[24]` and `means[25]` are the same number and both conventions publish the same interval.
  Five geometric points (`2**-2 .. 2**-6`) give 56 distinct resample means, where the two readings
  are `0.03125` and `0.034375`.
- **Two contract guards were unreachable from every test that drove the functions.**
  `CohortStatistics`' "an absence must be named" branch and `OutcomeStatisticsReport`'s
  "the family holds exactly the tested cohorts" count check were both green under deletion,
  because the producers never violate them. Both are reachable from a *document*, which is the path
  a stored report is read back through, and the count check is not redundant with the identifier
  check beside it: two cohorts sharing one identifier have an identifier *set* of size one, so only
  the count says that two tested cohorts are being answered by one q-value.
- **An untested cohort excluded from the family costs nothing arithmetically, which is the
  opposite of what this module's own docstring first claimed.** It said carrying one in at
  `p = 1.0` would raise every other cohort's q-value. False in both directions: a stand-in
  `p = 1.0` can never clear its own line, because every critical value is
  `rank * rate / (family_size * penalty)` with `rank <= family_size` and `rate < 1` and is
  therefore strictly below one; and it cannot lower an unclamped q-value above it either, which
  would need `family_size < reported + 1` while adding the row requires the opposite. The
  exclusion stands on a reporting argument instead -- a q-value published for a cohort nothing
  was measured on -- and `test_a_stand_in_p_value_of_one_can_never_clear_its_own_line` is the
  test the corrected sentence now rests on.
- **Mutation sweep**: baseline proven green first (72 passed across the two unit modules, the
  registry audit and the integration module), then **38 mutants, 38 killed**, per-mutant timeout
  and restore-on-signal, no gate ever run with a mutant on disk.

- **The roadmap's "现金下限" is not a third limit.** Under long-only accounting
  `equity == cash + market_value`, so `cash / equity >= f` and `market_value / equity <= 1 - f`
  are one inequality: a 30% cash floor and a 70% exposure ceiling fund exactly the same book, and
  `test_the_cash_floor_and_the_exposure_ceiling_are_one_inequality_and_the_tighter_one_binds`
  drives both through the policy and compares the weights. The field ships because the row asks
  for it and because stating intent as a floor is legible; what the code does not do is pretend
  the two compose.
- **An industry cap has no input on any shipped face, so it is refused rather than satisfied.**
  `shortlist_view` builds its ranking with `exposures=None` and the stored answer renders no
  industry for any name, so `RankedCandidate.exposure` is `None` everywhere a caller can reach.
  A cap that cannot see an industry is satisfied by every book, so a declared
  `max_industry_weight` over candidates carrying no `industry_code` is a **named refusal** on the
  CLI and in the SDK. `OpenAlphaSDK.construct_portfolio_from_ranking` is where it starts working
  the day exposures are loaded (`V2-P5-015`), and the cap's arithmetic is unit-tested there today.
- **`V2-P5-002` is not a breaking change to a stored row, and that was measured rather than
  assumed.** `PortfolioTransition` embeds `PortfolioOrder` and *is* persisted, under
  `single_version()`, so AGENTS.md rule 3 applies and `V2-P4-001`'s window is closed. A payload
  written before the field reads back unchanged through the same `read_versioned` the ledger uses,
  because the default supplies the missing key. What *does* move is the bytes: the payload
  `SQLitePortfolioLedger.append` compares by equality now carries `"target_weight":null`, so
  **re-appending a transition an older build stored raises the conflict guard**. That is the
  migration cost -- a ledger rewrite, not a contract version bump, since there is no second
  version of this model and no portfolio contract is among the five checked-in schemas.

- **The placeholder attribution is deleted; what a run cannot measure is now a named residual**
  (`V2-P5-005`, `V2-P5-006`). `OutcomeValidator._attribute` claimed the entire net active return
  in fixed proportions nothing had measured -- 20% to a `rule` called `decision-policy`, 30% to a
  `factor` called `benchmark-and-cost` (two quantities `net_active_return` has *already*
  subtracted), and the remaining 50% split across the agents by `abs(signal.strength)` with the
  **last agent absorbing whatever was left over**. That last step is why
  `ValidationResult.validate_window_and_attribution` had never once failed on a computed result:
  a reconciliation with a free variable in it cannot fail, and so had never measured anything.
  Two terms survive, both exact: `transaction-cost` (`-transaction_cost`, emitted even at zero so
  "cost was nil" stays distinguishable from "cost is not modelled") and, for a decision that took
  **no** position, `no-position-versus-benchmark` -- worth `realized_return - benchmark_return`,
  which is `-benchmark_return` exactly, with one claimant and nothing left over. A decision that
  *held* a position books its whole selection return to `unexplained_return` instead: a finished
  `ResearchRunResult` carries a conviction, a confidence and some version strings, and none of
  those is a return, so no rule/factor/agent/model share can be shown. `KNOWN_ATTRIBUTION_LIMITATIONS`
  (the thirty-fifth registry, four entries) states the four things now never claimed.
  The control is closed-form and has **two arms**, because one arm separates nothing: every figure
  is a dyadic rational, so both arms are asserted with `==` rather than `approx` -- held reads
  `net 0.1796875 / residual 0.1875 / one term −0.0078125`, flat reads `net −0.0703125 /
  residual 0.0 / two terms`. An implementation that routed everything to the residual passes the
  held arm and fails the flat one; one that keeps any invented split fails the held arm.
  Driven through **both** product faces (`OpenAlphaSDK.validate_outcome` and
  `POST /api/v1/backtests/validate`, byte-identical, and queryable back out), and the web
  attribution panel now prints 未归因残差 beside the terms -- a residual computed and then dropped
  on the way to a reader is the same defect as one never computed.
  **Mutation sweep** (baseline proven at `2970 passed, 1 skipped`): **24 mutants, 24 killed**.
  The one survivor was not equivalent and was **measured rather than labelled** -- spelling the
  flat term `-benchmark_return` instead of `realized_return - benchmark_return` agrees on every
  reachable value except `benchmark_return == 0.0`, where it yields `-0.0` against `+0.0`;
  canonical JSON writes the sign, so the same result took two addresses
  (`val_dba127649bf529e77e53d6aa` vs `val_470895b1ba7335601a265760`). A test now drives that.

- **Two guards for quantifiers and gates that prose asserted and nothing measured** (`V2-P4-112`,
  `V2-P4-115`). `AgentRouter` satisfies an evidence family when **any** declared family is present
  and a feature dependency only when **every** declared column is, and two docstrings cite
  `ThemeAgent`'s `{theme, catalyst, disclosure}` as the reason for the asymmetry. The feature half
  had `test_every_declared_column_must_be_on_the_plane_and_not_merely_one_of_them`; the family half
  had nothing, because every `evidence_families=` in that file declared exactly one family, and on
  a single-family declaration `&` and `<=` agree for every run. Mutating `&` to `<=` left the
  router's own unit file green. Two tests now close it -- one symmetric to the feature half, one
  routing the real `ThemeAgent` so the citation is executable. (The mutant was already killed
  incidentally by `test_research_cycle.py`, whose fixture carries exactly one of the three
  families; what was missing was a *named* guard in the file that owns the rule.)
  Separately, two mutation survivors from `V2-P4-007/008/009` classified "provably equivalent" were
  remeasured and are not: a `Literal` member inside a local-variable annotation survives pytest but
  **`mypy` reports 2 errors**, so it is a sweep-tooling survivor rather than an equivalent mutant --
  a sweep whose oracle is pytest alone under-reports whenever a second gate ships with the build --
  and `ensure_ascii=False` on `shortlist compare --json` was equivalent **on the fixture only**,
  now killed by a test that renders a non-ASCII exchange name. `@dataclass(slots=True)` is the one
  of the three that really is equivalent.

- **`openalpha panel doctor --no-limitation-detail` and `GET /api/v1/panel/health?limitation_detail=false`**
  (`V2-P4-110`). Measured on a generated panel asked about `index_daily`, the `--json` answer was
  16,936 bytes of which **14,359 (84.8%) were the limitation paragraphs** and 1,340 were the
  findings — prose that is byte-identical on a healthy panel and a broken one, on the first run
  and the thousandth. The text face has rendered them as a count since it was written, for the
  reason in `_echo_report`; a machine reader had no such choice. Declining keeps each entry's
  `code`, `datasets` and `dates` and drops only the paragraph, and the default is unchanged —
  a registry served only on request is a registry that stops being read. **The report was filed
  as "the whole ledger, unrelated to the dataset asked about" and that half does not survive
  measurement**: `known_limitations` already selects on `wanted & set(item.datasets)`, so four of
  the ten are `index_daily`'s own and six are the storage plane's, which name no dataset because
  they hold for every dataset alike.
- **`openalpha migrate prune-backups`** (`V2-P4-111`), the documented cleanup path for
  `runtime/backups/`. `--keep N` (default 10), `--dry-run` to list first, `.bak` files only, and
  exit `0` whether or not anything was removed.
- **A durable scheduling primitive, where audit `F98` measured there was none** (`V2-P5-010`).
  `openalpha_cn/job_contracts.py` (the durable shapes), `storage/jobs.py` (`SQLiteJobStore`) and
  `openalpha_cn/scheduler.py` (`TradingDayScheduler`) give the six things `F98` enumerates: a
  persistent job table with a next-fire-time, a lease, a per-trading-day idempotency key, a
  catch-up policy, a calendar dependency, and crash recovery. No new runtime dependency —
  ADR-0003's nine stand; SQLite through the existing `storage/` layer, and the lock is
  `BEGIN IMMEDIATE` rather than a broker.
  - **The idempotency key is the `PRIMARY KEY`**, not a check: `job_id@YYYY-MM-DD`, so a second
    run of the same trading session is an `IntegrityError` from SQLite rather than a race two
    processes can both win between a `SELECT` and an `INSERT`.
  - **Crash recovery is lease expiry**, not a sweeper — a sweeper would itself need scheduling.
    `claim()` takes an expired lease as readily as an absent one, so a process that died holding
    the job is recovered by the next process that asks for it.
  - **`due()` deliberately does not read `next_fire_time`.** A stored fire time is derived from a
    calendar that changes; asking `panel_ingest.newest_published_session` — the one function that
    owns the 16:30 `DAILY_AVAILABILITY_TIME` rule — and comparing against `last_fired_session` is
    the only formulation that survives a holiday being announced after the fire time was written.
    The stored column is kept as a poller's index and recomputed on every advance.
  - **`panel_ingest.session_publication_instant`** is the one new function on the panel plane: the
    inverse of `_sessions_published_through`, placed beside it and reading the same constant.
    `V2-P4-063` found that rule restated three times with two disagreeing and `V2-P4-114` found a
    fourth; a scheduler computing `time(16, 30)` for itself would have been the fifth. The two are
    pinned against each other by a round trip over a full year at half-hourly resolution (17,520
    instants), not against a literal.
  - **Measured while building this, and it decided the shape**: on a *fresh* `state.sqlite3`,
    `create_app()` reaches `schema_version: 2` and stops — migration 3
    (`demo_add_runs_archived_at`) raises `MigrationNotYetApplicable` because `runs` does not exist
    yet, and `run_migrations` breaks out of the loop on that, so **migrations 4 through 8 never
    run on a new database**. A ninth migration adding these tables would never have run either.
    `CREATE TABLE IF NOT EXISTS` in the owning store is the only construction that works on both a
    new database and an old one, which is what `_baseline_apply`'s docstring already says.
  - **Not yet done, and stated rather than implied**: these three modules have no CLI command, no
    REST route and no entry in `build_storage`. Nothing in the shipped product calls them yet, so
    no product-surface claim is made for them here; a later row has to give them a face.
- **The request-body ceiling is now metered on the way in, not read off a header** (`V2-P5-012`,
  audit `F100`). It read `Content-Length` and nothing else, so a chunked request bypassed it
  entirely. Measured on `c847295` against a deliberately tiny 1,024-byte ceiling: a chunked
  `POST /api/v1/research/batches` of **36,000,030 bytes** was answered `422 json_invalid` -- the
  JSON *parser's* verdict, reachable only after the whole body had been read -- with a
  `tracemalloc` peak of **108,346,472 bytes**, three times the body, because Starlette
  accumulates the chunks in a list and then joins them. Bodies with no declared length are now
  counted chunk by chunk and reading **stops** at the ceiling; measured through
  `httpx2.ASGITransport`, which pulls one chunk per `receive`, the fix reads **1 of 400 chunks**
  (100 KB instead of 40 MB) before answering `413`. A declared `Content-Length` above the ceiling
  is still refused before anything is read at all. The refusal carries the same `reason` and
  `limit_bytes` from either gate and adds `measured_bytes` beside `declared_bytes`, exactly one of
  which is ever non-null -- `measured_bytes` is a **floor** on the body, never its size, because
  the rest was never asked for. One case is deliberately still unmetered and is documented as
  such: a body sent to a route that never reads one.
- **The three browser hardening headers audit `F102` named** (`V2-P5-012`):
  `Strict-Transport-Security: max-age=31536000; includeSubDomains` (without `preload`, which is a
  commitment about a domain that a library must not make on an operator's behalf),
  `Cross-Origin-Embedder-Policy: require-corp` and `Cross-Origin-Resource-Policy: same-origin`.
  The same finding's second half is fixed with them: the headers were **appended** to whatever a
  response already carried, so a route setting `x-frame-options: SAMEORIGIN` produced two raw
  header lines and a browser read `SAMEORIGIN, DENY` (measured). They are replaced by name now.
- **`openalpha serve` no longer advertises `server: uvicorn`** (`V2-P5-012`, `F102`).
  `--no-server-header` was passed by the `Dockerfile` and not by the command a developer runs, so
  one deployment of the same application leaked its server software and the other did not.
- **CORS admits every method this service serves, plus the three v2 will add** (`V2-P5-011`,
  audit `F101`). The list was `["GET", "POST"]`, written by hand, and the roadmap row states the
  cost as a v2 risk -- a later `PUT`/`DELETE`/`PATCH` route refused at the browser. **Measured, it
  had already fallen behind the route table it guards**: a preflight naming `HEAD` answered
  `400 Disallowed CORS method` while the application declares four `HEAD` routes. The allowed
  origins (the two local Vite dev servers) and `allow_credentials=False` are unchanged, and both
  are now pinned by tests, because widening methods is only safe while those two do not move.
  The guard against a third divergence reads the methods off the running application rather than
  restating them. `docs/api/http.md` now states the method list and all nine response headers, and
  two tests read the document and the live response together so the table cannot drift — which is
  how a false claim written into that document during this change was caught: `Starlette` does
  **not** append `OPTIONS` to `Access-Control-Allow-Methods` (measured on 1.3.1, it carries
  exactly the list it is given), so `allow_methods=["*"]` and the explicit tuple are *not*
  observationally identical the way the first draft of this code's docstring asserted.
- **`CORSMiddleware` is now the outer of the two middlewares.** While `SecurityHeadersMiddleware`
  sat outside it, every refusal that middleware short-circuits -- the `413` `V2-P4-043` worded so
  carefully, naming the number exceeded and the variable that raises it -- skipped the layer that
  adds `Access-Control-Allow-Origin`, so a cross-origin browser caller saw an opaque network
  failure instead. The one thing given up is the hardening headers on a CORS *preflight*
  response, which renders nothing and carries no body.
- **`V2-P4-043` raised the request ceiling in `config.py` and nowhere it ships** — found while
  documenting that ceiling for `V2-P5-012`, and this one is not a stale sentence. `Dockerfile`
  carried `OPENALPHA_MAX_REQUEST_BYTES=8388608` and `deploy/compose.yml` carried
  `${OPENALPHA_MAX_REQUEST_BYTES:-8388608}`; both are **configuration that overrides the
  default**, so the shipped container ran at 8 MiB. Measured: with that environment,
  `load_config().max_request_bytes` is `8388608`, and `V2-P4-043`'s own measurement of a
  `MAX_BATCH_ITEMS` batch — **9,840,054 bytes** — is still `413`. The row exists to make that
  batch postable and it was postable nowhere the product is deployed. Both files, the deployment
  doc's table, and a second contradicting sentence in `docs/api/http.md` (which said 8 MiB two
  hundred lines after the same file said 33554432) are corrected, and
  `test_every_deployment_that_sets_the_ceiling_sets_the_one_this_service_declares` now reads
  every byte count beside `OPENALPHA_MAX_REQUEST_BYTES` in the four files that set it and
  requires each to equal `OpenAlphaConfig`'s **declared** default. That test also falsifies a
  claim its neighbour made: `test_the_request_body_ceiling_is_named_in_the_http_doc_with_its_variable`
  says in its docstring that "a deployment-doc number that fell behind `config.max_request_bytes`
  goes red here", and it never read the deployment doc at all.
- **The README's own API landscape diagram said `8 MiB 默认请求上限`**, and has since
  `V2-P4-043` raised the default to 32 MiB -- a fourth restatement of a number that lives in
  `config.py`, and the one that was wrong. `scripts/generate_api_relationship_diagrams.py` now
  reads the **declared** default off `OpenAlphaConfig.model_fields` (declared, not effective, so
  the generated asset never depends on the environment of whoever regenerates it), and
  `openalpha-api-01-landscape.svg` is regenerated: one line changed.
- **A model plane reachable from a command line, and a prediction store something can fill.**
  `openalpha model evaluate` fits one declaration once per walk-forward fold and reports the
  five statistics `V2-P4-014` measures; `openalpha model daily-run` fits on the outcomes that
  have already closed, scores one stored cross section and **registers the prediction before its
  outcome is known** (Story S32). Before them, eight issues of contracts -- the `AlphaModel`
  protocol, the versioned feature matrix, the walk-forward split with purge and embargo, both
  stdlib baselines, the content-addressed artifact and the prediction record -- had no caller
  outside `tests/`: the CLI had no `model` command, no route's path contained `model` or
  `prediction`, and `OpenAlphaSDK` had no method that fitted anything. `POST
  /api/v1/models/{evaluate,daily-run}`, `GET /api/v1/predictions[/{record_id}]` and
  `OpenAlphaSDK.evaluate_model()` / `.run_daily_model()` / `.held_prediction()` are the other two
  faces; all three resolve and run through one module, so they cannot fit three models from one
  declaration.
- **`FilePredictionStore` is wired into the composition root**, as the twelfth store, under
  `runtime_dir / "predictions"` and with `build_storage`'s own clock. `V2-P4-017` shipped it and
  left it out by name -- two `lint-imports` contracts stand between a `PredictionBatch` producer
  and `openalpha_cn.storage`, one per direction, so nothing could hand it a batch until a face
  above both planes existed.
- **`RunManifest.alpha_model_versions` is filled**, by `model daily-run` and only by it. The slot
  was declared at `V2-P4-010`, which named `V2-P4-016` for it; that issue measured that
  `run_cycle` has no `AlphaModel` on its path and passed it on, and `V2-P4-017` reached the same
  conclusion from the store side. A daily run files a `mode=daily` manifest naming the one
  artifact it consumed, under a `run_id` derived from the prediction's own content address, so
  re-running an identical day reports `unchanged` on both stores rather than a duplicate on one.
  `model evaluate` writes no manifest and registers no prediction, and both absences are stated:
  it fits one artifact per fold and acts on none of them, and every record it could register
  would stand `unwitnessed`, because a simulated prediction is dated at the instant it simulates.
- **`feature_matrix.require_declared_features` has its first caller.** `--feature-version`
  omitted resolves from the columns the request declares (`--code-commit`'s arrangement, because
  nobody can type a `feat_` digest by hand) and supplied is checked, with a mismatch refused by
  name on all three faces. The answer records which of the two happened, because a resolved
  recipe proves only that the artifact records what it was fitted on. `V2-P4-014` had been named
  as this function's first caller and structurally could not be:
  `backtest-no-numeric-stack-or-panel-plane` forbids `openalpha_cn.feature_matrix` to the whole
  `backtest` package.
- **`--min-scored-ratio` on both model faces, with no default, and a refusal that is not an empty
  answer.** Above the declared floor: exit `0` / `200` with `admitted` carrying what the run
  stands behind. Below it: exit `1` / `409` with `"admitted": null` and both sides of the bar
  under `blocks`, while the `measurement` body stays byte-identical across the pair. It exists
  because `FoldEvaluation.scored_ratio` does -- abstaining on the hard names is otherwise a free
  way to win -- and it is a coverage verdict and never a quality one. A refused `daily-run` still
  registered its prediction, and the `record_id` is on the `409` body.
- **A stale model abstains out loud, and the abstention is not free** (Story S35).
  `--shelf-life-days` on both model faces, `shelf_life_days` on both routes and both SDK methods,
  declares how far past its training cutoff a fit may still be asked. Beyond it every security in
  the batch carries `ABSTAIN_STALE_MODEL` instead of a score -- not a raise, which would delete
  the answer, and not a `0.0`, which is a number a reader cannot tell from an opinion. The check
  lives in `domain/alpha_model.py::prediction_batch_for`, the one chokepoint every implementation
  goes through including a third party's, and it is `require_features`' own argument for being
  there. The span is a property of the *ask* and reaches no artifact field: putting it on the
  declaration would give one fitted model as many addresses as there are opinions about how
  strictly to read it.

  What stops such a model looking skilful is machinery that already existed. An expired fold
  scores nothing, so no test day is `measured`, `FoldEvaluation` refuses to carry a `mean_rank_ic`
  beside a coverage that is not, and `scored_ratio` reads `0.0` -- which `--min-scored-ratio`
  refuses. The interesting case is a fold that expires *partway*: its headline is taken over the
  fresh days alone and is not a worse-looking number, which is exactly why `V2-P4-014` made
  `scored_ratio` the one statistic that is never `null`.
- **The abstention vocabulary is coded and closed over what this repository produces.** Three
  codes for three conditions -- `incomplete_features`, `unrankable_cross_section`, `stale_model` --
  in `ABSTENTION_VOCABULARY`, with `abstention_code` reading one back. `V2-P4-014`'s two sentences
  moved from `backtest/alpha_baseline.py` to `domain/alpha_model.py` and are re-exported unchanged;
  `Prediction.abstention` stays free text, so a third-party model's own reason answers `None`
  rather than raising.
- **A synthetic corpus with a known signal-to-noise ratio and a known-null control**
  (`tests/known_signal_corpus.py`). Sixty securities over thirty prediction days, two columns of
  which one carries the plant, and a realized return of `beta * signal + noise` whose population
  rank IC is closed-form -- `0.317` for the alpha arm and exactly zero for the null, which is the
  same draw with the coefficient set to zero and nothing else changed. Measured: the alpha arm's
  folds read `0.286`/`0.294`/`0.372`; the null arm's read `-0.009`/`-0.008`/`-0.033`. It separates
  a fitted model from an unfitted one three ways, which a one-column corpus provably cannot -- with
  a single feature every rank statistic is invariant to the coefficient, and the two readings come
  out bit-identical. What it cannot do is certify a *realistic* IC: the null arm's own folds wander
  as far as `0.113` from zero, so a plant of `0.03` would be inside this corpus's noise, and that
  is stated where a reader meets it rather than hidden behind a plausible-looking number.
- **Every rendered prediction says what its `standing` does *not* prove.** `V2-P4-017` states
  plainly that `predicted_at` is unverifiable and that nothing defends against whoever owns the
  disk; a face printing `"standing": "forward"` and stopping would turn a single-user
  bookkeeping fact into what reads like an attestation. Both sentences travel in the body and in
  the terminal rendering.
- **`feature_matrix.stored_cross_section_instants`**, so a face can take a **range** of
  prediction days rather than one flag per instant: the builds every declared column shares,
  visible at the reading `as_of`. The intersection and not the union, which is
  `_resolve_instant`'s existing rule read forward.
- **`openalpha model evaluate` and `daily-run` need `adj_factor` and `shortlist run` does not**,
  while `shortlist run` needs `namechange` and these do not. A label is a return *between two
  sessions*, so the labeller requires an adjustment series; nothing on the model faces builds a
  `MarketBar`, so no name history is read. A panel built for one face is short for the other in
  both directions, and each refusal names the `panel build` line that repairs it (`V2-P4-078`'s
  bar).
- **`runs.mode` is a queryable, indexed column, and the payload is still its only copy.**
  Listing every `paper` run used to mean a full table scan plus one JSON parse per stored run,
  because `mode` existed only inside the opaque `runs.payload`. It is now a `GENERATED ALWAYS
  AS (json_extract(payload, '$.mode')) VIRTUAL` column with an index on `(mode, run_id)`, and
  `SQLiteRunRepository.list_runs(mode=...)` is the query that uses it. A generated column
  rather than a written one so there is no second copy to drift: SQLite derives it, refuses
  every attempt to write it, and recomputes it whenever the payload changes. Measured through
  `list_runs` on 100,000 stored runs: 461 ms → 106 ms for a one-in-five spread, 450 ms →
  5.3 ms at one-in-a-hundred, 437 ms → 0.8 ms at one-in-a-thousand — the saving is rows *not*
  parsed, so it scales with how rare the mode is rather than with the table. Most of the first
  win is the column rather than the index, which is why both were measured separately.
  Migration 6, `add_runs_mode_projection`, makes the same change a recorded, backed-up event on
  an existing database and re-derives the projection in Python before committing, so a
  generating expression that silently produced `NULL` for every row rolls the migration back
  instead of turning every mode-filtered listing into a confident empty answer.
- **A factor plane reachable from a command line.** `openalpha factor build` computes and
  stores the raw, processed and neutralised tiers through the real `compute_factor`,
  `apply_factor_transform` and `apply_factor_neutralization` and the three write-time
  guarded writers. Before it, a store built by `openalpha panel build` held no factor
  partition, `openalpha factor run` against it was refused by name, and
  `openalpha panel build --dataset factor_obs_...` answered that the dataset is not one of
  its thirteen build targets — the three engine functions had no operator-reachable caller
  anywhere in the repository and no usage example outside one integration test. On the
  command line and in the SDK only, matching `panel build`: it writes panel partitions and
  the service ships with no authentication of its own.
- **`openalpha factor list` / `openalpha factor describe`, `GET /api/v1/factors`,
  `OpenAlphaSDK.factor_catalog()` / `.describe_factor()`.** The legal values of
  `--factor`, `--transform` and `--neutralization` were listed by no face, route or
  document; the only discovery channel was a typo, which answered with nineteen opaque
  content addresses. The catalog serves every declaration with its **whole** prose note
  (705 to 4,830 characters each), the tier order, one sentence per attribution verdict, and
  a flag on the grid cell the acceptance criterion is decided on.
- The nineteen `note_for` disclosures now reach an operator. Several state in full what a
  factor deliberately does *not* measure — `return_vol_60/v1` records that it occupies
  `V2-P3-013`'s residual-volatility slot, is deliberately not named for a residual, and
  that neither residual is computable in this build.
- Durable bounded-concurrency batch research with progress events, cooperative
  cancellation, item retry, and restart recovery.
- Model capability registry, classified transient retry, and SQLite token/cost
  usage accounting.
- Immutable portfolio transition ledger and multi-day return, benchmark,
  turnover, capacity, and exposure reports.
- Contract-first ChainLin BYOK data Provider with PIT/revision clocks, Bearer
  authentication, client rate limiting, and explicit failure categories.
- Ablatable bull/bear and three-perspective risk committee.
- Event-study CAR, t-statistic, and deterministic Bootstrap confidence interval.
- Structured screening, durable watchlists, and immutable report center through
  REST and Python SDK interfaces.
- Durable per-agent recovery with request-digest and graph-signature isolation.
- SQLite-backed decision memory exposed through the SDK and HTTP API.
- Deterministic A-share portfolio accounting with cash, T+1/FIFO lots, costs,
  realized PnL, and hard single-position/total-exposure limits.
- Secure OpenAI-compatible BYOK model provider with structured-output validation
  and custom-agent injection through the Python SDK.
- Fixed-SHA source audit against TradingAgents, AI Hedge Fund, and
  TradingAgents-CN.

### Changed

- **`web/`'s npm dependency surface is recorded as ungoverned by count, and `V2-P5-014` stays
  open with the measurement on it.** ADR-0003 mentions the frontend **zero** times (its only
  grep hit is the phrase "chain **react**ion"); it constrains `pyproject.toml`'s
  `[project].dependencies`, and the three assertions enforcing it in
  `tests/unit/test_repository_assets.py` read only that file. Nothing anywhere counts, pins or
  bounds `web/package.json`'s dependency table. The PRD's "frontend runtime dependencies =
  only `react` + `react-dom`" sits in a table headed **实测基线** with a **验证方式** column --
  a measurement taken 2026-07-29, not a rule -- and its neighbouring row listing seven backend
  dependencies is already stale against today's nine with nothing going red, which is what
  proves the table descriptive. What *is* governed, and what any future npm addition must
  satisfy, is reproducibility (`pnpm install --frozen-lockfile`, pinned by CI and by
  `test_quality_workflow_covers_supported_platforms_and_locked_dependencies`) and
  vulnerability (`pnpm audit --audit-level high` in the `web` CI job, whose precedent is the
  `brace-expansion: 5.0.8` override in `pnpm-workspace.yaml`). `scripts/verify_publication.py`
  does not scan npm licences. So nothing forbids React Router or TanStack Query -- but the row
  is not landed alone, for measurable reasons: its own metric is "consumes only 6 of 28
  routes", and a router consumes no additional routes (the four page rows do); its one extant
  dependent, `V2-P5-019`, shipped without it, falsifying the `019 dep 014` edge; and a routing
  test in a single-page app cannot separate "routing works" from "the app rendered".
  ADR-0003's reasoning is explicitly **not** transferable here: wheel size, BLAS thread
  pinning and numerical reproducibility do not apply to a dependency bundled into a 205.74 kB
  JS artifact.
- **The neutralised tier builds inside the membership year, not after it (`V2-P4-028`).**
  `panel_neutralization.load_industry_market_cap_cross_section` reads `index_member_all` through
  `panel_ingest.load_industry_cross_section` — `V2-P4-027`'s day-scoped door — instead of
  `load_industry_histories`, which took `PanelStore.read_if_ready` and decided `not_yet_knowable`
  on a partition's **max** `available_time`. A membership year was therefore unreadable until its
  last adjustment took effect, which on the real corpus is the annual constituent review (613
  assignments start 2021-07-30, 255 on 2022-07-29), so a walk-forward that fetched today and
  replayed history was refused once a year. Measured on the generated fixture, the cross section
  assembled on **3 of the window's 10 sessions** before this change and on **10 of 10** after it,
  and `openalpha factor build --tier neutralized` at a mid-window prediction instant now stores
  all three tiers where it used to exit `blocked` — `factor run` answers over the same two days
  at the end of `test_the_dead_end_the_acceptance_review_found_is_closed_end_to_end`. The storage
  door itself shipped with `V2-P4-027` and was never on the product path.
- **A behaviour change inside that: `panel_neutralization._industry_answer` folds two absences
  where it folded three.** "This read cannot speak for that day" — a stored membership year at or
  before the day that `membership_years` did not name — used to be counted as `industry_missing`
  alongside "no assignment covers this day", which made a fail-closed refusal look like a
  property of the market. It is now a **named refusal** that says which year to add. A caller who
  narrows `membership_years` past the day being priced gets an error where it previously got a
  cross section short by exactly the securities it could not speak for.
- **Two `KNOWN_*` codes renamed because `V2-P4-028` made their sentences false**, which is the
  registry mechanism working rather than an edit around it.
  `KNOWN_FACTOR_RUN_LIMITATIONS.the_builder_cannot_produce_a_residual_before_its_years_stored_horizon`
  becomes `...for_a_session_that_has_not_closed` — the third tier is now bounded by one session
  (the prediction instant must be at or after its own day's close, on a day the exchange was
  open) rather than by any year's horizon. `KNOWN_NEUTRALIZATION_LIMITATIONS
  .the_industry_input_is_read_whole_partition_so_a_mid_year_as_of_can_be_refused` becomes
  `a_stored_membership_year_left_unread_refuses_the_day_rather_than_answering_it`, which is the
  narrowing cost that survives. Registry totals are unchanged at 32 / 301.
- **Three breaking contract versions cut at once, with the identity rewrite they require.**
  `RunManifest.mode` gains `paper` and `daily` (`run-manifest/v2`), `AttributionTerm.category`
  gains `model` and `ValidationResult` gains an explicit `unexplained_return`
  (`validation-result/v2`), and `DecisionLedger` carries the run declaration's content address
  (`decision-ledger/v2`). Two of those move a **stored key**, so reading an un-migrated row of
  either raises `IdentityRewriteRequiredError` rather than upcasting it and stranding every
  reference; `openalpha migrate run` applies `rewrite_contract_identities`, which recomputes
  each identity and re-points `validation_results`, `research_memory`, `research_reports` and
  `batch_tasks` in one transaction and refuses to commit an incomplete rewrite. Checked-in
  schema documents are now named after the version they hold
  (`docs/api/schemas/decision-ledger-v2.json` and two siblings).
- **`SignalFrame.horizon` is a countable, comparable span.** It narrows from four units to
  trading days -- the only unit with a session count, so any two horizons a signal carries can
  be ordered and every one of them sizes the return window that scores it. A narrowing changes
  no serialized value, so no `signal_id` moved and `signal-frame` stays at v1. A stored signal
  carrying a calendar horizon is refused by name during migration rather than converted with a
  constant this repository has never measured.
- **The research-cycle modes are declared once.** `domain/run_mode.py` replaces the three
  independent copies in the manifest contract, the request contract and the CLI, so
  `--mode paper` and the two contracts could not disagree.
- **`openalpha factor run` says which grid row is the answer, and warns when the grid
  measured nothing.** The `processed->neutralized` rows are marked inline, and an
  experiment whose six cells are all `not_measured` prints a named warning on stderr in
  both `--json` and plain modes. Exit `0` and `200` still cover it — it did assemble, and
  each tier carries its own coverage codes — but "no `removed` cell" had been readable as
  "the factor survived neutralisation" about two tiers that never computed a number.
  `docs/api/http.md` documented the `removed` case and not this one.
- **Every required option of `openalpha factor run` has help text.** Fourteen of the
  seventeen showed a bare `[required]`, on a command whose own docstring says the numbers
  move every verdict it prints.
- A mistyped `--factor` is answered with the declared **qualified keys** and a pointer to
  `openalpha factor list`, instead of nineteen `fct_` content addresses from a help text
  that had just said "the key is the form for a human".
- `KNOWN_FACTOR_RUN_LIMITATIONS` replaced
  `nothing_in_this_repository_builds_a_factor_panel_from_a_command_line` — which
  `openalpha factor build` makes false — with
  `the_builder_cannot_produce_a_residual_before_its_years_stored_horizon`, the part of it
  that was still true and the residual `V2-P4-026` closes. `V2-P4-028` then made *that*
  sentence false in turn; see the entry at the top of this section.
- `docs/HANDOFF_CURRENT.md` no longer says "v2 implementation has not started; the next
  step is `V2-P0A-001`". P0.A, P0.B, P1, P2 and P3 are merged; a reader following the
  repository's own pointer would have concluded the factor plane does not exist.
- The feature ledger now contains 160 terminally reviewed capabilities, with 155
  supported by local source and test evidence (`96.88%` true completion,
  `UNREVIEWED=0`, `UNKNOWN=0`).
- The repository work pointer now targets v2. `AGENTS.md`, `CONTRIBUTING.md` and
  `docs/HANDOFF_CURRENT.md` reference the `docs/specs/v2/` workspace, which holds
  the re-scoped PRD, a seven-phase roadmap sliced to issue level, and a four-seam
  code audit whose findings each map to a closing issue. The v1 spec remains
  the contract baseline.
- **A walk-forward panel now carries its own invariants, and three claims that were
  broader than what held are narrowed to it.** `V2-P4-013` said an unordered split is
  *unrepresentable*; the acceptance measured that the ordering behind that derivation lived
  only in the `labelled_panel` factory, while `LabelledPanel` and `PanelSection` were exported
  frozen dataclasses with no `__post_init__` — so one `dataclasses.replace` moving an early day
  to the end of the tuple produced a fold the shipped `walk_forward_folds` accepted and whose
  own `leaked_sessions` reported six shared sessions. A second bypass found while closing it
  purged 0 of 48 candidates by moving a section's instant without its prediction day. Both are
  refusals on the types now; the factory keeps only what a panel cannot see. The registry code
  moved to `train_membership_is_unrepresentable_and_the_order_behind_it_is_only_refused`,
  because membership really is unrepresentable and the order really is only refused.
- **Two `KNOWN_BASELINE_LIMITATIONS` entries contradicted each other and the false one is
  rewritten.** A mean rank IC does not separate a leaked fold from a purged one: measured
  `-1.0` in all four configurations of both corpora, with the leak visible only in the
  coefficient. The `1.0` and `0.0` that do separate them are `V2-P4-013`'s concordance numbers.
  The audit that binds every registry `code` to executable test code cannot see a false
  `detail`, and the cheapest structural candidate for closing that was measured and declined —
  it would have been satisfied by this very sentence while raising 38 false alarms — so the
  boundary is written where a reader meets it instead.
- **`Prediction.score` normalises the sign of a zero**, the last of the three addressed floats
  to get `V2-P4-016`'s `_unsign_zero`; before it, two batches that compared equal were filed
  under two `record_id`s. Filed as latent and measured otherwise: the reference model's
  `sign * (value - centre)` hands `-0.0` to any security sitting exactly on the learned centre
  under a negative sign, which is the shipped `predict` and not a hand-built payload. `FilePredictionStore.put`'s `supersedes` referent check is recorded
  as contract-only — no face can supply a lineage edge — with an AST assertion that goes red
  the day one is wired. `feature_matrix._PANEL_FAULTS` speaks for five loaders, not six, and
  the count is now executable. And `lint-imports` alone does not stop a new `backtest/*.py`:
  a probe importing `numpy` and a store reads `8 kept, 0 broken`, so the sentences that said
  otherwise now point at the pytest assertion that does.

### Fixed

- **A migration inserted at an already-used version number froze every database that had
  crossed it, permanently and silently** (`V2-P5-026`). Measured on a **copy** of the user's own
  `runtime/state.sqlite3` (the file itself never opened for writing): `user_version = 4`,
  `schema_migrations` holding `[(1, baseline), (2, demo_add_runs_archived_at),
  (3, demo_add_runs_archived_at), (4, create_query_path_indexes)]`, no `validation_results`
  table, and `create_app(runtime_dir=…)` returning `4, 4, 4` across three starts. The cause is a
  pair of commits six hours apart: `1e54104` shipped the registry with the demo migration at
  version 2 and that database recorded it there, then `6eba39c` inserted
  `create_validation_results` **at version 2** and renumbered the demo migration 2 -> 3.
  `_pending()` filters on `version > user_version` alone, so version 2 sat below the watermark
  forever, `validation_results` was never created, and `rewrite_contract_identities` -- which is
  `require_table`-bound to it -- deferred on every process start with no error and no
  terminating condition. This is the same database whose deferral `V2-P4-111` measured as 125
  identical backups; that row stopped the backups, this one starts the chain.
  **Three readings of the symptom were falsified by measurement.** The duplicate is not a
  constraint failure: `schema_migrations.version` is a PRIMARY KEY and held perfectly -- what
  repeats is a *name*, at two versions, because the same migration genuinely ran twice under two
  numbers. The counter and the audit trail do not disagree with each other either: they agree
  exactly, at a contiguous 1-4; what disagrees is the trail's **names** and the current
  registry's names for the same numbers, visible only by comparing against the registry. And it
  is not a one-off: dropping `validation_results` from a healthy database reaches the identical
  state, which is now a `CliRunner` test.
  **The repair inspects the schema and invents nothing.** `Migration` gains an `effect_present`
  predicate restricted to `sqlite_master` / `PRAGMA table_xinfo` -- never either counter, since
  those two are precisely what cannot be trusted in this state -- and `run_migrations` runs a
  reconciliation pass before the pending loop: effect absent, run it and record `applied`;
  effect present, record `verified` and run nothing; **no predicate, stop and report**, because
  re-running an identity rewrite can corrupt records and recording it unchecked fabricates the
  history this engine exists to keep. `PRAGMA user_version` is never written by a repair (the
  version is already passed) and **not one row of `schema_migrations` is deleted or rewritten**
  -- the stale row is a true statement about an older registry, and `version` being that table's
  PRIMARY KEY makes correcting it in place impossible anyway, which is why repairs land in a
  companion `schema_repairs` ledger instead. `openalpha migrate status` reports both, in text
  and `--json`; `migrate run` says what it repaired. The user's database goes `4 -> 8` on its
  first start and stays there, and a fresh database's sequence is unchanged (`2`, then `8`, then
  stable). `test_no_shipped_migration_may_be_renumbered_or_renamed` freezes the version-to-name
  map so this cannot recur -- the existing "unique and increasing" guard was fully green on the
  day it happened, because uniqueness is a property of one snapshot and a number's *meaning* is a
  property held across releases by databases already on disk.
- **The tradable tier named nothing -- not the rule, not the security** (`V2-P4-066`). A
  whole-market screen answered `5545 listed -> 5542 scored -> 5533 tradeable` and `measured
  tradable=0.9978`, and the words `halted`, `below_board_minimum` and `up_limit` appeared nowhere
  in the body: `funnel.excluded_by_coverage` explains stage **one** only, so the arrow
  `--min-tradable-ratio` actually gates was a subtraction with no explanation beside it. The
  census underneath was never the missing part -- `TradeabilityCensus` has carried
  `refused_by_verdict` and `rejection_reasons` since `V2-P4-005` and neither reached a shipped
  surface. It now carries `refused` as well, every non-admitted security **by name** with the rule
  that decided it and, for exactly the ones the execution policy refused, that policy's own
  sentence; `__post_init__` holds the names to the counts in four directions, so a census whose
  list disagreed with its own cells fails its own arithmetic rather than reporting a plausible
  total. All four cells are reported on every answer, occurred or not -- `ScoreCensus`' rule that
  "nobody was `below_board_minimum`" and "nothing looked" are different claims -- while the named
  list is bounded by `MAX_NAMED_UNTRADEABLE` with `untradeable_not_named` carrying the residual,
  so a body cannot scale with the market (`V2-P4-110`'s 13.18 MiB lesson). The
  `tradable_ratio_below_floor` refusal now names the rules, the first securities under each and
  the commonest policy sentence, and the terminal grew an `untradeable` line beside `unscored`.
  Driven through `CliRunner` and `TestClient` on a panel where one name is limit-up and one is
  over budget, because a test importing `shortlist_view` would have been green throughout.
- **`failed` and `interrupted` runs resolved evidence and cleared a `1.0` floor** (`V2-P4-075`).
  A `RunManifest(status="failed")` stored under an address, with evidence filed against it,
  answered `exit 0, researched_ratio=1.0, is_blocked=False` under `--min-researched-ratio 1.0` --
  while the refusal that floor raises described the ratio as "a fact about which runs finished".
  `stored_run_manifest_ids` was *literally* true throughout (the deployment did hold those runs),
  so what was corrected is the thing built on it: it now returns `held` and `finished` apart, the
  evidence join resolves against `FINISHED_RUN_STATUSES`, and the two ways an address fails to
  resolve are reported apart -- `evidence_without_a_stored_run` for a run nobody made and
  `evidence_from_an_unfinished_run` for one that broke, because the remedies differ. The
  quantifier is over the **address**, not over a row: `status` is in
  `RUN_MANIFEST_UNADDRESSED_FIELDS`, so an interrupted run and its successful re-run are one
  declaration at one address.
- **The adjustment corpus can now say how far a read looked** (`V2-P4-086`, first of its two
  edits). `build_adjustment_history` and `adjustment_histories_from_panel_rows` take
  `answerable_through`, `statement_histories_from_panel_rows`' shape one dataset over;
  `covered_through` returns it when given and `observed_through` is the newest observation, kept
  apart because on a step function "no row after D" means the factor did not move, never that the
  series stopped. **The second edit is now measured rather than argued, and one half of
  `V2-P4-079`'s reasoning did not survive the measurement.** Moving `load_adjustment_histories`
  onto the row-filtered door *does* work for the census -- `adj_factor` is
  `ClockStrategy.daily_close`, the visible slice and the per-date census agree with no subject
  axis anywhere, and `panel doctor` answered at an earlier instant instead of losing
  `unpriced_explained` and `return_paths` to `not_yet_knowable`. What binds is the per-security
  half, and it is now reproducible from a shipped report: with a read-level horizon in place the
  `adjustment.factor_series_stops_inside_the_window` shape went from provoking
  `return_path_disagreement` to provoking nothing, i.e. a series that genuinely ended started
  being answered by a factor carried across a window it never covered. The move was therefore
  reverted, a frontier rule was checked and rejected for failing the ordinary "quiet since the
  opening anchor" case, and the measurement is pinned by
  `test_a_horizon_the_read_declares_cannot_carry_the_per_security_half` rather than left as prose.

- **The web contract-drift guard stopped guarding the run manifest, and `pnpm test` has been red
  in CI since 2026-08-20** (`V2-P5-025`). `V2-P4-010` (`9f68d65`) renamed
  `docs/api/schemas/run-manifest-v2.json` to `run-manifest-v3.json` when it gave the manifest its
  three component planes, and touched no file under `web/`. The `ResearchResult.manifest` spec in
  `web/src/typesContractDrift.test.ts` still named the v2 file, so `readSchema` threw `ENOENT`
  before `findFieldDrift` ever ran (`1 failed | 51 passed (52)`) and the manifest mirror had *no*
  drift protection at all for five days. Attribution correction: `V2-P4-001`/`V2-P4-025`
  (`5b3383f`) did **not** cause this -- that commit updated `web/src/types.ts` and the drift test
  in step (v1 -> v2). The sync was a manual habit rather than a test, and it lapsed on the next
  re-version. The spec now names `run-manifest-v3.json`, against which the measured drift is
  **zero**: the mirror declares only `run_id` and `status`, both unchanged across v2 -> v3, and
  `findFieldDrift` is one-directional by design (`schemaDrift.ts:277`), so the sixteen properties
  v3 has that the mirror lacks are never inspected. The mirror was therefore not lying and was
  not expanded -- declaring a subset is what that module explicitly permits. What the `ENOENT`
  hid was the guard's own liveness, so a new test requires every `schemaFile` in `DRIFT_CHECKS`
  to exist **and** to still declare the version its filename claims; the second half catches what
  a rename cannot, where a stale-but-present document loads cleanly and drift-checks against the
  wrong contract silently. All five checked-in schemas were re-verified: every filename matches
  its own `schema_version` const, and no second stale reference remains in `DRIFT_CHECKS`. Also
  corrects `domain/run_mode.py`'s prose, which pointed `mode`'s full enum at the deleted
  `run-manifest-v2.json`.

- **The write-time session census stopped one session short of what every other layer required**
  (`V2-P4-114`). `panel_ingest._session_census` bounded a partition at `fetched_at - 1 day` and
  justified it with the 16:30 publication rule -- which is that rule only *below* 16:30, and is
  the sentence `V2-P4-063` deleted from `cli._build_sessions` and left standing here. Above 16:30
  the two came apart by exactly one session: the build loop fetched through `D`,
  `_price_requirement` and `panel doctor` required `D`, and the write-time refusal stopped at
  `D-1`, so a partition that had lost precisely the newest session was **accepted at write time**
  and refused by the reader afterwards. Measured on a weekday-open January 2026 calendar at
  `fetched_at=2026-01-20T17:00+08` holding every session but 2026-01-20:
  `_sessions_published_through` answered `2026-01-20` and the census answered
  `([], 2026-01-01, 2026-01-19)`. The bound is now that same function rather than arithmetic of
  its own, so the three layers are one set by construction. Of the eight panel/CLI files, two
  assertions moved and **both were rendered date literals rather than behaviour** (`2026-08-07`
  to `2026-08-08`); the missing session `2026-06-12` is found under either rule. `cli.panel_build`'s
  claim that the two layers apply "the same rule" is true again, and now carried by a shared name.
  A mutation sweep over the two functions' executable lines, with the baseline proven green
  first, ran **9 mutants, 8 killed**. One of the kills is new: `>=` to `>` on
  `DAILY_AVAILABILITY_TIME` had survived, and is invisible at every instant of the day except
  exactly 16:30 -- the side the constant's own wording decides -- so the test now drives that
  instant too. The single survivor, `closes_on < opens_on` to `<=`, is **measured unreachable**
  rather than relabelled: separating the two would need a year-2026 batch fetched
  2026-01-01T16:30+08 that is *missing* 2026-01-01, and `ColumnarPanelBatch` refuses any row
  whose availability is after the fetch, so such a batch can carry only the 2026-01-01 row it
  is supposed to be missing.

- **A registry read pinned at the last prediction instant is look-ahead, and nothing measured it**
  (`V2-P4-113`). `V2-P4-064` recorded that `factor build`'s per-instant registry read "fails in
  both directions". Remeasured on `037ffa8`, mutating only that read's `as_of`: pinning at
  `as_ofs[0]` is red but by *refusal* rather than by a count, and pinning at `as_ofs[-1]` is
  **green over the whole file** (`36 passed`) and answers `[8, 7]` -- the correct answer. The late
  read is the look-ahead direction and it was unguarded. `universe_counts` cannot see it on **any**
  fixture: `stock_basic` is `calendar_static`, so a lifecycle row is visible exactly when its date
  is at or before the reading day, which is exactly when it can change `listed_on` for that day.
  What a late read does move is `subjects`, taken from `universe.securities`, which is not
  date-filtered -- so a new test gives a registry-only security a listing and a termination that
  both fall *between* the two instants, and the late-pinned read hands the earlier cross section a
  name that had not listed yet (`not_in_universe` 2 instead of 1). The `V2-P4-064` roadmap row and
  the citing docstrings in `factor_view._computed` and `test_factor_build.py` are corrected.

- **`openalpha factor run` never named the command that builds a factor it could not read**
  (`V2-P4-067(b)`). The row was closed on a fix that landed in `shortlist_view.py`; its own
  reproduction command goes through `factor_view.py`, which was untouched, so all three tiers on
  the factor face refused with `the raw reversal_1d/v1 observations could not be read out of
  <path>: factor_obs_reversal_1d_v1 year=2026 cannot be read at …: ['partition_missing',
  'field_missing']` and no remedy anywhere in it. All three tiers on **both** faces now carry
  `openalpha factor build --factor <key> --tier <tier> --year <year>`, on the CLI, the REST body
  and the SDK, with the store path still absent from the disclosable half.
  **The raw-only boundary was retired rather than narrowed, because its stated reason was measured
  false.** It said `neutralized` has "two partition spellings depending on the declared
  neutralization (`factor_neut_*` and `factor_neutmn_*`)"; `neutralized_factor_dataset` takes the
  *definition* and no neutralisation at all, `factor_neutmn_*` is the manifest dataset — the
  structural twin of `factor_procmn_*`, which the same paragraph did not treat as making
  `processed` ambiguous — and `load_neutralized_factor_observations` says "the neutralisation is a
  filter here and the factor is the dataset". Residuals written under one neutralisation are read
  back by a request naming another, and no rebuild is suggested. Two further measurements: the
  `tier != "raw"` guard that carried the whole justification was **unreachable** (its one call
  site passed the literal `tier="raw"` from inside `if tier == "raw":`), and the test that pinned
  the boundary **could not separate the two answers** — driven without `--transform`,
  `shortlist run --tier processed` exits `3` at request time, so `assert "openalpha factor build"
  not in result.stderr` was asserted against a sentence that could never contain it.
  `KNOWN_SHORTLIST_VIEW_LIMITATIONS.only_the_raw_tiers_…` is removed (8 → 7) and
  `KNOWN_FACTOR_RUN_LIMITATIONS.the_unbuilt_factor_remedy_fires_only_when_no_year_of_the_tier_is_registered`
  records the boundary that survives (8 → 9): the remedy fires on "no year of this tier is
  registered" and on nothing else.
- **A `422` no longer answers with a copy of the request** (`V2-P4-043`'s own fix reproducing
  `V2-P4-040`'s shape). `max_length=MAX_BATCH_ITEMS` on `ScreeningApiRequest.research` made an
  over-count a `422` naming the number, and pydantic's `too_long` error carries `input` — the
  whole rejected collection. Measured: `POST /api/v1/screen` with 10,001 records (14,771,528
  bytes in) replied **13,821,594 bytes**, and `POST /api/v1/research/batches` with 10,001
  requests replied **9,261,138 bytes** — the same defect on a second route, which the row
  neither named nor measured. The sharpest case is not a ceiling at all: a misspelled top-level
  key is two errors, each echoing the body, so at *200* records the refusal was **1.87×** the
  request. A ceiling this service declares now answers with the `{"reason", "message", "field",
  "limit", "received"}` object `V2-P4-041` built for the same route in the same commit, and every
  other `422` keeps FastAPI's list with each `input` elided past `MAX_ECHOED_INPUT_BYTES` (512)
  and the list truncated past `MAX_VALIDATION_ERRORS` (20) with a final entry saying how many
  were dropped. `V2-P4-051`'s documented shape discriminator is unchanged — every entry still
  carries a `loc` — and `docs/api/http.md` gains the fourth row and both rules. A ceiling fault
  reported *alongside* per-item faults is derivative (a body whose items all fail leaves nothing
  and trips `min_length`), measured as `Counter({'missing': 25, 'too_short': 1})`, so the item
  faults answer in that case.
- **An agent written against an older `ResearchAgent` crashed instead of being refused**
  (`V2-P4-008`/`V2-P4-010`). Both rows added a required attribute to a Protocol whose own
  docstring calls it an extension contract, and neither installed a check.
  `runtime/router.py:222` read `agent.feature_dependencies` unguarded — one line above
  `UndeclaredAgentDependencyError`, the named refusal built for this class of failure — so a
  third-party agent got `AttributeError: 'LegacyAgent' object has no attribute
  'feature_dependencies'`. **`provenance` was in the same state and worse**: its docstring claims
  an agent omitting it "fails structurally at the point it is handed to the engine", and there
  was no such check — `ResearchEngine._pair` reads it inside a dict comprehension *after* every
  selected agent has run, measured as `AttributeError` at `engine.py:223` **with a recovery row
  already on disk**. `REQUIRED_AGENT_DECLARATIONS` and `MissingAgentDeclarationError` are the
  check the contract advertised, at the router, over the whole roster and before any agent runs,
  naming the agent, the missing attributes and what to declare instead. A `TypeError` and
  deliberately not a subclass of `UndeclaredAgentDependencyError`: "this object does not
  implement the current contract" and "this agent can never be satisfied by any run" have
  different remedies. Nothing is defaulted — the guess `feature_dependencies` would need is
  indistinguishable from the misdeclaration the sibling refusal exists to name.
- **`factor build`'s residual refusal named three remedies, two of which could not work**
  (`V2-P4-109`). A Saturday and a session hours before its own close both exit `1` and both got
  the same sentence — "move `--as-of` … or fetch the later sessions first" — of which nothing
  helps a day the exchange is never going to open. `RESIDUAL_REMEDIES` is keyed by
  `CalendarDayStatus`, which is three-valued for exactly this reason, and the calendar is already
  loaded before the read that raises. The **exit code is deliberately not split**, recorded as
  `KNOWN_FACTOR_RUN_LIMITATIONS.a_closed_day_and_an_unclosed_session_share_one_exit_code`
  (9 → 10): `bad_request` means "no amount of re-fetching fixes it", and a day the loaded
  calendar reports `closed` can also be a day whose `trade_cal` partition is merely short, for
  which re-fetching *is* the remedy.
- **`V2-P4-108`'s roadmap row recorded the wrong exit code.** It said the fix yields `3`; it
  yields `1` (`FACTOR_EXIT["blocked"]` is `PanelExit.unhealthy`, and `factor build --help` already
  said "1 when the panel could not answer"). The envelope name was right and the number beside it
  was not, which is what a reader would have written `if [ $? -eq 3 ]` against.
- **Importing the API module wrote to the user's runtime directory, and the backups never
  stopped** (`V2-P4-111`). `app = create_app()` at module scope meant a bare `import
  openalpha_cn.api.app` ran migrations and wrote a ~139 KB backup, in a function whose docstring
  makes a point of that line being filesystem-free with respect to `.env`. **The growth had a
  second cause, and it is the one that mattered**: `run_migrations` took the backup *before* the
  loop, so a migration that raises `MigrationNotYetApplicable` cost a full copy and applied
  nothing — on every process start, with no terminating condition. Measured against a copy of a
  real `state.sqlite3` stuck at `user_version=4` (its history predates `create_validation_results`,
  so `_rewrite_contract_identities` has no `validation_results` table to alter): three runs,
  three backups, nothing applied. That is 125 of the 128 files in that repository's
  `runtime/backups/`. `app` is now built on first attribute access (PEP 562), so
  `uvicorn openalpha_cn.api.app:app` and `from … import app` are unchanged while a bare import
  touches nothing; a run that applies nothing removes the backup it took, which is provably its
  own (`O_CREAT | O_EXCL`) and provably redundant; and a failed migration still keeps the copy
  `MigrationFailedError` points at. **No existing backup was deleted** — `openalpha migrate
  prune-backups --keep N [--dry-run]` is the cleanup path, chosen over an automatic retention cap
  precisely because a cap would have removed a user's existing data on their next command.
- **`--config-digest` is refused when the request is read, not after the store is** (`V2-P4-065`).
  `shortlist_request` had checked `code_commit` at request time since `V2-P4-046`, and the comment
  above that check says exactly why: `build_ranking_manifest` "raises the same objection after a
  store has already been read and would therefore report a mistyped flag as a fact about the
  panel". `config_digest` was left on that later path, so a mistyped one came back as *the
  shortlist could not be joined to the evidence this request supplied* — naming an evidence join
  for a request that supplied no evidence, quoting the internal `CandidateRankingManifest`, and
  only after a whole panel read. The separator in the test is an **empty** store rather than a
  built one: a request-time refusal cannot depend on a panel, so before the fix the empty store
  answered first (`['partition_missing', 'field_missing']`, exit 1) and the digest was never
  examined; after it, the digest answers first (exit 3) and no partition is opened. A built panel
  would have made both orderings refuse and the test could not have told them apart. Six bad
  inputs are refused by name on all three faces, and a seventh well-formed one must still reach
  the panel — that row is what separates a real check from one that refuses everything.
  Adjacent, and fixed with it: `code_commit` was checked only for `< 7` while the contract is
  `min_length=7, max_length=64`, so a 65-character commit still failed late in the same wrong
  place.
- **A factor read that cannot open a partition now names the command that builds it**
  (`V2-P4-067`). The prefix half was already right at `be262ea` — repaired in passing by
  `V2-P4-032`/`049` with nothing pinning it, so an audit was added and proved red by writing
  `rmf_` back into the real file. The second half corrected the row's own diagnosis: `_read`'s
  docstring claimed the factor-tier reads "already refuse with `openalpha factor build ...` (see
  `_resolve_instant`)". `_resolve_instant` refuses a read that *succeeds and returns nothing*;
  an empty store reaches the read that *raises* first, and that refusal named no command at all.
  The exemption covered the class and left the instance uncovered. `_unbuilt_factor_remedy`
  copies `_unbuilt_dataset_remedy`'s boundary — it fires only when no year of that factor's
  partition is registered — and is **raw-only on purpose**: `neutralized` has two partition
  spellings (`factor_neut_*`, `factor_neutmn_*`), so asking `registered_years` about the wrong
  one would answer "nothing is stored" for a panel holding the other and hand back a rebuild the
  caller does not need. Both directions are asserted, and the asymmetry is recorded rather than
  left in prose.

- **Recovery wrote every completed result again after every agent** (`V2-P4-020`).
  `ResearchEngine` saved the whole accumulated `RunRecoveryState` once per agent, and each save
  serialised it twice over — `_updated_recovery` round-tripped it through `model_dump` and
  `model_validate` before `SQLiteRecoveryStore.save`'s own `model_dump_json` ran — so persisting
  `N` results cost `N(N+1)/2` serialisations. Measured on `be262ea` with empty agents: 12 agents
  cost the roadmap's 78; 200 cost 20,100 and 11.74 MB of JSON; 400 cost 80,200 and 46.68 MB, of
  which the dump-and-revalidate half alone was 0.327 s. A run's graph now lives in
  `run_recovery_results`, one row per agent **slot** carrying the `agent_id` the graph declares
  there and a `payload` that is `NULL` until that agent completes, and `RecoveryStore` gained
  `append_result` — one `UPDATE` on the primary key, guarded by `agent_id = ?` and
  `payload IS NULL`, so a result written into the wrong slot or over a finished one is refused by
  name rather than found later as a state that no longer validates. `agent_ids`,
  `completed_results` and `next_agent_index` are derived from those rows on read, which makes
  `validate_progress`' prefix invariant true by construction. After: one serialisation per result
  at every size — 0.23 MB at `N=400` against 46.68 MB — and the loop's wall clock flat per agent.
  **No migration, deliberately**: a row written before the split carries `completed_results`
  inside its payload and is read whole by the key's presence, and `append_result` converts it in
  place the first time it finds no slot to claim. `storage/migrations.py`'s
  `_refuse_uncountable_stored_horizons` now reads both tables, because the recovery plane is the
  only place a whole `SignalFrame` is stored and it had moved.
- **A whole-market shortlist was blocked by the shortlist ceiling, not by the batch it restates**
  (`V2-P4-031`). `V2-P4-019` raised `MAX_BATCH_ITEMS` from 1,000 to 10,000 so a 5,545-security
  market could be expressed, could not touch `backtest/`, and left `MAXIMUM_SHORTLIST` at 1,000
  with its test weakened from `==` to `<=` — an assertion true of every number from 1 to 10,000,
  so nothing was left saying the wall had moved. The two are equal again and the assertion is an
  equality. **The concern that this would make an unreachable path look reachable is measured and
  does not hold**: `V2-P4-043`'s 8 MB wall is on `POST /api/v1/screen`, which carries
  already-researched results inline, while `POST /api/v1/shortlists/run` names a stored cross
  section — 450 bytes at `shortlist_size=1` and **454 at 10,000**. The answer grows and the
  request does not: 53 bytes per shortlist entry and 191 per admitted candidate on the fixture
  panel, so the new ceiling extrapolates to roughly 2.4 MB.
- **Reading an N-year history assessed readiness N times over N partitions** (`V2-P4-069`).
  `read_if_ready` and `read_visible_at` each judge the *whole* requirement and then read *one*
  year, so a full-history read paid N² catalog round trips for a verdict identical all N times.
  Reproduced on a store of 20 securities per partition, which is what says the cost is the
  catalog and not the data: 36 partitions cost **1,296** `_read_coverage` calls and **4.087 s** —
  the same 1,296 and 4.0 s `V2-P4-059` profiled on the real 5,545-security market, where the
  Parquet the read actually wanted was 0.21 s of it. `PanelStore.assessed()` takes the verdict
  once and hands back the per-year reads it licenses; the seven `panel_ingest` loaders that walk
  years take it. After: 36 partitions cost 36 lookups and 0.727 s, and 72 cost 72 and 1.256 s —
  linear, not a smaller constant. `read_if_ready` and `read_visible_at` are unchanged for their
  fourteen callers, being one line each on top of it. **Two docstrings that called this
  "milliseconds" are corrected with numbers**: `load_stock_universe`'s, and `load_daily_bars`',
  where a caller walking a year of 244 sessions spends **5.367 s** re-assessing (22 ms a call)
  against 3.025 s of the `query` calls it wanted — that one is linear rather than quadratic and
  is left standing with a measurement on it. New `KNOWN_STORAGE_LIMITATIONS` entry
  `an_assessed_read_scope_checks_each_partition_file_once_and_not_once_per_read` records what a
  scope gives up.
- **The allowlist whose purpose is reviewing `read_visible_at` callers could not see a new one**
  (`V2-P4-074`). `FILTERED_READ_CALLERS` is scoped to a *file*, so once `panel_ingest.py` was
  granted, every later caller written in it arrived without a line moving — which is not what
  "adding a name here is a deliberate act with a review attached" describes. `V2-P4-061` added
  `load_daily_bars` and `load_price_limits`, `V2-P4-083` added `load_statement_histories`, and
  none of the three tripped it. `FILTERED_READ_REACHERS` is the finer table and the file-level
  allowlist is derived from it. **The row's own framing is corrected by measurement**: those
  issues added no `read_visible_at` *call sites* — `panel_ingest.py` has had exactly two since
  `V2-P4-027` and still has two — they added *reachers* of an existing private helper, so the
  call-site granularity the row's acceptance line offers first would have stayed silent through
  `V2-P4-061` as well. The audit follows intra-module calls instead, and running it surfaced the
  third unreviewed reacher the row does not mention. All three are legitimate and each carries
  its own measured justification; what was missing was the review, not the argument.

  Verified across the four rows by a **52-mutant sweep, 41 killed / 11 survived**, with the
  baseline proven green on all four target suites before a single mutant was generated. Five
  survivors landed on docstring prose and are not mutants at all (recorded rather than removed
  from the denominator); one is the ordering of years inside an error message; one is
  `read_visible_at`'s `pooled_years` condition, moved verbatim by this branch rather than
  written by it. The remaining four were all the same defect this project books most often --
  an assertion that exists but cannot separate the two answers on its fixture -- and all four
  are now killed. Three mutants pinning the scan to `requirement.years[0]` survived because
  every partition in the cost fixture held identical values, so "read year `k`" and "read year 0
  `N` times" returned the same row *count*; the fixture now makes each partition's values name
  its own year, and a fourth test drives the two un-scoped public doors on a multi-year
  requirement, which nothing else did. The fourth replaced `assessed.read_visible_at(...)` with
  `store.read_visible_at(filtered, ...)` inside `_read_visible_event_dated_rows` -- semantically
  identical, byte-identical answer, and the N-squared quietly back for the very caller
  `V2-P4-069` was filed about. No assertion about the answer can catch that, so the audit is
  structural: a function that opens a readiness scope may not also take a per-call door on the
  same store, discriminated by the *receiver* because `assessed.read_visible_at` and
  `store.read_visible_at` share an attribute name.
- **`max_concurrency` was narrowed from 32 to 8 and no user-facing document said so**
  (`V2-P4-042`). `V2-P4-019` lowered `MAX_BATCH_WORKERS` on `POST /api/v1/research/batches`, so a
  request that worked the day before answered `422 Input should be less than or equal to 8` —
  and `grep -rn max_concurrency docs README.md README.en.md web CHANGELOG.md` returned **zero
  hits**, so nowhere a caller looks explained it. The reasoning existed all along, in
  `batch_contracts.py`'s docstring, with the measured 1/2/4/8/16/32 throughput plateau behind it;
  a source comment is not documentation. `docs/api/http.md` now carries the ceiling, the
  measurement table, and the fact that `8` is a property of how batch state is persisted rather
  than a throttle that can be turned back up — and this entry is the release note the narrowing
  should have shipped with. **Breaking-change note for callers still on 32:** nothing about the
  behaviour changed here, only its documentation.
- **`GET /api/v1/research/batches` inlined every item of every batch** (`V2-P4-040`). Twenty
  whole-market batches — about a trading month — answered `items: 115,355, bytes: 36,857,096`
  (36.9 MB) in 2.35 s, and three batches already exceeded the 8 MiB body this same service
  refuses on the way *in*: a listing that had become a bulk export, because `V2-P4-019` raised
  the item ceiling tenfold and the route stayed `return batch_store.list()`. It now answers a
  paginated envelope of **summaries** — `batch_id`, status, the two clocks,
  `cancellation_requested`, `item_count` and a per-status census — with `limit` (default 50, max
  500) and `offset`, and a `total` for the whole shelf. The counting is a `GROUP BY` inside
  SQLite rather than 115,355 items through pydantic. **This is a response-shape change**: the
  route returned a bare JSON array of full `BatchResearchTask` objects and now returns
  `{"batches": [...], "total": n, "limit": n, "offset": n}` with no `items` key per batch. The
  items moved to `GET /api/v1/research/batches/{batch_id}`, which is unchanged. Nothing in this
  repository consumed the listing — no test, no SDK method, no page under `web/` — which is also
  why the defect shipped. No stored contract moved and no migration is involved.
- **The batch ceiling and the request-body ceiling contradicted each other** (`V2-P4-043`). A
  whole-market screen of 5,545 names is 8,190,016 bytes and answered `200`; 6,000 names is
  8,862,051 and answered `413`, against an 8 MiB `OPENALPHA_MAX_REQUEST_BYTES` default — 198,592
  bytes of headroom, about 134 more listings. Measured here and worse than the report: a batch at
  exactly `MAX_BATCH_ITEMS` (10,000, raised by `V2-P4-019` *because* "the market is a moving
  number") is 9,840,054 bytes and was refused `413`, so the ceiling this service declares was
  **unreachable through the only surface that can express it** — no test caught it because every
  test at that scale builds the task in process instead of posting it. The default is now
  33554432 bytes (32 MiB), which clears both declared ceilings with a factor of two for the
  richer evidence real callers send; a body over the ceiling is still refused before it is read.
  The `413` now carries `{"reason": "request_too_large", ...}` naming
  `OPENALPHA_MAX_REQUEST_BYTES`, the declared size and the configured limit, and
  `POST /api/v1/screen` states its own 10,000-item ceiling so one name too far is a `422` naming
  the number rather than a `413` about bytes.
- **`POST /api/v1/screen`'s 422 collapsed three distinct causes into one sentence**
  (`V2-P4-041`). `_parse_research_result` distinguishes `signal_id`, `decision_id` and
  `run_manifest_id` each failing to match its own content, and all three came back as
  `Research result failed integrity validation.` — so a caller holding 5,545 results learned
  neither which record nor which of the three addresses had moved. The refusal now carries the
  `{"reason", "message"}` object the panel gate's `409` established, plus `index`, `subject`,
  `field`, and both the `claimed` and the `derived` address, which is the difference between an
  edited record and an edited identifier. `malformed_research_result` is a fourth, separate
  reason. `POST /api/v1/reports` and `POST /api/v1/backtests/validate` share the fix.
- **The HTTP reference's content-address examples were pinned to the minting function**
  (`V2-P4-067`(a)). The documented `run_manifest_id` prefix was `rmf_` where this repository
  mints `run_`, so a caller copying the example was refused; it was repaired in passing by
  `V2-P4-032`/`V2-P4-049` and nothing held it there. Every `<prefix>_…` example in
  `docs/api/http.md` is now checked against the AST-read prefix census, and the audit is itself
  proved to fail on the exact text the row measured.
- **`openalpha panel build --as-of T` produced a panel that `openalpha panel doctor --as-of T`
  called `BLOCKING`** (`V2-P4-063`). `cli._build_sessions` bounded the fetch loop at the fetch
  clock's Asia/Shanghai date **minus one day**, unconditionally. That is
  `panel_ingest._sessions_published_through` only for the part of the day *before* 16:30
  (`DAILY_AVAILABILITY_TIME`); above it the two came apart by exactly one session, and that
  session is the one the rest of the price plane already agreed about — `_price_requirement`
  clamps a dataset's `required_dates` at it, so a health check **required** it;
  `_read_visible_price_session` refuses only what is past it, so a read would have **served** it;
  and `newest_published_session` resolves a shortlist's pricing session through it, so
  `shortlist run` **priced** against it. Three rules against one, and the one was the build.
  Measured through `CliRunner` at one instant used twice: build exit `0`, eleven sessions ending
  2026-01-19; doctor at that same literal instant exit `1`, `blocking date_gap 1 required date(s)
  are absent from stk_limit, starting at 2026-01-20`. The loop now shares
  `_sessions_published_through` rather than restating it, and the bound is
  `min(date(year, 12, 31), published_through)` — `_price_requirement`'s own expression — so what a
  build fetches and what a health check requires are the same set by construction.
- **`openalpha factor run` and `openalpha factor build` published artifacts stamped with a commit
  the caller never declared** (`V2-P4-052`, `V2-P4-046`'s defect on two more commands). Both
  declared `--code-commit` with an empty-string default and then wrote
  `_resolved_code_commit(code_commit or None)`, so there was no value the parser could hand back
  that meant "the caller typed an empty one": `""` collapsed into *omitted* and resolved from the
  server's git, while the same literal reached the request contract's seven-character rule on the
  SDK and over HTTP and was a `bad_request`. Measured: `factor run --code-commit ""` exited `0`
  having **sealed** an experiment, and `factor build --code-commit ""` exited `0` having written
  four partitions — and `code_commit` is inside every observation's build column, so the mis-stamp
  outlives the command. Both flags now default to `None`; omitting them still resolves the real
  commit, which is driven separately on each command.
- **`--max-staleness-days` refused a factor build on a price panel one day old** (`V2-P4-064`).
  The flag is a *session* bound — its own refusal says so, "a price panel whose newest session is
  a month old has missed a month of the market" — and it was applied unchanged to the security
  registry, which is event-driven: `stock_basic`'s newest instant is the last time a security
  listed or delisted, so its age measures the market's corporate-action calendar rather than this
  fetch. The only way to run the command was to widen the bar to 20–25 days, which switches off
  the check it exists for. `panel doctor` already answers this correctly through
  `DATASET_CADENCE`, and `factor_view.CADENCE_WAIVED_READS` is now held against that table — a
  strict containment plus a literal complement, so a sixth `event_driven` dataset turns it red
  naming itself. What is **not** waived is recorded rather than left to be discovered: the four
  quarterly statement datasets keep the caller's bar because `compute_factor` refuses a waived
  one for every dataset a factor reads, and `index_member_all` keeps it because
  `load_industry_market_cap_cross_section` states one bound for it and `daily_basic` together.
  Both are `KNOWN_FACTOR_RUN_LIMITATIONS
  .the_freshness_bar_is_waived_by_cadence_only_where_the_read_is_outside_the_engine`.
  Two existing guards turned out to be resting on the defect and were re-grounded rather than
  relaxed: the test that proves the registry is read once per *prediction instant* separated the
  two instants by this bound, and now separates them by a delisting whose `available_time` falls
  between them — `universe_counts` reads `[8, 7]`, against `[8, 8]` for a read pinned at the first
  instant and `[7, 7]` for one pinned at the last, so it fails in both directions where the bound
  failed in one. And the sweep that requires every declared build parameter to reach the answer
  had this flag reaching it only by refusing the registry; it now drives the flag at the one
  instant in the fixture window where a session bound can decide anything — the Saturday after the
  newest session, where `1` is `stale` and `2` builds.
- **`openalpha factor build --tier neutralized --as-of <a day the exchange was shut>` exited `5`
  with a withheld traceback instead of a verdict** (`V2-P4-108`, found by the same acceptance and
  pre-existing). `_neutralized` catches `_PANEL_FAULTS` around
  `load_industry_market_cap_cross_section`, and `PriceDataError` — which is what
  `_read_visible_price_session` raises for a non-session day, and which
  `cli._PANEL_WRITE_REFUSALS` and `panel_doctor._LOAD_FAILURES` have both called a fact about data
  for eleven error types — was not in it. So a refusal designed to be an answer reached
  `cli._panel_command` as an unanticipated exception: "a defect in the command, not a verdict
  about the panel — nothing was checked", with the refusal's own sentence withheld because an
  unanticipated frame can be holding the credential. `V2-P4-060`'s shape, one refusal over. Fixed
  at the read rather than in the constant, which is that issue's own arrangement: `_PANEL_FAULTS`
  is restated by `shortlist_view` and pinned as a union across both faces' read seams, and the
  registry read cannot raise this at all. Measured at the same instant: `--tier raw` and
  `--tier processed` both exit `0`, so the residual is the whole of the hole.
- **`openalpha panel doctor --dataset index_daily --no-calendar` had a fix with no product-surface
  test under it** (`V2-P4-087`). The bare `KeyError` was closed when it was found, but the
  assertion beside it calls `panel_health_report` directly while the report is about a command
  line — everything between the two is unasserted. The literal command is now driven through
  `CliRunner`, and the test was checked to separate: removing `_PRICE_SHAPED_FIELDS`' `index_daily`
  row turns it from exit `1` (an empty store is unhealthy, which is the point of the fallback) to
  exit `5`.
- **An agent that declares a *feature* dependency was never routed, and nothing said so**
  (`V2-P4-008`, S38). `AgentRouter.route` was `agent.evidence_families & families`, so an agent
  whose whole dependency is a panel column -- and which therefore declares no evidence family --
  intersected the empty set and was dropped: no entry in `DecisionLedger.routing_path`, no
  `AgentVersion` in the manifest, no abstention. "This agent had nothing to say about this run"
  and "this agent can never say anything about any run" were one observation. `ResearchAgent`
  now declares `feature_dependencies` beside `evidence_families`, and routing satisfies **both**
  halves: a family is satisfied by *any* declared family being present (`ThemeAgent` scores
  whichever of its three arrived, so a partial arrival is a smaller sample), a column only when
  *every* declared one is on the plane (an agent's arithmetic names a column by `feature_id`,
  and a missing column is a missing term rather than a smaller sample). An agent declaring
  neither is refused by name with `UndeclaredAgentDependencyError` rather than dropped -- the
  fail-open answer is worse than it looks, because `SignalFrame` refuses every non-abstaining
  direction with no `evidence_ids`, so such an agent's only reachable output is an abstention
  that `_aggregate` would then average into the run. **This entry understated the breaking
  change and the ninth-wave acceptance measured it**: "an agent declaring an empty
  `evidence_families` is now refused by name" is not the same statement as "an agent that does
  not declare `feature_dependencies` at all crashes", which is what a third-party agent written
  before this row actually got. See `MissingAgentDeclarationError` under Fixed.
- **`AgentContext` had no handle for anything but evidence** (`V2-P4-009`, S36/S38). It now
  carries `features: FeaturePlane | None`, a `runtime_checkable` Protocol declared beside its
  consumer -- `ShortlistDocumentStore`'s and `ExperimentDocumentStore`'s arrangement -- which
  `domain/alpha_model.py::FeatureCrossSection` satisfies structurally with no adapter, so
  `agents/` gains no edge into `feature_matrix` and through it DuckDB. Under
  `arbitrary_types_allowed` the field is an `isinstance` check on method presence rather than a
  pydantic rebuild, which is Implementation Decision 31 on a ~5,500-row panel read; object
  identity is asserted, because equality passes on a rebuilt copy. **The row's proposal to reuse
  `tools/base.py::ResearchTool` was measured and declined**: `ToolRequest.kind` is
  `max_length=64` and the neutralized spelling of this build's longest factor key is **89
  characters** (refused with a `ValidationError`), and `ToolResult` has exactly three fields
  under `extra="forbid"`, none numeric, with `status="success"` requiring a non-empty
  `evidence_ids`. `agents/feature.py::FeatureScoreAgent` ships as the consumer, so the new seam
  is not a second declared-and-unused extension point. Reachable through
  `OpenAlphaSDK(features=...)`; the CLI and REST faces compose no plane, which is recorded
  rather than implied.
- **A cycle in which every routed agent abstained raised `ValidationError` out of `run_cycle`**.
  `_aggregate` computed `direction` from the mean strength before anything looked at
  `evidence_ids`, so an all-abstaining run built a `neutral` frame citing nothing and
  `SignalFrame.validate_conclusion` refused it. Measured on `be262ea` before `V2-P4-008` touched
  anything, with a deterministic agent returning an abstention -- so this predates the row that
  found it and only a `StructuredSignalAgent` whose model abstained could reach it before; a
  feature-dependent agent reaches it on any security the composed column has no number for. The
  repair is `V2-P4-029`'s, one module over: an abstention is the claim that the evidence supports
  no direction, and overruling it means minting a directional conclusion from a frame that cites
  nothing. The aggregate abstains, says **which** of the two reasons applied (nobody was routed,
  or everybody abstained), and carries the abstaining agents' `risk_flags` forward so a `block`
  does not become a `pass`.
- **"Run it, run it again tomorrow, and compare the two" ended at the comparing** (`V2-P4-007`,
  S44/S49). `openalpha shortlist get`'s own docstring describes that workflow and
  `tests/integration/test_shortlist_workflow.py` had to do the last step with a `set` difference
  written into the test. `openalpha shortlist compare <baseline> <current>` and
  `OpenAlphaSDK.compare_shortlists` now report added, removed and held names with each held
  name's rank change, score change and **changed reason** -- direction, risk flags, backing run,
  or a name that stopped being published at all. Both addresses are arguments and neither is
  inferred: `shortlist_id` is a content address and `list_ids` is ascending by sha256, so the
  store genuinely cannot say which answer came first
  (`the_stored_answer_is_addressed_by_content_and_not_by_when_it_was_run`), and a command that
  guessed would be inventing the ordering. Two answers to *different* questions are refused
  naming the key that differs, because a diff across two questions reports every name added and
  every name removed -- true about two lists and false about one market. `rank_change` and
  `score_change` both read "positive means the name moved up", and the sign is asserted against
  the pair it was derived from rather than against a literal.
  A mutation sweep over the two rows' code ran **341 mutants, 326 killed**; the fifteen
  survivors are seven provably equivalent (`Literal` members inside local-variable *type
  annotations*, `@dataclass(slots=True)`, `ensure_ascii` on an all-ASCII payload) and eight
  CLI presentation strings.
  **Two of those three "provably equivalent" examples were remeasured under `V2-P4-115` and
  neither claim held; see that row.** In short: the `Literal` one is killed by `mypy`, which
  this project ships as a gate, so it is a *sweep-tooling* survivor and not an equivalent
  mutant — a sweep whose oracle is pytest alone under-reports whenever a second gate is part
  of the build. The `ensure_ascii` one was equivalent **on the fixture only**, and there is now
  a test with a non-ASCII exchange that kills it. `@dataclass(slots=True)` is the one of the
  three that is genuinely equivalent.
  Two survivors were closed by **changing the design rather than
  adding an assertion**: `schema_version` was removed from `COMPARABLE_KEYS`, where it was
  dead because the shape is refused by name before the two answers are compared with each
  other. And the sweep found a real defect in the refusal it was probing -- `declaration`
  keys were compared with `.get(key)`, so a key **absent** on one side and `null` on the
  other compared equal and the refusal reported "these differ on `[]`", naming nothing.
  `declaration.neutralization` is rendered `null` on every answer this build produces, so
  that is the path an older stored answer takes. It now uses a sentinel.

- **A closed vocabulary with no way to refuse: an undeclared `quality_flags` string answered
  `500 text/plain` on `POST /api/v1/research/run`** (`V2-P4-101`). `V2-P4-030` closed the
  risk-flag set and was right to — a payload writing `future-data` instead of `future_data` used
  to be *scored*, and scored **above** the flag it misspells, so the typo moved its candidate up
  a governed screen. What it did not do is give the refusal a delivery. Measured on `d748796`
  with an evidence payload shaped `{"schema", "family", "facts", "quality_flags"}` (the first two
  are required or `MarketAgent` drops the item by family before this code sees it, and every
  assertion goes vacuously green): `['future_data']` → `200`; `['future-data']` and
  `['totally_made_up']` → **`500`, `content-type: text/plain`, body `Internal Server Error`**.
  `_quality_flags`' own docstring names five paths reachable from outside the process. **The
  fail-open is not restored**: the refusal is correct and only its delivery was wrong.
  `domain/risk_flag.py` now raises `UndeclaredRiskFlagError(ValueError)` carrying the offending
  string, the vocabulary, and — filled in by `_quality_flags`, the only frame that knows them —
  the offending snapshot's `evidence_id` and the flag's position. A **named** exception rather
  than `except ValueError` around the route, which would report an unrelated arithmetic or
  parsing defect as the caller's spelling mistake (the over-broad catch `V2-P4-045` booked on the
  shortlist face). The route answers the FastAPI field-error **list** — not the `{reason,
  message}` object a panel refusal carries, the two `422` schemas this app's docstring records —
  with `loc == ["body", "evidence", 1, "payload", "quality_flags", 1]`, the `input` echoed, and
  a `msg` byte-identical to the one pydantic already writes for `signal.risk_flags` on
  `POST /api/v1/research/deliberate`. `evidence_id` and not an index crosses the agent boundary
  because an agent sees only its own family's items, so an index taken there names the wrong
  item on any mixed-family request.
- **The same refusal was equally undeliverable on two more faces** (`V2-P4-102`).
  `openalpha research run` rendered a rich Python traceback and exited 1 — the *message* was
  already right and the presentation was a stack trace, which `create_app`'s own docstring rules
  out ("naming the specific variable, never a bare traceback"). It now prints the flag and the
  vocabulary on **stderr** and still exits 1: the finding is about presentation, and moving the
  code too would fail a CI job already branching on it for a second, unrelated reason. And
  `POST /api/v1/research/batches` degraded to `{"status":"failed","error_type":"ValueError"}` —
  no message, no flag name, no vocabulary, discarding exactly the diagnostic `parse_risk_flag`
  promises. `error_type` now names the specific subclass, and the whole reason goes into the
  `item_failed` progress event's `detail`, a free `str | None` already published by
  `GET /api/v1/research/batches/{batch_id}/events` — so nothing about a stored contract changed
  and no migration was needed (`BatchTaskItem` is `extra="forbid"`, where an added key is a
  breaking change). The default is still the type alone: `DISCLOSABLE_ITEM_FAULTS` is an
  allow-list, because an unanticipated exception carries whatever the frame it escaped was
  holding and a progress event is append-only and durable. **One claim in the report was
  falsified by measurement**: `POST /api/v1/backtests/replay` was named alongside the other two
  and was never broken — `ReplayRunner.run()` catches `(RuntimeError, ValueError)` per case and
  records `f"{case.run_id}: {type(error).__name__}: {error}"`, so it returned `200` with the
  offending string and all ten flags in `failures[0]` all along. It is the model the other three
  now copy, and it works because the new exception still subclasses `ValueError`; the test is
  kept as a regression guard on that base class rather than deleted.
- **`factor build --tier`'s option help kept a bound `V2-P4-028` had already retracted, and
  contradicted the same `--help` two paragraphs up** (`V2-P4-103`). The option said
  `--tier neutralized` "only succeeds at a prediction instant at or after the panel's own stored
  horizon"; the command's own docstring, in the same output, said that bound "IS GONE" and that
  what remains is one session wide. Measured before choosing: the command line **does** write the
  neutralised tier before the panel's horizon, so the help was the stale half and the code was
  right. The prose and a real build are now asserted in one test — a test that only greps
  `--help` proves the sentence changed, not that it is true, which is the exact failure this
  file exists for. (The report's "eight sessions before the panel's horizon" is eight *calendar*
  days; by sessions it is four and five.)
- **`--min-securities` documented a floor the face does not have, and refused with a pydantic
  model name instead of the flag** (`V2-P4-104`). The help said "the contract's own floor is 3";
  passing `3` got `1 validation error for RedundancySpec … Input should be greater than or equal
  to 4` and exit 3 — no occurrence of `--min-securities` anywhere in it. There is no single
  contract: `factor_request` hands the same integer to `FactorICSpec` (floor 3) and
  `RedundancySpec` (floor 4), and the higher binds. Measured before choosing: the **help** was
  wrong. Both floors are arithmetic — three points are the first cross section at which
  `|r| < 1` is attainable, and at `n = 3` an untied rank correlation is only `±0.5` or `±1`, so
  no `--redundancy-threshold` at or below 0.5 distinguishes anything and lowering the redundancy
  floor would make the survival row call every pair redundant. `factor_request` now refuses
  before constructing either spec, naming the option and both floors, so `openalpha factor run`
  and `POST /api/v1/factors/run` get it from the one shared resolver; previously whichever spec
  happened to be built first decided the message (`2` reported `FactorICSpec`, `3` reported
  `RedundancySpec`). The help interpolates the two constants rather than restating them.
- **The offline guard shadowed a class, not a surface; it is an audit hook now** (`V2-P4-105`).
  `tests/offline_guard.py` shadowed four names on `socket.socket` — the Python *wrapper*, which
  inherits every one of them from the C `_socket.socket` and defines none of its own. `import
  _socket` is one line, and from inside a non-e2e test under the autouse fixture, loopback only,
  three probes walked straight out: `_socket.socket` `connect`+`sendall` delivered
  `b'ESCAPED-TCP'`, `_socket.socket.sendto` returned 11 and the listener received
  `b'ESCAPED-UDP'`, and — needing no fresh class at all — a **guarded** socket's own `detach()`ed
  file descriptor re-wrapped in `_socket.socket` delivered `b'ESCAPED-DETACH'`. The test that was
  supposed to close the surface asserted over `vars(socket.socket)` and is structurally blind to
  all three. The row's preferred repair, widening the shadow onto the base class, was measured and
  **cannot be done**: `setattr(_socket.socket, "connect", …)` raises `TypeError: cannot set
  'connect' attribute of immutable type '_socket.socket'`, and the C class is reachable by too
  many spellings to hold by name (`__bases__[0]`, `__mro__[1]`, `type(sock).__mro__[1]`). So the
  guard moved *below* the class graph instead of across it: a PEP 578 audit hook on
  `socket.connect`, `socket.sendto` and `socket.sendmsg`, raised inside `_socket`'s own C code, so
  a caller reaches them whichever class object it got. Narrowing the claim to "outbound TCP"
  stayed refused for `V2-P4-039`'s reason. Three events and not four is a measurement: CPython
  raises `socket.connect` for `connect_ex` too, and there is no `socket.connect_ex` event. The
  price is stated rather than hidden — an audit hook can never be uninstalled, so `_depth` is what
  turns it on and an e2e test runs with it installed and inert; the compensation is that
  `socket.socket` is now never mutated at all, so the `delattr` a mutation could once skip does
  not exist. Overhead is below this suite's noise (`tests/unit` 33.58s with, 35.49s without). The
  closure argument for `send`/`sendall`/`sendfile` is now driven over the C class rather than
  asserted about a class dict: connect is refused, `sendall` fails as an unconnected socket fails,
  and the loopback listener receives nothing. DNS stays outside the guard and is **not** quietly
  swept in — a child interpreter measures that resolving a name raises the declared event and no
  guarded one — and one new limit is declared: code that reaches the kernel without passing
  through `_socket` (`ctypes.CDLL(None).connect(…)`) raises no event, which is the same class of
  deliberate evasion as a child process. Restoration is now observed end to end in a child
  process: refused inside the block, delivered after it.

- **The content-address audit disclosed one evasion where there were three** (`V2-P4-106`).
  `V2-P4-037` keyed on the literal `24` written at a slice and disclosed `hexdigest()[:_WIDTH]`.
  Two more were found, neither disclosed, each minting a valid `sgs_<24 hex>` and each violating
  all three canonicalisation keywords. Measured on that module alone: the control
  `sha256(c).hexdigest()[:24]` **2 failed**; `[:_WIDTH]` **39 passed**;
  `sha256(c).digest()[:12].hex()` **39 passed**; `blake2b(c, digest_size=12).hexdigest()`, which
  has no slice anywhere, **39 passed**. The third settles it — same hash function, same bytes, and
  it minted the byte-identical address the control did (`sgs_2d711642b726b04401627ca9`), so it is
  not a loophole with a different meaning but the same mint with one token moved. The extractor was
  widened rather than the disclosure: it no longer looks for a slice, for `24`, or for `sha256` by
  name, but finds **every `hashlib` constructor call under `src/`** and sorts each by whether its
  digest is *narrowed* below the algorithm's full width — a subscript on `digest()`/`hexdigest()`
  whatever is in the brackets, a length argument to either (`shake_128(…).hexdigest(12)`, a third
  spelling added as a probe), or `digest_size=`/`digest_length=`/`dklen=` on the constructor.
  `037`'s reason for not widening — that it would sweep in the plain 64-hex checksums — is answered
  by construction rather than by a skip list: those are declared in their own equality-pinned table
  and it is the narrowing *measurement*, not a name, that decides which table a site belongs to, so
  a mint parked among the checksums is red and a checksum that starts truncating is red. Two of the
  row's own numbers moved: "seven checksums" is right about calls and off by one about functions —
  seven full-width `hexdigest()` calls in **six** functions, because
  `ResearchEngine._load_or_start_recovery` hashes twice — and the whole tree measures **14 sites,
  15 calls, 8 mints + 6 checksum functions**. `DIGESTS_PER_SITE` records the one two-hash site and
  closes the direction two function-keyed tables cannot see: a second mint added *inside* a function
  that already hashes. The extractor carries its own test, run over source the module writes itself
  and `exec`s to prove each probe really does mint an address the live pattern accepts.

- **The threshold-2 risk-flag audit fell to one broken literal; half closed, half disclosed by
  name** (`V2-P4-107`). In `decisions/risk.py`, `frozenset({"future" "_data", "look_ahead" +
  "_violation"})` passed (**1 passed**): adjacent literals fold at parse time and *were* caught,
  explicit `+` is an `ast.BinOp` whose halves are each a non-name and was not — and the `blocked`
  band has exactly two members, so a regressing `_blocking_flags` only ever needed one literal
  broken to hide. All four spellings were reproduced: written out **1 failed**, implicit **1
  failed**, `+` **9 passed**, `"".join([…])` **9 passed**. `+` is folded now, and the reason is
  stated rather than dressed up as closing the class: it removes a difference that was an accident
  of where CPython folds constants, not a line anybody drew. The rest of the class is disclosed
  **specifically and executably** — `KNOWN_RUNTIME_ASSEMBLY_EVASION` holds the source, not prose,
  and a test drives all three spellings through the real extractor, requiring the first two to be
  seen and `"".join([…])` **not** to be; it goes red the day somebody closes the class, which is
  the right signal. It is deliberately not a `KNOWN_*` registry entry: all thirty-two registries
  are limitations of the shipped product declared in `src/`, and this is a limitation of a test —
  the precedent is `037`'s own disclosure, living in the module that owns the audit, except that
  this one is executable and so cannot rot into a sentence that used to be true.
  `REGISTRY_ENTRY_COUNTS` is untouched. The identical helper duplicated in
  `tests/unit/domain/test_run_mode.py` carried the identical hole and was fixed and covered too.
  Two more of this repository's sentences were falsified on the way: the helper's claim that
  counting docstrings "would make every one of those modules an offender" is false on this tree —
  the comparison is exact equality between a whole `ast.Constant` and a flag name, a docstring is
  one long constant that never equals `"future_data"`, and counting docstrings changes neither
  audit's answer on **any** module of `src/`; the filter is kept for the case that would match (a
  docstring that *is* a flag name) and now has a test that drives it, because it was unexercised
  code carrying a justification the tree does not support. And `DECLARATION_THRESHOLD` is lifted
  out of the comparison and named, because the threshold *is* all of `V2-P4-030` and nothing
  pinned it: `domain/risk_flag.py` spells all five names and satisfies any threshold at all, so a
  threshold that drifted upward left the suite green.

- **Nothing stopped a second content-address canonicalisation; now an AST audit does**
  (`V2-P4-037`). `domain/_identity.py` says every identity goes through `stable_model_id`, and
  that a second spelling of "canonical" would put two things in play whose difference is
  "invisible until two IDs disagreed" — with nothing enforcing it. The row's own probe turned
  out **not** to be green: rewriting `ShortlistGateManifest.gate_manifest_id` as its own
  `json.dumps` plus `sha256[:24]` moves that declaration's address (`sgt_6c3ec68a…` to
  `sgt_3248f195…`) and does go red, but on arithmetic and by accident — the prefix census counts
  *call sites* of the one function, so replacing one drops it from 27 to 26. Adding a mint
  instead of replacing a call moves that census not at all: a second computed field spelling its
  own `sgs_<24 hex>` left ruff, mypy (140 files), `lint-imports` (8 kept) and `tests/unit`
  (2813 passed) green. `tests/unit/domain/test_contract_identity.py` now reads every truncation
  to a content address's width off `src/` by AST, keyed by the function it sits in — per
  function and not per file, because `domain/factor.py` already holds two and a file-level
  allowlist would admit a third — with equality in both directions, and holds each mint's
  `json.dumps` keywords to `stable_model_id`'s. Two of this repository's own sentences were
  falsified doing it: the allowlist is **eight** mints where the row named five (it missed
  `cross_section_digest`, `stable_answer_digest` and `ParquetEvidenceStore.append`, whose
  `part-<24 hex>` is a file name the pattern rejects), and `_identity.py`'s "three builders" is
  **seven**, because `chr_`, `rkc_`, `sla_` and `ev_` all match `CONTENT_ADDRESS_PATTERN` too.
  What the audit cannot see is written beside it: it reads the literal `24` at the slice, so
  `hexdigest()[:_WIDTH]` would pass, and widening to every `sha256` call in `src/` would mix in
  seven plain 64-hex checksums that are a different question.
- **The `KNOWN_*` entry count is an equality per registry, and a code cannot recur across two
  registries unnoticed** (`V2-P4-038`). `sum(...) >= 301` is satisfied by any non-negative net
  change, and nothing asserted anything about a `code` across registries — which matters because
  the binding one section up tests membership in a set of every literal the *whole* suite
  evaluates, so a code carried by registry A is "bound" by a literal written about registry B,
  making a foreign code the cheapest possible filler for a hole. The row's probe is caught
  already, in `tests/integration` rather than `tests/unit`: 30 of the 32 registries carry a
  literal collection equal to their whole code set, measured by AST. The two that do not are
  where the hole lives, so the probe was rebuilt there — adding
  `KNOWN_CROSS_SECTION_LIMITATIONS`' own `the_cut_is_broken_by_subject_code_when_two_scores_tie`
  as a tenth `KNOWN_INDEX_MEMBERSHIP_LIMITATIONS` entry left `tests/unit` at 2816 passed and the
  seven integration and contract modules that touch a registry at 233 passed, with the total up
  from 301 to 303 under a floor of 301. The floor is now `REGISTRY_ENTRY_COUNTS`, one line per
  registry: per registry rather than one scalar because a scalar sees only the net, and because
  two siblings editing two different lines merge correctly where this module's own history
  records two siblings bumping one scalar to the same wrong value and git merging it silently.
  Cross-registry recurrence is a table of `code → the exact registries it lives in` rather than a
  bare "no code twice", because global uniqueness is **false today and rightly so**: three codes
  recur, in 4, 3 and 2 registries, each for a stated reason.
- **The offline guarantee covers UDP, not only TCP `connect`** (`V2-P4-039`). Reproduced: with
  the guard installed, `connect` on an `AF_INET` socket raised while `sendto` and `sendmsg` each
  returned 5 bytes — a wrapped set of `{connect, connect_ex}` and nothing else — and the guard
  refuses by family rather than by address, so a routable destination was no more refused than
  loopback. Wrapped rather than narrowing the claim to "outbound TCP", because narrowing makes
  the sentence true by making the guarantee smaller, which is the direction every Critical this
  project has booked already went. `GUARDED_SOCKET_METHODS` is the whole outbound surface and
  that is an argument rather than a list: `send`, `sendall` and `socket.sendfile` all need a
  connected socket and `connect` is guarded, so shadowing them would be code no input can reach —
  asserted in both directions. Along the way the patching moved into `tests/offline_guard.py` as
  `refusing_outbound_traffic(target)`: inside an autouse fixture no test could ever see
  `socket.socket` unguarded, so "deleting the shadow is the only restoration that leaves the
  class exactly as it was found" was a `finally` block with nothing under it — measured by
  replacing that `delattr` with `pass` and watching 59 tests stay green. The round trip is now
  driven over a throwaway subclass that inherits the same methods from the same C base.
- **`V2-P4-068`'s ordering-dependent test is closed**, by `V2-P4-089`'s containment rather than by
  anything in this change — verified rather than assumed, because the row asked for a measurement
  either way. The original failing selection is green in both directions
  (`tests/unit/test_import_layering.py` with `tests/unit/runtime/`, 70 passed either order), and
  both reproductions written verbatim into `tests/import_linter_containment.py` are green where
  they were 4 failed and 6 failed. The green is caused by the containment and not by luck: the
  mechanism is still live, and `raw_lint_imports_disables` asserts that the **raw** CLI still
  disables existing loggers, so returning either call site to a bare call turns those selections
  red again.
- **The risk committee no longer answers `500` to an abstention, and the risk-flag vocabulary has
  one owner** (`V2-P4-029`, `V2-P4-030`, `V2-P4-036`). `SignalFrame` has always called itself "a
  research conclusion **or abstention**", and `DeliberationCommittee.review` could not accept one:
  it recomputed `direction` from `adjusted_strength` into a `Literal` with no `abstain` in it, so
  an abstention -- which carries no `evidence_ids`, because that is what abstaining means -- came
  back out directional and died on its own output. `POST /api/v1/research/deliberate` answered
  **`500` with a `text/plain` body reading `Internal Server Error`**, and `OpenAlphaSDK.deliberate`
  raised `ValidationError: directional signal requires evidence`. Both now return the abstention
  unchanged, with the debate still reported beside it: widening the annotation alone would not have
  been enough, since an abstaining signal has `strength == 0` and a live debate would have put
  `debate_net / 2` into it and minted a conclusion out of a frame with no evidence behind it.
- **`risk_flags` is a closed vocabulary, declared once with what each flag is worth.**
  `domain/risk_flag.py::RiskFlag` replaces three disjoint sets -- two on `RiskGate`, one a literal
  inside `DeliberationCommittee.review`'s body -- and both gates derive from it, so
  `regulatory`, `data-quality`, `suspension` and `committee-disagreement` no longer reach the
  runtime gate and clear it. A misspelling is now a **`422` naming `risk_flags` and listing the
  vocabulary** instead of a silent demotion to `unrecognised`, which used to move the candidate
  carrying it *up* a governed screen. Closing the set exposed a drift nobody had recorded: all
  three shipped providers declare `redistribution="restricted"`, so `redistribution_restricted`
  was the only redistribution flag this build could produce and **no gate named it**, while the
  one that was named could not be generated at all. `RiskFlag` is a `StrEnum`, so every stored
  `signal_id` is byte-identical; `docs/api/schemas/signal-frame-v1.json` now states the
  vocabulary instead of `"items": {"type": "string"}`.
- **`SHIPPED_RISK_GATES` is deleted rather than wired up.** It called itself the single source for
  what counts as severe and nothing read it: adding an always-blocking third gate left
  `flag_severity('bogus-flag')` at `unrecognised`, and emptying the registry entirely left
  `flag_severity('future_data')` at `blocked`. A declared vocabulary leaves it nothing to do -- a
  gate does not get to decide what a flag is *worth*, only what to do about one -- so the registry,
  the synthetic one-flag probe that existed to route around the committee's crash, and the
  `lru_cache` that memoised a severity derived by running the gates all went with it.

- **A stored prediction that cannot be parsed is a named refusal, not "a defect in the command"**
  (`V2-P4-096`). A write a power cut stopped half way reached the command line as `exit 5` with
  the message withheld, HTTP as a bare `500 text/plain`, and the SDK as an unenveloped
  `JSONDecodeError` — while a document with one number *edited* was already refused perfectly,
  because the store re-derived the address and never checked the parse. Measuring the class first
  is what changed the fix: `read_versioned` is the single entry point every deserializing store
  in this package reads through, and **four damaged documents reach three different exception
  types** — a truncation raises `JSONDecodeError`, a newer build's `schema_version` and a payload
  that is an array rather than an object raise `UnknownSchemaVersionError`, and one retyped field
  raises pydantic's `ValidationError`. So the faults are named once as
  `domain.versioning.STORED_DOCUMENT_FAULTS` beside the function that raises them, rather than as
  a fourth `except json.JSONDecodeError` at a fourth call site, and `FilePredictionStore.get`
  converts them where it already re-derives the address. One `except` covers both readers: `put`
  reads through `get`, so re-running the daily run that would register the same prediction is
  refused by name too — and refused rather than repaired, because "never write where something is
  already held" is this store's one guarantee. The message names the record, the underlying fault
  and the document to remove. `openalpha model predictions` still lists the address, and that is
  now deliberate: verifying every name means parsing every body, measured at 3.6 ms per document
  at market width — 22 s for five models over five years — and it would *hide* the damage from
  the one person who needs to see it.
- **A same-day `daily-run` may set `--end` to the last session it built** (`V2-P4-095`). A
  training range reaching within `--horizon` sessions of the panel's newest session died reading
  price bars for a session that had not published yet, on all three faces, so a caller had to pull
  `--end` back `horizon + 1` sessions and nothing — no message, flag or limitations entry — said
  so. It contradicted the command's own contract: the training set is every example whose outcome
  window had closed at `--predict-at`, and those cross sections were always going to be purged.
  The labelling read simply ran first. `run_daily` now drops them **before** labelling, through
  `_outcome_had_closed` — the one inequality `trainable_at` already applied, extracted so the two
  cannot drift — so nothing asks the panel for prices it does not hold. Measured on the
  ten-session corpus: `--end 2026-01-15` refused before and now answers with the same
  `artifact_id` and the same `record_id` as `--end 2026-01-14`. A window the *calendar* cannot
  place at all is untouched and stays `V2-P4-088`'s named refusal: an outcome dated after the
  deadline and an outcome that cannot be dated are two different facts.
- **Both `openalpha model --help` examples run, and a test executes the ones that are printed**
  (`V2-P4-094`). Neither did. Three faults, only the first of which was reported: `--as-of` is a
  **partition**-level clock, so the printed `2026-01-20T04:00:00+00:00` refuses any 2026 panel
  holding a row published after it; the bound runs the other way as well, because the calendar
  requires every session up to `--as-of` to be present, so a later instant is a `date_gap` and the
  wall-clock default lands outside the interval on every panel not built up to today; and
  `model evaluate`'s example could not run on *any* panel, since `--horizon 5d` over the seven
  prediction days it names purges the first fold to nothing and `walk_forward_folds` refuses the
  schedule — a reason this repository's own test corpus had already recorded. The examples now
  read a whole year from after it, both spell `--as-of` out, and the help states the rule in both
  directions. The `not_yet_knowable` refusal stops describing a **maximum** as when the dataset
  "first became available", says that the judgement is per partition rather than per row, and
  names the earliest `as_of` that would read it — the number a caller needs was always in the
  message, framed as a fault rather than as a bound, which is why the acceptance found the
  reachable set by bisection. **What is not fixed is the partition-level gate itself**, and the
  reason is measured rather than deferred: see `panel_ingest.load_adjustment_histories`.
- **A daily run on the last trading day of the year is a named refusal on all three faces,
  not a bare `500`** (`V2-P4-088`). The prediction store seals a batch against the calendar's
  answer to when its outcome becomes knowable, and derives that answer through the same
  `build_label_window` the training side goes through — but `run_daily` handed the batch over
  *after* its only `try` block had closed, so `CalendarHorizonError` reached the REST route as
  `500 text/plain` and `OpenAlphaSDK` as an unenveloped `ValueError` subclass. It is not an
  exotic input: `daily_request` requires `predict_at`'s date to be strictly after `end`, so the
  prediction day is always later than every training day, and any prediction day in the last
  `horizon.sessions + 1` sessions of a year-keyed calendar has an outcome window the exchange
  has not published. Both places that build such a window now share one fault tuple and one
  sentence, and the remedy names the command: `openalpha panel build --dataset trade_cal --year
  <next>`, then declare that year with `--year`.
- **`panel_fixtures.generate_panel` can price a window anywhere in its calendar year**, which is
  what made the above reachable from a test at all. The generated calendar has always covered
  the whole partition year while the priced window was ten sessions in January, so no generated
  panel could put a prediction day near the calendar's last session — the third time a fixture
  has been found hiding a wall by never walking up to it (`V2-P4-080`, `V2-P4-085`). Each batch's
  fetch instant and the panel's `as_of` now follow the last session priced, which is
  `_index_weight_batch`'s existing rule extended to the five builders that lacked it; the default
  window is unchanged instant for instant.
- **One test file no longer disables logging for the whole process** (`V2-P4-089`).
  `tests/unit/test_model_view.py` imported the raw `importlinter.cli.lint_imports` under the
  alias `_lint_imports` — the name of the containment wrapper one directory over — so it read as
  contained while `dictConfig(disable_existing_loggers=True)` silently disabled every logger
  already in the process, and six `caplog` acceptances under `tests/integration` failed whenever
  that file was collected first. The convention that was supposed to prevent this had failed
  twice (`V2-P4-068`, `V2-P4-012`) because it was a per-file regex over a *call spelling*: an
  alias dodges it and another file is out of its scope. `tests/import_linter_containment.py` is
  now the only import of `importlinter.cli` in the tree, both of its exports restore the whole
  logger snapshot, and one AST sweep over every file under `tests/` replaces the three private
  regex guards. That sweep found four files reaching the raw CLI rather than two, and then the
  same defect inside the guard itself: the test that proves the pollution is real restored only
  the logger it named, taking four `test_batch_research.py` acceptances with it whenever the unit
  tests ran first — so four of the six failures had two causes, and routing the reported call
  sites through the existing wrapper would have left `pytest tests/unit tests/integration
  tests/contract` red.
- **One name declared twice with values of two types is one verdict on every face**
  (`V2-P4-091`). `ModelRunApiRequest.declared_hyperparameters` sorted whole `(name, value)`
  pairs while `cli._model_hyperparameters` sorted by name, so two hyperparameters sharing a name
  made the HTTP sort compare `1 < "a"` and answer `500 text/plain` where the command line and the
  SDK answered `bad_request`. A caller error reported as a service fault pages an operator and
  trips retries. The ordering rule now lives once, in `model_view.declared_hyperparameters`, and
  both faces call it — the HTTP copy had carried a comment claiming it was "`cli
  ._model_hyperparameters`' rule", which is what kept the disagreement invisible.

## [1.0.0] - 2026-07-24

### Added

- Four-clock point-in-time evidence contracts and content-addressed snapshots.
- SQLite WAL ledgers plus Parquet/DuckDB evidence storage.
- File, BYOT Tushare, and optional allowlisted AKShare provider adapters.
- A-share market-event, theme, catalyst, disclosure, and capital normalizers.
- Deterministic multi-agent research, structured model output, bounded retry, router, risk gate, memory, and immutable manifests.
- Same-path live/replay/backtest engine, A-share execution constraints, 300-event frozen replay corpus, outcome validation, and reconciled attribution.
- REST API, Python SDK, CLI, responsive React workbench, and Playwright golden flow.
- Non-root read-only Docker Compose deployment with persistent-volume recovery verification.
- Windows/Linux CI, dependency audits, publication safety scan, and 100% feature-destination ledger.

### Security

- Restricted CORS origins, strict Pydantic boundary validation, request size limit, browser security headers, BYOT credentials, and public-release secret/artifact checks.

### Boundaries

- No live broker execution, short/cover execution, commercial data resale, or bundled provider credentials.
