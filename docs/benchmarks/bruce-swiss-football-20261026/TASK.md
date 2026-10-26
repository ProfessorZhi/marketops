# 瑞士户外天然草足球场：公开有效邮箱研究（Bruce 基线题）

第二道反向题。和英国那道（`../bruce-uk-pitchfinder-20261026/`）用同一套做法：
出题只规定**范围**与**判据**，不规定做法——怎么找、找几轮、用什么工具，留给答题方。

| | |
|---|---|
| 族 | 瑞士｜户外天然草足球场（俱乐部 + 市政体育设施） |
| 子任务 | 16 个批次（`CH-PE-001` … `CH-PE-027` 中有交付的那 16 个） |
| 规模 | 44 个账户／31 个官网域名／27 个城镇 |
| 输入 | 每账户一条：机构名 + 官方站点 URL + 城镇 + 机构类型 |
| 交付 | 逐账户的「研究有效邮箱」表（19 列，见 §6） |
| 基线 | 见同目录 `BASELINE.md` |
| 语料 | `marketops-session` 仓库 `corpus/codex`（Bruce 的 Codex 会话导出） |

---

## 1. 任务

给定 **44 个账户**（瑞士的足球俱乐部、球场、体育中心与市政体育设施），
为每个账户在**公开网页**上找到「研究有效邮箱」，按 Bruce 的分层契约打标，
并让每一条邮箱都带可复核的证据（引用原文 + 来源页 + 本地镜像路径）。

一个账户允许产出 0 到 N 条邮箱；同一个机构的不同账户分开计。

## 2. 机构类型

- 足球俱乐部及其主场 / 训练基地（`Fussballplatz`、`Sportanlage`、`Stadion`）
- 市政运营的体育设施与体育中心（`Sportamt`、`Sportanlagen`、`Service des sports`）
- 市属园艺 / 场地维护部门（`Stadtgärtnerei`、`Direction Infrastructures`）
- 少量跨市镇的俱乐部-市政接口（`Sports-Reunis`、市属场馆代管）

两个语言区：德语区 60 行、法语区 6 行（基线口径）。意大利语页面在本族没有交付行，
但题面不限语言——页面用什么语言写，就按什么语言读。

题面不预筛"是不是天然草"：输入里有共用市政站点、有跨账户同域的情况
（St. Gallen 5 个账户同一站点、Basel 4 个、`staatskalender.bs.ch` 5 个），
答题方需要自己判断某个邮箱属于哪个账户、哪块场地（见 §7）。

## 3. 输入

东京本机文件（不入 Git）：

```text
/data/home/vince/artifacts/marketops/benchmarks/swiss-football/task_input.csv
```

列：`batch, account_id, organization, official_url, city, account_type, language`。
`account_id` 是 Bruce 的账户编号（`CH-<类型>-<代号>`），
`official_url` 是 Bruce 当年为这个账户定位到的官方站点的根地址。

> 为什么给 URL：和英国题同理——Bruce 这套流水线的这个阶段输入本来就是
> "已定位到官方页面"的账户。给一个机构名让模型自己找官网是另一道题，
> 会把"找站"和"找人"混在一起，基线就没法逐账户对比。

## 4. 想要的岗位（roles_wanted）

照抄 Bruce 这一族的「优先角色」三段（原文，德/法/意三语并列）：

### 4.1 A 级：可推动场地划线、草坪维护、设备采购或现场演示

`Geschäftsführer` / `Direktor` / `Directeur` / `Direttore` / `General Manager`；
`Leiter Sportanlagen` / `Responsable des installations sportives` / `Responsabile impianti sportivi`；
`Sports Facilities Manager` / `Facility Manager`；
`Grounds Manager` / `Head Groundskeeper` / `Greenkeeper` / `Platzwart` / `Intendant`；
`Head of Operations` / `Betriebsleiter` / `Responsable exploitation`；
`Estates` / `Facilities` / `Technical Services Director`；
`Procurement` / `Purchasing` / `Achats` / `Acquisti`；
`Municipal Sports Director` / `Sportamt` 负责人；
`Project` / `Capital Works` / `Infrastructure Manager`。

### 4.2 B 级：直接负责场地维护或现场执行

`Assistant Groundskeeper`、`Groundsperson`、`Greenkeeper`；
`Platzwart`、`Mitarbeiter Sportanlagen`、`technischer Mitarbeiter`；
`Responsable entretien`、`agent d'entretien des terrains`；
`Addetto manutenzione`、`custode impianti`；
场地或草坪维护主管、设备 / 车队负责人。

