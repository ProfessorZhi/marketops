# MarketOps：减少 Member-A 人工介入的执行计划

更新：2026-09-27。状态：**实施规格；除本次设计文档外，下面工作不因计划提交而变成已完成。**

依据：[增量架构](MEMBER_A_AUTOMATION_ARCHITECTURE.md)、[证据与研究](research/MEMBER_A_AUTOMATION_EVIDENCE_2026-09-26.md)。产品固定基线 `df689f52e0e07dc35b88af7a3a5dd48808008a75`；分析固定基线 `06cdae471f794e37877637d69ea93d1c020272a2`。开工重新核对分支和生产调用图；已有修复复验即可，不重复实现。

2026-09-27补充的权威规格：[共享市场/Scene/学习模型](PRODUCT_LINE_AND_SCENE_MODEL.md)、[多Session插件/监控/通知](MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md)、[采集与供应商接入](ACQUISITION_AND_PROVIDER_DESIGN.md)、[本轮代码/技术核对](research/MARKET_MODEL_ACCESS_REVIEW_2026-09-27.md)。这些补充合入同一 B00–B30 计划；不是另起一套产品，也不是要求先实现完整PIM、统计学习平台或R3。

## -1. 2026-09-27 当前真实进度：PR #8 是底座，不是最终架构完成

当前实现候选为 `marketops/member-a-automation-r1-r2-20260926 @ 426a09f6632ac5dd63c0da39669036d4c8163755`（PR #8，未合并）。其代码验收固定在 `b2d0992011f4c63fdfe47b155a101a48e795e370`；报告明确 `live-acceptance=N/A`。它按旧设计 `61419b7` 完成并验收 B10/B11/B12/B13/B14、B20/B21/B22 与当时定义的 G1/G2。**这些结果保留，不重做、不改写历史 YES。**

但目标架构已经继续演进到本分支最新设计。下表是**PR #8 相对于本文件最新目标的差额**；这里的 DONE 只表示已有代码底座可复用，最终仍要在收敛分支做回归/组合验收。

| 最新工作项 | 以 PR #8 为基线 | 现在的判定 | 下一步 |
|---|---|---|---|
| B10 identity/scope/domain | 已实现并有纵向fixture | DONE-baseline | 不重写；在动态Strategy/多Workstream下回归 |
| B11 canonical route + rejection | 已实现 | DONE-baseline | 接 SavedView/Watch/Action 投影后回归 |
| B12 product-generated Cohort | 已实现 | DONE-baseline | 扩为 Workstream selector，不退回YAML |
| B13 independent draft | 已实现 | DONE-baseline | 接 Product/Offer 可选上下文及未来事件失效链 |
| B14 Jev lineage | `decision_id` 因果引用已实现 | PARTIAL | 保留实现；补按spec/model的质量/成本增益评估与最终对象消费报告 |
| B20 Review Inbox | 已实现七条工具/生产者 | DONE-baseline | 作为 B23/B24/B25 的人工裁决入口继续复用 |
| B21 continuation | 已实现断线续跑/受管唤醒入口 | PARTIAL | 保留；升级成唯一 durable Schedule/event driver，不再只有“能醒一次” |
| B22 external refresh/reuse | 两个页面aspect已接真实取回执行器（fixture） | DONE-baseline | 与 B15 Access/Attempt/熔断及 Watch due work 合并验收 |
| B01 Product/Offer可选 + Workstream启动 | PR #8 没有 ProductVersion/OfferVersion/Launch 契约 | NOT BUILT | 增量实现，不强迫纯市场研究绑定产品 |
| B02 EffectiveScene/跨任务共享 | 有旧 SceneVersion，但无最新 Geo/Scenario/optional Product 编译层 | PARTIAL | 在旧 Scene 上增量迁移，不另起平台 |
| B15 acquisition/access control | 已有WAF识别、终态禁止绕过、HTTP目标防护；仍缺 FetchAttempt/429共享限流/来源熔断等，且 browser 路径仍有 `status_code or 200` 与事后落点检查 | PARTIAL | 先关闭承重安全/工件/计费缺口，再做性能优化 |
| B16 scene-aware research strategy | 无 ResearchContext / StrategyCompiler / EffectiveResearchPlan | NOT BUILT | 新增动态场景识别与版本化策略编译 |
| B17 incremental Run / RunDelta / execution mode | PR #8 有Job/continuation，但没有最新 RunDelta 与 `manual|bounded_auto|watch` 授权语义 | NOT BUILT | 新增默认手动的增量Run合同；保留已受理Run内部自动续跑 |
| B23 controlled learning | 无 ExperienceCandidate/PolicyPatch/Outcome事件层 | NOT BUILT | 先接研究反馈；R3回复数据仍延期 |
| B24 SavedView/Watch/ActionItem | 无对应领域对象/存储/工具 | NOT BUILT | 新增，复用B20/B21/B22 |
| B25 Plugin/multi-session/Schedule/Notification | 无对应领域对象；无Codex/WorkBuddy/MUC正式兼容验收 | NOT BUILT | 新增，不把客户端本地状态变成真源 |
| B30/G3 Member-A效果比较 | 只有fixture链上的0/0桥接/催续跑；业务质量/成本 unavailable | PARTIAL | 等受控pilot/匹配基线，不报虚构收益 |

因此从现在起同时保留两种结论：

- `R1_VERIFIED/R2_VERIFIED`：只引用 PR #8 对**旧设计范围**的技术验收，永不被本计划改写。
- `TARGET_ARCHITECTURE_CONFORMANT`：只有本文件最新工作项 + 最新架构文档的最终门全部通过才可为 YES；当前必须为 **NO**。

### 收敛分支/合并策略

下一次实现**不要从 `df689f52` 或 `61419b7` 重新开工**。以 PR #8 HEAD `426a09f` 为代码底座创建新的 convergence worktree/branch，再把 PR #7 从 `8cf9c26` 以后形成的最新设计文档变化有意识地合入；发生计划/验收文档冲突时保留 PR #8 的历史验收文件，使用本文件作为未来唯一施工计划。禁止 reset 掉 PR #8 代码，也禁止把旧 `R1_R2_AUTOMATION_ACCEPTANCE` 改成新架构验收报告。

收敛后的第一条提交只做：设计同步、状态矩阵、schema/catalog当前基线与全量回归；**零功能重写**。通过后再做下面剩余切片。

## 0. 先冻结目标和停止条件

本轮目标是把 PR #8 的研究底座收敛成最新目标架构，同时继续保持 R3 真实邮件/回复、Laya、ServerOS、Docker、Web UI、CRM 不进入当前 gate。Feishu 在 B25 只做到通知/适配合同与获授权 Sandbox 验收，不因此开启真实外联。

**当前默认运行哲学：manual-first。** Member-A 通过同一个 MCP/插件明确启动一轮有限 Run；系统在这份 Run 内可以断线续跑、自动完成已批准工作并生成 RunDelta，但完成后默认停下等待 Member-A 看结果、给反馈、决定下一轮。只有显式 `bounded_auto` 或 `watch` 授权才允许服务创建未来 Run。自动化能力要实现，但不是第一次真实任务成功的前提。

