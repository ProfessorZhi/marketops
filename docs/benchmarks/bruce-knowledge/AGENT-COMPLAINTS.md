# AGENT-COMPLAINTS — 对 agent 的不满（类型化，仅 D 类）

> 出处：本文由本机语料派生层装配（`ARTIFACTS_ROOT/marketops/bench/v0.6.2/bruce-knowledge/`），
> 该目录不随仓库分发。文中 `locator`（`voice/…`、`work-Wxx.md:行号`、`codex-<id>@<时间戳>`）
> 指向派生层文件，用于回查原话，仓库内无对应文件。语料本身（11 GB 会话、页面正文、第三方联系方式）不进 Git。

专题综合产物。输入为 `findings-by-line/*.jsonl` 的全部 finding，并以 `final/lines2/*.md` 的归并结果做交叉核对；不读语料原始 jsonl，不联网。

## v2 修订（2026-10-26）

本版只统计 D 类（对 agent 的不满）。E 类（Bruce 自己改规则）原被并进「口径反复变动」的 214 条里，其中 180 条其实是他自己下的新规则，把规则变更读成抱怨会同时高估这一类的规模和它的可操作性，因此 E 全部移出本文，改由 `CORRECTIONS.md` 承载。原 H 类「口径反复变动」在剔掉 E 后只剩 17 条 D，内容也不是他改规则，而是 agent 对「可发／有效／该排除」的判据出错或收得过紧，故改名为「H. 有效/可发判据失误与过度排除」，正文按这 17 条重写。覆盖列改为对照本工作树的 5 个研究侧 SKILL.md（`marketops-research`／`marketops-brief`／`marketops-sources`／`marketops-tools`／`marketops-benchmarks`），不再引用旧的 `marketops-plugin/skills/marketops/SKILL.md`。

## 口径

- 原始记录：4,022 条 finding。本文取 **D（对 agent 的不满）239 条**：其中 **223 条**归入 15 类抱怨，**16 条**属于需求、追问或运营指令，单列第 16 节，不计入抱怨。
- 去重参考：同一「引文+时间戳」只计一次（同一消息常被两个业务线分片各收一次；时间戳相差不足 1 分钟视为同一时间戳）。按此口径，全文为 **236 条唯一 D**（抱怨 220、非抱怨 16），按精确到秒为 237 条（抱怨 221、非抱怨 16）。**总表与各节条数不按去重数计**：一律是 jsonl 行数（223 + 16 = 239），因为 D 行要逐字保留、不能合并；去重数只用于说明同一句话被分片重复收录的规模。
- E 类不在本文：E（他自己改规则）有 **217 条原始 finding、210 行曾出现在本文**，现在只写在 `docs/benchmarks/bruce-knowledge/CORRECTIONS.md`，本文不再重复。L 类（他想要的自动化）有 **157 行曾出现在本文**，不计入抱怨，已归并到 `WISHLIST.md`。
- “次数”= 该类唯一 finding 数，不是独立事件数；同一次事故他连发三条消息就计三条。机器可读行见每节末尾的 jsonl。
- `category` 字段保留原分片的 `D:<类型名>` 标签；`project_line`、`locator`、`quote`、`count`、`confidence` 与原分片逐字一致。`quote` 已做脱敏并截到 ≤200 字。H 节的 `category` 仍是原标签 `D:口径反复变动`（用于回查），类名已按内容改过。
- 证据：每条发现至少一个 locator，可回派生层原文；写不出证据的条目没有收进来。

**重要覆盖说明：** 36 个 `work-*` 分片（agent 与工具段）中 D/E/L 均为 **0 条** —— 这三类按定义只存在于他的原话分片 `voice-*`，本文只统计其中的 D。work 分片里的 C（卡点）、G（编排）、K（工具）可佐证，但不在本专题口径内。因此第 17 节为 36 个空分片留了 `{"empty":true}` 记录。

## 总表

| 类型 | 次数（D） | 占抱怨 | 插件 0.6.4 覆盖 |
|---|---:|---:|---|
| A. 空转与自动停止 | 26 | 12% | 已有 |
| B. 必须人工点继续 | 2 | 1% | 已有 |
| C. 虚报完成与自评失真 | 7 | 3% | 已有 |
| D. 数字与清单对不上 | 16 | 7% | 已有 |
| E. 目标跑偏与范围走样 | 12 | 5% | 部分 |
| F. 越权改流程与新开线程 | 7 | 3% | 没有 |
| G. 重复发送与重复投递 | 5 | 2% | 不适用（外呼） |
| H. 有效/可发判据失误与过度排除 | 17 | 8% | 部分 |
| I. 只解释不执行、不认错 | 16 | 7% | 部分 |
| J. 效率与 ROI 不达标 | 42 | 19% | 部分 |
| K. 进展与状态不透明 | 15 | 7% | 部分 |
| L. 交付返工（排版/名片/素材） | 17 | 8% | 不适用（外呼） |
| M. 单点故障拖垮全批 | 15 | 7% | 部分 |
| N. 覆盖遗漏与数据不全 | 21 | 9% | 部分 |
| O. 误发与错配 | 5 | 2% | 不适用（外呼） |
| **A–O 合计（抱怨）** | **223** | **100%** | — |
| — 非抱怨记录（第 16 节，D 行原样保留） | 16 | — | 不适用 |
| **合计（本文全部 D 行）** | **239** | — | — |

表内自洽：26+2+7+16+12+7+5+17+16+42+15+17+15+21+5 = 223；223 + 16 = 239，等于全文档 D 行数。

覆盖判据：本工作树 `plugins/research-tools/marketops-research-assistant/skills/` 下的 5 个 SKILL.md —— `marketops-research`、`marketops-brief`、`marketops-sources`、`marketops-tools`、`marketops-benchmarks`。`已有`=该失败形态在某个 SKILL.md 里有明确指令或报告纪律，并在本节正文写出是哪个文件的哪一行；`部分`=有相邻能力但缺关键环节，同样写清缺什么；`没有`=5 个文件都未涉及；`待核`=描述不足以判定；`不适用（外呼）`=发信、退信、回复监控、投递去重、素材排版这类本插件不做的环节（本插件只做研究，不发信）。

---

## A. 空转与自动停止（26 条）

**典型原话（已脱敏）**

- “你没有在处理，96批已经停了很长时间了，96处理完应该自动处理后面的” — `voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-06T07:23:54.038Z]`（uk-quality）
- “你现在卡在哪里，已经没有产出了” — `voice/codex-01a0a94b-ed18-75c2-a9a6-d1efebd9fec0.md@2026-09-18T09:32:46.956Z`（fr-grass/法国天然草运动场）
- “你调研明确有问题，比如PL01线程，11：25是1,857 / 2,347  进展，现在12：21还是同样进展，压根就没动” — `codex-01a047ff#2026-09-02T04:21:24`（US-停车场划线-PL01）
- “解决产出一批就线程停止问题” — `voice/codex-01a066c5-1412-7392-bdde-7aa87a525134.md [2026-09-04T05:53:20.675Z]`（swiss-football）

**他对 agent 的明确要求：** 批与批之间不要等他；停之前先说停在哪、为什么停；恢复要自动，不要靠他发现图标变灰。

