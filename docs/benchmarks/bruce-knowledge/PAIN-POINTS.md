# PAIN-POINTS：什么让调研变慢，什么让它变快

> 出处：本文由本机语料派生层装配（`ARTIFACTS_ROOT/marketops/bench/v0.6.2/bruce-knowledge/`），
> 该目录不随仓库分发。文中 `locator`（`voice/…`、`work-Wxx.md:行号`、`codex-<id>@<时间戳>`）
> 指向派生层文件，用于回查原话，仓库内无对应文件。语料本身（11 GB 会话、页面正文、第三方联系方式）不进 Git。

覆盖 Bruce 语料的 C（卡点）、G（编排与速度）、K（工具）、J（投入与成本）四类发现。
本片只读 `findings/*.jsonl` 与 `final/lines/*.md`，未读原始语料、未联网、未发送任何东西。

- 证据基数：52 个分片（voice-01..16、work-01..36），4,022 条 finding；C 649 · G 448 · K 433 · J 236，按 (category, locator, quote) 去重后 1,766 条。
- 本文引用 210 条（去重后），每条一行 JSON，字段固定：`category, project_line, locator, quote, count, confidence`。
- `count` 直接取自 finding 的 count 字段，本文未重新计算；上游抽取偶有与 quote 内数字对不齐的情况（如把批次号写成 count），一律保留原值。原 finding 无数字时为 `null`。
- `quote` 为原文前缀，≤200 字；人名/邮箱/机构/电话已替换为 `<name>`/`<email>`/`<org>`/`<phone>`。
- 同一事实在不同消息里数字不一致时，**并列、不裁决**（见第十二节）。
- 交叉核对：`final/lines/` 下 9 个 md 共抽出 1,146 行 JSON、1,103 条唯一；其中 774 条与 findings 的 quote 不是逐字一致（合并 agent 做过改写），未作为证据收录。`golf.md`、`other.md` 是纯叙述体，无 JSON 行。

**一句话结论**：把时间吃掉的不是抓取，而是「等」和「重做」——等租约、等回执、等下一个线程启动、等一个不会来的定时任务；重做来自静默丢行、断链、并发覆盖和门禁返工。提速的杠杆顺序是：修缺陷 > 换确定性脚本 > 并发 > 加机器（原文实测 21.6 倍 vs 1.0 倍，G280）。

## 一、让调研变快：并行与分片

同一台机器、同一批站点，只有并发结构变了的对照。

```jsonl
{"category": "G", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L204@1790072889017", "quote": "还有两个性能坑：MX 验证 DoH 单线程 39 分钟只跑 1,800 条 → dnspython+24 线程 **156 秒**；官网抓取单线程 57 分钟只跑 40 站（不可达站点阻塞 72s/站）→ 16 线程 **10 分钟**。", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L342@1790078110356", "quote": "分片并行：8 进程 × 32 线程 → 11,698 域约 50 分钟（单进程 4 小时），按 domain 断点可续。", "count": 11698, "confidence": "direct"}
{"category": "G", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L244@1790075030291", "quote": "三进程并行生效：402 域 / 2.5 分钟 ≈ **160 域/分**，约 73 分钟完成。", "count": 160, "confidence": "direct"}
{"category": "G", "project_line": "golf/DE", "locator": "work-W27.md#1790065650992", "quote": "73.7 域/min at 28 workers → full 5,755 domains ≈ 78 min. Launching the full sweep.", "count": 28, "confidence": "high"}
{"category": "G", "project_line": "golf/DE", "locator": "work-W27.md#1790065730613", "quote": "110 域/min → full sweep ≈ 50 min. Let me record progress to memory now, then continue monitoring.", "count": 110, "confidence": "high"}
{"category": "G", "project_line": "golf/global", "locator": "work-W27.md#1790082611131", "quote": "★ **必须多线程** —— 单线程跑了 **26 分钟仍在第一阶段**；12 线程后 **3 分钟跑完两阶段**。", "count": 12, "confidence": "high"}
{"category": "G", "project_line": "golf", "locator": "W24/c8b43899@1790050582918", "quote": "详情 17,206 次 × 10 并发 = 12.1/s，约 24 分钟，带断点续跑。", "count": 17206, "confidence": "direct"}
{"category": "G", "project_line": "golf", "locator": "W24/c8b43899@1789964003188", "quote": "Confirmed: ~43,421 records (matches KICKOFF's 「45k sites」), concurrency works (8× speedup), and the cursor is a partitionable `modified` timeline.", "count": 8, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:764 [1789981451083]", "quote": "**端到端验证通过**：8 站 8 路 **63.5s**（vs 1 路 196s）→ **2.74× 提速**", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "golf", "locator": "W24/c8b43899@1790065324495", "quote": "并发采集把重采从 59 分钟压到 16.7 分钟（2 worker，每 worker 独立 CookieJar）", "count": 16.7, "confidence": "direct"}
{"category": "G", "project_line": "golf/GB", "locator": "W22:1790068650", "quote": "速率提上来了（4.4/秒/片 × 3 片 ≈ 13/秒，预计 18 分钟跑完）", "count": 13, "confidence": "direct"}
{"category": "J", "project_line": "golf/global", "locator": "work-W27.md#1790084176781", "quote": "29 秒完成（对比 v1 卡 15 分钟）—— 分块+多线程有效。读结果：", "count": 29, "confidence": "high"}
```
## 二、让调研变快：换协议、换通道

不是加机器，而是换一条更便宜的路：静态 HTTP 替浏览器、DNS 预算替逐域重试、收紧超时替加并发。

```jsonl
{"category": "G", "project_line": "golf/DE", "locator": "work-W27.md#1790065478544", "quote": "Static HTTP gives 25.8 domains/min vs CDP's ~2/min — 13× faster. Let me tune concurrency higher and measure.", "count": 25.8, "confidence": "high"}
{"category": "G", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L266@1790076399161", "quote": "DNS 预过滤是根因修复：**95 域/分**（原 26）且发现 6.1% 域已失效。", "count": 95, "confidence": "direct"}
{"category": "G", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L1393@1790150512635", "quote": "诊断出 phase2 瓶颈是**超时不是并发**（16 个候选页常一半 404、各烧满 8s），收紧超时后提速 **×15**", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L1250@1790147581959", "quote": "收紧超时（页 8→5s、PDF 16→11s、PDF 上限 9MB→6MB）后重启为 **6 分片 × 60 并发**", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L1088@1790145916530", "quote": "Rate is now **1.81/s (2.7× faster)**, ~1.8h remaining.", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "uk-paintmaster-natural-grass", "locator": "codex-01a07534-69c6-7522-a713-ae334dd01be9 @2026-09-06T12:45:16Z", "quote": "40 组并行查询在 30.2 秒得到 134 个去重后的官方域名候选，按实测可在 3 分钟内超过 200。", "count": 134, "confidence": "direct"}
{"category": "G", "project_line": "global-golf-pool", "locator": "work-W38.md:L1303@2026-09-21T05:21:23Z", "quote": "发现成本从 O(国家数) 降到 O(形态数)。", "count": null, "confidence": "direct"}
```
## 三、让调研变快：改流水结构，而不是加人

把「每个小步都等一次 agent」改成「确定性脚本跑完再交 agent」，把串行关卡并掉，把「凑满 10 个再交」改成「确认一个交一个」。

