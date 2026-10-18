# MarketOps 数据模型

> This document distinguishes the **current research-engine contracts** from the **target platform extensions** required for continuous discovery, execution and maintenance.

## 2026-09-27 design extension (not implemented-schema declaration)

The normative target for ProductLine/ProductVersion/Offer, GeoScope/Scenario/Scene, Asset- and Account-driven pools, RoleAssignment vs role priority, and cross-Scene facts is [Product and market model](PRODUCT_LINE_AND_SCENE_MODEL.md). Provider/fetch observations are specified in [Acquisition design](ACQUISITION_AND_PROVIDER_DESIGN.md); implementation and migrations are tracked only in [the execution plan](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md).

These are versioned references and business use cases, not a requirement for one table per noun. Reuse current SceneVersion, ApprovedClaim, evidence and policy stores. Current Python/contracts remain the authority for what is actually expressible; this documentation change does not claim those extensions already exist. Scene is within a workspace, not a workspace itself; ProductLine is not a job or a source pool. No private product document is published to Git by implication.

## 0. Target platform extensions

The existing Account / VenueAsset / ContactRoute / Evidence model remains the centre of the business graph. The full platform adds these concepts around it:

| Target concept | Purpose | Authority |
|---|---|---|
| `Scene` | long-lived market task inside a workspace: approved geography + application scenario + Offer + policies | MarketOps |
| `SceneRun` | one incremental human-steered execution cycle for a Scene/Workstream | MarketOps |
| `ExecutionMode` | `manual | bounded_auto | watch`; controls who may create the next Run, not how facts are stored | MarketOps |
| `RunDelta` | accountable merge result of one Run: discovered/promoted/updated/superseded/held/blocked/unchanged plus evidence/usage/next suggestions | MarketOps |
| `ResearchContext` | normalized geo/locale/entity/scenario/objective/product-use context resolved for a Run | MarketOps |
| `EffectiveResearchPlan` | frozen compiled source/query/page/role/acquisition/decision/budget/stop plan consumed by WorkItems | MarketOps |
| `FeedbackEvent` | Member-A/human correction, instruction or business knowledge with source, scope and proposed effect | MarketOps |
| `SourceMethod` | versioned definition of a repeatable source (OSM, GIS, directory, etc.) | MarketOps |
| `SourceRun` | one refresh/materialization of a source, with cursor/receipt/counts | MarketOps |
| `AssetCandidate` | raw/normalized candidate before eligibility is proven | MarketOps |
| `ResearchJob` | durable user/business request | MarketOps |
| `WorkItem` | executable unit compiled from a job/business event | MarketOps application layer |
| `ExecutionRef` | mapping from WorkItem to Quay task/run/gate | MarketOps ↔ Quay adapter |
| `DecisionRecord` | Jev/rules/LLM bounded decision with spec/version/confidence | MarketOps |
| `TemporalAssertion` | time-aware derived assertion over immutable observations | MarketOps |
| `ResearchPolicy` | sufficiency goals + hard query/fetch/token/time limits | MarketOps |
| `ContactSignal` | observed public non-email signal such as phone/contact page/profile URL | MarketOps, non-email-route |
| `WritingPolicy` | campaign writing constraints and approved personalization envelope | future authority zone |
| `SavedView` | side-effect-free saved query over shared market facts/workstream assessments | MarketOps |
| `Watch` | approved monitoring policy over a View/object set, aspects, budget and notification policy | MarketOps |
| `ActionItem` | durable next-step work/owner/due/blocker; follow-up pool is a projection over these | MarketOps |
| `ScheduleDefinition` | one-shot/calendar/due-state/event trigger policy; execution owned by one trigger driver | MarketOps |
| `NotificationEvent` | immutable material-change/needs-human/routine event emitted once from business state | MarketOps |
| `NotificationSubscription` | principal/team scope, filter, sink, cadence and quiet-hours policy | MarketOps |
| `DeliveryReceipt` | per sink attempt/delivery/unknown/failure without mutating the business event | MarketOps |
| `SessionBinding` | optional convenience focus/cursor for a client session; never business authority | MarketOps/client integration |
| `MessageDraft` | per-recipient evidence-grounded draft tied to one ContactRoute | future authority zone |
| `OutreachManifest` | frozen authorized candidate set for a wave | future authority zone |
| `ReplyEvent` | human reply/bounce/unsubscribe/system event | future authority zone |

### Scene / Run lifecycle

```text
Scene / Workstream
  → explicit RunSpec + ExecutionMode
  → ResearchContext
  → EffectiveResearchPlan (frozen)
  → SceneRun
  → due WorkItem[]
  → Quay execution
  → deterministic validation
  → RunDelta
  → validated merge into shared Market Graph
  → FeedbackEvent / Review / next recommendation
  → [manual: stop and wait for Member-A]
     [bounded_auto/watch: only create next Run if the explicit authorization still permits it]
```

A Scene/Workstream is the user's long-lived research objective. A SceneRun is one bounded execution cycle, not proof that a market is fully covered. Quay tasks are execution children of that run rather than the durable business identity of the Scene.

`manual` is the default execution mode. Disconnecting the client must not cancel an admitted Run, but **finishing that Run does not authorize the service to invent the next Run**. `bounded_auto` and `watch` are separate explicit authorizations with cumulative budget/scope/time/stop conditions.

A Run's merge is represented by `RunDelta`; pool growth is therefore not synonymous with row-count growth. Supersession, withdrawal, conflict, held, blocked and unchanged are legitimate outcomes and preserve evidence/history.

### Human knowledge / feedback lineage

```text
Member-A/client natural-language feedback
  → FeedbackEvent (literal source + principal + run/workstream/object refs)
  → classify effect/scope
     ├─ fact correction → append observation/supersession + affected re-evaluation
     ├─ run instruction → only future unleased work / next Run
     ├─ workstream policy → impact preview → approved policy revision
     └─ experience candidate → replay/evaluation → PolicyPatch proposal
  → versioned effect + rollback path
```

Feedback scope is explicit (`object/run/workstream/segment/scenario/geo/workspace`) and defaults to the narrowest justified scope. A chat message is evidence that Member-A said something; it is not automatically a global business rule. Client memory/Skill may help present context but is never the authority for whether a feedback item is currently effective.

### Research planning lineage

```text
RunSpec + current shared facts
  → ResearchContext Resolver
  → versioned Strategy modules
  → EffectiveResearchPlan
  → WorkItem snapshots
  → provider/worker receipts
```

Every provider/worker call should be attributable to the exact plan step that selected it. Country and language remain separate fields; translation/normalization never overwrites literal source text.

### Temporal assertion model

MarketOps should preserve immutable observations and derive current business assertions from them.

