# B17 · Incremental Run / RunDelta / ExecutionMode —— 切片划分（2026-09-28）

权威：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:239-262`（7 条关闭条件）、`:29`（现状判词
`NOT BUILT`）、`:503`（每项必须有生产者、存储/事务、实际调用者、MCP 读取/控制、故障回执及
关闭测试）。顺序依据 `:503` 的 C00→B15→B16→**B17**→B01/B02→…

## 三份侦察的结论（2026-09-28，本 worktree）

**已经在盘上的（复用，不另造）：**

| 面 | 落点 | 它已经守住了什么 |
|---|---|---|
| 作业受理 | `domain/job.py:128`、`ports/jobs.py:210 admit(*, idempotency_key, request_sha256)` | 同一键配不同内容 = 冲突；同键重发拿回同一个作业 |
| 租约/结算 | `ports/jobs.py:391 lease` / `:422 settle`（唯一结果写者） | 两个 worker 领同一件；陈旧 attempt 不覆盖新版本 |
| 已受理作业内的续跑 | `application/continuation.py:427 ContinuationController.tick`、`interfaces/controller.py:559 run_resident` | §B21：断线后 work item 继续并回池；唯一受管唤醒入口（30s） |
| 维护到期判定 | `domain/maintenance.py:612 what_is_due`、`:328 ChangeSignal`、`:419 due_dedupe_key` | 到期/信号 → 一件格子工作；`(dedupe_key, attempt)` 主键天然幂等 |
| 预算 | `ports/budget.py:45 BudgetLedger`、`domain/budget.py:34 reservation_id`、超支抛 `BudgetExhaustedError`（`sqlite/ledger.py:163`） | 累计只有一处 `SUM` 语义；幂等靠主键；**不自动退款** |
| 截断 | `application/caps.py:93 apply_cap` + `TruncationDecision` | 「哪 25 个」必须被记录，不能静默丢 |
| 计划预览 | `application/plan_preview.py:239 preview_plan` | 零 provider 调用是**构造**（签名拿不到端口）；它明说 stop 条件不在这层 |

**零命中的（B17 要造）：** `RunSpec`、`RunDelta`、`ExecutionMode`、`bounded_auto`、
`watch`、`SavedView`、`Schedule`、`deadline`/`stop_condition`/`budget_envelope`、Run 实体、
`list_jobs` 式队列入口。另：`RunStatus`（`domain/enums.py:449`）**有定义、零消费者**；
`run_id` 今天只是记账标签（`adapters/fetch_attempts.py:224` 逐字写着它的业务归属随 B17）。

**两个必须诚实记录的差量：**

1. **`Workstream` 全仓不存在**（`marketops/` 里只有注释说它不存在）。Run 的十格里那一格今天
   由 `workspace` 承载（`domain/scene.py:129 SceneVersion.workspace`）。将来 Workstream 落地时
   它是**另一个格**，不是这一格改名 —— 现在假装它有值就是伪造。
2. **`cutoff` 这一维不存在**（两处命中都不是运行截止）。三阈值 stop 里只有对象数与预算有抓手。

（a 片落地时补记的第三条差量，切片 d 会正面撞上它）

3. **`run_id` 这个名字今天指着两个不同的空间。** 一个是本片造的
   `domain/run.py::run_id(workspace, idempotency_key)` —— 内容寻址、32 位十六进制；
   另一个是既有的**标签**：`application/pipeline.py:125 _derived_run_id()` 造
   `run-20260926t101530` 这种形状，同一个值被 `RunReport` / `RouteContext` /
   `FetchAttempt.run_id` / `ResearchRun.run_id` 四处带着，而
   `adapters/fetch_attempts.py` 逐字写着那几格的「业务归属随 B17 的 Run 与 WorkItem
   到来」。两者形状不撞（一个是 hex、一个是带前缀的秒级时刻），所以
   `test_the_four_id_spaces_do_not_collide` 不覆盖它 —— 它**不是**一个 id 冲突，
   是一次命名冲突：切片 d 要么让 B17 的 `run_id` 落到那些格上（于是「这一轮取过的
   每一个字节」可归因），要么明确它们继续是两件事。**在 d 之前不许假设前者**：
   今天的 `_derived_run_id()` 是一个只保证「这一趟有名字」的值，不是可追责的 id。

## 切片（每片一个提交，每片自带判据与故障回执）

| 片 | 内容 | 关掉哪条 |
|---|---|---|
| a | **受理面**：`ExecutionMode` 闭集 + `RunSpec` 十格（含 `BudgetEnvelope` 与第一个真正的 `cutoff`）+ `Run` 实体 + `RunStore`（admit/get/set_state）+ SQLite/内存两实现 + 受理用例（拒绝回答）+ MCP `submit_run`/`get_run` | 1 的前提 |
| b | `RunDelta` 的形状与一次结束：十五格计数 + 「合法零新增仍能以 unchanged/held/blocked 结束」+ 落盘 + 读回 | 2 |
| c | 幂等合并与双向对账：同一 Delta 重放不重复；业务键双向（池子行 ↔ Delta 计数） | 2 |
| d | 增量：同一 workspace 连续两个 manual Run，第二轮复用已沉淀事实（接 `reuse` / `refresh.census_for`），只补新/缺/过期 | 1 |
| e | 授权语义：`manual` 默认（终态后 idle，绝不自行扩下一批）+ `bounded_auto` 的显式窗口 + 三阈值 stop + 重放 trigger 幂等 | 3 / 4 |
| f | `watch`：把维护到期（`what_is_due`/`ChangeSignal`）接成维护 Run；SavedView 查询不创建 Run | 5 |
| g | Run preview：零 provider 调用地报告「复用多少 / 补哪些缺口 / 预算与 stop / 哪些歧义进 Review」 | 1 的后半 |
| h | 并发/重放/CAS 负例：Delta 合并的并发负例；旧 session 不用旧 snapshot 覆盖新事实 | 7 |
| m | **MCP 面**：受理面（`submit_run`）与读回面（`get_run` / `get_run_increment`）接线 —— 补 §6 的「实际调用者」与「MCP 读取/控制」两格；**关的不是某一条关闭条件** | §6 完成判据 |

关闭条件 6（无可证明市场总量时报告可观测池子状态，不产生「国家完成率」）横跨 b/h，随池子读数一并关。

## 落地记录（上表是计划；这一段是实际）

**a 已落（2026-09-28）**，三个提交，工作树 clean：

* `b82f0cf` B17-a1 —— 域层形状：`ExecutionMode` / `RunStatus` / `domain/run.py`。
* `10387d4` B17-a2 —— `ports/runs.py`（含 `admission_refusal`）+ 两个实现 +
  `tests/test_run_contract.py`（30 条）。
* `c2bf1df` B17-a3 —— `application/run_admission.py` + 模块表一行 +
  `tests/test_run_admission.py`（13 条）。全量 `5830 passed, 4 skipped`、`ruff` 干净。

与计划的三处差量，逐条记下（都不是顺手改，是查清之后改的）：

1. **`RunStore` 上没有 `set_state`，`Run` 上也没有 `state`。** 唯一说得出口的结局是
   `stop_reason: RunStatus | None`，而 `stop` 这一动**随 `RunDelta` 一起进**（切片 b），
   理由与 `settle` 带着 `Settlement` 一起进完全一样：「这一轮停了吗」与「它产出了
   什么」必须在同一个写事务里发生。a 里先放一个 `set_state`，就是先开出一条能单独
   写下结局的路。
2. **没有 `BudgetEnvelope` 这个类型。** 信封就是 `tuple[RunBudgetLine, ...]`，校验器
   在 `RunSpec` 上（非空 + 同科目不重复）。多包一个名字今天只会多一处要同步的地方。
3. **MCP 面从 a 里拆了出去。** 它要新开一份目录文档与一个新阶段字母
   （`tests/test_security_catalog.py` 按**来源**判阶段，产品环那份 `J` 是先例），
   而读回工具值得读的那部分（Delta）在 b 之后才存在。拆出来单独一片，是为了让
   「一个新权限面」不跟着一次重构一起过评审 —— 它是 §6 的「MCP读取/控制」那一格，
   不是 §7.2 的收尾动作。**B17 因此仍未关闭。**

## 本片（a）的边界

只做**受理面**，且只让 `manual` 走通：`bounded_auto` / `watch` 在 a 里**受理即拒**，
理由逐字写「授权窗口这一格今天没有生产者」—— 那不是缺陷，是这几格还没有上游（e/f 才造）。
拒绝要比假装受理诚实：一个受理了却不带授权语义的 Run，会让「mode 不是权限捷径」这句
（`:249`）在盘上无处成立。

## 落地记录 · b

**b 已落（2026-09-28）**，一个提交，工作树 clean：

* `208b426` B17-b1 —— `domain/run_delta.py` + `Run` 的三格配对 + `RunError`/
  `RunStateError` + `ports/runs.py` 的三条判据与 `RunStore.stop` + 两个实现 +
  `_RUN_DDL` 的两列与三格 `CHECK` + `tests/test_run_delta.py`（44 条）
  + `tests/test_run_ending_contract.py`（33 条）。全量 `5909 passed, 4 skipped`、
  `ruff` 干净。七次变异全部 `exit == 1` 且红名单非空。

与表里那一行（「每片一个提交」）的差量：**没有拆**。理由记在提交信息里 ——
这一片的两个半边是同一个机制：`Run` 上那两个新格存在的唯一理由是「结局与产出必须
同时落下」，而 `ports/runs.py` 那三个函数在 `stop` 落地之前一个调用者都没有。
拆开会得到一个装着三条无人调用的函数的提交，那正是 §6 点名不算完成的那种形状。
（对比 a：a 的三片是三个不同的受众 —— 域层形状 / 端口与实现 / 用例，所以拆是对的。）

另外三条差量，都不是顺手省掉的：

1. **`Action` 半格不建。** 计划 `:251` 的 Delta 清单里 `Action/Review` 是并列的，
   而 `ActionItem` 本仓不存在（`:31` / `:321-329` 逐字 `NOT BUILT`，那是 B24 的
   东西）。所以 `RunDelta` 只有 `reviews` 一格。**切片 c/h 不因此少判任何一条** ——
   Action 那半格随 B24 一起进，届时 `RunDelta` 加一格。
2. **`usage` 装预留 id，不装金额。** 金额只有一个权威（`ports/budget.py` 那一处
   `SUM`），在回执里再抄一个数就是同一个量的第二个答案。于是 `RunDelta` 保持是
   `StrictModel`，**不**引入 `arbitrary_types_allowed`（全仓零命中，不为这一处开）。
3. **`ending_refusal` 只拒 `COMPLETED` + **每一格都空**。** `PARTIAL` / `FAILED` 可以是
   空的：一轮在跑起来之前就断掉，产出确实是零，要求它编点什么出来才是伪造。
   三格配对的 `CHECK` **不**表达这一条（SQL 表达不出「`COMPLETED` 必须非空」的
   那半句，近似写法会让形状的改动变成一次迁移），它在 `ending_refusal` 里。

**条件 2 的完成度**：前半句（完整 Delta 落盘 + 读回）本片关了；「业务键双向对账」
与「同一 Delta 重放不重复」是切片 c。**条件 2 因此未关闭。B17 未关闭。**

## 落地记录 · c

**c 已落（2026-09-28）**，一个提交，工作树 clean：

* `80c5c8b` B17-c1 —— `domain/run_delta.py` 的第三个划分 + `domain/errors.py` 的
  `RunReconciliationError` + `application/run_reconciliation.py`（新）+ 模块表一行
  + `tests/test_run_reconciliation.py`（64 条）。5 files changed, 1147 insertions(+),
  0 deletions。全量 `5909 passed, 4 skipped` → **`5973 passed, 4 skipped`**（差值
  `+64` 与本文件条数逐个相等，没有别的文件被动到）、`ruff` 干净。
  十一次变异全部 `exit == 1` 且红名单非空，还原后 sha256 MATCH。

与表里那一行（「同一 Delta 重放不重复；业务键双向（池子行 ↔ Delta 计数）」）的差量，
两条，都不是顺手省的：

1. **对账是「池子行 ↔ Delta **键集**」，不是「池子行 ↔ Delta **计数**」。** 表里
   「Delta 计数」那个措辞是设计时的草稿，而它对不上计划 `:251` 那句话 ——
   「"池子变大"包括纠错和 currentness 改变，不只计算新增行」。纠错与 currentness
   改变**对计数是零**，所以按计数对账的实现读不出计划点名的那件事。判据是
   `test_the_reconciliation_compares_key_sets_not_counts`（两侧计数相等、键集不同）。
2. **`close_run` 单列成一片里的一个函数，而不是让调用方自己看读数。** 条件 2 是一句
   关于**结束**这个动作的话；挪到调用点上，分家的形态是两次结束里有一次忘了看，
   而报告上那一次与这一次逐字相同。

三处是**判据先红、我才改**的（逐条写在提交信息里）：`missing_rows` 必须算 `folded`
的归宿；「六个键表两两不相交」把 `contradicted_folds ⊆ named_without_a_pool_claim`
这个**必然**关系判成了违规；`ordered_union` 是**并集**原语而**不是**去重的尺子
（`seen = list(left)` 把左表原样收下），初稿援引它当尺子而一个重复都没去掉。

**条件 2 已关**：完整 Delta 落盘 + 读回（b）与业务键双向对账 + 幂等（c）都在。
**B17 未关闭**：d（增量）/ e（授权语义）/ f（watch）/ g（Run preview）/
h（并发·重放·CAS 负例）与 MCP 面都还没做。

## 落地记录 · d

**d 已落（2026-09-28）**，一个提交，工作树 clean：

* `B17-d1` —— `application/run_increment.py`（新）+ 模块表一行 +
  `tests/test_run_increment.py`（33 条）。3 files changed（2 新增、1 修改一行）。
  全量 `5973 passed, 4 skipped` → **`6006 passed, 4 skipped`**（差值 `+33` 与本文件
  条数逐个相等，没有别的文件被动到）、`ruff check .` 干净、契约重生成与 committed
  逐字节一致（本片没有新增契约模型）、13 次变异全部 `exit == 1` 且红名单非空，
  还原后两个被变异文件的 sha256 **MATCH**。

### 本片最大的一个发现，它改掉了原设计

原打算是**新建一套四格判定**。动手之前查明：`domain/maintenance.py::aspect_states`
**已经存在**，而且给**每一格（含没到期的）**一个 `AspectStatus`，它的 docstring 逐字
写着「它是**读法**，不是第二处判定」。于是四格里**三格已经在盘上**：

| 本片判词 | `AspectStatus` | 那一格在钟上的位置 |
|---|---|---|
| `NEW` | `NEVER_OBSERVED` | 从没看过 |
| `EXPIRED` | `DUE` | 看过，窗口过了 |
| `REUSE` | `CURRENT` | 看过，窗口未到 |
| `MISSING` | —— | **本片加的第二根轴** |

所以本片是 `aspect_states` ∧ `census_for` 的**复合**，不是第二处判定。这正是本仓反复
警告的那个形状（两处各算一遍，两处迟早分家）。若按原设计写，`aspect_states` 会变成
一个「有定义、零消费者」的读法，而新的四格判定会与它在同一件事实上给两个答案。

`state.status is AspectStatus.CURRENT` 之外的两档走一张**显式的表**
（`_VERDICT_OF_STATUS`），并由一条判据钉住它「覆盖除 `CURRENT` 以外的每一档」——
照 `_STALE_PRECEDENCE` 的先例（「有测试钉住这张表覆盖全部理由」）。没有执行器的覆盖表
会在枚举新增一档时静默漏掉它。

### 真正的缺口是「缺」，而它的判据不是钟

`AspectStatus` 的 docstring 对 `NEVER_OBSERVED` 单独加了一句：「它也**到期**，但不与
前者合并……两者的处置都是「去看」，但报告上必须分得开 —— 一个说「维护跑得对不对」，
一个说「这个市场建起来了没有」。」本片的 `NEW` 与 `EXPIRED` 就是那句「分得开」的落地，
`reason` 是它唯一说得出口的地方。

而第四格 `MISSING` 的存在理由是：**`SOURCE_COVERAGE` 这一格会用「一个空盘点」满足钟**。
一个主体名下一条可绑定证据都没有时，`census_for` 照样返回（`rows=0`、
`store_present=True`），`run_once` 照样记一条观测 —— 于是这一格在钟上是 `CURRENT`，
而 `CURRENT` 的处置逐字是「记着它，别动」。它欠的却是一笔**从来没被满足**的账，钟只会
把它推得更远。判据是 `EvidenceCensus.new_source_gaps`，而它「数的是「几笔待办」，不是
「几个来源」」—— 一条都没有就是**一笔**。

**承重判据是 M1**：把实现变回天真的划分（`works = NEVER_OBSERVED ∪ DUE`，也就是今天
的 `what_is_due`），6 条判据点名转红。天真的划分会把一个**一条依据都没有**的主体整个
报成「复用」—— 而「复用」是一句「有东西可复用」的话。这与切片 c 挡住的错同形：
报告上说没变，而事实是缺。

优先级是**钟先判、盘点只在 `CURRENT` 上问**：「新」压过「缺」（两者都可能是空盘子，但
「缺」的前提是「我们看过、结论是没有」）。由此「到期的格子不读盘」与「别的面不读盘」
都是这一条的直接后果，而它们各有一条判据（用一个数 `list_evidence` 次数的替身量具 ——
断言结论相同分不出「没问」与「问了、答没有」）。

### 与表里那一行的差量，两条，都不是顺手省的

1. **「接 `reuse`」这一句没照做，因为它是设计时的草稿。** `reuse.py` 的 `ReuseDecision`
   是**取回前**的去重（同一个 URL 在同一个许可范围里只取一次），执行在
   `refresh.py` → `adapters/page_mirror.py` 那条链上，`plan_preview.py` 与
   `interfaces/controller.py` 已经在用它。本片要判的「复用」是**格级**的：这一格这一轮
   动不动。把 `ReuseDecision` 接进来会是两个主语混成一个 —— 一次取回被省下，不等于
   一格被复核过。
2. **`census_for` 拿到了它模块外的第一个调用者。** 本会话实测：生产代码里此前只有
   `refresh.py:937` 一处调它，别的都在测试里。本片让它成为「缺」这一格的判据 ——
   §6 的「实际调用者」那一格因此有了一个真的。

### 差量 3（`run_id` 指着两个空间）**关闭**

处置是**明确它们继续是两件事**，理由是接起来需要一次真正的端到端改动（「哪一轮取过
哪些字节」真的可归因，而那条链今天拿不到 Run），顺手接上会得到一堆「字段填了、没有
生产者」的格 —— 正是 §6 点名不算完成的形状。执行器是 `RunIncrement.run_id` 的**形状
校验器**（32 位小写十六进制）：B17 的 `run_id` 是内容寻址的，而
`application/pipeline.py::_derived_run_id()` 造的是标签。两者形状本来就不撞，
所以挡住它们并排躺在同一列里的**唯一位置**就是这一格 —— M7 判它。

### 本片的第二个半边：条件 1 的后半句，挡在开工之前

`admit` 是幂等的：同一个键重发拿回的是**同一行**，哪怕那一行已经结束
（`admission` 的 docstring 逐字：「幂等由主键保证，不是靠写之前查一下」）。于是
「重发一个已经结束的键」在**受理**上长得像一次成功 —— 而它会让第二轮真的又跑一遍：
池子第二次折、成本第二次花，而报告上第一轮与第二轮逐字相同。`plan_increment` 对已结束
的一轮**拒**（`RunStateError`），而且**在存储被读到之前**（有判据：用那个数
`list_evidence` 次数的替身，拒的时候一次都没读到）。

### 一条守卫是照抄既有纪律，不是新造的

`observations` 里任何一条的 `workspace` 不是这一轮 Run 的工作区 → `ValueError`。
`Observation.workspace` 的 docstring 逐字写着它是「**授权链的第一段**」，而
`ports/store.py` 对混装写的是「写别人的被拒，读别人的**报错**」。读别人的观测再记成
这一轮的复用，是一次不留痕的跨工作区读，而报告上它看起来只是「复用得很充分」。

### 条件 1 **未关闭**

判据齐了（第二轮的四格读法 + 已结束就不开工），而它在生产代码里**零调用点** —— 与
`admit_run`（a3）、`close_run`（c1）同一个形状：调用点随 **MCP 面**那一片落地，而
§6 的「实际调用者」那一格由那一片一起补。另外，「不重复池子」的**并发**那一半（两个人
同时拿着同一行开工）是切片 h 的正面问题，本片不为它猜一个形状。

### 本片新增的遗留（不要丢）

* **`domain/errors.py` 的 `__all__` 缺三个已定义的名字**：`RunError`、`RunStateError`
  （b 的遗留）、`ContinuationError`（更早）。本片**没修**：后一个与本片无关，只补前
  两个会让这张表看起来修好了而仍然不全。而今天**没有执行器**判 `__all__` 的完整性
  （`command grep -rn "__all__" tests/` 只命中三个 fixtures 文件）—— 要修就得连执行器
  一起做，是独立一片。判据（本会话实测）：`vars(errors)` 里定义、`__all__` 里没有
  的正好这三个。
* **`RunStore.stop` 的幂等在 `close_run` 里是「逐字段相等」判的**，因为 `Run` 是
  frozen dataclass 而**不是** pydantic 模型（没有 `model_dump_json`）。后面写结束面
  判据的人会再撞一次这个。

## 落地记录 · m1（受理面接线）

**m1 已落（2026-09-28）**，一个提交，工作树 clean：

* `marketops/adapters/mcp_server/runs.py`（新）—— `run_handlers` + `submit_run`；
* `marketops/interfaces/mcp/__main__.py` —— `_compose` 里 `runs = run_handlers(...)`
  与 `build_server(runs=runs, ...)`（第 23 个关键字参数）；
* `marketops/security/catalog.py` —— 目录第 56 条（`submit_run`，`_CONTROL`）+
  `§B17 经营 Run（受理面）` 子块标记；
* `docs/MCP_PRODUCT_LOOP.md` —— §2 表格尾部一行；
* `tests/test_mcp_runs.py`（新，21 条）、`tests/test_mcp_product_loop.py`（载荷与
  `runs=` 接线）、`tests/test_run_contract.py`（+3）、`tests/test_security_catalog.py`
  （+4，目录加一条 = 四处参数化）、`tests/test_mcp_isolation.py`（+1）。

全量 `6035 passed, 4 skipped`（本片之前在 `000ebbf` 上量的基线是
`6006 passed, 4 skipped`，增量 **+29**，四个文件逐一对上：21 = 20+1 / 3 / 4 / 1）、
`ruff check .` 干净。**八条变异全部 `exit == 1` 且红名单非空**，八条还原后
`sha256` 与变异前一致（逐条报文见 `m1-mutation-report.txt`）。

### 为什么 `m` 这一片原表里没有

原表是按**关闭条件**排的（a 到 h 各自关掉 1/2/3/4/5/7 的某一条），而 MCP 面关的是
**§6 的完成判据**：「每项必须有生产者、存储/事务、实际调用者、MCP读取/控制、故障回执
及关闭测试」。它不关任何一条关闭条件 —— 关的是「这些东西有没有**调用者**」那一格。
a 的差量 3 当时写着「MCP 面从 a 里拆了出去」，拆出去之后它在表上没有位置，这一片落地
时把它补进表里，是为了让表与实际不再对不上（而不是它变成了一条关闭条件）。

这一片之所以不能省：d 的落地记录（上面 :257）逐字写着「调用点随 **MCP 面**那一片落地，
而 §6 的「实际调用者」那一格由那一片一起补」。`admit_run`（a3）、`close_run`（c1）、
`plan_increment`（d）三个用例函数到今天为止在**测试之外零调用点** —— 而在
`_compose` 接上之前，`submit_run` 也只是目录里的一行：目录里有它，服务实例上没有它
（`guard.py::unbacked_message` 那一句「在目录里，但当前服务实例尚未接线」正是为这种
形状写的）。

### `_shared.py` 延后 —— 两条新理由，且触发句已到

上一片记下的触发条件是「第三个处理器族也要 `_readable` 时抽 `_shared.py`」。**已到**：
实测 `_readable`（定义在 `tools.py:1197`）现在有**四个**调用族 —— `outreach.py` /
`product_loop.py` / `review.py` / `runs.py`（本片新加的那个）。仍然延后，两条理由：

1. **剩下的家是 `tools.py`，不是一个新的 `_shared.py`。** 四个族共用的是**同一个形状**
   （把一次 `ValidationError` 摊成多行、供 `ToolError` 正文读），而 `tools.py` 已经
   是处理器共用的那个家（`Handler` / `require_scene` / `require_bundle` 都在那里）。
   `application/validation.py` 的 docstring（:15-19）已经把「什么时候该**另**写一种
   形状」写下来了 —— 它在那边是**一行**、要嵌进逐行去向里，所以那一份不并过来。四个
   族之间没有这种形状差别，新开一个模块只会多一处要同步的地方。既成事实也已经是这样：
   `adapters/mcp_server/review.py:89` 早就在 `from .tools import ... , _readable, ...`。
2. **`runs.py` 真正想搬的那两个搬不动。** `_plan_context`（`product_loop.py:3500`）与
   `_scene_record`（:3273）绑在一起（前者 :3522 调后者），而 `_scene_record` 是这个
   模块自己的私事（它读 `SceneRecord` 并解释那两种缺席）。搬 `_plan_context` 就得连
   `_scene_record` 一起搬 —— 那不是搬一个共用助手，是把 `product_loop.py` 的一段拆走。

顺带记一处**陈旧引用**：`application/validation.py:15` 与 `:18` 两处措辞说的是
`product_loop.py::_readable`，而它盘上在 `tools.py`。这是更早那次**纯搬动**的遗迹
（位置搬了，引用它的散文没跟着改）。本片不改它 —— 它与本片无关，而单独改措辞不改
位置会造成一次「文档说 A、代码在 B」的新版本。

### `composition.py` 的 `__all__` 陈旧（实测数字）

`marketops/interfaces/composition.py` 的 `__all__`（:844）**列了 17 个名字，定义了 22 个**：
缺 `default_attempt_store` / `default_review_store` / `default_run_store` /
`default_scene_store` / `default_wakeup_registry` —— **五个**，不是四个（本片新增的
`default_run_store` 让这个数从 4 变成 5，但缺口在本片之前就在）。方向是安全的
（列了但没定义的是 **0** 个，所以 `import *` 不会炸），所以它不是缺陷、是一处陈旧。
与 `domain/errors.py` 的 `__all__`（缺 `RunError` / `RunStateError` / `ContinuationError`
三个，b 片记下的遗留）合成一片，连执行器一起做。

### 本片发现并处置的三件事（都是实测，不是推理）

1. **`SqliteRunStore.admit` 写 `research_run` 却不 `bind_workspace` —— 一处真实缺陷。**
   它不是「少一句保险」：`research_run` 在 `db.py::OWNED_TABLES` 里，而 `ensure_schema`
   有反向判据 —— **有带所有者的行却没有工作区绑定就当场拒绝打开**。用探针实测过：
   受理成功之后**另开一个** `SqliteRunStore` 立刻抛 `StoreError`（UNBOUND）。在真部署里
   这是致命的，因为 `default_run_store` 是**工厂**，`get_run` 的每一次调用都新建一个
   store —— 也就是每一次读回都会退 `StoreError`，而回执上说这一轮被受理了、盘上也真的
   有一行。修法是与 INSERT **同一个写事务**里先 `bind_workspace(conn, spec.workspace,
   error=StoreError)`（类型与 `fetch_attempt.py` / `pool.py` 逐字同一条理由：判词一份、
   类型归主语）；内存实现补对等；`tests/test_run_contract.py` 加两条关闭判据（「受理之后
   那个库还能被打开」+「两个工作区不能受理进同一个 store」，后者两个实现都跑）。M1/M2
   两条变异各判它一半。
2. **两条歧义判据原先「措辞分不开」，只有版本号分得开。** 「场景是 DRAFT，没有生效版本」
   与「记录写着生效版本是第 N 版，而库里读不到那一版」是两句话，处置相反。原先两条判据
   靠**措辞**区分（一条断言含「生效版本」、另一条断言含「生效版本读不出来」）—— 而两条
   生产消息**互相引用**对方那句话（`runs.py` 那条含「生效版本读不出来」，`_plan_context`
   那条含「还没有生效版本」），所以**任一支被折成另一支都照样绿**。改成按**版本号**判
   （DRAFT 那条必须**不含** `第 1 版` 与 `latest_version=`；读不出来那条必须**含** `第 1 版`），
   M8 判它。
3. **一次变异抓出了一条判据没有笼罩的代码路径。** `run_handlers` 里
   `if runs is None: return {}` 这一支原先**没有任何判据**：那条「没接 Run 存储要说
   没接线」的测试用的是 `handlers={}`，走的是**服务面自己的 `runs=` 缺省**，与
   `run_handlers(runs=None)` 是两处不同的代码。M4（把那一支改成照样注册）第一次跑
   时 `exit == 0` —— 而那一支被改坏的表现是：这一条工具**注册了**，调用时炸在
   `runs.admit` 上，交回给调用方一句内部异常。判据已改成**两条来路都走**（参数化
   `server-defaults` / `handler-table-refuses`），M4 重跑后红的正是新增的那条参数，
   另一条仍绿 —— 红落在那一条路径上，不是蹭另一条蹭红的。

### 本片新增的遗留（不要丢）

* **`get_run` 与 `get_run_increment` 仍未接线**（m2）：它们今天在目录里、在两份文档
  里，而服务实例上没有 —— 与 m1 之前的 `submit_run` 逐字同一种形状。读回面那两条各自
  还有一条判据要新写：`get_run` 的两个分支都抛 `ToolError`（含
  `unknown_run_refusal(run_id)` 的逐字报文）；`get_run_increment` 需要一条**今天不存在**
  的守卫 —— `version.workspace != run.spec.workspace` 时要点名两者（增量是按工作区取的，
  拿 A 区的版本去读 B 区的 Run 是一次不留痕的跨工作区读）。
* **`close_run` 缺 per-run 的 `PromotionOutcome` 日志**：`reconcile_run_delta` 的第二个
  方向今天传 `folded=()`，那会**静默弱化**条件 2 —— 传一个空元组进去，判据看起来在判，
  而它判的是一个恒真的条件。这条不关闭，条件 2 不算关。
* `docs/B17_ACCEPTANCE_MATRIX_<实际日期>.md` **尚未创建**（照
  `docs/B16_ACCEPTANCE_MATRIX_2026-09-28.md` 的骨架：状态词表四词、§0 时点与基线读数、
  七条关闭条件各一个 `###`、结尾「这份矩阵不做的事」）。

