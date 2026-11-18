# local-runtime 数据集

> 这是 `benchmark/datasets/pilot/` 的**并列扩展**，不是它的替代。pilot 的 62 条
> 用例原样保留，校验和 `28157ca3b4a9ac31` 不变。

本目录服务于 A6000 本地运行时实验室：它需要比 pilot 更宽的覆盖面（文档解析、
真实世界页面形态、组合策略），同时必须能离线复现。

---

## 1. 目录

```
local-runtime/
  search/      检索式 + 金标 URL 集合
  fetch/       单个 URL + 期望的页面性质
  document/    单个文档 + 期望的解析结果（PDF / DOCX / PPTX / XLSX）
  e2e/         一句业务目标 + 金标实体与联系人
  fixtures/    本地确定性页面，供离线回归
  fixtures/serve.py   fixtures 的本地 HTTP 服务
  PROVENANCE.jsonl    每条用例的来源、哈希、核验状态
```

`search/`、`fetch/`、`e2e/` 复用 `benchmark/schemas/` 的既有 schema —— 校验命令和
pilot 是同一套。`document/` 在 Phase 5 引入自己的 schema。

---

## 2. 来源分类（强制）

每条用例在 `PROVENANCE.jsonl` 里必须有一个 `origin`，取值只有三种：

| origin | 含义 | 例子 |
|---|---|---|
| `bruce_derived` | 从 Bruce 的真实工作模式提取（地区、实体类型、问法） | 法国足球俱乐部官网发现 |
| `public_real_world` | 公开可访问的真实页面/文档，任何人可复核 | 某市政府官网的团队页 |
| `synthetic_fixture` | 我们自己构造的内容，用于测确定的边界 | 多栏 PDF、分页列表 |

**为什么主 schema 不写这个字段**：三份 case schema 都是 `additionalProperties: false`，
往 JSONL 里加字段会让 `validate` 失败，也会让 pilot 与 local-runtime 的校验口径分叉。
来源信息因此放在旁挂文件里，**以 `case_id` 为键**，不改用例本体。

`PROVENANCE.jsonl` 每行：

```json
{"case_id": "SRCH-FR-...-001", "origin": "public_real_world",
 "source_ref": "https://...", "content_sha256": "...",
 "captured_at": "2026-11-18T16:00:00+08:00",
 "review_state": "draft", "reviewed_by": null,
 "notes": "..."}
```

- `content_sha256`：`public_real_world` 记抓取时刻的响应体哈希；`synthetic_fixture`
  记 fixture 文件哈希。用于证明「金标当时对的是哪份内容」。
- `review_state`：`draft` → `reviewed`（第二人看过）→ `human_verified`（双人复核完成）。
  **主 schema 的 `label_status` 只在到达 `human_verified` 时才改**，此前一律 `draft`。

---

## 3. 数据状态

| 状态 | 含义 | 能否进结论表 |
|---|---|---|
| `draft` | 机器生成或单人整理，未逐条打开核对 | 否 |
| `reviewed` | 第二人核对过，但未完成双人流程 | 否 |
| `human_verified` | `../GROUND_TRUTH_GUIDE.md` 的双人复核完成 | 是 |

**未经人工核验，不得标记 `human_verified`。** 这条规则没有例外，包括「看起来很明显」
的用例。

---

## 4. Holdout 规则

`holdout` 用例一经写入即锁定：

- 不因某个 provider 表现差而修改；
- 不在选型迭代中被打开逐条查看；
- 阈值在跑之前定死，跑完不改。

违反其中任何一条，这一轮 holdout 作废，要新开一批。

---

## 5. fixtures

`fixtures/` 存放本地确定性页面，用 `fixtures/serve.py` 在 `127.0.0.1` 上提供：

- 静态 HTML（联系方式、团队页、目录页）
- JS 渲染页（内容只在执行脚本后出现）
- 分页列表
- 重定向链
- 需要 Cookie 才能看到的内容
- 空壳 SPA（`<div id="root"></div>`）

它们让 fetch 回归不依赖外网，也不会因为目标站点改版而失效。

---

## 6. 与 pilot 的关系

| 项 | pilot | local-runtime |
|---|---|---|
| 规模 | 24 / 28 / 10 | 按 Phase 2–5 逐步扩 |
| 覆盖 | 以 FR/GB/US 的官网发现为主 | 增加文档、真实复杂页面、企业发现、社交页 |
| 状态 | 全部 draft | 从 draft 起步，逐条升级 |
| 用途 | 方法与判据 | 测量与策略对比 |