### 4.3 C 级：官网明确公开、且与场地或体育设施直接相关的业务入口

`sport@`、`sports@`、`sportanlagen@`、`facility@`、`facilities@`、`grounds@`、
`maintenance@`、`technik@`、`betrieb@`、`infrastruktur@`、`stade@`、
`installations.sportives@` 等。

通用 `info@` / `contact@` / `admin@` 仅作补充记录，**不计入有效邮箱主池**，
除非官网明确其处理球场、设施、维护或场地预约 / 运营事务。

### 4.4 排除或降级（exclusions）

| 排除项 | 理由 |
|---|---|
| 单纯票务、会员、球迷服务、营销、媒体、公关、青训报名、赞助销售 | 与场地运营无关 |
| 仅负责竞技队伍、教练或球员事务，未显示其管理场地或设施 | 同上 |
| 行政、财务、人事等与球场、草坪、设施、设备、项目或采购无直接关联的岗位 | 同上 |
| 推断、生成、猜测、探测或 SMTP 验证得到的邮箱 | 非字面观察 |
| `no-reply@`、`donotreply@`、明显的私人邮箱 | 不可触达 |
| 高尔夫 / 室内 / 人工草专属页面（无天然草场地） | 产品场景不符 |

## 5. 分级契约（照抄 Bruce，`global-sports-field-terminal-research`）

### 5.1 邮箱分层 `Role Tier`

| 值 | 判据 |
|---|---|
| `A` | 关键决策 / 推动岗位 + 公开真实工作邮箱 |
| `B` | 球场、草坪、设施、维护或设备一线负责人 + 公开真实工作邮箱 |
| `C` | 相关官方业务 / 部门入口（字面公开、场景相关） |
| `D` | 无场地职能证据的通用入口——**仅审计，不计入有效邮箱** |
| 推断候选 | **仅审计，永不计入有效邮箱** |

### 5.2 职责边界（Bruce 原文的结构，英国题里叫 `decision_function`）

A/B 是「有效邮箱」，C 是「官方业务入口」，D 与推断候选是「审计资产」。
这条区分是本族全部回执都在强调的口径：C/D 不得被描述为 A/B 可触达覆盖。

### 5.3 两条硬约束

- `Research-only = TRUE`，`Send Authorized = FALSE`：本族**全部 66 行**都是研究用途，
  行内 `research_only` / `send_authorized` 两列无一例外。
- 不读发送历史、不判断可发性、不建发送队列、不发信。结果是「研究有效邮箱」，
  不得称「可发邮箱」。

### 5.4 姓名不是必要条件

Bruce 原文：「姓名不是必要条件。公开真实邮箱只要与足球场运营、维护、草坪、
体育设施、设备、项目或采购职能明确绑定，即可进入有效邮箱池。」
但基线的实际情况正相反——42 条 A/B **全部**具名（见 `BASELINE.md §2.7`），
所以本题把「具名联系人」也当主指标之一：能不能找到人，才是这族的难点。

## 6. 交付格式

一张 CSV，列名照抄 Bruce 的「研究有效邮箱」表：

```text
batch, account_id, account_name, account_type, city, email, contact_name,
role_department, role_tier, language, original_url, final_url, evidence_excerpt,
mirror_path, extraction_method, email_evidence_status, role_relevance,
research_only, send_authorized
```

- `role_tier`：`A` / `B` / `C` / `D`（D 与推断候选只作审计，可留但必须标出）
- `evidence_excerpt`：页面上的可见原文片段（含邮箱出现的那一块上下文），不是转述
- `mirror_path`：答题方自己保存的页面副本路径
- `extraction_method`：如 `literal_mailto_from_local_html` / `literal_text_from_local_pdf`
- 无邮箱的账户可以不出现，但要在回答里给出「未找到」计数

## 7. 排除规则之外的三个陷阱

这三条在本族真实出现过，题面单独写出来，因为它们决定"同一机构多账户"怎么算：

1. **跨页拼接**：市政站点的 `Sportanlagen` 汇总页会一次列出多个场地的信箱；
   把汇总页的信箱记到某一个具体球场账户上，是 Bruce 明令禁止的（证据不支持）。
2. **同域多账户**：St. Gallen / Basel / Uster / Morges / Bern 都有多个账户共用一个站点；
   Bruce 的做法是**按页面归属**分配，不按域名合并。
3. **PDF 与 HTML**：66 行里有 10 行来自 PDF（`literal_text_from_local_pdf_*`），
   1 行来自 HTML 实体解码、1 行来自 Cloudflare 包裹页。只抓 HTML 会漏掉这一档。

