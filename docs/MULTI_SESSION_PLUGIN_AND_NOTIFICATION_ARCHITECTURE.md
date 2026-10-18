# 多 Session、插件、监控池与通知：MarketOps 最终交付形态

更新：2026-09-27。状态：**目标架构，待实现/验收；不是客户端插件已发布、飞书已接通或服务已上线声明。**

本文件把 MarketOps 收敛为一个用户可理解的产品：**一个 MarketOps 插件入口 + 一个持久化 MarketOps MCP 服务**。Member-A 可以同时打开多个 Codex、WorkBuddy 或以后其他获批准客户端 session，分别负责不同地区/任务；所有 session 共享同一个授权范围内的事实、任务、预算、监控和通知账本。聊天窗口不是业务状态的唯一持有者。

实施唯一清单仍是 [MEMBER_A_AUTOMATION_EXECUTION_PLAN.md](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md)。业务数据/场景/产品资料边界见 [PRODUCT_LINE_AND_SCENE_MODEL.md](PRODUCT_LINE_AND_SCENE_MODEL.md)，采集与供应商职责见 [ACQUISITION_AND_PROVIDER_DESIGN.md](ACQUISITION_AND_PROVIDER_DESIGN.md)。

## 1. 最终用户体验

Member-A 可以这样工作：

```text
Codex Session A: 英国 Devon 户外运动场研究
Codex Session B: 法国学校/体育设施研究
WorkBuddy Session C: 检查重点关注池 / 待跟进池
Codex Session D: 处理 Review Inbox / 草稿审核
Feishu 一个业务群: 接收有意义的摘要、异常和待决事项
```

它们全部连接：

```text
                 ┌─ Codex Session A
                 ├─ Codex Session B
MarketOps Plugin ├─ WorkBuddy Session C
                 ├─ 其他获准客户端
                 └─ Feishu 通知/命令适配（后续）
                         │
                         ▼
                  MarketOps MCP
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
Shared Market Graph   Workstreams       Control Plane
事实/证据/关系        研究/维护/草稿      schedule/watch/notify/review
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
       SQLite/受管工件 + Quay/Claude + Serper/HTTP/Crawl4AI/Jev
```

关闭 Session A 不停止服务器已经受理的任务；Session B 改成法国不会改变 A 的上下文；明天新开 Session E 可以读取 A 的真实进度继续工作。**不再迁移聊天全文，不再把进度总结进 Skill 才能接班。**

## 2. 一个插件、一个主 Skill、一个业务 MCP

### 2.1 一个插件是“分发单位”，不是一个巨型提示词

建议 canonical 客户端包包含：

```text
marketops-plugin/
├── SKILL.md                       # 主入口：识别任务、选择工作流、调用MCP
├── references/
│   ├── build-market.md            # 建池/身份/责任组织
│   ├── research-contact.md        # 人员/职位/公开联系路线
│   ├── monitor-and-refresh.md      # 监控池/变化/刷新
│   ├── follow-up-work.md           # ActionItem/Review/跟进工作
│   ├── prepare-draft.md            # 草稿与产品资料
│   └── access-diagnostics.md       # 抓取失败/访问受限的合法处理
├── client-projections/             # 按客户端格式生成，避免手工复制规则
└── mcp-connection-template         # 只含endpoint/profile，不含secret
```

主 Skill 采用渐进式披露：先读取用户正在处理哪个 Workstream/SavedView，只有任务真的需要时才展开对应 reference。地区、客户、预算、任务状态、产品主张和权限都从 MarketOps 服务读取，不写死在 Skill。

**“一个 Skill”是 UX 入口，不是永远只能有一个文件。** 如果以后回复处理、销售交接等形成完全不同的用户角色，可拆成第二个 Skill，但仍来自同一 canonical 包、同一 MCP、同一策略来源。不能按国家/产品复制成几十个互相漂移的 Skill。

### 2.2 各客户端只做薄适配

