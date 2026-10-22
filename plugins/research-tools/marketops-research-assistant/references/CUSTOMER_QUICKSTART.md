# MarketOps 安装与第一次调研

> 本源码包版本为 **0.5.10-rc7（release candidate）**。它不同于既有 `0.5.5` 发行包；请使用本包自己的 ZIP 与 SHA256，不要覆盖或复用 `0.5.5` 的安装目录。

一次安装包含业务 Skills、一个 MarketOps MCP、内嵌 TinyFish/SerpApi 官方 MCP 客户端，以及 Serper REST、Crawl4AI 本地 SDK 和 TypeSafe Jev SDK。**不用再安装两家供应商插件。** 四家账户由自己注册；Crawl4AI 本地无需账号。

## 1. 安装源码包

支持 **Python 3.12、Windows x64 / Linux x64**。下载并解压完整的 `*-source.zip`，保留目录结构；若提供 SHA256，先按交付清单核对。

Windows：双击根目录 `Install.cmd`。没有 Python 3.12 时会通过 winget 安装官方 Python；若机器无 winget，按提示从 [Python 官网](https://www.python.org/downloads/windows/)安装 3.12 x64 后重跑。选择固定安装目录；默认在当前用户 LocalAppData 的 MarketOps 版本目录。无需管理员权限；企业策略禁止下载时需由本机管理员处理。

Linux：先有 Python 3.12，运行：

```bash
sh install.sh /绝对路径/MarketOps-Install both
```

安装会下载 hash 锁定的依赖和 Chromium；这些软件下载不会消费研究服务额度。每个平台使用自己的 lock，结束运行 `pip check`。Codex 插件配置使用 PATH 中的裸 Python 启动包内 launcher，再通过 `MARKETOPS_RESEARCH_PYTHON` 转入本机 venv；WorkBuddy/Claude 使用各自生成的 MCP 配置。浏览器位于安装目录 `browsers/`，通过 `PLAYWRIGHT_BROWSERS_PATH` 绑定。目录存在不代表 Chromium 在该操作系统启动验收通过，查看安装回执和后续诊断。

需要自选宿主或非交互安装时，在源码包根运行：

```bash
python scripts/setup_clients.py --destination /你的/安装目录 --client all --apply --bootstrap-runtime --resume
```

Windows 用 `py -3.12` 代替 `python`，安装路径放在引号内，含空格的目录也必须完整加引号。`--client` 可选 `codex`、`workbuddy`、`both`（前两种）、`claude` 或 `all`。支持的 Codex CLI 可加 `--register-codex` 自动添加 marketplace 并安装插件。仅看计划则去掉 `--apply --bootstrap-runtime`；只装核心 SDK 可用 `--install-deps`，之后本地 Crawl4AI 仍需完整安装。

## 2. 四账户、赠金与本地 Key

查看[四账户启动指南](FOUR_ACCOUNT_START.md)，注册、按官方条件领取新用户额度、创建四个 API Key。已有账户直接使用。安装后的本地向导通过隐藏输入保存 Key 到系统凭据库，不写入插件目录；也可以把 Key 直接交给本机帮你安装的 agent，它用 `configure --stdin` 写进同一个凭据库。Key 不要发到邮件、工单、群聊或其他外部系统。

安装目录的 `INSTALL_RECEIPT.json` 包含 `customer_setup_argv` 和 `credentials_configure_argv`，根目录 `START_HERE.md` 已转换成可直接执行的本机命令。

可单独查看离线账号报告：

```bash
python scripts/provider_onboarding.py --client codex --provider all
```

请使用安装回执中的 venv Python；该报告只查本地线索，不进行真实认证，不显示 Key。`--client` 为 `claude|codex|workbuddy`，`--provider all` 显示五种能力，其中只有四家需云账户。

## 3. 选择集中数据位置

首次本地设置会询问：研究数据目录，以及单独收取的 `Research_Experience.md` 文件绝对路径。经验汇总文件放在研究目录之外；数据库、材料、证据、运行事件与导出集中于研究目录。保持它们在安装目录和 Git 仓库之外。已有配置不重复询问，也不被升级覆盖。

随后可选择本次研究服务、目标与允许域名。旧请求次数模式保持兼容；长调研推荐在明确同意后使用 `research_runtime_setup.py configure-money --confirm --spend-fraction 0.30`：每项最多分配当前余额的 30%，保留至少 70%；免费 Crawl4AI 不占金额预算。可用 `--total-usd` 与多个 `--provider-usd PROVIDER=USD` 设置更低上限。余额未知或过期的收费服务暂停，其他已授权免费工作继续。恢复会话不会重新分配预算；不重置历史账本。详情见 [长调研与金额预算](LONG_RESEARCH_AND_BUDGETS.md)。默认不开启联网，TinyFish 付费 Agent/Browser 不属于首次验收。

已有任务时继续使用**同一研究数据目录**，重新运行本地 `customer_setup.py`。向导会显示原/新累计总上限、累计已用与预约/UNKNOWN 操作，输入 `AMEND` 后才续授权；已有 Run、材料、经验及消耗不会清零。例如原上限 20、已用 7，改为 30 表示总上限 30，而不是额外增加 30。高级命令为 `research_runtime_setup.py configure --data-root <已有目录> --provider <服务> --domains <域名> --max-calls <累计总上限> --task <目标> --amend --confirm`，须使用安装后的 Python；服务可重复指定，保留本次需要的全部服务。

## 4. 在宿主启用

**Codex**：安装器检测到支持的 CLI 时执行 `codex plugin marketplace add <本机安装目录>` 与 `codex plugin add marketops-research-assistant@marketops-local --json`。旧版不支持 plugin add 时，在 Plugins → marketops-local → MarketOps Research Assistant 点击 Install/启用。完全退出重开，再建新会话。精确命令在生成的 START_HERE；不要使用不存在的 `codex plugin install/enable` 子命令。[官方本地插件说明](https://developers.openai.com/plugins/build/plugins)

**WorkBuddy 4.24.0+**：连接器管理 → 添加/导入自定义连接器，选择生成的 START_HERE 所列本机目录；若只支持 ZIP，则选该目录旁的 ZIP。启用后新建会话。不同版本界面名可能不同，导入状态在实测前保持 `PENDING_HOST_IMPORT`。已存系统凭据库时四 Key 表单可留空。**不能把本机生成的 WorkBuddy connector 给另一台电脑导入**；换机器运行 portable 源码包安装器。

**Claude Code**：按生成的 `claude_next_argv`，使用 `claude --plugin-dir <安装后的插件目录>` 打开会话。本包也保留 Claude manifest。

## 5. 启动任务与验收

新会话直接说：

> 使用 MarketOps。先检查安装、数据位置、账号和本次授权；按我的目标建立 Research Brief。先调查 3 个候选，确认场地、责任组织、相关岗位及有出处的公开联系路线。保存原文和逐字段引文，缺项标 HOLD，导出 Excel，并记录有效方法、卡点与纠正。下一批先检索这些经验。

工具列表应有 `marketops_setup_status` 和研究工具；`marketops_setup_status` 的 `provider_readiness` 列出五个服务是否 READY。随后分别检查真实连接、材料保存、Excel 回读及经验复用。工具名出现、Key 存在、静态测试通过均不能代替这一验收。发布包需要实际测试生成的 `references/BUILD_QA.json`；缺失或过期的开发包只用于诊断。

## 中断、升级与排障

- 软件下载中断：保留目录，运行相同入口或原命令加 `--resume`。仅恢复同源码、同选项、本安装器拥有的目录；陌生目录绝不覆盖。
- 换版本：使用新的安装目录，数据、系统凭据库和路径配置保持不变。源码变化导致 resume 拒绝时不要删除数据，选择新安装目录。
- 401：重新核对对应服务 Key；402/额度不足：查看该账户余额，保留结果，不自动购买。
- OS keyring 不可用：检查系统凭据服务；不使用明文文件代替。Linux 无桌面时由操作者给独立进程注入 Key。
- Chromium 缺系统库：查看对应 Playwright 诊断，由本机管理员安装所需系统库；安装器不擅自改系统软件或其他浏览器配置。

内嵌连接细节见 [EMBEDDED_OFFICIAL_MCP](EMBEDDED_OFFICIAL_MCP.md)，官方账户和活动来源集中在 [FOUR_ACCOUNT_START](FOUR_ACCOUNT_START.md)。
