# benchmark/ —— Provider 评测框架 v1

**当前状态：Phase 1（标准 + 骨架 + 离线验证）。未发起任何真实收费调用，未消耗任何
生产 API 配额，未修改生产业务逻辑，未提交或推送。**

这个目录回答的问题是：

1. 在被测量的能力上，代码里到底有什么？（`PROVIDER_INVENTORY.md`）
2. "哪个 provider 更好"要怎么量，才算一次测量而不是一次印象？（`STANDARD.md`）
3. 金标从哪来，谁有权确认它？（`GROUND_TRUTH_GUIDE.md`）
4. 模型判断在哪一步可以进入结论，在哪一步不行？（`JUDGE_CONTRACT.md`、`JUDGE_CALIBRATION.md`）
5. 怎么跑，怎么扩，怎么证明它没坏？（`EXECUTION_GUIDE.md`）
6. 要真正跑一次，需要多少调用、多少钱、什么前置条件？（`PHASE2_LIVE_PLAN.md`）

---

## 30 秒上手

```bash
# 校验数据集（离线，无凭据）
python -m benchmark.runner.cli validate --dataset benchmark/datasets/pilot

# 用已记录的 provider 输出算一张计分卡（离线，无凭据）
python -m benchmark.runner.cli score --dataset benchmark/datasets/pilot \
    --recorded <recorded 目录> --out out/run.json
```

`<recorded 目录>` 的布局见 `EXECUTION_GUIDE.md` §3。没有它时，前一条命令仍然可用 ——
数据集本身是可校验的独立产物。

---

## 目录

| 文件 | 内容 |
|---|---|
| `STANDARD.md` | 三层结构、全部指标公式、公平协议、角色判定、拒绝条件 |
| `PROVIDER_INVENTORY.md` | 每个 provider 的 DOCUMENTED/IMPLEMENTED/WIRED/TESTED/LIVE 状态 + 10 条已知误读 |
| `GROUND_TRUTH_GUIDE.md` | 标注流程、双人复核、七个标注陷阱 |
| `JUDGE_CONTRACT.md` | judge 的输入/输出/禁止事项/prompt 版本 |
| `JUDGE_CALIBRATION.md` | 校准四指标、三个等级、失败处置 |
| `EXECUTION_GUIDE.md` | recorded 布局、CLI、加 provider/加用例、离线保证 |
| `PHASE2_LIVE_PLAN.md` | 调用清单、成本估算、停止条件（**未执行**） |
| `IMPLEMENTATION_REPORT.md` | 实施报告：验收十问、测试结果、未完成项、风险、Phase 2 条件 |
| `schemas/` | search / fetch / e2e / judge-verdict / scorecard，JSON Schema 2020-12 |
| `datasets/pilot/` | 24 search + 28 fetch + 10 e2e，全部 `draft` |
| `runner/` | 纯 Python，无第三方依赖；`metrics` 无 I/O |
| `templates/` | 报告模板 + 计分卡结构示例 |

---

## 三条设计约束

1. **确定性指标与语义判断分块，永不加权成一个总分。** 计分卡里不存在
   `overall_score` 字段，将来也不会有：加权需要价值判断，而价值判断一旦写进公式就
   不可见了。
2. **算不出来的一律 `null`，不记 0。** `n/a`（没观测到）与 `0`（观测到是零）在业务上
   是相反的结论。
3. **只给 WIRED 的实现打分。** 存在但没接线的代码（`GuardedHttpClient`）、恒不可用的
   兜底（MinerU）、没有入口的机制（`run_shadow`）进 inventory，不进排行榜。

---

## 这个目录现在还不能做什么

- **不能给 provider 排名。** pilot 的 62 条用例全部是 `draft`，且规模远低于
  `STANDARD.md` §5 的 `Insufficient Evidence` 门槛（每能力每市场 ≥ 30 条
  `human_verified`）。
- **不能回答"哪家更便宜"。** 只有 `cost_per_100_relevant_results_usd` 是可比的成本
  口径，而它需要 `human_verified` 的金标。
- **不能替代业务链路的判断。** 它量的是零件；装配起来好不好用是 C 层的事，而 C 层
  目前只有 10 条 draft 用例。

把现在的数字当结论用，得到的是"官方首页在三家搜索引擎里都排第一"。
