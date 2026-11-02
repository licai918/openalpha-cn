# P7 开发批次 1 · A 轨：真实数据冒烟的工具与运行手册（`V2-P7-019`）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `V2-P7-019` 的冒烟能在真实 Tushare 数据、真实时间上一晚一晚地跑起来，且跑的过程中**不可能**花掉超过用户同意的请求数、**不可能**打印一份不带"这些单不要下"横幅的订单清单、**不可能**碰到 P6 的运行目录或主检出。交付物是四件工具（受绑定代码里的冒烟横幅；不受绑定的请求上限与账本、一次性环境构建、驱动脚本）、运行手册的一节与 T10 报告骨架，外加一个**等用户明确同意才执行**的操作任务（第一晚的补抓与第一个晚间）。

**Architecture:** 冒烟登记是一份普通的 `survivor_book/v1` 登记，只在它的 `settings` 里多一个标记键 `smoke_run`（配置是封闭的三个键，`config_id` 不变）。横幅在两个脚本的**打印边界**上执行（`scripts/daily_selection.py` 的 `main` / `_main_morning`、`scripts/forward_report.py` 的 `main`）：读登记文件的 `settings`，有这个键就把横幅作为 stderr 的首行和末行、stdout 文本的首行和末行、JSON 文档的 `smoke` 键打印——只打印、从不入库。其余全部放在 `scripts/` 下三个**不受登记绑定**的新文件里：`p7_smoke_requests.py`（一个"超过额度就拒绝计数"的 `Counter`，经已有的 `daily_selection.counted_transport` 挂到唯一的传输接缝上；只追加的请求账本）、`p7_smoke_env.py`（路径规则、`git clone --local`、在克隆里写并提交冒烟登记、`cp -c` 克隆面板、`--pin-worktree` 同一套函数建钉住的 worktree 与 `.venv`、`.env` 链接）、`p7_smoke.py`（命令行：`prepare` / `evening` / `morning` / `weekly` / `remedy` / `status` / `phase`，每个花请求的子命令都在上限下、在进程内调用每日命令自己的 `main`）。

**Tech Stack:** Python 3.11（`.python-version`）、argparse、pytest（`daily_market.Market` 脚本化市场，面板在测试时生成）、git、macOS APFS `cp -c`；门禁 `ruff check`、`ruff format --check`、`mypy`（`files = ["src", "scripts"]`，strict）、`lint-imports`。

**Spec / 依据:** 路线调整 `docs/superpowers/plans/2026-11-01-p7-route-adjustment.md` §2.3、§2.6、§2.8、§3（"修订后的冒烟"）、§4 的 `V2-P7-019` 行；规格 `docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5.2 的 G-0（冒烟阶段 1 go 是闸门条件之一）、§7、§9 的 2026-11-01 各行（尤其"冒烟计划的更正"与"真钱闸门"）；路线图 `docs/specs/v2/openalpha-cn-v2-roadmap.md` §P7 的 `V2-P7-019` 行；运行手册 `docs/runbooks/daily-selection.zh-CN.md` 的「幸存者形态」一节（第 434–622 行）；成本测量 `docs/research/p7-step0-validation/T6-T7-daily-cost.md` §0。

---

## 0. 动手前核实过的事实（`file:line` 均已读过）

| # | 事实 | 出处 |
|---|---|---|
| F1 | 每日命令的请求只经一个接缝：`cli._panel_transport`（`panel build` 构造 `TushareProvider(transport=_panel_transport(), …)`）；整个 `src/` 与 `scripts/` 里没有别处构造会发请求的传输（`cli.py:660` 的 `TushareProvider()` 只给 `doctor` 报凭据，不取数） | `src/openalpha_cn/cli.py:2299-2307`、`:7418`、`:647-667` |
| F2 | `counted_transport(counts)` **只计数，不能停**：它把 `cli._panel_transport` 换成返回 `CountingTransport(original(), counts)` 的函数，退出时换回；`CountingTransport.post` **先** `counts[api_name] += 1`、**再**转发 | `scripts/daily_selection.py:1207-1242`（`:1219-1221`） |
| F3 | 每日命令没有"到 100 自动停"：最坏的一天会超过 100（撤回复核 ≤20 对、收益路径复现每对 4 次且不设上限） | `scripts/daily_selection.py:371-402`；运行手册第 596 行 |
| F4 | `TushareProvider` 只重试 `ProviderFailure` 与网络类 `OSError`/`URLError`/`TimeoutError`；别的异常（例如一个 `RuntimeError`）直接穿出，不重试 | `src/openalpha_cn/providers/tushare.py:4638-4668` |
| F5 | `panel build` 把命令里没预料到的异常一律转成 `PanelExit.internal_error` 并**扣下消息**，只打印类型名；所以上限抛出的异常在每日命令里表现为第 2 步 `` `panel build` exited N: … unhandled RequestCapReached `` | `src/openalpha_cn/cli.py:2381-2438`；`scripts/daily_selection.py:2049-2068` |
| F6 | 准入：登记文件必须在 `--repo` 的 `HEAD` 已提交且字节相同；受绑定路径 = `src`、`scripts/research`、`pyproject.toml`、`uv.lock` 加调用方的 `scripts/daily_selection.py`（周报再加 `scripts/forward_report.py`）；`openalpha_cn` 与 `grid`/`registry` 必须从 `--repo` 的树里导入，否则 `ForeignPackageError`/`ForeignScriptsError`（退出 3）。工作区检查只看受绑定路径（`git status --porcelain -- <bound>`），所以 `.venv`、`.env`、受绑定路径以外的未跟踪文件不会让准入失败 | `scripts/research/registry.py:224-296`、`:303-329`、`:359-383`；`scripts/daily_selection.py:556-618` |
| F7 | 幸存者配置的顶层键恰好是 `kind`、`survivor_book`、`adapter`，`adapter` 块 `extra="forbid"`；所以冒烟标记**不能**放进配置（会被第 1 步拒绝，也会改 `config_id`），只能放进 `settings`（`Registration.settings` 是开放的字典，每日路径只读 `random_seed`） | `src/openalpha_cn/strategy_survivor.py:111-118`、`:178-209`、`:279-345`；`scripts/daily_selection.py:605-617`、`:643` |
| F8 | 周报要求 `settings["survivor_forward_protocol"]`（`survivor-forward-protocol/v1`，`extra="forbid"`，没有任何缺省值），主期限必须是 20 | `src/openalpha_cn/strategy_survivor_forward.py:17-42`、`:298`、`:370-403` |
| F9 | 每日命令打印订单的全部路径都在 `main` 里：成功时 `survivor_summary_lines(result)` 或 `json.dumps(result)`；第 8 步拒绝时先打印到停下为止的文档；第 2–8 步拒绝时 stderr 上的 `error.aftermath`（列出纸面账簿仍有效的订单）；清晨复核在 `_main_morning`；周报在 `forward_report.main`。`this_run` 只打印、不入日志 | `scripts/daily_selection.py:6337-6392`、`:5755-5785`、`:5003-5014`、`:5251-5257`；`scripts/forward_report.py:926-983` |
| F10 | `--pin-worktree` = `pin_worktree(registration, repo, DIR)` + `sync_pinned_environment(DIR, uv=…)`（离线，`_run_sync` 是测试接缝）+ `link_env_file(repo, DIR)`；**链接指向 `<--repo>/.env`**，`--repo` 是一份 `git clone --local` 时那里没有 `.env`（它被 `.gitignore` 忽略），于是"nothing was linked" | `scripts/daily_selection.py:5885-5931`、`:5963-6007`、`:6057-6117`、`:6292-6300` |
| F11 | 补救命令一律是 `pinned_command` 的形状：`UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv run --no-sync --env-file .env "$PWD/.venv/bin/openalpha" panel build …`，或 `… "$PWD/.venv/bin/python" scripts/daily_selection.py …`；面板分批补抓打印 `--incremental --dataset adj_factor --dataset price --dataset stk_limit`；会话级分区彼此不齐时打印**不带** `--incremental` 的整年重建；日历补救是不带 `--incremental` 的 `--dataset trade_cal` | `scripts/daily_selection.py:2279-2326`、`:2329-2360`、`:2398-2477`、`:2976-3008`、`:4791-4808` |
| F12 | 会话级目标 = `adj_factor`、`price`（`daily`、`daily_basic`、`suspend_d`）、`stk_limit`：每个会话 5 次请求，`stk_limit` 分页后 7 次；增量构建会把库里最后一个会话再取一次 | `src/openalpha_cn/cli.py:3146-3152`；运行手册第 582、596 行 |
| F13 | 2026-10-08 的 P6 日志（只读看过 `panel.requests`）：`stk_limit` 2 个会话 2 次请求——**截至 10-08 还没分页**；`TUSHARE_STK_LIMIT_ROW_CAP` 的余量在 2026-08-10 测得约 29.6 个会话，所以随时可能开始分页 | `~/openalpha-research/daily_selection/a9ecdb37065f0d6b/2026-10-08.json`；`src/openalpha_cn/providers/tushare.py:1861-1877` |
| F14 | P6 登记的 `walk_forward.features` 包含四个组合因子的 `processed:cross_section_standard/v1`；研究库里有 `factor_proc_{return_vol_60,downside_vol_60,turnover_60,residual_vol_60}_v1`。所以幸存者日的第 4 步要从 P6 最后一次运行（10-08）之后逐个会话补建，`MAX_CATCH_UP_SESSIONS` = 20：**第一晚若在 2026-11-06 或之后，第 4 步会拒绝并打印 `openalpha factor build` 分批命令**（0 次请求、本机 CPU；T6 测得年末一个会话四个因子约 4 分钟）。"P6 建到 10-08"是**推断**（由配置与 10-08 的日志得出，没有读目录库），操作任务在副本上核实 | `docs/research/p6-registration.json`；`scripts/daily_selection.py:364`、`:3011-3051`；T6-T7 §0 第 2–3 条 |
| F15 | 纪元：还没有纸面账簿时，纪元到当天最多 `1 + max_panel_catch_up_sessions` 个会话（设计值 10 → 11）；书在第一次运行时开（第 5 步），纪元当天只开账，第一晚是纪元的下一个交易日 | `src/openalpha_cn/strategy_survivor_book.py:624-702`；运行手册第 451、518、585 行 |
| F16 | 时间：晚间以**钉住的** `as_of` 与下一交易日 09:15（`registration_cutoff`）比；发布时刻 16:30（`session_publication_instant`）。北京 UTC+8，多伦多 2026-11-01 起 EST（UTC−5），差 13 小时：北京 18:30 = 多伦多 05:30，北京次日 09:15 = 多伦多**当天** 20:15 | `src/openalpha_cn/strategy_registration.py:1017`；`src/openalpha_cn/panel_ingest.py:2983`；运行手册第 513、521–526 行 |
| F17 | 面板目录库存的是相对路径（`panel_partitions.relative_path`），APFS 克隆后指向克隆里的文件；本机是 APFS，`~` 所在卷剩 118 GiB | `src/openalpha_cn/panel/store.py:989-1003`、`:1917-1920`；`diskutil info /` |
| F18 | 面板与纸面账簿的锁原语 `_try_lock(descriptor, *, exclusive)` / `_unlock(descriptor)` 跨平台（POSIX `flock`、Windows `msvcrt.locking`），`strategy_survivor_book` 已经这样复用它们 | `src/openalpha_cn/panel/store.py:1264-1310`；`src/openalpha_cn/strategy_survivor_book.py:169-173`、`:586-621` |
| F19 | `scripts/verify_publication.py` 对仓库里每个 `.md` 检查相对链接（Markdown 的"方括号文字 + 圆括号路径"写法，以及 HTML 的 `src`/`href` 属性）必须存在——连写在反引号里的示例也算；新文档里引用尚不存在的文件只能用反引号写路径本身 | `scripts/verify_publication.py:45-46`、`:125-137` |
| F20 | `tests/unit/test_repository_assets.py` 对 `scripts/**/*.py` 做全仓 AST 检查：仓库相对路径转字符串要 `.as_posix()`、不许无效转义、`multiprocessing` 只能走显式 spawn 上下文 | `tests/unit/test_repository_assets.py:925-944`、`:1700-1720`、`:1965-1985` |

## Global Constraints

逐字取自 `AGENTS.md`、规格与路线调整；每个任务隐含这一节。

- 分支 `feat/v2-p7-019-smoke-harness`；每个提交、PR 写 `V2-P7-019`；提交信息末尾 `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。
- **受绑定代码只有任务 1 碰**：`scripts/daily_selection.py`、`scripts/forward_report.py`（打印边界）。任务 2–4 只新增 `scripts/p7_smoke_requests.py`、`scripts/p7_smoke_env.py`、`scripts/p7_smoke.py`——不在 `src/`、`scripts/research/` 下，也不是那两个脚本，所以**改它们不使任何登记失效**。不改 `src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`。
- 契约：横幅**只打印**，日志 `openalpha-daily-survivor/v1`、清晨文件、`survivor_forward_report/v1` 写出的文件一字不变（硬规则 3）。新的 `openalpha-p7-smoke/v1`（`smoke.json`）与 `openalpha-p7-smoke-ledger/v1`（`requests.jsonl`）只存在于冒烟根目录，不入库。
- `domain/` 不碰（硬规则 2）；单一组装根（硬规则 5）：驱动不自建存储，存储都由每日命令与周报自己经 `build_storage` 打开。
- 面板夹具在测试时生成（硬规则 6）：测试只在 `tmp_path` 下写 `.parquet`/`.duckdb`，**不入库**任何此类文件。
- 测试不联网（`tests/conftest.py::_refuse_outbound_connections`），一律用 `tests/unit/scripts/daily_market.Market`；**不读 `.env`**——测试里的 `.env` 写一个哨兵串，断言它不出现在任何输出与文件里。
- 措辞：冒烟的一切输出是"候选"，**不是证据**；横幅原文 `smoke: do not place these orders`。T10 报告**不出现任何收益数字**（账本权益、相对收益、IC 都不抄）。
- 真钱闸门（规格 §9 2026-11-01）：在 `006` 经用户确认并登记、账户成交对账与现金约束上线、用户明确说"开始"之前，不用打印的清单真钱交易。
- 不装 launchd；不 push；冒烟克隆与运行目录不入主仓库；不使用也不修改 `docs/research/p6-registration.json`；`~/openalpha-research` 只被 `cp -c` 读一次。
- **没有任何真实请求，除非用户明确同意**：任务 6 是唯一花请求的任务，开始前停下来问，说明范围与请求数。
- `.md` 里引用尚不存在的文件用反引号写路径，不用 Markdown 链接语法——连示例都不要写出那种形状（F19）。脚本里仓库相对路径转字符串用 `.as_posix()`（F20）；`sys.path` 设置之后的导入加 `# noqa: E402`（与 `forward_report.py` 同法）。
- 工作树纪律（`MEMORY.md`）：在任何 worktree 里跑测试一律 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree>/src`；先跑本任务的测试文件，**每次提交前**再跑 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree>/src uv run pytest tests/unit -q`；全量套件串行，绝不在 git hook / `--exec` 里跑；测试运行期间不改被测检出。
- **变异探针的统一做法**（每个任务列出要跑的几条）：`cp <file> /tmp/p7-019-orig`（或草稿目录）→ 按探针改一处 → `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree>/src uv run pytest <测试文件> -q`，**必须变红**（写下哪条测试红）→ `cp /tmp/p7-019-orig <file> && cmp <file> /tmp/p7-019-orig` → 再跑一次变绿。探针结果写进任务报告。
- 门禁：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`（读 `files = ["src", "scripts"]`）、`uv run lint-imports`。

## Review Focus

最可能伤到使用者的五种输入或失败，每条在负责的任务里有钉住它的测试：

1. **一份没有横幅的订单清单**——凌晨五点照着打印出来的 BUY 下单。冒烟登记的每一种输出（文本摘要、JSON、第 8 步拒绝的文档、第 2–8 步拒绝后列出的有效订单、清晨复核、周报）都以横幅开头和结尾，且横幅不进入任何文件。→ 任务 1：`test_a_smoke_evening_printed_again_as_text_is_bannered_first_and_last`、`test_a_refused_smoke_evening_is_bannered_around_its_refusal_block_and_its_document`、`test_a_smoke_morning_recheck_is_bannered`、`test_a_smoke_weekly_report_is_bannered_and_the_report_it_writes_is_not`、`test_the_banner_is_printed_never_filed`。
2. **绕过上限**：直接粘贴拒绝信息打印的补救命令（不经驱动，不计数），或者照"整年重建"那条补救去做（实盘规模约 1,200 次请求）。驱动的 `remedy` 只接受本冒烟根目录的命令，拒绝整年重建与别的运行目录。→ 任务 4：`test_a_printed_remedy_is_read_back_into_the_driver_s_own_invocation`、`test_a_remedy_the_smoke_never_runs_is_refused_by_name`。
3. **上限在一次补抓中途触发，半写的面板接着被用**。上限只放行恰好额度那么多个请求（下一次请求连同它的重试都不发），触发后根目录标为"已丢弃"，之后的每一次调用都拒绝；被中断（Ctrl-C、SIGTERM、SIGHUP）的写面板调用同样丢弃；被 SIGKILL 杀掉、没记下结束的调用让账本拒绝继续。→ 任务 2：`test_the_cap_stops_the_invocation_marks_the_root_discarded_and_refuses_every_later_one`、`test_an_interrupted_invocation_that_writes_the_panel_discards_the_root`、`test_an_invocation_that_never_recorded_its_end_refuses_every_later_one`；任务 4：`test_the_cap_stops_an_evening_mid_fetch_and_the_root_is_discarded`。
4. **冒烟碰到 P6 或主仓库**：根目录在 `~/openalpha-research` 里、在 `/tmp` 或 `$TMPDIR`（几周后被系统清掉，账簿丢失）、在主检出里或路径带空格（uv 按空格拆 `--env-file`）；克隆的 `origin` 指回主检出（一次手滑的 `git push` 就把冒烟登记送进主仓库）；克隆面板时源目录被改。→ 任务 3：`test_prepare_refuses_an_unsafe_smoke_root_before_it_makes_anything`、`test_prepare_clones_the_commit_and_commits_a_smoke_registration_in_the_clone_only`（`git remote` 为空、源仓库的 refs / status 前后一致）、`test_prepare_clones_the_panel_whole_and_leaves_the_source_as_it_was`、`test_prepare_refuses_a_source_that_changed_while_it_was_cloned`。
5. **钉住的 worktree 绑错东西**：`.env` 没链上（晚间没有凭据）或链到别处、`.venv` 是指向主检出的符号链接（准入 `ForeignPackageError`）、驱动从主检出运行（跑的不是登记的代码）、冒烟登记写出的配置不是本构建读得通的那份。→ 任务 3：`test_prepare_pins_a_worktree_with_its_own_environment_and_links_the_env_file_without_reading_it`、`test_the_smoke_configuration_is_the_design_s_with_the_epoch`；任务 4：`test_a_remedy_from_a_checkout_that_is_not_the_pinned_worktree_is_refused`。

## 任务总览与依赖

| 任务 | 交付 | 文件 | 依赖 |
|---|---|---|---|
| 1 | 冒烟标记与横幅（受绑定代码） | `scripts/daily_selection.py`、`scripts/forward_report.py`；新测试 `tests/unit/scripts/test_daily_selection_smoke_banner.py` | — |
| 2 | 请求上限与请求账本 | 新 `scripts/p7_smoke_requests.py`；新测试 `tests/unit/scripts/test_p7_smoke_requests.py` | — |
| 3 | 一次性冒烟环境（`prepare_smoke`） | 新 `scripts/p7_smoke_env.py`；新测试 `tests/unit/scripts/test_p7_smoke_env.py` | 1（引用 `daily_selection.SMOKE_SETTINGS_KEY`） |
| 4 | 驱动命令行 | 新 `scripts/p7_smoke.py`；新测试 `tests/unit/scripts/test_p7_smoke_driver.py` | 2、3 |
| 5 | 运行手册一节、T10 骨架、CHANGELOG | `docs/runbooks/daily-selection.zh-CN.md`、新 `docs/research/p7-step0-validation/T10-real-data-smoke.md`、`CHANGELOG.md` | 4 |
| 6 | **操作任务（等用户明确同意）**：准备、第一晚补抓、第一个晚间 | 冒烟根目录（仓库外）；T10 的第 1 晚一行 | 1–5 合入 `main`；用户的"开始" |

```
任务 1 (横幅, 受绑定) ──► 任务 3 (环境) ──┐
任务 2 (上限 + 账本) ─────────────────────┼──► 任务 4 (驱动) ──► 任务 5 (文档) ──► [合入 main] ──► 任务 6 (操作, 等用户同意)
```

任务 1 与任务 2 可并行（文件不相交）；任务 3 可与任务 2 并行（在任务 1 之后）。

---

## Task 1：冒烟标记与横幅（`scripts/daily_selection.py`、`scripts/forward_report.py`）

**Issue:** `V2-P7-019`。**依据:** 路线调整 §2.6、§3 第 5 条；F7、F9。

**为什么放在打印边界，而不是 `strategy_survivor_summary`：** 订单出现在六条打印路径上（F9），全部汇于两个脚本的 `main`；在那里判断一次，就覆盖文本、JSON、拒绝块、清晨复核与周报，且不改 `src/`（没有导入隔离、台账、契约的连锁）。代价：直接调用 `strategy_survivor_summary.summary_lines` 自己打印的调用方不带横幅——今天除这个脚本外没有这样的调用方。标记从登记文件**原样读**（准入之前，只为决定打印什么）：读不出的文件不给横幅，而它的准入本来就会拒绝、不会打印任何订单；标记键下**任何值**都算冒烟（坏掉的标记照样出横幅：失败时偏向"有横幅"）。

**Files:**
- Modify: `scripts/daily_selection.py`（常量、三个小函数、`main` 的尾部拆成 `_main_day`、`_main_morning` 加一个参数）
- Modify: `scripts/forward_report.py`（`main`）
- Create: `tests/unit/scripts/test_daily_selection_smoke_banner.py`

**Interfaces（产出，后续任务依赖，名字不得改）：**
- `daily_selection.SMOKE_SETTINGS_KEY: Final[str] = "smoke_run"`
- `daily_selection.SMOKE_BANNER: Final[str]`，以 `"smoke: do not place these orders"` 开头，纯 ASCII（Windows 控制台的编码不出错；中文写在运行手册里）。
- `daily_selection.SMOKE_DOCUMENT_KEY: Final[str] = "smoke"`
- `daily_selection.smoke_banner(registration: Path) -> str | None`
- `daily_selection.bannered(lines: Sequence[str], banner: str | None) -> list[str]`
- `daily_selection.with_smoke(document: Mapping[str, Any], banner: str | None) -> Mapping[str, Any]`
- 规则：冒烟登记的一次运行，**stderr 的首行与末行**是横幅（在运行开始前与结束后各打印一次，任何日志或拒绝都夹在中间）；**stdout 文本的首行与末行**是横幅；JSON 文档多一个 `smoke` 键。没有标记的登记，所有输出逐字节不变。

### Steps

- [ ] **Step 1：写失败的测试** `tests/unit/scripts/test_daily_selection_smoke_banner.py`：

```python
"""A smoke registration's every output says so (`V2-P7-019`, task 1).

A registration whose `settings` carry `daily_selection.SMOKE_SETTINGS_KEY` is a real-data smoke
registration: a one-off, committed only in a throwaway clone (`scripts/p7_smoke_env.py`), never
the `V2-P7-006` registration. The daily command, its morning re-check and the weekly report print
`SMOKE_BANNER` as the first and the last line of stderr, and as the first and the last line of the
text they print on stdout -- or as the `smoke` key of a JSON document -- so an order list from it
cannot be read without the banner. The marker is read from the registration file at the print
boundary; it never enters the configuration (its `config_id` is unchanged), the journal, the
morning file or the report the weekly command writes. A registration without the marker prints what
it always printed: every other test of the two scripts runs one (`test_daily_selection_survivor_day
.py` asserts `err == []` for a completed evening, for instance).

The world is `test_daily_selection_survivor_day.py`'s: its scripted market, its seeded panel (3
April) and its configuration (epoch 3 April), with `test_forward_report_survivor.py`'s registration
helper so that the settings carry the test protocol and the marker. Nothing reaches the network.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import shutil
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
import test_forward_report_survivor as survivor_report
from test_daily_selection_survivor_day import seeded  # noqa: F401  (a module fixture, reused)

from openalpha_cn import strategy_survivor_book

daily = tds.daily
forward_report = survivor_report.forward_report

MARKER: Final[dict[str, str]] = {"issue": "V2-P7-019", "note": "a test's smoke registration"}
SMOKE_SETTINGS: Final[dict[str, Any]] = {**survivor_report.settings_with(), "smoke_run": MARKER}
"""The settings of a smoke registration: the P6 measurement settings, the test protocol and the
marker -- its key spelled here, so that renaming `SMOKE_SETTINGS_KEY` turns this file red."""
MORNING_AS_OF: Final[datetime] = datetime(2026, 4, 7, 8, 30, tzinfo=daily.SHANGHAI)
"""Before 7 April's call auction: the morning re-check of 6 April's plan."""
REPORT_AS_OF: Final[datetime] = datetime(2026, 4, 7, 10, 0, tzinfo=daily.SHANGHAI)
"""After 6 April's registration cutoff (09:15 on the 7th): the weekly report reads its record."""


