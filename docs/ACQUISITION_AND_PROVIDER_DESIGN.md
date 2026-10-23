# 采集、监视与供应商接入：一个 MarketOps 研究产线

更新：2026-09-27。性质：**要实现的契约，不是全站可抓、集成已通过或部署已启用声明**。与 [业务模型](PRODUCT_LINE_AND_SCENE_MODEL.md)共同补充主架构；执行项并入 [统一计划](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md)。产品基线 `df689f52`、设计前身 `61419b7`；技术资料/阅读限制见 [研究说明](research/MARKET_MODEL_ACCESS_REVIEW_2026-09-27.md)。

## 1. 什么是“爬取监视产线”

它不是为每个产品/国家创建一条长期聊天或爬虫进程，而是一套共享能力，消费冻结 ScenePolicy、Cohort 与预算，持续产出可验证业务对象。

```text
Product/Offer + GeoScope + Scenario + approved policies
  -> launch/SceneRun + versioned source plan
  -> reuse/coverage check
  -> structured source import OR Serper discovery
  -> candidate triage / scope / identity enrichment
  -> approved acquisition plan and per-origin budget
  -> HTTP/PDF OR allowed anonymous browser rendering
  -> immutable artifact + parser/extraction receipts
  -> Account/asset/responsibility/person/role observations
  -> deterministic evidence binding + bounded Jev advice
  -> canonical routes / opportunities / rejected reasons
  -> cohort and draft projections
  -> durable continuation / due revalidation / Review Inbox
```

“监视”分三种：服务健康（无需 LLM）、来源可访问/到期变化（有限获取）、业务产出/待办/预算变化（读权威账本）。未启用 R3 时不监视私人收件箱，不代为发送。读进度不付费获取；计划到期与运行启动是明确有权限的操作。

## 2. 各个组件只能承担合适的工作

| 组件 | 合同输入 → 输出 | 不负责 | 本阶段验收 |
|---|---|---|---|
| Serper adapter | 版本化 query + 搜索地区/语言上下文 + 上限 → hits/URLs + 原始回执 | 官网归属、场景合格、邮箱强绑定、证明互联网全集覆盖 | 真实查询回执及计划/尝试/成功/未知计费分开；搜索 bias 不代替地理证据 |
| HTTP / PDF acquisition | 批准 URL 与策略 → 原始字节/headers/hash + 解析来源链 | 从403猜IP封锁，猜测邮箱，擅自跨登录 | 安全入口/逐跳、type/size/time、解析失败、重试与复用都可测 |
| Crawl4AI | 公开且允许的 JS-required/renderable 页面计划 → rendered artifact 与抓取回执 | 保证穿透 Cloudflare、破解 CAPTCHA、继承 Member-A 登录态 | 固定版本下真浏览器样本、拒绝/重定向/并发/关闭恢复/缓存行为测试 |
| Jev DecisionProvider | 有出处的小段 state + 服务拥有的闭集 DecisionSpec → typed answers/probabilities + DecisionRecord | 搜索、事实补造、写邮件、提升权限、决定预算/禁用规则 | 真实适用判定被下游按 id 消费；校准与对照评价另报 |
| 派发执行器 + 受限 worker | 有界 WorkItem、角色与预算 → evidence/findings/合法终态 | 每URL一个Agent、无限重试、替代码作授权判断 | 独立真worker、断线续跑、租约恢复、受理驱动下游、不因软件landing重复 |
| 普通应用代码 | 调用/结果/版本 → gates、事务、归并、状态、统计、恢复 | 自己发明业务真值 | MCP/CLI/controller用同一用例；重放与故障不丢失、不重复副作用 |

这是职责分工，不要求每件研究调用全部组件。入口数据已经充分可以不搜索；HTTP内容完整不启动浏览器；确定性事实无需模型再投票。需要的集成未就绪就明确能力缺口，不能把 fixture、CLI旁路或目录里有工具名计为 live 产品接线。

### 当前代码值得优先复核的具体接缝

