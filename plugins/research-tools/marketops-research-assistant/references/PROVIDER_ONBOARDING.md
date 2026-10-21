# 五种能力与四个账户

首次安装按[客户快速开始](CUSTOMER_QUICKSTART.md)操作；账户注册、API Key、官方新用户额度及余额入口统一见[四账户启动指南](FOUR_ACCOUNT_START.md)。本页说明各能力如何用于研究。

| 能力 | 插件内接入 | 研究用途 |
|---|---|---|
| Serper | 受管 HTTPS REST | Google 网页、Places/Maps、新闻、学术等发现；结果先作为候选 |
| SerpApi | **内嵌官方 MCP Client** | 按需读取引擎索引/schema，使用所选引擎搜索；不要求客户另装 MCP |
| TinyFish | **内嵌官方 MCP Client** | Search/Fetch 获取公开网页；付费 Agent 单独限权，首次试用不启用 |
| Crawl4AI | 本地 Python SDK + Chromium | 渲染网页、正文/结构化提取；无需第五个账号或 Cloud Key |
| TypeSafe Jev | 官方 Python SDK | 对已保存证据做 Choice/Noul/Score 判断；不能替代来源事实、联系人确认或业务审批 |

两家 MCP 都是 MarketOps 自身的客户端通过官方 HTTPS 服务连接，不是让用户再在宿主安装供应商插件。所有研究调用应经过统一入口、范围与次数许可，并保存材料、预约记录和结果。

## 凭据与激活

1. 客户自己注册四家账户，取得 `SERPER_API_KEY`、`SERPAPI_API_KEY`、`TINYFISH_API_KEY`、`TYPESAFE_API_KEY`。
2. 使用安装回执 `credentials_configure_argv` 运行本地隐藏输入，或由本机安装 agent 用 `research_credentials.py configure --stdin` 写入。原生系统凭据库保存 Key；状态查询和 MCP 启动时读取。显式进程环境值优先，空值和未替换占位符视为未配置。
3. TinyFish 可选择已有的本机 OS Keyring OAuth 登录命令；这是 API Key 的可选替代。它与目标网站登录权限分开，不会自动授权网站 Profile。
4. `provider_onboarding.py` 只显示配置线索，`research_runtime_setup.py status` 只读检查本地运行条件；真实认证/schema 通过逐家 bootstrap 检查。缺失其他账户不应伪装成所选账户不可用。
5. 本地设置为任务选择服务、目标范围、域名和有限请求数。Key 存在不代表已允许消费；请求数限制不代表已经掌握美元费用。

## 结果与费用

- 先持久化 Research Brief，再执行小批研究；保存来源文本及逐字段引文，未知关系和未公开联系信息保留 HOLD。
- 搜索发现不能替代原文核实，TypeSafe 判断不构成事实来源。经验反馈也不自动变更已批准业务规则。
- TinyFish Search/Fetch 的当前免费条件在四账户指南列明；付费 Agent/Browser 不因赠金或登录成功自动开启。未知费用保持未知，不自动充值、不开启 auto-reload。
- 任一服务返回鉴权、配额、站点访问或 schema 问题时记录原因；不静默重新扣费或换供应商消耗第二笔预算。
- 已发出的任务用既有 operation/run 标识查询或恢复；不能用重发新任务掩盖超时。重复研究优先使用仍适用的已存证据。

Crawl4AI Cloud、自托管 Docker 和 TinyFish 网站 Profile 是另外的部署/授权选项，不属于本次四账户默认流程。更详细的传输约束见 [EMBEDDED_OFFICIAL_MCP](EMBEDDED_OFFICIAL_MCP.md) 和 [PROVIDER_TRANSPORT_BILLING](PROVIDER_TRANSPORT_BILLING.md)。
