# 四Provider选用合同：先看业务问题，再看可用能力

这是MarketOps编写的路由方法，不是厂商官方Skill，也没有在此实现统一MCP Server。当前客户端可使用已经配置的官方MCP、审核过的API/SDK或CLI；未来统一MarketOps MCP复用同一合同。官方事实与设计建议分开如下。

### 搜索供应商名称纠偏

**SerpApi.com 与 Serper.dev 是不同产品。** `https://serpapi.com/manage-api-key` 是 SerpApi Key 页面；`https://google.serper.dev/search` 是已有 Serper Adapter。不能用 SerpApi Key 访问 Serper，也不能在没有专用响应解析与预算验证的情况下把两家后端悄悄互换。

SerpApi 官方拥有 [托管 MCP 与 MIT 开源 MCP Server](https://serpapi.com/integrations/mcp)，适合半自动 Codex/WorkBuddy 客户端直接连接试用；它不是当前市场业务 Store 的自动写入接口，必须仍将结果经统一证据/实体流程提交。详见 [SerpApi 入门及安装](../../../providers/serpapi.md)。**现有四种研究能力仍是搜索、抓取、交互、判断；搜索槽可以选择 Serper 或 SerpApi。**

## 1. 来源与接口事实（2026-10-15核对）

| 项目 | 已核对来源 | 可使用形态 | 不该声称 |
|---|---|---|---|
| Serper | https://serper.dev/ | 官方搜索API，展示Search/Maps/Places；MCP可经MarketOps Adapter或另行审核的社区封装 | 未确认官方MCP，不把社区封装称官方 |
| Crawl4AI | https://docs.crawl4ai.com/core/self-hosting/ ，https://docs.crawl4ai.com/ | 官方开源SDK/自托管MCP，Cloud MCP另一套接口 | 不把Cloud和自托管工具名/凭据/传输混用 |
| TinyFish | https://github.com/tinyfish-io/tinyfish-cookbook/blob/main/skills/use-tinyfish/SKILL.md （blob `5b769d783895732d61caccf03cd24ee52761a51a`）；官方INSTALL.md | 官方MCP/CLI；Search→Fetch→交互能力 | CLI命令不是MCP schema；不能假定换接口OAuth通用 |
| Jev/TypeSafe | https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md （blob `0109513f9656917dc93cbc5ecddfca465a53ce66`） | 官方Skill + SDK/API；MarketOps已有Jev adapter可作为受管工具接线基础 | Skill本身不提供可调用服务；不称第三方Jev站点为TypeSafe官方 |

本次TypeSafe live docs读取失败，官方Skill读取成功；不猜测最新模型别名/版本依赖参数。Crawl4AI自托管文档列出md/html/crawl等工具，Cloud另有scrape/extract；执行时必须重新列出本连接实际schema。价格与限额不复制为恒定事实，查用户当前账户；自托管也有算力费用。

## 2. 运行前Capability Map

每个能力记录 `provider, connection, interface, actual_tool, schema/version, configured, auth_checked, invocation_tested, allowed_scope, budget_ref, material_kind`。configured、connected、auth_verified、called、business_result_verified是不同状态。未连通不假装有工具，缺凭据不引导把密钥粘进聊天。

一插件不等于四个供应商凭据；最少权限逐家配置。客户直连的供应商调用不能宣称已经由MarketOps硬控费用。统一MCP将来负责硬预算，Skill只指导行为。

## 3. 建议策略（不是强制四步串行）

| 当前问题 | 优先动作 | 合法升级条件 | 返回并保存 |
|---|---|---|---|
| 未知来源/地区候选 | Serper Search/Maps/Places、已配置 SerpApi 官方 Search/Maps MCP 或已批准TinyFish Search | 覆盖不足且新查询有具体依据 | query/locale/source URL/候选，不写verified |
| 已知URL，需要正文 | 复用有效缓存；Crawl4AI或TinyFish Fetch | 确认是JS/交互限制而非解析bug | 单URL状态、正文材料、时间、变换/出处 |
| 结构稳定的多页目录 | Crawl4AI批量/非LLM抽取 | 小样本字段保真后，预算内扩展 | 原始行、规范字段、每行来源、游标 |
| 必须点击筛选/分页 | TinyFish交互，明确只读目标和输出结构 | 已批准网站、动作、费用；不跨访问限制 | provider run id、结果、步骤、失败/未知用量 |
| 语义归属/资格/角色存疑 | Jev对已有候选和材料做Choice/Noul/Score | 规则可表达且证据足够，阈值有业务验证 | 原始概率、题面/模型版本、输入证据 |
| 行数、去重、join、预算判断 | 确定性代码/存储查询 | 无需模型 | 可复算计数或拒绝 |

四家接入能力要分别验收，但不要求每个Run调用四家。路由根据数据缺口、质量、权限和实际成本，不是固定“Serper永远最低价”。不能把厂商Skill“所有网页都用我”带成全局优先级。

## 4. 先诊断再升级

分类：DNS/迁移、超时/服务失败、鉴权、真实访问受阻、JS/分页、抽取丢字段、schema漂移、落库失败、证据不支持。先区分“取得了但没解析”“解析了但没保存”和“根本没取得”。不要为抽取器吞mailto/gzip/编码问题重复购买浏览器请求。

每次切换记录候选工具、选中原因、未选原因、实际收益和停止条件。只对有新依据且在批准重试预算内的请求重试。已受理但状态未知先查询job，不自动重发。明确权限/验证码/登录墙不靠另一个Provider绕过，换获准公开来源或人工处理。

## 5. 统一写回与复盘

调用完成→检查材料→按统一Schema提交候选/观察→读回实体/证据→登记每个输入的处置。结果和资料留客户store；operation记录进入本轮经验包。模型判断、内容真实性、调用成功和写入成功四者分别记录。

官方TinyFish CLI建议lightest→heaviest；TypeSafe建议已知规则/计算留代码、独立题同批、低置信度按后果处理。本版把这些方法放到MarketOps范围约束之内，不继承自动安装、付费升级或访问绕过指令。
