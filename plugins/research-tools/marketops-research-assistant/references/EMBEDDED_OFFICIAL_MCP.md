# MarketOps 内嵌官方 MCP 客户端：TinyFish + SerpApi

**不是只预置链接。** MarketOps 自己是对 Member-A/Claude/Codex/WorkBuddy 暴露工具的 stdio MCP Server；内部同时作为 *Streamable HTTP MCP Client*，通过官方 HTTP MCP 端点执行 `initialize → tools/list → tools/call`。

```text
Claude Code / Codex / WorkBuddy
  └─ MarketOps Research MCP (唯一业务工具入口, 本地 stdio)
       ├─ TinyFish official MCP client (https://agent.tinyfish.ai/mcp)
       │    ├─ search / fetch_content  [不自动升档；过滤器与fetch标志按官方28工具目录]
       │    ├─ get_run / batch_status / list_runs  [只查状态，绝不新建 run]
       │    ├─ get_wallet / *_usage  [只读；无充值路径]
       │    └─ run_web_automation → get_run(id)  [逐次人工授权 + 新铸UUID session_id + 只读钱包]
       ├─ SerpApi official MCP client (https://mcp.serpapi.com/mcp)
       │    └─ search(params={engine: ..., q: ...}, mode=compact)
       ├─ Serper official REST API (不是编造官方MCP)
       ├─ Crawl4AI local AsyncWebCrawler + Chromium (免Cloud Key)
       └─ TypeSafe Jev Python SDK (Choice / Noul / Score)
```

## 用户只装 MarketOps；官方连接器无需再装一遍

安装器的 `--apply --bootstrap-runtime` 会在**用户同意软件下载后**于目标电脑安装受支持的Python MCP SDK和本地依赖；`research_official_mcp.py` 和 `research_official_mcp_auth.py` 在MarketOps安装包内。既不要求用户在Claude Code再安装官方TinyFish/SerpApi插件，也不强迫暴露几十个同义工具。它调用官方远端MCP协议/服务，**不把供应商服务端代码拷贝到客户机器**。

供应商OAuth/Key仍需要用户自己注册与授权：

- TinyFish 官方 https://agent.tinyfish.ai/mcp ：最简方式为到 https://agent.tinyfish.ai/ 注册、在本机安全环境/WorkBuddy密钥设置配置 `TINYFISH_API_KEY`；内嵌MCP尝试用它作为Bearer（是否得到服务端认可必须经真实认证验收）。也可以由用户在本机命令行明确运行 `python scripts/research_official_mcp_auth.py login --provider tinyfish`，通过真实浏览器授权获得Token，**仅OS Keyring存储**，不写Git/SQLite/调研数据。这个登录是可选功能；若操作系统没有安全的keyring backend，就拒绝持久化，建议使用安全配置的API Key。还可选择注入短时有效的 `TINYFISH_MCP_ACCESS_TOKEN`（安全环境变量）。
- SerpApi 官方 https://mcp.serpapi.com/mcp ：去 https://serpapi.com/dashboard 注册领取 `SERPAPI_API_KEY`，由MarketOps内嵌MCP Client透过 HTTPS Authorization Bearer Header 提供；不把Key写进URL。
- 不需要注册五家才能首次开始研究。Serper REST Key、TypeSafe SDK Key、Crawl4AI可选Cloud Key仍按实际需要使用。本地Crawl4AI无需云账户或云Key。

**OAuth边界**：OS Keyring中有Token≠实时认证已成功；Token过期可由SDK按协议尝试刷新，需重新授权时只返回状态，不会在后台弹窗或自动访问其他账号。用户在TinyFish平台登录也不等于对其他目标网站的Browser Context Profile已授权。

## 真实可调用 MarketOps MCP 工具

