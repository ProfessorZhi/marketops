# Member-A 会话分析 → 业务 Skills → 可验证经验

状态：2026-10-15 知识层增量设计与组装；不是新的业务 SOP，不是全流程运行验收。

## 1. 依据与可信边界

本次读的是 **已有分析结果与规则文件**，没有登录东京、没有复算原始会话，没有把历史分析误称为客户当前状态。

- 分析仓固定 revision：`ProfessorZhi/example-session-repo@06cdae471f794e37877637d69ea93d1c020272a2`（audit-v2，不能用 main 的旧计数替换）。
- [干预图谱](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/MEMBER_A_INTERVENTION_MAP.md)：78 个归纳干预，POLICY 22、GUARD 23、HUMAN_DECISION_REQUIRED 21、DETERMINISTIC_WORKFLOW 7、AGENT_ASSISTED 3、UNKNOWN 2。
- [25 项职责记分卡](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/MEMBER_A_TAKEOVER_SCORECARD.md)：用于定位人工负担；其 AUTO=0 是严格审计标准下的结果，不是 Codex/WorkBuddy 没自动化，也不是今天 MarketOps 的完成率。
- [正确模式](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/GOLDEN_PATTERNS.md)：采用 GP-02 至 GP-08 的适用机制，不照搬整篇。
- [方法与覆盖](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/METHODOLOGY_AND_COVERAGE.md)：样本为 Member-A 在 2026-08-14 至 09-24 的历史。文件数、线程数、摘要数不是同一分母；经验不能代表所有客户。
- 产品基线：`ProfessorZhi/marketops@ae7ea40c6273f72327a2ca09653a988957aa2269`。业务分级以该基线 `profiles/uk-acme-natural-grass/grading.yaml` 的原义为准；历史状态描述不代替最新代码验收。

**分层：** 会话分析是问题证据；批准 SOP 决定业务语义；本文件提出工程做法；真实试用决定其是否有效。三者发生冲突时登记并交给业务所有者，不由 Agent 静默合并。

## 2. Member-A 实际需要的不是更多窗口

分析中的主线是建母池、读网页、识别责任组织、抽取公开邮箱、岗位判断、质量评估、交付，再到独立授权的触达与回复。我们本轮只做研究和可审核交付。

45/78 条干预被分析者归为 POLICY 或 GUARD，只能说明这批被归纳的干预中规则与校验重要，不能推导节省工时比例。对应的产品机会是把 Member-A 重复说的话变成稳定的输入合同、晋升条件和回读检查，而不是把更多循环交给模型。

## 3. 可追溯业务规则清单

| 编号 | 历史依据 | 本版落点 | 实际应检查什么 | 不推广什么 |
|---|---|---|---|---|
| B01 | INT-010、INT-056；职责1 | intake / BUSINESS_PLAYBOOK | 明确优化的是场地覆盖、目标机构还是有效联系路线 | 不把“邮箱优先”作为所有需求的默认目标 |
| B02 | INT-033；职责1、22 | venue-account | 场地、组织、责任关系独立；一组织可管多场地 | 不能按 hostname 当场地主键 |
| B03 | INT-052、INT-073；职责4 | person-research | sales motion、应用场景、角色矩阵与版本 | 某次“卖设备的是竞品”不排除所有渠道任务 |
| B04 | INT-038；职责5 | evidence / person-research | A/B/C/D 是路线等级，账号资格另一轴 | 不把“有名字但无邮箱”当 B |
| B05 | INT-025、INT-047；职责2；GP-04 | evidence / crawl4ai | 正文可用、关键字段可定位、真实资料可回读 | HTTP 200、hash格式或 completed 不等于事实正确 |
| B06 | INT-054、INT-074；职责3、15 | handoff / experience | 每条提取结果的落点或拒收理由；导出后读回 | 输出文件存在不等于数据已交付 |
| B07 | INT-008、INT-058、INT-071；职责14 | excel / shared-data contract | 同一快照、同一分母、按对象唯一键统计 | 不把网页数、邮箱数、机构数互相相减 |
| B08 | INT-009；GP-03、GP-04 | source-discovery | 说明样本范围与未覆盖类别 | 样本批不是全市场；零命中不是市场不存在 |
| B09 | INT-002、INT-013、INT-019；职责17 | research主Skill | 授权 Run 内继续下一项；每步落盘 | 不靠定时重复“继续”冒充后台执行 |
| B10 | INT-028、INT-041；职责18、20 | provider-router / experience | 暂停原因、累计预算、重试依据 | 暂停不是重开同名Run重置额度 |
| B11 | INT-063；职责13 | Review Queue | 人给裁决，Agent回写并读回结果 | reviewed_by 自填文字不是可信审批凭据 |
| B12 | INT-069；职责23；GP-05 | experience | 候选经验追加、版本化、复验、人工采纳 | 不自动覆写既有Skills或历史报告 |
| B13 | GP-08 | source-discovery / serper | 已知资料→公开名录/分类索引→真实链接，记录来源收益 | 历史网站吞吐、命中率不是现在的SLA |
| B14 | GP-07、GP-09的抽取诊断部分 | provider-router | 分开死域、解析错误、JS内容、真实访问限制 | 不采纳其中绕WAF的手段，不制作绕过指南 |
| B15 | GP-06 | provider-router / retrospective | 样本单位、合格产出定义、预算、停止方法的理由 | 30家、5%/10%等历史阈值不得自动成为全局默认 |
| B16 | INT-024；GP-05 | handoff | 不可消费产物保留HOLD，新包更正并通知下游 | 不篡改密封交付，不静默丢行 |

## 4. 当前英国规则中特别需要保留的差别

[固定规则源](https://github.com/ProfessorZhi/marketops/blob/ae7ea40c6273f72327a2ca09653a988957aa2269/profiles/uk-acme-natural-grass/grading.yaml)：

- A：关键决策/demo推进职能 + 字面公开业务邮箱 + 强绑定。
- B：关键一线运营/维护角色 + 字面公开邮箱 + 强绑定。
- C：目标账号绑定的官方业务入口，业务语境按该SOP给定集合。
- D：目标账号绑定的真实公开泛化官方邮箱，低优先储备，不冒充关键联系人。
- X：需要分别说明无关/排除、规则未知、证据不足或冲突，不能混成“没有客户”。

该基线明确登记 `strongly_bound_named_procurement_role` 缺口：具名采购角色即使有邮箱，也没有被这些规则明确分到 A/B/C/D。保留 `RULES_UNKNOWN` 并请业务所有者裁决，不能为顺滑体验擅自把它放进A。上述规则只属于这个profile，其他客户/场景重新绑定已批准规则。

## 5. 经验采纳而非照搬

GP-08 的标题称“三层”，正文实际列四项；本版保留其来源，不拿标题重构一个假统计。GP-06的具体收益阈值与GP-01/09的访问绕过倾向不作为插件通用规则。来源中的 AUTO、最高ROI及效率数字不是本版实测。我们采用“诊断输入和解析→选择合法替代源→记录边界”的机制，而不是把历史所有做法都自动变成技能。

交付目标：可执行的业务步骤 + 可用工具选择 + 有证据的经验候选。完整统一MCP/数据库与四Provider端到端仍须单独实现和实测。