## 落地记录 · m2（读回面的上半：`get_run`）

`m` 行在表上写的是「受理面（`submit_run`）与读回面（`get_run` / `get_run_increment`）接线」。
受理面在 m1 已落；本片落**读回面的一半**：`get_run`。`get_run_increment` 拆到 m3，理由见下。

### 为什么拆：两半要的东西不一样

m1 之后 `run_handlers` 手里只有 `settings` 与 `scenes`，`runs` 是它自己按需展开的工厂。
`get_run` 要的**恰好**就是这一份（按 id 读一行），所以它落在同一个处理器表里、**零新增
接线**。`get_run_increment` 要的是 `plan_increment`（`application/run_increment.py`），
而那一支还要 `RefreshPlan`、当轮的 `Observation` 与 `EvidenceStore` —— 也就是要给
`run_handlers` **新加一个 `maintenance` 参数**，并在 `_compose` 与
`tests/test_mcp_product_loop.py` 两处接线。那是一件与「加一条读」不同的活，混在一个提交里
会让「本片关掉了什么」读不出来。

### 一件实测出来的更正：这两条读回工具**不在**目录里，也不在文档里

m1 的落地记录（上面 :369）逐字写着「`get_run` 与 `get_run_increment` 今天在目录里、在两份
文档里，而服务实例上没有」。**那句话是错的**，本片实测：