```jsonl
{"category": "G", "project_line": "美国", "locator": "work-W33.md:324 [1789964985514]", "quote": "但这次失败让我意识到一件更重要的事：**Stage 1 根本不需要 agent**。CDP 脚本已经能做同样的事（抓页面 + 发现子页 + 写缓存），且**确定性、无速率限制、还通吃 JS/CF**。", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:501 [1789976674097]", "quote": "**新分工：Stage 1 用 CDP · agent 只做 Stage 2 语义判断**（且串行，≤2 路）。", "count": 2, "confidence": "direct"}
{"category": "G", "project_line": "uk-grass", "locator": "work-W12.md:3164", "quote": "每批 = 10 个独立、净新增、可批量产出的官方母池渠道；每确认 1 个渠道即立即下发，不等待凑齐 10 个。", "count": 10, "confidence": "confirmed"}
{"category": "G", "project_line": "英国", "locator": "work-W14.md:1409", "quote": "每个渠道验证后立即交接，不等凑满 10 个；", "count": 10, "confidence": "direct"}
{"category": "G", "project_line": "uk-pmpro-natural-grass", "locator": "work-W42.md#L568", "quote": "8 条真实入口验收已完成，约 19 秒，游标从 8 推进到 16。", "count": 19, "confidence": "direct"}
{"category": "G", "project_line": "UK-PMPro-natural-grass", "locator": "work-W43.md:L485", "quote": "第二组 8 条在约 19 秒内完成“镜像→字面邮箱→质量归档→游标推进”，没有线程等待。", "count": 19, "confidence": "direct"}
{"category": "J", "project_line": "US|非高尔夫运动场母池", "locator": "work-W44.md:2488", "quote": "补跑成本极低（粗提 34,156 页只用 **2.4 分钟**），收益是唯一邮箱数从 32,796 → **72,484（+121%）**。", "count": null, "confidence": "direct"}
{"category": "J", "project_line": "global-golf-pool", "locator": "work-W38.md:L1286@2026-09-21T05:15:25Z", "quote": "**验证省下了 35 分钟**", "count": 35, "confidence": "direct"}
{"category": "G", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789997892831", "quote": "修 3 个缺陷收益 21.6 倍；加并发收益 1.0 倍", "count": 21.6, "confidence": "direct"}
```
## 四、让调研变快：强制增量落盘

两条对照都来自同一个失败形态：agent 跑了一小时、上下文耗尽，磁盘上零产出。

```jsonl
{"category": "G", "project_line": "US|非高尔夫运动场母池", "locator": "work-W44.md:840", "quote": "**强制增量落盘** —— 每完成约 5 个单元就覆盖写一次。**实测：没写这条时，5 个 agent 跑 1 小时零产出**（上下文耗尽前无法落盘）", "count": 5, "confidence": "direct"}
{"category": "G", "project_line": "other", "locator": "W31/workbuddy-77a872c4:1789730040141", "quote": "不写这条，5 个 agent 跑 1 小时零产出（上下文耗尽前没落盘）。写上之后 46 个单元 20 分钟跑完。", "count": 46, "confidence": "direct"}
{"category": "G", "project_line": "英国", "locator": "work-W50.md:3497", "quote": "已开启定时汇报：每 10 分钟更新一次处理进度；批次闭环、同步失败或需人工判断时会立即汇报。", "count": 10, "confidence": "direct"}
```
## 五、让调研变快：发信速率

速率提升来自发件箱数量和逐步放开间隔，不是文案或名单变化。各条口径不同（分钟/小时、瞬时/全程），不合并。

```jsonl
{"category": "G", "project_line": "波兰学校-发信和监控", "locator": "work-W37.md:397", "quote": "切换后最新一分钟已到约 **7 封/分钟**；近 5 分钟仍包含切换前数据，约 **4.8 封/分钟**", "count": 7, "confidence": "direct"}
{"category": "G", "project_line": "au-schools", "locator": "work-W39.md:L453@2026-08-27T09:10:40Z", "quote": "当前确认发送速度约 280–300封/小时，即每分钟约 4.6–5封", "count": 300, "confidence": "direct"}
{"category": "G", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-08-20T19:25:37Z", "quote": "最近15分钟确认发送159封（10.60封/分钟）", "count": null, "confidence": "high"}
{"category": "G", "project_line": "uk-football-pitch-outreach", "locator": "codex-01a06b86-51c1-7032-ade2-677c2d25c7a0 @2026-09-08T15:18:59Z", "quote": "近 10 分钟发送 224 封，22.4 封/分钟", "count": 22.4, "confidence": "direct"}
{"category": "G", "project_line": "英国运动场/学校-发信和监控（一次）", "locator": "work-W55.md:L2261@2026-09-08T16:11:32.826Z", "quote": "00:11 进展：成功发送 6,539 / 7,000（93.4%）；待发 251；首轮暂缓 204；已对账跳过 6。近 10 分钟发送 226 封，22.6 封/分钟；进程正常运行。", "count": 22.6, "confidence": "direct"}
{"category": "G", "project_line": "us-golf", "locator": "W67/codex-01a00f13:2026-09-07T13:17:30Z", "quote": "当前确认发送速度约 550–590 封/小时", "count": 550, "confidence": "direct"}
{"category": "G", "project_line": "au-schools", "locator": "work-W39.md:L197@2026-08-27T04:45:25Z", "quote": "先用4条邮箱通道健康启动，最多扩展到9条", "count": 4, "confidence": "direct"}
{"category": "G", "project_line": "au-schools", "locator": "work-W39.md:L209@2026-08-27T04:54:16Z", "quote": "并发已完成健康扩展，最近一分钟确认了17封，速度较启动阶段明显提升", "count": 17, "confidence": "direct"}
```
## 六、让调研变慢：串行关卡与空转

这些数字指向同一件事：时间不是花在抓取上，而是花在「等下一个线程/下一张回执/上一个 agent 结束」上。

```jsonl
{"category": "G", "project_line": "uk-pmpro-natural-grass", "locator": "work-W42.md#L447", "quote": "当前效率低的根因不是下载网页慢，而是每完成一个小步骤就结束一次任务、等待下一线程启动。", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "uk-pmpro-natural-grass", "locator": "work-W42.md#L230", "quote": "现在真正多余的是“母池密封包审核 → 独立主体归并审核 → 再交网页工位”的两层串行关卡", "count": 2, "confidence": "direct"}
{"category": "G", "project_line": "UK-PMPro-natural-grass", "locator": "work-W43.md:L154", "quote": "当前真正多余的是“母池密封包审核 → 独立主体归并审核 → 再交网页工位”的两层串行关卡。", "count": 2, "confidence": "direct"}
{"category": "G", "project_line": "fr-sports-field-joint", "locator": "codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3 @2026-09-17T09:57:24Z", "quote": "你的判断基本正确：当前“网页提取 → 网页质量 → 网页镜像”拆成 3 个线程，已经成为主要瓶颈。", "count": 3, "confidence": "direct"}
{"category": "G", "project_line": "uk-paintmaster-email", "locator": "work-W51.md:1641", "quote": "它没有活跃网络连接且对 36×10 个页面按顺序处理，完成时间可能很长", "count": 360, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:913 [1789984712844]", "quote": "解方程得：**固定头 ≈ 46.8KB（11.7K tokens/片）**，每页内容仅 1.9KB。", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:850 [1789984312345]", "quote": "任务书 **13.7K~30.6K tokens/片** —— 这就是 6 路并发崩溃 + 慢的根因。", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789994811934", "quote": "任务书拆得越碎，固定头（≈46.8KB/片）被重复得越多，总 token +123%。这是负收益。", "count": 123, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:2172 [1790053910230]", "quote": "**agent 分片需 42 个**（4,473 候选 / 1,233 页）。用 60 页/片降到 ~21 个", "count": 21, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:816 [1789981451083]", "quote": "**但 Stage 2 的 agent 才是真瓶颈**：DE 238 户用了 40 分钟 → 全量 **≈140 小时**，比抓取还长，且 **agent 并发上限实测只有 2**（6 路全灭）。", "count": 140, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:624 [1789976674097]", "quote": "DE 238 户 ≈ 2 小时 → 全量 113,839 户 ≈ **956 小时单线程（40 天）**，4 路并行约 10 天 → **必须按州分批**。", "count": 956, "confidence": "direct"}
```
## 七、让调研变慢：并发用错

并发不是单调收益。超过机器或下游上限之后，吞吐反而下降；分片维度选错，会让一片顶三片。

