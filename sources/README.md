# sources/ —— 业务原始材料冻结快照

## 1. 这是什么

`sources/` 是 **MarketOps 使用的业务原始材料的冻结快照**。

冻结 = 里面的字节自写入之日起不再变。修错、补材料、换口径，都不是改已有文件，
而是**追加**一条新的 snapshot 记录（见 §8）。历史 provenance 一旦写下就是事实，
覆盖它等于把「我们当时读的是哪一版」这件事删掉。

快照里**只有业务材料**。技术研究、第三方项目、论文、博客、工具文档都不属于这里 ——
它们属于 `docs/research/`（见 §6）。

## 2. 权威在哪

**`ProfessorZhi/upstream-records` 是长期原始档案权威。** `sources/` 不是第二权威。

```text
upstream-records            = 上游权威（飞书 archive，持续同步）
sources/               = 本仓库自包含的冻结快照（一次性、可校验、不再变）
```

为什么要有一份快照：让 canonical repo 能**脱离 upstream-records 检出**被独立理解与
独立测试。一个必须旁边摆着另一个仓库才能跑的仓库，不是一个独立可理解的仓库。

两者的关系由 `manifest.yaml` 固定：每条 snapshot 都记着它的
`upstream_path`、`upstream_blob_sha` 与自己的 `snapshot_sha256`。
上游检出不在这里时，快照的**自身完整性**仍可被校验（哈希对文件）；
上游检存在时，还可额外校验**它确实是上游的子集**。

## 3. 不允许人工改写业务原文

`sources/` 下的每一行、每一个字节都是上游的**逐字节副本**：

* 三人群 `messages.jsonl`：完整复制，快照 sha256 **必须等于**上游 blob 的内容 sha256；
* 图片与 `.docx` 附件、SOP 正文：整文件原样复制，同样是 sha256 相等；
* 三份私聊 `marketops-messages.jsonl`：**取出来的行**，每一行都是上游对应行的
  逐字节副本 —— 不补字段、不删字段、不重排、不改标点。取法有两种，都在 manifest 里
  写明了：按**关键词**筛（`keyword-relevant`）与按**时间边界**取整段
  （`segment-relevant`）。

**二进制附件不做文本层加工**：`.docx` 里出现的内部域名会保留原值，而同一个域名若落在
**文本字段**里会被上游脱敏管道换成 `<redacted-*>` 占位符。这是**选择而不是漏做** ——
改附件字节会破坏 `upstream_blob_sha` 的可回溯性（改完的字节对应不上上游那个对象，
「这是它当时的样子」这句话就没了）。代价是：`tests/` 里那条凭据形状扫描对二进制
**没证明力**（`.docx` 是 zip，它扫的是压缩后的字节），二进制的凭据结论来自上游
archive 的脱敏记录，不是来自本仓库的扫描。

所以：**不要在 `sources/` 里做任何编辑。** 看到错别字、看到飞书的
`<redacted-*>` 占位、看到已经过期的「链路暂停」状态 —— 那都是原文的一部分，
改掉它这份快照就不再是证据了。

（`<redacted-*>` 占位符是上游 archive 的脱敏结果，不是本仓库加的。
凭据、token、cookie、私钥一律不进任何仓库 —— 这一条在 `sources/` 上同样生效。）

## 4. `docs/` 是从 `sources/` 派生的 projection

```text
sources/   ← 原始证据（authority 的本地快照）
docs/      ← 从原文派生的产品规格（projection，不是 authority）
```

`docs/SOURCES.md` 是这份派生关系的索引：哪一条产品结论出自哪一份原始材料。
`docs/` 里写的东西如果和 `sources/` 冲突，**以 `sources/` 为准**，
并应当把 `docs/` 改过来（记一条修订说明，不要静默抹掉）。

## 5. `profiles/` 是从 `sources/` 的 SOP 编译出的执行规则

```text
sources/party-group/sop/uk-natural-grass-acme-sop-v1.md   ← SOP 原文
        ↓ 编译
profiles/uk-acme-natural-grass/{profile,grading,role-matrix}.yaml ← 可执行规则
```

**SOP 才是 Source of Truth。`profiles/` 下的 yaml 是编译产物，不是它的替代品。**

这条区别是硬性的，因为它规定了改错的方向：

* 规则要改 → **先改 SOP 原文**（在 `upstream-records` 侧），再重新编译到 `profiles/`；
* 不要只改 `profiles/` —— 那会让编译产物与原文分叉，而分叉之后没人会发现，
  因为两边都「看起来对」。

同理，任何时候都**不要**把 `profiles/` 的 yaml 当作「SOP 原文」来引用。
yaml 里出现的、SOP 里没有的内容，是编译时混进去的东西，应当被当成缺陷查。

## 6. `docs/research/` 是技术研究，不是业务需求来源

```text
docs/research/   ← 外部技术研究与实现选型（Serper / Crawl4AI / MinerU / Playwright /
                   第三方开源项目 / 论文 / 技术博客 / 架构方案对比）
```

