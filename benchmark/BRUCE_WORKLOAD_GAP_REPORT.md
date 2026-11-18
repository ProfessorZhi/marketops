# Bruce Workload Gap Report

**范围**：只做只读审查，不改数据集，不阻塞本轮测试。
**语料**：`ProfessorZhi/marketops-session`（`origin/main`）的 6 份主文档 + `derived/bruce-distill-20261027/findings/*`。
**被审对象**：`benchmark/datasets/pilot` 的 24 条 Search + 28 条 Fetch（全部 `draft`）。

**先给结论**：现有 62 条用例覆盖的是**执行层形态**（页面类型、反爬、编码、假成功），这部分证据最硬；
明显不足的是**判定层**——运营主体归属、邮箱质量分级、字面抽取边界、来源 ROI 与停手。
如果只能补三类，补「运营主体归属」「A/B/C/D 与严格 ROI」「假成功验收」。

---

## 0. 证据强度的第一道筛子

6 份主文档不是检索任务日志，是干预/接管/审计文档。真实信息获取案例集中在 `findings/`。
因此本报告对每一条都标了证据等级：

| 等级 | 含义 |
|---|---|
| **强** | 有一手会话原文（Bruce 自己说的话或自己做的动作） |
| **中** | 有逐案记录，但是代理产出、Bruce 事后接受 |
| **弱** | 只有原则性叙述或聚合统计，没有逐案过程 |
| **无证据** | 全语料零命中 |

findings 内部也要分级：英国 / 法国 / 学校 / 高尔夫有 Bruce 一手话；农业 / 停车的客户定义多由代理提出、
Bruce 一句「按建议开始调研」接受（`agri.md` L7、L26、L217–219；`parking.md` 代理口径），证据强度低一档。

---

## 1. 当前测试集已经覆盖什么

### Search（24 条 / 9 个 task_type）

| task_type | 条数 | 对应 Bruce 模式 | 证据 |
|---|---|---|---|
| `official_website_discovery` | 4 | 组织 → 唯一官网 | 强（GP-17 L866–922） |
| `directory_association_discovery` | 3 | 官方名册 / 协会名录建池 | 强（`france.md` L70–84；`agri.md` L63–68） |
| `government_open_data` | 6 | Data ES 设施表、Companies House、IRS、NCES | 强（`france.md` L309；`golf.md` L95） |
| `contact_discovery` | 2 | 找联系页 | 强（`method.md` L85–96） |
| `local_venue_discovery` | 3 | 场馆 / 设施发现 | 强（`uk.md` L25–27） |
| `pdf_discovery` | 2 | 官方 PDF 来源 | 中（`uk.md` L52–55，仅英国线） |
| `long_tail_search` | 1 | 小机构 / 地方组织 / 冷门网站 | 强（`agri.md` L63–68） |
| `freshness_search` | 1 | 时效 | 弱（只有「最新附件整体替换」，`school.md` L55） |
| `social_media_discovery` | 2 | Facebook / Instagram | **无证据** |

### Fetch（28 条 / 10 个 task_type）

| task_type | 条数 | 对应 Bruce 模式 | 证据 |
|---|---|---|---|
| `static_html` | 10 | 官网正文 | 强 |
| `directory_page` | 4 | 名录 / 列表页 | 强（`uk.md` L187） |
| `js_rendered` | 3 | JS 重页面 | 强（GP-08 L456–460） |
| `cookie_consent` | 4 | 同意墙 / Cloudflare 前置 | 中 |
| `antibot_challenge` | 2 | 登录墙（Instagram / Facebook） | 登录墙**无证据**；CF challenge 强（GP-01 L50–101） |
| `pdf_text` | 1 | 有文字层 PDF | 中（PPOSS） |
| `redirect_chain` | 1 | 重定向 | 弱 |
| `broken_link` | 1 | 死链 | 中 |
| `pagination` | 1 | 翻页 | **无证据** |
| `empty_or_irrelevant` | 1 | 假成功 / 空壳 | 强（`FINAL-REPORT.md` L167–177） |

**覆盖良好的部分（可以直接出结论）**：静态页、目录页、JS 渲染、PDF 文本、假成功识别。
这五类在 Bruce 语料里都有逐案记录，本轮跑出来的差异可以直接采信。

---

## 2. 当前测试集没有覆盖什么

按「在 Bruce 工作量中的权重」排序：

### 2.1 运营主体归属（最严重）

Bruce 的去重单元**不是页面，是运营主体**：

- 同一 council / trust / leisure operator 的一封 `info@` / `bookings@` 挂在多个场馆上，必须归到运营主体
  （`uk.md` L25–27、L187）。
