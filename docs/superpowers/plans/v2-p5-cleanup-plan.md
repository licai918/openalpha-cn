# v2 收尾：无守卫的重复、验收债、以及两项待决

分支 `feat/v2-p0a`。基线 `efcb8ca`。执行方式：`superpowers:subagent-driven-development`，
每个任务一个全新实现 agent + 一次任务评审，最后一次整枝评审。

## 背景

这条分支的 CI 已七绿，本地 5623 passed，e2e 在全新面板上重跑中。剩下的是本轮明确
**留而未做**的三类事。本计划只收纳事实已经查清、可以立刻执行的部分。

## 全局约束（评审的注意力透镜）

1. **`import-linter` 8 条契约必须保持 `8 kept, 0 broken`。** 关键的一条是
   「`openalpha_cn.domain` 不 import 任何兄弟子包」(`pyproject.toml:158`)。
2. **TDD 强制**：先让测试红，看它红在正确的地方，再让它绿。变异验证是验收标准的一部分——
   每条新测试都要在它该抓的改动下被观察到变红。
3. **不得写 `runtime/`**，不得读或打印 `.env`，不得跑 `tests/e2e`（走用户付费接口）。
4. **提交前必跑 `tests/unit`**，不是只跑被改文件的测试：本仓库有跨文件审计。
5. **docstring 不得声称代码没做的检查。** 本轮反复出现的缺陷形状就是这个，评审要专门盯。
6. 静态门：`ruff check`、`ruff format --check`、`mypy src`（strict）、`lint-imports`。

## 任务

### Task 1：合并 `note_for` 的三份重述

**事实**（已用 AST 比对确认，非推测）：`domain/factor.py:985`、`domain/factor_transform.py:745`、
`domain/factor_neutralization.py:632` 三个 `note_for` 的**函数体逐字相同**，宿主分别是
`FactorRegistry`、`FactorTransformRegistry`、`FactorNeutralizationRegistry`。

**关键点**：三者**同在 `domain/` 子包内**，分层契约不禁止它们共享。当初 `V2-P5-071` 对
`_board` / `average_ranks` 用「重述 + 等式守卫」是**被分层逼出来的**，这里没有那个约束——
所以正解是**合并**，不是再加一条等式守卫。

方法自选（mixin、共享基类、或接收 protocol 的模块级函数），但必须：
- 三处调用行为不变，`note_for` 仍先走 `self.get(qualified_key)` 再查 `self.notes`
  （「问一个注册表没声明的因子是错误，问一个没有注记的因子是答案」这条语义不能动）
- 保住 `tests/unit/domain/test_factor_neutralization.py:438` 既有的部分覆盖
- 新增一条测试，覆盖三个注册表**都**走同一实现

### Task 2：合并 `freeze_payload` 的两份重述

**事实**：`providers/base.py:96` 与 `domain/evidence.py:70` 的函数体**逐字相同**。
`providers/base.py:18-20` **已经在 import `openalpha_cn.domain`**，所以共享实现放在 `domain`
一侧、由 `providers` 复用是分层允许的。

两个宿主是不同的 Pydantic 模型，合并需要一个 mixin。要求同上：行为不变、加一条覆盖两面的测试。

### Task 3：裁决 `storage` 两个构造函数

`storage/batch.py:91` 与 `storage/memory.py:15` 的 `__init__` 前四行相同
（`self.path` → `mkdir(parents=True, exist_ok=True)` → `WAL` pragma），此后**分叉**（各自建表）。

**这一任务的产出可以是「不合并」。** 先判断这四行是真抽象还是偶然相似：如果只有 WAL 与 mkdir
是共享的，抽一个基类可能是把两个独立的东西绑在一起。无论结论如何，把判断依据写进代码注释或
台账——留一个「已经看过、结论是 X」的记录，而不是留一处无人知其状态的重复。

### Task 4：迁移第一批 10 行散文式验收

**事实**：`artifacts/openalpha-v1-feature-coverage/features.csv` 共 185 行，
`pytest` 96 / `legacy-prose` 85 / `not-applicable` 3 / `ci-job` 1。
**85 行散文的 `acceptance_test` 列写的是散文而不是 pytest node id**——所以这不是「把类型改个名」，
而是逐行找出或补出真正的验收测试。（更正：控制方原先写「证据格全部为空，实测 0/85」，那个数字来自查一个**这张表里并不存在**的
`evidence` 列，于是每行都返回空串。结论对，理由是假的。）

本任务只做 **10 行**，并把 `tests/unit/test_feature_ledger_test_tree.py:297` 的
`UNVALIDATED_ACCEPTANCE_ROWS` 从 85 压到实际达成的数字（棘轮是 `==`，不是 `<=`，
压不到就必须如实报告，不许改成不等式）。

对每一行：找到覆盖该特性的测试并填 `acceptance_kind=pytest` + node id；确实无覆盖的，
**不要硬填**——报告它，留在散文里，由下一批处理。

### Task 5：外键强制测试少了两个 store