def _invoke(
    entry: Callable[..., int], arguments: Sequence[str], *, clock: Callable[[], datetime]
) -> tuple[int, str, list[str]]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = entry(list(arguments), clock=clock)
    assert tds.SECRET_TOKEN not in out.getvalue() + err.getvalue()
    return code, out.getvalue(), err.getvalue().splitlines()


def _day_arguments(world: tds.World, as_of: datetime, *extra: str) -> list[str]:
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    return [*arguments, "--repo", str(world.repo), "--as-of", as_of.isoformat(), *extra]


def _smoke_world(root: Path, seeded_runtime: Path) -> tds.World:
    repo, registration = survivor_report._repository(
        root, survivor_day.CONFIG, settings=SMOKE_SETTINGS
    )
    runtime = root / "runtime"
    shutil.copytree(seeded_runtime, runtime)
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    return tds.World(repo=repo, registration=registration, runtime=runtime, market=market)


@dataclasses.dataclass(frozen=True)
class Evening:
    world: tds.World
    code: int
    out: str
    err: list[str]


@pytest.fixture(scope="module")
def smoke_evening(
    tmp_path_factory: pytest.TempPathFactory,
    seeded: Path,  # noqa: F811
) -> Evening:
    """A smoke registration's first evening (6 April), run once through `main` with `--json`."""
    world = _smoke_world(tmp_path_factory.mktemp("smoke-evening"), seeded)
    with pytest.MonkeyPatch.context() as patch:
        tds._bind(patch, world.repo, world.market)
        world.market.today = survivor_day.DAY
        code, out, err = _invoke(
            daily.main,
            _day_arguments(world, survivor_day.DAY_AS_OF, "--json"),
            clock=lambda: survivor_day.RUN_CLOCK,
        )
    return Evening(world=world, code=code, out=out, err=err)


@pytest.fixture
def world(smoke_evening: Evening, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tds.World:
    """A copy of the smoke evening's runtime directory, under its registration."""
    runtime = tmp_path / "runtime"
    shutil.copytree(smoke_evening.world.runtime, runtime)
    tds._bind(monkeypatch, smoke_evening.world.repo, smoke_evening.world.market)
    return dataclasses.replace(smoke_evening.world, runtime=runtime)


# --- the marker ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "marked"),
    [
        ({"settings": {"smoke_run": {"issue": "V2-P7-019"}}}, True),
        ({"settings": {"smoke_run": None}}, True),
        ({"settings": {"smoke_run": False}}, True),
        ({"settings": {"random_seed": 3}}, False),
        ({"config": {"smoke_run": True}, "settings": {}}, False),
        ({"settings": "smoke_run"}, False),
        ([1, 2], False),
    ],
)
def test_any_value_under_the_settings_key_marks_a_smoke_registration(
    tmp_path: Path, body: object, marked: bool
) -> None:
    """Under `settings`, the key alone marks the file -- a malformed marker too: the banner fails
    on, never off. A key of the configuration is not the marker (the configuration is closed)."""
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    assert daily.smoke_banner(path) == (daily.SMOKE_BANNER if marked else None)


def test_a_registration_that_cannot_be_read_carries_no_banner(tmp_path: Path) -> None:
    """Its admission refuses it at step 1 and prints no order, so there is nothing to banner."""
    broken = tmp_path / "broken.json"
    broken.write_bytes(b"\xff not json")

    assert daily.smoke_banner(tmp_path / "missing.json") is None
    assert daily.smoke_banner(broken) is None


def test_the_banner_says_do_not_place_these_orders() -> None:
    assert daily.SMOKE_SETTINGS_KEY == "smoke_run"
    assert daily.SMOKE_DOCUMENT_KEY == "smoke"
    assert daily.SMOKE_BANNER.startswith("smoke: do not place these orders")
    assert daily.SMOKE_BANNER.isascii()
    assert daily.bannered(["a"], None) == ["a"]
    assert daily.bannered(["a"], "B") == ["B", "a", "B"]
    assert daily.with_smoke({"a": 1}, None) == {"a": 1}
    assert daily.with_smoke({"a": 1}, "B") == {"a": 1, "smoke": "B"}


def test_the_marker_lives_in_the_settings_so_the_configuration_identity_is_unchanged(
    tmp_path: Path,
) -> None:
    plain, smoke = tmp_path / "plain.json", tmp_path / "smoke.json"
    commit = "0" * 40
    tds.registry.register(
        survivor_day.CONFIG,
        tds.CRITERIA,
        plain,
        code_commit=commit,
        settings=survivor_report.settings_with(),
    )
    tds.registry.register(
        survivor_day.CONFIG, tds.CRITERIA, smoke, code_commit=commit, settings=SMOKE_SETTINGS
    )

    bodies = [json.loads(path.read_text(encoding="utf-8")) for path in (plain, smoke)]
    assert bodies[0]["config_id"] == bodies[1]["config_id"]
    assert plain.read_bytes() != smoke.read_bytes()


# --- the evening ---------------------------------------------------------------------------------


def test_a_smoke_evening_in_json_carries_the_banner_as_a_key_and_on_stderr_first_and_last(
    smoke_evening: Evening,
) -> None:
    assert smoke_evening.code == 0, smoke_evening.err
    shown = json.loads(smoke_evening.out)
    assert shown["smoke"] == daily.SMOKE_BANNER
    assert shown["paper"]["outcome"] == "created"
    assert smoke_evening.err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]


def test_the_banner_is_printed_never_filed(smoke_evening: Evening) -> None:
    journal = survivor_day._journal(smoke_evening.world)
    assert "smoke" not in journal["result"]
    text = survivor_day._journal_path(smoke_evening.world).read_text(encoding="utf-8")
    assert "smoke:" not in text


def test_a_smoke_evening_printed_again_as_text_is_bannered_first_and_last(
    world: tds.World,
) -> None:
    """The completed day printed again (no request): the same print path as the evening's."""
    code, out, err = _invoke(
        daily.main,
        _day_arguments(world, survivor_day.DAY_AS_OF),
        clock=lambda: survivor_day.RUN_CLOCK,
    )

    lines = out.splitlines()
    assert code == 0, err
    assert lines[0] == lines[-1] == daily.SMOKE_BANNER
    assert lines[-2].startswith("wording")
    assert any("ORDERS FOR THE OPEN OF D+1" in line for line in lines[1:-1])
    assert sum(line.startswith("  BUY ") for line in lines) == 10
    assert err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]


def test_a_refused_smoke_evening_is_bannered_around_its_refusal_block_and_its_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: Path,  # noqa: F811
) -> None:
    """Step 8 refused: the document as far as it got on stdout, and on stderr the refusal, its
    requests and what it means for the account -- the orders the book still holds -- all inside
    the banner."""
    smoke = _smoke_world(tmp_path, seeded)
    tds._bind(monkeypatch, smoke.repo, smoke.market)
    monkeypatch.setattr(strategy_survivor_book, "evening", survivor_day._refused_evening)
    smoke.market.today = survivor_day.DAY

    code, out, err = _invoke(
        daily.main,
        _day_arguments(smoke, survivor_day.DAY_AS_OF),
        clock=lambda: survivor_day.RUN_CLOCK,
    )

    assert code == 1
    assert err[0] == err[-1] == daily.SMOKE_BANNER
    assert err[1] == survivor_day.INJECTED
    assert survivor_day.NO_NEW_ORDERS in err[1:-1]
    lines = out.splitlines()
    assert lines[0] == lines[-1] == daily.SMOKE_BANNER
    assert any("NONE PLANNED tonight" in line for line in lines)


# --- the morning and the week --------------------------------------------------------------------


@pytest.mark.parametrize("json_output", [False, True])
def test_a_smoke_morning_recheck_is_bannered(world: tds.World, json_output: bool) -> None:
    world.market.today = MORNING_AS_OF.date()
    flags = ["--morning-recheck", *(["--json"] if json_output else [])]

    code, out, err = _invoke(
        daily.main,
        _day_arguments(world, MORNING_AS_OF, *flags),
        clock=lambda: MORNING_AS_OF + timedelta(minutes=5),
    )

    assert code == 0, err
    assert err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]
    if json_output:
        assert json.loads(out)["smoke"] == daily.SMOKE_BANNER
    else:
        lines = out.splitlines()
        assert lines[0] == lines[-1] == daily.SMOKE_BANNER
        assert lines[1].startswith("morning re-check")
    morning = survivor_day._journal_path(world).with_name("2026-04-06.morning.json")
    assert "smoke" not in json.loads(morning.read_text(encoding="utf-8"))


@pytest.mark.parametrize("json_output", [False, True])
def test_a_smoke_weekly_report_is_bannered_and_the_report_it_writes_is_not(
    world: tds.World, json_output: bool
) -> None:
    arguments = ["--runtime-dir", str(world.runtime), "--registration", str(world.registration)]
    arguments += ["--repo", str(world.repo), "--as-of", REPORT_AS_OF.isoformat()]
    arguments += ["--json"] if json_output else []

    code, out, err = _invoke(forward_report.main, arguments, clock=lambda: REPORT_AS_OF)

    assert code == 0, err
    assert err == [daily.SMOKE_BANNER, daily.SMOKE_BANNER]
    written = world.runtime.resolve() / "reports" / survivor_report.report_name(REPORT_AS_OF)
    assert "smoke" not in json.loads(written.read_text(encoding="utf-8"))
    if json_output:
        assert json.loads(out)["smoke"] == daily.SMOKE_BANNER
    else:
        lines = out.splitlines()
        assert lines[0] == lines[-1] == daily.SMOKE_BANNER
        assert lines[-2] == f"written to {written}"
```

- [ ] **Step 2：运行，确认失败。** `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree>/src uv run pytest tests/unit/scripts/test_daily_selection_smoke_banner.py -q`。预期：收集通过，标记类测试以 `AttributeError: module 'daily_selection' has no attribute 'smoke_banner'`（或 `SMOKE_BANNER`）失败；晚间、清晨、周报类测试以 `KeyError: 'smoke'` 或 `assert [] == ['smoke: …', 'smoke: …']` 失败——因为横幅还不存在。若模块级 fixture 的晚间本身失败（`smoke_evening.code != 0`），先停下来报告：那说明带额外 `settings` 键的登记在现有代码上就跑不通，与 F7 矛盾。

- [ ] **Step 3：最小实现。** 在 `scripts/daily_selection.py` 的 `THIS_SCRIPT`（第 311 行）之后加常量：

```python
SMOKE_SETTINGS_KEY: Final[str] = "smoke_run"
"""The key of a registration's `settings` that marks it a real-data smoke registration
(`V2-P7-019`): a one-off, committed only in a throwaway clone and never the `V2-P7-006`
registration. Any value at all marks it. Never a key of the configuration, which is closed
(`strategy_survivor.REGISTRATION_KEYS`) and whose `config_id` it would change; the settings are
open, and the day reads only `random_seed` from them."""

SMOKE_BANNER: Final[str] = (
    "smoke: do not place these orders -- a V2-P7-019 smoke registration, not the V2-P7-006 "
    "registration; nothing it prints is evidence"
)
"""What every output of a smoke registration says first and last (`smoke_banner`)."""

SMOKE_DOCUMENT_KEY: Final[str] = "smoke"
"""The key a printed JSON document of a smoke registration carries the banner under. Printed
only: the journal, the morning file and the written weekly report never hold it."""
```

在 `_missing_registration_next`（第 6191 行）之前加：

```python
def smoke_banner(registration: Path) -> str | None:
    """`SMOKE_BANNER` when the registration file at `registration` marks itself a smoke run --
    its `settings` hold `SMOKE_SETTINGS_KEY`, whatever the value -- else `None` (`V2-P7-019`).

    Read at the print boundary, before the day admits the file, and only to decide what is
    printed: a file that cannot be read or is not JSON gives `None`, and the day's own admission
    refuses it, printing no order. A malformed marker still marks the file: the banner fails on,
    never off."""
    try:
        body = json.loads(registration.read_bytes())
    except (OSError, ValueError):
        return None
    settings = body.get("settings") if isinstance(body, dict) else None
    if isinstance(settings, dict) and SMOKE_SETTINGS_KEY in settings:
        return SMOKE_BANNER
    return None


def bannered(lines: Sequence[str], banner: str | None) -> list[str]:
    """`lines` with `banner` first and last, or as they are when there is none."""
    return [*lines] if banner is None else [banner, *lines, banner]


def with_smoke(document: Mapping[str, Any], banner: str | None) -> Mapping[str, Any]:
    """A printed JSON `document` with the banner under `SMOKE_DOCUMENT_KEY`, or as it is."""
    return document if banner is None else {**document, SMOKE_DOCUMENT_KEY: banner}
```

把 `main` 在 `options = DailyOptions(...)`（第 6337–6347 行）之后的部分换成：

```python
    banner = smoke_banner(arguments.registration)
    if banner is not None:
        print(banner, file=sys.stderr)
    try:
        if arguments.morning_recheck:
            return _main_morning(options, as_json=arguments.json, clock=clock, banner=banner)
        return _main_day(
            options, as_json=arguments.json, top=arguments.top, clock=clock, banner=banner
        )
    finally:
        if banner is not None:
            print(banner, file=sys.stderr)


def _main_day(
    options: DailyOptions,
    *,
    as_json: bool,
    top: int | None,
    clock: Callable[[], datetime],
    banner: str | None,
) -> int:
    """`main`'s day: the result printed, or the refusal with its requests and -- for a survivor
    day -- its aftermath; under a smoke registration's banner when there is one (`V2-P7-019`)."""
    try:
        result = run_daily_selection(options, clock=clock)
    except StepFailedError as error:
        if error.document is not None:
            shown = {
                **error.document,
                "this_run": {
                    "requests": error.requests,
                    "request_count": sum(error.requests.values()),
                    "wrote": True,
                },
            }
            if as_json:
                print(json.dumps(with_smoke(shown, banner), sort_keys=True, ensure_ascii=False))
            else:
                print("\n".join(bannered(survivor_summary_lines(shown), banner)))
        print(str(error), file=sys.stderr)
        print(
            f"tushare requests   {sum(error.requests.values())} this run "
            f"{json.dumps(error.requests, sort_keys=True)}",
            file=sys.stderr,
        )
        for line in error.aftermath:
            print(line, file=sys.stderr)
        if (
            error.step == "registration"
            and error.exit_code == DailyExit.no_registration
            and not options.registration.is_file()
        ):
            print(_missing_registration_next(options.repo), file=sys.stderr)
        return int(error.exit_code)
    if as_json:
        print(json.dumps(with_smoke(result, banner), sort_keys=True, ensure_ascii=False))
    elif result.get("kind") == SURVIVOR_RESULT_KIND:
        print("\n".join(bannered(survivor_summary_lines(result), banner)))
    else:
        shown_top = top if top is not None else result["targets"]["holding_count"]
        print("\n".join(bannered(summary_lines(result, top=int(shown_top)), banner)))
    return int(DailyExit.done)
