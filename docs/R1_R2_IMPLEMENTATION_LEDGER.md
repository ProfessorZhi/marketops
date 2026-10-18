# R1 / R2 实现台账

**这一份文件的存在理由只有一个：让下一段上下文能从这里接着干，而不必重读全部历史。**

它**不是**验收报告。验收读数住在 `docs/R1_R2_AUTOMATION_ACCEPTANCE_2026-09-27.md`
（切片 11 产出，**已落地**）。台账记的是**进度**：做过什么、下一步是什么、哪些判决已经下过
（以及为什么），以及那些**不该再被重新问一遍**的取舍。

## 怎么用

- 每一段上下文开始：读这一份的最上面那一节（当前状态），按它的「下一步」走。
- 每一段上下文结束（或即将压缩）：把当前状态那一节改写成事实 —— HEAD、已完成、
  未完成、测试读数、真机读数、**下一步的那一个动作**。
- 判决写进「已下的判决」，连同**理由**：没有理由的判决下一段会被重新推翻一遍。

---

## 当前状态

| 格 | 值 |
|---|---|
| 分支 | `marketops/member-a-automation-r1-r2-20260926`（本机 worktree） |
| 切片 1（B10） | **已提交** `d69d8af` |
| 切片 2（B11） | **已提交** `920c5b6` |
| 切片 3（B12） | **已提交** `27d2d61` |
| 切片 4（B13） | **已提交** `915718a` |
| 切片 5（B14） | **已提交** `02305ec` |
| 切片 6（G1） | **已提交** `d626bf8`（见下） |
| 切片 7（B20） | **已提交** `949452c` + `7be6b90` + `a3b15f6`（见下） |
| 切片 8（B21） | **已提交** `8c08cf8` + `c7d456c`（见下） |
| 切片 9（B22） | **已提交** `6c94552`（见下） |
| 切片 10（G2） | **已提交** `b2d0992`（见下） |
| 切片 11（文档 + 最终验收） | **已提交** `0527bc2`：`docs/R1_R2_AUTOMATION_ACCEPTANCE_2026-09-27.md` + 本表更新（见下） |
| 全量套件 | `5081 passed, 4 skipped`（2026-09-27，切片 10 之后；上一片是 `5058 passed`） |
| `ruff check .` | 通过 |
| 契约重生成 | 切片 6–10 之后 **14 份逐字节未变**（这五片都没动契约模型）。切片 5 动过一次：`contracts/account-venue-relation.schema.json` 多一格 `decision_record_ids`。重生成是**不动点**（连跑两次无新 diff） |
| 切片的划分 | R1 = 切片 1–6（B10/B11/B12/B13/B14 + G1）；R2 = 切片 7–10（B20/B21/B22 + G2）；切片 11 = 验收报告。**全部落地** |
| R1_VERIFIED | **YES**（判词、限界与逐项证据：`docs/R1_R2_AUTOMATION_ACCEPTANCE_2026-09-27.md` §0 / §2 / §3） |
| R2_VERIFIED | **YES**（同上 §0 / §3） |
| BLOCKED（只此一条） | 付费 live run（无授权预算）。它阻塞 §4 里 3–6 号指标的**真实**读数，**不阻塞** R1/R2 的技术通过 |

### 切片 4（B13）· 独立草稿用例与业务回执

**一句话**：起草从 `plan_wave` 的下游**变成一条并列的用例**（`plan_drafting`），
并把「这一批每一条去了哪一格」做成一份能对账的业务回执。

**已落地的代码**

| 层 | 文件 | 内容 |
|---|---|---|
| domain | `marketops/domain/outreach.py` | `DraftState.REVIEW_ONLY`（与 `HELD` 方向相反的一档）；`OutreachDraft.entry_kind` / `.route_grade`（起草时**冻结**，缺省 `None` 而不是一个像样的取值） |
| application | `marketops/application/outreach.py` | `plan_drafting` / `DraftTarget` / `DraftingPlan`；`family_for`（按 `(stage, role_class)` 挑族，`FAMILY_MATCH_ROLE_CLASS` / `FAMILY_MATCH_STAGE`）；`FALLBACK_TO_STAGE_FOR_GRADES`；`review_reasons`；`GenerationReceipt.outcome` 词表加 `review_only`；`DISPOSITIONS` / `_DISPOSITION_BY_OUTCOME` / `DraftingReceipt` / `drafting_receipt`（**对不上账就拒，不补行**）；`generate_drafts` 改收 `DraftingPlan` |
| wire | `marketops/adapters/mcp_server/outreach.py` | `draft_outreach` 的 `business` 一格（六个档位 + `rows_written` + 逐条 `rows` + `excluded`）；`_draft_row` 端出 `entry_kind` / `route_grade` |
| docs | `docs/MCP_PRODUCT_LOOP.md` §2 | `draft_outreach` 那一行重写（见下） |

**新增判据**（`tests/` 两个文件，共 12 条）

`tests/test_outreach_contract.py` §「三之二、B13：起草是一个**独立用例**」九条：

| # | 判据 | 证的是什么 |
|---|---|---|
| 1 | `test_a_b_c_d_each_get_a_draft_written_for_their_own_route` | A/B/C/D 各拿到自己那一族的草稿；`family_match` 全是 `role_class`；草稿上冻结的 `entry_kind` / `route_grade` 与路线逐条相符 |
| 2 | `test_x_is_not_drafted_and_the_reason_names_the_rule_that_stopped_it` | 两条 X 走两条**不同**判据（入口性质 / 等级），理由逐字不同 |
| 3 | `test_a_route_that_already_left_the_intake_still_gets_its_draft` | 同一条 `sent_stage_1`：`plan_wave.entries == ()` 而 `plan_drafting` 有它；草稿落库、`store.wave(WAVE) is None` |
| 4 | `test_drafting_has_no_parameter_that_could_send_anything` | `inspect.signature` 判两个入口不含 `transport` / `mailer` / `manifest` / `authorization` / `sendbox` |
| 5 | `test_a_route_the_binding_never_judged_is_review_only_not_sendable` | `binding=NONE` → `review_only` + `REVIEW_ONLY` 落库 + 不进清单 |
| 6 | `test_a_row_nobody_judged_the_freshness_of_is_review_only` | `first_seen_at=None` → 时效为空 → 另一句理由 |
| 7 | `test_the_business_receipt_accounts_for_the_frozen_target_set` | 4 条路线（1 accepted / 1 held / 1 failed / 1 excluded）**逐条指名**；`rows_written=1 < len(plan.targets)` |
| 8 | `test_re_running_the_batch_is_reused_not_a_second_write` | 第二趟 `reused=1`、`rows_written=0` |
| 9 | `test_a_receipt_that_cannot_be_reconciled_is_refused_not_padded` | 两个方向各抛一次（缺回执 / 不在计划里） |

`tests/test_mcp_outreach.py` §「九、§B13：起草的产出对得上它的输入」三条：

| # | 判据 | 证的是什么 |
|---|---|---|
| 1 | `test_a_fact_that_cites_another_account_s_evidence_is_refused_whole` | **`_checked_facts` 的承载点**：形状/摘要都对、只是属于另一个账号的证据 → 整次调用被拒、草稿一行不多（先跑一遍引用对了的，看它写出两稿）。库里没有的 id 也在同一条里 |
| 2 | `test_the_evidence_a_draft_cites_really_is_in_the_store_and_is_its_own` | 回读草稿引的证据，逐条回库核 `account_id`；再拿一个同形但不存在的 id 问一次，证明前一个循环查的是表不是非空 |
| 3 | `test_the_business_row_reconciles_with_the_manifest_it_is_frozen_into` | `business` 与 `prepare_wave` 的回执、`get_wave` 读回的清单三处指同一批**收件人**；清单每一行的证据回库核归属 |

**三条判据做过变异验证**（改代码 → 判据必须红 → 复原）：`_checked_facts` 的归属核对
改成恒假 → 判据 1 红（并且真的把 account-0001 的稿子写成引了 account-0002 的证据）；
`_draft_row` 的 `evidence_ids` 里塞一个假 id → 判据 2 红；`prepare_wave` 回执的
`allowed_count` 加一 → 判据 3 红。

**关闭条件的逐分句对照**

| 分句 | 落在哪 |
|---|---|
| 已知合格 A/B/C/D fixture 各有合适草稿 | 判据 1 |
| X / 未批准主张被拦 | 判据 2（X）；§三既有 `test_a_writer_may_not_cite_a_claim_nobody_approved` |
| 无条件者说明原因 | 判据 5/6（review-only 的两句理由）+ 判据 7 的逐条指名 |
| **不安装 SMTP 仍可生成** | §六既有 `test_drafting_still_works_without_any_transport` |
| 默认无发送工具权限 | §一既有 `test_no_outreach_tool_is_visible_to_a_researcher` + 判据 4 |
| 结果由 MCP 直接回读，无后台人工桥 | 判据 3（`business` / `drafts` / `receipts` 三格都在回执里） |
| 以冻结对象集对账六格及下一层写入 | 判据 7/8/9；MCP 层判据 3 |
| 实体内容与证据关联验证不能仅检查非空/hash | MCP 层判据 1/2 |

**真实 cohort 的读数**留给切片 11 的验收报告：本切片只能报 fixture 那一条。

### 切片 3（B12）· 账号池 → 系统生成 Cohort

**关闭条件（逐分句）**：干净数据根、没有 inputs YAML，仅通过 MCP 发现/晋升后可列
账号、选 cohort、维护并起草；分页/新增账号/重启不丢记录；暂停 Scene 不接新工作；
每批可回读成员/选择依据/排除原因。

**已落地的代码**

| 层 | 文件 | 内容 |
|---|---|---|
| domain | `marketops/domain/cohort.py` | `CohortSelection` / `CohortMember` / `CohortExclusion` / `AccountCohort` / `CohortExclusionCode` / `cohort_id_for` / `freeze_cohort` / `exclusion_reason` |
| ports | `marketops/ports/pool.py` | `AccountCohortStore`：**只有** `put_cohort` / `get_cohort` / `list_cohorts`，**没有** `merge` 构造参数 |
| adapters | `marketops/adapters/sqlite/pool.py`、`adapters/memory.py` | 两个实现；`created_at` 只往早走（`min`） |
| application | `marketops/application/cohorts.py` | `freeze_from_pool` / `current_cohort` / `cohort_dossiers`（+ 既有的 demo loader 那半） |
| application | `marketops/application/inputs.py` | `cohort_subjects` → `SubjectResolution`（失败一律是 `blocker`，不抛） |
| wire | `marketops/adapters/mcp_server/product_loop.py` | `_subject_source` / `_subjects` / `_source_receipt` 单一派发；`freeze_cohort` / `list_cohorts` / `get_cohort` 三条工具 |
| wire | `marketops/adapters/mcp_server/outreach.py` | `draft_outreach` / `prepare_wave` 收 `cohort`；回执报 `subjects` |
| catalog | `marketops/security/catalog.py` | 三条新工具；四条老工具的参数说明补上「这一批的主体由 cohort 或 name/pool 指出」 |
| docs | `docs/MCP_PRODUCT_LOOP.md` §2、`docs/DATA_MODEL.md` | 三条新行 + 扩展行；`docs/MCP_SERVICE.md` **刻意不动**（它被 baseline 钉住） |

**新增测试**

| 文件 | 条数 | 证的是什么 |
|---|---|---|
| `tests/test_domain_cohort.py` | 31 | 冻结这个纯函数：id 的原像、三态、上限 |
| `tests/test_cohort_store_contract.py` | 17 | 两个实现对着同一组判据各跑一遍 |
| `tests/test_mcp_cohorts.py` | 11 | §B12 那条链端到端（见下） |
| `tests/test_sqlite_route_tables.py` | 6 | `route_observation` / `route_rejection` 各在 `db.OWNED_TABLES` 里（v5/§B12 落地时漏列） |

`tests/test_mcp_cohorts.py` 逐条对着关闭条件的分句：

| 分句 | 判据 |
|---|---|
| 干净数据根、没有 inputs YAML | `test_a_clean_data_root_never_grows_a_yaml_list`（**每一步之后**都数一次 `.yaml`） |
| 只走 MCP：发现 → 晋升 → 列账号 → 选 cohort → 维护 | `test_the_chain_runs_from_the_pool_without_a_yaml_list` |
| 起草 | `test_drafting_from_a_cohort_selects_its_recipients` |
| 每批可回读成员/选择依据/排除原因 | `test_a_frozen_batch_reads_back_member_by_member` |
| 分页 | `test_a_capped_roster_reports_what_it_dropped` |
| 新增账号 | `test_a_new_account_makes_the_next_freeze_a_second_batch` |
| 重启不丢记录 | `test_a_second_session_reads_what_the_first_one_froze` |
| 暂停 Scene 不接新工作 | `test_a_paused_scene_still_freezes_but_takes_no_work` |
| 两个来源恰好给一个 | `test_the_two_subject_sources_refuse_together_and_apart` |
| 没接线时各说各的 | `test_the_cohort_tools_say_not_wired_rather_than_empty` |

### 切片 3 里**顺手修掉的**两处仓内缺陷

按「本仓代码范围内的缺陷必须修，不能只登记」那一条落的：

1. **`run_maintenance` 的回执不报出这一轮按哪一份名单定序。**
   `get_maintenance_plan` 与 `get_ops_report` 都报 `subjects`，只有它不报 —— 而它
   恰恰是**写**工具，且 `cohort` 的空串是「用这个 Scene 最近冻的那一份」（调用方
   手里没有那个 id）。结果：「这一轮跑的是谁」在这份回执上只答得出「最近那一份」，
   而这个说法在两份报告里都成立、它们读的却可以是两份不同的名单。已补上
   `subjects`（与另外两条同形），并写进 `note`。
2. **`_subject_source` 的拒绝里写死了参数名 `name`。**
   外联那两条工具的第一个参数叫 `pool`，它们的 schema 里**没有** `name` —— 一条
   照着拒绝去改的调用方会去找一个不存在的参数。已改成由调用方传入
   `label`（`*` 之后，**没有缺省**，因为「写死一个名字」正是这一格要挡的东西）。

---

## 已下的判决（不要再重新问一遍）

### D-B13-1 · `plan_drafting` 与 `plan_wave` **并列**，不合成一个带开关的入口

合成之后，「起草这一趟到底读了发送前序没有」就再也问不出来了 —— 而那正是 §8 要
拆开的那条链（上一轮真实验收 `planned=0` 的另一面：先排发送、再照着起草）。

同步的取舍：`draft_them` / `freeze` 两个测试助手**各自**从同一批 routes 算自己的计划，
因为生产里就是两个入口各读一遍同一批路线。

### D-B13-2 · 族的挑选按 `(stage, role_class)`，**只有 A/B 才允许回落到本阶段族**

`family_for` 的第一层逐字比 `role_class`，第二层取本阶段 `family_id` 字典序最小的
那一族；`plan_drafting` 再补一条：`match is FAMILY_MATCH_STAGE` 且等级不在
`FALLBACK_TO_STAGE_FOR_GRADES = {"A","B"}` 时**跳过**。

这是 §8「**C/D 不冒充具名决策人**」的可执行形态。另外，`generate_drafts` 里原先那句
`families.get(entry.family_id) or family_for_stage(...)` 的**兜底已经删掉**：它会让
「清单换了、计划还指着旧族」在写信那一刻被**静默换文**，而 §8 逐字要的是「不能
静默换文」。现在计划点心名了族，那里只负责确认它还在 —— 不在了就 ``failed``。

（`_campaign_family_id` 保留：它产出的是**活动**那一格的缺省声明，不是「这一稿用了
哪一族」。要读后者，读草稿上的 `family_id`。）

### D-B13-3 · `wrote_a_row = outcome != "unchanged" and draft_version > 0`

三种写法试过之后定这一种。另有一个 `DraftingOutcome.sendable` 被**删掉**：它是
推测性 API（没有任何调用方），而「可发」这件事在这条链上的唯一读法是
`prepare_wave` 放不放它进清单。

### D-B13-4 · 「证据引用得对不对」判在 **MCP 层**，不在 `plan_drafting`

`plan_drafting` 手上没有证据库，它能问的只有「有没有挂」；而 `ContactRoute` 自己
钉着「非 X 的等级必须挂证据」，所以那一条筛选**正常写路径根本走不到** —— 它守的是
「库里的行被绕过校验写进来」（`model_copy`，与既有的 `test_a_route_with_no_evidence_is_not_planned` 同一手法）。

