# 质量评分脚手架 — 证据引用 → 实体/角色 → 有效结果 → Excel/JSON

**归 W-F 所有。** 对应 owner 验收文档 `docs/acceptance/MARKETOPS_LIVE_CC_QUALITY_AND_BUDGET_GATE_2026-10-17.md`
§3（"独立质量评估，不只是工具执行成功"）与 §4.3（"独立质量记分卡与可复现缺陷/修复"）。

**它是什么：** 把一次研究的产物（候选行 + 证据谱系 + 成本账本 + 人工复核）变成一份
**确定性、可机器读、可发表**的记分卡，并给出一个**独立的复核程序**去重算它。

**它不是什么：** 不是新的评分标准。评分等级（A–F）、零容忍项（AF-1..AF-12）与成本账本形状
都是 **W-QA 的 fixture**（`docs/wave1-qa/fixtures/`）已经冻结的；本模块只**应用**它们。
它也不发起任何调用：无网络、无凭据、无付费、不接触目标网页。

## 1. 跑法（全部离线）

```bash
cd plugins/research-tools/marketops-research-assistant
PY=/home/operator/toolchains/marketops-research-plugin-e2e/bin/python   # 插件套件的正确解释器

$PY scripts/research_quality_scorecard.py \
    --brief   <run>/brief.json      --rubric  <run>/rubric.json \
    --checks  <run>/checks.json     --rows    <run>/rows.jsonl \
    --lineage <run>/lineage.json    --artifacts-dir <run>/artifacts \
    --targets <run>/targets.jsonl   --reviews <run>/reviews.jsonl \
    --ledger  <run>/ledger.json     --receipts <run>/receipts.jsonl \
    --out     <run>/scorecard

$PY scripts/research_quality_recheck.py \
    --scorecard <run>/scorecard.json  # …上面同一组 --brief/--rows/--lineage/--artifacts-dir
```

退出码：记分卡 `0`=PASS、`1`=FAIL、`2`=输入不足；复核 `0`=AGREE、`1`=DRIFT、`2`=BLOCKED。
**FAIL 是一个结果，不是工具错误** —— 退出码区分二者，正是为了让 CI 能分辨。

`tests/wave2_quality_fixture.py`（辅助模块，`_` 前缀不被 discover 收集）生成一份 12 目标的
确定性离线运行，覆盖 A–F 每一档与每一个可离线判定的零容忍项：

```bash
$PY -c "
import sys; sys.path[:0]=['scripts','tests']
import _wave2_quality_fixture as fx, research_quality_scorecard as sc
from pathlib import Path
p = fx.write_run(Path('/tmp/run')); print(sc.write_scorecard(sc.score(**sc.inputs_from_paths(p)), '/tmp/run/card'))"
```

## 2. 记分卡的语义（读之前先读这一段）

**四档测量状态。** 只有 `MEASURED` 是一个答案。

| 状态 | 含义 |
|---|---|
| `MEASURED` | 算出来了 |
| `PARTIAL_UNKNOWN` | 算出来了，但分母里含**未对账**的敞口；`value` 只是已确认那一部分 |
| `BLOCKED` | 输入没给，算不了（`excuse` 写明缺什么） |
| `UNKNOWN` | 有作业但没有可归因的数值 |

**金额与配额分计量单位。** `usd_per_accepted_row` 与 `credits_per_accepted_row` 是两个字段、
两个 `meter`，**不相加**。未对账的敞口放在 `unknown_exposure_usd`，全含口径放在 `all_in_usd`
—— 都不是 `value`，所以一个确认值不会被读成全含值。

**未知不是零。** 派发过、实际未对账 ⇒ `PARTIAL_UNKNOWN`；一行也没被接受 ⇒ `value` 是 `null`
（没有分母），不是 `0`。

**派生优先于声明。** 行里的 `source_grade` 是产者的说法。记分卡从该行**实际引用**的那一页
（由已存谱系算出）推出 `domain_class`，再与声明比对：

- 在**自己的官方域名**或其子域上 → `SELF`；在**主管公共机构**域名上 → `PARENT_BODY`（两者都算证据）；
- 商业目录 → `DIRECTORY`；无检索页 → `NO_RETRIEVED_PAGE`；验证墙 → `CHALLENGE`；
- 对非证据页声明 A/B 级 → **`OVERCLAIM`** —— 该行**不能**被接受；
- 声明得比页面支持得更弱 → `UNDERCLAIM`：记录，但不致命，**因为误导的方向不是它**。

`CHALLENGE` **由已存字节里的事实标记判定**，不看 `challenge_suspected` 标志 —— 标志被清掉
也照样判出来（有测试）。同理，`traceable` 只看"页面与引文能否重新找到"，**不**看引文是否支撑
字段值：两件事分开，读者才能分辨两种失败。

**不可评估 = BLOCKED，不是 pass。** 每个检查点名它要读的产物；没给就 BLOCKED，并在 `excuse`
里点名。按 rubric 自己的 evaluation rule，**一条 BLOCKED 就阻止 PASS**。所以 `PASS` 的含义是
"这次能查的都查了且都过"，不是"没发现问题"。

**Verdict：** 任一 P0 失败 ⇒ `FAIL`（压过 BLOCKED）；否则任一条 BLOCKED ⇒ `BLOCKED`；全查且全过
⇒ `PASS`。

