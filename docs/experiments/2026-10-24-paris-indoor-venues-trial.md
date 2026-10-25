# 巴黎室内场馆真实任务试跑（2026-10-24 收口）

一次真实的客户型 Run，用 rc12 插件在一台 Linux 部署上从零跑到底：把巴黎（75）的室内体育场馆重建出来，
再给能决定或运营这些场馆的人找到**具名、岗位绑定的工作邮箱**，排除 `info@` / `booking@` 一类公共信箱。
这不是合成 fixture：每个候选都来自真实抓取的公开页面，每次检索都走了真实计费通道。

本文件记录**本轮做了什么、怎么验的、结果是什么、哪里没做到**。它不含任何第三方的姓名或邮箱——
交付物本身留在仓库之外（见 §7）。

| | |
|---|---|
| Run | `paris-indoor-venues-20261023` |
| 范围 | Paris, France (75) — 室内体育场馆 / 场馆运营方 |
| 简报 | `brief-30bf5bd44b8fa07bd736f266`，`minimum_ab: 50` 名具名 A/B |
| 插件 | `marketops-research-assistant` 0.5.11-rc12（手工 cache 安装） |
| 交付 | `Artifacts/.../exports/paris-indoor-venues-20261023/handoff-paris-20261024-r4/` |
| 花费 | **$0.219**（Run 累计，估算，见 §5） |
| 结论 | 交付可读可用，**但未达 50 名人的简报下限**，且 handoff 拒绝定稿 |

## 1. 结果

交付九表（XLSX + 8 张 CSV + `证据索引.csv`）+ `MANIFEST.json` + `client-view/` + `contact-pack/`，
`generated_at` 2026-10-23T18:14:16Z（北京时间 2026-10-24 02:14）。

| 表 | 行数 |
|---|---|
| 场地与资产 | 709 |
| 客户总表 | 129 |
| 联系人路线 | 379 |
| 人员岗位 | 106 |
| 排除与待确认 | 510 |
| 证据索引 | 3,771 |
| 来源与查询 | 2,303 |
| 本轮说明 | 42 |
| 候选数据审计 | 5,914 |

入库记录 5,914（其中 active 5,607），准入 1,323 条、拒绝 392 条。`evidence_hash_readback: ALL_OK`，
证据链接 4,294 条，无无出处记录。

**具名 A/B 人数：Run 内 46 人，实际进表 42 人。** 两个数都离 50 有距离，不能合并报成 50。

| 分母 | A | B | C | D | X | 合计 | 具名 A/B 人 |
|---|---|---|---|---|---|---|---|
| Run 内（实体去重后） | 4 | 44 | 311 | 138 | 73 | 570 | **46** |
| 进表（准入）路线 | 4 | 38 | 203 | 61 | 72 | 378 | **42** |

库里 683 条 `contact_point`，线上分级按 `entity_key` 去重后是 570 条：多出的 112 条是被后来版本
取代的旧记录，全部落在 C/D，去重不影响 A/B 和人数。直接把库里 683 条全部重判会得到
682 条、C 377 / D 184——那是把被取代的版本又算了一遍，人数仍是 46。这个坑先记在这里。

差的 4 人不是没找到，是**组织没进表**：`Mairie du 13e` 名下两条路线（两个不同人）、`Paname Boxing Club`、
`Cercle du Marais`。这三条组织记录是在 `ddf95f64` 之前抽的，卡在身份闸
（`IDENTITY_VALUE_NOT_IN_EXCERPT` 134 条、`MISSING_REQUIRED_IDENTITY` 94 条）上。
用修好的抽取器重抓这三页，42 → 46 是机械可得的；再往上才需要新矿脉。

`ab_per_100 = 29.2`（分母 144 个已完成的池目标），不是 50%——这个指标本身也说明场馆类目标的产出密度低。

## 2. 测试结构

三层，互相独立，失败要能分开报：

**① 结构单测（无网络、无 provider、无凭据）**。本轮改动 6 个文件，69 项全绿：

| 文件 | 项数 | 钉住的行为 |
|---|---|---|
| `tests/test_extract_directory.py` | 14 | 名录页一次读成组织/人/地址；每条引文都是页面的规范化子串且 12–600 字符；`field=identity` 能被闸接受；人物与地址的引文同时含机构名（`ORGANIZATION_PROVENANCE_MISMATCH`）；纯散文被拒；分页不切断一条记录 |
| `tests/test_jev_page_work.py` | 7 | 名录拆分的归属、Jev 只在候选间选、不确定就留空；标签尾部裁剪（`Association Tsunagari Discipline(s)` → `Association Tsunagari`）；街道与时段不会被当成人名/机构名 |
| `tests/test_workflow_pool.py` | 10 | 池目标的已处置状态与来源建档 |
| `tests/test_reading_site_harvest.py` | 13 | http-only 站点回落、Wix 占位地址、法式五对电话号码、目标自带时的跳筛 |
| `tests/test_workflow_long_research.py` | 24 | 长研究的预算面与 continue/stop 判别 |
| `tests/test_workflow_replay_essex.py` | 1 | 回放体积上限随「可检索」的资助状态变化 |

