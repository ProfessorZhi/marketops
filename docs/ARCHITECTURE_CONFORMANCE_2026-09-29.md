# 架构一致性矩阵 · 2026-09-29

执行计划 §G4 逐字要求：「验收报告必须生成一张 `architecture_conformance_matrix`」，
列固定为 `producer / store / consumer / MCP surface / auth / test / live evidence / status`。

这份文件是 §23 的产物，**在 G4 跑过之后生成**（§5.7 逐字：先写会写出一批未实测的
格子）。

## 读数条件（**每一条数字都受这一节约束**）

| | |
|---|---|
| 被测的树 | **`1764ae17`**（`feat(b15-b25): 目标架构收敛收口 …`） |
| 护栏（全量跑前 / 跑后 / 各道门之后） | `git status --porcelain marketops/ tests/` = **0 / 0 / 0** 行 |
| 上面那个 HEAD 的树**干不干净** | 干净（提交后 `git status --porcelain` 为空） |
| §21 最终验证链 | `final-validate-1764ae17.out`（`EXIT=0`），`gates` 段的修复版见 `final-validate-1764ae17-gates2.out` |
| 解释器 | `/home/operator/toolchains/marketops-mvp/bin/python` |

**这份文件提交之后 HEAD 就不是 `1764ae17` 了，而那不使下面任何一条失效**：每一条
读数的意思都是「在 `1764ae17` 这棵树上取到的」，它可被独立复跑。

### §21 最终验证链的完整读数（`1764ae17`）

| 段 | 读数 |
|---|---|
| 全量套件 | **7849 passed, 4 skipped**（734.47s） |
| `ruff check .` | All checks passed |
| `python -m marketops.schema` 重生成 | 14 个 schema 写出；护栏跑后 0 行 ⇒ 与 committed **逐字节一致** |
| G0（§5.2） | **15 passed** |
| G1'（§5.3；`LEGACY_R1_VERIFIED`） | **8 passed** |
| G2'（§5.4） | **30 passed** |
| R2（§5.4；`LEGACY_R2_VERIFIED`） | **11 passed** |

### G4 各条在最终组合根上的点名读数

| 批次 | 读数 |
|---|---|
| G4 点名的十一族判据（安全/身份/出口/预算/Run 受理/窗口/Watch/反馈五种/cohort/插件无权限） | **788 passed**（47.14s，护栏跑前跑后各 0 行） |
| 组合根探针（走真 `_compose` + 真协议） | 目录 **93 条工具**；`backed=95 / not_registered=3 / stubbed=0`；§B01/B02 要接的七条 **backed 7/7**；§B25 那八条 **backed 8/8** |
| §2.59 全表按终 HEAD 重跑（七条的承重判据） | **640 passed**（115.55s，护栏跑前跑后各 0 行） |
| §2.59 行 1 与行 4 的**变异**（`MAIN/mutation-259/`） | 见下面「变异」一节 |

### 变异（§5.5 纪律 #4 要求的那个量具）

§2.59 那张表里**只有行 1 与行 4** 标了「承 §2.40，终 HEAD 需重跑」。重跑的可执行
形式不是「它又绿了一次」（判据会因别的原因绿），而是**造一个违反它的世界**：

| 变异 | 动了什么 | 读数 |
|---|---|---|
| a（行 1「可复现 digest」） | `research_plan.py` 的 `digest` 里掺一个不属于内容的量（`self.scene_version` → `+ str(id(self))`） | **`EXIT=1`，`1 failed, 44 passed`**，红：`test_the_same_market_and_the_same_task_compile_to_the_same_plan` |
| b（行 4「硬的一半是八维」） | 从 `_HARD_AMBIGUITY_DIMENSIONS` 摘掉 `JURISDICTION` | **`EXIT=1`，`4 failed, 111 passed`**，红含 `test_the_half_of_the_table_is_the_eight_dimensions`（枚举式那条）与 `test_the_two_ambiguity_tables_partition_the_closed_set` |