**完成目标：**已有池子先复用；每一轮 Run 对有限对象/问题补充或纠正 Asset/Account/Person/Role/Contact/Evidence，并以可对账 RunDelta 合回共享 Market Graph。Member-A 的事实纠错、当前任务规则和可泛化经验进入服务端反馈/策略版本；无人工 YAML、SQL、开发者桥接脚本或靠聊天“记住”业务真相。

不是承诺所有候选都合格，也不是承诺每个真实 cohort 都产 A/B 或草稿。合规零结果必须是可解释终态。可用性另用已知正/负/歧义样本验证，不靠放宽规则凑数。

**禁止再次把“验收完成，结论 NOT_READY”当作本轮实施完成。** 发现范围内产品断点，形成最小复现后回到对应工作项修复，再测；只有真实权限/预算/外部服务缺口，或需要改变已批准业务规则，才把受影响部分 BLOCKED。PR 需要人工合并不是停止修复的理由。

## 1. 分三批小交付，不再逐表横向铺开

```text
C00 收敛 PR #8 代码底座 + 最新设计文档，不重做已完成切片
  -> B15 先关闭采集/访问/工件一致性承重缺口
  -> B16 ResearchContext / Scene fingerprint / StrategyCompiler
  -> B17 incremental Run / RunDelta / manual-first execution mode
  -> B01 Product/Offer 可选上下文 + Workstream 启动
  -> B02 EffectiveScene/策略模块编译 + 跨任务共享
  -> [回归 B10/B11/B12/B13/B14/B20/B21/B22 已有能力]
  -> G0/G1' 最新上下文下的研究纵向回归
  -> B23 ExperienceCandidate / PolicyPatch / controlled learning
  -> B24 SavedView / Watch / ActionItem
  -> B25 canonical plugin + multi-session + Schedule + Notification + Feishu sink
  -> G2' 多客户端持续经营验收
  -> G4 Target Architecture Conformance
  -> READY_FOR_MEMBER_A_TRIAL
  -> G5 Member-A本人真实manual任务试用
  -> B30/G3 受控 pilot 与“是否优于 Member-A”效果比较
```

B14（Jev 因果引用/范围核验）与B15（采集/访问/供应商）伴随 B10/B11，不需要等整链完工。B11/B12 可在稳定输入契约后并行；以少量明确共享接口协作，不让多个 agent 同时改同一核心文件。

每批必须交付一条能从 MCP 实际走通的用户故事，而不是“domain 写完、store 写完、工具写完”三个孤立成绩。数据库迁移、读取面、执行路径、错误回执与负例随同一业务切片交付。

## 2. 工作项及关闭条件

### B00 · 固定真实基线与需求权威

**消除的人工动作：**反复核对“到底哪份代码/口径是真的”。

- 用 GitHub 读取当前 ref 与 CI，用本机 git 确认正确 worktree；禁止误用 `marketops-research-pipeline` 退役 checkout。
- 固定分析 SHA，无需等待其 PR 合并才能引用；不自动合并分析 PR #1 或产品 PR #6。
- 对 session 干预以 INT/TR 标识回链；截短/填零的 session_id 不是原始 UUID，需原文回查时用真实 locator。
- 建执行登记：work item、消除哪项人工动作、代码路径、验收命令、结果引用、build、状态与阻塞。状态分“代码/契约测试/集成/业务抽查/部署”，禁止单列 CLOSED 覆盖全部。

**关闭：**形成当前代码与旧断点对照，只追未解决者。分析比例、测试数、历史提速都不是产品效果基线。

### B01 · 产品资料、Offer（可选）与 Workstream 启动：登记不等于开工

**消除的人工动作：**每个Scene重贴产品资料、改型号后逐处改prompt、弄不清“启动研究”到底执行了什么。ProductLine 不是本项前置。**当前落点：**`domain/scene.py::SceneVersion`的product、`domain/outreach.py::{TemplateFamily,ApprovedClaim}`、相应store/application/MCP入口；只增加必要产品目录/版本引用，不新建PIM框架。

最小实现：ProductVersion来源索引/能力/限制/区域变体、可选OfferVersion明确产品组合/允许claims，以及资料访问控制；ProductLine 仅在有真实管理需求时作为可选分组。原始资料进受管artifact store；Git仅获准索引/schema/规则，不上传私有手册/联系人。新型号无核实资料只可draft；旧字符串兼容显示并明确alias迁移，歧义不能猜。

启动用例先preview：固定研究目标/GeoScope/Scenario、可选Product/Offer、policy、预算/周期与下一步动作；有批准才持久受理Scene/Workstream及Job。多任务不自动形成笛卡尔积；失败原子记录或分项回执，不出现“一半开跑、一半无记录”。跨run预算继承，重复提交不重建、不重置费用。

**关闭：**一个获批产品版本可被两个Scene共用且草稿回链相同资料版本；不带Product的纯市场身份/责任研究也可启动并明确限制；claim撤回让受影响未发草稿hold、无关任务继续；ProductLine/产品资料登记不产生网络调用；启动授权前零外部调用；批准后从MCP返回持久Workstream/job和实际队列状态；重启重复启动不再付费。资料缺失返回具体missing字段，不用临时SQL救流程。

### B02 · Scene/Workstream 编译、边界与跨任务复用

**对应：**INT-010/033/052/073。**落点：**现有SceneVersion、scene_impact、ProfileBundle、role/source/freshness/writing配置及Cohort用例，必要字段增量迁移。

编译GeoScope、ScenarioProfile、Offer、SalesMotion和可覆盖policy为不可变EffectiveScene版本；包含source/provenance/units/locale/owner/hash，规则优先级和冲突可解释。ProductLine不是运行边界，Segment不等于新授权。Scene ID稳定，不把每个参数变更/聊天/批次改成新Scene。

支持Asset-driven与Account-driven入口；不要求维护商、泳池搭建商或渠道任务捏造Venue。岗位原文/任职关系、function、购买影响力推断、route grade与priority分开。跨Scene共享事实和可访问工件，准入/评分/草稿独立；共享费用不双计，业务Key不串。

**关闭：**同组织在两个不同Offer Scene里事实一份、评价各一份；地区子集Cohort不能越出Scene边界；组合Offer可在同Scene内冻结，但未获批组合不自动出现；终端与渠道角色排除不同且正确；改产品版本优先重评不全量重抓；暂停一个Scene/Workstream不会影响无关Scene，源端共享限额仍有效。

若维护ProductLine分组或扩大国家范围，必须有来源/批准；仅因样本少或角色回复略有差异不自动拆Scene。默认值不可来自历史会话随口建议，当前approved profile/SOP有效。

### B10 · 组织/场地/范围/官网：先找对，再找人

**对应：**INT-010/033/052/073，TR-X04，最新验收 F.2/F.6。**现有落点：**`application/{discovery_run,pool,promotion,inputs,qualify}.py`、`domain/profile.py`、现有 pool store。

**实现：**在现有 promotion 上增加证据支持的身份/范围评估，不将抓取 host 或页面标题当核实事实；区分 provisional 与 verified。官网声明以 Account 为归属，可多域/共享站路径；记录域声明与 evidence。不能仅哈希场地句柄就宣布组织解析完成。

