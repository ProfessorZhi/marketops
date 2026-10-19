# MarketOps Research Plugin — 五家 Provider 安装、鉴权与降级指南

> 官方Skill/MCP的逐项审核、上游文件SHA及按需安装方法见 [已核验官方Skill/MCP登记](OFFICIAL_SKILLS_MCP_REGISTRY.md)。
>
> 2026-10-16 核对的官方入口。**用户选择安装并授权**后才启用网络能力；这份文档不注册账号、不创建 Key、不充值、不验证登录、更不会把密钥写入仓库。各厂商限额、价格随时会变，以实际账户和官方公告为准。
>
> 插件的 17 个 Skill 属于业务流程和调用方法。底层 MarketOps Lite MCP 含23个工具，负责候选数据、来源、预算账本与工作簿。**TinyFish/SerpApi官方MCP的已允许工具**由MarketOps内嵌MCP Client直接调用并进入Lite账本；仅额外宿主直接连接的厂商工具绕开Lite账本，需要宿主级权限/费用拦截与独立回执。

调用后需要看清 **MCP 是外部工具入口，不是厂商账单**。REST API／SDK／MCP 的两跳调用链、实际 Key 传输、每种计费单位与“预约次数不等于扣款金额”见 [运行时通道与费用回执](PROVIDER_TRANSPORT_BILLING.md)。

完整面向最终用户的一次安装步骤见 [CUSTOMER_QUICKSTART](CUSTOMER_QUICKSTART.md)，可执行的五家非机密连接模板见 [PROVIDER_CONNECTIONS.json](PROVIDER_CONNECTIONS.json)。安装工具 `setup_clients.py --client all --apply --bootstrap-runtime` 是用户**显式选择**的单命令运行环境下载与引导（MCP Python SDK、Jev SDK、Crawl4AI本地和Chromium）；它不是预先激活所有厂商账号、支付或站点Session的动作。安装后 `START_HERE.md` 指向这份文档，`provider_onboarding.py` 能离线查看下一步。

TinyFish/SerpApi内嵌MCP协议与受管调用方式见 [实际嵌入说明](EMBEDDED_OFFICIAL_MCP.md)：**不需要用户再装两个官方宿主Plugin**。TinyFish可提供账号Key，也可通过本插件可选浏览器OAuth + OS Keyring；SerpApi仍使用其API Key的HTTPS Bearer Header。

## 0. 首次安装：先不填任何厂商密钥

1. 由 Member-A 选择研究数据根目录与唯一 `Research_Experience.md` 的绝对路径（恰好两个路径问题），先运行 `marketops_setup_status`，必要时调用 `marketops_setup_configure`。
2. 使用受管安装方式 `python3 scripts/setup_clients.py --destination <空目录> --client codex|workbuddy --apply --install-deps`。这是**显式**的安装选择：`--install-deps` 会联网下载依赖，不可在后台擅自执行；安装后生成包使用该安装目录自己的 Python。
3. Claude Code 在插件源码中的 `.mcp.json` 使用自带 `scripts/launch_research_mcp.sh`（POSIX），它启动的入口是 `scripts/research_mcp_facade.py`（宿主兼容门面）。Linux/macOS 建议指定 `MARKETOPS_RESEARCH_PYTHON` 为**有 MCP SDK 的绝对私有 venv Python**。启动器只探测，不自动安装；传给它的入口路径必须落在插件目录内，否则以短码拒绝。**Windows 不读这份源码配置**：安装器生成绝对 venv Python + `.py` 入口、不含任何 shell 依赖，Windows 契约判在安装产物上（`client_install_probe --artifact installed`）。
4. 初次开启所有网络开关均为 `0`，所有预算 `0`，先调用 `marketops_capabilities` 检查本机 SDK/浏览器提示、搜索/抓取余额尝试计数，看到 `DISABLED_BY_PROJECT_OR_PROCESS_POLICY` 是正常的。
5. **各项目分别启用和核准**：Key 已填≠签名可用；连接成功≠余额足够；登录成功≠目标网站有权访问；低级 Search/Fetch 可用≠付费 Agent 可用。

## 1. TinyFish — 同一厂商，至少四种费用和两种鉴权层

官方门户：https://agent.tinyfish.ai/ （注册/登录 → API Keys → Create API Key），官方文档：https://docs.tinyfish.ai ，官方MCP和 Agent 文档：https://www.tinyfish.ai/ 。