- `marketops/security/catalog.py` 在 m1 之前只有 `submit_run` 那一条 §B17 项（m1 加的），
  没有 `get_run`；`docs/MCP_SERVICE.md` 与 `docs/MCP_PRODUCT_LOOP.md` 同理。
- 所以 m2 不是「接线一条目录里已有的工具」，而是**新增一条工具**：目录 56 → 57 条、
  `docs/MCP_PRODUCT_LOOP.md` §2 的并集 +1 行、`_compose` 零行（m1 已接的 `runs` 就够）。

这条更正有实际后果：m1 里那句「同一种形状」的类比在**这一半**上不成立。受理面那一次是
「目录里有、服务上没有」（`guard.py::unbacked_message` 那句「在目录里，但当前服务实例尚未
接线」正是为它写的），而读回面是「两处都没有」。同一个词在这一片里指的不是同一件事。

### `_projection`：两份回执共用的那一半

受理回执与读回面报的是**同一行**，所以「哪些格子进这份回执」只能写一遍 ——
`_projection(run)` 是那一份，`_receipt` 在它之上加三格（`admitted_at` / `recorded` /
`note`）。两份各写一遍的代价不是那十来行：先漂的是格子集合，于是一份说 `delta`、另一份
不说，而两份都声称自己报的是同一行。

### 本片发现并处置的一件事：一条判据的空洞（**变异抓出来的**）

`test_get_run_reports_exactly_these_fields` 是 M3/M4 跑完之后**补的**。

第一轮的 M3（`_projection` 的 `delta` 恒 `None`）与 M4（`stopped_at` 恒 `None`）都判事，
但红的只有 `test_a_finished_run_is_still_readable` —— 也就是「那两格**有值**」在判事，而
另一条判据（两份回执逐格相同）**照样绿**。原因是结构性的：

**两份回执同源于 `_projection`。** 于是「少一格」这件事在**任何**「两份一致」的判据里都
看不出来 —— 少的时候两份一起少，两个集合仍然相等。而这份投影**就是**这条工具的全部内容：
`budget` 少一格调用方读不到信封，`plan_ref` 少一格读不到这一次读的是哪份计划，
`idempotency_key` 少一格重发之后认不出是不是同一轮。

处置是**补判据**（写下一份 19 个名字的 `_READ_FIELDS` 逐字比集合，与受理面那张参数表同
一条纪律），而不是改变异躲开它。M7（删掉 `_projection` 里 `plan_ref` 那一行）重跑后红的
正是这条新判据。

### 参数面上一件反直觉的事：`run_id` **在**禁名单里，而读回面必须收它

`_FORBIDDEN_PARAMETERS` 是 21 名，`run_id` 是其中一员 —— 因为受理面若收下它，就等于让
调用方替 `run_id(workspace, key)` 这条内容寻址定一个值，于是同一个键可以指向不同的行。
而读回面的**唯一**入参就是它。这不矛盾：那张表管的是「**写下**一格事实的时候不准由载荷
替它说话」，而读回面在**读** —— 这一格是查询键本身，它不是被写进任何一行的东西。

判据把**相交**也一起断言出来（`parameters & _FORBIDDEN_PARAMETERS == {"run_id"}`）：将来若
有人给读回面加 `workspace=`，交集会变成两个名字，而那时红的话正好问的是那一句。

### 跨工作区那条守卫是**可达**的（所以它有判据）

`_one_run` 的第二条守卫（读到的一行属于别的工作区就拒）有个可读性上的问题：绑定契约**拒**
第二个工作区写进同一个库（`tests/test_run_contract.py` 那条），所以「库里有一行属于别的
工作区」在正常路径上不成立。它可达的那条路是：**部署换了 `settings.profile`、却仍指着同一
个 `database_path`** —— 绑定的那一侧拦的是**写**，不拦读；那时 `ensure_schema` 通过
（库有绑定），`admit` 会因「已绑定别人」被拒，而 `get` 照样读得到那一行。

判据因此是可造的：夹具先正常受理一轮，再把那一行的 `workspace` 与 `run_id` 用裸 SQL 改成
另一个（不 INSERT —— 唯一知道怎么拼出一份合法 `RunSpec` 的生产者是受理面自己）。

### 判据（`tests/test_mcp_runs.py` 第 7 节，9 条）

