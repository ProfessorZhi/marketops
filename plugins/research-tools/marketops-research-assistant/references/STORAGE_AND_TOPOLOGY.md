# MarketOps Research Assistant — 完整拓扑、数据区和经验记录

> 2026-10-15。现有 v0.2.x 是 Skills、可选单家供应商连接、本地 CSV 交接包和**已接到本地命令的事件记录器**。未来 v0.3 的统一 Research MCP / Market Graph 仍待实现；此文区分当前和目标，不伪装为已上线。

## 1. 插件源码在哪里，用户数据放哪里？

- **插件源码、Skills、安装包装**：ProfessorZhi/marketops 仓库的 plugins/research-tools/marketops-research-assistant/。源文件属于版本化发布物。
- **用户数据**：必须放在客户自己的、Git 仓库以外的目录。推荐明确设置环境变量 MARKETOPS_RESEARCH_DATA_ROOT；不能把密钥、业务原文和执行日志提交到 Git。
- Windows 试用目录示例：%LOCALAPPDATA%\MarketOps\Research\
- macOS 试用目录示例：~/Library/Application Support/MarketOps/Research/
- Linux 试用目录示例：~/.local/share/marketops-research/
- 服务器：由部署者配置的专属持久数据卷，独立非特权服务账号持有。

上述都是**建议位置**，不是在 Member-A 的设备上已查到的真实路径。MarketOps 后端还有自己的 MARKETOPS_DATA_ROOT；切勿擅自将客户试用库指向生产库。

当前的文件形状与未来扩展：

