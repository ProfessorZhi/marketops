# MarketOps RC QA Blocker 台账（2026-10-20）

状态：**OPEN / 等待 Lane 交付**。只记录事实与判据，不把“未完成”写成“通过”。

## B-01 五条 Lane 均尚未提交（阻断集成）

- 观察时间：2026-10-20 13:07–13:10 CST。
- A/B/C/D/E 工作树 HEAD 都停在基线 `4f87bc33`；改动全部是工作树内的 modified/untracked 文件。
- 文件 mtime 持续更新（A 13:06、B 13:07:15、E 13:07:48、Q 13:07:49、C/D 随后开始），
  说明 Agent 仍在工作。
- 结论：**不 cherry-pick、不复制工作树内容、不猜测 SHA**。集成脚本此时返回
  `INTEGRATION_PENDING`（exit 4）。

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

## B-06 D 的测试模块尚未命名

- Lane D 目前只改了 `search_canonical.py`，尚无测试文件。
- 结论：`check_release_gate.py` 按意图自动发现 `test_*search*{dedup,transport,reserve,cost}*.py`；
  找不到即 `G4-D = BLOCKED`，不跳过。

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
