# Judge Contract（`marketops.judge.v1`）

判定器（judge）只做一件事：**在确定性指标算不出来的地方给出语义判断**，并且让这个判断
可被检验、可被否决。

本文是 judge 与基准之间唯一的契约。实现：`benchmark/runner/judge.py`，
输出 schema：`benchmark/schemas/judge-verdict.schema.json`。

---

## 1. 输入：judge 看得到什么

### 1.1 盲化

| 会进 payload | 绝不进 payload |
|---|---|
| `case_id`、`market`、`language`、`query`、`task_type`、`top_k`、用例 note | provider 名字、端点、模型名 |
| 候选结果：`rank`、`title`、`url`、`snippet`（截 400 字符） | 延迟、成本、`from_cache`、额度 |
| 候选正文（fetch 层，截 6000 字符，标 `truncated`） | 其余候选的标签映射 |
| 交付物（e2e 层） | 操作者、运行时间 |

**候选标签**是 `candidate_a` / `candidate_b` / …，由
`blind_labels(providers, salt=...)` 生成：

```python
random.Random(sha256(salt, *sorted_names)[:16]).shuffle(names)
```

两个性质：

1. **同一 provider 不会永远是 `candidate_a`**。若位置固定，judge 会学到"A 通常更好"，
   而这不是它该学的东西。抖动由 `salt`（每用例不同）驱动。
2. **可复现**。同一个 salt 生成同一份标签，所以重放逐字节一致。

**标签映射**由 `write_sealed_mapping()` 单独落盘，**不进 judge 请求**。分析者在打分
结束后才打开它。这就是"密封"的含义：不是因为文件权限，而是因为请求里从未出现它。

### 1.2 fetch 层的 payload

```json
{"prompt_version": "marketops.judge.v1", "layer": "fetch",
 "case": {"case_id": "...", "task_type": "js_rendered", "url": "...",
          "expected_outcome": "content", "notes": "..."},
 "candidates": [{"candidate": "candidate_a", "extracted_text": "...", "truncated": false}],
 "instructions": ["Judge whether the extracted text is the page's real content or a shell.",
                  "Quote the exact span that decides it; if none exists, abstain."]}
```

### 1.3 e2e 层的 payload

judge 看到的是**业务交付物**（entities + contacts + evidence_url），不是中间 API 响应。
指令里明写："每一个声称的联系人都必须带一个它确实出现过的页面。"

---

## 2. 输出：一条 verdict

必须是一个 JSON 对象（`judge-verdict.schema.json`，`additionalProperties: false`）。
`case_id` / `layer` / `blind` 三项由基准侧校验，不匹配即作废。

### 2.1 七个维度

| 维度 | 什么时候有值 | 什么时候必须 `null` |
|---|---|---|
| `search_relevance` | search | 不是 search 层 / payload 不足以判断 |
| `source_authority` | search、e2e | 无法判断域的性质 |
| `content_completeness` | fetch、e2e | 无 `min_text_chars` 参照 |
| `evidence_quality` | 三层 | 没有任何可引证据 |
| `entity_attribution` | e2e | 无实体归属问题 |
| `freshness` | 有时效要求的用例 | 用例未声明时效要求 |
| `final_answer_quality` | 三层 | 无交付物 |

**不适用的维度必须给 `score: null`，不能给 0。** 0 是"做了但很差"，`null` 是"这一格
没法判"。把不适用记成 0 会让一个只在 search 层有意义的维度污染 fetch 层的均值。

### 2.2 每个维度必须带证据

```json
{"score": 0.8, "confidence": 0.9,
 "evidence": [{"quote": "Contact: service.presse@fff.fr", "source_ref": "candidate_a.results[2].snippet"}],
 "rationale": "..."}
```

校验规则（`validate_verdict`）：

- `score` ∈ [0, 1] 或 `null`；
- 有分的维度**至少一条 evidence**，否则整条 verdict 作废；
- `quote` **逐字**出现在 payload 里（校验用前 120 字符做子串比对）；
- 释义、概括、翻译**不是证据**。

