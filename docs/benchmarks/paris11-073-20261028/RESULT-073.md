# Paris 11e 室内体育协会 · plugin 0.7.3 结果

smoke 第 7 次跑。题目与偏差在 `TASK.md`，原始过程日志（已脱敏）在 `process/`。
**读数字前请先看 `TASK.md` §2 的四处偏差**：本轮简报多了 `max_per_source: 8`，不是标准简报，
与 `references/benchmarks.md` 基线表里六行不能逐列直接比较。

## 1. 数字

| 指标 | 本轮 | 基线最好那行（0.7.3 `3622ca3`，11e+12e，7 人） |
|---|---:|---:|
| 用时 | 45 分 11 秒（其中 13 分空转，有效约 32 分） | ~20 分（提前停止） |
| 数据源 注册 / 有机构 / 标记丢弃 | 6 / 4 / 3 | 13 / 7 / — |
| 机构 | 328 | 279 |
| 场馆 | 67 | — |
| 已研究机构 | 31（Mairie 9、OMS 9、Paris Data 8、MVAC 5） | — |
| verdict 落盘 | 29 | 89 |
| 有确认 A/B 的机构 | 17（占 328 的 5.2%；占已研究的 55%） | 14 strict（占已研究 16%） |
| 严格账路线（`strict_ledger=AB`） | 20 条 / 17 家；**按值去重后 18 条 / 15 家**（§4.3） | 38 条 |
| 联系人行 A/B/C/D/未评/X | 13 / 19 / 7 / 77 / 274 / 280（共 670） | 38/37/3/127/571 |
| 覆盖率（有任一 A–D 的机构） | 26.5% | — |
| 待复核 A/B | 12 条 | 31 |
| 工具调用 / 错误 | 127 / **0** | 394 / 2 |
| 按 agent 的调用 | researcher 62、miner 29、finder 21、lead 12、未署名 3 | — |
| 供应商花费 | **US$0.166**（deepseek 0.1163、serper 0.030、jev 0.020；tinyfish 0.0） | ~US$0.20 |
| 每机构成本 | US$0.0005 | — |
| 抽检准确率 | **未测**（`check.checked = 0`） | — |

### 1.1 按数据源

| 源 | 池内机构 | 已研究 | 有 A/B | A/B 率 | 状态 |
|---|---:|---:|---:|---:|---|
| Mairie 11e – créneaux sportifs associatifs | 130 | 9 | 5 | 55.6% | scale |
| OMS 11e – annuaire des associations affiliées（PDF 2025-2026） | 25 | 9 | 9 | **100%** | scale |
| Paris Data – Occupation des créneaux sportifs | 78 | 8 | 2 | 25.0% | scaling |
| MVAC 11 – annuaire des associations sportives | 95 | 5 | 1 | 20.0% | **dropped** |
| RES Paris 体建设施普查 | 0 | 0 | 0 | — | dropped（`data.iledefrance.fr` 从本节点不可达） |
| OMS 每俱乐部 HTML 页 | 0 | 0 | 0 | — | dropped（与 OMS PDF 名册重复） |

`reconcile.adds_up = true`：328 家机构全部可归到一条登记过的来源，`without_logged_source = 0`。

## 2. Pass（the chain）逐条

| 判据 | 结论 | 依据 |
|---|---|---|
| ≥3 个源带 URL、机构归在其下 | ✅ | 6 个注册，4 个出机构，`reconcile` 对账成立 |
| 每个源的机构都研究过（没有“一个源停在 pilot 而另一个源已过 pilot”） | ✅ | 四个源都到达 `max_per_source` 上限（9/9/8/5） |
| 站点读到首页之后 | ⚠️ 部分 | 29 家读了站，17 家 `done`；11 家 `no_website`、1 家不可读。真正的高产页不是站点，而是区体育办公室的年鉴 PDF（§4.6） |
| 每个 worker 一直干到通道结束 | ❌ | 14:12 三个 worker 同时停手，3 个源任务开着、310 家未读，13 分钟后才被 lead 发现（§4.2） |
| 每个 worker 有 journal | ✅ | finder 6 条、miner 5 条、researcher 8 条、lead 6 条 |
| `RETRO.md` 写出 | ✅ | `process/RETRO.md` |
| 每条联系人都有引文 | ✅ | 670/670 有 `quote` |

七条里五条通过、一条部分、一条失败。失败的那条是**信号缺失**（通道空了没人说），
不是 worker 不干活——同一件事在 0.7.3 这一轮出现了两次。

## 3. 质量

- **A/B 命中 55%（17/31）看着很高，但不能当质量结论**：31 家里 9 家来自命中率 100% 的 OMS 年鉴 PDF。
  换成按 328 家算只有 5.2%，比基线那行低——因为基线那行的分母是 279 家，覆盖了 11e+12e 两个区。
- **12 条 A/B 待复核**：6 条“没记职务”（`president@` / `secretaire@` 这类角色箱），6 条 Jev 置信度低
  （p=0.35–0.57）。其中一对是**同一域名下的正拼与错拼**（`secret...@` 与换位后的 `secreteri...@`），
  两条都进了待复核——读页时把一个字母换了位，没有任何规则拦住它。
- **抽检没做**：`check.checked = 0`，交付门槛里的 ≥90% 精度这一轮没有读数。

## 4. 发现的缺陷（每条都能复现）

### 4.1 `project(brief=...)` 是整体替换，不是合并

