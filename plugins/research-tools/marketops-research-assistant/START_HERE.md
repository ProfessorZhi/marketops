# MarketOps 调研插件：从这里开始

版本 **0.5.11-rc11**，适用于 Windows 10/11（64 位）。可以装进 **Codex、WorkBuddy、Claude Code、DeepSeek Harness**，以及其他支持 MCP 的 agent。

## 它能做什么

你告诉 agent 一个调研需求，比如「荷兰的高尔夫球场，找 head greenkeeper 的邮箱」。插件自动完成下面这条链：

**需求 → 目标名单（母池）→ 场地/组织 → 关键角色和联系方式 → 证据核对 → Excel 交付表 + 报告**

- **六个服务一起用**：Serper、DeepSeek、SerpApi（含地图）、TinyFish 同时搜索找目标；Crawl4AI 读网站（本地运行，免费），读不了的交给 TinyFish 或 DeepSeek；Jev 判断「是不是目标」「名录里的邮箱属于哪个俱乐部、哪个人」以及「这个邮箱属于什么角色」。
- **每个邮箱都有原文证据**：只收网页上真实出现过的邮箱，绝不编造。被拦截的网站会标 HOLD，并写明原因。
- **多个对话可以同时干**：所有对话共用本机同一个数据库。一个对话负责建名单，其他对话各自领取一批目标，互不重复、互不覆盖。
- **长时间自己跑**：不会每做一点就停下来问「要不要继续」，只有在授权、预算、业务定义这几类事情上才会问你。
- **不联系名单（No-Go）**：退订、投诉、竞品等名单导入一次之后，所有交付都会自动排除这些联系人。
- **自动积累经验**：遇到网站拦截、工具出错、你纠正它的地方，agent 都会自动记下来，以后的调研会参考。每次结束时会生成一个**反馈包（zip）**，请把它发给插件维护者，用来改进插件。

## 一、安装（大约 10–20 分钟，只需一次）

1. 把整个压缩包**解压**到一个普通文件夹，比如「下载」文件夹里新建的 `MarketOps安装包`。不要直接在压缩包里双击运行。
2. 双击 **`Install.cmd`**，按提示操作：
   - 没有 Python 3.12 时，会问是否自动安装，输入 `y` 回车。
   - 「Install directory」直接回车，使用默认位置。
   - 「Add this install to your global Codex CLI now?」用 Codex 就输入 `y`。
   - 依次输入 API Key：Serper、SerpApi、TinyFish、TypeSafe 四家，最后一项是**可选的 DeepSeek**（没有就直接回车跳过，只少一路搜索和一路读页）。输入时屏幕上**不会显示字符**，这是正常的；粘贴后直接回车即可。Key 存在 Windows 凭据管理器里，不会写进文件或安装目录。
   - 如果让 agent 代装（见 `AGENT_INSTALL.md`），Key 直接发给它就行，安装器里这些项一路回车跳过。
   - 「研究数据保存目录」「经验汇总文件」直接回车，使用默认位置。
   - 「现在开通联网调研？」输入 `y`；「允许抓取……公开网站」输入 `y`；「请求次数上限」直接回车（默认 2000）。
3. 安装完成后会自动打开一份**本机专属**的 `START_HERE.md`，里面是在 Codex / WorkBuddy / 其他 agent 中启用插件的具体步骤和路径。

也可以让 agent 帮你装，见 `AGENT_INSTALL.md`。

## 二、申请 API Key（每个大约 2 分钟）

| 服务 | 去哪里申请 | 新用户额度 |
|---|---|---|
| Serper（谷歌搜索） | https://serper.dev/ → Sign up → 登录后首页复制 API Key | 2,500 次免费查询，不用绑卡 |
| SerpApi（谷歌地图 / 搜索） | https://serpapi.com/users/sign_up 注册 → https://serpapi.com/manage-api-key 复制 | 免费版每月 250 次 |
| TinyFish（搜索 + 网页读取） | https://agent.tinyfish.ai/sign-up 注册 → https://agent.tinyfish.ai/api-keys 创建 Key | 搜索和读取免费；Agent 收费功能默认关闭 |
| TypeSafe / Jev（智能判断） | https://console.typesafe.ai/ 注册 → 按页面指引创建 API Key | 以控制台显示为准（Settings → Billing） |
| DeepSeek（**可选**：多一路搜索 + 一路读页） | https://platform.deepseek.com/ 注册 → https://platform.deepseek.com/api_keys 创建 Key | 按量付费，$0.75 / 1,000 次，失败不计费；不配不影响其余服务 |

