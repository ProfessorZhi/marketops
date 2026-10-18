# 目标架构缺口矩阵（C00 收敛基线）

本文件是**收敛分支的第一条提交**（C00）的产物。它回答一个问题：
**从 PR #8 的代码底座到最新目标架构之间，盘上实际缺什么。**

它**不是**验收报告。本文件里没有任何一格是「通过」，因为它没有跑过任何一条目标
架构的业务路径 —— 那些路径今天还不存在。它也不是计划 §-1 那张表的抄本：那张表
是**设计者写的**判断，本文件是**在代码上量出来的**读数，两者逐条对过，不一致的地方
在 §5 写出来。

## 0. 四个 SHA 与基线读数

| 格 | 值 |
|---|---|
| `base_pr8_sha`（代码底座） | `426a09f6632ac5dd63c0da39669036d4c8163755`（PR #8 HEAD） |
| `latest_design_pr7_sha`（设计权威） | `0e48105b51e2e79851a21066b4f777489a0c04a4`（PR #7 HEAD） |
| `convergence_start_sha`（收敛起点） | `01f20349f8acff6c2b2291ad08d13302f22e58b7` |
| `baseline_test_sha`（基线跑的那棵树） | `01f20349f8acff6c2b2291ad08d13302f22e58b7`（同一个 SHA） |

`convergence_start_sha` 是把 PR #7 的 11 份设计文档合入 PR #8 代码底座之后的树。
合入是 `--no-ff` 的合并提交，**只动 `docs/`，零行代码** —— 这一点是可核的：

```text
$ git -C <convergence worktree> diff --name-only 426a09f6632ac5dd63c0da39669036d4c8163755 HEAD
docs/ACQUISITION_AND_PROVIDER_DESIGN.md
docs/ARCHITECTURE.md
docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md
docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md
docs/DATA_MODEL.md
docs/MCP_SERVICE.md
docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md
docs/PRODUCT_LINE_AND_SCENE_MODEL.md
docs/README.md
docs/SCENE_EVOLUTION.md
docs/research/MARKET_MODEL_ACCESS_REVIEW_2026-09-27.md
```

11 个文件、`1507 insertions(+), 45 deletions(-)`，全部在 `docs/` 下。冲突面只有
`docs/DATA_MODEL.md` 一处，合并干净。

### 基线读数（在 `baseline_test_sha` 上实测）

| 检查 | 读数 |
|---|---|
| `pytest -o addopts="" -q -rf`（全量） | **5081 passed, 4 skipped in 330.18s** |
| `ruff check .` | `All checks passed!` |
| `python -m marketops.schema` 重生成后 | 与 committed **逐字节一致**（`git status` 空） |
| `git diff --check` / `git diff --cached --check` | 空 |

`5081 passed / 4 skipped` 与 PR #8 自己的读数**同值**，这是文档合并应有的结果
（零行代码改动），但**它不因此成为本分支的新期望值** —— 后面每一条切片各自量各自
的树，任何一次数字变化都要能指着一条业务改动说清为什么。

## 1. 仪器：普查脚本，以及它先跑过的对照组

承重概念的「在不在盘上」不是靠读文档判断的，是一份普查脚本量的。脚本在
`/home/operator/artifacts/work-items/marketops/TARGET-CONVERGENCE-C00/census/`
（**不在仓库里**：它是 C00 的证据，不是产品的一部分）。

它对每个名字找三样东西：`^class <Name>` 的定义、`^<Name>[:=]` 的模块级赋值、
以及标识符出现。**仪器本身先跑对照组**：一批**已知存在**的名字，全零则仪器坏了，
上面所有的零一个都不算数。

对照组第一次跑就抓到了错，而且是**我的错**：

| 对照组里的名字 | 读数 | 真相 |
|---|---|---|
| `Scene` | class=0 assign=0（**零**） | 盘上**没有** `Scene` 这个模型类。`domain/scene.py` 只有 `SceneVersion` / `SceneState` / `SceneApproval` / `SceneBudgetLine`；scene 的身份是 `SceneVersion.scene_id` **一格**（`domain/scene.py:126`） |
| `OutreachWave` | 全部为零（**零**） | 波次的模型类叫 `Wave`（`domain/outreach.py:1881`），不叫 `OutreachWave` |

两条都是**我写错了对照组**，不是仪器漏读 —— 换名重跑后 20 条对照组全部非零，
仪器才被放行。第一条的副产品是一条真结论：**「Scene」在本仓不是一等对象，
是 `SceneVersion` 的一格**，这一点在 B02 编译 EffectiveScene 时会直接撞上。