```jsonl
{"category": "G", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789994811934", "quote": "本机实测：24 核 → 8 路 Chrome = 2.84×；且必须每实例独立端口 + 独立 profile（共用 profile 名时后两个会静默失败），不宜盲目超 12 路。", "count": 2.84, "confidence": "direct"}
{"category": "G", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789995012625", "quote": "分片按「户数」而不是「站点数」 —— 50 户切成 [25户/8站点] vs [25户/25站点] —— 户数一样但工作量差 3 倍，后者成瓶颈", "count": 3, "confidence": "direct"}
{"category": "G", "project_line": "other", "locator": "W31/workbuddy-77a872c4:1789908486251", "quote": "按户数切成 [25户/8站点] vs [25户/25站点] —— 户数一样但工作量差 3 倍", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789995012625", "quote": "原因是户/站点比是 2.28（同学区多所学校共享同一官网）。按行号切 1000 户，各片实际要抓的站点数可能差 3 倍", "count": 2.28, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:1661 [1790017279969]", "quote": "抓取速度**明显偏慢**（合计 7.3 页/分钟，而 A 批单跑 12 路是 20.5 页/分钟）—— 193 个 Chrome 进程已造成资源竞争。", "count": 7.3, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W33.md:1784 [1790017279969]", "quote": "单个 12 路 = 18~20.5 页/分钟；**两个 fanout 各 6 路并行 = 7.3 页/分钟**（Chrome 进程翻倍导致资源竞争）。**串行才对**", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "uk", "locator": "codex-01a06617-2143-7431-b70d-c43863864253.md.part3 @ 2026-09-04T02:35:48Z", "quote": "大多数批次约 2–5 分钟，异常批次可拖到 7 分钟以上；之前有一批耗时约 32 分钟。慢页面、Cloudflare、超时和重定向是主要原因。", "count": 32, "confidence": "direct"}
```
## 八、让调研变慢：单位产出效率

这些是「跑得动但产出不够」的读数，决定同一小时能拿到多少可用邮箱/站点。

```jsonl
{"category": "G", "project_line": "uk", "locator": "codex-01a06617-2143-7431-b70d-c43863864253.md.part3 @ 2026-09-04T02:35:48Z", "quote": "近期的实际有效产出约为 30–35 个严格有效邮箱/小时，而不是 146 ÷ 3.5 = 42 个/小时。", "count": 35, "confidence": "direct"}
{"category": "J", "project_line": "UK-学校户外运动场划线-邮箱调研", "locator": "01a06a7e-d801-7c12-b790-4fbba7b628a7/2026-09-04T10:53:19", "quote": "活跃产出阶段：1.69 个有效邮箱/小时；此前为约 1.52 个/小时，提升约 11%", "count": 1.69, "confidence": "direct"}
{"category": "J", "project_line": "美国", "locator": "work-W33.md:2635 [1790069936652]", "quote": "逻辑是**逐条写**（896 条 = 896 次 CLI 调用）。之前 42 分钟全耗在这——每次调用还有 3 次重试。", "count": 896, "confidence": "direct"}
{"category": "J", "project_line": "美国", "locator": "work-W33.md:1803 [1790017279969]", "quote": "**第 1 块完成：3,236 页 / 3.0 小时 / 全 exit 0**", "count": 3236, "confidence": "direct"}
{"category": "J", "project_line": "美国", "locator": "work-W33.md:2849 [1790111499481]", "quote": "✅ **c05 块成功**（新增 **5,289 页**，9.6 小时）—— 缓存涨到 **21,422 页**", "count": 5289, "confidence": "direct"}
{"category": "J", "project_line": "FJD终端触达-监控", "locator": "work-W36.md:340@1790082209856", "quote": "反查跑了 26 分钟还没完 —— 逐条全会话搜索确实太慢", "count": 26, "confidence": "direct"}
{"category": "J", "project_line": "US|非高尔夫运动场母池", "locator": "work-W44.md:3248", "quote": "**剩余 4,468 站点 ÷ 2.1 ≈ 35 小时**", "count": null, "confidence": "direct"}
{"category": "J", "project_line": "美国-非高尔夫运动场-母池深度调研", "locator": "codex-01a0c3f4-33d3-7500-88eb-4bb9a8ef424b.md@2026-09-21T12:34:14.680Z", "quote": "4,554 批、3,567 小时（连续运行约 149 天）", "count": 4554, "confidence": "direct"}
{"category": "J", "project_line": "global-outreach-ledger", "locator": "work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790133872198", "quote": "单箱 07-01 起可超 **3 万封**（`<email>` 32,021、`<email>` 33,306），**37 箱合计近百万封**。单进程要 **36 小时**", "count": 36, "confidence": "direct"}
{"category": "J", "project_line": "cross-project-tool-runtime", "locator": "work-W47.md#codex-01a00db7", "quote": "有一个任务的输入上下文达到 626,397 tokens；超长任务与不完整工具调用会显著增加卡死/假死风险", "count": 626397, "confidence": "direct"}
{"category": "G", "project_line": "golf", "locator": "W24/c8b43899@1790000704158", "quote": "实测 8 条/秒、成功率约 19%（3,224 行 → 614 个域名）…剩下 16,776 行要跑约 35 分钟…为了把 42.5% 抬到 47% 烧 35 分钟，我认为是坏交易", "count": 16776, "confidence": "direct"}
{"category": "G", "project_line": "other", "locator": "W31/workbuddy-77a872c4:1789904679570", "quote": "180 次写入 → 只有 75 个唯一页面 —— 58.3% 是重复抓取。", "count": 180, "confidence": "direct"}
{"category": "C", "project_line": "golf/GB", "locator": "W22:1790067350", "quote": "每个域失败时会串行试 11 个 contact 路径 × 9 秒 = 最坏 99 秒/域。我停掉重写，加单域硬性时间预算。", "count": 99, "confidence": "direct"}
```
## 九、卡点时长（明确写出时长的）

只收原话里带明确时长的条目，单位保留原文（小时/分钟/天）。

