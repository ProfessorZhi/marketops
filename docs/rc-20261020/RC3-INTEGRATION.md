# MarketOps 0.5.6-rc3 集成记录（2026-10-20）

> **历史记录（RC3）。** 后续写入时 supersedes 校验与缺口驱动来源选择见
> [`RC4-INTEGRATION.md`](RC4-INTEGRATION.md)。

状态：**DRAFT / RC3 candidate — 不是正式发布**。未合并 `main`，未创建 GitHub Release，
未调用真实付费 Provider，未发邮件，未改共享凭据、系统策略或全局配置。

本文件记录 PR #22 在原 `be77fe0d` 之后追加的 P0 修复。RC2 的记录保留在
[`RC2-INTEGRATION.md`](RC2-INTEGRATION.md)；它描述的是 rc2，不是本候选。

## 1. 分支与版本

- PR #22 head 分支：`rc/marketops-0.5.6-rc2-20261020`（**保留原分支名**；只允许一条
  产品开发分支，版本推进不新建分支）
- 产品版本：`0.5.6-rc3`
- base：`38dcef853f526f64f669c475c96ad9c3bad3ad47`（H）
- RC2 已验证 head：`be77fe0de9527afe3ecc6a136b579a06d0c2fb14`
- RC3 变更：在 RC2 之上追加提交，无 force-push、无历史改写
- RC3 follow-up（同分支追加）：统一 `check_release_gate.py` 的
  `technical_checks_pass` / `release_ready` 报告层，并修正 live public E2E
  的归属证据（真实局部跨度或诚实 HOLD）

## 2. P0-1：字段级/实体级证据准入

`research_delivery.evaluate_admission` 之前只要记录存在**任意** linked quote 就可能通过。
现在：

- 组织/场地的 `name` 必须出现在其自己的 `identity` claim 的已链接、SHA 验证 quote 中；
- 人员的 `name`、`department`、`role_title`/`role_function` 每个展示字段都必须有一个
  已链接 quote 实际包含该值；
- 岗位的 `department` 与 `role_title`/`role_function` 同样逐个字段验证；
- 匹配是**完整 token 序列连续出现**，不是子串：`Acme Natural Grass` 不会被
  `Acme Natural Grass Ltd` 支持；单 token 短别名（长度 <6）不能单独承担身份；
- 无关 identity quote 不再能让记录通过。

新增 reason codes：`IDENTITY_VALUE_NOT_IN_EXCERPT`、`PERSON_NAME_NOT_IN_EXCERPT`、
`PERSON_DEPARTMENT_NOT_IN_EXCERPT`、`PERSON_ROLE_NOT_IN_EXCERPT`、
`ROLE_DEPARTMENT_NOT_IN_EXCERPT`、`ROLE_TITLE_NOT_IN_EXCERPT`。

缺证据记录进入 `排除与待确认` / `候选数据审计`，不进入客户业务表。

## 3. P0-2：实体归属与关系证据

`_binding_supported` 之前把“共享 artifact id”当作归属。公开目录页一个页面列多家组织
时，这会把 A 机构的邮箱挂到 B 机构。现在只接受两种原文佐证：

1. **同一局部跨度**：一个已链接 quote 同时包含组织名称与记录自己的值
   （联系人 value、场地名、人员名/部门/岗位）；
2. **具名关系引用**：一条 admitted `relationship` 记录，其数据按 `contact_point_id` /
   `venue_id` / `person_id` / `role_id` / `subject_id` 等字段指向该记录，且其
   `relationship` quote 同时包含组织名称与该值。

共享页面、同 artifact、调用方自称的 `organization_id` 都不再足够。拒绝码仍是
`ORGANIZATION_PROVENANCE_MISMATCH`。

## 4. 写入时告警

`research_lite_store._span_caveat` 之前只要有任何 link 就不告警，导致组织/场地用
`field=name` 写入时无提示，直到导出才被 `MISSING_REQUIRED_IDENTITY` 拒绝。现在：

- 组织/场地已链接但缺少 `field=identity` → 非阻塞
  `MISSING_REQUIRED_IDENTITY: ... add facts.claims field=identity ...`；
- relationship 已链接但缺少 `field=relationship` → `MISSING_REQUIRED_RELATIONSHIP`；
- 单条与 batch 使用同一判定；错误字段会以名字出现在修复提示里；
- 写入端不修改、不补造 caller 的 claims。

## 5. 自动客户视图

`research_delivery.write_delivery`（真实 MCP handoff 与 `marketops_export_xlsx` 的底层）
现在：

