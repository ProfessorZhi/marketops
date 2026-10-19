# MarketOps Research Assistant — 安装后从这里开始

**目标：** 一次安装 MarketOps 统一 MCP + 17 个 Skills + **真实内嵌的 TinyFish 与 SerpApi 官方 MCP Client** + Serper REST / Crawl4AI SDK / Jev SDK。只需为选用的供应商自行注册、在本机安全配置 API Key 或通过本插件进行 TinyFish OAuth，不需要另外在 Claude Code/Codex 安装两个厂商 MCP 插件。高级 Agent仍默认禁止、需独立付费授权。

> 安装程序不创建外部账户、不会拿到你的 OAuth、不会代你购买 Credits、不会写入真实密钥；安装依赖／Chromium 下载是明确选择的软件下载，不是调用供应商收费服务。不同操作系统仍须下载与该机器匹配的 Python Wheels/浏览器，不能将一个开发者的 venv/Chromium 打进跨系统ZIP后保证可运行。

## 第一步：安装 MarketOps（选一条）

将本插件目录作为下载或解压好的源码，在该目录下运行。**Python 3.11+**。

- 想一次性准备本地 MCP、Jev SDK、Crawl4AI 本地 SDK 和 Chromium 浏览器（会联网下载依赖，耗磁盘）：

```bash
python3 scripts/setup_clients.py --destination /YOUR/EMPTY/marketops-install --client all --apply --bootstrap-runtime
```

- 只准备 MarketOps + Jev 的核心 SDK，稍后再装大型浏览器：

```bash
python3 scripts/setup_clients.py --destination /YOUR/EMPTY/marketops-install --client both --apply --install-deps
```

- **只查看计划，不动磁盘**：

```bash
python3 scripts/setup_clients.py --destination /YOUR/EMPTY/marketops-install --client all
```

选项：`--client claude` = Claude Code，`codex` = Codex，`workbuddy` = WorkBuddy，`both` = Codex + WorkBuddy，`all` = 三种环境。Windows 使用自己的Python启动器，并选择Windows绝对安装目录；平台的浏览器安装可能还需要额外系统依赖。

安装后首先打开安装根目录自动生成的 **`START_HERE.md`**；运行 `INSTALL_RECEIPT.json` 里显示的 `provider_onboarding_command`（会自动包含正确的 venv Python、插件路径）。它只打印账号申请与连接状态，不联网。

## 第二步：首次启动只配置两个本地路径

在受管 MarketOps MCP 中调用 `marketops_setup_status`。若 `CONFIG_REQUIRED`，用户**自己确定**：

1. 研究数据根目录的绝对路径。
2. 单独收取的 `Research_Experience.md` 文件绝对路径（不得存于研究数据根或 Git 内）。

调用 `marketops_setup_configure` 后只问这一次，重启不会重新要求。这里**不需要提供五个 API Key**。

## 第三步：按项目需要注册供应商

安装包自带 `scripts/provider_onboarding.py` 和 `references/PROVIDER_CONNECTIONS.json`，可以选择一个或全部：

```bash
python scripts/provider_onboarding.py --client claude --provider all
python scripts/provider_onboarding.py --client codex --provider tinyfish --json
```

如果从已安装包执行，应使用 `INSTALL_RECEIPT.json` 中的完整命令或用户选定 venv 的Python。CLI 本身**不存/不输出密钥**。

注意参数轴不同：安装器 `--client all` 表示一次为三种宿主准备文件；本地账号向导 `--client` 只接受 `claude|codex|workbuddy`，而 `--provider all` 表示一次列出五家供应商。首次配置后可以在每个实际使用的宿主分别运行向导；**不能从一个宿主已登录推断另一个宿主的MCP也已连接**。

`--bootstrap-runtime` 下载的 Chromium 放在客户安装根下的 `browsers/` 专属目录，并在新安装MCP和WorkBuddy连接配置中绑定 `PLAYWRIGHT_BROWSERS_PATH`。这能避免隔离HOME导致浏览器丢失，但**仍需在该操作系统实际启动Chromium验收**，不要因为目录存在便报告浏览器可用。

| 厂商 | 注册／申请 | 默认适用通道 | 用户需要做什么 |
|---|---|---|---|
| TinyFish | https://agent.tinyfish.ai/ | **已内嵌官方MCP Client**（Search/Fetch；限额后Agent/原Run等待） | 安装后安全配置 `TINYFISH_API_KEY`，或在本机明确执行安装包内的 TinyFish OAuth登录命令；OS Keyring保管Token。目标网站Profile不随平台OAuth自动授权 |
| SerpApi | https://serpapi.com/dashboard | **已内嵌官方MCP Client**，多引擎 `search(params=...)` | 官网取 `SERPAPI_API_KEY` 并在 MarketOps 的安全配置输入；由MarketOps Client向官方HTTPS MCP附加Bearer Header，不在URL中放Key，REST仅显式兼容 |
| Serper | https://serper.dev/ | Lite内受管 REST | 官网申请 `SERPER_API_KEY`；未核实厂商官方MCP，不误装社区版 |
| Crawl4AI | https://docs.crawl4ai.com/ | 已安装的本地Python SDK（无需云Key） | Chromium需安装在这台机器；只有用Cloud才去 https://crawl4ai.com/ 申请独立 `CRAWL4AI_API_KEY` |
| TypeSafe Jev | https://console.typesafe.ai/ | 已安装Python SDK | 取得 `TYPESAFE_API_KEY`；Choice、Noul、Score按需收费，不能用作事实审批 |