## 2. 普查结果：44 个目标概念，1 个存在，43 个零

| 族 | 概念 | 读数 |
|---|---|---|
| 上下文与编译 | `Workstream` `ResearchContext` `SceneFingerprint` `EffectiveResearchPlan` `StrategyCompiler` `GeoLocaleSourceProfile` `ScenarioProfile` `ObjectivePolicy` `AccessPolicy` `ProductOverlay` `WorkstreamPolicy` `WorkstreamOverride` | **11 个全零** |
| Run 语义 | `RunSpec` `RunDelta` `RunRecommendation` `ExecutionMode` `RunPreview` | **5 个全零** |
| 采集与访问 | `FetchAttempt` `RetryAfter` `RateLimiter` `SourceCircuitBreaker` `AccessFailure` | **5 个全零** |
| 反馈与学习 | `FeedbackEvent` `ExperienceCandidate` `PolicyPatch` `BusinessDefinition` | **4 个全零** |
| 视图与跟进 | `SavedView` `Watch` `ActionItem` `FollowUpPool` | **4 个全零** |
| 调度与通知 | `ScheduleDefinition` `NotificationEvent` `Subscription` `DeliveryReceipt` `NotificationPolicy` `SinkFailure` | **6 个全零** |
| 多会话与插件 | `SessionBinding` `ClientProjection` `SkillManifest` | **3 个全零** |
| 产品线（可选上下文） | `ProductVersion` `OfferVersion` `GeoScope` `SalesMotion` `EffectiveScene` `SourceArtifactRef` | **6 个全零** |
| 产品线（可选上下文） | `ApprovedClaim` | **存在**（`domain/outreach.py:727`，7 个文件引用） |

**这条表的时点。** 上面每一格都是 `convergence_start_sha` 上的读数。本文件是**那一个
SHA 的快照**，不随后续提交改写：改写它会把「当时量到什么」变成「现在的代码是对的」，
而 §6 末尾那句「下一片按计划 §1 的顺序是 B15」记的正是这个时点。B16-a 落地之后，
第一行里 `ResearchContext` 与 `SceneFingerprint` 两个名字**已经存在**
（`marketops/domain/research_context.py`），所以「11 个全零」今天要读作
「9 个仍全零 + 2 个已存在，但**都还没有生产消费者**」。

**这一页不是活读数，今天也没有别的地方是。** B16 的活读数会落在它自己的验收矩阵上
（按计划 §B16 的 7 条关闭条件逐行对账，形状照 `docs/ACQUISITION_ACCEPTANCE_MATRIX_2026-09-28.md`），
而那份文件**在本片里还没有写** —— 本片关的是 B16 的输入面，7 条关闭条件一条都还没关：
`research_context.py` 的生产消费者要等编译器与 WorkItem 冻结那几片才出现，
在那之前按计划 §6「只有类名/字段/接口的无消费者实现不算完成」它不算数。

在零之外另作两条交叉核对，避免「名字不同但东西在」被记成缺口：

* `Watch` / `ActionItem` / `follow_up` / `action_item` 在 `marketops/` 下**零命中**。
* `schedul` 只在 `application/dispatch.py` 命中（是派发的节奏，**不是**一个调度器）。
* `notif` 只在四处命中：`adapters/feishu/client.py`、`adapters/mail_capture_source.py`、
  `application/correspondence.py`、`domain/outreach.py` —— 全部在**收信/回复识别**那一侧，
  与外发**通知投影**不是同一件事。

所以 §2 与 §6 的零是干净的零，不是改名。

## 3. 与计划 §-1 那张表逐条对过

计划的判定与实测读数**没有一处矛盾**，但有四处计划表没有说、而实测看得见的东西，
记在 §5。逐条：

| 计划 §-1 的判定 | 实测 | 一致？ |
|---|---|---|
| B10 / B11 / B12 / B13 / B20 / B22 = `DONE-baseline` | 对应的领域对象、store、应用用例与 MCP 工具都在盘上（`AccountRecord` `MarketAccount` `Cohort` `ContactRoute` `RouteOpportunity` `ReviewCase` `Continuation` `Evidence` …） | 一致 |
| B14 / B21 / B30 = `PARTIAL` | `Continuation` 有 7 个文件、65 处引用（B21 的底子在）；B14 的 `decision_id` 连线在 `decision_record` 表与 `get_decision` 工具上 | 一致 |
| B01 = `NOT BUILT`（无 `ProductVersion`/`OfferVersion`/Launch 契约） | `ProductVersion` `OfferVersion` 零；但 `ApprovedClaim` **已有**（计划把 B01 的落点写在 `domain/outreach.py::{TemplateFamily,ApprovedClaim}`，与实测吻合） | 一致，且补充了一格 |
| B02 = `PARTIAL` | 有 `SceneVersion`，无 Geo/Scenario/optional Product 编译层（`GeoScope` `SalesMotion` `EffectiveScene` 全零） | 一致 |
| B15 = `PARTIAL` | 承重缺陷**逐行定位到了**，见 §5.1 | 一致，且比计划更具体 |
| B16 / B17 / B23 / B24 / B25 = `NOT BUILT` | 36 个概念零命中 | 一致 |