Codex/WorkBuddy/Claude Code 等客户端对 Skill/插件 manifest、MCP 配置、session id、主动消息支持不同。canonical 内容只维护一份，通过生成/安装脚本产生客户端 projection；适配层不得复制业务规则。

客户端适配只负责：连接远程 MCP、把当前 principal/会话标识传给插件层（若客户端提供）、展示结果、可选轮询通知。凭据存在客户端/受管secret引用，不入Git/Skill。客户端没有某能力时必须降级诚实，例如不支持主动推送就只读 Notification Inbox，不能伪称消息已经发到 session。

## 2.3 客户端优先级：Codex / WorkBuddy / MUC(Hosting) 是首批一等公民

这个优先级来自两类不同证据，不能混成一句“都已经在用”：

- **Codex**：Member-A session 审计里有 199 个登记线程（182 个有 item），大量使用 `send_message_to_thread`、`wait_threads`、自动化槽、MCP/Node/CUA 和跨线程调度；它证明 Member-A 已经把 Codex 当多线程经营控制面使用。优先解决它的远程MCP、多个session共享状态、断线重连、thread-context薄绑定和报告一致性。
- **WorkBuddy**：审计里有 40 个主会话，实际大量使用 Bash/PowerShell/Read/Write/Edit/Web/Agent/Skill；DE/NL/FR/GB/US 等曾被拆成独立会话且需要人工唤醒。它证明 WorkBuddy 是 Member-A 真实日常工作面，优先消除“文件夹/skill/CSV就是状态”的模式，改成同一MCP权威账本与持久Workstream。
- **MUC / Hosting（Hermes）**：Member-A 的 Codex/WorkBuddy 导出不是它的行为证据面，因此不能声称 Member-A 已经在 MUC 这样工作；它来自既有产品需求与 Member-C 讨论，目标是**正式生产的人机入口**：飞书/Hosting中的 MarketOps Steward 使用同一MCP、轻量记忆/Skill/知识和可选trigger。它不拥有第二套市场状态，也不要求每个Scene一个永久Agent。

首批支持等级定义：

| 客户端 | 首批角色 | 必须通过的P0合同 | 可后置能力 |
|---|---|---|---|
| Codex | Member-A多session控制台；也可做只读审计/Review | 远程MCP鉴权、tool discovery、并发session隔离、reconnect、同一snapshot报告、持久job不随chat关闭、插件/Skill投影 | 主动thread push、平台特定sidebar/automation UX |
| WorkBuddy | Member-A日常研究/跟进工作台 | MCP连接/鉴权、Skill加载、session重连、只读与写权限分离、同一Workstream续接、无本地CSV为权威、长任务不依赖主会话存活 | 平台特定Agent fanout、主动push/本地自动化桥 |
| MUC/Hosting | 正式持续Steward/飞书入口 | 远程MCP、principal映射、同一业务对象读写、Review/Watch/Action、Notification Inbox、重连/多session一致性 | Hosting cron作为可替换trigger、Feishu主动通知/命令、平台专属记忆体验 |

三者必须共享**同一业务API语义和同一服务端权限模型**。不得为WorkBuddy做一套“CSV版状态”，为Codex做一套“thread版状态”，再为MUC做一套“云端状态”。客户端差异只允许出现在安装、manifest、显示、可选session bridge和通知能力。

### 2.4 支持声明分级，避免“能连MCP=已适配”

每个客户端分别记录：

```text
installed_projection
mcp_connected
auth_verified
tool_catalog_verified
read_flow_verified
write_flow_verified
durable_job_disconnect_verified
reconnect_verified
multi_session_isolation_verified
notification_pull_verified
notification_push_verified?
plugin_install_verified?
client_version / protocol / tested_at / build
```

只有该客户端真实跑过对应合同，才写 supported。Member-A历史会话中出现过某个工具或Skill不能代替当前版本兼容验收；MUC/Hosting的目标设计也不能冒充live support。

## 3. 底层不是“大表”，而是共享市场事实图

共享事实包括：