**发表的只有标识符、闭集码与数字。** 页面正文、联系值、人名都不进文档；最后一关 `sanitize()`
扫描 email / URL / 查询串 / 凭据形态，命中就**拒绝产出**而不是降级发表 —— 泄漏会挂在一个 PASS 上。

## 3. 两个 pass 与"独立"在哪

| | 做什么 | 能抓到什么 |
|---|---|---|
| Pass 1（重算） | 用同一批输入再算一遍并逐键比对 | 事后被编辑过的文档；**点名差异键**，不笼统说"不一样" |
| Pass 2（重推） | 用**另一份短实现**直接从已存字节推"哪些行本该被接受" | **评分器自己的 bug** —— 它刻意不复用评分器的判定路径，也不共用码表 |

`inputs.sha256` 记录每个输入文件的摘要，读者据此把记分卡钉到具体产物上；同一批输入两次评分
**逐字节相同**（有测试）。

## 4. I / Q 怎么接

- **I（集成）**：把 `research_quality_scorecard.score()` 当一个纯函数用；产物落
  `exports/<run>/scorecard.{json,csv}`。CSV 是扁平的 `measurement,state,value,meter,excuse`，
  给看板/表格；JSON 给机器。记分卡不写任何生产状态、不改任何业务记录。
- **Q（独立验收）**：拿一份**别人产出的**记分卡跑 `research_quality_recheck.py`。`DRIFT` 即是
  结论（差异键在 `differences`），`BLOCKED` 表示"没能查"，**不是**同意。`rederive_acceptance()`
  可单独调用，用来核对"被接受的行"是否真由字节支撑。
- **Jev 的位置**：Jev 只能作用于**已保存、可追溯的 artifact**（`research_adapter_jev.bind_evidence`
  强制）。所以一条 Jev 判断在这份记分卡里最多是一个 `decision_response` 派生件，**永远不是**证据页，
  这一点由 `research_evidence` 既有的 `DECISION_NOT_EVIDENCE` 与 `AF-3/AF-9` 共同钉住。

## 5. 最小真实研究执行计划（owner 的 Stage 0 → 3）

目标样本：W-QA `targets.jsonl` 里的 **15 个伦敦公共文化/场馆机构**（落在 owner 要求的 10–20 内）。

| 阶段 | 内容 | 谁 | 现状 |
|---|---|---|---|
| **Stage 0** 集成/安全前置 | 冻结的集成 SHA；worker 模块齐备；离线独立 QA（SSRF/出口、预算/政策、重复计费、候选完整性、恶意 MCP schema、客户端安装器） | I + Q | **部分**。模块齐备度可查（`research_wiring.probe()`），离线 QA 差分可读（`qa_gate` 矩阵） |
| **Stage 0b** 凭据注入 | owner 把 provider key 安全注入**MarketOps 运行时**（不经过 Agent prompt / 子进程日志） | owner | **BLOCKED**。见 §6 |
| **Stage 1** 只读上游契约发现 | TinyFish/SerpApi MCP 的 initialize→tools/list、账户额度；Serper 无计费端点；Jev SDK 与用量；Crawl4AI 本地浏览器+出口 | C/D/E/F | **BLOCKED**（需一次有界只读握手授权） |
| **Stage 2** 真实 CC 小任务 | **真实安装的插件、经 Claude Code**（不是 Python 直调）跑 15 个目标；人批准的 Brief → 能力发现 → 官方检索 → 安全抓取 → 证据 artifact/hash → 候选组织/角色/公开联系路线 → 展示 → 表格导出 → 成本报告 | G + owner | **未开始**（依赖 0b/1） |
| **Stage 3** 独立质量评估 | 跑本模块出记分卡；Q 跑复核；P0 缺陷按 `issue_id` 派修复；修后**在新 SHA 上重跑** | F + Q | **脚手架已就绪**（本目录），**输入未产生** |

**Stage 3 之前必须先把 `--reviews` 与 `--ledger` 真实化**，否则记分卡会诚实地报
`accepted_valid_rows`/`false_positives`/成本三项为 `BLOCKED` —— 那是正确行为，不是缺陷。

**预估成本**：Stage 1 约 < $1（owner 文档 §2 口径）；Stage 2 检索/抽取按 T2 计价，逐次预约；
Jev 单价极低。**本模块自身零成本**（纯本地）。

## 6. 明确 BLOCKED（不是"做完了"）

1. **真实调用授权** —— 无任何上游握手授权 ⇒ Stage 1/2 全部 BLOCKED，本模块的能力标签上限
   **只能到 `OFFLINE_TESTED`**。
2. **凭据注入未验证** —— vault 里**存在**非空的 `TYPESAFE_API_KEY`（只确认了"有一行非空赋值"，
   未读取值）；**是否有效、归属、是否被授权付费调用，全部未验证**。凭据文件存在**不等于**客户端会继承。
3. **`op_signature` 缺失** —— W-QA 的 ledger schema 没有这一列，所以 **AF-7（超时后重复计费）
   无法评估，报 BLOCKED**，不报 PASS。补上该列即可解除。
4. **离线没有出口日志/请求日志/起始 brief 哈希** ⇒ AF-4 / AF-8 / AF-11 同样 BLOCKED。
5. **`FORBIDDEN`/阈值** —— owner §3 明确"首个真实基线再定阈值"。本模块**不设**任何通过阈值，
   只报数；伪造一个 100% 目标就是把假判据写进代码。
