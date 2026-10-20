# MarketOps RC QA Blocker 台账（2026-10-20）

状态：**OPEN / 等待 Lane 交付**。只记录事实与判据，不把“未完成”写成“通过”。

## B-01 Lane 交付状态（分批集成中）

- 2026-10-20 13:08:52：Lane B 提交 `76991cb7`（client-view，3 文件 / +1007）。
- 2026-10-20 13:09:35：Lane E 提交 `da300501`（Jev locator，2 文件 / +461/-18）。
- 2026-10-20 13:13:07：Lane C 提交 `ed4836ba`（installer opt-in，6 文件 / +844/-63）。
- 2026-10-20 13:15:36：Lane A 提交 `6b254899`（delivery admission，3 文件 / +816/-122）。
- 四者工作树均已干净；已按精确 SHA cherry-pick 为 RC
  `b0749936` / `38c717df` / `533d706e` / `1faa6656`。
- 定向测试：B `Ran 11 tests OK`；E `Ran 19 tests OK`；C `Ran 21 tests OK`；
  A `Ran 3 + 12 tests OK`；A+B 跨 Lane `Ran 5 tests OK`。
- 截至 13:17，D/Q 仍未提交；Q 在 13:13:40 仍有文件写入。
- 结论：不复制工作树内容、不猜测 SHA。A/C/D 提交前，集成脚本对它们是
  `INTEGRATION_PENDING`；已交付的 B/E 不阻塞后续追加。

## B-02 `origin/feat` 曾短暂指向非基线提交（已澄清）

- fetch 前本地 `origin/feat/bruce-plugin-delivery-20261020` 为 `9b659fe`，且不是基线的后代；
  GitHub API 与 fetch 后均为 `4f87bc33`（基线本身）。
- 结论：基线以 `4f87bc33` 为准；PR #9 的 headOid 也是 `4f87bc33`。无动作。

## B-03 C 工作树含字面 `~/` 未跟踪目录

- 路径：`rc-installer-20261020/plugins/research-tools/marketops-research-assistant/~/`
- 这是安装器测试可能产生的路径展开残留，**不是产品文件**。
- 结论：cherry-pick 只取提交；若该目录进入 Lane C 的提交，停止并请求 Lane C 清理，
  集成者不删除、不修改 Lane 工作树。

## B-04 B 契约中“打包清单未含 helper”的说法与实际实现不一致

- `CLIENT_VIEW_CONTRACT.md` §7 说 `build_client_bundles.py` 的清单没有
  `research_client_view.py`；实际实现是 `scripts/*.py` 通配拷贝（只排除 `cc_host_probe.sh`）。
- 结论：不靠注释判断；G7 会真实构建 connector bundle 并断言
  `scripts/research_client_view.py` 在包内。若断言失败，再按缺陷处理。

## B-05 A/B 接缝的误拒风险（需 G6 实证）

- B 的 `_admit` 在任一单元格含 `HOLD` / `CONFLICT` / `冲突`，或状态列精确等于
  `APPROVED/READY/VERIFIED/FINAL` 时整份拒绝。
- A 当前 admitted 行使用 `CANDIDATE_NOT_APPROVED`、`CANDIDATE_NOT_VERIFIED`、
  `PENDING_SOP_QUALIFICATION`、`REVIEW_REQUIRED`，预期不触发。
- 结论：G6 用合法行 + 伪造行混合夹具实证；若触发，停止并交 A/B 双方裁决，
  集成者不私改任一侧过滤规则。

## B-06 D 的测试尚未提交

- Lane D 工作树已有未跟踪的 `tests/test_serpapi_cross_transport_no_double_charge.py`，
  但 HEAD 仍在基线，尚未提交。
- 结论：`check_release_gate.py` 按意图自动发现 `test_*` 中含
  `transport` / `double_charge` / `dedup` / `reserve` 的文件；找不到即
  `G4-D = BLOCKED`，不跳过。D 提交后才可集成。

## B-07 全量 offline plugin suite 尚未运行

- 为遵守“不要高并发运行完整 pytest”，Lane 活跃期间不跑全量。
- 用户声明基线可能有 23 failures；本机尚未复核。
- 结论：Lane 全部提交后只跑一次，使用 `--baseline-failures 23`；
  红项逐条区分“基线同样红 / RC 新增红 / 无法判定”，不改测试、不加 skip。

## B-08 版本号仍是 0.5.5，RC 产物不得按该名称发布

- `plugin.json` / `build_client_bundles.META["version"]` 均为 `0.5.5`。
- 结论：gate 只写仓库外临时目录，绝不覆盖既有 `0.5.5` zip；PR 保持 Draft，
  不宣布正式发布。版本号变更属于发布决策，不在本轮静默执行。

## B-09 真实 Provider / 付费 / 外发均未启用

- G6 / G3–G5 全部离线；gate 将 `MARKETOPS_*_ALLOW_NETWORK` 与调用上限设为 0。
- 结论：本轮没有真实 Provider、付费调用、生产部署或真实客户邮箱外发。

## B-10 B 的测试夹具含合成私钥字符串（REVIEW，非泄露）

- `tests/test_client_view_generation.py:205` 含一个 RSA 私钥头 + `MIIBsynthetic`
  的合成夹具字符串，用于测试 helper 拒绝私钥材料；本文档刻意不复制该密钥头原文。
- 判定：路径在 `tests/` 且正文含 `synthetic`，门禁记为
  `PRIVATE_KEY_SYNTHETIC_TEST_FIXTURE`（REVIEW），不是 FAIL。正文不是可用密钥。
- 与仓库惯例的差异：`REDACTION.md` 规定合成夹具用行内 `# gitleaks:allow`；该行没有。
  gitleaks 未接入 CI workflow，因此不阻断本轮。
- 结论：不静默改 B 的文件。最终集成时由 B 或集成者按 `REDACTION.md` 惯例补行内标记，
  或在 `.gitleaks.toml` 增加与既有条目同形状的 path+match allowlist；二选一需在 PR 说明。