两次都**双 sha256 闭合**：`research_plan.py` `cc9e6feb…` → 变异 `0ef09a3a…` → 还原
`cc9e6feb…`；`research_context.py` `66f17d85…` → 变异 `32b9defd…` → 还原 `66f17d85…`。
每次变异后清 `__pycache__`（同尺寸同秒的还原会被 pyc 校验骗过）。

---

## 两条判定纪律（§G4 逐字）

* 「**只有类名/schema 而没有生产调用者的格子不是完成**」—— 本矩阵里凡只有 class /
  schema 的格，`status` 记 `NOT DONE`。
* 「**某概念刻意仅为逻辑投影时也要点名其实际承载位置**」—— 本矩阵里有**两处**，
  在下面「三、刻意仅为逻辑投影的两格」里逐一点名。

### 五条从本轮施工里长出来的纪律（每条带一个真错）

**纪律一：零命中只否证那个名字，不否证那件事。** 要判「某能力不存在」，必须先写下
它的**语义**，再去读有没有人在算它。只报 `grep` 的零，等于把一个量具的输出当成了一条
世界的事实。**实例**：B16 条件 3 后半，我按 `translator|def translate` 搜得 0 命中，
就写了「那条路还不存在」—— 而语义是「有没有人在把内容换成另一种语言」，那份 0 只否证
了那三个名字。

**纪律二：宽集命中要先甄别，才能拿来否证。** 窄集 0 命中不足以写进产物；真正的形状是
**两个集都跑、并把同形异义词逐条点掉**，于是「不存在」这个结论**带着它的反例一起**
写下来。**实例**：同一条，把名字放宽到 `translate|translator|translation|本地化|机器翻译|译文`
得 **11 命中**，逐条读过 —— 全部是**异常→ToolError 的转译**（`runs.py` 的 `_translate`）
与 `str.translate` 折破折号。若只报「0 命中」，下一个人用 `translate` 一搜会以为我说错了。

**纪律三：判「不存在」时，理由若是一条「那条路没被调用」的读数，它就必须带一个复核
触发点** —— 因为调用者会加，而判词不会自己变红。**实例**：§2.16 → 本矩阵 G4 #7 那格
的 `Watch → Run` 面：判词落在一个**后来变了的世界**上，而它自己不会响。

**纪律四：判一条约束的强弱，唯一有效的量具是「违反它的世界，哪条判据会红」。**
不是「有没有人写过它」，不是「有没有一个名字与它同名」，也不是「另一份文档怎么概括
它」。**实例**：B16 条件 3 后半与条件 5 第三半各一次 —— 前者造出「有人加了一个就地改写
原文的翻译器」⇒ `test_the_literal_is_actually_in_the_source_text` 红；后者造出「词表与
两条投影一起长」⇒ **三条正证据全绿**，于是矩阵原来那句「而那一步会红」被实测推翻并
订正。

**纪律五：「因缺席而成立」是一个结论，不是一个理由。** 把它当理由用，会让两格完全不同
的问题（一格有执行器、一格连反例都造不出来）共用同一个判词。**实例**：§2.40 的条件 3
（有执行器，只是没人加翻译器）与条件 5 的第一半（运行期根本没有可变的排序，**造不出
反例**）—— 前者记 `DEMONSTRATED`，后者只能记「结构性成立」。

---

## 矩阵

`status` 四个取值：`DEMONSTRATED` / `PARTIAL` / `NOT DONE` / `BLOCKED_EXTERNAL`。

### 1 · 一个逻辑 MarketOps MCP，按角色分面

| 列 | 内容 |
|---|---|
| producer | `interfaces/mcp/__main__.py::_compose`（唯一的组合根）→ `build_server` |
| store | —（这一格是**接线**，不是存储） |
| consumer | Member-A / controller / Quay worker 三类主体，同一服务、不同角色工具面 |
| MCP surface | `tools/list` 上 **93 条工具**；探针 `backed=95 / not_registered=3 / stubbed=0` |
| auth | `security/catalog.py::authorize(principal, name)`；`_CONTROL` 与 `_AUTHORIZE` 分开；角色三值 `adapters/mcp_server/server.py:135-146` |
| test | `tests/test_security_catalog.py`、`tests/test_security_identity.py`、`tests/test_cli_mint_token.py`（含在 788 里）；组合根探针 |
| live evidence | `MAIN/g4-final/probe-1764ae17.out`（走真 `_compose` + 真 Streamable HTTP 协议） |
| **status** | **`DEMONSTRATED`** |