```text
Evidence / Observation
    source_url
    content_hash
    source_published_at?
    observed_at
          │
          ▼
TemporalAssertion
    subject_id
    predicate
    object/value
    valid_from?
    valid_to?
    last_verified_at
    currentness = current | historical | stale | contradicted | unknown
    supersedes / contradicted_by refs
```

A dated 2021 PDF can prove that a person held a role in 2021; it does not by itself prove the person still holds that role today.

### Research sufficiency and stop semantics

`ResearchPolicy` should express both desired coverage and hard limits, for example:

```text
preferred_functions
min_strong_routes
desired_redundancy
max_queries
max_fetches
max_tokens
max_wall_time
```

A `ResearchRun` / `WorkItem` records a structured stop reason such as `goal_met`, `budget_exhausted`, `blocked`, `ambiguous_hold`, `no_useful_next_action` or `manual_pause`.

### ContactSignal vs ContactRoute

A public phone number, official contact form/page, or public professional profile can be retained as a `ContactSignal` or attached to a `RouteOpportunity`.

It is not automatically a valid email `ContactRoute`, does not receive A/B/C/D email-route semantics by implication, and carries no send authority.

### Personalized outreach draft

Future outreach drafting is per recipient:

```text
WritingPolicy + ContactRoute + Account/Asset evidence
    → MessageDraft
    → factual/style validation
    → candidate manifest
    → explicit authorization
```

The draft stores evidence refs for personalized factual claims so later validation can reject unsupported personalization.

### Source lineage

```text
SourceMethod
  → SourceRun
  → AssetCandidate
  → VenueAsset
  → AccountVenueRelation
  → Account
```

A source candidate is not automatically an eligible asset/account. Promotion requires the profile's evidence policy.

### Work/execution lineage

```text
ResearchJob
  → WorkItem[]
  → ExecutionRef (Quay task/run)
  → artifacts
  → deterministic validation
  → business-state mutation
```

Quay state is execution metadata, not business truth.

### Decision lineage

```text
DecisionSpec + structured state + evidence refs
  → DecisionProvider
  → DecisionRecord
  → deterministic policy
  → action / escalation / hold
```

A Jev/LLM decision never replaces Evidence.

A `DecisionRecord` is durable as of §2's close-out: it is a row in
`state/marketops.db` (`decision_record`), readable by `record_id` through
`get_decision`, and **attributable by construction** — `work_item_id` is a real
foreign key, so the database itself refuses a record that belongs to no work.
Two keys, deliberately different:

| Key | What it answers | Contains a clock? |
|---|---|---|
| `record_id` | which row is this (content-addressed: input + provider + `decided_at`) | yes |
| `(work_item_id, provider, case_id)` | has **this question** already been paid for | no |

The second is a `UNIQUE` constraint and is checked **before** the provider is
called. `record_id` alone cannot do that job: `decided_at` is in its recipe, so a
replay computes a *different* id — content addressing stops one judgement from
being filed twice, not from being *bought* twice. The `provider` segment is
load-bearing: the same question asked of two backends is two payments and two
records, and `case_id` — derived from the spec version, the questions, the text
and the refs — carries no provider.

### Freshness

Target facts/routes should carry enough temporal state to distinguish:

- newly observed;
- currently verified;
- stale and due for review;
- contradicted/superseded;
- no longer visible but not proven false.

This is required for continuously maintained account intelligence.

每一行落在哪一格由 `domain/temporal.py` 决定，取值是 `Freshness.currentness`
（`Currentness`），而**原因**另存一档（`FreshnessReason`）—— 取值答「现在算哪一档」，
原因答「凭什么」，两者都要存，因为它们会各自变化：

| 上面那一格 | 取值 | 原因 | 依据住在哪 |
|---|---|---|---|
| newly observed | `unknown` | `no_time_anchor` | 还没有可用时刻 |
| currently verified | `current` | `within_policy_window` / `recently_reverified` | 材料 |
| stale and due for review | `stale` | `beyond_policy_window` | 材料 |
| contradicted | `conflicting` | `contradicted` | 材料 |
| 来源划定了已结束的区间 | `historical` | `describes_a_past_state` | 材料 |
| **no longer visible but not proven false** | **`not_seen`** | `not_seen_on_recheck` | **一次观测** |

这张表是「上面那五种处境各由哪一格承载」，**不是一份穷尽的划分**：一条刚被观测到、
来源也写了发布时间的行落在 `current` 上，而上面第一行说的是「只有观测时刻」的那一种。
`superseded` 那一半今天没有专门取值 —— 它走的是「追加新行、保留旧行」，不是把旧行
改写成另一个当前性。

最后一行与其余几行的**依据**不同，这一点是本表存在的理由：其余的依据都在**材料**
里（来源写了什么时刻、什么区间），而 `not_seen` 的依据是**我们去看过一次、那次没
看到**。所以它不支撑任何业务判定（`Freshness.is_actionable` 为假）—— 「我们这次没
看到它」既不能说「他还在」，也不能说「他走了」；它触发的是复查，不是结论。
相应地在这一档上**不写任何日期**：`valid_to` 只由来源声明的区间写入 ——
`docs/ARCHITECTURE.md` 里那句是这条判据的出处，逐字：「新页面不见某人只触发复查，
不生成虚构离职日期」。

---

## Current research-engine contracts

```yaml
contracts:      contracts/*.schema.json（从代码导出，禁止手写）
enforcement:    tests/test_contracts_sync.py
```

**契约从模型导出，不手写。** 手写的契约一定会和代码漂移；
`marketops/schema.py` 从 pydantic 模型生成 JSON Schema，
`tests/test_contracts_sync.py` 断言磁盘与模型一致 —— 漂移会直接让测试失败，
磁盘上多出一份过时的契约文件同样会失败。

重新生成（**规范命令**，就是 `tests/test_contracts_sync.py` 失败信息里那一条）：

```bash
python -m marketops.schema
```

`python -m marketops.interfaces.cli export-contracts` 是同一个函数（`write_contracts`）
的转调，方便已经在本仓库 CLI 里的人；两条命令做的事**逐字节相同**。写下这层关系是
因为「一份操作、两个都发布出去的名字」正是本文件开头反对的那种情形（那里说的是
契约名，这里是命令名，危害一样）：必须指明哪一条是规范的，否则照做的人会以为是
两件不同的事，而两件不同的事**会漂移**。

---

## 1. 模型按**问题**切分，不按分层

标题原来写着「六个模型，回答两个不同的问题」。**两个数都不再成立**（模型早就多于
六个，问题也多于两个），而它们是一句没有任何执行器的算术 —— 与 §1.1 开头那句
同一个错法，所以这里不再给数，只给下面这张表。

