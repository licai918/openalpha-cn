# P7 开发批次 2 收尾记录（2026-11-07）

> **状态：记录，不是验收。** 本文记下批次 2 交付了什么、没交付什么、评审与修复各走了几轮、哪些裁决改了计划、哪些事留到以后、哪些事要用户来定，以及真正对真实数据下手之前要具备的前提。它只写工程状态；**工程完成不是研究证据**（`AGENTS.md`「研究诚实」），措辞仍是"候选"，没有任何一行把"通过的测试"写成别的东西。

## 0. 先读这几句

- 批次 2 是两条计划一起做的一批：`V2-P7-025`（登记簿 `stock_basic` 的新年缺口，任务 T1–T5）与 `V2-P7-024` 的四个前置（`V2-P7-004` 收尾记录 §5 第 10 条 ①–④，任务 1–5）。计划是 `docs/superpowers/plans/2026-11-07-v2-p7-025-registry-new-year.md` 与 `docs/superpowers/plans/2026-11-07-v2-p7-024-prerequisites.md`。
- 代码在分支 `feat/v2-p7-025-batch-2` 上（从 `main` 的 `587a158` 分出）。**批次代码在 `4b4ec94` 冻结**，控制者的串行全量闸门 `tests/unit` 在 11:49 起就跑在这一版上（日志 `.superpowers/sdd/batch-2/gate/unit-4b4ec94.log`，不在版本库里）；**它的结果不在本记录里，本文不写它通过**。冻结之后只有 docstring、注释与文档，清单见下一条。**未推送；没有对真实数据或真实面板运行过，一次 Tushare 请求也没有发过**：所有测试都跑在脚本化行情和测试时生成的夹具上。
- 规模（`git diff --shortstat 587a158 4b4ec94`）：23 个文件、+3,977 / −75。其中 `src/` 5 个文件、`scripts/` 4 个文件、文档 3 个（两条计划 + 运行手册）、测试 11 个文件（新增 45 个测试函数，部分参数化）。**受登记绑定的文件**（`scripts/research/registry.REGISTERED_PATHS` = `src`、`scripts/research`、`pyproject.toml`、`uv.lock`；`scripts/daily_selection.py` 由 `also_bound` 加绑）都动过；**没有任何登记存在**（每日登记自 2026-10-08 暂停），所以没有绑定因此被作废。
- **`4b4ec94` 之后动过字节的文件（本轮的文档/文本波）。** 受登记绑定的三个：`scripts/research/p7_reading.py`（一处 docstring）、`src/openalpha_cn/panel_ingest.py`（`_write_empty_partition` 的 docstring）、`src/openalpha_cn/strategy_survivor_facts.py`（两处 docstring）；三个都用 `ast.dump`（去掉 docstring）与改前逐字符相同证明**可执行 AST 不变**，脚本与哈希留在工作树外，不入库。不是绑定代码的：`tests/unit/test_survivor_facts_panel.py`（注释）、`tests/integration/panel/test_registry_ingest.py`（docstring）、`docs/runbooks/daily-selection.zh-CN.md`、`docs/specs/v2/openalpha-cn-v2-roadmap.md`、`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md`、两条计划文档、本记录、`CHANGELOG.md`、`docs/HANDOFF_CURRENT.md`、`AGENTS.md`。**没有一处行为改动。**
- 集成分支由两条轨道逐个挑拣而成，提交在历史里交错，所以下面**列出提交**而不写区间；哈希取自 `git log 587a158..4b4ec94`，账本（不在版本库内：`.superpowers/sdd/`）里的哈希是挑拣之前的，不能直接用。
- 各任务的"通过"数字是实现者和评审者各自最后一次**定向**运行的数字，来自账本，本轮文档没有重跑。整个 `tests/unit` 由控制者在集成分支上串行跑一次。
- 账本、任务书与代码不一致的地方，以代码为准，见 §7。

## 1. 交付了什么（逐个 issue）

### `V2-P7-025` 登记簿（`stock_basic`）的新年缺口——代码已交付、本行未关闭

