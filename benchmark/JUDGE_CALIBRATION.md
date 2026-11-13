# Judge 校准指南

一个没有校准过的 judge 不是测量工具，是一个观点。本文规定怎么把它变成可用的量具，
以及校准失败时怎么办。

实现：`benchmark/runner/judge.py::calibrate` / `calibration_grade`。
CLI：`python -m benchmark.runner.cli calibrate --verdicts … --labels …`。

---

## 1. 校准在做什么

同一批用例，两条独立的判断：

- **judge** 的 verdict（盲评、带证据）；
- **人** 的标签（`human_labels`，同一条用例的 `overall` 分数 0–1，可带维度分）。

比的是：**把两者都二值化之后，它们是否给出同一个结论。**

二值化：`score >= 0.6` → 可用。阈值可配（`--threshold`），但**两侧必须用同一个**。
用不同阈值比较，等于用两把不同的尺子量同一根棍子。

|  | 人：可用 | 人：不可用 |
|---|---|---|
| **judge：可用** | TP | **FP** |
| **judge：不可用** | **FN** | TN |

---

## 2. 四个输出，四个不同的问题

| 指标 | 公式 | 回答的问题 |
|---|---|---|
| `agreement` | (TP+TN) / (TP+FP+TN+FN) | 两者同意的比例 |
| `false_positive_rate` | FP / (FP+TN) | judge 说"行"、其实不行的比例 |
| `false_negative_rate` | FN / (FN+TP) | judge 说"不行"、其实行的比例 |
| `mean_absolute_error` | 平均 \|judge − 人\| | 分数本身差多远 |

**`agreement` 单独看会被骗。** 一个永远说"不可用"的 judge 在 80% 用例都不相关的
样本上会得到 0.80 的一致率，但它把每一个好结果都判死了。所以：

- `false_positive_rate` 是**成本**风险：放行了一个错的结果；
- `false_negative_rate` 是**机会**风险：埋掉了一个对的结果；
- 两者必须分开报告，**不能合成一个 F1**。合成之后就分不清"太宽"还是"太严"。

按维度还有 `per_dimension_mae`：某个维度系统性偏高（如 `source_authority` 总是给人
0.9），说明那个维度的定义在 judge 那里和在标注员那里不是同一件事 —— 要改的是定义。

---

## 3. 等级判定

```python
if sample_size < 10 or agreement is None:      -> "insufficient_sample"
if agreement < 0.80:                            -> "uncalibrated"
if false_positive_rate > 0.20:                  -> "uncalibrated"
otherwise                                       -> "calibrated"
```

三条阈值的理由：

| 阈值 | 理由 |
|---|---|
| 样本 ≥ 10 | 少于 10 条时，一条翻转就能把一致率挪 10 个百分点；那不是统计量 |
| agreement ≥ 0.80 | 低于此值，judge 与人的分歧已经和"人和人的分歧"同量级（双人标注的常规上限约 0.85，`GROUND_TRUTH_GUIDE.md` §3） |
| FP ≤ 0.20 | **先管假阳性**。假阳性会被当成证据往下游走；假阴性只是丢了一个候选，代价不对称 |

**样本量按"已判决"计**（`sample_size` 是 verdict 与 human label 都存在的用例数，
`abstentions` 单列）。弃权率高不扣分，但要在报告里写清：一个弃权 50% 的 judge，
即使剩下的一半全对，也只能覆盖半个数据集。

---

## 4. 分层校准，不要混层

`agreement` 是**跨层混算会失去意义**的指标：

- search 层的 `final_answer_quality` 与 fetch 层的同名维度不是一件事；
- fetch 层的用例大多是二元的（拿到正文 / 没拿到），agreement 天然偏高；
- e2e 层的分数分布更接近连续。

**规则：每层各自校准、各自判等级。** 计分卡里的 `judge.calibration` 是全量汇总，
仅作总览；选型结论只能用**对应层**的校准等级。

---

## 5. 操作流程

```
1. 从 development / validation 抽样：每层 ≥ 20 条，覆盖各 task_type
2. 人对每条给 overall（0–1）与需要校准的维度分 —— 与 judge 完全同一批用例
3. 跑 judge，得到 verdicts（必须 blind=true）
4. python -m benchmark.runner.cli calibrate --verdicts v.json --labels h.json
5. 读等级；uncalibrated 就回去看 per_dimension_mae 找系统性偏差
6. 调整（改维度定义 / 改 prompt / 换模型），版本号 +1，重跑
7. 只有在 calibrated 之后，才允许把 judge 分写进结论表
```

**holdout 参与校准，但不能参与调参。** 正确顺序是：在 development 上反复校准直到
`calibrated`，然后拿 holdout 的 10–20 条做一次**只读**确认。若 holdout 上掉到
`uncalibrated`，说明校准过拟合了 development，结论作废。

---

## 6. 校准失败的三种情形与处置

| 现象 | 含义 | 处置 |
|---|---|---|
| agreement 低但 FP、FN 都不高 | 分数尺度不同（judge 普遍偏低 0.1） | 允许整体平移后再校准，但要**在报告里写明平移量** |
| FP 高 | judge 太宽，把壳页/错误联系人当成功 | 收紧证据要求；提高阈值；检查 `quote` 校验是否生效 |
| FN 高 | judge 太严，或 payload 被截断 | 检查 `truncated` 比例；截断率高的用例根本不是 judge 的判断，是输入不足 |
| 某维度 MAE 显著高于其它 | 该维度的定义在两侧不一致 | 改定义并升版本号，不要靠 prompt 打补丁 |
| 弃权率高 | payload 不足，或指令要求太严 | 先看 `abstain_reason` 的分布；如果是截断，加长 `max_chars` |

**"平移后再校准"必须留痕。** 一个被平移过的 judge 与一个原生一致的 judge 不是同一件
东西，读者需要知道这 0.1 是校准出来的还是本来就那样。

---

## 7. 校准不通过的 judge 还能干什么

可以干这些，且只能干这些：

- 作为**第二意见**：与确定性指标并列展示，标注 `advisory only`；
- 作为**发现工具**：judge 与确定性指标分歧最大的那 10 条，是人最该先看的 10 条；
- 作为**标注辅助**：它给出的候选让人去确认（但确认者仍必须打开页面）。

不能干这些：

- 进任何排名；
- 进任何"provider A 更好"的结论；
- 被引用为"质量已量过"。

---

## 8. 一个具体的陷阱

`validate_verdict` 用 `quote[:120] in json.dumps(payload)` 做子串校验。这意味着：

- 一个被截断到 6000 字符的正文，其后半段**无法被引用为证据**；
- 一个 judge 若引用了截断之外的内容，verdict 直接作废 —— 这是对的，因为那段内容
  它根本没看到。

副作用：**`max_chars` 太小时，作废率会升高，看起来像 judge 不行。** 先看作废原因，
再怀疑 judge。这类现象在 `insufficient_sample` 里不会暴露（被丢弃的 verdict 不进
`sample_size`），所以作废率必须单独记 —— 它是输入不足的信号，不是 judge 质量的信号。