**② 离线重判（read-only）**。把库里 `records` 读出来、用 `scripts/research_grading.py` 原样重算，
与线上 MCP 的 A/B 面逐格对齐（A 4 / B 44 / AB 46 完全一致）。这一步的价值是：分级规则可以本地改了再验，
不必每改一次就烧一次 provider 调用。

```python
import sqlite3, json, sys
sys.path.insert(0, "<plugin>/scripts")
import research_grading as g
c = sqlite3.connect("file:<data>/state/research-lite.sqlite3?mode=ro", uri=True)
rows = c.execute("select record_id,entity_key,kind,payload_json,artifact_ids_json "
                 "from records where run_id=?", ("paris-indoor-venues-20261023",)).fetchall()
recs = [{"record_id": rid, "entity_key": ek, "kind": k, "data": json.loads(p),
         "artifact_ids": json.loads(a) if a else []} for rid, ek, k, p, a in rows]
brief = next(r["data"] for r in recs if r["record_id"] == "brief-30bf5bd44b8fa07bd736f266")
grades, _ = g.grade_routes(recs, brief)
```

**③ 活路径**。真实 MCP 动词 + 真实 provider + 真实公开站点：`marketops_search` / `site_harvest` /
`harvest_batch` / `directory_extract` / `next` / `method` / `handoff`。只有这一层能证明来源裁决与准入闸
在真数据上成立；①②不能替代它。

## 3. 本轮改了什么（5 个提交）

| 提交 | 一句话 |
|---|---|
| `8736dbce` | 名录页一次调用读成候选：`research_extract.py` + `marketops_directory_extract`，被 139 条名录堵死的地方变成能力 |
| `8756cc6b` | 资助中的 Run 不再读成「预算耗尽」：红线型金额策略（line − spent）不再 KeyError 掉进「未声明的额度 = 0」 |
| `bdf92bd6` | 体积上限跟着检索能力走，不再靠「什么都搜不了」来凑 |
| `35eb4d42` | 活捉的四个自站缺陷：https 抬升让 http-only 站点不可达、Wix 示例地址被当成真路线、五对电话号码读不到、目标自带时还被筛掉 |
| `ddf95f64` | 名录页的引文同时承载机构名与人/地址；渲染列表粘上的标签词被裁掉 |

第 2 条是本轮最贵的一课：**规则说「预算耗尽即停」，而线上告警说的耗尽是个假零**。
`marketops_costs` 同时显示 $0.219 已花、两个免费额度还剩 2405/2500 与 166/250——
若照告警停机，这次 Run 会在有余额时自杀。修在代码里，但**运行中的 MCP 仍持有旧模块**，
所以线上 `next` 还会说 `BUDGET_EXHAUSTED_NO_SEARCH`，直到会话重启。这条要按「未加载」记，不按「已修」记。

## 4. 全过程：哪条矿脉真的产出

来源裁决（`source_yield`，单位是「本 Run 完成的池目标」，A+B 记该组织有 A 或 B 路线）：

| 来源 | 完成 | 有 A/B | 待处理 | 裁决 |
|---|---|---|---|---|
| `paris-committee-organigrammes` | 8 | 2 | 0 | **PROMOTE** 0.25 |
| `idf-federation-committees` | 10 | 2 | 0 | **PROMOTE** 0.20 |
| `idf-handball-committees-v2` | 5 | 1 | 0 | **PROMOTE** 0.20 |
| `paris-universities-suaps` | 15 | 1 | 0 | SMALL_DONE 0.067 |
| `bulk-0f862e1f…`（俱乐部源） | 30 | 0 | 0 | **STOP** 0.00 |
| `bulk-cfc740fb…`（俱乐部源） | 33 | 0 | 0 | **STOP** 0.00 |
| `oms-paris-arrondissements` | 4 | 0 | 0 | SMALL_DONE |
| `venue-groups-city-…` | 20 | 0 | 0 | SMALL_DONE |
| `paris-indoor` 四个批量源 | 19 | 0 | **707** | PILOT |

