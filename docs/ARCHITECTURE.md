# MarketOps 主架构：一个帮助 Member-A 持续经营市场的 MCP

更新：2026-09-27。**本文件是目标架构入口，不是已实现/已上线声明。**

完整增量设计见 [Member-A 自动化架构](MEMBER_A_AUTOMATION_ARCHITECTURE.md)，最终客户端/多 Session/监控与通知形态见 [插件与通知架构](MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md)，实施任务与关闭条件见 [执行计划](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md)，session、源码及技术研究依据见 [证据基线](research/MEMBER_A_AUTOMATION_EVIDENCE_2026-09-26.md)。

产品核对基线：`df689f52e0e07dc35b88af7a3a5dd48808008a75`（PR #6）；session 分析基线：`06cdae471f794e37877637d69ea93d1c020272a2`（example-session-repo PR #1）。2026-09-25 的 Research v1 验收仍是 **NOT_READY_FOR_REAL_USE**；本次没有重跑或改写它。

2026-09-27 进一步明确：[产品线、启动与Scene业务模型](PRODUCT_LINE_AND_SCENE_MODEL.md)和[采集、访问故障与供应商契约](ACQUISITION_AND_PROVIDER_DESIGN.md)是本轮专门规格；相关 B01/B02/B15/B23 与新增验收已合入同一执行计划。源码与公开资料复核见 [本轮证据说明](research/MARKET_MODEL_ACCESS_REVIEW_2026-09-27.md)。

## 1. 一句话产品定义

Member-A 通过一个 MarketOps 插件在多个 Codex/WorkBuddy/MUC session 中分别处理不同地区、对象或待办；这些 session 连接同一个 MarketOps MCP。**默认由 Member-A 手动发起一轮轮有界 Run**：服务先复用共享市场事实，再补充场地、组织、候选人、联系人与证据，并把 RunDelta 持久合回池子。Member-A 看结果、纠正业务知识、决定下一轮；自动化只在显式 `bounded_auto/watch` 授权后替人触发未来 Run。产品上下文按需要加入，真实外发属于另行授权的下一阶段。

**产品目标是减少人工抄名单、搬文件、催续跑、重复纠错，不是增加 Agent、工具、测试和报告的数量。**

## 2. 对外一个接口，内部明确分工

```text
Member-A / 获授权用户
  -> 一个 MarketOps 插件 / 主 Skill（首批一等公民：Codex、WorkBuddy、MUC/Hosting-Hermes 的薄客户端投影）
  -> MarketOps MCP（鉴权、作用域、类型化业务用例）
  -> application / domain
       |-- ProductVersion / Offer（按需）/ GeoScope / ScenarioProfile；ProductLine仅可选分组
       |-- Scene/Workstream、SavedView/Watch、ActionItem、Review Inbox
       |-- 资产、组织、责任关系、证据、唯一路线、Cohort、草稿
       |-- 持久 continuation / Schedule / Notification / 预算与恢复
       |-- 派发执行器 -> 独立受限 research workers
       |-- Serper / HTTP-PDF / Crawl4AI / Jev
       `-- SQLite 事务状态 + 不可变证据文件
