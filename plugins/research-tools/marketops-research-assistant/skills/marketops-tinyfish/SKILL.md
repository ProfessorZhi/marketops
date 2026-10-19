---
name: marketops-tinyfish
description: Use TinyFish search and fetch first and authorized browser interaction only when necessary; inspect current MCP schema, preserve asynchronous run identity, and record provider output separately from verified evidence.
---

# TinyFish：合法的交互式补充能力

MarketOps自编任务Skill，不复制厂商“所有网页都选TinyFish”的全局触发。官方安装入口：https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/INSTALL.md；参考Skill：https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/skills/use-tinyfish/SKILL.md 。

输入：未知来源查询或已知URL、需要的事实/字段、允许网站/动作/费用、统一store。

**真实执行入口（默认就是官方MCP，不是REST伪装）**：`marketops_tinyfish(run_id,operation_id,task='search',query=关键词,transport='mcp')`，或`task='fetch',url=允许的HTTPS网址`。TinyFish 官方MCP的已复核实现是 `research_adapter_tinyfish.py`（`research_official_mcp.py` 中那份TinyFish工具表已过期，见下）：它作为MCP Client实际连接 `https://agent.tinyfish.ai/mcp`、`initialize → tools/list → tools/call`，统一保存资料和额度预约；不要求Member-A在宿主另安装TinyFish官方插件。仅明确设置 `transport='rest'` 时走兼容REST，绝不自动双发。

**上游工具目录以 `tools/list` 为准，并以仓库内的钉住清单为准绳。** 官方文档列出 **28** 个工具。本仓库只放行经复核的子集，其余一律登记为“已描述、未启用”，未知的新工具默认拒绝而不是自动放行：

| 档 | 工具 | 本仓库状态 |
|---|---|---|
| T1 只读 | `search`、`fetch_content` | 启用（受域名白名单与网络开关约束） |
| T1 只读 | `get_run`、`list_runs`、`batch_status` | 启用（只查状态，绝不新建 run） |
| T1 只读 | `get_wallet`、`get_search_usage`、`list_fetch_usage` | 启用（只读，绝不充值） |
| T3 计费 | `run_web_automation`、`cancel_run`、`batch_cancel` | 关闭，需逐次人工授权 |
| T4 | Browser Session / Profile / Monitor 全族、`run_web_automation_async`、`guide_next_step` | 仅登记，不启用 |

平台认证：受保护的 `TINYFISH_API_KEY`、`TINYFISH_MCP_ACCESS_TOKEN` 二者择一；或由账号本人运行已打包的 `research_official_mcp_auth.py login --provider tinyfish`，通过OS Keyring安全持久化浏览器OAuth。登录成功不等于目标网站Profile已授权。**注意（两种凭据分两个请求头）**：`TINYFISH_API_KEY` 走 `X-API-Key`，`TINYFISH_MCP_ACCESS_TOKEN` 走 `Authorization: Bearer`，OAuth（Keyring）由 SDK 自管——provider key 与 OAuth access token 是两类凭据，不共用一个头。官方文档未公布这条线契约，故本仓库把整条判断标为 `auth_wire_verified: false`；真实账号是否接受，必须在**单独授权的一次真实调用**中核实，不能从代码绿灯推定。

**收费Agent**由 `marketops_tinyfish_agent` 单独处理，硬性条件：项目付费开关、`max_charge_usd`（>0 且不超过5美元）、**逐次人工授权对象**（`approved_by`、`scope=TINYFISH_PAID_AGENT_RUN`）以及**明确承认“上游没有公布按次步数上限”**；任一缺失即拒绝。官方 `run_web_automation` **没有** `agent_config` 参数，因此“50步/300秒硬上限”在本仓库中不存在服务端强制，`max_charge_usd` 只登记为客户端断言（`ceiling_enforced_by=CLIENT_ASSERTION_NOT_PROVIDER_ENFORCED`）。**`session_id` 由适配器每次调用新铸一个 UUID**，调用方自带的一律拒绝——否则重放会继承别人提交的浏览器会话。