这些**不能放进 `sources/`**：它们不是 business source。

两类材料的区别不是「重要不重要」，而是**它们能改变什么**：

| | 能改变 | 不能改变 |
|---|---|---|
| `sources/` 业务材料 | 产品边界、分级口径、角色矩阵、合规约束 | —— |
| `docs/research/` 技术研究 | 用哪个库、怎么实现、性能取舍 | **SOP 定义的业务边界** |

## 7. 外部技术资料不能修改 SOP 业务边界

这是 §6 的操作化表述，也是本项目最容易犯的一个错：

> 「Crawl4AI 能拿到这个页面的内容」→ 于是把原本排除的页面纳入范围。
> 「Serper 能返回 snippet 里的邮箱」→ 于是让 snippet 邮箱进了候选集。

技术能力不是业务授权。SOP 写的硬排除、literal 邮箱要求、不绕过约束，
不会因为某个工具能做到而被放开 —— 放开它需要**改 SOP**，而改 SOP 是业务决定，
不是一个技术选型决定。

## 8. refresh 规则

1. 上游更新后，**追加**一条新的 snapshot 记录，不要原地改写已有条目的
   `upstream_blob_sha` / `snapshot_sha256` / `message_ids`。
2. 新条目的 `source_repository_commit` 记下当时 `upstream-records` 的 HEAD。
   **这个值是逐条目解析的**：条目自己带它就用它自己的，没有才回退到
   `manifest.yaml` 顶层那个。顶层那个是**第一条快照的 pin**，不是「所有条目共用的
   一条上游」—— 把它当成后者，后来追加的条目会被拿去和一条更早的上游比，
   于是校验报「blob 已变」（按第 1 条的处置：追加新条目），而真实处置是「这一条没写
   pin」。两种处置相反，所以这两件事不能合并成一个值。

   这个值还必须是**上游远端可达**的（不只是本机读得到）：pin 的用途是让别人能
   `checkout` 到我们当时读的那一版，一个只活在本机某个 ref 上的提交做不到这件事。
   这个形态**在拥有该对象的机器上没有任何信号**（实测：另三条上游对照在旧 pin 下
   照样全绿），只在别人的检出上变成「声明的 commit 不在检出里」——一句关于配置的
   报文。判据
   `tests/test_sources_snapshot.py::test_upstream_entry_pins_are_reachable_from_the_remote`。
   上游若**改写**了一个从未推出的提交（`upstream-records` 2026-09-23 做过一次发布面投影），
   处置是把 pin 迁到承载**同样字节**的那个后继提交，并把旧值记进
   `manifest.yaml` 头部 —— 移 pin 不是改写历史：§8 第 1 条禁改的是
   `upstream_blob_sha` / `snapshot_sha256` / `message_ids`，不含 pin，而
   「当时读的是哪一版」从会 dangle 的机器字段挪到不会 dangle 的散文里。
   豁免恰好一个值：顶层那个历史回退值（它的不可达是被记录过的缺陷）。
3. 旧的 snapshot 文件保留在原处，除非确实要替换 —— 那就同时留下旧条的条目
   并把新文件放到带版本后缀的路径，而不是覆盖同一路径。
4. **本仓库不反向写上游。** 补下载缺失附件、重新抓取 SOP、重新脱敏，
   都属于 `upstream-records` 侧的 archive 工作。
   `manifest.yaml` 的 `missing_attachments` 记录已知缺口，供那边补。

## 9. 目录

```text
sources/
├── README.md                                  本文件
├── manifest.yaml                              每条快照的 provenance
├── member-a-private/
│   └── marketops-messages.jsonl               Member-A 私聊中与本项目直接相关的消息
├── member-d-private/
│   └── marketops-messages.jsonl               Member-D 私聊 —— 零命中，零字节（见 manifest）
├── member-c-private/
│   ├── marketops-messages.jsonl               Member-C 私聊 —— 2026-09-20 起的整段（67 条）
│   └── files/
│       ├── 01-us-golf-demo-email-template.docx
│       ├── 02-france-football-codex-email.docx
│       └── 03-ds-garbled-output-screenshot.png
└── party-group/
    ├── messages.jsonl                         三人群完整原文
    ├── sop/
    │   └── uk-natural-grass-acme-sop-v1.md
    └── files/
        ├── 01-sop-generation-session.jpg
        └── 02-codex-task-inventory.jpg
```

`member-d-private/marketops-messages.jsonl` 是**零字节**，这是查过之后的结果，不是遗漏：
376 条按同一套关键词全量扫描，0 条命中本项目主题（该私聊的实际内容是另一条业务线
LiteLLM 的上下文）。理由与逐条判读记录在 `manifest.yaml` 的 `selection_note` 里。
零字节比写一条「我检查过了」的合成记录更诚实 —— 后者会破坏
「每一行都是上游原文」这条不变量。