由 C3 的复审查出，**不是文档问题**。`tests/integration/storage/test_foreign_key_enforcement.py`
的 `_STORE_FACTORIES` 只登记了 8 个 store，而 `src/openalpha_cn/storage/` 实有 **10 个**
（已独立枚举确认）：缺 `SQLiteJobStore`（`jobs.py`）与 `SQLiteModelUsageStore`（`models.py`）。

那条测试存在的全部意义，就是把**每一个** store 钉在同一条 `PRAGMA foreign_keys` 规则上
（`storage/connection.py` 的 `open_state_connection()` 只上提了这一行，正是为此）。
现在它覆盖 8/10——**这两个 store 有没有开外键，没有任何东西会发现**。这是本轮反复出现的
「一个修复只落在 N 份里的一份」。

**先测量再修**：把两个 store 加进登记表，看它们是过还是不过。
- 若过：登记表补齐即可，并加一条守卫，让「新增一个 store 却不登记」这件事变红
  （否则同样的洞会第三次出现）。
- 若不过：那是一个真实缺陷，不是覆盖缺口——按 TDD 修，并如实报告它此前一直没开外键。

`SQLiteModelUsageStore` 目前只在测试里被构造、未在 `runtime/composition.py` 出线；
`SQLiteJobStore` 在 `composition.py:207` 出线，与其余 store 指向同一个 `state.sqlite3`。
这个差别要写进结论，但不改变两者都该被登记这一点。

### Task 6：`SQLiteModelUsageStore` 在 `src/` 里没有任何构造点

由 C3 第三轮复审在独立重数 store 时发现，**不是文档问题**。

- `src/openalpha_cn/runtime/composition.py` 自己的计数互相矛盾：第 3 行说 "eight"、
  第 151 行说 "the other eight"、第 172 行说 "nine at its root-level `state.sqlite3`"。
- 更实质的是：`grep -rn "SQLiteModelUsageStore(" src/` **无结果**。这个类只在测试里被构造，
  `build_storage()` 从不装配它——也就是说**模型用量追踪看起来接好了，在跑起来的应用里是死的**。

先判性质再动手：这是「本该接上而漏了」，还是「一个已经作废但没删的类」？
两种结论都可接受，但都要有依据，并留下可追溯的记录。若是前者，接上之后它就该同时进
`_STORE_FACTORIES`（见 Task 5）。

### Task 7：`OA-BT-008` 这一行描述的功能已经被删掉了

由 C4 发现并经其评审独立确认。该行散文描述 rule/factor/agent 三类归因拆分，而
`V2-P5-005` 已经删除了这种拆分——现有测试
`test_no_contribution_is_a_fraction_of_the_net_active_return` 反而断言
`{item.category for item in held.attribution} == {"rule"}`，**证明拆分不存在**。

所以这一行不该被迁移，**该被改正或删除**。挂任何测试上去都会变成「测试证伪这行散文」。

这引出一个比单行更值得查的问题：**台账里还有多少行描述的是已经不存在的功能？**
先只处理这一行，同时报告扫描方法与结果，由用户决定要不要扩大。

### Task 8：README 与 marketing 声称两项代码不提供的能力

由 C6 与 C7 分别查出，**这是本批能见度最高的缺陷**——项目正门上、两种语言。

**(a) 归因类目。** `README.md:44`、`README.md:1185`、`README.en.md:51` 与
`docs/marketing/openalpha-cn-100-promotion-plans.zh-CN.md`（430/446/466/470）声称
「规则、因子、智能体与模型归因」/「rule/factor/agent/model attribution」。
实测 `src/openalpha_cn/backtest/validation.py:333,341`：只产出 `category="rule"`，
条目仅 `transaction-cost` 与 `no-position-versus-benchmark`，其余记为具名残差。
C7 的评审指出关键一点：那个三分拆分**不是被窄化，是完全且永久缺席**——
`KNOWN_ATTRIBUTION_LIMITATIONS` 把它写成结构性限制而非 TODO。

**(b) 用量/成本追踪。** `docs/marketing/...` 声称自动用量/成本追踪。
`SQLiteModelUsageStore` 现在接进了 `build_storage()`（C6），但写入侧
`OpenAICompatibleProvider` **在 `src/` 里没有任何构造点**——只有调用方自带 LLM agent
才会产生数据。出厂路径产不出它。

改成如实描述，并且**不要把更正也写过头**：不要用"暂不支持""即将支持"来暗示路线图上有它，
除非 `docs/specs/v2/openalpha-cn-v2-roadmap.md` 里真有对应条目——先查再写。

## 不在本计划内（需要你先决定）

- **证据环节的批量命令**：50 只票要跑 50 次 `research run` 再手工拼 JSON。这是新功能，
  按你的常设要求要先 `brainstorming`。
- **第五种验收类型（web 证据）**：`ci-job` 这第四种已经存在；Playwright 证据要不要单列，
  等 Task 4 跑完、看清真实需求再定。
- **鉴权 / 跨进程写序列化**：两者都是**声明过的边界**而非缺陷
  （`api/app.py:2101`、`panel/store.py:318`），在没有部署形态时做等于猜。
