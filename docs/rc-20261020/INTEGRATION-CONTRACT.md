# MarketOps RC 跨 Lane 集成契约（2026-10-20）

状态：**DRAFT / 等待 Lane 提交**。本文件由唯一 Integrator 维护，不宣布正式发布。

- 仓库：`https://github.com/ProfessorZhi/marketops`
- 基线：`feat/bruce-plugin-delivery-20261020@4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44`（PR #9）
- RC 分支：`rc/marketops-release-candidate-20261020`
- RC worktree：`worktrees/marketops/rc-integrator-20261020`
- 最终 PR base：`feat/bruce-plugin-delivery-20261020`（**不是** `main`）

## 1. 所有权与边界

| Lane | 工作树 | 分支 | 拥有的产品路径 | 集成方式 |
| --- | --- | --- | --- | --- |
| A 证据门禁 | `rc-gate-20261020` | `fix/marketops-rc-delivery-gate-20261020` | `scripts/research_delivery.py`、`tests/test_delivery_research_delivery.py`、`tests/test_grading_admission_gate.py` | cherry-pick |
| B 客户视图 | `rc-clientview-20261020` | `fix/marketops-rc-client-view-20261020` | `scripts/research_client_view.py`、`tests/test_delivery_client_view_generation.py`、`references/CLIENT_VIEW_CONTRACT.md` | cherry-pick |
| C 安装器 | `rc-installer-20261020` | `fix/marketops-rc-installer-20261020` | `install.sh`、`scripts/setup_clients.py`、`tests/test_install_optin_security.py` | cherry-pick |
| D 搜索去重 | `rc-search-cost-20261020` | `fix/marketops-rc-search-cost-20261020` | `scripts/search_canonical.py` 及 Lane 测试 | cherry-pick |
| E Jev 定位 | `rc-jev-target-20261020` | `fix/marketops-rc-jev-target-20261020` | `scripts/research_adapter_jev.py`、`tests/test_jev_targeted_excerpt.py` | cherry-pick |
| Q 独立 QA | `rc-independent-qa-20261020` | `qa/marketops-rc-customer-gate-20261020` | `tests/test_customer_delivery_boundary_qa.py`（QA 工件，不入产品分支） | 只取裁决 |

集成者只新增以下自有文件，**不修改任何 Lane 拥有的文件**：

- `docs/rc-20261020/*`
- `scripts/rc-20261020/*`
- `plugins/research-tools/marketops-research-assistant/tests/test_install_integration_gate.py`

## 2. 跨 Lane 接口

### 2.1 A → B（客户视图的唯一数据源）

A 的 `research_delivery.build_delivery(root, run_id)` 返回 `(list[Sheet], dict)`：

- `CLIENT_BUSINESS_SHEETS = ("客户总表", "联系人路线", "场地与资产", "人员岗位")`
- `INTERNAL_AUDIT_SHEETS = ("排除与待确认", "证据索引", "本轮说明", "来源与查询", "候选数据审计")`
- 报告键：`counts`、`admission`（含 `decisions` / `admitted` / `rejected` / `reason_counts`）、`admission_status`、`client_business_tables_admitted_only`
- `ADMISSION_SCHEMA = "marketops.research.delivery-admission.v1"`

B 的 `research_client_view.write_client_view(destination, sheets, *, label, generated_at=None)`：

- **只接受已过滤的业务 Sheet**；HOLD / CONFLICT / 无引文 / 重复业务键都会拒绝，而不是静默丢弃。
- 输出 `client-view/` 的 XLSX + 每表 CSV + `MANIFEST.json`，`status = CANDIDATE_NOT_APPROVED`、`send_authorized = false`、`formal_sop_claimed = false`。

**连接规则（集成者执行，不改 `research_delivery.py`）：**

```python
sheets, report = delivery.build_delivery(root, run_id)
business = [s for s in sheets if s.name in delivery.CLIENT_BUSINESS_SHEETS]
receipt = client_view.write_client_view(
    destination / "client-view", business,
    label=run_id, generated_at=fixed_or_now)
assert client_view.verify_client_view(destination / "client-view")
```

- 绝不把九张表全量传给 B：`排除与待确认` 必然含 HOLD/冲突文字，B 会整份拒绝。
- 父级 delivery `MANIFEST.json` 只并入 B 返回的 `artifact/label/status/counts/files`；运行时字段
  `directory/xlsx_path/manifest_sha256/verified/replayed` 不入父 manifest。