能稳定产出具名岗位的只有**委员会/联盟的名录页**：一页上同时印出机构、姓名、职务、邮箱。
手球 IDF 联盟的技术团队页一次 19 条记录、12 个具名邮箱，两个「Directeur」直接升 A；
CD77 的 organigramme 给出主席/副主席的个人邮箱；natation 的 annuaire 稳定给 B。

量化否掉的（都留有页），不是猜的：

- **商业室内场馆**：Neoness 9 家门店、CrossFit Louvre、Arkose、Climbing District、Block'Out、Casa/Urban Padel
  ——只印 `accueil@` / `contact@`，没有一个具名决策人。
- **basketidf**：人物页有名字无邮箱，联系页有邮箱无名字，两页拼不起来。
- **排球 / 击剑 / 体操 / 柔道 / 田径 / 足球联盟**：organigramme 只有姓名没有邮箱，或整页是 PDF/图片，
  或站点不可达。
- **`handball75.fr`**、`comite-handball95.fr/bureau…`：名录是图片。
- **Yvelines 交互式俱乐部地图**：写入 38 条，全是俱乐部总邮箱，X 因此升到 73。
- **`ROLE_SEARCH` 探针（「directeur de salle」）**：返回餐馆与招聘页——法语里这是餐厅岗位，不是场馆岗位。
  阶梯把它当下一步，实测不是线索源。

## 5. 花了什么

| provider | 调用 | 估算 |
|---|---|---|
| deepseek | 126 | $0.0945 |
| serper | 95 | $0.0950 |
| typesafe_jev | 1,464 | $0.0295 |
| crawl4ai | 320 | $0（本地） |
| serpapi | 84 | $0（免费额度） |
| tinyfish | 109 | $0（免费额度） |
| **合计** | 2,198 | **$0.219** |

免费额度余量（本数据目录口径）：serper 2,405/2,500，serpapi 166/250。Jev 占了 2/3 的调用数，只占 13% 的钱。

## 6. 没做到的，以及为什么

`marketops_handoff(mode="final")` 拒绝定稿：

```
status: CHECKPOINT_RESEARCH_NOT_FINISHED
finalization_refused: RESEARCH_OR_LOCAL_FINALIZATION_REMAINS
next_action: CONTINUE_OR_RESUME_TARGET_POOL
pending_pool_targets: 707
```

707 = `paris-indoor` 四个批量源剩下的目标（139 + 178 + 186 + 204），全部是 Data ES 的场馆行，
本身没有网站——它们的「运营方是谁」不在任何一页上，只能逐个去查。

其他如实记下的缺口：

- `goal_assessment.status: MORE_RESEARCH_REQUIRED`，`goal_satisfied: false`；
  `coverage_complete: false`，`business_verified: false`，`send_authorized: false`。
- `evidence_assessment: EVIDENCE_GAPS`——`DERIVED_EXCERPT_ONLY` 96、`NO_SOURCE` 39、
  `MISSING_REQUIRED_EXCLUSION` 39、`EMAIL_NOT_IN_PRIMARY_MATERIAL` 7。
  供应商清洗后的 HTML 不等于原始字节，引文匹配也不等于业务核实。
- `client-view` 状态 `CANDIDATE_NOT_APPROVED`，未授权外发。
- `evidence_envelope_export_gate: BLOCKED_LEDGER_NOT_WIRED`。
- 线上 MCP 仍是旧模块，`BUDGET_EXHAUSTED_NO_SEARCH` 与三条身份闸组织都只能等重启后复验。

## 7. 证据与复现

交付目录（**不在仓库内**，含第三方姓名与工作邮箱）：

```
/data/home/vince/artifacts/marketops/rc9-local-20261023/data/exports/paris-indoor-venues-20261023/handoff-paris-20261024-r4/
```

- `Research_Result.xlsx` — 2,068,282 B，`sha256:67563c38…936e13`
- `MANIFEST.json` — 5,520,337 B，全部文件哈希与准入决策逐条在案
- 证据库：`…/data/state/research-lite.sqlite3`；3,771 份保存材料；`evidence_hash_readback: ALL_OK`

复现单测：

```sh
for t in test_extract_directory test_jev_page_work test_workflow_pool \
         test_reading_site_harvest test_workflow_long_research test_workflow_replay_essex; do
  python -I tests/$t.py
done
```

这些测试不需要网络、provider 凭据或真实数据根；活路径那一层需要真实部署，不可在 CI 复现。

## 8. 2026-10-25 续跑：重抓一页，交付反而变差（一次可量化的失败）

重启加载 rc12 之后，把 mairie 13 的名录页重抓、重抽并 stage 进同一个 Run（309 条），
再重建一份快照 `handoff-paris-20261025-r5`。结果**比 r4 差**，这是本轮最有价值的一次测量：