**不许读大**：Serper / Crawl4AI / Jev / Quay **不是**各自独立的业务 MCP —— 它们在这一
服务里是 provider（`ports/` 一族），没有各自的 `tools/list`。探针的 `not_registered=3`
是**目录里有、这一部署没接**的三条，不是三个 MCP。

### 2 · 一个共享业务真源

| 列 | 内容 |
|---|---|
| producer | 各 `application/` 用例（受理、折入、提案、点火……） |
| store | `adapters/sqlite/` 一族（`db.py` 的 schema + 十一座 store） |
| consumer | 全部工具处理器；**客户端文件、聊天 memory、Skill 都不是权威** |
| MCP surface | 每条工具各自的读/写面（`security/catalog.py` 是那个闭集） |
| auth | 同上（按工具名授权） |
| test | `tests/test_*_store_contract.py` 一族（十四个契约文件）；全量套件 7849 条 |
| live evidence | `contracts/*.schema.json` 重生成后与 committed **逐字节一致** |
| **status** | **`DEMONSTRATED`** |

**同一条注**：本格与第 6 条（Feedback Loop）共用一处接线读数（见下面「二、不许数两遍
的两处」）。

### 3 · Scene-aware routing 已真实消费

| 列 | 内容 |
|---|---|
| producer | `ResearchContext` → `EffectiveResearchPlan`（`domain/research_plan.py`）；生产者 `application/workstream.py` / `effective_scene.py` |
| store | `scene_snapshot` **按 `scene_version` 连字节存**（ND-2）；`workstream` / `effective_scene` 两张表 |
| consumer | provider/worker 调用；业务对象反向可追溯 |
| MCP surface | `start_workstream` / `get_workstream` / `list_workstreams` / `control_workstream` / `preview_effective_scene` / `get_effective_scene` / `withdraw_claim` —— **backed 7/7** |
| auth | `_CONTROL` 面；建 Workstream 要权限 |
| test | **G0 纵向验收：`15 passed`**（走真 `_compose` + `mcp_harness.drive` 真协议，`drive_with_settings` 那几格一个都没注入） |
| live evidence | 组合根探针（七条 backed）；G0 每句要紧的话都在 `research_run` / `launch_record` / `workstream` / `effective_scene` / `market_account` / `budget_ceiling` / `budget_reservation` 的行数或行内容上**再判一次** |
| **status** | **`DEMONSTRATED`** |

**这一格本轮变过**：17:5x 时它记 `PARTIAL`（修复在途，`a782e65cfd809ca29`）。那处修复
（「撤回的被引说法在写稿那条路上不再算活的批准出处」）**已随 `1764ae17` 落地**，G0 里
的判据按**三种处置分开**判（`missing` / `awaiting_approval` 里一个被撤的字都不能有，
`invalid` **恰好**一条）。⇒ 按读数改判，**不是**按结论继承。

### 4 · PR #8 的 R1/R2 能力没有回退

| 列 | 内容 |
|---|---|
| producer | identity / route / cohort / draft / Jev lineage / Review / continuation / real refresh 各自的用例 |
| store | 各自 store |
| consumer | 各自的工具面 |
| MCP surface | 各条工具 |
| auth | 各自 |
| test | **G1'：`8 passed`**；**R2：`11 passed`**（两道各自具名的门） |
| live evidence | `final-validate-1764ae17.out` 与 `-gates2.out`；切片 I 交回的八条必须失败的对照**条条有承载点、条条红过一次** |
| **status** | **`DEMONSTRATED`** |

`LEGACY_R1_VERIFIED` 与 `LEGACY_R2_VERIFIED` **各要一个 source SHA**，所以这两道门在
`final_validate.sh` 里各占一段。R2 那一段此前只在全量套件里跑过、没有被当成一道具名的门。

