# 落地记录 · B17-h —— 并发 / 重放 / CAS 的负例

Item：`TARGET-CONVERGENCE-B17`。工作树
`/home/operator/workspace/worktrees/marketops/target-architecture-convergence-20260927`，
分支同名。前一片是 g（提交 `f913363`，已 push）。

动手之前那一份是 `ARTIFACTS_ROOT/work-items/marketops/TARGET-CONVERGENCE-B17/h/DESIGN-h.md`
（**不在仓库里**，照 e3 的先例：动手之后不再改它）。落地记录只写两样东西 ——
**实际选了什么**与**哪一句落空了**。行号一律是**写这一份那一刻的树**，读到它时按旁边
写的符号名重新定位。

**这一片落的是一个文件**：`tests/test_run_concurrency.py`（新）。既有文件**零改动** ——
`git status` 在本片最后一刻只有那一个新文件（`??`）。原因逐字在设计件第七节第 1 条：
判据优先住新文件，跨面才回既有文件，而这一族没有跨面。

---

## 一、十二个判据（候选表逐条落实）

条件 7 逐字（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:262`）：

> RunDelta 合并对并发/重放/CAS有负例；旧 session 不能用旧 snapshot 覆盖新事实。

切片表那一行（`docs/B17_DESIGN_SLICES_2026-09-28.md:58`）把这一条指给 h，**没有新增
要求** —— 它要的只是「有负例」。候选表 → 落地文件的对应与实测（全部**绿**）：

| # | 判据（`tests/test_run_concurrency.py`） | 判哪一句 | 实测 |
|---|---|---|---|
| C1 | `:213` `test_only_one_auto_run_can_open_this_chain_at_once` | 受理面的并发上限（条件 4「不创建下一 Run」的并发形态） | 绿 |
| C2 | `:261` `test_the_same_key_submitted_at_once_admits_one_run` | 同键同规格并发 ⇒ 一行、**一本空账** | 绿 |
| C3 | `:292` `test_the_same_key_with_two_specs_admits_exactly_one` | 同键**异**规格并发 ⇒ 恰好一份规格赢 | 绿（**首跑红**，见下） |
| C4 | `:346` `test_two_different_endings_arriving_at_once_leave_one_on_disk` | 结束面的并发（一份产出） | 绿 |
| C5 | `:380` `test_two_identical_endings_arriving_at_once_are_one_replay` | C4 的反面：重放不是第二次结束 | 绿 |
| C6 | `:411` `test_a_snapshot_taken_before_the_end_cannot_overwrite_it` | 条件 7 第三半（旧快照不许盖新事实） | 绿 |
| C7 | `:450` `test_a_fold_that_arrives_after_the_end_writes_neither_side` | 结束之后再折 ⇒ **两侧都不许写** | 绿 |
| C8 | `:483` `test_folds_arriving_at_once_all_get_their_own_seq` | 折入账 `seq` 由事务自己数 | 绿 |
| C9 | —— （**不重写**） | 并发折同回执行同句柄 | 引用 `tests/test_run_fold_ledger.py:370` |
| C10 | `:525` `test_two_handles_for_one_receipt_row_leave_one_entry` | 账本的 CAS 等价物：同一个键必须配同一个值 | 绿 |
| C11a | `:574` `test_two_writers_on_one_row_keep_both_observations` | 证据指针取并集（一个观察都不丢） | 绿 |
| C11b | `:605` `test_two_verdicts_on_one_row_are_refused_rather_than_merged` | 官网判定冲突要抛（唯一有主语的那一处） | 绿 |
| C12 | `:654` `test_two_chains_admitting_at_once_do_not_block_each_other` | 上限**只属于每一条链自己** | 绿（**换过**，见下） |

两条要单独说的：

**C3 首跑是红的，红的是判据自己，不是实现。** 第一次写成「八次里四份规格 A、四份规格 B
⇒ 恰好一份成功」，跑出 `assert 4 == 1`。根因是**契约**：拿到**赢的那一份**的四个 worker
是**重放**，按契约本来就该成功（条件 1 的「重复提交同一 idempotency key 不重复池子或
成本」）。判据改成了三句 —— 「恰好一个指纹赢了」「赢的那些拿的是同一个 `run_id`」
「盘上那一行是赢的那份规格（`spec_sha256` 与 `objective` 逐字）」，并且**加了一句反向的
印子**：输的四个拿的确实是**另一个**指纹。设计件里 C3 那句「盘上留的是先落的那一份」
因此是**不精确的**（留的是**赢的那一份**；而且按契约，那一行永远不会被改）。

**C12 是替换进来的，不是候选表里那条。** 候选表 C12 原本写的是「受理与结束同时到达的
不变量」，落笔时发现**它没有能杀它的变异** —— 我为它编的那个红形态在实现里不存在。
照 e3「落地时实际选的第四条：不动」的先例，这一处替换登记在这里：换成
``test_two_chains_admitting_at_once_do_not_block_each_other``（八条链互不阻挡），它的红
形态**是真的**（M7，下表），而且它判的东西是候选表里别处没有的：**`progress_of` 的
摘要筛选**（`ports/runs.py:121` 起那句 `window is None or window.digest != window_digest
⇒ continue`）在**适配器喂进去的行**上依然成立。纯函数那一侧已经有
`tests/test_run_window_admission.py::test_only_the_named_chain_is_counted` 钉着同一句，
这一条**不重写它** —— 它判的是实现按 `workspace` 取行（**不按摘要**）之后，筛选是不是
真的落在那一处函数里。

**C9 不重写**：并发折同一 `(receipt_id, ordinal)` **同句柄**那一条 e3 已经落了
（`tests/test_run_fold_ledger.py:370`），它在候选表里只作交叉引用。本片新增的是**异
句柄**那一半（C10）—— 两条理由不同，见那一格自己的 docstring。

---

## 二、两条量具的 bug，与一条更重要的**负面发现**

这一片的价值一半在判据，另一半在这三处 —— 前两条是**我自己量具的 bug**，第三条是
**实测推翻了设计件的一处解释**。

### 1. 还原的字节比较写在写回**之前**（`MUTATION-H.py::_restore`）

第一版是：

```python
if path.read_bytes() != before:   # ← 此刻文件还是被变异过的样子
    broken.append(rel)