| 业务动作 | MarketOps工具 | 内部实际调用 | 费用边界 |
|---|---|---|---|
| TinyFish基础搜索 | `marketops_tinyfish(task='search',transport='mcp')` | `tinyfish.search(query=...)` | 当前账号Search对应费率/限额；与Agent钱包分开 |
| TinyFish已知URL提取 | `marketops_tinyfish(task='fetch',transport='mcp')` | `tinyfish.fetch_content(urls=[...])` | 当前账号Fetch对应费率/限额；目标域名白名单 |
| SerpApi通用多引擎搜索 | `marketops_serpapi_mcp_search(params={engine:'google_scholar',q:'...'})` | `serpapi.search(params=...,mode='compact')` | 该引擎真实账户次数/credits与任务预算 |
| TinyFish收费Web Agent | `marketops_tinyfish_agent(url,goal,max_charge_usd)` | `tinyfish.run_web_automation` | 默认禁止；先验项目付费开关+目标站允许、逐次人工授权、官方钱包quote与余额；**上游未公布 `agent_config`/按次步数上限**，故 `max_charge_usd` 只登记为客户端断言，授权时必须显式承认这一点；`session_id` 由适配器新铸UUID |
| 已提交Agent的原任务状态 | `marketops_tinyfish_agent_wait(original_operation_id,agent_run_id,poll_index)` | `tinyfish.get_run(id)` / `batch_status(run_ids)` | 须有同Run已保存/哈希校验的提交回执；不会新建Agent；最多10次独立人工轮询；**官方没有 `wait_for_run` 这个工具** |
| Serper | `marketops_search(provider='serper',...)` | REST | 独立Key/搜索次数预算 |
| 兼容旧SerpApi REST | `marketops_search(provider='serpapi',...)` | REST | 显式选择时才使用；禁止自动双搜 |
| 兼容旧TinyFish REST | `marketops_tinyfish(...,transport='rest')` | REST | 显式选择时才使用；禁止自动双发 |

**重要**：`marketops_search(provider='serpapi')` 是既有REST路径，为维持客户兼容暂时保留；新业务优先使用 `marketops_serpapi_mcp_search`，可以通过通用`params`使用不同引擎。这两条不是同一个调用，**绝不能为了“多渠道保险”两者自动一起调用**。

## 强制执行与审计

1. 正式Research Brief和Run必须先保存；`MARKETOPS_RESEARCH_ALLOW_NETWORK=1`、`MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS>0` 及当前Project权限均需要显式配置。不足一律阻断。所有内嵌MCP调用与普通本地Provider走同一个 `provider-reservations.sqlite3`，`marketops_events`有 started/succeeded/blocked、Tool/Provider、操作ID；响应作为候选来源artifact被保存SHA并可以复查。未知消费不自动重试。
2. `run_web_automation` 还需要 `MARKETOPS_TINYFISH_AGENT_ALLOW_PAID=1`、用户明确`max_charge_usd`（上限5USD的代码级保险）、`max_steps`（1–50）、`max_duration_seconds`（1–300）、服务端实际Schema支持硬上限及同账号 `get_wallet` 可读余额/每步费率。不知道报价、无余额或参数不支持时拒绝提交，不假设余额大于零就可花钱。
3. Agent提交和真正结束是两件事。只有取得官方原任务runId才登记为 `SUBMITTED_NOT_TERMINAL`，不把提交成功冒充资料已提取。后续用同一runId查询并单独保存状态；不靠换opId创建第二个收费Agent。
4. 网页来源只限Research Brief允许的公开HTTPS域名；挑战页/403/429等拒绝后不换Agent/代理/IP绕过。全部结果在MarketOps中为候选，不自动批准SOP等级/联系人/外联。
5. `marketops_capabilities` 可离线显示内嵌MCP代码和安全凭据有无，但**不是OAuth/有效余额的网络验收**。若当前机器没有Key、Keyring或公网，结果应显示MISSING/UNKNOWN；不能把此前ChatGPT宿主绑定的账户当成客户本机账户。
6. 官方MCP收费由服务商账户决定。MarketOps目前有调用次数预约、Agent步骤+时限的入场约束，但**尚不具备对所有厂商的美元实际账单自动对账**；`actual_amount:null, charge_verified:false` 是正确结果，不能把一次成功MCP调用直接写成0美元。

**验收级别**：必须逐一完成：源代码导入/离线Fake Session schema→目标机器 `tools/list`→授权用户实际MCP鉴权/额度验证→最多一项获准服务调用并读回事实材料。在任何付费功能通过正式授权之前，始终保持默认禁止。测试日志不得包含OAuth回调code、Bearer Key、钱包充值地址或客户站点Profile Cookie。