- Asset/Venue 及地区、表面/用途等事实；
- Account 规范身份、官方域/路径声明、地址/服务区域；
- Asset↔Account 的 owner/operator/maintainer/procurement 等关系；
- Person/Department 与 RoleAssignment 的时间化任职事实；
- literal ContactPoint、SourceArtifact、Evidence、freshness；
- 授权范围内可复用的抓取工件与研究回执。

**不共享为全局事实的内容：**某个研究目标下的 eligible、ProductFit、角色优先级、route grade、草稿角度、沟通结果归因。这些属于 Workstream/Scene assessment。

同一组织被英国任务和法国任务同时看到时，只有符合授权和事实相同的部分复用；不同 workspace 不默认共享。多 session 不能靠“都看到了一个邮箱”形成越权跨租户合并。

## 4. Workstream：真正长期运行的调研任务

现有 Scene 继续承担服务端长期经营单元；用户层可称 Workstream。它不是产品线，也不是一次 batch。

一个 Workstream 至少绑定：

```text
workspace
objective
GeoScope
scenario / customer relationship
optional product/offer context
qualification + role/source policies
budget envelope
freshness expectations
owner
state/version
```

产品资料是可选输入：建公共市场池、核实Account身份时可不绑定具体产品；判断“最值得联系谁”、ProductFit或生成推广草稿时需要明确用途/产品上下文。

50户、100户只是 Cohort/Run。批次完成不新建 Workstream；新增地区若仍在批准范围内可用 Segment/Cohort，只有需要独立授权/预算/暂停/目标时才另建 Workstream。

## 4.1 Member-A 的日常不是“一键跑全国”，而是一轮轮 Run 扩池

默认使用路径：

```text
Member-A描述本轮目标
→ 插件读取已有Workstream/池子
→ preview本轮ResearchContext/计划/预算
→ Member-A启动manual Run
→ server独立把这轮跑到终态
→ RunDelta合入共享Market Graph
→ Member-A看新增/纠错/held/blocked/下一步
→ Member-A给业务反馈
→ MarketOps落Feedback/Policy版本
→ Member-A决定是否开下一Run
```

这意味着“人工为主”不是 Member-A 替机器抄 CSV 或逐页催 Agent，而是**人决定方向、范围、关键业务口径和下一轮是否启动；机器完成批准 Run 内部的机械/研究执行并持久沉淀。**

插件应优先支持一句话式用例：`继续法国足球场，再研究100个`、`把这批结果给我看`、`这个Account判错了`、`以后这个场景Facilities优先`。Skill 负责把它路由到现有 Workstream/Run/Feedback/Review 用例，不新建影子任务。

## 4.2 自动化只改变“谁触发下一 Run”，不复制第二条流水线

`manual / bounded_auto / watch` 共用同一个 ResearchContext → EffectiveResearchPlan → Job/WorkItem → RunDelta 管线。Schedule/Event 只负责在授权条件满足时创建下一 Run；不能绕过 Workstream scope、预算、pause、Review 或版本检查。

当前首个真实 Member-A 任务验收必须以 `manual` 模式完成；Watch/Schedule 可并行做技术验收，但真实自动经营在用户看过 manual 结果、确认规则稳定后再逐项开启。

## 5. “母池、监控池、跟进池”统一为三个不同概念

### 5.1 Pool / shared graph：事实集合

母池是被发现/导入的 Asset/Account 候选和已晋升事实，不是单一 CSV。支持 Asset-driven 与 Account-driven。共享池长期积累，不因当前任务排除某对象就删除它。

### 5.2 SavedView：可重复查询的工作池

“重点账号池”“Devon待研究”“有C/D路线但缺具名联系人”“本周到期重查”等定义为 SavedView：

```text
view_id
workspace
name
query/filter/version
columns/sort
owner/visibility
created_at
```

SavedView 只是查询，不产生外部调用、不产生预算。一个 Account 可同时出现在多个 View；移动/读某个 View 不复制 Account 行。

