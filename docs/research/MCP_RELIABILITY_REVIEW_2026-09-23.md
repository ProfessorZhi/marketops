# MCP 可靠性研究补充（2026-09-23）

性质：技术研究输入，不是业务需求来源、实现声明或 MarketOps 测试结果。配合 [主架构](../ARCHITECTURE.md) 与 [本次代码/需求审查](../ARCHITECTURE_REVIEW_2026-09-23.md) 阅读。

## 1. 检索与使用边界

本次通过 SciSpace 检索“多步网页研究中工具型 Agent 的失败、证据校验、独立评估与确定性控制”，再核对下列论文的 arXiv 原始摘要页。阅读范围是摘要与元数据，不是全文实验复现。SciSpace 的索引类型不作为论文已经同行评审的证明。

论文说明的是可借鉴的失败模式和评估方法，不能证明某个模型在本项目更好，不能推出 MarketOps 的提速、成本下降、A/B 产出率或回复率。业务授权、场景排除与路线等级继续来自冻结 SOP，不由论文决定。

## 2. 与本项目直接相关的研究

| 一手来源 | 与本项目相关的观察 | 本次采用的设计推论 | 不采用的推论 |
|---|---|---|---|
| [MCP-Bench: Benchmarking Tool-Using LLM Agents with Complex Real-World Tasks via MCP Servers](https://arxiv.org/abs/2508.20453) | 评估同时覆盖工具使用、执行轨迹与任务完成；协议可调用不等于复杂任务成功 | 分开验收 MCP 契约、实际执行、业务证据与交付，不用一次 tools/list 代替全链路验收 | 接入 MCP 就能自动提高研究准确率 |
| [WebDS: An End-to-End Benchmark for Web-based Data Science](https://arxiv.org/abs/2508.01222) | 端到端网页数据任务暴露弱证据关联、重复行为与走捷径等失败 | 从原始输入一直追到交付结果；单列重复搜索、无效查询、泛邮箱和未解决项 | 照搬论文任务成功率作为 MarketOps 的验收阈值 |
| [Where LLM Agents Fail and How They can Learn From Failures](https://arxiv.org/abs/2509.25370) | 单个根因可能沿规划、动作与系统环节传播；需要定位失败发生在哪一层 | 保存 assignment、attempt、工具回执与 result 的关联；针对根因修正，不只让模型重试或道歉 | 引入新的通用 Agent 编排框架，或让模型自己宣布修复成功 |

这些是工程推论，不是论文直接测试过 MarketOps。尤其不能把 Member-C 对 Codex/terra 与 DSH/DS 的使用反馈改写成通用模型排行榜：模型、宿主、提示、工具、预算和输入并没有被同时控制。

## 3. MCP 协议与 SDK 必须分开固定版本

一手协议资料：

- [MCP 2025-11-25 Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)。该版本区分 MCP 资源服务器与授权服务器，要求相应发现信息及令牌受众校验。一个本地令牌校验器不等于一个可被发现并完成授权流程的授权服务器。
- [MCP 2026-07-28 Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)。该版本改变了 HTTP 会话/流机制；不能把较早版本的连接、会话和恢复约定原样视为所有版本的要求。

代码审查基线中的 [pyproject.toml](../../pyproject.toml) 声明 `mcp>=2.2,<3`。这是依赖范围，不是一次部署实际安装版本，也不是已经验证的协议版本列表。此次未运行服务、未测客户端，也未修改依赖。

采用以下发布纪律：

1. 部署清单记录精确 Python SDK 版本、服务 build、客户端及其版本、实测协议版本和 transport/auth mode。
2. 以该组合实际支持的生命周期、工具发现与错误语义为准；不凭最新规范页面宣称当前服务已经兼容。
3. 升级协议作为独立兼容性变更，重测发现、认证、tools/list、调用、错误、断线后查询和作用域隔离。
4. MarketOps 的 durable job 是应用层对象；不依赖某版协议的 session id 或流恢复标识维持存在。协议任务扩展也不替代 SQLite/Quay 的业务状态与对账。

## 4. 对这次架构的具体约束

**一套小而明确的业务工具，而不是把供应商 SDK 全部透出。** 控制客户端提交研究、读取报告；研究 worker 只拿被分配任务的研究工具。未来资源或提示模板可减少上下文重复，但不创建另一份事实库，也不是当前目录已经实现的能力。

**证据引用可验证，不等于语义判断已正确。** 哈希、字段模式、literal 邮箱出现位置由代码校验；账号、角色与时效性仍需业务判断与独立抽查。检索到的网页内容是数据，不能成为改权限、改 Skill 或取得发送权的指令。

**分开成本优化和业务授权。** Jev/Laya 可做已定义的有界判断；去重、预算、任务重放、报表编码和未来发送安全门由确定性程序承担。模型便宜或自信，都不能降低授权与证据要求。

**评估真实目标，不奖励表面忙碌。** 有效 A/B 关键路线覆盖、证据正确率、母池补全质量和可复现交付，比页数、工具调用次数与文本长度更接近 SOP 目标。没有标注、分母或真实回执的指标应报告 unavailable，而不是填 0 或推测百分比。

## 5. 本次未做

没有复现论文；没有比较实际模型性能；没有升级 MCP SDK；没有对生产 Hosting、Jev、Laya、Quay 或邮件渠道做任何操作；没有根据外部技术资料改动业务原文或启用新场景。