**一处必须写下来的缺陷（已修）**：`final_validate.sh:60` 的 echo 里用了**反引号**包
`LEGACY_R2_VERIFIED`，bash 在双引号内照样做命令替换 —— 那一次跑在输出里打了
`LEGACY_R2_VERIFIED: command not found`，于是**一个必须逐字交付的字段名恰好从报告上
消失**。测试读数不受影响（R2 照跑 11 条），所以它是一处**只在打印上出错**的缺陷。
2026-09-29 实测到并修掉（`final-validate-1764ae17.out:80-81` 是修复前的读数）。

### 5 · 增量 Run 与自动化边界

| 列 | 内容 |
|---|---|
| producer | `application/run_admission.py`（`admit_run` 的模式/窗口校验）、`application/scheduling.py` |
| store | `research_run`、`schedule_fire`、`run_fold_ledger` |
| consumer | `close_run`（账平了才停表）；`interfaces/trigger_driver.py`（**一个进程**，不是一条工具） |
| MCP surface | `submit_run` / `preview_run` / `get_run` / `close_run` / `reconcile_run_delta` |
| auth | `_CONTROL` |
| test | `tests/test_run_admission.py`、`tests/test_run_window_admission.py`、`tests/test_watch_runs.py`（含在 788 里）；G2' `30 passed` |
| live evidence | §2.26 探针 `MAIN/wire-probe/scheduling_replay.py` |
| **status** | **`DEMONSTRATED`**（附一条**必须记下的边界**） |

**`manual` 是被拒不是被降级**：`execution_mode='manual'` 走 `admit_run` 时明确拒绝，
不是静默改成需要授权的模式。

**同一条注**：`same fire replay` 的**两半守卫强度不一样** —— `no new Run` 有**三道**
（幂等键 + 版本 CAS + §B17 串行），而 `same result` **只有幂等键在扛**（摘掉它，重放那
一次就报错）。两半都成立，但**强的只有前半**，不许写成「重放两半等价」。

### 6 · Member-A Feedback Loop

| 列 | 内容 |
|---|---|
| producer | `propose_policy_patch`（收 `rollback`）、fact correction、`ExperienceCandidate` |
| store | 补丁行（`PolicyPatchState`）、评审案 |
| consumer | `list_policy_patches`（**两格分开报**：已生效那版带 `rollback`、提案自己写的在 `declared_rollback`）；`_application_of`（**它想产生的那一版在不在盘上、是不是当前生效版**） |
| MCP surface | 反馈那一族；`security/catalog.py` **零个回滚动词** |
| auth | `_CONTROL` |
| test | `tests/test_application_feedback.py`、`tests/test_mcp_feedback.py`、`tests/test_mcp_feedback_is_offline.py`、`tests/test_feedback_preview.py`、`tests/test_feedback_domain.py`（含在 788 里） |
| live evidence | 788 那一批 |
| **status** | **`DEMONSTRATED`**（其中 `rollback` 属「刻意仅为逻辑投影」，见下面「三」） |

`scenes is None` 时那一档如实报 `unreadable`，**不是** `not_produced` —— 「没接线」与
「还没产版」是两句话。

### 7 · Watch / Action / Schedule / Notification

| 列 | 内容 |
|---|---|
| producer | `SavedView` **纯读**；`Watch` 产生 due work；`Action` 承载下一步；**一个 authoritative scheduler** = `interfaces/trigger_driver.py`（`a15e0f5`） |
| store | `views` / `watch` / `action_item` / `schedule` / `notification` 各表 |
| consumer | `followups_handlers` 的 `items` 与**结束面共用同一个闭包**；一次 event **可投影多 sink** |
| MCP surface | `run_saved_view` / `put_watch` / `list_watches` / `open_action_item` / `list_action_items` / `resolve_action_item` / 调度的八条 |
| auth | `_CONTROL`；`open_action_item` **要权限，但不花预算、不建 Run** |
| test | `tests/test_mcp_views_are_offline.py`（构造面三条 + 三条否定各配一条反面）、`tests/test_views_application.py`、`tests/test_schedule_action.py`、`tests/test_calendar_rule.py`；**G2' `30 passed`** |
| live evidence | §2.19 端到端探针：一次点火开出一行 Run |
| **status** | **`PARTIAL`** |