### 5.3 Watch：监控规则

Watch = `SavedView/对象集合 + 要监控的aspect + 触发/刷新政策 + 预算 + 通知策略`。

例如：

```text
监控 Devon 重点账号：
- official_domain / responsibility / role/email currentness
- 每天只做 due check
- 真正到期或收到 change signal 才外部重查
- 每周最多 N 次搜索 / N 次浏览器获取
- 变化/连续失败/新增A-B路线时通知
```

启用 Watch 是写操作，需要权限和预算；保存 View 不是。Watch 只驱动必要 due work，不为每个对象固定周期重抓。

### 5.4 ActionItem：跟进池不是标签，是下一步工作

跟进池由 ActionItem 构成：

```text
action_id
workspace
scope_ref (Account/Route/Review/未来Campaign等)
action_type
owner
reason/evidence refs
due_at?
state
blocked_by?
expected_version
created_at / resolved_at
```

例子：`核实转介联系人`、`补查responsibility`、`审核某条冲突`、`批准草稿`。ActionItem 可以由研究结果/Watch/人工裁决产生；完成后记录结果并更新投影。它不是一个全局 `account.status`。

## 5.5 Feedback Inbox：Member-A 的业务知识也是一等输入

插件把“结果纠错”和“规则建议”分开呈现。Member-A 说“这个人已经离职”时走事实修订；说“这个场景 Facilities 比 Owner 有用”时先展示影响 scope，再保存为 Workstream override 或 ExperienceCandidate。

客户端可以自然语言输入，但服务端必须保存类型化 `FeedbackEvent/HumanResolution/PolicyRevision`，并把 `source session / run / evidence / principal` 回链起来。Codex 确认过的规则，WorkBuddy 和 MUC 下次读取同一 Workstream 时立即看到同一生效版本；不需要把记忆同步到三份 Skill。

默认不把单次反馈全局化。系统应能回答：`这条反馈现在影响哪些对象？从哪个Run开始生效？谁批准的？如何撤回？`。

## 6. Account 不只有一个状态

Account identity 本身稳定，变化的是不同维度：

| 状态面 | 例子 | 作用域 |
|---|---|---|
| Identity/Fact | provisional / verified / conflicted / historical | 组织/事实 |
| ResearchAspect | not_started / researching / current / stale / blocked / held | Account × aspect × Workstream |
| Qualification/Priority | eligible / excluded / hold；normal/high priority | Workstream/Segment |
| ContactRoute | current / stale / contradicted / suppressed；A/B/C/D/X | Route × Workstream |
| Engagement（R3） | never_contacted / pending / replied / referred / handed_off / closed | Campaign/Account/Route |
| Action | open / claimed / waiting / done / dismissed | ActionItem |

收到回复以后主要改变 Engagement、相关 Route currentness/ActionItem 和后续触达停止条件，不改写组织身份。一个产品的回复不自动变成另一个产品的“已回复”。没有回复也不能直接把 Account 改为低价值；必须考虑送达、观察窗口与样本选择。

## 7. 多 Session：共享业务状态，不共享隐式“当前任务”

### 7.1 禁止一个全局 current_scene

Member-A 同时开多个 session 时，服务端不能保存 `member-a.current_scene=France` 这种全局可变状态。每次业务写调用必须明确目标对象，或携带由服务端签发/校验的 ContextRef。

可增加轻量 SessionBinding，仅为便利：

```text
binding_id
principal
client_kind
client_session_ref?    # 客户端有稳定id时才写
workspace
focus_refs[]           # Workstream/SavedView/Review queue
report_cursor
expires_at / last_seen
```

它不提供额外权限，不成为 Account/Job 的外键，不参与业务幂等。客户端没有稳定 session id 时插件自己持有一个 context token，并且所有写动作仍带显式 object/version。

### 7.2 同一任务可以被多个 session 看和操作

一个 Workstream/Job 可以同时被多个 session 读取。写操作使用 expected_version/revision/CAS；旧 session 不能覆盖新状态。相同昂贵研究按业务键 single-flight/复用，不因两个 session 同时请求而做两遍。