模型的切分不是分层洁癖，是**几个不同的问题**：

| 问题 | 落在哪 | 模型 |
|---|---|---|
| **是什么**？谁拥有 / 运营 / 维护 / 预约控制 / 采购哪块场地；一条路线绑在哪个账号、哪个人、哪个岗位、哪条记录块上 | `domain/entities.py` | `VenueAsset` · `ResponsibleEntity` · `AccountVenueRelation` · `Person` · `Role` · `ContactRoute` · `RouteOpportunity` |
| **凭什么这么说**？字面证据与运行记录 | `domain/evidence.py` | `Evidence` · `AccountRecord` · `ResearchRun` |
| **这个市场现在有什么**？一个 Scene 手上的账号（§6） | `domain/account.py` | `MarketAccount` |
| **这一行走到了哪一步**？发现期的候选、母池回执里每一行的去向 | `domain/candidate.py` · `domain/receipt.py` | `AccountCandidate` · `SourceReceipt` |

因此 `ContactRoute.evidence_ids` 是**字符串引用**，不是对象引用：
一条路线在没有证据可读的情况下也应该能被读懂，而证据一旦被删掉，
路线必须**降级**而不是静静地继续成立。

### 1.1 契约表

这张表的**成员**由 `tests/test_contracts_sync.py` 对着 `EXPECTED_CONTRACTS` 逐行比对。

标题原来写着「十条契约」。那个数已经落后过两次（`account-candidate` 与
`source-receipt` 各自落盘时都没有回来改它），因为它是一句**没有任何执行器的算术** ——
与 `docs/MCP_PRODUCT_LOOP.md` 里那句「十六行里的十五行」同一种错法。所以这里不再给数：
下面每一行都被一条机器判据看着，而「一共几条」是数出来的，不是写下来的。

| 契约 | 模型 | 作用 |
|---|---|---|
| `account-record` | `domain/evidence.py::AccountRecord` | 名单里的一个待研究账号（**一次作业的输入**） |
| `account-candidate` | `domain/candidate.py::AccountCandidate` | 发现期的一行候选 —— 还不是账号（没有 `cohort`，也没有 `account_id`） |
| `market-account` | `domain/account.py::MarketAccount` | **这个市场现在有哪些账号**（§6）：有血统、有证据、有新鲜度，以及本 Scene 的准入判定。它的「关联场地」与「责任关系」不在这行上，由 `account-venue-relation` 回答 |
| `account-cohort` | `domain/cohort.py::AccountCohort` | **一次读池子冻下来的成员名单**（§B12）：成员与顺序（决定 `work_item_id`）、选它的规则、以及被排除的每一个账号**为什么**没进来。它不判断准入 —— `status` 是读回来的、不是这里重判的 |
| `source-receipt` | `domain/receipt.py::SourceReceipt` | 一次母池读取里**每一行去了哪里** |
| `venue-asset` | `domain/entities.py::VenueAsset` | 一块场地 / 资产 |
| `responsible-entity` | `domain/entities.py::ResponsibleEntity` | 对某块场地负责的组织 |
| `account-venue-relation` | `domain/entities.py::AccountVenueRelation` | 「谁对哪块场地负什么责」 |
| `person` | `domain/entities.py::Person` | 一个具名自然人 |
| `role` | `domain/entities.py::Role` | 一个字面岗位 + 它的类别与业务口径 |
| `contact-route` | `domain/entities.py::ContactRoute` | **一条可联系的路线** |
| `route-opportunity` | `domain/entities.py::RouteOpportunity` | 一条尚未解决的路线机会 |
| `evidence` | `domain/evidence.py::Evidence` | 一条字面证据 |
| `research-run` | `domain/evidence.py::ResearchRun` | 一次运行的元数据 |
| `fetch-attempt` | `domain/fetch_attempt.py::FetchAttempt` | **一次访问的实测记录**（采集设计 §3）。它与上一行不是同一个层次：`evidence` 说的是「据此认为业务事实是什么」，它说的是「那个 URL 那天到底发生了什么」。前者可以没有（没抓到就没有证据），它**每一次尝试都有**，包括失败的那些 |

全部 `extra="forbid"` 且 `frozen=True`：
多出来的字段会被拒绝而不是静默丢弃；对象构造后不可变。

### 1.2 四条硬约束（都由 pydantic 在校验期强制）

1. **账号 ≠ 联系路线**（SOP L78）。一个 Council / Trust 可以管很多场地，
   一个场地可以由别的组织运营维护，一个人可以对多个场地负责。
2. **A 与 B 要求字面公开邮箱**（SOP L125 / L126）。`ContactRoute.email`
   是必填且非空的。
3. **P3 没有邮箱**，所以它只可能出现在 `RouteOpportunity` 上。
4. 路线与本阶段所有产出一样，`send_authorized` 恒为 `False`。

在这四条之外还有一条**跨产出**的硬约束：`Evidence` / `ContactRoute` /
`RouteOpportunity`，以及三张**池子行**（`VenueAsset`、`AccountVenueRelation`、
`MarketAccount`）都必填 `workspace`（**无默认值**，见 §5.1）。它约束的不是业务取值，
而是「这一行属于谁」—— 上面四条里没有一条管得了「这条结论是哪个场景的」。

---

## 2. 场地与责任：为什么场地不是账号的字段

```text
VenueAsset          venue_asset_id · name · venue_type · surface · country · address
ResponsibleEntity   entity_id · name · entity_type · country · domain
AccountVenueRelation workspace · account_id · venue_asset_id · relation · evidence_ids
                    · decision_record_ids · first_seen_at · last_verified_at
```

`AccountVenueRelation` 的键是
**`(workspace, account_id, venue_asset_id, relation)`** —— 四者的组合，**不是
`venue_name`**。这个模型存在的全部理由就是「同名场地串号」在结构上不可能发生：
场地从来不是键。`workspace` 那一段与 `VenueAsset` / `Evidence` 同一条理由（跨 Scene
的复用止于工作区边界，见 §5.1）：同一块场地与同一个账号，在两个 Scene 下可以判出
不同的关系，所以那是两行。

`relation` 取值：`owns` / `operates` / `maintains` / `booking_control` / `procures` /
`unresolved`。同一条关系可以有多条证据；**一条证据都没有的关系写不出来** —— 空表
在校验期报错，不是一句自觉（`unresolved` 与其余五个一样严）。`relation_id` 没有作为
一列存下来：四段键就是身份本身，再存一个句柄是同一句话的第二份拷贝，会漂；要一个
可打印的键时用派生方法 `relation_key()`。

