# RC 客户交付验收与安全门槛（独立 QA）

> 本文是 **独立 QA 的验收门槛与失败记录**，不是发布说明，也不宣布验收通过。
> 所有夹具均为纯合成数据（`example.org` / `example.net`），不含真实客户、
> 真实联系人、真实运行日志、凭据或 Provider 调用。

## 0. 审查基线

| 项 | 值 |
| --- | --- |
| 被审对象 | `ProfessorZhi/marketops` PR #9 |
| base branch | `feat/bruce-plugin-delivery-20261020` |
| 被审 SHA | `4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44` |
| 插件版本 | `0.5.5`（`plugin.json` / `.claude-plugin` / `.codex-plugin` 三份） |
| QA 分支 | `qa/marketops-rc-customer-gate-20261020` |
| QA worktree | `/data/home/vince/workspace/worktrees/marketops/rc-independent-qa-20261020` |
| 判据文件 | `tests/test_customer_delivery_boundary_qa.py` |
| 解释器 | Python 3.12.3；pytest 9.1.1（`toolchains/marketops-mvp`） |
| 执行时间（北京） | 2026-10-20 13:08–13:12 |
| 数据边界 | 纯合成；无网络、无真实 Provider、无收费调用、无本机共享配置改动 |

### 当前裁决

**`NOT PASS`（基线红灯）**：9 条定向判据中 **6 红 / 3 绿**。

这不是“环境错误”，而是 `build_delivery` 在 0.5.5 基线上把**候选审计面**直接
当成了**客户交付面**：伪造组织、无字面邮箱、无引文联系渠道会进入客户 Sheet，
而超过 100 条的审计样本窗口会把后续问题记录挡在排除表之外。

第二方复核完成、修复进入可追溯 SHA、CI 归因清楚之前，任何人均不得把本报告
读成 PASS。

---

## 1. 两个受众必须分开

`research_delivery.build_delivery` 返回的 9 个 Sheet 里，有两类完全不同的受众：

| 受众 | Sheet | 规则 |
| --- | --- | --- |
| **客户版** | `客户总表`、`联系人路线`、`场地与资产`、`人员岗位` | 只允许通过准入的候选。伪造名称、无字面值、无引文、未绑定到已准入组织的记录**必须排除** |
| **内部候选审计** | `排除与待确认`、`证据索引`、`本轮说明`、`来源与查询`、`候选数据审计` | **可以且必须保留**全部候选。拒绝不是删除；复核人必须看得到被拒对象和原因 |

“内部审计保留”与“客户版排除”不是同一件事。前者绿、后者红，不能互相抵消。

### 准入维度（目标契约）

一个业务记录只有在下列每个维度都通过时才可进入客户版：

1. **身份**：组织/场地名称可读且由同一资料的引文支撑（`identity`），不是占位符。
2. **引用跨度**：存在 `(artifact, field, span)` 证据链接，且引文在保存资料中逐字命中。
3. **资料类型**：锚点必须是 `rendered_html` / `raw_response` / `provider_html`；
   `provider_extracted`、图片、模型决策不能单独作为锚点。
4. **联系字面值**：邮箱/电话必须作为完整字面值出现在可准入资料中，且该字面值
   出现在某条已记录引文里；“页面里出现过”不等于“这条渠道有引文”。
5. **组织绑定**：联系渠道/场地必须与**已准入**组织共享资料，或其资料中出现了
   该组织的记录名称。裸 `organization_id` 只是断言，不是绑定。
6. **处置与版本**：HOLD / EXCLUDED / DUPLICATE、版本冲突、非法处置都必须拒绝。
7. **样本窗口独立**：准入判定必须遍历**全部**业务记录，不得读取
   `audit_records(..., result_limit=100)` 的 `samples[:100]` 作为判定集合。

---

## 2. 纯合成失败重现

同一 Run 内混合以下记录：

- `Example Borough Council`：身份引文、场地、人员、联系人字面值均有引文和跨度。
- `Totally Fabricated Org`：引用了资料，但没有任何引文把名称绑到资料上。
- 伪造场地 / 伪造人员：无资料引用。
- `fabricated@example.net`：邮箱在保存资料中**无字面值**。
- `secondary@example.org`：邮箱有字面值，但**无 quote / 无跨度**。
- 绑定到伪造组织的联系人：字面值和引文都存在，但组织本身未准入。
- `unbound@example.org`：字面值和引文都存在，但资料中没有任何组织名称，
  与合法组织无共享资料 → 组织绑定不成立。