## 4. 基线核对：MCP catalog / roles / schema / migrations

这四项是「目标架构要挂上去的那根梁」，C00 必须先把它们的**当前形状**量下来。

### 4.1 MCP 工具目录

`security/catalog.py::TOOL_CATALOG` 实测 **53 条**，权威是
`docs/MCP_SERVICE.md` §2/§3 与 `docs/MCP_PRODUCT_LOOP.md` §2 两张表的**并集**，
执行器是 `tests/test_security_catalog.py`（它把两份文档读出来，断言并集与代码逐字相等）。
角色分布（逐条实测）：

| 角色面 | 条数 | 备注 |
|---|---|---|
| 只有 `controller` | 41 | Codex / WorkBuddy / MUC 这些控制面客户端看得见的那一半 |
| 只有 `researcher` | 6 | `get_assignment` `search_web` `fetch_document` `crawl_site` `decide` `submit_findings` |
| `controller` + `researcher` | 2 | `get_decision` `get_evidence` |
| 只有 `authorizer` | 2 | `authorize_wave` `approve_policy_change` |
| `authorizer` + `controller` | 2 | `list_review_cases` `get_review_case` |

### 4.2 角色

`Role` 是三个值（`security/catalog.py:76`）：`CONTROLLER`（Claude Code）、
`RESEARCHER`（一个研究 worker）、`AUTHORIZER`（**一个人**，不是代理）。
两条已经落地的约束，B23/B25 必须沿用而不是重造：

* `authorize` 只吃 `(Principal, 工具名)`，不吃载荷、不吃环境、不吃时钟 ——
  「拿模型自己填的字符串当认证」在这个签名里**没有位置可放**。
* 环里的一条条目**不得同时带** `controller` 与 `authorizer`（`tokens.py` 在装载期拒），
  于是「一个主体同时握住闸门两半」只能靠改环文件，不能靠工具走出来。

### 4.3 契约

`marketops/schema.py::CONTRACT_MODELS` 导出 **14 份** `contracts/*.schema.json`，
`python -m marketops.schema` 重生成后与 committed 逐字节一致。目标架构新增的
每一个持久对象（Workstream / Run / RunDelta / FeedbackEvent / Watch / ActionItem /
Schedule / NotificationEvent …）都要走同一条路：**契约从代码导出，不手写**。

### 4.4 migrations / schema

实测（开一个空库跑 `ensure_schema` 后读 `sqlite_master`，证据在
`TARGET-CONVERGENCE-C00/tables-01f2034.txt`）：

| 项 | 值 |
|---|---|
| `SCHEMA_VERSION` | **6**（`adapters/sqlite/db.py:157`） |
| 表 | **42** 张 |
| 索引 | **28** 条 |
| `read_schema_state` | `standing=current, recorded=6, expected=6` |

42 张表：`account_cohort` `account_venue_relation` `budget_ceiling` `budget_reservation`
`change_signal` `contact_route` `continuation` `continuation_owner` `decision_record`
`evidence` `execution_ref` `human_resolution` `job_attempt` `job_settlement`
`maintenance_claim` `market_account` `observation` `outreach_attempt`
`outreach_authorization` `outreach_campaign` `outreach_claim` `outreach_draft`
`outreach_event` `outreach_family` `outreach_handoff` `outreach_manifest`
`outreach_suppression` `outreach_wave` `outreach_writing_policy` `research_job`
`review_case` `route_observation` `route_opportunity` `route_rejection` `scene`
`scene_snapshot` `scene_version` `schema_meta` `venue_asset` `work_item`
`work_item_guidance` `work_item_snapshot`。

另有一条**加表不改版本**的既有约定写在 `db.py:654`：DDL 每次 `ensure_schema` 整份重放，
所以加一张表不抬 `SCHEMA_VERSION`。目标架构的新表要遵守它，不另开一套迁移节奏。

