---
name: marketops-research-retrospective
description: Capture observable research bottlenecks, successful methods and corrections during each stage; synthesize a scoped evidence-linked experience pack and replay proposals without automatically rewriting Skills, policy or permissions.
---

# 调研流程复盘与经验包

此Skill在阶段交接、卡点/工具换路/人工纠正、暂停或最终交付时触发。研究进行中只追加短记录，结束时做一次归纳；不是另一个常驻Agent，也不读取私人推理链。协议见 [EXPERIENCE_PROTOCOL](../../references/EXPERIENCE_PROTOCOL.md)，历史依据见 [MEMBER_A_SKILL_ANALYSIS](../../references/MEMBER_A_SKILL_ANALYSIS.md)。

## 经验事件的持久化（v0.2.2）

若当前使用本地插件模式，先确认插件已配置：`marketops_setup_status` 应为 `CONFIGURED`
（未配置时先按 marketops-setup 问那两个路径问题，不要猜数据根）。每次关键阶段结束应检查
`marketops_events` 对本 Run 的事件；本地 init 与 Excel-safe CSV 导出已自动产生日志。
已经直接调用的厂商 MCP 不会自动出现在日志里：优先用 `marketops_note` 按实际工具回执补记
（只记非敏感 ID/状态与简短文本）；否则**明确报告 event_not_captured**，不能假称自动保存成功。
跨 Run 的阅读视图由插件自动维护在用户选定的 `Research_Experience.md`，不要再让用户逐目录去找
`experience.json`；那份 JSON 仍是单 Run 的审计原件。

在每次手动交接、暂停或研究完成时，使用 scripts/research_event_journal.py draft 生成**未审批** experience.json 初稿，并用 scripts/experience_pack.py validate 检查格式；只有掌握实际回执与证据时才能由Agent补充根因/经验假设，仍不得自行批准SOP/Skill更新。日志、经验初稿和阅读版都应落在客户数据根中，不在代码仓库。

## 输入

本轮Brief/规则/工具版本、operation回执、实际资料/提交/读回定位、失败与成功样本、人工反馈、成本/耗时（缺失为null，不补0）。只用授权范围内可见资料。客户原件留本地；用脱敏局部定位符，不复制会话全文、邮箱名单、cookie、密钥。

## 每次卡点如何记

在**阶段卡点、工具换路、人工纠正、暂停或完结**时，调用 `marketops_note` 写下一条记录：
`run_id / note_id / stage / category / problem / action_taken / observed_result /
hypothesis / evidence_ref`。这一步会自动刷新用户定期收取的那一个
`Research_Experience.md`，所以卡点是"记下来"而不是"留在会话里"。

`category` 是闭集，按协议枚举；技术错误、业务规则不明和授权暂停不是同类。常用几条：
`contact_found_not_stored`（发现联系人但未入库）、`field_template_pollution`（字段模板污染）、
`artifact_without_delivery`（只生成了文件未交付）、`source_blocked`、`provider_blocked`、
`evidence_missing`、`human_correction`。先写 observation，再写 hypothesis；
**没有复现或对照不得称根因已确认**，`hypothesis` 就是待验证的经验候选。

只写脱敏 ID 和简短文本：`marketops_note` 会拒绝邮件、URL、Key/Token/cookie 字面值，
也**没有**放隐藏思维链的字段。`evidence_ref` 必填，指向可回读的回执/资料。

**交付时的结构缺口是自动记的，不是你写的。** `marketops_export_xlsx` / `marketops_handoff` 会用**与工作簿同一份
快照**跑一次结构检查，未满足目标时就以 `note_id = goal-<snapshot 前缀>` 追加一条 hold 事件与 note，类别是
`other`，内容只含状态码与缺口计数。看到这类记录时：不要当成自己的观察去补充根因，也不要因为后续快照达标
就以为它应该消失（历史不抹）。它只说明结构合同未满足，**不代表事实、SOP 或覆盖率结论**。

用一次事件更新代替每分钟重复记录。成功也记录，特别是合法换源、复用材料、修复抽取和回写校验。注明它是否独立样本，不把同一父任务的子Agent当多次验证。

## 归纳

1. 按卡点机制+阶段+工具版本分组，不能只凭类似文字合并。
2. 产生少量条件化经验：when / do / avoid / applicability / limitations / source incident refs；一次成功为draft，不是通用定律。
3. 明列反例、尚未证实原因、未做试验。没有效果计时就不承诺节省比例。
4. 每个改进提案带基线、改变的唯一机制、固定评估集、holdout案例、验收指标/停止条件、风险和回滚。
5. 遇到SOP/权限/预算变化只提议，须独立批准。不要自动编辑既有Skills，不自动创建Issue、发邮件或向GitHub上传客户日志。

## 输出

按模板生成 `experience.json`，运行 `scripts/experience_pack.py validate ...`；需要阅读版时运行 `render ... --output <Git外新文件>`。两者是候选知识/审查报告，不是执行系统。业务结果、未完成项和store snapshot仍按handoff单独交付。

本版经验包的promotion固定candidate_only。测试回执可以附上，但自动验证只检查结构与引用，不证明事实或因果。人批准采纳后在独立维护PR里发布新Skill/规则版本，下一次同类任务加载已批准版本。即使API调用成功，若未落库/未交付，也必须记录business_incomplete。