| 能力 | MarketOps 集成形态 | 鉴权 | 默认费用策略 |
|---|---|---|---|
| Search | 默认 `marketops_tinyfish(task=search,transport=mcp)` **内部MCP Client**；兼容 REST `transport=rest` | 本插件的用户配置API Key Bearer 或由本人本机完成 OS Keyring OAuth | 账户当前费率可为 $0；**单独**看限流和真实成功，不因 Agent 余额不足自动封禁 |
| Fetch | 默认 `marketops_tinyfish(task=fetch,transport=mcp)` **内部MCP Client**；兼容REST | 与Search复用同一已批准TinyFish账号的Key/Keyring OAuth | 当前账户可为 $0，适合明确 URL 的 JS 内容，不自动升级 Agent |
| Browser | **官方连接器/Browser API**（不是 Lite 内置工具） | TinyFish 账户+钱包，独立浏览器会话 | **按分钟计费**，只在脚本化交互需要云浏览器时启用 |
| Web Agent | MarketOps `marketops_tinyfish_agent` 内嵌官方MCP的 `run_web_automation`，默认禁止且有硬步数/费用门槛 | TinyFish 账户+钱包+按任务显式授权 | **按 step 计费**，必须有范围、步骤与持续时间上限；严禁默认调用 |
| Profile / Vault | 官方 `create_profile` / `start_profile_setup_session` / `save_profile_setup_session` | 用户**亲自**在 TinyFish 托管的 setup URL 完成站点登录/MFA | 仅用于用户明确授权的账户和具体站点；不经手密码、Cookie、Token |

**两个不同登录！** ① 登录 TinyFish 控制台获取 API Key 或完成官方 MCP 连接认证；② 目标网站（例如公司门户）必须由用户在自己的 Browser Context Profile 安全登录。Google 登录仅适用于已通过该网站授权的具体账户；不是通用网站通行证。签入会失效，`signed_in_sites` 列表也不能代替实时登录验证。

MarketOps 的站点策略只保存 `provider=tinyfish`、`profile_id` 的**不含认证数据的引用**、明确授权的域名/项目/允许动作和有效期，不复制 Cookies、Vault secrets、会话导出或含 token 的浏览器链接。无明确授权绝不 `use_profile/use_vault`。

**钱包与限额**：可通过官方 `get_wallet` 只读核实各 meter 当前费率/余额。负余额或余额未知时付费 Browser/Agent 标记 `PAID_BLOCKED`；Search/Fetch 若费率=0且服务实际正常，仍按自己的速率额度可用。MarketOps的离线能力目录不知道钱包余额；只有账号授权下**同一内嵌MCP会话**的只读钱包回执才可用于这次Agent费用预检，绝不伪称已核验。**绝不自动充值、开启auto-reload、重复发起超时的 Agent run**；要先保存 run_id 并使用 `wait_for_run`。


### TinyFish 钱包快照传递（只读；**不是**自动登录或自动扣费）

在已连接官方 TinyFish MCP 的宿主中，或在MarketOps内嵌MCP客户端中，用户授权后由客户端调用官方只读 `get_wallet`；将其**最小必要字段** `available_balance`、`currency`、`as_of`、`rates.meters[].{label,unit_amount,per}` 传给 `marketops_capabilities(task_kind='known_public_url', tinyfish_wallet_snapshot={...})`，可做短期一次性路由建议。不要传递含token的 `wallet_url`、充值链接、个人账号、API Key 或完整网页响应；函数即使收到多余字段，也**不会保存或回显**它们。调用时余额与费率必须同账户、短时效（15分钟内）才视为可供参考的报价，仍标为 *HOST_REPORTED、NOT_AUTHENTICATED*，不能自动授权收费调用。

例如余额为负、官方当前报价 `Agent>0`、`Fetch=0`：只向付费Agent显示 `INSUFFICIENT_WALLET_BALANCE`，给Fetch显示 `HOST_REPORTED_ZERO_LISTED_PRICE_NOT_AUTH_VERIFIED`。**后者绝不是可用成功**：仍需实际连接、项目策略、URL许可、账号认证、速率/每日免费配额与来源支持。旧快照不允许决定新调用费用。除非项目另有已批准的宿主级真实预算Broker，直接官方 MCP Agent 不受 Lite 的本地预约账本约束，故本插件不会自动触发。

