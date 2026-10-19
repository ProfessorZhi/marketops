# MarketOps 架构审查：Member-C 业务反馈 → 代码缺口 → 验收（2026-09-23）

这是一份有界的代码/文档审查记录与实施建议，不是新的运行验收。主设计以 [ARCHITECTURE.md](ARCHITECTURE.md) 为入口；工具目录仍以 [MCP_SERVICE.md](MCP_SERVICE.md) §2–3 为准。

审查基线：`ProfessorZhi/marketops@<sha-redacted>`（本轮文档提交之前）。此次读取 GitHub 源码、既有验收记录、冻结业务材料，并直接核对 `上游私有档案库` 中 Member-C 会话的相关原文。没有连接部署进程、没有跑测试、没有调用计费研究工具。

## 1. 来源与权威

业务原文：

- [英国天然草坪 AcmeCorp SOP](../sources/party-group/sop/uk-natural-grass-acme-sop-v1.md)，2026-09-10 v1.0；blob `<sha-redacted>`。
- [Member-C 会话的本仓冻结快照](../sources/member-c-private/marketops-messages.jsonl)，2026-09-20 至 2026-09-23 的 67 条相关消息；blob `<sha-redacted>`。
- 上游原文：`上游私有档案库（路径已脱敏）/member-c/messages.jsonl`；本次读到的 blob 为 `<sha-redacted>`，核对至 2026-09-23 12:03 的相关消息。

`上游私有档案库` 是长期档案权威；`sources/` 是冻结快照；本文是派生解释。没有改写原始消息、附件、历史来源哈希或 SOP。Member-C 的一般场景经验补充产品设计，但不能悄悄撤销英国天然草坪 profile 的硬排除。技术论文另见 [研究补充](research/MCP_RELIABILITY_REVIEW_2026-09-23.md)。

### 1.1 本次明确没有取得的证据

没有读取 Member-C 所链接维护表的实际单元格，也没有解析两份邮件模板 DOCX 的正文；只核对了聊天引用与快照清单。因此不宣称已审计该表、模板质量或回复率。Member-C 对模板成效的评价是业务反馈，不是带分母的验证结果；模型使用评价也不是受控实验。

## 2. 从聊天到设计的追溯表

消息位置是原会话的 `message_position`，不是 Markdown 行号。用 message_id 定位，避免归档行数变化造成错配。

| 原文依据 | 可确认的需求/反馈 | 架构落实 | 不能外推成什么 |
|---|---|---|---|
| 09-20，pos27，`<message-id-redacted>` | Member-C 用 Python/OSM 形成母池，再让 AI 找人/岗位邮箱 | 可导入母池；来源与资产独立于联系人研究；不要要求她把母池先手工变成完整 Account | OSM 是唯一来源，或 OSM 场地 ID 就是 Account |
| pos33，`<message-id-redacted>`；09-23 pos44–45 | 细化任务可能改善效果，但还需测试 | 按业务对象和依赖拆任务；默认一个 Account 一件研究任务，复杂项再分解 | Agent、工位或 Scene 越多越好 |
| 09-23，pos39，`<message-id-redacted>`；pos59，`<message-id-redacted>` | MCP 接 Hosting/飞书；Claude Code 加工具/Skill 执行 | 一个外部 MarketOps MCP；客户端不安装全部供应商；Skill 是方法，不是服务事实库 | 再建独立聊天平台，或每个供应商交付一个业务 MCP |
| pos54，`<message-id-redacted>` | OSM 行可能只有 ID，缺场地名 | 原始候选先保存，再补全名称/地点/身份；未解决项有去向 | 把内部 slug 当搜索身份，或要求用户编造官方域名 |
| pos56，`<message-id-redacted>` | 母池精准；扩大网页搜索 | 多源发现、跨来源证据补全、官方关系核实；分别记录来源质量与联系路线质量 | “全网”是可证明穷尽的范围，或任何命中都能当证据 |
| pos67，`<message-id-redacted>` | 泛邮箱多、结论经不起核对、权限/乱码交付问题 | A/B 与 C/D 分开；独立证据复核；状态和输出由结构化回执派生 | 泛邮箱全部无效，或换模型即可解决工程交付问题 |
| pos70，`<message-id-redacted>` | 重复性工作希望更省钱 | 确定性去重/渲染/状态控制；小模型只做受限语义判断 | 用弱模型掌管发送权限和抑制名单 |
| pos71，`<message-id-redacted>`；pos78，`<message-id-redacted>` | 场景不同，高价值联系人不同；学校与高尔夫岗位不同 | 版本化角色矩阵；岗位优先序、路线等级和证据强度分开 | 学校 AD、COO 或 golf owner 仅凭头衔自动成为 A |
| pos81，`<message-id-redacted>`；pos85，`<message-id-redacted>` | 模板/产品按场景变化 | 场地事实与 ProductFit/WritingPolicy 分开；不同场景使用独立已批准配置 | 给英国天然草坪任务混入人工草或高尔夫 |
| pos89，`<message-id-redacted>`；pos90–91 | 产品受季节/场景影响，未来可能扩展品类 | 保留有生效期的产品适配配置；复用事实，按策略重评 | 清扫、平沙、捡球已经是 MVP 或已有验证的产品能力 |

