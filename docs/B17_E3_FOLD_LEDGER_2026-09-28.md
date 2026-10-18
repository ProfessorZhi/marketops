# 落地记录 · e3（折入账：一次折入归属于哪一轮 Run）—— 2026-09-28

**这是新开的一份，不是 `docs/B17_DESIGN_SLICES_2026-09-28.md` 的续篇。** 那一份在发布
记录里逐字声明了「冻结之后这一份不再长」，所以 e3 的落地记录按那份声明另外落在这里。
计划与设计决定仍在 artifacts 那一侧：`TARGET-CONVERGENCE-B17/E3-DESIGN-DECISION.md`
（它的第四节在落地时被更正过一次，见下面第三节）。

提交：一个。逐文件清单见 `git show --stat` —— 这里不抄一份，因为「改动几个文件」这个数
会**随本文件自己的存在而变**，抄下来就立刻要维护。三块新的东西是
`marketops/domain/fold.py`、`tests/test_run_fold_ledger.py`、以及本文件。

## 一、这一片关掉的那条假账

`close_run` 此前只有一个关于「这一轮折了什么」的通道：**调用方递上来的 `folded`**。
盘上没有第二处能佐证它。于是 `reconcile_run_delta` 的 `unclaimed_folds` 只查 `folded`
里有的键（`run_reconciliation.py` 的 `_refusal`），`folded=()` 时它恒空 —— **池子变大
了而账是平的，`completed` 照常停表**。设计与复现见 `E3-DESIGN-DECISION.md` 第五、六节。

落地的三件事，缺一件这条假账就还在：

1. **受理当场开一份空账。** `RunStore.admit` 在写 `research_run` 那一行的**同一个写
   事务里**往 `run_fold_ledger` 插一行。插进去的是**存在性**，不是内容。
2. **`RunStore.folds_of(run_id)` 是这一侧唯一的权威读数**，且它有**三种**结局、不许塌
   成两种：折了几行 / 有账且为空 / **账本不在**。第三种抛 `RunFoldLedgerError`，
   **绝不返回 `()`**。`run_id` 不在册也抛同一个异常，只是报文里 `run_exists` 那一格
   说清了是哪一种（`ports/runs.py::fold_ledger_refusal`）。
3. **池子那一行的写与那一笔账同生共死**（下一节）。

`close_run` 于是有两道闸，次序是承重的：**先**拿账核主张（`claimed_folds(folded)` 对
`folds_of(run_id)`），**再**跑 `reconcile_run_delta` 的三方向。第一道先问，因为主张与
账对不上时连「拿哪份 Delta 对账」都没有答案 —— 那时报出来的对账结论是拿一份没人认领
的 Delta 算的。

## 二、账本的口径，以及它顺手解掉的那个分层环

**账记的是「池子那一行落了盘」，不是「这一次 promotion 的处置是什么」。** 这一句是
`domain/fold.py` 的模块 docstring，也是本片所有取舍的源头：

* `PromotionDisposition.ASSET_ONLY` 的池子行**确实落了盘**，而它是 `asset` 非空那
  一族里的一员 —— 按处置记账会把这一类漏掉，**漏掉的正是假账本身**。
* `reconcile_run_delta` 认的是 `asset is not None`，与账本同一口径。

设计稿第四节把「`ports/runs.py` 要引用 application 的类型」当成一个先要解开的环，
列了三条出路。**落地时三条都没走**：口径换掉之后，账本需要的只是
`(run_id, receipt_id, ordinal)` 加一个 `venue_asset_id`（`domain/fold.py` 的
`FoldAttribution` / `FoldReceipt`），**一个 application 层的词都不出现**，
`PromotionDisposition` / `PromotionOutcome` 原地留在 `application/promotion.py`。
那条环是**误认**：把它当成「处置的账」才需要把处置搬进 `domain/`。更正已写进
`E3-DESIGN-DECISION.md` 第四节（新加的一小节「落地时实际选的是**第四条：不动**」）。

## 三、原子性怎么落的（不造 UoW，两处各用各的写法）

`upsert_asset` 多了一个 `fold: FoldAttribution | None = None`（`ports/pool.py`）。

* **SQLite**：`SqliteVenueAssetStore.upsert_asset` 里已有那一个
  `with session(...), write_transaction(conn):`；`record_fold(conn, ...)` 就在
  `venue_asset` 的 INSERT **之后**、同一个 `conn` 上调用，`at` 与 `stored_at` 取的是
  **同一次** `utc_now()`。`BEGIN IMMEDIATE` 之下一旦 `record_fold` 抛，整个事务回滚，
  池子那一行也不在。