## 8. 评分口径

对每个批次和总计，按下列字段打分：

1. 机构数（去重账户所属机构）
2. A+B 机构数（至少一条 A 或 B 的机构）
3. A+B 联系人数
4. 每百机构 A+B（= A+B 联系人数 ÷ 机构数 × 100）
5. C 数、D 数
6. 未找到数（该账户没有任何合格路线）
7. 具名联系人数（`contact_name` 非空，且**人工核实为真人姓名**）
8. 覆盖率（有任一合格路线的账户 ÷ 输入账户）
9. 耗时
10. 人工介入次数（人给下的指令、纠偏、放行）

主指标是 **每百机构 A+B** 与 **精度**：本族的基线 A/B 密度很高（`BASELINE.md §2.5`），
单看"有没有邮箱"区分不出能力差异。

## 9. 数据边界

- 机构清单（`task_input.csv`）与逐账户基线明细（`baseline_rows.csv`）**只放东京本机**，
  路径见 §3 与 `BASELINE.md §7`。
- TASK.md 与 BASELINE.md 不含任何第三方姓名、邮箱、电话。
- 语料里可能出现凭据和个人信息，一律不得带出东京节点、不得进 Git。

## 10. 出题依据

### 10.1 为什么换这一族

第二轮的题目要求「和英国学校不同国家、不同机构类型，且基线里要有足够多的 A/B」。
按这三条筛完，只剩瑞士这一族：

| 族 | 国家 | 机构类型 | 最终交付 | A/B 条数 | 逐账户可衡量 | 结论 |
|---|---|---|---|---|---|---|
| **瑞士 户外足球场** | 瑞士 | 俱乐部 + 市政体育设施 | 66 条（A16/B26/C24） | **42** | 是（账户 + 官网 + 邮箱 + 分层 + 证据原文 + 镜像路径） | **选中** |
| 美国 非高尔夫户外运动场 | 美国 | 运动场 / 公园 | B002_v1 50 账户（A1/B7/C4/D3） | 8 | 是，但只有 2 批 | 落选：A/B 太少，测不出精度 |
| 法国 天然草运动场 | 法国 | 运动场 | 只到网页候选包，无邮箱交付 | — | 否 | 落选：交付不完整 |
| 美国 PL01 停车场 | 美国 | 停车场 | 638 条邮箱 / 53 家机构 | 少 | 是，但机构太少 | 落选：机构数不足 |

选瑞士的三条理由：

1. **A/B 密度最高，且全部具名**：42 条 A/B 覆盖 27 个账户，每一条都有姓名 + 岗位。
   英国那道题 376 行里一条具名都没有（`../bruce-uk-pitchfinder-20261026/BASELINE.md §8`），
   所以第一轮只能测"邮箱对不对"，这一轮可以测"人找没找到"。
2. **机构类型不同**：英国是学校+学院（行政体系），瑞士是俱乐部+市政（社团与市政府两套体系），
   德语/法语双语言，`Platzwart` / `Materialwart` / `Sportamt` 这类岗位在英文里没有直译。
   这正好检验插件是不是只会"英文学校官网"这一种表面。
3. **每条都能逐行复核**：66 行都带 `Original URL` + `Local Mirror Path` + `Evidence Excerpt`，
   精度检查可以打开原页面逐条对四项（字面存在 / 同块绑定 / 岗位在口径内 / 未被排除）。

### 10.2 为什么只取 16 个批次、44 个账户

- Bruce 这一族一共跑了 27 批（`CH-PE-001`…`CH-PE-027`），但**只有 16 批留下了最终交付行**。
  其余的批次在他本机，语料与飞书里都没有逐账户结果，取进来就没有基线可比。
- 44 个账户 = 66 行的 `Account ID` 去重。这 44 个账户就是本题的输入。
- 31 个官网域名 = 44 个账户的 `Original URL` 去重；
  差额来自 6 组同域多账户（见 §2、§7.2）。

### 10.3 基线的已知偏差（写在题面上，不藏）

这 44 个账户是 Bruce **交付成功**的那一批，不是他尝试过的全部账户。
所以基线的「覆盖率」必然是 100%、「未找到」必然是 0——这两个数不能当基线优势看。
真正可比的是 **同一批账户上，A/B 密度、具名人数与精度**。
详见 `BASELINE.md §2.6`。

### 10.4 基线在哪里

`BASELINE.md`；逐账户明细在东京本机 `baseline_rows.csv`（66 行）。
