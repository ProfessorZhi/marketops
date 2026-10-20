# MarketOps RC 测试矩阵（2026-10-20）

状态：**PENDING — 等待 Lane 提交**。执行一次后把结果与证据路径回填到本表。

基线：`4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44`。
解释器：`/data/home/vince/toolchains/marketops-research-plugin-e2e/bin/python`
（插件套件必须用 e2e venv；用 mvp venv 会多出 3 条 error，那是量具差异，不是回归）。

## 1. 需求 → 判据

| # | RC 判据 | Lane | 定向判据 | 状态 |
| --- | --- | --- | --- | --- |
| R1 | release manifest / hash 可复核，既有 0.5.5 zip 不被覆盖 | 集成 | G7：`build_source()` 到仓库外；逐文件哈希、`.sha256`、zip 哈希一致 | PASS（B 合入后；source 164 files，bundle 含 helper；最终重跑留证） |
| R2 | 真正自动生成客户视图 | A+B | G6：`build_delivery` → 四张业务表 → `write_client_view` → `verify_client_view` | PASS（`6b254899`+`76991cb7`；Ran 5 tests OK） |
| R3 | P0 伪造数据绝不进入业务表 | A+Q | A 的 admission 测试 + Q 的边界测试 + G6 负例 | PASS（A `Ran 12 OK`；G6 负例；Q against RC `Ran 9 OK`） |
| R4 | 合格样本能进入，而不是被门禁一刀切 | A+I | A 的正例测试 + G6 正例（4/4 admitted） | PASS（A 正例 + G6 4/4 admitted） |
| R5 | 安装明确 opt-in | C | G3：无 `--apply` 不落盘；非交互无 destination 被拒；全局 Codex 注册默认 no | PASS（`ed4836ba`；Ran 21 tests OK） |
| R6 | SerpApi 不重复 reserve | D | G4：REST 与 embedded 同 signature；同一 operation 只一条 reserve | PASS（`eefd8b6a`；Ran 23 tests OK） |
| R7 | Jev evidence locator | E | G5：locator 2–60、未命中 `JEV_EVIDENCE_LOCATOR_NO_MATCH`、box 拒绝 | PASS（`da300501`；Ran 19 tests OK） |
| R8 | 所有相关定向测试 | 全部 | G1–G6 | PASS（A 15 / B 11 / C 21 / D 23 / E 19 / G6 5；Q 9 全绿） |
| R9 | 完整 offline plugin suite 一次 | 集成 | G8：`python -m unittest discover -s tests -v` | NOT_RUN（刻意等 Lane 完成，避免并发） |
| R10 | 不引入私有数据 / 密钥 | 集成 | G9：只扫本分支新增行；私钥/token 为 FAIL，个人邮箱/手机号为 REVIEW | PENDING |
| R11 | 基线红如实区分 | 集成 | G8 + `--baseline-failures N`；只有“总数等于声明基线”才记 `BASELINE_RED_NOT_NEW` | NOT_RUN |
| R12 | 不合并 main / 不发布 / 不付费 / 不外发 | 集成 | PR 保持 Draft；无 Provider key、无真实邮箱；gate 强制网络开关为 0 | 约束已生效 |

## 2. 执行顺序

```bash
cd /data/home/vince/workspace/worktrees/marketops/rc-integrator-20261020

# 1) 只校验 Lane SHA 与顺序，不落提交
python scripts/rc-20261020/integrate_lanes.py --dry-run

# 2) 集成（冲突即 abort + 停止）
python scripts/rc-20261020/integrate_lanes.py

# 3) 定向与跨 Lane 门禁（不跑全量）
/data/home/vince/toolchains/marketops-research-plugin-e2e/bin/python \
  scripts/rc-20261020/check_release_gate.py \
  --evidence-dir /data/home/vince/artifacts/skills/marketops-rc/20261020

# 4) 全量 offline plugin suite，只跑一次
/data/home/vince/toolchains/marketops-research-plugin-e2e/bin/python \
  scripts/rc-20261020/check_release_gate.py \
  --full-suite --baseline-failures 23 \
  --evidence-dir /data/home/vince/artifacts/skills/marketops-rc/20261020
```

## 3. 结果回填格式

每个判据记录：`run_id`、命令、`Ran N tests`、`failures/errors/skipped`、HEAD SHA、
证据 JSON 路径。红项必须写“基线同样红 / RC 新增红 / 无法判定”三种之一，禁止改测试或加 skip 冒充绿。

## 4. 已知量具注意

- 全量套件耗时受主机负载影响，**耗时不是回归信号**。
- `pytest --maxfail` 会截断失败集；本 gate 用 `unittest discover`，不做 `--maxfail`。
- 插件套件与核心 `tests/` 是两套；`pyproject.toml` 的 `testpaths=["tests"]` 管的是核心套件。
- Q 的 `tests/test_customer_delivery_boundary_qa.py` 在仓库根 `tests/`，是 QA 工件，
  不进入产品分支，也不由插件套件发现。