- 提交（4 个代码与测试，另有 2 个文档：`171415f`、`4b4ec94`）：面板层 `ab93a7c`；`panel build` 接线 `a1378ee`；端到端 `0128e64`；周报跨年 `c41377e`；运行手册与模块文档 `171415f`、`4b4ec94`。
- **写**：整份 `stock_basic` 抓取成功、观测年没有上市/退市时，`panel build` 用同一批次把该年记成零行生命周期分区（`panel_ingest.record_empty_universe_year`，`cli._write_registry_year`，写成功之后在 stderr 印一行 `EMPTY stock_basic year=…`）。写在整个批次的有行年份之后、且只在写入成功时才打印。零行分区的覆盖记录：`row_count` 0，`as_of`/`fetched_at`/`last_event_time` 是抓取时刻，`max_available_time` 是该年第一个瞬间，列取自**这次抓取自己的最新子批次**（不是库里的最新有行年——与 `021` 的 `write_empty_name_year` 不同）。
- **读**：`domain.stock_universe.UNIVERSE_YEAR_EMPTY_THROUGH = (12, 31)`；`stock_universe_requirement` 把 `empty_admissible_through` 带给每个读者（唯一的构造点，`load_stock_universe` 内部会调用它，`panel doctor` 是唯一的外部调用者），目录库在窗口之后把零行分区按规则判 `stale`（`max_staleness=None` 也一样）。读者只在到该年 12 月 31 日（含，上海日历日）为止把它读作"今年还没有上市或退市"；当年第一条真实事件到来时零行分区被整份替换；同一年的更晚观测把零行分区重记一次。
- **四条拒绝条件**：不是一次成功的整份抓取（`no_data` 的整份回答由 `write_stock_universe` 先拒）；观测日已过该年的窗口；库里该年已有行而这次抓取的 `as_of` 不早于写那些行的观测（会"退市"已存的证券）；数据集不是 `stock_basic`。更早的重放什么都不写（`write_stock_universe` 对一个从批次里整年消失的年份的既有规则）。此外 `by_year` 为空的分支对成功的整份抓取**到不了**，是防御分支（§4）。
- **读者与端到端**：`test_a_lifecycle_year_with_no_event_yet_is_recorded_empty_and_read_across_the_history`（`years_read` 含该年、4 日的成员与 31 日的相同）；`test_the_registry_requirement_carries_the_empty_year_window_every_reader_needs`；`test_a_lifecycle_year_never_written_is_still_refused_as_missing`（没有分区仍是 `partition_missing`）；`test_the_skipped_year_refusal_still_fires_beside_an_empty_year`；`test_the_first_session_of_a_year_with_no_listing_or_delisting_runs_to_its_evening`（2027-01-04 晚间走完第 2–9 步，12 次请求，其中 `stock_basic` 1 次、`namechange` 2 次——第 12 次是既有的 `V2-P7-016` 年前名称重建）；`test_a_report_across_a_new_year_reads_the_registry_year_the_first_evening_recorded`（周报跨年，`first_session=2027-01-04`、`witnessed=1`）；两种拒绝形状 `partition_file_missing` / `partition_missing` 各有测试。
- **0 次请求的测量（2026-11-07，只读真实面板的 parquet，无写入、无网络）**：过去 14 年（2013–2026）有 **10 年**在当年第一个会话时还没有任何登记事件，缺口可以长到几个月（2009 年第一条是 2009-05-06，2013 年是 2013-02-08）；1990–2026 **每一年都至少有 4 行**。它量的是"每年第一个会话之前该年有没有事件"，**不是**"当年第一晚那一刻库里有没有该年的分区"——现存分区是事后回填的，那一刻的状态无法从今天的库里直接读回（测量文件同样写了这一点，且不入库）。后者正是"空年只在自己那一年之内可读"之所以失败即关闭：年外一个空的登记年只可能是失败或截断的抓取。
- **未做 / 未关闭**：没有对真实注册表运行过，没有登记；**取值（到 12 月 31 日，含）是暂定的，待用户确认**（计划 U1）；把 `025` 列为 `006a` 的一项并加入 `006` 的依赖仍是控制者的建议、待用户确认。路线图与规格 §9 本行都保持打开。

