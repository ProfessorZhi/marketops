# Member-A 自动化设计：证据基线、复核与研究边界

日期：2026-09-26。性质：本次有界静态审查与设计依据，不是重新执行产品验收，也不是对全部 session 的逐字复核。

## 1. 固定基线，避免再次把分支/目录当当前事实

| 对象 | 固定版本 | 本次获取方式 |
|---|---|---|
| `ProfessorZhi/marketops` 产品实现 | `df689f52e0e07dc35b88af7a3a5dd48808008a75`，PR #6 未合并 | GitHub PR/ref/file；Remote Desktop Commander 只读核对实施 worktree，HEAD 相同且干净 |
| `ProfessorZhi/marketops` main | `9347b566a65f3c3081059432ea52dd3199260d9d` | GitHub PR base；不是这次代码审查的产品实现版本 |
| `ProfessorZhi/example-session-repo` 分析 V2 | `06cdae471f794e37877637d69ea93d1c020272a2`，PR #1 未合并 | GitHub PR/file；只读核对 audit worktree，HEAD 相同且干净 |
| 冻结业务规则 | 本仓 `sources/party-group/sop/uk-natural-grass-acme-sop-v1.md`，blob `372f4f20f8ae571ba8d7392a121304252f00f0f1` | GitHub 正文读取 |

两份 PR 无需先合并才能按 SHA 引用；本轮没有合并它们。未读取或发布新的原始客户邮箱/凭据，没有上传 corpus，没有更改共享服务或已有研究工件。

### 来源分级

- **业务权威**：当前明确批准的需求、冻结 SOP 和明确有范围/主体的后续批准。
- **行为证据**：Member-A 原始对话及可回溯分析；用于决定值得消除哪类人工动作，不是自动发布新政策的授权。
- **实现证据**：固定 revision 的代码、调用路径与该 build 的运行回执。分析表写 CLOSED 不等于生产上线。
- **技术研究**：支持设计方法，不能证明 MarketOps 已省钱、提高准确率或已自动接管某职责。

## 2. 本次读取的分析资产

固定链接：

- [干预图谱](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/MEMBER_A_INTERVENTION_MAP.md)
- [78 行登记数据](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/derived/member-a-interventions.csv)
- [接管记分卡](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/MEMBER_A_TAKEOVER_SCORECARD.md)
- [会话到产品追溯](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/SESSION_TO_PRODUCT_TRACEABILITY.md)
- [分析断言审计](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/FINAL-REPORT-CLAIM-AUDIT.md)
- [方法与覆盖](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/METHODOLOGY_AND_COVERAGE.md)

本次机械读取 CSV 得 78 行：GUARD 23、POLICY 22、HUMAN_DECISION_REQUIRED 21、DETERMINISTIC_WORKFLOW 7、AGENT_ASSISTED 3、UNKNOWN 2。**这是登记条目的类别分布**。45/78 不能用来估计节省 57.7% 工时、自动化 57.7% 职责或未来收益。图谱注明是下界、不是消息总量；部分记录合并了不同动作，部分 session id 只有前缀后补零。

记分卡的 AUTO=0 是历史样本的严格判级结果，不等于当前产品没有自动化。分析 V2 修正了旧摘要的文件数、存量重复计数和若干无分母最高级说法；不能继续引用旧“最高 ROI”“可直接砍半”“两个闲置池相加”等强结论。

不以字符串搜不到 `scheduler` / `inbox` 证明功能不存在，也不以搜到类名证明功能可用。本次对关键能力读实际调用和写入路径。Golden Patterns 中任何绕过访问限制的历史方法都不作为当前采集许可。

### 行为 → 设计 → 实施映射