§B13 那句「不能仅检查非空/hash」的承载点因此是 `_checked_facts`：它逐条核对
「这条证据在库里、且属于这个账号」。判据在 `tests/test_mcp_outreach.py` §九。

### D-B13-5 · 回执对不上账就**拒**，不补行

`drafting_receipt(plan, outcome)` 两个方向都判：计划里的每一条在回执里**恰好出现
一次**，且回执里不出现计划外的人。对不上抛 `ConfigError`。补一行会让「这一批每一条
去了哪」这件事在盘上有一个看不出错的读数。

### D-B12-1 · 研究侧**当时**不加 `JobInputKind` 成员 —— 已在切片 6 推翻

**这条判决已经作废，留在这里是因为推翻它的理由比判决本身有用。**

切片 3（B12）当时的决定：§B12 的实现行把「研究」也列为消费者之一，但研究侧只从
闭集三成员的 `JobInputKind` 收作业，给它开一条 cohort 路会改一个契约级的枚举。当时的
理由是「§B12 的落点写的是 MCP product-loop consumers，关闭条件列的是可列账号 / 选
cohort / 维护 / 起草 —— 没有研究」。

**这个读法是错的**，切片 6 的纵向链把它证出来了：§G1 的顺序逐字是
`Scene → discovery/import → 准入/identity → cohort → Quay research → Jev（确有用途）
→ 唯一路线/拒收 → 草稿/合法 hold`，而 **cohort 后面紧跟着 Quay research**。也就是说
「把一份冻好的名单交给研究」不是 §B12 的边角，它是纵向链上的一节；而 §B12 的实现行
本来就逐字写着「维护/草稿/**研究**接受 Scene selector 或 durable cohort 引用」——
只有前两条兑现了，第三条在工具面上根本没有入口。当时的误读来自只读了「落点 +
关闭条件」两行，没读那一行实现要求。

**代价与处置：** 一份冻好的名单谁都送不到研究那一步，链在这一节断开。这在本仓代码
范围内，所以切片 6 补上了 `JobInputKind.COHORT`、`subjects_for` 的第四路与组合根那
两格接线。两条当时用来支撑「不加」的理由，逐条都不成立：

* 「会改一个契约级的枚举」—— 改了，而它**正是**该改的地方：那一格 CHECK 与枚举是
  同一份东西（`tests/test_sqlite_job_kind.py` 现在钉住这一点），加成员本来就要连带
  一次真迁移（v5 → v6），而不是避开枚举。
* 「研究侧本来就有一条不要 operator YAML 的路（`SOURCE_RECEIPT`）」—— 那句话对
  「不要 YAML」成立，对「把**名单**送进去」不成立：回执折出来的账号与
  `freeze_cohort` 冻下来的名单是两个不同的主体集合，前者不能代替后者。

**留下的教训（下一段照此办）：** 一条判决如果读的是「落点/关闭条件」而不读**实现要求
那一行**，它会把一节纵向链判成边角。G1 之所以先写底稿再动手，正是因为这类缺口只有
把链从头走一遍才现形。

### D-B12-2 · `list_accounts` 没有接 cohort（**偏离，已决定**）

`list_accounts` 读的是一份**清单**（`name`），它答的是「那份清单里现在有什么」。
账号池那一侧的问法是 `list_account_pool`（不接 `name`，直接读池子），而「这一批
冻下来的成员是谁」是 `get_cohort`。三者各答各的，不合成一个工具。

### D-B12-3 · `inputs/<name>.yaml` 保留为兼容路径

「删除正常使用依赖它的前提」不等于「删掉这个入口」。判据落在
`test_a_clean_data_root_never_grows_a_yaml_list`：发现/晋升/冻结之后 `inputs_dir` 里
**仍然一个 `.yaml` 都没有**，而旧路径照旧读不到东西 —— 一条**后台写影子 YAML** 的
实现会让那个数在第一步之后变成 1。

---

## 剩余切片（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §25 的顺序）

| # | 项 | 状态 |
|---|---|---|
| 1 | B10 | 已提交 `d69d8af` |
| 2 | B11 | 已提交 `920c5b6` |
| 3 | B12 | 已提交 `27d2d61` |
| 4 | B13 | 已提交 `915718a` |
| 5 | B14 | 已提交 `02305ec` |
| 6 | G1 | 已提交 `d626bf8` |
| 7 | B20 | 已提交 `949452c` + `7be6b90` + `a3b15f6`（另两笔底稿/取证：`0596a13`、`9d8cc4b`、`86384a4`） |
| 8 | B21 | 已提交 `8c08cf8` + `c7d456c` |
| 9 | B22 | 已提交 `6c94552`（底稿与落地记录见下） |
| 10 | G2 | 未开始 |
| 11 | 文档与最终验收 | 未开始 |

### 下一步的那一个动作

**进切片 10（G2，受控自动经营）**，然后切片 11（文档与最终验收）。切片 9 已提交
（`6c94552`：实现 + 判据 + 底稿与落地记录），全量 5058 passed / 4 skipped、
`ruff check .` 干净、契约重生成与 committed 逐字节一致。G2 动手前照前九片的样子先读
`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` 与架构里对应的那一段，把需求底稿写进这一份
再动手；预算是它的一个已知交点（`RunBudget` 的三类主语与 `BudgetLine` 的科目今天只
有 `SEARCH` / `DOCUMENT` 两个有消费者）。

### （历史）切片 9 动手前的那一段

§B22 有一句是本片与前八片形态上最大的不同，底稿必须正面答它：

> 当前 refresher 只支持本地 `source_coverage` census。R2 必须接入目标账号官网/
> 责任关系/联系人页面的**有界外部重查**；至少把 R2 产品承诺中的方面**全列为
> required**，而不是通过过滤掉未实现 aspect 得到全绿。

即：**判据不得靠「把没实现的那几格过滤掉」变绿** —— 那是一句对判据本身的要求，与
`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §19 的「绝不放宽 SOP 使数字好看」同一条。

---

## 切片 4（B13）需求底稿（**已落地，`915718a`，此节留作历史**）

三份权威里关于 B13 的话摆在一起，免得实现时凭印象。

### 权威一：`docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md` §8（草稿那一段）

逐字要做的：

1. **草稿准备与发送队列准备是「两个用例」。** Research v1 的草稿**不要求** SMTP、
   manifest 授权或已发送前序；后续正式外发仍必须通过全部独立安全门。
2. `A→B→C→D` 候选序列被 SOP 允许；**不存在全局 A/B-only 首触达禁令**。
3. **C/D 草稿需明确是部门/通用入口，不冒充具名决策人**，且不挤占 A/B 研究或容量。
4. **X 不进入正常草稿。**
5. 准入、当前性或绑定不够时只给**明确标注的 `review-only` / `held` 状态**，
   不称 `outreach-ready`。
6. 草稿冻结 **Account / Route / Scene / Policy / Writer 版本、事实/主张引用、正文哈希**。
7. 新证据或策略使旧草稿过时，**不能静默换文**。
8. **没有可起草路线时合法输出零份 + 具体原因。**

### 权威二：执行计划 §B13

- 落点：`application/outreach.py`、MCP product-loop/outreach adapters、
  template/writing-policy stores。
- 「既有模板/说法需**真正批准**；修订生新版本。」
- 「**不要仅把 `KEY_CONTACT_GRADES` 全局放宽**而意外改变真实发送执行器。」
- 关闭：已知合格 A/B/C/D fixture 各有合适草稿，X/未批准主张被拦；真实 cohort 中
  满足条件者可生成、无条件者说明原因；**不安装 SMTP 仍可生成**；默认无发送工具
  权限；结果由 MCP 直接回读，无后台人工桥。
- 回执：以**冻结对象集**对账 `accepted / held / excluded / failed / reused`
  及下一层写入。允许合法零输出，但**已知正例必须走到草稿**。
  「实体内容与证据关联验证不能仅检查非空/hash。」

### 权威三：上一轮真实验收为什么卡住（`RESEARCH_V1_ACCEPTANCE_FINAL_2026-09-25.md`）

**结论是 `NOT_READY_FOR_REAL_USE`，唯一不成立的一条是 `Draft count > 0`**：
`routes_considered: 22`、`planned: 0`、`skipped: 22`，
`skipped_reasons: {"等级不是关键联系人": 22}`。

那一条自己的诊断（F.1）：`plan_wave` 的等级闸门 `if route.grade.value not in
KEY_CONTACT_GRADES` **不读 `stage`** —— 它对 `stage-1` 到 `stage-4` 一视同仁，
而 `KEY_CONTACT_GRADES = {"A","B"}` 写的是**首次接触**的语义。

它的 F.2 明确否掉了「拆闸门」这条修法：那批 22 条全是 D/X，正确修法是给
`venue_asset.verified_official_domain` 一个真实生产者（**切片 1/B10 已做**），
而不是把闸门放宽。

### 由此得到的实现边界（开工时按此判）

* 那 22 条全 D/X —— 放宽闸门到 `{A,B,C,D}` **不会**让它们变成草稿；
  它只会让实现看起来满足了关闭条件而事实上没有。所以 B13 的验收样本必须是
  **fixture**（A/B/C/D 各一 + X 一个）**加上**一条真实 cohort 的读数，
  两条分开报。
* X 的处理不是「不生成草稿」那么含糊：要说得出**它没进哪一步**（被拦）以及拦它的
  那条规则。
* 「草稿准备不依赖 `plan_wave` 的授权」是一条**调用链**判据，不是一条措辞判据：
  要有一条判据在**没有** `authorize`/`execute` 的状态下真的产出草稿。

---

## 切片 5（B14）· Jev 真实因果引用与校准边界 —— 需求底稿

**对应：**TR-D03，验收 C.6。**落点（计划原文）：**`ports/decisions.py`、
`adapters/sqlite/decisions.py`、decision tools、责任/角色结果生产者。

### 计划原文（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B14）

> 报告说 `decision_record.work_item_id` 为 NULL，但当前 DDL 声明 NOT NULL，store 接受
> work item。**先核对 live schema、列、payload 和 API 序列化，不能照旧报告假定数据库
> 需补同一个字段。**
>
> 结果使用某一决策时，**显式写** `decision_record_id` / work_item / evidence /
> spec/version 关联；**证据集合与答案恰相同不是完整因果证明**。`answered` 不等于
> 正确，高/低置信都不能直接代替业务证据。Jev 是 assist；模糊建议可 hold，不能为
> 「57 条全 answered」标成全确认。
>
> **关闭：**真实必要判定可从**结果**反查具体调用；不存在的/跨任务决策引用被拒；
> replay 不重复计费；unavailable/abstain 被正确消费。不强迫每任务调用 Jev，不引入 Laya。

### 上一轮真实验收量到的事实（`RESEARCH_V1_ACCEPTANCE_FINAL_2026-09-25.md` §C.6）

`decision_record` **57 行**，全部 `provider=jev` / `model=jev-1.13.0` /
`outcome=answered` / `mode=assist`。而「被消费」只能按**最弱**的那条判据成立：
`account_venue_relation` 13 行里 10 行满足「证据集合与某条决策的 `evidence_refs` 完全相同
（且非空）**且**该决策选中的答案就是那条关系的值**且**那条决策带 `record_id`/`case_id`」。

那一节自己写下了它的弱处，逐字：

> **`decision_record.work_item_id` 全部为 `NULL`** —— 所以这条判据只能走**证据集合 +
> 答案取值**，不能走外键。这一点如实写下来：判据更弱，而它是这一版**唯一**能成立的那条。

以及：

> 差的 3 条**没有被解释成「Jev 判了别的」**：它们的证据集合与任何一条决策都对不上，
> 于是它们在这条判据下就是**不可判定**，不折算。

「证据集合恰好相同」之所以不是因果：同一个账号的两条不同判定可以引用同一批证据
（来源相同、问的问题不同），而**集合相等**分不出这两件事。所以 B14 要做的是把那条
隐式的关联换成**显式的一格**：结果上说得出「我这条结论是哪一次调用产生的」。

### 由计划原文直接得到的三条实现边界（开工时按此判）

1. **不假定 DDL 缺一格。** 计划逐字要求先核对 live schema / 列 / payload / API 序列化。
   若 `work_item_id` 在 DDL 里确实 NOT NULL 而真实库里全是 NULL，那说明**写路径**从没
   给它值（或者那份读数读的不是这一版），而不是「数据库需要补字段」——
   这两种诊断的下一步相反。
2. **显式引用要能双向对得上**：结果 → 决策（从一条关系反查到产生它的那次调用），
   且引用**不存在**或**跨任务**时**被拒**（不是记一条备注）。
3. **`answered` 不是 `confirmed`**：`unavailable` / `abstain` / 低置信的模糊建议要能
   **hold**，而「57 条全 answered」不得被折算成全确认。Jev 不强制每个任务都调。

### 取证结论（2026-09-27）：报告的「`work_item_id` 全为 NULL」**复现不出来**

计划要求先核对的那一条诊断，在**仓里**与**真库上**都不成立：

| 核对项 | 读数 |
|---|---|
| `marketops/ports/decisions.py` 的记录模型 | 17 个字段，`work_item_id` / `subject_ref` / `recorded_at` 是**存储参数**，由 sqlite 适配器 `_row()` 合并进返回对象 |
| sqlite 适配器的 DDL | `work_item_id TEXT NOT NULL REFERENCES work_item (work_item_id)` —— **NOT NULL 且是真外键** |
| 全机 16 个 `marketops.db`（只读 `file:…?mode=ro` 扫过） | 只有 `artifacts/marketops-acc-final/run2/state/marketops.db` 有 57 行判定；该库 `null_work_item = 0` |

⇒ 两个候选诊断（「数据库需要补这一格」与「写路径从没给它值」）**都不对**。这一格
一直存在、一直有值，所以那条最弱的反查判据（证据集合 + 答案取值）**不是因为外键
读不出来才退而求其次的** —— 上一轮那份报告把自己读到的 NULL 归因错了，而它据此
写下的那句「判据更弱」于是也建立在一个不成立的前提上（判据仍然弱，但弱在**反查**，
不弱在**外键**）。B14 要做的事因此不是补一列，而是把那条关联**写到结果行上**。

### 落地：连线从「推出来的」换成「写下来的」

`AccountVenueRelation` 新增 `decision_record_ids: list[str]`（规范 id 校验，缺省空表）：

* **写**：`remember_researched_relations` 每条存活结论写 `[finding.decision_id]`，而那个
  id 是**已经回台账核对过**的那一个（五条判据：解得开 / `work_item_id` 是这一件 /
  `subject_ref` 是这个账号 / `spec_id` 是责任关系那道题 / `outcome == answered` 且答案
  等于结论的 relation）。手工路径（`remember_responsibility`）写**空表** —— 它手里
  没有、也不该有判定台账。
* **合并**：取并集、保序、去重，与 `evidence_ids` 逐字同一条规则。只留最新那一个 id
  会让更早那次调用从结果上反查不到，而它带进来的证据还在这一行里。
* **读**：写回执（`_relation_outcome`）与 `list_responsibilities` 两个方向都报出这一格；
  没写进去的行给 `None`（那一行不在表里），空表只说一件事 ——「这一行不是判出来的」。
* **不能伪造**：这一格**不**在 `RelationClaim` 上（它是 `extra="forbid"` 的），而是
  `remember_relations` 的**按位置对齐**参数，只由本模块内部那一条承重生产者填。交行
  的人多带一格 `decision_record_ids` ⇒ 整行 `rejected`（长度错位则一行都不写）。
* **不需要迁移**：这一行的正文在 `payload`（JSON）里，旧行缺键时用缺省值仍能
  `model_validate`；`SCHEMA_VERSION` 不动。契约要重生成（`AccountVenueRelation` 在
  `CONTRACT_MODELS` 里）—— `contracts/account-venue-relation.schema.json` 多一格、
  描述多两节，这是本次**唯一**的契约 diff。

### 四条关闭条件逐条对照

| # | 关闭条件 | 承载它的判据 | 状态 |
|---|---|---|---|
| (a) | 真实必要判定可从**结果**反查具体调用 | `test_the_result_names_the_call_that_produced_it`（端到端：行只指名真的那一次，四格关联全对，**长得一模一样的干扰项**不被指名） | 已覆盖 |
| (b) | 不存在的 / 跨任务 / 跨题面的引用被拒 | 不存在：既有 `evidence_unknown` 一族；跨任务：`test_a_decision_from_another_work_item_is_refused`（真台账）；跨题面：`test_a_decision_about_another_question_is_refused`（拿 `PAGE_TRIAGE` 那道题判一次） | 已覆盖 |
| (c) | replay 不重复计费 | `test_a_replayed_judgement_is_not_paid_for_twice`（三次调用**逐次**断言：`replayed` 与 `len(backend.calls)`；并按 case 回读存储）+ 既有 `test_the_same_case_asked_of_two_backends_is_two_records_and_two_payments`（幂等键三段里 `provider` 那道） | 已覆盖 |
| (d) | `unavailable` / `abstain` 被正确消费 | 参数化两条 `DecisionOutcome`，并断言理由里报出**哪一种** | 已覆盖（限责任关系那道题，见下） |

### 变异验证：每条新判据都承重（9 条，全部被抓住）

改一处 → 清 `__pycache__` → 跑那一条判据 → 复原 → `cmp` 逐字节确认（记忆
`pyc-cache-defeats-same-size-mutation-reverts`）。

| 变异 | 抓它的判据 |
|---|---|
| 并集 → 只留最新那一个 id | `test_the_decisions_accumulate_and_never_replace` |
| 错位的长度前置检查整条去掉 | `test_a_decision_column_that_does_not_line_up_is_refused_before_any_write` |
| 按位置对齐那一格恒为空 | `test_each_row_names_the_decision_that_produced_that_row` |
| 没写进去的行给空表而不是 `None` | `test_the_rows_that_did_not_land_are_named_by_disposition` |
| 读表那条路不再报这一格 | `test_a_judgement_lands_and_the_read_joins_the_venue_name` |
| 规范 id 校验改直通 | `test_a_decision_id_on_a_relation_is_a_canonical_id_too` |
| 题面那条判据改成「只拒空 `spec_id`」 | `test_a_decision_about_another_question_is_refused` |
| 付费前那次幂等查询整条短路 | `test_a_replayed_judgement_is_not_paid_for_twice` |
| 幂等键里去掉 `provider` 那一段 | `test_the_same_case_asked_of_two_backends_is_two_records_and_two_payments` |

### 剩余缺口（如实登记，不假装覆盖）

1. **`ROLE_FUNCTION` / `PAGE_TRIAGE` 那两个 task 的判定结果在仓里没有任何消费者。**
   所以「结果反查调用」与「`unavailable`/`abstain` 被正确消费」这两条只在**责任关系**
   那道题上成立；另外两道题的记录写得下来、读得回来，但没有任何下游拿它推结论。
   这一条不因为本切片而改变 —— 它是 B14 范围里**唯一**没有被覆盖的部分。
2. **置信度今天没有任何消费者。** `confidence` / `probabilities` 只在记录与回执里如实
   带着（`ports/decisions.py` 第三条：不补缺省、不从概率推标签），全仓没有一处用它
   做分级或放行。所以「高/低置信不得代替业务证据」在代码上是**结构性**成立的，代价
   是 Jev 报的置信度也**没有**被用来做任何事 —— 这一条与计划的意图一致，但它是「没有
   那条路」，不是「那条路被挡住了」。

---

## 切片 6（G1）· 无人工桥的 R1 纵向验收 —— 需求底稿（**已落地，`d626bf8`，此节留作历史**）

**对应：**执行计划 §G1。它**不是**一个 B 切片：G1 没有「新增一处生产代码」的落点，
它要的是一条**能从 MCP 实际走通的纵向链**加一组**必须失败的对照**。

### 计划原文（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §G1）

> 先跑 3–5 个**有出处**的结构样本：已知正例、无邮箱、同名异地、共享域/第三方官网、
> 冲突记录。合成 fixture 与真实公开样本**分开**；人提供测试种子不等于生产路径允许
> 人工拼 Account。
>
> 顺序：Scene → discovery/import → 准入/identity → cohort → Quay research →
> Jev（确有用途）→ 唯一路线/拒收 → 草稿/合法 hold。**全程通过一个 MCP，从零手工
> roster 开始。**
>
> 小样通过再用已批准的预算扩大 cohort。**不要每个局部修复都重新花一整轮 20–50
> Account 成本**；优先用已取得工件重放，最后对相同代码构建做有界 live run。
> **没有授权预算只阻塞 paid 验收。**
>
> **必须失败的对照：**错国家被标 eligible、目录别人的邮箱被绑定、人工 YAML 是必需、
> 重复 route 计新增、空正文声称镜像成功、X 被包装为关键联系人、跨 workspace 证据、
> 单页坏时间使全批无回执。

### 三条由原文直接得到的边界（动手前先钉住）

1. **「有出处」与「合成」要分得开。** 五类样本各自要有出处（哪一次真实验收/哪一条
   缺陷留下来的形状），而夹具本身是合成的。报告里两栏分开写，**不把合成样本读成
   真实公开样本**。
2. **paid 验收与纵向验收是两件事。** 本机没有新的 Serper / Jev 授权预算 ⇒ 真实付费
   那一段是 `BLOCKED`（预算）。但链的**结构**（每一步真的走 MCP、真的落盘、真的按
   业务键对账）不需要付费就能验证 —— 假检索与假 HTTP 是本仓既有的做法
   （`tests/test_mcp_discovery_chain.py` 的模块 docstring 逐字：假检索 + 假 HTTP、
   零网络、零 key）。**用假后端跑通不算「live run 通过」**，报告里必须写明这一轮
   的量具是什么。
3. **八条「必须失败」的对照各自要有执行器。** 已经有执行器的那几条**照旧复用**
   （G1 不重测别处已经量过的东西），没有执行器的那几条要**补**上 —— 而「没有
   执行器」本身是 G1 要报出来的缺口之一。

### 交付物（形态）

一个纵向验收文件（外加它暴露出来的仓内缺陷的修复）：从**干净数据根**开始、**一个**
MCP 会话、只用一个 controller 令牌（`Role.CONTROLLER` —— 目录里它的「它是谁」是
写下来的：「Claude Code。决定研究什么、经营哪个市场、读报告」），把 §G1 那八步
按顺序走一遍，每一步的读数都从工具回执里取，中间不手工拼 Account、不写 YAML。
八条对照各自落到「哪一条判据让它失败」上。

---

## 切片 6（G1）· 落地记录（`d626bf8`）

### 落到盘上的东西

| 面 | 文件 | 形态 |
|---|---|---|
| 纵向链 | `tests/test_r1_vertical_acceptance.py`（**5 条**） | 一条九个阶段的链（`_run_chain`）+ 三条判据 + 一张八条对照表 |
| 那一节断链的修复 | `domain/enums.py`、`application/inputs.py`（第四路 `subjects_for`）、`interfaces/mcp/__main__.py` + `adapters/mcp_server/{tools,server}.py` | `JobInputKind.COHORT` + 组合根接线 |
| 词表与枚举一起长 | `tests/test_sqlite_job_kind.py`（**4 条**）、`tests/test_sqlite_migrate.py` 的 v5→v6 一段（**5 条**） | `db.JOB_TABLE_DDL` 点名的那个执行器，加一次手写真迁移 |
| 工具面 | `tests/test_job_inputs.py`、`tests/test_mcp_research_tools.py`、`tests/mcp_harness.py` | 按 cohort 受理的正面与反面 |

### 九个阶段各自判在哪一条上

| 阶段 | 判据 | 读数（断言的核心） |
|---|---|---|
| Scene | `test_the_whole_chain_runs_from_a_clean_root_on_one_mcp` | `scene.state == "active"` |
| discovery / import | 同上 | 上游工件 → 回执 → 晋升，池子里有那几个账号 |
| 准入 / identity | `test_a_cohort_member_without_identity_clues_refuses_the_whole_batch` | `state == "blocked"`、`work_items.count == 0`、理由里点出那个成员 |
| cohort | 链那一条 + `test_mcp_cohorts.py::test_a_clean_data_root_never_grows_a_yaml_list` | 名单不落 YAML，`inputs_dir` 仍然一个 `.yaml` 都没有 |
| Quay research | 链那一条 | `submit_research(input_kind="cohort")` —— **这一步在 G1 之前没有入口** |
| Jev | `test_the_jevs_step_hands_over_the_specs_and_forces_no_call` | 交出 `decision_specs`，而台账**一行都没有** |
| 唯一路线 / 拒收 | 链那一条 | 回执条数 == 表里行数；`view["rejections"]["by_code"]["unbound_email"] == 1` |
| 草稿 | 链那一条 | `subjects.kind == "cohort"`、`cohort_id` 就是刚冻的那一份、`send_authorized is False` |
| 合法 hold | `test_a_route_that_was_never_judged_is_review_only_and_leaves_named` | `accepted == 1 and review_only == 1`；`prepare_wave` 只放行 1 条且**点名排除**并带理由 |

### 八条「必须失败的对照」逐条落到执行器上

`CONTROLS` 是一张**可执行**的表（`test_every_control_names_a_real_executor`）：每条点名
`路径::测试函数名`，那条执行器必须真的在盘上、真的是那个模块里的一个函数。它挡的是
「某一天有人把那条测试改了名或移走了，而这张表照旧读得通」。**它不证明那些判词定得
对** —— 「指向哪里」可执行，「说得准不准」只能靠人读（这条分界在
`tests/test_job_inputs.py` 里已经写过一次）。

八条一共点名 **16 处**执行器，散在 **12 个文件**里，其中**只有 1 处**是本文件的那条
链。也就是说：**七条对照整个落在别的文件上**，一条（「人工 YAML 是必需」）除了
`tests/test_mcp_cohorts.py` 那一条之外，第二个执行器**就是**本文件那条链 —— 它真的
从干净数据根走完了全程，而一条后台写影子 YAML 的实现会让它在阶段 3 就红。

这是刻意的：G1 不重测别处已经量过的东西。它要报的是「每一节都有人守着」，而不是
把 16 条判据抄一遍 —— 抄一遍的代价是它们会分家，而分家之后两边都读得通。

### 变异验证：每条新判据都承重（4 条，全部被抓住）

改一处 → 清 `__pycache__` → 跑那一条判据 → 复原 → 逐字节确认（记忆
`pyc-cache-defeats-same-size-mutation-reverts`）。

| 变异 | 抓它的判据 | 变异后的表现 |
|---|---|---|
| `cohort_subjects` 里 `if refusals:` 整条去掉 | `test_a_cohort_member_without_identity_clues_refuses_the_whole_batch` | 从「一件工作都不建」变成**派发 3 件真工作单元** —— 这一条挡的是真金白银 |
| `_plan_job_kind` 里 `if on_disk == declared:` → `... or True` | `test_a_v5_database_widens_the_input_kind_vocabulary_and_keeps_every_row` | 红在 `assert report.jobs.rebuilt is True`：报告说「已经一致」而盘上还是三个词 |
| `_require_job_rows_kept` 的 `SELECT {joined} FROM research_job` → 加 `WHERE 0` | 同上（第二条用例） | `StoreError` 同时点出**缺的 job 键**与 `work_item.job_id` 那个孤儿父行 —— 事务内对账真的会响 |
| `db.JOB_TABLE_DDL` 的 CHECK 去掉第四个成员 | `test_sqlite_job_kind.py` 的 3/4 条 | 第三条红在一句真的 `IntegrityError: CHECK constraint failed: input_kind IN ('source_receipt', 'known_accounts', 'discovery_scope')` —— **v5 那次事故按需复现** |

### 剩余缺口（如实登记，不假装覆盖）

1. **paid 那一段是 `BLOCKED`（预算）。** 链上零真实网络调用：检索与抓取是夹具，人
   提供的是测试种子。这份文件证的是**结构接得上**，不是「一次 live run 通过了」。
   本机没有新的 Serper / Jev 授权预算，按 §G1 末尾那句「没有授权预算只阻塞 paid
   验收」记 BLOCKED，**不拿假后端冒充**。
2. **`_plan_job_kind` 的 `present=False` 分支走不到。** `migrate()` 先跑 `replay_ddl`，
   所以「表不存在」在真命令上不可达；那一条用直接调用钉住，并且在测试里写明了这是
   刻意的。它是**计划器**的分支被覆盖，不是**命令**的路径被覆盖。
3. **八条对照表只证明执行器存在。** 判词对不对要靠人读；这一条分界写在
   `test_every_control_names_a_real_executor` 的 docstring 里。
4. **G1 的五类结构样本只走到了其中一类。** 底稿逐字列的是「已知正例、无邮箱、同名
   异地、共享域/第三方官网、冲突记录」五类，而这一条链上真的走到的只有**一类**：

   | 底稿里的那一类 | 这条链走了吗 | 依据 |
   |---|---|---|
   | 已知正例 | **走了** | `_contact_page` → 具名角色 + 本组织域邮箱 → B 级路线落库 |
   | 无邮箱 | **没走** | 链上的拒收是 `unbound_email` —— 「**有**邮箱，但说不出这是谁」，与「没有邮箱」是两种形状 |
   | 同名异地 | **没走** | 两个成员的组织名与主机名都不同（`example-village` / `example-sports`） |
   | 共享域 / 第三方官网 | **没走** | 两个成员各一个独立域名，没有「同域两个俱乐部」那种形态 |
   | 冲突记录 | **没走** | 链上没有任何一处两行互相矛盾 |

   后四类在别处**有**覆盖（`tests/test_domain_attribution.py`、
   `tests/test_paired_runner.py`、`tests/test_grading.py` 各自那一档），但**不是**
   从这条纵向链上走的，所以它们证的是「那一层的判据在」，不是「链在这一类样本上
   接得上」。**报告里必须按实际走到的写：五类里一类。** 这一条是底稿第 1 条边界
   （「有出处」与「合成」分得开）的另一半 —— 走到的与没走到的都要写出来，不然
   「五类样本各有出处」在报告里会读成「五类都验过了」。

---

## 切片 7（B20）· 有效策略版本 + 最小 Review Inbox —— 需求底稿

**对应：**执行计划 §B20；架构 §9（策略版本）与 §12（Review Inbox）。
**这一片有两个半，而它们之间的接缝是这一片最要紧的一条判据。**

### 计划原文（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B20）

> **落点：**现有 `SceneVersion/scene_impact`、profile 与 writing rules，新增必要人工
> 审核用例/持久记录。
>
> 先连接现有策略来源、版本、批准与运行快照，**不造新通用规则语言**。需要人工的案件
> 使用 `ReviewCase/HumanResolution`，**不要覆盖现有 Jev `DecisionCase`**。
>
> 提供少量类型化 MCP 用例：列待办、读证据/影响、认领、按 expected_version 提交裁决。
> 单案纠错默认不发布全局 policy；批量裁决固定对象与共同前提，过期版本需重新检查。
>
> **关闭：**规则提议不自动生效；批准一次后符合该规则的后续对象不重复问；新旧版本
> 影响预览可复现、历史不变；ReviewCase 去重/重启/并发审批不丢；一个案件 hold 不堵
> 无关 Account；权限/过期/跨域批量均有拒绝测试。

### 权威二：架构里决定形态的那几句

§9（策略是现有版本机制的扩展）：

> 复用 `SceneVersion`、`scene_impact`、ProfileBundle、角色矩阵和 WritingPolicy。
> 逻辑上增加有效 PolicyBundle：明确规则来源、scope、版本、审批主体、编译/解释器
> 版本、内容哈希及生效范围。
>
> 优先顺序：服务硬限制不可被下层覆盖；业务授权范围不可由模型扩大；Scene 的具体
> 规则必须显式覆盖允许覆盖的字段；同优先级冲突必须报告，**不按「最后一条聊天」
> 静默取胜**。预算取适用限制中的有效最严边界。
>
> 变更流程：提议 → 类型/冲突校验 → 固定样本重放与影响预览 → 获授权者批准 → 激活
> 新版本。worker 的方法建议**不能自改评分规则**；从历史 session 提取到的规则**也
> 不能自动生效**。
>
> 运行中的语义判断**固定原版本**；立即停止、撤销权限、更新禁用边界在**下一次受控
> 动作前**生效，不能被旧快照绕过。……修正单个事实不等于发布全局规则。

§12（人只决策，系统准备和执行）：

> 不是每个 unknown 都找人：可低成本补证的交研究，无公开资料的保留 no-route，
> **政策/身份冲突且值得处理的**才入 Inbox。
>
> 提交裁决验证可信 principal、权限、`expected_version` 与幂等键；**过时案不写回
> 新事实**。裁决后自动重评受影响对象，保留历史和恢复进度。单案裁决**默认不成为
> 全局策略**，策略提升走 §9。
>
> 待办是**累计状态**，不是每轮覆盖的窗口；`pending_start + opened/reopened -
> resolved/dismissed = pending_end`，另记合并/转移。自动化目标不是消灭全部人工
> 授权，而是让 Member-A **一次批准之后不再重复填相同答案**。

§14（部署约束）：

> MCP 新工具**只为用户动作增加**，不暴露全部数据库 CRUD；新增工具**必须与角色目录、
> schema 和兼容测试同交付**。

### 由原文直接得到的边界（动手前先钉住）

1. **不造新通用规则语言。** 规则来源、版本、批准与运行快照都接现有的
   `SceneVersion` / `scene_impact` / ProfileBundle / 角色矩阵 / WritingPolicy。
   新东西只允许是**逻辑上的**那一份 PolicyBundle：来源、scope、版本、审批主体、
   编译/解释器版本、内容哈希、生效范围 —— 都是**指向既有东西**的坐标，不是一套
   可以描述任意规则的小语言。
2. **`ReviewCase` / `HumanResolution` 与 `DecisionCase` / `DecisionRecord` 分开。**
   不覆盖、不复用、不把「模型推理」与「人工裁决」存成一行。两者的差别正是这一片
   要报出来的东西（谁判的、按什么判的）。
3. **接缝：单案裁决默认不成为全局策略。** 「批准一次后不重复问」有两种读法，而
   只有一种成立：**同一个问题**不再问第二遍（去重），**不是**同类对象自动适用
   （那需要走 §9 的提议 → 批准 → 激活）。把两种读法合成一条的实现会让
   「规则提议不自动生效」这一条关闭条件**当场不成立** —— 所以这两条关闭条件
   是一句话的两半，必须一起测。
4. **入 Inbox 有门槛，而门槛要有执行器。** 原文逐字排除了两类（可低成本补证的 →
   交研究；无公开资料的 → 保留 no-route）。所以「全量进 Inbox」是本片要挡的
   反面；门槛必须是一处**写得出来、测得出来**的判据，而不是「看起来该找人」。
5. **待办有一个会计恒等式。** `pending_start + opened/reopened - resolved/dismissed
   = pending_end`，合并与转移另记。它是这一片唯一一个**可以对账**的读数，
   所以它要有自己的执行器。
6. **两类变更的生效时刻不同**（§9 最后一段）：语义判断**固定原版本**；而
   停止 / 撤销权限 / 更新禁用边界在**下一个受控动作前**生效，且不能被旧快照绕过。
   合成一条「生效时刻」会让其中一个方向静默错掉 —— 而两个方向的表现都是
   「报告上说新版本已激活」。
7. **批量裁决不能凭语义相似扩规则。** 必须固定**适用对象与相同前提**；跨国家/
   跨 workspace 的批量是拒绝测试的第一条。
8. **新工具与角色目录、schema、兼容测试同交付**（§14）。

### 这一片最大的风险：Inbox 没有生产者

`ReviewCase` / `HumanResolution` 今天在仓里**只有散文**（全仓 grep 只命中两份文档，
零个模型、零张表、零个工具）。而「列待办恒返回空」与「这个功能不存在」在报告上
**长得一样** —— 这正是切片 6 抓到的那类缺口（一条链上有一节没有入口，而三段各自
全绿）。所以这一片必须同时交付**一个真的生产者**：至少一处真实的判定路径，
在走到「政策/身份冲突且值得处理」时**把案件落到库里**，而不是只在报告里说一句
「需要人工」。

门槛按原文写成闭集（照本仓既有的做法：`JobInputKind`、`SourceKind`、
`BINDING_ELIGIBLE_SOURCES` 都是闭集 + 「每个成员要么有生产者、要么有一句写下来的
理由」的执行器）。**没有生产者的成员要有一句话说明为什么它今天不产生案件**，
否则「门槛」会变成一张比实际能力长的清单。

### 关闭条件逐条落到哪一类判据上

| # | 关闭条件 | 判据的形态 |
|---|---|---|
| (a) | 规则提议不自动生效 | 提交一条裁决**之后**，同一个对象的下一次判定**读数不变**；只有走完提议 → 批准 → 激活才变 |
| (b) | 批准一次后符合该规则的后续对象不重复问 | 同一个问题第二次出现时**不新开案件**；并且这一条要与 (a) 一起测（见边界 3） |
| (c) | 新旧版本影响预览可复现、历史不变 | 同一份固定样本跑两次，预览逐字节一致；而**旧版本的读数在新版本激活后仍然读得回来** |
| (d) | 去重 / 重启 / 并发审批不丢 | 去重按内容寻址的 case id；重启后重放不产生第二个案件；并发提交带 `expected_version` 的那一条被判过期而**拒**（不是后写覆盖） |
| (e) | 一个案件 hold 不堵无关 Account | 有未决案件时，**无关**账号照常走完；受影响的那个如实报阻塞 |
| (f) | 权限 / 过期 / 跨域批量均有拒绝测试 | 三条各一条拒绝测试：角色不足、`expected_version` 过期、跨 workspace/跨国家的批量 |

### 取证已经得到的一条：批准必须是**另一个人**，不能是提议者自己（**范围见下面「取证结论二」，已收窄**）

`marketops/security/catalog.py:76-95` 里 `Role.AUTHORIZER` 的 docstring 逐字写着它
存在的理由：

> ``AUTHORIZER`` —— **一个人**，而不是一个代理。……它的令牌只铸来登记授权记录，
> 别的什么都做不了，因为这一格的全部价值就是**它不是一个代理能自己走到的位置**。

而 §9 要求「**获授权者**批准」，§12 要求「提交裁决验证**可信 principal**、权限」，
`docs/ARCHITECTURE.md:52` 又写着「网页/模型/跨线程文字不能成为修改权限或批准发送的
来源」。三句话合起来是一条**已经在本仓下过**的判决在 B20 上的延伸：

* **激活一个新策略版本**要 `AUTHORIZER`。给 `CONTROLLER` 开一个 `activate_policy`
  会让「谁提议的」与「谁批准的」合成同一个主体 —— 而那正是 `authorize_wave`
  当初被单独拎出来时拒绝的那件事。**控制器可以提议，不能批准。**
* **单案纠错**（不发布全局策略的那一类）走 `CONTROLLER` 是够的：它改的是一个对象
  的一项事实，而 §9 逐字说「修正单个事实不等于发布全局规则」。两个档位对应
  §9/§12 已经分开的两件事，不是新发明的分级。
* 「principal 从令牌来、不从 payload 来」这条本仓已有先例
  （`test_mcp_product_loop.py::test_activating_records_the_principal_from_the_token_not_from_the_payload`），
  B20 照抄，不发明第二套。

这一条先记在这里，是因为它决定了工具怎么分、角色怎么给 —— 而那是这一片最早要动的
东西，动手之后再改代价最大。

### 取证结论一：现有版本机制比底稿假设的完整得多（**底稿那一节要按这一节修正**）

`SceneVersion` / `SceneRecord` / `activate` **已经存在**，而且「提议 → 校验 → 影响预览
→ 批准 → 激活」这条变更流程**已经是代码**，只是没有用那三个名字：

| 变更流程里的一步 | 今天的落点 |
|---|---|
| 提议 | `application/scenes.py:163 add_version` ← MCP `add_scene_version` |
| 类型/冲突校验 | `application/scenes.py:84 validate` → `SceneValidation` |
| 影响预览 | `scene_impact(before, after)`（`domain/scene.py:625`），随 `add_version` 回执出 |
| 批准 + 激活（**同一步**） | `SceneRecord.activate`（`domain/scene.py:471`）← MCP `control_scene(action="activate")` |
| 审批主体 | `SceneApproval{version, at, by}`，`by` 取自**令牌**（`_require_principal()`），不是参数 |

所以 B20 的「有效策略版本」不是从零造，而是**往这套上补三处缺的东西**：

1. **影响预览没有读的工具。** `application/scenes.py:250 impact_between(store, scene_id,
   from_version, to_version)` 存在，**没有任何 MCP 工具接它** —— 它只在 `add_version`
   的回执里露一次脸。而 §B20 明写要「读证据/**影响**」。这是一处真实的接线缺口。
2. **规则的来源没有被记下来。** §9 要求有效 PolicyBundle 明确「规则来源」。今天
   `SceneVersion` 有 `note: str`（自由文本）与 `supersedes`，**没有**一处说得出
   「这一版是对哪一个提议/哪一件待办的答复」。而这一格正是「规则提议不自动生效」
   那条关闭条件**唯一能挂上去**的判据。
3. **`approvals` 与「谁提议」没有关系。** `activate` 记 `by`，但没有任何东西阻止
   同一个主体先 `add_scene_version` 再 `activate` —— 也就是说**一个模型可以自己提议、
   自己批准**。这正是 §9 那句「worker 的方法建议不能自改评分规则」要挡的东西。

### 取证结论二：**不改** `control_scene(action="activate")` 的角色（推翻我上一节那句更强的写法）

上一节我写的是「激活新策略版本要 AUTHORIZER」。取证之后这条要收窄，理由有两条，
而第二条是硬的：

* `activate` 今天要 `_CONTROL = {CONTROLLER}`（`security/catalog.py:354`），而
  **切片 1–6 的每一次验收、以及上一轮真实验收报告都建立在这条既有行为上** ——
  `create_scene(activate=True)` 是正常入口。把它改成要 AUTHORIZER 是**改动一条已经
  被批准、已经被验收过的业务行为**，按 §0 那属于 BLOCKED 的处置，不该在这一轮顺手做。
* 更要紧的是**它改了也没用**：要独立批准的是「**一条规则的变更**」，而不是「把一个
  Scene 立项并激活」。把 `activate` 一律收紧会让「站立一个市场」也要第二个人，
  而那既不是 §9 说的那句话，也会让这条判据在一堆无关的线上红。

**收窄之后的做法**：控制器照旧可以提议与激活**普通**版本（既有行为逐字不变）；
而当一版**是由一件待办/提议来的规则变更**时，它多一个「等批准」的状态 ——
通过 `control_scene` 激活它会被**拒**并指名该走哪个工具，只有 `AUTHORIZER` 能批准。
这样「模型可以自己提议、自己批准」这条缝被堵在**它真正存在的那一处**，而现有
每一条线都不动。判据的形状也因此变得干净：**同一份提议，控制器走哪条路都激活不了，
换一个主体的令牌才激活得了。**

### 待取证（动手前，见下两节）

* `SceneVersion` / `scene_impact` 的确切形状（能不能挂「审批主体」与「内容哈希」）；
* `DecisionCase` / `DecisionRecord` 的形状（避免重名、避免看起来像同一族）；
* MCP 工具注册、存储与 DDL 自愈的既有接线方式（照抄，不发明）；
* 既有 `expected_version` / 幂等键 / 认领-租约那一族惯用法。

---

## 切片 7（B20）· 落地记录（`949452c` + 本次工作树）

### 落到盘上的东西

| 面 | 文件 | 形态 |
|---|---|---|
| 领域 | `domain/review.py`（**新**） | 两件对象（`ReviewCase` 当前投影 / `HumanResolution` 只增不改）、`INTAKE` 选项表、`may_decide`、`apply_resolution`、`claim`、`inbox_flow` 与那条恒等式、`awaiting_approval_for` |
| 端口 | `ports/review.py`（**新**） | 五个抽象方法（`open_case` / `get_case` / `list_cases` / `claim` / `record_resolution` / `list_resolutions`），无第二把问案件的钥匙 |
| 存储 | `adapters/sqlite/review.py`（**新**）、`adapters/sqlite/db.py`、`adapters/memory.py` | 两张表（`review_case` 是**当前投影**、`human_resolution` **只增不改**），`CREATE TABLE IF NOT EXISTS` + 每次构造 `replay_ddl` 自愈；内存替身供契约测试对跑 |
| 编排 | `application/review.py`（**新**） | 四样 → 一件**内容寻址**的待办；一条决定折进去之前要跨的**跨对象**判据（提议挂的版本要存在、批准的人不是提议的人）；`block_activation` 那半条闸门 |
| 生产者 | `adapters/mcp_server/tools.py`、`application/inputs.py` | `SubjectResolution.identity_refusals` —— 被**身份判据**拒掉的那几行在**拒绝发生的那一处**落成待办；四种处置（新开／已未决／已裁决／写不进去）各报各的，一个都不压成另一个 |
| MCP | `adapters/mcp_server/review.py`（**新**）、`security/catalog.py`、`adapters/mcp_server/product_loop.py`、`server.py`、`interfaces/mcp/__main__.py`、`interfaces/composition.py` | 七条工具（全部 `phase="J"`）、`default_review_store`、`control_scene(activate)` 上那条闸门 |

### 七条工具与那一张角色矩阵

| 工具 | 角色 | 数 |
|---|---|---|
| `list_review_cases` / `get_review_case` | `_REVIEW_READ = {CONTROLLER, AUTHORIZER}` | 授权者也要读得到自己批的是哪一件 |
| `get_review_flow` / `claim_review_case` / `resolve_review_case` / `get_scene_impact` | `_CONTROL = {CONTROLLER}` | 做事的四条 |
| `approve_policy_change` | `_AUTHORIZE = {AUTHORIZER}` | **不在控制器手上** |

矩阵实测为 **控制器 6 / 授权者 3 / 研究者 0**（`test_the_role_matrix_matches_the_catalog`，三角色各一格）。研究身份那七条**逐字与「工具不存在」同形** —— 逐条比而不是只比第一条，否则后面几条的措辞分歧漏得掉。

`_REVIEW_READ` 比 `_CONTROL` 宽一格，而那一格是承重的：授权者读不到那一件，批准就发生在看不见案子的人手里 —— 那正是 §9 那句「**一个人**，而不是一个代理」被架空的方式：批准成了盖章。

### 缺陷一（实测）：批准路径**完全不可达**

`may_decide` 原来只有 `REOPEN` 一个例外，于是 `APPROVE_POLICY_CHANGE` 在存储那一层落进
「它在不在 `case.options` 里」被拒 —— 而生产路径的 `options` 来自 `INTAKE`（按原因给的
三个答案），`apply_resolution` 进 `AWAITING_APPROVAL` 时**不重写它**。后果：

> 一个人提议之后**谁也批不了**；而报告上只显示「没有人批准过」—— 一条死路与一件没人
> 做过的事在那一格上是同一个样子。

修的是 `may_decide` 的例外分支（与 `REOPEN` 同一条理由：两者都是「对一件**已有答案**的
东西的处置」，不是作者写给人看的那几项里的一个）。判据落在
`test_approval_is_available_without_being_declared_in_the_options`（正反两面 + 「提议不
重写选项表」），MCP 那一层另有 `test_only_the_authorizer_approves_and_the_proposer_is_reported`。

顺带钉住的是**传输层的不可表达性**：`refuse_collapsed_gate` 让一枚同时持
`CONTROLLER` 与 `AUTHORIZER` 的令牌**铸不出来**，`TokenRing._from_payload` 另有一条
`by_principal`（同一主体不得出现两次）。所以「同一个人既提议又批准」不是一个要靠自觉
避开的操作 —— 它**构造不出来**。`test_a_collapsed_gate_cannot_even_be_minted` 把这件事
本身钉住，于是那条「批准的人不是提议的人」的测试不必再靠一个造得出来的「双角色」夹具。

### 缺陷二（实测）：一件关掉的案件可以用**另一个**关闭结果重关，恒等式当场差一

`apply_resolution` 那条「结果状态必须与当前状态不同」挡的是**同一条**决定放两次
（`DISMISS` → `DISMISS`），它挡不住**两条不同的关闭决定**：`RESOLVED` → `DISMISSED`
是一次真的状态迁移，因此一路放行到盘上。而 `inbox_flow` 逐事件计数，实测：

```
equation : 1 + 0 + 0 - 1 - 1 - 0 = 0      reconciles: False
```

一件案件离开待办**两次**，而它只开了一次、也没重开过。这件事流水账那一层**无法**自己
发现 —— 它只看得见「关过一次、又关过一次」，两次用的都是合法结果。这与那一族自己的
docstring 也矛盾：它写着「要改一件已经裁决过的案件，先重开它（REOPEN）」。

修法是把 `may_decide` 的那条判据写全（两半并排看才看得出是一件事）：

```python
if outcome is ResolutionOutcome.REOPEN:
    return case.state in CLOSED_STATES          # 关掉的才谈得上「重新打开」
if case.state in CLOSED_STATES:
    return False                                # 关掉之后唯一还有意义的动作是重开
```

判据：`test_a_closed_case_takes_only_a_reopen`（域层，正反 + 重开循环的账对得上）与
`test_an_outcome_the_case_cannot_take_is_refused`（存储契约，两个实现各跑一遍）。

**同一条判据的两条旧判据都保留不动**，而它们各自钉的是另一半：

* `test_a_decision_must_actually_change_the_state` 的反面那一半（「连做两次不同的决定
  仍然放行」）**改了夹具**：原来用「改对象 → 放下」，而那一对正是上面这个缺陷。现在用
  「提议 → 改对象」—— 两条决定的结果状态不同，而案件**只离开待办一次**（提议把一件
  未决的换成了另一件未决的，账面上什么都没变）。
* `test_a_reopen_and_a_second_dismiss_still_reconcile` 原样保留：关掉 → 重开 → 再关掉
  要对得上账，而重开之后重关一次是那条恒等式**允许**的循环（一件回来过，所以它离开
  两次也成立）。新判据与它并排，正反两面才是完整的。
* `test_review_store_contract.py` 那条夹具里的 `options=(APPROVE_POLICY_CHANGE, DISMISS)`
  **手写了一张生产路径上不存在的表**。它的断言（不能选的结果整次拒绝）是真判据、保留，
  但夹具换成 `INTAKE` 那三个答案 —— 旧形状读起来像「`APPROVE_POLICY_CHANGE` 本来就该
  写进选项里」，而那正是缺陷一那条例外**存在**的理由被抹掉的读法。

### 关闭条件 (a)–(f) 逐条对照（**含没覆盖到的那几条**）

| # | 关闭条件 | 执行器 | 状态 |
|---|---|---|---|
| (a) | 规则提议不自动生效 | `test_the_gate_blocks_the_proposed_version_and_nothing_else` | **覆盖**：闸门只拦那一版，控制器 `activate` 它被拒并指名该走哪条工具 |
| (b) | 批准一次后**符合该规则的后续对象**不重复问 | —— | **没覆盖**。今天有的是**同一件**不重复问（内容寻址 `case_id`，`test_the_same_question_opened_twice_leaves_one_case`）。「同一条规则覆盖**另一个**对象时不再问」需要一个规则级抑制表，这一片没有造 |
| (c) | 新旧版本影响预览可复现、历史不变 | `test_the_impact_preview_is_a_read_and_refuses_bad_versions` | **部分**：读得到、坏版本号拒得掉、与 `add_version` 回执**同一次计算**（不是第二份实现）。「同一份样本跑两次逐字节一致」与「旧版本激活后仍读得回来」在 `scenes` 那一族里，**不在**这一片的新判据里 |
| (d) | 去重 / 重启 / 并发审批不丢 | `test_the_same_question_opened_twice_leaves_one_case`、`test_record_resolution_with_a_stale_version_writes_nothing`、`test_claiming_is_not_a_decision_and_two_people_cannot_claim_one_case`、`tests/test_sqlite_review.py`（**新**，5 条：持久化 1 条 + 认领持久化 1 条 + 跨连接 CAS 2 条 + 无迁移自愈 1 条） | **覆盖**。跨连接那两条是本片补的，而它们的承重方式不同：裁决那条钉的是**拒绝的理由是版本**（变异 3b 实测会红），认领那条钉的是**结局**（赢家的名字还在行上） |
| (e) | 一个案件 hold 不堵**无关** Account | —— | **没覆盖**。这一片的闸门只作用于**那一版**被提议的策略（`awaiting_approval_for` 按版本号比），所以「无关的账号照常走完」在结构上成立，但**没有一条判据真的跑过一件未决案件旁边的无关账号** |
| (f) | 权限 / 过期 / 跨域批量均有拒绝测试 | `test_the_controller_can_read_the_inbox_but_cannot_approve`、`test_a_decision_lands_in_the_history_with_who_and_when`（陈旧版本）、`test_a_decision_from_another_workspace_is_refused` | **覆盖**。底稿写的第三格是「跨 workspace / 跨国家**批量**」——本片覆盖的是**跨 workspace 的单条决定**，批量那一格今天没有入口 |

(c)(e) 与 (b) 的缺口**不是这一片的遗漏**，而是 §B20 的关闭条件比这一片范围宽：它们各自
要动的是调度层（`maintenance` / `refresh`），在切片 8–9 里。**报告里必须按实际写**，不然
「关闭条件逐条有判据」会读成 B20 整片已经关上。

### 变异验证：每条新判据都承重

改一处 → 清 `__pycache__` → 跑那一条判据 → 复原 → 逐字节确认（记忆
`pyc-cache-defeats-same-size-mutation-reverts`）。

| # | 变异 | 抓它的判据 | 变异后的表现 |
|---|---|---|---|
| 1 | `may_decide` 去掉 `if case.state in CLOSED_STATES: return False` | `test_a_closed_case_takes_only_a_reopen`、`test_a_decision_lands_in_the_history_with_who_and_when` | 两条红；后者红在「换一个关闭结果被静默接受」上 |
| 2 | `may_decide` 去掉 `POLICY_APPROVAL_OUTCOMES` 那一支（退回「只在 `options` 里找」） | `test_approval_is_available_without_being_declared_in_the_options`、`test_only_the_authorizer_approves_and_the_proposer_is_reported`、`test_the_gate_blocks_the_proposed_version_and_nothing_else` | 三条红 —— **缺陷一按需复现**：批准那一格整条链一起塌 |
| 3b | `SqliteReviewCaseStore._read_for_update` 去掉事务内的 `case.version != expected_version`（`UPDATE` 的 `WHERE` 留着） | `test_two_connections_racing_one_decision_leave_exactly_one_winner` | 红在抛出的是 `StoreError`（「事务边界丢了」）而不是 `ReviewConflictError` —— 两道判据是**冗余**的，所以这一条钉的是**拒绝的理由**，不只是「被拒了」 |
| 3c | 3b 再加上 `UPDATE … WHERE case_id = ? AND version = version` | 同一文件 1 条 + 契约测试 12 条（两个实现里的 sqlite 那一半） | 13 条红 —— 两道判据一起去掉之后，并发的那一次**真的会覆盖** |
| 4 | `SqliteReviewCaseStore.__init__` 不跑 `ensure_schema` | `tests/test_sqlite_review.py` 全部 5 条 | 5 条红在 `OperationalError: no such table: review_case` |

**一条被抓住的假绿（我自己写的）**：`test_two_connections_racing_one_decision_leave_exactly_one_winner`
的第一版让输家选 `DISMISS`，而 `DISMISS` 对赢家留下的 `RESOLVED` **本来就不合法** ——
于是它红在缺陷二那条领域判据上，而把版本比对整段删掉它**照样绿**（变异 3b 实测：第一版
不红，改写之后红）。现在输家选的是 `REOPEN`：一条对当前状态合法的决定，所以唯一能拒它的
就是版本。同一族的 `…racing_a_claim…` 则**如实收窄了措辞**：认领只对 `OPEN` 成立，所以
第二个无论如何都会被状态判据拒掉，那一条钉的是**结局**（赢家的名字还在行上），版本比对
的承重由上面那条裁决竞态来钉。

未逐条变异的新判据（`test_the_flow_equation_is_computed_here_not_left_to_the_reader`、
`test_the_role_matrix_matches_the_catalog` 之类）**不算已验证** —— 它们钉的是接线与措辞，
而措辞那类判据的承重方式是「改回去就红」，本片只对上面六处做了这一遍。

### 剩余缺口（如实登记，不假装覆盖）

1. **`INTAKE[EVIDENCE_CONFLICT]` 今天没有生产者。** 报告上它那一格是 0，而那是
   「**没有生产者**」而不是「量过了，是零」。生产者是那个 `no_producer` 字段点名的三段
   接线（`marketops/application/contact_routes.py`、`grading.py`、`pipeline.py`），
   `test_every_reason_names_exactly_one_way_it_gets_produced` 会把那三个文件核在盘上。
2. **闸门只拦「由待办提议出来」的那一版**（取证结论二）。一个由 `add_scene_version`
   直接写下的版本照旧由控制器激活 —— 既有行为逐字不变，理由是不该把「站立一个市场」
   也改成要第二个人。一件 `AWAITING_APPROVAL` 的案件可以被 `correct_object` / `dismiss`
   关掉从而让闸门解除，而它**不构成新的能力**：任何控制者都能直接写一版并激活（同 1 之
   外的既有路径）。
3. **(b) / (c) 的那两格与 (e) 整格没覆盖** —— 见上面那张对照表。
4. **`claim_review_case` 的租约没有到期时间。** 认领是一件当前状态（会被下一次认领覆盖），
   不是一条只增不改的历史；一个人领走之后不放手，另一个人只能等他放。这一片没有造
   「多久之后自动放开」，因为那需要一个时钟与一条策略 —— 而两者都不在 §B20 的原文里。

---

## 切片 8（B21）· 持久续跑，不靠 Member-A 发「继续」 —— 需求底稿

> 本片的底稿与落地记录**同一次**写进台账（前七片是先写底稿、再动手）。动手前钉住的
> 那几条边界当时落在各文件自己的 docstring 上，这一节把它们与出处抄拢在一处。读法不变：
> 下面每一条都能在代码里找到写着它的那一段，而**不是**事后替实现补的一段辩护。

### 计划原文（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B21）

> **对应：**INT-019/023/032/041/046。**落点：**现有 dispatch/reconcile/job/maintenance，
> 增加最小 continuation/outbox 与受管入口。
>
> 每次已受理业务事件可驱动下一批，保存事件、幂等键、执行 epoch 和 checkpoint。未来到期
> 需要唯一的实际唤醒 owner；选当前可用的受管 host timer 或服务循环，不等待
> ServerOS/Docker。
>
> 分开：正在研究、无可运行任务、缺凭据/预算、等人、暂停、完成、等待到期。没有工作不调用
> 模型；异常恢复有次数/成本界限，不能用「持续运行」绕无限预算。
>
> **关闭：**正常 MCP 提交后断开客户端，第二批确实自动执行并回池；控制器崩溃后恢复不重复
> 昂贵工作；暂停与恢复正确；无任务无模型调用；仅相关对象等人。Quay 原始软件 landing 观察
> 保持可见，但不能让它重复已受理研究；在真实 driver 轨迹核验，不只改 report 状态。

### 权威二：架构 §10（逐字）

> MarketOps 负责“下一件业务工作是什么、允许花多少、结果怎样回池”；Quay 负责有界 Agent 的
> 派发、租约与执行。已有 dispatch/reconcile/maintenance 不重写，增加缺失的持久
> continuation 即可。
>
> 业务事件受理与 continuation intent 原子落账。控制器基于该事件生成下一批/投影/重评任务，
> 按幂等键执行并保存 checkpoint。至少一次交付配合幂等效果，不承诺外部 API exactly-once。
>
> 完成事件推进当前研究；时间到期由**一个**受管唤醒来源触发。……必须登记唯一 owner，
> 不能并行双触发器。没有常驻触发源的 `run_maintenance` 只是可调用函数，不等于自动维护。
>
> 空队列不调用 LLM 写心跳。……暂停阻止新昂贵动作并有观测回执；在途合法结果结算保留。
> 恢复从 checkpoint 继续。只阻塞依赖该缺口的对象。

### 由原文直接得到的边界（动手前先钉住）

| # | 边界 | 为什么不是另一条写法 |
|---|---|---|
| 1 | **不重写 dispatch/reconcile/maintenance，只补 continuation** | 执行器把两个动词各接上**已有**的那两趟（`application/dispatch.py::WorkDispatch`、`application/maintenance.py::run_once`），本片没有第二份派发逻辑（`interfaces/controller.py::LiveContinuationExecutor`）。模块 docstring 明写它**不自己判断该不该做**：闸门在 `ContinuationController` 里一次判完 |
| 2 | **意图与业务事件同一个写事务**，端口也不新开 | `ports/jobs.py` 那一节：写在事务**之前**，一次被拒的结算会留下「欠着一次复核」——而那次结算根本没发生；写在**之后**，进程死在两次写之间时「跑完还有下一批」**永久丢失**，而它正是 R2 那一行的全部内容。所以 `JobStore` 从「持久作业记录」变成「持久**业务事件**记录」，续跑意图是这一组的第四个动词族 |
| 3 | 幂等键是**业务事件**的稳定身份，不是 attempt id | `admit` 欠派发 → `job_id`；`settle` 欠复核 → `work_item_id`。用 attempt id 会让**同一次结算的重发**算成两个事件，而重发按定义是幂等的 |
| 4 | **唯一**的唤醒 owner，不能并行双触发器 | 盘上一行 `continuation_owner`（每个工作区一行）+ `(holder, epoch)` CAS。`--once` **也要先 acquire**：一次性的跑法最容易写成「跑一轮看看」，而它与常驻那个推的是同一批共享资源 |
| 5 | 七种状态**分开**，且后两种的差别是**历史** | `ControllerState` 七个成员各对应一种处置；`COMPLETE` 与 `NO_WORK` 只能从**终态的行**读出来（`ever_done`），所以计数接口单独有 `continuation_counts`，而不是把终态行整批读回来 |
| 6 | 没有工作**不调用模型**，也不 tick 空主体集 | 空队列那一格是 `NO_WORK`/`WAITING_DUE`，执行器一次都不被调用；主体两格都空是 `BLOCKED(CAPACITY)`，**不是**去 `run_once` 一个空集（那会把「没做的事」记成做完的事） |
| 7 | `BLOCKED` **不是一次尝试** | `attempts` 只在 `FAILED` 时加一。暂停一周的场景不会因为每 60 秒 tick 一次而被判成「重试了 10080 次」 |
| 8 | 异常恢复**有次数界限** | `MAX_ATTEMPTS=3`，退避 `RETRY_BACKOFF=60s` 封顶 `RETRY_BACKOFF_CAP=3600s`，被挡之后隔 `BLOCKED_BACKOFF=60s` 再看一眼。四个常量**都没有标定过** —— 与 `domain/maintenance.py` 的 `calibrated` 同一条纪律：未标定的值可以用（否则系统什么都不做），但不许在报告里被读成「已批准」 |
| 9 | 「回池」只按本仓**可核**的那一半写 | 可核的是结算落账、findings 回读得出来、它欠下的复核被下一趟跑掉；「账号回到池子里」是另一条工具（`remember_accounts`），本片不经过它，所以不声称 |

---

## 切片 8（B21）· 落地记录（`8c08cf8` + `c7d456c`）

### 落到盘上的东西

| 面 | 文件 | 形态 |
|---|---|---|
| 领域 | `domain/continuation.py`（**新**，729 行） | `ContinuationKind`（2 成员，各有一个说得出口的产出者）、`ContinuationState`（5 成员，`BLOCKED` 与 `FAILED` 分开）、`CONTINUATION_TRANSITIONS`、`BlockedOn`（三个「等人」的理由，闭集）、`Continuation`、`ContinuationClaim`、`WakeupOwner`、`ContinuationInputs`/`ControllerInputs`、`classify`（六个数 + 一个布尔 → 七格之一，**纯函数**）、`after_failure`、`intents_for_admission`、`intents_for_settlement` |
| 端口 | `ports/continuation.py`（**新**）、`ports/jobs.py` | `WakeupRegistry` 五个动词（`acquire` / `heartbeat` / `release` / `current` / `owners`）；`JobStore` 的第四个动词族：`continuations` / `continuation` / `continuation_counts` / `claim_continuation` / `finish_continuation` |
| 存储 | `adapters/sqlite/wakeup.py`（**新**）、`adapters/sqlite/db.py`、`adapters/sqlite/jobs.py`、`adapters/memory.py` | 两张表 + 一条索引（`_CONTINUATION_DDL`，整份重放、`IF NOT EXISTS` 自愈，**不动 `SCHEMA_VERSION`** —— 版本号只在**已有表改形状**时才动）。内存替身供契约测试对跑 |
| 编排 | `application/continuation.py`（**新**，686 行） | `ContinuationController`（一轮 tick：认领、驱动、按结果四选一地结账）、`ContinuationExecutor`（两个动词的抽象）、`survey` / `ContinuationStatus`（那一页的两个半边） |
| 生产者 | `application/inputs.py`、`application/maintenance.py` | 受理与结算**在生产路径上**真的欠下下一步；`_skip_reason` 抬成公开的 `skip_reason`（控制器要对被暂停的场景说**同一句**话） |
| 受管入口 | `interfaces/controller.py`（**新**，635 行）、`pyproject.toml` | `marketops-controller`（**第二个** console script，不是 `marketops` 的子命令）：`--once` / 常驻 / `--interval` / `--limit`。退出码 `0/1/2/3/130`，其中 `3` 单独占一格 = 「归属不在我手里」 |
| 服务面 | `adapters/mcp_server/continuation.py`（**新**）、`security/catalog.py`、`server.py`、`interfaces/mcp/__main__.py`、`interfaces/composition.py`、`docs/MCP_PRODUCT_LOOP.md` | 一条**只读**工具 `get_continuation_status`（`phase="J"`，`_CONTROL`），以及 `default_wakeup_registry` 那个**惰性**工厂 |
| 判据 | `tests/test_domain_continuation.py`（41）、`tests/test_continuation_contract.py`（39）、`tests/test_sqlite_continuation.py`（17）、`tests/test_continuation_controller.py`（31）、`tests/test_resident_controller.py`（16）、`tests/test_mcp_continuation.py`（8）、`tests/test_r1_vertical_acceptance.py`（+1） | 另有三处旧判据被这一片的接线逼着更新：`test_mcp_product_loop.py`（角色判据的期望集由 `TOOL_CATALOG` 派生）、`test_adapters_hold_the_vendors.py`（组合根由四个变**五个**，`interfaces/controller.py`）|

### 七种状态各自判在哪一格

| `ControllerState` | 从哪几个数读出来 | 处置 |
|---|---|---|
| `RESEARCHING` | `in_flight > 0` | 什么都不用做 |
| `PAUSED` | `blocked_paused > 0` | 等人按一个按钮 |
| `WAITING_ON_HUMAN` | `blocked_human > 0` | 人要去处理一件待办 |
| `MISSING_CAPACITY` | `blocked_capacity > 0` | 运维要补接线 |
| `WAITING_DUE` | `waiting_due > 0` | 什么都**不许**做（还没到期） |
| `COMPLETE` | 其余全零且 `ever_done` | 盘上没有欠账 |
| `NO_WORK` | 其余全零且**不曾** `ever_done` | 还没有过事 |

顺序就是全部内容 —— `classify` 是纯函数，判据落在 `test_domain_continuation.py`；变异 M13
（把 `COMPLETE`/`NO_WORK` 合成一个）被抓住。

### 这一片实测出来的东西

* **`SqliteWakeupRegistry.__init__` 会建库。** 它调 `ensure_schema`，而 `ensure_schema` 是
  整份 DDL 重放 —— 于是「构造一个只读页的注册表」这个动作本身会**写出一个数据库文件**。
  后果是那一条「还没有产物」的判据会被自己破坏：读一次，盘上就多了一个空库，第二次读
  它答「有产物、里面什么都没有」。修法两半：`default_wakeup_registry` 是
  `Callable[[], WakeupRegistry]`（惰性，不与 `default_scene_store` 那条「读也不建」的
  理由混同），而**读工具自己**先问 `settings.database_path.is_file()`。判据
  `test_a_deployment_that_never_wrote_anything_reports_no_store_and_leaves_none_behind`
  的最后一行就是 `assert not settings.database_path.is_file()` —— 「读一次不留痕」这条
  要求只有这一种写法能钉住，改 `is_file()` 那一处守卫（变异 M1）它当场红。
* **`SUBJECT_KIND_ACCOUNT` 从 `application/inputs.py` 搬进了 `domain/subject.py`。** 续跑
  意图要用它，而领域层不得 import 应用层 —— 留着第二个字面量的后果不是文案不一致：
  `Observation` 的主键里就有这一列，对不上的表现是**同一格被当成两格**（一格「从来没
  看过」，另一格也是），而 `what_is_due` 会因此一直说这一格该看。

### 关闭条件逐条对照（**含没覆盖到的那几条**）

| # | 关闭条件 | 执行器 | 状态 |
|---|---|---|---|
| (a) | 正常 MCP 提交后断开客户端，第二批确实自动执行并回池 | `test_the_work_after_the_client_is_gone_is_driven_by_the_row_on_disk` | **覆盖（「回池」按可核的那一半）**。受理并编译之后**再无任何第三方调用**，两趟控制器（两次独立的 `_build`，两趟之间注册表是空的）把派发与复核各跑掉一趟；第二趟**外部一件都没被再碰**（派发意图那一行已 `DONE`）。「账号回池」不在本片链上 |
| (b) | 控制器崩溃后恢复不重复昂贵工作 | `test_dispatching_the_same_batch_twice_hands_out_nothing_the_second_time`、`tests/test_sqlite_continuation.py` 的陈旧认领收回、`tests/test_continuation_controller.py` 的 `_reclaim` 一族 | **覆盖**。不重复这件事是**结构性**的：同一个事件欠下的下一步只有一行（内容寻址 id），所以重启之后重新执行的是同一行 |
| (c) | 暂停与恢复正确 | `test_a_paused_job_is_blocked_on_pause_and_resuming_puts_the_same_row_back_to_work`、`test_a_maintenance_tick_on_a_paused_scene_is_blocked_not_failed` | **覆盖**。暂停不只是一个新的作业状态：同一事务里未决意图记 `BLOCKED(PAUSE)`，恢复时被 `PAUSE` 挡住的重排回 `PENDING` 而 `attempts` **一格不动** |
| (d) | 无任务无模型调用 | `test_a_resident_round_with_nothing_due_does_not_call_the_executor` | **覆盖** |
| (e) | 仅相关对象等人 | `test_a_maintenance_intent_without_a_subject_is_blocked_and_no_empty_set_is_ticked`、`test_a_maintenance_tick_with_a_subject_records_an_observation` | **部分**。意图本身点名一个主体（`subject_ref`），空主体集**拒绝而不是 tick**；但「一条 `ReviewCase` 不冻结整个市场」那半句的真判据在 B20 的复盘里已经登记为缺口（(e) 整格），本片没有补 |
| (f) | Quay 原始软件 landing 观察保持可见，且不让它重复已受理研究 | —— | **没覆盖**。这一格要的是 Quay 那一侧的 driver 轨迹，本片没有 Quay 的受控运行（付费 live run 仍是 BLOCKED） |
| (g) | 在真实 driver 轨迹核验，不只改 report 状态 | `test_the_work_after_the_client_is_gone_is_driven_by_the_row_on_disk`（两趟走**真存储**：`SqliteJobStore` / `SqliteWakeupRegistry` / `SqliteMaintenanceStore`，观测真的落在盘上） | **部分**。本片核的是**本仓这一侧**的真实轨迹（那一行真的被推起来、观测真的落盘、findings 真的回读得出来）。「外部 driver」的轨迹要 Quay 那一侧，见 (f) |

### 变异验证：25 条，全部被抓住

改一处 → 清 `__pycache__` → 跑那一组判据 → 复原 → 再跑一次基线（记忆
`pyc-cache-defeats-same-size-mutation-reverts`）。两份驱动都在盘上：
`artifacts/work-items/marketops/member-a-automation-r1-r2-20260927/B21-MUTATION/run_mutations.py`
（13 条，控制器那一半）与 `…/B21-MCP-MUTATION/run_mutations.py`（12 条，服务面 + 组合根）。

| 组 | 靶 | 结果 |
|---|---|---|
| 控制器那一半（13） | `interfaces/controller.py`、`domain/continuation.py`，判据 `tests/test_resident_controller.py` | **13/13 被杀**（提交前重跑确认过一次）。逐条：拿不到归属回 0；没装执行器照样报 `DONE`；暂停的派发拒绝不再分类成 `BLOCKED`；指向不存在作业的意图不再被识别；两格都空时 tick 一个空主体集；心跳恒真（并行双触发器）；被顶掉之后不丢手里那一份；无条件让出归属；进度用「在账上的件数」而不是「这一次交出去的件数」；场景暂停时那次跳过不抬成 `BLOCKED`；复核抛了却报 `DONE`；`--limit 0` 落成「一件都不做」；`COMPLETE`/`NO_WORK` 合成一个 |
| 服务面（9） | `adapters/mcp_server/continuation.py`，判据 `tests/test_mcp_continuation.py` | **9/9 被杀**：一次只读查询凭空建库；`no_store` 报成 `present`；没产物被答成业务结论；那句把「还没有产物」与「没有工作在跑」分开的话被删；只读页顺手认领归属；驱动者那一格丢掉；心跳过期反过来报；三个工厂有一个可以缺；读的不是本部署那个市场 |
| 组合根（3） | `interfaces/composition.py`、`interfaces/mcp/__main__.py`，判据 `tests/test_r1_vertical_acceptance.py` | **3/3 被杀**：归属工厂指向另一个库；组合根漏接归属注册表（生产上这一条工具对任何调用都回「尚未接线」）；组合根整条没接（适配器那一层全绿，生产上这一条不存在） |

**第一次跑是 24/25，M9 活着**（`workspace=settings.profile` → `"elsewhere"`，只在 `survey`
那一处调用里）：抓不出来是因为那个文件里**每一条判据的判词都是 `no_work`** —— 没有一个
非平凡判词，于是「读的是哪个市场」在结果上不可区分（驱动者那一格独立取
`settings.profile`，所以它也不动）。这不是变异写得刁，是**判据有洞**：补
`_seed_open_work`（写一条场景记录 + 受理一个作业，于是判词是
`waiting_due / due_now=1 / open_total=1`）与 `test_the_page_reads_this_deployments_own_scene`
之后 M9 被杀。同一轮里 M10（归属工厂指向另一个库）也一度不可观测 —— **构造上**不可观测：
测试写归属行用的是**同一个（可能已被变异的）工厂**，两边一致。补 `_owner_store(settings)`
（直接按路径构造 `SqliteWakeupRegistry`，注释写明「盘上那一行」）之后，这条判据才真的
在说「这一页必须看得见控制器写下的那一行」。

### 剩余缺口（如实登记，不假装覆盖）

1. **(f) 整格没覆盖**，理由是没有 Quay 那一侧的受控运行 —— 与付费 live run 同一处阻塞。
2. **(e) 只覆盖了「只 tick 点名的那个主体」那一半**，「一条 `ReviewCase` 不冻结整个市场」
   那半句的缺口在切片 7 已经登记过，本片没有补。
3. **四个时间常量未标定**（`BLOCKED_BACKOFF` / `RETRY_BACKOFF` / `RETRY_BACKOFF_CAP` /
   `MAX_ATTEMPTS`）。它们可以用，但报告里**不许**被读成「已批准」。
4. **`--interval` 的缺省 30 秒同样未标定**：它不表达任何业务节奏，只决定「多久看一眼」。
5. **没有 systemd / supervisor 单元文件。** 受管入口是**进程**这一层给到的（一个
   console script + 一套退出码），把它拉起来的那份 unit 不在本仓 —— §B21 允许「主机 timer
   或服务循环」，本片给的是后者，而「谁拉起它」是部署的事（`docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md`
   §10 也说后续 Hosting/ServerOS 可替代）。
6. **服务面只有「读」这一条**：暂停 / 恢复 / 取草稿这些**动词**今天仍只在 CLI 与控制器上，
   B30 那一页（Member-A 的受控使用说明）要的那几条不在本片。

---

## 切片 9（B22）· 先复用，再对过期事实真实重查 —— 需求底稿

### 计划原文（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B22）

> **对应：**TR-D11、INT-019/033/041。**落点：**`application/acquisition.py`、page mirror
> store、`application/{maintenance,refresh}.py`、budget 与报告。
>
> 获取前检查授权、页面/解析版本、事实用途和时效；重用返回原证据引用，过期重查预留预算。
> 共享内容复用与 Account 关联分开。不将未命中的无限负缓存当永久 no-route。
>
> 当前 refresher 只支持本地 `source_coverage` census。R2 必须接入目标账号官网/责任关系/
> 联系人页面的有界外部重查；至少把 R2 产品承诺中的方面全列为 required，而不是通过过滤掉
> 未实现 aspect 得到全绿。
>
> 合并同一对象/aspect 的相近到期理由，记录原因集合；新写入字面块不自动等于新页面/外部
> 事实变化。无内容变化不产生重复纠错任务，有变化定向重评并保留历史。
>
> **关闭：**相同冻结输入第二轮无额外获取；时钟推进到期后确实发出批准重查；无变化/变化/
> 不可达三种状态分开；变化只更新受影响对象，不重扫全场景；首次观察+信号只造成一次必要
> 重查；失败不写刚验证；跨工作区缓存不泄漏。

### 权威二：架构 §11（逐字，含它对本片最狠的那一句）

> 获取前检查已有工件、使用权限、内容/解析版本、所需事实与时效；可复用则返回引用，不调用
> 供应商。过期时可条件请求或定向抓取，**不能只靠“文件已存在”避免写入却仍重复抓取**。
>
> 成本账包含搜索、HTTP/浏览器尝试、可取得的子请求、Jev 与 Agent 用量、重试和复用。未知
> 金额留空并给原因；仍能用请求/时间/并发上限控制，不能把未知当免费。
>
> 分别保存 publication/effective/observed/verified 时间、失效理由和 next_due。**盘点本地
> 证据不是外部重新验证**：`source_coverage` census 完成不能把角色/邮箱标成
> freshly_verified。R2 必须给官网/责任关系/联系人提供真实刷新执行器；**未接的 aspect 报
> coverage gap，不能通过 out_of_scope 把必需功能从验收分母删掉**。
>
> 同一对象/面上同时出现首次观察与变更信号时**合并必要工作，保留全部原因**；新证据写入不
> 自动证明世界变化。以内容/语义指纹与已消费信号版本判断是否重评，防“研究产信号→维护→
> 新信号”的循环。

### 由原文直接得到的边界（动手前先钉住）

| # | 边界 | 为什么不是另一条写法 |
|---|---|---|
| 1 | **复用是一次判定，不是一个副作用** | 「可复用则返回引用，**不调用供应商**」—— 复用的可观测后果是**抓取次数为 0**，不是「文件没被重写」。架构那一句「不能只靠『文件已存在』避免写入却仍重复抓取」正面否掉了「写不写」当判据：重复抓取而只避免写入，在账上是省了钱、在事实上是花了钱 |
| 2 | 复用判定**建在本工作区自己的证据行上**，不建在镜像文件系统上 | 镜像按 URL 的内容寻址、落在 `mirrors/pages/<scope>/`，而**一个部署的 `mirrors_dir` 是跨工作区共享的**。按文件判复用，等于让 B 工作区把 A 工作区抓过的页面读成自己的 —— 那正是「跨工作区缓存不泄漏」要挡的。证据行那一侧有工作区绑定（`adapters/sqlite/db.py::owned`：行的工作区与库的绑定不一致时**点名拒绝**），于是复用判定只可能落在自己的记录上 |
| 3 | **共享内容复用与 Account 关联分开** | 镜像那一半按内容/URL 共享（`adapters/page_mirror.py::store_page_bytes` 只在文件不在时写），证据与观测那一半按账号各写各的。合成一件的后果是「同一个页面被两个账号看过」在报告上变成「一个账号的证据更多了」 |
| 4 | 时效判据是**这一面**的 `after_days`，不是「文件的新旧」 | `RefreshRule.after_days` 按 aspect 分档（身份/属性/关系 180 天、角色/邮箱 90 天、产品 30 天）。用文件 mtime 会让「三个月该核一次的邮箱」与「半年核一次的身份」共用一个数 |
| 5 | 解析版本要**一起判** | 「内容/解析版本」是两件事：同一份字节交给换了实现/版本的解析器，得到的事实可能不同。所以证据行上记着 `parser`，复用要它等于本部署今天用的那个 |
| 6 | 过期之后是**定向抓取**那一个 URL，不是重新检索 | 「条件请求**或**定向抓取」二选一。本片取后者（`HttpFetcher` 今天没有条件请求的头，加它是改端口 + 改适配器，而收益是省带宽 —— 不是本片要证的任何一条）。**这一条是取舍，要写下来** |
| 7 | **未接的 aspect 报 coverage gap，且计入分母** | 今天 `run_once(aspects=REFRESHABLE_ASPECTS)` 在认领**之前**把未实现的面滤掉，它们于是**不出现在任何一份报告里** —— 报告全绿，而绿来自「没算它」。修法不是把面补全就完事，是让「承诺了但没实现」这件事**有一个被计数的去处** |
| 8 | 三种状态**分开**，且只有两种写观测 | 无变化 / 变化 / 不可达。不可达**不写观测**（「失败不写刚验证」）：一次假观测会把真正的复核推迟一个周期，而这一格的真实状态是「我们不知道」 |
| 9 | 变化之后**只动受影响的那一个对象** | 判据是抓取与写入的**面**：一条变化信号只给那一个主体、不再扫这个场景里的其他主体；而「重评」的落点是把新页面的字面块写进那个主体的证据里 |
| 10 | **新写入字面块 ≠ 新页面** | 变化与否判在**页面指纹**上：`ParsedDocument.sha256`（**文件字节**的散列，与证据行上的 `content_sha256` 是同一个量），不判在「有没有新证据行」上。判在证据行上的后果正是 §11 那句要防的循环：研究写下一条字面块 → 维护看见「新证据」→ 报变化 → 又研究。**`FetchResult.body_sha256` 不是这个量**（它是解码后文本的散列），拿它来比会让每一页都读成「变了」—— 上面边界 5（解析版本要一起判）与它同源：能比的只有「同一把尺子量两次」 |
| 11 | 负缓存**有界**，且不是 no-route | 一次抓不到不产生「永远没有」。这一格不记观测（所以它仍然到期），而重试的界**由预算账本给**（`BudgetLine.DOCUMENT` 预留失败就等于这一轮不再花），不另造一个计数器 |
| 12 | 合并的是**同一 (对象, aspect) 的必要工作**，不是理由 | §11 逐字「合并必要工作，**保留全部原因**」。今天 `what_is_due` 对「首次观察 + 变更信号」给出**两行**，于是一次必要重查会被驱动两次 —— 那正是关闭条件「首次观察+信号只造成一次必要重查」要挡的。合并之后理由集合留在行上，所以「信号提前触发的那一次」不会在账上消失（旧注释担心的正是这个，而它担心的东西由 `reasons` 那一格承担） |

### 关闭条件逐条落到哪一类判据上

| # | 关闭条件 | 判据形态 |
|---|---|---|
| 1 | 相同冻结输入第二轮无额外获取 | 一次 tick 里两个面共用同一批页面 ⇒ **抓取恰好 1 次**；第二次 tick 到期后**仍然**只在真过期时才抓 |
| 2 | 时钟推进到期后确实发出批准重查 | 时刻推过 `after_days` 之后重跑 ⇒ 抓取发生、且有预算预留 |
| 3 | 无变化 / 变化 / 不可达三态分开 | 三个假抓取器各喂一次，三个 `state` 互不相同，且只有两个写观测 |
| 4 | 变化只更新受影响对象，不重扫全场景 | 变化信号只落在那一个主体上；同场景另一个主体的证据行数不变 |
| 5 | 首次观察 + 信号只造成一次必要重查 | `what_is_due` 对那个组合出**一行**、`reasons` 两格 |
| 6 | 失败不写刚验证 | 不可达那一趟之后，`observations()` 里**没有**这一格 |
| 7 | 跨工作区缓存不泄漏 | 镜像文件在盘上、但本工作区没有证据行 ⇒ **仍然抓取**（复用判定不读文件系统） |

### 本轮不做（写下来，免得被读成已覆盖）

- **条件请求（`If-None-Match` / `If-Modified-Since`）**：见边界 6，取的是定向抓取。
- **跨主体的镜像字节复用**：一个部署里两个主体抓同一个 URL 会各落一份文件（`mirrors/pages/<scope>/` 按主体分目录）。§4.3 允许复用，本片**没有**实现它 —— 于是「共享内容复用与 Account 关联分开」在本片里的含义是「复用判定不碰共享字节」，而不是「跨主体共用一个文件」。
  > **2026-09-28 收口（B15-h 4/4，矩阵 211 行）**：上面那句「本片**没有**实现它」已经不成立 ——
  > 同一个工作区里两个主体抓到同一个 URL 时，第二次取回不再发生（`application/reuse.py` 的
  > 一轮台账）。而**落盘那一半照这条边界的原话办**：字节是**复制**进接收方自己的目录
  > （`adapters/page_mirror.py::copy_page`），不是「跨主体共用一个文件」—— 所以这条边界的
  > 措辞今天仍然逐字成立，改的只是「没有实现它」这一句。
  >
  > 同一处勘误：边界 #3 里的 `http.py::_persist` 已换成 `adapters/page_mirror.py::store_page_bytes`
  > （写入判据在那一天收成了一处，理由见矩阵 212 行）。
- **网页事实的语义抽取**：本片的复核是**页面指纹**级的（抓回来、比对、把新字面块按既有 `evidence_ingest` 落行）。「官网上的责任关系换人了」这类判断今天仍由研究侧做，本片不声称能自动判出来。
- **`product_fit`**：它是判断不是页面事实，本片把它报成 **coverage gap**（见下），不假装实现。

### 落地记录（`6c94552`）—— 三份桶，以及那个分母

**分母没有被本片动过。** `REQUIRED_ASPECTS = ALL_ASPECTS`（`domain/maintenance.py:130`）
仍是七个面的**声明**：`source_coverage`、`asset_identity`、`asset_attribute`、
`account_relation`、`contact_person_role`、`email_evidence`、`product_fit`。本片做的是让
「今天接得上的面」从一个变成三个，其余**照旧留在分母里**：

| 桶 | 今天的内容 | 它出现在哪 | 由谁定义 |
|---|---|---|---|
| **未实现**（`coverage_gaps`） | `asset_identity`、`asset_attribute`、`account_relation`、`product_fit` | 每一份报告上当**一个数**，且留在分母里 | `COVERAGE_GAPS = uncovered_aspects(REFRESHABLE_ASPECTS)`（**算出来的**，不手写差集 —— 手写的那份在 `REFRESHABLE_ASPECTS` 变大的那天不会跟着变，而它看起来仍然正常） |
| **实现了但没接线**（`missing_wiring`） | 缺 `plan` / `acquisition` / `router` / `documents` 里的哪几样 | 逐**页**写进 `deferred`（`not_wired:<名>`），整格 `UNREACHABLE` | `refresh.missing_wiring`，与 `WIRING_MEANING` 同一组名字（有判据钉两半是同一集合） |
| **接上了的** | `source_coverage`（读本地记录）+ 两个页面面（`recheck_pages`，真的联网） | 逐格的状态与观测 | `REFRESHABLE_ASPECTS` |

`PAGE_BACKED_ASPECTS` 与 `CENSUS_BACKED_ASPECTS` 是**手写**的两份，不是求差集，因为
求差集会在「新加一个面而忘了写派发分支」时照旧把新面算进盘点的名下 —— 而那一刻报告说
这一面有产出。两者的并集恰好等于 `REFRESHABLE_ASPECTS`（新判据
`test_the_two_kinds_of_aspect_partition_the_declaration` 钉死），于是「加了一个面」
必然要在那一处分一次。

**七条关闭条件分别由哪一条判据执行**（形态见上一张表，这里补上落点）：

| # | 落点 |
|---|---|
| 1 | `test_application_page_refresh.py::test_two_page_aspects_share_one_fetch_per_page`（`http.calls == [PAGE]`）；接线之后的那一层在 `test_mcp_product_loop.py::test_a_wired_tick_fetches_each_page_once_for_both_page_aspects` |
| 2 | 反面与正面各一条：`test_application_maintenance.py::test_a_recent_observation_is_not_due_yet`（窗口内不到期）配 `test_application_page_refresh.py::test_two_page_aspects_share_one_fetch_per_page`（400 天前的那一页确实被重取）；「重取要预留预算」由 `test_a_budget_smaller_than_the_page_set_defers_the_rest` 与 `test_the_declared_ceiling_is_what_the_profile_says` 钉 |
| 3 | 三个假抓取器各喂一次，三个 `state` 互不相同（`unchanged` / `changed` / `unreachable`，见 `test_a_changed_page_lands_new_evidence_and_says_what_changed` 与 `test_an_unreachable_page_leaves_the_whole_cell_open`） |
| 4 | `test_a_changed_page_lands_new_evidence_and_says_what_changed`（应用层）与 `test_mcp_product_loop.py::test_only_the_page_that_changed_gets_new_evidence`（接线之后：未变的那一页**保留原证据行与其摘要**） |
| 5 | `test_domain_maintenance.py::test_a_first_observation_and_a_signal_are_one_piece_of_work`（出**一行**、`reasons` 两格；旁证 `test_replaying_one_signal_is_still_one_piece_of_work`）与 `test_a_cadence_rerun_and_a_signalled_rerun_are_one_piece_of_work` |
| 6 | `test_an_unreachable_page_leaves_the_whole_cell_open`（这一趟之后 `observations()` 里**没有**这一格） |
| 7 | `test_a_mirror_file_on_disk_does_not_confer_freshness`（镜像文件在盘上、本工作区没有证据行 ⇒ **仍然抓取**） |

**两处本片扫出来的同类缺陷，一并修在这里**（都是「看起来正常的错」）：

1. 服务面回执上的 `changed_pages` 转发的是 `payload["changed_pages"]` —— 而
   `RecheckRecord.payload` 里**没有**这个键（只有 `pages` / `page_set_size` / `examined`），
   于是它在**每一份**回执上恒为 `null`。改成从每页自己的 `cause` 推出来，判据与
   `PageRecheck.changed` 逐字同一条。
2. `_plan_context` 把「这一份 Scene 还没有生效版本」（`effective_version is None`，正常）
   与「记录写着第 N 版、而库里读不到那一版」（材料缺了）合并成同一句话 —— 后者按前者
   处置会静默降级成「没有规则可依」。三态拆开之后，第三态**点名拒绝**。

**一处取舍写下来**：共享的是**取回**，不是判断。一趟里两个面共用同一批页面时，第二个面
复用第一个面的**取回结果与逐页结论**，但它**不**因此获得一个新鲜窗口 —— 一页都没取的
那一趟不进备忘（`test_the_shared_pages_do_not_buy_the_other_aspect_a_fresh_window`）。
让 A 面（窗口 90 天）的判断替 B 面（窗口 30 天）作证，省下的是零次抓取。

**本片的变异检验**（改一行清一次 `__pycache__`）：`fetch.max_documents` 那条上限上五处
变异全部转红 —— 读错键、`source` 写错键名、科目写成 `SEARCH`、把声明的 `0` 当成零额度、
以及把「缺省」当成上限；每一条都对应一种「报告上一切正常」的走法。

---

## 切片 10（G2）· 受控自动经营 —— 需求底稿

### 计划原文（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §G2）

> 在 G1 相同状态上：断开控制客户端 → 下一批自动执行 → 重启控制器 → 重放事件 → 暂停 →
> 恢复 → 模拟到期 → 真/受控来源变化 → ReviewCase 裁决 → 受影响对象重评 → 草稿版本更新。
>
> 区分三种时间：即时重复 tick（应无重复工作）、来源新证据产生后的 tick（可能合理有工作）、
> 真实到期后外部 revalidation（需要获取/验证回执）。单次 `claimed=0` 不能证明后两者。
>
> 长周期语义用受控时钟测，不等待一个月；长期运行稳定性仍另标未观测。基础设施停机与
> 业务无新数据必须可区分。

### 权威二：架构里决定本片形态的那几句

> §8（`docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md:107`）写入观察、更新投影、登记下游变更在
> 同一事务或可恢复事务交接中完成；**重放不重复计数**。
>
> §10（`:141`）业务事件受理与 continuation intent **原子落账**。控制器基于该事件生成
> 下一批/投影/**重评任务**，按幂等键执行并保存 checkpoint。至少一次交付配合幂等效果，
> 不承诺外部 API exactly-once。
>
> §9（`:169`）提交裁决验证可信 principal、权限、expected_version 与幂等键；过时案不写回
> 新事实。**裁决后自动重评受影响对象**，保留历史和恢复进度。单案裁决默认不成为全局策略。

「裁决后自动重评受影响对象」这一句是本片唯一的**功能性缺口**，其余各步今天已经跑得动
（见下面「动手前逐条核过」那张表）。它不是一条可以登记后结束的缺口：它在 §9 里逐字
写着，在本仓代码范围内，而今天**没有任何生产者**。

### 动手前逐条核过（四条结论，其中一条推翻了底稿的假设）

| # | 结论 | 依据 |
|---|---|---|
| 1 | 「重放事件」在本仓的载体是**重启后重读盘上那一行**，不是一份事件日志 | `run_once_cli` → `ContinuationController.tick` → `_reclaim`（`application/continuation.py:560`）把上一代遗留的 `CLAIMED` 放回 `PENDING`，它的 docstring 逐字写着「收回**不是**重跑」。全仓没有事件日志存储，也没有第二个重放入口（`replay_ddl` 重放的是建表 DDL，`reconcile_*` 读的是外部看板）。架构那一句要的性质于是是**幂等**：`:107` 逐字「重放不重复计数」 |
| 2 | 「模拟到期」今天**不必**改时钟也做得出，因为到期判定是 `now` 与 `observed_at` 的**纯函数** | `what_is_due(plan, observations, now)`；`run_once(now=…)` / `due_work(now=…)` 收 `now` 参数。服务面的 `run_maintenance` 取 `utc_now()`（不是参数），所以在**接线那一层**模拟到期的手法是把 `observed_at` 种老。两条路都要有判据：一条测纯函数（受控时钟），一条测接线（种老） |
| 3 | 「真/受控来源变化」不可能用一个本地 HTTP 服务顶替 | `security/network.py::check_target` 拒环回、拒 `.example` 等保留域（`RESERVED_HOST_SUFFIXES`），而 G1 的合成主体域正是 `.example`。所以受控来源变化的唯一诚实做法是换**那一个工厂**，而它有**两个**绑定：`interfaces.composition.default_acquisition`（`_compose` 走它）与 `interfaces.controller.default_acquisition`（控制器在 import 时绑死，`controller.py:283` 用它）。**只换一个**的形态是「工具那一趟抓的是假页面、控制器那一趟抓的是真网络」——两边报告都正常 |
| 4 | 「暂停 / 恢复」是**场景**级的，不是控制器级的 | `control_scene(action=pause/resume)` → `SceneRecord.accepts_new_work`；`application/continuation.py::_inputs` 先看闸门再看期，于是暂停的场景报 `blocked_paused` 而不是 `waiting_due`。执行器那一侧由 `run_once` 的 `not_ticking` 抬成 `BLOCKED(PAUSE)`（不吃 `attempts`） |

### 由原文直接得到的边界（动手前先钉住）

| # | 边界 | 为什么不是另一条写法 |
|---|---|---|
| 1 | 三种时间**各有一条判据**，且「没有工作」与「没有新数据」在三格上分开 | 「单次 `claimed=0` 不能证明后两者」逐字否掉了「空转即无事」这一读法。三种时间的观测面不同：即时重复看**外部被碰过几次**（`claimed` 可以是 0 而重抓仍在发生），新证据后看**盘上多了什么**，真实到期看**取回回执**（`RecheckRecord` / 观测行） |
| 2 | 「重放不重复计数」判在**两个量**上 | 外部调用次数（`FakeHttp.calls` / `fake.created_units`）与盘上**行数**（那一行还是那一行，不是两行）。只判其中一个的形态是「没有重复花钱、但账上多了一件」或者反过来 |
| 3 | 裁决之后欠下的是**一次重评**，不是一个结论 | 与 `intents_for_settlement` 的 docstring 逐字同一条：「本函数只说『这一件欠着一次复核』。**不许**在这里顺手判一个到期」。裁决人只说「这件事该怎么办」，「该重看哪些格子」由 `what_is_due` 在那一刻判 |
| 4 | 受影响对象取自**案件自己带的快照** | `ReviewCase.affected_refs` 就是 §12 的「受影响对象快照」（`application/review.py::case_for` 的入参，受理侧写的是那一行本身）。从别处再推一遍等于给「这一件针对谁」第二个产地，而两个产地漂移时**两份报告都自洽** |
| 5 | 重评走**已有的**维护那一路，不另造一套 | 架构 `:135` 逐字：「重评**先用现有事实**，证据不足才重抓」。`REFRESHABLE_ASPECTS` 那一路（窗口内复用、过期才定向抓取、不可达不写观测）就是这句话的实现。另造一套的代价不是多写代码，是**两个产地判同一件事** |
| 6 | 「草稿版本更新」走**已有的**起草那一路 | 版本递增与 `supersedes` 的机制已经在了（`application/outreach.py:1399` / `:1443`，`generation_key` 把输入与 `policy_version` 算在一起）。它今天由 `draft_outreach` 这一条**工具**驱动，而 §G2 那三步里只有前两步写着「自动」 |
| 7 | 停机与无新数据**分开**判在工具层 | `classify` 那一层答不了这一问：一个缺执行器的部署与一个真的没活的部署在 `classify` 里可以同形（都落 `NO_WORK`）。分开它们的是服务面那两条（`continuation.py:82` 缺一个工厂就整条工具不注册；`store_state` 报 `no_store`） |
| 8 | 长周期语义用**受控时钟**，且**说清哪一半没测** | 「不等待一个月；长期运行稳定性仍另标未观测」是两句：前半句是本片的方法，后半句是本片的**边界**，要写进报告而不是留白 |

### 关闭条件逐条落到哪一类判据上

| # | 关闭条件 | 判据形态 |
|---|---|---|
| 1 | 断开控制客户端之后下一批自动执行 | 受理与编译之后**再无任何第三方调用**，`run_once_cli` 把盘上那两行推起来（G1 状态上重跑一次完整链） |
| 2 | 重启控制器之后重放不重复计数 | 两个**独立的** `_build`；第二次不重复派发（外部计数不变）、盘上行数不变、到期行照常被驱动 |
| 3 | 上一代遗留的在途件被收回并真的被重跑 | 种一行 `CLAIMED` 且 `holder` 是上一代 → 下一次 tick 报 `reclaimed`，且那一行真的被驱动（不是只放回队列） |
| 4 | 暂停 → 恢复 | 暂停期间那一行报 `BLOCKED(PAUSE)` 且 `attempts` 不动；恢复之后**同一行**被驱动 |
| 5 | 模拟到期 | 一条：`run_once(now=…)` 推过 `after_days`（受控时钟）；另一条：`observed_at` 种老之后服务面那一趟真的重取 |
| 6 | 受控来源变化 | 换那**一个**工厂（两个绑定都点名）→ 页面指纹变 → 新证据落行 + 记账 |
| 7 | 裁决 → 受影响对象重评 | 裁决落账**同时**欠下一行重评意图；下一趟控制器把它做掉，而受限对象**只有**案件上那一份快照 |
| 8 | 草稿版本更新 | 输入变之后重起草 → `draft_version` 加一、`supersedes` 指回上一版、上一版逐字未动 |
| 9 | 三种时间分开 | 三条判据各一条（外部计数 / 盘上新增 / 取回回执），且**即时重复那一趟的 `claimed` 是 0 而它仍被报出来** |
| 10 | 停机与无新数据可区分 | 缺一样接线 → `MISSING_CAPACITY`（含具体缺哪一样）；接齐而无活 → `NO_WORK`/`WAITING_DUE`；工具层「还没接线」与「没有工作在跑」两句分开 |

### 本轮不做（写下来，免得被读成已覆盖）

- **自动重起草**：见边界 6。「裁决 → 重评」是自动的（§9 逐字），「重评 → 重起草」不是，
  本片不声称它是。要让它自动，缺的不是一个调用点，是**一条把裁决与 wave 关联起来的
  业务判定**（哪一批草稿因这一版规则过时），而那是一条业务规则，不是接线。
- **真的事件日志与「重放一批历史事件」**：见边界 1/结论 1。本仓的持久重放是「重启读盘上
  那一行」，本片只证它的幂等，不造第二种重放。
- **长期运行稳定性**（跨天/跨周的服务进程）：§G2 明写「仍另标未观测」。
- **公网真来源**：受控来源变化用的是夹具那一趟（本地假抓取器），不是一次真的互联网抓取。

### 落地记录（工作树，本片）

#### 落到盘上的东西

| 文件 | 是什么 | 为什么是它 |
|---|---|---|
| `domain/continuation.py::intents_for_resolution` | 一次裁决欠下**恰好一行** `MAINTENANCE_TICK` 意图；**还在待办里的裁决什么都不欠** | 策略在领域。`business_key` 取 `resolution_id` 而不是 `case_id`：同一件案件能离开待办两次（放下 → 重开 → 再裁决），而那两次重看**都要发生**。受影响对象取 `case.subject_ref` —— §12 的受影响对象快照，从别处再推一遍等于给「这一件针对谁」第二个产地 |
| `adapters/sqlite/continuation_rows.py`（新文件） | `continuation` 表**写**那一半：`CONTINUATION_COLUMNS` / `flatten` / `insert_continuation` / `owe` | 第四个产出者落在**另一个端口**上（`ReviewCaseStore.record_resolution`）。`jobs.py` 里那条自己的 `INSERT` 一并收口到这里 —— 两份列序的形态是「加一列只改一处，另一处悄悄写空」 |
| `adapters/sqlite/review.py::record_resolution` | 在**同一个写事务**里 `owe` | §9 逐字；与 `ports/jobs.py` 的 `admit` / `settle` 同一分工（策略在领域，事务在实现） |
| `adapters/memory.py` | `owe` 由私有转**公开**；`InMemoryReviewCaseStore(*, jobs)`（**没有缺省值**）；`record_resolution` 同样欠账 | 缺省值会让「裁决欠下一次重评」在每个内存测试里**静默失效** —— 而失效的形态是那一族判据全绿 |
| `interfaces/controller.py::_run_payload` | 回执增 `not_ticking[]`（场景 + 逐字理由）与 `truncation` | 两笔**没有发生**的账。`not_ticking` 的主语是**场景**（闸门按场景关），`truncation` 的 `None` 与「装了上限、一条没砍」是两个值 |

#### 关闭条件 7 落在哪几条判据上

| 面 | 判据 |
|---|---|
| 裁决**同时**欠下一行，且写着那个对象是谁 | `test_a_resolution_owes_one_recheck_of_the_object_it_was_about`（两实现） |
| 还在待办里的裁决**什么都不欠** | `test_a_decision_that_leaves_the_case_pending_owes_nothing`（两实现） |
| 同一件重开之后再裁决 → **第二行**（键取 `resolution_id` 的理由） | `test_a_reopened_case_owes_a_second_recheck`（两实现） |
| 被拒的那一次一个字都不欠 | `test_a_refused_resolution_owes_nothing` + `test_an_outcome_the_case_cannot_take_is_refused` 末尾那次 ``continuations == ()`` |
| 决定与欠账**同生共死**（主存） | `test_a_resolution_whose_owe_fails_takes_the_decision_with_it`（注入故障） |
| 纵向：裁决 → 下一趟控制器把它做掉 | `tests/test_r2_continuity_acceptance.py::test_a_resolution_owes_one_recheck_on_the_case_own_subject_snapshot` |

#### 回执那两格（`not_ticking` / `truncation`）落在哪几条判据上

这两格此前**没有任何执行器**（回执形状的空缺）：`tests/test_resident_controller.py` 里
`test_a_scene_the_gate_holds_shut_is_named_in_the_receipt`（暂停的场景**点名**报出来，
且 `truncation is None`）与 `test_the_cap_that_dropped_work_is_in_the_receipt`（上限砍了
一条 ⇒ `cap/kept/dropped` 与那一行的名字都在，被砍的那一行**原样留在队列里**）。

#### 这一片实测出来的三个缺口（都补了判据或改了措辞，没有只登记）

1. **`owe` 的「没有才写」没有执行器，而且从端口出发根本够不着它。** 这是量出来的：
   `admit` 在 `existing is not None` 处直接 `return`，`settle` 在 `_already_done` 处直接
   `return` —— 三条产出者的重发**都走不到** `owe`。原 docstring 把「重放会落到同一个
   主键上」写成了事实，而那一句在今天的代码里不成立。**修法**：docstring 据实重写（它是
   第二道闸、第一道在上游短路、留着是因为 `owe` 是**跨端口**写入口而第四个产出者的重发
   没有上游短路替它挡），并补一条**直接对着 `owe`** 的判据
   （`test_owing_an_intent_that_is_already_there_leaves_that_row_alone`）。
2. **两条 `..._twice_still_owes_one` 的承重点缺失。** 它们的重放停在 `PENDING` 上，
   于是「只有一行」在 `INSERT OR REPLACE` 下同样成立 —— 判据看不出差别。**修法**：重放
   挪到那一行**已被控制器做掉之后**，断言从「行数」改成「**整行一格不动**」；docstring
   写清它们守的是上游短路，不是 `owe`。
3. **裁决那一侧的原子性没有执行器。** `jobs` 那一侧有
   `test_an_outbox_failure_takes_the_job_row_with_it`，而 `record_resolution` 这一侧没有 ——
   而这一片刚给它加了一条事务内的写。**修法**：补
   `test_a_resolution_whose_owe_fails_takes_the_decision_with_it`（注入故障，断言决定、
   案件、欠账三样一起回滚）。

**另有两处 docstring 声称了不成立的执行器作用**，一并据实改正（这属于「宁可少写一句，
不写一句假的」）：`tests/test_review_store_contract.py::test_a_refused_resolution_owes_nothing`
原写着「把 `owe` 挪到 `apply_resolution` 之前，它当场红」，
`adapters/memory.py::record_resolution` 原写着「挪到 `return` 之后、或挪到那两条 `raise`
之前，判据会红」。**两条都是假的**，实测如下。

#### 变异验证

| 变异 | 抓它的判据 | 结果 |
|---|---|---|
| A：`intents_for_resolution` 里 `is_pending` 分支失效 | `test_a_decision_that_leaves_the_case_pending_owes_nothing`、`test_a_reopened_case_owes_a_second_recheck`（两实现各一条） | **抓住**（4 红） |
| B：`business_key` 从 `resolution_id` 改成 `case_id` | 同上 4 条 + `test_a_resolution_owes_one_recheck_on_the_case_own_subject_snapshot` | **抓住**（5 红） |
| C：只把 `insert_continuation` 的 SQL 改成 `INSERT OR REPLACE` | —— | **变异本身没生效**：它在 `owe` 的守卫**之后**，是死路径。这一条是我自己搞错的，记在这里免得下次再犯 |
| C′：守卫去掉 + 先删后写（真正的无条件覆盖） | `test_owing_an_intent_that_is_already_there_leaves_that_row_alone`（**补了判据之后**） | **抓住**（1 红，且只红它） |
| D：内存侧把 `owe` 挪到「已记过」那条 `raise` 之前 | —— | **未抓住** ⇒ 查明：撞的是同一个 `resolution_id` ⇒ 同一个 `continuation_id`，被「没有才写」吸收 |
| D′：内存侧把 `owe` 挪到 `apply_resolution` 之前 | —— | **未抓住** ⇒ 查明：`intents_for_resolution` **自己**会先算一次 `apply_resolution`（判「还在待办里吗」），在被拒的输入上它比 `owe` 更早抛 |
| E：主存侧把 `owe` **移出写事务** | `test_a_resolution_whose_owe_fails_takes_the_decision_with_it`（**补了判据之后**） | **抓住**（1 红：决定留在盘上而欠账没落） |

C′ 与 E 是**先补判据、再变异**的顺序 —— 它们各自的判据就是为这两条承重判断新写的，
所以这两格同时也是那两条判据的存在理由。

#### 全量结果

`PYTHONPATH=. … -m pytest -o addopts="" -q -rf` → **5081 passed, 4 skipped**；
`ruff check .` 干净；`python -m marketops.schema` 重生成后与 committed **逐字节一致**。

#### 剩余缺口（如实登记，不假装覆盖）

1. **内存侧 `owe` 的位置结构上不可判**（D / D′ 两轮实测）。三条拒绝路径要么在 `owe`
   之前就抛（够不着它），要么撞同一个键被「没有才写」吸收。今天守这个位置的是主存那
   一条注入判据；内存侧没有等价物，也不硬造假判据。
2. **`test_a_refused_resolution_owes_nothing` 与 `test_an_outcome_...` 末尾那次
   `continuations == ()` 对今天的两个实现恒真**（理由见上面第 3 条与两处 docstring）。
   它们钉的是**盘面**（被拒不留欠账），不是那一句的位置。
3. **`tests/test_sqlite_continuation.py` 里有 4 处 `with connect(path) as conn`** ——
   `adapters/sqlite/db.py` 明文劝止这种写法（`sqlite3.Connection` 的上下文管理器管的是
   隐式事务，既不管事务也不关连接）。这 4 处是**本片之前**的既有写法，本次不动它们
   （属于另一片的范围），但**本片新增的那一处已改用 `session`**，不再添新的。
4. **`QueryLedger` 的缺省 `budget=8` 仍在**（另一片登记过的遗留，与本片无关，重述于此
   只为不丢）。

---

## 切片 11 · 文档与最终验收（本片）

**一句话**：把「哪几件事现在不必 Member-A 做了」写成一份**带证据位置的判词**，而不是把
测试数当完成。

### 落到盘上的东西

| 文件 | 内容 |
|---|---|
| `docs/R1_R2_AUTOMATION_ACCEPTANCE_2026-09-27.md` | **新**。分层判词（`R1_VERIFIED` / `R2_VERIFIED` / `PILOT_WITH_REVIEW` / `BLOCKED`）、五个 SHA、R1 与 R2 逐子项、G1 八条对照、§4 七项指标读数、未观测清单、自查 |
| 本表「当前状态」 | 切片 10 与 11 归位；全量读数从 `5058` 更新到 `5081`；R1/R2 判词从 `NO` 改为 `YES`（指向验收报告） |

### 这一片做出的四个判断（写下来，因为它们是这一片唯一的实质内容）

1. **`tested` 那一格填的就是 `implementation` 的 SHA，不另填。** 本轮的套件跑在
   `b2d0992` 那棵树上，没有第二份被改过的检出。**`live-acceptance` 那一格留空** ——
   本轮没有任何一次真实计费运行，「用同一个 SHA 冒充 live 验收」正是那一栏要防的事。
2. **§4 七项指标里 3 号与 4 号写 `unavailable`，不写 0。** 计划 §4 逐字要求
   「有独立标注…无标签时 unavailable。A/B 和 C/D 分开」「不以 evidence 行数算网页数；
   不凭价格缺失写 0 成本」。本轮**没有独立标注**，也没有真实请求 ⇒ 这两项没有仪器。
   可给的只是链上读数（G1 样本 2/2 走到草稿），而它在报告里被明确标成
   「这条链走得通」的证据、**不是**业务完成率。
3. **`test_every_control_names_a_real_executor` 不能替人读那八条对照。** 它自己的
   docstring 逐字写着「它不证明那些判据是对的」。所以报告 §3 的结论只说到
   「八条各自有在盘上的执行器且本轮跑绿」，并明写「措辞是否恰好覆盖 §G1 那一句的意思，
   需要人读一遍」。
4. **报告里不写「本分支 HEAD」。** 五个 SHA 全部指向**具体的提交**（`implementation`
   = 代码的最后一个提交，`本报告` = 本片的提交）。HEAD 会漂，而验收报告要能对着具体的
   树被复查 —— 何况现在本片之后还有一个只回填 SHA 的提交，写 HEAD 当场就错。

### 这一片**没有**做的事（免得被读成已覆盖）

- **没有写任何新的判据**，因此也没有变异可做。本片是文档片：它的产物是判词，
  判词的正确性由它引用的那些判据承担，不由本片承担。
- **没有顺手修**台账里登记的三处遗留（4 处 `with connect(...)`、`QueryLedger` 的缺省
  `budget=8`、内存侧 `owe` 位置无执行器）。它们各有归属片，混进文档片会让这一片的
  内容与标题对不上。
- **没有报任何节省百分比**：计划 §4 逐字「没有匹配基线就只交本轮观测，不报虚构节省
  百分比」，而本轮没有匹配基线。

---

## 工具链与约定（省得每段都重新试一遍）

```bash
# 套件（PYTHONPATH 必需；裸 python 在本机不存在）
PYTHONPATH=. /home/operator/toolchains/marketops-mvp/bin/python -m pytest -o addopts="" -q -rf
/home/operator/.local/bin/ruff check .          # CI 只跑这条，从不跑 ruff format
PYTHONPATH=. /home/operator/toolchains/marketops-mvp/bin/python -m marketops.schema
```

- ruff 行宽 100；提交尾必须有 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- PR 基础分支 `marketops/product-loop-20260923`；正文要写 base / design /
  implementation / tested / live-acceptance 五个 SHA；不自动合并、不 force-push。
- `docs/MCP_SERVICE.md` 被 `tests/test_security_catalog.py` 钉在 baseline `3744262af7…`，
  **不要动它**；新工具的文档行进 `docs/MCP_PRODUCT_LOOP.md` §2。