- `application/escalation.py` 已区分 OK/终态与 JS_REQUIRED/EMPTY_SHELL/RENDER_FAILURE，保留，不改成“任何 HTTP 错都 browser retry”。
- `adapters/crawl4ai_fetcher.py` 每次新建 crawler，且渲染后检查落点的注释明确是**事后检测**。若使用于多租户/不受信URL，必须在发出请求前控制导航、重定向和子资源；仅丢弃返回值不能防 SSRF。
- 同 adapter 用 `status_code or 200` 作响应判断：不能把未观测状态写成成功诊断。原始状态应 nullable，正文可用性与 HTTP 状态分别判断。
- 同 URL 原路径已存在时不写新内容，却可能为新 HTML 计算新 hash：必须验证读取的字节与回执 hash 始终一致。使用内容/请求版本工件与 URL-index 分离，不能“防覆盖”变成引用旧字节的新回执。
- `adapters/serper.py` 当前 payload 主要是 q/num，错误提示把401/403与key/额度混说。补上部署实际支持的搜索上下文与分层诊断，不能靠改 prompt 把所有 Devon 解释成英国。
- `adapters/jev.py` 已有结构化输出解析并关闭 SDK 隐式重试；保留一次逻辑调用/显式attempt账。`application/relations.py` 已按 decision_id 核查任务/主体/spec/答案，复验并补最终对象可回读关联，不重写成另一套因果链。

这些是固定代码上的静态观察，是否被其他部署防护补偿仍需读实际组合根/策略并测试。本文件不把潜在风险直接说成生产已发生事故。

## 2.5 Scene-aware Research Router：根据 Member-A 的任务动态编译研究方法

MarketOps 不要求 Member-A 选择“用 Serper 还是 Crawl4AI、先搜哪些页面、德国用什么关键词”。客户端/主 Skill 负责把自然语言目标变成结构化请求，服务器把它解析成 `ResearchContext`，再用版本化模块编译 `EffectiveResearchPlan`。**动态识别的是细粒度研究上下文和执行计划，不是无上限自动创建长期 Scene。**

```text
Member-A request
  -> Workstream / explicit scope
  -> ResearchContext Resolver
       geo / jurisdiction / locale candidates
       entity/asset/account classes
       scenario candidates
       objective / relationship
       optional product-use context
       ambiguity + evidence requirements
  -> Strategy Compiler
       Geo/LocaleSourceProfile
     + ScenarioProfile
     + ObjectivePolicy
     + AccessPolicy
     + optional Product overlay
     + Workstream override
  -> frozen EffectiveResearchPlan
       structured-source order
       search locales/query families
       page/link archetypes
       role/function hints
       acquisition/fallback order
       DecisionSpec refs
       budget/sufficiency/stop conditions
  -> execution + receipts
```

### 国家、语言、场景不是同一个维度

- `country/jurisdiction` 决定行政体系、可用公开数据源族、搜索定位和某些政策；
- `locale/language` 由地区先验 + 页面实际语言共同决定，多语国家不得被强行归成单一语言；
- `scenario/entity class` 决定什么责任关系、网页类型、岗位词和证据最值得找；
- `objective` 决定爬多深：建母池、找责任组织、找联系人、刷新旧路线需要的页面和预算不同；
- `product/use-case` 只在 ProductFit、角色优先级、草稿等需要时加入，不阻塞纯身份/责任研究。

原始职位、页面和证据始终保留原文；翻译/规范化是派生层。搜索可用本地语言同义词，不能把翻译后的title冒充来源原文。

### 动态场景识别的权限边界

Resolver 可匹配服务端已知 Scenario/Profile registry，也可输出多个候选和置信/依据。若歧义仅影响低风险query/page排序，可在已批准Workstream envelope内使用保守计划；若会改变目标人口、hard exclusion、SalesMotion、产品claim、预算或访问权限，必须 hold/Review/用户选择。模型置信度不能扩大授权。

`SceneFingerprint` 是派生标签，不是Scene ID：同一Workstream可产生多个细粒度 fingerprint/Segment。系统只有在长期观察证明 source strategy、责任模型和治理边界持续不同后，才提出 `SceneSplitCandidate`；它本身不能创建新Workstream。

### 运行中允许自适应，但只在批准envelope内

