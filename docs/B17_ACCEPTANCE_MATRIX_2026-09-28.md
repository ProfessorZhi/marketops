# 增量 Run 验收矩阵 · 逐行对账（2026-09-28）

这份文件把 `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B17 的 **7 条关闭条件**逐行落成
可核实的证据指针：每一行给出机制、生产者、消费者、存储/事务、正证据、负证据、标注、
状态，以及**缺口归谁**。

它**不改**计划文件，也不改 `docs/TARGET_ARCHITECTURE_GAP_MATRIX.md`。那两份各自记着
「要求什么」与「`convergence_start_sha` 上量到什么」—— 改前者等于把要求改成今天的
实现，改后者等于把当时的读数改写成事后追认。

**路径约定**：本文的路径都相对**仓库根**；代码在 `marketops/` 下，所以
`ports/runs.py:273` 指的是 `marketops/ports/runs.py:273`。每个文件**首次**出现时给全
路径，之后按这个约定简写。字面上有歧义的简写（例如光写 `runs.py`，仓库里有两个同名的
`runs.py`）一律不用 —— 全文的指针过了一遍机器扫描（见末尾「这份矩阵的指针是怎么核的」）。

## 状态词表

| 状态 | 含义 |
|---|---|
| `DEMONSTRATED` | 有生产者、有消费者，正负证据都在本仓测试里，且**读得回** |
| `PARTIAL` | 某几格有判据，另几格没有；缺口逐条写在该行的「缺口」栏里 |
| `NOT_BUILT` | 全仓没有实现（不是「没测」——是「没有那个东西」） |
| `OUT_OF_SCOPE_HERE` | 属于别的切片（B15/B24…），本矩阵只登记归属 |

**这份矩阵不宣称任何 live 结果。** 下面每一条证据都是 fixture 或纯函数判据，零网络
调用，零 provider 调用。§18 的 provider 版本与现场验证不在这份文件里。

**不给完成百分比。** 7 行里各状态的**行数**不是进度 —— 每一行的判词各自带着自己的
证据，而一行的状态会因为一条判据落地而变，不会因为别的行变了而变。

## 0. 时点与基线读数

| 格 | 值 |
|---|---|
| 计划权威 | `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B17（`### B17` 在 `:239`；目标与 `ExecutionMode` 三成员在 `:241-253`；**7 条关闭条件逐字在 `:256-262`**） |
| 本文件读数时点 | `dc8d0d9`（B17-e2c），分支 `marketops/target-architecture-convergence-20260927` |
| 本文件读的数 | 工作树 `c26f2ceb56f75d0ad403146c14dbe5b439f3a82c`（`git write-tree`），与 `dc8d0d9` 的提交树逐字相同 |
| 全量套件 | `6146 passed, 4 skipped`（`392.30s`），跑在上面那一棵树上 |
| `ruff check .` | 干净（未跑 `ruff format`）；`python -m marketops.schema` 重生成后工作树无 diff |
| MCP 工具目录 | **58** 条（`marketops/security/catalog.py` 的 `TOOL_CATALOG`）；与 Run 有关的只有三条 —— 这一格在 B17-e4 之后是 **59** 条 / 四条，见文末「补记 B」 |
| B17 落地序列 | `b82f0cf`(a1 Run 形状) → `10387d4`(a2 RunStore 契约) → `c2bf1df`(a3 受理面) → `208b426`(b1 RunDelta 与一次结束) → `80c5c8b`(c1 双向对账) → `000ebbf`(d1 增量) → `691700b`(m1 MCP 受理面) → `7c952fe`(m2 `get_run`) → `9d1c324`(m3 `get_run_increment`) → `61e05a9`(e1 授权窗口的形状) → `5ebc789`(e2b 受理面打开) → `dc8d0d9`(e2c 窗口接上服务面) |
| 七行判词 | 1 `PARTIAL` · 2 `PARTIAL` · 3 `PARTIAL` · 4 `PARTIAL` · 5 `NOT_BUILT` · 6 `PARTIAL` · 7 `PARTIAL` |
| 缺口去向 | 条件 1/2/3 的缺口指回**同一处空宿主**：一个 Run 的产出（`RunDelta`）今天在 `marketops/` 下**没有任何生产者**，因此「结束一轮 Run」在服务面上无处发生；条件 4 的缺口（`RunDelta.usage` 无生产者）与条件 7 的（`RunDelta` 没有合并、Run 链没有 CAS）也落在那里；条件 5 的主语（Watch / SavedView / ActionItem）归 **B24** 与切片 **f** |

那 7 行里 `PARTIAL` 有 6 行、`NOT_BUILT` 1 行 —— 这是**行数**，不是进度。写在这里
是因为这 7 行的缺口有一个共同形状：**B17 造出的是「一轮 Run 从受理到读回的整条链」，
而「一轮 Run 怎么产出、怎么结束」那一半今天还空着**。

---

## 逐条对账

### 关闭条件 1 · 同一 Workstream 连续两个 manual Run：第二轮明确复用第一轮已沉淀事实，只补新/缺/过期；重复提交同一 idempotency key 不重复池子或成本

**机制。** `plan_increment`（`marketops/application/run_increment.py:341`）把「已沉淀的
观测 + 复核计划 + 变更信号」折成**一格一个处置**，四档的判据分两处：

* `_VERDICT_OF_STATUS`（`run_increment.py:134`）把钟的两档直接映射 ——
  `NEVER_OBSERVED → NEW`、`DUE → EXPIRED`。钟来自 `aspect_states(...)`
  （`marketops/domain/maintenance.py:923`，在 `run_increment.py:455` 调用）。
* `_current_cell`（`run_increment.py:499`）在钟说 `CURRENT` 时问**盘**：非
  `SOURCE_COVERAGE` ⇒ `REUSE`（理由逐字 `:544`「上一轮看过…窗口未到，且盘上有能当依据的
  来源」）；`SOURCE_COVERAGE` 且 `census.new_source_gaps` 非空 ⇒ `MISSING`
  （理由逐字 `:536`「一条能当依据的来源都没有」）。

**生产者**：`aspect_states`（钟）与 `census_for`（盘）。
**消费者**：MCP 工具 `get_run_increment`（`marketops/adapters/mcp_server/runs.py:363-374`
算它，`marketops/security/catalog.py:750` 注册它）。
**存储/事务**：**无** —— `RunIncrement` 不落盘，每次调用现算。这一层自己写在
`security/catalog.py:753-762`：「那份读数的输入（复核计划、观测、变更信号）没有一格存在
Run 那一行上，所以同一个 Run 过一会儿再读可能不一样」。

**正证据**：
* `tests/test_run_increment.py` 全族（纯夹具，零网络）：四档各自的判据、
  `:246` 复用整行（断言 `:252`）、`:256` 无来源时是 `MISSING` 而不是 `REUSE`
  （断言 `:266`）。
* **幂等键那一半**：`tests/test_mcp_runs.py:365`
  （`test_the_same_key_twice_is_the_same_row_and_not_a_second_round`，断言在 `:401`
  `_rows(settings) == 1`）、`:404`（同键异内容 ⇒ 拒）、`:487`（空键 ⇒ 拒，docstring
  逐字引条件 1 后半句）、`:900` / `:923`（带窗口的同键同/异）、
  `tests/test_run_window_admission.py:500` / `:525`（链停了之后重放仍不产生第二个 Run）。

**负证据 / 缺口（三条）**：
1. **「连续两个 Run」零判据。** 所有「提交两次」的用例都用**同一个键**，所以盘上只有
   **一行 Run**（`tests/test_mcp_runs.py:401`）；用**两个键**的地方都在测「第二轮回绝」
   —— `tests/test_run_window_admission.py:356`（`「还有 1 轮没有结束」` 逐字在 `:368`）、
   `:371`（对象数）、`:402`（累计预算）—— 不是在测「第二轮复用第一轮」。
2. **它读不到「第一轮已沉淀的事实」。** `plan_increment` 的七个形参里没有一格是「上一轮的
   产出」或「池子现状」；它读的是 `MaintenanceStore` 的 `Observation` 行。要让它读到，
   先得让「哪一轮记了哪一格」落盘 —— 那一格今天不存在（`run_increment.py:75` 逐字）。
3. **「不重复池子」零判据**；「不重复成本」只有**账本层**的判据
   （`tests/test_budget_contract.py:239` / `:225` / `:248`），**没有一条经过 `submit_run`
   这条面**断言一个 Run 不重复花钱。

