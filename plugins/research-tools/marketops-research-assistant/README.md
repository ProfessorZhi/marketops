# MarketOps Research Assistant 0.5.11-rc13

**Release candidate — 不是正式发行版。** 本包只用于 RC 验收；不得覆盖或重命名既有的
0.5.5 ZIP，不得据此宣布正式发布。正式发布前仍需完成真实供应商费用与客户授权验收。

版本号以 `.claude-plugin/plugin.json` 为唯一权威（Claude Code 读取它，而不是 git tag）。
每次发版必须递增该字段，否则 `claude plugin update` 会判定「已是最新」而静默不更新。

把市场研究方法、六个项目的使用经验、集中证据与交付放在同一个插件中。人确定目标和有限额度，Agent 连续研究、保存证据、交付结果，再将方法和反馈用于后续任务。

## 安装和首次启动

从 [START_HERE](START_HERE.md) 开始。Windows x64 双击 `Install.cmd`；Linux x64 使用 `install.sh`。安装器装好 Python 3.12 私有运行时，并在目标电脑生成 Codex、Claude Code 或 WorkBuddy 配置；WorkBuddy 导入须在客户端完成（尚未在真实 WorkBuddy 上验证）。

Claude Code 也可以从仓库的 marketplace 装插件：

```bash
/plugin marketplace add ProfessorZhi/marketops
/plugin install marketops-research-assistant@marketops
```

市场版只拷贝插件、不带运行时：先跑一次上面的安装器（或设 `MARKETOPS_RESEARCH_PYTHON`），否则不会出现 `marketops_*` 工具，会话开始时插件会提示缺什么。Windows 上市场版不能单独使用（MCP 用 `sh` 启动）。

### 本机环境体检（0.5.11-rc13 新增）

"装好了却用不了"多数时候不是插件坏了，而是**跑服务的那个解释器**缺东西——工具起不来、
读页降级到付费路、或者停在 HOLD。`marketops-doctor` 技能对应
`scripts/research_doctor.py`：它从解释器外部探服务真正会用的那个，Key 只报名字不打印值，
`--repair` 只做不丢数据的修复（建已配置的数据根、复位 crawl 缓存），`--install` 才装包且
装进目标解释器。退出码 `20` 表示还有 blocker。详见 `skills/marketops-doctor/SKILL.md`。

### 名录页提取（0.5.11-rc13 新增）

名录、协会列表、组委会页面这类「一条一条印着联系人」的页，过去只能靠 `material_excerpt`
一次 1200 字地读，读完整页要几十次调用，剩下的还得 Agent 自己写解析。现在
`marketops_directory_extract(run_id, artifact_id)` 一次读完整张已保存的页：用证据门同一套解码器
和去标签逻辑取文，按页面上重复出现的字段标签切分记录，返回「单位 + 联系人 + 邮箱」成组的候选，
每条引文都是页面原文的精确片段。一页超过 20 个条目就用 `offset` 翻页，成组切分不会把一条记录拆开；
`stage=true` 直接写入候选。结果状态是 `DIRECTORY_EXTRACTED_NOT_VERIFIED`——提取不等于核实，
分数仍由场景规则在交付时判定。页型认不出来时它返回 `warnings` 和较少的记录，技能
`marketops-directories` 说明怎么判断和继续。

[账户指引](references/FOUR_ACCOUNT_START.md)提供注册、API Key、官方赠金及余额入口：Serper、SerpApi、TinyFish、TypeSafe。Crawl4AI 在本地运行，无需账户。**DeepSeek 是第五把可选的 Key**（`DEEPSEEK_API_KEY`），配了它就多一路搜索和一路读页（$0.75/1k 次，失败不计费）；不配只是这条路不出现，其余照常跑。Key 在本地隐藏输入、或由安装 agent 写进系统凭据库，重启后读取。