SourceMethod 累积 eligible yield、useful-page precision、duplicate、access failure、latency、cost/unknown usage 等指标。运行中可下调持续低yield/被阻断来源、上调已验证高yield来源、调整page archetype优先级；变化与样本量可回读。硬排除、客户定义、新国家、新角色语义、claim和授权只能形成 Experience/Policy 候选。

每个 WorkItem 固定 exact plan/version/digest；运行到一半配置更新不改变已派发attempt。下一run按impact选择复用、重评或必要补抓，不因为策略版本变化全库重爬。

**执行证明：**每次 Serper/HTTP/Crawl4AI/Jev/派发执行器 选择都应能反查对应 plan step 与理由；一个任务没有必要调用某 provider 时，零调用是正确结果，不为“工具都接了”制造无意义调用。

## 3. SourceMethod / AccessPolicy / Attempt：先区分三种权威

SourceMethod 表示可重复的发现方法：结构化公开清单、官网目录、sitemap、公开 API/PDF、搜索 query family。保留适用地区/客户类型、版本、cursor/cutoff、单位和成功判据；Account级/场地级/页面级母池不能混算。

AccessPolicy 表示允许的访问：目标来源/路径、访问模式、机器人协议处理、并发/速率/请求/字节/时长、重试上限、数据保留、允许的替代来源、凭据类别。它来自批准策略，不由 webpage 或 worker 声明放宽。授权不明需要 access_review，不能默认为可抓。

FetchAttempt 表示一次实测：workspace/run/work item、URL/finalURL/redirect trace、方法、内部 connection/egress profile id、开始/结束、status nullable、safe response headers、error code、body kind/bytes/hash、parser版本、预算reservation/settlement、retry_after、action_taken。原始敏感headers/cookies/key/IP不出报告。

**观测和根因分开：**`observed_status=403`、`challenge_detected=true`、`suspected_cause=ip_policy`、`cause_evidence_ref=...`。没有证据时是 unknown，不因为某页有 Cloudflare 就填“出口IP封禁”。

## 4. Cloudflare/IP：能诊断、能合理改道，不能保证穿透

官方资料说明 Ray ID 用于关联经过 Cloudflare 的请求；它不是封IP证明。普通请求也可能带 cf-ray。403 可以来自源站权限规则或 Cloudflare，多种规则都可触发，不能一对一推出原因。`cf-mitigated: challenge` 可识别标准 Challenge Page；缺少该头不等于证明没有其他拦截。[CF1–CF3]

因此此前“14站都有Ray ID，所以确认同一IP被封”的二级分析只能保留为**待核实历史诊断**，不能写成产品判定规则。要确认 IP/ASN 规则需站点方的匹配规则/日志、明确错误说明或批准的诊断证据；本项目不通过轮换身份探测封禁边界。

| 实际证据 | 诊断与默认动作 | 不应做 |
|---|---|---|
| DNS/TCP/TLS失败、明确本地出网错误 | network_unknown/egress_denied；检查自有主机配置、批准连接和共享健康状态 | 立即声称网站不存在或无联系人 |
| robots/访问政策拒绝 | policy_blocked；记规则/版本，转允许来源或请求许可 | 换user-agent/代理绕限制 |
| 401/登录页/订阅权限要求 | auth_required；本阶段匿名采集停止该路径，给获授权操作人处理 | 读取用户cookies、自动登录或购买访问权 |
| HTTP 403且根因不明 | access_denied_unknown；保存诊断，有限人工/运维复核及合法替代来源 | 把任何403写ip_banned或立即browser重试 |
| Cloudflare challenge/CAPTCHA正文或对应头 | waf_challenge；不当网页证据，暂停此方法，选择批准来源/站点授权 | CAPTCHA求解、指纹伪装、暗换出口、重试刷门 |
| 429 + 可用Retry-After | rate_limited；按等待期+有界退避，共享origin/credential限流 | 每worker独立重试形成放大或立即换IP |
| 503/短期超时/断线 | transient_or_unknown；按策略有限重试，未知计费先对账 | 无上限重试或把失败记为0结果 |
| 正常公开JS壳且无拒绝信号 | allow_browser_render；进入隔离匿名上下文与受限浏览器 | 把这一规则推广为突破访问控制 |
| 404/410或域迁移线索 | 记来源失效/候选迁移并核实；保留旧证据时间 | 推出整个组织消失或负责人离职 |
| HTTP200但正文是挑战/错误/占位 | content_unusable；保留诊断、不进入业务证据 | nonempty/exit0即当成功 |