地区、资产环境、表面等有真/假/未知；排除规则未判则不写合格。明确的 out-of-region 在昂贵联系人研究之前排除；其他不确定项走小预算身份补证任务。一个组织具有多个不同表面/室内外资产时按目标资产判断，不全组织误杀。

**关闭测试：**同组织两个场地不重复深研；共享域两个组织不串；同名美国人物不能进入英国地区合格队列；第三方页面能发现但不能自行证成官网；跨域真实归属有证据可接受，无证据 hold；伪邮箱/目录他人仍拒绝。

**迁移：**保留旧 provisional ID 的可解释映射和历史引用。不可靠映射隔离，不静默改历史 evidence。账号存在性直接核对业务对象/关系，不以“名下有证据”替代。

### B11 · 路线观察 → 唯一路线池 + 拒收分布

**对应：**INT-026/054，TR-D07/D08。**落点：**`application/contact_routes.py`、`domain/bind.py`、`ports/store.py`、SQLite/JSONL 实现和 dossier/report。

**实现：**不可变观察保留；当前投影遵照 SOP 的工作区/账号/场景/规范化邮箱键。不要为 upsert 覆盖旧结论，不取“最高 grade”。区分页面无证据、实际反证、历史失效与冲突；相同 endpoint 的自然人身份是额外关联。

`RouteProduction.rejected` 要有逐项审计引用及聚合：reason、rule/policy、对象、证据、run。既报观察数量，也报唯一可用路线；失败/跳过/hold 不能塌成一个整数。

**关闭：**同一邮箱跨 3 页/两轮/重试只有一条当前路线及完整观察；并发写不重复；D+证据不足不能任意升级或降级，确切反证进入正确冲突状态；不同 Scene/工作区不串；旧表迁移计数与业务键双向对账。

### B12 · 账号池 → 系统生成 Cohort

**对应：**TR-D06、INT-011/024。**落点：**`application/inputs.py::account_subjects`、`application/cohorts.py`（当前仅 demo loader）、现有 pool/scenes store、MCP product-loop consumers。

**实现：**维护/草稿/研究接受 Scene selector 或 durable cohort 引用，从池子/assessment 形成冻结有序成员。保留显式导入兼容，但删除正常使用依赖 `inputs/<name>.yaml` 的前提。不要在后台生成一份同样依赖操作者路径的影子 YAML 充当修复。

**关闭：**干净数据根、没有 inputs YAML，仅通过 MCP 发现/晋升后可列账号、选 cohort、维护并起草；分页/新增账号/重启不丢记录；暂停 Scene 不接新工作；每批可回读成员/选择依据/排除原因。

### B13 · 独立草稿用例与业务回执

**对应：**TR-X03、INT-027/042；业务依据冻结 SOP 模块 E。**落点：**`application/outreach.py`、MCP product-loop/outreach adapters、template/writing-policy stores。

**实现：**草稿准备不依赖 `plan_wave` 的发送前序与授权。A/B/C/D 按批准策略排序并选择对应模板；C/D 不冒充关键联系人，X 不产正常草稿。不要仅把 `KEY_CONTACT_GRADES` 全局放宽而意外改变真实发送执行器。

既有模板/说法需真正批准；修订生新版本。每份草稿能回链至同一 Account、路线、Scene、策略、writer 和事实；身份/范围/当前性不足则持有明确 review-only/held 状态，不把出现邮箱等同客户适合。

**关闭：**已知合格 A/B/C/D fixture 各有合适草稿，X/未批准主张被拦；真实 cohort 中满足条件者可生成，无条件者说明原因；不安装 SMTP 仍可生成；默认无发送工具权限；结果由 MCP 直接回读，无后台人工桥。

**回执：**以冻结对象集对账 accepted/held/excluded/failed/reused 及下一层写入。允许合法零输出，但已知正例必须走到草稿。实体内容与证据关联验证不能仅检查非空/hash。

### B14 · Jev 真实因果引用、校准与增益评估

**对应：**TR-D03，验收 C.6。**落点：**`ports/decisions.py`、`adapters/sqlite/decisions.py`、decision tools、责任/角色结果生产者。

报告说 `decision_record.work_item_id` 为 NULL，但当前 DDL 声明 NOT NULL，store 接受 work item。先核对 live schema、列、payload 和 API 序列化，不能照旧报告假定数据库需补同一个字段。

当前 `application/relations.py` 已按 `RelationFinding.decision_id` 校验记录/任务/主体/spec/答案，必须复用与回归。结果使用某一决策时，在最终投影显式保留 `decision_record_id` / work_item / evidence / spec/version 关联；证据集合与答案恰相同不是完整因果证明。`answered` 不等于正确，高/低置信都不能直接代替业务证据。Jev 是 assist；模糊建议可 hold，不能为“57 条全 answered”标成全确认。

**关闭：**真实必要判定可从结果反查具体调用；不存在的/跨任务决策引用被拒；replay 不重复计费；unavailable/abstain 被正确消费。不强迫每任务调用 Jev，不引入 Laya。

另做固定证据/标签/预算的基线与Jev-assisted对照，分清“调用/被消费/有质量或成本增益”；不能以57条answered或通用置信阈值判定已更准。校准按spec/model/primitive分开，变模型需重验。独立标签不足时增益标not_demonstrated，保留assist及Review，不降低规则凑成功。

### B15 · 采集与访问故障：正确诊断、有限成本、安全替代

**对应：**TR-X15，Member-A访问受限/无效重试类问题。**代码落点：**`application/{acquisition,escalation,site_crawl,document_router}.py`、`adapters/{http,crawl4ai_fetcher,serper,page_mirror}.py`、`security/egress`、budget和报告。完整行为契约见 [采集设计](ACQUISITION_AND_PROVIDER_DESIGN.md)，不要新建第二套爬取平台。

保留HTTP足够就不browser的现有门；补FetchAttempt观测/根因区分、来源级熔断、共享origin/API-key速率与Retry-After、合法fallback、可行动AccessReview。cf-ray普通页面不能判IP封禁，403 unknown不能判无联系人；challenge不得提取业务证据。时间/字节/子请求/重试/费用有边界，SDK隐藏重试统一记账。

验证浏览器发请求**之前**的主导航/重定向/子资源/DNS目标约束，固定版本不具备则补可验证受管层或禁用有风险模式；事后final_url检查不是预防。禁止通过轮换IP、反检测、CAPTCHA求解或安全软件绕行满足验收。修自有授权配置与站点允许的API/导出/白名单路径可以进入运维待办。

工件键修正到内容/抓取版本，URL索引与不可变blob分离；旧文件存在时新hash必须对应实际读回字节。提取保留literal文本/记录块与变换版本；缺status不默认200；gzip/编码/解析故障不能变成零邮箱。

**关闭：**采集设计§11每个承重案例有正负证据；true HTTP/JS公开样本通过，挑战fixture正确停止/改合法来源；20个账号撞同源只有有界探针而非20套重试；两个Scene共享同源配额不翻倍；旧URL内容改变无hash错配；恶意导航在受控网络前拦；缺一个供应商时按受影响阶段显式降级/hold，不宣称全功能live。