- 105 条批量组织：都引用资料、都有已链接引文，但缺少 `identity` 字段；
  记录号 `bulk-000` … `bulk-104`，正好越过审计的 100 条样本窗口。

判据只读 `build_delivery` 返回的 Sheet 与 manifest counts，不依赖 Lane A 的
内部函数名或 manifest 私有字段。

---

## 3. 基线实测：6 红 / 3 绿

| # | 判据 | 期望 | 4f87bc33 实测 |
| --- | --- | --- | --- |
| 1 | `test_customer_business_sheets_exclude_fabricated_candidates` | `客户总表` 仅合法组织；`场地与资产`/`人员岗位` 仅合法对象 | `客户总表` 为 `['Example Borough Council', 'Totally Fabricated Org']` |
| 2 | `test_contact_sheet_excludes_held_and_cross_bound_channels` | `联系人路线` 仅 `contact-legit` | 5 条渠道，包含 `HOLD_NOT_LITERAL`、`HOLD_EMAIL_NO_SOURCE_QUOTE`，以及跨组织/未绑定联系人 |
| 3 | `test_public_channel_count_counts_only_admitted_channels` | 合法组织 `公开联系渠道数 = 1` | **4**（HOLD 与未绑定渠道都被计数） |
| 4 | `test_refused_candidates_are_listed_with_reasons_in_exclusion_table` | 7 个被拒对象都在排除表 | 只有 5 个；`contact-cross-org`、`contact-unbound` **不在排除表**（前者在客户表中还被标成非 HOLD） |
| 5 | `test_records_beyond_100_sample_window_stay_out_of_customer_sheet` | `客户总表` 仅 1 行 | **106 行**（1 合法 + 105 未准入） |
| 6 | `test_records_beyond_100_sample_window_are_listed_in_exclusion_table` | 105 个 `bulk-*` 全部被裁决 | 恰好 100 个；`bulk-100` … `bulk-104` 缺失，正是样本窗口外的记录 |
| 7 | `test_internal_audit_retains_every_candidate` | `候选数据审计` 保留混合场景的全部候选 | **绿**：11/11 都在 |
| 8 | `test_internal_audit_retains_all_records_beyond_sample_window` | `候选数据审计` 保留 106 条候选 | **绿**：106/106 都在 |
| 9 | `test_delivery_is_immutable_and_manifest_hashes_match_disk` | ZIP 可回读、文件哈希一致、同 ID 不覆盖 | **绿** |

第 7、8 条绿说明系统**有能力**保留内部审计；缺的是客户版准入闸门，不是审计能力。
第 9 条绿说明产物写入路径本身是只读可核、不可覆盖的。

### `>100` 截断的机理

`research_delivery.py` 以 `result_limit=100` 调用 `evidence.audit_records`，并把
`audit_all["samples"]` 写入 `排除与待确认`。`gap_counts` 仍统计全部记录，但
排除表的逐条明细只剩前 100 条。客户 Sheet 又不做准入过滤，于是：

- 第 101 条以后的问题记录**仍会进入客户 Sheet**；
- 复核人却**看不到**它们，因为排除表被样本窗口截断。

这是“红在客户表、也红在排除表”的同一个缺陷，不能只修一边。

---

## 4. 与 Lane A / Lane B 的对照

### Lane A（交付闸门）

- 工作树：`worktrees/marketops/rc-gate-20261020`，分支
  `fix/marketops-rc-delivery-gate-20261020`。
- 审查时**未提交**：`research_delivery.py` 与 `test_delivery_research_delivery.py` 为
  modified，新增 `test_grading_admission_gate.py`。
- 同一组 9 条判据在 Lane A 当前工作树（HEAD 仍为 `4f87bc33`；
  `research_delivery.py` sha256 在 13:10:56 为 `d4dd3729…`，13:15:01 已变为
  `3af5ac9683029d82293520a360015c8777813815a1b10c0772ddedf74ca995cd`，
  两次均为 **9/9 绿**）上通过。
- 这只证明“目标行为可以被实现”，**不是验收**：没有提交 SHA、没有 PR、
  工作树会继续变化，不能作为发布件。

Lane A 的实现方向与目标契约一致：`CLIENT_BUSINESS_SHEETS` /
`INTERNAL_AUDIT_SHEETS` 分离、全量 `evaluate_admission`、组织根节点准入后
继承到场地/联系人、`sample_window_independent`。落地时必须把该实现固定到
可审 SHA，再由第二方重跑本组判据。

### Lane B（安装）

- 工作树：`worktrees/marketops/rc-installer-20261020`，审查时干净、停在
  `4f87bc33`，没有可对照的提交。