| 干预/追溯指针 | 应消除的重复操作 | 新设计/执行项 |
|---|---|---|
| INT-019、023、032 | 每批后人工唤醒、手工恢复 | B21 continuation + 实际 wakeup owner |
| INT-002、026 | 催状态、辨认真在跑还是只说在跑 | 同源业务进度与阻塞原因，B21/B30 |
| INT-008、038、067、071 | 口径在聊天、文档、代码间反复重定 | B20 已有 SceneVersion 扩展、影响预览与批准 |
| INT-063；TR-D10 | 人工答案散落、待办每轮覆盖 | B20 Review Inbox 与裁决后自动回写 |
| INT-010、033、052、073 | 目标/角色/应用场景反复纠偏 | B10 准入与角色策略正交，按责任组织研究 |
| INT-011、024、054 | 产物生成了却没进下游 | B11–B13 业务键对账、投影与消费回执 |
| TR-X04 | 抓取主机充当组织域 | B10 Account 级官网归属证明 |
| TR-D07/D08 | 同邮箱多计、看不到拒收原因 | B11 观察/唯一路线分离与拒收报告 |
| TR-D06 | 从池子手抄 YAML 才能维护/起草 | B12 产品 Cohort |
| TR-D11；INT-041 | 重复抓取、无增量仍烧模型 | B22 获取前复用、预算与止损策略 |
| INT-034、046 | 授权/停止只留在聊天里 | B20/B21 可信批准、暂停与撤销；外发仍延期 |

INT/TR 是分析记录标识，行内证据定位器需进一步回到原始用户轮才能确认为新的业务批准。本次没有重算全部 L1/L2 或原始 corpus。

## 3. 产品侧核对：这次不照抄“最后五条缺口”