## 3. 当前代码已经有什么，缺的究竟是什么

下列“代码存在”仅指本次静态阅读；“历史 FAIL”只归属于报告注明的那次运行。不能由此推出当前线上健康或缺陷已修复。

| 能力 | 本次证据 | 结论 |
|---|---|---|
| MCP 主入口与角色目录 | [server.py](../marketops/adapters/mcp_server/server.py)、[catalog.py](../marketops/security/catalog.py)、[pyproject.toml](../pyproject.toml) | 已有 MCP 实现与 15 个唯一工具：controller 可见 9 个，researcher 可见 8 个，其中 2 个共享。未接线部署仍可显式返回缺口，不等于所有部署都具备完整能力 |
| 依赖边界 | [application/__init__.py](../marketops/application/__init__.py) | 已采用 domain/application/ports/adapters/interfaces；不是需要从头建一个服务框架 |
| 已知 Account 输入 | [inputs.py](../marketops/application/inputs.py) 的 `known_account_dossiers`、`SubjectResolution` | 已传递身份 dossier，不再仅把内部主键交给 worker；缺身份不能靠猜测补齐 |
| 母池回执输入 | 同文件 `_NO_PRODUCER[JobInputKind.SOURCE_RECEIPT]` | **明确缺少可再投喂的抓取回执生产链**；现有抓取/EvidenceStore 不是该回执生产者 |
| 发现范围输入 | 同文件 `_NO_PRODUCER[JobInputKind.DISCOVERY_SCOPE]` | **明确缺少把展开、查询、抓取、投影写回串起来的 runner**；`openweb.py`、`pairing.py`、`qualify.py`、`caps.py` 已存在，不能说“发现零件全无” |
| 授权互操作 | `server.py` 模块说明、`AuthSettings` 组装 | 本地令牌校验与角色控制已存在；但发现元数据指向不存在的授权服务器，是明确记录的接入缺口，不是认证默认开放 |
| 工作区边界 | `catalog.py` 的 `Principal(name, roles)` | 该对象本身不含租户/工作区身份；不能仅凭角色隔离宣称多租户隔离。此次未做全量越权审计，也不据此断言已发生越权 |
| 飞书表格投影 | [feishu/client.py](../marketops/adapters/feishu/client.py) 的 `ADAPTER_STATE` | `interface_only`；配置凭据不等于接线。与“Hosting 通过 MCP 在飞书对话里使用”是两件事。**2026-09-24 更新**：现为 `partially_wired`（`handoff` 写入路径已实现并读回行 id/版本/条数；真实写入 `BLOCKED`；`route`/`opportunity` 缺业务键）—— 「配置凭据不等于接线」这一条不变，且现在多了一条：凭据齐备也**不等于**被授权写入 |
| 六项集成 | MCP 组合与现有 adapter 目录；[历史验收报告](MVP_ACCEPTANCE_REPORT.md) | 已有工程实现与部分真实运行记录；不得恢复旧盘点里“Jev/Laya/MCP 不存在”的描述，也不得把这些证据合并成完整 MVP 已通过 |
| 并发、隔离、判定落盘与状态汇总 | 验收报告 §C/§F/§H/§N 等，运行 revision `<sha-redacted>` | 报告明确保留 FAIL 与 queued/终态不一致等历史问题；是否在当前构建消除，需要新回执，不能改旧报告 |

便于复核的关键 blob：`inputs.py=<sha-redacted>`；`server.py=<sha-redacted>`；`catalog.py=<sha-redacted>`；`feishu/client.py=<sha-redacted>`。

> **后续状态（2026-09-23 补记，不修改上表）。** 上表的「本次证据」一列是**该基线 revision 上的读数**，此后代码动过。已经变动的是**授权互操作**那一行：发现元数据指向不存在的授权服务器这个缺口已关闭（`resource_server_url` 显式设为 `None`，不发布任何发现文档；受众与有效期落到本层判，诊断走 `--check`）。其余各行以本次补记时的代码为准，见 `docs/ARCHITECTURE.md` §9.2 与 `MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md`。这一节保留原始读数，理由与 §3.1 对 `CURRENT_STATE.md` / `MVP_ACCEPTANCE_REPORT.md` 的处置相同：**冻结的是当时读到了什么，不是它现在是什么**。