浏览器进程池/批量获取是有条件优化：保留新匿名context和工作区隔离；先量启动/等待/抓取/解析耗时，再比较相同样本下吞吐、峰值内存与失败。`arun_many`有接口不等于已接入本产品；实际调用/下游消费者/close恢复要验证。

### B16 · Scene-aware Context Resolver / Strategy Compiler：动态识别，受控编译

**业务依据：**Member-C 的原始反馈支持“任务越具体越容易做好”“场景名录越具体越利于联系人调研”“不同场景高价值联系人不同”；她对“Scene是不是越细越好”本身给的是需要测试，而不是已验证规律。因此实现目标是**细粒度 ResearchContext + 少量长期 Workstream**，不是自动制造成百上千个 Scene。

**输入：**Member-A/客户端明确给出的 objective、GeoScope、对象/场景描述、可选产品用途、输出需求、预算与硬边界；共享池已有事实可作为上下文，但历史模型猜测不能自动扩大范围。

新增逻辑对象（不要求一对象一表）：

```text
ResearchContext
- geo / jurisdiction / candidate locales
- entity / asset / organization classes
- scenario candidates + provenance/confidence
- sales/research objective
- optional Product/Offer/use-case
- evidence requirements
- ambiguities / hard constraints

SceneFingerprint (derived, not identity)
- normalized geo + scenario + entity + relationship + objective + product-use dimensions

EffectiveResearchPlan
- source methods and ordering
- locale/query families
- page archetypes / link priorities
- role/function vocabulary and priority hints
- acquisition modes/fallbacks
- DecisionSpec refs
- budget/stop/sufficiency
- exact policy/module versions + digest
```

**Strategy modules：**`Geo/LocaleSourceProfile + ScenarioProfile + ObjectivePolicy + AccessPolicy + Product overlay(可选) + Workstream override`。编译优先级、来源、冲突与版本必须由普通代码确定并可解释；Agent/Jev 可做闭集候选分类或提出候选，不可自行改硬排除、预算、产品 claim、访问权限或发送权限。

**动态识别规则：**

- 明确输入能匹配现有profile：自动选择并展示依据。
- 多个候选且差异只影响低风险搜索顺序：可在批准envelope内编译保守计划并记录候选。
- 歧义会改变目标人口、hard exclusion、商业关系或产品/权限：生成 ReviewCase/要求用户选择，不猜。
- 页面级语言检测只影响查询/解析/翻译策略；原始title/evidence保留原文。Country不能一步推出唯一language。
- 运行中可根据 source yield/access/duplicate/cost 在批准范围内调整排序；改变客户定义/新增国家/新role语义只能生成 `ExperienceCandidate/PolicyPatch`，不能静默发布。
- 系统可提出 `SceneSplitCandidate`（例如同一Workstream中K12与大学来源/责任模型持续不同），但只有治理/预算/目标值得独立时才批准成新Workstream；否则保持Segment。

**关闭：**

1. GB/FR/DE + 至少一个多语地区fixture，同一自然语言任务稳定编译可复现plan digest；preview零provider调用。
2. 同一Workstream的学校/高尔夫/维护商等不同context得到不同source/page/role策略，但共享事实/工件仍复用。
3. Country与language分离：多语国家不会被强塞单一locale；翻译不覆盖原文title/evidence。
4. 模糊输入影响hard scope时明确hold/review；不能由模型置信度越过。
5. Source yield变化只能改批准范围内排序；低样本不会自动拆Scene或改变客户资格。
6. `EffectiveResearchPlan`随WorkItem冻结；中途profile更新不改正在跑的attempt，新run才用新版并给impact。
7. Serper/HTTP/Crawl4AI/Jev/Quay调用能反查是谁因哪一个plan步骤被选择；没需要的provider不为“集成完整”而强制调用。

### B17 · Incremental Run / RunDelta / ExecutionMode：默认手动，一轮一轮长大

**目标：**把“研究一次”从临时 Job 提升为 Member-A 可理解、可复用、可对账的增量事务。Workstream 可以长期存在且池子永远不要求一次爬完；每个 Run 只回答一个有限问题/对象集并把 Delta 合回共享市场图。

最小 RunSpec：`workspace/workstream, objective, selector/cohort, ResearchContext ref, EffectiveResearchPlan ref, execution_mode, budget envelope, cutoff, policy versions, idempotency key`。Run 内部 Job/WorkItem/Quay attempt 继续复用 PR #8 已有实现，不另造执行器。

**ExecutionMode：**
- `manual`：默认。Member-A/获授权用户创建本 Run；Run 内断线续跑直到终态，**Run 完成后不得自动创建下一 Run**。
- `bounded_auto`：只有显式授权窗口存在时，按累计对象数/预算/截止时间/stop conditions 创建后续 Run。
- `watch`：由 Watch 的 due/event/schedule 产生维护 Run，只能检查批准对象/aspect。
- mode 不是权限捷径；pause、预算耗尽、Review hold、scope/version变化都可以阻止下一 Run。

每个 Run 结束持久化 `RunDelta`：discovered、promoted、updated、superseded/withdrawn、excluded、held、blocked、unchanged、routes、evidence/artifacts、Action/Review、usage/unknown usage、next_recommendations。Delta 合并必须幂等；“池子变大”包括纠错和 currentness 改变，不只计算新增行。

Run preview 必须先报告：已有事实复用多少、预计补哪些缺口、计划可能调用哪些 provider、预算/stop、哪些歧义会进入 Review。Preview 本身零 provider 调用。

**关闭：**
1. 同一 Workstream 连续两个 manual Run：第二轮明确复用第一轮已沉淀事实，只补新/缺/过期；重复提交同一 idempotency key 不重复池子或成本。
2. Run 结束有完整 Delta，业务键双向对账；合法零新增仍能以 unchanged/held/blocked 结束。
3. 关闭所有客户端后**当前 Run**继续并完成；完成后在 manual 模式下保持 idle，绝不自行扩下一批。
4. bounded_auto 在达到对象数/累计预算/截止时间任一 stop 后不创建下一 Run；重放 trigger 不重复创建。
5. watch 只为到期/信号对象创建维护 Run；SavedView 单纯查询不创建 Run。
6. 无可证明市场总量时报告可观测池子状态，不产生“国家完成率/已爬完”。
7. RunDelta 合并对并发/重放/CAS有负例；旧 session 不能用旧 snapshot 覆盖新事实。

### B20 · 有效策略版本 + 最小 Review Inbox

**对应：**INT-008/038/063/067/071，TR-D10。**落点：**现有 `SceneVersion/scene_impact`、profile 与 writing rules，新增必要人工审核用例/持久记录。

先连接现有策略来源、版本、批准与运行快照，不造新通用规则语言。需要人工的案件使用 `ReviewCase/HumanResolution`，不要覆盖现有 Jev `DecisionCase`。

提供少量类型化 MCP 用例：列待办、读证据/影响、认领、按 expected_version 提交裁决。单案纠错默认不发布全局 policy；批量裁决固定对象与共同前提，过期版本需重新检查。

**关闭：**规则提议不自动生效；批准一次后符合该规则的后续对象不重复问；新旧版本影响预览可复现、历史不变；ReviewCase 去重/重启/并发审批不丢；一个案件 hold 不堵无关 Account；权限/过期/跨域批量均有拒绝测试。