```

（原来的注释原样搬进 `_main_day`；`options.registration`、`options.repo` 就是原来的 `arguments.registration`、`arguments.repo`。）`_main_morning` 加关键字参数 `banner: str | None = None`，两处打印改为：

```python
            if as_json:
                text = json.dumps(
                    with_smoke(run.document, banner), sort_keys=True, ensure_ascii=False
                )
            else:
                from openalpha_cn.strategy_survivor_morning import morning_lines

                text = "\n".join(
                    bannered(morning_lines(run.document, this_run=run.this_run()), banner)
                )
```

`scripts/forward_report.py` 的 `main`：在 `arguments = parser.parse_args(argv)` 之后加

```python
    banner = daily_selection.smoke_banner(arguments.registration)
    if banner is not None:
        print(banner, file=sys.stderr)
    try:
        return _main_report(arguments, clock=clock, banner=banner)
    finally:
        if banner is not None:
            print(banner, file=sys.stderr)
```

并把原来从 `as_of = …` 到 `return 0` 的部分原样移进 `_main_report(arguments: argparse.Namespace, *, clock: Callable[[], datetime], banner: str | None) -> int`，只改最后的打印：

```python
    if arguments.json:
        printed = (
            {**payload, "this_run": dict(report.this_run)}
            if isinstance(report, SurvivorForwardReport)
            else payload
        )
        print(
            json.dumps(
                daily_selection.with_smoke(printed, banner), sort_keys=True, ensure_ascii=False
            )
        )
    else:
        lines = [*summary_lines(report), f"written to {out}"]
        print("\n".join(daily_selection.bannered(lines, banner)))
    return 0
```

写出的文件（`_replace_whole(out, json.dumps(payload, …))`、P6 的 `out.write_text(...)`）**不改**。

- [ ] **Step 4：运行，确认通过**，再跑回归：`tests/unit/scripts/test_daily_selection_smoke_banner.py`、`test_daily_selection.py`、`test_daily_selection_dispatch.py`、`test_daily_selection_survivor_day.py`、`test_daily_selection_survivor_morning.py`、`test_forward_report.py`、`test_forward_report_survivor.py`（改动前后各跑一次，把两次的通过数写进报告：P6 与无标记的幸存者输出必须逐字节不变）。然后 `tests/unit` 全量、ruff、mypy、lint-imports。

- [ ] **Step 5：变异探针**（`scripts/daily_selection.py`、`scripts/forward_report.py`）：
  1. `smoke_banner` 里把 `SMOKE_SETTINGS_KEY in settings` 改为 `isinstance(settings.get(SMOKE_SETTINGS_KEY), dict)` → `test_any_value_under_the_settings_key_marks_a_smoke_registration[…None…]` 与 `[…False…]` 变红。
  2. 删掉 `main` 里 `finally` 的那次 `print(banner, file=sys.stderr)` → 晚间、清晨的 `err == [BANNER, BANNER]` 变红。
  3. `_main_day` 拒绝分支里 `bannered(survivor_summary_lines(shown), banner)` 改回 `survivor_summary_lines(shown)` → `test_a_refused_smoke_evening_is_bannered_around_its_refusal_block_and_its_document` 变红。
  4. `_main_morning` 的 JSON 分支去掉 `with_smoke` → `test_a_smoke_morning_recheck_is_bannered[True]` 变红。
  5. `forward_report._main_report` 把写出的文件改成 `json.dumps(daily_selection.with_smoke(payload, banner), …)` → `test_a_smoke_weekly_report_is_bannered_and_the_report_it_writes_is_not` 变红。

- [ ] **Step 6：提交**（显式列路径）：`feat(V2-P7-019): a smoke registration's every output says "smoke: do not place these orders"`。报告里写明：本任务改了受绑定代码（两个脚本），使所有现行登记的代码绑定失效——预期如此（P6 每日暂停，`006b` 之前没有登记在用）。

---

## Task 2：请求上限与请求账本（`scripts/p7_smoke_requests.py`）

**Issue:** `V2-P7-019`。**依据:** 路线调整 §3 第 3 条、V6；F1–F5。

**设计要点（不另写传输）：** 上限是一个 `Counter` 子类：计数将超过额度时，不计数而抛 `RequestCapReached`。把它交给**未改动的** `daily_selection.counted_transport`，包住整次调用；每日命令自己的 `counted_transport` 嵌在里面，于是每个请求先进每日命令的计数、再进上限的计数、最后才到 HTTP（F2）。超额的那个请求不转发，它的任何重试也不转发（F4：这个异常不在重试的两类里；就算重试，每次都同样被拒）。代价写进模块文档：上限触发时，每日命令自己打印的 `tushare requests` 比真正发出的多 1（它先计数）；账本记的是真正发出的。

**Files:**
- Create: `scripts/p7_smoke_requests.py`
- Create: `tests/unit/scripts/test_p7_smoke_requests.py`

**Interfaces（产出）：**
- `LEDGER_SCHEMA: Final[str] = "openalpha-p7-smoke-ledger/v1"`；`CAP_EXIT: Final[int] = 4`
- `class RequestCapReached(RuntimeError)`（属性 `allowance`、`api_name`；消息不带请求内容）
- `class SmokeBudgetError(ValueError)`
- `class CappedCounter(Counter[str])`：`__init__(self, allowance: int)`；属性 `allowance`、`reached: str | None`；`sent -> int`
- `capped_requests(allowance: int) -> contextlib.AbstractContextManager[CappedCounter]`
- `Spent`（冻结）：`total: int`、`phase: int`、`discarded: str | None`、`unclosed: tuple[int, ...]`、`invocations: int`
- `class RequestLedger`：`__init__(self, path: Path)`；`events() -> list[dict[str, Any]]`；`append(event: Mapping[str, Any]) -> None`；`spent() -> Spent`
- `CappedRun`（冻结）：`invocation: int`、`exit_code: int`、`wrapped_exit_code: int | None`、`sent: Mapping[str, int]`、`reached: str | None`、`seconds: float`
- `run_capped(ledger, *, kind: str, arguments: Sequence[str], cap: int, ceiling: int | None, headroom: int, discard_on_cap: bool, call: Callable[[], int], clock: Callable[[], datetime], monotonic: Callable[[], float] = time.monotonic) -> CappedRun`

**账本的事件**（每行一个 JSON，`schema` 必有）：`prepared`（任务 3 写）、`phase`（`phase: 2`、`reason`）、`start`（`id`、`kind`、`arguments`、`phase`、`cap`、`allowance`、`spent_before`、`at`）、`end`（`id`、`exit_code`、`fault`、`requests`、`count`、`reached`、`seconds`、`at`）、`discarded`（`reason`、`at`）。已花 = 所有 `end.count` 之和；阶段 = 最后一个 `phase` 事件（缺省 1）。

**`run_capped` 的规则：** 根目录已丢弃 → 拒绝；有 `start` 没有 `end`（进程被 SIGKILL、断电）→ 拒绝（发了多少请求不知道，面板可能半写：只能丢弃重来）；剩余（`cap − 已花`）小于 `headroom` → 拒绝、不写 `start`；额度 = 剩余与 `ceiling` 中较小者；写 `start` → 在 `capped_requests(额度)` 里调用 `call`（`SystemExit` 的码当作退出码；`RequestCapReached` 直接冒出来时退出码为空）→ `finally` 写 `end`；`discard_on_cap` 且（上限触发**或**调用以异常结束，含 `KeyboardInterrupt`）→ 写 `discarded`；异常照样抛出。上限触发时返回 `CAP_EXIT`。

### Steps

- [ ] **Step 1：写失败的测试** `tests/unit/scripts/test_p7_smoke_requests.py`：

```python
"""`scripts/p7_smoke_requests.py`: the smoke's request cap and its ledger (`V2-P7-019`, task 2).

The cap is a `Counter` that refuses the increment past its allowance, handed to the daily
command's own `counted_transport` -- whose `CountingTransport` counts a request before it forwards
it -- so the request that would pass the cap never reaches the transport. The ledger is the
smoke root's append-only record of what each invocation sent. The market is the scripted one;
nothing reaches the network.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection_survivor as tss
from daily_market import Market

from openalpha_cn import cli

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


caps = _script("p7_smoke_requests")
daily = tss.daily
T0: Final[datetime] = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)


class Recording:
    """A transport that records the `api_name` of every request that reaches it."""

    def __init__(self) -> None:
        self.posted: list[str] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.posted.append(str(payload["api_name"]))
        return {"code": 0, "data": {"fields": [], "items": []}}


def _spend(*names: str) -> Callable[[], int]:
    """An invocation that sends one request per name through the panel's transport."""

    def call() -> int:
        transport = cli._panel_transport()
        for name in names:
            transport.post({"api_name": name, "params": {}})
        return 0

    return call


def _run(ledger: Any, call: Callable[[], int], **rules: Any) -> Any:
    keywords: dict[str, Any] = {
        "kind": "evening",
        "arguments": ["--json"],
        "cap": 10,
        "ceiling": None,
        "headroom": 1,
        "discard_on_cap": True,
    }
    keywords.update(rules)
    return caps.run_capped(ledger, call=call, clock=lambda: T0, **keywords)


def _events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Recording:
    recording = Recording()
    monkeypatch.setattr(cli, "_panel_transport", lambda: recording)
    return recording


# --- the counter ---------------------------------------------------------------------------------


def test_a_capped_counter_counts_to_its_allowance_and_refuses_every_increment_past_it() -> None:
    counter = caps.CappedCounter(3)
    counter["daily"] += 1
    counter["daily"] += 1
    counter["adj_factor"] += 1

    with pytest.raises(caps.RequestCapReached, match="3 of 3"):
        counter["stk_limit"] += 1
    with pytest.raises(caps.RequestCapReached):
        counter["daily"] += 1

    assert dict(counter) == {"daily": 2, "adj_factor": 1}
    assert counter.sent == 3
    assert counter.reached == "stk_limit"  # the first request refused


@pytest.mark.parametrize("allowance", [-1, 1.5, True])
def test_an_allowance_is_a_whole_number_of_requests(allowance: object) -> None:
    with pytest.raises(ValueError, match="allowance"):
        caps.CappedCounter(allowance)


def test_the_request_past_the_cap_never_reaches_the_transport(transport: Recording) -> None:
    with caps.capped_requests(2) as counter:
        wrapped = cli._panel_transport()
        wrapped.post({"api_name": "daily", "params": {}})
        wrapped.post({"api_name": "daily", "params": {}})
        with pytest.raises(caps.RequestCapReached):
            wrapped.post({"api_name": "adj_factor", "params": {}})

    assert transport.posted == ["daily", "daily"]
    assert (counter.sent, counter.reached) == (2, "adj_factor")
    assert cli._panel_transport() is transport


def test_inside_the_day_s_own_counter_the_day_counts_the_refused_request_and_the_cap_does_not(
    transport: Recording,
) -> None:
    """The order `run_capped` and the daily command nest them in: the day's counter is inner, so
    it counts first -- its `tushare requests` line reads one more than was sent."""
    day: Counter[str] = Counter()
    with caps.capped_requests(1) as counter, daily.counted_transport(day):
        wrapped = cli._panel_transport()
        wrapped.post({"api_name": "daily", "params": {}})
        with pytest.raises(caps.RequestCapReached):
            wrapped.post({"api_name": "daily", "params": {}})

    assert transport.posted == ["daily"]
    assert counter.sent == 1
    assert day["daily"] == 2


@pytest.mark.parametrize("allowance", [16, 10])
def test_an_ordinary_day_under_the_cap_sends_at_most_its_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allowance: int
) -> None:
    """The real `panel build`, in process, for an ordinary survivor day (16 requests): a cap of 16
    lets it through; a cap of 10 stops it at step 2 with exactly 10 sent."""
    store = tss._seeded(
        tmp_path, monkeypatch, Market(open_days=tss.OPEN_2026), as_of=tss.SEEDED, year=2026
    )
    targets = daily.targets_for_survivor(tss.SURVIVOR_BUILDS)

    with caps.capped_requests(allowance) as counter:
        if allowance >= sum(tss.STEADY_SURVIVOR.values()):
            _panel, counts = tss._update(store, tss.DAY, targets=targets, survivor=tss._survivor())
            assert dict(counts) == tss.STEADY_SURVIVOR
        else:
            with pytest.raises(daily.StepFailedError):
                tss._update(store, tss.DAY, targets=targets, survivor=tss._survivor())

    assert len(store.market.payloads) == counter.sent == min(allowance, 16)
    assert (counter.reached is None) == (allowance >= 16)


# --- the ledger ----------------------------------------------------------------------------------


def test_run_capped_records_what_reached_the_transport_and_nothing_else(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    run = _run(ledger, _spend("daily", "daily", "stk_limit"))

    assert (run.invocation, run.exit_code, run.reached) == (1, 0, None)
    assert dict(run.sent) == {"daily": 2, "stk_limit": 1}
    spent = ledger.spent()
    assert (spent.total, spent.phase, spent.discarded, spent.unclosed) == (3, 1, None, ())
    events = _events(tmp_path / "requests.jsonl")
    assert [event["event"] for event in events] == ["start", "end"]
    assert (events[0]["allowance"], events[0]["spent_before"], events[1]["count"]) == (10, 0, 3)
    assert all(event["schema"] == caps.LEDGER_SCHEMA for event in events)


def test_the_cap_stops_the_invocation_marks_the_root_discarded_and_refuses_every_later_one(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    _run(ledger, _spend(*["daily"] * 8))

    run = _run(ledger, _spend(*["daily"] * 5))

    assert (run.exit_code, run.reached) == (caps.CAP_EXIT, "daily")
    assert len(transport.posted) == 10
    spent = ledger.spent()
    assert spent.total == 10
    assert spent.discarded is not None
    with pytest.raises(caps.SmokeBudgetError, match="discarded"):
        _run(ledger, _spend("daily"))
    assert len(transport.posted) == 10


def test_a_ceiling_bounds_one_invocation_and_its_breach_need_not_discard(
    tmp_path: Path, transport: Recording
) -> None:
    """The weekly report's ceiling is 0: any request it tries is refused, and since it writes no
    panel the root is not discarded."""
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    run = _run(
        ledger, _spend("daily"), kind="weekly", ceiling=0, headroom=0, discard_on_cap=False
    )

    assert run.exit_code == caps.CAP_EXIT
    assert transport.posted == []
    assert ledger.spent().discarded is None


def test_an_invocation_with_less_than_its_headroom_left_is_refused_before_it_starts(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    _run(ledger, _spend(*["daily"] * 8))
    before = (tmp_path / "requests.jsonl").read_bytes()

    with pytest.raises(caps.SmokeBudgetError, match="2 request"):
        _run(ledger, _spend("daily"), headroom=3)

    assert (tmp_path / "requests.jsonl").read_bytes() == before
    assert len(transport.posted) == 8


def test_an_interrupted_invocation_that_writes_the_panel_discards_the_root(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    def interrupted() -> int:
        cli._panel_transport().post({"api_name": "daily", "params": {}})
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(ledger, interrupted)

    events = _events(tmp_path / "requests.jsonl")
    assert [event["event"] for event in events] == ["start", "end", "discarded"]
    assert (events[1]["fault"], events[1]["count"]) == ("KeyboardInterrupt", 1)


def test_an_invocation_that_never_recorded_its_end_refuses_every_later_one(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    ledger.append(
        {
            "event": "start",
            "id": 1,
            "kind": "panel-build",
            "arguments": [],
            "phase": 1,
            "cap": 190,
            "allowance": 190,
            "spent_before": 0,
            "at": T0.isoformat(),
        }
    )

    assert ledger.spent().unclosed == (1,)
    with pytest.raises(caps.SmokeBudgetError, match="never recorded"):
        _run(ledger, _spend("daily"))
    assert transport.posted == []


def test_the_phase_is_the_last_phase_event_and_starts_at_one(tmp_path: Path) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")
    assert ledger.spent().phase == 1

    ledger.append({"event": "phase", "phase": 2, "reason": "the user's go", "at": T0.isoformat()})

    assert ledger.spent().phase == 2


def test_a_ledger_line_that_is_not_its_own_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    path.write_text('{"event": "start"}\nnot json\n', encoding="utf-8")

    with pytest.raises(caps.SmokeBudgetError, match="requests.jsonl, line 1"):
        caps.RequestLedger(path).spent()


def test_a_fault_of_the_invocation_is_recorded_and_raised(
    tmp_path: Path, transport: Recording
) -> None:
    ledger = caps.RequestLedger(tmp_path / "requests.jsonl")

    def broken() -> int:
        raise OSError("the disk is full")

    with pytest.raises(OSError, match="disk"):
        _run(ledger, broken, discard_on_cap=False)

    events = _events(tmp_path / "requests.jsonl")
    assert (events[-1]["event"], events[-1]["fault"], events[-1]["count"]) == ("end", "OSError", 0)
```

- [ ] **Step 2：运行，确认失败**：`ModuleNotFoundError: No module named 'p7_smoke_requests'`（模块还不存在）。

- [ ] **Step 3：实现** `scripts/p7_smoke_requests.py`：