**TinyFish 和 SerpApi 的官方 MCP 客户端直接随包安装。** 宿主只连接一个 MarketOps MCP，不必再分别安装两个厂商插件。首次设置检查实际连接、已批准网站和有限请求数。已有账号检查剩余额度，不自动充值或重新领取新用户赠金。

## 从需求到有效结果

1. `marketops_runs` 查已有任务；优先恢复。同类新批沿用确认过的 scope 和规则版本。
2. 保存 Brief：地区、目标单位、角色、数量与证据要求。
3. 召回适用经验，检查当前能力；优先复用材料，再搜索、抓取和判断。
4. 保存场地、组织、责任关系、人员/部门与公开联系渠道；关键字段带同源引文。
5. 检查证据缺口，每个输入目标都保留处置，区分已取得结果、HOLD、排除和重复。
6. 交付九表 XLSX、CSV、来源/hash 清单与经验汇总，并重新读取核对。

搜索摘要只用于发现。抓取成功、文本匹配、模型判断都不自动证明业务事实。联系人和角色必须有可回读原文及归属证据；输出供业务复核，不授予正式 SOP 等级或外联权限。

## 六个项目和降级

| 项目 | 用途 | 连接 |
|---|---|---|
| Serper | 来源搜索 | 受管 REST |
| DeepSeek | 搜索、网页读取（另一套独立网页索引） | 受管 REST |
| SerpApi | 已审核搜索引擎 | 内嵌官方 MCP |
| TinyFish | 搜索、网页提取 | 内嵌官方 MCP |
| Crawl4AI | 本地浏览器抓取，保存渲染 HTML | SDK + Chromium + 受限出口 |
| Jev / TypeSafe | 对保存的材料做有界语义判断 | SDK |

`marketops_search(provider='all')` 把同一条 query **同时**发给 Serper、DeepSeek、SerpApi、TinyFish，按 URL 合并结果：四家是彼此独立的网页索引，各自漏的页由别家补上。某一路没额度、被拒或超时，**只有它自己退出**，本次搜索照常返回，回执里记 `search_routes` 的逐路结果（各自找到的页数、去重后贡献）。DeepSeek 只对成功且非空的结果计费（$0.75/1k 次），失败不计费；它的余额由 `marketops_costs` 直接读取账户并折算成还能买多少次搜索/读页。读页同样有第三路：DeepSeek Fetch 与 Crawl4AI、TinyFish 并读同一页。

`marketops_research_step` 在已准入服务之间进行有限降级，并记录原因和每次结果。最多三次尝试；未知消费/超时、预算耗尽和明确访问限制不会触发盲目重试。语义服务不可用时保留待核实。见[供应商选择](references/PROVIDER_ROUTING.md)。

## 集中数据与经验迭代

首次设置研究数据根和独立的 `Research_Experience.md`。任务、原始材料、候选、调用台账、反馈、导出都持久化；经验 Markdown 是可收取的阅读视图。升级保留数据，同根可明确续授权，累计消耗不会清零。多电脑不能共享 SQLite 文件并发写入。

每条经验保存现象、动作、结果与证据。后续任务按范围、规则、阶段和供应商召回，采用后记录 `helped / not_helped / contradicted / unknown`。反例改变后续方法建议，历史不覆盖。历史方法与反馈是有条件建议，不改写 SOP、权限或额度。

## 验证与发行证据

`references/BUILD_QA.json` 由真实测试生成，绑定发行源码摘要；缺失或漂移会明确报告。安装器实测依赖与 stdio 启动，完整安装还测试本地浏览器。安装、供应商认证、研究质量和原生客户端验收分别报告。

提效按同范围的有效结果、证据、请求数、耗时和人工介入测量。历史网页可能变化，任务壁钟不等于人工工时；不能用单测数量推算提效比例。

更多：[工具目录](references/MCP_TOOL_CATALOG.md)、[业务手册](references/BUSINESS_PLAYBOOK.md)、[经验协议](references/EXPERIENCE_PROTOCOL.md)、[安装详解](references/CUSTOMER_QUICKSTART.md)。
