# Serper.dev — Search / Maps / Places（与 SerpApi 不同）

**与 SerpApi 的区别：** https://serpapi.com/manage-api-key 是另一家厂商 **SerpApi.com** 的后台，不能用于下述 Serper 接口；如使用该账户，请读 [SerpApi 官方 MCP / API 指南](serpapi.md)。

**官方 API**：https://serper.dev/ 。用于快速搜索发现企业、场地、官网链接，以及 Maps / Places 的地理信息。**尚未确认 Serper 官方维护的通用 MCP。** 网上社区封装属于第三方项目，安装前要单独审核来源、权限、依赖和更新维护。

## 申请 API Key

1. 访问 https://serper.dev/ 注册或登录用户账号。官网提供免费试用搜索额度（具体数量/有效期以当前控制台为准）。
2. 在账号 Dashboard 找到 API Key / Playground；由账号所有者创建或复制 Key。
3. 在用户自己的安全凭据管理器保存；本地手工实验可放在终端环境变量 `SERPER_API_KEY`，**不要写进仓库**。
4. 在 Dashboard 查看余额、使用记录和额度，再决定是否开通付费计划。

## 手工 API 验证

官方 Serper Search 使用 `https://google.serper.dev/search` 和 `X-API-KEY`，建议先打开账号 Playground 查看最新样例和地区参数。下面示例会实际消耗搜索额度，需经使用者许可：

    curl -sS https://google.serper.dev/search \
      -H "X-API-KEY: $SERPER_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"q":"football clubs Devon UK","gl":"gb","hl":"en","num":3}'

Maps/Places 搜索可以辅助定位，但**地图结果不等于该地点经营性质或业务适配已被核实**。候选必须继续进入官网取证步骤。

## Codex / WorkBuddy 如何使用

- **首选**：用户连接自己的经批准 MarketOps Research MCP，由后端复用已经存在的 Serper Adapter（待正式服务与授权接线）。
- **独立试用**：可用 Serper 官方 API、按需编写个人脚本；若使用例如 https://github.com/garylab/serper-mcp-server 等社区 MCP，应先审查源码/来源，不将它宣传为 Serper 官方插件。
- **不要**把 Serper Key 填入客户研究 Skill、Codex Plugin 的 Markdown 文件或 WorkBuddy 连接器元数据。

## 验收口径

应保存 query、国家、语言、响应摘要、时间、Provider 费用；搜索摘要仅能产生 `discovered` 候选。来源：Serper 官方 https://serper.dev/ 。