读进度是纯读取，不触发下一批或计费。Session A 关闭后 server-side job/schedule继续；Session E 重新连接后从权威状态恢复，不消费旧聊天摘要作为真相。

## 8. 灵活 schedule：MCP 配置，服务端执行

“MCP 定时任务”应理解为 **MarketOps 服务拥有的 durable Schedule + 一个受管 trigger driver**，而不是 MCP 协议本身在后台运行计时器。

建议 ScheduleDefinition：

```text
schedule_id
workspace
owner
scope_ref       # Watch / Workstream / SavedView digest / maintenance aspect
trigger_type    # one_shot | calendar | due_state | event
timezone
calendar_rule?  # RRULE/受限cron等可验证表示
window?         # 柔性执行窗口，不要求秒级
next_due_at?
action_spec
budget_policy_ref
notification_policy_ref
enabled
revision
created_at / updated_at
```

四类触发：

1. **event**：研究settled、Evidence变化、Review解决、未来R3收到回复；事件立刻评估下一步。
2. **due_state**：按每个事实/Route的`next_due_at`，只唤醒最早到期的一批。
3. **calendar**：每日/每周digest、固定业务窗口等；时区明确，补跑/错过规则明确。
4. **one_shot**：某日重新检查或一次提醒。

同一个业务动作只有一个 authoritative schedule owner。外部cron、Hosting、ServerOS以后都只能作为 trigger driver，带`fire_id/schedule_revision`调用同一 application use case；重复fire幂等。不允许客户端cron和MarketOps各自启动一套全量刷新。

调度器醒来先做低成本due check，再决定是否搜索/浏览器/Jev/Claude。空队列不调用LLM。paused/预算耗尽/等人分别记录。错过执行窗口可按policy skip/catch-up一次，不无限补跑。

用户可灵活调整 schedule，但改变范围、预算、通知目标等写操作有版本和权限；“每天看一下”不能偷偷变成每天全量重爬。

### 8.1 `action_spec.params` 收哪些键（`kind = research_run`）

`action_spec` 是自由形状，而 `ResearchContext` 全仓**没有存储**（`RunSpec.context_ref` 只是一份摘要）——“这一轮研究什么”只能由声明自己带。所以今天真的受理一轮研究的那个动作（`marketops/adapters/mcp_server/schedule_action.py::ResearchScheduleAction`）把键收成**闭集**，多一个键**拒**并点名是哪一个：

| 键 | 必填 | 语义 |
|---|---|---|
| `context` | 是 | 一份 `ResearchContext` 声明（此时还没过 pydantic，那是受理那一趟的事） |
| `cutoff_hours` | 是 | 这一轮**从受理那一刻算**跑多久（钟点）。刻意不是写死的时刻：写死时刻第二次点火必然过期，而那个失败与“这条规格本身不对”在报告上同形 |
| `execution_mode` | 是 | `bounded_auto` 或 `watch`；`manual` 是被**拒**、不是被降级——普通 Workstream 不自动化 |
| `budget` | 否 | 与 `submit_run` 同名参数同形同义 |
| `window` | 否 | 同上 |
| `name` | 否 | 同上 |
| `cohort` | 否 | 同上 |

另外三类 `ActionKind` 今天一律回 `UNRESOLVED`，**不回 `SKIPPED`**——恒回 `SKIPPED` 的动作会让每次点火看起来都是 `ok`，而它一次研究都没开过。

### 8.2 一条今天真实存在的边界：`next_due_at` 恒为 `None`

上面写的“会话关闭后 schedule 继续”今天**只成立到一次点火**：动作算完，`next_due_at` 四类 `trigger_type` 一律回 `None`，调用方据此把这条调度**关掉**（新版本 `revision_reason="fired:no_next_due"`，在 `list_schedule_definitions` 上读得出来）。全仓没有 `calendar_rule` 求值器。四类各缺什么：`one_shot` 的 `None` 是**正确**的；`calendar` 缺规则求值；`due_state` 缺“每个事实/路线各自 `next_due_at` 里最早的那个”；`event` 缺事件源。