```python
"""The request budget of the real-data smoke (`V2-P7-019`, task 2): a hard cap on the Tushare
requests one smoke invocation may send, and the append-only ledger that adds them up.

**The cap rides the daily command's own counter.** `daily_selection.counted_transport(counts)`
routes every request `panel build` makes -- the one seam that reaches Tushare,
`cli._panel_transport` -- through a `CountingTransport`, which increments `counts[api_name]` and
only then forwards the request (`scripts/daily_selection.py:1219-1221`). `CappedCounter` is a
`Counter` that refuses the increment past its allowance by raising `RequestCapReached`: entered
around a whole invocation, the request that would pass the cap is never forwarded, and neither is
any retry of it -- `TushareProvider` retries a `ProviderFailure` or a network `OSError` only, and
every retry would be refused the same way. No transport is written here.

**The day's own count reads one more.** The daily command enters its own `counted_transport`
inside this one, so its counter increments first: when the cap stops a request, the day's
`tushare requests` line counts that unsent request, and this ledger -- which counts what was
forwarded -- does not. `panel build` reports the exception as an internal error with its message
withheld, so a capped day refuses at step 2 with `` `panel build` exited ... RequestCapReached ``.

**The ledger** (`requests.jsonl` in the smoke root) is one JSON event a line, flushed and synced:
`prepared`, `phase`, `start`, `end` and `discarded`. What was spent is the sum of the `end`
counts. An invocation that started and recorded no end was killed: what it sent is unknown and
the panel copy may be half-written, so nothing more runs in the root. An invocation that writes
the panel and stops on the cap, or on any fault or interruption, discards the root.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402

LEDGER_SCHEMA: Final[str] = "openalpha-p7-smoke-ledger/v1"
CAP_EXIT: Final[int] = 4
"""What an invocation the cap stopped exits with: none of the daily command's exits (0 to 3)."""


class RequestCapReached(RuntimeError):
    """The request that would have passed the cap. It was not sent."""

    def __init__(self, *, allowance: int, api_name: str) -> None:
        super().__init__(
            f"the V2-P7-019 smoke's request cap: {allowance} of {allowance} requests of this "
            f"invocation's allowance are spent, and this {api_name} request was not sent"
        )
        self.allowance = allowance
        self.api_name = api_name


class SmokeBudgetError(ValueError):
    """An invocation the ledger refuses to start, or a ledger that cannot be read."""


class CappedCounter(Counter[str]):
    """A `Counter` of requests by `api_name` that refuses the increment past `allowance`."""

    def __init__(self, allowance: int) -> None:
        if type(allowance) is not int or allowance < 0:
            raise ValueError(
                f"an allowance is a whole number of requests, 0 or more, not {allowance!r}"
            )
        self.allowance = allowance
        self.reached: str | None = None
        """The `api_name` of the first request refused, or `None`."""
        super().__init__()

    def __setitem__(self, key: str, value: int, /) -> None:
        total = sum(self.values()) - self.get(key, 0) + value
        if total > self.allowance:
            if self.reached is None:
                self.reached = key
            raise RequestCapReached(allowance=self.allowance, api_name=key)
        super().__setitem__(key, value)

    @property
    def sent(self) -> int:
        return sum(self.values())


@contextlib.contextmanager
def capped_requests(allowance: int) -> Iterator[CappedCounter]:
    """Every request through `cli._panel_transport` for the body, counted, and refused past
    `allowance` (the module docstring)."""
    counter = CappedCounter(allowance)
    with daily_selection.counted_transport(counter):
        yield counter


@dataclass(frozen=True, slots=True, kw_only=True)
class Spent:
    total: int
    phase: int
    discarded: str | None
    unclosed: tuple[int, ...]
    invocations: int


class RequestLedger:
    """The smoke root's `requests.jsonl` (the module docstring)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def events(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        found: list[dict[str, Any]] = []
        text = self.path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            try:
                event = json.loads(line)
            except ValueError as error:
                raise SmokeBudgetError(
                    f"{self.path.name}, line {number}, is not JSON ({error}): the ledger is "
                    "written by this module alone and is left as it is"
                ) from error
            if (
                not isinstance(event, dict)
                or event.get("schema") != LEDGER_SCHEMA
                or not isinstance(event.get("event"), str)
            ):
                raise SmokeBudgetError(
                    f"{self.path.name}, line {number}, is not a {LEDGER_SCHEMA} event; the ledger "
                    "is left as it is"
                )
            found.append(event)
        return found

    def append(self, event: Mapping[str, Any]) -> None:
        line = json.dumps({**event, "schema": LEDGER_SCHEMA}, sort_keys=True, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def spent(self) -> Spent:
        events = self.events()
        ended = {event["id"]: event for event in events if event["event"] == "end"}
        starts = [event for event in events if event["event"] == "start"]
        phases = [int(event["phase"]) for event in events if event["event"] == "phase"]
        discarded = [str(event["reason"]) for event in events if event["event"] == "discarded"]
        return Spent(
            total=sum(int(event["count"]) for event in ended.values()),
            phase=phases[-1] if phases else 1,
            discarded=discarded[-1] if discarded else None,
            unclosed=tuple(int(event["id"]) for event in starts if event["id"] not in ended),
            invocations=len(starts),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CappedRun:
    invocation: int
    exit_code: int
    wrapped_exit_code: int | None
    sent: Mapping[str, int]
    reached: str | None
    seconds: float


def run_capped(
    ledger: RequestLedger,
    *,
    kind: str,
    arguments: Sequence[str],
    cap: int,
    ceiling: int | None,
    headroom: int,
    discard_on_cap: bool,
    call: Callable[[], int],
    clock: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
) -> CappedRun:
    """`call` under the cap, recorded in `ledger`; see the module docstring for every refusal."""
    spent = ledger.spent()
    if spent.discarded is not None:
        raise SmokeBudgetError(
            f"this smoke root was discarded ({spent.discarded}); nothing more runs in it -- "
            "prepare a new one"
        )
    if spent.unclosed:
        raise SmokeBudgetError(
            f"invocation(s) {list(spent.unclosed)} started and never recorded their end -- the "
            "process was killed: what they sent is unknown and the panel copy may be "
            "half-written. Discard this root and prepare a new one"
        )
    remaining = cap - spent.total
    if remaining < headroom:
        raise SmokeBudgetError(
            f"{remaining} request(s) of phase {spent.phase}'s cap of {cap} remain, and a {kind} "
            f"needs {headroom} to start; ask the user before the cap is raised"
        )
    allowance = remaining if ceiling is None else min(remaining, ceiling)
    invocation = spent.invocations + 1
    ledger.append(
        {
            "event": "start",
            "id": invocation,
            "kind": kind,
            "arguments": list(arguments),
            "phase": spent.phase,
            "cap": cap,
            "allowance": allowance,
            "spent_before": spent.total,
            "at": clock().isoformat(),
        }
    )
    started = monotonic()
    counter = CappedCounter(allowance)
    wrapped: int | None = None
    fault: str | None = None
    try:
        with daily_selection.counted_transport(counter):
            try:
                wrapped = call()
            except RequestCapReached:
                wrapped = None
            except SystemExit as error:
                wrapped = error.code if isinstance(error.code, int) else 1
    except BaseException as error:
        fault = type(error).__name__
        raise
    finally:
        seconds = round(monotonic() - started, 3)
        ledger.append(
            {
                "event": "end",
                "id": invocation,
                "exit_code": wrapped,
                "fault": fault,
                "requests": dict(sorted(counter.items())),
                "count": counter.sent,
                "reached": counter.reached,
                "seconds": seconds,
                "at": clock().isoformat(),
            }
        )
        if discard_on_cap and (counter.reached is not None or fault is not None):
            cause = (
                f"the cap stopped its {counter.reached} request"
                if counter.reached is not None
                else f"it ended on {fault}"
            )
            ledger.append(
                {
                    "event": "discarded",
                    "reason": f"invocation {invocation} ({kind}) wrote the panel and {cause}",
                    "at": clock().isoformat(),
                }
            )
    return CappedRun(
        invocation=invocation,
        exit_code=CAP_EXIT if counter.reached is not None else int(wrapped or 0),
        wrapped_exit_code=wrapped,
        sent=dict(sorted(counter.items())),
        reached=counter.reached,
        seconds=seconds,
    )
```

（`CappedCounter.reached` 记**第一个**被拒的 `api_name`：一次调用里上限之后的每个请求都被拒，第一个才说明是哪里撞上的。）

- [ ] **Step 4：运行，确认通过**；`tests/unit` 全量、ruff、mypy、lint-imports。

- [ ] **Step 5：变异探针**（`scripts/p7_smoke_requests.py`）：
  1. `__setitem__` 里 `total > self.allowance` 改为 `total > self.allowance + 1` → `test_the_request_past_the_cap_never_reaches_the_transport` 与 `test_an_ordinary_day_under_the_cap_sends_at_most_its_allowance[10]` 变红。
  2. 删掉 `if spent.unclosed:` 那一段 → `test_an_invocation_that_never_recorded_its_end_refuses_every_later_one` 变红。
  3. `discard_on_cap and (counter.reached is not None or fault is not None)` 去掉 `or fault is not None` → `test_an_interrupted_invocation_that_writes_the_panel_discards_the_root` 变红。
  4. `allowance = remaining if ceiling is None else min(remaining, ceiling)` 改为 `allowance = remaining` → `test_a_ceiling_bounds_one_invocation_and_its_breach_need_not_discard` 变红。
  5. `remaining < headroom` 改为 `remaining < 0` → `test_an_invocation_with_less_than_its_headroom_left_is_refused_before_it_starts` 变红。

- [ ] **Step 6：提交**：`feat(V2-P7-019): a hard request cap for the smoke, riding the daily command's own counter`。

---

## Task 3：一次性冒烟环境（`scripts/p7_smoke_env.py`）

**Issue:** `V2-P7-019`。**依据:** 路线调整 §3 第 1、2 条，V1、V4、V5；F6–F10、F15、F17。

**做什么（`prepare_smoke`，0 次请求），按顺序；越过第 (6) 步之前的每一种拒绝都什么也不建：**
1. 路径规则（`PathPolicy.checked`）：冒烟根目录解析后不含空白、不在任何临时目录（`/tmp`、`/private/tmp`、`/var/folders`、`/private/var/folders`、`tempfile.gettempdir()`）、不在主检出里、不在 P6 运行目录（`~/openalpha-research`）里；它必须**不存在**、父目录存在。
2. 源：主检出里有 `.env`（只看 `is_file()`，从不打开）；源面板里有 `catalog.duckdb`；两个上限是整数且第二个不小于第一个。
3. 提交：完整的 40 位 id；在源仓库里存在；**源仓库的 `HEAD` 就是它**、且受绑定路径（`registry.REGISTERED_PATHS` + 两个脚本）没有未提交改动——因为冒烟登记由这个检出的代码写出，必须就是被克隆的代码；它在 `main` 的历史上（`merge-base --is-ancestor <commit> refs/heads/main`）。所有 git 调用丢掉继承的 `GIT_*`、`GIT_CONFIG_GLOBAL=os.devnull`、`GIT_CONFIG_NOSYSTEM=1`、`GIT_OPTIONAL_LOCKS=0`（`git status` 不刷新主检出的索引）。
4. 同一卷：`device_of(源面板) == device_of(根目录的父目录)`，否则 `cp -c` 不是克隆（会真的复制 6 GB）。
5. 配置先验：`strategy_survivor.read_survivor_registration(smoke_configuration(epoch, book), seed=grid.PROTOCOL_RANDOM_SEED)` 读得通。
6. 克隆：`git clone --quiet --local --no-checkout <source> <root>/repo` → `checkout -b v2-p7-019-smoke <commit>` → `remote remove origin`（克隆里不再有指回主检出的远端：手滑的 push 无处可去）。
7. 登记：`registry.register(配置, SMOKE_CRITERIA, <repo>/docs/research/smoke/v2-p7-019-smoke-registration.json, code_commit=<commit>, settings=smoke_settings())`；`git add`；以固定身份 `-c user.name="V2-P7-019 smoke" -c user.email=smoke@openalpha.invalid -c commit.gpgsign=false commit --no-verify`；`registry.registered_checkout(...)` 得到登记提交。
8. 面板：`copy_tree(源面板, <root>/runtime/panel)`（缺省 `cp -c -R`，只在 macOS；测试注入 `shutil.copytree`）；源面板复制前后的文件清单（相对路径 → 大小）相同、副本与源相同；记下源 `catalog.duckdb` 的 sha256。
9. 钉住：`daily_selection.pin_worktree(登记, <root>/repo, <root>/pinned)`、`daily_selection.sync_pinned_environment(<root>/pinned, uv=uv)`、`daily_selection.link_env_file(<主检出>, <root>/pinned)`——链接指向**主检出**的 `.env`（F10：`--repo` 是克隆时，链接会指向克隆里并不存在的 `.env`）。
10. 写 `<root>/smoke.json`（只写一次）与账本的 `prepared` 事件。

**Files:**
- Create: `scripts/p7_smoke_env.py`
- Create: `tests/unit/scripts/test_p7_smoke_env.py`

**Interfaces（产出）：**
- 常量：`SMOKE_SCHEMA = "openalpha-p7-smoke/v1"`、`MANIFEST_NAME = "smoke.json"`、`LEDGER_NAME = "requests.jsonl"`、`REPO_NAME = "repo"`、`PINNED_NAME = "pinned"`、`RUNTIME_NAME = "runtime"`、`LOGS_NAME = "logs"`、`REGISTRATION_PATH = "docs/research/smoke/v2-p7-019-smoke-registration.json"`、`SMOKE_BRANCH = "v2-p7-019-smoke"`、`FORWARD_REPORT_SCRIPT = "scripts/forward_report.py"`、`DESIGN_ADAPTER`（设计的 adapter 块，不含 `epoch_session`）、`SMOKE_PROTOCOL`、`SMOKE_MARKER`、`SMOKE_CRITERIA`
- `class SmokeEnvironmentError(ValueError)`
- `PathPolicy`（冻结）：`temporary_roots: tuple[Path, ...]`、`main_checkout: Path`、`research_runtime: Path`；`PathPolicy.default(main_checkout: Path) -> PathPolicy`；`checked(name: str, path: Path) -> Path`
- `smoke_configuration(epoch: date, *, book: SurvivorBookConfig | None = None) -> dict[str, Any]`；`smoke_settings() -> dict[str, Any]`
- `clone_command(source: Path, target: Path) -> list[str]`；`apfs_clone(source: Path, target: Path) -> None`；`tree_manifest(root: Path) -> dict[str, int]`
- `SmokeManifest`（冻结）：`root`、`commit`、`registration_commit`、`registration_sha256`、`config_id`、`epoch: date`、`catch_up_bound: int`、`source_panel: Path`、`source_catalog_sha256: str`、`panel_files: int`、`caps: Mapping[int, int]`、`created_at: datetime`；属性 `repo`、`pinned`、`runtime`、`registration`（`pinned / REGISTRATION_PATH`）、`ledger_path`、`logs`；`write() -> None`；`read(root: Path) -> SmokeManifest`（类方法）
- `prepare_smoke(*, root: Path, source_repo: Path, commit: str, source_panel: Path, epoch: date, caps: Mapping[int, int], policy: PathPolicy, uv: Path, clock: Callable[[], datetime], book: SurvivorBookConfig | None = None, copy_tree: Callable[[Path, Path], None] = apfs_clone, device_of: Callable[[Path], int] = _device) -> SmokeManifest`

**冒烟登记的内容（占位值，标明"冒烟，不是 006 的提案"）：** 配置 = 设计的书 `SurvivorBookConfig(seed=grid.PROTOCOL_RANDOM_SEED)` 与设计的 adapter 块（`tests/unit/scripts/test_daily_selection_dispatch.py:47-68` 的值：`min_coverage` 0.5、`max_carry_sessions` 250、`max_panel_catch_up_sessions` 10 等）加 `epoch_session`；`settings` = `grid.protocol_settings()` + `survivor_forward_protocol`（`anchor_ordinal` 1、`primary_horizon` 20、`secondary_horizons` [5]、`primary_metric` `rank_ic`、`secondary_metrics` [`survivors_minus_ew`, `ew_minus_worst_decile`]、`window_periods` 2、`bootstrap_samples` 1000、`random_seed` = `grid.PROTOCOL_RANDOM_SEED`、`q` 0.1）+ `smoke_run`（`{"issue": "V2-P7-019", "note": "…"}`）；`criteria` = `{"smoke": "V2-P7-019: no criterion; nothing this registration produces is evidence"}`。这些值是**用户决定 5** 的推荐（见文末）。

### Steps

- [ ] **Step 1：写失败的测试** `tests/unit/scripts/test_p7_smoke_env.py`：