* **内存**：`InMemoryVenueAssetStore.upsert_asset` 的次序是
  `_require_workspace` → `merge` → `record_fold` → `bind_workspace` + 两次字典写。
  **会失败的四步全在字典写之前**，所以「池子动了而账没记」在这个实现上不可构造。

**另一个方向——「账记了而池子没落盘」——两个实现上都构造不出来**（SQLite 的写序是
池子先、账后；内存的四步全在字典写之前）。所以负例不伪造这一条，而是按**四个可构造的
拒词**统一断言「两边都不动」：坏 `run_id`、已结束的 Run、重放键撞上另一个句柄、内存池
没接账本。理由逐字写在 `tests/test_run_fold_ledger.py` 的模块 docstring 里。

## 四、负例（`tests/test_run_fold_ledger.py`，新，29 用例）

夹具是 `@pytest.fixture(params=["sqlite", "memory"])`：**同一族判据对着两个实现各跑
一遍**，这是本仓判断「这是一个契约」的方式（`tests/test_asset_store_contract.py` 的
模块 docstring 里记着这一格为什么刻意不在那一份、而在这一份）。

覆盖的是这些格，逐条都是「两件事不许长成一件」：

| 判据 | 挡的是 |
|---|---|
| `an_admitted_run_opens_an_empty_ledger` | 受理没开账 |
| `a_run_that_folded_nothing_still_closes_with_an_empty_claim` | 把「真零折入」也拦下来 |
| `a_ledger_that_was_never_opened_is_not_an_empty_ledger` | 没开过账被读成空账 |
| `a_claim_of_nothing_when_the_ledger_names_a_row_is_refused` | e3 那条假账原样回来 |
| `a_claim_that_names_one_key_too_many_is_refused` | 主张多报一个键 |
| `a_ledger_that_is_gone_is_refused_and_never_read_as_zero` | **账本缺失被读成零折入** |
| `the_same_receipt_row_folded_twice_is_one_entry` | 同一笔重放不是第二笔 |
| `the_same_receipt_row_folded_into_another_row_is_refused` | 同一个键配另一个句柄 |
| `the_ledger_reads_back_in_the_order_the_rows_were_folded` | 账的次序 |
| `two_connections_folding_the_same_receipt_row_write_one_entry` | 两个连接并发折同一笔（`threading.Barrier`，真并发） |
| `two_runs_folding_the_same_row_do_not_share_a_ledger` | 两个 Run 操作同一个 Asset 时串账 |
| `a_fold_that_cannot_be_recorded_leaves_neither_side[no-such-run\|already-stopped]` | 拒的时候池子也不许动 |
| `a_memory_pool_that_has_no_ledger_refuses_rather_than_dropping_it` | 内存池有归属却无处记 |
| `the_two_implementations_refuse_in_the_same_words[unknown-run\|ended-run\|ledger-gone]` | 两个实现报同一句话 |

`tests/test_run_reconciliation.py` 那一侧改了夹具（池子接上账本，`fold=` 才给得出归属）
并新增三条：折过而池子读不出来、空账对空主张平、以及那条「主张与账对不上」的报文里
**不含** `reconcile_run_delta` 的措辞（证明第一道闸先开）。两个文件合计 **81 passed**
（新文件 29 + `test_run_reconciliation.py` 52）。

## 五、变异控制：6/6（`E3-MUTATION-CONTROL.py`）

口径只有一条：**`exit == 1` 且红名单非空且命中期望**。每条变异都清 `__pycache__`
（同尺寸同秒的还原骗得过 pyc 校验，见 memory `pyc-cache-defeats-same-size-mutation-reverts`）。

| 变异 | 打红了什么 |
|---|---|
| M1 结束那一层不再核对主张与账 | 两条 e3 负例（`sqlite` + `memory` 各一） |
| M2 账本读不出来时返回 `()` | `a_ledger_that_is_gone_is_refused…` 等 4 条 |
| M3 受理那一笔不再开账 | 21 条 |
| M4 记账挪到事务外 | 3 条（含「拒时池子也不许动」） |
| M5 重放判据不看句柄 | `the_same_receipt_row_folded_into_another_row_is_refused[sqlite]` |
| M6 内存记账挪到字典写之后 | 3 条（`memory` 那一侧） |

### 这份量具自己出的两次岔子（记下来，因为它们在报告上长得像别的东西）

1. **M5 的锚点最初写错了**：源码是**嵌套两层** `if`，脚本里写成了一行
   `and`。锚点对不上时脚本打 `SKIP` 并把这条计入失败 —— 也就是说，
   **「锚点写错」与「变异没被抓到」在总读数上是同一个数**。修锚点后 M5 正常打红。
2. **M6 的期望名最初写错了**：实际用例 id 带参数后缀（`…leaves_neither_side[memory-no-such-run]`），
   脚本里写的是 `…[memory]`。变异**确实**打红了正确的那三条，只是判据按子串比对没认出来，
   于是第一次跑是 `5/6`。改用 `[memory-` 前缀后 6/6。