**这一格本轮变过（两处）**：

* `next_due_at` 恒 `None` 导致「按 Schedule **持续**自动执行」不成立 —— 已判为**可修**并
  随 `1764ae17` 落地（`domain/calendar_rule.py` + `adapters/mcp_server/schedule_action.py`）。
  所以本格**不记 `NOT DONE`**。
* B24 条件 4 的 **Action 半边**（`RunDelta.actions` 与它的生产者）本轮落地：id **随机**
  （`new_action_id()`，理由：内容寻址在第一次做完之后物理上写不出第二件），幂等的判据
  住在 `(workspace, action_type, scope_ref)` 那条 **partial unique index** 上（不是
  「写之前先查一下」），次序是「开待办在停表之前」。

**仍然开着的**：**`Watch` → Run 面**记 **`NOT DONE`**，且它要的是**产品决定**
（缺的是产品级的意图来源 —— `ResearchContext` 由 Member-A/客户端给出，而 `Watch` 的四个
字段都不是「要什么」），**不是**存储或场景号。**G2' 的报告与本矩阵都不许**把这半读成
「Watch 自动执行已通过」（B17 条件 5 逐字要的是 **Run**）。另：G2' **自陈只判了三类时间
的第一类**（即时重复 tick），另两类的判据**没有**。

**一条承载位置的点名**：「一个 event 可投影多 sink 但不重执行业务」的承载点是
**驱动**（`interfaces/trigger_driver.py::deployment_sinks`），**不是服务面** —— 一次投递
是「醒来 → 投出去 → 退出」那条路上的一步。本部署今天注册**一个**出口（本机收件箱
`InboxSink`），而它是唯一一个 `accepted` **证明得出来**的出口（判据是那一行此刻在盘上
且读得回来）。Feishu 那一格是 §20 定下的 `BLOCKED_EXTERNAL`，**明说不是试运行的 blocker**。

**「维护面」与「Run 面」是两件事**：`Watch` 到期 → **due work** 已由
`interfaces/controller.py` 的常驻控制器承载、链可查；而 `Watch` 到期 → **一行 `watch`
Run** 今天没有生产入口。两者不许合成一句。

### 8 · 插件 / 客户端真实兼容

| 列 | 内容 |
|---|---|
| producer | `marketops-plugin/` 的三份投影 |
| store | —（客户端侧） |
| consumer | Codex / WorkBuddy / MUC-Hosting |
| MCP surface | 同一个服务 |
| auth | 各自 |
| test | `tests/test_plugin_skill_has_no_authority.py`（含在 788 里） |
| live evidence | **`client-projections/codex/RECEIPT.md`**：真跑过（`codex-cli 0.153.4`，client info `codex-mcp-client`/`Codex`/`0.153.4`，Streamable HTTP，协议版本 `2025-06-18`） |
| **status** | **`PARTIAL`**：Codex 一档 `DEMONSTRATED`；WorkBuddy / MUC-Hosting 各记 **`BLOCKED_EXTERNAL`（精确到客户端）** |

**两档客户端（WorkBuddy / MUC-Hosting）的形态**：各 12 字段全 `blocked_external` +
四条 `measured_absence` + 入口文件名叫 **`mcp-server-entry.unverified.json`**（Codex 那份
叫 `mcp-server-entry.toml`）。**我们这边没有一处没做完** ⇒ 记 `BLOCKED_EXTERNAL`，
**不记 `NOT DONE`**。

**不许合成一格**：「两档客户端的 `BLOCKED_EXTERNAL`」与「WorkBuddy / MUC 的**客户端级
不存在**」是**两件事** —— 前者是「本机没有那台客户端可连」，后者是「我们没做那一档」。
本矩阵里只有前者。

⇒ **`TARGET_ARCHITECTURE_CONFORMANT` 不写 `YES`**（§7.4：只写「仅剩客观外部权限导致的
精确 BLOCKED」）。**它不挡 `READY_FOR_MEMBER_A_TRIAL`**（Codex 那一半 verified）。

### 9 · canonical Skill + client projections

