# MarketOps：调用通道、鉴权及费用展示合同

**API ≠ API Key，MCP ≠ 登录账号，成功 ≠ 已付费。** API / SDK / MCP 是调用或封装方式；API Key、OAuth、Bearer、站点Cookie／Profile 是认证方式；计费单位属于第三方产品与账户套餐，独立于调用传输层。

## 一条真实受管调用必须能回答

| 显示字段 | 含义 | 例子（只表示接口事实，不是已调用记录） |
|---|---|---|
| 客户端入口 | Agent 如何调用 MarketOps | MarketOps 统一 MCP（本地 stdio） |
| 实际执行 | MarketOps 再通过什么方式取得资料或判断 | Serper REST、Crawl4AI 本地 Python SDK、TypeSafe Jev Python SDK |
| 凭据机制 | 厂商实际如何接收认证 | SerpApi REST 的 API Key URL 查询参数；Serper/TinyFish REST 的 X-API-Key；Cloud Bearer；本地无厂商密钥 |
| 登录态 | 本轮是否使用站点账户的 Session／Profile | MarketOps Lite **没有** 自动使用；官方 TinyFish MCP/Profile 为独立授权 |
| 计费单位 | 厂商规则如何计费 | 搜索次数或 Credits、每 URL、每 Agent step、每 Browser minute、本地 CPU |
| 调用额度 | 本地预算账本预订了多少次 | 1 次预约 ≠ 一笔真实美元消费 |
| 实际扣费 | 必须由厂商钱包／账单／用量回执校核 | 未校核为 `null`、`charge_verified=false` |
| 当前结果 | 成功保存资料、阻断还是未知 | `SAVED_MATERIAL_NOT_VERIFIED`、`UPSTREAM_INSUFFICIENT_CREDITS`、`SOURCE_CHALLENGE_PAGE` |

## 两跳调用示意

```text
Member-A / Claude Code / Codex / WorkBuddy
  └── MCP stdio → MarketOps Lite Research\n       ├── MCP Streamable HTTP → TinyFish官方Search/Fetch/Agent（内嵌Client）\n       ├── MCP Streamable HTTP → SerpApi官方多引擎Search（内嵌Client）
       ├── REST → Serper/兼容SerpApi（各自 API Key、搜索查询额度）
       ├── REST → 兼容TinyFish Search/Fetch（仅显式选择时）
       ├── Python SDK → Crawl4AI local（无Cloud Key，但占本地算力）
       ├── REST → Crawl4AI Cloud（Cloud API Key、Cloud credits）
       └── Python SDK → TypeSafe Jev（其 API Key、模型使用量）
```

另一条**不经过Lite的执行路径**：

```text
Member-A / Host Client ── 官方提供商 MCP（HTTP或其它传输）
   ├── TinyFish 官方 OAuth MCP：Search/Fetch/Agent/Browser/Profile 等
   ├── SerpApi 官方 MCP：Bearer API Key、多引擎搜索
   └── Crawl4AI 官方 Docker/Cloud MCP：自托管Token/Cloud Key
```

**现在经MarketOps内嵌的TinyFish/SerpApi官方MCP调用进入Lite预约与事件日志**；只有宿主额外安装的官方MCP直连绕开Lite时不进入账本。 MarketOps 可以给出候选路由和官方安装引导，但不能凭宿主工具存在就宣称账户已认证、余额足够、已经调用过它，或直接替用户消费。Serper 官方 REST 和 TypeSafe SDK 不应虚构成官方 MCP 产品。官方网站或第三方 MCP 的实时凭据、用量仍需用户/宿主授权核验。

## 当前实现的低上下文可视化

- `marketops_capabilities()`：**只读一次**，返回紧凑的 `lite_invocation_channels`（包含已内嵌MCP的受管调用通道）、`official_mcp_channels`（仅已核实的3家官方MCP入口），同时显示安装/Key变量存在、受管请求预留量，以及授权和账单未验证；不会联网。
- 每次 `marketops_search`、`marketops_crawl`、`marketops_tinyfish`、`marketops_jev_choice` 的正常回执中，有 `invocation`：当前供应商、`client_to_marketops`、`marketops_to_provider`、`authentication`、`billing`。预检拒绝时尽可能保留同一类别、但预约状态为 `UNKNOWN_BLOCKED_BEFORE_OR_AFTER_PROVIDER_ATTEMPT`。
- `marketops_events(run_id)`：最近20条事件里有简短 `invocation_route`，顶部 `latest_lite_provider_call` 给最近受管调用的完整但脱敏结构。只返回代码级对应传输/计费单位，金额未知，不读取外部MCP日志。老 Run 没有协议字段也可从已记录的 `provider` 分类，但未记录的外部调用不可恢复。
- 全部金额默认 `actual_amount:null`，`charge_verified:false`，避免把成功状态、余额快照或一次预约换算成账单。TinyFish官方只读钱包的费率可作为短时路由参考，仍不代表已扣款/允许使用登录态。

### 样例（示意，不代表某次真实收费）

```json
{
  "provider": "serpapi",
  "client_to_marketops": "MARKETOPS_MCP_STDIO_SURFACE",
  "marketops_to_provider": "REST_HTTPS",
  "authentication": {
    "method": "API_KEY_ENV",
    "credential_variable": "SERPAPI_API_KEY",
    "credential_delivery": "API_KEY_HTTPS_QUERY_PARAMETER",
    "provider_auth_verified": false
  },
  "billing": {
    "unit": "SEARCH_QUERY_OR_ACCOUNT_CREDIT",
    "reservation": "ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED",
    "actual_amount": null,
    "currency": null,
    "charge_verified": false
  }
}
```

## 必须具备的真实收费控制（仍待宿主集成）

1. 第三方 API／官方 MCP 统一纳入**宿主级**预算Broker，明确项目、账户、供应商、capability、每次允许的美元/查询/step/min、每日上限；额度未知拒绝升级为付费 Agent。当前 Lite SQLite 只是请求尝试账本，不是真实支出系统。
2. 收费接口若已经受理，但网络超时/任务失去响应，先用原 `run_id` 查状态与账单，**不能换操作 ID 再创建付费任务**。
3. 真实官方 MCP 在 Host 侧产生可核验的（不含Cookie/密钥的）`operation_id,provider,capability,transport,status,units,cost_confirmed` 后，才允许安全转存到统一经验/费用面板；没有此集成时显示“外部 MCP 未监控”。
4. 浏览器 Profile 使用与 API Key 使用是两次不同授权。站点登录态依赖用户实际访问权，不能默认随所有 Google 账号传播，也不可复制到 Lite DB。

安装路径、当前账号费率及各家Provider认证方法见 [PROVIDER_ONBOARDING](PROVIDER_ONBOARDING.md)，有证据的官方MCP/Skill来源见 [OFFICIAL_SKILLS_MCP_REGISTRY](OFFICIAL_SKILLS_MCP_REGISTRY.md)。

### 内嵌MCP的新调用面

用户只安装MarketOps，实际第二跳为官方MCP而不是REST：`marketops_tinyfish(transport='mcp')`、`marketops_serpapi_mcp_search`、`marketops_tinyfish_agent`、`marketops_tinyfish_agent_wait`。这几类工具的`invocation.marketops_to_provider`为`EMBEDDED_OFFICIAL_MCP_HTTP`。官方真实美元账单仍需对账，不应将存储成功视为费用为零。见 [EMBEDDED_OFFICIAL_MCP](EMBEDDED_OFFICIAL_MCP.md)。
