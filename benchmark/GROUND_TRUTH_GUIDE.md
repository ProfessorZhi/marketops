# Ground Truth 标注指南

金标是整套基准的地基。地基错了，后面每一个指标都只是在精确地测量错误。

本文规定**谁能标、怎么标、标成什么样才算数**。

---

## 1. 三条硬规则

### 1.1 人确认，模型不确认

金标只能由**打开过页面的人**确认。理由不是"不信任模型"，是一个可证明的偏差：

- 若标注由模型 M 生成，评分也由 M 完成，则 M 的错误在两侧**同向**；
- 同向误差在任何一致率统计里都表现为"高度一致"；
- 于是"模型与金标一致率 0.93"这句话，实际含义是"M 与自己一致"。

所以：**生成金标的模型不得参与同一批用例的 judge。** 若要复用同一厂商，必须换模型
且换 prompt 版本，并在报告里写明。

### 1.2 联系地址必须带"印着它的那一页"

`gold_label.contacts[].evidence_url` 是必填。判据：

> 打开 `evidence_url`，`Ctrl+F` 搜 `value`，必须能在页面上找到。

找不到就**不是金标**。它可能是对的（从商会、从第三方目录来），但没有可复核的出处，
就不能用来评判别人"漏了它"。

### 1.3 不确定就标 `disputed`

`label_status` 的合法值是 `draft` / `human_verified` / `disputed`。

- `draft`：机器或人给出的候选，**未复核**。可以用于开发，**不得进结论表**。
- `human_verified`：人打开过、确认过、记了证据。
- `disputed`：两个标注者结论冲突，或确认时发现与预期不符。

**`disputed` 不是废案，是记录。** 它保留"这里曾经有分歧"这个事实；直接把有分歧的
用例删掉，等于让数据集只留下容易的部分，指标会系统性偏高。

---

## 2. 标注流程

### 2.1 单条 search 用例

```
1. 读 query / market / language / task_type
2. 在一台干净浏览器里执行这条检索（不在系统里执行 —— 那会污染缓存与额度）
3. 逐条打开候选结果，只回答一个问题：
   这条页面，能不能满足 task_type 里写的那个交付物？
4. 给 relevance：
      3 = 正是要的那个页面（官方或权威，直接可用）
      2 = 相关，但需要再点一层才能拿到交付物
      1 = 沾边（提到主题，但没有交付物）
   不给分的页面**不要列进 relevant_urls**，不要列成 relevance 0。
5. is_official：这个域是不是该实体的官方/权威来源？
   - 机构自己的域（协会、俱乐部、政府部门）→ true
   - 政府数据门户、官方 registry → true
   - 聚合站、黄页、维基、新闻 → false
6. required_entity_ids：这条用例**必须**找到的实体；找不到就说明这条用例没被满足
7. 记 validation_timestamp
```

**为什么给 1/2/3 而不是 0/1**：nDCG 需要分级增益；"相关但不够"和"正好可用"在业务上
的差别是"要不要再跑一轮"，把它压成布尔值，这个差别就消失了。

### 2.2 单条 fetch 用例

```
1. 用一台干净浏览器打开 url（或 curl，用于确认"不用 JS 能不能拿到"）
2. 记录 page_kind：static / spa_shell / login_wall / pdf_text / pdf_scanned / ...
3. requires_js：关掉 JS 再打开一次，正文是否消失？
4. min_text_chars：正文（去导航/页脚/脚本）实际有多少字符 —— 填你**测到**的数字，
   不是估的
5. expected_text_markers：2–3 个"如果读对了必然出现"的短语（机构名、页标题）
6. expected_emails / phones：只填**页面上印着**的，含 mailto
7. expected_links：这一页应当能到达的关键链接（联系页、目录页、下载）
8. forbidden_strings：只有拿到登录/同意墙/错误页才会出现的文案
```

**`expected_outcome` 的取值**：`content`（应当拿到正文）/ `no_content`（应当被判为无内容）。
登录墙、404、纯壳页都是 `no_content`。**把它们标成 `content` 是最常见的标注错误**，
因为它会让"返回登录页"看起来是一次成功。

### 2.3 单条 E2E 用例

