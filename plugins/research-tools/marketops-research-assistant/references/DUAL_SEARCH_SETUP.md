# 双搜索接入：SerpApi.com + Serper.dev（v0.3.0 候选）

**已经新增可调用的本地 stdio MCP 搜索适配**：scripts/dual_search_mcp.py。它把两家作为同一个“来源发现”能力的不同后端：每次调用必须指定 serpapi 或 serper，不自动切换，也不默认双倍计费。**这不是整套四能力统一Research MCP，也不提供业务市场数据库写入。**

## 1. 账号和源码

| 供应商 | 申请 Key | 后端协议 | 官方开源情况 |
| --- | --- | --- | --- |
| SerpApi.com | https://serpapi.com/manage-api-key | https://serpapi.com/search.json，或官方 https://mcp.serpapi.com/mcp | https://github.com/serpapi/serpapi-mcp 为 MIT 开源MCP包装；搜索SaaS闭源 |
| Serper.dev | https://serper.dev/ | https://google.serper.dev/search 与 /places，X-API-KEY | 搜索SaaS闭源；第三方MCP不冒充官方 |

两个 Key 彼此不通用：SERPAPI_API_KEY 与 SERPER_API_KEY。用户分别注册账户；不向我们提交 Key，不把 Key 复制进 Git、Excel、聊天或公共日志。调用额度、免费层、付费定价由两家各自控制台为准。

## 2. 客户机运行

安装 Python 3.11+ 和官方 MCP Python SDK：

```bash
python -m pip install 'mcp>=2.2,<3'
```

在**客户控制的 Git 外目录**设置 MARKETOPS_RESEARCH_DATA_ROOT（例如 Linux 下 $HOME/marketops-research-data）。设置相应 Key，并且默认将 MARKETOPS_SEARCH_ALLOW_NETWORK 设为 0、MARKETOPS_SEARCH_MAX_CALLS 设为 0。仅当操作者明确批准一个小批查询时，才将 network 设置为 1、max calls 设置为该数据根下的**累计搜索预留次数上限**，然后启动一个新的 MCP 子进程。操作次数上限不是美元结算硬上限，Provider 仪表盘仍应设置限额。

## 3. Codex

本插件 .codex-plugin/plugin.json 已引用 .mcp.json；后者实际注册**一个 marketops-dual-search stdio server**，其中有 SerpApi/Serper 两种可调用后端。需在启动 Codex 的环境中配置上述环境变量及 Python SDK，插件会从宿主继承变量；不用插件时也可在本机登记绝对路径：

```bash
codex mcp add marketops-dual-search -- python /ABSOLUTE/PATH/TO/scripts/dual_search_mcp.py
codex mcp list
```

请根据客户的实际 Python 环境、插件版本和安装目录调整命令；目前**没有真实 Codex 安装与 API 验收记录**。

## 4. WorkBuddy

默认 build_client_bundles.py 输出 skill-only 包；若用户明确要本地双搜索 MCP，在目标客户**同一台设备**运行：

```bash
python scripts/build_client_bundles.py --output /USER/LOCAL/PATH --with-search-mcp
```

这会生成一个 MCP + Skills 候选连接器，只有一个本地 MCP Server，token-schema.json 让用户填入两个可选供应商 Key、客户数据根、联网开关和累计调用上限。生成包钉住了该台电脑的 Python 解释器和插件源码绝对路径，**不适合直接复制到其他电脑**；其他设备必须重新配置或重新构建。WorkBuddy 实机安装、相对路径和子进程环境透传待验收。

## 5. 工具与可观测性

marketops_search_capabilities() 不发网络请求，只报告配置就绪状态，绝不回传 Key。它同时返回 `serper_coverage`——Serper 九个分类各自带核实等级与出处；口径见 [Serper 覆盖口径](SERPER_MODE_COVERAGE.md)。