- 基线安装交付测试：14 条，**12 过 / 2 环境阻塞**。
  - 阻塞码：`PYTHON_ENSUREPIP_UNAVAILABLE`。
  - 本机 Python 3.12（系统与 `marketops-mvp`）都没有 `ensurepip` 模块；
    `setup_clients.plan` 在**写任何文件之前**拒绝，属于 fail-closed 的正确行为，
    但这两条不能记 PASS。
  - 需要在带 `ensurepip` 的真实 Python 3.12 环境复跑，才能对“部分安装可恢复”
    与“Codex 注册”给出 PASS/BLOCKED 结论。

---

## 5. 原 0.5.5 ZIP：可回读、哈希、不覆盖

| 项 | 实测 |
| --- | --- |
| 路径 | `/data/home/vince/artifacts/marketops/bruce-delivery-20261020/release/marketops-research-assistant-v0.5.5-source.zip` |
| 大小 | 1,161,315 bytes |
| SHA-256 | `0c3a2e1689479a7cf640185dd6f6ef45fd348c8375685e7a608cd8c2783f0ff5` |
| ZIP 完整性 | `testzip() = None` |
| 条目数 | 164，单一根目录 `marketops-research-assistant/` |
| 三份 manifest | `plugin.json`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json` 均为 `0.5.5` |
| 与 PR #9 自述哈希 | **一致** |

不覆盖由构建器保证：`build_client_bundles.make_zip` 使用
`zipfile.ZipFile(output_zip, "x")`；`build_source` / connector 输出在目标或
archive 已存在时抛 `FileExistsError`。本次审查**没有重建、没有覆盖**原 ZIP。

若修复后需要新包：必须生成**新版本名/新哈希**，并把旧 0.5.5 归档保持只读；
不得用同名覆盖来“保持哈希不变”。

---

## 6. 安装副作用门槛

安装验收必须在**干净临时目录**完成，且满足：

1. 不写 canonical checkout、其他 Lane 的 worktree、`HOME` 根或共享配置。
2. 目标目录已存在且非本安装器所有时，拒绝并保留用户文件（`INSTALL_TARGET_NOT_OWNED`）。
3. 先写 ownership/source/stage 状态，再复制；resume 只接受同一 source digest、
   同一 client、同一 destination。
4. 依赖安装是显式 opt-in；只进本地 venv；`pip check` 通过。
5. 凭据只进系统 keyring 或显式环境变量；报告、日志、异常文本不得回显值。
6. 安装阶段零 Provider 调用、零计费、零客户数据改写。
7. 升级/重装保留既有研究数据、配置与经验文件。
8. 无可用 venv 组件时**在写入前**以命名错误码拒绝（本机即
   `PYTHON_ENSUREPIP_UNAVAILABLE`），不得留下半安装状态。

---

## 7. 质量门槛（G1–G8）

| 门槛 | 内容 | 通过条件 |
| --- | --- | --- |
| **G1** | 客户版组织/场地/人员准入 | 判据 1 绿；伪造对象不出现在四个客户 Sheet |
| **G2** | 联系渠道准入与组织绑定 | 判据 2 绿；HOLD、跨组织、未绑定渠道不出现，且排除表有原因 |
| **G3** | 客户可见计数 | 判据 3 绿；`公开联系渠道数`/`关联场地数` 只计已准入对象 |
| **G4** | 内部审计保留 | 判据 7、8 绿；`候选数据审计` 与排除表不丢候选 |
| **G5** | 样本窗口独立 | 判据 5、6 绿；`>100` 时每条都被裁决，客户表不泄漏 |
| **G6** | 产物可回读/不可覆盖 | 判据 9 绿；ZIP 完整、manifest 哈希与磁盘一致、同 delivery ID 拒绝 |
| **G7** | 发布 ZIP 冻结 | 0.5.5 哈希与 PR 自述一致；构建器不覆盖；新包用新哈希 |
| **G8** | 安装安全 | 第 6 节 1–8 全满足；本机 ensurepip 阻塞需在合格环境复跑 |

**G1–G6 必须由同一固定 SHA 上的定向测试证明；G7–G8 必须有独立证据。**
任何一条 BLOCKED 都不能用其他条目的绿来抵消。

---

## 8. 双人复核 checklist

### 作者（实现方）

- [ ] 在独立 worktree 从指定 SHA 起分支，未复用/覆盖他人分支。
- [ ] 只改本 Lane 拥有的文件；列出每个文件的 diff 与理由。
- [ ] 自测：本组 9 条判据全绿；另跑插件定向测试，记录解释器与数量。
- [ ] 明确“内部审计保留”与“客户版排除”各自的断言，不把两者混写。
- [ ] 不把真实客户数据、运行日志、凭据、token 或机器标识写进测试/文档/提交。
- [ ] 提交 SHA、测试日志哈希、产物哈希；PR 为 Draft，base 正确。

### 独立复核人（不得是实现者）

- [ ] `git rev-parse HEAD` 与作者报告的 SHA 一致；工作树干净。
- [ ] 亲自运行 `tests/test_customer_delivery_boundary_qa.py`，记录 9 条的绿/红，
      不引用作者截图或口头结论。
- [ ] 对照 `排除与待确认` 与 `候选数据审计`：被拒对象是否都可见、原因是否非空。
- [ ] 构造一个 >100 的批量场景，确认第 101 条以后仍被裁决。
- [ ] 检查客户 Sheet 的计数是否只含已准入对象，绑定是否基于资料而非 ID 字符串。
- [ ] 复算发布 ZIP 与交付 manifest 的 SHA-256；确认原 ZIP 未被覆盖。
- [ ] 在干净环境复跑安装测试；把 ensurepip 类环境阻塞记为 BLOCKED，不记 PASS。
- [ ] 检查 diff 中没有真实数据、密钥、客户日志、共享配置改动。
- [ ] 两方签字后才可把 Draft 转 Ready；QA **不自行宣布 PASS**。

### 发布负责人

- [ ] CI 红项已归因：基线既有 vs 本 PR 回归；不能只写“已知失败”。
- [ ] 0.5.5 归档只读；若重建则使用新版本号并在 PR 中更新哈希。
- [ ] 客户版与内部审计版的受众说明随包发布，避免销售把候选表当正式联系人。

---

## 9. 剩余 blocker

| ID | 阻塞 | 关闭条件 |
| --- | --- | --- |
| B1 | `4f87bc33` 无客户版准入闸门（6 红） | G1–G6 在固定 SHA 上全绿 |
| B2 | Lane A 修复未提交、无 PR；工作树会变 | 提交并给出确切 SHA；第二方重跑 |
| B3 | PR #9 CI：`test (3.11)` / `test (3.12)` 均 **23 failed / 7809 passed / 21 skipped**；`research-plugin-e2e` 四条均绿 | 逐条归因到基线或修复；新 SHA 上 CI 绿 |
| B4 | 本机安装测试 2 条因 `ensurepip` 缺失 BLOCKED | 在带 venv 组件的 Python 3.12 环境复跑并记录结果 |
| B5 | PR #9 自述运行把未核验对象计入 `客户总表`（6 个组织，其中 3 个带“已验证场地+渠道”） | 明确客户版口径：HOLD/EXCLUDED 不得计入客户可见组织数；或改称候选表并改变受众 |
| B6 | 0.5.5 归档与最终交付哈希尚未形成双重签字 | 两方独立复算并记录到发布记录 |

---

## 10. 复现

```bash
cd /data/home/vince/workspace/worktrees/marketops/rc-independent-qa-20261020
PYTHONDONTWRITEBYTECODE=1 \
  /data/home/vince/toolchains/marketops-mvp/bin/python \
  -m pytest tests/test_customer_delivery_boundary_qa.py -q -p no:cacheprovider
