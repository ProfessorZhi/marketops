# 2026-10-28 · Paris 11e smoke 跑 · 过程证据

| 文件 | 内容 |
|---|---|
| `TASK.md` | 简报全文、四处与标准简报的偏差、跑法、判据 |
| `RESULT-073.md` | 数字、Pass 逐条、质量、发现的 7 个缺陷、下一轮要改的 |
| `process/RETRO.md` | 插件在 `export` 时自己写的复盘（数字 + 每阶段等待 + provider 读数 + journal） |
| `process/calls.jsonl` | 127 条工具调用：时间、工具、agent、参数（按 `logged()` 的掩码规则）、耗时、成败 |
| `process/journal.jsonl` | 4 个 agent 的过程 journal（blocker / smooth / decision / idea / retro） |
| `process/costs.jsonl` | 每次供应商调用的花费（动作 + 查询词，无结果正文） |
| `process/trace.jsonl` | 逐机构的分阶段耗时与是否在该阶段拿到 A/B |

## 脱敏口径

本仓库是公开的脱敏快照（见仓库根 `REDACTION.md`），而这一轮是真实跑，产物里带着第三方
（巴黎 11 区各体育协会的负责人）的**个人邮箱、手机号与姓名**。因此 `process/` 下的五个文件
做过一次替换：

| 对象 | 替换为 |
|---|---|
| 邮箱地址 | `<email>` |
| 法国电话号（`0X XX XX XX XX`、`+33 …`） | `<phone>` |
| 第三方人名（名字 + 姓，如会长、秘书） | `<person>` |

`calls.jsonl` 本身由插件写时就已把参数掩码（简报只留键名、列表只留长度），
所以这轮替换在它里面只命中一处 `tasks(note=...)` 的正文。

校验：替换后对五个文件重扫，邮箱 0、电话 0、已知姓氏 0。

## 没有上传的东西（以及为什么）

跑测的交付物**不进公开仓库**，因为它们逐条带着真实的个人联系方式：

| 文件 | 内容 | 为什么不上传 |
|---|---|---|
| `contacts.csv` | 670 条联系人（223 条带邮箱、112 个电话） | 第三方个人数据 |
| `verdicts.csv` | 29 条机构裁决，正文含负责人姓名与获址路径 | 同上 |
| `orgs.csv` | 328 家机构（含 223 个邮箱、34 个人名列） | 同上 |
| `*.xlsx` / `*-routes.csv` | 工作簿与 Bruce 19 列交付表 | 同上 |
| `pages.csv` / `crawl.csv` / `phone*` | 页面缓存与抓取记录 | 体积 + 其中含地址 |

这些留在跑测主机 `/data/home/vince/MarketOps/v0.7.3/bench-paris11-0.7.3-20261028/`。
需要复核某条读数时，用同一份 `calls.jsonl` / `RETRO.md` 定位到机构 id，再到主机上查原始行。

## 复现

```sh
# 0.7.3，标准 smoke 简报（去掉 max_per_source / fresh_since）
# 见 TASK.md §2 —— 本轮多了 max_per_source: 8，数字与基线表不可直接比
```
