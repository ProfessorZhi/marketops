---
name: marketops-provider-router
description: Select one actually available, authorized provider capability for a concrete current-stage gap; load only that provider's Skill and preserve budget, source lineage and failures.
---

# Provider router — 按需加载

**触发：** 当前 Run 存在具体外部数据/提取/语义判断缺口。输入是 Brief、marketops_next 的 missing_facts、已存材料和允许费用，不是整个工具库。

1. 先复用适用的已存材料。调用统一 MCP 的 `marketops_capabilities`；配置、SDK安装、认证和真实调用成功是四种不同状态。首次选择 Crawl4AI local 时先看 `browser_cache_hint`：`CHROMIUM_CACHE_MISSING_OR_INCOMPLETE` 表示先修本机安装/HOME隔离路径，不要连续预订十几次失败请求；`CHROMIUM_CACHE_PRESENT_NOT_LAUNCH_VERIFIED` 只是线索，不是浏览器已能启动的证明。
2. 未知URL：SerpApi或Serper的 `marketops_search`，也可选择获批TinyFish search。有URL：获批Crawl4AI local/cloud或TinyFish fetch。已有证据的语义辅助：Jev Choice。去重/计数/硬规则不交给模型判断。
3. 选择后只加载相应 marketops-serpapi / marketops-serper / marketops-crawl4ai / marketops-tinyfish / marketops-jev Skill，细节才读 [Provider路由](../../references/PROVIDER_ROUTING.md)。不要一次加载所有Provider文档。
4. 统一 MCP 的 TinyFish 当前只有search/fetch，浏览器Agent需另行发现并获授权的官方连接器；Jev当前只有Choice，不能据知识文档宣称Noul/Score已接入。**宿主内置免费WebSearch/WebFetch和外部直连官方MCP不在本地自动调用账本/去重链内**，不得因受管账本显示无重复就声称所有搜索都没有重复。已实际使用时只记录脱敏的操作ID、来源类型、获得的材料和失败状态，不复制查询全文或原始页面；避免用直连工具反复绕过同一受限站点。
5. 请求前明确provider、operation_id、目标、边界、调用上限；没有批准不自动收费，不把next建议当成授权。返回后确认真实材料ID、候选写入与readback，不只报告“工具跑了”。
6. 区分网络、权限墙、提取、存储和业务缺口。已接受job但本地超时，先查原job，不能默认未计费或重复创建。不得用换工具绕过同一访问限制，也不得静默切到另一家收费Provider。
7. **去重与访问墙：** 同 Run 同一请求指纹即使更换 operation_id 也不得自动重试。遇 `SOURCE_CHALLENGE_PAGE` / `SOURCE_ACCESS_BLOCKED`（包含 Cloudflare/验证码/403/429）停止本 Run 同站点进一步抓取；不切换代理、账号、浏览器伪装或另一家 Provider 去绕挑战。先查已保存材料、公开开放数据/合法可读的官方文件或**不同机构**的公开来源；仍无证据则 HOLD 并注明未访问。网络失败只代表访问未知，不是没有客户。环境缺浏览器先诊断并修复路径，但不能换 ID 盲重试，必要时另行批准与记录。
8. 目标已完成、方法明确阻塞或额度耗尽就停止该方法；已授权的独立对象可以继续。卡点/换路/修正通过marketops_note留下观察、动作和结果，假设不冒充根因。

**输出/退出：** 实际来源/材料/判断引用和持久化处置，返回调用方阶段Skill；阶段是否可推进由marketops_next的结构检查和后续证据验收共同决定。统一数据合同见 [WORKFLOW_CONTRACT](../../references/WORKFLOW_CONTRACT.md)。