~~~text
<customer-data-root>/
├── state/
│   └── research-events.sqlite3         已实现；本地工具第一次自动写回时创建
├── runs/
│   └── <request_id>/                   用户明确 init 后建立
│       ├── intake.json
│       └── stages/*.csv                当前 11 张单写者交接表
├── experience/                         目标约定目录；由操作者指定输出位置
│   └── <run_id>/
│       ├── experience.json             draft 命令生成的候选经验包
│       └── REVIEW.md                   render 命令生成的阅读版
├── artifacts/                          目标：原始与变换后资料按来源/hash保存
└── exports/                            目标：Excel、CSV和交付清单
~~~

**已存在并自动写入的只有 event journal 的基础路径**；研究包由明确的 init 创建。其它目录不是安装后自动存在。CSV不是多人共享的权威数据库，不能用网络共享盘让几个Agent并发写CSV/SQLite。

## 2. 总拓扑与 MCP 角色

~~~mermaid
flowchart TD
 subgraph HOST["Agent 宿主：MCP Client"]
   COD["Codex"]
   WB["WorkBuddy"]
   DSH["DeepSeek Harness"]
 end
 HOST --> SK["MarketOps Skills：Member-A业务 + 工具选择 + Excel + 复盘"]
 SK --> G["MarketOps Research MCP Server（目标：尚未注册）"]
 G --> AUTH["认证 / Scope / Run / 成本与晋升门禁"]
 AUTH --> ROUTE["Provider Router"]
 ROUTE --> S["Serper 官方托管 Search API"]
 ROUTE --> C["Crawl4AI 开源本地 / Docker 或 Cloud MCP"]
 ROUTE --> T["TinyFish 官方托管 MCP / SDK / API"]
 ROUTE --> J["Jev TypeSafe 官方云 API / 开源 SDK"]
 AUTH --> DB[("统一 Market Graph + Evidence Store（目标）")]
 AUTH --> EV[("本地 SQLite Event Journal（基础代码已入库）")]
 EV --> X["复盘经验候选 / 人工审查"]
 DB --> OUT["Excel / CSV / 研究报告"]
 X -. "经验证且获准" .-> SK
~~~

现阶段完整 Research MCP 网关 G 仍未实现，但 v0.3.0 已新增**局部双搜索 stdio MCP 候选**，可调用 SerpApi/Serper 并保存搜索发现回执与事件。，所以用户分别接入已授权的厂商 MCP/API，Skill 只能指导操作。**无法自动观察直接从 Codex/WorkBuddy 发往厂商的全部调用。**

未来统一 G 的双重角色：对 Codex/WorkBuddy/DSH，它是 **MCP Server**；向官方 TinyFish、Crawl4AI 等 MCP 提供方调用时，它又是 **MCP Client**。若供应商只提供 HTTP/SDK，则网关用普通 HTTP/SDK Adapter，而不需要臆造官方 MCP。

## 3. 四个项目：源码、本地部署、MCP 的区别

**搜索槽有两家容易混淆的候选供应商。** 当前代码是 Serper，用户提供的 Key 管理地址属于 SerpApi。后者官方拥有托管 MCP、MIT 开源自托管 MCP 包装，但实际搜索数据仍来自其收费云 API。二者不能共享 Key。比较见 [SerpApi 官方 MCP 说明](../../providers/serpapi.md)。



| 项目 | 源码情况 | 可自托管核心能力？ | MarketOps 目标调用方式 | 当前客户独立接入 |
| --- | --- | --- | --- | --- |
| SerpApi.com（搜索槽可选替代） | 官方 `serpapi/serpapi-mcp` MIT 开源 + 托管搜索服务闭源 | **MCP 包装可自托管；真实搜索仍依赖 SerpApi 订阅 API** | 官方 `https://mcp.serpapi.com/mcp` 或自托管官方 MCP，后续可新增正式 HTTP Adapter | 官方 MCP / Codex 内置连接命令；WorkBuddy 待认证实测 |
| Serper.dev | 搜索服务闭源；存在第三方 MCP 的可见源码，但不能冒充官方，许可证要逐项目核对 | 没有确认可自部署的 Serper 搜索服务 | 现有 Serper HTTP API Adapter | 个人 API/经审核的社区 MCP |
| Crawl4AI | 官方开源核心，Apache-2.0；Cloud 是另一个托管产品 | **可以**，Python 或 Docker 服务，也提供官方自托管 MCP 能力 | 本地爬虫/自托管 MCP，或 Cloud MCP | 官方自托管服务或 Cloud MCP |
| TinyFish | 官方 Cookbook/安装器/Skills 源码开放（Cookbook MIT）；核心浏览器 Agent 托管服务不因此开源 | 未确认托管 Agent 可自行部署 | 官方托管 MCP（由我们的网关做上游 Client）或官方 SDK/API | 官方 https://mcp.tinyfish.ai，OAuth 或 API/CLI |
| Jev / TypeSafe | 官方 Skills、Python/JS SDK MIT；Jev 模型本身闭源 | 没有确认官方开放模型自托管 | 现有 Jev/TypeSafe SDK Adapter、HTTP API；封装为我们自己的 MCP Tool | 官方 Skill 是使用方法，不是现成的 Jev 推理 MCP 服务 |

代码开源、SDK开源、MCP可连接、模型权重开源、API免费是五件不同的事情。自托管 Crawl4AI 仍需要安全隔离和浏览器计算资源。

官方资料：Crawl4AI https://github.com/unclecode/crawl4ai / https://docs.crawl4ai.com/core/self-hosting/ ；Serper https://serper.dev/ ；TinyFish https://github.com/tinyfish-io/tinyfish-cookbook ；TypeSafe https://github.com/typesafe-ai/skills 和 https://github.com/typesafe-ai/typesafe-sdk-python 。

## 4. 经验到底如何自动存？

已经加入 scripts/research_event_journal.py：

- 用客户自选 root 下的 state/research-events.sqlite3 建立追加式 SQLite WAL 日志。
- 记录 run_id、operation_id、stage、kind、status、provider、receipt_ref、reason_code、timestamp、事件体SHA256；字段采用受限的非敏感 ID，**不接收全文、邮箱、URL、Cookie、密钥或隐藏推理**。
- 相同 operation/event 的重放幂等；同标识而内容改变会拒绝，避免把不同结果静默覆盖。
- **已加自动挂钩**：通过本插件的本地 research_pack CLI 初始化研究包、通过 Excel-safe CSV exporter 导出时，自动保存各自操作事件。
- 可以用显式 record 命令临时补录其他操作，也可以用 draft 命令从 blocked/failed/hold/unknown 事件生成尚未审批的 experience.json。
- 当前事件日志只证明“该工具调用记录了某状态”，**不能**自动证明供应商真的完成查询、原始资料真实可用或经验已具备普适性。

**尚未做的自动化接口**：四个 Provider 的统一 MCP 运行时尚不存在，不能强制旁路调用写日志。未来必须将事件钩子放在网关每个真实 Provider 调用、证据落地、实体晋升、人工复核和导出事务中；一条调用成功而写日志失败不能被报告为可审计的成功。

手动测试命令（在插件目录下，Linux/macOS 示例）：

~~~bash
export MARKETOPS_RESEARCH_DATA_ROOT="$HOME/marketops-research-data"
python scripts/research_pack.py init "$MARKETOPS_RESEARCH_DATA_ROOT/runs/devon-001" --request-id devon-001 --country GB --scene gb-natural-grass --objective "Public sports grounds"
python scripts/research_event_journal.py --data-root "$MARKETOPS_RESEARCH_DATA_ROOT" list --run-id devon-001
python scripts/research_event_journal.py --data-root "$MARKETOPS_RESEARCH_DATA_ROOT" record --run-id devon-001 --operation-id fetch-001 --stage acquisition --kind tool --status blocked --provider crawl4ai --receipt-ref receipt-001 --reason-code challenge_detected
mkdir -p "$MARKETOPS_RESEARCH_DATA_ROOT/experience/devon-001"
python scripts/research_event_journal.py --data-root "$MARKETOPS_RESEARCH_DATA_ROOT" draft --run-id devon-001 --scope gb-natural-grass --rule-version approved-sop-v1 --skill-version 0.2.2 --output "$MARKETOPS_RESEARCH_DATA_ROOT/experience/devon-001/experience.json"
python scripts/experience_pack.py validate "$MARKETOPS_RESEARCH_DATA_ROOT/experience/devon-001/experience.json"
~~~

这是**自动落事件、可重复生成候选经验**，不是自动生成可信因果知识。最终经验由 marketops-research-retrospective 对真实证据、反例与回放进行分析；自动生成的内容必须停在 candidate，人工批准才能修改 SOP/Skill。

## 5. Lite 和未来正式服务的数据权威

- Lite：本地受控 Research Pack 是业务交接表；SQLite Event Journal 是**观察与经验元数据**，不是第二套联系人母池。当前仍缺“一个可并发查询的权威业务 Research Store”。
- 正式 MarketOps 服务：用已有 MARKETOPS_DATA_ROOT/state/marketops.db 和 Evidence Store 作为业务唯一真源，把事件记录合并到同一服务的持久事务模型里；不要把 Lite 的 events.sqlite3 当作永远独立的第二个权威。
- 多设备共享：应由一台有认证的 MarketOps MCP 服务写数据库，不在多个 PC 之间共享 SQLite 文件。
- MCP 工具只能约束通过它的请求；本机 Codex/WorkBuddy 还可能直接读写文件。客户数据隔离仍需操作系统账号、ACL、受限进程和备份/保留策略。运行目录和 Secrets 必须隔离；不在 Git 里保留真实客户语料。