### 3.1 文档的真正问题

`CURRENT_STATE.md` 是刻意冻结的 Phase A 盘点，不是应当覆写的错误文件。`MVP_ACCEPTANCE_REPORT.md` 同样保存具体运行事实。这两份保持原样。

需要修正的是当前阅读入口：把“目录已实现”“部署已接线”“某次实测通过”“尚未实现的设计”分开；不能一边让工具目录测试以规格为准，一边笼统称整个目录还不存在。本轮更新主架构、MCP 规格状态说明和 docs 导航，不改历史读数。

## 4. 实施顺序与关闭条件

这是建议的工程实施序列。除文档外，本次没有实现以下工作；不得因本审查已提交就把条目标成完成。P0 表示下一轮研究/客户端接入前优先核对，不代表本次授权上线。

| ID / 优先级 | 具体交付 | 现有落点/责任边界 | 关闭条件 |
|---|---|---|---|
| D0 / 本次文档 | 一个 MCP 的定位、分层、来源追溯和状态分离 | ARCHITECTURE / MCP_SERVICE / docs 索引 | 文档提交、链接与目录对照；不计入运行 PASS |
| E1 / P0 | 可导入的 SourceReceipt + 原始候选 → 身份补全 → Account 的闭环 | 从 `application/inputs.py` 接现有获取/存储；新增必要领域契约而非另起平台 | 用含只有 OSM ID、重复、同名、缺域名和排除项的 fixture；每行有处置，重放不重复建 Account；授权真实样本能从 MCP 进入下游 |
| E2 / P0 | bounded discovery runner | 连接 `openweb/pairing/qualify/caps` 与既有预算、证据、派发端口 | 无需用户手造 Account/域名；输入 cutoff、实际查询、抓取证据、候选写回与预算同一 run 可追溯；越界/耗尽返回明确原因 |
| E3 / P0 | MCP 客户端认证与版本兼容清单 | 现有 server/auth/guard 与 Hosting 配置边界 | 真实客户端完成所选认证流程；发现元数据无虚构 AS；权限错误可诊断且不泄漏凭据；SDK/协议/客户端精确版本有记录 |
| E4 / P0 | 对历史运行 FAIL 做当前版本复验 | 现有 Quay dispatch/reconcile、job 状态与 DecisionRecord | 两个独立 worker 真正重叠；无跨任务证据混用；判定可按 id 读回；终态/进度/用量相符。每项新回执注明 build，旧 FAIL 保留 |
| E5 / P0（多用户前） | 明确实例工作区与对象访问范围 | principal、jobs、evidence、decisions/report 的读取及 worker assignment | 单工作区部署明示信任范围；共享多工作区前具备服务端解析的身份和对象授权。对别人的 job/evidence/decision/报告及缓存命中分别做拒绝测试 |
| E6 / P1 | 业务质量评估集与可复现报表 | grading/evidence/metrics/report，沿用 SOP | 难例按 Account/来源分组切分；研究者不兼任唯一裁判；A/B、C/D、机会、hold、blocked 分开；修订能追溯；无结果与未运行不混淆 |
| E7 / P1 | 场景/产品/季节配置演进 | 既有 profiles + 版本化 Scene 指引 | 新场景独立准入和角色矩阵；已有任务仍用冻结版本；只改变产品优先序时可重评而非全库重抓；未知表面不被猜成真草 |
| E8 / P1（授权接线后） | 飞书投影可靠交付 | 现有 FeishuSink；不要建第二个机器人后端 | 结构化输入、UTF-8/转义、幂等写、行 id/版本/数量回读；失败重试不重复、不冒充成功；本地报告不因投影不可用而丢失 |
| E9 / P1 | MCP handler 保持薄接口 | 现有 application 用例与 mcp_server handlers | 若处理器承载可独立复用的业务编排，逐项迁回 application；工具名/权限/结果保持兼容，原有测试不减弱。不要仅为行数拆层或先建空端口 |
| E10 / 后续产品阶段 | 经批准的个性化草稿、发送与回信闭环 | 独立授权的 command/sink，研究角色不可达 | Manifest 冻结内容/收件人/附件；逐封安全门、未知发送结果对账、退订/回复停止和人工交接；单独验收与授权 |