这些应用在“插件包内附带”的是**我们的Adapter代码、安装指引、连接模板和声明好的SDK安装步骤**；不是代替官方服务商签发API Key，更不是把官方远程MCP服务本体的代码或OAuth令牌私自复制到本地。

**重要**：TinyFish 的Search/Fetch若当前账户显示零费用，可单独使用；付费Web Agent/Browser要通过额度和授权检查。MCP存在或OAuth已完成不代表费用为零。

## 第四步：激活插件**已经内嵌**的两个官方MCP Client

**你不用再额外安装 TinyFish 或 SerpApi 的宿主插件。** 市场研究插件自己会通过 `mcp` Python SDK 连接官方 `https://agent.tinyfish.ai/mcp`、`https://mcp.serpapi.com/mcp`，读取工具schema并执行一次受管请求；所有成功/失败、请求预约与素材存储归同一个MarketOps Run。

1. **TinyFish**：选其一即可。（a）在客户端安全凭据设置输入 `TINYFISH_API_KEY`，此值由MarketOps MCP Client以Bearer Header传给官方MCP（需真实鉴权验收）；（b）由账号主人**自行运行**安装凭据回执所给命令：`python scripts/research_official_mcp_auth.py login --provider tinyfish`，然后在浏览器里登录官方账号并把OAuth回调粘贴到本地隐藏输入提示。成功后令牌只保存在 OS Keyring，普通工具调用不会弹浏览器。若本机没有安全Keyring则拒绝持久化，改用API Key。平台OAuth≠其它网站Profile的登录。
2. **SerpApi**：在 https://serpapi.com/dashboard 取得 `SERPAPI_API_KEY` 并由用户填入其客户端受保护的环境/Secret设置。内嵌官方MCP的通用 `search` 由 `marketops_serpapi_mcp_search(params={engine:'google_light',q:'...'})` 调用；不需另在Claude/Codex执行 `mcp add serpapi`。
3. **TinyFish付费Agent**：`marketops_tinyfish_agent` 默认拒绝；用户须设置项目/进程 `MARKETOPS_TINYFISH_AGENT_ALLOW_PAID=1` 并每次指定最多50步、300秒和最多5美元（可进一步收紧）。实际调用前必须从同一MCP会话查询钱包、核实费率及供应商schema的硬限制；拿不到就不提交任务。成功提交后的 run ID 由 `marketops_tinyfish_agent_wait` 只查该任务状态；禁止超时重新启动新任务。**不能因此断言所有货币费用已完成对账。**
4. Crawl4AI：默认本地SDK，无云端账号；官方Docker/Cloud MCP仅预留。Serper：受管REST。Jev：受管TypeSafe SDK。
5. 若用户另外想把厂商工具独立装入Claude/Codex宿主（例如不经过MarketOps做网页工作），那属于**另一条不受MarketOps Lite预算和证据链自动追踪的直连路径**，不是正常安装步骤，不建议默认重复安装。

完整的受管真实MCP客户端与OAuth/费用边界见 [EMBEDDED_OFFICIAL_MCP](EMBEDDED_OFFICIAL_MCP.md)。
## 第五步：检查状态、再运行调研

1. 先验证 MarketOps `tools/list` 中能看见受管工具，再调用 `marketops_capabilities()`；单纯“发现MCP工具名”不代表认证或余额通过。
2. 检查供应商连接是否已成功、是否显示当前真实费用/配额、目标网站是否允许访问；`marketops_capabilities` 对宿主官方MCP只接收**未经认证的工具发现提示**。
3. 由用户为单个项目打开相应网络开关和受管累计请求上限；默认都为0/禁用。付费功能每次还需费用边界和权限许可。
4. TinyFish Agent/Browser预算未知或已耗尽，不影响已验证可用的零费Search/Fetch或本地Crawl4AI；不得换另一个Provider绕访问墙。
5. Research Brief必须在任何外部消费之前持久化；真实来源保留引文及HASH、无法核实保留HOLD，最后产出9表Excel及经验汇总。

更多内容：[五家Provider及Key](PROVIDER_ONBOARDING.md)、[官方Skills/MCP来源](OFFICIAL_SKILLS_MCP_REGISTRY.md)、[真实通道／计费回执](PROVIDER_TRANSPORT_BILLING.md)、[完整发布验收清单](RELEASE_GATE.md)。

**交付等级说明**：本包把安装目录、代码、依赖和配置步骤封装好，但只有在 Member-A 实际机器上完成 Claude/Codex/WorkBuddy 启动、官方OAuth及单次真实只读验收后，才能称该客户环境“安装完成可用”。离线单测不能代替真实厂商账号与网页登录验收。