**插件现状（0.6.4）：已有。** `marketops-research/SKILL.md:9`：“Keep going until the job is done; do not stop to ask.”；`:52` 一次 `run(project, follow=True, shards=4)` 起管线，不要求批间批准；`:27-29` 五个池同时填、不等上一个池完成；`:61-63` 来源轮流，晚加入的来源不排在队尾。本节里发信与回复监控侧的 4 条属于外呼环节，不在这 5 个研究侧文件的范围。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 3 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:空转与自动停止", "project_line": "US-停车场划线-PL01", "locator": "codex-01a047ff#2026-08-31T15:45:25", "quote": "我看美国停车场已经调研已经停了，而且调研的数量特别少，这是不是一个 bug。", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "US-停车场划线-PL01", "locator": "codex-01a047ff#2026-09-02T04:21:24", "quote": "你调研明确有问题，比如PL01线程，11：25是1,857 / 2,347  进展，现在12：21还是同样进展，压根就没动", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "US-停车场划线-PL01", "locator": "codex-01a047ff#2026-09-02T04:24:25", "quote": "你能严格按照规则执行不犯错么 1、母池调研和针对母池清单深度调研同步，要高ROI的调研策略，不能自动停止", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "agent-platform", "locator": "voice/codex-01a00340-4eca-7a22-a2a7-6a08ad8a5687.md#2026-08-17T05:13:50", "quote": "我发现在其他线程的调研，每次都是调研到一个阶段就自己停了，比如我主动让他“继续”，甚至我说“持续调研，直到所有调研完成、中间不要停”也不行", "count": null, "confidence": "direct"}
{"category": "D:空转与自动停止", "project_line": "agent-platform", "locator": "voice/codex-01a00340-4eca-7a22-a2a7-6a08ad8a5687.md#2026-08-18T08:42:31", "quote": "我看你没有启动01a00084-2f1a-7080-8801-f10704f849a7的持续调研任务，他自动停了也没让他继续", "count": null, "confidence": "direct"}
{"category": "D:空转与自动停止", "project_line": "agent-platform", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md#2026-09-07T14:22:44", "quote": "卡点还是太多了，你要想办法先把整个流程跑通了。不必要的审核就全部删掉。", "count": null, "confidence": "direct"}
{"category": "D:空转与自动停止", "project_line": "cross-project", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md [2026-09-07T14:21:14.293Z]", "quote": "卡点还是太多了，你要想办法先把整个流程跑通了。", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "cross-project", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md [2026-09-07T15:05:12.816Z]", "quote": "这个我觉得是不是去评估质量的这种流程太多了，都卡在这里。", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T09:57:00.516Z]", "quote": "现在一直卡在网页镜像失败上面，我觉的应该合并成一个，就是直接根据母池，自己镜像网页，然后邮箱提取，邮箱质量，流程多的效率低，问题多", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "fr-grass/法国天然草运动场", "locator": "voice/codex-01a0a94b-ed18-75c2-a9a6-d1efebd9fec0.md@2026-09-18T09:32:46.956Z", "quote": "你现在卡在哪里，已经没有产出了", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a06c69-b003-72f3-85db-1a6f035ded65.md@2026-09-05T03:58:06.167Z", "quote": "为什么又没有自动评估好的网页，直接给到下一个线程去提取邮箱呢？为什么一直没有自动。", "count": 8, "confidence": "medium"}
{"category": "D:空转与自动停止", "project_line": "golf", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1789872507134", "quote": "官网有读取完了么？怎么停止了", "count": null, "confidence": "direct"}
{"category": "D:空转与自动停止", "project_line": "problem-handling", "locator": "voice/codex-01a00074-d1e9-7620-bbe2-0f736e2de85c.md [2026-08-14T14:04:47.468Z]", "quote": "恢复刚才的3个线程，你要看下为何每次codex重启后，线程就消失了", "count": 3, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "swiss-football", "locator": "voice/codex-01a066c5-1412-7392-bdde-7aa87a525134.md [2026-09-04T05:53:20.675Z]", "quote": "解决产出一批就线程停止问题", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "uk-quality", "locator": "voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-05T07:39:40.115Z]", "quote": "你这里是不是卡住了?", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "uk-quality", "locator": "voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-06T07:23:54.038Z]", "quote": "你没有在处理，96批已经停了很长时间了，96处理完应该自动处理后面的", "count": 96, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "uk-schools", "locator": "voice/codex-01a06617-2143-7431-b70d-c43863864253.md [2026-09-03T14:45:12.200Z]", "quote": "状态又是停止的，不用停止，直到所有调用结束。", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "us", "locator": "codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-08T23:16:55.616Z", "quote": "现在都早上7点了", "count": null, "confidence": "inferred"}
{"category": "D:空转与自动停止", "project_line": "us", "locator": "codex-01a0a386-dffc-73c3-ae77-18613d44280d@2026-09-15T09:34:31.049Z", "quote": "所以还未产生第一批50个？", "count": 50, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "us-k12", "locator": "voice/codex-01a00083-555a-7150-b181-c596d9a7d1c7.md [2026-08-14T15:01:46.774Z]", "quote": "你是不是又停了,怎么一直在停啊?你要一直调研到所有五个州的学校都结束啊?", "count": 7, "confidence": "inferred"}
{"category": "D:空转与自动停止", "project_line": "us-nongolf-fields", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1790221160731", "quote": "你昨天的数据就是下面的，跑一晚上没有新增，我怀疑你停止了", "count": null, "confidence": "direct"}
{"category": "D:空转与自动停止", "project_line": "us-un-schools", "locator": "voice/codex-01a00084-2f1a-7080-8801-f10704f849a7.md [2026-08-14T15:21:41.285Z]", "quote": "图标又变成停止状态了,你需要一直在运行啊。", "count": 4, "confidence": "inferred"}
{"category": "D:空转与自动停止", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-20T11:44:45.178Z", "quote": "没在发邮件了？", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-20T11:48:33.965Z", "quote": "想办法重新启动发信任务", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1790227605044", "quote": "你停止了？", "count": null, "confidence": "high"}
{"category": "D:空转与自动停止", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-17T12:12:31.025Z", "quote": "怎么没有开始发送？已经过了20：10了", "count": null, "confidence": "high"}
```

---

## B. 必须人工点继续（2 条）

**典型原话（已脱敏）**

- “请继续执行任务” — `voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790062840000`（golf）
- “我看四国调研没有开始，需要我给什么提示词给他们” — `voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790141718358`（golf）

**他对 agent 的明确要求：** 不要让他每次发一句“继续”才动；一批结束就自动下一批，直到明确的全量目标完成。

**插件现状（0.6.4）：已有。** `marketops-research/SKILL.md:9`（同上：“do not stop to ask”）；`:46` 只允许开头问一次缺什么（“ask at most once for what is missing”）；`:52` 管线按批推进，没有批间人工批准这一步。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 23 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:必须人工点继续", "project_line": "golf", "locator": "voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790062840000", "quote": "请继续执行任务", "count": 4, "confidence": "direct"}
{"category": "D:必须人工点继续", "project_line": "golf", "locator": "voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790141718358", "quote": "我看四国调研没有开始，需要我给什么提示词给他们", "count": null, "confidence": "direct"}
```

---

## C. 虚报完成与自评失真（7 条）

**典型原话（已脱敏）**

- “你整个线程流程都没跑起来” — `voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-16T10:33:27.776Z]`（fr-grass）
- “你实际产出就基本没有” — `voice/codex-01a079ae-bd7a-70e3-98a5-a601762b0eff.md@2026-09-08T04:11:37.202Z`（fr-grass/英国 <org>）
- “你现在还有什么作用，我已经自己对每个线程做调整了” — `voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T12:35:33.152Z]`（fr-grass）

**他对 agent 的明确要求：** 没跑通、没产出、没验证过的事不要报“已完成”；未完成要显式标缺口。

**插件现状（0.6.4）：已有。** `marketops-research/SKILL.md:89-90`：“Green but empty: status fields said done while nothing was on disk. Judge by what is on disk: contacts, pages, the workbook - never by a ‘completed’ line.”；`:112` 要求每个机构以联系人或“找不到的原因”收尾。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 1 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:虚报完成与自评失真", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-16T10:33:27.776Z]", "quote": "你整个线程流程都没跑起来", "count": null, "confidence": "high"}
{"category": "D:虚报完成与自评失真", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T12:35:33.152Z]", "quote": "你现在还有什么作用，我已经自己对每个线程做调整了", "count": null, "confidence": "high"}
{"category": "D:虚报完成与自评失真", "project_line": "fr-grass/法国天然草运动场", "locator": "voice/codex-01a0a94b-e0c4-7640-af33-8634cd995482.md@2026-09-17T07:54:59.821Z", "quote": "你这个线程没用了吧，有一个网页提取线程，我已经和他调整了很多策略", "count": null, "confidence": "high"}
{"category": "D:虚报完成与自评失真", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a079ae-bd7a-70e3-98a5-a601762b0eff.md@2026-09-08T04:11:37.202Z", "quote": "你实际产出就基本没有", "count": null, "confidence": "high"}
{"category": "D:虚报完成与自评失真", "project_line": "swiss-golf", "locator": "voice/codex-01a022b1-6f56-7e83-8d92-041d3f258e8b.md [2026-08-24T02:52:00.043Z]", "quote": "是问题没解决？", "count": null, "confidence": "high"}
{"category": "D:虚报完成与自评失真", "project_line": "uk-send", "locator": "voice/codex-01a06b86-51c1-7032-ade2-677c2d25c7a0.md [2026-09-08T23:11:10.221Z]", "quote": "昨天是不是发错了？", "count": null, "confidence": "high"}
{"category": "D:虚报完成与自评失真", "project_line": "us-nongolf-fields", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1789990536008", "quote": "讨论：我发现你一直没有真正跑起来，现在调研效率太低了", "count": null, "confidence": "direct"}
```

---

## D. 数字与清单对不上（16 条）

**典型原话（已脱敏）**

- “我看有2000多个邮箱啊，怎么才313” — `voice/codex-01a038e5-097b-74d1-8bf6-ffc4f326d1d7.md [2026-08-27T10:36:14.193Z]`（us-pool-builder）
- “不是有9000多邮箱么，怎么最后只有3000多个了” — `voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T04:27:51.537Z`（波兰学校）
- “65+438+2586+3062也不是3000多个啊” — `voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T04:31:10.682Z`（波兰学校）
- “数据有问题：1、上面1说终端已加旗明确的有559，已加旗的默认就是有效回复了 2、你下面2又说终端且有效回复是572，未加旗还有236” — `workbuddy-e4bdcdff#1789959428007`（有效回复监控）

**他对 agent 的明确要求：** 他给的清单、原始条数、去重前后、已发/未发必须能对上；对不上就回原文核，不要给一个自洽但错的数。

**插件现状（0.6.4）：已有。** `marketops-research/SKILL.md:91-93` 把“静默丢行”列为已知失败并给动作：“when the pool is large and the output tiny, find the stage whose output was not merged.”；`marketops-benchmarks/SKILL.md:49-51`：“Count records, not lines”，并给出一次 12,927 声称、1,874 实际的例子。

```jsonl
{"category": "D:数字与清单对不上", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a07534-0eef-77a1-b2ff-1b9e1a83322e.md@2026-09-08T11:22:14.486Z", "quote": "什么原因，调研了16000多个，去重后只有2300多个", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a07534-2091-7e12-94eb-311fd519a140.md@2026-09-06T10:48:49.977Z", "quote": "怎么只有1个发送出去？不是11个么", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "golf", "locator": "codex-01a013fd-d363-7e13-a157-306a09f06647@2026-08-18T11:02:53.451Z", "quote": "你再看下，我仔细看了表格，没有什么重复邮箱", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "golf", "locator": "codex-01a013fd-d363-7e13-a157-306a09f06647@2026-08-18T11:04:45.851Z", "quote": "不对吧，<email>我人工看只有2个怎么20次了", "count": 20, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "us", "locator": "codex-01a000c9-93de-7270-8d91-dfc9fd5b0668@2026-09-17T15:29:06.446Z", "quote": "美国足球应该不是六百六千八百一十八个，应该是八千多个，因为历史得有六七千八百一十八个，我还发了两千多个。", "count": 6818, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "us", "locator": "codex-01a0a9c7-b536-70a1-a311-dd27aadde9ec@2026-09-16T13:03:38.290Z", "quote": "应该是这2个，提供错了", "count": 2, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "us-pool-builder", "locator": "voice/codex-01a038e5-097b-74d1-8bf6-ffc4f326d1d7.md [2026-08-25T14:25:58.959Z]", "quote": "公司名称可以，但是后面的地址是哪来的", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "us-pool-builder", "locator": "voice/codex-01a038e5-097b-74d1-8bf6-ffc4f326d1d7.md [2026-08-27T10:36:14.193Z]", "quote": "我看有2000多个邮箱啊，怎么才313", "count": 313, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1789959428007", "quote": "数据有问题：1、上面1说终端已加旗明确的有559，已加旗的默认就是有效回复了 2、你下面2又说终端且有效回复是572，未加旗还有236", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1789976456774", "quote": "我知道的有加旗和推卡的，推过飞书群的就已经有300以上了，怎么可能只有100多？你那个加旗但未推过卡的那个数据感觉有问题，会照成重复发飞书群", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1789994085774", "quote": "你再查一下那个285个，我们现在所有的待评审、复核、有异议的都已经处理完了，为什么还有285条？你的数据是不是错了？", "count": 285, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1789996292258", "quote": "除了我，还有其他人也会通过他的飞书推，你就只针对飞书邮箱的收件箱，根据终端判断哪些已经加旗和发群，我看你的883都是错的", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1790074612732", "quote": "我感觉是现在越来越乱了，然后我也不知道你哪个数据是真的，哪个数据是假的。", "count": null, "confidence": "high"}
{"category": "D:数字与清单对不上", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T04:27:51.537Z", "quote": "不是有9000多邮箱么，怎么最后只有3000多个了", "count": null, "confidence": "direct"}
{"category": "D:数字与清单对不上", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T04:31:10.682Z", "quote": "65+438+2586+3062也不是3000多个啊", "count": null, "confidence": "direct"}
{"category": "D:数字与清单对不上", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T08:49:16.070Z", "quote": "708 个确认无效或缺少 SMTP 记录你怎么得出的", "count": null, "confidence": "direct"}
```

---

## E. 目标跑偏与范围走样（12 条）

**典型原话（已脱敏）**

- “你这里不是德国的调研么，怎么串别的国家了？” — `voice/workbuddy-7aeb1541-a888-4313-8d18-910f724c8723.md@1790165728984`（户外运动场-法国）
- “你是在已有的学校母池里面看？不是去网页自己批量下载？” — `voice/codex-01a0ae65-0fb1-77c1-a2ca-1f4cd681916a.md@2026-09-17T13:45:08.397Z`（us/美国非高尔夫运动场）
- “我发现德国的调研线程，自动跑了其他几个国家调研” — `voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790165908460`（golf）
- “你这轮最早就是根据德国母池做的深度调研，只需要做德国的，其他国家和你没关系，输出邮箱清单结果也只有德国的” — `voice/workbuddy-7aeb1541-a888-4313-8d18-910f724c8723.md@1790220609296`（户外运动场-法国）

**他对 agent 的明确要求：** 严格按他指定的母池、国家、场景与来源页干活；要换来源先说明，不许拿现成母池充当“网页批量下载”。

**插件现状（0.6.4）：部分。** `marketops-brief/SKILL.md:37` 把“一个国家、一个应用场景”写进 brief（割草与划线分开跑），`:39-40` 区分买家用产品的客户与卖同类产品的竞争者，范围是被钉住的。缺的关键环节在来源侧：`marketops-sources/SKILL.md:27-29` 反而先让向客户要现成清单，5 个文件里没有一句要求“现有母池属于另一个场景时必须从网页重建”。

```jsonl
{"category": "D:目标跑偏与范围走样", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-16T09:23:53.402Z]", "quote": "为何要分割草和划线2个任务？我是一起推的，发同一封开发信", "count": 2, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "golf", "locator": "voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790165908460", "quote": "我发现德国的调研线程，自动跑了其他几个国家调研", "count": null, "confidence": "direct"}
{"category": "D:目标跑偏与范围走样", "project_line": "golf", "locator": "voice/workbuddy-23da98e1-8cae-4124-ad81-39b8e4e5f275.md#1790062333660", "quote": "BE · NO · FI 的域名都不对或不可达 → 重新找正确域名，", "count": null, "confidence": "direct"}
{"category": "D:目标跑偏与范围走样", "project_line": "us", "locator": "codex-01a0a364-d36a-76a1-a5a6-f0ecc5c86e66@2026-09-15T05:10:18.668Z", "quote": "你直接调研了？", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "us", "locator": "codex-01a0a364-d36a-76a1-a5a6-f0ecc5c86e66@2026-09-15T05:12:02.632Z", "quote": "不是讨论好的分几个流程模块么？我也没看到你新建新线程", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "us/美国非高尔夫运动场", "locator": "voice/codex-01a0ae65-0fb1-77c1-a2ca-1f4cd681916a.md@2026-09-17T12:46:19.864Z", "quote": "你为何不能自己下载", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "us/美国非高尔夫运动场", "locator": "voice/codex-01a0ae65-0fb1-77c1-a2ca-1f4cd681916a.md@2026-09-17T13:21:11.049Z", "quote": "怎么都是学校资源母池，美国运动场不是都学校吧", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "us/美国非高尔夫运动场", "locator": "voice/codex-01a0ae65-0fb1-77c1-a2ca-1f4cd681916a.md@2026-09-17T13:45:08.397Z", "quote": "你是在已有的学校母池里面看？不是去网页自己批量下载？", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "户外运动场-全球母池", "locator": "voice/workbuddy-c8b43899-114a-47ef-9148-5b411256b2a2.md@1789995706901", "quote": "找到正确网站批量下载，现在明确各个国家母池的数量太少了", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "户外运动场-法国", "locator": "voice/workbuddy-7aeb1541-a888-4313-8d18-910f724c8723.md@1790165728984", "quote": "你这里不是德国的调研么，怎么串别的国家了？", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "户外运动场-法国", "locator": "voice/workbuddy-7aeb1541-a888-4313-8d18-910f724c8723.md@1790220609296", "quote": "你这轮最早就是根据德国母池做的深度调研，只需要做德国的，其他国家和你没关系，输出邮箱清单结果也只有德国的", "count": null, "confidence": "high"}
{"category": "D:目标跑偏与范围走样", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1789996497464", "quote": "你跑偏了，我的意思是你就关注<name>和<name>的邮箱回复。已经判断好了代理和终端，已经全部判断好了…为什么现在还有那么多问题？", "count": null, "confidence": "high"}
```

---

## F. 越权改流程与新开线程（7 条）

**典型原话（已脱敏）**

- “不能解决旧的网页质量线程问题么？不要动不动新开线程” — `voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T03:51:29.341Z]`（fr-grass）
- “你不需要命令，我再每个线程自己调试，现在有2个网页提取线程，乱套了” — `voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T04:27:58.756Z]`（fr-grass）
- “你不是从网页质量的线程，应该是从邮箱抽取的线程，你不要跨线程。” — `voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-05T07:46:05.874Z]`（uk-quality）
- “不要加太多线程，明确功能线程达到目的就行，线程越多越乱” — `voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T02:57:08.487Z]`（fr-grass）

**他对 agent 的明确要求：** 只改被点到的那条线；不要擅自新开、合并、拆分线程，也不要跨到别人正在调试的线程里动手。

**插件现状（0.6.4）：没有。** 5 个 SKILL.md 都没有线程归属、不得新开或合并线程的指令；`marketops-research/SKILL.md:65-67` 反而要求起 3-6 个 `marketops-worker` 子代理并按要求派活。线程边界在这个插件里不是一个被表达的约束。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 11 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:越权改流程与新开线程", "project_line": "agent-platform", "locator": "voice/codex-01a0bcfd-e352-7161-839a-8bef8a5be0f9.md#2026-09-20T04:50:42", "quote": "之前尝试过一个调研分几个线程，分开调研。线程做速度就特别慢。容易出非常多bug", "count": null, "confidence": "direct"}
{"category": "D:越权改流程与新开线程", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T02:57:08.487Z]", "quote": "不要加太多线程，明确功能线程达到目的就行，线程越多越乱", "count": null, "confidence": "high"}
{"category": "D:越权改流程与新开线程", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T03:51:29.341Z]", "quote": "不能解决旧的网页质量线程问题么？不要动不动新开线程", "count": null, "confidence": "high"}
{"category": "D:越权改流程与新开线程", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-17T04:27:58.756Z]", "quote": "你不需要命令，我再每个线程自己调试，现在有2个网页提取线程，乱套了", "count": 2, "confidence": "high"}
{"category": "D:越权改流程与新开线程", "project_line": "fr-grass/法国天然草运动场", "locator": "voice/codex-01a0a94b-f001-7963-ae4e-bf2199bee793.md@2026-09-17T04:59:53.958Z", "quote": "你和邮箱质量线程功能是不是重复了？而且到你这基本没有过关的邮箱", "count": null, "confidence": "high"}
{"category": "D:越权改流程与新开线程", "project_line": "uk-quality", "locator": "voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-05T07:46:05.874Z]", "quote": "你不是从网页质量的线程，应该是从邮箱抽取的线程，你不要跨线程。", "count": null, "confidence": "high"}
{"category": "D:越权改流程与新开线程", "project_line": "跨项目/多线程编排", "locator": "codex-01a047ff#2026-09-01T05:34:08", "quote": "现在怎么你在跑，另一个线程也在跑，这个是合理么？", "count": null, "confidence": "high"}
```

---

## G. 重复发送与重复投递（5 条）

**典型原话（已脱敏）**

- “监测到的已经是历史加旗的，就说明已经处理过的，不能重复发飞书群” — `voice/codex-01a0ae9c-3c8a-7642-ad99-b2644a0985ea.md@2026-09-17T14:51:37.496Z`（us/美国运动场发信与监控）
- “附件明确是有效回复且已经发群的，不需要再发给我确认，我看还发了2遍” — `codex-01a013fd-d363-7e13-a157-306a09f06647@2026-08-19T10:44:00.185Z`（golf）
- “现在可以看到了,但是我手机上显示这三个星层都有,每个都有两个重复的。” — `voice/codex-01a00074-d1e9-7620-bbe2-0f736e2de85c.md [2026-08-14T15:07:05.505Z]`（problem-handling）

**他对 agent 的明确要求：** 发群、发信、扫描前先查历史；同一条回复只发一次，同一批邮箱只触达一次；状态变化要第一时间同步给发信线程。

**插件现状（0.6.4）：不适用（外呼）。** `marketops-research/SKILL.md:111`：“Research only: nothing is sent to anyone.” 重复发群、重复发信、跨线程触达去重都属于发信与投递环节，这 5 个文件不做。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 13 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:重复发送与重复投递", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a079ae-bd7a-70e3-98a5-a601762b0eff.md@2026-09-08T10:01:08.437Z", "quote": "还是持续产生重复的", "count": 3, "confidence": "medium"}
{"category": "D:重复发送与重复投递", "project_line": "golf", "locator": "codex-01a013fd-d363-7e13-a157-306a09f06647@2026-08-19T10:44:00.185Z", "quote": "附件明确是有效回复且已经发群的，不需要再发给我确认，我看还发了2遍", "count": 2, "confidence": "high"}
{"category": "D:重复发送与重复投递", "project_line": "problem-handling", "locator": "voice/codex-01a00074-d1e9-7620-bbe2-0f736e2de85c.md [2026-08-14T15:07:05.505Z]", "quote": "现在可以看到了,但是我手机上显示这三个星层都有,每个都有两个重复的。", "count": 2, "confidence": "high"}
{"category": "D:重复发送与重复投递", "project_line": "us", "locator": "codex-01a085d3-2c84-73c2-8a1f-6d20544f0cf5@2026-09-16T06:20:21.517Z", "quote": "现在是不是发信和邮件监控都没问题了，也不会重复发有效回复到飞书群", "count": null, "confidence": "high"}
{"category": "D:重复发送与重复投递", "project_line": "us/美国运动场发信与监控", "locator": "voice/codex-01a0ae9c-3c8a-7642-ad99-b2644a0985ea.md@2026-09-17T14:51:37.496Z", "quote": "监测到的已经是历史加旗的，就说明已经处理过的，不能重复发飞书群", "count": null, "confidence": "high"}
```

---

## H. 有效/可发判据失误与过度排除（17 条）

**典型原话（已脱敏）**

- “每一关口把得太严的话，它的效率就很差，可能很长时间都拿不到多少邮箱” — `voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-16T13:18:49.539Z]`（fr-grass）
- “我发现你有大量邮箱排除” — `voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-05T01:57:36.699Z]`（uk-quality）
- “排除的382是什么” — `voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-04T14:23:16.733Z]`（uk-quality）
- “然后就是无法判断是有效的话，你给我看。因为昨天发现很多你判断是有效的，实际上是无效的” — `workbuddy-e4bdcdff#1790046671775`（有效回复监控）

**他对 agent 的明确要求：** 什么算有效回复、什么算可发、什么该排除，要按他给的定义判；判不准就拿来给他看，不要用一道过严的关口把成批真实邮箱挡在外面，也不要把他判过的回复再翻成无效。

本节 jsonl 的 `category` 保留原分片标签 `D:口径反复变动`，便于回查；类名按实际内容改为「有效/可发判据失误与过度排除」。

**插件现状（0.6.4）：部分。** 研究侧有明确规则：`marketops-brief/SKILL.md:59-60` 把 A+B 定为指标、C/D 定为资产、X 定为浪费并分开计数；`:68-69` 禁止整域排除公共邮箱（“Public mail domains are never excluded wholesale - judge the local part”，一次整域过滤删掉 4,586 行而其中只有 12 行是错的）；`:85-89` 把口径更正当作新事实 —— 改 brief、`grade(regrade=True)`、`learn`；`marketops-sources/SKILL.md:67-69` 要求“do not over-gate”（一次把 94 个真实公共地址压进人工复核）。缺的关键环节在回复侧：什么算“有效回复”、什么算“二次开发信可发”属于外呼判定，这 5 个文件不涉及。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 17 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:口径反复变动", "project_line": "fjd-outreach", "locator": "voice/codex-01a000c9-4fd8-75b2-859d-c273a067a68d.md@2026-08-14T14:59:48.925Z", "quote": "A confirmed prior send is always `skipped_already_sent`; never duplicate it.", "count": 4, "confidence": "inferred"}
{"category": "D:口径反复变动", "project_line": "fjd-outreach", "locator": "voice/codex-01a000c9-4fd8-75b2-859d-c273a067a68d.md@2026-08-14T14:59:48.925Z", "quote": "A missing or inaccessible historical log is a reconciliation hold, never proof that the address was unsent.", "count": 4, "confidence": "inferred"}
{"category": "D:口径反复变动", "project_line": "fjd-outreach-terminal", "locator": "voice/codex-01a07b57-45b9-7a13-ace1-31462c37bb6b.md@2026-09-07T10:59:42Z", "quote": "不对啊，这样不是算有效回复么", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "fr-grass", "locator": "voice/codex-01a0a8a3-a7e5-7842-ac80-27adcd9be4a3.md [2026-09-16T13:18:49.539Z]", "quote": "每一关口把得太严的话，它的效率就很差，可能很长时间都拿不到多少邮箱", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a07534-2091-7e12-94eb-311fd519a140.md@2026-09-06T13:42:39.889Z", "quote": "2 条上游保护暂停、7 条候选暂不放行  ---为什么不发？调研出来的都可发", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "golf", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1789873122489", "quote": "给你官网主要让你更好的判断角色，做好深度调研，我需要更多更高质量的关键角色邮箱用于发开发信，你这个技能和发开发信无关", "count": null, "confidence": "direct"}
{"category": "D:口径反复变动", "project_line": "uk-quality", "locator": "voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-04T14:23:16.733Z]", "quote": "排除的382是什么", "count": 382, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "uk-quality", "locator": "voice/codex-01a06c45-8b3e-74f2-9997-99b814f1beec.md [2026-09-05T01:57:36.699Z]", "quote": "我发现你有大量邮箱排除", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "us", "locator": "codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-08T02:38:18.380Z", "quote": "看下这个，默认是按二次开发信可发，去重看下", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "us", "locator": "codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-08T04:16:21.865Z", "quote": "这两个不是有效回复，撤回", "count": 2, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "us", "locator": "codex-01a0a9c7-b536-70a1-a311-dd27aadde9ec@2026-09-16T11:26:51.691Z", "quote": "另外10条人工回复不是有效回复么", "count": 10, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "us-nongolf", "locator": "voice/codex-01a06c2f-3a68-7813-a9e6-bd200712f50a.md [2026-09-21T05:32:58.523Z]", "quote": "场地分细，那就要完全流程隔离", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "us-nongolf-fields", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1790045963228", "quote": "先评估已有的邮箱呃质量情况，就不要出现那种是一些这种教师类的这种无关的角色邮箱。", "count": null, "confidence": "direct"}
{"category": "D:口径反复变动", "project_line": "户外运动场-法国", "locator": "voice/workbuddy-7aeb1541-a888-4313-8d18-910f724c8723.md@1790080081216", "quote": "不要只是抓官网一个机构，它可以有多个高邮箱信号页去抓取邮箱，你需要去往真实的关键角色邮箱去挖，官网的公共邮箱只能作为一个保底的转介绍基础。", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1790046671775", "quote": "然后就是无法判断是有效的话，你给我看。因为昨天发现很多你判断是有效的，实际上是无效的", "count": null, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1790059990072", "quote": "推群的17条，你再重新核对一下里面的回复内容，很多都不是有效回复，要把它取消加旗，然后把邮件撤掉", "count": 17, "confidence": "high"}
{"category": "D:口径反复变动", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-17T10:01:59.299Z", "quote": "15分钟太绝对了，不现实，我觉得就按第一版的开发新文案，然后排版你可以调整下，你觉得呢", "count": 15, "confidence": "high"}
```

---

## I. 只解释不执行、不认错（16 条）

**典型原话（已脱敏）**

- “你要想办法提效，不要字面回复，要执行” — `voice/codex-01a07534-69c6-7522-a713-ae334dd01be9.md@2026-09-06T15:52:25.631Z`（fr-grass/英国 <org>）
- “不要总分析，我需要解决的结果” — `voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md#2026-09-07T16:34:10`（agent-platform）
- “你还是没能解决问题” — `voice/codex-01a0a883-bfff-7733-93ae-04981f5d930c.md@2026-09-16T05:10:55.214Z`（fr-grass/法国天然草运动场）
- “你不要迎合我，需要作为AI系统架构师去分析，我们就是要讨论一个最好的方案” — `codex-01a047ff#2026-08-31T13:36:03`（跨项目/多线程编排）

**他对 agent 的明确要求：** 不要给方案、解释和“字面回复”，直接执行到结果；解决不了就说清卡点，不要换个说法绕过去。

**插件现状（0.6.4）：部分。** `marketops-research/SKILL.md:8-9`（“You do the research; the tools give you reach… do not stop to ask”）和 `:52` 把执行交给 `run()`，相邻能力是有的；但 5 个文件里没有“把解释换成执行”“失败要认账”的报告纪律，也没有对“只分析不动手”的禁止。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 21 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:只解释不执行、不认错", "project_line": "UK-natural-turf", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md [2026-09-07T13:54:57.158Z]", "quote": "卡点就在来源寻找了，你看下我在线程给的回复，现在线程都很傻，不会自己想办法", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "agent-platform", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md#2026-09-07T13:54:57", "quote": "卡点就在来源寻找了，你看下我在线程给的回复，现在线程都很傻，不会自己想办法", "count": null, "confidence": "direct"}
{"category": "D:只解释不执行、不认错", "project_line": "agent-platform", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md#2026-09-07T16:34:10", "quote": "不要总分析，我需要解决的结果", "count": null, "confidence": "direct"}
{"category": "D:只解释不执行、不认错", "project_line": "cross-project", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md [2026-09-07T16:34:10.792Z]", "quote": "不要总分析，我需要解决的结果", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "feishu-migration", "locator": "voice/codex-01a00004-fd67-79b2-bc6d-3493ccafd348.md#2026-08-14T12:09:40", "quote": "你自己想办法,因为之前我有授权成功过,但是我因为我重新卸载了Codex,所以重新开始了。", "count": null, "confidence": "direct"}
{"category": "D:只解释不执行、不认错", "project_line": "fr-grass/法国天然草运动场", "locator": "voice/codex-01a0a883-bfff-7733-93ae-04981f5d930c.md@2026-09-16T05:10:55.214Z", "quote": "你还是没能解决问题", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a07533-cb5c-7270-b2a6-380e41aa9a98.md@2026-09-06T11:32:32.753Z", "quote": "你发现异常要直接让他修复啊，你不然要你干嘛", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a07534-69c6-7522-a713-ae334dd01be9.md@2026-09-06T15:52:25.631Z", "quote": "你要想办法提效，不要字面回复，要执行", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "golf", "locator": "voice/codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md#2026-09-16T06:21:36.253Z", "quote": "自行解决阻断", "count": 3, "confidence": "direct"}
{"category": "D:只解释不执行、不认错", "project_line": "outreach-email", "locator": "voice/workbuddy-18ad4fbb-2305-4356-804d-a79a92763487.md#1790073208665", "quote": "想办法解决接口问题，不然无法工作", "count": null, "confidence": "direct"}
{"category": "D:只解释不执行、不认错", "project_line": "poland-schools-sports", "locator": "voice/codex-01a00109-42c1-74d2-8402-4555879f70f2.md@2026-08-15T03:11:43.071Z", "quote": "你看其他的调研 現程已經是自動在調研了,不會停下來。你自己想办法", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "户外运动场-全球母池", "locator": "voice/workbuddy-c8b43899-114a-47ef-9148-5b411256b2a2.md@1789999389753", "quote": "自行解决网络的问题。", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "户外运动场-德国", "locator": "voice/workbuddy-aa3fe2fc-5e87-4f61-9b37-80623ba48b4d.md@1790163326269", "quote": "R8的问题你自行解决 现在可发信的邮箱清单是哪个？", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T12:15:26.782Z", "quote": "自行恢复发信任务，不要一直扎提供，出错的直接记录并跳过，不要影响发信", "count": null, "confidence": "direct"}
{"category": "D:只解释不执行、不认错", "project_line": "跨项目/ROI口径", "locator": "codex-01a047ff#2026-09-02T14:02:08", "quote": "2、实际已经运行1天，你没有做过判断，都是我发信后和你讨论的 3、你现在的能力到底能不能自动判断自动调整，我做自动化目的就是要节省人力，现在结果是花我很多时间和token", "count": null, "confidence": "high"}
{"category": "D:只解释不执行、不认错", "project_line": "跨项目/多线程编排", "locator": "codex-01a047ff#2026-08-31T13:36:03", "quote": "你不要迎合我，需要作为AI系统架构师去分析，我们就是要讨论一个最好的方案", "count": 2, "confidence": "high"}
```

---

## J. 效率与 ROI 不达标（42 条）

**典型原话（已脱敏）**

- “我看一晚上过去了怎么才60个目录档案。这个调研ROI很低啊” — `codex-01a047ff#2026-09-01T02:12:10`（US-停车场划线-PL01）
- “1、一下午调研几乎没有进展 2、发信也是1800封，只发了252 现在就是流程跑通了，但是ROI完全没法让我正式自动化工作” — `codex-01a047ff#2026-09-02T20:12:31`（US-停车场划线-PL01）
- “你花了快8小时了，还需要多久，有没有更快的方案” — `voice/codex-01a01f1f-135c-7ef1-a9fd-88471aee6233.md [2026-08-20T12:22:05.959Z]`（de-16states）
- “评估下这个roi，3.5小时只有146个，分析慢的原因” — `voice/codex-01a06617-2143-7431-b70d-c43863864253.md [2026-09-04T02:35:04.758Z]`（uk-schools）

**他对 agent 的明确要求：** 按每 30 分钟产出多少有效邮箱、每小时发多少封来证明 ROI；慢要定位到环节，不是重复“我会优化”。

**插件现状（0.6.4）：部分。** 研究侧有明确的 ROI 纪律：`marketops-sources/SKILL.md:43-48`：“Stop a source that does not pay, not the job”，约 30 家样本、≥10% A/B 加码、5-10% 改善一次、<5% 停止；`marketops-research/SKILL.md:94-96` 用两次事故（128,685 个机构无可发、220 页换 5 条路线）要求报“机构数、A/B 路线、尝试次数”；`marketops-tools/SKILL.md:78-84` 要求“更多机构在飞，不是更多页”，并禁止轮询；`marketops-benchmarks/SKILL.md:31-37` 要求报每家机构的分钟数与成本。缺的两处：发信吞吐（本节约 12 条）属外呼，不在这 5 个文件里；他要求的“每 30 分钟产出多少有效邮箱”也没有对应的指标定义，包里报的是每家机构成本、A/B 占比与各阶段耗时。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 2 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:效率与 ROI 不达标", "project_line": "US-停车场划线-PL01", "locator": "codex-01a047ff#2026-09-01T02:12:10", "quote": "我看一晚上过去了怎么才60个目录档案。这个调研ROI很低啊", "count": 60, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "US-停车场划线-PL01", "locator": "codex-01a047ff#2026-09-01T05:37:08", "quote": "现在针对美国停车场，你现在的调研策略是什么，之前就是花费我很多token，然后调研的产出很低，我还怎么批量，而且还要发开发信", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "US-停车场划线-PL01", "locator": "codex-01a047ff#2026-09-01T11:45:59", "quote": "我看又是十几个小时过去了，可发开发信的邮箱依然很少，roi有问题，这样我一天哪怕多个任务同时跑，也发不了多少开发信", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "US-停车场划线-PL01", "locator": "codex-01a047ff#2026-09-02T20:12:31", "quote": "1、一下午调研几乎没有进展 2、发信也是1800封，只发了252 现在就是流程跑通了，但是ROI完全没法让我正式自动化工作", "count": 252, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "agent-platform", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md#2026-09-15T04:07:28", "quote": "现在整体流程你评价下，我能看到的是，调研的ROI不高，且邮箱质量一般", "count": null, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "cross-project", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md [2026-09-07T15:43:25.695Z]", "quote": "现在的效率太低了。", "count": 2, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "cross-project", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md@2026-09-15T04:07:28.068Z", "quote": "这里先讨论：现在整体流程你评价下，我能看到的是，调研的ROI不高，且邮箱质量一般", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "de-16states", "locator": "voice/codex-01a01f1f-135c-7ef1-a9fd-88471aee6233.md [2026-08-20T12:22:05.959Z]", "quote": "你花了快8小时了，还需要多久，有没有更快的方案", "count": 8, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "de-16states", "locator": "voice/codex-01a01f1f-135c-7ef1-a9fd-88471aee6233.md [2026-08-20T12:22:05.959Z]", "quote": "还有没有更快的，现在才一个州，我要的是全国", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "de-sports-field", "locator": "voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md [2026-08-18T12:53:05.050Z]", "quote": "先临时暂停任务，没token了", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "de-sports-field", "locator": "voice/codex-01a00eff-cbd8-7cc2-9b19-8c8c6d12f995.md [2026-08-20T09:13:43.620Z]", "quote": "你花了快8小时了，还需要多久，有没有更快的方案", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "fjd-mother-pool", "locator": "voice/codex-01a0c3f4-33d3-7500-88eb-4bb9a8ef424b.md@2026-09-21T12:32:41Z", "quote": "讨论，现在我发现基于母池的深度调研效率非常慢", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "fr-grass/法国天然草运动场", "locator": "voice/codex-01a0a94b-ed18-75c2-a9a6-d1efebd9fec0.md@2026-09-18T11:24:13.082Z", "quote": "你为什么一开始批量效率就很差", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a07534-69c6-7522-a713-ae334dd01be9.md@2026-09-06T12:52:26.570Z", "quote": "明显有问题，新增了200个，抓取快了，剩下的筛选超级慢，还不如前面抓取", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a07534-69c6-7522-a713-ae334dd01be9.md@2026-09-06T13:34:30.147Z", "quote": "效率还是低，要按1分钟100个 目标提速", "count": 6, "confidence": "medium"}
{"category": "D:效率与 ROI 不达标", "project_line": "global-process", "locator": "voice/codex-01a0a341-3e75-7dd1-b3d8-e9d88a996445.md [2026-09-15T04:32:31.774Z]", "quote": "现在整体流程你评价下，我能看到的是，调研的ROI不高，且邮箱质量一般", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "golf", "locator": "voice/codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md#2026-08-28T02:47:46.846Z", "quote": "你们不能同时发送么", "count": null, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "golf", "locator": "voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1789995634336", "quote": "美国运动场的基于母池的深度调研慢，它现在还在跑，慢在跑了一天，产出邮箱数量很少", "count": null, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "poland-schools-sports", "locator": "voice/codex-01a00109-42c1-74d2-8402-4555879f70f2.md@2026-08-17T04:46:14.028Z", "quote": "现在波兰的的母池调研太慢了，更别说后续需要对每个母池里面的机构做深度调研了，你看下", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "schools", "locator": "voice/codex-01a04159-0433-7ea3-8b03-5b4d2bced55e#2026-08-27T09:11:50.804Z", "quote": "需要提高发信速度", "count": null, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "swiss-football", "locator": "voice/codex-01a066c5-1412-7392-bdde-7aa87a525134.md [2026-09-04T05:37:44.693Z]", "quote": "roi太低，需要短时间足够多有效邮箱", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "swiss-football-send", "locator": "voice/codex-01a06cac-7fc9-76b3-8631-137a04d35012.md [2026-09-08T07:18:34.661Z]", "quote": "1个多小时怎么才发了25封？", "count": 25, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "uk-schools", "locator": "voice/codex-01a06617-2143-7431-b70d-c43863864253.md [2026-09-04T02:35:04.758Z]", "quote": "评估下这个roi，3.5小时只有146个，分析慢的原因", "count": 146, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "uk-schools", "locator": "voice/codex-01a06617-2143-7431-b70d-c43863864253.md [2026-09-04T03:04:09.375Z]", "quote": "按新策略，找到100个有效邮箱", "count": 100, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "uk-schools", "locator": "voice/codex-01a06617-2143-7431-b70d-c43863864253.md [2026-09-04T09:55:48.502Z]", "quote": "调研可以先停止了，现在roi太低了，移动才10个邮箱", "count": 10, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "uk-send", "locator": "voice/codex-01a06b86-51c1-7032-ade2-677c2d25c7a0.md [2026-09-08T09:55:04.095Z]", "quote": "可以提速么，现在24个邮箱同时发，速度应该提高", "count": 24, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "us", "locator": "codex-01a0a386-dffc-73c3-ae77-18613d44280d@2026-09-15T09:35:25.577Z", "quote": "爬取速度太慢，提速", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "us", "locator": "codex-01a0a386-ebe3-7b13-a2c6-10b97014cb5f@2026-09-15T09:01:27.035Z", "quote": "效率太低，19个账户，调研花了20分钟", "count": 19, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "us", "locator": "codex-01a0c2f7-ac43-7df0-8c15-7ff7be5d8676@2026-09-21T08:53:22.215Z", "quote": "复盘B001网页镜像效率", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "us-nongolf-fields", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1789890954160", "quote": "特别是現在的深度調研花的时间太长了，而且你25户调研出来的邮箱是不是都是我要的关键类的角色邮箱？无效邮箱就没必要了", "count": 25, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "us-nongolf-fields", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1789902449009", "quote": "现在多个子agent做法，调研效率很差。", "count": null, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "us-nongolf-fields", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1790075323296", "quote": "输出一份飞书表格的邮箱清单，你处理了42分钟还没完成", "count": 42, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-20T11:14:17.239Z", "quote": "速度需要提升，参考“美国学校-发信和监控”的发信策略", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-20T11:36:51.937Z", "quote": "发信速度提升，参考线程：美国学校-发信和监控", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-20T13:06:29.681Z", "quote": "能提升发信速度么", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-20T13:09:07.174Z", "quote": "是不是并发发送的", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T09:14:41.039Z", "quote": "发信速度有点慢，你现在发信速度多少，想办法提升", "count": null, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T09:21:53.041Z", "quote": "你读取线程名称“美国学校-发信和监控”的发信策略，他的速度比你快几倍", "count": null, "confidence": "direct"}
{"category": "D:效率与 ROI 不达标", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-17T16:05:23.395Z", "quote": "飞书的问题你想办法解决，后面还有一堆邮件，现在效率太低", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "跨项目/ROI口径", "locator": "codex-01a047ff#2026-09-02T08:25:42", "quote": "现在整体问题就是你的调研速度、发信速度都很慢，没有之前先调研好，然后发信效率高，这个我理解就多agent问题", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "跨项目/ROI口径", "locator": "codex-01a047ff#2026-09-02T15:31:53", "quote": "现在调研的策略，有明显提高roi么？数据证明", "count": null, "confidence": "high"}
{"category": "D:效率与 ROI 不达标", "project_line": "跨项目/多线程编排", "locator": "codex-01a047ff#2026-09-02T02:59:22", "quote": "1、两个线程调研的进度都很慢，一晚上只有小几十封邮件，ROI太低了 2、看下限制邮件发送问题，不需要太多限制", "count": null, "confidence": "high"}
```

---

## K. 进展与状态不透明（15 条）

**典型原话（已脱敏）**

- “要显示实时进展，不是光回复，我都不知道你在不在监控” — `voice/codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187.md@2026-09-22T07:56:41.097Z`（us/美国运动场发信与监控）
- “不要到后台运行，我看不到，会以为你不在调研” — `voice/codex-01a00083-555a-7150-b181-c596d9a7d1c7.md [2026-08-14T14:08:40.335Z]`（us-k12）
- “在？” — `codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-13T10:59:40.675Z`（us）
- “1、中文显示 2调研完全结束了饿么” — `voice/workbuddy-d87ce9a7-dc86-4ac9-9abf-73b72f674a67.md@1790229508783`（户外运动场-英国）

**他对 agent 的明确要求：** 运行中要能看到实时进展（批次、已处理/总数、速度、卡点），不能用“正在处理”顶替；回复与日志要中文、可复核。

**插件现状（0.6.4）：部分。** `marketops-research/SKILL.md:69` 说 `project(name)` 随时可看进度（池、runs、队列、成本、余额，见 `:31-33`），`marketops-benchmarks/SKILL.md:31-37` 规定了要报哪些数；但没有对用户汇报的节奏与格式，`marketops-research/SKILL.md:97-98` 还明确要求不要轮询、只在有决定可做时看 `project()`。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 19 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:进展与状态不透明", "project_line": "cross-project", "locator": "voice/codex-01a00ede-71c2-7a22-87f4-1a6cf34bc701.md [2026-08-17T08:53:05.049Z]", "quote": "你的回复要中文", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "golf", "locator": "voice/codex-01a041a3-3c83-7820-91e3-09a7dd2361f9.md#2026-08-28T02:33:42.342Z", "quote": "发信结束了么？", "count": 2, "confidence": "direct"}
{"category": "D:进展与状态不透明", "project_line": "golf", "locator": "voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790168363388", "quote": "现在德国的线程的名称到底是哪一个？", "count": 3, "confidence": "direct"}
{"category": "D:进展与状态不透明", "project_line": "reply-monitor", "locator": "voice/codex-01a0431b-02d7-7f40-b479-5b3caf2c92e8.md [2026-09-15T09:18:58.069Z]", "quote": "人工复合的具体内容，中文展示，不然无法判断", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "us", "locator": "codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-13T10:47:08.928Z", "quote": "是你的定时任务么？后续定时任务在本线程打开，那个归档", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "us", "locator": "codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-13T10:59:40.675Z", "quote": "在？", "count": 2, "confidence": "inferred"}
{"category": "D:进展与状态不透明", "project_line": "us", "locator": "codex-01a0a364-d36a-76a1-a5a6-f0ecc5c86e66@2026-09-15T05:32:22.708Z", "quote": "之前出现和总协调线程沟通反馈问题，效率低，且一直调不好，我去每个线程沟通，才调好", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "us", "locator": "codex-01a0c2f7-ece1-7de0-8db3-6a562c7c5adc@2026-09-21T13:18:53.803Z", "quote": "94条候选是什么，就本线程聊", "count": 94, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "us-k12", "locator": "voice/codex-01a00083-555a-7150-b181-c596d9a7d1c7.md [2026-08-14T14:08:40.335Z]", "quote": "不要到后台运行，我看不到，会以为你不在调研", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "us/美国运动场发信与监控", "locator": "voice/codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187.md@2026-09-22T07:56:41.097Z", "quote": "要显示实时进展，不是光回复，我都不知道你在不在监控", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "德国运动场", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-28T04:14:25.797Z", "quote": "这个是那个邮箱发的，主题是什么", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "户外运动场-德国", "locator": "voice/workbuddy-aa3fe2fc-5e87-4f61-9b37-80623ba48b4d.md@1790220562376", "quote": "还在继续么？", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "户外运动场-英国", "locator": "voice/workbuddy-d87ce9a7-dc86-4ac9-9abf-73b72f674a67.md@1790229508783", "quote": "1、中文显示 2调研完全结束了饿么", "count": null, "confidence": "inferred"}
{"category": "D:进展与状态不透明", "project_line": "户外运动场-跨国家", "locator": "voice/workbuddy-d87ce9a7-dc86-4ac9-9abf-73b72f674a67.md@1790163193110", "quote": "我能发信的邮箱清单是哪个文件", "count": null, "confidence": "high"}
{"category": "D:进展与状态不透明", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-18T12:08:42.238Z", "quote": "你还在执行2小时的有效邮件监控任务么", "count": 2, "confidence": "high"}
```

---

## L. 交付返工（排版/名片/素材）（17 条）

**典型原话（已脱敏）**

- “邮件排版还不对，手机端明显不是平铺的，更像嵌入的，看附件就不对，电脑端也是” — `voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T04:49:19.352Z`（波兰学校）
- “<name>的名片错的，用我提供的” — `voice/codex-01a07b79-7713-7f41-bea0-e5cf270f81a7.md@2026-09-07T11:45:44Z`（高尔夫）
- “格式对了，图片，特别gif动图很模糊” — `voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T05:19:50.576Z`（波兰学校）
- “你的关键镜头没提取到，重新剪辑” — `voice/codex-01a0cd46-11e8-7872-9adb-76151b1249d7.md#2026-09-23T09:29:05`（video-assets）

**他对 agent 的明确要求：** 邮件正文必须左对齐流式平铺、手机与电脑一致；GIF 保持原尺寸与清晰度；名片、地址、主题、正文占位要逐项核。

**插件现状（0.6.4）：不适用（外呼）。** 本插件只做研究，`marketops-research/SKILL.md:111` 明确“nothing is sent”；导出只有工作簿与他要求的 18 列 CSV（`:42`），邮件平铺、GIF、名片、视频素材的渲染规范都不在这 5 个文件里。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 1 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:交付返工（排版/名片/素材）", "project_line": "fjd-outreach-sports-field", "locator": "voice/codex-01a07bd7-cec6-7e82-bb7c-a7ae8c4aecc1.md@2026-09-08T08:15:53Z", "quote": "评估下，里面<name>的文字名片错了，帮我改成<name>的文字名片", "count": null, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "golf", "locator": "codex-01a013fd-d363-7e13-a157-306a09f06647@2026-08-18T11:41:15.648Z", "quote": "你自己检查下邮件格式，电脑不是很看出来，手机明显看到邮件内容不是平铺的，重新发送", "count": null, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "us", "locator": "codex-01a0a9c7-b536-70a1-a311-dd27aadde9ec@2026-09-16T12:38:24.595Z", "quote": "正文里不要出现附件截图的标题，重新发送测试", "count": null, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "us-pool-builder", "locator": "voice/codex-01a038e5-097b-74d1-8bf6-ffc4f326d1d7.md [2026-08-25T13:07:14.224Z]", "quote": "1、格式有问题，不是平铺的，看附件1 2、附件2中的地址的字体和其他不一致 调整后重新发测试", "count": 2, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "video-assets", "locator": "voice/codex-01a0cd46-11e8-7872-9adb-76151b1249d7.md#2026-09-23T08:34:22", "quote": "1、产品ID展示不全 2、左边是大卖点、右边是产品ID", "count": null, "confidence": "direct"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "video-assets", "locator": "voice/codex-01a0cd46-11e8-7872-9adb-76151b1249d7.md#2026-09-23T08:37:56", "quote": "1、大卖点的排版再做优化下 2、产品的ID失真了", "count": null, "confidence": "direct"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "video-assets", "locator": "voice/codex-01a0cd46-11e8-7872-9adb-76151b1249d7.md#2026-09-23T09:29:05", "quote": "你的关键镜头没提取到，重新剪辑", "count": null, "confidence": "direct"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "德国运动场", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-28T02:54:15.557Z", "quote": "邮件没有平铺。优化后重新发送一封", "count": null, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "德国运动场", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-28T03:39:59.788Z", "quote": "继续尝试发1封", "count": 1, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T04:06:49.258Z", "quote": "1、飞书表格有3张表，你要全部读取 2、邮件格式不对，参考附件排版，图片要高清，重新发送", "count": null, "confidence": "direct"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T04:49:19.352Z", "quote": "邮件排版还不对，手机端明显不是平铺的，更像嵌入的，看附件就不对，电脑端也是", "count": null, "confidence": "direct"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T05:19:50.576Z", "quote": "格式对了，图片，特别gif动图很模糊", "count": null, "confidence": "direct"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-20T06:52:56.042Z", "quote": "电脑端直接剧中了，明显有问题，还是左对齐吧，效果更好一点，你觉得呢，不要让人看上去是嵌入的，手机端没问题", "count": null, "confidence": "direct"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-17T09:59:37.297Z", "quote": "评估下邮件质量，我觉得正文内容没有上一版本好，排版可以", "count": null, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "高尔夫", "locator": "voice/codex-01a022b2-6f1a-72c1-b118-4a5cc56bb423.md@2026-08-21T05:35:19Z", "quote": "你在正式发的时候，会带上正确的名称吧，不会像附件一样", "count": null, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "高尔夫", "locator": "voice/codex-01a07b79-7713-7f41-bea0-e5cf270f81a7.md@2026-09-07T11:45:44Z", "quote": "<name>的名片错的，用我提供的", "count": null, "confidence": "high"}
{"category": "D:交付返工（排版/名片/素材）", "project_line": "高尔夫", "locator": "voice/codex-01a07b79-7713-7f41-bea0-e5cf270f81a7.md@2026-09-07T11:54:03Z", "quote": "重新生成，然后发开发信是英文，给我评审可以中英文；给我评审时候要中英文版本", "count": 2, "confidence": "high"}
```

---

## M. 单点故障拖垮全批（15 条）

**典型原话（已脱敏）**

- “如何保证高发信率，现在一次发信失败很多，必须做二次三次” — `codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-17T15:36:13.041Z`（美国棒球）
- “这里讨论问题，我发现同一个邮箱有多个发信任务并行，容易导致发信失败” — `codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-18T12:40:04.408Z`（美国棒球）
- “调研不主动打开电脑浏览器完成不了的调研吗？现在非常消耗电脑的内存大，打开了非常多的浏览器。” — `voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790131129527`（golf）
- “你没法扫描我的飞书邮箱么？我有22个用于开发客户的邮箱” — `voice/codex-01a00004-fd67-79b2-bc6d-3493ccafd348.md#2026-08-14T14:30:45`（outreach-email）

**他对 agent 的明确要求：** 单个邮箱锁死、单封失败、单页超时、单表行满，都不能让整批停或把整批标错；错一条记一条、跳过后继续。

**插件现状（0.6.4）：部分。** 研究侧有逐页失败隔离：`marketops-sources/SKILL.md:71-84` 区分抽取 bug 与封锁，并写明“A failure is not a dead site”——限流、5xx、断连各重试一次，一次 DNS 限流把 183 个活域名当死站扔掉的教训也在；`marketops-tools/SKILL.md:47-56` 规定页面读不了时先分抽取还是封锁，403/429 自动重试一次，记录 blocker 后继续。缺的是发信侧：邮箱锁、单封失败、同一邮箱并行任务的逐条隔离不在这 5 个文件里。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 12 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:单点故障拖垮全批", "project_line": "codex-runtime", "locator": "voice/codex-01a00db7-7fe2-7cc3-a1ea-d3fd9f5a07f6.md@2026-08-17T03:27:09.548Z", "quote": "再次确认原因，未响应主要出现在切换对话", "count": 1, "confidence": "inferred"}
{"category": "D:单点故障拖垮全批", "project_line": "cross-project-ops", "locator": "voice/codex-01a000c9-4fd8-75b2-859d-c273a067a68d.md@2026-08-14T14:59:48.925Z", "quote": "只有规则包，技能包呢？", "count": 1, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "de-golf", "locator": "voice/codex-01a022af-89bf-7d13-8f97-32625630733d.md [2026-08-25T12:49:05.746Z]", "quote": "想办法解决，需要邮箱回复监控正常", "count": null, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "fjd-email-infra", "locator": "voice/codex-01a07aa5-47d4-73b0-a37f-4d84de4d4f2d.md@2026-09-07T10:00:19Z", "quote": "你再检查下，什么原因导致的", "count": 2, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "fjd-inventory", "locator": "voice/codex-01a0ad85-5b18-7c33-80be-4cfa6933ed8f.md@2026-09-17T08:34:46Z", "quote": "打开不行", "count": null, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "fjd-marketing-assets", "locator": "voice/codex-01a01d5b-8ac9-7221-a7a4-9d0054e5b197.md@2026-08-20T04:24:14Z", "quote": "你没法提取视频里面的讲话语言么", "count": null, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "golf", "locator": "voice/workbuddy-5e08e2b0-4bb3-4426-8591-9d0acc98f348.md#1790131129527", "quote": "调研不主动打开电脑浏览器完成不了的调研吗？现在非常消耗电脑的内存大，打开了非常多的浏览器。", "count": null, "confidence": "direct"}
{"category": "D:单点故障拖垮全批", "project_line": "outreach-email", "locator": "voice/codex-01a00004-fd67-79b2-bc6d-3493ccafd348.md#2026-08-14T14:30:45", "quote": "你没法扫描我的飞书邮箱么？我有22个用于开发客户的邮箱", "count": 22, "confidence": "direct"}
{"category": "D:单点故障拖垮全批", "project_line": "us", "locator": "codex-01a000c9-93de-7270-8d91-dfc9fd5b0668@2026-09-16T03:08:45.661Z", "quote": "我看迁移数据已经几个G了，能交接么", "count": null, "confidence": "inferred"}
{"category": "D:单点故障拖垮全批", "project_line": "us", "locator": "codex-01a000c9-93de-7270-8d91-dfc9fd5b0668@2026-09-16T03:36:31.105Z", "quote": "有效邮件监控技能和规则、发信等都继承了么", "count": null, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "us", "locator": "codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187@2026-09-22T10:23:11.078Z", "quote": "是其他人也在同时使用相同邮箱发信导致的么", "count": null, "confidence": "inferred"}
{"category": "D:单点故障拖垮全批", "project_line": "us", "locator": "codex-01a0aef1-f7dd-7c61-bfd3-196097ef0187@2026-09-23T13:49:41.947Z", "quote": "13个 发件邮箱都沒有额度了嗎?", "count": 13, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-25T12:49:33.832Z", "quote": "想办法解决，需要邮箱回复监控正常", "count": null, "confidence": "direct"}
{"category": "D:单点故障拖垮全批", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-17T15:36:13.041Z", "quote": "如何保证高发信率，现在一次发信失败很多，必须做二次三次", "count": null, "confidence": "high"}
{"category": "D:单点故障拖垮全批", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-18T12:40:04.408Z", "quote": "这里讨论问题，我发现同一个邮箱有多个发信任务并行，容易导致发信失败", "count": null, "confidence": "high"}
```

---

## N. 覆盖遗漏与数据不全（21 条）

**典型原话（已脱敏）**

- “你有没有监控6万多个发送记录的回复？我感觉你有遗漏” — `voice/codex-01a0ae9c-3c8a-7642-ad99-b2644a0985ea.md@2026-09-17T13:27:43.631Z`（us/美国运动场发信与监控）
- “不对吧，我刚提供的数据至少应该有几万封邮件了，你附件和飞书表格都读取了么？” — `codex-01a000c9-93de-7270-8d91-dfc9fd5b0668@2026-09-17T09:29:21.413Z`（us）
- “你怎么没有评估第4批网页质量？” — `voice/codex-01a06c69-b003-72f3-85db-1a6f035ded65.md@2026-09-04T14:42:32.049Z`（fr-grass/英国 <org>）
- “自行完成3和4后再做后续工作，现在你的问题主要 1、判断不准 2、抓取到的数据不全” — `workbuddy-e4bdcdff#1790067876021`（有效回复监控）

**他对 agent 的明确要求：** 扫描要覆盖全部收件箱/全部批次/全部历史；没覆盖到的要报“覆盖异常”，不能把没扫到说成“无回复”。

**插件现状（0.6.4）：部分。** 研究侧有覆盖纪律：`marketops-research/SKILL.md:91-93` 要求每个机构必须以联系人或原因收尾，池大产出小时要找“输出没被合并的那个阶段”；`:112-113` 规定弱证据与 review 只是标签、不丢行；`marketops-sources/SKILL.md:41-42` 要求先把已落盘的结果捞回来（tender 1,029 行、一个被丢掉的“other”桶）。缺的是收件箱扫描侧：本节约一半的 D 行问的是“全部收件箱/全部历史有没有扫到”，属回复监控（外呼），这 5 个文件不涉及。

**该类相关的自动化诉求（L 类，不计入抱怨计数）：** 本节原有 4 条 L 记录，已移出本文；L 类整体归并为 16 条，见 `WISHLIST.md`。

```jsonl
{"category": "D:覆盖遗漏与数据不全", "project_line": "fr-grass/英国 <org>", "locator": "voice/codex-01a06c69-b003-72f3-85db-1a6f035ded65.md@2026-09-04T14:42:32.049Z", "quote": "你怎么没有评估第4批网页质量？", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "golf", "locator": "voice/workbuddy-23da98e1-8cae-4124-ad81-39b8e4e5f275.md#1789903794541", "quote": "现在的主要问题是，我都没法判断哪一个母池是最全的。我给你提供的也不一定是最全的。你现在如果母池都不全，那后面的实际的深度调研肯定会有遗漏", "count": null, "confidence": "direct"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us", "locator": "codex-01a000c9-93de-7270-8d91-dfc9fd5b0668@2026-09-09T00:51:22.197Z", "quote": "你有扫描遗漏么？", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us", "locator": "codex-01a000c9-93de-7270-8d91-dfc9fd5b0668@2026-09-17T09:29:21.413Z", "quote": "不对吧，我刚提供的数据至少应该有几万封邮件了，你附件和飞书表格都读取了么？", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us", "locator": "codex-01a000c9-93de-7270-8d91-dfc9fd5b0668@2026-09-17T15:32:38.218Z", "quote": "还有没有遗漏的美国运动场。", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us", "locator": "codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-08T03:25:10.539Z", "quote": "有大量这种的，复盘都复盘到了么", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us", "locator": "codex-01a07b74-426e-7310-b2ad-0432cd738a05@2026-09-08T03:30:28.942Z", "quote": "重新复盘", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us", "locator": "codex-01a0a386-ebe3-7b13-a2c6-10b97014cb5f@2026-09-15T09:33:37.553Z", "quote": "50个账户只有4个邮箱？", "count": 50, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us", "locator": "codex-01a0a9c7-b536-70a1-a311-dd27aadde9ec@2026-09-17T03:15:25.291Z", "quote": "邮件回复结果要增加人工复核的，刚才的一轮扫描遗漏了，会少很多leads机会", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us-k12", "locator": "voice/codex-01a00083-555a-7150-b181-c596d9a7d1c7.md [2026-08-15T13:09:58.522Z]", "quote": "怎么只有7个邮箱可发？", "count": 7, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us-nongolf-fields", "locator": "voice/workbuddy-77a872c4-20fe-49ae-b016-f6ce00940600#1790223534944", "quote": "现在的邮箱数量太少，对于里面有六万多个邮箱还是要去做筛选，需要最后更多的能够发开发性的邮箱", "count": 60000, "confidence": "direct"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us-pool-builder", "locator": "voice/codex-01a038e5-097b-74d1-8bf6-ffc4f326d1d7.md [2026-08-26T04:58:36.020Z]", "quote": "第三封是我这边的主题么？我们的主题没有这个，怎么监控到这个回复", "count": 3, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us-un-schools", "locator": "voice/codex-01a00084-2f1a-7080-8801-f10704f849a7.md [2026-08-14T14:48:24.049Z]", "quote": "五洲只有这么几个么？学校那么多，你不调研全部五洲的学校结束就一直跑下去", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us-un-schools", "locator": "voice/codex-01a00084-2f1a-7080-8801-f10704f849a7.md [2026-08-18T11:21:11.218Z]", "quote": "怎么没有1个邮箱？你不是在浪费我时间和token？", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "us/美国运动场发信与监控", "locator": "voice/codex-01a0ae9c-3c8a-7642-ad99-b2644a0985ea.md@2026-09-17T13:27:43.631Z", "quote": "你有没有监控6万多个发送记录的回复？我感觉你有遗漏", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T05:50:30.954Z", "quote": "监控一次有效回复，我已经看到有效回复了，如附件", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1790065439779", "quote": "对判定为终端的回复，结合正文都需要重新判断下，我理解的真实有效回复一定不止882，我刚才已经在其他渠道扫描发现9.21的有效回复，你根本就没有识别到", "count": null, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "有效回复监控", "locator": "workbuddy-e4bdcdff#1790067876021", "quote": "自行完成3和4后再做后续工作，现在你的问题主要 1、判断不准 2、抓取到的数据不全", "count": 2, "confidence": "high"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-21T05:39:40.869Z", "quote": "戒指现在一封有效恢复都没有么？我看飞书群一个都没有", "count": null, "confidence": "direct"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "波兰学校", "locator": "voice/codex-01a01d40-942f-7ca0-a37b-860b558a978b.md#2026-08-23T11:23:19.707Z", "quote": "监控有效邮件回复，发了这么多开发新，不可能一封没有", "count": null, "confidence": "direct"}
{"category": "D:覆盖遗漏与数据不全", "project_line": "美国棒球", "locator": "codex-01a00f13-b20d-72e3-9506-ed17f670d499@2026-08-18T02:02:44.103Z", "quote": "开启邮件监控，我发现还有读取超时的邮件，需要解决，不然会有遗漏，无法判断的邮件回复发给和我的聊天框", "count": null, "confidence": "high"}
```

---

## O. 误发与错配（5 条）

**典型原话（已脱敏）**

- “误发销售群的，确认撤回” — `codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T02:44:31.851Z`（德国学校）
- “确认“撤回这 41 张私聊误卡并恢复监控”” — `codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T02:53:06.336Z`（德国学校）
- “确认撤回这两张退订卡” — `codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T06:09:50.203Z`（德国学校）

**他对 agent 的明确要求：** 卡片只能进指定销售群或本线程；系统通知、OOO、退订不得推群或私聊，发现误发要能撤回并记录。

**插件现状（0.6.4）：不适用（外呼）。** 这是飞书群与私聊的投递路由和撤回，属于外呼环节；`marketops-research/SKILL.md:111` 明确不发信，5 个文件里没有投递目标或撤回规则。

```jsonl
{"category": "D:误发与错配", "project_line": "de-golf", "locator": "voice/codex-01a022af-89bf-7d13-8f97-32625630733d.md [2026-08-23T11:21:00.254Z]", "quote": "1、撤回上述 2 张错误群卡并取消其邮件 Flag 2\\否重新授权完成剩余 92 封 3\\现在总共多少有效回复", "count": 2, "confidence": "high"}
{"category": "D:误发与错配", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T02:44:31.851Z", "quote": "误发销售群的，确认撤回", "count": null, "confidence": "high"}
{"category": "D:误发与错配", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T02:48:13.501Z", "quote": "每 3 小时监控仍暂停；待清理我这里误推送的系统/OOO 复核卡后再恢复", "count": null, "confidence": "high"}
{"category": "D:误发与错配", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T02:53:06.336Z", "quote": "确认“撤回这 41 张私聊误卡并恢复监控”", "count": 41, "confidence": "high"}
{"category": "D:误发与错配", "project_line": "德国学校", "locator": "codex-01a01d6b-bbd6-7171-870f-9f6b9ea0b667@2026-08-21T06:09:50.203Z", "quote": "确认撤回这两张退订卡", "count": 2, "confidence": "high"}
```

---

## 16. 非抱怨记录（需求、追问、运营指令）（16 条，不计入抱怨）

这里保留的是原分片 `category` 标为 D、但内容是需求、追问或运营指令的记录。它们不表示 agent 做错了什么，只提供需求与口径的上下文；按硬规则同样逐条给出 locator。本节原有 13 条 E、30 条 L，已按要求移出：E 见 `CORRECTIONS.md`，L 见 `WISHLIST.md`；以下只保留 16 条 D 行。

```jsonl
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "cross-project", "locator": "voice/codex-01a063d6-9b5b-7cb1-91ce-3034cca05953.md@2026-09-08T10:53:43.527Z", "quote": "讨论，不该任何流程，你读取现在的流程，一直到模板与排版线程已经调通了", "count": null, "confidence": "inferred"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "cross-project-ops", "locator": "voice/codex-01a000c9-4fd8-75b2-859d-c273a067a68d.md@2026-08-14T14:59:48.925Z", "quote": "already-running model sessions must be explicitly refreshed and cannot be claimed as retroactively updated", "count": 3, "confidence": "inferred"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "fjd-email-infra", "locator": "voice/codex-01a07aa5-47d4-73b0-a37f-4d84de4d4f2d.md@2026-09-07T07:01:23Z", "quote": "再优化下，我主要用于发邮件", "count": null, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "france-golf-schools", "locator": "voice/codex-01a04189-c935-76c0-849e-9a7a7a4cdd2d.md@2026-08-27T14:50:39.018Z", "quote": "请在完成当前正在写入的文件后立即做本子任务的阶段校验与 checkpoint，然后结束子任务并汇报；不要继续进入公开邮箱扫描或下一来源。整体市场仍标 continue_required 即可。", "count": 1, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "france-golf-schools", "locator": "voice/codex-01a04189-c935-76c0-849e-9a7a7a4cdd2d.md@2026-08-27T14:54:31.729Z", "quote": "立即停止继续研究。不要再修改大型母池；只读取现有已落盘结果，做必要的只读统计和阶段校验，写一个简短 checkpoint（如尚未存在），然后立刻结束并汇报。", "count": 1, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "global-sports-fields", "locator": "voice/workbuddy-18ad4fbb-2305-4356-804d-a79a92763487.md#1790052028663", "quote": "你就明确告诉我怎么发信，我需要开多个线程，还是就本线程直接就可以发了？因为我马上就要发全球所有运动场的开发信", "count": null, "confidence": "direct"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "outreach-email", "locator": "voice/workbuddy-18ad4fbb-2305-4356-804d-a79a92763487.md#1790065855802", "quote": "一个发信线程，比如我今天要发美国一次开发信，和其他国家二次开发信，开发信模版都不一样，不会乱么？", "count": null, "confidence": "direct"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "tooling", "locator": "voice/workbuddy-921a0643-40a1-44ce-a2c2-2b36df0e01a5.md#1789956742877", "quote": "我们聊的都是现在新的电脑上 WorkBuddy，跟别的没关系，我现在只是在新的 WorkBuddy 上面跑了六七个任务。想把里面的核心任务做转移而已。", "count": 2, "confidence": "direct"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "us", "locator": "codex-01a00025-f35a-7f40-811c-d9ad62b1d8b4@2026-08-14T12:11:56.367Z", "quote": "所以现在是不是等我把所有的那个技能飞梳连接,以及我那些所有的发行监控技能都安装好以后,你就可以了。", "count": null, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "us", "locator": "codex-01a01f31-092e-7a10-aa0b-749918943fe5@2026-08-20T12:43:20.405Z", "quote": "你能直接继承线程：美国学校-发信和监控 的所有记录、任务么，我想在新的线程继续，因为我发现，旧线程已经不能按照我的指令工作了", "count": null, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "us", "locator": "codex-01a0a364-d36a-76a1-a5a6-f0ecc5c86e66@2026-09-15T05:20:06.045Z", "quote": "整个流程会长么，我担心后面调试问题很多", "count": null, "confidence": "inferred"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "户外运动场-全球母池", "locator": "voice/workbuddy-c8b43899-114a-47ef-9148-5b411256b2a2.md@1790004068792", "quote": "现明确跑通一个国家完整的母池。", "count": null, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "跨项目/多线程编排", "locator": "codex-01a047ff#2026-08-31T14:48:14", "quote": "线层的命名用中文不要英文。", "count": null, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "跨项目/多线程编排", "locator": "codex-01a047ff#2026-09-01T12:23:27", "quote": "线程名我觉得不对，应该是调研和发信，现在名称容易误解只是调研", "count": null, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "高尔夫", "locator": "voice/codex-01a05d03-05dd-7883-90cd-a9825fa834c7.md@2026-09-02T03:43:19Z", "quote": "附件里面的高尔夫都已经发过邮件了么", "count": 2, "confidence": "high"}
{"category": "D:非抱怨记录（需求、追问、运营指令）", "project_line": "高尔夫", "locator": "voice/workbuddy-dd0039f0-2074-4796-a411-fd35d51364b2.md@1789902057517", "quote": "我发现应该放到电脑别的硬盘，比如D盘，有780G的内存，本任务先讨论，能不能做迁移，只前都错误按默认的地址走了", "count": 780, "confidence": "high"}
```

---

## 17. 空分片记录

`work-01.jsonl` 至 `work-36.jsonl` 中没有 D/E/L 相关条目（三类只在他的原话分片里）。逐片留空记录如下：

```jsonl

{"empty": true, "shard": "work-01.jsonl"}
{"empty": true, "shard": "work-02.jsonl"}
{"empty": true, "shard": "work-03.jsonl"}
{"empty": true, "shard": "work-04.jsonl"}
{"empty": true, "shard": "work-05.jsonl"}
{"empty": true, "shard": "work-06.jsonl"}
{"empty": true, "shard": "work-07.jsonl"}
{"empty": true, "shard": "work-08.jsonl"}
{"empty": true, "shard": "work-09.jsonl"}
{"empty": true, "shard": "work-10.jsonl"}
{"empty": true, "shard": "work-11.jsonl"}
{"empty": true, "shard": "work-12.jsonl"}
{"empty": true, "shard": "work-13.jsonl"}
{"empty": true, "shard": "work-14.jsonl"}
{"empty": true, "shard": "work-15.jsonl"}
{"empty": true, "shard": "work-16.jsonl"}
{"empty": true, "shard": "work-17.jsonl"}
{"empty": true, "shard": "work-18.jsonl"}
{"empty": true, "shard": "work-19.jsonl"}
{"empty": true, "shard": "work-20.jsonl"}
{"empty": true, "shard": "work-21.jsonl"}
{"empty": true, "shard": "work-22.jsonl"}
{"empty": true, "shard": "work-23.jsonl"}
{"empty": true, "shard": "work-24.jsonl"}
{"empty": true, "shard": "work-25.jsonl"}
{"empty": true, "shard": "work-26.jsonl"}
{"empty": true, "shard": "work-27.jsonl"}
{"empty": true, "shard": "work-28.jsonl"}
{"empty": true, "shard": "work-29.jsonl"}
{"empty": true, "shard": "work-30.jsonl"}
{"empty": true, "shard": "work-31.jsonl"}
{"empty": true, "shard": "work-32.jsonl"}
{"empty": true, "shard": "work-33.jsonl"}
{"empty": true, "shard": "work-34.jsonl"}
{"empty": true, "shard": "work-35.jsonl"}
{"empty": true, "shard": "work-36.jsonl"}
```

---

## 18. 判不准与缺口

- **次数口径**：本文件的“次数”是 D 行（finding）条数，不是独立事故数，也不按去重数计（去重后全文 236 条唯一 D，抱怨 220；精确到秒 237 条，抱怨 221）。若要“独立事故数”，需按会话+时间窗合并；本轮没有做，因为合并规则会改变每类排序，应由核验阶段统一定义。
- **H 类的处理**：原 H「口径反复变动」214 条 = E 180 + L 17 + D 17。E 是他自己改规则，不是抱怨，已整体移出；L 不计入抱怨；剩 17 条 D 的内容是 agent 对“可发／有效／该排除”的判据出错或收得过紧，故改名为「有效/可发判据失误与过度排除」。原 H 正文里的 6 组“旧说法→新说法”对照表随之删除——那 6 组的主语都是他改口径，属于 `CORRECTIONS.md` 的范围。
- **插件判定**：覆盖只判到 5 个研究侧 SKILL.md 的文档指令层，未对照服务端实现。发信、投递、回复监控、素材排版四类没有对应实现，标“不适用（外呼）”；线程归属（F）5 个文件未涉及，标“没有”。
- **未纳入**：work 分片的 C/G/K 里可能有同一事故的 agent 侧证据（如 `work-W37.md` 的 328 封超出批准 19 封），本轮按口径未采；核验时若要证伪“虚报完成”，应回 work 分片取 agent 自述。
- **隐私**：已替换人名/邮箱/机构名/聊天 ID；`B001`/`PL01`/`EG10`/`R8`/`W01` 等内部批次与线程代号保留，用于回原文。