第 3 条是这个契约的核心。没有它，judge 可以凭"感觉相关"给分，而"感觉"无法被复核。
有了它，任何一条分数都能被指向一段原文。

### 2.3 弃权是一等结果

```json
{"abstained": true, "abstain_reason": "payload truncated at 6000 chars; the contact block is not in the excerpt"}
```

- 证据不足、payload 被截断、维度不适用 → **弃权**；
- 弃权必须给理由，没有理由的弃权作废；
- **弃权不算"不相关"**。`verdict_to_binary()` 对弃权返回 `None`，在校准里被排除，
  而不是记成负样本。

理由：如果把弃权记成负样本，一个"什么都不判"的 judge 会显得精度极高。排除它，
弃权率就变成一个必须单独报告的指标 —— 一个弃权 60% 的 judge，即使剩下 40% 全对也
没有用。

### 2.4 成本要记

`usage.cost_usd` 是可选的，但**应当填**。judge 的成本进计分卡的 `judge` 块，
不计入 provider 成本 —— 它属于测量开销，不属于被测量的东西。

---

## 3. 禁止事项

| 禁止 | 为什么 |
|---|---|
| 猜 provider 身份并据此打分 | 盲评失效；猜到的身份与"更好"无因果 |
| 用外部知识补 payload 里没有的事实 | 那个事实没有证据链，等于自己造金标 |
| 输出一个总分并声称是结论 | 七维加权需要价值判断；权重写进公式就不可见了 |
| 对不适用的维度给 0 | 见 §2.1 |
| 省略 evidence 只给 rationale | 无法复核 |
| 用同一模型既生成金标又打分 | 误差同向，一致率虚高（见 `GROUND_TRUTH_GUIDE.md` §1.1） |
| 在 holdout 上调 prompt | 等于把 holdout 变成 development |

---

## 4. Prompt 版本

`JUDGE_PROMPT_VERSION = "marketops.judge.v1"` 写进每份 payload 与每份 verdict。

**只要 instruction 文本、维度定义、截断长度、标签策略之一变化，版本号必须变。**
理由：不同版本的分数不可比。一个"v1 judge 给 0.72"和"v1.1 judge 给 0.68"放在同一张
表里，读者会以为 provider 退步了。

---

## 5. 可用的 judge 实现

| provider 值 | 说明 |
|---|---|
| `jev` | 核心 `JevDecisionProvider`（`typesafe_sdk`）。**同时是业务链路的一部分**，须遵守 §3 最后一条 |
| `deepseek` | 插件侧可用的判定后端；须声明 `model` |
| `other` | 任何被声明的外部模型 |
| `none` | 本次运行**不做语义判定**，只报告确定性指标 |

`judge.provider = "none"` 是合法且常见的状态。Phase 1 的全部运行都应当是这个值 ——
离线状态下没有做任何语义判定，计分卡里会留一行
`judge: none -- deterministic metrics only`。

---

## 6. judge 不作数时的处置

判分链路的顺序是固定的：

```
verdict ──validate──> 结构合法？
        ──calibrate──> agreement ≥ 0.80 且 FP ≤ 0.20 且样本 ≥ 10？
        ──grade──────> calibrated / uncalibrated / insufficient_sample
```

| 等级 | 报告里怎么写 |
|---|---|
| `calibrated` | judge 分可用于结论，与确定性指标**分块**呈现 |
| `uncalibrated` | 分照列，标 `advisory only`，**不得用于选型** |
| `insufficient_sample` | 不列出 judge 分，只写样本量 |
| 校验失败 | 整条 verdict 丢弃，计入 `invalid_verdicts`，不影响确定性指标 |

**判定器失败不阻断基准。** 确定性层永远独立可算 —— 这是把两者分块的最实际收益。

校准方法见 [`JUDGE_CALIBRATION.md`](JUDGE_CALIBRATION.md)。