## 2. Serper — 适合常规 Google 结果检索

官方站点/注册/API Key：https://serper.dev/ 。独立的 `SERPER_API_KEY`，不能拿 SerpApi Key 混用。

- Lite 已在同一个受管 `marketops_search` 中支持 Web、Maps/Places、Images、News、Videos、Shopping、Scholar、Patents、Autocomplete 等常用官方Google API类别；所有这些仍只是**发现候选**，支持并非账号鉴权或实时调用验收。
- 默认使用受管的 `marketops_search(provider="serper", ...)`：以实际账户成功查询扣费额度/活动赠送额度为准，研究账本的**调用次数**不等于美元成本。具体端点新增前要核对请求和返回 schema。
- 没有核实“Serper 自家官方维护的 MCP/Skill”。社区 MCP 是可选能力，不要称为官方、也不要在没有单独费用保护时直接启用。
- 额度不足/429/401：停止该方法，使用已经连接且授权的其它来源；**不能自动换 SerpApi 扣第二笔钱**。

**内嵌MCP使用步骤**：用户只安装 MarketOps，无须在 Claude/Codex 额外安装 TinyFish 宿主插件。用户可在受保护的客户配置中选择 `TINYFISH_API_KEY`（内嵌 Client 用 Bearer，需实际厂商鉴权验收），或者由本人在**本机终端**执行安装后 receipt 里的 `research_official_mcp_auth.py login --provider tinyfish` 完成 OAuth，Token 只存可用的安全 OS Keyring；没有安全 Keyring 就明确失败，不能存到Git/客户SQLite。平台OAuth和网站 Browser Context Profile 是两层独立权限。普通 Search/Fetch不调用按步收费Agent；Agent只有在用户明确批准钱包、调用步骤和总额限制后才可执行。更多见 [EMBEDDED_OFFICIAL_MCP](EMBEDDED_OFFICIAL_MCP.md)。

## 3. SerpApi — 多引擎按需，而非一个引擎一个 MarketOps 工具

官方注册/API Key：https://serpapi.com/users/sign_up 和 https://serpapi.com/manage-api-key 。通过 `SERPAPI_API_KEY`。官方 API：https://serpapi.com/search-api 。