`decision_record_ids` 是产生（或再次确认）这一行的那些判定的
`decision_record.record_id`。它存在是因为「这条关系是被判出来的」这件事必须**写下来**
而不是**推出来**：上一轮真实验收只能按「证据集合与某条判定的 `evidence_refs` 恰相同、
答案取值也恰相同」反查，而同一个账号对同一批页面可以问出两个不同的问题 —— 两条判定
引用同一批证据、给出同一个取值，集合相等分不出这两件事。取**并集**（保序、去重，与
`evidence_ids` 同一条规则）：一条关系被再判一次是加强，只留最新那一个 id 会让更早那次
调用从结果上反查不到，而它带进来的证据还在这一行里。

**空表在这一层是合法的**，它说的是「这一行不是一次判定得出的」—— 手工那条路径
（`remember_responsibility`）就是人把结论交上来，它手里没有判定台账。研究那条路径
（`remember_researched_relations`）不允许空：每条结论本来就带 `decision_id`，且当场
回台账核对（工作单元、主语、题面、答案取值四条），核对不过的行根本不写。

**已知缺口（属于 §11）**：`region` 或 `profile_id` 改了以后，
`SCENE_IMPACT_RULES`（`domain/scene.py`）要求责任关系重判，而盘上没有任何一格能说明
某一行是改之前判的。这一格没有加在这里，因为它该同时落在资产池、账号池与关系表上，
而它的读者是「什么该重做」那一层（`what_is_due(scene, now)`）。在那之前，
`first_seen_at` / `last_verified_at` 说的是「我们什么时候看的」，**不是**「当时按哪一版
规则判的」。

`ResponsibleEntity.entity_id` 与 `AccountRecord.account_id` 是**同一空间里的同一个
标识**：被研究的目标组织在这里是责任实体，在名单里是账号。分成两个模型是刻意的 ——
「谁负责这块场地」可能超出当前名单，而名单里的组织也可能一块场地都不负责。

---

## 3. 人与岗位

```text
Person   person_id · full_name · account_id · notes
Role     role_id · title · account_id · person_id · role_class · business_function
```

`person_id` 与 `role_id` 都是**内容寻址**的：同一账号下同一个字面姓名就是同一个人，
同一个字面岗位就是同一个岗位。「同一个人换了岗位」因此是两条 `Role`。

**人没有自己的 A/B/C/D/X** —— 等级挂在路线上。同一个人可以有多条路线
（工作邮箱、部门邮箱），它们各自的强度与语境不同。

`role_class` ∈ `decision` / `influencer` / `operational` / `gatekeeper` /
`excluded` / `unknown`；`business_function` ∈ `grounds` / `facilities` / `estates` /
`venue` / `operations` / `maintenance` / `bookings` / `procurement` / `finance` /
`admissions` / `hr` / `teaching` / `administration` / `customer_service` / `unknown`。

两者是**独立**的业务事实：grounds leadership 是 `decision + grounds`，
venue manager 是 `influencer + operations`，finance 是 `excluded + finance`。
压成一个字段，C 级「与场地业务口径绑定」这条规则就没法表达。

---

## 4. ContactRoute —— 定级的主语

一条路线 = **Account + Person/Department + Role/Business Function +
literal 公开 Email + Evidence**（SOP L125–L129）。

| 字段 | 说明 |
|---|---|
| `workspace` | **必填、无默认值** —— 这条路线属于哪个 Scene（= 部署的 `profile`）。见 §5.1 |
| `account_id` | 归属账号（必填） |
| `venue_asset_id` | 绑在哪块场地资产上。**可空** —— 组织级入口不对应单一场地 |
| `person_id` / `person_name` / `role_id` / `role_title` | 具名人与岗位。多义时留空（见 `has_conflict`） |
| `role_class` / `business_function` | 见 §2 |
| `entry_kind` | `named_role` / `official_business_entry` / `generic_official` / `excluded` |
| `block_priority` | P1 / P2 / P3 / P4，**或 `None`** |
| `binding` | `strong` / `account` / `none` |
| `email` | **必填**，规范化后（域名小写，本地部分保持原样 —— RFC 5321 下大小写敏感） |
| `evidence_ids` | 支撑本条路线的全部证据 id。空列表意味着「无证据」 |
| `grade` / `grade_reason` | A/B/C/D/X + 为什么 |
| `status` | 路线自己的状态机，见 §6 |
| `has_conflict` | 证据之间归属冲突（同页多邮箱 + 具名角色，无法确定对应关系） |
| `first_seen_at` / `last_verified_at` / `source_published_at` / `valid_from` / `valid_to` | §9 的四个时刻，全部可空 —— 见 §4.3 |
| `freshness` | 一次新鲜度判定的**结论**（存下来而不是现算）—— 见 §4.3 |
| `research_only` / `send_authorized` | **恒为 True / False**，validator 拒绝另一侧 |

`block_priority = None` **是有含义的取值**：这条路线不属于 SOP 定义过的任何一级
（既没有同块的具名人或岗位，也不是泛化官方入口）。把它强行读成 P1 或 P4，
会让「这是哪一级」变成实现细节；留成 `None`，定级规则会因没有匹配而 fail closed，
读到的是「SOP 没定义这一级」这一事实。

### 4.1 RouteOpportunity —— 没有邮箱的那一半

```text
RouteOpportunity   workspace · account_id · venue_asset_id · person_id / person_name ·
                   role_id / role_title · role_class · business_function ·
                   block_priority（默认 P3）· evidence_ids · reason ·
                   四个时刻 + freshness（与 ContactRoute 同名同义，见 §4.3）
```

它是**一个类型，不是一个等级**。模型里没有 `email` 字段 —— 不是"留空"，
是"不存在"，因此不可能被下游当成一条可联系的路线。`reason` 当前只可能取
`no_literal_email`。

把它做成独立类型，是因为「有具名角色但拿不到邮箱」最常见的处置就是
**把它记成一条 B 级路线**：那样"拿到路线的比例"会看起来比实际高，
而且没有任何一处会报错。

### 4.2 两条模型级不变量

```python
# 1) 本阶段不可能构造出会发送的对象
ContactRoute(send_authorized=True)    # → ValidationError
RouteOpportunity(research_only=False) # → ValidationError

# 2) 非 X 的判定必须挂证据
ContactRoute(grade=Grade.A)           # → ValidationError（没有 evidence_ids）

# 3) 机会必须至少有一个字面姓名或字面岗位
RouteOpportunity(account_id="a1")     # → ValidationError
```

### 4.3 §9 的时刻与当前性（路线与机会两族同名同义）