### `V2-P7-024` 那唯一一次读数之前的四个前置——代码已交付、本行未关闭

- 提交（7 个代码与测试，另有计划 `bafb80d`）：运行目录 `dc5a259`、接线 `9089fc2`、测试 `322a75b`；精确上界 `54baf0c`；`second_use` 顺序 `d057664`；`as_of_reason` `f68c68a`；`_readable_instant` 两条回退分支 `9260b12`（提交标题按它所属的批次 1 issue 写成 `V2-P7-008`，内容是 `024` 的 Task 5）。
- ①**运行目录拒绝**（`p7_windows.checked_runtime_directory`、`RESEARCH_RUNTIME`、`MAIN_CHECKOUT`、`RuntimeDirectoryRefusedError`）：运行目录若在 P6 的研究根 `~/openalpha-research` 或主检出之内，在打开任何东西、写任何认领之前按名拒绝；只读连接也会在目录库旁建 `catalog.duckdb.lock` / `.gate`（`O_RDWR | O_CREAT`），读一次的默认测量还在整个计算期间持共享侧。`p7_reading.read_once` / `_panel_measure` / `_panel_preflight` / `register_reading`（给了 `runtime_dir` 时）与 `p7_diagnostics._open_sources` 都调用它。判定有拼写、`..`、大小写与同一文件两层；只搬冒烟的两条规则（研究根、主检出），临时的克隆不被拒。
- ②**认领之前的精确上界**（`p7_reading._exact_most_periods`）：`minimum_periods` 的数据无关上界（`_most_periods`，工作日/步长/期限）是必要不充分；精确上界在认领之前从受限存储读 `trade_cal`（日历不是结果数据），按 `plan_signal_days` 的同一禁运与 `summarize` 的抽样算，超了在登记时（给了 `runtime_dir`）或 `read_once` 的默认预检里拒绝，消息给出那个数。日历读不出来时**拒绝**（登记时什么都不写，认领前写成问题），不是跳过。批次 1 把"26 可达"钉成规范的两个测试按裁决 R2 改写：26 只是数据无关上界，日历上界更低。
- ③**`second_use` 必须先读验证窗口**（`ValidationNotSpentError`、`_require_the_validation_window_spent`）：`second_use` 读数要求验证窗口的 `<validation>.window-claim.json` **已提交**，且能从某个 ref 或未过期的 reflog 到达（与 `_refuse_a_second_read` 同一个 `_LOG_EVERYTHING` 扫描，不是第二套机制）；磁盘上但从未提交的不算（可以删掉，然后在看过 2024–2026 之后重读验证窗口）。反方向不检查：验证窗口的读从不看二次使用段。
- ④**`as_of_reason` 与结果行**（`p7_reading._KNOWLEDGE_LINE`）：登记体必须有 `as_of_reason`——一行、`strip()` 后非空、无 `\n`/`\r`；校验在 `_refuse_a_second_read` 与认领写入之前（写坏的登记不能花掉窗口）。结果文本（`.result.txt`，`reading_lines`）重述这次读数落在哪个知识时刻，以及"每个分区按 `as_of` 时点读，所以之后再公布的修订或更名会进入窗口内会话的分数；这是在该时点的重算，不是逐会话发布时刻的回放"。`READING_SCHEMA` 仍是 `.../v1`：没有登记存在，`v1` 就是第一个格式，这个体不是硬规则 3 的契约行，加字段不需要迁移。
- 另加 Task 5（纯测试，`src/` 逐字节不变）：`_readable_instant` 的两个回退分支（`readable is None`、`readable <= filed`）重新抛出原来的 `PartitionNotYetKnowableError`，不是再读一次。
- **未做 / 未关闭**：读数本身、停止规则的数值（指标、阈值、`minimum_periods`）、K 与中期无效性、主期限，以及明确的"开始"仍是用户的；没有登记，没有对真实面板运行过。

### `V2-P7-004` 读一次的工具——再添四条

- 本批次给 `V2-P7-004` 的读一次工具加了上节的四条（①②③④ 就是 004 收尾 §5 第 10 条的那四条）。这些文件的字节已不再是批次 1 的 `22d65ae`；本行保持打开（Task 7 与真实读数仍未开始）。