### B21 · 持久续跑，不靠 Member-A 发“继续”

**对应：**INT-019/023/032/041/046。**落点：**现有 dispatch/reconcile/job/maintenance，增加最小 continuation/outbox 与受管入口。

每次**已受理 Run 内**的业务事件可驱动它自己的下一工作单元，保存事件、幂等键、执行 epoch 和 checkpoint。不要把“Run 内续跑”实现成“Run 完成后无限扩市场”。未来新 Run 只有 B17 的 `bounded_auto/watch` 授权才可创建；`manual` 完成后停在 idle/next_recommendations。未来到期需要唯一的实际唤醒 owner；选当前可用的受管 host timer 或服务循环，不等待 ServerOS/Docker。

分开：正在研究、无可运行任务、缺凭据/预算、等人、暂停、完成、等待到期。没有工作不调用模型；异常恢复有次数/成本界限，不能用“持续运行”绕无限预算。

**关闭：**正常 MCP 提交 manual Run 后断开客户端，Run 内后续 work item 确实自动执行并回池；Run terminal 后没有授权则不再创建下一 Run。控制器崩溃后恢复不重复昂贵工作；暂停与恢复正确；无任务无模型调用；仅相关对象等人。Quay 原始软件 landing 观察保持可见，但不能让它重复已受理研究；在真实 driver 轨迹核验，不只改 report 状态。

### B22 · 先复用，再对过期事实真实重查

**对应：**TR-D11、INT-019/033/041。**落点：**`application/acquisition.py`、page mirror store、`application/{maintenance,refresh}.py`、budget 与报告。

获取前检查授权、页面/解析版本、事实用途和时效；重用返回原证据引用，过期重查预留预算。共享内容复用与 Account 关联分开。不将未命中的无限负缓存当永久 no-route。

当前 refresher 只支持本地 `source_coverage` census。R2 必须接入目标账号官网/责任关系/联系人页面的有界外部重查；至少把 R2 产品承诺中的方面全列为 required，而不是通过过滤掉未实现 aspect 得到全绿。

合并同一对象/aspect 的相近到期理由，记录原因集合；新写入字面块不自动等于新页面/外部事实变化。无内容变化不产生重复纠错任务，有变化定向重评并保留历史。

**关闭：**相同冻结输入第二轮无额外获取；时钟推进到期后确实发出批准重查；无变化/变化/不可达三种状态分开；变化只更新受影响对象，不重扫全场景；首次观察+信号只造成一次必要重查；失败不写刚验证；跨工作区缓存不泄漏。

### B23 · Member-A Feedback Loop + 结果归因 + 受控策略更新

**落点：**`docs/SCENE_EVOLUTION.md`对应现有策略/指标/ReviewCase用例；新增最少的ExperienceCandidate、PolicyPatch和来源关联，复用B20版本审批。

R1/R2 首先接 **Member-A 使用过程中的业务反馈**，其次接研究收益、错绑、访问失败、复用和成本等运行结果。R3 reply/referral/demo 仅预留版本化事件 schema 与批准导入/fixture验证；**不因此开启真实发送/收件箱同步**，也不把 Jev 概率当回复率。

新增最小 `FeedbackEvent`：literal feedback/artifact ref、principal/source session、object/run/workstream refs、feedback kind、proposed scope、created_at。自然语言不能直接改全局 policy；先区分：
- `fact_correction`：追加观察/反证/supersession并定向重评；
- `run_instruction`：只影响尚未租约的当前Run工作或下一Run；
- `workstream_policy`：先 impact preview，再批准为 scoped policy version；
- `experience_candidate`：结合 Run 指标形成可评估候选；
- `business_definition`：改变客户/资格/产品/权限的规则，必须可信业务批准。

scope 支持 `object/run/workstream/segment/scenario/geo/workspace`，默认最窄合理范围。Fast loop 让 Member-A 明确的 scoped 反馈快速用于下一批；Slow loop 走 `ExperienceCandidate -> replay/evaluation -> PolicyPatch -> approval/promotion -> monitor/rollback`。历史 Run 的 frozen plan 不被事后重写。

将 observed title/RoleAssignment、function、grade 与排序策略分开。运行经验可调已批准来源/页面/query排序，但低样本不能自动拆 Scene 或改变客户资格。按 Product/Offer、Scene/Segment、Account class、role、template/stage/timewindow 记录未来结果；OOO/退订/退信不算正面回复，未成熟观测不算失败；不以单次高 raw reply rate 把 D 升级为 A 或把竞品纳入。

**关闭：**
- Member-A 对一个具体 Account 的纠错只更新/重评该对象及真实依赖，不自动污染整个 Scene。
- Member-A 明确“法国学校 Facilities 优先”可 preview 影响并发布 Workstream/Segment 新版；下一 manual Run 使用新版，在途 attempt 保持原 plan。
- 同一句反馈从 Codex 输入后，WorkBuddy/MUC 读取同一服务端有效版本；无需同步 Skill memory。
- 系统能回答每条反馈“谁说的、作用在哪、从哪一 Run 生效、影响哪些对象、如何撤回”。
- 可泛化经验先是候选；proposal不能自行生效，扩大客户/claim/访问/权限必须审批。
- 明确窗口/样本量/分母的结果例子可重算，未观察结果=unknown；不为完成R2引入 bandit/RL。

### B24 · SavedView / Watch / ActionItem：监控池与跟进池不复制客户

**目标：**Member-A 可以保存“重点账号”“本周到期”“有通用邮箱但缺负责人”等工作池；启用 Watch 后系统只对到期/变化对象做有限工作；跟进池展示下一步行动、负责人、理由和 due，而不是一个混乱的 `account.status`。

实现 side-effect-free SavedView（query/version/owner/visibility），读取不得调用provider。Watch 绑定 view/object set + aspects + freshness/trigger + budget + notification policy，启用/变更需要权限和版本；停用后不产生新昂贵工作。ActionItem 绑定对象/原因/evidence/owner/due/state/expected_version，可由研究、Review或未来R3事件产生。

账号身份、ResearchAspect、Qualification/Priority、Route currentness、Engagement、Action state分开；Watch/View只做投影，不复制Account/Route。多个View命中同一对象不能重复研究。

**关闭：**保存/查询View零网络/模型调用；enable Watch产生durable due work；同Account出现在3个View仍只有一份事实和一次兼容refresh；变化产生一次业务Event及适用Action；Action重放幂等、过期version拒写；停用Watch后未来fire不派昂贵工作；workspace/visibility隔离有反例。

### B25 · 一个插件、多Session、灵活Schedule与统一Notification

**目标：**Member-A 在多个Codex/WorkBuddy session分别做不同地区/任务，全部连接同一MarketOps MCP；关闭聊天后任务/Watch继续；新session可接续；重要变化可进入一个Feishu群，客户端没有主动推送能力时诚实降级为Notification Inbox/拉取。完整合同见 [MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md](MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md)。

插件：维护一份canonical主Skill及references，生成各客户端projection和MCP连接模板；Skill不存客户事实/预算/授权/secret。客户端差异只在manifest/安装/可选通知bridge，不复制业务规则。

