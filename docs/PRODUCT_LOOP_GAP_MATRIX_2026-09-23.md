# 产品闭环缺口矩阵（起点快照）

盘点时间：2026-09-23。盘点构建：`b57b02a`（分支 `marketops/product-loop-20260923`）。
方法：只读勘探 + `command grep -r` 全仓核对，**未改动任何文件**。

这份文件记的是**开始做产品闭环之前**的真实状态。它存在的理由只有一条：
把「文档写着已实现 / 入口存在 / 配置就绪 / 单测通过 / 真实业务成功」五件事分开。
每一行只有一列是证据，其余四列即使全绿也不构成第五列。

列义：

- **文档**：`docs/` 里有对应叙述 —— **最弱的一列**，它只说明有人想过。
- **代码**：`marketops/` 里有可运行的实现（不是类型注解、不是契约导出、不是测试夹具）。
- **生产路径**：存在**非测试**的调用链，从 `interfaces/` 或 MCP 工具一路走到实现。
- **持久化**：落盘（SQLite 表 / JSONL / 受管存储），且重启后读得回来。
- **真实运行回执**：在真实来源、真实 worker 或真实外部系统上跑过并留下可核对的读数。

---

## 1. 输入侧：Scene 与账号主体

| 环节 | 文档 | 代码 | 生产路径 | 持久化 | 真实回执 | 一句话事实 |
|---|---|---|---|---|---|---|
| **Scene 生命周期**（立项/校验/启用/暂停/恢复/归档） | 有（`SCENE_EVOLUTION.md` 写成目标态） | **无** | **无** | **无** | 无 | 全仓没有 `class Scene`、没有 `domain/scene.py`；`draft`/`activate`/`archive` 在 `application/` 里的落点全是 **Job 状态机**，不是 Scene |
| **Scene 版本** | 有 | 有（但只是内容摘要） | 有 | 有（`scene_snapshot`） | 有 | `SceneFiles.version` 是按目录字节算的 sha256（`domain/profile.py:413`，全仓唯一一处）；它回答「这一版规则的字节是什么」，**不回答**「这个场景现在什么状态」 |
| **Scene 注册表 / 状态列** | 无 | **无** | 无 | **无** | 无 | SQLite 15 张表里没有 scene 注册表；`research_job.scene_id` 是唯一带 scene 的列 |
| **`get_scene` MCP 工具** | 有 | 有 | 有（只在 `jobs` 存在时） | — | 有 | 纯读：装载 + 报摘要，**不写任何状态**；只服务 `settings.profile` 那一个 id，别的 id 拒绝而不退回默认 |
| **Asset（`VenueAsset`）** | 有（契约） | 类存在 | **无** | **无** | 无 | 构造点只有自身类定义、`schema.py` 契约导出、`test_models_invariants.py`；**零生产者** |
| **责任主体（`ResponsibleEntity`）** | 有（契约） | 类存在 | **无** | **无** | 无 | 同上；唯一另一次出现是 `application/openweb.py:101` 的占位符黑名单字符串 |
| **责任关系（`AccountVenueRelation`）** | 有（契约） | 类存在 | **无** | **无** | 无 | 同上 |
| **Account（`AccountRecord`）** | 有 | 类存在 | **只有读** | **无写入路径** | 有（操作员手写 YAML） | `load_accounts` 读操作员给的 YAML；**本仓没有任何代码写账号清单** |
| **候选账号（`AccountCandidate`）** | 有 | 有 | 有（`run-discovery`） | 仅工件行（dict） | 有 | 有 application 生产者，但模型本身不落库；落的是 `inputs_dir/*.jsonl` 里的 **dict 行** |
| **`official_domain` 的语义** | 有 | 部分 | 有 | 有 | 有 | 见 §3.1 待修项：发现期 URL 与「证据确认的官方归属」目前共用同一个词 |
| **ContactRoute / RouteOpportunity** | 有 | 有 | 有（`run-demo`） | 有（`contact_route` / `route_opportunity`） | 有 | 本仓唯一两条「真的从文本产出并落库」的对象 |
| **Evidence** | 有 | 有 | 有（两条：`run-demo`、MCP `fetch_document`/`crawl_site`） | 有（`evidence` 表 + JSONL） | 有 | 唯一有两个真生产者的对象 |

## 2. 增量维护

| 环节 | 文档 | 代码 | 生产路径 | 持久化 | 真实回执 | 一句话事实 |
|---|---|---|---|---|---|---|
| 时新性模型（`Currentness`/`FreshnessPolicy`/`evaluate_freshness`） | 有 | **有**（`domain/temporal.py` 377 行） | **零消费者** | 无 | 有（仅单测） | 全仓 import 它的只有 `tests/test_domain_temporal.py`；**没有任何 profile 声明 `FreshnessPolicy`** |
| 逐面刷新周期 | 无 | **无** | 无 | 无 | 无 | 没有 `RefreshPlan`、没有 aspect 概念 |
| 到期工作选取器（due work） | 无 | **无** | 无 | 无 | 无 | 对 `\bdue\b`/`next_due`/`is_due`/`due_at` 全仓搜索 **零命中** |
| checkpoint / cursor / 租约 | 部分 | 部分 | 有（作业侧） | 有 | 有 | `cursor` 是检索查询展开的局部变量与 CLI 参数；`job_attempt` **没有** `expires_at`，租约不按时钟失效（刻意） |
| 调度 / 守护循环 | 有（目标态） | **无** | **无** | — | 无 | `marketops/application/dispatch.py:940` 逐字：「**本构建里没有任何计时器会去跑它**」；`tick` 全仓零命中 |

## 3. 外向链（邮件写作 → 发送 → 回信）