**标注**：纯函数 + 夹具判据 + 字段清单。
**状态**：`PARTIAL`。**缺口归 B17**（三条都要求「一个 Run 的产出落盘」，与条件 2 同一处）。

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:256` 原文，本行标题即其逐字。

---

### 关闭条件 2 · Run 结束有完整 Delta，业务键双向对账；合法零新增仍能以 unchanged/held/blocked 结束

**机制。** 三块都在，且都是**造好了但接不上**：

* `RunDelta` —— `marketops/domain/run_delta.py` 的 `RunDelta`，九格（`effects` / `routes` /
  `evidence` / `artifacts` / `reviews` / `actions` / `usage` / `unaccounted` /
  `recommendations`），两个校验器都是形状判据（`_ids_are_distinct_and_not_blank`：引用列表
  不重复非空；`_one_disposition_per_key`：一个业务键只能有一个处置）。
  （**2026-09-29 订正**：这里原写「八格」并漏了 `actions`，同句那三处行号与今天的位置
  也对不上。行号不再逐处引用，改成点名成员 —— 行号引用只在指定 revision 时才有意义，
  而这一份是要长期活着的。同文档下面 `RunDelta.counts` 那一行写的一直是「九格恒在」，
  两处从前互相矛盾。）
* `ending_refusal`（`marketops/ports/runs.py:408`）—— 结局与产出**同时**要有的那条判据，
  两个存储实现共用同一份（`adapters/sqlite/runs.py:201`、`adapters/memory.py:2623`）。
* `reconcile_run_delta`（`marketops/application/run_reconciliation.py:272`）与它的前置判据
  `close_run`（`:324`）—— 账不平就不停表，这一轮留在 `running`，盘上一个字节都不动。

**生产者**：**零。** `RunDelta(` / `DeltaCell(` / `PoolEffect(` / `UnaccountedUsage(` 在
`marketops/` 下的**唯一**出现就是它们自己的类定义（`run_delta.py:110` / `:146` / `:201` /
`:241`）。构造只发生在五个测试文件里。
**消费者**：**零。**（**e4 之后不再是零 —— 见文末「补记 B」**）`close_run` 没有生产调用者；而 `RunStore.stop` 的唯一生产调用者是
`close_run` 内部那一行（`run_reconciliation.py:360`）。⇒ **今天盘上没有任何生产路径能把
一个 Run 结束掉。**
**存储/事务**：`RunStore.stop`（`marketops/ports/runs.py:569`）把结局、产出、停表**同事务**
落下；SQLite 实现 `adapters/sqlite/runs.py:181` 起。

**正证据**（第二半，走得通的那一半）：
* `tests/test_run_ending_contract.py:309-310`
  （`test_a_zero_gain_run_ends_cleanly`，参数化 `UNCHANGED` / `HELD` / `BLOCKED` 三条）——
  这一条就是条件 2 后半句的执行器。
* `tests/test_run_delta.py:377` / `:389` / `:316`（零新增 / 只有 `routes` / 「有东西但不空」）。
* 反面：`tests/test_run_ending_contract.py:271`（`COMPLETED` 配一份**每一格都空**的 Delta ⇒ 拒），
  它的 docstring 逐字引 §B17 条件 2。

**负证据**（第一半，走得不通的那一半）：
* MCP 目录今天共 **58** 条工具（`marketops/security/catalog.py` 的 `TOOL_CATALOG`），
  与 Run 有关的只有三条：`submit_run`（`:708`）、`get_run`（`:739`）、
  `get_run_increment`（`:750`）。**`close_run` / `stop_run` / `end_run` 一条都不存在。**
  （**e4 之后 `close_run` 存在 —— 见文末「补记 B」**；`stop_run` / `end_run` 仍然不存在。）
* handler 表同样只有三条（`marketops/adapters/mcp_server/runs.py:426-428`）。
  （**e4 之后是四条，`:577-581` —— 见文末「补记 B」**）
* 测试自己逐字承认这件事：`tests/test_mcp_runs.py:960` 与 `:1619` ——「夹具直接调
  `RunStore.stop` —— `close_run` 那条工具还没接线」。

**标注**：类型定义 + 契约判据 + MCP 目录（都是结构读数）。
**状态**：`PARTIAL`。第一半**没有生产者、没有 MCP 面**；第二半有执行器。

**缺口（三条，同一处）**（**e4 之后只剩一条 —— 见文末「补记 B」**）：①「完整 Delta」没有生产者；②「双向对账」有实现但零调用者；
③**MCP 结束面不存在**。三者互为同一个空宿主 —— **一个 Run 的产出，今天没有任何一处会
写下来**。`close_run` 要调用方递 `folded: tuple[PromotionOutcome, ...]`，而那正是这一处
缺的东西：折进池子的 `PromotionOutcome` 今天不带 run 身份、也不落盘（折那条路走
`promote_receipt`，它只收 `receipt_id`）。**归 B17**，且它与条件 3 后半互为前提。

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:257` 原文，本行标题即其逐字。

---

### 关闭条件 3 · 关闭所有客户端后当前 Run 继续并完成；完成后在 manual 模式下保持 idle，绝不自行扩下一批

**机制（两半分属两处，这正是不对称的原因）。**

**前半 —— 有人推，但推的不是 Run。** `ContinuationController`
（`marketops/application/continuation.py:427`），常驻驱动是
`marketops/interfaces/controller.py:602` 的 `while not stop.asked:`，每 30 秒一 tick
（tick 在 `:624-626`，节拍是 `:628` 的 `time.sleep(interval)`；`DEFAULT_INTERVAL_SECONDS`
在 `:92`）。**但 `ContinuationKind` 只有两个成员**
（`marketops/domain/continuation.py:73`，`:83` 的 `DISPATCH_BATCH` 与 `:85` 的
`MAINTENANCE_TICK`），**两个都不创建 Run**。

**后半 —— 没有任何东西会自行创建 Run。** `admit_run` 在**生产代码里只有一个调用点**
：`marketops/adapters/mcp_server/runs.py:251`（在 `submit_run` 里，由客户端驱动）。
绕过 `admit_run` 直接调 `RunStore.admit` 的生产路径**不存在**
（`store.admit(` 的另一处 `adapters/mcp_server/tools.py:2017` 是 `JobStore`，不是 Run）。
常驻控制器与续跑那一族里，`RunStore` / `admit_run` / `.admit(` **零命中**。

**正证据**：
* 前半：`tests/test_r2_continuity_acceptance.py:404`
  （`test_the_batch_runs_with_the_client_already_gone`）—— docstring 逐字「关闭条件 1。
  **链走完之后不再有任何一次工具调用。**…`_tick` 进的是 `run_once_cli` —— 它自己
  `_build`、自己 `acquire`、自己驱动，中间不经过 MCP」。**它证的是续跑批次，不是 B17 的 Run。**
* 后半（类型层）：`tests/test_run_delta.py:296`
  （`test_the_recommendations_are_prose_not_a_trigger`）—— 断言
  `RunDelta.model_fields["recommendations"].annotation == tuple[str, ...]`。一个能直接喂回
  受理面的结构会让这句话在盘上无处成立，所以这一格在类型上只是字符串。

**负证据**：后半今天**无法**有行为判据 —— 要判「manual 的 Run 结束之后不再开一轮」，先得
能把一轮 Run 结束掉，而那个面不存在（见条件 2 的缺口）。条件 4 的两条上限判据都要夹具
直接调 `RunStore.stop` 造出一个已结束的链，**不是** manual 的 idle。

**标注**：结构读数（调用点穷举）+ 类型层判据 + 常驻驱动的循环体。
**状态**：`PARTIAL`。后半靠「没有自主驱动」成立，且是**结构上**成立的（可判、已判到
类型层），但它缺一条行为判据。

**缺口（两条）**：
1. **「一个 Run 在客户端断开后继续」零判据。** 现有那一条判的是续跑批次
   （`ContinuationKind` 的两个成员），而条件 3 的主语是**当前 Run**。二者今天甚至不在
   同一条链上。
2. **「manual 完成后保持 idle」没有行为判据**，且**今天不可能有** —— 它与条件 2 的
   MCP 结束面互为前提。**归 B17**（切片 e3 与条件 2 一起做）。

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:258` 原文，本行标题即其逐字。

---

### 关闭条件 4 · bounded_auto 在达到对象数/累计预算/截止时间任一 stop 后不创建下一 Run；重放 trigger 不重复创建

**机制。** 三个阈值写在一份显式授权里 —— `AuthorizationWindow`
（`marketops/domain/run.py:150`，三项都是必填；`not_after` 那一格过
`_not_after_is_timezone_aware`（`:217`），`digest` 属性在 `:221`）。三项都必填的理由是
那一格自己写的：「一个缺席的阈值与『那条判据没触发』在回执上同形」。读法与拒绝面在
`marketops/ports/runs.py::window_refusal`（`:273`），五条顺序判定；
累计由 `ChainProgress` / `progress_of`（`ports/runs.py:59` / `:120`）从**已结束的轮**上算，
跨轮去重取并集。

**生产者**：服务面的第七个参数 —— `submit_run(window=...)`
（`marketops/adapters/mcp_server/runs.py:238` 起，切片 e2c）。**这是本条件唯一的正生产者。**
**消费者**：受理面自己（`admission_refusal` 在 `ports/runs.py:176`，
`window_refusal` 在 `:273`）—— 两条都在**受理那一刻**判。
**存储/事务**：`RunStore.chain_progress`（`ports/runs.py:547`）读已结束的轮；
`window_refusal` 必须在写事务里调用（并发下才有意义）。

**正证据**：
* 对象数那一支：`tests/test_mcp_runs.py`
  `test_a_chain_that_reached_its_object_ceiling_opens_no_next_round`。
* 累计预算那一支：`tests/test_mcp_runs.py`
  `test_a_chain_that_reached_its_budget_ceiling_opens_no_next_round`（走 `set_ceiling` →
  `reserve` → `spend`，把账本读数写进第一轮的 `delta.usage`）。
* 重放不重复创建：`tests/test_run_window_admission.py:500` / `:525`。
* 授权窗口自身的形状与跨格校验：`tests/test_run_window.py`（含 `:170-171` 的
  「只有 `bounded_auto` 能带窗口」）、`tests/test_run_admission.py:340-341`、
  `tests/test_mcp_runs.py` 第 3′ 节（15 个用例函数，该文件实测收集数 42 → 65）。

**负证据 / 缺口（三条）**：
1. **`RunDelta.usage` 没有生产者** ⇒ 条件 4 的「累计预算」在**任何真实链**上都读到 0。
   本片那两条判据里的 `delta.usage` 是**夹具直接写**上去的。按计划 `:503`，读法与拒绝面
   造出来了不等于关闭。
2. **截止时间那一支不可达。** `at < cutoff <= not_after` 由两条上游校验器保证
   （`RunSpec._window_agrees_with_the_rest` 与 `admission_refusal` 的「截止时刻不在将来」），
   所以三项里只有两项试得到。这一条是**实测**出来的（e2b），不是推断。
3. **「`window_refusal` 必须在写事务里调用」没有判据。** 单线程下放在事务外行为一样，
   要判它需要一条**并发受理**的用例 —— **切片 h**。

**标注**：域层类型 + 端口判据 + 服务面端到端（各一条），零网络调用。
**状态**：`PARTIAL`。**缺口 1 与 3 归 B17**（前者与条件 1/2 同一处空宿主，后者是切片 h）。

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:259` 原文，本行标题即其逐字。

---

### 关闭条件 5 · watch 只为到期/信号对象创建维护 Run；SavedView 单纯查询不创建 Run

**机制。** 这一条的主语今天**一个都不存在**
（**前半句在切片 f 之后有了主语 —— 见文末「补记 D」**）：

* `ExecutionMode.WATCH` 在 `marketops/` 下**只有一个消费者，而且是把它拒掉的**
  —— `marketops/ports/runs.py:242`（那一段到 `:250` 的 `bounded_auto` 那句收，`:251` 是
  收尾的 `)`）。那一句逐字说清了原因：「它的来源是 Watch 的到期/信号/日程
  （`domain/maintenance.py::what_is_due`），而把那个来源接成维护 Run 的那一片（**f**）
  还没有落 —— 今天没有一处会**因为一个对象到期**而提交一次受理」。
  （**这一段在切片 f 之后已被删除 —— 见文末「补记 D」**）
* `SavedView` 在 `marketops/` 与 `tests/` 下**零命中**；全部命中都在 `docs/`
  （`docs/DATA_MODEL.md:35`、`docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md:186`/`:200`、
  `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:31`、`docs/TARGET_ARCHITECTURE_GAP_MATRIX.md:83`）。
  （**`tests/` 那一半在切片 f 之后不再逐字成立 —— 见文末「补记 D」**）
* 同族的 `ActionItem` 也不存在，而且 `RunDelta` 那一侧自己记着这件事
  （`marketops/domain/run_delta.py:69` 逐字：「`ActionItem` 在本仓**不存在**
  （§21 的 B24 一行逐字 `NOT BUILT`）」）。

**存在的（但不是这一条的机制）**：到期判定 `what_is_due`
（`marketops/domain/maintenance.py:612`）与变更信号 `ChangeSignal`（`domain/maintenance.py:328`，
生产者 `application/contact_routes.py:599`）。两者都判得出来，但**「到期 ⇒ 创建维护 Run」
这条线不存在**（**切片 f 之后存在 —— 见文末「补记 D」**）。日程 / scheduler / cron 在本仓
**没有任何实现**：全仓唯一的 `scheduler`
命中是 `marketops/application/dispatch.py:307` 的一句注释（引 Quay 0.11.0 的
`concurrent-batch-scheduler`，说的是那个模板渲染给谁），不是实现。
（**这一句在切片 f 之后仍然成立 —— 见文末「补记 D」**）

**正证据**：无（没有可运行的主语）。
（**切片 f 之后有了 —— 见文末「补记 D」**）
**负证据**：`tests/test_run_contract.py:186`（那一支的理由表在 `:179`，`:196` 逐字
「**这一份请求没有写下授权**」）、`tests/test_run_admission.py:340-341`、
`tests/test_run_window.py:170-171`（WATCH 带窗口 ⇒ 拒）。
（**前两处在切片 f 之后已不指它 —— 见文末「补记 D」**）
**标注**：结构读数 + 拒绝面判据。
**状态**：`NOT_BUILT`（全仓没有实现 —— 不是「没测」）。
（**切片 f 之后前半句是 `PARTIAL`、后半句仍 `NOT_BUILT` —— 见文末「补记 D」**）

**缺口（两条，都不归 B17）**：①「到期/信号 ⇒ 维护 Run」这条线归**切片 f**；
②`SavedView` / `Watch` / `ActionItem` 三个对象归 **B24**（计划 `:31` 逐字 `NOT BUILT`）。
本行只登记归属，不在 B17 里造它们。
（**①在切片 f 里已落 —— 见文末「补记 D」**）

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:260` 原文，本行标题即其逐字。

---

### 关闭条件 6 · 无可证明市场总量时报告可观测池子状态，不产生“国家完成率/已爬完”

**机制（前半走得通，后半只有散文）。**

**前半 —— 可观测池子状态。** 资产池读数经 MCP 工具 `list_assets` 到面
（`marketops/adapters/mcp_server/product_loop.py:1610` 定义），回执三格：
`asset_count` / `pool_state`（三态 `present` / `no_store` / `not_wired`）/ `summary`；
`summary` 由 `_pool_summary`（`:1564`）产出，六格在 `:1594-1606`
（`by_domain_verdict` / `by_source_namespace` / `with_verified_domain` /
`with_an_unchecked_upstream_claim` / `with_no_domain_lead_at_all` / `never_re_checked`），
而它的 docstring 明说自己**不是**「有多少块场地过时了」。链那一侧另有
`RunDelta.counts`（`domain/run_delta.py:299`，九格恒在）
与 `RunIncrement.counts`（`application/run_increment.py:280`，四档含零），后者经
`get_run_increment` 到面。

**后半 —— 不产生完成率/已爬完。** `完成率` / `已爬完` / `爬完` 在 `marketops/` 下
**零命中**。`覆盖率` 有五处命中，**没有一处算得出市场完成率**：两处是影子对比里
「哪个后端答了」（`application/shadow.py:28` 的 `PARTIAL_COVERAGE` 那一档），
一处是收据里那句警语「母池覆盖率的分母从一开始就是错的」（`domain/receipt.py:29`），
两处是**禁止方向**的 docstring（`domain/fetch_attempt.py:102`、`ports/acquisition.py:234`）。
而 MCP 那一侧的 `_coverage_receipt`（`product_loop.py:3216`）分母是 `REQUIRED_ASPECTS`
—— 「本部署接了几面能力」，与市场覆盖无关。

**生产者**：`VenueAssetStore.list_assets`（`marketops/ports/pool.py:150`；实现
`adapters/sqlite/pool.py:163`、`adapters/memory.py:1366`）。
**消费者**：MCP 工具 `list_assets`（上引）与 `get_run_increment`
（`adapters/mcp_server/runs.py:363-374`）。
**存储/事务**：只读（池子是 workspace 级的表）。

**正证据**：`tests/test_mcp_product_loop.py` 那一族对 `list_assets` 回执形状的判据
（`asset_count` / `pool_state` 三态恒有）。
**负证据**：**没有一条判据在钉「不产生完成率」。** 那句禁令今天只活在散文里
（`docs/PRODUCT_LINE_AND_SCENE_MODEL.md:92` 逐字「覆盖率如果没有可证明分母，只能报告…
不能写『法国覆盖80%』或『该国已完成』」），**在 `marketops/` 里没有对应代码或断言**。
纪律的近亲 `tests/test_shadow.py:68`（`_FORBIDDEN_REPORT_KEYS`）管的是影子对比报告，
不是市场覆盖。

**标注**：MCP 回执字段 + 全仓文本扫描 + 端口签名。
**状态**：`PARTIAL`。前半有生产者、有消费者、读得回；**后半零执行器**。

**缺口（一条）**：后半要靠「没有东西算得出完成率」成立，而**一个只靠「那条路还不存在」
成立的约束不算被守住**（与 B16 条件 5 缺口第 2 条同一条纪律）。要让它可判，先得有一条
「这一次报告里有没有分母」的读数。**归 B17**（与条件 5 同批：分母来自池子的总量，而那
正是 B24 的 View 要回答的东西）。

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:261` 原文，本行标题即其逐字。

---

### 关闭条件 7 · RunDelta 合并对并发/重放/CAS有负例；旧 session 不能用旧 snapshot 覆盖新事实

**机制（三条子要求，命运完全不同）。**

**重放 —— 有，而且多。** `RunStore.stop` 的判重放依据是**这一套结局与产出**而不是被调过
几次（`ports/runs.py:569` 的 docstring），指纹算在 `stopping_outcome`（`:439` 起）。
**并发 —— 没有。** **CAS —— Run 这条链上没有。**（**e3 与本片之后「并发」这半已不成立 —— 见文末「补记 F」**）

**生产者**：`stopping_outcome`（两个存储实现共用，调用点在
`adapters/sqlite/runs.py:231`、`adapters/memory.py:2657`）。
**消费者**：`RunStore.stop` 自己。
**存储/事务**：`adapters/sqlite/runs.py` 的 `stop`（`:181`）在**写事务**里读—判—写 ——
`write_transaction`（定义在 `adapters/sqlite/db.py:1305`，`BEGIN IMMEDIATE` 在 `:1307`）
在 `adapters/sqlite/runs.py:105` 被调；但那条 `UPDATE`（`:207`）的 `WHERE` **只有
`run_id`**（`:208`），**没有版本列**。

**正证据（重放那半）**：
* `tests/test_run_ending_contract.py:165`（重放同一套结局拿回同一行，`stopped_at` 不变）、
  `:187`（同一个 Run 的第二份**不同产出** ⇒ 拒）、`:210`（产出同、结局词不同 ⇒ 拒）、
  `:224`（指不到 Run ⇒ 拒且不新写）、`:239`（没开始过与已结束分得开）、`:287`（被拒的
  结束一个字节都不落）。
* `tests/test_run_contract.py:148` / `:157`（幂等键冲突、旧行不被改）。
* `tests/test_run_reconciliation.py:518`（账不平不结束）、`:602`（结束两次拿回同一行）。

**负证据 / 缺口（三条）**：
1. **并发零判据**：`tests/` 下并发原语的命中只有四处 ——
   `tests/test_budget_ledger.py:38`（并发预留，`ThreadPoolExecutor` 在 `:55`）与三个
   网络夹具（`test_smtp_credentials.py` / `test_egress_proxy.py` /
   `test_browser_egress_live.py`）。**没有任何两个写者争一个 Run 的用例。**（**e3 与本片之后已不成立 —— 见文末「补记 F」**）
2. **CAS 零判据**：Run 这条链上没有 `expected_version` / `revision` / `epoch` 参与判定
   （`RunDelta` 只有形状校验器，`domain/run_delta.py:269` / `:283`）。CAS 在别的子系统
   有（`tests/test_application_scenes.py:385` / `:412`、`tests/test_scene_store_contract.py:263`
   / `:287` / `:232`），**不是** Run 这一条。
3. **「旧 snapshot 不能覆盖新事实」只有一半**：守卫是**内容指纹**而不是版本 —— 能挡住
   「不一样的第二份覆盖第一份」，但一份**恰好指纹相同**的晚到写会被当成重放接受；
   而「旧 session」在本层没有对应物（Run 这一层最近的操作单位是 Run，没有 session）。
   更上游的是：`RunDelta` 没有 `merge`（`application/merge.py:37` 的 `later_of` /
   `:53` 的 `ordered_union` 两个原语，调用点全在池子那一侧），所以「合并」这件事今天
   **没有主语**。

**标注**：契约判据 + 存储实现的写路径读数 + 并发/线程扫描。
**状态**：`PARTIAL`。**缺口 1/2/3 归 B17**，且它们与条件 2 是同一处：`RunDelta` 没有
生产者与消费者，所以「合并」没有主语，也就没有可测的并发与 CAS。

**这一行的外部要求行**：`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:262` 原文，本行标题即其逐字。

---

## 这些缺口的共同形状（写在这里，因为五行的缺口指回同一处）

条件 1 第 2 条、条件 2 全部、条件 3 第 2 条、条件 4 第 1 条、条件 7 第 3 条 —— 五条缺口
落在**同一个空宿主**上：**一个 Run 的产出（`RunDelta`）在 `marketops/` 下没有任何生产
者，因此「这一轮走到哪了、折了什么」在盘上无处成立。** 它的下游后果一次排出来：

* 条件 2 的「完整 Delta」与「双向对账」没有主语 ⇒ 没有 MCP 结束面；
* 条件 3 的「manual 完成后保持 idle」没有可结束的 Run ⇒ 行为判据不可能有；
* 条件 1 的「第二轮复用第一轮已沉淀事实」读不到第一轮产出了什么；
* 条件 4 的「累计预算」读到 0（`RunDelta.usage` 无生产者）；
* 条件 7 的「合并」没有主语 ⇒ 并发与 CAS 负例无从谈起。

而这一处今天**卡在一个具体的接口选择上**（**e4 已解 —— 见文末「补记 B」**）：`close_run` 要调用方递
`folded: tuple[PromotionOutcome, ...]`，而折那条路（`promote_receipt`）只收
`receipt_id`、产出的 `PromotionOutcome` **不带 run 身份也不落盘**。要接上它，先要回答
「一次折入怎么归属到某一轮 Run」—— 那是下一片（e3 与条件 2 一起）的第一个决定。

---

## 这份矩阵的指针是怎么核的

这份文件里每一处 `文件:行号` 都过了一遍机器核对：一份
`B17-MATRIX-CITATION-CHECK.py`（在
`ARTIFACTS_ROOT/work-items/marketops/TARGET-CONVERGENCE-B17/`）把 **139** 条
`(文件, 行号, 该行必须含的串)` 逐条读回来比对。

**第一遍 139 条里 30 条没过。** 一部分是行号差了一两位（把装饰器行写成函数行、把
import 或 docstring 里提过这个符号的行写成它的定义行），但有几条是**真的指错了地方**，
而且指错的方式恰好最坏 —— 指到的行上**有那个符号**（在 import 或注释里提过一次），
于是「读了一眼，确实在」与「读的是定义」分不出来。改完之后的六处：

| 原本写的指针 | 那一行实际是什么 | 真地址 |
|---|---|---|
| `adapters/sqlite/runs.py:190` | 「一次结束」那一节的分节注释 | `:181`（`def stop`） |
| `adapters/memory.py:2618` | `_already_ended` 的 docstring 开头 | `:2657`（`stopping_outcome` 调用） |
| `domain/run.py:211` | `_budget_is_an_envelope` 的 `return value` | `:221`（`def digest`） |
| `ports/runs.py:250` | WATCH 那一支的收尾 `)` | `:242`（`if ... is WATCH`） |
| `tests/test_run_window_admission.py:366 / :378 / :396` | `:366` 是文档串，另两处是别条用例 | `:356` / `:371` / `:402`（三条「第二轮回绝」用例），逐字断言在 `:368` |
| `tests/test_run_delta.py:299` | 目标用例的**上一行** | `:296` |

**第二件量具**（`B17-MATRIX-SELFSCAN.py`）把**正文里**抽得出的 94 处 `文件:行号` 逐条
按约定消解（相对仓库根、代码在 `marketops/` 下）并查范围。它抓到了上面那张表**抓不到的
东西**：一处我光写了 `runs.py` 加行号的地方，按约定消解**不唯一** —— 仓库里有两个同名的
`runs.py`（`adapters/mcp_server/runs.py` 与 `ports/runs.py`），而我想指的是**第三个**
（`adapters/sqlite/runs.py`）。那一行在内容核对里是**过**的（我验的是
`adapters/sqlite/runs.py:105` 这一条），错只错在**写进散文时把限定前缀丢了**。
两件量具查的是两件事，缺一件都会漏。

**这一条值得写下来而不是悄悄改掉**：一份验收矩阵的价值全在它的指针上。指针错一位，
读的人按图索骥、找到一行确实含那个符号的代码、于是**确认了一遍本来就有的成见** ——
它比「这里没有证据」更坏，因为它伪装成证据。所以核对不是排版，是这份文件能不能被读的
前提。

---

## 这份矩阵不做的事

* **不宣称任何 live 结果。** 上面每一条都是 fixture、纯函数或结构读数，零网络调用、零
  provider 调用。「测试通过」不等于「现场 provider 已验证」（§18）。
* **不把 fixture 说成现场。** 条件 4 那两条判据的 `delta.usage` 是夹具写的，本文件逐字
  写了这一点，而不是让「累计预算有两条判据」读起来像「累计预算真的接上了」。
* **不给完成百分比。** 7 行里各状态的行数不是进度。
* **不改两份上游文件**（理由见开头）。
* **不把切片当关闭。** 条件 5 的两条缺口归 f 与 B24，本文件只登记归属 —— **登记不等于
  完成**，它只是让下一片开始的时候不必重新发现它们。
* **不为缺口造占位判据。** 条件 3 后半今天没有行为判据，而它的处置**不是**补一条恒真的
  「没有自主驱动」断言 —— 那种判据此后会被读成「这里已经判过」。它的处置是让
  「结束一轮 Run」这件事在服务面上真的发生（条件 2）。

---

## 补记 B：B17-e4 之后的读数

上面那些行是 `dc8d0d9`（B17-e2c）那一棵树上的读数，**原样留着**；本节的读数是**另一棵
树**上的，凡与上面冲突处以本节为准。两片 e4 各自改的是什么，写在它们自己的提交信息里；
这里只登记**上面哪一行不再成立**，以及现在量到的是什么。

**本节只写随代码而定的结构读数。** 时刻、用时、全量套件的计数与本次被测的树记在
artifacts 那一侧（`ARTIFACTS_ROOT/work-items/marketops/TARGET-CONVERGENCE-B17/`）——
它们随「这份文件什么时候被写」而变，写进这里会让下一次读的人拿一个已经过期的数去核。

| 上面那一处 | 它当时说 | 现在量的 |
|---|---|---|
| `:40` / `:133` | MCP 目录 58 条；与 Run 有关的只有三条 | **59** 条；与 Run 有关的是**四条**，第四条 `close_run` 在 `marketops/security/catalog.py:762` |
| `:134` | `close_run` / `stop_run` / `end_run` 一条都不存在 | `close_run` **存在**（`marketops/adapters/mcp_server/runs.py:478`）；`stop_run` / `end_run` 全仓**仍然零命中** |
| `:136` | handler 表只有三条（`runs.py:426-428`） | **四条**（`runs.py:577-581`）；组合根接在 `marketops/interfaces/mcp/__main__.py:701`，且它多要了**第五样** `assets`（`:711`） |
| `:117-119` | 消费者：零；`stop` 的唯一生产调用者是 `close_run` 那一行 | `close_run` 的调用者有**两处**：应用层 `marketops/application/run_reconciliation.py:378` 与 MCP 工具 `runs.py:478`；`runs.stop` 的唯一生产调用者仍是 `run_reconciliation.py:443`。⇒「盘上没有任何生产路径能把一个 Run 结束掉」**不再成立** |
| `:144-147` | 缺口三条：①完整 Delta 无生产者 ②双向对账零调用者 ③MCP 结束面不存在 | ③**关闭**、②**关闭**（两道闸都有了服务面调用者）；①**换了主体**：产出一份 Delta 的入口现在是 MCP 的 `close_run` 参数（`runs.py:826` 的 `RunDelta.model_validate`），而 `RunDelta(` / `DeltaCell(` / `PoolEffect(` / `UnaccountedUsage(` 在 `marketops/` 下**仍然零构造**（实测零命中）。⇒ 这一行从 `PARTIAL` 收窄到「只剩条件 4 那处引用的 `usage` 没有生产者」，即与条件 4 那一行是**同一处** |
| `:146` / `:394-396` | `close_run` 要 `folded: tuple[PromotionOutcome, ...]`，而折那条路不带 run 身份 | e4（一）已改：`folded: Iterable[str]`（`run_reconciliation.py:385`）—— 主张收窄成**池子句柄**，规范化点唯一（`fold_claim`，`:306`）。「一次折入归属哪一轮」由 e3 的折入账（`run_fold_ledger` / `run_fold`）承担 |
| `:138-139`（它引的两处测试） | 「夹具直接调 `RunStore.stop` —— `close_run` 那条工具还没接线」 | 那句引文在两处都已改写（现在是 `tests/test_mcp_runs.py:983` 与 `:1660`）：夹具**仍然**直接调 `stop`，但理由换了 —— 它要的是「盘上那一行已经停了」，不是「结束经由那两道对账闸发生过」 |

**这一节不宣称条件 2 关闭。** 它登记的是「上面对应的那几句话不再成立」，而不是
「条件 2 已经满足」—— 条件 2 还牵着条件 1（连续两轮）与条件 7（合并），那两处各自有
自己的行。

---

## 补记 C：B17-e5 之后的读数

B17-e5 在 `tests/test_mcp_runs.py` 里新开了**第 10 节**（`:3016-3305`），四条端到端
判据。上面那些行是 `dc8d0d9`（B17-e2c）那一棵树上的读数，**原样留着**；本节的读数是
**另一棵树**上的，凡与上面冲突处以本节为准。

**本节只写随代码而定的结构读数**（与补记 B 同一条口径）：时刻、用时、全量套件的计数、
本次被测的树 sha 记在 artifacts 那一侧
（`ARTIFACTS_ROOT/work-items/marketops/TARGET-CONVERGENCE-B17/mutation/e5/`）。

| 上面那一处 | 它当时说 | 现在量的 |
|---|---|---|
| `:86-88`（条件 1 缺口 1） | 「连续两个 Run」**零判据**：所有「提交两次」的用例都用**同一个键**，盘上只有一行 | **关闭**。`tests/test_mcp_runs.py:3101` 用两个不同的键（`:3041` 的 `KEY2`）受理出**两行**（`:3165` 断言 `_rows(settings) == 2`），两次读数取在**两个不同的 Run** 上（`:3174` 的 `assert after["run_id"] == second`）；`:3164` 另钉一句「两个键受理出了同一个 id ⇒ 盘上就只有一轮」 |
| `:89-92`（条件 1 缺口 2） | 它**读不到**「第一轮已沉淀的事实」：`plan_increment` 的七个形参里没有一格是「上一轮的产出」，它读的是维护账 | **仍然开着，但换了交代方式。** 冻结的设计记录已裁定「『第一轮』在结构上没有对应物」，所以第 10 节**不假装**判得动那一句，改判这一层实际在做的事 —— **读数由账决定，不由轮次决定**（取舍逐字写在 `tests/test_mcp_runs.py:3027-3037`）。⇒ 这一格**没有关闭**：它与「哪一轮记了哪一格要落盘」是同一件事，归条件 7 |
| `:93-96`（条件 1 缺口 3） | 「不重复池子」**零判据**；「不重复成本」只有**账本层**判据，**没有一条经过 `submit_run`** | **两条都补上**。池子：`tests/test_mcp_runs.py:3227`，数**两本账**（`_streams`），重发前后 `(1, (1, 1))` 逐字不变（`:3269`、`:3274`）。工具面：`:3278`，三次受理（含同键重发一次、另一个键一次）之后 `budget_reservation` **零行**（`:3312`、`:3303`、`:3305`） |
| `:185-187`（条件 3 那一节的**负证据**） | 后半「今天**无法**有行为判据」—— 要判「manual 的 Run 结束之后不再开一轮」，先得能把一轮结束掉，而那个面不存在 | 条件 2 的结束面在 e4 有了，所以现在有：`tests/test_mcp_runs.py:3180`，一轮结束之后盘上仍只有**一行**（`:3224`），且先断言那份散文建议**确实写下来了**（`:3223`）—— 不然判的是「没有建议」而不是「建议没被执行」 |
| `:194`（条件 3 缺口 2） | 「manual 完成后保持 idle」没有行为判据，且今天不可能有 —— 它与条件 2 的 MCP 结束面互为前提，**归 B17** | **关闭**（同上一行）。这一格的处置正是它当时写下的那一条：等条件 2 的结束面落地，再判 |

**两条留在原地的**（本节不宣称它们不再成立）：

* 条件 3 缺口 1（「一个 Run 在客户端断开后继续」零判据）不在 e5 射程内 —— 它的主语是
  续跑批次，与第 10 节判的「连续两轮」不是同一条链。
* 条件 1 缺口 2 如表格第三行所述**仍然开着**。⇒ **这一节不宣称条件 1 关闭**，它登记的是
  「缺口 1 与缺口 3 有了判据、缺口 2 换了交代」，而缺口 2 与条件 7 同一处。

### 本节新引的指针，机器核过

上面表格里每一个行号都进了 `B17-MATRIX-CITATION-CHECK.py`（`ARTIFACTS_ROOT` 侧）的核对
表，实测 **20 条全命中、0 条 MISS**。同一趟跑出 `共 159 条，MISS 44 条` —— 那 44 条
**全部**是本片之前就已存在的漂移，与本节无关，判据是 `git diff --numstat`：本片对被核
文件只增不删（`tests/test_mcp_runs.py` `292/0`、本文件 `27/0`），所以第 10 节落在文件
**末尾**这件事不可能移动上面任何一行的行号。

**那 44 条不在这里重算**：它们指向本文件**补记 B 之前**的正文，而补记 B/C 已声明「凡与
上面冲突处以补记为准」；重算它们是「矩阵正文的指针维护」这件独立的事，与本节要坐实的
四条缺口不是同一件事。

---

## 补记 D：B17-f 之后的读数

B17-f 把「到期/信号 ⇒ 维护 Run」这条线接上了：库层多了一道**要读盘**的核验
（`marketops/application/run_admission.py:228` 的 `due_refusal`），MCP 层多了一格
「给了主体来源而模式不是 watch ⇒ 拒」，`ports/runs.py` 的纯函数里**删掉了**那段
「它的来源还没有生产者」。判据新开了两节：`tests/test_mcp_runs.py` **第 11 节**
（`:3351-3813`，端到端走真的 MCP 调用面）与 `tests/test_run_admission.py` **第 4 节**
（`:482` 起，库层读盘的那一段）。

上面那些行（条件 5 那一节的正文，含本节前刚加的行内标记）仍是 `dc8d0d9`（B17-e2c）
那棵树上的读数，**原样留着**；本节的读数是**另一棵树**上的，凡与上面冲突处以本节为准。

**本节只写随代码而定的结构读数**（与补记 B/C 同一条口径）：时刻、用时、全量套件的计数、
本次被测的树 sha 记在 artifacts 那一侧
（`ARTIFACTS_ROOT/work-items/marketops/TARGET-CONVERGENCE-B17/f/`）。

| 上面那一处 | 它当时说 | 现在量的 |
|---|---|---|
| `:248-249`（条件 5 机制的开场：这一条的主语**一个都不存在**） | 全仓没有一处会因为一个对象到期而提交一次受理 | **前半句有了主语。** `marketops/application/run_admission.py:228` 的 `due_refusal` 就是那个执行者 —— 它答「这一批主体上今天有没有到期/信号」，答「有」才放行；调用点在 `:201`（`spec_of` 之后、`store.admit` 之前，也就是**先判后写**）。后半句（`SavedView` 那一半）见本表第 3 行 |
| `:251-255`（`ExecutionMode.WATCH` 只有一个消费者，且是把它拒掉的 —— `marketops/ports/runs.py:242` 那一段） | 那一段逐字说「它的来源是 Watch 的到期/信号/日程……而把那个来源接成维护 Run 的那一片（**f**）还没有落」 | **那一段已被删除。** `marketops/ports/runs.py:177` 的 `admission_refusal` 正文今天只剩两条（`:261` 无窗口的 `bounded_auto`、`:273` `cutoff <= at`），`WATCH` 那条不在了；`ports/runs.py` 的 docstring 新开了一节说明它**换了住处**，并写下**取回它的针**：`git log -S "没人问它" -- marketops/ports/runs.py`（`5ebc789` 写进去、切片 f 那一次删掉）—— 主干那句「它的来源是 Watch 的」**不能用**，新 docstring 又逐字引了它一次，于是它在 `-S` 眼里「次数没变」，那次删除不会被列出来。新那一句落在 `due_refusal` 里。顺带：上面那一处引的 `ports/runs.py:242` 今天落在 docstring 的散文中间（「这一份规格今天走不走得通」那一行），**已经不再指它**。**这条线今天存在**：`submit_run(execution_mode='watch')` 经 `marketops/adapters/mcp_server/runs.py:897` 取主体来源（`product_loop.py:3438` 的 `_subjects`，「名字从哪来」判在 `:3332` 的 `_subject_source`），再到 `due_refusal` 读维护账 |
| `:256-259`（`SavedView` 在 `marketops/` 与 `tests/` 下**零命中**） | 两处都零命中；全部命中都在 `docs/` | **`marketops/` 那一半仍然成立，`tests/` 那一半不再逐字成立。** 工作树的 `tests/` 下有 **4 处**命中，全在 `tests/test_mcp_runs.py`（`:3353`、`:3357`、`:3358`、`:3608`），**全部是引用条件 5 后半句的注释，没有一处构造它**；`git archive HEAD` 解出的那片树上两处仍是**零命中**（对照读数在 `f/head-tree/`）。⇒ 判据（「这个对象不存在」）仍然成立，变的只是**写下这个名字的地方**。先例是同族的 `ActionItem`：`marketops/domain/run_delta.py:69` 写下那个字面名，正是为了声明它不存在。**另外半句要另说**：括号里列的那 4 个文档**不是全部**（它是举例，但读起来像穷举）—— 同一趟实测，HEAD 树上 `docs/` 下有 **8 个文件、29 处**命中（`docs/ARCHITECTURE.md`、`docs/MCP_SERVICE.md` 与 `docs/B17_*` 两份都在其中）。那一句的**主张**（`marketops/` 与 `tests/` 之外才有）是对的，**清单**从来就不是穷举。这是一处与本片无关的既有读法缺陷，**登记在这里**，不在本片改它 |
| `:261-263`（`ActionItem` 也不存在） | `run_delta.py:69` 逐字「`ActionItem` 在本仓**不存在**」 | **仍然成立**（本趟复测：那份逐字还在 `marketops/domain/run_delta.py:69`） |
| `:267-268`（「到期 ⇒ 创建维护 Run」这条线**不存在**） | 到期判定与变更信号都判得出来，但那条线不存在 | **存在了。** `due_refusal` 的第三支（`run_admission.py:326`）在「一条都没有」时拒，报出**判了几个主体**；有到期才放行。正证据见本表下一行 |
| `:269-271`（日程 / scheduler / cron 在本仓没有任何实现） | 全仓唯一的 `scheduler` 命中是 `marketops/application/dispatch.py:307` 的一句注释 | **仍然成立**（本趟复测：`command grep -rn scheduler marketops/` 仍是这一处）。切片 f 接的是**到期判定**，不是日程 |
| `:274`（正证据：无 —— 没有可运行的主语） | 无 | **有了。** 端到端在 `tests/test_mcp_runs.py:3476`（受理得下来、盘上恰一行 `:3507`、回执里 `window is None` `:3505`）；「有**信号**才算数」在 `:3549`（信号经 `put_signal(` 写入 `:3579`）；「重发同一个键仍只有一行」在 `:3671`。库层三条在 `tests/test_run_admission.py` 第 4 节：`:544`（没人看过的格子算到期）、`:564`（刚看过的不算）、`:597`（**判不了 ⇒ 拒**，不冒充受理） |
| `:276-277`（负证据前两处：`tests/test_run_contract.py:186`（理由表 `:179`）、`tests/test_run_admission.py:340-341`） | 两处都指「watch 受理即拒」 | **两处都已不指它。** `tests/test_run_contract.py:181` 的 `_REFUSALS` 表今天**只剩一行**（`BOUNDED_AUTO`），`watch` 那条已从表里去掉，函数名也换了（`test_a_windowless_automatic_mode_is_refused_at_admission`，`:187`，parametrize 在 `:186`）；`:195` 那句逐字仍在，但它现在说的是 `bounded_auto`。`tests/test_run_admission.py` 那两条 `340`/`341` 已移位并合并，判据本体在 `:351` |
| `:278`（负证据第三处：`tests/test_run_window.py:170-171`，WATCH 带窗口 ⇒ 拒） | 同上 | **仍然准确。** 那一句住得比「来源没接」更深、与模式无关：`marketops/domain/run.py:412` 的 `_window_agrees_with_the_rest`，逐字在 `:452`「带了授权窗口」与 `:457`「由 due 判定回答」。切片 f 另给它配了一条**反回归**判据（`tests/test_mcp_runs.py:3709`），防的正是「判据 1 打开的不是一个口子」 |
| `:280-281`（标注 / 状态：`NOT_BUILT`，全仓没有实现） | `NOT_BUILT` | **前半句 `PARTIAL`、后半句仍 `NOT_BUILT`。** 「到期/信号 ⇒ 维护 Run」这一半有了实现与判据；「`SavedView` 单纯查询不创建 Run」这一半仍**没有主语**（`SavedView` 一处也不构造，见本表第 3 行）。⇒ 整行**不能**改成 `BUILT`。另外「读面不创建 Run」在 `tests/test_mcp_runs.py:3603` 有了一条判据，但它判的是**已有的两个读面**（`get_maintenance_plan` 与 `get_run_increment`，两半都量三本账：Run / 观测 / 信号），**不是在判 `SavedView`** —— 这一条不能读成后半句有了依据；另有一条第 11 节的判据（`:3780`）判「暂停的场景上拒」，它也不属于后半句 |
| `:284-285`（缺口①归切片 f、②归 B24） | 本行只登记归属 | **①已落**，归属不变（这一片就是 f）。**②不变**：`SavedView` / `Watch` / `ActionItem` 三个对象仍归 **B24**（计划 `:31` 逐字 `NOT BUILT`） |

**这一节不宣称条件 5 关闭。** 它登记的是「上面对应的那几句话不再成立」，而不是
「条件 5 已经满足」—— 条件 5 是**两半**，f 只落了前半；后半（`SavedView` 单纯查询不
创建 Run）的主语仍不存在，它的归属是 B24。所以条件 5 的状态从那一个 `NOT_BUILT` 变成
**前半 `PARTIAL` / 后半 `NOT_BUILT`**，而不是 `BUILT`。

**三处留在原地的**（本节不宣称它们不再成立，本趟逐条复测过）：

* `:261-263`（`ActionItem` 不存在）与 `:269-271`（scheduler 唯一的命中是一句注释）——
  见上表，两处都仍准确。
* `:278` 的第三处负证据（`tests/test_run_window.py:170-171`）仍准确 —— 它判的是域层的
  跨格一致性，与「来源接没接」是两件事。
* 条件 5 那节引的其余结构指针（`what_is_due` 在 `marketops/domain/maintenance.py:612`、
  `ChangeSignal` 在 `:328`、它的生产者在 `marketops/application/contact_routes.py:599`）
  本趟复测**逐条仍在原处**。

### 本节新引的指针，机器核过

上面表格里每一个行号都进了 `B17-MATRIX-CITATION-CHECK.py`（`ARTIFACTS_ROOT` 侧）的核对
表；条件 5 那节新加的**九处行内标记**（`:249`、`:256`、`:260`、`:268`、`:272`、`:275`、
`:279`、`:282`、`:287`）与本节标题（`:526`）也在表里。实测：**这些条目一条 MISS 都没有**。

读数（两趟跑的是**同一份脚本**，只有 `ROOT` 不同；对照树是 `git archive HEAD` 解出的那
一片，在 `f/head-tree/`）：

| 量 | 工作树 | HEAD 树 |
|---|---|---|
| 核对表条目数 | 224 | 224（同一份脚本） |
| MISS | **44** | 118 |

两件事要分开读：

* **工作树上这 44 条 MISS，每一条在 HEAD 树上也 MISS** —— 判据是
  `comm -23 miss-tree.txt miss-head.txt` **为空**（`f/miss-*.txt`）。这同时否掉了两种可能：
  f **没有修好**其中任何一条（修好了它会在这里命中而在 HEAD 上 MISS），也**没有造出**其中
  任何一条（造出来了它会在 HEAD 上命中而在这里 MISS）。44 这个数与补记 C 记下的
  「MISS 44 条」**同值**。
* **74 条在 HEAD 上 MISS、在工作树上命中** —— 全部是**本片自己的指针**（第 11 节、库层
  第 4 节、条件 5 那九处标记、以及本节新引的条目）。它们在 HEAD 上本来就不该命中：那棵树
  上没有这一片的代码与这几段文字。

⇒ **核对表条目数会随补记长大，44 这个数不会。** 下一片要读的是 44 有没有变，不是总数。

**那 44 条不在这里重算**（与补记 C 同一条）：它们指向本文件**补记 B 之前**的正文，重算它们
是「矩阵正文的指针维护」这件独立的事。


## 补记 E：B17-g 之后的读数

本节每一处引用都过了 `B17-MATRIX-CITATION-CHECK.py`（`ARTIFACTS_ROOT` 侧的量具，**不在工作树、
不在 git**）；对照树是 `g/head-tree-f/`（= HEAD `cb402ba`，切片 f 提交的那一刻）。补号由
`g/bump-matrix.py` 落笔（逐行精确替换 + 三条守卫 + 写盘前自证）。

### 一、本片自己的编辑挪走了 54 条指针，全部补号

补号目标分三档，每档的地址都由**实测**定，不手抄：

1. **位移** —— `git diff -U0` 各 hunk 的净增求和。阈值 = `a + max(b, 1)`；`shift(file, L)` =
   `Σ net where L ≥ 阈值`（纯插入的阈值是 `a + 1`）。
2. **整块搬走** —— `marketops/adapters/mcp_server/runs.py` 的 323/331/336 **不在原地插行**，位移
   算出来的 348/353/358 是错的。真地址按「那一行逐字同一行」全文件找，得 897/905/910。
3. **改名级联** —— `marketops/application/run_admission.py` 的三条。本片把 `_require_due` 改名成
   `due_refusal`（判定），另起一个只换类型的 `_require_due` 包装，并把 `_spent_of` / `_spec_of`
   公开成 `spent_of` / `spec_of`。这三条**连必须含的串一起改** —— 否则补了号也命中不了（实测：
   `:228` 那一行今天是 `def due_refusal(`，老串 `def _require_due` 在它上面一个都没有）。

实测（`g/reconcile-moved.txt`、`g/reconcile-after.txt`）：

| 量 | 读数 |
|---|---|
| 本片挪走的条目 | **54**，逐行命中 **54/54**（★ 逐行实测） |
| 仪器复跑 | `exit=1`，**文件真能 import**（不是语法错造出来的假红），行级 MISS 恰好 **44** |
| 与 f 那棵树逐条比 | `comm -12` = 44、`comm -23` = **0**、`comm -13` = **0** |

第三行同时否掉两种可能：本片**没有修好**其中任何一条（修好了它会在这里命中而在 f 那棵树上
MISS），也**没有造出**其中任何一条（造出来了它会在 f 那棵树上命中而在这里 MISS）。核对表条目数
本节落笔**之后**是 232 条 = e5/f 那两段留下的 224 + 本节自己新引的 8 条（`marketops/adapters/mcp_server/runs.py` 的 `:660`/`:760`/`:763`/`:1289`、`tests/test_mcp_runs.py` 的 `:1013`/`:3076`/`:3318`、以及本节标题那一行） —— **这个数会随补记长大，44 不会**；下一片读的是 44 有没有变，不是总数
（与补记 C、D 同一条）。

### 二、本片改过的 9 个文件

`git diff --name-only` 实测：

```text
docs/MCP_PRODUCT_LOOP.md
marketops/adapters/mcp_server/runs.py
marketops/application/__init__.py
marketops/application/run_admission.py
marketops/application/run_increment.py
marketops/security/catalog.py
tests/test_mcp_product_loop.py
tests/test_mcp_runs.py
tests/test_run_admission.py
```

**没被本片碰过的**文件（`marketops/ports/runs.py`、`marketops/domain/run.py`、
`marketops/domain/run_delta.py`、`marketops/domain/maintenance.py`、
`marketops/adapters/sqlite/runs.py`、`marketops/adapters/memory.py`、
`marketops/adapters/mcp_server/product_loop.py`、`marketops/application/dispatch.py`、
`tests/test_run_increment.py`、`tests/test_run_window.py`、`tests/test_run_window_admission.py`、
`tests/test_run_contract.py`…）的号**不可能被本片挪走**，指向它们的引用一律不动 —— 包括 `:545`
引的 `ports/runs.py:242`/`:177`/`:261`/`:273` 与 `product_loop.py:3438`/`:3332`。

### 三、矩阵正文：23 行引用补号（编号 + 短形式 + 旧名）

| 矩阵行 | 旧 | 新 | 依据 |
|---|---|---|---|
| :55 | `marketops/application/run_increment.py:295` | `:341` | 位移 |
| :58 | `run_increment.py:133` | `:134` | 位移（+1） |
| :60 | `run_increment.py:352` | `:455` | 位移（同行 `domain/maintenance.py:923` 未碰，不动） |
| :61 | `run_increment.py:403` | `:499` | 位移 |
| :62 | `:448` | `:544` | 位移 + 主语「上一轮看过」 |
| :64 | `:440` | `:536` | 位移 + 主语「一条能当依据的来源都没有」 |
| :68 | `marketops/security/catalog.py:732` | `:750` | 逐字同文（`"get_run_increment",`） |
| :70 | `security/catalog.py:735-744` | `:753-762` | 两端同文 |
| :133 | `:721` | `:739` | 同文（`"get_run",`；上下文 catalog.py 由 `:132` 点名） |
| :134 | `:732` | `:750` | 同文 |
| :212 | `marketops/adapters/mcp_server/runs.py:196` | `:238` | 同文 + 主语逐字对上 |
| :305 | `application/run_increment.py:289` | `:280` | 位移（**负**：那一段被本片缩短了） |
| :473 | `marketops/security/catalog.py:744` | `:762` | 同文（`"close_run",`） |
| :501 | `:3301` | `:3312` | 核对行的针就是池子断言 `before == (1, (1, 1))`，f 那棵树上绿（**号对、挂错子句**，见第五节） |
| :532 | `:3340-3802` | `:3351-3813` | 两端同文（第 11 节标题 / 节末） |
| :544 | `_spec_of` | `spec_of` | 改名（同行 `run_admission.py:228` 号不变） |
| :546 | `:3342` / `:3346` / `:3347` / `:3597` | `:3353` / `:3357` / `:3358` / `:3608` | 四处同文 + 主语印证 |
| :529 / :544 / :545 / :548 | `_require_due` | `due_refusal` | 改名（`:545` 那一行有两处，都改） |
| :545 | `marketops/adapters/mcp_server/runs.py:323` | `:897` | 整块搬走 |
| :548 | `run_admission.py:297` | `:326` | 改名级联 |
| :550 | `tests/test_mcp_runs.py:3465` | `:3476` | 位移 |
| :550 | `:3496` / `:3494` / `:3538` / `:3568` / `:3660` | `:3507` / `:3505` / `:3549` / `:3579` / `:3671` | 五处同文（SavedView 命中注释） |
| :552 | `tests/test_mcp_runs.py:3698` | `:3709` | 位移 |
| :553 | `tests/test_mcp_runs.py:3592` | `:3603` | 位移 |
| :553 | `:3769` | `:3780` | 同文 |

落笔自证：**23 行变了（计划 23）、行数不变**；守卫一（每条旧 token 在该行出现次数）逐条过，
守卫二（新号由位移函数重算、不是我手抄）逐条一致，守卫三（全表重扫找漏改）无两头都不在的候选。

### 四、指名不改的（登记）

**(a) f 时代的核对行就是红的**（既有漂移，本片之前就不指那一行）：`:77` 的 `:365`、`:85` 的
`:401`、`:79` 的 `:404`/`:487`、`:80` 的 `:900`、`:138` 的 `:960`/`:1619`、`:167` 的
`marketops/adapters/mcp_server/runs.py:251`。

**(b) 无核对行，且主语不落在那一行**（真地址本趟逐条实测）：

| 矩阵行 | 引用 | 那一条的真地址 |
|---|---|---|
| :67 / :319 | `runs.py:363-374` | 真 handler 表在 **HEAD 646-649 / 今天 760-763**；HEAD 363 是 `except (` |
| :136 | `runs.py:426-428` | 同上（HEAD 上就不指 handler 表） |
| :137 / :475 | `runs.py:577-581` | 同上 |
| :474 / :476 | `runs.py:478` | 真 `def close_run` 在 **HEAD 546 / 今天 660** |
| :477 | `runs.py:826` | 真 `RunDelta.model_validate` 在 **HEAD 895 / 今天 1289**（HEAD 826 是散文） |
| :479 | `:983` / `:1660` | HEAD 983 是 `        settings,`、HEAD 1660 是 `updated_at` 那段散文；真引文「盘上那一行已经停了」在 **HEAD 1002 / 今天 1013** |
| :489 | `:3016-3305` | 真第 10 节 **HEAD 3048–3339 / 今天 3059–3350**（两端都偏） |
| :499 | 全形式 `:3101` | 平凡行（HEAD 就是 `[`） |
| :499 | `:3041` | `KEY2` 真在 **HEAD 3073 / 今天 3084** |
| :499 | `:3165` | `_rows(settings) == 2` 真在 **HEAD 3197 / 今天 3208** |
| :499 | `:3174` | `assert after["run_id"] == second` 真在 **HEAD 3206 / 今天 3217** |
| :499 | `:3164` | 「两个键受理出了同一个 id —— 那盘上就只有一轮」真在 **HEAD 3196 / 今天 3207** |
| :500 | `:3027-3037` | 真取舍句「读数由账决定，不由轮次决定」在 **HEAD 3065 / 今天 3076** |
| :501 | 全形式 `:3227` | 平凡行（HEAD 就是 `[]`） |
| :501 | `:3269` / `:3274` | 真读数不变断言 `assert (_rows(settings), _streams(settings, first)) == before` 在 **HEAD 3307 / 今天 3318** |
| :501 | `:3278` | 真工具面 test 定义 `test_admitting_a_round_reserves_nothing_from_the_budget` 在 **HEAD 3310 / 今天 3321** |
| :501 | `:3303` / `:3305` | HEAD 3303 是 `again = _ok(`、3305 是平凡行 `)`；真零行断言见第五节 |
| :502 | 全形式 `:3180` | 平凡行（HEAD 就是 `            []`） |
| :502 | `:3224` | `assert _rows(settings) == 1` 真在 **HEAD 3256 / 今天 3267** |
| :502 | `:3223` | `assert ending["delta"]["recommendations"] == [NEXT_ROUND_NOTE]` 真在 **HEAD 3255 / 今天 3266** |

**(c) 位移为 0、或那一份文件本片没碰过**：`:225`/`:226`/`:277` 的
`tests/test_run_admission.py:340-341` 与 `:170-171`（该文件两个 hunk `@@ -606 +606 @@`、
`@@ -749 +749 @@` 净增均 0 ⇒ 606 行以下位移 0）；`:550` 的短 `:544`/`:564`/`:597`（上下文是
`tests/test_run_admission.py`，同上）；`:551`（`tests/test_run_contract.py` 未碰 +
`tests/test_run_admission.py` 位移 0）；`:552` 的 `run.py` 与 `tests/test_run_window.py`（均未碰）；
`:553` 的 `:280-281`（矩阵自指）。

**(d) 不涉代码行号或为本文件自指**：`:35`、`:231`、`:285`、`:554`、`:258`、`:353`、`:373`、`:374`、
`:563`、`:574`、`:575`。

**(e) 另外三份文档里的旧名/旧号只登记不改**：`docs/B17_F_WATCH_MAINTENANCE_RUN_2026-09-28.md`、
`docs/B17_E5_TWO_ROUNDS_2026-09-28.md`、冻结的 `docs/B17_DESIGN_SLICES_2026-09-28.md`。本片只
编辑**矩阵**（它是 B17 还没关闭时的活仪器）；那三份是历史件与冻结件，差量清单在 g 的落地记录里。

### 五、这份文档自身的两处缺陷（登记，不在本片修）

1. **`:489` 的第 10 节范围在 HEAD 上就已偏**：写的是 `:3016-3305`，而 f 那棵树上的真标题在
   **3048**、节末在 **3339**（第 11 节标题 3340）；今天对应 **3059–3350**。因此它既不是本片推
   走的，也不能靠位移修好。
2. **`:501` 把 `budget_reservation` 零行的证据挂错了行**：它写 `:3301`/`:3303`/`:3305`，而
   `:3301` 是**池子**断言（`assert before == (1, (1, 1))`）、`:3303` 是 `again = _ok(`、`:3305`
   是 `)`。真正的零行断言是 `assert _reservations(settings) == 0` 三条，在 **HEAD 3333/3335/3337
   / 今天 3344/3346/3348**，且三条各红在不同实现上（首次受理、同键重发、另一个键）—— 文档只
   说了「零行」这一件事，把三条判据压成了一条。`:3301` 那一处**号是真的**（核对行的针就是池子
   断言），只是被写进了工具面那半句里。

### 六、判据升级：五处从「补号」翻成「登记」

先行记录的判据是「**逐字同一行** ⇒ 补号即可」。本片换成两条并列：**f 时代核对表那一行的状态是
绿的**（有核对行时以它为准），或**无核对行但逐字同文（非平凡行）且主语印证**。换成这条之后，
五处从补号翻成登记，理由全是实测的「HEAD 上就不指那一行」：`:474`、`:477`、`:479` 的两条、`:500`。

反例是先有的：矩阵 `:78` 引 `tests/test_mcp_runs.py:401`，而 401 那一行在 HEAD 与今天**逐字
相同**，可那一句说的是「断言在 `:401` `_rows(settings) == 1`」—— 指的是**另一个断言**，401 上
那一句跟它没关系。**逐字同文证明的只是「这一行被本片原样搬走了」，不是「它就是文档指的那一行」。**

### 七、量具侧的三处修正（可复用的方法学）

1. **仪器读现表会让 HEAD 绿红判据整片失效**：本片已把 54 条 `(文件, 行号)` 补到新号上，现表里
   那些旧号一律「无核对行」（实测：修之前 `:212` 的 `runs.py:196` 显示「无核对行」，而它在 f 那
   棵树上是绿的）。判 f 时代的绿红**必须读 `g/CHECK.py.before-g-bump`**。另：备份名不以 `.py`
   结尾，`spec_from_file_location` 返回 `None` ⇒ 必须显式给 `SourceFileLoader`。
2. **仪器对短形式的上下文归属会误判，必须人工裁**：`:550` 的 `:544`/`:564`/`:597` 被解析成
   `tests/test_mcp_runs.py`（真实上下文是 `tests/test_run_admission.py`，位移 0 ⇒ 不该改）；
   `:532` 的 `:3340-3802` 被解析成 `run_admission.py`（真实上下文是 `test_mcp_runs.py`）。
   仪器给出的短形式上下文**不可信**，只能当线索。
3. **「逐字同文」是必要不充分 —— 写盘前必须自证**：本片实测踩过：核对表补号那一趟 108 处
   `diff` 全对（54 × 2），文件却是 `SyntaxError`，核对器退回 `exit=1` 的**假红**，看着像验收。
   所以补号量具的写盘前自证是三条：**能被 Python 解析**、**改了恰好计划那么多行**、**总行数
   不变**。矩阵侧再多一条**全表重扫**：每个「本片挪走 + 逐字同文 + 非平凡」的候选，要么在补号
   表里、要么在已裁登记表里，两头都不在就**拒绝写盘**（防漏改，与「恰好一次」防错改是两件事）。

### 八、44 条既有漂移：没有一条能靠位移修好

（已实测复核）它们指向本文件**补记 B 之前**的正文，重算它们是「矩阵正文的指针维护」这件独立的
事（与补记 C、D 同一条）。本片的判据升级之后，**表行与文档引用两侧都不动** —— 直接后果是：
先行记录里曾设想「表行原样保留、而文档侧指向它们的号可补」这个差，在这条判据下**差为零**
（两次实测：`comm -23` = 0、`comm -13` = 0）。这不是巧合：HEAD 上就红的那一条，既不该进表的
新号，也不该进文档的新号。

## 补记 F：B17-h 之后的读数

本片新增 `tests/test_run_concurrency.py`（12 条判据，**既有文件零改动**），落地记录
`docs/B17_H_CONCURRENCY_2026-09-28.md`（**不在本矩阵的正文里**，与 e3/e4/e5/f/g 同一条
处置：每片开一个新文件）。下面只写**影响本矩阵判词**的那几条。

### 一、条件 7：三条缺口逐条处置

| 缺口（原判词在 `:367-380`） | 原判词 | 今天 |
|---|---|---|
| 1. **并发零判据** | 「**没有任何两个写者争一个 Run 的用例。**」 | **关闭。** e3 先落了折入面那一条（`tests/test_run_fold_ledger.py:370`）；本片落十二个：受理面（`tests/test_run_concurrency.py:213` / `:261` / `:292`）、结束面（`:346` / `:380` / `:411`）、折入面（`:450` / `:483` / `:525`）、合并面（`:574` / `:605`）、每一条链自限（`:654`）。十一个变异逐条被杀，红名单在落地记录第三节。 |
| 2. **CAS 零判据** | 「Run 这条链上没有 `expected_version` / `revision` / `epoch` 参与判定」 | **有主语了，判的是语义等价物。** 本层**有意**用「事务内重读 + 内容指纹」（`ports/runs.py:448`）代替版本列（`adapters/sqlite/runs.py:344` 那条 `UPDATE` 的 `WHERE` 只有 `run_id`，`:345`）。等价物今天有判据：`:525`（同一个键必须配同一个值）、`:346`、`:411`。**不引入版本列** —— 那是一次存储语义变更，不在 B17 内。 |
| 3. **「旧 snapshot 不能覆盖新事实」只有一半** | 守卫是内容指纹而非版本；「旧 session」在本层没有对应物 | **补上另一半。** `:411` 判「结束**之前**取到的快照不能盖掉已落盘的产出」（**不用线程** —— 那个快照的**年龄**就是被判的东西）；`:450` 判「结束之后再折 ⇒ 两侧都不许写」。**「session」是一次读法收窄**（读作旧 `Run` 快照），不是「已满足」；要 session 实体本身，是 h 之外的要求。 |
| （缺口 3 末段那句话） | 「『合并』这件事今天**没有主语**」 | 这一句在 e3/e4/e5 之后**已经过时**：`RunDelta` 今天有生产者（结束面）与一份权威读数（`RunStore.folds_of`）。合并规则本身住在 `application/pool.py:285`（`merge_assets`），本片给它配了两条负例：`:574`（证据指针取并集，一个观察都不丢）、`:605`（官网判定冲突要抛，且落败者整笔回滚）。 |

**状态**：`PARTIAL` → **`CLOSED`**（**按两处已登记的读法**）。读法逐字在落地记录第四节：
①「CAS」判的是**语义等价物** —— 本层没有版本列，这不是缺口，是一条被选中的设计；
②「session」读作**旧 `Run` 快照**。判词这样写是为了让这个「关闭」**能被反驳**：若验收人
要的是字面版本列或 session 实体，那是一次**设计变更**，不在 B17 内。正文一个字没删 ——
`:348` 与 `:367-370` 两处加行内标记指向本节。

### 二、条件 4 缺口 3：**关闭**

矩阵 `:236-237` 逐字：「要判它需要一条**并发受理**的用例 —— **切片 h**」。用例有了：
`tests/test_run_concurrency.py:213`（八个自动受理同时打同一条链 ⇒ 恰好一个成功、其余被
`window_refusal` 第二条拒）。执行器是 M1 —— 把那一批行挪到 `BEGIN IMMEDIATE` **之前**读
⇒ `assert 8 == 1`。**「这条判据能被杀」在这里是实测的**：只把那一批行换成**另一条连接**
读、判定仍在写事务里，**不红**（`1 passed`）—— 承重的是**取锁的时刻**，不是用哪条连接读。
两种形状的读数与理由逐字在落地记录第二节第 3 条。

**条件 4 整行仍是 `PARTIAL`**：缺口 1（`RunDelta.usage` 没有生产者 ⇒ 累计预算在真实链上
读到 0）与缺口 2（截止时间那一支结构性不可达）都不在 h 射程内。

### 三、本片不动的东西

* 本矩阵 `:348` 与 `:367-370` 两处只加行内标记，**不删一个字** —— 那两处说的是 e3 之前的
  读数，删了就看不出它被更正过。
* 那 **44 条既有漂移**不动。本片加入自己新引的指针**之前**复跑核对器：`232 条 / MISS 44 条`，
  与 g 那一棵树**逐条同集合**（既不修好、也不造出）。
* 条件 1 缺口 2（`:89-92`，补记 E `:500`）**仍然开着** —— 它是「哪一轮记了哪一格要落盘」，
  与 h 无关。**本片不宣称碰过它**，也不借条件 7 的关闭把它带走。