两次都不是实现错，而是**判据自己写错了**；两次都会把一条活的判据报成死的。

## 六、纵向消费者：`run_id` 接到了 MCP 那一面

`promote_receipt`（`application/promotion.py`）与 MCP 工具 `promote_receipt`
（`adapters/mcp_server/product_loop.py`）各多一个 `run_id: str | None = None`。

* **缺省 `None` 是一个被声明出来的事实**，不是「还没接上」：研究流程今天不在 Run 里跑，
  把它变成必填会当场打断产品环。但「这一批折入不属于任何一轮」因此是**数得出来的**
  （这一批的池子行没有任何一笔归属），而不是消失在 `folded=()` 后面。
* 指一个不存在或已结束的 `run_id` 是一句拒词，走 `ToolError` 回到工具结果上（与
  `StoreError` / `SceneConflictError` 同一条处置），**不回退成「不记账也照折」**。
* `_promote_one` 的两支提前返回的拒绝（跨工作区、绑定冲突）**不在账上留一笔** ——
  账记的是「池子那一行落了盘」，而那两支行压根没走到池子那一步。

`docs/MCP_PRODUCT_LOOP.md` 里那一段（「A fold cannot be truncated, backdated, or
pointed at another market.」）随之同步：`promote_receipt` 的参数从「恰好一个
`receipt_id`」改成两个，并说明 `run_id` **不是**那条规则的例外 —— 账本按它键、store
拒未受理/已结束的 Run、`close_run` 拿账核主张、`None` 是「不属于任何一轮」的被声明
事实、跨市场由 Run store 的单 workspace 绑定堵住（拒绝时池子一个字节都不写）。

## 七、实测（今天，`marketops-mvp` 那个 venv）

| 量 | 读数 |
|---|---|
| 全量套件 | **6165 passed, 4 skipped** |
| 相关十套件（run 族 + 池子契约 + MCP） | **445 passed** |
| e3 两个测试文件 | **81 passed**（新文件 29 用例） |
| `ruff check .` | All checks passed |
| `python -m marketops.schema` 重生成 | `contracts/` 与 committed **逐字节一致**（`git status --porcelain contracts/` 为空） |
| 变异控制 | **6/6**（上一节） |

**上面每一格的时刻、用时与被测的那棵树**都不写在这份文件里，而是记在 artifacts 那一侧
（`E3-FULL-SUITE.txt` 与它旁边的 header：树 hash、HEAD、起止时刻）。理由是这份文件
**自己是那棵树的一部分**：把一次实测的时刻写进被它测的那棵树，写下去的瞬间读数就不再
描述盘上的东西 —— 改一句散文就要重跑一次全量，而重跑出来的用时又会立刻过期。
这与冻结的那一份在「本节自身不参与测量」里说的是同一件事，只是这里的射程更大：
**凡随时间变而变、或随本文件自身而变的量，一律不落在这份文件里。**

## 八、这一片**不**做什么

* **不扩大 API 重构**：`folded` 参数留着，只是从「真源」降级成「一份要被核对的主张」
  —— 与账不一致时明确拒绝。**去掉调用方 `folded` 是下一件事，不是这一件**（它今天
  还挡得住「调用方以为自己折了、而其实没折成」）。
* **不动 `RunDelta` 的形状**，不给它加字段。
* **不造通用 UnitOfWork / 分布式事务框架**：两处原子性各用各的写法（第三节）。
* **不改 `promote_receipt` 的既有语义**：`run_id` 是**多出来的可选参数**，不传时逐位不变。

## 九、仍然开放（e3 之后）

B17 的条件 2 到此**通了**（账本有生产者、有事务、有实际调用者、有 MCP 读取/控制面、
有故障回执、有负例与变异控制）。仍未关闭的、与本片相邻的：

* **条件 1 的「连续两轮」端到端判据** —— 结束面通了之后才有主语，是下一片。
* 结束面在 MCP 上仍**没有工具**：`close_run` 今天只有库调用者与测试（遗留 ⑦）。
* `reconcile_run_delta` 与 `close_run` 的**生产调用点**仍只有 e3 新造的这条链。
* **`close_run` 缺 per-run 的 `PromotionOutcome` 日志**（遗留 ⑥）。
* 累计预算那一格仍是 **PARTIAL**（`RunDelta.usage` 零生产者，e2b 记录里那张表）。
* 其余遗留（`domain/errors.py` 的 `__all__` 仍缺 `ContinuationError`、
  `composition.py` 的 `__all__` 缺 5 个、`application/choices.py` 不存在而两处引它…）
  与 B17 无关，不在本片射程内。