E1 与 E2 是两种入口，可独立形成有用交付，不能互相等待导致持续空转。E3 也可与研究管线完善并行；不需要为此改 Hosting 源码。现有六项集成的完整研究 MVP 验收要求仍然保留，不因实施拆片而取消任何一项。

## 5. 质量验收：直接回应 Member-C 的问题

先固定 cohort/source receipt、profile、输入证据、研究预算、模型与宿主版本，再评估。两个 runtime 使用不同模型的既有体验不能被解释为单独的模型效果；shadow 比较也不能被当作生产省钱。

| 指标 | 明确分母与解释 |
|---|---|
| 母池补全质量 | 已核实身份的唯一候选 / 去重后接收候选；同时报告原始行数、重复、排除、待补全和失败，不隐去困难输入 |
| A/B Account 覆盖 | 至少有一条独立复核合格 A/B 的唯一 Account / 冻结研究 cohort 中的合格 Account；未完成项单列，不能只对成功项计算 |
| 路线准确率 | 独立抽查中邮箱、Account、职能和当前性都成立的路线 / 被抽查路线；报告抽样方式与样本量 |
| 高价值路线新增 | 按 Account + 场景 + 规范化邮箱及历史修订去重后的新增 A/B；同一泛邮箱反复发现不算新成果 |
| 每百镜像页产出 | 同一 run 新增合格路线 / 实际镜像页 × 100；与浏览器网络子请求数分开 |
| 单位产出成本 | 已知总成本 / 新增合格路线或独立复核 A/B；没有产出或价格未知时显示 unavailable，并保留已知用量 |
| 交付一致性 | 报告版本、记录数、证据链接、CSV/JSON 哈希与回读结果一致；不是“模型说已写入” |

最小难例集应覆盖：OSM 缺名、同名不同地、同域多个机构、同一 Account 多场地、所有者/运营者/维护者并存、旧 PDF 新下载、staff 离职/缺失、目录中他机构邮箱、仅有泛邮箱、仅有姓名无邮箱、公开访问受阻、页面恶意指令、重复结果、超预算、worker 崩溃以及多语言编码。

没有业务批准和真实标注时，不编造通过率阈值。测试中的合成 fixture 与真实业务 cohort 分开；协议测试可以全绿而业务评估仍未通过。自然没有 A/B 是允许的结果，不允许因此造路线；任务确实执行过、证据充分且停止理由真实，才可把 no_route 作为研究结果。

## 6. 本轮结论

MarketOps 不缺一个新名字或新编排平台，缺的是把已有的受控研究内核与 Member-C 的实际母池入口、角色优先序和可靠交付接成同一条可追溯链。交付形态维持一个 MCP；首要补齐两类输入生产链、真实客户端认证契约和当前构建的失败复验，再做质量与多场景演进。

## 7. 收口：§4 那张表在本构建上关了几格（被测构建 `<sha-redacted>`）

被测构建是 `<sha-redacted>`；本节所在的提交只多出 `docs/` 的改动，没有一行代码差异
（`git diff --name-only <sha-redacted>..HEAD | grep -v '^docs/'` 为空）。把这两件事分开写，
是因为「读数来自哪个 revision」在本仓是一个反复出错的地方 —— 混在一起的后果是
一个正确的读数被记到错误的构建上。

§4 是一张**计划**表，它逐条写明「本次没有实现以下工作；不得因本审查已提交就把条目标成完成」。
这一节是它的对账，写在**实现之后**，逐格给出裁决与可重跑的读数。原始读数落在
`/home/operator/artifacts/marketops-closeout/`，汇总报告是
[`MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md`](MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md)。

裁决只四种：**已关 / 未关 / 本阶段不做 / 阻塞**。别把「机制写了」读成「已关」——
下面每一格后面那句话才是这格的重量。