## 2. 评审与修复轮次（每个任务一行）

评审者的型号写在括号里（opus 或 sonnet）。所有任务最终都是 Spec ✅、Approved、0 个 Critical / 0 个 Important，只有 `025` 的 T5 第一次评审未通过（见末行）。

- 025 T1（面板层）：实现者报 NEEDS_CONTEXT——任务书的测试代码有两个缺陷（提供者时钟钉在取数时刻，2027 的上市行被时点过滤丢掉；覆盖需求的分支在断言前先撞上 12 个日历缺口），裁决 A/B 后重跑；评审（opus）通过，独立导出上 177 条通过、变异体 M1、M2、M3、M5–M8 重跑全部被杀（M4 由 M3 的移除覆盖）；6 个 Minor 延后。
- 024 Task 1（运行目录）：实现者第一次报 DONE_WITH_CONCERNS——诊断测试在删掉检查后仍通过；预评审修复 `ec5a41a` 改成断言拒绝本身（monkeypatch `MAIN_CHECKOUT`，什么都不建，退出 2）并钉住 `..` 的拼写；评审（opus）通过，四条接线逐条删掉都变红；6 个 Minor 延后。
- 025 T2（`panel build` 接线）：实现者报 DONE_WITH_CONCERNS（两处任务书测试代码更正 D1、D2）；评审（opus）通过，核对了"空年写在有行年之后"、打印只在成功路径、`--full-update` 与 `--resume` 都到得了；5 个 Minor 延后。
- 024 Task 2（精确上界）：实现者报 DONE_WITH_CONCERNS（演练夹具缺 `STOP_RULE_SCHEMA`；探针 5）；评审（opus）通过，按测试日历手算复核 519 个工作日 → 26 个信号、禁运 1 个 → 25，演练 6 / 4，并确认没有测试再断言高于日历上界的值可达；2 个 Minor 延后。
- 024 Task 3（顺序）：实现者报 DONE_WITH_CONCERNS（任务书的测试先写了未提交的窗口认领；三个既有的二次使用测试要先提交验证窗口的认领）；评审（opus）通过，直接探针核对了磁盘上未提交、已提交、`git rm` 后、reset + 删分支后的 reflog、合并侧分支五条路；6 个 Minor 延后。
- 025 T3（端到端）：实现者报 DONE_WITH_CONCERNS（请求计数是 12 不是 11；任务书的 M2 探针不生效）；评审（opus）通过，独立复现 12 并逐条认出 12 次请求，第 12 次是既有的 `V2-P7-016` 年前名称重建，登记簿空年 0 次；4 个 Minor 延后。
- 024 Task 4（`as_of_reason`）：实现者报 DONE_WITH_CONCERNS（探针 3 按错误的理由通过；常量命名；任务书漏了一个直接调用点）；评审（sonnet）通过，核对了校验先于拒绝与认领、结果文本、验证窗口的文本逐字节不变、`READING_SCHEMA` 不变；5 个 Minor 延后。
- 025 T4（周报跨年）：实现者报 DONE_WITH_CONCERNS（任务书的 2027-01-06 报告时刻不可满足，改到 2027-01-05T10:00；M3 的预期不可达；M2 在晚间第 4 步拒绝）；评审（opus）通过，确认两条拒绝形状互斥、成功例跑的是真实周报；无 Critical/Important。
- 024 Task 5（回退分支）：实现者 DONE；两行 monkeypatch 的调用次数断言是相对任务书的最小加强（任务书的第二分支测试在移除分支后仍绿）；评审（sonnet）通过，`src` 的 md5 不变；3 个 Minor 延后。
- 025 T5（运行手册与文档串）：实现者 DONE；**第一次评审（sonnet）未通过**——同一份运行手册的 P6 第 6 条还说缺口是开着的，第 608 行的限定词被删，doctor 的机制写错，两个测试文档串已假；修复 1 轮 `7ee33ae` 后限定范围的再评审通过，只留一个新 Minor（第 419 行自己的限定词），归入本轮文本波（已修）。

## 3. 覆盖了计划的裁决（账本里的 `Ruling:`）

