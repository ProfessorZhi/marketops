# MarketOps — 业务来源权威声明

本文件不是「来源说明」。它声明**谁是业务事实的权威、谁不是**，以及两边冲突时哪一边赢。

`docs/` 下其它文件回答「我们怎么做」；本文件回答「我们凭什么认为该这么做」。

## 1. 两个仓库的分工

| 仓库 | 存什么 | 不存什么 |
|---|---|---|
| `records` | 事实与原始材料：飞书聊天镜像、SOP 正文、需求正文与验收判据 | 产品设计、代码、架构 |
| `marketops`（本仓库） | 从那些事实**推导出来**的产品与代码 | 需求正文；原始材料（唯一例外是 `sources/`，见 §1.1） |

一句话判据：

```text
「Member-A 到底要什么？」   → records
「这个怎么实现？」       → 本仓库
```

本仓库不复制需求正文。需求正文只有一份，在 records 里。

### 1.1 `sources/` 的例外，以及它不是「第二权威」

`sources/` 里有原始材料的**冻结副本**（SOP 正文、几条私聊的行、被引用的附件）。它与上面
那一栏的关系是：**权威仍在 records，副本在这里，两者是同一件东西的两个位置，不是一个
东西的两个版本。**

- 副本是**逐字节**的（`upstream_blob_sha` + `snapshot_sha256` 各锁一头，见 §7）。
  本仓库里的这份**不得编辑** —— 要改就去上游改，然后按 `sources/README.md` §8 追加新条目。
- 它存在的原因是**自包含**：canonical 仓库要能脱离 upstream-records 检出被理解与被测试。
  一个「读得懂但要旁边有另一个仓库」的核心仓库，在 CI 与在别人机器上都不成立。
- 它**不包含**需求正文：`brief.md` / `card.yaml#source_refs` 指向的 dossier 仍然只在
  records 里。这条边界是 §4 的 `NO_SELF_REFERENCE` 的邻居，不是它的例外。
- 挂在上面的**识别用**字段（`file_key`、`upstream_path`、上游原始文件名）是 provenance，
  不是我们对材料的描述。判断材料说什么，读正文。

## 2. 四个 approved 业务来源通道

业务事实只有四个来源。它们都是飞书镜像归档，**身份键是 `chat_gid`**，人名/群名段只是
导航（见 `records` 的 `information/raw/README.md`）。

| 通道 | 覆盖什么 | records 中的位置（身份键） |
|---|---|---|
| **Member-A** | Member-A 的需求历史、业务背景、已有分析 | `information/raw/feishu/chats/people/member-a/` (`oc_00000000000000000000000000000001`) |
| **Member-D / Member-D** | 他个人给出的技术判断、工具经验与建议 | `information/raw/feishu/chats/people/member-d/` (`oc_00000000000000000000000000000003`) |
| **Member-C** | **产线实操者**给出的口径：母池生产（OSM → codex/dsh）、场景→最佳联系人、产品×场景推送规则、模型分档主张，以及她转发的两份开发信模板 | `information/raw/feishu/chats/people/member-c/` (`oc_00000000000000000000000000000002`) |
| **Member-A + Member-B + Member-D 三人群** | 三方直接讨论、最新共识、SOP v1 正文 | `information/raw/feishu/chats/groups/party-group/` (`oc_00000000000000000000000000000004`) |

Member-C 那条通道的入选是**第四条**，且它不是从上面三条推出来的 —— 她的口径有一部分
与 SOP **并列**（例如按场景分的角色清单、产品×场景的推送规则），有一部分**比 SOP 宽**
（她给的跨场景表里含高尔夫，而本期 SOP 的硬排除把高尔夫排除在本场景之外）。两者不一致
时哪一边赢，取决于问题问的是「本期范围」还是「产线通识」—— 这个区分写在各 profile 与
`docs/SOP_MAPPING.md` 里，不写在这里。

首个业务 profile 的全部约束来自**三人群那条通道**里的 SOP v1（按名字点它，不按序号 ——
「第 N 条通道」这种说法每次加通道都要改一遍，改漏一次就变成一句看着没问题的话）：

```text
.../oc_00000000000000000000000000000004/links/docx-research-outreach-sop/content.md
```

## 3. 推导规则

**业务需求只允许从这四个通道推导。** 核心系统不认识任何具体市场，本仓库也不认识任何
未经这四个通道确认的需求 —— 这是同一条纪律的两个面。

