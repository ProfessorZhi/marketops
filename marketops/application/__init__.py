"""**用例层**：把领域规则按一次研究的流程串起来。

这一层回答「做一件事要按什么顺序、遇到分支怎么走」，不回答「什么叫合格账号」
（``domain/``）也不回答「怎么发出一个 HTTP 请求」（``adapters/``）。

## 住在这里的东西

这张表是**穷举**的，而它有一个执行器：``tests/test_application_module_table.py``
遍历本目录下的每一个模块，缺一行就点名。一张靠自觉维护的目录表，在它漏掉第一个
模块的那一天就已经不再是一张目录表了 —— 而读的人不会知道。

| 模块 | 它编排的是什么 |
|---|---|
| ``pipeline`` | 一次研究的完整链路：检索 → 抓取 → 解析 → 绑定 → 定级 → 落库 |
| ``budget`` | 一个账号这次研究的额度：从冻结的 profile 播种上限，按两段式扣账 |
| ``billed_search`` | 一次**花钱**的检索：三段式记账 + 「付过账的不重发」，合成一个动作 |
| ``metrics`` | 运行指标。**只累积数字，不做判定** |
| ``cohorts`` | 「这一批要处理哪些账号」的两半：demo 队列计划（读 ``inputs`` 里的 YAML）与**从账号池冻出来的成员名单**（读池子，产物内容寻址） |
| ``queries`` | 由账号与 profile 生成检索词 |
| ``openweb`` | 开放网络查询展开器 —— **还不知道任何域名**时的检索式从这里来 |
| ``research_strategy`` | 四个**有声明来源**的策略模块：从声明里读出计划上可写的那八格（语言/来源、页面与角色、决策题、获取方式）；读不到就抛，不画空格 |
| ``plan_preview`` | **编之前先看一眼**：硬歧义逐条点名并 hold（不编），软的照编但记下来；答得出的一句不省，答不出的逐条写明为什么（零 provider 调用是构造 —— 它拿不到端口） |
| ``acquisition`` | 统一抓取入口：``fetch_document(request, mode=auto/http/browser)`` |
| ``escalation`` | 抓取结果 → 是否升级到浏览器 |
| ``document_router`` | 解析文档时按优先级试多个 parser（只认端口） |
| ``evidence_ingest`` | 抓到的材料 → 一行**可被引用**的证据（正文一行、标题一行） |
| ``lineage`` | 一行**历史**产出属于哪个工作区：从本部署自己的输入里取证，证不出来就说证不出来 |
| ``evidence_refs`` | 一条结论挂的证据必须**真的存在**：引用 → 证据行的解析 |
| ``sitemap_frontier`` | 站点抓取的前沿：robots → 站点地图 → 页面优先级（只认端口） |
| ``site_crawl`` | 有界多页抓取：前沿说去哪，它说取多少、什么时候停、为什么停 |
| ``grading`` | 两条互不决定的判定：账号准入 与 路线定级 |
| ``decision_specs`` | 决策题面的**生产者**：把 profile 声明的题面编译成 ``DecisionSpec`` |
| ``shadow`` | 影子：多个决策后端跑**同一批冻结样本**，报告只报分歧 |
| ``pairing`` | 配对键 —— 把两支臂的候选落到**同一个单位**上 |
| ``qualify`` | 候选的资格判定 —— **共享的**判定引擎，两支臂跑的是同一个 |
| ``classification`` | 一页材料 → 四条轴上的**资产事实**（`organisation_kind` / `venue_environment` / `surface_class` / `country`）：判据是页面文字与主机名，词表住 profile，读不出来就是 `unknown`（正式结论，不是空串） |
| ``caps`` | 上限截断，以及**被截掉的那部分**（截断必须被记录） |
| ``discovery_run`` | ``discovery_scope`` 的跑法：发查询、取页面，写回一份**可重新喂进受理**的池子 |
| ``dispatch`` | 把一件工作单元交给外部执行器，并维持那条跨系统的映射（§6） |
| ``research_terminal`` | 研究件在外部执行器那里的终态 —— **权威在本仓** |
| ``inputs`` | 作业的 ``input_ref`` → 工作单元的 subject；已知账号与来源回执两类有产出者 |
| ``motherpool`` | 上游交进来的一份导出 → 一份**来源回执**：每一行都有一个说得出来的去向 |
| ``reconcile`` | 运维对账：把历史投影对齐，而**不开始任何新的付费工作** |
| ``scenes`` | 一次立项的生命周期：立项、改版（附带影响面）、批准生效、暂停、恢复、归档 |
| ``maintenance`` | 持续经营的调度：选出到期的格子、认领、跑、结账；**新来源缺口与真实场地新增分开数** |
| ``pool`` | 场地资产池：母池回执 → 池子里的行（**句柄怎么来**），以及一次新观察怎么折进已有的那一行（**哪些格子不许被抹掉**） |
| ``merge`` | 池子这一族共用的两个合并**原语**：``later_of`` / ``ordered_union`` —— 同一句判据只有这一处定义 |
| ``validation`` | 一次校验失败的**一行**说法（字段路径 + 消息）—— 逐行去向共用的那一句渲染 |
| ``relations`` | 责任关系表：一批判定怎么落进表里（**逐行给出去向**），以及一次新的判定怎么折进已有的那一行（键是 workspace/账号/场地/关系四段，**一个账号可以同时有多种责任**） |
| ``accounts`` | 账号池：一批账号怎么落进池子里（**逐行给出去向**），以及一次新的账号研究怎么折进已有的那一行（主语那一格**先看到的为准**、其余写法进别名；域名/地址/证据取并集；**「还没有判定」不覆盖判定**） |
| ``promotion`` | 发现期的候选 → 场地资产 → **canonical 账号**：`row-*` 与账号 slug 曾经是两个没有交点的身份空间，这一步把研究件的主语换成 `market_account.account_id`（**账号 id 由场地句柄派生**，因为句柄是回执自己那条去重判定的结果） |
| ``refresh`` | 一格到期工作**真的**怎么复核：读一遍本地记录、落一份盘点、报出读到什么；**一次 re-read 不是一次观测** |
| ``reuse`` | 获取前的 **ReuseDecision**：同一个许可范围里同一个 URL 只取一次，字节落进接收方自己的目录（**省下的是一次取回，不是一份磁盘**）；许可范围 = 工作区，执行在构造处而不在比较处 |
| ``continuation`` | 谁把上面这些推起来：一条落盘的**续跑意图** → 一次 tick（认领、驱动、按结果四选一地结账），以及「为什么此刻没有更多工作在跑」那一页；**驱动者不是进程，是盘上那一行** |
| ``run_admission`` | §B17 的**受理面**：一次提交拼成 ``RunSpec``（十二格里六格从盘上的 Scene 与上下文取，六格由调用方给 —— 其中 ``workstream`` 是 B01 拆开计划那一格 ``workspace/workstream`` 时新增的那一半，缺省 ``None`` 是「这一轮不在任何一次启动之下」，只有 ``start_workstream`` 那条路会给它），先比一次「这份计划是不是从这份上下文编出来的」，再交给两个实现共用的那份受理判据（模式今天受理不了、截止不在将来）；**受理不是执行** —— 它不派发、不开工单、不碰预算 |
| ``run_reconciliation`` | §B17 的**结束面**：一次 Run 结束之前，把它的 Delta（**这一轮自己的主张**）与它**真的**折进池子的那些行对起来 —— 九格里五格主张「池子里已有的那一行」，所以逐键问池子读不读得出来；折进去了而 Delta 没记、或记成「它没进池子」，两个方向分开报（处置一个要补、一个要改）。**账不平就不结束**：它指出的是报告上唯一读不出来的那种错 —— 池子变大了（纠错与 currentness 改变对「新增了几行」是零）而报告说没变 |
| ``run_followups`` | §B24 条件 4 的**待办那一半**：一次收盘之后，这一轮产生的那件「要人接一手」的事真的变成盘上一条 `ActionItem`，而这一轮 Delta 的 `actions` 那一格记下它的 id —— 判决在**盘**上，不在 Delta 里。三处承重点各在自己的位置上：**重放不多开一条靠的是插入失败**（判据是 `(workspace, action_type, scope_ref)` 那条**部分唯一索引**，不是先查后插；插入被拒之后把盘上那一条**读回来**，而读回正是重放能算出**同一批** id 的那一步——`stopping_outcome` 的指纹比的就是这串 id，凭空发一个新的会让同一趟收盘长得像另一趟）；**今天只有一条映射有生产者**（`application/contact_routes.py` 出的 `canonical_route_key` → `route-<16 hex>` 就是那条 `scope_ref`，另外三条 `ActionType` 开不出待办，理由逐条点名本仓已有的载体写在模块 docstring 里，不是「不在本仓库里」）；**没有转介路线的一轮一条都不加**（反例是测出来的：`tests/test_run_followups.py::test_a_run_with_no_referral_route_opens_no_item`） |
| ``run_increment`` | §B17 的**增量面**：第二轮开工前把已沉淀的事实读一遍，一格一个处置（复用 / 新 / 缺 / 过期）—— 四格里**三格的判据已经在盘上**（`aspect_states` 的 `NEVER_OBSERVED` / `DUE` / `CURRENT`，它逐字自称「读法，不是第二处判定」），第四格「缺」是本片加的那一根轴：`SOURCE_COVERAGE` 那一面会用**一个空盘点满足钟**（一条能当依据的来源都没有），而钟只会把它推得更远。**只补新/缺/过期**；已经结束的一轮在开工之前就拒 —— `admit` 的幂等让「重发同一个键」在受理上长得像一次成功 |
| ``run_preview`` | §B17 的**读数面**：一次提交**开跑之前**的那份读数 —— 四个门槛（这一批主体上有没有到期 / 这份请求走不走得通 / 这个键在不在盘上 / 这条链到没到 stop）按受理面**同样的次序**问**同一批函数**，不在这里再判一遍。报出这一轮的信封与「停在哪」、格子四档与它们的依据，而 `not_reported` 用**减法**（计划层那五项减去这一层答得出来的三条）。**它一个字节都不写**，也不花一次检索 —— 后一句是执行出来的：类型注解里连一个检索/抓取端口的名字都不许出现 |
| ``product_readiness`` | 产品资料与方案的**查缺面**（PSM §10「启动前返回结构化 missing/invalid/awaiting_approval」）：一份读数三张清单，每行说清**哪一条记录的哪一格、为什么**；主语不在盘上返回 `None`（三张全空与「什么都不缺」同形）；被引用的说法不是最新版时如实记进 `not_reported`（端口缺一个按版读的方法，**不拿最新版顶替**）；`price` 与 `region_variants` 明确不判 |
| ``launch_preview`` | B01 第五条关闭条件的**启动预览**（切片 b01-d）：某一版 Scene 如果现在被启动，还缺什么、等谁、下一步是哪一件事。它读的是**盘上已经写下的那一版声明**（声明本身由调用方读出来交给它），三张清单加一张答不上来的清单，主语不在盘上/是别人的时**拒绝整次读数**（不出一行缺口）；被引的产品版本与方案**整份**嵌在 `cited` 里不折平；`next_action` 是一个**属性**、五档。**启动授权前零外部调用**在这一层是构造：签名收不到端口、注解里不许出现检索/抓取端口的类名、一个 `except` 都没有 |
| ``scene_receipt`` | 一版 Scene 的**回执**：把它固定的那几格（区域 / 场景 / 产品 / 角色策略 / 来源计划 / 预算 / 复核节奏 / 触达节奏**连同标定状态** / 档案版本 / 摘要）写成一个 dict。**一个定义、两个出口**：`create_scene` / `add_scene_version` / `get_scene` 交出去的那一格，与启动预览的 `declared` 逐字是同一个函数 |
| ``not_reported`` | 「这一层答不上来的一项，**以及为什么**」那一格的值对象（`NotReported`，两个字符串，没有行为）。**一个定义、四个出口**：计划 / Run / 查缺 / 启动四份读数都用它。住在自己一个模块里是因为它**不能**住在任何一个出口旁边：它原来在 `plan_preview` 里，而那一层为了编计划 import 了 `research_strategy`（进而 `ports.acquisition`），于是另外两个只借这一格起名字的读数层也跟着把抓取契约拉进了自己的 import 闭包 —— 而它们正是「启动授权前零外部调用」的读数面（判据见 `tests/test_mcp_launch_is_offline.py`，它第一跑就给出了那条链） |
| ``effective_scene`` | B02 的**编译面**：把声明的那一版 Scene 编译成 `EffectiveScene` —— 逐格写出来源 / 凭什么 / 单位 / 语言 / 负责人 / 值摘要，优先级与「盖住了谁」逐条写出；对不上的读法进 `conflicts` 而**不取赢家**，没有的那几格进 `not_reported` 而**不是空着**（没有 Product 的纯市场研究照样编译得出，ProductFit 明确 unavailable）。`compile_effective_scene` 是一个**纯函数**（签名里没有任何端口），`preview_effective_scene` 只多一个读端口（`ProductCatalog`），`read_effective_scene` 只读 `EffectiveSceneStore` 并由自己判一次跨工作区。**零 provider 是构造**：判据 `tests/test_effective_scene_is_offline.py` 走 AST 算这一族的 import 闭包，白名单精确相等 |
| ``workstream`` | B01 的**启动面**：`start_workstream` 绑一版已批准的 Scene、落一条 `LaunchRecord`、建一个 `Workstream`，并让第一轮 Run 走**既有的**准入路径（那一趟由 handler 注入，这一层碰不到 `RunSpec`）；同一 `(场景, 版本, 幂等键)` 再交一次回同一次启动（不重建、不重置、不再开一轮），`scene_version` 与生效版对不上时**点名两版**。另有 `get_workstream`（读状态与「现在能不能派发」，不能时连原因一起给）、`list_workstreams`（空表要说成「本工作区没有工作流」）、`control_workstream`（带类型的暂停/恢复/改名 + `expected_revision` 的 CAS，恰好碰一行，配额那一格它够不到） |
| ``outreach`` | 外向链：筛选 → 写稿 → 冻结清单 → 登记授权 → 投递 → 对账；**默认停在待授权** |
| ``claim_withdrawal`` | 撤一条**已批准**的说法（承重点 3）：受影响集合按 `OutreachDraft.claim_ids` 那个**结构化引用**算，**不是关键词匹配**；「还没发出去」按 `OutboundAttempt` 的 `(draft_id, draft_version)` 与它自己的 `sent_or_unknown` 判（已发出的不撤回，放过时**连出处一起给**）；`held` 走 `model_validate` 出**新的一版**而不是就地改（`model_copy` 不校验那两格）。两半（说法的新版本 + 草稿的新版本）是**同一次** `store.withdraw` 的入参 —— 这一层只调一次，所以「全成或全不成」不是自觉，是没有第二种写法；把两半拆开的变异要去改适配器（判据 `tests/test_claim_withdrawal_store_contract.py`） |
| ``correspondence`` | 收信侧：压制 → 分类 → 落库；抑制是**查**出来的，游标只在事件落库之后才前进 |
| ``freshness`` | 一行产出的时效：把行的时刻换成断言、判一次、**重建成新的一行**；没有时刻锚点的行不判也不替它挑一个 |
| ``contact_routes`` | 路线与路线机会的**唯一**一趟生产者：材料 → 绑定 → 判时效 → 定级 → 落盘；一份坏材料只作废它自己，`CLI` 与服务面共用这一趟 |
| ``route_rejections`` | 拒收回执的**读**那一半：一张闭集全列的分布 + 按时序排好的逐条回执；只写不读的表与「算出来就丢掉」在报告上同形，这一层是「持久化」这句话的唯一执行器 |
| ``review`` | 收件箱的编排：``(工作区, 场景, 原因, 主体)`` → 一件**内容寻址**的待办（选项表不在这里另写一遍），以及一条决定折进去之前要跨的判据（批准要知道批的是哪一条提议、提议人**不是**批准人；一条提议要知道它挂起的那一版真的存在），外加 §9 那条恒等式与那条闸门 |
| ``feedback_preview`` | `preview_feedback_impact` 的**纯读数**：一条反馈若被采纳会改到什么 —— 跨了几档、哪几格受保护、下一件事是哪个动词。两张清单**分开给**：`not_usable`（**有理由的做不到**，逐条点名为什么）与 `not_reported`（**这一层答不上来**），因为把前者折进后者会让一条本来判得出的边界读起来只是「信息不全」。**零 provider、零写入是构造**：它拿不到端口，也不读时钟（`at` 由调用方给），判据 `tests/test_mcp_feedback_is_offline.py` 走 AST 算这一族的 import 闭包 |
| ``feedback`` | B23 的**反馈环编排**（§B23）：反馈事件那一行怎么进盘、候选与补丁两份**提案**怎么提起来、三份读数怎么读出来。三个承重点都在这一个文件里：**作用域的缺省是拒绝**（不给 scope 得到的是最窄那一档，本层不替它兜底，`resolve_scope` 明说「什么都没说」是拒绝而不是工作区）；**提得起补丁的不是「慢环那几类」**而是一张与 `DISPOSITION` 同键的表（`workstream_policy` 在内 —— 快慢的分界是**有没有 replay/evaluation**，不是「有没有版本」）；**批准不在这里**：`PolicyPatch` 里根本没有 approved 那一格，`standing_of` 的批准读数从**既有的** `ReviewCase` 推（`approve_policy_change`，要另一个人），四档而不是两档 |
| ``views`` | B24 的**工作池编排**（§5.2-§5.4）：存下的查询、监控、待办这三样怎么进盘、怎么读出来。三处承重点各在自己的位置上：**跑一份存下的视图零 provider、零预算、零 Run**（`run_saved_views` 只认 `ports.pool` 与 store，签名里连一个检索/抓取端口的名字都没有）；**「同一个账号出现在三个视图里是一个账号读了三次」**（`ViewRun.accounts_read` 与 `accounts_distinct` 两格并排报，一次按视图各读一遍 —— 「已经读过就不重读」那种优化删掉的正是这条判据）；**目标视图已被删掉的 Watch 如实报成那个样子**（`watch_card` 一处定义，`None`/`True`/`False` 三档，`None` 不折成 `False` —— 那会让「不指视图」与「指的视图不在」同形）。`put_watch` 是这一族里唯一要权限与预算的那一条（预算那一半在 handler 那一侧的 `set_ceiling`），而 `put_saved_view` 的签名里没有任何一格可以传进来一个账本 |
| ``watch_runs`` | §17 那条链的**第四格**：到期的 Watch → 一次 watch Run（前三格在 `views` 那一族里，而到期的请求在 `marketops/` 里一直没有生产调用方）。三处承重点各在自己的位置上：**一份执行不了的请求一次 `admit_run` 都不调**（`blocked_reason` 非空那一支直接记 `BLOCKED`、`subjects` 恒为空 —— 替它补一批主体就是把「它指着的东西没有了，没人知道」变成一个看起来成功的 Run）；**「拒 ≠ 没跑」在回执上是构造**（每一份请求一行、`ADMITTED`/`REFUSED`/`BLOCKED` 三档分开，另带扫描自己报的两份计数，`WatchRunRow.__post_init__` 钉住「有 run_id ⟺ 受理成了」与「未受理必有理由」）；**事实只有一处来源、而且都在盘上**（Scene 与 workspace 从 `scene_store` 的**生效版本**取而不是从参数取，幂等键是 `WatchDueRequest.idempotency_key` 这个推导属性，信封是这条 Watch 自己声明的 `ceilings`，展开视图用的是 `Watch.owner` —— 把 actor 做成参数就是把一次权限判定交给调用方）。它不读时钟、不睡、没有循环：什么时候醒来是 `interfaces/trigger_driver.py` 那一次醒来的事 |
| ``scheduling`` | B25 的**调度编排**（§8）：一条 ``ScheduleDefinition`` 怎么进盘、到点了怎么点一次火、清单怎么读出来。三处承重点各在自己的位置上：**幂等来自插入失败**（``fire_schedule`` 的顺序是 读那版声明 → 纯 ``due()`` → ``claim_fire`` 占位 → 动作 → 结结论 → 记事件 → 推进声明，而 ``claim_fire`` 回 ``recorded=False`` 时整条路停手、**不跑动作**）；**到期检查在任何 provider 调用之前**（``due()`` 是纯函数，`not due` 那一支一条端口都不碰、一个字节都不写 —— 判据数的是动作被调了几次，不是回执里写了什么）；**唯一一个触发驱动**（模块 docstring 那一节：全仓只有 ``tick`` 一处按 ``at`` 醒来，MCP 面与 ``Watch`` 都不自带定时器）。``_finish`` 是重放路径：结论已落而下一版还没写下时把收尾重做一遍（两件都幂等），**占位行不自动回收也不重跑动作** —— 分不出「正在跑」与「跑它的进程没了」，如实报出去 |
| ``notification`` | B25 的**通知编排**（§9）：一条事件怎么记下、一条订阅怎么进盘、一次投递怎么算、失败之后怎么重试。承重点两条：**一条业务变化只产生一条事件**（``book_event`` 只核对 ``event_id`` 真的是从 ``(workspace, dedupe_key)`` 推出来的 —— 去重键由**业务处置点**算，这一层不生成它，也不把「通知了谁」写进事件）；**投递失败只重试通知**（``retry_delivery`` 是唯一一条投递路径，判据按次序走 模式 → 免打扰 → 已接受 → 次数上限 → ``retry_after`` → 尝试，每一步都在盘上有一格；一张新收据是一次 INSERT，``attempt`` 进主键，**重试产生新行而不覆盖**）。``DeliveryState`` 七格里没有一格叫「已送达」—— ``DeliveryOutcome`` 只有 ``accepted`` 与 ``unknown`` 两个成员 |
| ``session`` | B25 的**会话绑定**（§7.1）：一个客户端会话的便利焦点怎么进盘。三条**否定**在这里各有一处可执行的落点：**不授予任何权限**（判据里没有 ``Role``、返回类型里没有一格能表示「这个会话能做什么」）；**不是任何 Account 或 Job 的外键**（``focus_refs`` 是不解析的字符串 —— 写一条指向「还没有的视图」的绑定是成功的）；**不参与业务幂等**（这个文件里没有一处 ``dedupe_key`` 或 ``fire_id``，同一会话再写一次是就地更新而不是第 2 版）。三个内容格在签名上**没有缺省值**，于是「每一次写入仍然自己说清对象与版本」不是一句话而是调用方写不出来别的形状 |

## 这一层与 `domain/`、`adapters/` 的边界

``pipeline`` **不构造任何 adapter** —— 全部依赖经 ``PipelineDeps`` 注入，所以它可以
在没有任何真实凭据的情况下用替身跑完整条链路。这条不是风格：它是「本地 pipeline
必须能脱离 Serper key 与飞书凭据独立运行」这条验收标准的落地方式，而一条写在
文档里的「应当注入」不会有任何执行器。

``adapters/`` 里那几个具体类型（``SqliteEvidenceStore`` 之类）在这里只作为
**注入进来的依赖**出现，不在本层被 import 后 new 出来。``document_router`` 是这条
规矩在 B7a 之后的第一个**本层成员**：它本来就是纯策略，只是原来与 ``default_router()``
共用一个文件名，于是被按接线的那一半判成了 adapter。拆开之后 ``pipeline`` 不再
import ``adapters/`` —— 见 ``application/document_router.py`` 的模块 docstring。

``sitemap_frontier``（Phase E）是同一条规矩的第二个成员，也是**同一个错误**换了个
方向：它整个文件只认端口，却因为名字里有「站点地图」被放在了 ``adapters/``。判据是
``adapters/__init__.py`` 开头那一句（import 之后要不要磁盘、网络或进程），答案是
不要，所以它搬到这里。**它没有走 ``ports/``**，这一点是有意的：这一层里唯一一个
实现就是它自己，而本仓的纪律是**等第二个实现出现再提取端口**（``ports.store`` 与
``ports.budget`` 都是这么来的）。

``site_crawl``（Phase E 第二片）是本层的**第一个消费者**，它把上面那个预测纠正了一次：
它确实需要一个替身才测得了（``tests/test_site_crawl.py``），但那个替身落在
``ports.acquisition.HttpFetcher`` 上 —— 一个**早就存在**的端口，前沿与 ``acquisition``
本来就是从这里取字节的。所以这里没有出现 ``ports/frontier.py``：那第二个实现没有
落在「前沿」这个接缝上，它落在更下面一层。这条要按原话读：等第二个实现出现再提取
端口，指的是**在它真正出现的那一层**提取，不是为编排再包一层。

``budget`` 是同一个规矩在**端口**上的样子：它只认 ``ports.budget.BudgetLedger``，
不认识 ``SqliteBudgetLedger``。上限的数值也不来自本层 —— 它来自冻结的 profile，
本层只负责「读出来、播种、按三段扣」。§8 要求上限不得被调用方静默扩大，而调用方
恰好就是能改环境变量、能改构造函数参数的那个人，所以那个数字必须住在本层改不了的
地方。

## 三个过渡包在 B6 消失

``orchestration/`` `search/` `acquisition/` 都搬完了：前者的三个模块、后两者各自的
残留（``queries`` / ``escalation``）都到这里。``search/fake.py`` **没有**跟着走 ——
它是零导入者的死代码（``docs/CURRENT_STATE.md`` 两处记过），删掉而不是搬走。
"""

from __future__ import annotations

from .cohorts import CohortPlan, load_accounts, validate_plan
from .metrics import AccountMetrics, RunReport
from .pipeline import AccountRunResult, PipelineDeps, ResearchPipeline

__all__ = [
    "ResearchPipeline",
    "PipelineDeps",
    "AccountRunResult",
    "AccountMetrics",
    "RunReport",
    "CohortPlan",
    "load_accounts",
    "validate_plan",
]