```jsonl
{"category": "C", "project_line": "瑞士高尔夫-发信和监控", "locator": "work-W01.md:777", "quote": "检测到回复监控覆盖缺口：上一轮完整扫描为 08-24 07:27（北京时间），至本次恢复前间隔约 10 小时 49 分钟，超过设定的 3 小时周期。", "count": 649, "confidence": "direct"}
{"category": "C", "project_line": "de-municipal-sports-field", "locator": "work-W05.md / codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.part2 @2026-08-20T09:13:53Z", "quote": "你说得对：按现在“一户一户完整深研、逐页人工核验”的方式，剩余 160 个还需要约 25–40 小时，不合理。", "count": 160, "confidence": "direct"}
{"category": "C", "project_line": "de-municipal", "locator": "work-W48.md:826", "quote": "目前 <region> 市政队列共 422 个：已完成 262 个，剩余 160 个 SCREENED_UNRESOLVED。按现在“一户一户完整深研、逐页人工核验”的方式，剩余 160 个还需要约 25–40 小时。", "count": 160, "confidence": "confirmed"}
{"category": "C", "project_line": "global-outreach-ledger", "locator": "work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790133872198", "quote": "**代价**：白跑 14 小时，还不够 —— **它误触发了等待器 5 次**，把「脚本 bug」当成「接口故障」，去等恢复。", "count": 5, "confidence": "direct"}
{"category": "C", "project_line": "US|非高尔夫运动场母池", "locator": "work-W44.md:2405", "quote": "你的怀疑是对的 —— 名单确实停更了 22 小时", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "reply-monitor", "locator": "work-W48.md:362", "quote": "于是我的增量起点被顶到 10:02:48，窗口只剩 10:04→10:22，而日志里还写着「上次 08:14」—— 08:14→10:04 的 1 小时 48 分钟被静默跳过了。", "count": null, "confidence": "confirmed"}
{"category": "C", "project_line": "reply-monitor", "locator": "work-W48.md:795", "quote": "该地址早在 09-23 06:18Z 就写进了 04_NoGo抑制清单.csv L180，却仍在 09-23 15:18Z 被发信——发信晚于抑制 9 小时，客户被迫第二次要求停止。", "count": 6, "confidence": "confirmed"}
{"category": "C", "project_line": "us/pool", "locator": "W58:2026-09-03T04:00:00.000Z", "quote": "主机时钟在第二套运行期间从 9 月 3 日 04:xx 回拨到 9 月 2 日 20:xx（约 8 小时）", "count": 8, "confidence": "direct"}
{"category": "C", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1790166144912", "quote": "我 20:19 读到主文件是 90,823 字节，20:21 备份时已是 52,430 字节 —— 2 分钟内被别的线程重写过。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "golf/DE", "locator": "work-W27.md#1790083404808", "quote": "The jsonl hasn't been written in 27 minutes — genuinely stalled. Let me diagnose.", "count": 27, "confidence": "high"}
{"category": "C", "project_line": "golf/DE", "locator": "work-W27.md#1790083430697", "quote": "Confirmed hang — 27 min with no writes. Root cause: `urlopen(timeout=)` bounds only individual socket ops, not total request time, so a slow-dribbling server hangs a thread forever.", "count": 27, "confidence": "high"}
{"category": "C", "project_line": "golf/FR", "locator": "work-W27.md#1790230679936", "quote": "进程活着但 10 分钟零进展 —— 官方 API 限流了。我并行探测几个备用通道（含官方静态导出），不干等。", "count": 10, "confidence": "high"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:1859 [1790017279969]", "quote": "我直接写 `run_chunks.py --log xxx` 启动 30 小时任务，但**脚本当时还没这个参数** → 立即 exit 2，白等一轮。", "count": 30, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2089 [1790044385482]", "quote": "**凌晨 03:01 → 10:01 期间驱动器死了**：第 2 块 exit=1、第 3 块跑完但没记录，`chrome=0/python=0`，日志 0 字节。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "poland", "locator": "work-W32.md:127 (codex-01a00109/2026-08-17T04:48:11Z)", "quote": "自动任务显示 ACTIVE，但最后一份有效导入回执停在 8 月 15 日 22:25；今天已是 8 月 17 日。它是“已配置”，不是“持续产出”。", "count": 201, "confidence": "direct"}
{"category": "G", "project_line": "us-golf", "locator": "W67/codex-01a00f13:2026-09-08T23:17:39Z", "quote": "发现夜间在 23:17 后停止推进：进程仍在，但 8 小时未产生新的确认送达，当前 8,985 封", "count": 8985, "confidence": "direct"}
{"category": "J", "project_line": "UK-学校户外运动场划线-邮箱调研", "locator": "01a06a7e-d801-7c12-b790-4fbba7b628a7/2026-09-04T10:53:19", "quote": "| 实际有产出调研时长 | 5小时55分 |", "count": null, "confidence": "direct"}
{"category": "J", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789997892831", "quote": "4 段跑完 43 分钟，没有一段是瓶颈", "count": 43, "confidence": "direct"}
{"category": "C", "project_line": "uk-grass", "locator": "work-W12.md:68", "quote": "性能诊断已落盘：上一批 222 秒主要卡在逐页人工筛选；已改为三来源车道和向量化封装。UK-OFF-05-002 已交接：12 条原始、11 条去重候选，封装耗时 0.131 秒。", "count": 222, "confidence": "confirmed"}
{"category": "J", "project_line": "au-schools", "locator": "work-W39.md:L461@2026-08-27T09:10:40Z", "quote": "按当前速度，剩余邮件预计还需约6–8小时，包括首轮延后邮件的恢复重试", "count": 8, "confidence": "direct"}
{"category": "J", "project_line": "us-pl01-parking", "locator": "codex-01a047ff@2026-09-02T14:35:50Z", "quote": "PL01 停车场 | 8月31日 20:30（首个数据回执 22:47） | 9月2日 22:33 | 约 50小时", "count": 50, "confidence": "high"}
{"category": "J", "project_line": "FJD终端触达-监控", "locator": "work-W36.md:2034@1790173516933", "quote": "本轮实际覆盖：23:37:34Z → 01:02:28Z（约 1 小时 25 分）", "count": 805, "confidence": "direct"}
```
## 十（1）、停机原因：飞书 API 侧

同一类接口故障在多个业务线重复出现：502、限流码、草稿创建超时、无 handler。

```jsonl
{"category": "C", "project_line": "A", "locator": "work-W30.md:2811", "quote": "飞书 API 三次测试均 `Bad Gateway`（`mail +triage`、`im +chat-search` 全挂，间隔 15 分钟连测）。", "count": 3, "confidence": "high"}
{"category": "C", "project_line": "other", "locator": "work-W34.md#L3140", "quote": "**根因找到**：飞书 mail API 全线 **502 Bad Gateway** —— 是服务端临时故障（鉴权正常，`auth status` = ready）。刚才那次跑了 70s 全失败就是这个原因。", "count": 70, "confidence": "read"}
{"category": "C", "project_line": "FJD终端触达-监控", "locator": "work-W36.md:704@1790130508520", "quote": "首次运行时 37 个邮箱里 32 个返回 502（Bad Gateway），只有 5 个成功", "count": 32, "confidence": "direct"}
{"category": "C", "project_line": "A", "locator": "work-W30.md:3245", "quote": "**拿到 126/201 条正文**（失败的箱是 502 重试耗尽）。重跑失败的箱，并把新正文并入判定。", "count": 126, "confidence": "high"}
{"category": "C", "project_line": "美国高尔夫-发信和监控", "locator": "work-W73.md:3217", "quote": "的 Inbox/Spam 共 4 个文件夹均报 Feishu API `15180004 not support user type`，所以本轮只有 22/26 文件夹覆盖可证", "count": 4, "confidence": "direct"}
{"category": "C", "project_line": "us/pool", "locator": "W58:2026-09-07T10:00:00.000Z", "quote": "<email> 与 <email> 的 Inbox/Spam 连续返回 [15180004] not support user type，因此只有 22/26 覆盖", "count": 22, "confidence": "direct"}
{"category": "C", "project_line": "us/pool", "locator": "W58:2026-09-02T15:45:34.946Z", "quote": "发送接口返回飞书内部错误（1236019），目前不能视为已发送。我不会盲目重发", "count": 1236019, "confidence": "direct"}
{"category": "C", "project_line": "美国高尔夫-发信和监控", "locator": "work-W73.md:1957", "quote": "飞书在“创建草稿”阶段返回服务器超时；按防重复规则，这不能直接重试，因为草稿可能已在服务端落地。", "count": 1, "confidence": "direct"}
{"category": "C", "project_line": "golf/US", "locator": "work-W63.md#2026-08-24T12:53:12", "quote": "飞书在“创建草稿”阶段返回服务器超时；按防重复规则，这不能直接重试，因为草稿可能已在服务端落地。", "count": null, "confidence": "high"}
{"category": "C", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-08-20T10:10:25Z", "quote": "发送请求在“创建草稿”阶段超时，尚不能视为未发送；按防重复规则，我不会重试创建。", "count": 2, "confidence": "high"}
{"category": "K", "project_line": "美国高尔夫-发信和监控", "locator": "work-W73.md:2292", "quote": "自动化接口返回明确错误：`No handler registered for tool: codex_app.automation_update`。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "marketing-portal", "locator": "work-W48.md:2991", "quote": "飞书原表目前没有“编辑/新增工作表”权限（创建看板页被拒绝），而外部 Sites 域名又被当前网络拦截。", "count": null, "confidence": "confirmed"}
{"category": "C", "project_line": "英国", "locator": "work-W50.md:2147", "quote": "飞书服务连续三次连接失败；第41批已校验但尚未写入，未发生数据变更。", "count": 3, "confidence": "direct"}
```
## 十（2）、停机原因：网络、代理与 TCP 阻断

区分「目标站 403/WAF」和「本机网络层拒绝」——这两类在原话里被反复混为一谈。