Crawl4AI 在本机运行，**不需要账号**。前四家凑齐就能跑完整调研；DeepSeek 只是让搜索多一路独立索引、读页多一路候选。更详细的说明见 `references/FOUR_ACCOUNT_START.md`。

以后想换 Key：在安装目录的 `START_HERE.md` 里找到「本地账号与数据设置」那条命令，重新运行即可。

## 三、开始用：直接对 agent 说

**一次普通调研**

> 用 MarketOps 调研：<地区> 的 <目标类型>，大约 <数量> 个，要找 <角色1> / <角色2> 的邮箱。我们卖的是 <产品>，买它来用的才是客户，卖同类产品的是竞品要排除。预算和范围已授权，不要停下来问我，跑完给我 Excel 交付表、报告和反馈包。

**多个对话一起干（目标很多时）**

- 第一个对话：「用 MarketOps 做总控：为 <任务> 建共享目标池，名称 <pool 名>，建好后告诉我每个对话该说什么。」
- 其他对话：「用 MarketOps 领取共享池 <pool 名> 的目标，持续调研直到池子做完。」

**导入不联系名单**

> 把这份退订 / 投诉 / 竞品名单加进 MarketOps 不联系名单，来源写「<来源>」：<粘贴邮箱或域名>

**要反馈**

> 用 MarketOps 生成反馈包。

## 四、以后怎么更新

插件发布新版本后（agent 每次开始调研时会自动检查并提醒你），对 agent 说一句：

> 把 MarketOps 更新到 GitHub 最新版本：运行 MarketOps 文件夹里的 Update.cmd，完成后重启你自己，再调用 marketops_setup_status 确认版本号和各服务都就绪。

也可以自己双击 `%LOCALAPPDATA%\MarketOps\Update.cmd`。

- 新版本装在旧版本旁边，自检通过后才切换。失败就保留旧版本，什么都不改。
- 调研数据、Key、经验文件都不在安装目录里，更新不会动它们。
- 新版本有问题时，运行 `Update.cmd --rollback` 回到上一个版本。
- 更新后重启 Codex / WorkBuddy / Claude Code 即可。WorkBuddy 如果仍显示旧版本，在连接器管理里从 `MarketOps\current\connectors` 重新导入一次。

## 五、遇到问题

- 安装中断了：重新双击 `Install.cmd`，会从中断的地方继续，已保存的数据不会丢。
- agent 说某个服务不可用：通常是 Key 填错或额度用完，登录对应网站看一下余额。
- agent 说 Crawl4AI（本机免费网页读取器）不能用：双击 `%LOCALAPPDATA%\MarketOps\Repair.cmd`（Mac/Linux 运行 `repair.sh`）。
  它会重新安装 Crawl4AI 和它用的浏览器，然后依次试几种启动方式：Playwright 自带的浏览器、完整版 Chromium、本机的 Chrome、本机的 Edge。
  每种方式都要真的打开一个网页再关掉，第一种成功的会被记住，以后每次读网页都用它。整个过程需要几分钟，下载约 200 MB。
  有些 Windows 电脑上 Playwright 自带的 headless 浏览器会在关闭时卡死，这种情况插件会自动换成能用的方式，不需要你手动处理。
  修好前，agent 会先问你是否临时只用 TinyFish 读网页，不会悄悄换掉。
  如果所有方式都失败，多半是杀毒软件拦住了浏览器：请把安装目录下的 `browsers` 文件夹加入信任列表，或者装上 Edge 或 Chrome，再运行一次 Repair。
- 电脑上的代理设置失效（常见于 VPN 或开发工具退出后留下的代理，表现为 502 错误）：不需要处理。
  MarketOps 自己的请求不使用系统代理；安装和更新时，如果系统代理连不上，会自动改为直接连接。
- 安装失败：屏幕上会显示一行 `INSTALL_BLOCKED: 原因: 怎么做`，完整输出保存在临时目录的 `MarketOps-install-*.log` 里。
- 公司网络必须通过代理才能上网（大多数情况不需要）：在安装目录运行
  `<安装目录>\.venv\Scripts\python.exe <安装目录>\plugins\marketops-research-assistant\scripts\customer_setup.py --proxy http://代理地址:端口`
- 其他问题：让 agent 生成反馈包，把 zip 发给插件维护者。