path.write_bytes(before)          # ← 写回在比较之后
```

于是「还原之后字节不等」对**每一个**变异都成立 —— 一条恒定报警的假量具。它比不报警更
危险：它让「11 个变异全部还原失败」看起来像一个真结论。顺序反过来（先写回、再比）之后，
**没有一个变异报还原失败**。

### 2. 同一个文件被改两处时，`originals[rel]` 被**覆盖**成中间态

```python
originals[rel] = before           # ← 第二次读到的「原文」已经带着第一处编辑
```

还原写回的是那个中间态，于是**第一处编辑永远留在盘上**。实测到的残渣是 8 行
（`sqlite/runs.py` 里第一版 M1 那个 `_rows_outside_the_transaction`）。同尺寸同秒的残渣
不会报错（记忆 `pyc-cache-defeats-same-size-mutation-reverts` 记的是同一类骗局），它只会
让**后面每一个变异都跑在一份脏基线上**。改成 `setdefault` 之后，两次全量跑都在**干净的
树**上进行，收尾时 `git status` 只剩那个未跟踪的新文件。

⇒ 这两条合起来是一条可复用的纪律：**变异驱动的还原是一台仪器，它自己的读数要先被怀疑**
（与 g 第七节第 3 条同一条）。

### 3. **「只换一条连接读」不红** —— 设计件对 M1 的解释落空了一半

设计件把 M1 写成「把窗口那三条判定**移出写事务**（在 `with ... write_transaction` 之前
读一次 `chain_progress`）」，它的实现第一版却是「判定仍在事务里，只把那一批行换成**另一
条连接**读」。那一版 **`exit=0`、红名单为空** —— 一条判据都没红。

是解释还是读数？`h/M1-DIAG.py` 把两种形状各跑一遍（结果逐字）：

* **形状 A**（判定在事务里，只换连接读）：`1 passed` ⇒ **绿**；
* **形状 B**（把那一批行挪到 `BEGIN IMMEDIATE` **之前**读）：`assert 8 == 1` ⇒ 八个全过。

所以：**承重的是取锁的时刻，不是用哪条连接读。** 形状 A 里那次「另一条连接」的读发生在
`BEGIN IMMEDIATE` 成功**之后** —— 也就是前一个写者提交之后 —— 它读得到那一行。
`adapters/sqlite/runs.py:246` 那段注释说「它在事务外读不到并发」，**主句是对的**（「事务
外」= 拿锁之前），而把「事务外」理解成「换一条连接」是**错的读法**；这一处现在由 C1 的
docstring 逐字承担（我在落地时按读数改写了那段 docstring，见第七节）。

这一条也是条件 4 缺口 3 的执行器 —— 矩阵 `:236-237` 逐字写着「要判它需要一条**并发受理**
的用例 —— **切片 h**」。**它现在有了**：C1 判它，M1（形状 B）杀它。

---

## 三、变异控制：11 个变异全部被杀

驱动器 `h/MUTATION-H.py`（**不在仓库里**）。纪律照本仓：每次变异后清 `__pycache__`；
「`exit == 1` 且红名单非空」是**必要**条件不是充分条件，判的是**期望的那一条在红名单
里**；红名单**不截断**，多红的那些一并记下来（它们说明这个变异还打到了哪里）。

| 变异 | 打在哪 | 期望 | 实测红名单 |
|---|---|---|---|
| **M1** | `sqlite/runs.py::admit` 把那一批行挪到 `BEGIN IMMEDIATE` 之前读 | C1 | **C1** |
| M2 | `sqlite/runs.py::stop` 把「读那一行」拆到写事务之外 | C4 | **C4** |
| M3 | `sqlite/runs.py::record_fold` 的重放判定改成按**句柄**比 | C10 | **C10** |
| M4 | `sqlite/pool.py::upsert_asset` 把记账挪到写事务之外 | C7 | **C7 + C10**（+ C8，见下） |
| M5 | `ports/runs.py::window_refusal` 第二条（`open_runs`）关掉 | C1 | **C1** |
| M6 | `application/pool.py::merge_assets` 证据指针改成后写覆盖 | C11a | **C11a** |
| M7 | `ports/runs.py::progress_of` 的摘要筛选删掉 | C12 | **C12** |
| M8 | `sqlite/runs.py::record_fold` 的 `seq` 恒写 0 | C8 | **C8** |
| M9 | `application/pool.py::_merge_domain` 的冲突不再抛 | C11b | **C11b** |
| M10 | `sqlite/runs.py::_already_ended` 一律读成重放 | C4 | **C4 + C6** |
| M11 | `sqlite/runs.py::record_fold` 的「这一轮结束了没有」关掉 | C7 | **C7** |

**M1 是这一片必须有的那一条**：它是条件 4 缺口 3 的执行器，e2b 的变异清单里**故意**没为
它设一条（理由逐字是「因为没有执行器」）。它的形状**按第二节第 3 条实测纠正过**才成立。

**M4 的红名单是调度相关的**（实测：同一个变异连跑五次，C7 与 C10 **五次都在**，C8 **三次
在、两次不在**）。它连带红 C8 是有理由的 —— 记账被挪出写事务之后，那两笔 `seq` 各自在
自己的读里数最大值 —— 但**在不在红名单里取决于调度**。所以它算 **C7 的杀**（C7 五次都
红），而不是 C8 的杀；C8 的主判据是 M8。**一处红同时被两个变异触发时要说清哪一个是主
判据** —— 设计件第四节要求的那一句，在这里落实。

**M10 连带红 C6** 也是真的：把「已经结束过」一律读成重放之后，C6 里那半「拿**同一套**产出
再来是重放」仍然绿，而「拿**旧产出**来」那半 —— 旧快照不许盖新事实 —— 也绿不了。

---

## 四、三处「没有对象」的登记（h 不造）

### 1. 「旧 session 不能用旧 snapshot 覆盖新事实」——「session」在本层没有对应物

Run 这一层最近的操作单位是 **Run**（`RunStore` 的五个方法都以 `run_id` 为主键），没有
session 这个对象，也没有它的生命周期。所以这句话在本层**读作**：一个持有**旧 `Run` 快照**
的写者，不能把它的旧结局盖到已经落盘的新事实上 —— 那正是 C6 与 C7 判的。C6 **不用线程**：
那个快照的**年龄**就是被判的东西，而在两个线程里造它反而会让判据看起来像在判调度。

**这是一次读法的收窄，不是「已满足」。** 若验收人要的是 session 实体本身，那是一条
**h 之外**的要求，h 不冒充。

### 2. 「CAS」——Run 这条链上没有版本列，h **不引入**

`ports/jobs.py:237` 的 `JobStore.set_state(job_id, state, *, expected: JobState, ...)` 是
本仓**真正的 CAS 先例**。Run 这条链**有意**用「事务内重读 + 内容指纹」
（`stopping_outcome`，`ports/runs.py:448`）代替它：`sqlite/runs.py:344` 那条 `UPDATE` 的
`WHERE` **只有 `run_id`**（`:345`），**没有版本列**。

h 判的是**这门 CAS 的语义等价物**，并且它今天**有主语**：C10 逐字写着「同一个键必须配
同一个值」（账本按 `(run_id, receipt_id, ordinal)` 判重放，而句柄不许当那个键 —— 一份
回执里两行可以折进同一块场地）。**本层没有版本列，这不是缺口，是一条被选中的设计**；
要字面的版本列，那是一次存储语义变更，要单独批准。

### 3. 池子 `merge_assets` 的「非空即赢」是**业务语义裁决**，不在 h 的授权内

`_NON_EMPTY_WINS_FIELDS`（`application/pool.py:115`）里 `name` / `address` / `notes` /
`claimed_official_domain` / `discovered_url` / `candidate_domain` 六格是「非空赢」：一次
晚到的、带旧内容的写**可以**改回这些格子。这与条件 7 第三半字面冲突，但它是**池子的合并
语义**（谁有权改一块场地的名字），属已批准的业务语义。

h **只配判据、不放宽也不收紧**：C11a 判「证据指针不丢」（`ordered_union`，
`application/pool.py:338`），C11b 判「官网判定冲突要抛」（`_merge_domain`，
`application/pool.py:343` 起，`raise` 在 `:369`）—— 两条都是今天已经成立的规则。
「`name` 那一类格子谁赢」**不在 h 的授权内**，要改是一次业务裁决，归属 B01/B02 或更上游。

---

## 五、两处必须登记的更正（h 不回头改正文）

### 1. 矩阵 `:348` / `:366` 那两句，在 e3 之后已不成立

矩阵正文 `:348` 逐字：「**并发 —— 没有。** **CAS —— Run 这条链上没有。**」；`:367` 起
那一格逐字：「**并发零判据**：……**没有任何两个写者争一个 Run 的用例。**」

e3 落了 `tests/test_run_fold_ledger.py:370`
（`test_two_connections_folding_the_same_receipt_row_write_one_entry`，两个线程同时折同一
行 ⇒ 一笔账、一行池子），本片又落了十二个判据 ⇒ **这两句今天都不成立**。h **不删正文一个
字**，在 `:348` 与 `:370` 两处加行内标记指向文末「补记 F」（补记 D/E 的处置同一条）。
两处标记都是**行内追加**，一行都没有增 —— 增行会把核对表里指向本矩阵的条目整体推移，
而那属于「矩阵正文的指针维护」这件独立的事。

### 2. 设计件自己有一处自相矛盾：`CAS` 那一行说「对象由 h 造」

`DESIGN-h.md` 第〇节那张表里，CAS 一行的「缺什么」写的是「对象与判据」、归口写的是
「h 写判据；**对象由 h 造**（见第五节第 2 条）」；而它第五节第 2 条的结论是 **h 不引入
版本列**，并且逐字写着「本层没有版本列，这不是缺口」。**两处不能同时成立**，落地时按
第五节执行（也就是**不造对象**）。这一处不是「设计改了」，是那份文件里本来就有的矛盾 ——
它只在动手之后才看得清，所以记在这里。

---

## 六、实测读数（都是跑出来的，不是估的）

* **新判据单跑**：`12 passed`，连跑十次无一例外（1.73s–2.51s）。随机顺序与固定顺序都跑过。
* **全量套件**：`6292 passed, 4 skipped in 432.98s`，`EXIT=0`。上一片 g 的读数是
  `6280 passed`，**差的正是这 12 条**。本片落地记录落笔之后再跑一次（照 g 的先例，
  为的是确认这份记录本身没有动到盘上的东西）：`6292 passed, 4 skipped in 418.61s`，
  `EXIT=0`（日志 `suite-h-final.log`）。两次读数逐位相同，只有墙钟不同。
* **`ruff check .`**：`All checks passed!`。（本仓**从不**跑 `ruff format`。）
* **变异控制**：11/11 被杀；红名单逐条见第三节；**没有一个变异报还原失败**，
  收尾时 `git status` 只有那一个未跟踪的新文件。
* **指针核对器**（`ARTIFACTS_ROOT` 侧的 `B17-MATRIX-CITATION-CHECK.py`）：本片加入自己新引
  的指针**之前**，复跑 `232 条 / MISS 44 条` —— 与 g 那一棵树**逐条同集合**。加入本片新引
  的 14 条之后：`246 条 / MISS 44 条`（新引的全绿）。本片不修那 44 条（它们是「矩阵正文的
  指针维护」这件独立的事），也不造新的 —— 所以 MISS 一动不动。
* **矩阵自身**：正文 780 行**未变**（两处行内标记是行内追加，没有增行），补记 F 追加在
  文末，`sed` 复核「补记 E」标题仍在 `:602`（核对表里指向它的那一条因此仍然成立）。

---

## 七、差量台账（设计件说过的，与实际做的不一样的地方）

1. **C12 被替换**（候选表那条「受理与结束同时到达的不变量」→「两条链互不阻挡」）：
   理由与纠正见第一节。
2. **C1 的 docstring 按实测改写过**：原文把红形态解释成「`chain_progress` 走自己那条连接、
   看不见未提交的行」，实测证明**那种形状是绿的**；改后逐字写明两种形状与各自的读数。
   模块 docstring 末尾那一句同改。
3. **M1 的形状按实测纠正过**（第一版换成「另一条连接读」⇒ 不红；改成「挪到拿锁之前」
   ⇒ 杀）。这是这一片最值钱的一条负面发现，见第二节第 3 条。
4. **`MUTATION-H.py` 自己修了两处量具 bug**（还原的比较顺序、`originals` 的覆盖），
   见第二节第 1、2 条。
5. **「条件 1 缺口 2」这个说法在推进中被记错过一次**。按矩阵与补记 E 的编号，
   `window_refusal` 必须在写事务里调用是**条件 4 的缺口 3**（矩阵 `:236-237`，与切片 h
   同归）；**条件 1 的缺口 2** 是 `plan_increment` 读不到「第一轮已沉淀的事实」
   （矩阵 `:89-92`，补记 E `:500`），**它与 h 无关、仍然开着**。h **不宣称**碰过它。
6. **h 不关闭条件 1、也不关闭条件 2/3/5/6**：本片只与条件 4 的缺口 3、条件 7 有关。