```text
first_seen_at          我们第一次看到这条路线/机会所依据的材料的时刻（由 bind 折出）
last_verified_at       最近一次**确认它仍然成立**的时刻      —— 今天没有生产者
source_published_at    来源自己声明的发布时刻（多说一句：不是取回时刻）—— 由 bind 折出
valid_from / valid_to  事实自身的有效区间，若来源明说了        —— 今天没有生产者
freshness              一次判定的结论：currentness · reason · policy_version ·
                       policy_calibrated · evaluated_at · age_days · assertion_id
```

四个时刻里**只有两个有生产者**，另外两个与 `freshness` 是**格子先于读者**：写下来
是因为 §9 要求把它们存住，而不是因为它们今天就能被填上。「今天抓到一个旧文档」不等于
「今天验证了那个事实」—— 所以 `last_verified_at` 只能由真的去核过的那一步写，而重抓一次
材料**不算**（`domain/temporal.py` 的整段理由）。没有生产者的格子留 `None`：`None`
读作「这一格没有值」，不是某个用别的时刻顶上的数。

`freshness` 一旦写下来，就必须**能从这一行自己算出来**：`assertion_id` 是内容寻址的
（主语 `account:<workspace>:<account_id>`、谓语 `published_contact_email` /
`unresolved_contact_route`、值、观测时刻 = `first_seen_at`），行上的 validator 拿这一行
自己的时刻重算一遍，对不上就拒绝。这一条挡的是**复制一行改内容**时把上一行的判定结论
一起带过去 —— 带过去的那一格长得完全正常，读起来像「这一行也判过了」。

定级（§2 的 A/B/C/D）**不读**这些格：判据是 SOP 的归属与入口规则。把当前性折进等级
会让一条 A 级路线因为材料旧了而降级，而正确的处置是去重验它
（`tests/test_grading.py::test_the_grade_does_not_read_the_moments_or_the_currentness`）。

`freshness` 的**生产者**是 `application/freshness.py::judge`，由 `pipeline._parse_and_bind`
在每一行落库之前对两族行各调一次（路线与机会都判）。它读的时限来自 profile 的
`freshness` 块，**没有声明就抛**（`policy_of`，不落一组缺省天数）：那组数字看起来完全
合理，却没有任何人认过，而每一行的 `currentness` 都会显得是按某个业务标准算出来的。
判完的那一行是**重新构造**出来的（`model_validate`，不是 `model_copy`），所以每一行
落盘之前都真的过了一遍上面那条「结论必须属于这一行」的判据。
没有 `first_seen_at` 的行**不判**：断言 id 的原像里有观测时刻，没有它就算不出这条断言
的身份。那一格留 `None`，并按 `freshness_without_a_time_anchor` 单独计数 —— 它读作
「没判过」，不是「判了、结论是 stale」（后者是另一个结论）。

`policy_version` 与 `policy_calibrated` 两格都要存：版本答「按什么算的」，布尔答「那套
东西当时算不算获批口径」。同一批数字被一位人类批准之后版本号不必变（变的不是数字，
是「有没有人认过」），而此前按同一批数字算出的每一条结论的**读法**都变了。
`FreshnessPolicy.calibrated` 的缺省是 ``false`` —— 缺省是「没人认过」，不是「已批准」。

导出到契约后表现为 `const: false` / `const: true` / `required: ["email"]`，
下游系统据此就知道哪些字段不可被打开。

---

## 5. AccountRecord 与 Evidence

### AccountRecord

| 字段 | 类型 | 说明 |
|---|---|---|
| `account_id` | str | 会被用作文件名。**禁止**平台保留字符与首尾空白 |
| `organisation` | str | 组织名 |
| `cohort` | enum | `known_success` / `accessible_no_core_email` / `previously_blocked` |
| `status` | enum | 账号准入状态，默认 `discovered`。见 §6 |
| `domain` | str | 有值时生成 `site:` 限定检索 |
| `country` | str / null | 默认 `null` = **还没查**（不是「不在英国」）。旧缺省 `"UK"` 是一次没人做过的判定，会让 `out_of_region` 的 `requires: ["!country in [gb, uk]"]` 对没写国家的记录**静默不触发** |
| `notes` | str | 人类备注。**勿写 secret** —— 它会进运行数据目录 |

### Evidence

证据不是「结论」，是「结论的依据」。

| 字段 | 说明 |
|---|---|
| `workspace` | **必填、无默认值** —— 这条证据属于哪个 Scene。见 §5.1 |
| `evidence_id` | 内容寻址：`sha256(account_id ‖ source_url ‖ field ‖ literal_value ‖ content_sha256)[:32]`。同一处证据重复出现时 id 相同 → 写入天然幂等 |
| `source_kind` | `search_snippet` / `fetched_html` / `fetched_pdf` / `fetched_text` |
| `literal_value` | **字面**片段。不得首尾带空白；不得为空 |
| `content_sha256` | 指向镜像文件的具体字节 |
| `context` | 命中处周围文本，供人工复核。不参与自动化判定 |
| `page_locator` | 命中位置描述（如 `page 3` / `block 12`） |
| `parser` / `tier` / `http_status` | 谁抽的、走的哪一层、HTTP 状态 |
| `field` | 本条证据支撑的字段（email / name / role / organisation） |

```python
evidence.binding_eligible  # source_kind ∈ {fetched_html, fetched_pdf, fetched_text}
```

`search_snippet` 恒为 `False`。这是「SERP snippet 里的邮箱不能直接成为
Contact」在数据层面的表达 —— 不是流程约定，是类型层的拒绝。

### 5.1 血统：三行产出都写着自己属于谁

`Evidence` / `ContactRoute` / `RouteOpportunity` 各有一个 `workspace`，
**必填、无默认值**，取值是部署的 `profile`（当前只有
`uk-acme-natural-grass` 一个）。三条纪律：

**一、它没有默认值，也不许有。** 一个「缺省 = 当前 Scene」的默认值会把「没写血统」
变成一次**不留痕的放行**：写入失败的关系字段、旧版本写下的行、另一台机器导过来的
行，全都会安安静静地读成「属于当前部署」。必填之后，缺这一格的对象在构造期就
`ValidationError` —— 那是一次立刻可见的失败，而不是一份事后无人能查的报告。

**二、取值要过规范 id 校验。** `workspace` 走 `check_canonical_id`（slugify 不动点），
所以 `uk acme` 与空串都不是血统，是拼错的字段。**不做规范化**：把它悄悄
规范成 `uk-acme-natural-grass` 等于把一次错配读成一次命中。

**三、`evidence_id` 的原像里没有 `workspace`。** 三个存储看到的是**同一个 id 空间**
（§5 的公式没有变，`evidence_id` 仍是内容寻址）—— 分开两个 Scene 的不是 id，是
这一格加下面这条存储判据：