| ID | 裁决 | 依据（可重跑） |
|---|---|---|
| **E1** SourceReceipt 闭环 | **已关** | 真跑到 `receipt-57bf400076f21b22`：`raw=25 resolved=25 merged=0 excluded=0 pending=0 failed=0`，六个数相加等于原始行数。形状不对的输入退 2 并说清是形状问题。**未关的旁支**：一份**真实上游**导出在本机不存在（`inputs/` 下三份是受理输入，不是母池），本轮的池子由发现自己产出，按 SOP 属补充证据 |
| **E2** bounded discovery runner | **已关** | 真跑花掉 20/20 检索（上限来自冻结 profile 的 `search.max_queries`，不是调用方参数）：200 命中 / 82 主机 / 25 进池 / 50 行证据，`stops = ["budget_exhausted","cap_reached"]`。三件事在回执里分开：额度用尽 ≠ 候选枯竭，达到上限 ≠ 候选枯竭，未确认 ≠ 空 |
| **E3** 客户端认证与版本兼容 | **已关** | 受控带外预置令牌（格式 3），五格被判；**没有虚构 AS**（`resource_server_url = None`，无 RFC 9728 路由）。实测：无令牌 401；**受众不符 401**（同一份 HEAD 绑到 8789 时四条调用全拒）。协议 `2025-11-25`、SDK `mcp==2.2.0` 已记录。未做也不声称完整的 OAuth 发现/授权/受众往返 |
| **E4** 历史 FAIL 复验 | **未关** | 四条子判据里三条已关（隔离 6 条测试；判定持久化 8 条；作业状态有了说得出口的 `state_note` + `progress` + `research_terminals`）。**未关的是最字面的那一条**：§H 要求在**一次真实双 worker 运行里**观察到 `in_flight >= 2`。根因（正文缺 `## Touches` ⇒ 派发器保守串行）已修，并在 25 条**真实**任务正文上量到 `BATCH (25-wide, concurrent)`（阴性对照 `BATCH (0-wide, concurrent)`）—— 但那证明的是「正文形状不再是障碍」，不是「两个 worker 真的重叠过」 |
| **E5** 实例工作区与对象授权 | **已关（单工作区部署内）** | 链是 `传输身份 → 主体 → 工作区 → 对象授权`，`_require_scene` 是七个调用点的单一收口（其中结算那条**写**路径是本轮补的，并做过变异验证：删掉它，一条别的 Scene 的租约真的会被结算进盘）。**共享多工作区之前仍需**：`evidence` / `contact_route` / `route_opportunity` 三张表没有 Scene 血统，一个部署只服务一个 Scene 是今天唯一在挡这件事的东西 |
| **E6** 质量评估集与可复现报表 | **未关** | 评测 harness 只存在于分支 `marketops/v1-serper-discovery`（tip `<sha-redacted>`，merge-base `<sha-redacted>`，`branch...HEAD` = 22 / 129），**不在本构建里**。另外它的零分母口径与本仓不统一：那边返回 `None` 渲染成 `—（不可求值）`，本仓活体回执用的是 `{"available": false, "blocker": …}`（该分支 `marketops/experiments` 对 `unavailable` 零命中） |
| **E7** 场景/产品/季节演进 | **未关** | 本轮一字未改 `profiles/uk-acme-natural-grass`，冻结那份仍绿。但演进面本身没有推进：没有新增 profile 版本，也没有改 Scene 的装载路径 |
| **E8** 飞书投影可靠交付 | **阻塞** | `ADAPTER_STATE == "interface_only"` 由测试钉住；活体 `get_capabilities` 的 7 项集成里**没有 feishu**（配置了凭据 ≠ 接线）。解封条件：一个**被授权**的 sink，且必须在报告交付成功之前回读写入的行 id、版本与条数。本阶段 `send_authorized = false`，不做。**2026-09-24 更新**：解封条件的后半截已在**隔离**测试里兑现（写后读回行 id/版本/条数，条数不符即判失败），但前半截未兑现 —— `feishu_write_authorized = False` 且真实写入 `BLOCKED`，所以本格**仍是阻塞**，隔离的通过记为 `sandbox_pass`，不是真实厂商的 PASS |
| **E9** MCP handler 保持薄接口 | **已关** | 工具名/权限/结果与冻结时逐字一致（9 控制器 / 8 研究员，两侧不重叠）。隔离不止在列表上：controller 调 `search_web` 得到 `Unknown tool: search_web` —— 研究员工具对控制面**不存在**，不是「存在但拒绝」。**本格是持续的**：下一次往 handler 里塞编排时，它自己不会红 |
| **E10** 经批准的草稿、发送与回信 | **本阶段不做** | 只建边界，不发送。活体 settings 报 `research_only = true`、`send_authorized = false`；研究员角色**没有**任何发送凭据 |

### 这一节要连着读的三句话

1. **E4 那一格没有关，而且它是最容易被读成已关的一格。** 根因修掉、真实正文量到并发、阴性对照也在 —— 这三件事加起来仍然不等于字面判据。判据没取到就是没取到。
2. **E6 不在本构建里，这不是「以后完善」。** 它在另一条分支上且口径不同；把它收敛进来是一次独立的、有自己判据的动作。在那之前，「可复现评测 harness」这一条在本构建上的状态是**缺**。
3. **§4 那张表本身不改。** 它是当时的计划与承诺，改它就抹掉了「当时承诺了什么」。本节的裁决是叠加在它上面的对账，不是替换。

