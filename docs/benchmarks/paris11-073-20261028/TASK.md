# Paris 11e 室内体育协会 · plugin 0.7.3 smoke 跑（题目与方法）

`references/benchmarks.md` 里的 Step 1 smoke：一个区、一个细分、至多 20 家机构、从零开始。
本题是该 smoke 的第 7 次跑，插件的 0.7.3 是第一次带 `sources_take_turns` / `possible_chains` /
`FLOOD` / `calls.jsonl` 的版本。数字见 `RESULT-073.md`，过程日志见 `process/`。

| | |
|---|---|
| 插件版本 | 0.7.3（合并提交 `e6551891`，发行 tag `marketops-v0.7.3`） |
| 日期 | 2026-10-28，13:52–14:38 北京时间 |
| 主机 | 东京节点（`fj-cvm-jp-prod-it`） |
| 团队 | lead（本会话主模型）+ 3 个 worker，均为 `haiku` |
| 项目 | `bench-paris11-0.7.3-20261028` |
| 花费 | 供应商侧 US$0.166 |
| 产出 | 328 机构 / 67 场馆 / 670 条联系人 / 12 条 A/B 路线 |

---

## 1. 简报（实际下发的全文）

```json
{
  "goal": "people who run, manage or decide on indoor sports venues - venue and facilities managers, sports department heads, club presidents",
  "country": "FR",
  "language": "fr",
  "region": "Paris 11e arrondissement (75011)",
  "scenario": "sports associations and clubs of one district (indoor: basketball, handball, volleyball, badminton, table tennis, gymnastics, combat sports, dance, fencing, climbing): presidents, secretaries, facility leads - at most 20 organisations from at least 3 sources, no more than 8 from one",
  "pilot": 5,
  "max_per_source": 8,
  "address_rules": {"info": "X", "admin": "X", "book": "X"},
  "strict_ledger": "AB",
  "fresh": true,
  "fresh_since": "2026-10-28T05:52:50+00:00"
}
```

## 2. 与文档标准简报的四处偏差（读数字前必看）

`references/benchmarks.md` 第 24–31 行给的标准 smoke 简报是上面这份**去掉 `max_per_source`
和 `fresh_since`**。这四处偏差决定了本轮读数与基线表里的六行**不能逐列直接比较**：

| # | 偏差 | 后果 |
|---|---|---|
| 1 | 简报多了 `max_per_source: 8`，这一键不在标准简报里 | 每个数据源研究到 8 家即封顶，4 个源合计 31 家；sweep 通道随后“空”而 299 家未读。**这是本轮的实验上限，不是规划器缺陷。** |
| 2 | 用了 3 个 worker（finder / miner / researcher），文档写的是 2 个（sources+pool、research） | `source` 通道的 6 个任务被拆成两条线；除人力外读数不受影响 |
| 3 | `MARKETOPS_HOME` 是共享库，靠 `brief.fresh=true` 隔离，文档要求空 `MARKETOPS_HOME` | `project()` 报 0 个 brought-in 源，但页面/检索池是热的（99,525 页 / 45,107 次检索），复用会压低成本读数 |
| 4 | serpapi 两把 key 全部耗尽（余额 0），TinyFish 余额 US$0.26 | 没有 maps 检索；finder 只能走区级登记册，这一轮的源质量因此偏“区级册子” |

另外 `max_per_source` 之所以出现：开跑时 lead 只发了一个键想补它，而 `project(brief=...)`
是**整体替换**，把 goal/region/fresh 一起抹掉了，补回时把这一键留在了完整简报里。这条缺陷记在
`RESULT-073.md` §4.1。

## 3. 跑法

1. `project(name, brief=...)` → 建项目，无预置源、无预置池、无 playbook。
2. 起 3 个 `marketops-worker`，全部 `haiku`，后台：`finder`（find 通道）、`miner`（source 通道）、
   `researcher`（sweep 通道）。
3. 中途 lead 读 `flow.sources_take_turns`，按 pilot 结果下 `sources(set=...)`：
   Mairie 11e（8/4，50%）与 OMS 11e（5/5，100%）标 `scale`，MVAC（5/1，20%）标 `dropped`。
4. 两个通道都空后，lead 调 `grade`（Jev + 规则）与 `export`（工作簿 + `RETRO.md`）。

## 4. 判据

用 `references/benchmarks.md` 的 Step 1 Pass（the chain）七条，逐条对照见 `RESULT-073.md` §2。