**首批客户端优先级固定为 Codex + WorkBuddy + MUC/Hosting。** Codex/WorkBuddy有Member-A真实session行为证据，先做当前版本的MCP/auth/reconnect/multi-session/Skill安装兼容验收；MUC/Hosting作为正式长期Steward入口紧随其后，验证principal映射、Review/Watch/Action/Notification和重连。MUC并非Member-A导出语料里的已用客户端，不能把目标设计写成历史事实。未验证能力标unsupported。

多session：禁止principal级全局`current_scene`。可增加SessionBinding/ContextRef作为便利，但写操作始终显式workspace/object/revision；binding不提供权限、不进入业务幂等。两个session同时研究兼容对象时single-flight/复用；旧session CAS失败，不能覆盖新状态。

Schedule：MarketOps持久 ScheduleDefinition（one_shot/calendar/due_state/event），一个 authoritative trigger driver；MCP 只配置/读取/控制。它只能依据 B17 的 `bounded_auto/watch` 授权创建未来 Run，不能把普通 Workstream 自动化。外部 cron/Hosting/未来 ServerOS 只能带 fire_id 调用同一用例，重复 fire 幂等。due check 先于 provider 调用，空队列 0 LLM。timezone/window/catch-up/quiet-hours/预算/暂停明确。

Notification：业务Event一次落账，Subscription按workspace/workstream/view/watch/severity/sink投影，DeliveryReceipt逐sink记录。通知失败只重试通知，不重跑研究。默认critical/needs_human即时，routine摘要，progress heartbeat不主动推。

Feishu：新增消息sink/bot适配概念，和现有Bitable projection分开。真实群消息必须独立业务授权、app凭据、群范围、速率和vendor live验收；未接通不阻塞MarketOps。未来入站命令先验证飞书身份/签名→MarketOps principal→类型化intent→权限/version校验；群里自由文本不天然构成发送授权。

客户端报告：所有session都能读同一server-side report/as_of/cursor。MCP不保证向任意聊天主动push；有经验证thread/message API才做ClientNotificationBridge，否则session打开时/用户查询时拉取。默认不因5个session产生5份外部团队通知。

**关闭：**
- Codex A=UK、Codex B=France、WorkBuddy C=只读View并行：上下文不串、共享Account/URL不重复付费、C只读0 provider；Codex/WorkBuddy读取同一snapshot业务键/单位/as_of一致。
- 当前版本Codex和WorkBuddy分别完成：安装/连接→鉴权→读→写→提交长任务→断开→另一session/重启后读回同一结果；不允许靠本地CSV/skill memory补状态。
- MUC/Hosting完成独立兼容验收：同一principal/对象语义、Review/Watch/Action/Notification pull、断线重连；若平台有cron，只作为同一schedule fire driver验证，不启第二套刷新。
- 关掉全部session后schedule继续；旧session写被version拒绝；重复schedule fire不重复工作。Notification event一次→Inbox+Sandbox Feishu投影各一receipt；Feishu失败不影响研究。插件包不含secret/数据库/业务资料。

### B30 · Member-A 受控使用与接管计量

**交付：**一页 MCP 使用说明：启动批准 Scene、查看业务进度、查账号证据、处理待办、暂停/恢复、取草稿。用户无需 SSH，但一次部署/凭据配置仍由获授权管理员负责；不把管理员操作伪装成业务自动化。

**关闭：**用受控 pilot 记录 Member-A 的必要业务决策与可避免操作，分别计数。没有真实人工计时就标 unavailable。既报少做了哪些事，也报遗漏、错误、回滚和质量检查，不能以降低抽查或不交结果来减少人工次数。

预注册比较计划：同场景/截止时间/样本/来源许可，gold独立标注，warm/cold缓存条件分开；记录质量非劣容限、最低质量要求、成本与人工/总时长指标，阈值由业务批准。按Account/采购主体分组防样本互扰；必要审批时间与重复搬运/排错时间分开，不删hold/blocked。

`技术R1/R2通过`、`可带复核pilot`、`优于Member-A`三个结论独立。没有可比基线时前两者可按证据判定，第三者必须not_demonstrated；不能因测试全绿就宣称效率质量更高。

## 3. G1/G2：先纵向微型验收，再扩大真实样本

### G0 — Workstream 启动、可选产品上下文与跨任务复用

用两个Workstream（其中一个不绑定产品、一个绑定有批准出处/明确fixture身份的Product/Offer）、其中一个Account-driven、一个共享Account和一个区域反例：MCP预览目标/范围/可选产品资料→批准research→持久run。无价格不猜价；需要文案时资料/claims缺失返回draft/hold；纯身份研究不被迫创建Offer；scope/预算未批不外调；不同Workstream不复制客户库。

旧product字符串迁移、产品撤回、同一Account不同Scene评分、某任务暂停/另一任务继续、同源额度共享、launch幂等必须测。fixture不变成真实已批准产品资料。G0是功能验证，不是一场公司业务立项授权。

### G1 — Research R1

先跑 3–5 个有出处的结构样本：已知正例、无邮箱、同名异地、共享域/第三方官网、冲突记录。合成 fixture 与真实公开样本分开；人提供测试种子不等于生产路径允许人工拼 Account。

顺序：有效Scene/Workstream（Product/Offer按任务需要可选） → discovery/import → 准入/identity → cohort → Quay research → Jev（确有用途）→ 唯一路线/拒收 → 草稿/合法 hold。全程通过一个 MCP，从零手工 roster 开始。

小样通过再用已批准的预算扩大 cohort。不要每个局部修复都重新花一整轮 20–50 Account 成本；优先用已取得工件重放，最后对相同代码构建做有界 live run。没有授权预算只阻塞 paid 验收。

**必须失败的对照：**错国家被标 eligible、目录别人的邮箱被绑定、人工 YAML 是必需、重复 route 计新增、空正文声称镜像成功、X 被包装为关键联系人、跨 workspace 证据、单页坏时间使全批无回执。

### G2 — 可选自动化与多客户端持续经营（技术验收，不改变 manual 默认）

在 G1 相同状态上再做多会话/持续经营：Codex A=UK、Codex B=France、WorkBuddy C=只读重点View → 断开全部控制客户端 → 下一批/Watch按Schedule自动执行 → 重启控制器 → 重放事件 → 暂停UK且France继续 → 恢复 → 模拟到期 → 真/受控来源变化 → ReviewCase裁决 → ActionItem关闭 → 受影响对象重评 → 草稿版本更新 → 新session读取同一状态。

区分三种时间：即时重复 tick（应无重复工作）、来源新证据产生后的 tick（可能合理有工作）、真实到期后外部 revalidation（需要获取/验证回执）。单次 `claimed=0` 不能证明后两者。

长周期语义用受控时钟测，不等待一个月；长期运行稳定性仍另标未观测。基础设施停机与业务无新数据必须可区分。

增加访问/资源故障回放：Ray ID正常页、challenge、429、低质量解析、某provider缺席、跨Scene同时取同源、timeout后未知计费。控制器必须有限成本继续其他合法路径并保留正确hold，而不是把整条线无限重试或统一宣称没有客户。

