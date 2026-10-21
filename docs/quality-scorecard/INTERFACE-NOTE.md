# 接口说明（紧凑版）· W-F ↔ Integrator / 独立 QA

**读这一页就够。** 细节在 `INTEGRATOR-COMPAT.md`（对 I）与 `README.md`（评分语义）。

## 1. F 拥有的模块与入口

| 模块 | 一句话 | 入口 |
|---|---|---|
| `research_ledger_bridge.py` | 集成账本（整数微美元）→ QA 记分卡账本形状（十进制 USD） | `ledger_from_operations(ops, project=, baselines=, meter_by_provider=)`、`operations_from_ledger(ledger, project=)` |
| `research_quality_scorecard.py` | rubric + AF 检查 + 账本 → 确定性记分卡 | `score(**inputs_from_paths(paths))`、`measurement_from_ledger(ledger, accepted_rows=)`、`af7_from_ledger(ledger)` |
| `research_quality_recheck.py` | 独立复核：重算比对 + 从字节重推接受 | `recheck(scorecard=, inputs=)`、`rederive_acceptance(inputs)` |
| `research_run_audit.py` | 运行级审计，八个具名失败类 | `audit_run(root, run_id, paths=)` |

**I 必须提供、不可推导的两项**：`baselines={provider: {b0, floor, meter, source, observed_at}}`
（B0 是战役开始时读一次的值；不给就写 `B0_NOT_SUPPLIED:<provider>`，**绝不凭空造**）；
以及 scorecard 的 `artifacts_dir` —— 一个 `<artifact_id>.bin` 的目录（**不是** store 的哈希命名布局）。

## 2. 信封

**集成侧没有信封类型**（`research_gateway.py` 全文只有一处 "envelope"，是 docstring）。
**更正（2026-10-18，`479f428`）：`research_contract.py` 现在已经存在**（此前几轮它不存在；
`CapabilityRequest` 就在里面）。它仍未定义 `EvidenceEnvelope`，所以权威仍是冻结契约 §3.5 的
16 字段，`research_evidence_store.ENVELOPE_FIELDS` 是唯一实现，两条路径都断言字段集。

adapter 结果**必须支持属性访问**：gateway 用 `getattr(result,"outcome")` 与
`getattr(result,"caveats",())` 读；普通 dict 会被**静默读成 FAILED**。
`research_adapter_jev.AdapterResult(dict)` 已如此。

## 3. 钱的契约（本轮重点）

**敞口 = `max(estimated, reserved)`，统计 `RESERVED` 与 `UNKNOWN_CHARGE_OUTSTANDING` 两种状态。**

- `reserved` 是**已承诺**的钱，估算为 0 不能把它抹掉；只取其一就是"真实敞口变成 0"的成因（曾实测复现）。
- 该定义在**操作级**与 **provider 级**必须是同一个数；两侧不一致本身是缺陷（有测试）。
- provider 词汇表里 `UNKNOWN_CHARGE_OUTSTANDING` 是**映射后**的名字，原始账本写的是 `UNKNOWN`；
  拿原始名去比映射词表会**匹配不到**（已修，有测试）。

**状态语义**（`measurement.state`）：

| 状态 | 何时 | `value` |
|---|---|---|
| `MEASURED` | 全部已结算 + 费率已核实 + 无未结算操作 | 已确认部分 |
| `PARTIAL_UNKNOWN` | 有未对账敞口**或**有未结算预约 | 已确认部分（`all_in_usd` 给全含） |
| `UNKNOWN` | 任一 provider 的费率未核实 | **`null`**（未核实计算另放字段） |
| `BLOCKED` | 没给账本 | `null` |

**任何情况下"未知"都不得变成数字零**：未结算预约即使自身金额为 0，也让该测量**不是** `MEASURED`
（有测试 `test_a_reservation_with_no_amount_at_all_is_still_not_a_final_zero`）。

**对方也在收口（一手读过，非转述）：** 账本侧 `report()` 现在算 `basis_code`
（`OK` / `UNKNOWN_COST_OUTSTANDING` / `PRICE_UNVERIFIED` / `NO_SETTLED_OPERATIONS`），
非 `OK` 时 `cost_per_accepted_output_micros` **返回 `null`**。两侧方向一致。

## 4. QA 应断言什么（可直接抄的命令）

```bash
cd plugins/research-tools/marketops-research-assistant
PY=/home/operator/toolchains/marketops-research-plugin-e2e/bin/python
$PY -m unittest tests.test_wave6_adapter_conformance tests.test_wave3_sample_dataset \
                     tests.test_wave3_negative tests.test_wave3_e2e_audit \
                     tests.test_wave2_ledger_bridge tests.test_wave2_quality_scorecard \
                     tests.test_wave2_quality_recheck tests.test_wave2_jev_refusal_channel \
                     tests.test_wave1_jev_adapter tests.test_wave1_evidence_provenance
```

- **205 条**（2026-10-18 实测；命令里再加入 `tests.test_wave6_adapter_conformance`），
  在 typesafe-sdk **0.7.1** 与 **0.7.5** 上均 `OK`（0.7.1 多一条 Box 的 skip）。
- 判据要**绑 exact code SHA**：本轮为 `feat/marketops-capgateway-jev-evidence-20261017` 的提交，
  分支头请用 `git ls-remote --heads origin <branch>` 读。
- **检测器必须能响**：`tests/test_wave3_e2e_audit.py` 的每个类都有一次注入；
  中性化任一检测器会让**恰好那一条**转红。夹具的干净运行必须**零缺陷**。