- 业务表行数必须与 `report["counts"][sheet]` 一致。
- `research_client_view.py` 会被 `build_client_bundles.py` 的 `scripts/*.py` 通配拷贝自动带入；
  gate 会实际构建一次 bundle 并断言该文件在包内，而不是相信注释。

### 2.2 C 安装器 opt-in

- `setup_clients.py` 没有 `--apply` 时不得创建安装目录或 venv；依赖下载、浏览器、Codex 注册必须同时要求 `--apply`。
- `install.sh` 在**非交互**且未给 destination 时必须 `INSTALL_BLOCKED: INSTALL_DESTINATION_REQUIRED`，且不落文件。
- 全局 Codex 注册默认 **no**；只有 `--register-codex`（或交互时明确回答 y）才触碰全局配置。
- 安装日志不得把子进程输出原样回显（可能含 token/本地路径）。

### 2.3 D 搜索跨传输去重

- 同一 `(provider, mode, query, country, language, limit, page, account)` 的 REST 与 embedded 官方 MCP
  必须得到同一个 `search_canonical.operation_signature`。
- 同一 `operation_id` 只允许一条 reserve；`calls_reserved` 恰好为 1，账本只有一行。
- provider 失败也不得盲目重试；reservation 先于调用发生。

### 2.4 E Jev 证据定位

- `evidence_query` 是 locator，不是内容：2–60 字符、可打印、至少一个字母数字。
- 非空 locator 必须在已保存字节中命中；未命中 → `JEV_EVIDENCE_LOCATOR_NO_MATCH`，不得退化成“取开头”。
- `jev.box` 不接受 `evidence_query`（图像没有文本跨度）。
- 派发前从磁盘重新抽取并校验 `span_sha256`。

## 3. 集成顺序与冲突政策

顺序：A → B → C → D → E。理由：A 定义客户表语义，B 依赖它；C/D/E 相互独立。

每次只 cherry-pick **清单中精确 SHA**：

1. 校验该 SHA 存在，且其 merge-base 与基线一致；
2. `git cherry-pick <sha>`；
3. 记录 `source_branch`、`source_sha`、`rc_sha` 到 `docs/rc-20261020/LANE-LEDGER.md`；
4. 任何冲突立即 `git cherry-pick --abort`，RC 分支回到干净状态，停止并请求审阅。

禁止：`reset --hard`、`clean`、`stash`、force-push、删除/归档任何工作树或分支、改写 Lane 分支。

## 4. 发行门禁（全部离线）

| ID | 判据 | 执行 |
| --- | --- | --- |
| G1 | A 的定向测试 | `python -m unittest tests.test_delivery_research_delivery tests.test_grading_admission_gate -v` |
| G2 | B 的定向测试 | `python -m unittest tests.test_delivery_client_view_generation -v` |
| G3 | C 的定向测试 | `python -m unittest tests.test_install_optin_security -v` |
| G4 | D 的定向测试 | Lane D 提交的模块名 |
| G5 | E 的定向测试 | `python -m unittest tests.test_jev_targeted_excerpt -v` |
| G6 | A+B 集成 | `tests/test_install_integration_gate.py` |
| G7 | release manifest/hash | `build_client_bundles.build_source()` 到仓库外临时目录；逐文件哈希 + `.sha256` + zip 一致 |
| G8 | 完整 offline plugin suite | `python -m unittest discover -s tests -v`（只跑一次，不并发） |

G8 基线红必须逐条列出并与 `4f87bc33` 上的同一命令对照；不得改测试、不得标记 skip 冒充通过。
2026-10-20 实测（RC 树，HEAD `09494a66`）：`Ran 2135 tests, 0 failures, 0 errors,
30 skipped`。用户提到的“主线 23 failures”未在本基线分支复现；`main` 未复跑。

## 5. 非目标（本轮硬边界）

- 不合并 `main`、不发布、不部署、不重启生产。
- 不覆盖既有 `0.5.5` zip；RC 产物只写仓库外临时/证据目录。
- 不调用真实 Provider、不产生付费调用、不向真实客户邮箱外发。
- 不引入私有数据：测试与夹具只允许 `example.org` / `example.net` 等合成标识。
- 不宣布正式发布；PR 保持 Draft。