这些裁决由控制者在当时做出，代价写在账本里；若用户不同意，改动的范围在括号里。

- **R0（预检裁决）**：两条计划里"每次提交前串行跑一次 `tests/unit`"一律划掉。实现者只跑**定向**文件，整个 `tests/unit` 由控制者在批次末尾串行跑一次。理由：批次 1 的七个并发全量把 40 分钟变成数小时（10 核上负载 19）。代价：一条跨切面的守卫若坏得晚，由控制者的串行闸门抓。（`025` 计划第 55 行与 `024` 计划第 38 行已在本轮标注 corrected。）
- **R1**：同 R0（两份计划的同一句）。
- **R2**：`024` 的 Task 2 改写批次 1 里把 `minimum_periods == 26` 钉成"可达"的两个测试（批次 1 时的行号 `tests/unit/scripts/test_p7_reading.py:1147-1171`、`:1114-1124`）——裁决：**日历精确语义优先**，那两个测试随计划的明确说明一起改；26 只是数据无关上界。（代价：无，两个测试的语义被更正而不是放宽。）
- **R3**：`025` 计划者提出的 C1 被接受——三类读者并不自己构造 `stock_universe_requirement`；它在 `panel_ingest.py:1306`（`load_stock_universe`）与 `:1516`（它的逐时点读）里构造，`panel_doctor.py:1503` 是唯一的外部调用者。所以受绑定代码的清单比任务书猜的小；**代码优先**。
- **R4**：`025` 计划者的 C3（路线图里"真正缺分区（其前一年也缺…）"那句要收窄）延后到批次末尾的文档任务。本轮执行：`V2-P7-025` 行的验收格改正了两类缺口的条件（点名了库里没有的年 → `partition_missing`；事实适配器没有不晚于会话年的已存年 → `SurvivorFactsError`），不再要求"前一年也缺"。
- **025 T1 的 A**：测试辅助 `_registry_batch` 里加 `clock=lambda: as_of`（只改测试）——夹具要把批次呈现成 2027-01-04 的真实构建会看到的样子；否则提供者的时点过滤会丢掉 2027 的上市行。（代价：无，测试专用。）
- **025 T1 的 B**：`test_an_empty_year_satisfies_a_coverage_demand_but_a_hole_still_does_not` 的满足半边用 `years=(2026, 2027)`，洞的那半边不动。（代价：覆盖需求那条路证明得略少；gap 规则仍自证。）
- **025 T3 的计数裁决**：这个夹具的请求计数期望改成 **12**，因为被改的是一行断言——断言不是被放宽，而是被更正为真实命令的计数；原 11 是批次 1 `new_year` 夹具的形状（它自己的 `021` 测试仍断言 11 并通过）。计划第 53 行随本批次文档改为"`new_year` 种子 11、12 月 31 日种子 12"（已做）。

## 4. 延后的事项（带来源）

以下都是评审或实现者提出、控制者裁定"这一批不做"的。来源写作"计划 · 任务"（账本里同名条目）。【本轮已修】的条目是这一轮文本波处理掉的。

**024 运行目录（Task 1）**：参数化测试的 `iterdir()==[]` 在校验分支上是空断言（在研究根那一支才有内容）；诊断测试只钉"检查存在"，没钉它先于**成功**的读（顺序只由第一条语句保证）；与冒烟 `PathPolicy` 的交叉核对只比三条路径，抓不到身份分支的漂移；`checked_runtime_directory` 返回的已解析路径被丢掉（检查与打开之间的 TOCTOU，计划要求的单行接线）；`MAIN_CHECKOUT` 是"本模块跑在的那个检出"，所以 worktree 里的运行不会拒绝主检出的目录（研究根永远拒绝）；`_within` 的身份分支在任何 `OSError` 上返回 `False`（失败即开放），拼写分支是主要判据，且该辅助是冒烟那份的逐字拷贝，不会漂移。