- 学校归学校本体 / 运营主体，不归学区（`school.md` L40–41、L178；`us.md` L52–53）。
- 法国按 Installation / Owner / Commune 聚合多行 venue（`france.md` L84、L309）。
- 经销商不是终端：农业线明确「自有挖机、反复做排水/土地改良的施工方」才算，经销商剔除
  （`agri.md` L11–13、L34–37、L136–142）。

现有 24 条 Search 里，**没有一条**测「一个主体有多个场地」或「这个页面的主体是不是我要采的那个」。
`local_venue_discovery` 只测「能不能找到场馆」，不测「找到之后归给谁」。

### 2.2 邮箱质量分级（A/B/C/D 与两本账）

Bruce 的分级不是「有没有邮箱」，是「这个邮箱能不能发」：

- A/B/C/D/X 五级 + 严格 ROI 与可发两本账；D 可发但不计严格 ROI（`method.md` §五 L100–143）。
- **总机不算有效**（`agri.md` L158–160、L203）；自动回复不算（L144–146）。
- 角色邮箱 `info@` / 共享公司邮箱单列 HOLD，不算可发直邮（`agri.md` L95–96）。
- 无邮箱就写 `NO_EMAIL_FOUND`，**不用 `info@` 顶替**（`school.md` L151–152、L179）。

现有 Fetch 的 `email_precision` / `email_recall` 只回答「抽到没有」，回答不了「抽到的这个算不算数」。
一个把总机号码当成功的 reader，在这套指标下和真正抽到直邮的 reader 得分一样。

### 2.3 字面抽取的保真边界

- 抽取必须字面：不解码混淆、不探测 SMTP、不提交表单（`method.md` L129–132；`france.md` L178–180）。
- 混淆邮箱（Cloudflare `data-cfemail`、JS 拼接、`[at]`）明令**不解码**（GP-09 L489）。
- mailto 修复使产出从 1.3% 提到 45%（GP-09 L487–496；TR-X01 L154）——**mailto 丢失是最大单点损失**。

现有用例里，`expected_emails` 全为空（24 条 search + 28 条 fetch 的 gold 里没有一条给了期望邮箱），
所以「抽得对不对」这一格本轮**算不出来**，只能算「抽到了几个」。

### 2.4 假成功的判定口径

Bruce 的验收明确：不看 `completed` / `exit 0` / 状态字段，看字节数 + `source_ref` + SHA-256 + 邮箱数是否为 0
（`FINAL-REPORT.md` L167–177）。典型假成功：

- 占位符 339 条、空值、减法回填、只建 schema（同上 L167–177）
- gzip 声明但 urllib 不解压 → HTTP 200、字节正常、邮箱 0，8 个大城市全中（GP-09 L498–502）
- UTF-8 BOM 把首列读成 `U+FEFFprogram_id`（GP-11 L603–607）
- 空壳 / 哨兵值需换口径复验（GP-12 L630–676）

现有只有 1 条 `empty_or_irrelevant`（w3.org/mission）和 2 条 `antibot_challenge`。
`shell_page_false_success_rate` 这个指标只在 `expected_outcome == "no_content"` 的用例上有值——
也就是**全轮只有 3 条**在测假成功。这个分母太小，无法区分 reader。

### 2.5 人 + 角色 + 邮箱的同页绑定

角色、姓名、邮箱、页面 URL、原文引句必须同页绑定（`method.md` L106–111；`uk.md` L191；`school.md` L176；
`us.md` L156–159）。这是「可发」与「可审计」的分界。

现有 gold 里 `expected_persons` 全为空，`contact_person_accuracy` 本轮恒为 `null`。
Bruce 工作量里权重最高的一条判定，本轮**没有观测值**。

### 2.6 角色词按线分化

「角色词检索」不是通用模式：

- 美国 K12 / 市政：Athletic Director、Grounds Manager、Facilities、Parks & Recreation（`us.md` L144–159）
- 停车：五职能测试（`parking.md` L75–96）
- 法国：三语角色词（`france.md` L124–128）
- 英国：**明确「给页面类型，不给角色词」**（`uk.md` L74–92、L214–218）
- 高尔夫 / 农业：**零角色词**（`golf.md` L133–137、L265–266；`agri.md` L87、L217）

现有 24 条 Search 里没有一条测角色发现。这是 brief §4F 点名要补的。

### 2.7 来源级 ROI 与停手