```python
"""`scripts/p7_smoke_env.py`: the real-data smoke's one-off environment (`V2-P7-019`, task 3).

`prepare_smoke` builds a smoke root that touches nothing it was made from: a `git clone --local` of
a `main` commit with the smoke registration committed in the clone only and no remote, an APFS
clone of the panel (here `shutil.copytree`, injected: the source is a tiny tree), and a worktree
pinned at the registration with its own environment (the offline sync is replaced through
`daily_selection._run_sync`, the seam the daily command's own tests use) and a `.env` linked to the
source checkout's, never read. Every refusal before the clone makes nothing. The source checkout
is a throwaway `main` repository; nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
from research_repo import git, head

from openalpha_cn import strategy_survivor
from openalpha_cn.domain.survivor_book import SurvivorBookConfig

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


env = _script("p7_smoke_env")
daily = tds.daily
SENTINEL: Final[str] = "sk-smoke-env-sentinel-never-read-01919"
"""The source checkout's `.env` holds this: it must appear in nothing the prepare writes."""
NOW: Final[datetime] = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
EPOCH: Final[date] = survivor_day.EPOCH
BOOK: Final[SurvivorBookConfig] = survivor_day.BOOK
CAPS: Final[dict[int, int]] = {1: 190, 2: 460}


def _source_repository(root: Path) -> Path:
    """A throwaway `main` checkout: one file under each bound path (the weekly report's too), a
    `.python-version`, the ignore rules for `.venv/` and `.env`, LF line ends -- and a `.env`."""
    repo = root / "main"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "--template=", "-b", "main")
    for name in (*tds.BOUND, "scripts/forward_report.py"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(tds.LOCK if name == "uv.lock" else "RULES = 1\n", encoding="utf-8")
        git(repo, "add", name)
    (repo / ".python-version").write_text(f"{tds.RUNNING_PYTHON}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n.env\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    git(repo, "add", ".python-version", ".gitignore", ".gitattributes")
    git(repo, "commit", "-q", "-m", "initial", at=tds.COMMITTED)
    (repo / ".env").write_text(f"TUSHARE_TOKEN={SENTINEL}\n", encoding="utf-8")
    return repo


def _panel(root: Path) -> Path:
    """A tiny tree shaped like a panel: the research runtime directory holds it."""
    panel = root / "research" / "panel"
    (panel / "daily" / "2026").mkdir(parents=True)
    (panel / "daily" / "2026" / "data.parquet").write_bytes(b"\x00" * 64)
    (panel / "catalog.duckdb").write_bytes(b"catalog")
    (panel / "catalog.duckdb.lock").write_bytes(b"")
    return panel


def _offline_sync(
    command: Sequence[str], *, environment: Mapping[str, str], cwd: Path
) -> subprocess.CompletedProcess[bytes]:
    """`daily_selection._run_sync` without uv: the environment's `pyvenv.cfg`, as uv writes it."""
    venv = Path(environment["UV_PROJECT_ENVIRONMENT"])
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text(f"version_info = {tds.RUNNING_PYTHON}.0\n", encoding="utf-8")
    return subprocess.CompletedProcess(list(command), 0, b"", b"")


def _policy(tmp_path: Path, source: Path) -> Any:
    return env.PathPolicy(
        temporary_roots=(tmp_path / "volatile",),
        main_checkout=source,
        research_runtime=tmp_path / "research",
    )


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
    panel: Path,
    *,
    root: Path | None = None,
    commit: str | None = None,
    caps: Mapping[int, int] = CAPS,
    copy_tree: Callable[[Path, Path], None] = shutil.copytree,
    device_of: Callable[[Path], int] | None = None,
) -> Any:
    monkeypatch.setattr(daily, "_run_sync", _offline_sync)
    keywords: dict[str, Any] = {} if device_of is None else {"device_of": device_of}
    return env.prepare_smoke(
        root=root or tmp_path / "smoke",
        source_repo=source,
        commit=commit or head(source),
        source_panel=panel,
        epoch=EPOCH,
        caps=caps,
        policy=_policy(tmp_path, source),
        uv=Path("uv"),
        clock=lambda: NOW,
        book=BOOK,
        copy_tree=copy_tree,
        **keywords,
    )


def _refs(repo: Path) -> tuple[str, str, str, str]:
    return (
        head(repo),
        git(repo, "for-each-ref"),
        git(repo, "status", "--porcelain", "--ignored"),
        git(repo, "worktree", "list", "--porcelain"),
    )


# --- the registration ----------------------------------------------------------------------------


def test_the_smoke_configuration_is_the_design_s_with_the_epoch() -> None:
    configuration = env.smoke_configuration(EPOCH, book=BOOK)

    assert configuration == survivor_day.survivor_config(epoch=EPOCH, book=BOOK)
    default = env.smoke_configuration(EPOCH)
    assert default["survivor_book"] == SurvivorBookConfig(
        seed=tds.grid.PROTOCOL_RANDOM_SEED
    ).model_dump(mode="json", exclude_computed_fields=True)
    strategy_survivor.read_survivor_registration(default, seed=tds.grid.PROTOCOL_RANDOM_SEED)


def test_the_smoke_settings_carry_the_marker_a_placeholder_protocol_and_the_registered_seed() -> (
    None
):
    settings = env.smoke_settings()

    assert settings[daily.SMOKE_SETTINGS_KEY]["issue"] == "V2-P7-019"
    assert "not a V2-P7-006 proposal" in settings[daily.SMOKE_SETTINGS_KEY]["note"]
    protocol = settings["survivor_forward_protocol"]
    assert (protocol["schema_version"], protocol["primary_horizon"]) == (
        "survivor-forward-protocol/v1",
        20,
    )
    assert settings["random_seed"] == tds.grid.PROTOCOL_RANDOM_SEED
    assert {key: settings[key] for key in tds.grid.protocol_settings()} == (
        tds.grid.protocol_settings()
    )


def test_prepare_clones_the_commit_and_commits_a_smoke_registration_in_the_clone_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    before = _refs(source)

    manifest = _prepare(tmp_path, monkeypatch, source, panel)

    repo = manifest.repo
    registration = repo / env.REGISTRATION_PATH
    assert git(repo, "rev-parse", "HEAD^").strip() == head(source) == manifest.commit
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == env.SMOKE_BRANCH
    assert git(repo, "remote").strip() == ""
    assert git(repo, "diff", "--name-only", "HEAD^", "HEAD").split() == [env.REGISTRATION_PATH]
    assert git(repo, "show", f"HEAD:{env.REGISTRATION_PATH}") == registration.read_text(
        encoding="utf-8"
    )
    assert _refs(source) == before
    body = json.loads(registration.read_text(encoding="utf-8"))
    assert body["config"] == env.smoke_configuration(EPOCH, book=BOOK)
    assert body["config_id"] == manifest.config_id == tds.grid.config_id(body["config"])
    assert body["code_commit"] == head(source)
    assert body["settings"] == env.smoke_settings()
    assert body["criteria"] == env.SMOKE_CRITERIA
    assert hashlib.sha256(registration.read_bytes()).hexdigest() == manifest.registration_sha256
    assert manifest.registration_commit == head(repo)
    assert manifest.catch_up_bound == 10


# --- the panel -----------------------------------------------------------------------------------


def test_prepare_clones_the_panel_whole_and_leaves_the_source_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    stamps = {
        path.relative_to(panel).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in panel.rglob("*")
        if path.is_file()
    }
    copied: list[tuple[Path, Path]] = []

    def copy(from_: Path, to: Path) -> None:
        copied.append((from_, to))
        shutil.copytree(from_, to)

    manifest = _prepare(tmp_path, monkeypatch, source, panel, copy_tree=copy)

    assert copied == [(panel.resolve(), manifest.runtime / "panel")]
    assert env.tree_manifest(manifest.runtime / "panel") == env.tree_manifest(panel)
    assert manifest.panel_files == 3
    assert manifest.source_catalog_sha256 == hashlib.sha256(b"catalog").hexdigest()
    assert {
        path.relative_to(panel).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in panel.rglob("*")
        if path.is_file()
    } == stamps


def test_prepare_refuses_a_clone_that_is_not_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    def short(from_: Path, to: Path) -> None:
        shutil.copytree(from_, to)
        (to / "daily" / "2026" / "data.parquet").unlink()

    with pytest.raises(env.SmokeEnvironmentError, match="not the panel"):
        _prepare(tmp_path, monkeypatch, source, panel, copy_tree=short)


def test_prepare_refuses_a_source_that_changed_while_it_was_cloned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    def racing(from_: Path, to: Path) -> None:
        shutil.copytree(from_, to)
        (from_ / "daily" / "2026" / "data.parquet").write_bytes(b"\x00" * 65)

    with pytest.raises(env.SmokeEnvironmentError, match="changed while it was cloned"):
        _prepare(tmp_path, monkeypatch, source, panel, copy_tree=racing)


def test_prepare_refuses_a_panel_on_another_volume_before_it_makes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    with pytest.raises(env.SmokeEnvironmentError, match="different volumes"):
        _prepare(
            tmp_path,
            monkeypatch,
            source,
            panel,
            device_of=lambda path: 1 if "research" in path.parts else 2,
        )

    assert not (tmp_path / "smoke").exists()


def test_the_panel_is_cloned_with_cp_c() -> None:
    assert env.clone_command(Path("a"), Path("b")) == ["cp", "-c", "-R", "a", "b"]


# --- the pinned worktree -------------------------------------------------------------------------


def test_prepare_pins_a_worktree_with_its_own_environment_and_links_the_env_file_without_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    manifest = _prepare(tmp_path, monkeypatch, source, panel)

    pinned = manifest.pinned
    assert head(pinned) == manifest.registration_commit
    assert (pinned / ".venv").is_dir()
    assert not (pinned / ".venv").is_symlink()
    link = pinned / ".env"
    assert link.is_symlink()
    target = Path(os.path.normpath(link.parent / daily.link_text_as_path(os.readlink(link))))
    assert target == source.resolve() / ".env"
    assert git(pinned, "status", "--porcelain").strip() == ""
    assert manifest.registration == pinned / env.REGISTRATION_PATH
    assert manifest.registration.is_file()
    for written in (manifest.root / env.MANIFEST_NAME, manifest.root / env.LEDGER_NAME):
        assert SENTINEL not in written.read_text(encoding="utf-8")


def test_the_manifest_and_the_ledger_say_what_was_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    manifest = _prepare(tmp_path, monkeypatch, source, panel)

    assert env.SmokeManifest.read(manifest.root) == manifest
    events = [
        json.loads(line)
        for line in (manifest.root / env.LEDGER_NAME).read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == ["prepared"]
    assert events[0]["caps"] == {"1": 190, "2": 460}
    with pytest.raises(env.SmokeEnvironmentError, match="written once"):
        manifest.write()


# --- refusals ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("where", "named"),
    [
        ("space", "whitespace"),
        ("volatile", "temporary"),
        ("main", "main checkout"),
        ("research", "P6's runtime"),
        ("exists", "exists"),
    ],
)
def test_prepare_refuses_an_unsafe_smoke_root_before_it_makes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str, named: str
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    (tmp_path / "volatile").mkdir()
    (tmp_path / "taken").mkdir()
    root = {
        "space": tmp_path / "smoke root",
        "volatile": tmp_path / "volatile" / "smoke",
        "main": source / "smoke",
        "research": tmp_path / "research" / "smoke",
        "exists": tmp_path / "taken",
    }[where]
    before = _refs(source)

    with pytest.raises(env.SmokeEnvironmentError, match=named):
        _prepare(tmp_path, monkeypatch, source, panel, root=root)

    assert not (root / env.REPO_NAME).exists()
    assert where == "exists" or not root.exists()
    assert _refs(source) == before


@pytest.mark.parametrize("case", ["short", "side branch", "behind HEAD", "dirty"])
def test_prepare_refuses_a_commit_that_is_not_the_checkout_s_clean_main_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    commit = head(source)
    if case == "short":
        commit = commit[:12]
    if case == "side branch":
        git(source, "checkout", "-q", "-b", "side")
        (source / "notes.txt").write_text("side\n", encoding="utf-8")
        git(source, "add", "notes.txt")
        git(source, "commit", "-q", "-m", "side", at=tds.COMMITTED)
        commit = head(source)
    if case == "behind HEAD":
        (source / "notes.txt").write_text("later\n", encoding="utf-8")
        git(source, "add", "notes.txt")
        git(source, "commit", "-q", "-m", "later", at=tds.COMMITTED)
    if case == "dirty":
        bound = source / "src" / "openalpha_cn" / "strategy.py"
        bound.write_text("RULES = 2\n", encoding="utf-8")

    with pytest.raises(env.SmokeEnvironmentError):
        _prepare(tmp_path, monkeypatch, source, panel, commit=commit)

    assert not (tmp_path / "smoke").exists()


def test_prepare_refuses_without_the_env_file_it_links_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)
    (source / ".env").unlink()

    with pytest.raises(env.SmokeEnvironmentError, match=r"\.env"):
        _prepare(tmp_path, monkeypatch, source, panel)

    assert not (tmp_path / "smoke").exists()


@pytest.mark.parametrize("caps", [{1: 190}, {1: 190, 2: 100}, {1: -1, 2: 400}, {1: 1.5, 2: 400}])
def test_prepare_refuses_caps_that_are_not_two_whole_numbers_the_second_no_smaller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caps: Mapping[int, Any]
) -> None:
    source, panel = _source_repository(tmp_path), _panel(tmp_path)

    with pytest.raises(env.SmokeEnvironmentError, match="cap"):
        _prepare(tmp_path, monkeypatch, source, panel, caps=caps)


def test_the_default_policy_forbids_the_temporary_roots_and_the_research_runtime(
    tmp_path: Path,
) -> None:
    policy = env.PathPolicy.default(tmp_path)

    for root in (Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir()).resolve()):
        assert root in policy.temporary_roots or root.resolve() in policy.temporary_roots
    assert policy.research_runtime == (Path.home() / "openalpha-research").resolve()
    assert policy.main_checkout == tmp_path.resolve()
```

- [ ] **Step 2：运行，确认失败**：`ModuleNotFoundError: No module named 'p7_smoke_env'`。

- [ ] **Step 3：实现** `scripts/p7_smoke_env.py`。模块文档写清上面"做什么"的十步与每条拒绝的理由；关键代码：

```python
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402
import grid  # noqa: E402
import registry  # noqa: E402

from openalpha_cn import strategy_survivor  # noqa: E402
from openalpha_cn.domain.survivor_book import SurvivorBookConfig  # noqa: E402

SMOKE_SCHEMA: Final[str] = "openalpha-p7-smoke/v1"
MANIFEST_NAME: Final[str] = "smoke.json"
LEDGER_NAME: Final[str] = "requests.jsonl"
REPO_NAME: Final[str] = "repo"
PINNED_NAME: Final[str] = "pinned"
RUNTIME_NAME: Final[str] = "runtime"
LOGS_NAME: Final[str] = "logs"
REGISTRATION_PATH: Final[str] = "docs/research/smoke/v2-p7-019-smoke-registration.json"
SMOKE_BRANCH: Final[str] = "v2-p7-019-smoke"
FORWARD_REPORT_SCRIPT: Final[str] = "scripts/forward_report.py"
BOUND: Final[tuple[str, ...]] = (
    *registry.REGISTERED_PATHS,
    daily_selection.THIS_SCRIPT,
    FORWARD_REPORT_SCRIPT,
)
SMOKE_IDENTITY: Final[tuple[str, ...]] = (
    "-c",
    "user.name=V2-P7-019 smoke",
    "-c",
    "user.email=smoke@openalpha.invalid",
    "-c",
    "commit.gpgsign=false",
)
COMMIT_MESSAGE: Final[str] = (
    "smoke(V2-P7-019): a one-off smoke registration -- committed in this throwaway clone only, "
    "never pushed; not the V2-P7-006 registration"
)
_FULL_COMMIT: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")

DESIGN_ADAPTER: Final[dict[str, Any]] = {
    "schema_version": "survivor-adapter/v1",
    "exchange": "SSE",
    "transform": "cross_section_standard/v1",
    "min_coverage": 0.5,
    "max_carry_sessions": 250,
    "max_panel_catch_up_sessions": 10,
    "benchmark": "000905.SH",
    "prediction_horizon": "20d",
    "cost_policy": "protocol/v1",
    "paper_limits": {
        "max_position_weight": "1",
        "max_total_exposure": "1",
        "min_cash_weight": "0",
    },
    "unfilled_buy": "reject_whole_and_carry/v1",
}
"""The design's adapter block (Part D), less its epoch: `test_daily_selection_dispatch.py`'s
`SURVIVOR_CONFIG`, held equal to it by
`test_the_smoke_configuration_is_the_design_s_with_the_epoch`."""

SMOKE_PROTOCOL: Final[dict[str, Any]] = {
    "schema_version": "survivor-forward-protocol/v1",
    "anchor_ordinal": 1,
    "primary_horizon": 20,
    "secondary_horizons": [5],
    "primary_metric": "rank_ic",
    "secondary_metrics": ["survivors_minus_ew", "ew_minus_worst_decile"],
    "window_periods": 2,
    "bootstrap_samples": 1000,
    "random_seed": grid.PROTOCOL_RANDOM_SEED,
    "q": 0.1,
}
"""A placeholder the weekly report needs to run at all (`read_forward_protocol` has no default):
smoke values, not a `V2-P7-006` proposal -- the primary horizon is the one every survivor record
declares (20)."""

SMOKE_MARKER: Final[dict[str, str]] = {
    "issue": "V2-P7-019",
    "note": (
        "a one-off real-data smoke registration, committed only in its throwaway clone and never "
        "pushed; not a V2-P7-006 proposal; its forward protocol is a placeholder the weekly report "
        "needs to run"
    ),
}
SMOKE_CRITERIA: Final[dict[str, str]] = {
    "smoke": "V2-P7-019: no criterion; nothing this registration produces is evidence"
}


class SmokeEnvironmentError(ValueError):
    """A smoke environment that cannot be prepared as asked; nothing is made before the clone."""


def _within(path: Path, root: Path) -> bool:
    return any(path == base or base in path.parents for base in {root, root.resolve()})


@dataclass(frozen=True, slots=True, kw_only=True)
class PathPolicy:
    temporary_roots: tuple[Path, ...]
    main_checkout: Path
    research_runtime: Path

    @classmethod
    def default(cls, main_checkout: Path) -> PathPolicy:
        roots = {
            Path("/tmp"),
            Path("/private/tmp"),
            Path("/var/folders"),
            Path("/private/var/folders"),
            Path(tempfile.gettempdir()),
        }
        spelled = roots | {root.resolve() for root in roots}
        return cls(
            temporary_roots=tuple(sorted(spelled, key=str)),
            main_checkout=main_checkout.resolve(),
            research_runtime=(Path.home() / "openalpha-research").resolve(),
        )

    def checked(self, name: str, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        for spelling in (str(path), str(resolved)):
            if any(character.isspace() for character in spelling):
                raise SmokeEnvironmentError(
                    f"{name} {spelling!r} contains whitespace: uv splits --env-file on it and the "
                    "pinned commands are printed for a shell; choose a path without spaces"
                )
        for root in self.temporary_roots:
            if _within(resolved, root):
                raise SmokeEnvironmentError(
                    f"{name} {resolved} is under {root}, a temporary directory the system may "
                    "clear: the smoke runs for weeks and its paper book must survive them"
                )
        if _within(resolved, self.main_checkout):
            raise SmokeEnvironmentError(
                f"{name} {resolved} is inside the main checkout {self.main_checkout}; the smoke "
                "never writes there"
            )
        if _within(resolved, self.research_runtime):
            raise SmokeEnvironmentError(
                f"{name} {resolved} is inside {self.research_runtime}, P6's runtime directory; "
                "the smoke reads its panel once, by an APFS clone, and writes nothing there"
            )
        return resolved


def _git(repo: Path, *args: str) -> str:
    """git in `repo`, hermetic: no inherited `GIT_*`, no global or system configuration, no
    optional lock (a status never refreshes the source checkout's index), no prompt."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment |= {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }
    finished = subprocess.run(
        ["git", *args], cwd=repo, env=environment, capture_output=True, text=True, check=False
    )
    if finished.returncode != 0:
        raise SmokeEnvironmentError(
            f"git {' '.join(args)} in {repo} failed: {finished.stderr.strip() or 'no message'}"
        )
    return finished.stdout.strip()


def _check_commit(source: Path, commit: str) -> None:
    if not _FULL_COMMIT.fullmatch(commit):
        raise SmokeEnvironmentError(f"{commit!r} is not a full 40-character commit id")
    _git(source, "cat-file", "-e", f"{commit}^{{commit}}")
    standing = _git(source, "rev-parse", "HEAD")
    if standing != commit:
        raise SmokeEnvironmentError(
            f"the checkout stands at {standing}, not at {commit}: the smoke registration is "
            "written by this checkout's code, which must be the code the clone holds"
        )
    try:
        _git(source, "merge-base", "--is-ancestor", commit, "refs/heads/main")
    except SmokeEnvironmentError as error:
        raise SmokeEnvironmentError(
            f"{commit} is not on main: the smoke runs main's code"
        ) from error
    dirty = _git(source, "status", "--porcelain", "--untracked-files=all", "--", *BOUND)
    if dirty:
        raise SmokeEnvironmentError(
            f"the bound code of {source} has uncommitted changes ({dirty.splitlines()[0]} ...): "
            "the registration would be written by code the clone does not hold"
        )


def smoke_configuration(epoch: date, *, book: SurvivorBookConfig | None = None) -> dict[str, Any]:
    chosen = book or SurvivorBookConfig(seed=grid.PROTOCOL_RANDOM_SEED)
    return {
        strategy_survivor.KIND_KEY: strategy_survivor.SURVIVOR_KIND,
        strategy_survivor.BOOK_BLOCK: chosen.model_dump(mode="json", exclude_computed_fields=True),
        strategy_survivor.ADAPTER_BLOCK: {**DESIGN_ADAPTER, "epoch_session": epoch.isoformat()},
    }


def smoke_settings() -> dict[str, Any]:
    return {
        **grid.protocol_settings(),
        "survivor_forward_protocol": dict(SMOKE_PROTOCOL),
        daily_selection.SMOKE_SETTINGS_KEY: dict(SMOKE_MARKER),
    }


def clone_command(source: Path, target: Path) -> list[str]:
    return ["cp", "-c", "-R", str(source), str(target)]


def apfs_clone(source: Path, target: Path) -> None:
    if sys.platform != "darwin":
        raise SmokeEnvironmentError(
            "the panel is cloned with cp -c (APFS clonefile), which is macOS's; nothing was copied"
        )
    finished = subprocess.run(
        clone_command(source, target), capture_output=True, text=True, check=False
    )
    if finished.returncode != 0:
        raise SmokeEnvironmentError(f"cp -c failed: {finished.stderr.strip()}")


def tree_manifest(root: Path) -> dict[str, int]:
    """Every file under `root` by its POSIX relative path, with its size."""
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _device(path: Path) -> int:
    return path.stat().st_dev
```

`SmokeManifest`（`to_json` / `write` 只写一次、已存在则拒绝 `"… is written once"` / `read` 校验 `schema`，`caps` 以字符串键存、读回整数键）与 `prepare_smoke` 按上面十步写；`daily_selection.pin_worktree`、`sync_pinned_environment`、`link_env_file` 抛的 `daily_selection.StepFailedError` 一律包成 `SmokeEnvironmentError`（消息带原文）；`link_env_file` 之后若 `<pinned>/.env` 不是符号链接，就以它返回的那一行拒绝。拒绝信息里出现"刚建的根目录"时说明：它由这次 prepare 建、别无他物，由用户删掉后再 prepare（工具不删任何东西）。

- [ ] **Step 4：运行，确认通过**；`tests/unit` 全量（注意 `test_repository_assets.py` 对新脚本的 AST 检查）、ruff、mypy、lint-imports。

- [ ] **Step 5：变异探针**（`scripts/p7_smoke_env.py`）：
  1. 删掉 `_git(repo, "remote", "remove", "origin")` → `test_prepare_clones_the_commit_and_commits_a_smoke_registration_in_the_clone_only` 变红。
  2. `link_env_file(source_repo, pinned)` 改为 `link_env_file(repo, pinned)`（运行手册原来的写法）→ `test_prepare_pins_a_worktree_…` 变红（没链上）。
  3. `PathPolicy.checked` 里删掉临时目录那一段 → `test_prepare_refuses_an_unsafe_smoke_root_before_it_makes_anything[volatile-temporary]` 变红。
  4. 删掉"复制前后源清单相同"的比较 → `test_prepare_refuses_a_source_that_changed_while_it_was_cloned` 变红。
  5. `_check_commit` 里删掉 `standing != commit` 那一段 → `test_prepare_refuses_a_commit_that_is_not_the_checkout_s_clean_main_head[behind HEAD]` 变红。