出口故障能修的部分：让运维在已有授权内修正DNS/证书/代理/企业端点策略；请站点提供允许的 API、导出或白名单；改用批准的官方替代来源及仍有效的历史证据。公司服务器可减少对 Member-A 电脑环境的依赖，但数据中心出口也可能被限制，不承诺搬到服务器自动解封。

EgressProfile 只记录已批准基础设施能力、适用区域、owner和secret引用。Agent不可自行购买代理、关闭企业安全、轮换IP或凭据规避拒绝。预算/限速与网站访问政策在各出口之间仍共享，不能多出口把一个源的额度放大。

## 5. Fallback 不是固定“HTTP失败就浏览器”

推荐选择顺序：已有可授权复用事实/工件 → 已批准结构化来源 → 低成本 HTTP/PDF → 仅渲染问题时 Crawl4AI → 其他获准官方来源/权威目录 → access blocked/ReviewCase。

每个 SourceMethod 记录备选方法的前提、可信用途、限额与终止条件。例如第三方目录可发现组织但不自动提供官网证明；搜索结果摘要不直接成为 literal 邮箱强绑定证据。替代來源可信度可能更弱，来源变化不能隐式降低 grading。

source/origin 级熔断在 workspace/适当范围内共享，阻止20个账号对同一挑战页各烧一次。记录open/half-open/closed、到期、原因和有限探针；半开探针需policy允许，不是绕封禁。不同来源有工作继续跑；同一 Scene 无任何合法路径才在相应阶段 blocked。

## 6. 性能从资源模型和内容复用来，不从多开 Agent 来

- 获取前执行 ReuseDecision，缓存键包含许可范围、canonical URL、请求变体、render/locale/解析版本、时间有效性；有登录态的环境不进入公共缓存。本阶段默认匿名、无跨主体cookie复用。
- Artifact 的内容hash与原始请求/抓取版本关联；URL-index可更新指向新工件，但旧引用字节不变。条件请求304可记录重新验证，而旧来源发布时间不改；无外部请求不能刷新 verified_at。
- 先用解析器保留 mailto、结构化字段、公开PDF文字和有限记录块；不得为邮箱召回解密/猜测地址或用SMTP探测。原始提取与规范化/绑定分阶段保存，解析错误不得伪装无邮箱。
- Crawl4AI 可使用其批量/dispatcher能力实现有界并发，但必须测试所锁版本的配置；不能照当前网页把宽依赖 `crawl4ai>=0.6` 当可复现部署。[CR1–CR3]
- 可复用浏览器进程降低启动成本，**每个授权域/任务使用隔离context**；仅共享进程不共享登录态/历史/内容给其他工作区。Context泄漏、任务结束关闭和资源峰值要有测试；部署不支持安全池化时保留逐次隔离，报告性能成本。
- 全局Browser上限、每origin并发/速率、API key配额、worker上限与日/周期预算共同约束。应用预算是权威，供应商内置重试/限流不能在外面偷偷加第二层未记账attempt。
- 优先高价值且可访问的证据页，不把一个页面所有邮箱都作为相关产出；已足够且可信的Account按策略停止。来源不可达不立即放弃整个Account，但替代研究仍有上限。
- 预算量纲分别记搜索请求、页面请求、浏览器会话/时间、供应商token与未知消耗；相同内容给多个Scene只付一次获取费用，归属关联另算。

## 7. 安全与数据许可要在请求发出前执行

所有URL（初始、重定向、浏览器主导航、iframe/worker请求和子资源）在网络动作前检查允许协议、目标解析、私有/回环/link-local/元数据网段、端口及来源策略；DNS解析与实际连接绑定，防只检输入后DNS或重定向改向。

