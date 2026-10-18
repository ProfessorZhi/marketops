# 产品市场模型与访问可靠性：2026-09-27 设计核对

性质：有界源码读取、公开技术资料核对与设计决策记录，**不是新一轮 live 集成或效果实验**。对应 [产品线/Scene模型](../PRODUCT_LINE_AND_SCENE_MODEL.md)、[采集/供应商契约](../ACQUISITION_AND_PROVIDER_DESIGN.md)及 [统一执行计划](../MEMBER_A_AUTOMATION_EXECUTION_PLAN.md)。

## 1. 本次授权和基线

用户要求直接完善 GitHub 架构与实施计划，明确产品线、启动、产品/地区/场地/候选人/联系人/邮件的组合，支持动态学习，并使系统可运行且效率/质量可与 Member-A 比较。本次没有授权生产外发、账号登录、代理购买、IP轮换、关闭安全控制或改共享服务。

核对时：PR #6 实现为 `df689f52e0e07dc35b88af7a3a5dd48808008a75`；PR #7 设计为 `61419b7cd1b683da149a5b1e01d6c83829ed0816`；均 OPEN、未合并。Member-A分析引用 `ProfessorZhi/example-session-repo@06cdae471f794e37877637d69ea93d1c020272a2`。静态读取在独立文档worktree；实际代码、schema、profile与业务状态未改。

历史验收仍有效于其原始构建；本次不会把历史 `NOT_READY_FOR_REAL_USE` 改写为READY。既有运行回报不代表新代码版本的表现。

## 2. 代码读到了什么

| 固定df689f52下的位置 | 本次实际读到的事实 | 设计处理，不夸大结论 |
|---|---|---|
| `domain/scene.py:126-146` | `product/scenario/role_policy`主要为字符串，地区是字符串tuple，已有profile/版本/预算/refresh/outreach | 加产品/Offer、GeoScope/Scenario的版本化引用及兼容层；不宣称现有字段已经等价产品目录 |
| `domain/scene.py:573-606` | scene_impact中更改product/region等会指向某些重新获取范围 | 先计算可复用事实，再规划缺失补抓；不把变更产品/子地区必定解释为全量重抓 |
| `domain/outreach.py:597-615,721-732` | TemplateFamily.product字符串；ApprovedClaim有revision/source/approver/expiry | 扩scope/产品版本引用，保留已有审批和不可变历史；不是另造一套模板平台 |
| `application/escalation.py:26-69` | OK/blocked/not_found不升级，JS类允许；timeout/error不自动浏览器 | 复用既有门，细化诊断和共享熔断；不用“HTTP失败一律browser”替换 |
| `adapters/crawl4ai_fetcher.py:87-100,116-139` | 每次创建匿名crawler；最终URL安全检查是事后；状态缺失用于分析时回落200 | 网络动作前防护是独立验收项；未知状态/正文可用性分开；不能宣称现有落点检查预防全部子资源越界 |
| `adapters/crawl4ai_fetcher.py:147-159,189-205` | 新内容hash生成与已有路径不重写可并存 | 加同URL变更时读回字节/hash一致性复现；潜在工件错配不是已经独立确认的生产事故 |
| `adapters/serper.py:41,72-89` | 固定search endpoint，payload q/num，HTTP错误提示把401/403与key/额度并列 | 版本化geo/language上下文和精确错误分类；不从代码注释推测供应商所有计费语义 |
| `adapters/jev.py:151-203` | `typesafe_sdk`、`TypeSafeClient.system_one`、解析typed answers、SDK重试显式0、记录usage/model | 当前协议以实测锁定版本为准；不从新营销镜像替换endpoint/SDK；阈值需本任务评估 |
| `application/relations.py:391-429` | `RelationFinding.decision_id`已有生产消费并检查work_item/subject/spec/answer | 复验投影和存储引用即可，不能继续笼统说“没有Jev消费” |
| `pyproject.toml:26-49` | MCP限定主版本，browser/decision SDK仍为范围依赖 | 发布清单需记录精确安装/浏览器/模型版本并测兼容；本次文档不改依赖 |

以上只覆盖已读路径，不是全仓安全审计。原先“最终域检查已足够”“answered就有增益”“稳定ID就是真组织”等结论不得沿用。

## 3. 业务资料与 session 如何进入设计

