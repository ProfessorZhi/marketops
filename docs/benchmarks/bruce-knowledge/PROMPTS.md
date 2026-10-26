# PROMPTS — Bruce 提示词与 SOP 的可复用模板库

> 出处：本文由本机语料派生层装配（`ARTIFACTS_ROOT/marketops/bench/v0.6.2/bruce-knowledge/`），
> 该目录不随仓库分发。文中 `locator`（`voice/…`、`work-Wxx.md:行号`、`codex-<id>@<时间戳>`）
> 指向派生层文件，用于回查原话，仓库内无对应文件。语料本身（11 GB 会话、页面正文、第三方联系方式）不进 Git。

范围：B 类（提示词与 SOP）。输入为 `findings/` 下 52 个分片（voice-01..16、work-01..36，B 类共 **179 行**）与 `final/lines/` 下 7 份归并 md、4 份归并 jsonl。未读语料原始 jsonl，未联网，未对外发送。

## 方法口径

- **去重按会话+秒**：179 行分布在 **59 个不同会话**、**118 条不同消息**上。分片之间会重复上报同一条消息（同一会话的同一秒被不同 reader 各记一次，或同一长消息被不同分片各自截断），所以重复次数一律按会话去重，不按分片行数。典型例子：`codex-01a04189-...@2026-08-27T14:31:54` 这一条消息在 3 个分片里各有 1 行只读规则，按行数是 3，按会话只有 1。
- **五类**：开场提示、任务书、交接、验收、停线。一条发现只归一类；跨类的（如“只读任务”）归入约束最强的那一类。
- **`category`** 固定为 `B`；模板类型由所在小节标题承载，不增加字段。
- **`count`**：§0 的 `count` = 按会话去重的复述次数；其余各节的 `count` = 引文里出现的数字量，引文无数字则 `null`。
- **`project_line`** 归一到 8 条业务线（fr / uk / us / de / ch / golf / other / cross），原始子线见 locator 对应会话。
- **locator 写法**：统一为 `<分片目录>/<会话文件>.md@<时间戳>`（workbuddy 会话为 `... .md#<毫秒时间戳>`）。分片里的原始写法可能是 `<会话>@<ts>`、`<会话>.md [<ts>]` 或 `<会话>#<ts>`，按“会话文件 + 秒”等价，指向同一条消息；同一规则被多个会话复述时，locator 用 ` ‖ ` 并列。
- **`confidence`**：`direct` = 他的原话；`high` = 同期文本直读；`inferred` = 由多处文本或规则文件原文推断（英文契约段落多为项目 AGENTS/SKILL 原文，不是他本人口述）。
- **隐私**：人名/邮箱/机构名/电话已替换为 `<name>`/`<email>`/`<org>`/`<phone>`；引文 ≤200 字，超长处截断保留原句前半。
- **未裁决**：同一问题他给过相反指令的，两说并列，见 §6。

## 一句话结论

他的提示词不是“写得好不好”的问题，是**没有承载面**：同一批规则以口述形式重下 3–9 次，最长的一条从 08-20 重下到 09-16（27 天）。§0 前两条是他要求“把格式做成规范”的原话，其余按复述次数降序，先看这一节。

## 0. 反复重下的规则（重复次数 = 没有承载面）

判据：同一语义在**不同会话**里被再次给出。重复本身不直接等于“没有承载面”，但它等价于“承载面没有被加载”——要么规则只活在某个线程里，要么文档存在但发信/调研时不读它。

| 规则 | 会话数 | 时间跨度 | 承载面应该在哪 | 现状 |
|---|---|---|---|---|
| 邮件排版/渲染格式 | 9 | 08-20 → 09-16 | 「发信模板规范」文档，或发送器内置默认 | 08-20 他要求做成规范、08-21 要求并进监控规则；此后仍被重指 7 次 |
| 批量发信失败处置（记录跳过、首轮发完再补发） | 8 | 08-20 → 09-16 | 发信 skill 的重试策略 | 每次重给 |
| 开场四件套（模板、指定发件箱、先发测试、排版抄旧线程） | 6 | 08-20 → 09-16 | 发信 skill 的入口参数 | 每次新线程重给一遍 |
| 渲染契约（720px / CID 内联 / GIF 不重编码） | 5 | 08-14 → 08-31 | 共享渲染 profile | 已有 profile 名，仍被英文整段重述 |
| 跨线程联络史强制 | 5 | 08-14 → 08-31 | sent index / 联络史台账 | 反复写进各项目 AGENTS |
| 发件箱均分 | 4 | 08-27 → 09-02 | 发信 skill 的分配器 | 发件箱数靠人报（9 → 13） |
| 图片内联、不要附件 | 4 | 08-14 → 08-31 | 渲染 profile | 与口头版“图片高清、不要有附件”并行 |
| 未决消息只进本线程编号清单 | 3 | 08-27 → 08-31 | 监控 skill 的 review_route | 字段名在监控配置里只出现一次（见 §4） |
| 规则改动必须改权威共享源并升版本 | 3 | 08-14 → 08-31 | 规则仓库本身 | 反复重述，说明没人执行 |

最强的一条：`codex-01a01d40` 里他先后说「把邮件的现在的格式做成规范，确保其他线程和新线程都按照这个来，避免每个线程都要反复调试」（08-20 07:01）和「记录好规则，并更新到邮件监控规则中去，确保其他线程和新先都能直接用」（08-21 04:41）。规范确实被写了，但此后 08-20 09:37 到 09-16 12:29 之间，仍有 9 个不同会话被告知“按‘波兰学校-发信和监控’线程的最终排版规则来”。**规范存在，但没有一个线程在启动时读它。**

以下每条一行证据；`count` 为按会话去重的复述次数。