```

MCP 是协议接口，不是 CC 本身，也不自动提供后台调度。调度由 MarketOps 服务中的 durable Schedule + 唯一受管 trigger driver 执行，MCP 只提供配置/读取/控制入口。控制端 session 与研究 worker 分开身份；客户端关闭后，已持久受理的任务、Watch 和 Schedule 继续存在。

派发执行器调度需要动态判断的有界研究，普通程序处理导入、获取、解析、去重、预算、投影与刷新。MarketOps 负责业务完成定义；软件代码 landing 不代表研究完成。供应商 adapter 不各自对外成为业务 MCP。Codex/WorkBuddy 的真实Member-A语料证明它们是当前主要工作面；MUC/Hosting是目标正式入口，三者共享同一服务端状态与权限，不各建业务后端。

研究执行增加一层 **Scene-aware Research Router**：客户端不选择供应商，服务端把 Member-A 的地区/语言/对象/场景/目标/可选产品上下文解析为 ResearchContext，并从 Geo/Locale、Scenario、Objective、Access、Product overlay 编译冻结的 EffectiveResearchPlan，再选择结构化来源、Serper、HTTP/PDF、Crawl4AI、Jev 与受限 worker。细粒度 context 可动态识别，长期 Workstream 不因每个细标签自动分裂；影响硬范围/权限的歧义必须人工确认。

## 3. 当前范围与未来范围

| 交付门 | 内容 | 本阶段要求 |
|---|---|---|
| R1 可用研究 | Scene → 发现/导入 → 身份/准入 → Account/Cohort → 联系路线/证据 → 草稿 | 必须消除人工中间桥并完成纵向验收 |
| R2 持续经营 | 自动下一批、唯一时间唤醒、到期重查、复用、预算/暂停、人工待办 | 在 R1 上增量接线，必须实际断线/重启/变化验证 |
| R3 授权外联 | manifest/授权、发送、回信/抑制、销售交接 | 设计保留；当前不启用、不作为 R1/R2 外部验收门 |

当前辅助决策只要求 **Jev**；Laya、ServerOS 对接和 Docker 打包均 **DEFERRED**。不再用旧“六项全部必需”合同阻塞当前发布。未来 Laya 接现有 DecisionProvider，仍需兼容/质量验证。

## 4. 不变的技术与业务边界

- `domain/application/ports/adapters/interfaces/security` 沿用，不另造平台。MCP/CLI/控制器调用同一用例，handler 不持有另一套业务规则。
- Scene 长期存在，Job/Cohort 有限且固定输入/版本。事实可授权复用，Scene 的准入、角色、等级与优先序不混入事实。
- SourceRecord、Asset、Account、责任关系、人物/部门、邮箱路线是不同对象；同域/同名/同一场地句柄都不是组织已核实的充分条件。
- A/B/C/D 需要 literal 公开邮箱及适用绑定；无邮箱是机会/信号，X 不进正常主表。SOP 允许 A→B→C→D 候选顺序，C/D 不包装成关键联系人。
- 原始工件与提取文本分开，有 exact locator/来源/时间/转换版本。hash 和非空不证明语义正确；unknown 不代替 false，也不自动放行。
- 预算先预留后结算，未知外部用量保留 uncertain；重复事件有幂等键，陈旧 attempt 不覆盖新结果。数据库与外部调用不宣称天然 exactly-once。
- 工作区、对象与 assignment 作用域由服务端校验。网页/模型/跨线程文字不能成为修改权限或批准发送的来源。
- 网络访问与浏览器导航有范围/资源边界；不绕登录、WAF、验证码或其他访问限制，不探测/猜邮箱，不把供应商凭据交给研究 worker。
- 默认 research-only，禁真实外发。未来任何授权窗口仍须有明确对象/内容/数量/期限、撤销及每次动作前的安全重查。
- 受管服务独立于聊天；配置和持久数据可注入。现有受控令牌模式不冒充 OAuth，实际 SDK/协议/客户端组合需带版本验收。
- 历史业务来源、证据与验收保留；不能改旧 FAIL、拼接不同构建的绿灯或用一次运行估计整体成熟度百分比。

## 5. 这次调整的重点

**产品线不再是使用 MarketOps 的必需前置对象。** ProductLine 只作为可选分类/汇总维度；产品资料按版本共享。真正承重的是共享市场事实库 + Scene/Workstream。Scene 是可独立批准/预算/暂停的长期市场任务；小地区、批次和角色排序差异优先用 Segment/Cohort/SavedView，不因组合标签机械复制 Scene。启动研究直接从目标/范围/预算预览进入持久 Workstream/Run，不要求先维护产品线生命周期。

**Run 是当前最重要的操作单位。** Workstream 长期存在但不默认自行运行；`manual` 是当前默认，已批准 Run 内部可断线续跑到终态，完成后只产生 RunDelta 和下一步建议。只有有界连续授权或 Watch/Schedule 才能创建未来 Run。共享池因此通过多轮研究、纠错和刷新逐步增长，而不是一次性“爬完整个市场”。

Member-A 的业务纠正同样进入服务端：对象事实走 append/supersession，当前任务规则走 scoped policy version，可泛化经验先成为 ExperienceCandidate。一次自然语言反馈不直接升级为全局规则；生效 scope、影响、来源、批准和回滚都可查询。

母池支持Asset-driven与Account-driven；候选人、任职事实、规范职能、责任影响力推断、公开ContactPoint与Scene路线等级/排序分开。产品原件放受管资料存储，产品版本/主张授权在服务端；Git不自动收私有资料。不同产品Scene共享授权范围内事实，但评价/文案/结果归因独立。

Serper负责发现，HTTP/PDF先获取，Crawl4AI只解决允许的渲染问题，Jev做有界辅助语义，受限 worker做开放路径研究，代码管状态/预算/权限。Cloudflare Ray ID不证明封IP；访问受限的解决路径是精确诊断、有限重试、修复自有批准配置、合法替代来源/站点许可和保留进度，不是保证绕过。

角色策略可以依据经验调整，但不得用raw reply rate替代客户资格或产品事实。低样本/观察窗口/分母和选择偏差须显式，策略提议经评估/适用批准后版本化生效；当前R1/R2只接研究与纠错信号，真实外联效果仍需R3授权数据。


先关闭 R1 接缝：正确身份与官网归属、范围准入、唯一路线、产品 Cohort、独立草稿与拒收回执。再把已批准规则、Review Inbox、持久续跑、真实刷新和复用接成 R2。

不新建平行 Policy 平台：扩展已有 SceneVersion/影响计算；不新建第二个调度器：仅增加缺失的业务 continuation 与时间唤醒；不把 Jev DecisionRecord 当人工裁决。

**本地证据盘点不等于外部事实更新；同一 run 的所有 key 相等不等于找对客户；关闭所有人工判断不等于自动化成功。**

## 6. 文档导航与冲突处理

| 材料 | 负责什么 |
|---|---|
| [SOURCES](SOURCES.md)、冻结 SOP / 获批准变更 | 业务规则和授权来源 |
| [增量架构](MEMBER_A_AUTOMATION_ARCHITECTURE.md) | 本轮具体数据/控制/执行设计 |
| [执行计划](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md) | B00–B30统一实施（含B01/B02/B15/B23）、G0/G1/G2功能验收与G3效果比较 |
| [产品线/Scene模型](PRODUCT_LINE_AND_SCENE_MODEL.md) | 资料、Offer、市场划分、任职/路线、结果学习与跨Scene共享 |
| [插件/多Session/通知](MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md) | 一个插件+一个MCP、多客户端并发、SavedView/Watch/Action、Schedule、Notification、飞书通道 |
| [采集与供应商](ACQUISITION_AND_PROVIDER_DESIGN.md) | Serper/Crawl4AI/Jev组合、故障归因、限速/复用、访问边界与连接验收 |
| [证据说明](research/MEMBER_A_AUTOMATION_EVIDENCE_2026-09-26.md) | 本轮读取范围、追溯表的限制与一手研究 |
| [MCP_SERVICE](MCP_SERVICE.md)、[MCP_PRODUCT_LOOP](MCP_PRODUCT_LOOP.md) | 已有工具契约，精确名称以代码 catalog 对照；设计中的能力不自动成为已实现工具 |
| [DATA_MODEL](DATA_MODEL.md)、`domain/`、`contracts/` | 当前数据模型与代码导出 Schema；实施时同步，不手改导出 Schema |
| [DEPLOYMENT](DEPLOYMENT.md)、[开发基础设施](DEVELOPMENT_INFRASTRUCTURE.md) | 已确认部署约束；不是这次启动/轮换/上线授权 |
| [2026-09-25 验收](RESEARCH_V1_ACCEPTANCE_FINAL_2026-09-25.md)及更早验收 | 对应构建的运行事实，永不被设计文档改判 |
| [2026-09-23 审查](ARCHITECTURE_REVIEW_2026-09-23.md) | 早期需求到代码的追溯及后续补记，按 revision 阅读 |

较早主架构全文保存在 Git 历史；本入口替换其过期实现盘点，不修改冻结来源和历史报告。需求分析能发现矛盾，不能自动产生新的业务授权。