```
1. 先写 brief（目标 / 地域 / 交付物形状），不写答案
2. 自己走一遍：检索 → 打开 → 抽取 → 交叉验证
3. 金标 entities：只放你亲自确认存在、且能找到官方 URL 的实体
4. 金标 contacts：只放你在页面上找到的地址，每条带 evidence_url
5. sources：你实际用到的那几页
6. acceptance：这条用例"算完成"的最低线（实体召回、证据要求、假阳性上限）
```

**E2E 金标要允许"部分完成"。** 一条目标是"找出 10 家俱乐部"的用例，找到 8 家且都有
官方 URL，比找到 3 家但每条都带三条联系方式更有价值。`min_entity_recall` 就是用来
表达这个取舍的，不要把它写成 1.0。

---

## 3. 双人复核

规模允许时，**每条 holdout 用例由两人独立标注**，然后比对：

| 情形 | 处置 |
|---|---|
| 完全一致 | 升 `human_verified` |
| 只在 relevance 上差 1 级 | 取较低的那级（保守），记一条 note |
| 一个标相关、一个标不相关 | 第三人裁决；无第三人则标 `disputed` |
| 联系方式只有一人找到 | 第二人复核该页；确认后升级，否则不进金标 |
| 联系方式两人找到不同值 | 两个都记，标 `disputed`，note 写清各自 `evidence_url` |

**一致率本身是一个指标。** 两个人的 agreement 低于 0.85，说明**用例定义不清**，
不是标注员不用心 —— 这时要改的是用例的 `task_type` 或 brief，不是标注结果。

---

## 4. 已知的标注陷阱

| 陷阱 | 为什么会发生 | 防法 |
|---|---|---|
| 把"相关"标成"官方" | 页面看起来权威 | 只认机构自有域与官方 registry |
| 用系统自身跑出来的结果当金标 | 快，且"看起来对" | 禁止。金标只能在干净浏览器里人工产生 |
| 把登录墙标成 `content` | 页面有文字 | 用 `forbidden_strings` 表达"这段文案出现即失败" |
| 用当前 URL 当唯一答案 | URL 会变 | 记 `official_url` + 允许 `aliases`；实体按 id 匹配 |
| 只标容易的用例 | 难的不敢标，于是删掉 | 难的标 `disputed` 并写原因，保留在数据集里 |
| 时效性用例标完就过期 | `freshness_search` 的答案会变 | `max_age_days` + `validation_timestamp`；过期用例重标而不是复用 |
| 联系方式标了但没有出处 | 从别处抄来 | `evidence_url` 必填，且必须能 Ctrl+F 命中 |

---

## 5. pilot 数据集的当前状态（必须知道）

`benchmark/datasets/pilot/` 里的 62 条用例**全部是 `draft`**，`validation_timestamp`
统一为构造时间。具体地：

- **search**：`relevant_urls` 只放了公开已知的官方首页/入口（`om.fr`、`psg.fr`、
  `fff.fr`、`data.gouv.fr`、`mancity.com`、`apps.irs.gov/app/eos/` 等）。它们是
  **候选**，不是"这一条检索的正确输出"。其中 12 条的 `relevant_urls` 故意为空
  （PDF / 社交 / 长尾类），因为这几类的正确答案无法在不打开页面的情况下断言。
- **fetch**：`min_text_chars` 是**保守下界**，不是实测值；`expected_emails` /
  `expected_phones` 全部为空。
- **e2e**：`gold_label.contacts` **全部为空**，`min_verified_contacts` 为 0，
  并在 note 里写明"人类确认前不设联系人口径"。

**这意味着 pilot 现在只能验证"管道是通的、判据是可算的"，不能产出任何 provider 排名。**
把它当成排名用，得到的是"官方首页在三家搜索引擎里都排第一"这种无信息量的结论。

---

## 6. 升级 pilot 的操作顺序

1. 选 **development** 的 11 条 search，人工确认 `relevant_urls`，补 `required_entity_ids`。
2. 选 **development** 的 13 条 fetch，实测 `min_text_chars`，补 `expected_text_markers`。
3. 对 4 条 development e2e，人工走一遍并补 contacts（带 `evidence_url`）。
4. 拿 development 跑一遍完整链路，修数据集里**定义不清**的地方（不是修 provider）。
5. 方法稳定后再标 validation，最后标 holdout。**holdout 标注完成后封存**，
   在写结论之前不得打开逐条结果。

规模建议：每个能力每市场 ≥ 30 条 `human_verified` 用例，才有资格出角色判定
（`STANDARD.md` §5 的 `Insufficient Evidence` 门槛）。
