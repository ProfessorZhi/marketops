# MarketOps 0.5.6-rc4 集成记录（2026-10-20）

> **历史记录（RC4）。** 后续 review-type 与来源证据修复见
> [`RC5-INTEGRATION.md`](RC5-INTEGRATION.md)。

状态：**DRAFT / RC4 candidate — 不是正式发布**。未合并 `main`，未创建 GitHub Release，
未执行真实 Provider/付费调用，未改费用 UNKNOWN、额度、凭据或系统策略。

本文件记录 PR #22 在 RC3 `1bd84ac2` 之后的两项收口。RC3 记录保留在
[`RC3-INTEGRATION.md`](RC3-INTEGRATION.md)，不重写。

## 1. 分支与版本

- PR #22 head 分支：`rc/marketops-0.5.6-rc2-20261020`（历史名称；只允许一条产品分支）
- 产品版本：`0.5.6-rc4`
- 同分支追加提交，无新 PR/branch、无 force-push、无历史改写
- 既有 `0.5.6-rc3` ZIP 与真实试验目录未改动；本轮未生成新客户包

## 2. 核心 A：写入时 supersedes 版本校验

`research_lite_store._insert_candidate` 在插入前、同一事务内调用
`_validate_supersedes`。任何 `facts.supersedes` 必须满足：

- 前版本存在、同一 run；
- kind / stage / entity_key 与被替换版本一致；
- 前版本是当前唯一 tip（无 fork、无已 superseded 的父版本）；
- 链无环；
- review `target_disposition` 的 `target_kind` / `target_entity_key` 与前版本一致；
- 同一 target 只能有一个 live disposition；再次写入必须 `supersedes` 现有版本并复用其 entity_key。

封闭错误码（含具体修复，不泄露材料）：

- `SUPERSEDES_INVALID`
- `SUPERSEDES_SELF_REFERENCE`
- `SUPERSEDES_UNKNOWN_RECORD`
- `SUPERSEDES_CROSS_RUN`
- `SUPERSEDES_KIND_MISMATCH`
- `SUPERSEDES_STAGE_MISMATCH`
- `SUPERSEDES_ENTITY_KEY_MISMATCH: reuse entity_key from <old ID>`
- `SUPERSEDES_FORKED_HISTORY`
- `SUPERSEDES_NOT_CURRENT_TIP`
- `SUPERSEDES_CYCLE`
- `SUPERSEDES_REVIEW_TARGET_MISMATCH`
- `TARGET_DISPOSITION_ALREADY_EXISTS`
- `TARGET_DISPOSITION_FIELDS_INVALID`

行为边界：不更新/删除旧行、不 cascade、不改写 caller facts、不自动替换 entity_key。
合法的同实体 append-only 更正继续通过；新实体应新建候选。
历史已存在的 fork 不被改写；导出仍按原逻辑保留内审并拒绝选择分支。

测试：`test_grading_supersedes_write_guard.py` 17 条，覆盖 Essex 7 错 key 形状、
review 错 entity_key、跨 run、未知父、kind/stage 不一致、review 换 target、
无 supersedes 的重复 disposition、父版本非 tip、合法更正、review 更正、
幂等、批量原子回滚、并发冲突、历史 fork 不改写。

## 3. 核心 B：缺口驱动来源选择

新增纯离线模块 `research_source_selection.py`（`marketops.source-selection/1`），
由 `research_workflow.next_step` 调用，并随 `marketops_next` 返回
`source_selection` / `next_best_source_action`。它不联网、不改预算、不购买额度。

输入：结构化 Brief（`target_kind`/`goal`）、active candidates、已记录的
`evidence_links` 字段、source/query 元数据、预算快照、blocked domains。

输出每个 candidate 的：

- `missing_fields`：`organization_identity`、`venue_identity`、
  `venue_org_relationship`、`contact_binding`、`hourly_rate`、`official_email`、
  `named_person_contact_chain`、`phone`；
- `next_best_source_action`：动作、优先级、已有 URL 或搜索 hint、
  `requires_search`、attribution、reason；
- `completeness`，用于“先完成接近全部条件的候选”。

规则：

- 只认 `evidence_links` 中真实记录的字段；附件、source 记录或抓取成功**不是**
  字段已核实；
- 优先官方域名的 rate/booking/contact/venue 页面；共享目录页不算归属证据，
  除非 source 明确 `organization_id` 指向该机构；
- 缺 `hourly_rate` → rates/booking 页；缺 `official_email` → contact 页；
  `contact_binding` → 具名 contact-to-org relationship；
  venue 缺 org+venue 跨度 → 机构页链接的 venue 页；
- blocked domain 不再重试；0 预算不提议搜索；UNKNOWN-held 只标注不重试；
- 预算快照优先读 runtime-config 的管理额度，再回退 legacy env；读不到是
  `None`（未知），不会误报 0；
- 预算紧张时先完成最接近完成的候选，不随机发现更多 hall。

测试：`test_workflow_source_selection.py` 15 条（rates/contact/blocked/binding/
scarce budget/zero budget/fetch≠verified/unlinked quote/shared directory/
third-party attribution/workflow 暴露）。

## 4. 验证

| 门 | 结果 |
| --- | --- |
| 插件全量离线套件 | `Ran 2255 tests`，0 failures，0 errors，30 skipped |
| supersedes 写入控制 | 17 tests OK |
| 来源选择控制 | 15 tests OK |
| workflow 暴露 | `marketops_next` 返回 `source_selection`，本地离线 |

## 5. 未完成 / 阻塞

- **真实 Essex 试验未由开发 Session 执行**；费用 UNKNOWN 未改 0、未取消预留、
  未扩额度、未调用 Provider。独立费用 QA 的 8 条 UNKNOWN 保持原状。
- 根 `ci` 仍有既有 23 条失败（与 main/PR9 相同）。
- QA #15 冻结夹具仍 6/9（缺 org-name+value 跨度），未修改 QA 工件。
- OS 级 UDP 隔离 UNPROVEN；`gitleaks` CLI 缺失。
- **无正式 QA 签名**：`qa_verified=false`，release gate 仍为
  `BLOCKED / NOT_RELEASE_READY`，不得自签。
- 未生成新的 BUILD_QA receipt；不混用旧 rc3 receipt 与新 SHA。
- 既有 rc3 ZIP / 真实试验目录未覆盖、未删除、未移动。

## 6. 回滚点

- RC3 head：`1bd84ac25295f1e1db28287359887072c36b05fa`
- RC4 追加提交紧随其后；回滚只需回退该提交，不影响 Lane 分支或既有 ZIP。