```jsonl
{"category": "C", "project_line": "uk-football-pitch-outreach", "locator": "codex-01a06b86-51c1-7032-ade2-677c2d25c7a0 @2026-09-23T02:48:16Z", "quote": "UK：18/18 个 Inbox/Spam 目标均被 `open.feishu.cn:443` 的 TCP 网络限制拦截。", "count": 18, "confidence": "direct"}
{"category": "C", "project_line": "us", "locator": "work/codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187.md.part1#2026-09-22T09:44:37.678Z", "quote": "启动前的邮箱身份核验被本机网络权限拦截：Feishu Mail API 连接 `<ip>:443` 被系统拒绝（socket access forbidden）。为避免 4,599 条全部进入不确定重试状态，我没有盲目启动发送。", "count": 4599, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2681-2683 [1790077020217]", "quote": "① 走代理 → Bad Gateway ② 不走代理 → socket access permissions forbidden（沙箱拦截 socket）", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "global-golf-pool", "locator": "work-W38.md:L40@2026-09-20T18:22:36Z", "quote": "Found it — the env proxy (`127.0.0.1:56586`) is dead, but **direct network works**.", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "workbuddy-skills-migration", "locator": "W45/workbuddy-921a0643-40a1-44ce-a2c2-2b36df0e01a5.md:L289", "quote": "走沙箱代理 → 全部 502；绕过代理 → **全部 HTTP 200**。机器有网，是沙箱代理路由不通。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "golf", "locator": "W24/c8b43899@1790000704158", "quote": "环境代理会轮换端口…任何用裸 urlopen() 的代码都会静默走它 → 返回 502，看起来像目标站挂了，其实是我们自己的代理。我这轮踩了两次，两次得出相反结论。", "count": 2, "confidence": "direct"}
{"category": "C", "project_line": "golf", "locator": "W24/c8b43899@1790069116851", "quote": "OSM 在本机完全不可达（<org> 6 个镜像 + <org> 全部 WinError 10060 超时）", "count": 6, "confidence": "direct"}
{"category": "C", "project_line": "uk-natural-grass-mother-pool", "locator": "work-W15.md:3180", "quote": "本机对外直连被系统拒绝（socket 10013），因此 75 个官网字面地址均无法验证“可独立访问”；按失效关闭原则，`FEED-UK-008` 不会产生任何结构化 PASS。", "count": 75, "confidence": "direct"}
{"category": "K", "project_line": "uk-natural-grass-mother-pool", "locator": "work-W15.md:3187", "quote": "- 原因是本机公开网页直连均被 Windows socket 10013 拒绝，无法验证独立官方/受委托运营页面；<org> 草坪字段不能替代实体级责任证据。", "count": 400, "confidence": "direct"}
```
## 十（3）、停机原因：全局 Mail 读租约被别的 campaign 持有

这是最典型的「什么都没坏，但什么都不能做」：锁在别人手里，本地只能排队或记覆盖缺口。

```jsonl
{"category": "C", "project_line": "美国高尔夫-发信和监控", "locator": "work-W73.md:3439", "quote": "租约守卫正确 fail-closed，但当前实现是 60 秒无排队公平性的短租约，正在扫描的 Campaign 会连续占用读锁；Touch 1 因此全部返回 `retry_wait`。", "count": 8, "confidence": "direct"}
{"category": "C", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-09-08T23:49:59Z", "quote": "诊断确认是其他活动持有全局 Mail 读取租约，非权限或配置问题。", "count": null, "confidence": "high"}
{"category": "C", "project_line": "us/pool", "locator": "W58:2026-09-02T12:30:00.000Z", "quote": "本地证据确认当前租约由另一 Campaign 持有，本任务进程仍在等待，且尚未生成新的 raw 文件", "count": 0, "confidence": "direct"}
{"category": "C", "project_line": "golf/US", "locator": "work-W63.md#2026-09-08T23:49:01", "quote": "Touch 1 已按 fail-closed 退出：8/8 个 Inbox/Spam 都因全局读取租约不可用，未发生任何 Mail API 读取或外部写入；这轮是明确 coverage gap，不是“零回复”。", "count": 8, "confidence": "high"}
{"category": "C", "project_line": "australia-golf", "locator": "codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md@2026-09-08T23:59:48.935Z", "quote": "17 个文件夹页因全局读取租约超时失败，`<name>` Spam 尚未尝试", "count": 17, "confidence": "direct"}
{"category": "C", "project_line": "波兰高尔夫发信与监控", "locator": "codex-01a06069@2026-09-08T23:53:42", "quote": "全局读取锁持续被其他活动占用。本活动运行器仍保持排队，不会绕过该锁或并发读取", "count": null, "confidence": "high"}
{"category": "C", "project_line": "us", "locator": "work-W68.md:90 (codex-01a000c9/2026-09-09T00:19:51Z)", "quote": "在首次美国学校邮箱读取前，检测到 <name> 车道 <campaign> 正持有 Mail API 租约", "count": 0, "confidence": "direct"}
{"category": "C", "project_line": "us", "locator": "work-W68.md:179 (codex-01a000c9/2026-09-14T09:46:26Z)", "quote": "该 runner 缺少最新全局租约、分页和 contact-first 守卫。按门禁我不会扫描", "count": null, "confidence": "direct"}
```
## 十（4）、停机原因：文件锁与并发写覆盖

Windows 文件占用会让整个进程退出；多个 agent 全量重写同一文件会互相抹掉产出。

```jsonl
{"category": "C", "project_line": "波兰学校-发信和监控", "locator": "work-W37.md:589", "quote": "发送器并非邮件 API 失败，而是在读取某个已存在的邮箱锁文件时遇到 Windows `WinError 32`（文件暂被占用），该异常没有被降级为“该邮箱稍后重试”，导致整个进程退出。", "count": 1, "confidence": "direct"}
{"category": "C", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-08-20T11:40:35Z", "quote": "发送器已退出，原因是飞书共享邮箱写入锁冲突（`WinError 32`）；未将其误报为完成。", "count": null, "confidence": "high"}
{"category": "C", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-08-20T11:55:46Z", "quote": "发现第二个 Windows 锁路径：邮件发出后释放锁时偶发 `WinError 32`，仍会让进程退出。", "count": 2, "confidence": "high"}
{"category": "C", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-08-20T11:45:59Z", "quote": "波兰活动占用了 6 个相同的 <name> 发件箱锁，我没有强行抢占。德国活动现改用 3 个空闲邮箱", "count": 6, "confidence": "high"}
{"category": "C", "project_line": "other", "locator": "W31/workbuddy-77a872c4:1789905497138", "quote": "6 个 agent 会互相覆盖数据。flush() 是「全量重写」，两个 agent 各自读到 10 条、各写 5 条，后写的会把先写的整片抹掉。", "count": 6, "confidence": "direct"}
{"category": "C", "project_line": "瑞士高尔夫-发信和监控", "locator": "work-W01.md:1179", "quote": "我检测到运行期间该 Campaign 的 runner 文件被另一会话改写，当前运行内存中的分页版本仍在继续；我会把这作为“并发规则/文件变更”审计项。", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "global-outreach-ledger", "locator": "work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790051917400", "quote": "`wave1/wave2_send_list.csv` 在收编过程中**被其他线程多次改写**（行数 9,715 → 5,036）… 1,217 条里只有 512 条在名单中", "count": 705, "confidence": "direct"}
{"category": "C", "project_line": "other", "locator": "W31/workbuddy-77a872c4:1789711221549", "quote": "13:48–13:52 有另一进程并发部署了 16 个技能（从 .codex/skills 迁入）。这可能覆盖掉我之前的修复。", "count": 16, "confidence": "direct"}
```
## 十（5）、停机原因：预算与发送额度

预算是硬闸：402 之后无法再派 agent；每日收件人上限和 250 封读取上限会把整天挤掉。

```jsonl
{"category": "C", "project_line": "golf/GB", "locator": "W22:1790069601", "quote": "402 ExceededBudget: User=ou_<id> over budget. Spend=100.00514325600003, Budget=100.0", "count": 100.00514325600003, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2491 [1790069936652]", "quote": "碰到预算上限了（`402 ExceededBudget`）—— 无法再派 agent。我先**盘清当前状态、把已有产出全部结算**，再向你报告。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "spain-golf", "locator": "codex-01a05d03-05dd-7883-90cd-a9825fa834c7.md@2026-09-10T03:55:26.011Z", "quote": "总控已确认刷新回执，但当前 Codex 周期额度已满，暂不发放 live permit", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "us", "locator": "work/codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187.md.part1#2026-09-23T14:19:11.000Z", "quote": "四个新发件别名均被额度暂停；无争用。未启动重试，自动任务保留等待指示。", "count": 4, "confidence": "direct"}
{"category": "C", "project_line": "B", "locator": "work-W66.md:2795", "quote": "发送已开始，但飞书返回了每日外部收件人数上限错误（`1236008`）；已有部分邮件成功，另有地址进入 `retry_wait`。这类额度错误不应在同一邮箱反复尝试。", "count": null, "confidence": "high"}
{"category": "C", "project_line": "B", "locator": "work-W66.md:2813", "quote": "- 8 个邮箱明确触发日额度上限 `1236008`。", "count": 8, "confidence": "high"}
{"category": "C", "project_line": "A", "locator": "work-W30.md:451", "quote": "**更严重的是**：29 个邮箱撞了 250 上限，**退信把额度占满，UTC 09-20 全天被挤出去了**。我是通过\"每个邮箱拉到的日期下界都是 09-21\"才发现的 —— 补拉回来 **8,945 封**。", "count": 29, "confidence": "high"}
```
## 十（6）、停机原因：任务没启动，或静默死掉