产品代码链接均固定在 [df689f52](https://github.com/ProfessorZhi/marketops/tree/df689f52e0e07dc35b88af7a3a5dd48808008a75)。以下行号只适用于该 revision。

| 复核项 | 代码/报告位置 | 可以确认什么；不能确认什么 |
|---|---|---|
| 最新产品门 | [最终验收](../RESEARCH_V1_ACCEPTANCE_FINAL_2026-09-25.md) H | 报告明确 NOT_READY；本轮文档不改判 |
| 稳定键与组织身份 | `application/promotion.py:131-158` | Account id 由场地句柄哈希，修复跨表键错位；不能据此前缀推论已证明一组织多场地归并 |
| 存在性证明 | `application/promotion.py::unpromoted_subjects` | 当前用账号名下证据判断已晋升；应对照业务对象本身及部分写入状态，不将相关性作为恒等式 |
| 官网域接缝 | `application/inputs.py:342-346`；验收 F.2 | dossier.domain 仍取 row.official_domain；来源 host 与组织域的含义冲突需真实生产者解决，不能拆 binding guard |
| 地区/适配 | 验收 E.1/F.6 | country/表面未确认、out_of_region 等未判；不是“有 D 路线就 eligible”。scope/identity 应是使用门，不只是以后优化 |
| 路线去重 | `adapters/sqlite/db.py:217-225`；`application/contact_routes.py` | 当前路线 seq 插入，逐页观察与唯一路线未分清；22 行不是 22 个独立可用联系人 |
| roster | `application/inputs.py:587`；`application/cohorts.py` | 前者读清单，后者是 demo YAML loader；正常维护/草稿仍无池子选择快照 |
| 复用 | `application/acquisition.py:97-107` | 该入口直接调用 HTTP；后续文件去重不等于获取前避免请求。这里只判此入口，不宣称全仓无任何缓存 |
| 真实刷新 | `application/refresh.py::build_refresher/refresh_source_supply` | 仅 SOURCE_COVERAGE 本地盘点；当前成功 tick 不证明外部官网/联系人重新验证 |
| 策略基础 | `domain/scene.py::SceneVersion/scene_impact`；`application/scenes.py` | 已有冻结版本、生命周期、影响计算，不应重建一套平行 Policy 平台 |
| 草稿门 | `application/outreach.py:293,371`；SOP 模块 E | 实现过滤非 A/B；SOP 实际允许 A→B→C→D 候选。单改 stage 条件或取消所有门均不对，应拆草稿与发信准备 |
| Jev 关联 | `adapters/sqlite/db.py:426-434`；`adapters/sqlite/decisions.py::put_decision`；验收 C.6 | DDL 的 work_item_id 是 NOT NULL，但报告说全 NULL。先分清 DB 列/API payload/测量查询；不能重复传播成已确认 schema 缺陷。下游使用要显式引用决策，不靠集合相等猜消费 |
| 执行器完成 | 验收 C.4 | MarketOps settlement 已受理；Quay 外部 exited-not-landed 仍存在。需证明不会因软件门重试，不能只换对外状态 |

**两个重要纠正：**

1. 最新报告用“持有非 X 路线”的账号数命名 eligible，只能理解为路线产出覆盖。它不是 Scene 的资产/地区/产品准入结果。设计和指标必须拆开。
2. 两轮维护有新的变更信号时，第二轮执行不是必然重复；但同一账号同一面的两个原因导致两次盘点，也不能仅凭 claim key 不同就证明最省工作。需要检验同一事实版本的合并策略。

## 4. 学术与协议检索：只采方法，不借成绩

### 工具执行

- **SciSpace**：已查询工具型 Agent 的政策执行、人工介入与端到端可靠性评估，得到 tau-bench、公司政策 guard 等相关结果。
- **Elicit**：已尝试 `search_papers`，返回 `api_access_denied`，账户计划不包含 API access。本次**没有 Elicit 检索成果**，不宣称双平台交叉验证。
- **Web 一手核对**：核对下表官方页面/摘要与选读 HTML。未复现论文实验，未向检索服务提交 Member-A 原文、私有代码或客户数据。

| 一手来源与阅读范围 | 实际支持 | 本设计采用；不作什么推论 |
|---|---|---|
| [tau-bench, 2406.12045v1](https://arxiv.org/abs/2406.12045v1)，摘要 | 通过最终数据库状态和多次运行一致性评估工具型 Agent | 比较业务终态与重复/故障运行，不只看调用成功；不把论文模型成绩当本项目通过率 |
| [Towards Enforcing Company Policy Adherence in Agentic Workflows, 2507.16459](https://arxiv.org/abs/2507.16459)，摘要；[v1 HTML](https://arxiv.org/html/2507.16459v1) 选读 | 将政策处理与运行时动作 guard 分开，研究政策执行方法 | 批准策略经验证后由确定性 guard 执行；论文不是自动生成任意 guard 安全上线的保证 |
| [Guidelines for Human-AI Interaction, CHI 2019](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/)，官方摘要/项目介绍 | 人机交互应考虑能力边界、出错与长期使用；指南经过实践者评估 | 让纠错、解释、覆盖范围与长期规则变更可见；不是 MarketOps 减少多少人工工作的量化证据 |
| [MCP Architecture overview, 2026-07-28](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)，scope/participants/transport | MCP 规定客户端/服务器上下文交换，不规定业务怎样编排模型与状态 | 一个远程业务 MCP + 独立持久执行。实际 SDK/协议须按部署验证，不能照最新页直接宣称现有兼容 |

这些研究为设计提供独立方法依据，不是新增复杂多 Agent 框架的理由。无需为了引用论文增加 planner/critic/synthesizer 等角色。特定领域实验的成功率、提速和成本不得移植为 MarketOps 的承诺。

## 5. 本次明确未做

未合并两个已有 PR；未修改任何产品 `.py`、schema 或 profile；未运行 paid discovery、Quay 研究、Jev 推理；未轮换令牌、重启服务、迁移业务库、清理工作树或上传原始分析语料。新文档分支隔离于实施 worktree。

本轮验证只针对文档改动、引用路径和现有目录兼容；产品 R1/R2 的关闭条件仍需执行计划中的真实验证。缺少人工标签的精度和缺少时间基线的节省指标均保留 unavailable。