| # | 判据 | 它答的问题 |
|---|---|---|
| 1 | `test_get_run_reads_back_the_row_that_submit_run_wrote` | 读的是**盘上**那一行（另起一次 `_drive`：新服务端、新连接、同一个库），且两份投影在共有的格上相同 |
| 2 | `test_get_run_reports_exactly_these_fields` | 报的格**恰好**是那 19 个（「少一格」唯一的判据） |
| 3-5 | `test_an_unknown_run_id_says_there_is_no_such_run[...]` | 三个来路（形状对而库里没有 / 空 / 另一个 id 空间）都交出 `unknown_run_refusal` 的逐字报文，且服务面不在它前后另说一句 |
| 6 | `test_a_run_belonging_to_another_workspace_is_refused` | 点名两个工作区，而不是答成一次写错 id |
| 7 | `test_reading_writes_nothing` | 不多一行、也不改一行（`updated_at` 不动） |
| 8 | `test_a_finished_run_is_still_readable` | 结束之后读得到，且 `stop_reason` 与 `delta` 一起出现（空产出不是「还没有产出」） |
| 9 | `test_get_run_takes_exactly_the_id_and_nothing_else` | 入参恰好一格，且与禁名单的交集恰好是 `{"run_id"}` |

### 变异控制（7 次，每次清 `__pycache__`，判据是 `exit == 1` 且红名单非空）

| # | 变异 | 红 | 结论 |
|---|---|---|---|
| M1 | 跨工作区的读不再被拒 | 1 | 那条守卫在判事 |
| M2 | 读不到时的措辞被换掉 | 3 | 逐字报文在判事（三个来路一起红） |
| M3 | 读回面不再报产出 | 1 | 结束形态的那两格在判事 |
| M4 | 读回面不再报停表时刻 | 1 | 同上，另一格 |
| M5 | 一次读顺手写下另一行 | 1 | 「读没有副作用」在判事 |
| M6 | 「读不到」不再是一次拒绝 | 3 | 拒与不拒在判事（与 M2 判的不是同一件事） |
| M7 | 投影里少一格 | 1 | 新补的那条逐字名单在判事 |

还原后五个被变异文件的 sha256 与变异前**逐个一致**（`sha256sum -c` 全 OK）。

### 实测

- 全量 `6035 passed, 4 skipped`（m1 的读数）→ **`6049 passed, 4 skipped`**，增量 `+14`，
  四个来源逐一对上：`tests/test_mcp_runs.py` 21 → 30（+9）、目录加一条 → 四处参数化
  （`tests/test_security_catalog.py` +4）、`tests/test_mcp_isolation.py` 的 `_CONTROL_ONLY`
  （+1）、`tests/test_mcp_product_loop.py` 的载荷表（+1）。
- `ruff check .` 干净；本片没有新增契约模型。

### 本片新增的遗留（不要丢）

* **`get_run_increment` 仍未接线**（m3）。它要 `plan_increment`，而那一支的输入是**现读
  现算**的（`RefreshPlan` + 当轮 `Observation` + `EvidenceStore`）—— 所以它读回的不是盘上
  的一行，而是**按当下的盘重算一份**。这是它与 `get_run` 的结构性差别，也是它需要
  `run_handlers` 新加 `maintenance` 参数的原因。
* `close_run`（结束面，写）仍缺 per-run 的 `PromotionOutcome` 日志；`reconcile_run_delta`
  的第二个方向今天传 `folded=()`，那会静默弱化条件 2。
* `docs/B17_ACCEPTANCE_MATRIX_<实际日期>.md` 尚未创建。

## 落地记录 · m3（读回面的下半：`get_run_increment`）

m2 把读回面拆成两半，本片落第二半。§6 的「MCP读取/控制」那一格到今天才算接上：
两条读回都接上了。

### 为什么这一条不是「读盘上一行」

这是它与 `get_run` 唯一的、也是结构性的差别，值得逐字写下来：

`get_run` 报的每一格都在盘上那一行里。`get_run_increment` 报的每一格**都不在**：
它的输入是 `RefreshPlan`（场景生效版本中的一格）、当轮 `Observation`（维护账本）、
变更信号（同账本）与 `EvidenceStore`（盘点）。这四样没有一样存在 Run 那一行上 ——
`RunSpec` 里只有 `selector` / `policy_versions` / `budget` 那一组，
`policy_versions` 是 `policy_versions_of(plan)`，**不含** refresh 的 `policy_version`。

于是两处后果：

* 同一个 Run 前后读两次，**可以**得到两份不同的读数，而这不是缓存失效 ——
  中间有人往账本里沉淀了一条观测就会这样（本片判据 1 逐字钉住这件事）。
* 它所报的 `at` 是**这一次调用**的时刻。它答不出「这一轮当时是怎么算的」——
  那个问题今天没有落点，因为 `RunIncrement` **不被持久化**。这是本片留下的一处
  诚实缺口，写在下面「遗留」里。

### `subjects` 与 `scene_id` 两格为什么**没有**出现

`plan_increment` 有 `subjects: tuple[tuple[str, str], ...] = ()` 这个入参，本层**恒传
`()`**；它是 `_known_subjects(observations, subjects) = {(o.subject_kind, o.subject_id)
for o in observations} | set(subjects)` 的第二项，也就是「格子空间 = 已沉淀的观测 ∪
显式声明的」。所以本层报的格子空间**只由盘上沉淀了什么决定**。

不收 `subjects` 的理由：那是「显式声明一批主体」的入口，而收下它会让**同一条 Run 的
读数依赖调用方的参数** —— 一条读回面工具开始回答一个由调用方提的问题，而不是回答
「这一轮照今天的盘该做什么」。`get_maintenance_plan` 有 `cohort` / `name` 那个入口，
那是它的形状（它本来就是给一批主体算维护计划的）。

代价必须一起报出来：**零观测 ⇒ 零格**。而零格与「没有事要做」在同一份回执上同形。
处置不是补一个假格子，是**把喂进去的输入规模一并报出来** —— 回执上的 `inputs`：

```json
"inputs": {"observations": 0, "signals": 0}
```

判据 2 逐字断言 `cells == []` 与 `inputs == {"observations": 0, "signals": 0}`
**同时**成立，并在 `note` 里逐字写下这个搭配该怎么读（「零格配零观测是『这里还没有任何
沉淀下来的事实可复用』，**不是**『这一轮没有事要做』」）。

`scene_id` 不收的理由与 m1 的 `submit_run` 那一格逐字同款：`require_scene`
（`tools.py`）**硬拒任何非 `settings.profile` 的 `scene_id`**，所以这个参数只有一个合法
取值。收下它等于让调用方以为可以指另一个场景，而它不可选。

`now` 不收的理由：`plan_increment` 的 `at` 必填、时钟从外面进来，那是**域层的**纪律
（它保证「钟上到期」这件事不被函数内部偷偷读表）。服务面接一个时钟参数是另一件事 ——
那会让调用方替「今天几点」说话，于是判「该不该重取」的依据由调用方给。
服务面用 `utc_now()`，并把这一刻报在 `at` 上。判据 10 把这三格逐字点名断言它们**不在**
参数面上。

### 一条不可达的守卫由谁覆盖（两处，都写下来）

* `plan_increment` 的 `ValueError`（观测不属于这一轮工作区）在服务面上**到不了**：
  这里的观测来自 `maintenance().observations(workspace=run.spec.workspace)` ——
  工作区是**查询条件**（SQL 的 WHERE），不是事后比对。这与 m1 落地记录里那条
  「`version.scene_id != run.spec.selector` 不可达」是同一类处置。
* 本层因此**不为它造判据**，并在 `get_run_increment` 的 docstring 里逐字写明
  「这不代表它多余……造不出来，而一条判不出事的判据在报告上会长得像『这条守卫是活的』」。
  它由 `admit_run`（写那一侧把工作区绑进 Run）与 `observations(workspace=...)` 覆盖。

### 只报 `counts` 不报 `works`

`RunIncrement` 的两个 property 都不在 `model_dump` 里，服务面要自己决定报哪个。
报 `counts`（四档都出现，**零也出现**），不报 `works`：

* `works` 是 `verdict != REUSE` 的那些格 —— 调用方从 `cells` 里筛一遍就有了，
  多报一格是与 `cells` 同源的重复。
* `counts` 的那条纪律（**零也出现**）**读不出来**：从 `cells` 数只能数出「出现过几格」，
  「这一档是 0」与「这一档缺席」在 `cells` 上不同形，在 `counts` 上才同形。
  这正是判据 2 判的东西，也是变异 M2 弄坏的东西。

### `SqliteSceneStore` 的权威是 `payload` 列（判据 7 抓出来的实测）

判据 7（市场被关上时用自己那一句话拒绝）第一轮是**绿的、而什么都没发生**：夹具用裸 SQL
把 `scene` 表的 `effective_version` 列改成 `NULL`，回执却照样成功。

探针（`-s` 跑一条临时打印 `SELECT * FROM scene`）实测：**列确实变成了 `NULL`，而
`payload` JSON 里仍是 `"effective_version": 1`** —— `SqliteSceneStore.get_record` 从
`payload` 反序列化，而 `_check_record_columns` 的 SELECT **不含**那一列，所以改它既
不生效也不报不自洽。

修法是改走 store 自己的写路径（`put_record(current.model_copy(update={...}),
expected_revision=current.revision)`），并把这次实测逐字记在夹具的注释里。这条不是
关于 `get_run_increment` 的，它是**任何**用裸 SQL 造夹具的测试都可能踩的坑。

### 判据（`tests/test_mcp_runs.py` 第 8 节，10 条 / 12 个用例）

| # | 判据 | 它答的问题 |
|---|---|---|
| 1 | `test_the_increment_is_computed_against_the_book_as_it_stands_now` | 读的是**当下的盘**：同一行读两次、中间沉淀一条观测，零格 → 七格，而那一行的 `updated_at` 逐字不变 |
| 2 | `test_an_empty_book_reports_zero_cells_along_with_its_inputs` | 零格配零观测要一起读；`counts` 四档**都在**且都是 0 |
| 3 | `test_every_cell_carries_a_verdict_a_reason_and_the_plan_version` | 逐格三样齐全，且 `policy_version` 是本部署那一份；`counts` 与 `cells` 说的是同一件事 |
| 4 | `test_a_source_coverage_cell_with_nothing_to_bind_is_missing` | 七格齐、钟都在，只有「来源覆盖」那一格被判**缺**（reason 含「能当依据的来源」） |
| 5 | `test_a_change_signal_moves_its_own_cell_off_reuse` | 变更信号真的参与判定，且 `inputs["signals"]` 跟着动 |
| 6 | `test_a_finished_run_refuses_to_plan_an_increment` | 结束的一轮拒开工，措辞来自域层（本层一个字没改写） |
| 7 | `test_a_market_that_is_closed_says_so_in_its_own_words` | 市场被关上时说的是**这一侧**的话：含 `settings.profile` 与「受理过」，**不含**受理面那一句 |
| 8 | `test_an_unknown_run_id_is_refused_here_too[...]` | 三个来路都交出 `unknown_run_refusal` 的逐字报文，且只出现一次 |
| 9 | `test_reading_the_increment_writes_nothing` | 不多一行、也不改一行（`research_run` / `observation` / `change_signal` 三本账前后相同） |
| 10 | `test_get_run_increment_takes_exactly_the_id_and_nothing_else` | 入参恰好一格；与禁名单的交集恰好 `{"run_id"}`；三格**没有**出现且各有理由 |

判据 4 写第一版时红过：只观测 `SOURCE_COVERAGE` 一面的话，其余六格是 `NEW` 而不是
`CURRENT`（`seen is None` ⇒ `NEVER_OBSERVED`）。修法是**七格齐** —— 七格都过了钟，
那一句问的才是「同样过了钟的七格里，为什么只有它被判缺」，也才判得出 M4 那条变异。

### 变异控制（10 次，每次清 `__pycache__`，判据是 `exit == 1` 且红名单非空）

| # | 变异 | 红 | 结论 |
|---|---|---|---|
| M1 | 增量不再读盘上的观测 | 1 | 「按**当下的盘**算」在判事 |
| M2 | `counts` 只报出现过的档 | 1 | 「零也出现」在判事 |
| M3 | `counts` 与 `cells` 脱钩 | 1 | 两半说的是同一件事，在判事 |
| M4 | 「缺」那一格不再被判出来（**域层**） | 1 | 第四格在判事 |
| M5 | 变更信号不再参与判定 | 1 | 「读到了」与「用了」是两件事，在判事 |
| M6 | 结束的一轮不再被拒 | 1 | 拒开工在判事 |
| M7 | 市场被关上时改用受理面那一句 | 1 | 两处的话分得开，在判事 |
| M8 | 未知 id 不再走那一条读法 | 3 | 逐字报文在判事（三个来路一起红） |
| M9 | 一次读顺手写下另一行 | 1 | 「读没有副作用」在判事 |
| M10 | 入参面多一格 | 1 | 入参面在判事 |

