# MarketOps Research Assistant 0.5.7-rc6

**Release candidate — 不是 0.5.5，也不是正式发行版。** 本包只用于 RC 验收；不得覆盖或重命名既有的 0.5.5 ZIP，不得据此宣布正式发布。正式发布前仍需完成真实供应商费用与客户授权验收。

把市场研究方法、五个项目的使用经验、集中证据与交付放在同一个插件中。人确定目标和有限额度，Agent 连续研究、保存证据、交付结果，再将方法和反馈用于后续任务。

## 安装和首次启动

从 [START_HERE](START_HERE.md) 开始。Windows x64 双击 `Install.cmd`；Linux x64 使用 `install.sh`。运行时为 Python 3.12。安装器在目标电脑生成 Codex、Claude Code 或 WorkBuddy 配置；WorkBuddy 导入须在客户端完成。

[四账户指引](references/FOUR_ACCOUNT_START.md)提供注册、API Key、官方赠金及余额入口：Serper、SerpApi、TinyFish、TypeSafe。Crawl4AI 在本地运行，无需第五个账户。Key 在本地隐藏输入，保存在系统凭据库，重启后读取。

**TinyFish 和 SerpApi 的官方 MCP 客户端直接随包安装。** 宿主只连接一个 MarketOps MCP，不必再分别安装两个厂商插件。首次设置检查实际连接、已批准网站和有限请求数。已有账号检查剩余额度，不自动充值或重新领取新用户赠金。

## 从需求到有效结果

1. `marketops_runs` 查已有任务；优先恢复。同类新批沿用确认过的 scope 和规则版本。
2. 保存 Brief：地区、目标单位、角色、数量与证据要求。
3. 召回适用经验，检查当前能力；优先复用材料，再搜索、抓取和判断。
4. 保存场地、组织、责任关系、人员/部门与公开联系渠道；关键字段带同源引文。
5. 检查证据缺口，每个输入目标都保留处置，区分已取得结果、HOLD、排除和重复。
6. 交付九表 XLSX、CSV、来源/hash 清单与经验汇总，并重新读取核对。

搜索摘要只用于发现。抓取成功、文本匹配、模型判断都不自动证明业务事实。联系人和角色必须有可回读原文及归属证据；输出供业务复核，不授予正式 SOP 等级或外联权限。

## 五个项目和降级

| 项目 | 用途 | 连接 |
|---|---|---|
| Serper | 来源搜索 | 受管 REST |
| SerpApi | 已审核搜索引擎 | 内嵌官方 MCP |
| TinyFish | 搜索、网页提取 | 内嵌官方 MCP |
| Crawl4AI | 本地浏览器抓取，保存渲染 HTML | SDK + Chromium + 受限出口 |
| Jev / TypeSafe | 对保存的材料做有界语义判断 | SDK |

`marketops_research_step` 在已准入服务之间进行有限降级，并记录原因和每次结果。最多三次尝试；未知消费/超时、预算耗尽和明确访问限制不会触发盲目重试。语义服务不可用时保留待核实。见[供应商选择](references/PROVIDER_ROUTING.md)。

## 集中数据与经验迭代

首次设置研究数据根和独立的 `Research_Experience.md`。任务、原始材料、候选、调用台账、反馈、导出都持久化；经验 Markdown 是可收取的阅读视图。升级保留数据，同根可明确续授权，累计消耗不会清零。多电脑不能共享 SQLite 文件并发写入。

每条经验保存现象、动作、结果与证据。后续任务按范围、规则、阶段和供应商召回，采用后记录 `helped / not_helped / contradicted / unknown`。反例改变后续方法建议，历史不覆盖。历史方法与反馈是有条件建议，不改写 SOP、权限或额度。

## 验证与发行证据

`references/BUILD_QA.json` 由真实测试生成，绑定发行源码摘要；缺失或漂移会明确报告。安装器实测依赖与 stdio 启动，完整安装还测试本地浏览器。安装、供应商认证、研究质量和原生客户端验收分别报告。

提效按同范围的有效结果、证据、请求数、耗时和人工介入测量。历史网页可能变化，任务壁钟不等于人工工时；不能用单测数量推算提效比例。

更多：[工具目录](references/MCP_TOOL_CATALOG.md)、[业务手册](references/BUSINESS_PLAYBOOK.md)、[经验协议](references/EXPERIENCE_PROTOCOL.md)、[安装详解](references/CUSTOMER_QUICKSTART.md)。