权威业务源：[冻结英国天然草 SOP](../../sources/party-group/sop/uk-natural-grass-acme-sop-v1.md)；行为依据：[干预图谱](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/MEMBER_A_INTERVENTION_MAP.md)、[78行登记](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/derived/member-a-interventions.csv)、[追溯表](https://github.com/ProfessorZhi/example-session-repo/blob/06cdae471f794e37877637d69ea93d1c020272a2/docs/SESSION_TO_PRODUCT_TRACEABILITY.md)。

INT-010/033/052/073支持把产品用途、客户关系和实际职能写清，而不是按“职务越高越好”；INT-008/038支持版本与影响评估；INT-019/032/041支持自动续跑/有限成本；TR-D06/07/11支持产品内名单、去重、复用。

这些是可追溯的历史分析，不是某职位回复率的随机对照，也不是新的产品主张/发送批准。78条登记不能变成78个独立需求或57.7%节省时间。分析的 `UNAVAILABLE` 不被本轮文档引用“洗成”已验证事实。

两条澄清：

- **母池不是只含场地。** 维护商/泳池搭建商/渠道可以Account-driven；不存在物理场地不能造Venue占位。示例产品线/用途不是公司已批准产品分类或具体型号能力声明。
- **Scene不是越细越好，也不是产品一变必新建。** 独立商业目标/政策/预算的分开；同一批准组合Offer可同Scene；局部地区/角色差异优先Segment与policy overlay。旧聊天里的过度绝对建议以本轮决策为准。

## 4. Cloudflare的前次归因更正

先前对话把一份L2的“14站出口IP封禁”复述为确定诊断，且提到Ray ID作为依据。官方资料不足以支持这个推断：Ray ID关联所有经过Cloudflare的请求，且文档还注明不保证每次唯一；它不表示是否拦截，更不指出拦截规则。

本设计因此把历史说法降为**历史分析声称、根因未独立确认**，不修改原始证据。可以记录具体403、challenge header和诊断体；确认IP/ASN需要另外的匹配规则/站点日志/授权诊断证据。观察、推断与已验证根因分开。

公开站点仍可能拒绝自动访问。系统应修复可控故障、降低无效请求、复用与寻找合法来源，不把绕CAPTCHA/身份伪装/轮换IP设计成产线默认路径。若主要场景没有足够合法来源，应报告不可交付范围，而不是把blocked都包装成完成。

## 5. 公开一手资料：阅读范围与可得结论

访问日期：2026-09-27。以下只读正文/HTML摘要，没有读取论文PDF或复现其结果。

| 来源 | 本次读取 | 用在设计的内容/不能证明的内容 |
|---|---|---|
| [Cloudflare Ray ID](https://developers.cloudflare.com/fundamentals/reference/cloudflare-ray-id/) | 官方说明页 | Ray ID是请求诊断标识；不能证明IP封禁 |
| [Challenge detection](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/) | 官方说明页 | `cf-mitigated: challenge`是明确挑战信号；不用body非空证明抓到了目标页面 |
| [403排查](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-403/) | 官方说明页 | 403可能有多种来源/权限原因；不根据状态码唯一归因 |
| [Crawl4AI批量](https://docs.crawl4ai.com/api/arun_many/)、[dispatcher](https://docs.crawl4ai.com/advanced/multi-url-crawling/)、[缓存](https://docs.crawl4ai.com/core/cache-modes/) | 官方v0.9.x页面 | 有批量、受限并发和缓存接口；实际所锁版本、安全上下文和跨进程限额还须验证，不代表能越过WAF |
| [Serper](https://serper.dev/) | 官方公开页面 | 搜索供给；不采其营销速度/套餐数字作为本项目参数，不把SERP当事实证据 |
| [TypeSafe API](https://docs.typesafe.ai/api)、[Introduction](https://docs.typesafe.ai/introduction)、[Primitives](https://docs.typesafe.ai/primitives) | **本次读取失败/Cache miss** | 不宣称查证了最新API/价格/校准性能；当前实现依据仓内已测试SDK与合同，实施时需补精确版本验证 |
| [tau-bench](https://arxiv.org/abs/2406.12045) | 原论文摘要 | 最终业务状态与重复运行可靠性比单次tool success更有意义；不是MarketOps通过率证据 |
| [SNPL: Simultaneous Policy Learning and Evaluation for Safe Multi-Objective Policy Improvement](https://arxiv.org/abs/2503.12760) | 原论文摘要，SciSpace检出后核对 | 提醒目标收益与guardrail退化应共同评估，小效果/噪声与选策略带来统计风险；本项目不复制论文算法/实验提升数字 |

SciSpace这次查询的是稀疏回复/转化反馈下的策略学习、选择偏差和人工监督；仅把相关且可核对的一手论文作为方法输入。搜索还返回若干泛CRM/RL概念文章，未把其摘要包装成已证实工程最佳实践，也未向检索工具发送私有session正文、客户联系方式或密钥。

TypeSafe文档不可读时没有使用第三方Jev同名站的endpoint或营销概率作运行配置。技术可用性与具体SDK语义需要再次核验；文档可写接口边界，不能因此虚构厂商性能。

## 6. 对“优于Member-A”的证据要求

不承诺笼统完美或所有网站100%可抓。把目标拆成：范围/身份/联系人质量不降低、人工可避免操作减少、端到端用时/总资源可比较、不可逆副作用不增加。算法式提高raw reply rate不够：OOO、转介、投诉、重复和未成熟观察窗须分开。

受控对照以相同的批准任务、时间窗、来源访问与可比资源为前提；Blind review、hold/失败的分母保留与cold/warm cache两种条件分开。缺独立人工gold、Member-A主动操作计时或真实外联结果时分别记unavailable；技术闭环通过可允许受控使用，但不写`BETTER_THAN_MEMBER_A`。

本次只修改架构/计划/导航，不生成产品claims实例、不扩国家/人群、不迁移库、不运行付费provider或停启服务。实测验证限文档与相关契约测试，具体结果在本次PR更新里单列，不充当产品验收。