提交结果只是进行中的run。**查询原任务用官方 `get_run(id)`**（或 `batch_status(run_ids)`），不要每轮新建；`wait_for_run` 这个动词官方目录里**没有**，不要照抄旧快照。

- 没有URL可用Search；已有URL先Fetch。Fetch本身可取得JS渲染内容，不能只因“网站用JS”就升级Agent。
- 读取本连接实际MCP schema；常见search/fetch_content/run_web_automation只是能力例子，CLI选项不当MCP参数。
- **Search 过滤器**（官方多于此前的白名单，全部按名/类型/范围逐项校验，未公布的选项一律拒绝而非转发）：`purpose`、`domain_type`（web/news/research_paper）、`location`、`language`、`recency_minutes`、`after_date`/`before_date`、`pub_year_min`/`pub_year_max`、`include_domains`/`exclude_domains`、`include_thumbnail`、`page`。`recency_minutes` 与日期边界互斥；论文年份区间只在 `domain_type='research_paper'` 下可用。
- **Fetch 标志**：`format`(markdown/html/json)、`links`、`image_links`、`page_metadata`、`include_selectors`/`exclude_selectors`、`ttl`、`per_url_timeout_ms`、`include_etag_and_last_modified`；`if_none_match`/`if_modified_since` **仅限单URL批次**。批次上限10个URL，每个URL保留errors/result。缓存时间、published_at与observed_at分别记，缓存命中不冒充刚从源站验证。
- 真正需要点击公开筛选器/翻页时，才用获准交互工具。目标写清网站、字段JSON形状、最多对象/页面、只读动作、完成/停止条件和禁止外部提交。
- OAuth、API Key及客户MCP Token不可互换；不借用他人profile/vault、不开充值/监控或持久Browser session来绕授权。官方 `get_wallet` 只读，本仓库不加充值/自动续费路径。
- 异步：保存provider run_id后查询原任务（`get_run(id)`），超时/断线先核实状态，**且一律不重试**——已派发的调用若结果不明，记为 `UNKNOWN`（§2 `ChargeState`）并保留敞口，绝不重发、绝不改道另一家付费通道。批量相互独立任务仍受总额度限制。
- 输出看真实result/材料，不把COMPLETED或生成JSON看作业务验收。模型综合文本标provider_generated，不伪造原文或原始hash。

取得结果后调用统一存取路径并读回；缺少源URL/原文支持则保留候选。付费交互失败、Selector不匹配、错误缓存、数据未回写分别进入复盘。可以换合法源，不能用另一个Provider绕验证码或权限墙。

**费用和登录态合同**：官方当前能力分为 Search/Fetch（账户可显示免费）与 Browser（按时间）/Web Agent（按动作步数），必须分别核限额和钱包；免费基础服务无需因昂贵Agent余额为负而一刀切为不可用。浏览器Agent的 `use_profile`/`profile_id` 与 `use_vault` 必须由用户明确授权，官方TinyFish Profile只保留用户手工登录的具体网站上下文，不是Google账户的通用免登录。MarketOps只保留Profile外部引用，不读取或导出Cookie。用户注册TinyFish、API key与官方MCP是不同的认证方式，见 [五家Provider安装/成本/鉴权](../../references/PROVIDER_ONBOARDING.md)。

**状态标签（不要混为一谈）**：`coded`（代码在）、`offline_tested`（离线测试过）、`live`（真实账号核实过）是三个独立事实。本适配器交付时一律 `live: LIVE-UNVERIFIED`。清单、逐能力状态与接线契约见 [TinyFish官方MCP契约](../../references/TINYFISH_OFFICIAL_MCP_CONTRACT.md)。

**一处未闭合**：`research_official_mcp.py` 的旧TinyFish工具表仍写着 `wait_for_run` 与不存在的 `agent_config`。它**失败是封闭的**（真实服务器不会广告 `wait_for_run`，因此会以 `UPSTREAM_MCP_TOOL_NOT_ADVERTISED` 拒绝），但**不可用**。接线由 Integration 负责：把它指向 `research_adapter_tinyfish.py` 或退役该段，见同一份契约文档的"接线"一节。