- [ ] **Step 6：提交**：`feat(V2-P7-019): the one-off smoke environment -- clone, registration, panel clone, pinned worktree`。

---

## Task 4：驱动命令行（`scripts/p7_smoke.py`）

**Issue:** `V2-P7-019`。**依据:** 路线调整 §3 第 3–5 条；F1、F5、F6、F11、F12、F18。

**子命令：**

| 子命令 | 在哪里跑 | 做什么 | 上限规则（`RULES`） |
|---|---|---|---|
| `prepare --smoke-root DIR --epoch D --phase1-cap N --phase2-cap M [--commit SHA] [--source-panel DIR] [--uv PATH]` | 主检出（`uv run --no-sync python scripts/p7_smoke.py prepare …`） | `p7_smoke_env.prepare_smoke`，`PathPolicy.default(daily_selection.REPOSITORY)`，`source_repo` = 本检出，`commit` 缺省本检出的 `HEAD`，`source_panel` 缺省 `~/openalpha-research/panel`；打印根目录、提交、登记 sha256 与 `config_id`、纪元、面板文件数、下一步命令 | 0 次请求（不经上限） |
| `evening [-- 额外参数]` | 钉住的 worktree | `daily_selection.main(--runtime-dir RT --registration REG --repo PINNED + 额外参数)` | 无单次上限；起步余量 `ORDINARY_EVENING_REQUESTS` = 22；写面板 → 中断或触发即丢弃 |
| `morning [-- 额外参数]` | 同上 | 同上加 `--morning-recheck` | 单次上限 3；起步余量 1；不丢弃 |
| `weekly [-- 额外参数]` | 同上 | `forward_report.main(--runtime-dir RT --registration REG --repo PINNED + 额外参数)` | 单次上限 0；不丢弃 |
| `remedy LINE` 或 `remedy -`（从 stdin 读） | 同上 | 读回拒绝信息打印的一行（`parse_remedy`），按种类运行：`panel build`/`factor build`（`daily_selection.invoke`）或再跑这一天 | `panel-build`：无单次上限，起步余量 `7 × (catch_up_bound + 1)`，丢弃；`factor-build`：单次上限 0；`evening`/`morning` 同上 |
| `status` | 任意 | 阶段、上限、已花、剩余、是否丢弃、未关闭的调用、每次调用一行、按接口合计 | — |
| `phase --to 2 --reason TEXT` | 任意 | 阶段 1 → 2（账本 `phase` 事件），只此一种 | — |

额外参数里出现 `--runtime-dir`、`--registration`、`--repo`、`--morning-recheck`、`--pin-worktree`、`--launchd-plist`、`--factor-gaps` 一律拒绝（驱动设定它们）。`--smoke-root` 可省：脚本位于 `<root>/pinned/scripts/p7_smoke.py` 且 `<root>/smoke.json` 存在时就是 `<root>`。运行 `panel-build`/`factor-build` 之前先 `registry._refuse_a_foreign_package(manifest.pinned)`（从主检出跑驱动，跑的就不是登记的代码，退出 3）；日、清晨、周报由它们自己的准入把关。驱动锁 `<root>/driver.lock`（`openalpha_cn.panel.store._try_lock` / `_unlock`，F18）：同一根目录同时只有一个调用。每次调用的输出同时写进 `<root>/logs/<编号>-<种类>.out|.err`。SIGTERM、SIGHUP 在调用期间转成 `KeyboardInterrupt`（于是账本记下结束，写面板的调用丢弃根目录），调用结束后恢复原来的信号处理。退出码：被包住的命令自己的码；上限触发 `CAP_EXIT` = 4；驱动拒绝 `REFUSED_EXIT` = 5；中断 130。

**`parse_remedy(line, *, runtime, registration, pinned) -> Remedy`**（`Remedy` = 冻结 `(kind: str, arguments: tuple[str, ...])`）：`shlex.split`；找到以 `/.venv/bin/openalpha` 结尾的词（其后是 `panel build …` 或 `factor build …`，`arguments` = 从 `panel`/`factor` 起的全部），或以 `/.venv/bin/python` 结尾、下一个词是 `scripts/daily_selection.py` 的词（`arguments` = 去掉 `--runtime-dir`/`--registration`/`--repo` 之后的其余；含 `--morning-recheck` 则种类为 `morning` 并去掉它）。拒绝（`RemedyError`）：没有这两种程序；`openalpha` 的其他子命令；`--runtime-dir` 不是本冒烟的运行目录；日的 `--registration` 不是本冒烟登记、`--repo` 不是钉住的 worktree；`panel build` **不带** `--incremental` 且 `--dataset` 不只是 `trade_cal`——这是"整年重建"那条补救（F11），消息写明"约 1,200 次请求，冒烟从不跑它，停下、丢弃根目录、从原面板重新准备"。

**上限触发时打印**（stderr）：`REQUEST CAP REACHED  invocation N stopped: the <api> request that would have passed phase P's cap of C was not sent`；每日命令自己的请求行多算了这一次；根目录已丢弃、面板副本可能半写；**不要**在它上面跑任何 `panel build` 补救，尤其不要照整年重建（约 1,200 次）；先问用户，再从原面板、用新纪元 `prepare` 一个新根目录（打印完整命令的形状）。

**Files:**
- Create: `scripts/p7_smoke.py`
- Create: `tests/unit/scripts/test_p7_smoke_driver.py`

**Interfaces（产出）：** `REFUSED_EXIT = 5`、`ORDINARY_EVENING_REQUESTS = 22`、`PAGED_SESSION_REQUESTS = 7`、`DRIVER_LOCK = "driver.lock"`；`class SmokeDriverError(ValueError)`、`class RemedyError(SmokeDriverError)`；`Remedy`；`KindRule(ceiling: int | None, discard_on_cap: bool, headroom: Callable[[SmokeManifest], int])`、`RULES: Mapping[str, KindRule]`；`parse_remedy(...)`；`run(manifest, kind: str, arguments: Sequence[str], *, clock) -> int`；`status_lines(manifest) -> list[str]`；`main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int`。

### Steps

- [ ] **Step 1：写失败的测试** `tests/unit/scripts/test_p7_smoke_driver.py`：

```python
"""`scripts/p7_smoke.py`: the real-data smoke's driver (`V2-P7-019`, task 4), end to end.

A smoke root is prepared by `p7_smoke_env.prepare_smoke` from a throwaway `main` checkout
(`test_p7_smoke_env.py`'s) and `test_daily_selection_survivor_day.py`'s seeded panel (3 April), and
its pinned worktree is bound as the daily command's own tests bind a repository
(`test_daily_selection._bind`): the registry's import checks point at it and the panel's transport
at the scripted market. The driver then runs the real daily command, its morning re-check and the
weekly report in process, under the cap -- and the first night's catch-up from the remedy the
evening's refusal printed. Every count is checked against what reached the market. Nothing
reaches the network.
"""

from __future__ import annotations

import importlib
import os
import shutil
import signal
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import test_daily_selection as tds
import test_daily_selection_survivor_day as survivor_day
import test_p7_smoke_env as env_tests
from research_repo import head
from test_daily_selection_survivor import STEADY_SURVIVOR
from test_daily_selection_survivor_day import seeded  # noqa: F401  (a module fixture, reused)

from openalpha_cn import cli
from openalpha_cn.panel.store import _try_lock, _unlock

ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def _script(name: str) -> Any:
    directory = ROOT / "scripts"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return importlib.import_module(name)


driver = _script("p7_smoke")
env = _script("p7_smoke_env")
caps = _script("p7_smoke_requests")
daily = tds.daily
NOW: Final[datetime] = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
SESSION_REQUESTS: Final[int] = sum(
    len(cli.PANEL_BUILD_TARGETS[target]) for target in ("adj_factor", "price", "stk_limit")
)
"""Five: `adj_factor`, `daily`, `daily_basic`, `suspend_d`, `stk_limit` -- one each a session."""


@dataclass
class Smoke:
    root: Path
    manifest: Any
    market: survivor_day.SurvivorMarket


def _smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded_runtime: Path,
    *,
    epoch: date,
    caps_: dict[int, int] | None = None,
) -> Smoke:
    source = env_tests._source_repository(tmp_path)
    monkeypatch.setattr(daily, "_run_sync", env_tests._offline_sync)
    manifest = env.prepare_smoke(
        root=tmp_path / "smoke",
        source_repo=source,
        commit=head(source),
        source_panel=seeded_runtime / "panel",
        epoch=epoch,
        caps=caps_ or {1: 190, 2: 460},
        policy=env.PathPolicy(
            temporary_roots=(tmp_path / "volatile",),
            main_checkout=source,
            research_runtime=seeded_runtime,
        ),
        uv=Path("uv"),
        clock=lambda: NOW,
        book=survivor_day.BOOK,
        copy_tree=shutil.copytree,
    )
    market = survivor_day.SurvivorMarket(open_days=tds.OPEN_2026)
    tds._bind(monkeypatch, manifest.pinned, market)
    return Smoke(root=manifest.root, manifest=manifest, market=market)


def _drive(
    smoke: Smoke, capsys: pytest.CaptureFixture[str], *arguments: str, at: datetime
) -> tuple[int, str, list[str]]:
    command, *rest = arguments
    code = driver.main([command, "--smoke-root", str(smoke.root), *rest], clock=lambda: at)
    out, err = capsys.readouterr()
    assert tds.SECRET_TOKEN not in out + err
    assert env_tests.SENTINEL not in out + err
    return code, out, err.splitlines()


def _spent(smoke: Smoke) -> Any:
    return caps.RequestLedger(smoke.root / env.LEDGER_NAME).spent()


def _ends(smoke: Smoke) -> list[dict[str, Any]]:
    events = caps.RequestLedger(smoke.root / env.LEDGER_NAME).events()
    kinds = {event["id"]: event["kind"] for event in events if event["event"] == "start"}
    return [{**event, "kind": kinds[event["id"]]} for event in events if event["event"] == "end"]


_night = survivor_day.evening_of
"""A session's 18:30 in Shanghai: the scheduled evening's clock."""


# --- an evening ----------------------------------------------------------------------------------


def test_an_evening_through_the_driver_spends_exactly_what_reached_the_market(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    smoke.market.today = survivor_day.DAY
    as_of = survivor_day.DAY_AS_OF.isoformat()

    code, out, err = _drive(
        smoke, capsys, "evening", "--", "--as-of", as_of, at=survivor_day.RUN_CLOCK
    )

    assert code == 0, err
    lines = out.splitlines()
    assert daily.SMOKE_BANNER in lines
    assert sum(line.startswith("  BUY ") for line in lines) == 10
    assert _spent(smoke).total == len(smoke.market.payloads) == sum(STEADY_SURVIVOR.values())
    logs = sorted(path.name for path in (smoke.root / env.LOGS_NAME).iterdir())
    assert logs == ["0001-evening.err", "0001-evening.out"]
    assert daily.SMOKE_BANNER in (smoke.root / env.LOGS_NAME / "0001-evening.out").read_text(
        encoding="utf-8"
    )

    code, out, _err = _drive(
        smoke, capsys, "evening", "--", "--as-of", as_of, at=survivor_day.RUN_CLOCK
    )

    assert code == 0
    assert "the day was already complete" in out
    assert _spent(smoke).total == len(smoke.market.payloads) == sum(STEADY_SURVIVOR.values())


def test_the_first_night_s_catch_up_runs_from_the_printed_remedy_under_the_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """The panel stops at 3 April and the first evening is 21 April: eleven missed sessions, one
    more than the registered bound (10). Step 2 refuses before any request and prints the oldest
    batch; the remedy, run through the driver, fetches 3 April again and the ten oldest at five
    requests each; the evening then fetches the rest itself and plans the initial build."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=date(2026, 4, 20))
    night = _night(date(2026, 4, 21))
    smoke.market.today = night.date()

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=5),
    )

    assert code == 1
    assert smoke.market.payloads == []
    remedy = next(
        line.strip()
        for line in err
        if line.lstrip().startswith("UV_PROJECT_ENVIRONMENT=") and " panel build " in line
    )
    assert "--incremental" in remedy

    code, _out, err = _drive(smoke, capsys, "remedy", remedy, at=night + timedelta(minutes=10))

    assert code == 0, err
    assert len(smoke.market.payloads) == 11 * SESSION_REQUESTS

    code, out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=20),
    )

    assert code == 0, err
    assert sum(line.startswith("  BUY ") for line in out.splitlines()) == 10
    assert _spent(smoke).total == len(smoke.market.payloads)
    assert [end["kind"] for end in _ends(smoke)] == ["evening", "panel-build", "evening"]


def test_the_cap_stops_an_evening_mid_fetch_and_the_root_is_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    """16 April with the panel at 3 April: eight missed sessions, within the bound, so the
    evening itself fetches ten sessions (fifty-six requests) -- against a phase cap of thirty."""
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=date(2026, 4, 15), caps_={1: 30, 2: 460})
    night = _night(date(2026, 4, 16))
    smoke.market.today = night.date()

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=5),
    )

    assert code == caps.CAP_EXIT
    assert len(smoke.market.payloads) == 30
    text = "\n".join(err)
    assert "REQUEST CAP REACHED" in text
    assert "whole-year rebuild" in text
    spent = _spent(smoke)
    assert spent.total == 30
    assert spent.discarded is not None

    code, out, _err = _drive(smoke, capsys, "status", at=night + timedelta(minutes=6))
    assert code == 0
    assert any(line.startswith("discarded") and "cap" in line for line in out.splitlines())

    code, _out, err = _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        night.isoformat(),
        at=night + timedelta(minutes=7),
    )
    assert code == driver.REFUSED_EXIT
    assert any("discarded" in line for line in err)
    assert len(smoke.market.payloads) == 30


def test_a_morning_spends_one_request_and_a_weekly_report_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    smoke.market.today = survivor_day.DAY
    _drive(
        smoke,
        capsys,
        "evening",
        "--",
        "--as-of",
        survivor_day.DAY_AS_OF.isoformat(),
        at=survivor_day.RUN_CLOCK,
    )
    morning = datetime(2026, 4, 7, 8, 30, tzinfo=daily.SHANGHAI)
    smoke.market.today = morning.date()

    code, out, err = _drive(
        smoke,
        capsys,
        "morning",
        "--",
        "--as-of",
        morning.isoformat(),
        at=morning + timedelta(minutes=5),
    )
    assert code == 0, err
    assert daily.SMOKE_BANNER in out.splitlines()

    report = datetime(2026, 4, 7, 10, 0, tzinfo=daily.SHANGHAI)
    code, out, err = _drive(smoke, capsys, "weekly", "--", "--as-of", report.isoformat(), at=report)

    assert code == 0, err
    assert {end["kind"]: end["count"] for end in _ends(smoke)} == {
        "evening": sum(STEADY_SURVIVOR.values()),
        "morning": 1,
        "weekly": 0,
    }


# --- remedies ------------------------------------------------------------------------------------


def _paths(tmp_path: Path) -> dict[str, Path]:
    pinned = tmp_path / "smoke" / env.PINNED_NAME
    return {
        "runtime": tmp_path / "smoke" / env.RUNTIME_NAME,
        "registration": pinned / env.REGISTRATION_PATH,
        "pinned": pinned,
    }


def test_a_printed_remedy_is_read_back_into_the_driver_s_own_invocation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    runtime = str(paths["runtime"])
    batch = [
        "openalpha", "panel", "build", "--runtime-dir", runtime, "--year", "2026",
        "--as-of", "2026-10-22T16:30:00+08:00", "--exchange", "SSE", "--incremental",
        "--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit",
    ]  # fmt: skip
    calendar = [
        "openalpha", "panel", "build", "--runtime-dir", runtime, "--year", "2027",
        "--as-of", "2027-01-04T16:30:00+08:00", "--exchange", "SSE", "--dataset", "trade_cal",
    ]  # fmt: skip
    factors = [
        "openalpha", "factor", "build", "--runtime-dir", runtime, "--factor", "return_vol_60/v1",
        "--tier", "processed", "--transform", "cross_section_standard/v1", "--year", "2026",
        "--exchange", "SSE", "--max-staleness-days", "30", "--as-of", "2026-10-09T16:30:00+08:00",
    ]  # fmt: skip
    rerun = daily.rerun_command(
        daily.DailyOptions(
            runtime_dir=paths["runtime"],
            registration=paths["registration"],
            repo=paths["pinned"],
            as_of=datetime(2026, 11, 3, 18, 30, tzinfo=daily.SHANGHAI),
        )
    )

    assert driver.parse_remedy(daily.pinned_command(batch), **paths) == driver.Remedy(
        kind="panel-build", arguments=tuple(batch[1:])
    )
    assert driver.parse_remedy(daily.pinned_command(calendar), **paths).kind == "panel-build"
    assert driver.parse_remedy(daily.pinned_command(factors), **paths) == driver.Remedy(
        kind="factor-build", arguments=tuple(factors[1:])
    )
    assert driver.parse_remedy(rerun, **paths) == driver.Remedy(
        kind="evening", arguments=("--as-of", "2026-11-03T18:30:00+08:00")
    )


@pytest.mark.parametrize(
    ("change", "named"),
    [
        ("whole year", "whole-year rebuild"),
        ("research runtime", "runtime"),
        ("p6 registration", "registration"),
        ("doctor", "not a remedy"),
        ("no program", "no `openalpha`"),
    ],
)
def test_a_remedy_the_smoke_never_runs_is_refused_by_name(
    tmp_path: Path, change: str, named: str
) -> None:
    paths = _paths(tmp_path)
    runtime = str(paths["runtime"])
    build = ["openalpha", "panel", "build", "--runtime-dir", runtime, "--year", "2026"]
    build += ["--as-of", "2026-10-22T16:30:00+08:00", "--exchange", "SSE"]
    scoped = ["--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit"]
    line = {
        "whole year": daily.pinned_command([*build, *scoped]),
        "research runtime": daily.pinned_command(
            [*build[:4], str(Path.home() / "openalpha-research"), *build[5:], "--incremental"]
        ),
        "p6 registration": daily.rerun_command(
            daily.DailyOptions(
                runtime_dir=paths["runtime"],
                registration=paths["pinned"] / "docs" / "research" / "p6-registration.json",
                repo=paths["pinned"],
            )
        ),
        "doctor": daily.pinned_command(["openalpha", "panel", "doctor", "--runtime-dir", runtime]),
        "no program": "then rerun this command",
    }[change]

    with pytest.raises(driver.RemedyError, match=named):
        driver.parse_remedy(line, **paths)


def test_a_remedy_from_a_checkout_that_is_not_the_pinned_worktree_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    elsewhere = tmp_path / "elsewhere" / "src" / "openalpha_cn" / "__init__.py"
    monkeypatch.setattr(tds.registry, "_imported_package", lambda: elsewhere)
    line = daily.pinned_command(
        ["openalpha", "panel", "build", "--runtime-dir", str(smoke.manifest.runtime), "--year",
         "2026", "--as-of", "2026-04-06T16:30:00+08:00", "--exchange", "SSE", "--incremental",
         "--dataset", "adj_factor", "--dataset", "price", "--dataset", "stk_limit"]
    )  # fmt: skip

    code, _out, err = _drive(smoke, capsys, "remedy", line, at=survivor_day.RUN_CLOCK)

    assert code == int(daily.DailyExit.code_not_registered)
    assert any("ForeignPackageError" in entry for entry in err)
    assert smoke.market.payloads == []


# --- the driver's own guards ---------------------------------------------------------------------


def test_the_driver_sets_the_day_s_paths_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)

    code, _out, err = _drive(
        smoke, capsys, "evening", "--", "--runtime-dir", str(tmp_path), at=survivor_day.RUN_CLOCK
    )

    assert code == driver.REFUSED_EXIT
    assert any("--runtime-dir" in line for line in err)
    assert smoke.market.payloads == []


def test_one_invocation_at_a_time_in_a_smoke_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    descriptor = os.open(smoke.root / driver.DRIVER_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        assert _try_lock(descriptor, exclusive=True)
        code, _out, err = _drive(smoke, capsys, "evening", at=survivor_day.RUN_CLOCK)
    finally:
        _unlock(descriptor)
        os.close(descriptor)

    assert code == driver.REFUSED_EXIT
    assert any("another smoke invocation" in line for line in err)
    assert smoke.market.payloads == []


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
def test_the_driver_puts_the_signal_handlers_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)
    before = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP))

    _drive(smoke, capsys, "status", at=survivor_day.RUN_CLOCK)

    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)) == before


def test_phase_two_is_entered_once_with_a_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    seeded: Path,  # noqa: F811
) -> None:
    smoke = _smoke(tmp_path, monkeypatch, seeded, epoch=survivor_day.EPOCH)

    code, _out, _err = _drive(
        smoke, capsys, "phase", "--to", "2", "--reason", "the user's go", at=NOW
    )
    assert code == 0
    assert _spent(smoke).phase == 2
    code, _out, err = _drive(smoke, capsys, "phase", "--to", "2", "--reason", "again", at=NOW)
    assert code == driver.REFUSED_EXIT
```