{"category":"B","project_line":"other","locator":"voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T07:01:18.253Z","quote":"可以了，你要把邮件的现在的格式做成规范，确保其他线程和新线程都按照这个来，避免每个线程都要反复调试","count":null,"confidence":"direct"}
{"category":"B","project_line":"other","locator":"voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-21T04:41:09.619Z","quote":"可以，记录好规则，并更新到邮件监控规则中去，确保其他线程和新先都能直接用","count":null,"confidence":"direct"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a022b1-6f56-7e83-8d92-041d3f258e8b.md@2026-08-21T05:19:40.418Z ‖ voice/codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667.md@2026-08-20T09:37:23.331Z ‖ voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-21T05:27:06Z ‖ voice/codex-01a038e5-097b-74d1-8bf6-ffc4f326d1d7.md@2026-08-25T12:51:34.389Z ‖ voice/codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md@2026-08-27T05:47:52.462Z ‖ voice/codex-01a04159-0433-7ea3-8b03-5b4d2bced55e.md@2026-08-27T04:06:49.032Z ‖ voice/codex-01a06063-9eec-7c32-9627-2c7272d4f88c.md@2026-09-02T05:39:04Z ‖ voice/codex-01a06cac-7fc9-76b3-8631-137a04d35012.md@2026-09-04T13:47:27.528Z ‖ voice/codex-01a0a9c7-b536-70a1-a311-dd27aadde9ec.md@2026-09-16T12:29:00.251Z","quote":"邮件的排版格式按线程名“波兰学校-发信和监控”最终的排版规则来，已经总结好规则","count":9,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667.md@2026-08-20T10:23:32.486Z ‖ voice/codex-01a022af-89bf-7d13-8f97-32625630733d.md@2026-08-21T05:23:45.899Z ‖ voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-21T05:36:58Z ‖ voice/codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md@2026-08-27T06:38:25.489Z ‖ voice/codex-01a05d03-05dd-7883-90cd-a9825fa834c7.md@2026-09-01T13:05:55Z ‖ voice/codex-01a013fd-d363-7e13-a157-306a09f06647.md@2026-09-01T13:24:40.139Z ‖ voice/codex-01a06063-9eec-7c32-9627-2c7272d4f88c.md@2026-09-02T06:13:07Z ‖ voice/codex-01a0a9c7-b536-70a1-a311-dd27aadde9ec.md@2026-09-16T13:05:49.788Z","quote":"开始批量发送，遇到问题先记录跳过，直到第一轮全部发完，再把之前未发送成功的发送","count":8,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667.md@2026-08-20T10:05:56.452Z ‖ voice/codex-01a022b1-6f56-7e83-8d92-041d3f258e8b.md@2026-08-21T05:19:40.418Z ‖ voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-21T05:27:06Z ‖ voice/codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md@2026-08-27T05:47:52.462Z ‖ voice/codex-01a06063-9eec-7c32-9627-2c7272d4f88c.md@2026-09-02T05:39:04Z ‖ voice/codex-01a06cac-7fc9-76b3-8631-137a04d35012.md@2026-09-04T13:47:27.528Z","quote":"开发信模板见附件，用<name>的邮箱发，先发给<email>测试","count":6,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a000c9-4fd8-75b2-859d-c273a067a68d.md@2026-08-14T14:59:48.925Z ‖ voice/codex-01a013fd-d363-7e13-a157-306a09f06647.md@2026-08-20T08:39:53.917Z ‖ voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-21T02:00:46.055Z ‖ voice/codex-01a04159-0433-7ea3-8b03-5b4d2bced55e.md@2026-08-27T14:04:26.047Z ‖ voice/codex-01a022b1-6f56-7e83-8d92-041d3f258e8b.md@2026-08-31T05:40:41.446Z","quote":"It requires a simple natural left-aligned fluid body (`max-width:720px`), no centred card/embed appearance, CID-inline source images, and original animated GIF assets preserved without re-encoding","count":5,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a00004-fd67-79b2-bc6d-3493ccafd348.md#2026-08-14T13:40:56 ‖ voice/codex-01a000c9-4fd8-75b2-859d-c273a067a68d.md@2026-08-14T14:59:48.925Z ‖ voice/codex-01a013fd-d363-7e13-a157-306a09f06647.md@2026-08-19T13:33:54.573Z ‖ voice/codex-01a022b1-6f56-7e83-8d92-041d3f258e8b.md@2026-08-31T05:40:41.446Z ‖ voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-31T05:40:46Z","quote":"For every <org>/Landscaping outbound development-email queue, treat cross-thread contact history as mandatory.","count":5,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md@2026-08-27T06:38:25.489Z ‖ voice/codex-01a013fd-d363-7e13-a157-306a09f06647.md@2026-09-01T13:24:40.139Z ‖ voice/codex-01a05d03-05dd-7883-90cd-a9825fa834c7.md@2026-09-01T13:05:55Z ‖ voice/codex-01a06063-9eec-7c32-9627-2c7272d4f88c.md@2026-09-02T06:13:07Z","quote":"<name>所有9个邮箱平均分配数量，遇到问题先记录跳过，直到第一轮全部发完","count":4,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a00004-fd67-79b2-bc6d-3493ccafd348.md#2026-08-14T13:40:56 ‖ voice/codex-01a013fd-d363-7e13-a157-306a09f06647.md@2026-08-18T11:36:05.261Z ‖ voice/codex-01a0335e-adae-7e71-8bac-f026dd74c4ed.md@2026-08-24T12:56:59.387Z ‖ voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-31T05:40:46Z","quote":"do not send images only as attachments. Build an HTML email body that presents the images inline at the intended positions","count":4,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a04159-0433-7ea3-8b03-5b4d2bced55e.md@2026-08-27T04:06:49.032Z ‖ voice/codex-01a040fa-f5c2-7a83-9703-6bfe9f833c3f.md@2026-08-28T02:56:21.396Z ‖ voice/codex-01a022b1-6f56-7e83-8d92-041d3f258e8b.md@2026-08-31T05:40:41.446Z","quote":"unresolved campaign-matched messages and manual-verification gates default to a numbered list in the owning Codex task, never a <org> review card or <name> direct message","count":3,"confidence":"inferred"}
{"category":"B","project_line":"cross","locator":"voice/codex-01a013fd-d363-7e13-a157-306a09f06647.md@2026-08-19T13:33:54.573Z ‖ voice/codex-01a000c9-4fd8-75b2-859d-c273a067a68d.md@2026-08-14T14:59:48.925Z ‖ voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-31T05:40:46Z","quote":"Before treating a cross-project rule, skill, runtime, or SOP change as complete, identify its authoritative shared source; update that source and its version/changelog","count":3,"confidence":"inferred"}

## 1. 开场提示（Kickoff）

他的开场提示固定回答四件事：**读什么**（唯一上下文源）、**只做什么**（阶段边界）、**谁授权**（是否需要等确认）、**参数是什么**（国家、批次、母池来源）。四件事缺一件，后面就会补一次校正。

### 模板条款（每条一行证据）

- **只读三份，不许自行扩读：KICKOFF.md + 源清单 + 字段/门禁契约，三份之外的上下文一律不读。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-23da98e1-8cae-4124-ad81-39b8e4e5f275.md#1789899746632","quote":"先读这三份，它们是唯一上下文来源：任务启动书 KICKOFF.md；源清单 source_universe_plan.csv；字段与门禁契约 CHAIN_CONTRACTS.md","count":3,"confidence":"direct"}

- **先声明理解，再动手：会话开头用一句话说明对本任务场景/国家的解释；判错则整份作废。**
{"category":"B","project_line":"other","locator":"voice/workbuddy-c8b43899-114a-47ef-9148-5b411256b2a2.md@1789962781124","quote":"启动依据：先读这几份，它们是唯一上下文来源；读完上述文件 + 三个国家级技能，在会话里明说你的工作解释（场景/国家判错 = 整份作废）","count":null,"confidence":"high"}

- **国家批次写死在开场：按顺序推进，每批冻结后再开下一批，同一线程串行。**
{"category":"B","project_line":"other","locator":"voice/workbuddy-c8b43899-114a-47ef-9148-5b411256b2a2.md@1789962781124","quote":"按 KICKOFF 第 3 节的国家批次顺序推进：UK → FR → DE → IT/NL → AU/NZ/IE/CA → 其他。每批冻结后再开下一批。同一线程串行，不并行开多国。","count":null,"confidence":"high"}

- **阶段边界写死在开场：只做母池（机构层）时，明确“不调研联系人、不调研邮箱、不发信”，并且“母池一行 = 一个机构，不含邮箱字段”。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-23da98e1-8cae-4124-ad81-39b8e4e5f275.md#1789899746632","quote":"用 <org>-terminal-market-research 技能，建立「全球高尔夫」母池。【本任务范围】只做母池（机构层）。不调研联系人、不调研邮箱、不发信。母池一行 = 一个机构，不含任何邮箱字段。","count":null,"confidence":"direct"}

- **母池从官方源起，不从邮箱起：先建机构母池，不先查邮箱、不批量爬。**
{"category":"B","project_line":"uk","locator":"voice/codex-01a06617-2143-7431-b70d-c43863864253.md@2026-09-03T10:29:31.657Z","quote":"先用 <org> 的官方 <org> 数据建立“英格兰足球场母池”，不要先查邮箱，也不要批量爬 <org>。","count":null,"confidence":"high"}

- **读 skill 先于动作：写明必须读哪两个 SKILL.md 及其 references。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a04189-c935-76c0-849e-9a7a7a4cdd2d.md@2026-08-27T14:31:54Z","quote":"必须先读取并遵守 …\\skills\\<org>-terminal-market-research\\SKILL.md 与 …\\skills\\global-sports-field-terminal-research\\SKILL.md，以及各自要求的 mother-pool/country setup references","count":null,"confidence":"high"}

- **授权是一句话，必须显式二选一：要么“读取母池后立即开始，无需再次确认”，要么“未获明确批准前不得进入下一阶段”。两种都在他的语料里出现过，不能靠 agent 猜。**
{"category":"B","project_line":"ch","locator":"voice/codex-01a066c5-1412-7392-bdde-7aa87a525134.md@2026-09-04T04:28:29.306Z","quote":"执行授权：读取母池后立即开始预检、页面镜像与批量调研，无需再次确认。","count":null,"confidence":"high"}

- **（同一轴的另一端）需要批准才进入下一阶段。**
{"category":"B","project_line":"de","locator":"voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md@2026-08-17T10:32:56.074Z","quote":"Do not proceed to Deep Research until I explicitly approve it.","count":null,"confidence":"high"}

- **开场就带参数替换位：整套做法是“替换最上面的参数，再把整段复制给 Codex”，agent 自动搭建、测试、启动，只在真正发送前停一次确认。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-08-28T10:54:21","quote":"下面这套是「业务无关版」部署总指令。你同事先替换最上面的参数，再把整段复制给 Codex。Codex会直接搭建、测试并启动调研系统；只有真正发送前会停下来展示具体队列，等待一次确认。","count":null,"confidence":"high"}

- **母池由人交接：新线程的开场提示由人给，母池文件也由人手动给。**
{"category":"B","project_line":"fr","locator":"voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790063110115","quote":"1、法国母池我已经有了，你帮给新线程一个明确提示词，我在新线程手动给到母池就开始调研","count":null,"confidence":"direct"}

- **需要补齐时，输出一段可直接粘给母池线程的提示词。**
{"category":"B","project_line":"us","locator":"voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600.md#1789887415184","quote":"给我一个让母池调研线程需要它补齐的提示词，我来给它","count":null,"confidence":"direct"}

- **开场前先读索引：任务开始时先读 vault index 与相关页，再行动。**
{"category":"B","project_line":"de","locator":"voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md@2026-08-18T02:51:18.149Z","quote":"At the start of every task, read the vault index and any relevant pages before acting","count":null,"confidence":"inferred"}

### 可拼装模板

```text
# 任务启动
你只做 {{场景}} / {{国家}} 的 {{阶段：母池|深调|发信|监控}}。
1. 先读这三份，它们是唯一上下文来源：KICKOFF.md、source_universe_plan.csv、CHAIN_CONTRACTS.md。
   不要自行扩读其他线程或历史资产。
2. 在会话开头用一句话说明你的工作解释；{{场景}}/{{国家}} 判错 = 整份作废。
3. 本任务范围：{{只做母池（机构层）|只做深调|只做发信|只做监控}}。
   越界禁止：不调研联系人 / 不查邮箱 / 不发信（按阶段勾选）。
   母池一行 = 一个机构，不含任何邮箱字段。
4. 国家批次顺序：{{UK → FR → DE → IT/NL → AU/NZ/IE/CA → 其他}}；
   每批冻结后再开下一批；同一线程串行，不并行开多国。
5. 授权：{{读取母池后立即开始，无需再次确认}} / {{未获明确批准前不得进入下一阶段}}。
6. 母池来源：{{由人在本线程手动提供}}。需要补齐时，输出一段可直接粘给母池线程的提示词。
```

## 2. 任务书（Task brief / contract）

任务书是他唯一写得像“契约”的东西：编号、英文、带硬闸门。可分三段读——**范围与禁止项**、**分批与预算**、**数据结构与分级**。他把业务侧任务书当成“给另一台电脑的部署包”来写，所以载体要求本身就是任务书的一部分。

分级契约（T1–T5）不重复列在这里：它约束的是发信闸门，归 §5。

### 模板条款（每条一行证据）

- **范围窄到只剩要修的事：不重建 skill、不开新研究、只修列出的两个问题。**
{"category":"B","project_line":"de","locator":"voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md@2026-08-17T10:17:08.464Z","quote":"Do NOT recreate the skill. Do NOT begin live Germany or NRW research. Only fix the following two issues.","count":2,"confidence":"high"}

- **分批可恢复：每批约 50 个账户。**
{"category":"B","project_line":"de","locator":"voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md@2026-08-17T12:17:25.712Z","quote":"Internally process the work in resumable batches of approximately 50 accounts.","count":50,"confidence":"high"}

- **每完成一个账户立即落盘；中断后从 checkpoint 续跑；已完成的绝不重做。**
{"category":"B","project_line":"de","locator":"voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md@2026-08-17T12:17:25.712Z","quote":"Persist after every completed account. Resume from checkpoints after interruption. Never repeat completed Benchmark accounts.","count":null,"confidence":"high"}

- **允许 UNKNOWN：不为填可选字段超预算。**
{"category":"B","project_line":"de","locator":"voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md@2026-08-17T11:04:22.155Z","quote":"Do not exceed the normal research budget simply to fill optional fields. UNKNOWN is acceptable.","count":null,"confidence":"high"}

- **流水线是五段，不是一个大 agent：网页提取 → 网页质量 → 网页镜像 → 邮箱提取 → 邮箱质量；每段只做自己的事。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md@2026-09-16T12:48:05.577Z","quote":"网页提取--负责从母池找网页 网页质量--评估找到的网页质量，有没有合适邮箱 网页镜像--负责镜像网页 邮箱提出--对镜像的网页做邮箱提取 邮箱质量--审核邮箱质量，合格邮箱计入到飞书表格","count":5,"confidence":"high"}

- **筛选字段要写进上游：“有没有邮箱线索”是网页提取线程的筛选字段，不单独拆线程；“找到一个 URL”不算成果。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md@2026-09-17T03:07:15.101Z","quote":"关键调整是：“有没有邮箱线索”作为网页提取线程的筛选字段，不单独拆成线程。网页提取线程不能再把“找到一个 URL”当作成果","count":null,"confidence":"high"}

- **抓取页数写死：每机构 4–6 类高信号页、最多 6–8 页、禁全站。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md@2026-09-17T07:31:10.655Z","quote":"每个机构按 4–6 类高信号页面抓取，最多 6–8 页；禁止全站爬取。","count":8,"confidence":"high"}

- **抓取预算写死：并发 12–24；单 URL 超时 15–20 秒；单响应 ≤5 MB。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md@2026-09-17T07:31:10.655Z","quote":"默认跨域并发 12–24，单 URL 超时 15–20 秒，单响应最多 5 MB。","count":24,"confidence":"high"}

- **反机械化三条：不机械走网页 checklist；不为数量降低邮箱真实性；不找到一个联系人就停。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790229527579","quote":"不要机械执行网页Checklist。不要为了数量降低邮箱真实性要求。不要找到一个联系人就停止。","count":null,"confidence":"direct"}

- **岗位词按国家本地化：每国同时建 English Title + Local Language Title。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790229527579","quote":"不能只按美国Title搜索全球。进入每一个国家时：必须建立：English Title + Local Language Title","count":null,"confidence":"direct"}

- **完成判据前置：每个 Account 必须回答 7 个问题，写进任务书而不是留给验收。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790229527579","quote":"每个Account必须回答7个问题","count":7,"confidence":"direct"}

- **场景专属优先级必须显式写出（例：捡球机场景 Range Manager 优先于 Superintendent）。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790229527579","quote":"对于Ball Picker Robot：不要默认Superintendent永远排第一。优先考虑：Range Manager → Director of Golf → Golf Operations → GM","count":null,"confidence":"direct"}

- **四条链路前期完全独立：母池 / 深调 / 发信 / 监控，合在一起容易出错。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-d60f6c1b-e768-45c8-8c67-d5d27b492ca4.md#1789881362725","quote":"母池调研 深度调研 发信 邮箱监控 这四个前期先完全独立开来，合在一起的话会容易出错","count":4,"confidence":"direct"}

- **契约冻结成一页：三份交接契约 + sent index，一页短文档，作为四条链路的开发基准。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-d60f6c1b-e768-45c8-8c67-d5d27b492ca4.md#1789881670834","quote":"三份交接契约 + sent index 约定冻结成一份短文档（一页就够），作为四条链路各自的开发基准","count":3,"confidence":"direct"}

- **发件人与附件：美加用指定发件箱、其他地区用另一发件箱；职位名片在模板里；原则上不带附件。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-09-01T06:43:55","quote":"1、美国和加拿大的发件箱都是用<name>的邮箱…2、其他地区的发件箱都是用<name>的邮箱 3、职位和名片等都在开发信模板里 4、原则不带附件，需要带的我会注明","count":null,"confidence":"high"}

- **参数矩阵显式列出：应用场景正文 × 发信人设 × 语言地区 × 触达轮次。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-d60f6c1b-e768-45c8-8c67-d5d27b492ca4.md#1789880937419","quote":"应用场景正文\n× 发信人设\n× 语言地区\n× 触达轮次","count":4,"confidence":"inferred"}

- **规模是阶梯不是一步：20 网页 → 200 邮箱 → 500 邮箱，每级评估、调整、总结，最后固化成 skill。**
{"category":"B","project_line":"uk","locator":"voice/codex-01a06617-2143-7431-b70d-c43863864253.md@2026-09-03T07:06:31.279Z","quote":"策略一：针对产品终端、关键角色画像，先做梳理，然后先调研20网页下载到本地，再从这20网页下载邮箱，评估邮箱质量，调试到满意后，做一次总结，再同理产出200个邮箱，再评估调整并总结，再产出500个邮箱，最后形成一个调研skill","count":500,"confidence":"high"}

- **发信窗口：按本机时间 14:00–22:00，窗口外只记录、次日再发。**
{"category":"B","project_line":"uk","locator":"voice/codex-01a07534-2091-7e12-94eb-311fd519a140.md@2026-09-08T13:13:59.858Z","quote":"1、发信时间按电脑时间每天的14：00-22：00，其余时间就记录，等到第二天时间 2、在发信时间段，有调研产出新邮箱就去重和排除交接的已发记录后，直接让wave1发第一封开发信","count":null,"confidence":"high"}

- **试点闸门：先 50 户验证，再全量。**
{"category":"B","project_line":"us","locator":"voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600.md#1789904829105","quote":"现在跑，先跑50户验证","count":50,"confidence":"direct"}

- **抓取到约 55% 再派 agent 做语义判断。**
{"category":"B","project_line":"us","locator":"voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600.md#1790145014109","quote":"按你建议增量建议等抓取到 55% 左右再派 agent","count":55,"confidence":"direct"}

- **小批量机构验证：先做 1 次 25 个机构。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a0c3f4-33d3-7500-88eb-4bb9a8ef424b.md@2026-09-21T12:56:46Z","quote":"先做1次25个机构的验证","count":25,"confidence":"high"}

- **每机构 6–8 个网页目标，提取不到就跳过。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a0c3f4-33d3-7500-88eb-4bb9a8ef424b.md@2026-09-21T12:41:33Z","quote":"每一个机构就按 6 到 8 个网页目标去提取，然后提取不到就跳过","count":8,"confidence":"high"}

- **是否允许自建 skill 没有全局答案：德国线写“Do NOT recreate the skill”，美国线却问“需不需要你自己再创skill”。这是任务书级别的分叉，必须逐项目写明。**
{"category":"B","project_line":"us","locator":"voice/codex-01a040fa-f5c2-7a83-9703-6bfe9f833c3f.md@2026-08-27T03:17:36.984Z","quote":"1、确定哪些人有决策的，深挖这类人的真实邮箱，一个公司应该有几个关键决策人 2、如果高效对清单里面每个公司做深度调研，我现在的深度调研技能够不够，需不需要你自己再创skill","count":null,"confidence":"high"}

- **载体要求一：skill 全线程与新开线程都可用、更新要同步。**
{"category":"B","project_line":"us","locator":"voice/codex-01a040fa-f5c2-7a83-9703-6bfe9f833c3f.md@2026-08-27T05:04:39.848Z","quote":"1、该skill确保全线程和新开线程都能用，如果技能更新也是全线程和新开线程同步更新","count":null,"confidence":"high"}

- **载体要求二：要给可下载的 skill。**
{"category":"B","project_line":"us","locator":"codex-01a07b74-426e-7310-b2ad-0432cd738a05.md@2026-09-08T06:04:15.005Z","quote":"主要核心要包含如何高效批量发信，给我一个可下载的skill","count":null,"confidence":"high"}

- **载体要求三：多线程职责要合成一个“总 skill”供其他国家复用。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a06c2f-3a68-7813-a9e6-bd200712f50a.md@2026-09-21T04:44:51.535Z","quote":"把你先线程功能、职责、skill和配合的下面4个线程，做成一个组合的流程策略和skill，用于后续批量做其他国家+应用场景的深度调研总skill","count":4,"confidence":"high"}

- **载体要求四：策略更新要能给到其他电脑，发件箱不一定相同。**
{"category":"B","project_line":"us","locator":"codex-01a07b74-426e-7310-b2ad-0432cd738a05.md@2026-09-08T05:57:05.971Z","quote":"更新最新批量发信策略，给到其他电脑codex用，发件邮箱也不一定相同","count":null,"confidence":"high"}

- **字段与分群要声明：调研必须带国家字段；7 个销售团队按北美 1 + 非北美 6 分群。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-b5e45ffe-61da-416a-ab31-3635c74e52b9.md@1789875255001","quote":"1、涉及到我做调研，调研的时候一定要明确到国家，把国家字段也要放进去 2、一共7个销售团队，<name>负责的北美是1个，<name>有6个","count":7,"confidence":"high"}

- **历史源要声明：没有独立 CRM 时，明确“全局跨线程发送历史是唯一历史抑制源”。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-09-01T12:13:16","quote":"明确声明：美国 PL01 当前没有独立 CRM、退订、退信或投诉历史；本轮以全局跨线程发送历史作为唯一历史抑制源。","count":null,"confidence":"high"}

- **交付表结构写在任务书里：source_universe_plan、market_coverage_baseline、data/country_profile.csv。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a04189-c935-76c0-849e-9a7a7a4cdd2d.md@2026-08-27T14:31:54Z","quote":"为法国高尔夫和学校/非高尔夫运动场建立当前 source_universe_plan、market_coverage_baseline 与非高尔夫 data/country_profile.csv","count":null,"confidence":"high"}

- **归档约束：单个 ZIP ≤2 GB，并生成总 MANIFEST 标明每个 Session 在哪个分卷。**
{"category":"B","project_line":"other","locator":"voice/codex-01a0d237-2a17-7863-b4ae-95aacc18bae3.md#2026-09-24T07:04:01","quote":"每个 ZIP 尽量控制在 2 GB 以下，并生成一个总 MANIFEST 说明每个 Session 位于哪个分卷。","count":2,"confidence":"direct"}

- **人设是参数不是套话：明确“作为 20 年以上资深海外市场负责人”，并给出判断标准“终端客户看完就想约 demo”。**
{"category":"B","project_line":"other","locator":"voice/codex-01a01d5b-8ac9-7221-a7a4-9d0054e5b197.md@2026-08-20T04:34:19Z","quote":"我们先讨论方案，不做海报，主要画面如何呈现，要不要放产品卖点，你要作为一个20年以上资深海外市场负责人考虑，站在用户角度看，这是要让终端客户看完就想约demo的开发信","count":20,"confidence":"high"}

- **再触达节奏：T1 无响应 5 天后补进下一轮。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-d60f6c1b-e768-45c8-8c67-d5d27b492ca4.md#1789884112279","quote":"T1无响应5天后就补进去","count":5,"confidence":"direct"}

### 可拼装模板

```text
# 任务书
范围：只做 {{阶段}}。禁止：{{重建 skill / 开新研究 / 发信 / 查邮箱}}。只修 {{N}} 个列明的问题。
分批：每批约 {{50}} 个账户；每完成一个立即落盘；断点续跑；不重做已完成。
预算：允许 UNKNOWN；不为填可选字段超预算。
流水线：{{网页提取 → 网页质量 → 网页镜像 → 邮箱提取 → 邮箱质量}}；每段只做自己的事。
抓取：每机构 {{4–6}} 类高信号页、最多 {{6–8}} 页；禁全站；并发 {{12–24}}；超时 {{15–20}}s；响应 ≤{{5MB}}。
字段：必须带 {{国家}}；岗位词 = English Title + Local Language Title；场景优先级 {{…}}。
去机械化：不机械走 checklist；不为数量降真实性；不找到一个联系人就停。
分级：T1/T2 可自动发；T5 永不发送。
链路：母池 / 深调 / 发信 / 监控 前期独立；三份交接契约 + sent index 冻结一页。
载体：规则改权威共享源并升版本；skill 全线程可用、可下载、可交接。
试点：先 {{50 户 / 25 个机构}} 验证，再全量；抓取到约 {{55}}% 再派 agent。
```

分叉项必须在每个项目的任务书里显式勾选，不能继承：
`允许自建 skill：{{是|否}}` · `进入下一阶段：{{免确认|需明确批准}}` · `批次大小：{{25|50|60-80|200|500}}`。

## 3. 交接（Handoff）

他的交接规则只有一句核心：**线程之间只传文件，不传对话**。其余都是这句话的展开——串行顺序、交接前的必读清单、交接提示词要不要写线程标识、迁移包怎么校验。

### 模板条款（每条一行证据）

- **一个线程只做一段：做完把结果写成文件交给下一个线程，不对所有线程来回反馈。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a0c3f4-33d3-7500-88eb-4bb9a8ef424b.md@2026-09-21T12:41:33Z","quote":"每个线程就只管做好它本身的工作，然后把完成的工作交接给到下一个线程，就不对所有线程来来回回做反馈。只做自己的工作就可以","count":null,"confidence":"high"}

- **串行，不串线程：1 → 2 → 3 → 4，严格按序。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md@2026-09-17T12:45:43.054Z","quote":"线程按 `1 → 2 → 3 → 4` 串行交接--严格自行，不要串线程","count":null,"confidence":"high"}

- **协调走数据文件：通过数据文件协调，不通过聊天消息协调。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-d60f6c1b-e768-45c8-8c67-d5d27b492ca4.md#1789880937419","quote":"通过数据文件协调，不通过聊天消息协调。","count":null,"confidence":"inferred"}

- **交接提示词要可复用：给一段明确的提示词，把旧电脑的各发信线程逐个交接。**
{"category":"B","project_line":"us","locator":"voice/codex-01a085d3-2c84-73c2-8a1f-6d20544f0cf5.md@2026-09-16T06:45:11.783Z","quote":"是不是可以给到一个明确的提示词，我要把旧电脑其他发信线程，一个个都对应的做好交接","count":null,"confidence":"high"}

- **不写线程名：由人来做对应，提示词只保证“能正常交接”。**
{"category":"B","project_line":"us","locator":"voice/codex-01a085d3-2c84-73c2-8a1f-6d20544f0cf5.md@2026-09-16T06:50:38.296Z","quote":"线程名就不需要提了，确保能够正常交接就行","count":null,"confidence":"high"}

- **不写线程 id。**
{"category":"B","project_line":"us","locator":"voice/codex-01a085d3-2c84-73c2-8a1f-6d20544f0cf5.md@2026-09-16T06:51:24.931Z","quote":"当前线程id也不需要，我会人工对应给到各个线程","count":null,"confidence":"high"}

- **发信前的必读清单：no go list + 发信清单；一次发信读发信清单并去重；二次发信读“一次已发”+ no go list。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-18ad4fbb-2305-4356-804d-a79a92763487.md#1790064569408","quote":"发信线程每次发信前都需要读取no go list和发信清单，一次发信主要读取发信清单，如果已经发过一次的就要去重，二次发信主要读取发信清单里一次已发的用于二次发送和no go list","count":null,"confidence":"direct"}

- **首封先核对发件箱是否发过：37 个发件箱逐一确认，发过则跳过，新邮箱先去重，再按国家与模板发。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-18ad4fbb-2305-4356-804d-a79a92763487.md#1790059225325","quote":"第一次开发信：1. 确认我们37个邮箱里面的发件箱有没有发送过。2. 如果发送过，就不能发送。3. 持续提供的新邮箱，去除已发送过的。4. 根据不同的国家、不同的发信模板去发信","count":37,"confidence":"direct"}

- **上游交接默认可信：接收上游邮箱，默认可直接发第一封；模板按批次转给发信线程；二次开发信再判历史。**
{"category":"B","project_line":"uk","locator":"voice/codex-01a07534-2091-7e12-94eb-311fd519a140.md@2026-09-08T09:19:30.374Z","quote":"1、接收上游来的邮箱，默认直接可发第一次开发信 2、接收开发信模板，并根据要求给到不同批次开发信发信线程 3、对二次开始的开发信判断是否历史已发、退信、退订或需排除，并决定第几次触达","count":null,"confidence":"high"}

- **交接包要带部署指南：HANDOVER.md 含前置依赖、解压位置、依赖安装、授权流程、验证清单、已踩过的坑。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-921a0643-40a1-44ce-a2c2-2b36df0e01a5.md#1789709324873","quote":"HANDOVER.md：完整部署指南——前置依赖、解压位置、lark-cli 安装、飞书授权流程、验证清单、5 个已踩过的坑","count":5,"confidence":"direct"}

- **线程职责按件事写：例如“本线程就 2 件事：同步邮箱到表、把清单给指定线程”。**
{"category":"B","project_line":"uk","locator":"voice/codex-01a06c53-ab5e-7bb3-b7d9-e6ff2652eaa6.md@2026-09-04T12:52:46.252Z","quote":"本线程就2件事\n1、把线程codex://threads/<thread>同步过来的邮箱更新到<org>表格\n2、把更新后的邮箱清单同步给到指定线程去发开发信","count":2,"confidence":"high"}

- **分片固定：子 agent A/B 各固定 25 个账户。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-d60f6c1b-e768-45c8-8c67-d5d27b492ca4.md#1789880937419","quote":"子Agent A：固定25个账户\n└─ 子Agent B：固定25个账户","count":25,"confidence":"inferred"}

- **换机/迁移是交接的一种：校验 MIGRATION_MANIFEST + CHECKSUMS，只回执三件事。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a0a86b-aa95-7362-90e3-5a5ef44958cd.md@2026-09-16T04:13:52Z","quote":"读取并校验迁移包中的 MIGRATION_MANIFEST.json 和 CHECKSUMS.sha256。恢复完成后，只输出：1. RESTORE_RECEIPT.md 路径 2. 校验成功/失败数量 3. 缺失和冲突文件","count":null,"confidence":"high"}

### 可拼装模板

```text
# 交接契约
1. 每个线程只做自己的一段，做完把结果写成文件交给下一个线程，不做来回反馈。
2. 串行：1 → 2 → 3 → 4；不串线程；每段只负责自己的功能。
3. 交接只通过数据文件，不通过聊天消息。
4. 发信前必读：no go list + 发信清单。
   首封：核对 {{N}} 个发件箱是否发过；发过则跳过。
   二次起：读一次已发 + no go list，判退信/退订/已回复，并决定第几次触达。
5. 交接提示词不写线程名、不写线程 id（由人对应）。
6. 换机：MANIFEST + CHECKSUMS 校验，输出 RESTORE_RECEIPT（路径、成功/失败数、缺失与冲突）。
   HANDOVER.md 必含：前置依赖、解压位置、依赖安装、授权流程、验证清单、已踩过的坑。
```

## 4. 验收（Acceptance）

他验收的核心不是“做完了吗”，而是**回执能不能让他不看过程就判断**。所以验收条款几乎都是“只输出哪几项”“不要只说哪句话”。发信类任务另有一套：执行不是草稿、收件人必须对上批准哈希、结果逐条落盘、回复要分诊。

### 模板条款（每条一行证据）

- **完成定义分两级：阻断项必须 100%；关键项不达标可以交付，但缺口必须显式记录。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-23da98e1-8cae-4124-ad81-39b8e4e5f275.md#1789899746632","quote":"严格遵守 KICKOFF 第 7 节的五条硬纪律与第 8 节的完成定义。阻断项必须 100%；关键项不达标可以交付，但必须显式记录覆盖缺口。","count":null,"confidence":"direct"}

- **回执五项：权威来源、直接下载/API、当前与预期规模、已写文件、访问阻碍。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a04189-c935-76c0-849e-9a7a7a4cdd2d.md@2026-08-27T14:31:54Z","quote":"完成后汇报：权威来源、直接下载/API、当前/预期规模、已写文件、访问阻碍。","count":null,"confidence":"high"}

- **禁止“代码已创建”：必须给创建/修改的文件、项目绝对路径、watchdog 实际 PID、当前并发、队列 SHA256、全量 dry-run、下一句只需要用户确认的精确一句话。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-08-28T10:54:21","quote":"不要只告诉我「代码已经创建」。完成后必须报告：创建和修改的文件、项目绝对路径、supervisor/watchdog实际PID、当前研究并发、队列SHA256、全量dry-run结果、下一步只需要用户确认的精确一句话。","count":null,"confidence":"high"}

- **测试必须真跑：覆盖 T1/T2 判定、跨公司进 T5、suppression 命中、已 SENT 跳过、rate-limit 退避、timeout 不重发。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-08-28T10:54:21","quote":"必须创建并实际运行测试，至少覆盖：T1公司域名邮箱判定、T2官方来源免费邮箱判定、跨公司邮箱进入T5、suppression命中、已SENT邮箱跳过、rate-limit指数退避、timeout不会自动重发…","count":null,"confidence":"high"}

- **确认只问一次：展示具体队列后，只问“确认发送 X 的 N 封”。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-08-28T10:54:21","quote":"向用户展示具体队列后，只询问一次：「确认发送 <具体队列名> 的<N>封」","count":null,"confidence":"high"}

- **停研有最低条件：普通账户至少满足三项中的 2–3 项才可停。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790229527579","quote":"普通Golf Account可以停止深研的最低条件：至少满足以下三项中的2–3项","count":3,"confidence":"direct"}

- **回复要分诊：带字面替换邮箱的回复是联络路由事件，不是有效回复，也不触发销售卡。**
{"category":"B","project_line":"golf","locator":"voice/codex-01a013fd-d363-7e13-a157-306a09f06647.md@2026-08-19T13:33:54.573Z","quote":"A campaign-matched reply that explicitly gives a literal replacement email is a contact-routing event, not an effective-reply or sales-card event.","count":null,"confidence":"inferred"}

- **监控配置必须显式声明未决消息的去向：review_route 指本线程、thread_review_only、并落一份可追溯的 review 队列文件。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-22T10:06:33.855Z","quote":"New monitor configs must declare `review_route: \"current_codex_thread\"`, `thread_review_only: true`, and a durable `thread_review_queue.jsonl` artifact","count":null,"confidence":"inferred"}

- **监控四件事：已扫描不重扫、只扫新增；7 天窗口内查是否加旗，判为有效终端回复的发本线程评审；发群前判历史加旗与群是否已发；扫描结合标题和正文。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-e4bdcdff#1790151927159","quote":"1、后续邮箱监控，监控过的不要重复扫描，记录好时间节点，下次只扫描新增的 2、后续对于7天有没有加旗，但是判断是有效的终端回复，发本线程评审 3、发群前一定要判定有没有历史加旗和群是否已发国 4、扫描一定要结合标题、正文","count":4,"confidence":"high"}

- **回复展示可判读：N 条真人回复给中文内容；替换邮箱按新邮箱进下一轮清单。**
{"category":"B","project_line":"us","locator":"voice/codex-01a0a9c7-b536-70a1-a311-dd27aadde9ec.md@2026-09-16T11:35:23.774Z","quote":"7条真人回复具体内容中文展示，便于我判断 1条提供替换邮箱按新邮箱，后续一起和新的邮箱清单发","count":7,"confidence":"high"}

- **发信是执行不是草稿：收件人队列与批准哈希/上限不一致、suppression/历史有歧义、时间在窗口外，一律不发。**
{"category":"B","project_line":"us","locator":"voice/codex-01a00fa1-34b6-7780-8fe2-b782fec514bc.md@2026-08-17T12:10:21.341Z","quote":"Run the authorized U.S. baseball stage-1 bulk first-touch send at 20:10 Asia/Shanghai on 2026-08-17. This is an execution job, not a draft.","count":null,"confidence":"high"}

- **收件人哈希与上限是硬闸门：队列与批准的 138 人不一致就不发。**
{"category":"B","project_line":"us","locator":"voice/codex-01a00fa1-34b6-7780-8fe2-b782fec514bc.md@2026-08-17T12:10:21.341Z","quote":"Do not send if the fresh eligible queue differs from the approved 138-recipient hash/limit, if any suppression/history ambiguity appears, or if the current time is outside the manifest window","count":138,"confidence":"high"}

- **图片结构是验收项：2 张 CID 内联图、零普通附件。**
{"category":"B","project_line":"us","locator":"voice/codex-01a00fa1-34b6-7780-8fe2-b782fec514bc.md@2026-08-17T12:10:21.341Z","quote":"Preserve the 2 CID-inline image MIME structure and zero ordinary attachments.","count":2,"confidence":"high"}

- **结果即时落盘：每条结果立刻追加到 canonical campaign log 和批次结果文件。**
{"category":"B","project_line":"us","locator":"voice/codex-01a00fa1-34b6-7780-8fe2-b782fec514bc.md@2026-08-17T12:10:21.341Z","quote":"append each outcome immediately to the canonical campaign log .../send_results.jsonl and to the batch send-results file.","count":null,"confidence":"high"}

- **两阶段发送：create-draft → send，每条只发一次，用指定发件箱和逐收件人 HTML。**
{"category":"B","project_line":"us","locator":"voice/codex-01a00fa1-34b6-7780-8fe2-b782fec514bc.md@2026-08-17T12:10:21.341Z","quote":"send each remaining eligible record exactly once through a two-phase create-draft then send flow, using its exact assigned sender mailbox and per-recipient HTML body under the batch folder.","count":null,"confidence":"high"}

- **监控按国家分流：已加旗不管；未加旗判国家、是否有效、是否终端，再发对应飞书群。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a0bd5e-c37b-7c70-a15b-fec8a5f40a80.md#2026-09-20T05:51:42","quote":"1. 如果是已经加旗的，就不管 2. 如果没有加旗的，就判断：a. 是哪个国家的 b. 是不是有效回复 c. 明确就是终端的 然后就根据国家，发到对应的指定飞书群里面","count":null,"confidence":"direct"}

- **对时不能漏扫：时间以本机为准，时间必须对上。**
{"category":"B","project_line":"cross","locator":"workbuddy-e4bdcdff#1790216819536","quote":"1、时间都按本机电脑的时间来现在本机电脑时间是10：25 2、时间上一定要对上，不能漏扫描 3、其他人已经再发有效恢复了，说明是有有效回复的，你要看下","count":null,"confidence":"high"}

- **单封测试有上限：测试邮件限 1 封，且发件别名必须通过能力预检。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-09-01T10:44:43","quote":"发送美国 PL01 锁定模板测试邮件至 <email>，使用能力预检通过的 <name> 发件别名，限 1 封。","count":1,"confidence":"high"}

- **交付要附 SOP：要求输出整条调研的思考、SOP 和质量把关要求。**
{"category":"B","project_line":"uk","locator":"voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md@2026-09-10T06:31:50.798Z","quote":"读取英国天然草坪调研所有内容输出整个调研的思考、sop和质量把关要求","count":null,"confidence":"high"}

### 可拼装模板

```text
# 验收与回执
完成定义：阻断项 100%；关键项不达标可交付，但必须显式记录覆盖缺口。
回执只写结论：权威来源 / 直接下载或 API / 当前与预期规模 / 已写文件 / 访问阻碍。
禁止“代码已创建”：必须给 文件、绝对路径、PID、并发、队列 SHA256、全量 dry-run、下一句精确确认。
测试：真跑 T1/T2/T5、suppression、已 SENT 跳过、rate-limit 退避、timeout 不重发。
发信：这是执行作业不是草稿；队列哈希/上限/窗口任一不符即不发；每条结果即时落盘。
回复分诊：带字面替换邮箱 = 联络路由事件，不是有效回复，不触发销售卡。
监控：不重扫、只扫新增；发群前复核加旗与群重复；结合标题与正文。
```

**反例，直接写进验收**：`不要只告诉我「代码已经创建」`。这一句只出现一次，但它约束了后面所有回执的形状。

## 5. 停线（Stop line）

停线规则分三种强度：**静默停止**（异常不可解，停全部并只通知一次）、**阶段闸门**（做完这一段必须等口令）、**只读/幂等闸门**（越界写、重复发、超时重发一律禁止）。他的停线理由几乎都指向同一件事：夜里自动跑会烧掉无效 token，以及重复触达不可逆。

### 模板条款（每条一行证据）

- **异常久解不决 → 全停一次通知：所有流程先静默停止，只通知一次，避免夜里空烧 token。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md@2026-09-07T14:33:30.159Z","quote":"就这个流程在跑的时候，如果你遇到异常，然后一直解决不了，就让所有的流程先停止静默，然后你通知我一次就可以了，不然的话，比如说我晚上睡觉，你就在自动跑，会很多无效的 token 在浪费。","count":null,"confidence":"high"}

- **讨论期不改流程：讨论阶段只读现状，直到模板与排版线程调通。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md@2026-09-08T10:53:43.527Z","quote":"讨论，不该任何流程，你读取现在的流程，一直到模板与排版线程已经调通了","count":null,"confidence":"high"}

- **阶段闸门：上一阶段完成即停；只有收到“开始打包”这类明确口令才进入下一阶段。**
{"category":"B","project_line":"other","locator":"voice/workbuddy-f98d1ede-e3c8-4888-a94e-883c324eb9f7.md#1790233310446","quote":"第一阶段完成后停止。不要开始压缩。等待我确认。第二阶段：只有我回复“开始打包”以后才执行","count":null,"confidence":"direct"}

- **只读任务不落盘：不复制、移动、删除、压缩或修改任何文件。**
{"category":"B","project_line":"other","locator":"voice/codex-01a0d237-2a17-7863-b4ae-95aacc18bae3.md#2026-09-24T07:00:28","quote":"这一阶段只做只读扫描和统计，不要复制、移动、删除、压缩或修改任何文件。","count":null,"confidence":"direct"}

- **原始文件不可变：原始 Session 必须 byte-for-byte 原样保留。**
{"category":"B","project_line":"other","locator":"voice/codex-01a0d237-2a17-7863-b4ae-95aacc18bae3.md#2026-09-24T07:04:01","quote":"第一原则：原始 Session 文件必须 byte-for-byte 原样保留。","count":null,"confidence":"direct"}

- **只读范围写到系统边界：禁止发信、邮箱扫描、外部表格写入、表单提交。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a04189-c935-76c0-849e-9a7a7a4cdd2d.md@2026-08-27T14:31:54Z","quote":"只做公开来源研究与本地文件写入；禁止发信、邮箱扫描、飞书写入、表单提交。","count":null,"confidence":"high"}

- **只读任务列黑名单：不推群卡片、不加旗、不发邮件、不改口径源文件。**
{"category":"B","project_line":"cross","locator":"voice/workbuddy-06ad75e0-edf2-4dd1-8606-3375d276a10c.md@1790061008749","quote":"严格禁止（本任务只读）：不推送飞书群卡片、不加旗（FLAGGED）、不发送任何邮件、不修改 final_v6.json / audience_*.json 等口径源文件","count":null,"confidence":"high"}

- **不自动发信：研究/整理线程默认不自动发开发信。**
{"category":"B","project_line":"fr","locator":"voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md@2026-09-17T12:45:43.054Z","quote":"不自动发送开发信--严格执行","count":null,"confidence":"high"}

- **建队列 ≠ 已发送：历史 SENT 是唯一幂等依据；已 sent 的地址后续所有批次自动跳过。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-08-28T10:54:21","quote":"「创建了队列」绝不能统计成「已发送」。历史SENT结果是唯一发送幂等依据。任何邮箱一旦确认sent，后续所有批次都必须自动跳过。","count":null,"confidence":"high"}

- **超时不重发：标记 ambiguous_send_timeout，先核 SENT，SENT 不存在才允许补发。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-08-28T10:54:21","quote":"遇到API或网络超时：标记 ambiguous_send_timeout。禁止自动重发。先搜索对应发件箱SENT，按收件人、主题、时间和message_id核验…SENT不存在才允许补发。","count":null,"confidence":"high"}

- **历史缺失是挂起，不是“未发送”：缺失的历史日志只能记为对账挂起。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-31T05:40:46Z","quote":"For every <org> outbound development-email queue, treat cross-thread contact history as mandatory. A missing historical log is a reconciliation hold, never proof that the address was unsent.","count":null,"confidence":"high"}

- **前员工不进可触达：FORMER 联系人不得进入 READY_TO_OUTREACH。**
{"category":"B","project_line":"golf","locator":"voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790229527579","quote":"FORMER联系人不要进入READY_TO_OUTREACH。","count":null,"confidence":"direct"}

- **只有 T1/T2 可自动发：T5 永不发送。**
{"category":"B","project_line":"cross","locator":"codex-01a047ff#2026-08-28T10:54:21","quote":"T1：在目标公司官网或同公司官方页面明确公开的公司域名邮箱…T5：无来源、跨公司错配、占位符、无效地址或其他不安全路线，永不发送。默认自动发送候选范围只能是T1和T2。","count":5,"confidence":"high"}

- **迁移只碰本地：只恢复本地文件和规则，不发信、不读邮箱、不写外部表格，不混用其他国家 campaign 数据。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a0a86b-aa95-7362-90e3-5a5ef44958cd.md@2026-09-16T04:13:52Z","quote":"这是 Codex 迁移恢复任务。只恢复本地文件和规则，不发送邮件、不读取邮箱、不写入飞书。不要加载或混用其他国家 Campaign 数据","count":null,"confidence":"high"}

- **稳定环节不停：已跑稳的母池继续跑，只修下游跑不通的那一段。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md@2026-09-07T14:26:05.693Z","quote":"然后已经调整好的，比如说母池现在基本上运行稳定的来源这块，那就让它继续动就行了。要解决的就是下面的流程要跑通。","count":null,"confidence":"high"}

- **发群去重闸门：发群前检查是否已加旗、是否已发过群。**
{"category":"B","project_line":"cross","locator":"voice/codex-01a0431b-02d7-7f40-b479-5b3caf2c92e8.md@2026-09-15T09:25:27.042Z","quote":"发群前也要看下是否之前有加旗发群了，避免重复群","count":null,"confidence":"high"}

### 可拼装模板

```text
# 停线规则
- 异常久解不决：全部流程静默停止，只通知一次。
- 讨论期：不改任何流程，只读现状。
- 分阶段任务：上一阶段完成即停，收到明确口令才进入下一阶段。
- 只读任务：不复制/移动/删除/压缩/修改；不写外部系统；不发信/不扫描/不提交表单；不推群卡/不加旗/不改口径源。
- 原始文件：byte-for-byte 原样保留。
- 发信幂等：建队列 ≠ 已发送；确认 sent 的地址永远跳过。
- 超时：标记 ambiguous_send_timeout，禁止自动重发，先核 SENT。
- 历史缺失：记为对账挂起，不得当“未发送”。
- 分级：只有 T1/T2 可自动发；T5 永不发送。
- 联系人与群：FORMER 不进可触达；发群前查加旗与重复群。
- 稳定环节不停，只停/修出问题的那一段。
```

## 6. 未覆盖、冲突与缺什么

### 冲突（并列，不裁决）

1. **是否允许 agent 自建 skill**：德国运动场「Do NOT recreate the skill」 vs 美国泳池「需不需要你自己再创skill」。两句都有 locator，见 §2。合理读法是“改已有 skill 时禁止重建，新建场景时鼓励自建”，但语料里没有这句话，不能替他补。
2. **授权闸门**：瑞士足球「读取母池后立即开始…无需再次确认」 vs 德国运动场「Do not proceed to Deep Research until I explicitly approve it」。见 §1。
3. **批次大小**：25 个机构、50 个账户、60–80（249 中选）、200 邮箱、500 邮箱都出现过。它们不是同一量纲（机构 / 账户 / 邮箱 / 网页），但都叫“一批”，直接复用会错配。
4. **排版规则的载体**：他说“更新到邮件监控规则中去”，但同一时期又把排版指回“波兰学校-发信和监控”线程；监控规则与发信模板是不是同一个文件，语料里没有答案。

### 缺口

- **KICKOFF 正文缺失**：两处引用 KICKOFF 第 3/7/8 节，但分片里只有引用句，没有 KICKOFF.md 本体。模板库能给出结构，给不出他那五条硬纪律的原文。
- **验收文档缺失**：只有“回执五项”“只输出三件事”这类任务书条款，没有一份完整的验收清单文件。
- **停线没有预算/时长上限**：停线动机是“省 token”，但语料里没有出现“每批最多 N 分钟 / N 美元”的硬上限。
- **交接 schema 缺失**：提到“三份交接契约 + sent index”，但没有字段级 schema；字段名只在 H 类（交付物）里有，本片不越界引用。
- **B 类在部分业务线偏薄**：法国、英国主要靠 fr-grass / uk 的少量长消息；瑞士只有 1 条独占的开场/授权（另 2 条并入 §0 的跨线复述）；高尔夫和美国的契约文本最完整。因此“验收/停线”更偏 golf/us/cross，不能当成所有业务线都已验证。

### 本片计数

- 输入：52 个分片，B 类 **179 行**；去重后 **59 个会话 / 118 条消息**。
- 本文引用：**104 行**（§0 11 行，并列 locator 49 个；§1 12；§2 33；§3 13；§4 19；§5 16），覆盖 **93 条不同消息**。§0 的复述行按“代表消息”计一次。
- 未收进模板库的 B 行主要是：归档/报表类（300+ leads、7 项工作流）、单次路径约定（跨项目记忆 vault 路径）、以及与其他类重复的口径句。