Bruce 会按来源算 ROI 然后停手：NPA 26.67% 继续、FDOT 3.33% STOP、IPMI 9.80%、
泛目录 40→0（`parking.md` L57–72；`method.md` L146–161）。

这是**跨用例**的指标（一个来源下 N 条用例的命中率），现有 benchmark 是逐用例打分，没有「来源」这个轴。

### 2.8 反爬分级与回退

Cloudflare / JS challenge / 403 / 401 / 429 / 503 / 空壳页都有；21/26 会话遇到，跨 7 国
（GP-01 L50–101、GP-09 L473–517；TR-X15 L168；`FINAL-REPORT.md` L249）。
处置是「先轻量（会话预热 + 真实 header + 低频），CDP 仅在证明必要时用」，CDP 实战 96% `ConnectionResetError`
后弃用（GP-08 L456–460、GP-09 L487–502）。

现有 2 条 `antibot_challenge` 用的是 Instagram / Facebook 首页——那是**登录墙**，Bruce 语料里没有登录墙案例。
真正有证据的是 Cloudflare 403 与 JS challenge。

### 2.9 编码 / 传输

gzip 不解压、UTF-8 BOM、GBK 控制台 `UnicodeDecodeError`、PowerShell stdout 被吞 76+ 处
（WB 23/26、Codex 16/25）（GP-09 L498–502；GP-15 L773–822；TR L281）。

现有 28 条 Fetch 里没有一条是「HTTP 成功但内容解析错」的用例。这类问题在现有指标下会表现为
「text 很短」或「email 0」，被算进普通失败率，无法与「站点真的没有内容」区分。

### 2.10 一处需要修正的认知

`pagination` 在 Bruce 语料里**零命中**——他的批量来自名册 / 目录整表下载，不是逐页翻。
`social_media_discovery` 同样零命中（唯一相邻证据是 `docs/runs/paris-all-090-20261029.md` 的 LinkedIn，
不在本次语料内）。

这两类用例**可以保留**（它们是通用 Web 能力），但不能声称代表 Bruce 工作量。

---

## 3. 哪些 case 直接来自 Bruce 真实工作模式

| case | Bruce 模式 | 出处 |
|---|---|---|
| SRCH-FR-OFFICIALWEBSITE-001/002、GB-013 | 组织 → 唯一官网；官网覆盖率低于阈值拒绝整源 | GP-17 L866–922；`golf.md` L113–116 |
| SRCH-FR-DIRECTORY-004、GB-016 | 官方名册 / 协会名录建池 | `france.md` L70–84；`agri.md` L63–68 |
| SRCH-FR-OPENDATA-005/006、US-020/021/023 | Data ES 设施表、IRS、NCES | `france.md` L309；`golf.md` L95 |
| SRCH-FR-COMPANY-007、GB-014 | SIRENE / Companies House 实体解析 | `golf.md` L95 |
| SRCH-FR-CONTACT-003、US-024 | 联系页发现 | `method.md` L85–96 |
| SRCH-FR-VENUE-008、GB-017、US-022 | 场馆 / 设施发现 | `uk.md` L25–27 |
| SRCH-FR-LONGTAIL-012 | 小机构 / 地方组织 / 冷门网站 | `agri.md` L63–68 |
| SRCH-FR-PDF-009、GB-018 | 官方 PDF 来源 | `uk.md` L52–55 |
| SRCH-GB-CHARITY-015 | 慈善登记册 | 间接（`uk.md` 名册逻辑） |
| FECH-*-STATIC-* | 官网正文 | 强 |
| FECH-*-DIRECTORY-* | 名录 / 列表页 | `uk.md` L187 |
| FECH-*-JS-* | JS 重页面 | GP-08 L456–460 |
| FECH-*-COOKIE-005/006/015/026 | 同意墙 / Cloudflare 前置 | GP-01 L50–101（中） |
| FECH-FR-PDFTEXT-008 | 有文字层 PDF | `uk.md` L52–55（中） |
| FECH-FR-EMPTY-010、US-EMPTY-027 | 死链 / 空壳 | `FINAL-REPORT.md` L167–177 |

**不属于 Bruce 高频任务、只是通用测试的**：SRCH-FR-FRESH-010（时效，弱）、SRCH-FR-SOCIAL-011、
SRCH-GB-SOCIAL-019（社交，无证据）、FECH-GB-PAGINATION-017（翻页，无证据）、
FECH-GB-LOGIN-018/019（登录墙，无证据）、FECH-FR-REDIRECT-009（重定向，弱）。

---

## 4. 下一轮应该新增哪些 Search cases