| 动作 | 别人那些行 |
|---|---|
| 写（对象的 `workspace` ≠ 存储的） | `EvidenceError` |
| 读（行的 `workspace` ≠ 存储的） | `EvidenceError`，**点名那一行**（表 + 主键；JSONL 那一侧还有文件与行号） |
| 按 id 取（那个 id 没有行） | `None` —— 只剩这一个意思 |

**读别人的行必须报错，不能跳过。** 跳过之后，一个只装着别人那些行的账号返回的是
**空列表**，而它与「这个账号确实没有证据」在调用点长得完全一样 —— 越权读因此是一次
静默成功，报告里那句「这个账号没有证据」是假话。判据在
`marketops/ports/store.py` 的端口文档里，三个实现（`adapters/sqlite/`、
`adapters/jsonl/`、`adapters/memory.py`）由
`tests/test_evidence_store_contract.py` 一起跑同一组用例。

「一个存储只装一个工作区」这件事**逐实现**怎么拿到自己的取值，写在 §9。

---

## 6. 两条状态机

两个模型各有自己的状态，**互不决定**：

```text
AccountRecord.status   discovered → eligible | hold | irrelevant | duplicate
                       → researched → archived
ContactRoute.status    observed → queued_candidate → prepared_pending_authorization
                       → sent_stage_1..4 → reply | suppressed | bounced | manual_review | closed
```

账号侧由 `grade_account` 的规则产出（见 `docs/PRODUCT_SPEC.md` §4.2）；
路线侧由流程推进。注意 `sent_stage_*` 在 `send_authorized` 恒为 `False` 的
本阶段**不可达** —— `marketops/enums.py::UNREACHABLE_WITHOUT_AUTHORIZATION`
把这件事写成了一个显式常量，而不是一句注释。

---

## 7. Grade 与 GradeReason

**A/B/C/D 是不透明标签，代码里没有第二份语义定义。**

```text
语义来源：profiles/<id>/grading.yaml
引擎职责：路线事实 → 等级；规则取不到可求值的形式时拒绝判定（X + rules_unknown）
```

| GradeReason | 含义 |
|---|---|
| `rule_matched` | 命中了某条已定义的规则 |
| `rules_unknown` | 没有任何一级匹配（fail closed） |
| `no_eligible_evidence` | 没有任何合格证据 |
| `conflicting_evidence` | 证据归属冲突 |
| `excluded_entry` | 排除类别（SOP L129） |

判定优先级：**排除 → 冲突 → 无证据 → 规则**。
即 `has_conflict=True` 时不论规则多齐全都降为 X。

### 7.1 事实与子句方言

引擎只提供路线自己的中性事实，不含业务阈值：
`has_literal_email` · `binding` · `role_class` · `business_function` ·
`entry_kind` · `block_priority` · `has_conflict` · `email_domain_matches_account` …

规则子句语法（刻意极小，四种）：

```yaml
requires:
  - has_literal_email              # 事实为真
  - "!has_conflict"                # 取反
  - "binding==strong"              # 等值比较
  - "role_class in [decision, influencer]"   # 集合成员
```

未匹配任何一级 → `X` + `rules_unknown`。**没有 `fallback_grade`**，
profile 声明它会被装载层拒绝（见 `docs/PRODUCT_SPEC.md` §3.3）。

未知事实名在**装载期**就让 profile 装载失败，而不是静默当作 False ——
`grading.yaml` 里写错一个词不应该变成「这条规则永远不生效」的静默故障。

---

## 8. 运行指标（`application/metrics.py`）

核心不是 `domains/min`。因此这些量是一等公民字段：

| 指标 | 位置 | 含义 |
|---|---|---|
| `net_new_ab` | `AccountMetrics` / `RunReport` | 本次新拿到的 A + B |
| `net_new_cd` | 同上 | 本次新拿到的 C + D |
| `blocked_recovery` | `RunReport` | previous_blocked 队列中**产出了东西**的账号数 |
| `still_blocked` | `RunReport` | 仍然没产出的 |
| `false_attribution` | `RunReport` | 归属冲突数。目标 0 |
| `serp_queries` | 同上 | **计划**发出的 SERP 查询数 |
| `serp_queries_skipped` | `AccountMetrics` / `RunReport` | 已付过账、因此**未重发**的查询数 |
| `search_budget_exhausted` / `budget_exhausted_accounts` | `AccountMetrics` / `RunReport` | 被检索额度**截断**（的账号数） |
| `candidate_urls` / `candidate_urls_dropped` | `AccountMetrics` | 收下的候选 URL 数 / 被 `max_candidates_per_account` **砍掉**的候选 URL 数 |
| `browser_seconds` | 同上 | 浏览器耗时（成本指标） |
| `snippet_bindings_rejected` | `AccountMetrics` | 被拒绝的 snippet 绑定数 |
| `freshness_judged` / `freshness_without_a_time_anchor` | `AccountMetrics` / `RunReport` | 判过时效的产出行数 / **判据没能开工**的行数（没有观测时刻） |
| `currentness` | 同上 | 判过的行按取值汇总（`current` / `stale` / `historical` / `unknown`） |

`net_new_ab` 只统计**路线**。一个账号产出了 `RouteOpportunity` 不算进去 ——
机会不是路线，把它们并进同一个计数，正是 §4.1 要防的那种"看起来比实际高"。

`serp_queries` 与另外两个字段的关系值得写下来：它是**计划数**，不是实际发出的数。
差额有两个来源，而且它们指向相反的行动 —— `search_budget_exhausted` 是「额度用完了，
要么改 profile 的声明、要么接受这个账号只查到一半」，`serp_queries_skipped` 是
「这些查询上一次运行已经付过账，所以这次一分钱没花」。把它们压回一个「0 个候选」，
一次被预算截断的运行看起来就跟一次跑完了一模一样。

`candidate_urls` 与 `candidate_urls_dropped` 是同一条理由的第二个实例。上限是
`max_candidates_per_account`（默认 12），而**只有前者时**「这个账号只找到 12 条」
与「找到 30 条、被上限砍到 12 条」是同一行 —— 前者是市场结论（这个账号确实少），
后者是配置结论（上限设低了，或者该改 profile 的声明）。截断这一层在
`application/caps.py`，被砍掉的条目本身也留在那里（报告里只有那个数）。

`freshness_judged` 与 `freshness_without_a_time_anchor` 是同一条理由的第三个实例，
而且这一对**不可互换**：前者是「判过了，结论可能是 `unknown`（判据的答案）」，后者是
「判据没能开工」。`currentness` 那一格只统计前者，零分母时它是**空表**而不是
`{"stale": 0}` —— 后者会让一次什么都没判的运行看起来像「判了、都很新」。

> `blocked_recovery` 的准确含义是「**这次跑通了**」，不是「拦截被绕过了」。
> 本项目不绕过拦截，recovery 只可能来自改走其他公开来源。

