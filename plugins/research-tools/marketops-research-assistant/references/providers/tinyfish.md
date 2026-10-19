# TinyFish — Search / Fetch / Browser Agent

**官方安装指南** https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md · **官方 MCP** `https://agent.tinyfish.ai/mcp`。优先用 Search/Fetch 读取公开资料；只有必要、合法、经过明确授权时才考虑 `run_web_automation` 的多步网页操作。

> **端点以本页为准**：本仓库内嵌客户端连接 `https://agent.tinyfish.ai/mcp`。另有一处文档写 `https://mcp.tinyfish.ai`，与本插件实际使用的端点不一致；在拿到真实账号的 `tools/list` 之前，不要把二者当成同一个端点。

## 官方工具目录（28 个，2026-10-17 钉住）

上游页面明确声明：工具定义变更时**以服务器 `tools/list` 为权威**。本仓库因此把该目录钉成一份可校验的清单，漂移**按能力逐条**判定：上游新增一个未复核的工具时它拿不到 capability_id、永远不可调用；某个能力自己的工具消失时只有**那个**能力变成 `ABSENT`/`SCHEMA_DRIFT`，兼容的 T1 只读能力继续可用。

| 分组 | 工具 |
|---|---|
| Search / Fetch | `search`、`fetch_content` |
| Web automation（走钱包计费） | `run_web_automation`、`run_web_automation_async`、`get_run`、`list_runs`、`cancel_run`、`batch_status`、`batch_cancel` |
| Browser sessions | `create_browser_session`、`list_browser_sessions`、`close_browser_session` |
| Browser Context Profiles（保存的登录态） | `create_profile`、`list_profiles`、`start_profile_setup_session`、`save_profile_setup_session`、`cancel_profile_setup_session` |
| Monitors（周期任务） | `create_monitor`、`get_monitor`、`list_monitors`、`run_monitor`、`pause_monitor`、`resume_monitor`、`cancel_monitor` |
| Account / usage | `get_wallet`、`get_search_usage`、`list_fetch_usage`、`guide_next_step` |

**本仓库只启用其中 11 个只读工具与 3 个受门控工具**；其余 14 个只登记不启用。只读的账户清单（`list_monitors`、`get_monitor`、`list_browser_sessions`）已按 T1 启用——它们不花钱、不读凭据、不碰私有站点；而**读 Profile 会读账户凭据，仍是 T4 且不启用**。逐能力状态见 [TinyFish官方MCP契约](../TINYFISH_OFFICIAL_MCP_CONTRACT.md)。

### 四个必须按官方目录纠正的字段

| 字段 | 官方契约 | 常见错误（旧快照里就有） |
|---|---|---|
| run 状态查询 | `get_run(id)` / `batch_status(run_ids)` | 调用 `wait_for_run`——官方目录**没有**这个动词 |
| Agent 提交 | `run_web_automation(url, goal, session_id, ...)`，`session_id` 必须是**每次调用新生成的随机 UUID** | 省略 `session_id`，或由调用方复用一个旧值 |
| Agent 硬上限 | **没有** `agent_config` 参数，也没有公布按次步数/时长上限 | 传 `agent_config={max_steps, max_duration_seconds}` 并声称服务端强制 |
| Fetch 标志 | `format`、`links`、`image_links`、`page_metadata`、`selectors`、`ttl`、`per_url_timeout_ms`、条件请求头 | 只允许 `format/ttl/purpose`，把其余公开选项当非法 |

`browser_profile` 的官方取值是 **`LITE` / `STEALTH`（大写）**，不是小写字面量。

**第五处（供应商表格自身的歧义）**：上游把 `format` / `links` / `image_links` /
`page_metadata` 列在 Required 一栏，却又各自给出了默认值。本仓库**显式发送这四个的
文档默认值**（`markdown` / `false` / `false` / `false`），这样两种读法都成立，且不会改变
谁都没传时的行为；调用方显式给的值优先。这条只在真实 `tools/list` 握手后才能定论。

## 三种认证：API Key、MCP Access Token、OAuth

| 方式 | 变量 / 入口 | 走哪个请求头 |
|---|---|---|
| 受保护 API Key | `TINYFISH_API_KEY` | `X-API-Key: <key>` |
| 短时 access token | `TINYFISH_MCP_ACCESS_TOKEN` | `Authorization: Bearer <token>` |
| 浏览器 OAuth | `python scripts/research_official_mcp_auth.py login --provider tinyfish`，仅存 OS Keyring | 由 MCP SDK 自管，不注入静态头 |

**provider key 与 OAuth access token 是两类凭据，不共用一个请求头**——把 API Key 当 Bearer 发是旧快照的读法。

> **未闭合**：官方文档未公布这条线契约。上表是本仓库的**读法**，回执里 `auth_wire_verified: false`。真实账号是否接受必须在单独授权的真实调用中核实，不能从离线绿灯推定。

登录到 *TinyFish 平台* 与登录到 *目标网站的 Browser Context Profile* 是两件事；后者必须由账号持有人手工操作。

## 费用分档

| 档 | 能力 | 默认 |
|---|---|---|
| T1 | `search`、`fetch_content`、`get_run`、`list_runs`、`batch_status`、`get_wallet`、`get_search_usage`、`list_fetch_usage` | 启用（仍受域名白名单 + 网络开关约束） |
| T1 | `list_monitors`、`get_monitor`、`list_browser_sessions` | 启用（只读账户清单；不花钱、不读凭据） |
| T3 | `run_web_automation`、`cancel_run`、`batch_cancel` | **关闭**，需逐次人工授权 |
| T4 | Browser Session / Profile / Monitor 全族、`run_web_automation_async` | **仅登记**，不启用 |

- Monitors 按官方公布 **每次完成计 $0.005 到钱包**（失败免费）——周期计费需要常设授权，本仓库不持有。
- `max_charge_usd` 是**客户端断言**，不是服务端硬停。上游不公布按次上限，因此本仓库要求授权时显式承认这一点，绝不宣传不存在的美元硬停。
- Profile / Vault / 浏览器**会话的创建与关闭**需要命名网站授权，本仓库不启用；也**不读、不导出** Cookie。
- 账户与用量的参数**带文档默认值**（`get_search_usage(limit=100)`、`list_fetch_usage(limit=20)`、
  `list_browser_sessions(limit=100, page=1)`）：空参数调用合法，默认值由适配器显式补上。
- `get_wallet` 的**读取是宽容的**：账户报的形状不认识时照样返回它说的话，并标
  `shape_recognized: false` 与 `WALLET_SHAPE_UNRECOGNIZED`；**严格解析只用在付费前置校验**，
  那里形状不认识就是拒绝（因为那一步的解析就是价格校验）。

## 官方宿主直连（**不是**用本插件所必需）

    npx @tiny-fish/cli connect codex --dry-run
    npx @tiny-fish/cli connect codex
    codex mcp list

遇到故障先 `npx @tiny-fish/cli doctor --pretty`，**不要自动运行 `--fix`**。

## 风险与停止条件

搜索/抓取失败不意味着可以自动升级到收费的 Browser Agent；升级前检查任务授权及实际价格。已派发的付费调用若结果不明，记为 `UNKNOWN`（§2 `ChargeState`），**不重发、不改道**。不自动登录、下单、提交表单、绕过验证码或更改第三方数据。

来源：官方工具目录 https://github.com/tinyfish-io/tinyfish-mcp-server/blob/main/docs/tools.md （钉于 2026-10-17）；安装指南 https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md 。
