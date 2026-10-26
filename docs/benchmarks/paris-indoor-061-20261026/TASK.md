# 法国巴黎室内体育场馆 · 公开联系路线（0.6.1 开发期题）

第三道题。出题人给的是**一段话**，不是一份规格：范围限定在巴黎市，产品侧要的是
“高 ROI 的触达联系人邮箱，具体到岗位和个人，排除 info / admin / book 这类工单或前台信箱”。

## 1. 原始需求（逐字）

```text
@Vince.Huang 麻烦调研一下法国巴黎所有室内体育场馆的名单，然后找到最适合的，高ROI的
触达信联系人的邮箱，确保邮箱具体到岗位和个人，排除info，admin，book这一类工单或者前台邮箱。
```

本题按城市级收口（开发阶段）：**只到巴黎市（INSEE 75，20 个 arrondissement），不含大巴黎其他省**。

## 2. 与另外两道题的关系

| # | 题 | 级别 | 规模 | 作用 |
|---|---|---|---:|---|
| 1 | 瑞士户外天然草足球场 | 语料族切片 | 44 家 | 新族，能不能达标 |
| 2 | 英国运动场/学校（回归） | Bruce 5 批 = 全族 5.4% | 344 账户 | 同题与 0.6.0 比 |
| 3 | **法国巴黎室内体育场馆** | 城市 | 150 家（巴黎全量 738） | 换语言、换机构类型、池子从官方登记重建 |

## 3. 名单从哪来（数据源）

法国教育部体育设施普查（RES，`data.education.gouv.fr`，数据集
`data-es-recensement-des-equipements-sportifs-et-lieux-de-pratique-complet`）：

- 取数条件：`dep_code="75" AND equip_nature="Intérieur"`
- 巴黎室内设施记录 **1,787 条**，按 `inst_numero` 聚成 **738 家机构**
- 每家机构记录：名称、地址、邮编、arrondissement、业主类型、设施族、设施数、是否需要公众开放、
  名录自带官网（128/150 有站点）

开发期样本：按“设施数 + 是否有泳池 + 是否公众开放 + 有无官网 − 学校”打分，取**前 150 家**做第一波，
其余可追加进同一 `orgs.csv` 续跑（`run` 可续）。

## 4. 分级契约（不同于 Bruce 默认口径的两处）

| 档 | 判据 |
|---|---|
| A | 具名人员 + 岗位 + 真实工作邮箱三者在同一官方记录里绑定，且岗位能决定或直接运营该场馆 |
| B | 岗位专属信箱（`responsable.gymnase@`、`regie.piscine@`、`direction.sports@` 之类），无姓名也可，但必须岗位绑定 |
| C | 场馆业务入口：service des sports、réservations / location d'installations、secrétariat sportif，只作路由兜底 |
| D | 无岗位绑定的官方通用入口（`mairie@`、`administration@`），仅研究资产 |
| X | 按要求排除：`info@` / `admin@` / `book@` / `booking@` / 前台 `réservation` / `standard@`；通讯、市场、HR、财务、招生、票务、会员；no-reply 与私人邮箱；拼造地址；纯室外 / golf / 非体育设施 |

两处偏离 Bruce 默认：① **info/admin/book 从研究资产降为 X**（出题人明确要求）；
② A 与 B 都要求“岗位绑定”，不再允许“相关官方部门信箱”混进 A/B。

## 5. 指标

与瑞士、英国两题同口径，另加两项本题特有的：

1. 每百机构 A+B（= A+B 路线数 ÷ 机构数 × 100）
2. A+B 机构数 / 占比
3. 具名联系人数（`contact_name` 非空）
4. C / D / X / 未找到
5. 覆盖率（有 A–D 任一合格路线的机构 ÷ 机构数）
6. **被拦截的工单/前台信箱数**（命中了 X 的 info/admin/book 条数——这是本题的核心要求，必须单列）
7. 精度（逐条四项核对：字面存在、同块绑定、岗位在口径内、未被排除）
8. 成本、耗时、人工插话次数

## 6. 对照组

`paris-indoor-venues-20261023`（rc12，2026-10-23）在同一城市跑完过一次：

| | rc12 |
|---|---:|
| 场馆 | 709 |
| 客户 | 129 |
| 路线 | 379 |
| 具名 A/B | 46（进表 42） |
| `ab_per_100` | 29.2 |
| 花费 | US$ 0.219 |

跨版本对比只在**同城、同数据源、同口径**这三条同时成立时成立：本题池子从 RES 重建，与 rc12 同源，
但样本是前 150 家（rc12 是 726 个池目标里完成 144 个），对比时要把分母差异写清楚。

## 7. 数据边界

- 逐机构明细、页面镜像、联系人表只放东京本机：`/data/home/vince/MarketOps/v0.6.1/paris-indoor-061-20261026/`
- 名单与源数据：`/data/home/vince/artifacts/marketops/bench/v0.6.1/061-test-plan/france-source/`（`master-institutions.csv`、
  `res-fr-indoor.json`、`france-wave1.csv`）
- 本文件与同目录 `RESULT-061.md` 不含任何第三方姓名、邮箱、电话。