```

无 pytest 的环境可直接跑 unittest：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_customer_delivery_boundary_qa.py
```

对照另一个 worktree（只读；不写对方目录）：

```bash
PYTHONDONTWRITEBYTECODE=1 \
MARKETOPS_QA_PLUGIN_ROOT=/path/to/plugin \
  /data/home/vince/toolchains/marketops-mvp/bin/python \
  -m pytest tests/test_customer_delivery_boundary_qa.py -q -p no:cacheprovider
```

证据日志：

- `artifacts/skills/automated-testing/marketops-rc-customer-qa-20261020/pytest-final-baseline.log`
- `artifacts/skills/automated-testing/marketops-rc-customer-qa-20261020/pytest-laneA-wip.log`

## 11. 未覆盖 / 不声称

- 未做真实 Provider 调用、真实账号、真实客户运行；因此不证明业务语义正确性。
- 未在本机做完整安装（`ensurepip` 缺失）；Windows、Codex 注册、WorkBuddy 导入
  只有上游 CI 证据，不当作本机实测。
- 未重跑全仓套件（共享东京机并发约束）；CI 的 23 条失败只做归因要求，
  不宣称是本 PR 引入或不是本 PR 引入。
- 本报告不构成发布批准。最终 PASS 需要实现方固定 SHA、第二方独立复跑、
  CI 绿/归因完成与发布负责人签字。