- [ ] **Step 2：运行，确认失败**：`ModuleNotFoundError: No module named 'p7_smoke'`。

- [ ] **Step 3：实现** `scripts/p7_smoke.py`。模块文档写子命令表、上限规则与"驱动从不决定这一天做什么，这一天就是 `daily_selection.main`"。关键代码：

```python
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shlex
import shutil
import signal
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import FrameType
from typing import Any, Final, TextIO, cast

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
for _root in (_SCRIPTS, _SCRIPTS / "research"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection as daily  # noqa: E402
import forward_report  # noqa: E402
import p7_smoke_env as env  # noqa: E402
import p7_smoke_requests as budget  # noqa: E402
import registry  # noqa: E402

from openalpha_cn import cli  # noqa: E402
from openalpha_cn.panel.store import _try_lock, _unlock  # noqa: E402

REFUSED_EXIT: Final[int] = 5
INTERRUPTED_EXIT: Final[int] = 130
ORDINARY_EVENING_REQUESTS: Final[int] = 22
"""An ordinary evening's 16 requests, 20 once `stk_limit` pages (T6-T7 section 0), and 2 for a
year's data-dependent single requests (next year's calendar, the year before's names)."""
PAGED_SESSION_REQUESTS: Final[int] = 7
"""One session of the session-scoped targets once `stk_limit` pages: 1 + 3 + 3."""
DRIVER_LOCK: Final[str] = "driver.lock"
SESSION_SCOPED: Final[frozenset[str]] = frozenset(cli.INCREMENTAL_CENSUS)
PATH_FLAGS: Final[tuple[str, ...]] = (
    "--runtime-dir",
    "--registration",
    "--repo",
    "--morning-recheck",
    "--pin-worktree",
    "--launchd-plist",
    "--factor-gaps",
)


class SmokeDriverError(ValueError):
    """An invocation the driver refuses to make."""


class RemedyError(SmokeDriverError):
    """A printed remedy the driver does not run."""


@dataclass(frozen=True, slots=True)
class Remedy:
    kind: str
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KindRule:
    ceiling: int | None
    discard_on_cap: bool
    headroom: Callable[[env.SmokeManifest], int]


RULES: Final[Mapping[str, KindRule]] = {
    "evening": KindRule(None, True, lambda _manifest: ORDINARY_EVENING_REQUESTS),
    "panel-build": KindRule(
        None, True, lambda manifest: PAGED_SESSION_REQUESTS * (manifest.catch_up_bound + 1)
    ),
    "morning": KindRule(3, False, lambda _manifest: 1),
    "weekly": KindRule(0, False, lambda _manifest: 0),
    "factor-build": KindRule(0, False, lambda _manifest: 0),
}


class _Tee(io.TextIOBase):
    """Writes to every stream it is given: the terminal, and the invocation's log."""

    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def _values(arguments: Sequence[str], flag: str) -> list[str]:
    return [arguments[index + 1] for index, word in enumerate(arguments[:-1]) if word == flag]


def _same(given: str, expected: Path) -> bool:
    return Path(given).expanduser().resolve() == expected.resolve()


def parse_remedy(line: str, *, runtime: Path, registration: Path, pinned: Path) -> Remedy:
    try:
        words = shlex.split(line)
    except ValueError as error:
        raise RemedyError(f"the remedy cannot be read as a shell line: {error}") from error
    environment = f"/{daily.PINNED_ENVIRONMENT}/bin/"
    for index, word in enumerate(words):
        if word.endswith(f"{environment}openalpha"):
            return _openalpha_remedy(words[index + 1 :], runtime=runtime)
        if word.endswith(f"{environment}python") and words[index + 1 : index + 2] == [
            daily.THIS_SCRIPT
        ]:
            return _day_remedy(
                words[index + 2 :], runtime=runtime, registration=registration, pinned=pinned
            )
    raise RemedyError(
        "no `openalpha` or `scripts/daily_selection.py` of the pinned worktree in this line: "
        "paste the whole line the refusal printed"
    )


def _openalpha_remedy(arguments: Sequence[str], *, runtime: Path) -> Remedy:
    head = list(arguments[:2])
    if head == ["panel", "build"]:
        kind = "panel-build"
    elif head == ["factor", "build"]:
        kind = "factor-build"
    else:
        raise RemedyError(
            f"`openalpha {' '.join(head)}` is not a remedy this driver runs: only `panel build` "
            "and `factor build`"
        )
    given = _values(arguments, "--runtime-dir")
    if len(given) != 1 or not _same(given[0], runtime):
        raise RemedyError(
            f"this remedy's --runtime-dir is {given}, not this smoke's runtime {runtime}; "
            "the smoke never builds another runtime directory, least of all ~/openalpha-research"
        )
    if kind == "panel-build" and "--incremental" not in arguments:
        if set(_values(arguments, "--dataset")) != {"trade_cal"}:
            raise RemedyError(
                "this is the full rebuild of the session-scoped targets -- the whole-year rebuild "
                "remedy, about 1,200 requests at live scale -- which the smoke never runs: the "
                "panel copy is in a state only a discard repairs. Stop, ask the user, and prepare "
                "a new root from the original panel"
            )
    return Remedy(kind=kind, arguments=tuple(arguments))


def _day_remedy(
    arguments: Sequence[str], *, runtime: Path, registration: Path, pinned: Path
) -> Remedy:
    kept: list[str] = []
    named: dict[str, str] = {}
    words = iter(arguments)
    for word in words:
        if word in ("--runtime-dir", "--registration", "--repo"):
            named[word] = next(words, "")
        else:
            kept.append(word)
    for flag, expected in (("--runtime-dir", runtime), ("--registration", registration)):
        if flag not in named or not _same(named[flag], expected):
            raise RemedyError(
                f"this remedy's {flag} is {named.get(flag)!r}, not this smoke's {expected}"
            )
    if "--repo" in named and not _same(named["--repo"], pinned):
        raise RemedyError(f"this remedy's --repo is {named['--repo']!r}, not {pinned}")
    if "--morning-recheck" in kept:
        kept.remove("--morning-recheck")
        return Remedy(kind="morning", arguments=tuple(kept))
    return Remedy(kind="evening", arguments=tuple(kept))


def _refuse_path_flags(extra: Sequence[str]) -> None:
    for flag in PATH_FLAGS:
        if any(word == flag or word.startswith(f"{flag}=") for word in extra):
            raise SmokeDriverError(f"{flag} is the driver's to set, never an invocation's")


def _call(
    manifest: env.SmokeManifest,
    kind: str,
    arguments: Sequence[str],
    *,
    number: int,
    clock: Callable[[], datetime],
) -> Callable[[], int]:
    base = ["--runtime-dir", str(manifest.runtime), "--registration", str(manifest.registration)]
    base += ["--repo", str(manifest.pinned)]
    if kind in ("evening", "morning", "weekly"):
        _refuse_path_flags(arguments)

    def call() -> int:
        manifest.logs.mkdir(exist_ok=True)
        stem = manifest.logs / f"{number:04d}-{kind}"
        with (
            stem.with_suffix(".out").open("w", encoding="utf-8") as out,
            stem.with_suffix(".err").open("w", encoding="utf-8") as err,
            # `redirect_*` is typed for an `IO[str]`; a `TextIOBase` is not one to mypy.
            contextlib.redirect_stdout(cast(TextIO, _Tee(sys.stdout, out))),
            contextlib.redirect_stderr(cast(TextIO, _Tee(sys.stderr, err))),
        ):
            if kind in ("panel-build", "factor-build"):
                done = daily.invoke(list(arguments))
                sys.stdout.write(done.stdout)
                sys.stderr.write(done.stderr)
                return done.exit_code
            if kind == "weekly":
                return forward_report.main([*base, *arguments], clock=clock)
            flags = ["--morning-recheck"] if kind == "morning" else []
            return daily.main([*base, *arguments, *flags], clock=clock)

    return call


def _cap_lines(
    manifest: env.SmokeManifest, done: budget.CappedRun, *, cap: int, spent: budget.Spent
) -> list[str]:
    """What the driver prints on stderr when the cap stopped an invocation."""
    lines = [
        f"REQUEST CAP REACHED  invocation {done.invocation} stopped: the {done.reached} request "
        f"that would have passed phase {spent.phase}'s cap of {cap} was not sent",
        "  the day's own `tushare requests` line counts that unsent request; this ledger does not",
    ]
    if spent.discarded is not None:
        lines += [
            f"  the panel copy {manifest.runtime / 'panel'} may be half-written, so this smoke "
            "root is discarded: every later invocation in it is refused",
            "  run no `panel build` remedy on it -- above all not the whole-year rebuild one "
            "(about 1,200 requests at live scale)",
            "  ask the user before spending more; then prepare a new root from the original "
            "panel, with a new epoch:",
            "  uv run --no-sync python scripts/p7_smoke.py prepare --smoke-root <new root> "
            "--epoch <the session before the new first evening> --phase1-cap <N> "
            "--phase2-cap <M>",
        ]
    return lines
```

`run(manifest, kind, arguments, *, clock)`：在 `_driver_lock(manifest.root)` 里读 `spent`、取 `cap = manifest.caps[spent.phase]`；`panel-build`/`factor-build` 先 `registry._refuse_a_foreign_package(manifest.pinned)`；打印一行开场 `smoke <kind>: phase P, S of C requests spent (R remain)`；`budget.run_capped(..., call=_call(..., number=spent.invocations + 1, clock=clock), ...)`；打印收场一行（本次按接口、阶段合计）；`reached` 时把 `_cap_lines(manifest, done, cap=cap, spent=ledger.spent())` 打印到 stderr（`test_the_cap_stops_an_evening_mid_fetch_and_the_root_is_discarded` 要 `REQUEST CAP REACHED` 与 `whole-year rebuild` 两个短语）。`_driver_lock` 用 `os.open(..., O_RDWR | O_CREAT)` + `_try_lock(descriptor, exclusive=True)`，取不到就 `SmokeDriverError("another smoke invocation holds …")`。`_signals_as_exit` 只在主线程、只对存在的 `SIGTERM`/`SIGHUP` 装处理器（处理器 `raise KeyboardInterrupt`），`finally` 里恢复。`main` 解析参数、在 `_signals_as_exit()` 里分派；捕获 `env.SmokeEnvironmentError`、`budget.SmokeBudgetError`、`SmokeDriverError` → `smoke: refused: …`、`REFUSED_EXIT`；`registry.HoldoutRefusedError` → `smoke: refused: <类型>: …`、退出 3；`KeyboardInterrupt` → `smoke: interrupted; …` 、`INTERRUPTED_EXIT`。`status_lines` 打印：`smoke root`、`registration`（sha256 前 12 位、`config_id` 前 16 位、纪元）、`phase`（本阶段上限、两阶段上限）、`spent`（已花、剩余）、`discarded`（原因或 `no`）、`unclosed`（若有）、每次调用一行（编号、种类、上海时间的开始时刻、秒数、退出码、发出的请求数、`CAP REACHED`）、`by api`（JSON）。`prepare` 子命令打印准备结果与下一步（运行手册的那条 `smoke evening`）。`remedy -` 从 `sys.stdin.read()` 取一行。`--` 之后的额外参数：`argparse.REMAINDER`，去掉开头的 `"--"`。

- [ ] **Step 4：运行，确认通过**；`tests/unit` 全量、ruff、mypy、lint-imports。若 `test_the_first_night_s_catch_up_runs_from_the_printed_remedy_under_the_cap` 的 `11 * SESSION_REQUESTS` 与实测不同，**停下来报告**（`NEEDS_CONTEXT`，附 `BUDGET` 行）：那说明增量补抓的会话口径与 F12 不同，不要改断言凑数。

- [ ] **Step 5：变异探针**（`scripts/p7_smoke.py`）：
  1. `_openalpha_remedy` 删掉整年重建那一段 → `test_a_remedy_the_smoke_never_runs_is_refused_by_name[whole year-…]` 变红。
  2. `_openalpha_remedy` 删掉 `--runtime-dir` 的比较 → `[research runtime-runtime]` 变红。
  3. `RULES["evening"]` 的 `discard_on_cap` 改为 `False` → `test_the_cap_stops_an_evening_mid_fetch_and_the_root_is_discarded` 变红。
  4. `run` 里删掉 `registry._refuse_a_foreign_package(manifest.pinned)` → `test_a_remedy_from_a_checkout_that_is_not_the_pinned_worktree_is_refused` 变红。
  5. `_call` 的 `kind == "weekly"` 分支改成走 `daily.main` → `test_a_morning_spends_one_request_and_a_weekly_report_none` 变红。

- [ ] **Step 6：提交**：`feat(V2-P7-019): the smoke driver -- evenings, mornings, weekly reports and printed remedies under the cap`。

---

## Task 5：运行手册一节、T10 报告骨架、CHANGELOG

**Issue:** `V2-P7-019`。**依据:** 路线调整 §2.3、§2.6、§3；本计划任务 1–4 的实际命令。

**Files:**
- Modify: `docs/runbooks/daily-selection.zh-CN.md`（第 3–9 行的暂停说明、第 436 行幸存者形态的状态行各加一句例外；文末加一节）
- Create: `docs/research/p7-step0-validation/T10-real-data-smoke.md`
- Modify: `CHANGELOG.md`（`[Unreleased]` 下一段）

**Steps:**

- [ ] **Step 1：两处例外**。第 3–9 行的引用块末尾加："**例外：`V2-P7-019` 的真实数据冒烟**——一份只在一次性克隆里提交的冒烟登记、自己的运行目录与钉住的 worktree，经用户逐步明确同意、只经 `scripts/p7_smoke.py` 运行，见文末「真实数据冒烟（`V2-P7-019`）」。它不使用、不修改 P6 的登记与运行目录，不恢复每日登记。"第 436 行的状态行末尾加同一句的短版。

- [ ] **Step 2：文末一节**（全文中文；命令原样；不用 Markdown 链接语法）。结构与必须写进去的内容：

```markdown
## 真实数据冒烟（`V2-P7-019`）

> **性质**：一次性、隔离的工程冒烟，**不产生研究结论，不计入任何前向证据**。冒烟登记只在它自己的一次性克隆里提交、从不 push；它不是 `V2-P7-006` 的登记。`~/openalpha-research` 只被 `cp -c` 读一次，P6 的登记、预测、状态一概不碰；不装 launchd。每个花请求的步骤（补面板、每个真实晚上）在执行前都要用户明确同意，并说明范围与请求数。
> **这些单不要下。** 冒烟的每一份输出都以 `smoke: do not place these orders` 开头和结尾。真钱闸门：`006` 经用户确认并登记、账户成交对账与现金约束上线、用户明确说"开始"——三条全部成立之前，不用打印的清单交易。

### 为什么一律走 `scripts/p7_smoke.py`
（三条：每日命令没有"到 100 就停"，驱动在已有的 `counted_transport` 上挂到限即拒的计数器；拒绝信息打印的补救命令要交给 `remedy`，直接粘贴运行会绕过上限，而且其中一种是整年重建（约 1,200 次）；驱动固定运行目录、登记与 worktree，杜绝把冒烟写进 `~/openalpha-research`。）

### 时间（多伦多）
| 什么 | 北京时间 | 多伦多（EST，UTC−5，2026-11-01 起；2027-03-14 起 EDT 差 12 小时） |
|---|---|---|
| 晚间 | 交易日 18:30 之后，次一交易日 09:15 之前 | 当天 05:30 之后、**当天 20:15 之前** |
| 第一晚 | 同上；预留 2 小时（因子补建，见下） | 05:30 开始 |
| 晨查 | 次一交易日 08:30–09:00 | 当天 19:30–20:00（在当天晚间之后） |
| 周五的晚间 | 截止周一 09:15 | 截止周日 20:15 |
| 周报 | 任意时刻 | 周六 |
（再写一句：运行时钟早于北京 16:30（多伦多 03:30）时，"当天"是上一个交易日。）

### 准备（一次，0 次请求）
（在主检出、站在含本计划任务 1–4 的 `main` 提交上、受绑定路径干净：）
    uv run --no-sync python scripts/p7_smoke.py prepare --smoke-root ~/openalpha-smoke/r1 \
      --epoch <第一晚的前一个交易日> --phase1-cap <用户定> --phase2-cap <用户定>
（写清它做的十步与每条拒绝；之后定义一个 shell 函数：）
    smoke() { ( cd ~/openalpha-smoke/r1/pinned && UV_PROJECT_ENVIRONMENT="$PWD/.venv" \
      uv run --no-sync --env-file .env "$PWD/.venv/bin/python" scripts/p7_smoke.py "$@" ) }
（准备之后核对：`smoke status` 已花 0；`git -C ~/openalpha-smoke/r1/repo remote` 为空；`ls -l ~/openalpha-smoke/r1/pinned/.env` 指向主检出的 `.env`；`cat ~/openalpha-smoke/r1/pinned/.venv/pyvenv.cfg` 是 3.11.x。）

### 第一晚
1. `smoke evening` → 预期第 2 步拒绝、0 次请求，打印最老一批的 `panel build`。
2. 复制那一行，`pbpaste | smoke remedy -` → 约 `5 ×（批内会话数 + 1）` 次（`stk_limit` 分页时 7 倍），退出 0。
3. 回到 1，直到第 2 步不再拒绝；晚间自己取剩下的会话。
4. 第 4 步：若 P6 最后一次因子构建（10-08）到当天超过 20 个会话，拒绝并打印 `openalpha factor build`——同样 `pbpaste | smoke remedy -`（0 次请求，本机 CPU，约每会话 4 分钟）。
5. 第 5 步在纪元开账；第 6–9 步照常；摘要首尾是横幅；`smoke status` 记下累计。
6. 立刻再跑一次 `smoke evening`：0 次请求、`the day was already complete`（幂等）。

### 之后每晚
`smoke evening`；在第 3 个晚上**故意不跑**（第 4 个晚上的摘要应有 catch-up 一行补结算它、不做决定）；第 2 个晚上之后的多伦多 19:30 跑一次 `smoke morning`；周六跑一次 `smoke weekly`（预期：尚无成熟记录、没有 p 值、0 次请求）。

### 每晚要记什么（写进 T10 的表）
（列：北京交易日、多伦多开始/结束、子命令、退出码、拒绝的步骤与第一行、本次请求数/累计/上限、按接口、补抓的会话、因子补建的会话、ORDERS 的卖/买/顺延张数、unfunded/unaffordable、facts 的 universe/scored/coverage 与 `name_unknown_assumed_ordinary`、晨查的差异数、与脚本化市场的差异（`stk_limit` 分页、撤回复核 REFETCH、收益路径复现 BUDGET、停牌无日线、`namechange` 重述）、缺陷。**不抄任何收益数字、权益数字。**）

### 补救、上限与中断
（`remedy` 拒绝的几种与原因；上限触发时根目录被丢弃、之后一切调用被拒——不要在副本上补救，尤其不要照整年重建；先问用户，再从原面板、新纪元 `prepare` 新根目录；Ctrl-C、关掉终端（SIGHUP）、SIGTERM 中断一个晚间或补抓，同样丢弃；被 SIGKILL 杀掉的调用让账本拒绝继续。修了受绑定代码就是另一份登记：新根目录、新纪元；可以把未被丢弃的旧根的 `runtime/panel` 作为 `--source-panel`，省掉补抓请求。）

### 阶段 1 的 go / no-go（约 5 个真实晚上；结束时交用户确认）
go 需要全部满足：
- G1 至少 4 个晚间在截止前跑完：退出 0、日志有 `result`、记录 `standing` 为 `forward`、登记早于下一交易日 09:15。
- G2 故意漏跑的那一晚：下一晚的 catch-up 一行列出它、不做决定、它没有记录；周报把它列为缺失。
- G3 一次晨查在 09:15 前完成：恰好 1 次请求；`RT/paper/snapshots` 的文件列表与 `ledger.sqlite3` 的 sha256 前后不变。
- G4 一次周报：退出 0、0 次请求、没有 p 值、写明尚无成熟记录。
- G5 累计请求不超过阶段 1 上限；没有触发上限；没有未关闭的调用。
- G6 每一次拒绝都具名（`step N (名称) failed`），照它打印的补救（经 `remedy`）走得通；没有 traceback、没有 `internal_error`。
- G7 已完成的晚间重跑：0 次请求、0 次写入。
- G8 `~/openalpha-research` 的列表与 `panel/catalog.duckdb` 的 sha256、`docs/research/p6-registration.json`、主检出的 `git status` 前后一致；没有 launchd 配置。
- G9 每一份输出都带横幅。
no-go：任一条不满足。每个缺陷单独开 issue（测试先行 + 评审）；修了受绑定代码 → 新根目录、新纪元重做阶段 1。

### 阶段 2（延长到纪元之后至少 22 个会话）
（每晚照做；第 22 个会话之后的周报应出现第一条成熟记录——描述性，不检验；记录周报里 UNVERIFIABLE 的条数与占比（粗测，不是协议读数）；累计逼近阶段 2 上限时先停下问；**不跑 2027 年第一个交易日**，除非 `V2-P7-021` 已合入；阶段 2 不计入任何前向证据。用 `smoke phase --to 2 --reason "<用户的话与日期>"` 进入。）

### 结束
（保留根目录直到 T10 写完；克隆里的任何东西都不并入主仓库；不 push；结束后由用户决定删除根目录。）
```

