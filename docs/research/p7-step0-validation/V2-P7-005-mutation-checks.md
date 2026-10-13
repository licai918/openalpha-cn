# V2-P7-005 mutation checks

这是对 `domain/survivor_*.py` 的手工变异检查；脚本不入库；每个变异逐个应用、跑
`tests/unit/domain/test_survivor_*.py`、还原。

**结果：`killed 45 / 45`，`SURVIVED: []`，`PATTERN NOT UNIQUE: []`。** 没有存活的变异，因此没有需要判定是否等价的变异，
也没有为杀死变异而追加的测试。被测代码是 `domain/survivor_book.py`、`survivor_screen.py`、`survivor_selection.py`、
`survivor_policy.py`、`survivor_orders.py`、`survivor_lifecycle.py` 六个模块；测试是 `tests/unit/domain/test_survivor_*.py`
七个文件（含逐日仿真 `test_survivor_book_lifecycle.py`）。

## 方法

每个变异是对一个源文件的一处**唯一**文本替换（出现次数不是恰好 1 的模式会被记入 `PATTERN NOT UNIQUE`，不会被应用）。
应用后用 `pytest -x` 跑上述七个测试文件；进程退出码非 0 即"被杀死"，为 0 即"存活"；无论结果都在 `finally` 里把
源文件还原为原文。子进程的 `PYTHONPATH` 指向被测检出的 `src`，并设 `PYTHONDONTWRITEBYTECODE=1`，避免同尺寸变异读到
陈旧的 `.pyc`。变异按模块分布：`survivor_book.py` 2; `survivor_lifecycle.py` 4; `survivor_orders.py` 8; `survivor_policy.py` 21; `survivor_screen.py` 7; `survivor_selection.py` 3。

## 45 个变异

| # | 变异（标签） | 文件 | 结果 |
|---|---|---|---|
| 1 | cooldown never blocks | `survivor_policy.py` | killed |
| 2 | cooldown boundary off by one | `survivor_policy.py` | killed |
| 3 | discretionary exits uncapped | `survivor_policy.py` | killed |
| 4 | deferred exits not reported | `survivor_policy.py` | killed |
| 5 | refill not rate-limited | `survivor_policy.py` | killed |
| 6 | first build rate-limited | `survivor_policy.py` | killed |
| 7 | draws the end of the queue | `survivor_policy.py` | killed |
| 8 | sells survivors instead of losers | `survivor_policy.py` | killed |
| 9 | forced exit inverted | `survivor_policy.py` | killed |
| 10 | best composite exits first | `survivor_policy.py` | killed |
| 11 | entry floor ignored | `survivor_policy.py` | killed |
| 12 | pending names redrawn | `survivor_policy.py` | killed |
| 13 | pending buys ignored in occupancy | `survivor_policy.py` | killed |
| 14 | unaffordable never skipped | `survivor_policy.py` | killed |
| 15 | near-exit band off by one | `survivor_policy.py` | killed |
| 16 | held names redrawn | `survivor_policy.py` | killed |
| 17 | loser cut off | `survivor_screen.py` | killed |
| 18 | board test inverted | `survivor_screen.py` | killed |
| 19 | listing boundary | `survivor_screen.py` | killed |
| 20 | keeps the thin names out of the cut | `survivor_screen.py` | killed |
| 21 | inexact sum | `survivor_screen.py` | killed |
| 22 | partial composite | `survivor_screen.py` | killed |
| 23 | liquidity over all boards | `survivor_screen.py` | killed |
| 24 | duplicates not collapsed | `survivor_selection.py` | killed |
| 25 | seed ignored | `survivor_selection.py` | killed |
| 26 | sells one lot | `survivor_orders.py` | killed |
| 27 | proceeds ignored | `survivor_orders.py` | killed |
| 28 | cash ignored | `survivor_orders.py` | killed |
| 29 | cash not consumed | `survivor_orders.py` | killed |
| 30 | wrong expiry | `survivor_orders.py` | killed |
| 31 | sells unsorted | `survivor_orders.py` | killed |
| 32 | expiry off by one | `survivor_lifecycle.py` | killed |
| 33 | cancels survivors | `survivor_lifecycle.py` | killed |
| 34 | cooldown prune boundary | `survivor_lifecycle.py` | killed |
| 35 | expired buys do not cool down | `survivor_lifecycle.py` | killed |
| 36 | a suspended holding is sold | `survivor_policy.py` | killed |
| 37 | a live sell is sold again | `survivor_policy.py` | killed |
| 38 | a live sell still holds a slot | `survivor_policy.py` | killed |
| 39 | any suspension freezes | `survivor_policy.py` | killed |
| 40 | session ignored | `survivor_selection.py` | killed |
| 41 | live buys not reserved | `survivor_orders.py` | killed |
| 42 | selling an unheld name is silent | `survivor_orders.py` | killed |
| 43 | verdict reasons unchecked | `survivor_book.py` | killed |
| 44 | verdict order unchecked | `survivor_book.py` | killed |
| 45 | pending sell of an unheld name | `survivor_policy.py` | killed |