增加schedule/notification故障回放：重复fire、错过窗口、Feishu 429/5xx、sink未知投递、客户端不支持push、session cursor独立、Notification重试。业务Event不可重复，sink失败不能触发研究重跑；团队digest和session Inbox都能从相同event cursor重算。

### G3 — 是否优于Member-A的受控使用评价（不是R3外发）

执行B30批准的baseline比较。输出范围/身份/角色/路线/草稿质量及不确定性，人工主动分钟、end-to-end elapsed、worker-minutes和总成本；所有未完成/失败/待审有去向。若没有独立标签或Member-A同条件基线，状态为NOT_DEMONSTRATED，不阻止诚实的带复核试用，也不生成夸张效果文案。R3外发效果与职位回复学习仍等待单独授权及事件数据。

### G4 — Target Architecture Conformance：最终设计不能只存在于文档

这是本项目“最终实现是否就是设计架构”的唯一总门。只有以下全部为真，`TARGET_ARCHITECTURE_CONFORMANT=YES`：

1. **一个逻辑MarketOps MCP**：Member-A/controller与Quay worker使用同一服务、不同角色工具面；Serper/Crawl4AI/Jev/Quay不各自变成Member-A要管理的业务MCP。
2. **一个共享业务真源**：Account/Asset/Person/Role/Contact/Evidence/Workstream/Run/RunDelta/Feedback/Watch/Action/Schedule/Event 均由服务端持久化；客户端文件、聊天 memory、Skill 都不是权威。
3. **Scene-aware routing已真实消费**：ResearchContext→EffectiveResearchPlan→provider/worker调用→业务对象可反向追溯，动态识别不会偷偷扩大scope。
4. **PR #8已有R1/R2能力没有回退**：identity、route、cohort、draft、Jev lineage、Review、continuation、real refresh在最终组合根重新纵向通过。
5. **增量 Run 与自动化边界落地**：manual 为默认；已受理 Run 可断线完成并合入 RunDelta，但不自行创建下一 Run；bounded_auto/watch 只有显式授权才创建未来 Run。
6. **Member-A Feedback Loop 落地**：fact correction、scoped policy、ExperienceCandidate 有真实 producer/store/consumer/impact/rollback；单次自然语言不直接变全局规则。
7. **Watch/Action/Schedule/Notification落地**：SavedView纯读；Watch产生due work；Action承载下一步；一个authoritative scheduler；一个event可投影多sink但不重执行业务。
8. **插件/客户端真实兼容**：Codex、WorkBuddy、MUC/Hosting分别完成当前版本安装/连接、auth、read/write、长任务断线恢复、多session隔离、同一snapshot；unsupported能力明确降级。
9. **canonical Skill + client projections**：一份业务workflow来源，按客户端投影；国家/产品/Scene规则在服务端版本化，不复制进各Skill。
10. **访问/安全/预算闭环**：WAF/403/429/timeout/unknown usage/redirect/browser子资源等按B15处理；不绕访问限制；预算和限流跨session共享。
11. **报告/通知唯一口径**：Codex/WorkBuddy/MUC/Feishu看到同一as_of/业务键；sink失败不改变业务事实。
12. **历史验收不冒充live效果**：技术 conformance、受控pilot、优于Member-A、production authorization 四个结论分开。

验收报告必须生成一张 `architecture_conformance_matrix`：每个架构承重概念列 `producer / store / consumer / MCP surface / auth / test / live evidence / status`。只有类名/schema而没有生产调用者的格子不是完成；某概念刻意仅为逻辑投影时也要点名其实际承载位置。

**最终状态字段：**

```text
LEGACY_R1_VERIFIED = YES/NO + source SHA
LEGACY_R2_VERIFIED = YES/NO + source SHA
TARGET_ARCHITECTURE_CONFORMANT = YES/NO
READY_FOR_MEMBER_A_TRIAL = YES/NO
MEMBER_A_REAL_TASK_TRIAL = PASS / FAIL / NOT_RUN
CODEX_CLIENT_VERIFIED = YES/NO
WORKBUDDY_CLIENT_VERIFIED = YES/NO
MUC_HOSTING_CLIENT_VERIFIED = YES/NO
PILOT_WITH_REVIEW = YES/NO/NOT_RUN
BETTER_THAN_MEMBER_A = DEMONSTRATED / NOT_DEMONSTRATED
PRODUCTION_AUTHORIZED = YES/NO
```

这些状态不能互相推出。

### G5 — Member-A 本人真实 manual Run 试用：最终交付给用户验，不让 CC 自己宣布成功

G4 通过后，CC 停在 `READY_FOR_MEMBER_A_TRIAL=YES`。**不要代替 Member-A 选择真实市场任务，也不要为了“完成验收”自行花费真实 provider 预算。** 由 Member-A 自己在首批客户端之一（优先 Codex 或 WorkBuddy，MUC 可随后）输入一条真实研究任务并决定预算/范围。

首个真实试用固定 `execution_mode=manual`，规模刻意小：建议 5–20 个目标对象或等价的小预算，以能人工抽查为准。流程：

```text
Member-A描述真实任务
→ 插件识别/复用Workstream
→ preview ResearchContext / EffectiveResearchPlan / 已有池子复用 / 预算 / stop
→ Member-A确认并启动manual Run
→ Member-A可关闭session
→ Run完成，读取RunDelta和证据
→ Member-A人工抽查若干Asset/Account/Person/ContactRoute
→ Member-A至少给一条真实FeedbackEvent
→ 系统展示scope/impact，按Member-A选择落事实修订或scoped policy
→ Member-A手动启动第二个小Run
→ 验证第二Run复用第一Run池子且消费新反馈
```

**PASS 要求：**
1. Member-A 全程不需要 SSH、SQL、手写 YAML/CSV、开发者桥接或让我/CC替他改库。
2. Run 真的调用获授权 provider/worker并留下费用/未知费用与证据回执；不是 fixture。
3. 第一 Run 的 Delta 可理解、可对账；抽查错误能通过 Feedback 正确修订且不全局污染。
4. 第二 Run 明确复用第一 Run 的已有事实/工件，只补目标缺口，并消费已生效 scoped feedback。
5. manual 模式在第二 Run 完成后保持 idle；没有 Member-A 指令不自行第三轮扩池。
6. 若遇到 access blocked/预算不足/业务歧义，系统正确 hold/review，而不是放宽规则凑产出。
7. 记录客户端版本、MarketOps SHA、Workstream/Run ids、plan digests、provider调用、RunDelta、Member-A抽查与反馈回执。

G5 失败就把问题回到对应 B 项修复并重跑**同样的小任务或已有工件**；不要重新大规模扫市场。G5 通过也只表示“Member-A 可亲自使用 manual 研究闭环”，不等于优于 Member-A、生产外联授权或无人值守自动经营。

## 4. 发布判据与自动化指标