最贵的一类故障：进程/任务显示 ACTIVE，实际没有产出，也没有报错。

```jsonl
{"category": "C", "project_line": "australia-golf", "locator": "codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md@2026-08-28T02:36:07.719Z", "quote": "没有结束，实际是**没有启动**。抱歉，05:30 定时任务未触发。", "count": 1008, "confidence": "direct"}
{"category": "C", "project_line": "australia-golf", "locator": "codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md@2026-08-28T02:43:04.826Z", "quote": "原 05:30 自动化已经被系统移除（一次性任务错过后不存在），所以无法直接更新。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "golf", "locator": "W24/c8b43899@1789997253921", "quote": "后台任务在上一轮结束时被系统回收了（非交互运行退出会终止子进程）。我改成带时间预算的前台分块跑，进度可续。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "美国-非高尔夫运动场-母池深度调研", "locator": "codex-01a0c3f4-33d3-7500-88eb-4bb9a8ef424b.md@2026-09-21T13:07:52.413Z", "quote": "网页镜像任务尚未写出任何执行文件，说明瓶颈现在不在数据准备，而在任务启动本身", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "US|非高尔夫运动场母池", "locator": "work-W44.md:2222", "quote": "**根因找到**：`log()` 只把日志存**内存列表 `L`**，`_finish()` 才一次性写文件 —— 而 `_finish` **只在正常退出时调用**。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "uk", "locator": "codex-01a06617-2143-7431-b70d-c43863864253.md.part3 @ 2026-09-04T01:48:41Z", "quote": "刚才的批处理没有产生任何输出文件，属于运行未启动成功而不是研究结论。我已确认路径和输入都在，正在把“建清单”和“抓取”拆开执行，避免静默失败。", "count": 0, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:1169 [1789992848172]", "quote": "确认：`_finish` 的 `print(txt)` 遇到 ✅ 在 GBK 控制台崩溃，导致脚本**非零退出**。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:1343 [1789992848172]", "quote": "842 个站点就这样\"失败\"了，还留下 116 个 Chrome 孤儿进程。", "count": 842, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2986 [1790128337971]", "quote": "**`pages.jsonl` 有一个损坏的字节（0xb8，位置 4776）** → `pages_cache` 每次加载都崩 → **所有写入静默失败** → 缓存冻结在 21,422 页。", "count": 21422, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2869 [1790125160892]", "quote": "`wmic.exe` **被安全策略加入黑名单** —— 而 `fanout_cdp.py` 内部会调 `wmic` 去枚举进程才算 GPU/进程状态。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2696 [1790077020217]", "quote": "我一直在错的层面找原因，换了 11 种调用方式（PowerShell 管道 / `Out-File` / `-u` / `.NET Process` / bash / `Start-Process`），每次都是 0 输出", "count": 11, "confidence": "direct"}
```
## 十（7）、停机原因：数据断链与静默丢行

行数、回执、退出码都正常，只有内容没了。

```jsonl
{"category": "C", "project_line": "cross-project-reply-monitoring", "locator": "work-W47.md#workbuddy-564708e9", "quote": "并发线程把 747 个 message_id 写进去重集合但未写累积池 → 主链路把它们全部静默跳过，只落 58 行", "count": 747, "confidence": "direct"}
{"category": "C", "project_line": "cross-project-reply-monitoring", "locator": "work-W47.md#workbuddy-564708e9", "quote": "同窗口内补扫 596 行、主链路只落 58 行，**漏 539 行**（含 11 条外部回复）", "count": 539, "confidence": "direct"}
{"category": "C", "project_line": "reply-monitor", "locator": "work-W48.md:2099", "quote": "本轮各返回恰好 250 行（= --max 上限），RC=0 无任何报错；分片重扫后唯一 id 分别 441 / 420 / 412 → 漏 722 个 id。", "count": 731, "confidence": "confirmed"}
{"category": "C", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L556@1790086580990", "quote": "**后果量化**：**183 个域被静默漏抓**，含 <org> 71 场（全池最大账户）、<org> 33场、<org> 24场、<org> 20场、<org> 21场 —— **合计 1,033 块场地**。", "count": 183, "confidence": "direct"}
{"category": "C", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L660@1790128404346", "quote": "R3 的 FINAL 列名已经是 `email_tier_final` / `email_source_final`（不是 `_r3`）。我在 s18 里读错了列名 → tier 全成 unknown、去重 key 冲突丢了 66 行。", "count": 66, "confidence": "direct"}
{"category": "C", "project_line": "golf", "locator": "work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789995936108", "quote": "断链丢掉的是 agent 产出的 61.5 倍。你感觉「产出很少」，因为你看到的就是 agent 那 18 个 —— 而它旁边还躺着 1,107 个没被写进去。", "count": 1107, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2329 [1790060738077]", "quote": "**找到 2 个真断链**（不只是 1 个）：`extractor7_result.json` 有非法控制字符、`extractor9_result.json` 有 UTF-8 BOM。**两个分片的产出（共 61 条）被静默丢弃**。", "count": 61, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:527 [1789976674097]", "quote": "**找到第一处真断链**：`cand_script_only.jsonl`（**680 条候选**）在设计里说要\"脚本直接落库\"，但我**实际没写这个落库步骤** —— 它们凭空消失了。", "count": 680, "confidence": "direct"}
{"category": "C", "project_line": "美国", "locator": "work-W33.md:2419 [1790062657990]", "quote": "**又一条断链**：`build_pipeline_briefs` 忽略了我传的 `--only-pages`，硬读旧的 `cand_for_agent.jsonl`（4,473 条），导致 `actual_n: 0`。", "count": 0, "confidence": "direct"}
```
## 十（8）、停机原因：门禁、合规与产能边界

这些不是故障，是设计上的「不许做」；但它们同样消耗了当天的时间。

```jsonl
{"category": "C", "project_line": "uk-pmpro-natural-grass", "locator": "work-W06.md#L1793", "quote": "Release Desk 已拒绝该 handoff 的跨 campaign 导入：`v3_dynamic` 与其 `v2` campaign/profile 不精确匹配。", "count": 3, "confidence": "direct"}
{"category": "C", "project_line": "UK-PaintMasterPro-天然草坪运动场-字面邮箱提取v2", "locator": "01a07533-f9fe-7d60-a130-e4990d9c6085/2026-09-06T07:48:14", "quote": "manifest SHA 已验证，但 6 个 released 本地镜像均缺失，无法保留证据链", "count": 6, "confidence": "direct"}
{"category": "C", "project_line": "UK-PaintMasterPro-天然草坪运动场-Wave1发信", "locator": "01a07534-29ff-7d72-986d-e7fae497362c/2026-09-06T13:48:44", "quote": "Release‑003 未发送：最终模板内容哈希门禁失败", "count": 7, "confidence": "direct"}
{"category": "C", "project_line": "UK-PaintMasterPro-官网入口发现-D01", "locator": "01a07ff8-2276-72e2-9649-70c6c0b843e2/2026-09-08T12:19:02", "quote": "The coordinator has not yet issued its L0022 acceptance receipt or next lease, so work is paused at the coordinator-only queue boundary", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "英国-PaintMaster Pro-天然草坪运动场-网页镜像下载 v2", "locator": "work-W19.md:L5270@2026-09-06T11:42:30.605Z", "quote": "CP03 被预执行验证拦截：<org> 账户含 7 页，超过每账户 6 页上限。已写 block receipt 并通知 URL-QA 重新封存合规拆分包；未下载任何页面。", "count": 7, "confidence": "direct"}
{"category": "C", "project_line": "uk-natural-grass-terminal-outbound", "locator": "work-W11.md#codex-01a07533", "quote": "Rolling-01 页预算已验收并消费：24 条（4 户 × 6），其中仅 4 条为既有镜像源页；其余 20 条是未授权空槽位，禁止 URL 猜测或外扩", "count": 20, "confidence": "direct"}
{"category": "C", "project_line": "poland", "locator": "work-W32.md:23 (codex-01a00109/2026-08-14T16:17:25Z)", "quote": "当前的实质性阻塞是合规：手册明确禁止向学校/场馆公开邮箱批量冷开发。", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "us-pl01-parking", "locator": "codex-01a05840-0cdd-7c21-8207-704a17e0f247.md@2026-09-02T03:33:27.107Z", "quote": "`campaign_registry.json` 仍为 `RESEARCH_RUNNING_NO_EXTERNAL_WRITES`，且 `live_external_writes_enabled=false`", "count": null, "confidence": "direct"}
{"category": "C", "project_line": "us/pool-builder-research", "locator": "work-W62.md codex-01a040fa 2026-08-27T10:13:32.447Z", "quote": "这次完整枚举已经超过合理成本，而且主扫描已证明「新增实见邮箱为 0」。我现在停止这个低收益深层枚举，保留已落盘的主扫描和 750 URL 清单", "count": 750, "confidence": "direct"}
{"category": "C", "project_line": "五州高校深调", "locator": "codex-01a00084@2026-08-18T11:20:27", "quote": "可发邮箱为 0。去重后 798 条严格联系人均为 `unapproved_research_only` / `research_only_do_not_send`", "count": 798, "confidence": "high"}
{"category": "C", "project_line": "uk-sports-field-paintmaster", "locator": "shards/work-W49.md:587", "quote": "当前第二批的结论就是：10 家通过场地资格，但有效邮箱为 0，说明这批按场地规模筛选的策略不合格，应立即停止扩展。", "count": 0, "confidence": "direct"}
{"category": "C", "project_line": "A", "locator": "work-W30.md:453", "quote": "**2. 退信洪水**：17,048 封里 **15,979 封（94%）是退信**。", "count": 15979, "confidence": "high"}
```
## 十一、工具与运行时的坑（形态与绕法）

