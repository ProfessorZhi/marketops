---
name: marketops-research
description: Complete a bounded, human-led market research task from brief through venue/account and contact evidence to a verifiable result, using four-provider routing and producing a scoped retrospective experience pack.
---

# MarketOps Research Assistant：业务、工具、复盘三层

先读 [BUSINESS_PLAYBOOK](../../references/BUSINESS_PLAYBOOK.md)。历史依据和不能推广的结论在 [MEMBER_A_SKILL_ANALYSIS](../../references/MEMBER_A_SKILL_ANALYSIS.md)。本插件不是后台自动调度器，技能存在不代表四家MCP已接通。

## 一次用户任务内完整推进

1. `marketops-intake`读取原始目标、当前Brief/Scene/SOP/授权；[DATA_HANDOFF](../../references/DATA_HANDOFF.md)确定唯一store与schema，不让不同Skill各造CSV/数据库。
2. 读取已有池子和资料，找下一处缺口。只按需展开 `marketops-source-discovery`、`marketops-venue-account`、`marketops-person-research`；证据核验贯穿全程。
3. 通过 `marketops-provider-router` 选择Serper/Crawl4AI/TinyFish/Jev的实际已连接能力；原始资料、观察、模型判断、业务评级分别记录。
4. 每一步结果提交同一store并读回，保留每个输入的处置。若只有离线pack，则使用其固定字段与单写者纪律，不冒充统一MCP数据库完成。
5. 对照当前SOP判资格和联系路线；缺证据、归属矛盾、采购等级等规则缺口保留hold/unknown。Agent填reviewed_by不构成业务授权。
6. 当前Run内可执行的步骤继续推进，无需用户机械地逐页说“继续”；真正需要批准/裁决时停该对象或方法，其他独立获准对象可继续。不无声扩大范围/费用，不启动下一轮无限任务。
7. `marketops-excel`按同一数据快照导出，`marketops-handoff`回读校验并交付：业务结果、来源、未解决项、实际费用/未知与下一步。没有XLSX能力交付CSV并明说。
8. `marketops-research-retrospective`在过程卡点/人工纠正/方法变化时短记，交付时归纳经验包。候选经验不自动变规则，复盘不替代业务交付。

## 经验自动存储的实际边界

当前 Lite 记录器在客户选定的 MARKETOPS_RESEARCH_DATA_ROOT下保存 SQLite 操作事件。本地 init 与 Excel-safe CSV exporter 已加自动事件钩子。每次通过独立厂商MCP完成的操作，若客户端允许调用本地 recorder，则按实际工具回执记录 operation/status/receipt ID；不能自动拦截未经过我们的 Research MCP 的调用。缺少记录必须报告 observability_gap，不能说“已自动保存”。

每次暂停/交接/完成调用 marketops-research-retrospective，以实际日志起草经验候选，人工验证后才可更改正式业务SOP。先保存本轮业务结果，不能只交经验而漏交客户成果。

## 两套产出必须区分

业务：场地/组织母池→候选人/岗位→真实公开联系渠道→按SOP评估的联系路线→报告。证据池从第一份材料开始并行。

学习：operation与交接记录→观察/假设→卡点/成功经验→回放评测提案→人工采纳→后续独立版本。不是记录隐藏思维链，不把一次试用包装成全局成功。

禁止默认发送、表单提交、充值、绕访问控制、猜邮箱、全市场覆盖声明或秘密读取。Skills是方法层，OS隔离、硬预算、统一存储及受管MCP需要真实实现和验收。