---

## 9. 存储布局

```text
<MARKETOPS_DATA_ROOT>/
├── state/
│   ├── marketops.db                             **主存**：预算账本 + 作业/工作单元
│   │                                            + 证据/路线/机会 + 判定记录
│   │                                            + 立项/维护/外联 + 池子
│   │                                            （场地资产 / 责任关系 / 账号池）
│   │                                            + 工作区绑定（§9.1）
│   ├── marketops.db.v1-<UTC>.bak                v1→v3 迁移**之前**的备份（§9.1）
│   │                                            （`.v1-` 写的是这次运行**从哪个版本起步**）
│   ├── evidence/<account_id>.jsonl              导出/审计：每行一条 Evidence
│   ├── routes/<account_id>.jsonl                导出/审计：每行一条 ContactRoute
│   └── route-opportunities/<account_id>.jsonl   导出/审计：每行一条 RouteOpportunity
├── mirrors/
│   ├── serper/<scope>/<hash16>.json             原始 SERP 响应
│   ├── pages/<account>/<hash16>.html            内容寻址的页面镜像
│   └── mineru/                                  MinerU 中间产物（启用后）
├── benchmarks/<run_id>.json                     运行指标
├── logs/
└── test-data/
```

`routes/` 这个目录以前叫 `contacts/`。改名不是整理 —— 目录里装的从来不是
"联系人"：一条路线是「账号 + 人/部门 + 岗位/业务职能 + 字面邮箱 + 证据」，
而"联系人"这个词会让它读起来像一个可以独立存在的人。
`tests/test_config.py::test_routes_and_opportunities_have_distinct_directories`
守着两者不共用一个目录：机会与路线分开存，才可能在统计上分开。

`mirrors/serper/` 下面是**一层作用域目录 + 一个内容寻址的文件名**，不是
`<账号>--<检索词>.json`。这条之前是错的，而且错得不只是排版：文件名里带着检索词，
意味着**下一个打开这棵目录的人**（另一个代理、下一轮研究、交出去的产出）能读到
同一个组织上一轮查过什么。检索词本身就是要被测量的量 —— 「第二个人会不会独立
想到同一个 query」正是两臂对照在测的东西 —— 把它写在文件名上，等于把测量对象
泄漏给被测对象。

于是身份改由 **这条检索式的身份（`SearchQuery.identity`：`<国家>/<语言>/<检索式>`）
的 `sha256[:16]`** 承担：同一个 query 落同一个文件、不覆盖也不重复写，而**目录树
本身不携带任何检索词**。作用域那一层保留下来（`<scope>`，如 `discovery-v1-arm-b`），
因为那是归属信息 —— 抹平它更省事，代价是两份产出混在一棵树上、事后分不开是哪一臂
花的钱。

身份里**含市场**（`2026-09-28`，B15-f）：同一条检索式在 GB/en 与 FR/fr 下返回的是
两批页面，所以是两份留档。按文本散列的实现在第二个市场那里撞上 `path.exists()`，
**那份响应不落盘**，而现象只是「文件数比预期少一个」—— 它不报错，只让复现能力
少一半。幂等键（`search:<身份>`）取同一个值，两处是同一个谓词的两种写法。

`tests/test_mirror_names_do_not_carry_the_query.py` 是这条判据的执行器，
它遍历整棵树断言检索词不出现，同时断言检索词**在文件里面**仍然可读 ——
只查「不出现」的判据，在「什么都没写」时同样是绿的。

### 主存是 `state/marketops.db`，不是那些 `.jsonl`

两者都在，但它们回答的问题不同，判据也不同：

| | SQLite（`adapters/sqlite/`） | JSONL（`adapters/jsonl/`） |
|---|---|---|
| 角色 | 主存 | 导出与审计通道 |
| 保证 | 并发下不超支、崩溃后不丢、事务性 | 不需要任何工具就能读 |
| 写入 | 写事务（`BEGIN IMMEDIATE`） | 全量读 + 追加，**无锁、非原子** |
| 坏了 | 抛 `StoreError` | 抛 `EvidenceError`（带行号） |

选 SQLite 当主存的那一条理由：§8 要求「原子预算预留」，而那件事在文件格式上
做不出来 —— 两个 worker 都读到同一个 `held`、都通过检查、合计超支，全程不报错、
不留日志，只在账单上表现为「花超了」。JSONL 留下的那一条理由是它不可替代的性质：
`grep` 一份三个月前的产出，与「先装 sqlite3、再想起表名」是两种成本 —— 而产出要
交给一个只拿到目录的人时，这个差别是决定性的。

SQLite 那张表里 payload 存的是 `model_dump(mode='json')` 的 **JSON 文本**，不是摊平的
列。摊平等于把契约钉死在这个存储上（加一个字段 = 一次迁移），而契约归 `contracts/`
与 `domain/` 管。代价写在明处：按 payload 内部字段查询要走 `json_extract`，没有索引。

**损坏处理**：两边都报错，都不静默跳过 —— 跳过会污染后续所有统计。

### 9.1 绑定：一个库、一个存档目录，只装一个工作区

§5.1 那条读写判据要有一个**取值**，而「这个存储属于谁」逐实现不同，所以写在这里：

| 实现 | 自己的工作区从哪来 | 什么时候定下来 |
|---|---|---|
| `adapters/sqlite/evidence_store.py` | `schema_meta['workspace']` | 迁移时写入；或**第一行写入**时由那一行的 `workspace` 建立 |
| `adapters/jsonl/store.py` | `Settings.profile` | 装载配置时（存档目录是**按部署**的，不是按行的） |
| `adapters/memory.py` | 同 sqlite | 第一行写入 |

绑定是**逐库**的事实，不是每次调用可以给的参数：一个状态库只装一个 Scene 的产出，
所以一个已经绑在别的工作区上的库，换一个 `--profile` 也迁不动它（`_require_binding_matches`）。
库自己的 `research_job.scene_id` 是第二份旁证 —— 它只在拒绝的方向上有用（一个库可以
没有任何作业）。

**旧版本的库被这一版代码打开时当场拒绝**，消息里点名要跑的那条命令，并说清**它欠着
哪一步**（v1 欠血统那一格，v2 欠词表）。不这么做的话，那些行会在模型校验上炸成一句
pydantic `ValidationError`（v1）或 `Aspect("asset_fact")`（v2）—— 一个看起来像「库坏了」
的失败，而事实是「这个库还没迁移」，两种处置完全不同。

**迁移**：`python -m marketops.adapters.sqlite.migrate`。它由两步组成（v1 → v2 补血统，
v2 → v3 换词表），但一个库里两步都要跑时是**一次备份、一个事务、一个版本号** —— 没有
「只迁到 v2」这个选项（那个中间状态没有人想要）。