| 判据 | 分母/正确读法 |
|---|---|
| 人工桥接次数 | 每个冻结 cohort，从业务任务开始到结果回读；YAML/SQL/复制 CSV/身份手抄属于桥接，批准目标与人工 gold 不属于桥接 |
| 人工催续跑次数 | 已授权范围内本应续跑但需人发“继续”的次数；正常主动暂停/扩大范围不算缺陷 |
| 可自动对象完成率 | 无需新业务裁决的对象中，被系统处理并可回读结果的比例；全 hold 不等于 100% 自动化 |
| 业务质量 | 有独立标注的身份/范围/邮箱关联/角色正确性；无标签时 unavailable。A/B 和 C/D 分开 |
| 复用与成本 | 真实请求、重用、失败、重试、未知用量；不以 evidence 行数算网页数；不凭价格缺失写 0 成本 |
| 待办质量 | pending 起止账、必要案件数、重复案件、超时、响应后的重评；不是每个 unknown 都生成待办 |
| 可恢复性 | 重启/并发/乱序/暂停路径上的重复昂贵动作和丢失对象；比较业务键集合而不仅计数 |

R1/R2 技术通过要求可避免的人工桥接/催续跑为 0；这不是要求人工决策为 0。业务结果需要附抽查边界后决定受控使用，不能用本计划预先宣称 production-ready。

对比 Member-A 历史方式与新方式时固定 cohort/截止时间/规则/来源和资源条件，记录主动操作时长与中断次数；没有匹配基线就只交本轮观测，不报虚构节省百分比。

## 5. 工程执行纪律：防止再次无限实施或只出报告

- 每个失败先用现有工件或小 fixture 重现；补真正生产调用路径，不再靠多造 getter/DTO 填表。
- 每个切片跑相关测试；稳定检查点跑全量、ruff、schema、catalog 与干净克隆。只对权限、身份误绑、预算、重放/丢失等关键边界定向故障注入，不为普通字段做无穷 mutation campaign。
- 对已批准范围内的缺陷完成“发现→修复→重放”闭环。需改变业务定义的留 ReviewCase/阻塞；不要擅自放宽 SOP，也不要把需要人工合并 PR 当作实施失败的根因。
- 测试数量和注释行数不算进度；阶段汇报回答“Member-A 哪个动作现在不必做了，用哪次真实路径证明”。
- 单一应用用例供 MCP/CLI/控制器使用。新工具同步 catalog/schema/角色与测试，不暴露万能执行口。
- 迁移备份、业务键对账、回滚/隔离方案随功能交付；不重写 raw/evidence/旧验收，不删除他人工作树或用宽匹配杀进程。
- 默认 research-only、禁发。所有真实计费/部署变更使用既有授权范围；配置缺失不能编造通路或复制凭据到 prompt/报告。

## 6. GitHub 交付与范围边界

本设计提交使用独立 docs 分支，以现有产品 PR 分支为 base，避免向正在实施的工作树插写或把 89 个产品提交误记为文档改动。产品实现继续按仓库协作规则推进，不自动 merge PR #1/#6 或本设计 PR。

每个交付登记 `implementation_sha`、`tested_code_sha`、运行输入/配置/模型、证据位置、实际结果与差异。最终 remote ref 必须读取核对；纯文档追加不假装重新 live 测过。

Laya / ServerOS / Docker / SMTP / Feishu / 自动回复 / CRM 明确 DEFERRED。安全关闭状态必须保持，但它们不作为 R1/R2 失败原因。

设计PR更新后，实施会话先fetch并比对设计diff，保留已完成代码，不盲reset到旧61419b7。尚未开工的实现从最新设计ref/正确产品后代开始；已开工的逐项合入文档并记录已覆盖/待补，不能因为计划更新重做底座。

供CC的执行顺序以本文件为准：C00→B15→B16→B17→B01/B02→回归既有B10–B14/B20–B22→G0/G1'→B23/B24/B25→G2'→G4→READY_FOR_MEMBER_A_TRIAL。G5 必须由 Member-A 本人给真实任务后再跑；B30/G3 的“是否优于 Member-A”更晚，不能由 one-shot 实现会话伪造。每项必须有生产者、存储/事务、实际调用者、MCP读取/控制、故障回执及关闭测试；只有类名/字段/接口的无消费者实现不算完成。范围内缺陷继续修，客观外部权限/预算限制保留；不以“这次只验收”停在能自行解决的bug。

新的验收文件按实际日期写，不再反复创建名为 FINAL 却隐藏未完成项的版本。用 `R1_VERIFIED` / `R2_VERIFIED` / `PILOT_WITH_REVIEW` / `BLOCKED` 等分层结论并给证据，生产启用另列授权。

## 7. CC one-shot convergence implementation contract

后续给 Claude Code 的“一次性落实完”不是让它从头重写，而是让它在**一个持续 implementation session / 一个 convergence branch**里按本计划把剩余目标架构做到 G4，并留下可供 Member-A 直接试用的插件/服务。

### 7.1 起点

- 代码底座：PR #8 HEAD `426a09f6632ac5dd63c0da39669036d4c8163755`；保留其历史验收报告。
- 设计权威：执行时重新读取 PR #7 最新 HEAD 和本文件，不锁死当前文档 SHA；先比较自上次实现后的 design diff。
- 第一提交只同步最新 docs/status matrix + 跑 PR #8 全量基线；不重写功能。
- 不自动 merge PR #7/#8；在新 convergence branch 完成实现和 PR。

### 7.2 一次性实现范围

必须在同一施工链关闭：B15、B16、B17、B01、B02、B23、B24、B25；保留并回归 B10–B14/B20–B22；完成 G0/G1'、G2'、G4。范围内发现的代码缺陷直接复现→修复→重测，不以“报告 blocker”代替可自行修的工作。

必须交付：
- migration + domain/application/ports/adapters/composition；
- MCP catalog/schema/roles/negative tests；
- canonical MarketOps Skill + Codex/WorkBuddy/MUC projections/安装说明；
- client compatibility receipts；
- architecture_conformance_matrix；
- 一页 Member-A manual Run 使用说明；
- 一个**不执行真实付费请求**的 trial-preflight 命令/用例，能检查 endpoint、auth、provider配置、预算是否 ready；
- 最终状态 `READY_FOR_MEMBER_A_TRIAL=YES/NO`。

### 7.3 不允许为了“一次做完”扩大范围

仍不做真实邮件发送、自动回复、CRM、Laya、ServerOS、Docker/K8s 重构；Feishu 只按 B25 的通知/adapter 范围，缺真实授权则诚实 BLOCKED，不阻塞核心 manual trial。不要做通用PIM、通用workflow engine、RL/bandit或新数据库集群。

### 7.4 one-shot 结束条件

CC 只有在以下全部完成才结束 implementation session：
- 最终代码 SHA 与远端一致、worktree clean、PR open且未auto-merge；
- 全量测试/ruff/schema/catalog及新增纵向测试在同一 tested SHA；
- G4 的 matrix 每个承重格都有真实 producer/store/consumer 或明确 logical projection；
- Codex/WorkBuddy/MUC 的实际支持状态逐客户端诚实记录；
- `TARGET_ARCHITECTURE_CONFORMANT=YES` 或仅剩**客观外部权限**导致的精确 BLOCKED；可修的软件缺口不能留给 Member-A；
- 生成 `MEMBER_A_REAL_TASK_TRIAL.md` 模板和使用步骤，但 **G5保持 NOT_RUN**，等待 Member-A 自己给真实任务。

下一步才是 Member-A 亲自跑 G5。