工具链问题本身有固定形态：缺依赖、占位 Python、没有 bash/nohup、代理劫持、CLI 二进制被拦。

```jsonl
{"category": "K", "project_line": "瑞士高尔夫-发信和监控", "locator": "work-W01.md:864", "quote": "16:20Z 扫描存在1项详情读取失败（ENOBUFS），已报告覆盖缺口并启动50封/文件夹的恢复扫描。", "count": 50, "confidence": "direct"}
{"category": "K", "project_line": "波兰学校-发信和监控", "locator": "work-W37.md:3263", "quote": "第二次调用在写入前的正文读取阶段因 Windows GBK 解码失败而停止，也未改标；我会固定 CLI 输出为 UTF‑8 后再执行", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "global-outreach-ledger", "locator": "work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790080822512", "quote": "**连\"取 token\"的接口都 502。** 这不是某组接口的问题，是服务端多处异常 —— **没有可绕过的路径**。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "global-outreach-ledger", "locator": "work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790144636046", "quote": "`pip install pyarrow` 报 `502 Bad Gateway` —— 就是**你内存里记的那个坏代理**（pip 会读 `HTTP_PROXY`）。清掉代理变量后走官方源，13 秒装好。", "count": 13, "confidence": "direct"}
{"category": "K", "project_line": "global-outreach-ledger", "locator": "work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790076562147", "quote": "**512 个并发 worker 的 webmail 爬虫** —— 如果它也在打飞书接口，这就是 502 的真正来源。我不能杀别人的进程，但必须确认。", "count": 512, "confidence": "direct"}
{"category": "K", "project_line": "global-outreach-ledger", "locator": "work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790076967867", "quote": "接口故障时 `+messages` 会**静默返回 0 条**，不校验就会把\"接口失败\"误记成\"这箱没发信\"，造成**静默数据缺失**。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "uk-pmpro-natural-grass", "locator": "work-W06.md#L1687", "quote": "本机 PATH 中没有 Python（错误码 9009），所以刚才的“编译通过”记录不成立", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "uk-pmpro-natural-grass", "locator": "work-W42.md#L557", "quote": "硬截止实现遇到一个网站证书不匹配；我不会关闭证书校验。改为保留原有安全验证方式，但让每个公开页面请求在独立本地进程内执行，超过 7 秒直接终止并记录失败。", "count": 7, "confidence": "direct"}
{"category": "K", "project_line": "cross|终端触达四链路架构", "locator": "work-W44.md:13", "quote": "bash 工具链在本机不可用（已知问题），改走 Python 全路径。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "美国-高尔夫-协会会员体系与分会反查", "locator": "workbuddy-c5a3e76a-477a-444e-9f3f-cffad950268e.md@2026-09-20T04:09:47.402Z", "quote": "`agent-browser` 会把 PowerShell 会话直接拉死（命令 exit 1、日志截断），而且 headless 被 WAF 403", "count": 403, "confidence": "direct"}
{"category": "K", "project_line": "cross-project-skill-install", "locator": "work-W47.md#codex-01a0431b", "quote": "系统 `quick_validate.py` 依赖的 PyYAML 未安装，因此 validator 本身被环境依赖阻断，不是 skill 内容报错", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "英国", "locator": "work-W14.md:874", "quote": "本机当前 `python` 命令未解析（退出码 9009），我会使用环境内可用的 Python 运行时重新执行同一验证", "count": 9009, "confidence": "direct"}
{"category": "K", "project_line": "英国", "locator": "work-W50.md:2186", "quote": "邮箱分类与校验正常，第42批已完成资格评估（41 条候选），但飞书表格 API 出现间歇性网络连接失败，实际写入未发生。", "count": 41, "confidence": "direct"}
{"category": "K", "project_line": "fr-grass", "locator": "work/codex-01a0a94b-e9ec-7941-93c9-be4958fa24bb.md @2026-09-16T09:18:34Z", "quote": "Feishu synchronization was blocked because no 飞书 sheet connector is available", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "fr-sports-field-joint", "locator": "codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3 @2026-09-17T07:37:32Z", "quote": "19 条失败均是 `web_tool_error`，重试后以 unsafe URL 失败关闭", "count": 19, "confidence": "direct"}
{"category": "K", "project_line": "fr-sports-field-joint", "locator": "codex-01a0a94b-e413-72a1-b09f-7582f5a80242 @2026-09-17T10:16:55Z", "quote": "失败主因：渲染器安全拒绝与超时", "count": 1919, "confidence": "direct"}
{"category": "K", "project_line": "fr-grass", "locator": "codex-01a0a94b-f5ee-7311-9764-1fae54a743ea.md @ 2026-09-18T11:08:08Z", "quote": "刚才飞书 API 连接被本机代理拒绝，我正在切换到已打开的飞书表格界面完成追加并回读核验，不会把失败状态当成已同步。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "英国-PaintMaster Pro-天然草坪运动场-网页镜像下载 v2", "locator": "work-W19.md:L4556@2026-09-06T07:53:05.295Z", "quote": "Python 的网络层在若干站点上未能可靠地中断，因此未产生可用包。我将改用具有硬超时的单次公共 `curl` 请求实现；仍然是一 URL 一请求，超时会直接记失败，不会重试或扩大范围。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "英国-PMPro-官网入口发现 D03", "locator": "work-W55.md:L5113@2026-09-08T11:05:04.950Z", "quote": "直接访问预检发现两个页面连续两次失败（一个稳定返回 403、一个连续连接超时）。按规则我已将它们从候选集中降级为耗尽，不把搜索缓存当作可交付页面。", "count": 2, "confidence": "direct"}
{"category": "K", "project_line": "us/pool", "locator": "W58:2026-09-02T16:05:00.000Z", "quote": "61封查询缺口全部是飞书频率限制 99991400，不是投递失败", "count": 99991400, "confidence": "direct"}
{"category": "K", "project_line": "us", "locator": "work/codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187.md.part1#2026-09-22T10:30:47.044Z", "quote": "已确认问题在 飞书 CLI 的原生二进制连接层：同一地址的 Node/curl HTTPS POST 都可正常连通，只有当前 CLI 的邮件请求间歇性被系统拒绝。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "us", "locator": "work/codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187.md.part1#2026-09-22T10:31:55.873Z", "quote": "已找到可用修复路径：项目内置 CLI 二进制的连接被拦截，但本机全局安装的同版本 飞书 CLI 对同一 Feishu 邮箱请求正常。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "golf", "locator": "W24/c8b43899@1790000704158", "quote": "`tcp443 Timeout` = TCP 阻断；`443 通 + 403` = 站点 WAF；`443 通 + 200` = 其实是通的", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L264@1790076306012", "quote": "速率受**死站拖累**——urllib 的 timeout 不覆盖 DNS 解析阶段。加 **DNS 预过滤**（dnspython 快速剔除无 A 记录的域），这才是根因。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L700@1790128875829", "quote": "`__file__` 在 exec 环境不可用。用 `importlib` 加载更干净。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "fr-grass-mowing", "locator": "work-W25.md:L1080@1790145826225", "quote": "`nohup` 不可用。改用 4 个独立后台任务启动分片。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "golf/US", "locator": "work-W63.md#2026-08-24T15:38:41", "quote": "自动化接口返回明确错误：`No handler registered for tool: codex_app.automation_update`。", "count": null, "confidence": "high"}
{"category": "K", "project_line": "other", "locator": "W31/workbuddy-77a872c4:1789709056850", "quote": "依赖安装被沙箱代理挡住了（502）——这正是技能文档里记录过的坑。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "us-baseball", "locator": "W67/codex-01a00f13:2026-09-07T07:08:12Z", "quote": "两个发件箱的 Inbox/Spam 均被 Feishu API 拒绝（15180004 not support user type），三次有界重试后仍失败", "count": 2, "confidence": "direct"}
{"category": "K", "project_line": "FJD终端触达-监控", "locator": "work-W36.md:360@1790083017931", "quote": "原因找到了：`im +messages-search` 端点又 502 了（Bad Gateway），且 --max 参数不支持（我写错了）。群消息 API 现在整体不可用。", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "US-EG10-渠道调研", "locator": "work-W72.md:683@1790125880866", "quote": "浏览器已能读取页面，但其“导出内容”接口在当前运行时不可用；我会改用页面自身可见的公开 DOM 生成受审计的本地镜像文本", "count": null, "confidence": "direct"}
{"category": "K", "project_line": "uk-sports-field-paintmaster", "locator": "shards/work-W49.md:251", "quote": "官网邮箱抓取器在本机因缺少 `scrapling` 依赖而无法启动；我不会以猜测地址替代。", "count": null, "confidence": "direct"}
```
## 十二、数字冲突并列（不裁决）