marketops_search(provider, query, country, language, run_id, operation_id, mode='web'|'maps'|…, limit=10, page=1, account='') 只使用**指定的一个**供应商。它保存发现候选和搜索回执到客户数据根 search-receipts/<run_id>/，操作事件进 state/research-events.sqlite3，预留次数进 state/dual-search-ledger.sqlite3。重复 operation_id 会拒绝再次请求，防止超时后盲重试。

**预留键与传输无关。** 台账里存的指纹是 `search_canonical.operation_signature`：它按 provider + account + mode + 规范化后的 query + country + language + limit + page + engine 计算，**不含 transport**。因此同一条检索式无论走旧兼容 REST，还是走内嵌官方 MCP，落在**同一格**预留上，不会因为换了一条路就多计一次费用。`account` 是值不是通配：两个账户上的同一条查询是两次计费机会。

**SerpApi 的 REST 兼容路径默认是关的（Wave-1 D-2）。** `marketops_search(provider='serpapi', ...)` 在默认配置下直接以 `SERPAPI_REST_DISABLED` 拒绝，**不发网络、不占额度**——它是通往同一家付费供应商的**第二条**路，默认安装不该有它。它不是被删掉了（产品简报要求它活到官方 MCP 契约验证完成），而是需要操作者显式打开：设 `MARKETOPS_SERPAPI_REST_ENABLE=1`，然后自担该路径的未验证性。这个开关**刻意不进任何 manifest 的 `env_vars`**：出现在生成模板里的开关，会被「填个空就打开」，与「需要一次决定」正好相反。Serper 不受影响，恒为 `ENABLED`。`marketops_search_capabilities()` 如实报告两者的 `availability`。

**`page` 只对已声明可翻页的分类有效。** 不带 `page` 时请求体与加入本参数之前逐字节相同（有测试钉住）；`page>1` 只写入注册表声明的分类，其余情况按三句不同的话拒绝，见 [Serper 覆盖口径](SERPER_MODE_COVERAGE.md) §3。SerpApi 兼容 REST 的翻页参数**未经验证**，`page>1` 一律 `SERPAPI_PAGINATION_UNVERIFIED`，而不是发一个可能被忽略的参数。

返回状态是 DISCOVERED_NOT_VERIFIED：搜索摘要与地图 Place ID 只是线索，不能直接晋升为已核实场地、组织责任或邮件联系人。SerpApi 原生 REST GET 传输按官方要求将 Key 放在查询参数，代码不记录完整 URL、禁止重定向；若对代理/网络日志敏感，优先考虑 SerpApi 官方 Header Bearer 托管 MCP，但该直连还无法自动进入本地 MarketOps 事件日志。

## 6. 先进行业务 A/B 测试，再决定默认路由

在相同目标、国家、语言、预算和少量固定查询下分别调用两家，观察经官网验证的独立场地数量、运营组织匹配率、Place ID/URL 误合并、最终具名联系人/业务入口数量、每条有效结果的花费与人工检查成本。不能把两个供应商的相同场地相加算增长，也不能仅因SerpApi更贵或更全就直接宣布效果更好。

**未完成**：客户API Key、真实服务调用、双端实机验收、完整统一Research MCP和业务市场数据库自动写入。此候选先证明双搜索工具层可实现，之后才与MarketOps完整信息流接线。

## 7. 离线发现结果重合度对照（不调用 Provider）

如果对同一检索式、国家、语言、模式、limit 分别保存了 SerpApi 与 Serper 回执，可在插件目录内运行：

```bash
python scripts/compare_search_receipts.py /USER/ROOT/search-receipts/run-001/serper-receipt.json /USER/ROOT/search-receipts/run-001/serpapi-receipt.json
```

文件路径由实际运行生成的哈希文件名决定，不要照抄示例。报告只统计两家搜索结果行数、相同 Place ID 或完整规范 URL 的候选重合度、以及无法自动比对的行；**不是场地真实性、召回率、联系人转化率或性价比结论**。官方站点 URL 可能同时属于多个不同场地，真正业务 A/B 仍需要人工证据核查和付费账单。