十条的**还原比对全部一致**（脚本每次 `before`/`after` 各算一次 sha256）。外部旁证：
`marketops/application/run_increment.py` **不在** `git status` 里 —— 它不属于本片
（本片零改域层），所以那条域层变异还原后必须回到 HEAD 状态，而它确实回去了。

M4 是本表里唯一落在**域层**的变异，理由写在 `MUTATION-M3.py` 的模块 docstring 上：
m1/m2 的变异全在服务面，那是它们的形状决定的；m3 接的是 `plan_increment`，而第四格
（「缺」）住在域层 —— 服务面上没有任何一行代码能让那一格不再出现。这不是放宽纪律，
是把变异放在**判据钉住的那一行**上。

M7 与 M8 的第一版锚点**各命中两次**（M7 的 `if version is None:` 与 M8 的 `_one_run(...)`
在 `submit_run` / `get_run` 里各有一份同样的行）。这不是脚本的毛病，是这三条工具
**共享同样的行**的正面证据；处置是把锚点往下多取一行（各自的拒绝措辞与
`scene_id=None` 那条注释），而不是给锚点加行号 —— 行号会随下一次改动失效，而
逐字锚点失效会**报错**，行号失效会**静默改错地方**。

### 实测

- `tests/test_mcp_runs.py`：30 → **42**（+12）。
- 定向五件套（`test_mcp_runs` / `test_mcp_product_loop` / `test_mcp_isolation` /
  `test_security_catalog` / `test_run_contract`）：508 → **525 passed**（+17）。
- 全量：`6049 passed, 4 skipped`（m2 的读数）→ **`6066 passed, 4 skipped in 399.30s`**，
  增量 `+17`，三个来源逐一对上：`test_mcp_runs.py` 30 → 42（+12）、目录加一条带来的
  四处参数化（`tests/test_security_catalog.py` +4）、`tests/test_mcp_isolation.py` 的
  `_CONTROL_ONLY`（+1）。全量跑在**定稿内容**上，十条变异之后逐字节还原。
- `ruff check .` **All checks passed!**；本片没有新增契约模型。

### 本片关掉了什么

`plan_increment`（`application/run_increment.py`）今天**有了第一个生产调用点**。
在此之前它是 §503 那句「只有类名/字段/接口的无消费者实现不算完成」点名的形状 ——
它自己的判据（`tests/test_run_increment.py`）齐、域逻辑写过、**没有一个人在生产路径上
call 它**。本片接上之后，§6 的「MCP读取/控制」那一格对 B17 的 Run 面才算接上两条读回
（`get_run` 读盘上那一行、`get_run_increment` 按当下的盘现算一份）。

仍零生产调用点的只剩两条：`reconcile_run_delta` 与 `close_run`（都等 e/f/g/h 片）。

### 本片新增的遗留（不要丢）

* **`get_run_increment` 不接主体来源**（`subjects` 恒为 `()`）。后果是格子的空间
  **只**由已沉淀的观测决定，于是「还没有任何观测的工作区」拿到零格。处置是报 `inputs`
  并在 `note` 里逐字写明零格配零观测该怎么读，但**格子空间不可由调用方声明**这件事本身
  是一个缺口 —— 它要么由维护那一侧先沉淀观测来补，要么将来给这条工具加一个与
  `get_maintenance_plan` 同形的入口（那时要回答的是「两条工具报的格子空间为何不同」）。
* **`RunIncrement` 不被持久化**。于是「这一轮当时是怎么算的」今天没有落点：
  同一个 Run 前后读两次可以不同，而这不是缓存失效。这是 m3 与 `get_run` 最深的一处
  差别，也是 e/f/g/h 片（`close_run` 那一侧）要回答的问题 —— 如果定稿要留下
  「开工前那份读数」，那时的落点应当与 `RunDelta` 一起。
* `close_run`（结束面，写）仍缺 per-run 的 `PromotionOutcome` 日志 ——
  `reconcile_run_delta` 的第二个方向今天传 `folded=()`，那会**静默弱化**条件 2，
  所以条件 2 不算关。
* `docs/B17_ACCEPTANCE_MATRIX_<实际日期>.md` 尚未创建。

## 切片 e 的边界与拆法（侦察之后，2026-09-28）

m 片（m1/m2/m3）落了之后，切片表上剩 e / f / g / h。**下一片是 e**（授权语义，关
条款 3 / 4）。动手之前先侦察，四条结论：

1. **`bounded_auto` 与 `watch` 今天是受理即拒的**，而且有两条测试钉死这件事：
   `tests/test_run_contract.py:185` 与 `tests/test_run_admission.py:340`（各一处参数化，
   两个模式各一个用例）。判据在 `ports/runs.py::admission_refusal` 的第一条 ——
   逐字「模式今天没有授权窗口的生产者」，且它自己写明「**这不是缺陷**」。
   → e 片要做的是**把那个生产者造出来**，于是这两条测试要**改**（不是删）：从
   「两个模式都拒」改成「`watch` 仍拒（f 片造它）、`bounded_auto` 带窗口则受理、
   不带窗口则拒」。改判据不是修红，理由要写进提交信息。
2. **`RunStore` 只有三个方法**（`admit` / `get` / `stop`），其中没有一处会创建下一轮。
   生产代码里 `.admit(` 的调用点**恰好一个**（`application/run_admission.py:132`），
   它由受理面 `submit_run` 走到。→ 「manual 完成后绝不自行扩下一批」这句话今天在
   **结构上**成立，而 e 片的判据要做的是**把它钉住**（照
   `test_no_hardcoded_market_knowledge.py` 与 `test_security_catalog.py` 的先例：
   一条**面**判据，而不是一条行为判据）。
3. **条款 3 只有后半句属于 e。** 逐字：「关闭所有客户端后**当前 Run**继续并完成；
   完成后在 manual 模式下保持 idle，绝不自行扩下一批。」前半句是**已受理 Run 内**的
   续跑，计划 :341（B21 那一节）逐字把它划给了 B21：「每次**已受理 Run 内**的业务事件
   可驱动它自己的下一工作单元……不要把『Run 内续跑』实现成『Run 完成后无限扩市场』」。
   → e 接后半句；前半句由 B21 接，**本片不伪造它**（§7.3 不扩范围）。这条边界要写进
   e 的提交信息，否则「条件 3 关了」会被读成两半都关了。
4. **条款 4 是 e 的全部**：「bounded_auto 在达到对象数/累计预算/截止时间任一 stop 后
   不创建下一 Run；重放 trigger 不重复创建。」

### 设计要点：窗口的载体（这一处的选择有理由）

「授权窗口」是一组**累计**阈值（对象数 / 累计预算 / 截止时间），横跨这条授权链下的
**所有轮**。三个候选载体：

* **`RunSpec` 上的一格，窗口的标识是它自己的内容摘要。** 同一窗口下的每一轮都带
  **同一份**窗口值，「这个窗口下已经跑了哪些轮」由「窗口摘要相同的那些 Run」回答。
* 独立实体（新表 `authorization_window`）。多一张表、多一个 store 方法、多一处事务。
* 一条 `continuation_of` 链。**链会断** —— 中间一轮的记录一丢，前后就分成两个窗口，
  而报告上看不出断过。

**选第一个**，两个理由：它与本仓已有的 `policy_version`（内容摘要）同一条做法；
它不依赖任何一条可能断掉的引用。代价是要给 `RunStore` 加**一个**查询
（「按窗口摘要列出已经结束的各轮」，`get(run_id)` 答不了这个）。那一个查询是这一片
**唯一**必须新增的 store 面，且它的形状是「读」（不写、不事务）。

### 拆法（三片，每片一个提交）

| 子片 | 内容 | 关掉哪条 |
|---|---|---|
| e1 | 域层 `AuthorizationWindow`（三阈值）+ `RunSpec` 加窗口一格 + `admission_refusal` 改（`manual` 不许带窗口 / `bounded_auto` 必须带 / `watch` 仍拒）+ 两条既有测试改判据 | 条款 4 的前提 |
| e2 | 累计的读法：`RunStore` 加那个查询 + 决策函数（「达到任一 stop 即不创建下一轮」）+ 「重放 trigger 不重复创建」的幂等判据 | 4 |
| e3 | 「manual 完成后绝不自行扩下一批」的**面**判据（不是行为判据） | 3 的后半句 |

e1 与 e2 之间有一条**顺序**上的硬约束：e1 把 `bounded_auto` 的受理打开之后、e2 把
「不创建下一轮」的判据装上之前，中间那一个提交里 `bounded_auto` 是「能受理而无人
累计」的。**这一条在动手 e1 之前就要定下来**，否则中间那个提交会呈现成「自动授权
已经能用」。三个候选处置：① 窗口必须带 `not_after`，而 e1 就把它与本地时钟比一次
（「窗口今天还没到期」是受理时判得出的）；② e2 与 e1 合成一个提交（放弃「一个提交
一件事」）；③ e1 只在域层造 `AuthorizationWindow` 与校验，受理面**仍拒**
`bounded_auto`（把「打开受理」放进 e2）。

**选③**：它让 e1 成为一个纯域层的提交（新增一个类型 + 受理面的判据改成「窗口还
没有读者」的措辞），而把「打开」与「装上限」放进同一个提交 e2。这样两个提交之间
不存在「看起来能用」的状态 —— 那正是这条约束要防的。

---

## 落地记录 · e1（授权窗口那一格：纯域层的形状）

`AuthorizationWindow` + `RunSpec` 第十一格 + 跨格一致性 + 摘要纳入。**受理面仍然拒
`bounded_auto`**（方案③），所以两个提交之间不存在「自动授权已经能用」的状态。

### 与上面那张拆法表的一处**偏离**（要留着，不要抹平）

拆法表里 e1 那一行写的是「域层 …… + `bounded_auto` 必须带 / `watch` 仍拒」。落地时
**「`bounded_auto` 必须带窗口」这条没有进域层，也没有进受理面** —— 它不是被丢掉，是
被**搬到了唯一那个已经会说这句话的地方**。

触发这件事的是一次真实的红：`tests/test_mcp_runs.py:420` 那条用例（服务面递一份
`bounded_auto` 进去）在域层校验器加上之后，报出来的话变成了

> 这一份请求拼不成一轮 Run：(整体): Value error, execution_mode 是 'bounded_auto'，
> 而这一份规格没有授权窗口。

而 `ports/runs.py::admission_refusal` 里那句「它的授权窗口**有形状了**……但还没有一处
读过它」—— **一个字都没到任何人眼前**。原因很直白：`submit_run` 那一侧恒传
`window=None`（本部署没有窗口的生产者），于是**每一次**自动模式的提交都先撞在域层
校验器上，而 `admission_refusal` 在 `_spec_of` **之后**。

于是同一件事有了两处说法，先触发的那一处就是全部：

| | 域层那句 | 端口那句 |
|---|---|---|
| 说的是什么 | 「这一份规格拼不出来」 | 「这个模式今天受理不了」 |
| 处置 | 改参数 | 等切片 e2 造出读者 |
| 服务面读到的是 | **这一句** | 到不了 |

这正是 `ports/runs.py` 模块 docstring 一直在防的那件事（「同一句话的第二份实现 ——
它漂移的那天，正是『两个实现都通过』这句话开始没有依据的那天」），只不过这次漂移的
第二份不在某个 adapter 里，在域层。

**处置：那条判据只留在 `admission_refusal`，且它不写 `spec.window is None`。** 今天
没有窗口的生产者 ⇒ 两种输入（带窗口 / 不带窗口）在受理面是**同一个答案**，而那正是
「不许自行扩下一批」在 e1 这一层的形态。域层留判的是它**判得了**的四条内部一致性
（谁可以带窗口、截止超窗口、信封缺科目、信封超窗口）—— 那四条不依赖部署，也不读时钟。

