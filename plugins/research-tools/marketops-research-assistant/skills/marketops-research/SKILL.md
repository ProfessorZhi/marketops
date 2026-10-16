---
name: marketops-research
description: Single entry for one human-initiated MarketOps candidate research Run; resume persistent state, load only the needed stage Skill, deliver audited XLSX and collectable experience without formal contact promotion.
---

# MarketOps Research — 唯一主入口

## 开始 / 恢复

先调用 `marketops_setup_status`。只有 CONFIG_REQUIRED 才加载 `marketops-setup` 并询问数据目录、经验文件两个绝对路径；已配置不再问。缺少实际 MCP 工具就报告能力缺口，不能用聊天中的假回执代替。

新任务加载 `marketops-intake`，明确范围、目标、SOP版本和预算，调用 `marketops_start` 并按需读取 [统一合同](../../references/WORKFLOW_CONTRACT.md) 保存结构化 Brief。已有 Run 先读状态，不能默认重新爬取或重复建库。

## 一个小批次循环

1. 调用 `marketops_next(run_id)`，只加载其 `recommended_skill`；以当前 `target_ids` / `missing_facts` 为工作批次。
2. 使用同一 Run 的 `marketops_status` 按 kind/entity_key 过滤读取。只有真实数据缺口才加载 `marketops-provider-router`，由它选择一个已授权能力及对应 Provider Skill。
3. 所有来源、实体、岗位、联系方式和处置用 `marketops_propose` 写回，保留真实 artifact_ids。业务工作不以手写 CSV 交接。先检查源内容，再提交，再读回。
4. 一个批次后重新调用 `marketops_next`。READY_FOR_CANDIDATE_REVIEW 只代表结构齐全，不代表事实核验或正式业务通过。HOLD/排除/重复有明确原因；不要无限重试或扩大范围/费用。

未知 Brief → intake；未知来源 → source-discovery；来源已有、对象未提取 → venue-account；缺材料 → evidence；组织已有、缺岗位或联系渠道 → person-research；结构齐全 → research-acceptance。不要每次都加载所有 17 个 Skills。

## 交付 / 暂停

按 `marketops-research-acceptance` 核验后加载 `marketops-handoff`，主路径调用 `marketops_handoff` 获取真正的九表 XLSX、配套 CSV/manifest、经验候选与 `experience_file` / `experience_refresh`。只需中间工作簿时用 `marketops_export_xlsx`，不要重复生成最终研究快照。

交付时同时报告：实际研究产出、`goal_assessment` 的目标缺口、未完成的业务审查、经验刷新状态。运行成功不等于目标达成；HOLD可以如实交付部分结果，但不能说完整市场研究通过。用户定期只需收取选定的 Research_Experience.md。

卡点/换路/人工纠正时按需加载 `marketops-research-retrospective`，用 `marketops_note` 留下观察、处理和结果，假设单独标记。不自动修改 Skill/SOP。Lite 只保存候选，不能正式 A/B/C/D 晋升、发送邮件、提交表单或自行授权收费调用。