**025 面板层（T1）**：`_write_empty_partition` 的 docstring 说"每个调用者传该数据集最新有行年"——对第三个调用者 `record_empty_universe_year` 是假话（它传批次自己的最新子批次）【本轮已修】；`if not by_year` 对成功的批次到不了、也没测，保留为防御；`test_registry_ingest.py:880` 的 docstring 说 2026 "holds fourteen lifecycle rows"（夹具是 1 行）【本轮已修】；一个**更早**的 `as_of` 重放到已存的零行年上会重写它、把 `as_of` 往回拨（失败即关闭，形状同 `write_empty_name_year`，无测试）；`U1=(12, 31)` 下 M9 不可达，为别的 `U1` 取值保留（计划要求）；任务书的 M1 探针描述不准（只有域测试会死，读侧由 M6 覆盖）。

**024 精确上界（Task 2）**：`p7_reading.py` 里那句"The catalogue check before the claim found a year of the window missing"已过时（预检还会拒绝读不出的日历与不可达的 `minimum_periods`）【本轮已修；旧句在 :326，任务书说的 :304 已是 `_REQUEST_KEYS`】；预检的失败即关闭元组（:987）接 `TradingCalendarError` / `PanelStorageError`，但不接 `DiagnosticWindowError`，而登记那条路接（:503）——日历让窗口第一天不是会话时，预检以一个裸异常逃出（仍在认领之前，失败即关闭，只是两侧不对称）【本轮记录，不在本波】。

**025 接线（T2）**：一次多年调用（`--start 2014 --end 2027`）会按 `--year` 迭代各印一行同样的 `EMPTY` 并每次重写零行分区（请求数不变）——见 §4 末"本轮明确不做"；没有测试钉住"抓取带着观测年 → 不印 `EMPTY`"这一否定面，也没有钉"被拒的写入不打印"（成功路径已钉）；`tests/unit/scripts/test_daily_selection_survivor.py:845` 的 `# --- V2-P7-025` 分隔条现在出现在四个 `V2-P7-021` 测试之前，应下移到 :997；一个更早的 `as_of` 重放到已存的零行年上会把覆盖记录的 `as_of` 往回拨（失败即关闭，无测试）；`test_survivor_facts_panel.py:1280-1291` 用 025 之前的世界讲 `_registry_year`【本轮已修】。

**024 顺序（Task 3）**：`_require_the_validation_window_spent` 自己没有浅克隆/grafts 检查（唯一调用点在 `_refuse_a_second_read` 之后，那里两者都拒，方向失败即关闭）；`commits[-1]` 是**最老**一个碰过该路径的提交，而消息说"was committed at"，应说明指哪个版本；`:1334` 的 docstring 说"reachable from some ref"而实现用 refs + 未过期 reflog，应对齐；测试的 `match="commit"` 也能被"no committed … exists"满足，应改用"on disk but was never committed"；改写后的测试不再把命题与一个已提交的 `<reading>.claim.json` 文件种类配在一起（同一扫描，两种文件各有自己的测试）；只走 reflog 的分支没有测试（任务书接受，评审者直接探针验证过）。

**025 端到端（T3）**：计数期望同时钉住了 `V2-P7-016` 的重建（注释已说明）；夹具重复了 `new_year` 的两构建体（约 20 行，与文件既有写法一致）；`scored > 0` / `"result" in journal` 较松（任务书自己的断言）；周报那条路由 T4 覆盖。

**024 `as_of_reason`（Task 4）**：`reading_lines` 的 docstring 没提新的一行；参数化测试的 `match="as_of"` 也能被"缺键"的消息满足（变异 C 按错误理由通过；写一次那条测试抓住）；`as_of_reason` 是登记者的文字、逐字印出，`verdict_words` 只扫诊断文档，所以一个"confirmed"/"alpha" 会进结果文本（按固定模板当数据处理；过滤会拒掉合法措辞）【本轮记录，不在本波】；空白拒绝的消息没说"前后空白也拒绝"；结果测试断言三个子串，不是整句。

**024 回退分支（Task 5）**：分支 1 只用类型钉住（删掉分支会 `TypeError`，现实变异体会红）；attempts 列表绑到模块全局查找与关键字调用——合法重构可能让它失败，但会**大声**失败（空列表 / `TypeError`），方向失败即关闭；一处惰性 `type: ignore` 与一个 `list[object]` 注解（外观）。

