# Bruce 工作过程深挖：多 agent 工作流

在东京本机（持有 Bruce 语料的机器）由本地 Claude Code 编排执行。目标：把 Bruce 做调研时的**需求、提示词、卡点、困扰、对 agent 的不满、纠正、有效方法**系统挖出来，喂给插件的 skill（`marketops-*`）和 `references/projects/`。

已有的 `marketops-session` 分析（156 份 L1、12 份 L2、GOLDEN_PATTERNS、干预图谱）是**起点和索引**，不要从零重扫；本工作流补的是它没有系统化的部分：Bruce 本人的声音（原话、提示词、不满）和逐市场的方法与数字。

---

## 1. 要拿到的信息（12 类）

每条发现都要带：`category`、`project_line`（fr-grass / uk / us / de / ch / golf / other / cross）、`locator`（会话 id + 行号或 part）、`quote`（原文，≤200 字，已脱敏）、`count` 或数字（有就写）、`confidence`（read / inferred）。

| # | 类别 | 要什么 | 例子 |
|---|---|---|---|
| A | **需求与目标** | 每个项目他要什么：卖什么产品、什么场景、交付什么表、KPI、截止时间；需求怎么变的 | "一个国家一个应用场景"；"5 小时内完成我的所有工作" |
| B | **提示词与 SOP** | 他写给 agent 的开场提示词、任务书、KICKOFF / CHAIN_CONTRACTS、他自建的 skill（SKILL.md）、固定规则；整理成可复用模板 | 派单铁律、四禁（不反推、不补全、不 SMTP、不表单） |
| C | **卡点** | 哪里停住、停了多久、怎么解开的：技术（WAF、环境、编码）、流程（交接、等待）、口径 | "跑一晚上没有新增，我怀疑你停止了" |
| D | **对 agent 的不满** | 每一次抱怨、质疑、纠正：agent 做错了什么、他怎么说的、要求怎么改 | "我也不知道你哪个数据是真的"；"不要一直汇报/不要让我点继续" |
| E | **口径纠正** | 规则旧→新、原因：有效邮箱、A/B/C/D、谁是客户、什么算完成 | "打总机不算有效，必须明确指人" |
| F | **有效与无效的方法** | 数据源、页面类型、查询写法、每个来源的样本产出率、为什么停掉 | 30 家样本 ≥10% 扩 / <5% 停 |
| G | **工作编排** | 线程怎么拆、批次大小、并发、心跳、交接方式；什么让它变快、什么让它变慢 | 4 worker 反而从 13 分钟拖到 24 分钟 |
| H | **交付物** | 表结构（列）、格式、飞书同步、客户接受/打回了什么 | 32/33 列表、MAINTENANCE_MODEL 必填 |
| I | **岗位与分级** | 各市场被评为 A/B/C/D 的岗位词（当地语言）及次数；他亲手改过的分级 | Athletic Director = P1 |
| J | **投入与成本** | 每个项目的轮次、用户消息数、token、费用（402 预算墙）、每批耗时 | 25 账户 19.33 分钟 |
| K | **工具使用** | 用了哪些工具/供应商、各自的失败形态和绕法 | CDP 2–2.5 域/分 vs 静态 100+/分 |
| L | **他想要的自动化** | 他反复要求"别让我…"、希望系统自己做的事；可直接变成插件功能的 | 自动退出要自恢复；完成一批自动下一批 |

---

## 2. 编排（6 个阶段，约 55 个 agent）

```
S0 索引 ─> S1 抽人声 ─> S2 并行深读(40) ─> S3 按业务线归并(8) ─> S4 专题综合(3) ─> S5 核验(4) ─> S6 落盘+PR
 1 agent    脚本         voice 16 + work 24     fr/uk/us/de/ch/golf/other/cross   不满/提示词/编排     抽样/隐私/对账/口径
```

### S0 · 索引（1 个 agent，单遍，≤15 分钟）
- **只流式扫一遍**语料，建 `index.jsonl`：会话 id、来源（codex/workbuddy）、业务线（按 cwd + 标题 + 已有 L1 归属）、起止时间、用户消息数、轮次、字节数。
- 复用 `marketops-session/derived/`（`thread-inventory.csv` 按唯一 thread_id、`dialogue-index.csv`、`l1-index.csv`），不重算已有的。
- 产出分片计划 `shards.jsonl`：按 token 估算切，每片 ≤ 25k token（Read 上限是按 token 不是按行，1,500 行会被拒）。
- 教训（SELECTION.md §7.5）：**一条记录 = 一行账户数据，不是转录里的一个物理行**；重复的工具输出要去重。