代价：`RunSpec` 收得下一份没有窗口的 `bounded_auto`。盘上造不出来（落一行的唯一入口
是 `RunStore.admit`，它先问 `admission_refusal`），而 `tests/test_run_window.py` 里
专门有一条**正面**判据钉住这个边界 —— 哪天有人把那条校验器加回域层（理由会很正当：
一份自动规格该有边界），它会红。这由变异 E11 实测过。

### 判据表（14 条 / 18 个用例，全在新文件 `tests/test_run_window.py`）

| # | 判据 | 钉住的那一句 |
|---|---|---|
| 1 | `max_objects` 为 0 / 负数拒 | 「一个对象都不许走的窗口**就是没有授权**」（0 在 `RunBudgetLine.ceiling` 上合法，两句不是一句话） |
| 2 | 窗口的 `budget` 为空拒 | 「没设过」与「还没到」必须分得开 |
| 3 | 窗口里同一科目出现两次拒 | 两条上限谁说了算没有答案 |
| 4 | `not_after` naive 拒 | naive 时刻不得进入本系统（字符串序 ≠ 瞬间序） |
| 5 | 预算线换序 ⇒ 同一个 `digest` | 摘要对**写法**不敏感，否则累计在两条并行链上各算一半 |
| 6 | 三个阈值各改一次 ⇒ `digest` 都变 | §B17 条件 4 的「**任一** stop」有三个可求值项 |
| 7 | `manual` / `watch` 带窗口拒 | 两者带窗口的处置不同（一个「有人提交了这一次」，一个「来源是到期或信号」） |
| 8 | 本轮截止晚于窗口截止拒 | 那一轮会在授权过期之后还在跑，成为唯一没人管得住的后继者 |
| 9 | 本轮信封有窗口没有的科目拒 | 那条线在链上没有上限（处置：补窗口 = 授权范围） |
| 10 | 本轮信封单条高于窗口拒 | 这一轮自己就能把整条链花光（处置：调小信封 = 参数） |
| 11 | 窗口之内的一份自动规格**收得下** | 正面的那一半 —— 缺了它，上面四条在「没人填得对」时也绿 |
| 12 | `bounded_auto` + `window=None` **收得下** | 域层那条边界的位置（见上一节和变异 E11） |
| 13 | 换窗口 ⇒ `spec_fingerprint` 变；同窗口 ⇒ 不变 | 窗口不进散列则冲突检查恒不触发 |
| 14 | 预算线换序 ⇒ `spec_fingerprint` **不变** | 进散列的必须是 `AuthorizationWindow.digest` 而不是逐字 `repr` |

判据 14 是**刻意**加的：只判 13 的话，一个把 `repr(window)` 塞进散列的实现是绿的 ——
而它让「同一个键的重发」被读成冲突（报告上只显示「同一份键配了另一份规格」，而两份
规格说的是同一件事）。

### 变异控制（14 次，全部判事，还原逐字节一致）

脚本：`MUTATION-E1.py`。与 m1/m2/m3 的分布不同，值得写下来：那三片的变异几乎全在
服务面，**e1 全在域层**（`domain/run.py`），只有 E12 在端口。这正是这一片的形状。

| 变异 | 红 |
|---|---|
| E1 `value < 1` → `value < 0`（放行 0） | 1 |
| E2 窗口预算线可以空 | 1 |
| E3 窗口里重复科目放行 | 1 |
| E4 `not_after` 不再过 `to_utc` | 1 |
| E5 摘要不排序（对写法敏感） | 1 |
| E6 摘要漏掉对象数那一项 | 1 |
| E7 `manual` / `watch` 可以带窗口 | 2 |
| E8 本轮截止可以超窗口 | 1 |
| E9 本轮信封可以有窗口没有的科目 | 1 |
| E10 本轮信封单条可以超窗口 | 1 |
| **E11 域层把「`bounded_auto` 必须带窗口」加回来** | **4** |
| **E12 端口那句漂成 `and spec.window is None`** | **1** |
| E13 窗口不进 content 散列 | 1 |
| E14 散列进逐字 `repr` 而不是内容摘要 | 1 |

E11 与 E12 是这一片的重心，两条都在「同一句话有几处出处」这件事上：

* **E11** 红了 4 条，分散在**三个文件**（`test_run_window.py` 的正面判据、
  `test_run_contract.py` 的两个参数化、`test_mcp_runs.py` 的服务面）—— 这就是「域层
  先炸，端口那句到不了」的可执行证据。
* **E12** 只红 1 条，但红得极准：`test_the_two_implementations_refuse_in_the_same_words`
  里第 **4** 份规格（带窗口的 `bounded_auto`）被**静默受理**了。盘上会多出那样一轮 ——
  它什么都齐全（有窗口、有预算、有截止），只是没有任何一处问过它累计走到哪了。这是
  这一片不存在时会漏过去的那个形态。

### 实测

* 全量：**`6084 passed, 4 skipped`**（m3 后是 6066，增量 **+18**，即新文件的 18 个用例；
  MCP 那条用例是**改名**不是新增，计数不变）。
* `ruff check .` **All checks passed!**
* 定向（`test_run_window` / `test_run_contract` / `test_run_admission` / `test_mcp_runs` /
  `test_run_delta` / `test_run_ending_contract` / `test_run_increment` /
  `test_run_reconciliation`）：**280 passed**。

### 本片关掉了什么

**没有关掉任何一条 §B17 关闭条件。** 逐条说清，否则「e1 落了」会被读成条件 4 落了：

* 条件 4（「达到任一 stop 后不创建下一 Run；重放 trigger 不重复创建」）要的是**一个
  会读窗口的东西**，而读者在 e2。e1 只造了被读的那一格，并让受理面**继续拒绝**自动
  模式 —— 所以今天仍然**没有**任何一条自动 Run 能被受理，条件 4 的字面要求（一条
  `bounded_auto` 真的因为达到阈值而没开下一轮）在盘上**做不出来**。
* 条件 3 的后半句是 e3，前半句（已受理 Run 内的续跑）是 B21 的领地，本片不碰。
* §503 那句「只有类名/字段/接口的无消费者实现不算完成」**本片是它的反面样本**：
  `AuthorizationWindow` 今天确实还没有消费者。它不算完成，**e2 才算** —— 这一片在
  计划里的位置是「前提」，不是「交付物」。e2 的提交信息要引用这一行。

### 本片新增的遗留（不要丢）

1. **`AuthorizationWindow` 零消费者**（如上）。这是刻意的顺序，不是漏做；e2 落之前
   引用 §503 时要说清这一点。
2. **`admission_refusal` 的第一条不再提「生产者」这个词**，只提「读者」。e2 打开受理
   时，那一条要拆成两句：没窗口 ⇒ 「没有写窗口的生产者」；有窗口而累计到了 ⇒ 「达到
   stop」。**拆的时候要保留「两种输入今天同一个答案」这条纪律的历史**，否则 E12 那个
   形态会在 e2 之后重新出现（那时它更贵：盘上真的会多出一轮自动 Run）。
3. **`_window` / `WINDOW_UNTIL` 在 `tests/test_run_contract.py` 里只被两份规格用到**
   （`test_the_two_implementations_refuse_in_the_same_words` 的第 4 份，与
   `test_only_a_bounded_auto_specification_may_carry_a_window` 那一族在另一个文件）。
   `_spec` **不**替谁补窗口 —— 这个决定与域层那条边界是一件事的两面，改一处要想到另一处。
4. **`marketops/adapters/mcp_server/runs.py` 与 `adapters/sqlite/db.py` 的「十格」措辞
   已在 e1 一并改成「十一格」**（`db.py` 那处还补了「两格是嵌套的预算元组」）。它们是
   散文，没有执行器；下次再加一格时，`command grep -rn 十一格` 是唯一的检索面。

---

## 落地设计 · e2（累计的读法 + 决策面）—— 动手之前

权威：§B17 条款 4（`:259` 逐字）+ `:247`（`bounded_auto` 那一句）+ `:249`
（「mode 不是权限捷径」）+ `:278`（B21：以后新 Run 只有 `bounded_auto`/`watch` 授权才可
创建）。拆法表 e2 那一行：「累计的读法：`RunStore` 加那个查询 + 决策函数（「达到任一
stop 即不创建下一轮」）+「重放 trigger 不重复创建」的幂等判据」；上面那张载体表已经
定下**唯一**要新增的 store 面是「按窗口摘要读已经结束的各轮」。

### 一、侦察出来的四件事，逐条改变设计

**1. `RunDelta.usage` 记的是预留 id，不是金额，而且它今天没有生产者。**
`domain/run_delta.py:244-247` 逐字：「:attr:`usage` **不记金额**，只记**哪几笔**：金额
只有一个权威（``ports/budget.py`` 的那一处 ``SUM``）……要钱数就拿着这几个
``reservation_id`` 去账本读。」所以「累计预算」这一条要读的是**账本**，不是 Delta。
而 `RunDelta(` 在 `marketops/` 下**只出现在它自己的类定义处** —— 没有任何生产代码构造过
它（`close_run` / `reconcile_run_delta` 也是零生产调用点，遗留 ⑦）。
⇒ **「累计预算」今天在任何一条真实链上都只能读到 0**，因为把预留写进 Delta 的那一环
还没有。这一条**不能假装关掉**，见第五节。

**2. 预算的 scope 既不是 workspace 也不是 Run。** `application/budget.py:17-30` 逐字：
研究侧 scope 是 `<profile>:<account_id>`，发现侧是 `<profile>:<发现范围名>`，维护侧是
`<profile>:<主体 id>@<时刻>`。⇒ **窗口的累计预算不可能用一次 `ledger.usage(scope)`
读出来**，只能拿着链上各轮 Delta 里那几个 `reservation_id` 逐笔去 `ledger.get(rid)` 求和。
这正是 `RunDelta.usage` 那句「拿着这几个 id 去账本读」的**同一件用法**，只是主语从「一轮」
换成了「一条链」。

**3. `research_run` 上没有窗口列，而索引已经在。** DDL（`adapters/sqlite/db.py:814-834`）
把 `spec` 整份存 payload，并**已经**建了 `research_run_by_workspace (workspace, created_at,
run_id)`，注释逐字说它「是唯一被真实问过的筛选」。⇒ 不需要加列、不需要迁移
（`migrate.py` 有整表重建的迁移机制，但那是给改 CHECK 用的，这里用不上）：按
`workspace` 取候选行，再在**两份实现共用的那一份折叠**里按 `spec.window.digest` 过滤。
加列会把「窗口是什么形状」摊进 DDL，而它已经在 `spec` 里逐字节存着。

**4. `ports/runs.py` 明令**没有** `list_runs`**（`:16-21`），理由是条款 3 那半句
「绝不自行扩下一批」——「一个只被测试用到的列举入口，会让人以为有一个 Run 队列在那里
等着被推进」。⇒ e2 新增的那一个读**必须长得不像队列**：它不返回 run_id、没有次序、
没有「下一轮是谁」，只返回「这条链已经走了多远」。所以它是：

```python
@dataclass(frozen=True)
class ChainProgress:          # ports/runs.py
    window_digest: str
    runs: int                 # 这条链上**已经结束**的轮数
    objects: int              # 这些轮碰过的业务键，**去重后**的个数
    reservations: tuple[str, ...]   # 这些轮记下的预留 id，去重后按字典序
    open_runs: int            # 这条链上**还没有结束**的轮数
```

与一个**共用**的折叠（防两个实现漂移，与 `admission_refusal` 同一条分工）：

```python
def progress_of(window_digest: str, runs: Iterable[Run]) -> ChainProgress
```

两个实现各自提供 `chain_progress(*, workspace, window_digest) -> ChainProgress`，**都只
做「取候选行」**，折叠调上面那一个。判据照 `test_the_two_implementations_refuse_in_the_
same_words` 的先例：同一批输入，两个实现给出**逐字段相同**的读数。

### 二、「对象数」的口径：**去重后的业务键**

`max_objects` 的 docstring 逐字是「这条链累计最多走多少个对象」。一个**对象**是一个
身份（`PoolEffect.key` = `application/pool.py::venue_handle_for_row` 的产物），所以同
一个键在链上被走过三次仍是**一个**对象。理由是这一格在回答**覆盖广度**（「这条链已经把
多少对象走过了」），而重复走同一批对象是**成本**问题，那一条由窗口的 `budget` 管 ——
两条阈值各有各的主语，重合的那一天它们会互相掩盖。

