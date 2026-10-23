# 自动化工作流（正在建设）

MarketOps 要做的事，一句话：**把市场研究里那些有固定动作的环节交给机器跑完，只把该由人
决定的部分留给人。**

今天已经能跑完**一轮**完整研究：给它一个 Brief，它自己去搜、去读、去取证、去分级、出交付。
还没有做完的是**下一轮由谁发起**——什么时候该再跑一次、谁忘了跟进、哪个事实过期了。
这一半就是下面「正在建设」的五件事。

> 本文只描述**现在的形状**。哪一条今天成立、哪一条还没关，以
> [`MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md) 的关闭条件
> 和 [`B17_ACCEPTANCE_MATRIX_2026-09-28.md`](B17_ACCEPTANCE_MATRIX_2026-09-28.md) 的逐行裁决为准。
> 本文不给完成百分比：一行状态会因为一条判据落地而变，不会因为别的行变了而变。

![自动化工作流：已自动的六段与正在建设的无人值守部分](assets/workflow.svg)

## 谁做什么

| 角色 | 负责 | 不负责 |
|---|---|---|
| **人** | 定目标与范围、给额度、批准公开来源、复核 A/B | 不逐页读、不抄邮箱、不记台账 |
| **确定性程序** | 导入、获取、解析、去重、预算、投影、刷新、落盘 | 不做「这条线索值不值得跟」这类判断 |
| **Agent（Claude / Codex / WorkBuddy）** | 连接、发起 Run、在证据缺口上做有界判断、写报告 | 不持有业务规则，不自造事实 |
| **Quay** | 把需要**动态判断**的研究派给受限 Claude worker | 不做确定性步骤，不生成第二份业务事实 |

这四个分工是刻意的：普通的活由程序做（可重放、可对账），只有真的需要判断的地方才叫模型，
而且叫模型的那一层是**受限**的——它拿到的是一段有界任务，不是整个业务。

## 已经能自动跑完的六段

一次 Run 里，下面这些不需要人逐步点：

1. **发现**——四路同时发同一条 query：Serper、DeepSeek、SerpApi、TinyFish。
   某一路没额度或被拒，**只有它自己退出**，其余照常返回，Run 不中断。
2. **读页**——一次 Run 的**前 12 页**由 Crawl4AI、TinyFish、DeepSeek Fetch 三路同时读，
   取信息最全的那份；之后按各路**实际交付的效果**排序，而不是按某家的默认优先级。
3. **解析**——HTML / PDF 各自标注来源与变换，不互相冒充。
4. **证据绑定**——同源引文 + hash；抓取成功、文本匹配、模型判断都**不自动**证明业务事实。
5. **分级**——两条独立的轴：Account（discovered / eligible / hold / irrelevant / duplicate）
   与 ContactRoute（A / B / C / D / X）。两轴不可合并成一个分数。
6. **交付**——九表 XLSX / CSV、来源与 hash 清单、经验汇总，并重新读回核对。

跑够就停、跑不够就继续扩：`minimum_ab`（默认 50）是想要的 A+B 人数，只要还有在出
A+B 的来源，`marketops_next` 就返回 `EXPAND_PRODUCTIVE_SOURCE`，让 Agent 沿**同一类来源**
继续扩，而不是换一条没产出的线重来。

## 正在建设：把「一轮」变成「一直在」

下面五项是把上面这条链变成无人值守的部分。每一项都有可测的关闭条件，
写着「怎样才算做完了」，不写着「已经做了多少」。

### Watch —— 到期/变化才工作

要解决的：研究完一遍就放着，两个月后联系人换岗了也没人知道。

关闭条件（节选自计划 §B24）：保存/查询 View **零网络、零模型调用**；启用 Watch 产生
durable 的到期工作；**同一个 Account 出现在 3 个 View 里，仍然只有一份事实和一次兼容
refresh**；停用 Watch 后，未来不再派昂贵工作。

### Schedule —— 定时唤醒，但不许重复

要解决的：没人开新 session，任务就停在那里。

关闭条件：MarketOps 持有持久的 `ScheduleDefinition`（one_shot / calendar / due_state /
event），只有一个 trigger driver；重复 fire **幂等**；due check 先于任何 provider 调用，
空队列 **0 次 LLM**；时区、窗口、补跑、静默时段、预算、暂停都要明确。
外部 cron 只能带 `fire_id` 调同一个用例，不能另起一套刷新。

### ActionItem —— 跟进池，而不是又一个状态字段

要解决的：「这个账号下一步干什么」散在聊天记录里。

关闭条件：ActionItem 绑定对象 / 原因 / evidence / owner / due / state / expected_version；
重放幂等，过期 version 拒写；账号状态与 Engagement、Route currentness **分开**，
不塞进一个 `account.status`。

### Notification —— 变化落一次账，按订阅投影

要解决的：五个 session 各推一遍，团队收到五份一样的消息。

关闭条件：业务 Event **一次**落账；Subscription 按 workspace / workstream / view / watch /
severity / sink 投影；DeliveryReceipt 逐 sink 记录；**通知失败只重试通知，不重跑研究**。
客户端没有主动推送能力时，诚实降级为 Inbox / 拉取，而不是假装推了。

### Quay —— 需要动态判断的研究派给受限 worker

要解决的：有些站点不点开、不分页就看不到人。这种活没法写成固定程序。

Quay 负责这一层的调度：把有界的研究任务派给受限的 Claude worker，再把结果交回同一条
证据链。它**不是**第二个业务后端——口径、预算与证据只留在 MarketOps 这一层。
部署要求写在 [`deploy/README.md`](../deploy/README.md)：Quay CLI、Node 运行时与一个带
`.quay/config.yml` 的工作区根，三者齐全才启用（`MARKETOPS_QUAY_CLI` / `_NODE` / `_ROOT`）。

## 为什么不能更快

不是慢在写代码，是慢在**每一步都要能举证**。举一个已经落地的切片：

`docs/B17_F_WATCH_MAINTENANCE_RUN_2026-09-28.md` 关掉的是「watch 只为到期/信号对象创建
维护 Run」这**前半句**，文件里逐字写明它**不**声称后半段——「全仓没有任何执行器把一行
`Run` 变成工作中」。把没测过的那半句一起读进结论，就是拿一句没说过的话当完成标志。

这就是这个项目对「做完」的定义：一句话拆成两个主语，各判各的；
旧回执一个字节都不改，新事实另起一份追加。

## 现在能拿它做什么

- **做一轮真实研究**：给出地区、对象、角色、数量与证据要求，跑完拿九表交付。今天就能用。
- **看它怎么判断**：每条联系路线都带同源引文与 hash，能逐条回读。
- **接进自己的客户端**：Claude Code / Codex / WorkBuddy，见
  [`CLIENT_INSTALL.md`](CLIENT_INSTALL.md)。
- **还不能做**：让它自己每天早上跑一轮、把变化推到群里、自动跟进。那是上面那五项。

真实外联、回复监控与销售交接在独立授权范围内，当前默认关闭
（`research_only=true`、`send_authorized=false`）。