**这不是“先填个空”。**它意味着 §14 的 M3 今天量到的是“第一次会真的受理一轮 Run”，不是“一直持续”。解除条件：给 `research_run` 写一个真正的规则求值器，并把 `_next_due_at` 换成它的输出。

## 9. 通知：事件只产生一次，多个消费端各自投影

业务状态变化生成 NotificationEvent / DomainEvent：

```text
event_id + dedupe_key
workspace
kind/severity
scope/object refs
summary payload
occurred_at
source_event_ref
```

事件本身不写“已通知Codex/飞书”。订阅和投递分开：

```text
Subscription
- principal/team
- scope (workspace/workstream/view/watch/review)
- filters / severities
- sink
- delivery_mode: immediate | digest | inbox_only
- quiet_hours/timezone
- revision

DeliveryReceipt
- event/subscription/sink
- attempt
- accepted/message_ref?
- delivered_or_unknown
- failure/retry_after
```

同一个事件可以在 Feishu 推一条，同时在 Codex session 的 Notification Inbox 中可读；一个 sink 失败不改变业务事实，也不重复执行研究。

### 默认通知策略

- `critical`: 权限/安全/数据完整性、持续失败使关键范围停止 → 立即通知。
- `needs_human`: ReviewCase/预算扩张/策略冲突 → 立即或短时digest。
- `material_change`: 新A/B路线、Account身份/责任关系变化 → 可按Watch配置即时/摘要。
- `routine`: 完成批次、无变化、正常维护 → 每日/每周digest，避免噪声。
- `progress heartbeat`: 默认不通知；用户读取实时progress即可。

## 10. 飞书：未来一个业务通道即可，但它只是 Sink / Client

飞书未来拆两种独立集成，不能混成一个 adapter：

1. **FeishuMessageSink / Bot channel**：把 NotificationEvent/摘要发到一个获授权的业务群；未来可接受少量类型化命令。
2. **Feishu Bitable projection**：把选定handoff/报告投影到表；现有`adapters/feishu/client.py`属于这一类，真实写入仍BLOCKED。

一个群可以订阅多个 Workstream，但消息必须带`workspace/Workstream/view/event`短标识、摘要、下一步和服务端对象引用；不能仅靠群消息正文回写状态。

未来入站命令（例如`暂停法国研究`、`给我英国日报`）必须：飞书应用验证身份/签名→映射MarketOps principal→解析成类型化intent→服务端再次做对象权限/expected_version校验→回执。群里一句“批准发信”不能天然成为外发授权，除非专门设计并验收该授权流程。

飞书消息发送需要独立业务授权、app凭据、群范围、速率与真实厂商验收。未接飞书时 MarketOps完全可用；通知继续留在Notification Inbox/报告。

## 11. Codex / WorkBuddy 多 session 都能“报告”，但主动推送要诚实分层

所有客户端均可通过同一 MCP 读取：`get_job/get_report/Scene/View/Watch/Action/NotificationInbox`等服务端状态。报告内容由服务端生成/聚合，Skill只决定怎样呈现；因此Codex和WorkBuddy看到的是同一`as_of/version/cursor`，不会各算一个数字。

**主动向一个已打开的任意 session 发消息不是 MCP 的通用保证。** 如果某客户端有经过验证的thread/message API，可以写 ClientNotificationBridge，把已持久NotificationEvent投影过去；没有就采用：

- session打开/恢复时拉取“自上次cursor以来变化”；
- 用户说“进度/日报”时查询；
- 客户端自己有cron/automation时只轮询Notification Inbox，不启动研究；
- 真正可靠的跨session主动通知优先用Feishu等有授权sink。