## 5. 复核中发现的、计划表里没有的四件事

### 5.1 B15 的两处承重缺陷，逐行定位

计划 §-1 说 B15「browser 路径仍有 `status_code or 200` 与事后落点检查」。
逐行核到（`marketops/adapters/crawl4ai_fetcher.py`）：

| 行 | 内容 | 为什么是承重缺陷 |
|---|---|---|
| `:137` | `assess_http_response(status_code=status_code or 200, ...)` | **读不出的状态码按 200 分类** —— 把「未知」伪装成一次成功的测量 |
| `:157` | `status_code=status_code or None` | 与上面**同一行结果里的另一格**给的是 `None`。于是同一次抓取在「分类」与「报出」两处对同一个未知给了两种答案 |
| `:127` | `landed = check_target(final_url)` | **检测，不是预防** —— 请求已经发出去了。真正的预防是 `fetch` 入口的判据（`:74`），它拦下的目标一次请求都不发 |

这个文件自己的 docstring（`:238-248`）**已经把这件事写在明处**了：
「分类那一步仍然照旧收 `status_code or 200` …… 那是另一个决定，本次不动它，
也不假装动了。」C00 不动它（零功能重写），B15 的第一个提交动它。

### 5.2 `Scene` 不是一等对象，是 `SceneVersion` 的一格

见 §1 的对照组。计划 §-1 说 B02「有旧 SceneVersion，但无最新 Geo/Scenario/
optional Product 编译层」，措辞是对的；但「旧 SceneVersion」这个说法会让读者以为
scene 身份是稳的。实测：`scene_id` 是 `SceneVersion` 上的一个 `str` 字段
（`domain/scene.py:126`），`SceneVersion` 自带 `version` / `supersedes`
（`:130` / `:151`）。B02 要的「Scene ID 稳定，不把每个参数变更改成新 Scene」，
判据必须落在**这一格**上，而不是一个还不存在的 `Scene` 类型上。

### 5.3 多会话那条风险，当前实现**已经避开**

`Principal` 是 `(name, roles, workspaces)`（`security/catalog.py:172-174`），
其中 `workspaces` 缺省是**空集**而不是「全部」，注释写明这是失败关闭的那一边。
全仓 `current_scene` / `session_binding` **零命中** —— 也就是说，B25 要防的
「principal 级全局可变 `member-a.current_scene`」今天**不存在**，每一条业务调用都显式
带 workspace（`security/workspace.py::require_workspace`）。
这是一条**已满足**的约束（B25 的任务是保住它，而不是实现它），计划表没把它标出来。

### 5.4 目录 docstring 里的数字已经陈旧

`interfaces/mcp/__init__.py` 与 `adapters/mcp_server/{guard,tools}.py` 的散文里反复
出现「十五个工具」「十五条」。实测目录是 **53** 条（§4.1）。这不是代码错
（`catalog.py` 的措辞是准确的：两份文档的**并集**），是三处散文停在 Phase C 的
时点。**C00 不改它**（零功能重写，且散文不改变行为）；它记在这里，免得下一个人
按「十五个」去数接口面。

## 6. 这一条提交做了什么、没做什么

**做了：**

1. 建收敛分支 `/home/operator/workspace/worktrees/marketops/target-architecture-convergence-20260927`
   （分支 `marketops/target-architecture-convergence-20260927`），从 PR #8 HEAD `426a09f` 起。
2. 把 PR #7 HEAD `0e48105` 的 11 份设计文档 `--no-ff` 合入；`docs/DATA_MODEL.md` 冲突面
   保留 PR #8 的历史验收文件，未来施工计划以最新 `docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`
   为唯一准。
3. 在合并后的树上量了基线（§0）与梁的四项（§4），写了本文件。

**没做（刻意的）：**

* 一行产品代码都没改。§5 里列的两处缺陷、三处陈旧散文，一处都没动 —— 它们属于
  B15 与各自的切片，C00 只负责**把它们指出来**。
* 没有重做 B10–B14 / B20–B22 中任何一项。
* 没有改 PR #8 的历史验收报告（`docs/R1_R2_AUTOMATION_ACCEPTANCE_2026-09-27.md`）与
  台账（`docs/R1_R2_IMPLEMENTATION_LEDGER.md`）；`R1_VERIFIED=YES` / `R2_VERIFIED=YES` /
  `live-acceptance=N/A` 三条历史事实原样保留。
* 没有选市场任务、没有启动任何真实付费 Run、没有开启自动化。

下一片按计划 §1 的顺序是 **B15**。
