---
name: marketops-serper
description: Use authorized Serper Search, Maps and Places to discover market sources and venue/account candidates with explicit geography, query provenance and downstream fetching; not a Serper official MCP implementation.
---

# Serper：从需求找对来源

MarketOps自编使用Skill。官方API：https://serper.dev/；账号/Key申请及项目费率核查见包内 [五家 Provider 申请与鉴权指南](../../references/PROVIDER_ONBOARDING.md)。入口须已配置为官方API、受管MarketOps工具或经审核社区MCP，不能把本Skill当安装器。

输入：Brief、国家/语言/子区域、来源类型、存量索引、可消费额度。

**实际工具调用：** 在已保存结构化 Brief 且当前项目已授权搜索费用时，通过统一 MarketOps MCP 的 `marketops_search(provider='serper', query=查询词, country=国家代码, language=语言代码, run_id=本轮Run, operation_id=本次唯一操作ID, mode='web', limit=10)`；需要地图/新闻/图片等时选择已在`marketops_capabilities`中可用且参数已确认的对应 `mode`。返回是发现线索，不是直接核实的实体。不要把服务提供者的 HTTP REST 调用称为已单独安装的“Serper 官方 MCP”。

- 查询围绕“来源/名录/目标类型/区域”，先找有结构的协会/市政/学校/运营商目录，再找独立场地。地区参数与查询语言分别记，不用地名字符串代替授权范围。
- Search发现官网/文档；Maps/Places补位置/官网候选，店铺分类和摘要不证明天然草、运营权或采购责任。
- 批量前检查接口实际schema和分页口径；每次请求保留query、locale、时间、实际credits/unknown及原始回执。同query重试绑定原操作，未知消费先查。
- 不按主机折成唯一场地；source去重与实体去重分开。目录的多个页面和多个实体都保留来源。
- 按真实名称、地址、已确认外部id产生候选匹配；有冲突进入review，不推断官网。
- 下一步将选中URL交抓取Skill。未抓原文不能生成已核实邮箱或人物任职。
- 零命中记录检索覆盖和输入有效性；查询失败不是市场不存在，不无限猜词。

输出：sources/SourceReceipt与候选实体引用、尚未覆盖范围、下游读回结果。复盘按query/source family记录有效实体收益，不只统计搜索结果数。成功率分母带单位，失效域名、配额不足、schema变化分别归因。

Lite 已在统一 `marketops_search(mode=web|maps|images|news|videos|shopping|scholar|patents|autocomplete)` 增加对应Google REST类别，仍需要真实账户和逐类别响应回归，不等于全部模式已在生产验收。不要把社区MCP标成官方。实际项目配额和错误类别由 [五家Provider指南](../../references/PROVIDER_ONBOARDING.md)解释。

**报覆盖时必须分开说：声明九类，核实零类。** 上述九个 `mode` 是**已声明**的分类，每一个的真
实端点、结果键和出处登记在 `scripts/serper_modes.py`；`marketops_search_capabilities()` 的
`serper_coverage` 直接报告它们各自的核实等级。截至 2026-10-17，**没有任何一类是
`LIVE_VERIFIED`**：官网首页只给了纵向标签，`docs.serper.dev` 在本机没有解析，端点路径只从第三方
SDK/OpenAPI 读到，且**没有向 Serper 发出过任何请求**。所以「Serper 支持九类搜索」这句话本身是
不许说的；要说的是「声明九类、核实零类，出处见 [Serper 覆盖口径](../../references/SERPER_MODE_COVERAGE.md)」。

**翻页不是免费的修辞。** `marketops_search(..., page=n)` 只在已声明可翻页的分类上写入 `page`；
`autocomplete` 声明不分页、`maps` 与 `lens` 声明为翻页出处未取到，`page>1` 都会拒绝而不是发出一个
可能被忽略的请求。不传 `page` 时请求体与加入该参数之前逐字节相同。

**`reviews` 已声明但本适配器服务不了。** `/reviews` 要 `placeId` 且按 cursor（`nextPageToken`）
翻页，本适配器两者都不发，所以它**留在目录里可见**、但 `assert_dispatchable("reviews")` 直接拒绝
（`SERPER_MODE_REQUIRES_UNSENT_PARAM`）——不是静默缺席，也不是发一个注定上游失败的请求。
`scrape` 属于 `scrape.serper.dev`（**另一个主机**），不在本适配器的出网白名单里，**不声明**。

**计价进的是规则不是数字。** 「图片 `num>10` credit 翻倍」以 `pricing_terms(mode)["rules"]` 的形式
带出处、版本与观测日进入计价（Wave-1 验收 D-3 要求），而 `unit_price_usd` 恒为 `null`、
`unit_price_verified` 恒为 `false`——本账户的单价从来没有被读过。