多个session的read cursor可独立；业务事件和ActionItem只有一份。为了避免Member-A开5个session收到5次同样通知，默认客户端session不各自注册外部push subscription；Feishu/团队subscription按principal/team去重。用户明确要某thread观察某Workstream时再创建短期SessionWatch。

## 12. 插件安装与服务器部署是两件事

插件安装只完成：Skill/客户端投影、远程MCP profile、最小连接诊断。它不能启动另一套数据库/Quay/scheduler，不内嵌共享管理员token，不把产品/客户资料拷进本地Skill。

MarketOps server 独立部署/运行，有唯一数据库schema/迁移、后台控制器、schedule owner、Notification Outbox、Quay连接与provider凭据。升级客户端插件不能改变服务端policy；服务端API/catalog不兼容时客户端明确提示版本问题。

理想部署：Member-A所有设备/session连同一个受管endpoint。开发/测试另用隔离endpoint和workspace，不让测试session写生产市场池。

## 13. 最重要的并发不变量

- 相同 Account/URL/事实刷新在兼容scope下single-flight；不同session不重复昂贵动作。
- 所有业务写有workspace/object/revision/幂等键；session context不能扩大权限。
- 一个session pause Workstream A，不影响B；全局安全撤销可即时阻止全部适用动作。
- SavedView查询无副作用；Watch/Schedule启用才会产生未来工作。
- 一个NotificationEvent只生成一次；投递到N个sink是N个receipt，不是N次业务事件。
- 一个sink投递失败只重试通知，不重跑研究。
- Feishu/Codex/WorkBuddy展示的统计来自同一服务端query/receipt，不能各自从缓存减法反推。
- 未来R3回复/退订/退信先落事件/全局抑制，再更新相应Campaign/Action；不能只改某session内存。

## 14. 端到端验收场景

### M1 三个session并行，不串上下文

- Codex A启动UK Workstream，Codex B启动France Workstream，WorkBuddy C只读重点View。
- A/B各自写入正确对象；C的只读不产生provider调用。
- A切换焦点不改变B；任一旧session用过期revision写入会被拒。

### M2 共享组织和工件只研究一次

- A/B在授权范围内遇到同一Account/同一公开URL。
- 共享identity/evidence可复用，Scene assessment分别计算；兼容获取single-flight，预算/使用关联正确。
- 不兼容语言/render/version或workspace时不能错误共用。

### M3 关闭所有聊天仍持续

- 启动持久研究/Watch后关闭客户端。
- schedule/due/event继续推进；空队列0 LLM心跳。
- 重连新session读回相同job、ActionItem、budget和event cursor。

### M4 Watch/Follow-up

- SavedView保存不发网络；enable Watch才建立future due work。
- Account事实变化→一次Event→ActionItem/Review→对应View自动变化；Account不被复制。
- resolved Action再次重放幂等，旧Action不能覆盖新事实。

### M5 Feishu单群通知

在Sandbox/获授权配置下：多个Workstream的material events按subscription汇总；同event只发一次；daily digest有明确cutoff/cursor；API失败留receipt并有限重试。飞书不可用不影响研究。真实写入验收通过前状态仍BLOCKED。

### M6 Codex/WorkBuddy报告一致

两个客户端对同一report snapshot读取的业务键集合、单位、`as_of`一致；展示语言可以不同。一个session标记本地cursor已读不删除另一个session的可查询历史，也不改变团队Feishu投递状态。

## 15. 当前阶段的产品收敛

对 Member-A 的外部心智模型尽量只有：

1. **MarketOps 插件**：一个入口；
2. **工作任务/场景**：我现在经营什么；
3. **工作池/监控**：我想盯哪些对象和变化；
4. **跟进/待裁决**：下一步谁做什么；
5. **报告/通知**：发生了什么；
6. **MarketOps MCP**：背后的唯一业务服务。

产品线只保留为可选分类/报告维度，产品资料按版本共享；不要求Member-A先维护产品线生命周期才能研究。多个Skill、多个session、飞书和其他客户端都是**同一服务的使用方式**，不是新的事实库、任务系统或业务后端。