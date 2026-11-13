# Provider Benchmark 报告 · `<run_id>`

> 模板。尖括号处填实际值；标记 `（必填）` 的小节不写就等于报告无效。
> 每一张表都必须带 **样本量** 与 **冷/热口径**，否则数字不可解释。

---

## 0. 一句话结论（必填）

`<如果没有可信结论，就写"本次运行不产出结论"并说明原因。不要写"总体来看 A 更好"。>`

---

## 1. 运行元数据

| 项 | 值 |
|---|---|
| run_id | `<...>` |
| 时间 | `<started_at>` → `<finished_at>`（UTC） |
| 模式 | `<offline / live / replay>` |
| 重复次数 | `<n>` |
| 数据集 | `<name>` v`<version>` split=`<split>` |
| 数据集校验和 | `<sha256>` |
| 用例数 | search `<n>` / fetch `<n>` / e2e `<n>` |
| 代码版本 | marketops `<version>` @ `<git_commit>` |
| Python | `<version>` |
| 操作者 | `<pseudonymous id>` |
| 总成本 | `$<...>` |

**数据集变化会使旧结论失效** —— 校验和不同即不可比。

---

## 2. A 层 · Search

口径：`<cold / all samples>` · 每用例 top-k = `<k>`

| provider | cases | P@5 | P@10 | R@5 | R@10 | MRR | nDCG@10 | p95 ms | USD/100 rel. |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

其它指标（unique recall / official rate / diversity / duplicate / language / freshness）：

| provider | unique | official | diversity | duplicate | language | freshness |
|---|---|---|---|---|---|---|
| | | | | | | |

**`n/a` 的原因**（必填，逐条）：
- `<provider>` 的 `<metric>` 为 `n/a`，因为 `<不适用 / 无金标 / 样本为 0>`。

---

## 3. B 层 · Ensemble 与消融

口径：**union across `<N>` providers** —— 与 §2 不可并列比较。

| 配置 | recall@k | 增量 vs 最好单家 | 成本 |
|---|---|---|---|
| `<p1+p2+...>` | | | |

消融（去掉一个 provider 后组合召回的变化）：

| 去掉 | ensemble recall | 去掉后 recall | 下降 |
|---|---|---|---|
| | | | |

下降 ≈ 0 表示该 provider 的工作被覆盖（冗余），**不表示它差**。

---

## 4. C 层 · 端到端

| 配置 | cases | 实体 P | 实体 R | 已验证联系人 P | 已验证联系人 R | 证据完整 | 假阳性率 | 完成率 | $/实体 |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

抽样人工复核：`<n>` 条中 `<m>` 条一致；不一致项见 §8。

---

## 5. 失败清单（必填）

| error_code | 次数 | 涉及 provider | 说明 |
|---|---|---|---|
| rate_limited | | | 限速等待，**不算能力失败** |
| shell_page | | | 返回了内容但判定为壳页 |
| no_content | | | 期望无内容且确实无内容（**不是失败**，单列） |
| timeout | | | |
| parse_error | | | |
| exhausted | | | 额度耗尽 |

---

## 6. 必败类别（必填，单独报告）

| 类别 | 结果 | 结构性原因 |
|---|---|---|
| pdf_scanned | | 无 OCR 适配器；MinerU 恒不可用 |
| 登录墙 | | 期望 `no_content`；返回登录页 = 假成功 |
| 交互展开 | | 无自动化点击路径 |

不单列会让总体成功率既不能改进也无法解释。

---

## 7. 角色与帕累托（必填）

| provider | 能力 | 角色 | 依据 |
|---|---|---|---|
| | | Primary / Secondary / Specialist / Fallback / Not Recommended / **Insufficient Evidence** | 样本 `<n>`，`<指标>` |

帕累托前沿（召回 × 每 100 条相关结果成本）：`<列出前沿上的 provider>`。
被支配的：`<...>`。

**样本 < 30 一律 `Insufficient Evidence`。** pilot 规模达不到，请如实写。

---

## 8. Judge 分块（必填，即使没有 judge）

| 项 | 值 |
|---|---|
| provider / model | `<none / jev / deepseek / other>` / `<model>` |
| blind | `<true/false>` |
| 校准等级 | `calibrated / uncalibrated / insufficient_sample` |
| agreement / FP / FN | `<...>` |
| 弃权率 | `<...>` |
| 作废 verdict 数 | `<...>` |

`uncalibrated` 或 `insufficient_sample` 时，**本节分数不得用于选型**。
judge 分与 §2–§4 的确定性指标**永不加权合并**。

---

## 9. 长期因素备忘（不进评分）

| 因素 | 结论 |
|---|---|
| 覆盖范围 | |
| 来源独立性 | |
| 时效 | |
| 可靠性（近 90 天） | |
| 合规 | |
| 隐私 | |
| 可维护性 | |
| 锁定 | |
| 经济（阶梯 / 超额行为） | |

---

## 10. 这份报告回答不了什么（必填）

- 样本量 `<n>` 不足以支撑 `<...>`；
- `<某市场>` 只有 `<n>` 条用例；
- 金标状态：`draft <n>` / `human_verified <n>` / `disputed <n>` —— `draft` 用例的分数
  不构成结论；
- 未测：`<...>`；
- 未执行：`<...>`（如"未做真实收费调用"）。

---

## 附：复算方法

```bash
python -m benchmark.runner.cli score --dataset benchmark/datasets/pilot \
    --recorded <recorded> --out out/run.json
python -m benchmark.runner.cli report --scorecard out/run.json --out out/run.md
```

同一份 recorded + 同一份数据集必须得到同一张表。