技术来源（GitHub 上的开源项目、SciSpace、官方文档、搜索结果）回答「**怎么实现**」，
**不得修改「Member-A 到底要什么」**。它不是需求来源，是手段来源。

要加第五条通道，需要两次显式改动，**两次都在 diff 里、都能被 review**：

1. `sources/manifest.yaml` 的 `approved_channels` —— 那是**定义点**，本文件 §2 的表与
   `tests/test_business_sources.py` 都从它读（见 §7）；
2. 本文件 §2 的通道表。

有意的部分不是「两次」，而是**没有第三次**：测试不再自己持有一份通道元组。它曾经持有
（`APPROVED_SOURCE_ROOTS` 是那份元组的派生量），于是通道集合有两个定义点，而第二个
「只要顺手加一行就够」—— 那句话是在一条**已经不同步的**指引里被读到的：它指的第二个
改动点早就不是定义点了。现在测试从 manifest 读，加通道时它自动跟上，`§7` 那条
「`docs/SOURCES.md` 必须声明每条通道」的判据会替它把第 2 步钉住 —— 漏改本文件会红。

## 4. 本仓库的 `docs/` 是 projection，不是 authority

对应 ServerOS 的 `NO_SOURCE_AUTHORITY` 不变量：

- `PRODUCT_SPEC.md` / `SOP_MAPPING.md` / `DATA_MODEL.md` / `ARCHITECTURE.md` / `POC_PLAN.md`
  **不产生业务事实**。它们做的是 REQ → 模块 的映射，以及技术上的取舍。
- 需求变更改 records，不改这里。这里跟着改，是**结果**，不是原因。
- 本仓库里的任何文件都**不是** `source_refs` 的合法目标（`NO_SELF_REFERENCE`）。
  一份投影不能是自己的来源。
- 本仓库文档里出现的每个 `MKT-xxxx` 都必须在本文件 §6 的索引里声明，
  并且在 records 里真的有对应 Item。**前一半是本仓库内部的事，后一半不是**（见 §7）。

## 5. 技术证据的合法去处

| 技术来源 | 可以进 | 不可以进 |
|---|---|---|
| Crawl4AI / MinerU / Playwright / Serper / pypdf / 论文 / 官方文档 | `docs/ARCHITECTURE.md`、`docs/POC_PLAN.md`、ADR | 任何 `card.yaml#source_refs` |

理由不是洁癖：**技术选型会变，Member-A 的需求不会因为 Crawl4AI 出了新版本而改变**。两者混在
一份文档里之后，「我们为什么做这个」和「我们打算怎么做」就绑在了一起，改一个必然动另一个，
而那时候没人分得清动的是哪一个。

`SOP_MAPPING.md` 的「文档解析策略」表（原生解析 / pypdf / MinerU fallback）是这一栏的
标准例子：它写的是**怎么解析**，不是**为什么要有这个产品**。

## 6. 需求索引

本表是**索引，不是需求**。正文、验收判据与依据原文在 records 里各自的 `brief.md` 与
`card.yaml#source_refs`。

本表同时是**默认校验的见证**：文档里出现的每个 `MKT-xxxx` 都必须在这一节里，
这条不需要 records 就能查（见 §7）。

| Item | 标题（导航用） | 落点 |
|---|---|---|
| `MKT-0001` | 联系人邮箱只取页面明文（literal only） | `marketops/domain/extract.py`、`marketops/domain/evidence.py` |
| `MKT-0002` | 抓取失败必须落 blocked，不得伪装成已研究 | `marketops/adapters/classify.py`、`marketops/domain/enums.py` |
| `MKT-0003` | 研究产出不得携带发信授权 | `marketops/domain/evidence.py`、`contracts/` |
| `MKT-0004` | 邮箱归属必须绑定目标账户 | `marketops/domain/bind.py`、`marketops/ports/store.py` |
| `MKT-0005` | 质量规则变更必须追加式修订，不得静默重新计数 | `profiles/<profile>/grading.yaml`、`marketops/application/grading.py` |

## 7. 这条规则怎么被守住

不靠自觉。`tests/test_business_sources.py` 是本节的可执行版本。

### 默认那半：只需本仓库

这些在任何 CI 上都能跑，不需要旁边有任何别的 checkout：

- 通道集合来自 `sources/manifest.yaml` 的 `approved_channels` —— 那是**定义点**，
  本文件与测试都从它读，不各自另立一份；