**025 文档串（T5）**：第 419 行自己的"已有事件的年仍按名拒绝"缺"相同或更晚的观测"限定——本轮已补，与第 608 行一致【本轮已修】。

**延后（本轮新增 → 已修）**：`src/openalpha_cn/strategy_survivor_facts.py:31-40` 与 `:1063-1086` 仍用 025 之前的前提解释 `_registry_year`；本轮改写为"025 在写入端关掉了它，回退留给没有这条记录的库"【本轮已修】。

**本轮明确不做（记录在案）**：

- 一次多年的 `panel build --dataset stock_basic` 会按 `--year` 迭代各印一行 `EMPTY stock_basic` 并每次重写零行分区（请求数不变）。去重与"只写一次"是一个后续小任务。
- 预检的失败即关闭元组缺 `DiagnosticWindowError`，而登记那条路有它；结果是窗口第一日不是会话时预检以裸异常逃出——仍在认领之前、失败即关闭。
- `as_of_reason` 逐字印在结果文本里，不扫禁用词；它按固定模板当数据处理，过滤会拒掉合法措辞。

## 5. 要用户来定的事（全部还没有定）

在用户定之前，下面每一项都按"未定"处理；代码里的值只是按计划推荐值实现的默认。

1. **`V2-P7-025` 的取值**：零行生命周期年只在**它自己那一年之内**（到 12 月 31 日，含，上海日历日）可读、可写——`UNIVERSE_YEAR_EMPTY_THROUGH = (12, 31)`。计划按推荐值实现；用户可改（代价：一个常量与几处文档，另需重跑定向测试）。同时待定：把 `025` 列为 `006a` 的一项并加入 `006` 的依赖（控制者的建议，批次 1 起就挂着）。
2. **`V2-P7-024` 的读数本身**：等用户明确说"开始"；在此之前不克隆面板、不读、不登记、不认领。
3. **`024` 的停止规则数值**：读数前提交——哪几个指标、各自的"均值不高于"阈值、`minimum_periods`；以及"读不出来按触发处理"这条默认是否接受。**数值由用户定，工具没有默认。**
4. **K 与中期无效性规则**（例：累积 K=18 时均值 ≤ 0 即停；工具默认的形状是 K/2 处查看）；**主期限**（代码里写死 20，是否保留）；功效块的其余工具默认在 `006` 冻结。
5. **`as_of_reason` 的正文**：每次读数登记时由登记者（用户）写一行，说明为什么这个 `as_of` 是这次读数所处知识；工具只要求它一行、非空，并逐字印在结果里。
6. **`021` 的两个暂定取值**（两种 `no_data` 回答都算经核实为空；空的名称年读到 1 月 31 日；规则对所有 `panel build` 生效）：仍未确认，与批次 1 相同。
7. **冒烟的数值与分工**（批次 1 起仍待定）：阶段 1、阶段 2 的请求上限与起步余量；谁来跑每个晚上；阶段 2 手工还是另作安排；第一晚的日期；Task 6 的明确"开始"。
8. **Task 7 的明确"开始"**（研究期 2015–2021 的读数，零次请求）与真钱闸门的三条（`006` 确认并登记、账户成交对账与现金约束上线、用户明确说"开始"）。

## 6. 运行前提

**真实的验证读（`024`）之前**

1. 在跑读数的那个检出里先 `git fetch --unshallow`（读一次的校验读要看**已提交**的登记与认领；主检出今天仍是浅克隆）。
2. 认领必须**提交**并留在本仓库的 refs 里；`second_use` 还要验证窗口的 `<validation>.window-claim.json` 已提交（磁盘上但从未提交的认领不打开二次使用）。
3. 仓库里没有 grafts 文件（`info/grafts`）；替换 ref 被忽略。
4. 读数的登记必须覆盖**整个窗口**，且已在 `HEAD` 的提交历史里、字节与 `HEAD` 所持相同；受绑定代码在 `HEAD` 与工作树里都等于登记的 `code_commit`；停止规则的数值已由用户定并随登记一起提交；登记带一行 `as_of_reason`。
5. 从面板的 `cp -c` 克隆读；运行目录**不能**在 `~/openalpha-research` 或任何一个检出里（现在会在打开任何东西之前按名拒绝）。
6. `minimum_periods` 必须不高于面板日历的精确上界：登记（给了 `runtime_dir` 时）与认领前的预检都会给出那个数。评审在真实日历上的算术是 24（验证窗口、步长 20、h20），**本批次没有离线复核**；若真实消息给的不是 24，停下来报告，不猜、不调参。预检会读 `trade_cal`（日历不是结果数据）；它只看目录库列了哪些年，列出但不完整的分区、或没有因子构建的信号日，仍要到认领之后的测量才暴露——那次读就花掉了。
7. 先读验证窗口、提交它的窗口认领，再读 `second_use`。

