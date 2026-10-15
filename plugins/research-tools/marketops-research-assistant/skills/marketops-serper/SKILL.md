---
name: marketops-serper
description: Use authorized Serper Search, Maps and Places to discover market sources and venue/account candidates with explicit geography, query provenance and downstream fetching; not a Serper official MCP implementation.
---

# Serper：从需求找对来源

MarketOps自编使用Skill。官方API：https://serper.dev/；账号/Key申请见上级providers/serper.md。入口须已配置为官方API、受管MarketOps工具或经审核社区MCP，不能把本Skill当安装器。

输入：Brief、国家/语言/子区域、来源类型、存量索引、可消费额度。

- 查询围绕“来源/名录/目标类型/区域”，先找有结构的协会/市政/学校/运营商目录，再找独立场地。地区参数与查询语言分别记，不用地名字符串代替授权范围。
- Search发现官网/文档；Maps/Places补位置/官网候选，店铺分类和摘要不证明天然草、运营权或采购责任。
- 批量前检查接口实际schema和分页口径；每次请求保留query、locale、时间、实际credits/unknown及原始回执。同query重试绑定原操作，未知消费先查。
- 不按主机折成唯一场地；source去重与实体去重分开。目录的多个页面和多个实体都保留来源。
- 按真实名称、地址、已确认外部id产生候选匹配；有冲突进入review，不推断官网。
- 下一步将选中URL交抓取Skill。未抓原文不能生成已核实邮箱或人物任职。
- 零命中记录检索覆盖和输入有效性；查询失败不是市场不存在，不无限猜词。

输出：sources/SourceReceipt与候选实体引用、尚未覆盖范围、下游读回结果。复盘按query/source family记录有效实体收益，不只统计搜索结果数。成功率分母带单位，失效域名、配额不足、schema变化分别归因。