- 每条 `source_refs` 必须落在 approved 通道内，或使用能承载它们的 typed locator scheme；
- 技术依赖名不得出现在 `source_refs`；
- `source_refs` 不得指向本仓库（`NO_SELF_REFERENCE`）；
- fragment 必须是目标文件里**真实存在的标题** —— 坏锚点会失败，不会静默通过；
- `docs/` 里出现的 `MKT-xxxx` 必须在本文件 §6 的索引里声明；
- 通道之间不重叠，`check_ref` 的作用域恰好等于通道集合；
- 扫描器本身有阳性/阴性对照（见该文件里的 probe 测试）。

`sources/` 这份冻结快照的自身完整性（哈希对文件、声明与文件一一对应、
私聊行的 `message_id` 清单与实际行同序等长、零命中的那一条确实是零字节、
每条条目都解析出一个完整的 commit sha）由 `tests/test_sources_snapshot.py` 守，
同样不需要上游。

### 可选那半：需要 records 检出

拿上游权威再对一次：Item 卡片存在吗、`source_refs` 真指到上游文件吗、
§6 索引里的编号真立项了吗。数据来源：

1. 环境变量 `MARKETOPS_RECORDS_ROOT`；
2. 从本仓库根上溯，找同时含 `records/` 与 `src/` 的 workspace root；
3. 该 workspace 下的 `worktrees/records/*/`（dossier 常先落在 records 的一个分支上，
   只有那个分支的检出面才有 `projects/marketops/`）。

三者都找不到时**这三条 skip 并写明原因**，不是 fail。理由有两层：

- 默认那半已经把**本仓库内部的声明一致性**查完了，不是空跑；
- skip 会带着理由出现在报告里，不会被读成 passed。让一个普通 CI 因为旁边没有
  另一个仓库而变红，只会训练人忽略红色。

**但「上游在、需求却不在」仍然是 FAILED。** 判据是 `projects/marketops/items/`
目录存不存在：目录不在 → 上游这个检出里就没有这份 dossier（skip）；
目录在、一张 `card.yaml` 都没有 → 上游被人掏空了，接下来的断言会空跑通过（fail）。
空跑通过比失败更坏。

#### 另一半可选检查：`sources/` 快照对上游的对照

上面那半查的是**需求**（dossier 在不在）。`sources/` 冻结快照还有**它自己**的
三对上游对照 —— 快照确实是 `upstream-records` 的子集、blob 哈希确实吻合、解析后的定位符
真的可达。它的数据来源是**它自己的**变量：

1. 环境变量 `MARKETOPS_UPSTREAM_RECORDS_ROOT`；
2. 否则从本仓库根上溯，找含 `records/information/` 的 workspace root。

**两个变量是刻意的，不是历史遗留。** `MARKETOPS_RECORDS_ROOT` 回答「需求级不变量
用哪个检出」（找不到就 FAIL），`MARKETOPS_UPSTREAM_RECORDS_ROOT` 回答「快照的上游
对照要不要跑、对着哪个检出跑」（找不到就 SKIP）。一个变量兼两个决定时，本仓规范的
套件调用（把前者指向一个不存在的路径，好让需求级不变量脱开真实 records 的在场/缺席）
会**正好**让这半永远 skip —— 那三条对照因此从来没跑过（见 `docs/MVP_MIGRATION_PLAN.md`
§2.19）。点名的路径不是目录时它返回空、**不回退**到上溯。

这三条读的是**对象库**（`rev-parse <commit>:<path>` + `cat-file blob`），不是工作树，
因为 `manifest.yaml` 的声明全在 commit 空间。records 的工作树**可以**一直带着别人的
未提交 WIP，读工作树会让这三条红在一件与快照无关的事上。

**「哪个 commit」是逐条目解析的**：条目自己带 `source_repository_commit` 就用它自己的，
没有才回退到顶层的那个值。后加的条目按 `sources/README.md` §8 记下**当时**的上游 HEAD，
而顶层那个值是**第一条快照的 pin**；把它当成「所有条目共用一条上游」，会让后来追加的
条目被拿去和一条更早的上游比，于是报文说「blob 已变」（处置：追加新条目），真实的处置
却是「这一条没写 pin」。两种处置相反。这条机制自己也有判据（`test_sources_snapshot.py`
§6，用 `tmp_path` 造的仓证明条目 pin 真的压过顶层，不读任何真实 records）。

同一条纪律在 `docs/SOP_MAPPING.md` 里写作「拿不到，就写成拿不到」。