- Lite 的同一个 `marketops_search(provider='serpapi', mode=web|maps|images|news|videos|shopping|scholar|patents|autocomplete)` 覆盖常用Google专门引擎并生成发现候选；其余多引擎可通过 `marketops_serpapi_mcp_search` 直接走本插件内嵌官方MCP，仍需项目鉴权与账本约束。
- **当前默认内嵌调用**：`marketops_serpapi_mcp_search(run_id,operation_id,params={engine:'google_light',q:'...'} )`，通过MarketOps本地MCP Server内部的Python MCP Client连接官方 `https://mcp.serpapi.com/mcp`，从客户安全配置读取Bearer API Key。完整多引擎参数由官方服务端工具Schema决定；该调用已经进入MarketOps Run存储与请求预约，但用户真实账号/配额尚需验证。
- 官方 **[SerpApi MCP](https://serpapi.com/integrations/mcp)** 或 **[Claude Code Plugin](https://github.com/serpapi/serpapi-claude-plugin)** 可按引擎 schema 使用 100+ 搜索引擎（包括购物、新闻、职位、酒店、机票、Scholar 等）。要覆盖它们，**接官方通用搜索工具而不是维护100多个同名包装**。
- 官方托管 MCP 将 API Key 置于连接 URL 路径的官方示例；**不要将带密钥 URL 写入 Git、聊天、实验日志、普通 MCP server 输出**。使用客户端受保护的连接配置或自行受管的官方 MCP，并遵循所在客户端真实密钥配置方法；不自动创建连接。
- 优先选合适引擎/Google Light（若已核验）及必要字段；未知结果或429先保留已预订操作，不能默默重试/双发。

## 4. Crawl4AI — 本地 SDK、自托管服务、托管云是三种不同接入

项目/安装：https://github.com/unclecode/crawl4ai 、https://docs.crawl4ai.com/ 。Cloud账号/key：https://crawl4ai.com/ ，官方Cloud API/MCP：https://api.crawl4ai.com/mcp 。

| 形式 | 所需认证 | 当前与建议 |
|---|---|---|
| 本地 Python SDK | **无需云API Key**，但需 `crawl4ai`、Playwright/Chromium、系统依赖 | Lite 已有 `marketops_crawl(local)`，优先跑本地；可通过 `crawl4ai-setup` / `crawl4ai-doctor`（用户允许安装时）检查 |
| 自托管 Docker server/MCP | 主机算力，Docker 服务及独立的 `CRAWL4AI_API_TOKEN` | **可选**，工作量增长、共享浏览器池时才部署；Docker 不是“自动就在本机” |
| Cloud REST | `CRAWL4AI_API_KEY` / Bearer token | Lite 有基础 `/scrape`，更多 `/extract`、`/search`、`/serp`、`/answer`、batch/jobs 可接**官方 Cloud MCP**，需费用和Schema独立确认 |
| Cloud 官方 MCP | 官方Cloud账号/Key + 独立 MCP 连接 | 只在项目启用时按需加载；不把 Cloud 与本地 MCP 工具名、会话、费用混同 |

默认本地 SDK，处理普通公开网页；复杂批量用已批准的自托管；Cloud 是可选按信用额度计费的升级路径。Cloud 可能提供赠送 credits，但**credits 与免费不限量是两回事**。

## 5. TypeSafe Jev — Choice、Score、Noul 不是爬虫

官方 Console/API Key：https://console.typesafe.ai/ ，文档：https://docs.typesafe.ai/introduction/quickstart ，SDK：https://docs.typesafe.ai/sdk/python ；官方Skill：https://github.com/typesafe-ai/skills 。

- 独立 `TYPESAFE_API_KEY`，Lite 使用官方 Python `typesafe_sdk` / `TypeSafeClient`，现统一用一条 `marketops_jev_choice` 工具、`primitive=choice|score|noul` 参数；名称为兼容旧客户端保留，不意味着只支持 Choice。
- **Choice** 分类、**Score** 有序尺度评分、**Noul** 真假概率。三种可按同一原始证据批量组合（官方 SDK 支持），但不要把推理概率冒充源事实、法务/采购批准。
- 只有确定性去重/规则处理后仍存在明确语义歧义、拥有真实证据且用户允许付费调用时才使用；账户计费未核实则禁自动消耗。

## 6. 多处鉴权与项目权限边界

MarketOps **没有一个万能 API Key**。至少有：

1. 用户本地 MarketOps 配置：数据根和经验文件（不等于对外联网授权）。
2. 五家 Provider 各自 API Key / 官方 MCP OAuth 授权；按项目与客户端安装，有无应分别显示。
3. 资金/配额：项目可使用哪些供应商、全局和单Run限额、是否允许每一步计费、未知余额是否拒绝。
4. 站点权限：该项目可研究哪些域名、是否公开访问、是否可以使用现有登录态；Site Profile 与 Provider Token 不同。
5. 业务动作：只读网页采集 ≠ 发送邮件/填表单/付费购买/绕访问墙的授权。

若账户显示已接入 TinyFish MCP，但 `run_web_automation` 缺额度，正确降级：先尝试已核验免费 `fetch_content` → 已授权本地 Crawl4AI → 其他合法公开来源 → **HOLD**。不得静默改用另一个收费的官方 MCP。

## 7. 推荐验收顺序（零付费优先）

1. **静态**：源码编译、Skills链接、setup dry-run、MCP tools/list。
2. **本地**：`marketops_capabilities` 显示 SDK/Key 可配置、预算reserved/remaining、其他宿主工具列表**未核实**。看提示不意味着鉴权成功。
3. **连接器只读**：官方 MCP 的 tools/list、账户/钱包/配额，**不得**创建收费 Browser/Agent 试验以“验证”。
4. **合成负面**：余额不足、401、402、429、重复请求/超时、登录过期、访问墙；停下、保留来源、精确原因码、说明可选降级。
5. **真实业务**：明确定额少量公开站点；先免费/本地，再有用户批准才作单次收费验证；以来源证据和研究成果衡量而非“所有五家都调用一次”。

**结论**：五家提供的是互补能力和不同认证域。多供应商完全覆盖采用“官方工具按需扩展 + 受管Lite核心 +统一业务Skill/资料Store”。某家不可用不是全插件失败，且“可发现”不等于“可调用、可付费、可合法访问目标网站”。