代价写清楚：一条链重走同一批对象 100 遍，`max_objects` 永远不会触发。这不是本片引入的
漏洞，是「对象数」这个词本来的意思；处置是那条链的 `budget` 该说话。

### 三、决策面：一个函数、两个调用点，不是两处判据

三个阈值里有两个（对象数、截止）**能从这条链自己的记录里读出来**，第三个（累计预算）
要读另一个端口。于是判据的输入分两半，但**裁定只有一处**：

```python
def window_refusal(                      # ports/runs.py
    spec: RunSpec, *, at: datetime,
    progress: ChainProgress,
    spent: Mapping[BudgetLine, int] | None,
) -> str
```

> **实测更正（见末尾「落地记录 · e2」）：这个签名里的 `at` 落地时删掉了。**
> `at > window.not_after` 那一条在受理面上**不可达** —— 域层的
> `RunSpec._window_agrees_with_the_rest` 与 `admission_refusal` 的 `cutoff <= at`
> 合起来已经蕴含 `at < not_after`（任何构造得出来的规格都如此）。下面第 3 条理由
> 因此**不存在**；三条 stop 里只有两条在这一层。留改后的签名在 e1 那一节末尾的
> 遗留里点名过的那句「要拆成两句」也一并兑现了。

四条理由（各自 raise 一句话，因为四条处置不同）：

* `progress.open_runs` ⇒ 「这条链上还有一轮在跑」。依据是 `:247` 的**「创建后续 Run」**
  与 `:239` 的「一轮一轮长大」：**后续**逐字是「上一轮之后」，不是「同时」。
* `progress.objects >= window.max_objects` ⇒ 达到对象数 stop。
* `at > window.not_after` ⇒ 达到截止 stop（**严格大于**：`not_after` 的 docstring 逐字
  写的是「最迟到什么时候（**含**）」）。
* 逐科目 `spent[line] >= ceiling` ⇒ 达到累计预算 stop。
* `spent is None` 且这是一条窗口化的自动受理 ⇒ **拒**，理由「累计预算这一条读的是账本，
  而 Run 记录读不到它」。**这一条是承重的**：少了它，「没有账本读数」与「账本说没花」
  在受理面上同形，而两者处置相反（一个去接账本，一个可以继续）。

`admission_refusal` 的第一条因此**拆成两句**（这正是 e1 遗留第 2 条点名的那件事）：

| 输入 | 答案 | 出处 |
|---|---|---|
| `bounded_auto`，**没有**窗口 | 拒：「没有写窗口的生产者」 | `admission_refusal`（不变的那句的一半） |
| `bounded_auto`，有窗口，且 `window_refusal` 非空 | 拒：那一条 stop | `window_refusal` |
| `bounded_auto`，有窗口，`window_refusal` 为空 | **受理** | —— |
| `watch` | 仍拒（f 片造它） | `admission_refusal` |

**「带窗口与不带窗口今天同一个答案」那段历史要留在原地**（e1 遗留第 2 条逐字）：今天
它的形态变成「**没有窗口的自动模式仍然拒**，而那句拒词改说『没有写窗口的生产者』」。它
在盘上仍然成立 —— `submit_run` 那一侧恒传 `window=None`，直到 e2c 把面打开。

### 四、「重放 trigger 不重复创建」怎么判

两件事，**分开判**，因为它们挡的是两种不同的失败：

1. **同一轮的重放**：同一个 `(workspace, key)` + 同一份内容 ⇒ `admit` 拿回**同一行**。
   今天由主键保证（`run_id(spec)`），e2 要做的是给**窗口化的自动模式**补一条判据（原来
   那条只跑在 manual 规格上）。
2. **达到 stop 之后的重放**：同一个键第二次提交时，链的状态可能已经变了（上一轮跑完、
   累计到了）。此时该回答的是**冲突还是那条 stop**？答案是**先问 stop**：`admit` 在
   查行**之前**先判 `window_refusal` —— 与 `stop` 那条「先问『是不是已经写过的那一套』，
   再问『这一套能不能成立』」的顺序**相反**，理由是这两个问题的处置不同：重放要的是
   同一行，而达到 stop 要的是「别再开新的」。一行已经在盘上时，第二次提交本来就是重放，
   而重放**不该**因为链后来走到了 stop 就变成一次拒绝 —— 否则「重发同一个键」的结果
   随链的状态而变，而它承诺的是「拿回同一行」。

⇒ 顺序定成：**先 `get(run_id)` 判重放（在写事务里，与今天一致），命中就返回那一行；
否则才 `window_refusal`。** 这一条要有一条判据钉住（「链到 stop 之后重发旧键仍拿回原行」
以及「链到 stop 之后换一个新键被拒」）。

### 五、e2 关掉什么、关不掉什么（**先写在这里，落地后照抄进提交信息**）

* 条件 4 三项里，**「对象数」与「截止」end-to-end 可触发**：生产者是盘上真实的 Run 行与
  `RunDelta`（`close_run` 落盘的那一份），存储是 `research_run`，调用者是 `submit_run`
  → `admit_run` → `admit`，MCP 读取/控制是那条拒绝词与 `get_run`，故障回执是
  `window_refusal` 的四种理由，关闭测试是一组真的开两轮的用例。
* **「累计预算」这一项 e2 不关闭**：读法与拒绝面都造出来了，但**驱动它的那个生产者不
  存在**（第一节 1）。所以只要 `RunDelta.usage` 还是空的，任何一条真实链上它都读到 0。
  **条件 4 因此是 PARTIAL**，不许写成关闭。这一条要引 §503，并接到遗留 ⑦⑧。
* 条件 3 的后半句仍是 e3。

### 六、e2 的两个提交（原计划三个，e2a 并入 e2b —— 实测之后改的）

| 提交 | 内容 | 类别 |
|---|---|---|
| e2b | `ChainProgress` + `progress_of` + 两个实现的 `chain_progress` + `window_refusal` + `admission_refusal` 拆句 + `admit` 的接线与顺序 + `admit_run` 的账本读数 + 两个文件的判据 | 行为变更（「打开」与「装上限」是同一件事的两半） |
| e2c | MCP `submit_run` 接窗口 + 拒绝面逐字 + 重放判据 | §6 的「实际调用者 / MCP 读取控制」 |

**e2a 为什么不单独一个提交了。** 原计划里 e2a 是纯前提（`ChainProgress` + 两个实现的
`chain_progress` + 契约判据），并引 e1 的先例说「本片是前提不是交付物」。落地时发现这个
类比不成立：e1 的前提（`AuthorizationWindow` 的形状）**当时没有消费者是事实**，而
`chain_progress` 与 `window_refusal` 落进同一个 `RunStore.admit` 调用点 —— 拆开之后，
e2a 那一半在任何一次受理里都不会被走到，它的判据只能自己构造 `progress_of` 的输入。
一块**可以独立提交、但提交完对盘上任何一条真实链都没有影响**的代码，按 §503 逐字
（「只有类名/字段/接口的无消费者实现不算完成」）就是那一句点名的东西，只不过这次是我
自己造的。合在一起之后，「打开」与「装上限」在同一个提交里互为判据：只判前者的实现在
「永远受理」时绿，只判后者的在「永远拒」时绿，而两者都不是 e2。

## 落地记录 · e2b（受理面打开 + 两条 stop）—— 2026-09-28

提交：`e2b`（一个）。改动 7 个文件 + 1 个新文件；`tests/test_run_window_admission.py`（新，
33 用例）、`tests/test_run_admission.py`（+9）。落地时把第六节那张表里的 e2a 并了进来，
理由写在那张表下面。

### 一、与设计稿的三处不同（都写进了提交信息，这里记「为什么」）

**1. `at` 参数删掉了，`at > window.not_after` 那条分支没了。**
设计稿第三节列了四条理由，第三条是「达到截止 stop」。实测的蕴含：

    RunSpec._window_agrees_with_the_rest   拒绝  not_after < cutoff
    admission_refusal                      拒绝  cutoff   <= at
    ─────────────────────────────────────────────────────────
    合起来                                        at < not_after

也就是「任何一份**走得通受理面**的规格，窗口都还没到期」。构造即拒（报文见提交信息）。
所以那条分支是**永远不触发**的第二份实现 —— 本模块 docstring 第一条禁令要防的东西，
只不过这一份连红都不会红。它的判据换成了一条**蕴含**
（`test_an_admitted_auto_run_always_starts_before_its_window_ends`），判三件事：受理得到
的那一轮 `at < not_after`；`cutoff` 超出窗口的规格**构造都构造不出来**；`cutoff` 不在
将来的规格由 `admission_refusal` 拒、且**拒词里没有「授权窗口」**（说明窗口那一层根本没
被问到）。

**2. 次序反了：重放检查在窗口判定之前**（见提交信息第 2 条）。

**3. `progress_of` 的签名是 `(workspace, window_digest, runs)` 而不是 `(window_digest, runs)`。**
设计稿第四节写的是两参数。落地时发现工作区筛选**只能在这一层判**：两个 store 都
`bind_workspace` 到一个工作区（`bind_workspace` 拒绝第二个），所以「另一个工作区的行
算不算」在一棵正常的树上**造不出来**，只能拿一批构造出来的 `Run` 去问这个纯函数 ——
而那就要求它自己收 `workspace`。判据是 `test_only_the_named_workspace_is_counted`。

### 二、变异控制抓出来的一条**空判**

`M13 · 退款过的那一笔也算进累计` **活下来了**（`exit=0`，52 条全绿）。原因不是实现错了，
是**判据判不出话**：那一条原本只把**已结算**那笔的 id 写进 `delta.usage`，退款那笔留在账本
里没上链 —— 而 `_spent_of` 只看得见链上记着的那几笔，于是「退款算不算」这一问在盘上根本
没有输入。修法：两个 id 都上链，窗口上限正好卡在两者之和（15 + 5 = 20）上，于是这一问
**当场分得开**。这是本片唯一一处「变异控制改变了代码之外的产物」。

### 三、一处**没有执行器**的说法（不记成已关闭）

`window_refusal` 「必须在写事务里调用」这句话今天没有判据：单线程下放在事务外行为一样。
要判它需要一条并发受理的用例 —— 那是切片 h 的范围。因此变异控制里**故意没有**为它设一条：
必然活下来的变异不是证据，写进清单只会把「17/17」稀释成一个不好读的数。

### 四、条件 4 的状态：**PARTIAL**

| 阈值 | 生产者 | 存储/事务 | 实际调用者 | 判据 | 状态 |
|---|---|---|---|---|---|
| 对象数 | 盘上的 Run 行 + `RunDelta.effects` | `research_run` / 写事务内 | `admit_run` → `RunStore.admit` | M3 M8 M9 M10 | 关闭 |
| 截止时间 | `RunSpec` 构造 + 域层跨格校验器 | —— | —— | 蕴含那一条 | 关闭（**不在本层**） |
| 累计预算 | **不存在**（`RunDelta.usage` 零生产者，遗留 ⑦⑧） | 账本读得出、链上没数 | `admit_run` → `admit` | M5 M12 M13 M14 M15 M16 | **PARTIAL** |
| 重放不重复创建 | 主键 `run_id(spec)` | 写事务内、查行**之前** | 同上 | M11a M11b | 关闭 |

「累计预算」这一格 e2 造的是**读法**（`_spent_of` + `window_refusal` 那一支）与**拒绝面**，
而不是那一格的数据。按 §503 逐字，这不算关闭。

## 侦察 · §B17 其余六条关闭条件的现状（2026-09-28，只读核过一遍）

这一节是为 `docs/B17_ACCEPTANCE_MATRIX_<日期>.md` 备料，也为 e3 之后还欠什么画一张图。
**全部是事实与缺口，不是处置建议**；行号按本节写作时的树。六条共用的地基先说一次：

* **生产代码里没有任何一处构造 `RunDelta` 或 `DeltaCell`** —— 九个格子只在 `tests/` 里被构造。
  `domain/run_delta.py` 那一族有形状、有校验器、有对账器，**零生产者**。
* **已注册的 Run 工具只有三个**：`security/catalog.py:708 submit_run` / `:721 get_run` /
  `:732 get_run_increment`，都是 `_CONTROL`。**没有结束面**（`test_mcp_runs.py:960` 的注释
  自己承认「``close_run`` 这条工具还没接线」）。