对不受信网页防prompt injection：页面中的“调用工具/改策略/取凭据”只是文本；writer与研究worker不继承发布/审批权限。证据材料转发Jev/模型前做最小化和获批供应商数据处理检查，产品私有资料不自动发外部模型。

若所锁浏览器接口不能在发送前拦截全部必要请求，组合根需采用可验证的受管网络层/拦截器，或禁用该模式并明确阻塞；不能只保留“最终URL不合规就不存”宣称已安全。测试用隔离受控目标，不向真实内网或云metadata探测。

持久不可变历史与删除义务兼容：业务记录保留非敏感tombstone/撤回/审计哈希，原始个人数据按来源许可/保留策略删除或访问收回，不能以append-only为由无限保存。日志/导出不含cookies、API keys和未批准客户联系数据。

## 8. Jev 的正确使用和“有用”的验收

当前代码使用 TypeSafe SDK，DecisionSpec来自profile并解析typed answers；继续以实际已安装、已批准版本的请求/返回合同为准。不要从同名第三方站抄endpoint、SDK或价格来替换当前接线；本次 TypeSafe文档接口网页读取未成功，厂商最新参数/上限仍需实施时核对。

适合：页面优先档、role_function、有足够局部证据的responsibility relation等。先做可确定检查，再用Jev评估局部歧义；长开放研究交Claude。一个state里的问题要原子化，不依赖“前一题回答”自动填下一题事实；需要串联时程序显式形成下一输入。

每次记录 requested/returned model、sdk/endpoint身份、spec/version、input摘要与授权evidence引用、provider答案/概率、usage、attempt、latency、outcome；下游保存实际消费的decision id与采用/否决/hold原因。不要把应用生成的解释归为Jev生成的理由。

模型置信度不等于本场景正确概率。阈值依primitive/model/spec/segment在独立样本验证；score并非真实物理量，choice/noul阈值不互相搬运。未校准语义只assist/hold，不依网页的“通用0.85门槛”生效；某些状态证据冲突时即使高置信也不能过硬规则。

**三层成绩分开：**（1）被调用，（2）被业务路径消费，（3）质量/成本有增益。历史57条answered只支持前两层的部分证据，不足以证明第三层。既有关系decision_id校验应复用，不为追求调用数给每条事实加一次Jev。

效用测试：固定同一批事实与DecisionSpec，比较现行批准基线与Jev-assisted流程；结果由独立标签复核，记录错绑、召回/hold、严重错误、耗时、token、外部费用与人工复核。标签少时报告不确定性；无增益的spec保持assist或停用，不硬把所有判断交Jev。Laya与双模型部署仍延期。

## 9. 端到端事务与连接证明

一条真实研究的关联必须闭合：`Launch -> SceneVersion/Offer -> Cohort -> Job/WorkItem/Attempt -> Query/FetchAttempt -> SourceArtifact/Evidence -> DecisionRecord(若使用) -> Relation/Role/RouteAssessment -> Draft -> Receipt`。

并非所有关联都要各建新表；但要能从下游反向回读精确版本、单位、源和拒收。安全读取按工作区/对象授权，不把有某ID当持有权限。

缓存、事务和计费边界明确：先原子claim/reserve，再外部调用，再记录外部结果与settlement，必要时记录delivery/charge_unknown待对账。进程崩溃后不盲重发已受理调用。至少一次事件交付配幂等消费，外部API未提供幂等键时不承诺exactly-once；重复风险与恢复策略写明。

研究settlement accepted、业务projection完成、质量审核通过是不同状态。一个组件返回成功但下一层没消费要自动产出pipeline_gap事件，不等Member-A问“为什么没有进表”。部分失败可交付其余结果；没有报告的整批异常是产品缺陷。

## 10. 单一运行清单和最小健康面

部署清单记录 MarketOps代码SHA、数据库schema、Python、MCP协议/SDK、执行器、worker 运行时与模型、Crawl4AI/Playwright/浏览器版本、Jev模型/SDK、parser、effective policy hash与secret引用（不含值）。以实际运行版本为准，不用latest默认别名作可复现实验。