- [ ] **Step 3：T10 骨架** `docs/research/p7-step0-validation/T10-real-data-smoke.md`：

```markdown
# T10：真实数据冒烟（`V2-P7-019`）

> **性质**：工程冒烟，不是研究证据。本报告**不出现任何收益数字**（账本权益、相对收益、IC、十分位差都不写）；记录的是这套代码在真实数据、真实时间上会做什么、在哪里拒绝、花多少请求。冒烟登记不是 `V2-P7-006` 的登记，它的输出一律"这些单不要下"。

## 0. 结论（阶段 1 结束时填）
- go / no-go：待填（逐条对照运行手册「阶段 1 的 go / no-go」G1–G9）。
- 缺陷：待填（每条一个 issue id）。

## 1. 环境
| 项 | 值 |
|---|---|
| 克隆的 `main` 提交 | 待填 |
| 冒烟登记 sha256 / `config_id` / 纪元 | 待填 |
| 源面板 `catalog.duckdb` sha256、文件数 | 待填 |
| 阶段 1 / 阶段 2 上限 | 待填（用户定） |
| 机器、Python、uv | 待填 |

## 2. 每晚
| # | 北京交易日 | 多伦多开始–结束 | 子命令 | 退出 | 拒绝（步骤：第一行） | 请求 本次/累计/上限 | 按接口 | 补抓会话 | 因子补建 | 卖/买/顺延 | unfunded/unaffordable | facts universe/scored/coverage/UNKNOWN | 晨查差异 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

## 3. 逐类拒绝
（按 `step N (名称)` 分类：次数、补救是否走通、花了多少请求。）

## 4. 与脚本化市场的差异
（`stk_limit` 是否分页、撤回复核 REFETCH、收益路径复现 BUDGET、停牌无日线、`namechange` 重述、行业以外的任何新形状。）

## 5. UNVERIFIABLE 的粗略占比（阶段 2）
（周报 late-record check 一节的条数 / 已核查条数；注明是粗测，不是协议读数。）

## 6. 缺陷清单
| 缺陷 | 现象（输出原文） | issue |
|---|---|---|

## 7. 没有测到的
（例如：新年第一天（`V2-P7-021`）、长假后的全年补课、账户与纸面账簿的对账。）
```

- [ ] **Step 4：CHANGELOG**：在 `## [Unreleased]` 下加一段 `### P7 — the real-data smoke's tooling (`V2-P7-019`)`：横幅只在冒烟登记的输出里出现、不入库；三件不受绑定的工具；运行手册一节与 T10 骨架；"no request was made; the nights wait for the user's go"。

- [ ] **Step 5：检查** `uv run python scripts/verify_publication.py`（相对链接、敏感模式），`tests/unit` 全量（`test_documented_counts_match_the_code.py` 等文档计数检查）。

- [ ] **Step 6：提交**：`docs(V2-P7-019): the real-data smoke runbook section and the T10 report skeleton`。

---

## Task 6：操作任务——准备、第一晚的补抓与第一个晚间（**等用户明确同意；花真实请求**）

**前置：** 任务 1–5 已经评审并以 merge commit / fast-forward 合入 `main`；主检出站在那个提交上、受绑定路径干净；用户已给出"决定 1、3、4"的答案并明确说"开始"。**任何一步之前若与本节不符，停下来问。**

**本任务的请求预算**（开始前对用户说出来）：第一晚 ≈ `5 ×（M + B + 2）+ 6`——取到的会话是全部 M 个漏掉的、每批重取一次库里最后一个会话（B 次）、晚间自己再重取一次并取当天（2 次），每个会话 5 次请求，另加 `trade_cal`、`stock_basic`、`namechange` 各 1 与 `index_daily` 3；`stk_limit` 分页时每会话 7 次；若库里 2025 年的 `namechange` 建于 2025 年末之前，再 +1（一年的第一次幸存者运行重建上一年）。M = 2026-10-08 之后、第一晚之前的交易日数，B = 补抓批数。按设计上限 10：

| 第一晚（北京） | M | 批数 B | 预计请求（不分页 / 分页） |
|---|---:|---:|---:|
| 2026-11-09（一） | 21 | 2 | ≈ 131 / ≈ 181 |
| 2026-11-16（一） | 26 | 2 | ≈ 156 / ≈ 216 |
| 2026-11-23（一） | 31 | 3 | ≈ 186 / ≈ 258 |

超出用户给的阶段 1 上限之前，驱动在调用之间就拒绝起步；在调用之中触发则丢弃根目录——**两种情况都先停下来问**。

**步骤与检查清单：**

- [ ] **0. 零请求的前检**：`git -C <main> log -1 --format=%H` 是合入后的提交、`git status --porcelain -- src scripts pyproject.toml uv.lock` 为空；`ls ~/Library/LaunchAgents | grep -i openalpha` 为空；`pgrep -fl openalpha` 为空；记下 `ls -la ~/openalpha-research`、`shasum -a 256 ~/openalpha-research/panel/catalog.duckdb`、`shasum -a 256 docs/research/p6-registration.json`。
- [ ] **1. 准备**（0 次请求）：`uv run --no-sync python scripts/p7_smoke.py prepare --smoke-root ~/openalpha-smoke/r1 --epoch <第一晚的前一个交易日> --phase1-cap <N> --phase2-cap <M>`。看：登记 sha256、`config_id`、纪元、面板文件数；`git -C ~/openalpha-smoke/r1/repo remote` 空；`.env` 链接目标；`pyvenv.cfg` 版本。uv 缓存缺包时它会离线拒绝并给出联网补缓存的命令——**那是网络请求（不是 Tushare），先问用户**。
- [ ] **2. 第一个晚间的第一次运行**（预期 0 次请求）：多伦多 05:30 之后 `smoke evening`。看：横幅首尾；`step 2 (panel update) failed: N missed session(s) …` 的 N 与上表的 M 一致；打印的批次命令带 `--incremental`；`tushare requests   0 this run`。若 N 与预期差得多，或打印的是不带 `--incremental` 的整年重建，**停**。
- [ ] **3. 补抓批次**：`pbpaste | smoke remedy -`。看：`BUDGET`/`WROTE` 行、`REFETCH`（撤回复核）、`BUDGET return-path-reproduction`（收益路径复现，每对 4 次）；本次请求数 ≈ `5 ×（批内会话 + 1）`；退出 0。记进 T10。重复 2–3 直到第 2 步不再拒绝。
- [ ] **4. 晚间跑完**：`smoke evening`。看：第 3 步 doctor 是否干净（任何 warning 抄原文）；第 4 步是因子补建（≤20 个会话，可能 1 小时以上）还是拒绝并给出 `openalpha factor build`（照样 `remedy`，0 次请求）；第 5 步开账（`session index 1`）；facts 一行的 universe / scored / coverage、`name_unknown_assumed_ordinary`、`unrecognised_board`（应为 0）、`st_by_band`（2025-07-07 之后为 0 是预期）；screen 的合格与幸存者数、六种原因的计数；ORDERS：首晚应是初始建仓（至多 10 张 BUY，每张有 `limit-up`，股数是 100 的倍数，没有 `no limit computed`），资金一行、`needs the sells above` 不应出现（首晚没有卖单）；prediction 一行：`prd_…`、`forward`、结果可知时刻约为 21 个交易日之后；摘要的 `tushare requests` 与 `smoke status` 的本次计数一致（上限没触发时）。
- [ ] **5. 幂等**：立刻再 `smoke evening`：0 次请求、`the day was already complete`。
- [ ] **6. 后检**：第 0 步记下的三样逐字节一致；`git -C <main> status` 与开始前一致。
- [ ] **7. 写 T10**：第 1 晚一行、第 1 节环境；提交 `docs(V2-P7-019): T10 -- the first night`（只改 T10）。之后每一晚都按运行手册、每晚开始前再问一次用户。

---

## Bound code touched（受绑定代码）

| 路径 | 任务 | 改了什么 |
|---|---|---|
| `scripts/daily_selection.py` | 1 | 新常量 `SMOKE_SETTINGS_KEY`、`SMOKE_BANNER`、`SMOKE_DOCUMENT_KEY`；新函数 `smoke_banner`、`bannered`、`with_smoke`；`main` 的尾部拆成 `_main_day`（行为不变，加横幅）；`_main_morning` 加 `banner` 参数 |
| `scripts/forward_report.py` | 1 | `main` 读横幅；原体移进 `_main_report`；打印加横幅；写出的文件不变 |

不受绑定的新增：`scripts/p7_smoke_requests.py`、`scripts/p7_smoke_env.py`、`scripts/p7_smoke.py`（不在 `src/`、`scripts/research/` 下，也不是两个受绑定脚本）。不改 `src/`、`scripts/research/`、`pyproject.toml`、`uv.lock`。任务 1 使所有现行登记失效——在 `006b` 之前这正合适（P6 每日暂停、没有在用的登记）。

## 之后的文档任务要补的台账行（本计划不碰台账）

- 横幅：建议一行（每日命令与周报的用户可见行为）——`scripts/daily_selection.py#smoke_banner`，`acceptance_test` 绑定 `tests/unit/scripts/test_daily_selection_smoke_banner.py` 的 node id（例如 `::test_a_smoke_evening_printed_again_as_text_is_bannered_first_and_last`、`::test_the_banner_is_printed_never_filed`）。
- 冒烟工具：一次性运维工具；建议**一行或不加**，由文档任务定——若加，`scripts/p7_smoke.py#main`、`scripts/p7_smoke_requests.py#run_capped`、`scripts/p7_smoke_env.py#prepare_smoke`，测试绑定三个新测试文件。
- 一旦台账点名这些测试文件，就是三件产物的改动（`features.csv`、`summary.json`、台账同一提交重生成，硬规则 9）；今天新增它们不需要改 `features.csv`（`LEDGER_TEST_FILE_COUNTS` 只数台账点名的文件）。

## Decisions for the user（推荐与它挡住什么）

1. **请求上限的数值**（挡住任务 6）。推荐：阶段 1 上限按**第一晚的日期**定，取上面任务 6 表里"不分页"那一列再加约 70（其余 4 晚 + 晨查）与约 20% 余量——例如第一晚 2026-11-09 → 阶段 1 上限约 240；阶段 2（**整个冒烟累计**）上限 = 阶段 1 实花 + 17 × 实测的普通晚间请求数（不分页 16、分页 20）+ 余量——约 500–560。路线调整写的"阶段 1 约 150–190、阶段 2 总计约 350–400"在任何现实的开始日期下都偏低（见下方"矛盾"第 2 条）。
2. **阶段 2 手工跑还是 launchd**（只挡阶段 2）。推荐**手工**：多伦多的窗口是当天 05:30–20:15，不必凌晨三点；`--launchd-plist` 写死 P6 登记的路径、没有晨查任务，改它是 `006a` 的一项；另写一个只为冒烟的 plist 会是新的未评审代码，且要用户同意安装常驻配置。
3. **谁来跑每一晚**（挡住任务 6）。选项：用户自己按运行手册跑；或 Claude 在用户每晚明确同意后跑（规格 §2.8 第 3 条：每个真实 API 步骤在执行前先说明范围与请求数）。推荐：第一晚由 Claude 在用户在场时跑（补抓与拒绝最多），之后用户自己跑、Claude 每周汇总进 T10。
4. **第一晚的日期（= 纪元的下一个交易日）**（挡住任务 6 的准备）。推荐：任务 1–5 合入后的第一个周一；越晚补抓越贵（每推迟一个交易日约 +5 次请求），且若第一晚在 2026-11-06 或之后，第 4 步要先跑 `factor build` 分批（0 次请求、本机约每会话 4 分钟，20 个会话约 1.3 小时）；阶段 2 需要纪元之后 22 个会话，第一晚晚于 2026-12-01 左右，第 22 个会话就落到 2027 年第一个交易日之后（`V2-P7-021` 的硬闸门）。
5. **冒烟登记的参数**（挡住任务 3 的默认值）。推荐：设计的书与 adapter 原样（含 `max_panel_catch_up_sessions` = 10，与将来的登记一致，并能把"再拒绝一次、给出下一批"的路径真跑一遍）；`survivor_forward_protocol` 用测试里的占位值（`window_periods` 2 等），明确标"冒烟，不是 006 的提案"。
6. **横幅的文字与覆盖面**（挡住任务 1 的定稿）。推荐：ASCII 英文 `smoke: do not place these orders -- a V2-P7-019 smoke registration, not the V2-P7-006 registration; nothing it prints is evidence`，中文"这些单不要下"写在运行手册；周报也加横幅（它不印订单，但"冒烟的所有输出"包括它）。
7. **中断的处理**（挡住任务 2 的规则）。推荐：一个写面板的调用（晚间、补抓）被 Ctrl-C / SIGTERM / SIGHUP 中断 → 丢弃根目录（面板可能半写；代价是重新准备与再一次补抓的请求）。若用户宁可保留根目录，规则可改为"拒绝继续，直到用户看过 `status` 后确认"。
8. **阶段 1 的 go / no-go 判据**（挡住阶段 1 的结论）。推荐运行手册里的 G1–G9；阶段 1 结束时由用户确认。

## 矛盾与发现（问题文本 / 规格与代码不一致之处）

1. **`counted_transport` 不能设上限。** 它只计数，从不拒绝（`scripts/daily_selection.py:1207-1242`）。路线调整说"改用已有的 `counted_transport` 并在驱动脚本里设上限"——可行的读法是：把一个**到限即拒的计数器**交给未改动的 `counted_transport`，因为 `CountingTransport.post` 先计数再转发（`:1219-1221`）。副作用：上限触发时，每日命令自己打印的 `tushare requests` 比真正发出的多 1。
2. **请求预算的算术。** 普通晚间 16 次（`stk_limit` 分页后 20）。阶段 2 要到纪元之后至少 22 个会话：22 × 16 ≈ 352，再加首晚补抓约 80–110，**整个冒烟约 430–470（不分页）**，分页约 550+——"阶段 2 总计约 350–400"若指整个冒烟则偏低。阶段 1 的"150–190"假定首晚补约 16 个会话；任务 1–5 要先合入，现实的首晚在 2026-11-09 或之后，补抓 21+ 个会话，阶段 1 约 200–260（任务 6 的表）。截至 2026-10-08 `stk_limit` 还没分页（P6 日志），但余量只剩约 30 个会话（2026-08-10 测得），随时会变成每会话 7 次。
3. **`--pin-worktree` 的 `.env` 链接指向 `--repo`。** `link_env_file(arguments.repo, DIR)`（`:6299`、`:5979`）：照运行手册对一次性克隆跑 `--pin-worktree --repo <克隆>`，链接指向克隆里不存在的 `.env`，打印"nothing was linked"，晚间就没有凭据。本计划直接链到主检出的 `.env`。
4. **`git clone --local` 保留 `origin`。** 克隆的 `origin` 指回主检出；"不 push"只是约定，一次手滑的 `git push` 会把冒烟登记的提交送进主仓库。本计划在克隆后删掉远端。
5. **多伦多的时间窗比文中说的宽。** 文中写"北京 16:30 之后（多伦多 03:30–08:00）"；代码的截止是下一交易日北京 09:15（`registration_cutoff`），即多伦多**当天 20:15**（EST）。晚间可在多伦多 05:30–20:15 之间任意时刻跑，晨查在 19:30–20:00；不需要凌晨。
6. **第一晚的因子补建会超过 20 个会话（推断）。** P6 登记的 `walk_forward.features` 包含四个组合因子的 processed 档（`docs/research/p6-registration.json`），研究库有 `factor_proc_*`；P6 最后一次运行是 10-08，所以第一晚若在 2026-11-06 或之后，第 4 步拒绝并要先跑 `openalpha factor build` 分批（`MAX_CATCH_UP_SESSIONS` = 20，`:364`）。路线调整与路线图都没有提这一步；它不花请求，但要约一小时以上的本机时间。（没有读目录库核实最后一次构建的日期，任务 6 第 4 步在副本上看。）
7. **运行手册现在禁止冒烟要做的事。** 第 3–9 行"那之前不要运行本命令……也不要为恢复手工补面板"，第 436 行同样；不加例外，冒烟与手册矛盾（任务 5 第 1 步）。
8. **纪元一旦写进登记就不能挪。** 还没有账簿时纪元到当天最多 `1 + max_panel_catch_up_sessions` 个会话（设计值 11；`strategy_survivor_book.py:661-671`）；第一晚若拖过这个距离，就要用新纪元重新 `prepare`（新登记、新根目录）。冒烟登记的纪元必须在准备时就按"第一晚的前一个交易日"选定。
</content>
</invoke>