### S1 · 抽取 Bruce 的声音（脚本，不用 LLM）
- 从每个会话抽出**真人用户消息**，写 `voice/<session>.md`（带行号）。
- 排除：`<task-notification>`、`<automation_system_reminder>`、跨线程 `codex_delegation` 信封、纯"继续/好的/跑吧"（单列计数，不删）。
- 同时抽出他写的长文本：KICKOFF、任务书、SKILL.md、SOP（`prompts/<session>-<n>.md`）。
- 这一步决定 A/B/D/E/L 的质量：**人的原话是金子，agent 的复述不算**。

### S2 · 并行深读（40 个 worker，队列领取）
两类 worker 从同一个队列领片，每片做完立刻落盘 `findings/<shard>.jsonl`（**强制增量落盘**，跑一小时零产出是 Bruce 语料里的已知失效）：
- **voice worker × 16**：读 `voice/` 和 `prompts/`，产出 A 需求、B 提示词、D 不满、E 纠正、L 想要的自动化。每条不满要配上"agent 前一轮做了什么"（回读上下文 ≤30 行）。
- **work worker × 24**：读 agent / 工具段，产出 C 卡点、F 方法、G 编排、H 交付物、I 岗位、J 投入、K 工具。数字必须能落到行号；推断的标 `inferred`。

规则：
- 一条消息里一次派完同一批 worker（分多条消息派会退化成串行）。
- 每个 worker 一次只领一片，领片写 `claim`，30 分钟没回执自动释放给别人。
- worker 不改别人的文件、不改语料、不联网。
- 遇到"本片无相关内容"也要落一行 `{"empty": true}`，否则无法区分没做和没有。

### S3 · 按业务线归并（8 个 agent）
fr-grass、uk、us、de、ch、golf、other、cross（跨项目）各一个：
- 合并同一事实的多次出现（按 locator 去重），保留最早和最强的证据。
- 数字冲突**并列不裁决**，写明两个口径和各自出处（Bruce 语料里 882 vs 718 这类长期并存）。
- 产出 `lines/<line>.md`，按 `references/projects/README.md` 的模板分节，外加"需求演变"和"不满与纠正"两节。

### S4 · 专题综合（3 个 agent）
- **agent 不满与人机分工**：把 D/E/L 归成类型（虚报完成、口径乱跳、空转等待、越权/顺手修、要人点继续、方向跑偏、不认错……），每类：次数、典型原话、他要求的改法、插件现在是否已覆盖。
- **提示词与 SOP 库**：把 B 整理成可复用模板（开场、任务书、交接、验收、停线），标出哪些规则被他反复重下（说明没有承载面）。
- **速度与卡点**：C/G/K 汇总成"什么让调研变慢、什么让它变快"，每条带前后数字。

### S5 · 核验（4 个 agent）
- **抽样回查**：每条业务线随机抽 10% 的发现回原文核对 quote 和数字，错误率 >10% 的分片打回重做。
- **隐私扫描**：所有要进 Git 的文件扫 `@`、6 位以上数字串、人名对、电话格式；第三方的机构名、人名、邮箱、电话、账户号一律不得出现。
- **数字对账**：分项和 = 总数、并集 ≥ 子集、声明 = 实测（GOLDEN_PATTERNS GP-13）。
- **口径检查**：时长同时报"首尾跨度"和"轮次时长"；计数报唯一键，不报行数。

### S6 · 落盘与 PR
- 进 Git（开 PR 到 `main`）：
  - `plugins/research-tools/marketops-research-assistant/references/projects/*.md` 的补充；
  - `docs/benchmarks/bruce-knowledge/` 下：`NEEDS.md`、`PROMPTS.md`、`PAIN-POINTS.md`、`AGENT-COMPLAINTS.md`、`CORRECTIONS.md`、`METHODS.md`、`WORKFLOW-LESSONS.md`、`WISHLIST.md`（L 类，每条写"插件现状：已有 / 部分 / 没有"）。
- **只留东京本机**：`index.jsonl`、`voice/`、`prompts/`、`findings/`（含原文，不进 Git）。
- PR 描述写：覆盖了多少会话（分母）、每类多少条、核验错误率、没覆盖的部分及原因。

---

## 3. 约束

- 只读语料；不发任何东西；不碰飞书和邮箱。
- 硬时限：S0–S2 合计 ≤ 2 小时，全流程 ≤ 4 小时；到点交部分结果并写清缺什么。
- Bruce 本人的原话可以引用（脱敏后）；第三方信息永不进 Git。
- 不重复扫 11 GB 原始语料：S0 之后所有阶段只读索引指向的片段。
- 发现与 `marketops-session` 现有结论冲突时，并列写出，不覆盖。
