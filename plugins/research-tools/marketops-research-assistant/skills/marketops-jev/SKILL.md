---
name: marketops-jev
description: Apply TypeSafe Jev Choice, Noul and Score to evidence-backed market qualification, role relevance and candidate selection; retain probabilities, abstain on missing context and leave policy and execution outside the model.
---

# Jev：把小块语义判断交给专门模型

MarketOps自编业务使用Skill，参考官方：https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md 。官方Skill是方法知识，不等于已连接MCP；实际可调用能力来自已配置TypeSafe SDK/API或MarketOps受管decide工具。先读当前官方文档/已装SDK类型，访问失败写明版本未知，不猜接口。

输入：已取得的资料/证据引用、实体关联、规则版本、候选集合、明确问题。缺证据先补，不让Jev臆测一家公司或一个人的事实。

- Choice：在已定义的候选角色/来源/实体解释中选一项；允许none/unknown，不能省略真实可能答案。
- Noul：一个是否成立的条件；near 0.5是yes/no不确定，不是“中等强度”，不要杜撰独立confidence字段。
- Score：按可观察的有序描述评价相关性/证据支持程度，不把score当成交概率或原始计数。
- 独立问题共用同一state可一批提问；后题依赖前题结果/新资料则分轮。题目ID不承载必须让模型知道的语义，含义写在instructions和criteria。
- 输入中把身份、原职位、文档时间、事实、推断和目标场景分开。选择已有原文span后由程序复制值，不生成猜测邮箱。
- 保存原始分布、实际模型/题面版本、证据快照、用量和判断结果；语义未变的权重/显示调整不必重问。
- 阈值按获准业务数据验证；confidence集中不等于真实性，也不等于授权。规则、去重、计算、预算、正式晋升用代码/人，不靠Jev批准。

失败归因依次检查：证据缺失、候选集漏项、题意/规则冲突、模型误判、组合代码bug、服务错误。现有英国profile的采购等级缺口不能让Jev自行填平。低置信度/冲突进入review，保留原判断，复盘生成待验证题面改进而非自动发布新标准。