| | r4（10-24） | r5（10-25 重抓后） |
|---|---|---|
| 记录 | 5,914 | 6,252 |
| `contact_point` | 683 | 797 |
| 分级路线（客户视图） | 570 | **456** |
| A / B / C / D / X | 4 / 44 / 311 / 138 / 73 | 4 / 43 / 293 / 43 / 73 |
| 具名 A/B 人 | 46 | **45** |
| `entity_version_conflict` | 1 | **84** |

多写了 309 条记录，客户表里少了 114 条路线和 1 个 B——**库变大，交付变小**。

### 为什么

`research_workflow.active_records` 对每个 `(kind, entity_key)` 只接受**一条覆盖全组的
supersedes 链**（`_tip`）。组里有两个没有父记录的根，就是分叉：**整组都不进客户表**，
只记一条 `entity_version_conflict`；组织被拿掉后，挂在它下面的联系路线跟着
`ORGANIZATION_NOT_ADMITTED` 一起消失。

当天的 stage 造出了两个分叉来源：

1. **一页里同一个机构印了多次**。mairie 13 的名录里 `Paris Université Club` 出现 22 次
   （22 个分部），抽取器就写了 22 条 `organization` 记录，`entity_key` 全是同一个——
   22 个根，不是 22 个版本。
2. **重读一页写成第二个根**。`record_id` 带 artifact 盐之后不再撞库，但新记录没有
   `facts.supersedes`，于是和两天前的旧记录并列成了两个根。

实测分叉组 305 个：`organization` 84、`person` 107、`contact_point` 114；其中 **304 个是当天
stage 造出来的**。r4 只有 1 个，是 10-23 那轮手工补过 supersedes 之后剩下的。

### 修在哪里（`3b1e37b7`）

- `records_to_bundles`：**一页内每个身份只 stage 一次**。多出来的分部仍然 stage 自己的人与
  地址（它们的差异就在那里），机构行本身是同一个机构。被合并的读数在返回里计数
  （`duplicate_entity_readings`），不是悄悄丢掉。
- `propose_batch`：写入前查该身份的活版本（`store.live_versions`），**新读数声明
  `facts.supersedes=<当前 tip>`**——存储层文档里写明的更正路径；`record_id` 加父记录盐，
  免得和自己替换的那条撞 id。已经分叉的组**报出来、不动它**：挑一个赢家正是投递层拒绝做的猜测，
  再加一个根只会让这组继续消失。
- 同一个 artifact 重复 stage 仍然幂等：内容相同、artifact 相同就原样留下，不会变成「自己的版本」。
- 四条新测试钉住：一页两处分部 → 2 个机构 3 个人 3 条路线；重读 → `active_records`
  零冲突且 tip 带新 artifact；重复 stage → 9 条 `IDEMPOTENT`、记录数不变。

### 这个 Run 修不回来了

记录只追加、`_tip` 又要求**一条覆盖全组的链**，所以分叉没法靠再追加一条记录救回：
一条记录只有 `supersedes` 一个父，替掉两个根会剩下零个 tip，仍然是冲突。今天重抓的
`Paname Boxing Club`、`Cercle du Marais` 两页恰好也在这 304 个分叉里，所以 §1 里
「重抓三页 42 → 46」这条路径**在当前 Run 里已经不可达**。

- **r4 仍然是交付的准绳**（`handoff-paris-20261024-r4`，§7 的哈希不变）。
- **r5 只作为测量存档**，不对外，也不拿来替代 r4。
- 修复对**下一个 Run** 有效：那个 Run 里没有历史，一页一个身份、重读接在 tip 后面，
  不会再有分叉。要走这条路，需要在新的数据目录重建 Run 并重放已保存的材料，
  代价明显大于 +4 个人，留作选项而不是下一步。

## 9. 下一步（按性价比）

1. **重抓能不能救回 42 → 46：在本 Run 里已经不成立**（§8）。抽取器那一层是修好的
   （重抓 mairie 13 的 annuaire 给出 124 个组织，124/124 的身份引文能过投递闸；
   两天前这一页的记录被判 `IDENTITY_VALUE_NOT_IN_EXCERPT`），但**版本链**这一层当时没修，
   重抓把被救的对象本身写成了分叉。要验证这条路径，得在新 Run 里跑。
2. 若要 50：不是继续在场馆官网里翻，而是**再找 4 个委员会/联盟名录页**（本项目唯一稳定的矿脉），
   或者由需求方给源清单。
3. 707 个无网站场馆目标，先别按目标逐个啃；先把「谁运营这个场馆」变成可检索的字段（场馆→运营方映射），
   否则每个目标都是一次独立的搜索赌博。
