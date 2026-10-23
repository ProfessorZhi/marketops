# MarketOps 0.5.6-rc5 集成记录（2026-10-20）

状态：**DRAFT / RC5 candidate — 不是正式发布**。未合并 `main`，未创建 Release，
未执行真实 Provider/付费调用，未修改 14 条 UNKNOWN、额度、凭据或系统策略。

本文件记录 PR #22 在 RC4 `6a6f4f4e` 之后的两项 P0/P1 修复。RC4 记录保留在
[`RC4-INTEGRATION.md`](RC4-INTEGRATION.md)。

## 1. 分支与版本

- 同一 PR #22 分支 `rc/marketops-0.5.6-rc2-20261020`（历史名称）
- 产品版本：`0.5.6-rc5`
- 同分支追加提交；无新 PR/branch、无 force-push、无历史改写
- 未生成新客户 ZIP；旧 rc3/rc4 ZIP 与真实试验目录未改动

## 2. P0：review supersedes 类型不一致

`review_type=note` 曾可用同一 run/kind/stage/entity_key 加上
`facts.supersedes=<live HOLD disposition>` 成为 tip，导致 HOLD 消失、held venue
从 0 变 1。现 `_validate_supersedes` 对任何 `kind=review` 的 supersedes 强制：

```text
parent.review_type == child.review_type
```

不匹配返回 `SUPERSEDES_REVIEW_TYPE_MISMATCH: reuse review_type <parent> from <parent>`。
`target_disposition` 仍额外要求 `target_kind` / `target_entity_key` 不变、父版本是
唯一 tip；无 review_type 的 review 不能顶替处置；反向（处置顶替 note）也被拒绝。

- 单条：note 顶替 HOLD → 拒绝，held venue 保持 0
- batch：同一批次中的合法 item 与坏 note 一起回滚，held venue 保持 0
- 正控：同 review_type 的 HOLD → RESEARCHED_CANDIDATE → 第二次修订全部通过，
  最终 tip 为第三次修订，held → admitted 正常发生，完整历史仍在审计
- 旧历史 fork 不被修改，导出仍按原逻辑保留审计并拒绝选择分支

测试：`test_grading_review_type_guard.py` 6 条。

最小 reproducer（修复前）：note 插入成功，venue 0→1；修复后返回
`REFUSED:SUPERSEDES_REVIEW_TYPE_MISMATCH`，venue 保持 0。

## 3. P1：rate / phone / contact binding 证据

`research_source_selection._missing_fields` 之前把 raw `hourly_rate` 字段当成已覆盖，
phone 只看记录存在，`links.relationship` 也能单独绑定。现在：

- **hourly_rate**：必须有实际 rate 值 + 对应 field 的 `evidence_links` + 已存储
  claim quote 支持该数值。`£45/hour` 但无 claim、claim 未 linked、quote 不含 45，
  均保持 `hourly_rate` missing，并建议官方 rate/booking 页。
- **phone**：必须有 phone contact、合法 linked quote 包含电话号码，且该联系方式
  有有效机构归属（同一局部跨度 org name + phone value，或具名 relationship
  record + quote）。否则保持 `phone` missing，并建议官方 contact 页。
- **contact_binding**：不能再靠 `links.relationship` 单独通过；必须 claim quote
  同时包含 org name 与 contact value，或 relation record 明确指向该 contact 且
  quote 同时包含两者。
- 页面标题、unlinked price/email/phone、只有 source/抓取成功都不算已核实。
- blocked domain、零预算、UNKNOWN-held、共享目录页不归属、rate/contact 页优先级
  保持原行为；纯 advisory，不发网络请求。

测试：`test_grading_source_evidence.py` 10 条（rate 4、phone 4、binding 2）。

## 4. 验证

| 门 | 结果 |
| --- | --- |
| 插件全量离线套件 | `Ran 2271 tests`，0 failures，0 errors，30 skipped |
| review_type 控制 | 6 tests OK |
| source evidence 控制 | 10 tests OK |
| 定向 release gate | G19/G20 PASS；G15 仍 BLOCKED（无独立 QA） |

## 5. 未完成 / 阻塞

- **14 条 UNKNOWN 费用未改**（RC3 8 + 0.5.5 6）；无正式结算、无 Provider 调用、
  无额度变化。
- 无正式独立 QA 签名；release gate 仍 `BLOCKED / NOT_RELEASE_READY`；未自签、
  未复用旧 BUILD_QA receipt。
- 根 `ci` 既有 23 条失败与 main/PR9 相同。
- QA #15 冻结夹具仍 6/9；OS UDP UNPROVEN；gitleaks CLI 缺失。
- 未部署、未合并/关闭 PR、未发 Release、无 SMTP/充值/账号权限操作。

## 6. 回滚点

- RC4 head：`6a6f4f4e48cbde47ff6d46a302c41238ee5d631f`
- RC5 追加提交紧随其后；回滚只需回退该提交，不影响旧 ZIP 或 Lane 分支。