`get_capabilities`区分installed/configured/wired/runtime_ready/last_live_verified与blocked原因；读取不主动收费。显示“可研究但JS来源覆盖不足”“Jev不可用、需人工持有的任务数”等业务影响，不把provider不活等于全部场景不可用。

控制器独立受管，登记唯一timer/服务owner、最小权限、明确恢复策略。崩溃检查不以文件mtime独自判定执行者死亡；带lease/epoch与可回读状态。监视器只读取/触发预定义恢复，不自动改业务政策。

## 11. 本次设计要求的验收矩阵

| 案例 | 必须观察的结果 |
|---|---|
| 同源正常公开HTML | HTTP内容完整，无不必要browser；内容→绑定→route有真实消费者 |
| 正常JS-required fixture及批准公开样本 | Crawl4AI真实渲染；分别标synthetic与live；完整正文/链接不为占位 |
| 200+cf-ray普通页面 | 不判IP封锁或challenge |
| 200/403挑战页 | 隔离为challenge，0业务邮箱产出，替代策略/熔断有回执 |
| 429/503/Retry-After | origin共享有界退避，attempt/预算不被SDK隐藏重试放大 |
| 403根因不明 | 不称已证IP封禁、不强行升级browser；允许继续其他合法来源 |
| 多账号/Scene同一URL | 仅授权范围内single-flight复用；关联保留，不串账号/不重复费用 |
| 同URL内容改变/原文件已存在 | 新工件hash和实际读取字节一致，旧引用不变；不伪造verified_at |
| 缺状态码、解析错误、压缩/编码错误 | 记录unknown/parse_error，不折算HTTP200/零邮箱 |
| 恶意重定向/子资源 | 在受控测试里网络发出前被阻止，不只是最后不落库 |
| Jev超时/错schema/低把握/误绑 | typed failure或hold，无虚构答案；下游不按“answered”强行确认 |
| 断线/重启/重复事件 | 已批准下一批继续，重复昂贵动作无静默发生；未知计费显式对账 |
| 一个产品线暂停/另一个仍active | 前者不新增昂贵动作，后者照常受配额调度；账号事实仍共享 |

先跑小fixture和存量公开工件，再按批准预算做必要live路径；不为每次修bug重复一整轮20–50账号。负例永远不对真实受限站做绕行实验。

## 12. “克服访问问题”的发布承诺

可以承诺并验收：区分故障层、修复自有授权配置、尊重限速、避免无效重复请求、复用有效证据、合法替代来源、提供可行动异常和不丢进度的恢复。需要外部站点许可的阻塞如实留待处理。

**不能承诺：任意IP/任意站点必能通过，或部署Crawl4AI/Jev就能解决Cloudflare。** 获取失败可为正确结果，但若没有足够的合法来源能完成主要商业场景，就不应仅凭“全部blocked有回执”宣称该场景可用；必须报告覆盖上限、可交付价值和替代来源验证。

## 技术依据（只读官方页面，不代表本项目已经实现）

- CF1：[Cloudflare Ray ID](https://developers.cloudflare.com/fundamentals/reference/cloudflare-ray-id/)。请求标识/关联，不是IP封锁证明。
- CF2：[Detect a Challenge Page response](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/)。`cf-mitigated: challenge`可作为明确观测。
- CF3：[Error 403](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-403/)。403存在不同来源和原因。
- CR1：[Crawl4AI arun_many](https://docs.crawl4ai.com/api/arun_many/)。批量/dispatcher/streaming能力，实际部署需锁版本验证。
- CR2：[Multi-URL crawling](https://docs.crawl4ai.com/advanced/multi-url-crawling/)。限速、并发和内存约束可组合。
- CR3：[Cache modes](https://docs.crawl4ai.com/core/cache-modes/)。缓存模式不代替MarketOps事实时效、作用域与工件账。
- SP1：[Serper](https://serper.dev/)。搜索供应商；目标网站正文和企业归属仍由本产品另外验证。不从营销页面数字推断项目速度/价格/质量。

这些公开资料与当前锁定代码不一致时先验证兼容性，不能靠文档任务静默换SDK。最新厂商限制/价格的可信读取缺失时标pending verification；不从非官方镜像获取凭据或把客户材料发送给它。