- 集成侧**尚未**吸收这些提交（`git merge-base --is-ancestor` 实测为假）；
  在集成侧复现之前，上面的数字只属于 F 分支。

## 5. 未做的

无联网、无凭据、无账户、无付费调用；未改 I 的网关、W-B 的账本、QA 的 `qa_gate/` 与 `docs/wave1-qa/`；
未开 PR、未合并、未 force push。

## 6. Jev adapter 与 PR #78 组合根的一致性（2026-10-18，`479f428`）

`research_wiring` 原先报 `typesafe_jev` **NON_CONFORMANT**（`PROVIDER_MISMATCH jev!=typesafe_jev`、
`EXECUTE_SIGNATURE_NOT_CALLABLE_AS_execute(request)`、`NO_CANCEL_METHOD`）。已在
`research_adapter_jev.py` 内修正，**未动 I 的任何文件**：

| 项 | 现在 |
|---|---|
| `adapter.provider` | **`typesafe_jev`**（registry/ledger 的路由键）；`jev` 保留为 `LEGACY_PROVIDER` |
| `capabilities()` | `jev.choice` / `jev.noul` / `jev.score` / `jev.box`（registry 的 id） |
| `execute(request)` | duck-typed 读 `capability_id` / `params` / `run_id` / `operation_id` |
| `params` | 严格按 registry 的 allowlist：choice `{artifact_id,question,options}`、noul `{artifact_id,question}`、score `{artifact_id,question,scale}`、box `{artifact_id,target}` |
| `cancel(upstream_ref)` | **显式安全空操作**：Jev 没有可取消的远端对象，返回 `BLOCKED` + `CAPABILITY_NOT_AVAILABLE` + `adapter_local:JEV_CANCEL_NOT_SUPPORTED`，**绝不假装取消成功** |
| 派发前失败 | **全部是 §5.3 拒绝**（抛出 `JevRefusal` 或返回 `BLOCKED`），且**不构造 SDK client** ⇒ 不会在 reserve 之后变成假 `UNKNOWN` |

**组合根必须知道的一件事：** `assemble_adapters()` 用 `adapter_class()` **无参**构造 —— 这样
一致性检查过得了，但 `execute` 会以 `JEV_DATA_ROOT_ABSENT`（= `CAPABILITY_NOT_AVAILABLE`，
BLOCKED/NO_CHARGE，不是 UNKNOWN）拒绝，因为没有数据根就读不到已存证据。
要真正跑 Jev，构造时需给 `root=`（数据根）与 `api_key=`；**root 不从 request 里取**
（否则调用方就能指定另一个 store）。

**账本 provider 名的两个词表：** registry/ledger 用 `typesafe_jev`，W-QA 的
`cost-ledger.schema.json` 枚举用 `jev`。`research_ledger_bridge.PROVIDER_ALIASES` 做这一处转换，
baseline 两种拼写都认。

**实测收据（在 `479f428` 的树里跑）：** `research_wiring.probe()` → `WORKERS_PRESENT`、
`missing_by_owner {}`；`assemble_adapters()` 的 `failures {}` 且 **五家全部 built**，
`typesafe_jev` 的 `provider == 'typesafe_jev'`；`tests.test_wave6_adapter_conformance` **20 条 OK**（零 skip）。

## 7. 证据链工作簿（2026-10-18 新增，F 自有 `research_delivery.py`）

```python
delivery.write_evidence_workbook(root, run_id, delivery_id,
                                 ledger=<B 的 UsageLedger>, project=<ledger project>,
                                 reviews=<review 决定列表，默认空>)
```

**五张表**（英文列名，顺序固定）：`sources` / `claims` / `evidence` / `ledger_receipts` / `review`。
产物：`Evidence_Chain.xlsx` + 每表一份 CSV + `MANIFEST.json`（含逐文件 sha256）。

**三条不变量（都有从真实单元格读回的测试）：**

1. **`evidence_id` / `ledger_id` 是从 store 复制的，导出时绝不重算。**
   `evidence` 表的 id 集合必须等于 `research_evidence_store.stored_envelopes()` 的 id 集合；
   没有 ledger 链接的信封 `exportable=0` 且 caveats 带 `LEDGER_NOT_WIRED`。
2. **claim 只有经记录的复核决定才能成为最终态。** 有支撑但未复核 → `BLOCKED_NOT_REVIEWED`；
   **无支撑的 claim 即使复核者接受也停在 `BLOCKED_UNSUPPORTED`**（复核行仍被记录）。
   `APPROVED` 只经由 `reviews` 里的一条 `decision="ACCEPT"` 到达。
3. **账本状态逐字复制。** `UNKNOWN` 仍是 `UNKNOWN`、`actual_usd` 为空；`NO_CHARGE_PROVEN`
   不会被写成 `SETTLED`；没有任何求和把状态推进。

**I 接线时需要给的：** 一个 `ledger`（duck-typed：`operations(project=, run_id=)`）与
`project` 名；`reviews` 由人工复核步骤产生（默认空 = 全部 blocked）。

**本轮实测（在集成产品码 `be7f3e8` 的树里，用真 B 账本）：**
`tests.test_wave7_evidence_workbook` **20 条 OK**（三形状：默认/已批准/无支撑）。

**仍然不是 LIVE：** 三个机构、三个页面、三个 claim 全部是合成的；无网络、无凭据、
无厂商调用、无模型调用、无客户数据。它证明的是链路与闸门，不是调研质量。