* 三个取值（库、工作区、输入目录）默认都取**部署配置** —— 它就是这个动作的可信来源；
  `--profile` / `--inputs-dir` 可以覆盖，但盖上去的每一个值都还要过下面那串证明。
* 第一步逐行的证明**不来自被迁移的那一行**（那一行没有这一格，正是要补的东西），而来自
  本部署自己的输入，三档由强到弱：`roster-subject`（清单点名的账号）>
  `artifact-evidence-id`（发现工件逐字点名过的那条证据 id）> `artifact-subject`
  （工件派生出的候选 id）。清单、回执与发现工件是三种输入，读它们的活口在
  `application/lineage.py`。
* 第二步的判据不同：它要的不是**证明**而是**认得出来** —— 存量的 `aspect` 要么在 v2 的
  词表里（换名字的两个、被拆开的两个），要么整份拒绝。认不出来的词不许猜：猜出来的面
  会被人当一条事实读。
* **换名字的面**（`source_supply` → `source_coverage`、`responsibility` →
  `account_relation`）是同一格，三张表都就地改写。**被拆开的面**（`asset_fact` →
  `asset_identity` + `asset_attribute`、`role_email` → `contact_person_role` +
  `email_evidence`）在新词表里没有对应的那一格，于是逐表处置（`SPLIT_DISPOSITION`）：
  `observation` 与 `maintenance_claim` 里那几行**删除**（一次粗面的观测不能归给任何
  一半 —— 那会替另一半宣布「刚核过」，把真正的复核推迟一个周期），`change_signal` 里
  的那一行**拆成两条**（粗面变了就是两半都可能变了；方向是多花一次调用，不是少看一眼）。
  回执那一侧另有 `dedupe_key` 里编着 aspect 的约束：键按同一个函数**重算**，而存量键在
  重算之前要先被验证过一次，对不上就是「这一行不是它自己说的那个格子」。
  删了几行、为什么，逐表进报告 —— 而它们逐字都在备份里。
* **一行证不出来（或一个词认不出来）就整份拒绝，并点名那一行**（表 + 主键；存档那一侧
  是文件 + 行号）。半信半疑地改一部分比不改更坏：报告里「迁移完成」之后，剩下那些行就
  没有第二个人会回头看。拒绝发生在任何写入之前 —— 版本没动、存档逐字节未变、盘上没有备份。
* **备份**：库是 `<db>.v<起步版本>-<UTC%Y%m%dT%H%M%SZ>.bak`，迁移前用
  `sqlite3.Connection.backup` 生成并核对（打开它、读它的版本号必须是起步那个版本）；
  存档那一侧逐文件 `<file>.lineage-<stamp>.bak` —— JSONL 存档**不带版本**，往它的备份名
  里写一个版本号是写一句关于别的东西的声明。**重跑是核对，不是再改一遍**：已经带血统的行
  报告为「未改动」，不产生第二份备份 —— 否则「哪一份是迁移前」就没有答案了。
* 迁移**不建库**：建一个空的 v3 库由第一次写入自己做。两件事目的相反（迁移给历史行
  **证明**归属，建库是从今天开始记），让前者顺手做后者，一个拿错 `MARKETOPS_DATA_ROOT`
  的运行就会在别人的目录里建出一个空库，而那看起来完全正常。

**残余风险**（明说）：逐行证明的力气来自「输入是这次运行的输入」。两个 Scene 若共享
同一份清单、同一份工件与同一个库，这一层分不开它们 —— 那种情形下唯一的判据是部署
配置（`Settings.profile`）本身，而它正是绑定所记录的东西。

### 9.2 维护状态：三张表、七个面、以及五个**不落库**的字段

三个面各一张表，逐行都是一条**说过的话**，不是一次判定：

| 表 | 一行是什么 | 主键（= 幂等的范围） |
|---|---|---|
| `observation` | 「我核过这一格，而且拿到了内容」 | 工作区 + 主体 + 面 + 时刻 + 来源 |
| `change_signal` | 「有人报了这一格可能变了」 | 工作区 + 主体 + 面 + 时刻 + 理由 |
| `maintenance_claim` | 「这一格这一周期的这件事被谁领了」 | `dedupe_key` + `attempt` |

**七个面**（§11 那七个）是这个库的**词表**，不是这张文档的排版：`ALL_ASPECTS` 同时是
`RefreshPlan` 的顺序校验、缺面点名、到期选择器、报告里逐面计数与 MCP 工具面的唯一
来源。加一格不是加一行数据，是**这一层能表达的面多了一格**（`SCHEMA_VERSION` 因此跟它
走，见 §9.1）。两个面各自过期、各自有一次 `observed_at`，这就是维护按 `(主体, 面)`
分格的全部内容。

而 §11 要的那**五个字段**（`last_verified_at` / `next_due_at` / `status` /
`stale_reason` / `policy_version`）**一个都不在表里**：它们是
`domain/maintenance.py::aspect_states` 从上面三张表 + 生效版本那一版 `RefreshPlan`
现算出来的读法。这不是省事，是为了让「这格新不新」只有一个来源：

* `last_verified_at` 取这一格**最新的那条观测**（找不到就是 `None`，「从来没核过」）；
* `next_due_at` = 那个时刻 + 该面的 `after_days`；从未核过时它**空着**（那一格欠的是
  第一次，不是「下一次」，写 `now` 会让一个什么都没变的值每秒都在动）；
* `status` 与 `stale_reason` 是**同一次判定**的两面 —— 与 `what_is_due` 同源，
  所以不会出现「报告说它新、队列里却有它」；
* `policy_version` 是那份 `RefreshPlan` 的**内容摘要**（面 + 天数 + 是否标定），
  不是递增计数：它答的是「这两次读到的策略是同一份吗」。

落成一个列的话，这份读法就会有第二个来源，而它与「节奏改了」之间的漂移**不会报错** ——
报告上说某格还新，队列里却有它。`get_ops_report` 的 `freshness` 那一节就是这份读法
（`_state_row`），没到期的格子也在里面：它答的是「这个市场现在有哪些格子是旧的」，
而 `get_due_work` 答的是「下一轮做什么」。生效版本还不存在（`DRAFT`）时它是 `None` ——
「该多久看一次」这句话还没有，于是「旧不旧」无从谈起，那**不是**「一格都不旧」。

判据：`tests/test_sqlite_migrate.py`（迁移的成功与各种拒绝）、
`tests/test_evidence_store_contract.py`（三个实现同一组读写判据）、
`tests/test_models_invariants.py`（必填、无默认值、取值要规范）。