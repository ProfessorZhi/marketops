---
name: marketops-research
description: Drive one Member-A-style human-reviewed public market research run end-to-end through one MarketOps Lite MCP, evidence and candidate pools, Excel/CSV handoff and experience retrospective.
---

# MarketOps Research Assistant — 统一业务入口

先按需读取 references/MEMBER_A_SKILL_ANALYSIS.md、BUSINESS_PLAYBOOK.md、QUALITY_GATES.md、MCP_TOOL_CATALOG.md，遵循场景正式SOP。**本版本只有候选阶段的存储与导出，不具备正式 A/B/C/D 业务晋升或外联。**

## 在一个已授权的 Run 内连续推进

1. 使用 marketops-setup 核对本机 MCP/Skills 是否真正可用，marketops_capabilities 显示实际配置。不能把 key_configured、SDK installed 或工具已列出等同真实API或业务通过。
2. marketops-intake 固定目标场景、地理范围、客户资格、当前SOP版本和允许费用；marketops_start 创建 Run。已存在的 Run 先通过 marketops_status 读取记录，不能每次再从零爬。
3. 发现阶段通过 marketops-provider-router 选择 SerpApi 或 Serper；也可按需用 TinyFish Search。每次明确 provider，禁止未经用户批准自动切换另一家收费路径。搜索摘要与 Google Place ID 只进入 source candidate，不能直接证明场地责任或邮箱。
4. 采集阶段优先复用原始材料；按资源选择已安装 Crawl4AI 本地爬虫/Cloud、TinyFish Fetch。只有实际授权的公开 HTTPS 站点与读取动作允许访问；不绕登录墙、验证码或权限控制。材料需有真实出处、哈希和可回读 artifact_id。
5. 使用 marketops-venue-account 与 marketops-person-research 分离场地、组织、责任关系、人员、岗位和字面公开联系渠道；通过 marketops_propose 逐一保留候选、证据引用、未核实与冲突。可使用 Jev Choice 对**已拥有证据**的候选做语义辅助，但结论不是事实，也不能覆盖SOP。
6. 通过 marketops_status 对实际行数、来源材料、stage和用户任务目标复核。缺证据、业务规则冲突、人员归属不明进入review/hold；不能“为了完成报表”猜测邮箱或把候选标为verified。
7. 仅在已批准同一Run内继续可独立进行的工作；人类保留范围、预算、账户资格规则、等级冲突与外联等权力。不能自动扩大预算、启动无限下一批或发送邮件。
8. 完成当前可完成部分时调用 marketops_handoff（一个操作同时产候选CSV与经验初稿，全部保留到数据根），随后用 marketops-excel 与 marketops-handoff做材料核验和真正工作簿（若宿主有电子表格工具）。**没有XLSX能力只能说CSV。** 经验 Skill 在暂停/卡点/完结时记录明确的观察、假设、真实回执与反例，绝不自动更新正式Skill/SOP。

## 两条并行数据流

业务数据：Brief/Run → SourceCandidate → VenueAsset/Account候选及责任关系 → Person/RoleCandidate → ContactPoint候选 → 待核验联系路线 → 有来源的CSV/Excel研究结果。要成为**正式联系路线**，必须以后接入MarketOps主项目的可信SOP/授权晋升引擎，本 Lite 工具不提供此权力。

经验事件：每个受管工具调用的开始/结果及材料落地 → research-events SQLite → 失败/阻断/人工纠错 → experience.json候选 → holdout回放与人工采纳。统一五家Lite MCP内的调用自动记事件；**独立直连的官方TinyFish/Crawl4AI MCP不在本地自动记录范围内**。

不可把“工具调用成功”“保存一个JSON文件”“MCP启动成功”当成“完整市场任务已完成”。在真实业务E2E通过前，这是一份试用工作流，不是已交付成品。