每条都带 `provenance` / `source_ref` / `workload_pattern`。**不凭空生成业务案例**，
全部从 findings 里的真实案例改写，机构名与 URL 换成同类的公开对象。

| # | case_id 建议 | task_type | 测什么 | provenance | source_ref | workload_pattern |
|---|---|---|---|---|---|---|
| S1 | `SRCH-FR-MULTISITE-025` | `organisation_multi_site` | 一个运营主体名下 ≥3 个场馆，Search 能否返回多个独立资产而不是只有总部 | bruce-distill | `uk.md` L25–27、L187 | 运营主体扇出 |
| S2 | `SRCH-FR-ENTITY-026` | `wrong_entity_rejection` | 名字相似但不是目标机构（同城同名协会） | bruce-distill | GP-17 L883–891 | 错实体判定 |
| S3 | `SRCH-FR-DEALER-027` | `terminal_vs_dealer` | 经销商 / 代理 vs 终端客户 | bruce-distill | `agri.md` L11–13、L136–142 | 终端识别 |
| S4 | `SRCH-FR-ROLE-028` | `role_discovery` | 「谁负责这件事」：facilities manager / grounds manager / director | bruce-distill | `us.md` L144–159；`france.md` L124–128 | 角色发现 |
| S5 | `SRCH-FR-ROLE-029` | `role_discovery` | 法国三语角色词（responsable / directeur / président） | bruce-distill | `france.md` L124–128 | 角色发现（法语） |
| S6 | `SRCH-GB-NOROLE-030` | `page_type_over_role` | 英国线：给页面类型而非角色词，能否收敛到 Contact 页 | bruce-distill | `uk.md` L74–92、L214–218 | 页面类型优先 |
| S7 | `SRCH-FR-REGISTER-031` | `registry_bulk` | 整表下载而非逐页翻（CSV/XLSX 导出） | bruce-distill | `france.md` L309；`agri.md` L63–68 | 名册整表 |
| S8 | `SRCH-FR-WAF-032` | `antibot_search` | 目标站在 Cloudflare 后的检索降级 | bruce-distill | GP-01 L50–101 | 反爬分级 |
| S9 | `SRCH-US-SCHOOL-033` | `org_vs_district` | 学校本体 / 运营主体 vs 学区 | bruce-distill | `school.md` L40–41、L178；`us.md` L52–53 | 归属层 |
| S10 | `SRCH-FR-FRESH-034` | `freshness_search` | 用「最新附件」判断池子是否作废 | bruce-distill | `school.md` L55；TR-D09 L123 | 时效（补证据） |
| S11 | `SRCH-FR-TENDER-035` | `procurement_notice` | 地方采购 / 招标公告（BOAMP 类） | 主文档 | `FINAL-REPORT.md` L308–318；GP-06 L325 | 长尾（证据弱，标注） |
| S12 | `SRCH-FR-PDF-036` | `pdf_discovery` | 含联系方式的官方 PDF | 主文档 | GP-01 L76–81 | PDF 来源 |

**不建议新增**：更多 `social_media_discovery`。语料零命中，再加只会把「通用能力」伪装成「Bruce 工作量」。
若产品侧确实要 Facebook，应单列 `facebook_profile_discovery` 并明确它**不代表 Bruce 高频**（brief §4I 的要求）。

---

## 5. 下一轮应该新增哪些 Fetch cases