lead 为了补 `max_per_source` 只发了这一个键，`brief.json` 被整体换掉，goal / country / region /
fresh 一起没了，`needs_you` 随即报 “The brief has no goal”。docstring 只写
“brief (optional, saved as brief.json)”，没有一个字说替换。
证据：`process/calls.jsonl` 05:52:47（`{"brief": {"keys": ["max_per_source"]}}`）与 05:52:50 两条相邻调用。

### 4.2 通道空了没有推送信号

`plan` 只在 `claim` 里跑，所以 `done` 之后立刻读 `tasks(status)` 看到的是 `open: 0`——对 worker 而言
“通道空”字面成立，于是收工。14:12 三个 worker 同时这样停下，而 3 个源任务开着、310 家未读。
lead 8 分钟后读 `tasks.csv` 才发现，净损失 13 分钟（35 分钟盒子的 37%）。
同一现象在 0.7.3 这轮出现两次（14:12、14:34），第二次是 lead 主动 `SendMessage` 才续上。

### 4.3 重复机构被报成“连锁”，而去重没有动词

六对同一机构：

```text
CODERS 75        org-b7ecefb4cd = org-e36f80f70e
AFSJ             org-d7c903340d = org-17d591ac19
Aïkido Club 11e  org-7030a26984 = org-3e140e48b0
K2A              org-4f9d73b66e = org-d6b1818a9c
EKS Paris XI     org-945c5c6958 = org-8933d95582
OXALIS           org-5aa843f104 = org-ec36bfd79b
```

`flow.possible_chains` 把这六对（其中三对带同一站点）标成“同一连锁的多家门店，请 lead 归到总部”。
实际它们是同一家机构的重复登记。照它做 `orgs(set=part_of)` 会把去重写成“总部 + 分店”，污染 `part_of`。
严格账里 20 条有 2 条是同一个人的第二次计数（`bertrand.mayet-tissot@`、`daniel.derrien4@` 各出现两次），
按值去重后是 18 条 / 15 家。

### 4.4 带 `org_id` 去读“列表页”，整页地址都记到那一家头上

researcher 用 `org_id` 读 `oms-paris11.org/documents/tableau.pdf`（区里的儿童体育工坊表），
一份名单把 8 家别家俱乐部的邮箱全挂到了 GRS Paris XI 名下；她逐条改判 X 才止住。
归属是在**读取时**决定的、不是**分级时**决定的，所以 FLOOD 闸（按条数 held）挡不住这种整页误挂。

反面证据：FLOOD 在 CODERS 75 那次是有效的——它的站是全国退休体育联合会名录，
一页 346 个各地俱乐部的邮箱，只放出 1 条 B（它自己的主席），61 条判 X，274 条 held。

### 4.5 `held` 的行会永久留在“未评级”里

那 274 条 held 没有任何后续动词能把它们清掉（`grade` 不给 held 行降级，`dig` 不认它们），
于是 670 行里 41% 是“未评级”，而主因只有一个机构的名录页。工作簿的总行数因此虚高。

### 4.6 池里的 `website` 列不可靠

至少三家挂错：ASCA → `sortiraparis.com`（一个演出/活动目录站，白读 15 页，真站 `batoncanne.com`
只在年鉴的网址列里）、GRS Paris XI → `paris.fr`、ASC 11 → 死域名。年鉴 PDF 有逐家的官网列，
一次核对能把一批修掉。

### 4.7 简报里的数字目标不可执行

`scenario` 写“at most 20 organisations”，但那是散文字；插件能执行的只有 `pilot` 与
`max_per_source`。本轮实际研究了 31 家，已超过 20，工具不会拦。

## 5. 这轮真正学到的东西（playbook）

已写进 `playbook.json` 的 `mairie 11e - créneaux sportifs associatifs` 段：

- 这个市场的产量不在俱乐部官网，而在**区级册子**：
  `oms-paris11.org/documents/BROCHURE_OMS_2026.pdf`（pp.42–47）逐家给出 siège / 官网 /
  président·e 姓名 / 手机 / 私人邮箱。18 家里 16 家靠它解决，俱乐部官网只给通用箱加电话。
- 取址顺序：册子 PDF > 俱乐部 `mentions légales` > 俱乐部 contact 页 > 市府 `paris.fr` 活动页
  （通用箱，常与主管俱乐部共用）。
- 联邦级俱乐部名录（FFTT / FFBad / FFBB 等）在这个区没有可爬的行列表，基本是 JS 地图；
  `associations-v2beta.paris.fr` 从本节点所有 reader 都挂，`assofinder.fr` 有 Cloudflare 挑战。
- Opendatasoft 门户：`/explore/dataset/<id>/export/?format=csv` 返回的是 HTML，
  可用的是 `/api/explore/v2.1/catalog/datasets/<id>/exports/csv?delimiter=%3B`；Paris Data 的
  v2 导出确实带 `telephone_du_contact` 与 `adresse_mail_du_contact`。
- 同一份时段文件值得入库两次：先 `into=venues group_by=association` 得场馆视图，再
  `into=orgs name=association` 把行内地址变成联系人。

## 6. 下一轮该改的

1. 用**标准简报**（去掉 `max_per_source`）重跑一次，才有能进基线表的行。
2. 去重要有动词：`orgs(merge=[...])` 或让 `possible_chains` 区分“重复”与“连锁”。
3. 通道空了要推信号（服务端事件或 `claim` 内的 plan），别让 worker 靠“读到 open:0”收工。
4. `held` 的行要么能被 agent 处理，要么不算进行数。
5. 带 `org_id` 读列表页时应拒绝或告警。
6. 抽检（`grade(set=[{checked}])`）要进流程，否则精度门槛永远是空的。