- 从已过滤的四张业务 sheet 直接调用 `research_client_view.write_client_view`；
- 客户视图写入 delivery 目录内的 `client-view/`，不依赖 Agent 后处理；
- 顶层 `MANIFEST.json` 的 `files` 同时收录 `client-view/*` 的字节数与 SHA256，并记录
  `client_view.manifest_sha256`、`status`、`send_authorized=false`、
  `formal_sop_claimed=false`；
- 客户视图构建失败会清理整个 staging，不留下部分可交付目录；
- 9 张内部工作簿 sheet 与审计表保持不变。

`research_mcp.handoff` 回执增加 `client_view_directory`、manifest 路径/hash、状态与
发送授权字段。

## 6. 发布 QA 门禁（两个报告层统一）

- `SOURCE_MANIFEST.qa_verified=false` 不再被读成正式 QA PASS。
- **`check_release_hygiene.py`**：新增 `H12-independent-qa`；无外部签名
  `independent_qa` 时 **BLOCKED**，报告 `formal_qa=NOT_PERFORMED`、
  `release_readiness=NOT_RELEASE_READY`，退出码 2。
- **`check_release_gate.py`**：报告层现在分开
  `technical_checks_pass` 与 `release_ready`；`ok` 仅在 overall PASS 时为真。
  G7 的 ZIP/hash 即使全 PASS，只要 `qa_verified=false` 或缺独立签名，
  G15-release-readiness 为 **BLOCKED**，overall 为 **BLOCKED /
  NOT_RELEASE_READY**，退出码 2，绝不返回 `ok:true`。
- `build_source(qa_receipt=...)` 只有 receipt 匹配字节**且**外部 Ed25519 签名通过
  `MARKETOPS_QA_PUBLIC_KEY` 时才写 `qa_verified=true`；自生成 receipt 只能是
  `SOURCE_PACKAGE_PREVIEW_BUILT`。
- 测试覆盖：缺少 attestation、缺公钥、有效签名、篡改签名、revision 不一致、
  自生成 receipt 仍是 preview，以及 gate 的 `ok=false / status=BLOCKED` 报告层。

## 7. 验证结果

| 门 | 结果 |
| --- | --- |
| 插件全量离线套件 | `Ran 2212 tests`，0 failures，0 errors，30 skipped |
| P0 证据控制（`test_rc3_evidence_p0.py`） | 19 tests OK；含长页 4 条合法正向 |
| 发布 QA 控制（`test_rc3_release_qa.py`） | 6 tests OK |
| Gate readiness 控制（`test_rc3_release_gate_status.py`） | 8 tests OK |
| 定向 release gate | 技术检查全 PASS；G15 **BLOCKED**、`ok=false`、overall **BLOCKED / NOT_RELEASE_READY**、退出码 2 |
| 发布卫生门 | 技术检查 11/11 PASS；`H12-independent-qa` **BLOCKED**；整体 **BLOCKED / NOT_RELEASE_READY** |
| 自动客户视图 | 4 admitted / 4 held；`client-view/` 8 文件；顶层 MANIFEST 哈希一致 |

## 8. 阻塞与未证明

- **独立 QA**：没有外部签名 receipt / 公钥，`qa_verified=false`，禁止宣称正式 QA 通过。
- **QA #15 冻结夹具**：其“合法”记录只提供局部 quote，未包含组织名称与值的同一跨度；
  在 RC3 标准下 3 个用例失败（venue/contact/count）。没有修改 QA 工件，也没有为绿
  放宽产品核验。需要独立 QA 方把夹具升级为 org-name+value 跨度或具名关系。
- **live-readonly CI**：测试现在先在真实保存页面中寻找 12–600 字符、同时包含
  org name + venue/email 的连续局部跨度；找到才写具名 relationship，找不到则走
  “组织通过、场地/联系人 HOLD、客户视图不含它们”的诚实 fail-closed 断言。
  不恢复共享 artifact 归属，不 skip/xfail。CI 以新 run 为准。
- **OS 级 UDP 隔离 UNPROVEN**；未抓包。
- **gitleaks CLI 缺失**；仓库内正则门禁不是 gitleaks。
- 根 `ci` 的 23 条既有失败与 main/PR9 相同，不是本候选新增。

## 9. 回滚点

- RC2 head：`be77fe0de9527afe3ecc6a136b579a06d0c2fb14`
- RC3 变更紧随其后；回滚只需回退这些追加提交，不改写历史、不影响任何 Lane 分支。