**真实的冒烟（Task 6）之前**：见运行手册「真实数据冒烟（`V2-P7-019`）」。要点——先看 `git rev-parse --is-shallow-repository`（`prepare` 只印警告、不拒绝）；源面板是 `~/openalpha-research/panel`；面板的卷必须是 APFS 且与根目录同卷；根目录不含空白、不在临时目录或主检出里；纪元是第一晚的前一个交易日；每个花请求的步骤与每个真实晚上之前先得到用户的明确同意；**2027 年第一个交易日仍不跑**，除非 `V2-P7-025` 已合入主线、用户同意，且真实运行确认了它的行为——**Tushare 对一个当年还没有任何事件的年份的 `stock_basic` 回答什么，没有测过**（本批次只做 0 次请求的离线测量）；`025` 的代码在批次 2 分支上，未推送、未在真实数据上运行过。

## 7. 账本、任务书与代码不一致之处

- 任务书说 `scripts/research/p7_reading.py:304` 还写着旧句；实际旧句在 **:326**（`ReadingPreflightError` 的 docstring），:304 已经是 `_REQUEST_KEYS`。已按实际位置修正。
- 任务书说 `tests/integration/panel/test_registry_ingest.py:880` 的 docstring 说 2026 "holds fourteen lifecycle rows（the fixture has 1）"——代码核对：夹具里 2026 年只有 `000004.SZ`（2026-07-14 退市）这一行，已改成 1。
- 任务书转述计划 025 的 T4 M1 为"周报被拒"；实测是**晚间的第 2 步先以 `stale` 拒绝**，周报根本不会运行；M2 同理，拒绝点是晚间第 4 步的 `partition_missing`。
- 任务书说"the brief's M2 probe is inert as written and M2' substitutes"——指 T3 的 M2；计划 025 已按此标注，M2' 是 T3 实现者的替代探针，正文仍刻意不重写它。
- 任务书说计划 025 的 M3 预期 `stale` 不可达（2028 的日历检查先触发）——与计划一致，已在计划里标注。
- 任务书说"四条拒绝条件"——代码里是四条 `PanelBatchError`，另有一个 `by_year` 为空的防御分支，对成功的整份抓取到不了（本记录 §1、§4 写了这一点）。
- 任务书说 `024` 计划里 `KNOWLEDGE_LINE` → `_KNOWLEDGE_LINE`——代码核对：Interfaces 块（计划 :962）写的是前者，实现（:1017）是后者；已标注。
- 任务书说"record the fixture numbers actually pinned（calendar-exact 25；rehearsal 6 / 4；data-free 26）"——代码核对全部成立，另加演练夹具自己的数据无关上界 8 / 5；已记入计划。
- 任务书说"several are bound code; no registration exists, so this is free"——核对：`scripts/research/p7_reading.py`、`src/openalpha_cn/panel_ingest.py`、`src/openalpha_cn/strategy_survivor_facts.py` 都在 `REGISTERED_PATHS` 里（`src`、`scripts/research`）；三者的可执行 AST 与改前相同，且确实没有登记，所以没有绑定被作废。
- 账本把 `024` Task 5 的提交标成 `V2-P7-008`（它关的是批次 1 的一个延后项）；本记录按任务写，提交标题原样不改。
- 账本里的提交哈希是挑拣前的；本文只用 `git log 587a158..4b4ec94` 上的哈希。