| # | case_id 建议 | task_type | 测什么 | provenance | source_ref | workload_pattern |
|---|---|---|---|---|---|---|
| F1 | `FECH-FR-MIRROR-029` | `website_mirror` | 首页 → About → Contact → Team → Facilities → Services 连续 6 页，每页都要出 | bruce-distill | `method.md` L85–96；`france.md` L164–205 | 网站镜像 |
| F2 | `FECH-FR-CONTACT-030` | `contact_page_fields` | 同一页取 姓名 + 角色 + 邮箱 + 页面 URL + 引句 | bruce-distill | `method.md` L106–111；`uk.md` L191 | 人+角色同页 |
| F3 | `FECH-FR-MAILTO-031` | `mailto_fidelity` | 页面只有 `mailto:` 链接、正文无明文；丢了就是 1.3% 那一档 | bruce-distill | GP-09 L487–496；TR-X01 L154 | 字面抽取 |
| F4 | `FECH-FR-OBFUSCATED-032` | `obfuscated_email` | Cloudflare `data-cfemail` / `[at]` / JS 拼接；**期望 no_content 或原样保留，不解码** | bruce-distill | GP-09 L489 | 抽取边界 |
| F5 | `FECH-FR-GZIP-033` | `encoding_gzip` | gzip 声明但正文不解压 → HTTP 200 + 邮箱 0 | bruce-distill | GP-09 L498–502；`FINAL-REPORT.md` L175 | 编码 |
| F6 | `FECH-FR-BOM-034` | `encoding_bom` | UTF-8 BOM 首列被读成 `U+FEFF...` | bruce-distill | GP-11 L603–607 | 编码 |
| F7 | `FECH-FR-CF-035` | `cloudflare_challenge` | Cloudflare JS challenge（真 challenge，不是登录墙） | bruce-distill | GP-01 L50–101；TR-X15 L168 | 反爬分级 |
| F8 | `FECH-FR-EMPTY-036` | `empty_shell` | 200 + HTML 存在 + 只有导航/占位符 | bruce-distill | GP-11 L609–620；GP-12 L630–676 | 假成功 |
| F9 | `FECH-FR-WRONG-037` | `wrong_entity` | 200 + 内容完整 + 但不是目标机构 | bruce-distill | GP-17 L883–891 | 假成功（错实体） |
| F10 | `FECH-FR-PDFSCAN-038` | `pdf_scanned` | 扫描件无文字层；当前全系统必败，单列 | 主文档 | `PROVIDER_INVENTORY.md` §3.8 | PDF 硬化 |
| F11 | `FECH-FR-PDFTABLE-039` | `pdf_table` | 多页 PDF 含表格（联系方式表） | bruce-distill | `uk.md` L52–55 | PDF 硬化 |
| F12 | `FECH-FR-BROWSER-040` | `browser_network_denied` | 浏览器起得来但 `ERR_NETWORK_ACCESS_DENIED`，另一浏览器可读 | 主文档 | GP-08 L456–460、GP-09 L491–496 | 浏览器网络（诚实标注：无逐案证据） |

**需要降权或改写**：`FECH-GB-PAGINATION-017`。语料零命中，建议改写成「目录整表下载」
（`download` reader 的主场），或保留但标注「通用能力，不代表 Bruce」。

---

## 6. 一处必须写进下一轮标准的东西：来源级 ROI

Bruce 的停手判据是**来源级**的（NPA 26.67% 继续、FDOT 3.33% STOP、IPMI 9.80%、泛目录 40→0；
`parking.md` L57–72；`method.md` L146–161），而现有 benchmark 是逐用例打分。

下一轮应在 `scorecard` 里加一个按 `source_ref` / `domain` 聚合的块：

```
per_source: {source: {cases, useful_rate, ab_share, stop}}
```

否则「这个来源值不值得继续采」这个问题，在现有指标下永远答不出来。

---

## 7. 本轮不做什么

- 不改现有 62 条用例的任何 gold 字段去迎合 provider（brief §2、§11）。
- 不为「覆盖 Bruce」而临时生成没有出处的新案例（brief §22）。
- 不把 `social_media_discovery` / `pagination` 的得分当作 Bruce 工作量的证据。

---

## 8. 试点跑完之后追加的两条量具发现

这两条不属于 Bruce 缺口，但会直接改变下一轮怎么读分，所以记在这里。

**（1）金标的 URL 语义压低了所有 provider 的命中率。**
24 条 search 用例里多数 `relevant_urls` 写的是站点根（如 `https://annuaire-entreprises.data.gouv.fr/`），
而排序结果指向深层页面（`/etablissement/92968211000010`）。`benchmark.runner.metrics.normalize_url`
明确保留路径，于是「同一站点、更具体的页面」不算命中。四家一起被压，所以横向比较仍可用，
但绝对值和「谁找到了官方来源」这类结论都不能拿。**下一轮应把 gold 的口径改成
「域名 + 可选路径前缀」，或在评分时同时报 domain-level 命中率。**

**（2）SerpApi 有 77.9% 的结果 URL 是 Google 跳转包装。**
217 条 organic result 里 169 条的 `link` 是 `https://www.google.com/goto?url=CAES…`，不是目标站点。
插件的 `_serpapi()` 把它原样当 `url` 透传。实测 169/169 都能 302 到真实 URL（事后解析，
未改原始记录）。后果：URL 去重、站点归并、抓取路径全部失真；只看包装 URL 会把
Serper 与 SerpApi 的同 query 重合度从 0.249 低估到 0.054。**这不是 Bruce 的问题，
是 provider 适配层必须处理的一条，应进 `PROVIDER_INVENTORY.md`。**

---

*生成时间：2026-11-18（CST）。语料版本：`marketops-session` `origin/main` @ `5f29207`。只读审查，未修改任何语料文件。*