同一指标在不同消息里给出不同数值，或同一件事用了不同分母。成组并列，不做取舍。已在前文出现的行只在此处按 locator 复述，不重复计入 finding 数。

- **波兰发信全程均值**：384.1 封/小时（`work-W37.md:543`）/ 271 封/小时（`work-W37.md:423`）/ 5.55 封/分钟≈333 封/小时（`work-W37.md:1722`）/ 近 5-10-15 分钟 2.60-2.10-1.87 封/分钟（`work-W37.md:767`）。前三条为全程平均口径、第四条为短窗口读数，数值互斥。
- **德国学校发信**：5.53 封/分钟（`codex-01a01d6b@2026-08-20T11:40:35Z`）/ 10.60 封/分钟（`codex-01a01d6b@2026-08-20T19:25:37Z`）/ 约 6.6 封/分钟（`codex-01a01d6b@2026-08-28T08:27:35Z`）。
- **美国高尔夫发信**：首封到末封平均 1,535 封/小时（`W67/codex-01a00f13:2026-09-08T03:05:15Z`）/ 全程平均 13.4 封/分钟（`W67/codex-01a00f13:2026-09-08T12:56:23Z`）。
- **德国市政剩余工作量**：剩余 160 户约 25–40 小时（`work-W05.md / codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.part2 @2026-08-20T09:13:53Z`）/ 全量 113,839 户单线程 956 小时（`work-W33.md:624`）/ Stage 2 ≈140 小时（`work-W33.md:816`）/ 4,554 批合计 3,567 小时（`codex-01a0c3f4-33d3-7500-88eb-4bb9a8ef424b.md@2026-09-21T12:34:14.680Z`）。分母不同：一条是剩余量，三条是全量或批次总量。
- **德国市政推进节奏**：50 个 wave 约 26 小时（`work-W69.md:1005-1010`）vs 4,554 批 3,567 小时（同上）。每个 wave 约 30 分钟与整批口径不可直接换算。
- **任务书 token 成本**：固定头 ≈46.8KB（11.7K tokens/片）（`work-W33.md:913`）vs 13.7K–30.6K tokens/片（`work-W33.md:850`）vs 拆碎后总 token +123%（`work/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md.part1#1789994811934`）。第一、三条是同一实验的两端，第二条是另一业务的实测区间。

以下 9 条为本文首次出现（其余冲突行见对应章节）：

```jsonl
{"category": "G", "project_line": "波兰学校-发信和监控", "locator": "work-W37.md:543", "quote": "全程平均：384.1 封/小时", "count": 384, "confidence": "direct"}
{"category": "G", "project_line": "波兰学校-发信和监控", "locator": "work-W37.md:423", "quote": "全程平均：271 封/小时", "count": 271, "confidence": "direct"}
{"category": "G", "project_line": "波兰学校-发信和监控", "locator": "work-W37.md:1722", "quote": "全程平均：5.55 封/分钟，约 333 封/小时", "count": 333, "confidence": "direct"}
{"category": "J", "project_line": "波兰学校-发信和监控", "locator": "work-W37.md:767", "quote": "近 5/10/15 分钟：2.60 / 2.10 / 1.87 封/分钟（156 / 126 / 112 封/小时）", "count": null, "confidence": "direct"}
{"category": "G", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-08-20T11:40:35Z", "quote": "最近 15 分钟发送 83 封，约 5.53 封/分钟；九发件箱均有实际发送，提速生效。", "count": null, "confidence": "high"}
{"category": "J", "project_line": "de-school-sports-line-marking", "locator": "codex-01a01d6b@2026-08-28T08:27:35Z", "quote": "平均发送速度：约 6.6 封/分钟", "count": null, "confidence": "high"}
{"category": "J", "project_line": "us-golf", "locator": "W67/codex-01a00f13:2026-09-08T03:05:15Z", "quote": "首封到末封约 7 小时 49 分，平均约 1,535 封/小时。", "count": 1535, "confidence": "direct"}
{"category": "J", "project_line": "us-golf", "locator": "W67/codex-01a00f13:2026-09-08T12:56:23Z", "quote": "当前已确认发送 928 / 12,217（7.6%），剩余 11,289 封；全程平均：13.4 封/分钟", "count": 928, "confidence": "direct"}
{"category": "G", "project_line": "美国", "locator": "work-W69.md:1005-1010", "quote": "R52 至 R105 共 50 个 wave 在约 26 小时内连续完成，平均每个 wave 约 30 分钟。", "count": 50, "confidence": "inferred"}
```
## 十三、覆盖与缺口

- **没有总账**：C/G/K/J 里没有全语料的 token/成本汇总。能读到的只有点值：单任务输入上下文 626,397 tokens（`work-W47.md#codex-01a00db7`）、每日固定成本约 65 分钟（`work-W41.md / workbuddy-18ad4fbb-2305-4356-804d-a79a92763487 @1790055553946`）。无法给出「整项调研花了多少 token/多少钱」。
- **口径不齐**：`project_line` 未归一（`golf`/`us`/`美国`/`other`/`fr-grass`/`英国`/`uk`/`de-municipal`/单字母 `A`/`B` 混用），本文保留原值，未做映射。
- **速率不可横比**：同一业务线同时存在瞬时（近 10/15 分钟）、全程平均、单箱速率三种口径；本文只并列，不换算。
- **md 合并稿不可作证据**：`final/lines/*.md` 的 774 条与 findings 非逐字一致；`golf.md`、`other.md` 为叙述体。若后续要引用合并稿里的独有事实，需回到原始语料复核。
- **缺口本身**：C 类里大量条目是「覆盖缺口」而非「失败」（租约未取得、分片未覆盖、API 返回空），这类不能读成「零回复/零结果」；原话已明确区分，本文按原话保留。
- **未收入**：D（对 agent 的不满）、E（口径纠正）、F（方法）、L（想要的自动化）属于其它分片；本文只在与速度/停机直接相关时引用其数字。