* **被受理的 Run 没有执行路径**：生产里唯一构造 `SqliteRunStore` 的地方是
  `interfaces/composition.py:463 default_run_store`，而它**唯一的调用点**是
  `interfaces/mcp/__main__.py:701`（交给 `run_handlers`）。dispatcher / continuation /
  worker 一行都不碰它 —— `RunStore` 这个**名字**在十个文件里出现，但那些是类型注解与
  异常那一族的引用，不是「有东西在推进一轮」。`InMemoryRunStore` 只在测试里被构造。
* `close_run`（`application/run_reconciliation.py:324`）在**任何地方都没有调用者**（只有
  `tests/test_run_reconciliation.py:507` 直接调它）；`reconcile_run_delta`（`:272`）唯一的
  调用者是 `close_run` 自己（`:357`），所以它随 `close_run` 一起是死的。
  `plan_increment`（`application/run_increment.py:295`）的唯一生产调用者在
  `get_run_increment` 这个**读**工具内部（`adapters/mcp_server/runs.py:367`）。`application/` 下没有
  `run_stop.py`（有 `discovery_run.py` / `run_admission.py` / `run_increment.py` /
  `run_reconciliation.py` 四个）。

| 条件 | 有什么 | 缺什么（按「生产者 / 存储 / 调用者 / MCP / 回执 / 判据」逐项） |
|---|---|---|
| 1 · 连续两个 manual Run | 四格增量判据（`IncrementVerdict` / `IncrementCell` / partition 校验器）齐全，幂等有行级判据 | **没有"连续两轮"的端到端判据**（单轮：先摆账本再算一次）；**"第一轮"在结构上没有对应物**（`Observation` 无 `run_id`、`RunDelta` 不带轮次来源），所以"复用第一轮的沉淀"实际是"复用整个工作区账本此刻的沉淀"；无发起第二轮的调用者 |
| 2 · 结束时 Delta 完整、双向对账 | 形状 + `reconcile_run_delta` + `_the_books_are_flat` + `ending_refusal` / `stopping_outcome` + 两个实现的 `stop`（写事务内读-判-写）+ 一整套 application/adapter 判据 | **零生产者**（没有路径能让一个 Run 结束）；**没有 MCP 结束面**；`close_run` 零调用者；判据**没有一条走 MCP 或端到端** |
| 3 · 客户端断开后仍完成、完成后 idle | **后半句**是结构性的：端口**没有** `list_runs`（`ports/runs.py:23/64` 逐字写了理由）、没有自动创建 Run 的路径、`recommendations` 是散文（`test_run_delta.py:296`） | **前半句既无执行路径也无判据**：受理之后没有任何东西推进它，"继续并完成"没有可观测对象（真正证明这句话的机制在 B21 的 JobStore 续跑行上，是**另一个对象**）；**没有任何以 B17 Run 为对象**的"完成后不再开下一轮"判据 —— 这正是 e3 |
| 5 · watch 只为到期/信号创建维护 Run；SavedView 不创建 Run | 维护账（`what_is_due` / `aspect_states`）与 `AuthorizationWindow` 都在；WATCH 在受理层被拒 | 两个主体都没有：watch **结构性拒绝**（连受理都过不去），`SavedView` **全仓零命中**（只在文档里）。"读不写"的测试是整仓纪律的旁证，不是本条件的判据 |
| 6 · 无可证明总量时报可观测池状态 | 池报告六问 + `_coverage_receipt`（要求/实现/缺口三计数）；纪律是"报计数带来源、不报比率"；`no_store` / `not_wired` / 截断被报 / `None ≠ 0` | **没有以 B17 为对象的判据**：现有绿灯钉的是池报告工具的纪律，条件 6 是**间接**满足的；也没有对"国家完成率/已爬完"的代码级禁止（因为没有这种产出物，连负例都无从写） |
| 7 · Delta 合并的并发/重放/CAS 负例；旧 session 不能用旧 snapshot 覆盖新事实 | 重放身份 `stopping_outcome` + 两个 `stop` 的写事务；相邻对象的 CAS 先例（review `expected_version`、continuation claim、quay） | **两个半句都没有对象**：没有"Delta 合并"这个操作（`application/merge.py` 只有 `later_of` / `ordered_union`，不是 Delta 合并），也没有 session 对象（`grep` 命中的是 db 的 `session()` 上下文管理器与 `work_item_snapshot` / `scene_snapshot` 表）—— 所以两件事都**没有可违反的对象，也就没有负例** |

### 侦察时顺带发现的一条**引用缺陷**（不在上述六条里）—— 已处置

`marketops/application/run_increment.py:8`、`:216`、`tests/test_mcp_product_loop.py:1121`、
`tests/test_run_increment.py:526`（后者还给了 `DESIGN-SLICES.md:35-45` 这样的**行号引用**）
都引 `DESIGN-SLICES.md`，而**这个文件在工作树里不存在** —— 它只在本目录（artifacts）里。
四处引用里有一处是**行号引用**，而 artifacts 那一份不在版本控制里、也没有冻结：它今天
指的是这一节，靠的是「这一节恰好在文件头部、而此后只在该文件末尾追加」，不是一条保证
（`:33` 那一行 a 片补记的第三条差量就是一次插入式补记）。处置不是「读不到就算了」，而是
**把这份设计记录冻结后发进 `docs/` 并把四处引用改指过去**；本节之后的「发布记录」就是
那次处置。同款先例：`application/validation.py:15/18` 对 `_shared.py` 的陈旧引用（遗留 ⑤）。

## 发布记录（2026-09-28，从 artifacts 冻结进 `docs/`）

**正文没有增删一句**，只做了一件事：把每一处**以现在时陈述「盘上现在有什么」**的行号
指针（11 处）按发布树重校一次。理由与验收矩阵那一条同源：指针错一位，读的人按图索骥、
找到一行**确实含那个符号**的行，于是确认了一遍本来就有的成见 —— 它比「这里没有证据」
更坏，因为它伪装成证据。冻结之后这一份不再长，所以这次的校准能一直成立。

### 一、按发布树重校的 11 处（每一处都逐行实测过）

| 本节行号 | 原文 | 改成 | 那一行**今天**实际是什么 |
|---|---|---|---|
| `:13` | `domain/job.py:127` | `domain/job.py:128` | `class ResearchJob`（`:127` 是它的 `@dataclass` 装饰器行） |
| `:23` | `domain/enums.py:423` | `domain/enums.py:449` | `class RunStatus`（`:423` 今天是 `class ExecutionMode` —— 同一份文件里的前一个枚举，是这一处最容易读岔的地方） |
| `:37` | `application/pipeline.py:136` | `application/pipeline.py:125` | `def _derived_run_id()`（`:136` 是它 `return` 的那一行） |
| `:319` | `review.py:89` | `adapters/mcp_server/review.py:89` | 五个 `review.py` 里唯一那一行 `from .tools import ... , _readable, ...` |
| `:685` | `tests/test_run_contract.py:143` | `tests/test_run_contract.py:185` | `@pytest.mark.parametrize("mode", sorted(_REFUSALS, ...))` |
| `:685` | `tests/test_run_admission.py:321` | `tests/test_run_admission.py:340` | `@pytest.mark.parametrize("mode", [ExecutionMode.BOUNDED_AUTO, ExecutionMode.WATCH])` |
| `:1109` | `:717` | `:721` | `"get_run"`（同组的 `submit_run` 仍是 `:708`） |
| `:1110` | `:728` | `:732` | `"get_run_increment"` |
| `:1110` | `tests/test_mcp_runs.py:1035` | `tests/test_mcp_runs.py:960` | 那句「``close_run`` 这条工具还没接线」的注释本身 |
| `:1114` | `interfaces/mcp/__main__.py:700` | `interfaces/mcp/__main__.py:701` | `runs = run_handlers(` —— `default_run_store` 的那一条调用 |
| `:1121` | `runs.py:290` | `adapters/mcp_server/runs.py:367` | `plan_increment` 在 `get_run_increment` 里的调用点 |

其中三处不止是「漂了」，是**写的时候就没对上**（`domain/enums.py:423`、`domain/job.py:127`、
`application/pipeline.py:136`），另两处是**歧义**：`runs.py` 在本仓是三个文件
（`ports` / `adapters/mcp_server` / `adapters/sqlite`），只写基名时按约定消解**不唯一** ——
这类指针即使编号对，读的人也分不出来；`review.py` 同理，是五个里的一个。

另有一处**自引用**顺带改准：`:300` 的「b 的落地记录（上面 `:253`）」—— 那句话逐字在
**d 的**落地记录里（`:257`），原引用既差四行又挂错了片。

### 二、没有重校的指针，以及为什么

**`## 落地记录 ·` 那几节里的行号，相对的是那一节落地时的那棵树**，不是发布树。那些数字
是当时的事实，改掉它们就是把日志改成事后通报；每一节都记着落地提交与当时的全量测试读数，
要重新定位就按**旁边写着的符号名**去找 —— 本文件给 `文件:行号` 的地方几乎都同时给了符号名。

已知的、**故意留作原样**的一处：`tests/test_mcp_runs.py:420`（e1 的落地记录里那次真实的红
所引的用例），它在发布树上是 `:433`。留着的理由是它叙述的是「那时红了什么」，而不是
「今天哪里有什么」—— 与前一处不同类。

### 三、量具：`B17-DESIGN-SLICES-CITATION-CHECK.py`（在 artifacts 的同一个 Item 下）

它把 `文件:行号` 全抽出来（含 `:717` 这种续引用），消解路径、查范围，并在引用旁边写了
符号名时数那个符号离引用有多远。**跑在正文上**（`:1-1133`，即本节之前）读出来：

    51 条引用（含续引用）· OK 17 · NEAR 0 · FAR 1 · 旁边没写符号名、机器核不动 33

**本节自身不参与这项测量。** 它逐条引的是「改之前长什么样」—— 上面那张重校表逐条写着
「原文是 `domain/job.py:127`」，那是**旧值**，量具照规矩会去核它，于是一律报 FAR；
`DESIGN-SLICES.md:35`（发布前的文件名）、`runs.py:290`（写的是基名）同理。**本节不是
那份设计记录，是关于它的记录**，两者的指针不同类。也正因为如此，**整份文件上的读数
不写在这里**：本节每改一次它就跟着变一次（那个数由本节自己的文字决定），写下来就立刻
是错的 —— 这与「任何未实测的数字不得先进产物」是同一条纪律的两面。

- 正文里那 **1 条 FAR 是假报**：`domain/scene.py:129 SceneVersion.workspace` 指的是
  `SceneVersion` 上的 `workspace` 那一格，它**本来就在第 129 行**（`:113` 是类定义）——
  量具按「最近的符号出现处」判，认不出「点分写法点的是成员」。这类假报是量具的边界，
  不是把引用改掉的理由：改掉它反而会让指针离开真正那一行。
- 正文里那 **33 条**旁边没写符号名，机器核不动，逐条人工读过（读回那一行的原文，与上下文的
  叙述比）。**这一遍是手核的，不是机核的** —— 记在这里是为了让下一个人知道它的强度
  与上一节那张表不同。
- 上一节那 11 处就是这一遍的产出。**它不证明剩下的都对**：它证明的是「凡机器核得动的
  都对上了，凡机器核不动的都被人看过一眼」。这两句不是一回事，不要读成一句。

### 四、发布后，那四处引用改指的地方

| 引用处 | 改成 |
|---|---|
| `marketops/application/run_increment.py:8` | `docs/B17_DESIGN_SLICES_2026-09-28.md` 的差量 1 |
| `marketops/application/run_increment.py:216` | 同上，差量 3 |
| `tests/test_mcp_product_loop.py:1121` | 同上，差量 3 |
| `tests/test_run_increment.py:526` | `docs/B17_DESIGN_SLICES_2026-09-28.md:35-45` |

`:35-45` 这一条是**行号引用**，所以冻结的前提之一就是它必须仍然对得上：差量 3 从第 35 行
到第 45 行，发布时逐字核过。**冻结之后它成了一条保证** —— 这一份不再长，插入式的补记
不会再发生，所以这次的校准能一直成立；而在冻结之前，它靠的是运气。
