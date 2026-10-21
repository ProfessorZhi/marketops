# 五 Provider 能力路由合同（2026-10-16）

本页是**已开发代码/能力约束**，不是厂商官方Skill本身；账户注册、授权、令牌、价格与完整官方连接器安装见 [PROVIDER_ONBOARDING](PROVIDER_ONBOARDING.md)。

## 设计现状

- 一个MarketOps Lite stdio MCP；受管基础能力：Serper/SerpApi 受管Google Web/Maps/Images/News/Videos/Shopping/Scholar/Patents/Autocomplete、TinyFish Search/Fetch、Crawl4AI Local/Cloud /scrape、Jev Choice/Noul/Score。不同接入独立Key/钱包/调用上限，外部统一客户Store。
- **TinyFish/SerpApi官方MCP现在由MarketOps内嵌Python MCP Client直接连接**，经本地受管预算预约和证据存储；Crawl4AI官方Docker/Cloud MCP才属于宿主可选连接器。不能从当前客户端的官方 MCP 存在推断用户的另一个项目也已连接、授权或拥有额度。
- 本地主 Skill 按需读当前阶段资料；调用 `marketops_capabilities(task_kind=...)` 获取**只读** matrix 和路由候选列表。宿主报来的 TinyFish 工具名仍只证明“被报告”，并非余额/凭据/登录态验证。

## 逐级选择不是强制五步

| 任务 | 优先 | 条件性升档 | 禁止 |
|---|---|---|---|
| 有效已有证据 | 缓存/Store | 只在缺新证据时调用 | 同 URL 换ID重复抓 |
| 来源发现 | 已核价的 TinyFish Search 或受管 Serper/SerpApi | 特殊引擎使用MarketOps内嵌官方SerpApi MCP的通用search工具 | 无用户批准自动付费双搜 |
| 已知URL正文 | 已核价 TinyFish Fetch / 本地 Crawl4AI | 已授权的 Cloud/Selfhosted | 普通页面直接Web Agent |
| 稳定目录/批量 | Crawl4AI 本地/自托管 | 有凭证时 Cloud batch/jobs | 拿抓取量充当事实有效率 |
| 必须点击/翻页 | 可控本地浏览器、用户自建脚本 | TinyFish按分钟 Browser或按step Agent（收费） | 预算不足、未登录便启动 |
| 用户授权登录态 | 官方 Profile/Vault只存于厂商 | 仅指定site、账号范围、动作、过期策略 | 用Google Cookie推断所有站点都有权限 |
| 模糊证据分类 | 确定性规则 | Jev Choice/Noul/Score 单次有界调用 | 用语义评分冒充已证事实 |

## 可观察状态（缺一不可）

`implemented`、`host_tool_reported`、`credential_configured`、`auth_verified`、`rate_verified`、`wallet_or_quota_verified`、`site_authorized`、`last_result` 和 `reviewed_business_outcome` 绝不是一个布尔量。

本机Lite能严格判断默认禁网、SDK存在与否、Key变量有无、受管预订次数、浏览器缓存**提示**；不能替用户登录外部钱包或侦测独立MCP的 OAuth/配额。因此 `marketops_capabilities` 返回 `ELIGIBLE_BUT_AUTH_AND_CREDITS_UNVERIFIED` 也**不是**新费用授权。

宿主若已连接官方TinyFish MCP，应通过宿主工具独立读取其 `get_wallet` 费率与余额、`list_profiles`，检查项目权限后再交给主代理；不要向Lite写入密钥、Cookies或整个私有MCP schema。预算未知、402、401、429、其他访问限制等必须记录独立reason code。

## 降级路径

1. 当前能力返回成功且资料保存回读 → 继续结构化证据。
2. 本地SDK/浏览器缺失 → 免费预检、请求用户配置已安装解释器/浏览器目录，不反复消耗请求额度。
3. 当前某 **meter** 余额不足/402 → 只暂停该付费 meter，例如TinyFish Agent；免费 Search/Fetch 是否可用单独评估，严禁错误把整个Provider失效。
4. 某来源403/CAPTCHA/Cloudflare → 不换代理/IP/服务商绕访问控制，查另一个独立合法公开来源或HOLD。
5. Serper/SerpApi/Jev额度未知 → 本批次不自动升付费，不通过第二家隐式扣费。
6. 任务要求正式业务审批/外联 → Lite只能候选，人工和获批准的业务引擎处理。

## 统一证据写回

连接器来源始终携带 Run、操作Id、实际结果状态、源URL与材料Id、SHA和观察时间。**通过MarketOps内嵌**的TinyFish/SerpApi MCP调用进入Lite尝试账本；仅宿主绕开MarketOps、单独直连官方MCP的调用不自动进入账本；不能因此称所有费用都被硬性控制。调研收口需将读回的资料落到同Run Store、简短事件/卡点进入唯一的 `Research_Experience.md`。无效结论仍HOLD，模型判断不能自动晋升SOP A/B/C/D。

## 新：实际已嵌入的官方MCP调用

默认TinyFish使用 `marketops_tinyfish(...,transport='mcp')`；SerpApi多引擎使用 `marketops_serpapi_mcp_search(...,params={engine:'...',q:'...'})`。高级TinyFish Agent还需另行开关、钱包只读检查、步数/时长/美元上限，通过`marketops_tinyfish_agent`提交；返回runId后使用`marketops_tinyfish_agent_wait`检查原任务，不能重复提交。兼容 REST只在明确指定时使用。Crawl4AI仍本地SDK主路径、自托管MCP仅预留。协议、认证和预算说明见 [实际MCP嵌合](EMBEDDED_OFFICIAL_MCP.md)。