| 列 | 内容 |
|---|---|
| producer | `marketops-plugin/SKILL.md`（canonical 主 Skill）+ references |
| store | —（仓库内文件；**国家/产品/Scene 规则在服务端版本化**，不复制进各 Skill） |
| consumer | 各客户端 |
| MCP surface | 同一个服务 |
| auth | 各客户端自己的 |
| test | `tests/test_plugin_skill_has_no_authority.py`；`tests/test_no_stale_assumptions.py` |
| live evidence | `client-projections/{codex,workbuddy,muc-hosting}/` 三份**都在**；`client-projections/README.md` 逐字「One canonical Skill, three client projections… **never in business content**」 |
| **status** | **`PARTIAL`** |

**哪一半是哪一半**：**「一份业务 workflow 来源、按客户端投影」这一半 `DEMONSTRATED`**
（三份都在、业务内容同源、每份带自己的安装说明与状态）；**「真的跑过」那一半随第 8 条**
（Codex verified、另两档 `blocked_external`）。**本格与第 8 条不许合成一格数两遍。**

### 10 · 访问 / 安全 / 预算闭环

| 列 | 内容 |
|---|---|
| producer | `db.py:177 budget_ceiling`（每个 `(scope, line)` 一行，`source` 记「谁定的」—— 上限是**服务端事实**，不是调用方参数）；`application/budget.py` |
| store | `budget_ceiling` + `budget_reservation`（`reservation_id` **内容寻址**，幂等由主键本身保证；状态 `reserved` / `spent` / `refunded`）；`adapters/sqlite/ledger.py` |
| consumer | 受理前那一行 `_row_budget`；Run / fetch 那条路里（`tools.py`） |
| MCP surface | **一条只读工具 `list_open_reservations`**（`catalog.py:591`）；写入侧（spend / refund）**不是独立动词** —— docstring 逐字「退款必须由一次写明理由的显式调用产生」 |
| auth | `catalog.py:1244 authorize(principal, name)`；`_CONTROL` 与 `_AUTHORIZE` 分开 |
| test | `tests/test_mcp_budget.py`、`tests/test_budget_ledger.py`、`tests/test_budget_contract.py`、`tests/test_pipeline_budget.py`、`tests/test_security_egress.py`（含在 788 里） |
| live evidence | 788 那一批 |
| **status** | **`DEMONSTRATED`**（其中熔断开关属「刻意仅为逻辑投影」，见下面「三」） |

**预算与限流跨 session 共享**：上限是服务端事实（`source` 记「谁定的」），不是调用方
参数 —— 一个能由调用方填的上限在报告上与「有人管着」同形。

### 11 · 报告 / 通知唯一口径

| 列 | 内容 |
|---|---|
| producer | **五个 `as_of` 生产者**：`product_loop.py:3687` / `:3909` / `:4201` / `:4687` / `:4936`，全部取自**冻结物自身时刻**（`cohort.created_at`，内容寻址且 `put_cohort` 只让时间往早走 ⇒ 同一个 `cohort_id` 稳定） |
| store | 冻结读数本身 |
| consumer | 同一份报告的各调用方 |
| MCP surface | 报告面各条工具；`_FORBIDDEN_PARAMETERS` **禁止** `at` / `now` / `as_of` 三个入参 —— 服务器算这个时刻，**调用方填不了** |
| auth | 各自 |
| test | `tests/test_mcp_cohorts.py`、`tests/test_cohorts.py`、`tests/test_mcp_launch.py`（含在 788 里） |
| live evidence | §3 条目 14 的变异：两处锚改回 `utc_now()` ⇒ `EXIT=1` 且四条红名单非空 |
| **status** | **`PARTIAL`** |

**机制那一半 `DEMONSTRATED`**：12 处 `utc_now()` **逐处裁决、无一处被整片替换**；答不上
来的如实 `null` **不编**。

**`runs.py` 零命中不是缺口**：run 报告的同一性由 `run_id` + `created_at` 承载（同一
`run_id` 两次读回同一组数字），与「稳定的 `as_of`」是**同一条性质的两个表达**。§2.42 裁定
的缺陷本来就是「**同一份回执里**两个 `utc_now()`」。

