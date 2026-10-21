# 四账户、API Key 和新用户额度

核查日期：**2026-10-20**。下列入口都属于官方；实际资格、余额、有效期以自己的账户显示为准。注册由本人完成；MarketOps 不代开户、购买额度或开启自动充值。已有账户直接登录，已领取过的赠金不重复领取。

| 服务 | 注册与申请 Key | 当前可核实的启动额度 | 余额与有效期 |
|---|---|---|---|
| Serper | [官网](https://serper.dev/) → Sign up / Get 2,500 free queries → 登录后复制 API Key | 官网提供新用户 2,500 次免费查询，无需信用卡。不是每月赠送承诺 | 在控制台查看剩余 credits；未核实固定赠送有效期 |
| SerpApi | [注册](https://serpapi.com/users/sign_up) → [API Key](https://serpapi.com/manage-api-key) | [Free 套餐](https://serpapi.com/pricing)当前 250 次/月，50 次/小时 | [账户](https://serpapi.com/dashboard)查看余量与下次续期日；[免费 Account API](https://serpapi.com/account-api)也能查询 |
| TinyFish | [注册](https://agent.tinyfish.ai/sign-up) → [API Keys](https://agent.tinyfish.ai/api-keys) → 创建 Key | 注册页当前显示 $8 Wallet 新用户赠金，无需信用卡；Search/Fetch 免费 | 登录后查看 Wallet；已领取的账号只核对余额。未核实普通赠金固定有效期 |
| TypeSafe / Jev | [控制台](https://console.typesafe.ai/)注册/登录 → 按[官方 Quickstart](https://docs.typesafe.ai/introduction/quickstart)创建 API Key | **未核实统一新用户赠金金额**；如控制台有试用额度按该账户规则使用 | 选择 API Key 所属组织 → Settings → Billing → Available credits；[官方余额说明](https://help.typesafe.ai/articles/5505198974-what-s-a-402-error-and-how-can-i-add-more-credits) |

TinyFish 的[官方价格页](https://www.tinyfish.ai/pricing)当前列出 Search 30 次/分钟、500 次/小时；Fetch 150 URLs/分钟、1,000 URLs/天。两者不扣 Wallet；Agent/Browser 会计费，MarketOps 不因账户有赠金就自动使用它们。注册优惠和免费功能是两种不同额度。学生活动有额外资格要求，不作为普通客户默认领取路径。

**Crawl4AI 本地模式不需要账户或 API Key。** 安装器会准备本地 SDK 和 Chromium。Cloud/Docker 是另选配置，不属于四账户首次启动步骤。

取得四个 Key 后，继续本机安装器的隐藏输入提示；或者把 Key 交给本机安装的 agent（`configure --stdin`，见 `AGENT_INSTALL.md`）；也可以自己运行安装目录 `INSTALL_RECEIPT.json` 里的 `credentials_configure_argv`。对应变量：

- Serper：`SERPER_API_KEY`
- SerpApi：`SERPAPI_API_KEY`
- TinyFish：`TINYFISH_API_KEY`
- TypeSafe：`TYPESAFE_API_KEY`

Key 保存在原生系统凭据库，重启时由 MarketOps 读取。明确注入的进程环境变量优先；空值和未替换的 `${VAR}` 不会当成 Key。留空保留已有值，修改时重新运行同一配置命令。没有可用的安全凭据库时设置会拒绝保存；Windows 检查 Credential Manager，Linux 检查 Secret Service/桌面钥匙串，或由操作者向独立进程注入环境变量。不要把 Key 放进聊天、Git、安装回执或调研导出。

先用本地向导检查配置，再运行有次数上限的小任务。Key 存在仅证明配置过；实际认证、当期余量和运行结果需要分别验证。额度耗尽时保留当前结果并停止该服务，不自动充值。