| 环节 | 文档 | 代码 | 生产路径 | 持久化 | 真实回执 | 一句话事实 |
|---|---|---|---|---|---|---|
| 邮件**模板** | 有 | **无** | 无 | 无 | 无 | `template` 一词在代码里的落点**全是检索查询模板**（`application/openweb.py`），与邮件无关 |
| 撰写策略 / 已批准话术 | 有 | **无** | 无 | 无 | 无 | 无任何模型 |
| Draft（草稿） | 有 | **无** | 无 | 无 | 无 | 两处同名物都是无关的局部变量（`decisions.py:462`、`profile.py:471` 注释） |
| Campaign | 有（散文） | **无**（三目录 0 次） | 无 | 无 | 无 | 只在 `ARCHITECTURE_DECISIONS.md` 等文档里 |
| Wave + 冻结 manifest | 有（散文） | **无**（三目录 0 次） | 无 | 无 | 无 | 同上；SOP 里有字段清单，代码里没有对应物 |
| 发送授权 | 有（散文 + 一个状态名） | **无** | 无 | 无 | 无 | `prepared_pending_authorization` 只是一个 `RouteStatus` 取值，没有任何东西会去**校验**一份授权 |
| outbox / outbound | 有（散文） | **无**（全仓 0 次） | 无 | 无 | 无 | — |
| 发送执行 | 有 | **无** | 无 | 无 | 无 | `send_authorized` 是 `Literal[False]`，`SENT_STAGE_1..4` 属于「类型层构造不出来」的集合 |
| 回信（reply） | 有 | 只有一个枚举取值 | **无** | 无 | 无 | `RouteStatus.REPLY` 在 `UNREACHABLE_WITHOUT_AUTHORIZATION` 里 |
| 退信（bounce） | 有 | 只有一个枚举取值 | **无** | 无 | 无 | 同上 |
| 退订（unsubscribe） | 有 | **无**（三目录 0 次） | 无 | 无 | 无 | — |
| complaint / consent | 有（散文） | **无**（全仓 0 次） | 无 | 无 | 无 | `consent` 全仓 0 次 |
| 抑制（suppression） | 有 | 只有一个状态取值 | **无** | 无 | 无 | `RouteStatus.SUPPRESSED` **不在**不可达集合里 —— 今天可以合法构造出一条 suppressed 路线，但没有任何东西会去产生或读取它 |
| 销售交接（SalesHandoff） | 有（散文） | **无**（三目录 0 次） | 无 | 无 | 无 | — |
| FeishuSink | 有 | 有（字段映射） | 有（未接线） | 无 | 无 | `ADAPTER_STATE = "interface_only"`；`:85` 的 `NotImplementedError` **不可达**（要到达它需 `ADAPTER_STATE == "wired"`，而全仓没有任何一处给它赋值）。**2026-09-24 更新（本行已过时）**：该 `NotImplementedError` 已删除；`handoff` 的写入路径已实现（授权/凭据/令牌/查/写/读回核对 + 失败回执），`ADAPTER_STATE = "partially_wired"`，真实写入 `BLOCKED`；`route`/`opportunity` 仍拒写，理由是没有可判定的业务键 |

## 4. 权限与角色

| 项 | 事实 |
|---|---|
| 角色闭集 | **恰好两个**：`controller` / `researcher`（`security/catalog.py:50-60`，docstring 明说再加第三个之前先要有「它是谁」的答案） |
| MCP 工具数 | **15**（controller 可见 9、researcher 可见 8、共享 2） |
| phase 使用情况 | C×5、D×2、E×1、F×2、G×5；**A / B / H / I 未被任何工具使用** |
| `send_authorized` 能否被绕过 | **不能**。无 setter、无 env 读取、无 CLI 旗标、无 MCP 参数；`tests/test_config.py:112-121` 先 `monkeypatch.setenv("SEND_AUTHORIZED","true")` 再断言仍为 `False` |
| 跨 Scene 对象授权 | 有（工作区 × Scene 的越界面测试在 `test_mcp_workspace.py`、`test_mcp_isolation.py`），**但**：`VenueAsset`/`ResponsibleEntity`/`AccountVenueRelation` 根本没有对象可授权，因为它们是零生产者 |

## 5. 这张矩阵要说的一句话

产品闭环的**前半段**（Scene → 来源发现/母池导入 → Asset → 责任主体 → ContactRoute/Evidence）今天
只剩**中段**是通的：`ContactRoute` / `RouteOpportunity` / `Evidence` 有真生产者与落库路径，
而它们的**上游主语**（场地资产、责任实体、责任关系）一个生产者都没有，账号只能由操作员手写 YAML。
**后半段**（草稿 → Campaign/Wave → 授权门 → 发送 → 回信 → 交接 → 飞书）在代码面是 0。

因此本轮的**完成条件不是「再加几个工具」**，而是：这两段各自要有可运行的实现，
并且中间那段已有的东西要接到新的主语上 —— 否则「一次立项、持续经营一个市场」这句话
在代码里仍然拼不出来。

---

## 这份文件**不会**被改写成「现在的状态」

它的每一行都是 `b57b02a` 那一刻的读数，改写它等于让「起点」这个说法失去意义 ——
本仓已经因为同一件事吃过一次亏（`docs/MVP_ACCEPTANCE_REPORT.md` 里那些旧 FAIL）。
本轮的进展写在**另一份**文件里：`docs/PRODUCT_LOOP_ACCEPTANCE_*.md`，
它按构建命名，逐格回答「这一格现在在哪一列」，并写明 `tested_code_sha`。

两处的分工是：这份文件回答「开始之前缺什么」，那份回答「做完了之后到了哪里」。
一份文件同时承担两者时，先被牺牲的一定是前者。