**本格记 `PARTIAL` 只因客户端那一半随第 8 条**，不因 run 报告。

**不许写成「通知已验证」**：`sink 失败不改变业务事实` 那一半，本部署只有一个出口，
「一个 event 投影多 sink」在本部署只能用测试替身演示。

### 12 · 历史验收不冒充 live 效果

| 列 | 内容 |
|---|---|
| producer | — |
| store | — |
| consumer | 读者（Member-A） |
| MCP surface | — |
| auth | — |
| test | — |
| live evidence | 结束条件块的**四个结论分开**（技术 conformance / 受控 pilot / 优于 Member-A / production authorization） |
| **status** | **`DEMONSTRATED`** |

**这一格的判据不是一个机制，而是一条纪律**：§28 逐字「**这些状态不能互相推出。**」——
11 个字段里每一个都要**各自**有读数，不许由别的字段推。本矩阵不给
`BETTER_THAN_MEMBER_A` 任何依据（在拿到独立标签或同条件基线之前恒 `NOT_DEMONSTRATED`）。

---

## 二、不许数两遍的两处

**(a) B16 条件 6 的接线半边与条件 2 是同一处缺口** ⇒ 本矩阵第 2 条与第 6 条里那一处
接线读数**记在同一条注上**。B24 的条件 6 与条件 2 同理。

**(b) 第 9 条与第 8 条**：见第 9 条的「哪一半是哪一半」。**不许合成一格。**

## 三、刻意仅为逻辑投影的两格（点名实际承载位置）

**(a) `rollback` 是一个声明的字符串，不是可执行的。**

* 类型逐字：`domain/feedback.py:917` `rollback: str`。
* 上文逐字（`:886-889`）：「**回滚路径与版本一起报**，是因为一个不能回滚的策略版本和
  一个能回滚的，在部署那一刻是两件事」—— 必填非空是**为了让「说不出怎么退回去」的
  补丁在报告上不像一份能退回去的**，不是为了让程序去退。
* **MCP 面上零个回滚动词**；`PolicyPatchState` 只有 `PROPOSED` / `AWAITING_APPROVAL`
  两个成员，**没有 `approved` 也没有 `rolled_back`**。
* **实际承载位置**：producer = `propose_policy_patch`（收它）；store = 补丁行；
  consumer = `list_policy_patches`（**两格分开报**）。

⇒ 这是「刻意仅为逻辑投影」，**不是**「有回滚能力」。

**(b) 熔断此刻开不开，MCP 面答不出。**

* `security/catalog.py:586` 逐字说那是**进程级**状态、**盘上没有它的行**。
* **实际承载位置**：进程内存，**不是**账。

⇒ 问「现在熔断着吗」在本服务面上**问不出答案**，而这是**刻意的**形状。**不许**把它读成
「一条待补的缺口」，也**不许**用一句「盘上没有」来代替点名。

## 四、仍然开着的清单（一条都不许读大）

| 出处 | 开着的那一半 | 它要什么 |
|---|---|---|
| 第 7 条 | **`Watch` → Run 面** | **产品决定**（产品级的意图来源）—— 不是存储、不是场景号 |
| 第 7 条 | G2' 三类时间**只判了第一类** | 另两类的判据（新证据 tick / 真实到期后的外部 revalidation 取不到读数：本部署无 provider） |
| 第 7 条 | 「一个 event 投影多 sink」本部署只能用测试替身 | 第二个生产 sink（Feishu 是 `BLOCKED_EXTERNAL`，不进 trial blocker） |
| 第 8 / 9 / 11 条 | WorkBuddy / MUC-Hosting 两档客户端 | **客观外部权限**（本机没有那两台客户端可连） |
| 第 6 条 | `rollback` 不等于「可执行回滚」 | **不是一个缺口**，是刻意形状（见「三」） |
| 第 10 条 | 熔断面 | **不是一个缺口**，是刻意形状（见「三」） |

**这份清单里没有一条是「暂时跳过」**：每条要么点了一个具名决定，要么点了一处客观外部
权限，要么点明它是刻意形状而**不是**缺口。
