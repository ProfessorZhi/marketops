# MarketOps RC 发行卫生与 release-readiness（2026-10-20）

状态：**本 lane 的最小充分实现已完成；RC 仍为 DRAFT，不是正式发行批准。**

- RC 基线：`rc/marketops-release-candidate-20261020@bf33e958e1c1906bd1d681362f3edf99bf473979`
- 本 lane 分支：`fix/marketops-rc-release-hygiene-20261020`
- 版本：`0.5.6-rc1`
- 旧 `0.5.5` ZIP：**未覆盖、未改名、未删除**；本 lane 的构建只写仓库外证据目录。

## 1. 本轮变更（只动发行卫生面）

### 1.1 版本区分

旧 RC 的所有 manifest 仍写 `0.5.5`，与已发布的 `0.5.5` 无法区分。现统一为
`0.5.6-rc1`：

| 位置 | 值 |
| --- | --- |
| `plugin.json` / `.claude-plugin/plugin.json` / `.codex-plugin/plugin.json` | `0.5.6-rc1`，description 明确标注 release candidate |
| `scripts/build_client_bundles.py` `META["version"]` | `0.5.6-rc1` |
| `scripts/setup_clients.py` `VERSION` | `0.5.6-rc1` |
| `scripts/research_delivery.py` `VERSION` | `0.5.6-rc1` |
| `scripts/research_mcp.py` server / tool-visible version | `0.5.6-rc1` |
| README、START_HERE、CLIENT_SETUP、CUSTOMER_QUICKSTART、RELEASE_GATE | 明确写出 `0.5.6-rc1` 与“不是 0.5.5” |

版本 schema：Agent Plugins 1.0.0 的 `version` 字段是 `type: string`，未限制
semver 后缀，因此采用 `0.5.6-rc1`。新增
`tests/test_rc_release_hygiene.py` 防止 manifest / builder / installer 再次漂移。

### 1.2 Lane B 合成私钥夹具的 gitleaks REVIEW

原夹具是 `tests/test_client_view_generation.py` 中的
`-----BEGIN RSA PRIVATE KEY-----\nMIIBsynthetic`，用于证明客户视图拒绝私钥材料。
它不是可用密钥（payload 不是合法 base64），但原先没有按 `REDACTION.md` 的惯例
标注。现在：

- 夹具提取为局部变量，并在同一行加显式 `# gitleaks:allow`；
- `.gitleaks.toml` 增加**只按该文件 + 只匹配该 PEM 头**的 allowlist，不是
  repo-wide、不是 rule-wide；
- `REDACTION.md` 增加到既有同形状的表格行；
- 新增测试固定“行内标记 + path/match allowlist + payload 不是合法 base64”。

扫描标准没有被降低：release gate 的 G9 仍是“私钥/token = FAIL，个人邮箱/手机号
= REVIEW”，本次结果仍是 1 条 REVIEW（合成夹具本身），0 条 FAIL。

### 1.3 可复现的发行卫生验收

新增 `scripts/rc-20261020/check_release_hygiene.py`。它只做离线动作：

1. 从当前 checkout 构建 portable source ZIP 和 WorkBuddy connector 到仓库外；
2. 核对 `SOURCE_MANIFEST.json` 逐文件 SHA-256、ZIP SHA-256、`.sha256` sidecar、
   ZIP 成员与 client-view helper；
3. 解包后从解包目录启动真实 stdio MCP，`initialize` + `tools/list` +
   `marketops_setup_status`，网络/预算全为 0；
4. 只用合成数据（4 条合格 + 4 条 HOLD）走真实 `build_delivery` →
   `write_client_view` → `verify_client_view`，断言 HOLD、伪造值、仅证据中出现的
   邮箱/组织都不进入客户视图；
5. 输出 JSON 报告与 `RELEASE_HYGIENE_MANIFEST.json`，后者带 client-view 的
   manifest / 内容树 / XLSX 哈希。

## 2. 实测证据

证据目录：`$ARTIFACTS_ROOT/skills/marketops-rc/20261020/hygiene/final-9d7917a4/`
（仓库外）。以下哈希来自本 lane 的验收运行。**Source ZIP 与 client-view 内容在各次
运行间逐字节一致**；bundle ZIP 的 `mcp.json` 绑定本次临时构建的绝对路径，因此
bundle 哈希是 run-local 的，不能当作跨机器发布哈希。

| 项目 | 结果 |
| --- | --- |
| 版本一致性 | PASS；`0.5.6-rc1` |
| Source ZIP SHA-256 | `76823c758d65eedb1ca317a29162a0ad642c07a633e5b39a8bd017c827bbfb00` |
| Source manifest | PASS；164 files；`source_sha256=fa43183bb2340fa8db218518d3548e8203ebcd463bbe226f41f16b4d9f913783` |
| ZIP 成员 | PASS；165 members；含 `scripts/research_client_view.py`；无 tests/.git/.venv/data |
| Connector bundle SHA-256（run-local） | `b01ec6dcc3dbed7d018fd28136fe165b40e32f9d2da6ab1ef494742b0c29f918`；含本次临时绝对路径，不能跨机器复用 |
| 解包 + 离线 stdio | PASS；server `MarketOps Lite Research 0.5.6-rc1`；30 tools；`CONFIG_REQUIRED`；provider calls 0 |
| 自动客户视图 | PASS；4 admitted / 4 held；7 files；`CANDIDATE_NOT_APPROVED`；send_authorized=false |
| 证据/HOLD 泄漏检查 | PASS；合格值在，HOLD/伪造值/仅证据邮箱与组织均不在 |
| client-view MANIFEST SHA-256 | `68eba799abbe921a2139b825d4c977dae035bc2b835b64a51c7966aa2ac9b553` |
| client-view 内容树 SHA-256 | `842bac4b06b040066529060a8681cadadb738ca3a22ec6f6b84c8d18dc711759` |
| Client_View.xlsx SHA-256 | `f493a8d3877a06c50b1db7f68ecebe398f8e0272441e06943fb896807f9becaa` |

## 3. 测试计数

| 范围 | 结果 |
| --- | --- |
| 新增发行卫生单测 | `Ran 5 tests`，OK |
| 定向 lane / seam | `Ran 35 tests`，OK（client-view 11、setup-clients 11、MCP refusal 8、A+B seam 5） |
| 完整 offline plugin suite（只跑一次） | `Ran 2140 tests in 261.934s`，`OK (skipped=30)`，0 failures / 0 errors |
| 解释器 | `$TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python` |

## 4. Release-readiness（把不同来源的红绿分开）

| 线 | 判定 | 依据 |
| --- | --- | --- |
| 插件套件（本 RC 分支） | **PASS** | 2140 tests，0 failures/errors，30 skipped；定向 40 条全绿 |
| GitHub `main` CI 的 23 条基线失败 | **BASELINE_RED，不是本 RC 引入** | `main@63a5fe486113a83afb063265e3458b46877789fa` 的 run `39194528248`：3.11 与 3.12 均 `23 failed, 7809 passed, 21 skipped`；失败集中在 `tests/test_evidence_store_contract.py`、`tests/test_portability.py`、`tests/test_security_egress.py` 与 `tests/test_sources_snapshot.py`。本 RC 基于 `feat/bruce-plugin-delivery-20261020@4f87bc33`，没有并入 `main`，也没有声称修复这 23 条 |
| PR #17 / PR #19 CI | **插件工作流 PASS；core test 为同一 23 条基线红** | PR #19 的 `research-plugin-e2e`（offline-3.12、installed-ubuntu、installed-windows、live-readonly）全部 success；`ci` 的 `test (3.11)` / `test (3.12)` 均 `23 failed, 7809 passed, 21 skipped`，与 `main` 基线同集合、同数量，不是本 lane 新增红 |
| 公开历史潜在私有提交风险 | **BLOCKED（owner 决策）** | `REDACTION.md` 记录 public `main` 既有 244 条基线命中，本分支不重写历史、无法移除；本分支新增行 G9 为 0 FAIL / 1 REVIEW（已显式 allowlist）。是否重写公开历史、是否接受剩余基线风险，超出本 lane 权限 |
| gitleaks 实际执行 | **BLOCKED（量具缺失）** | 本机没有 `gitleaks` CLI，CI workflow 也未接入；本轮只做了 path/match allowlist 静态核对与 release gate 自有扫描，不能宣称 gitleaks 已跑过 |
| F：浏览器出口安全线 | **BLOCKED（未并入）** | `fix/marketops-rc-browser-egress-20261020@f977c6a1` 基于基线，`merge-base --is-ancestor` 对 RC 为假；本 RC 未包含 WebRTC/DNS 出口加固，也未做真实宿主浏览器出口验收 |
| G：准入/发行门安全线 | **REVIEW_REQUIRED** | Lane A 的 admission gate 已并入（`1faa6656`）且 15 条定向绿；但“拒绝边界”的独立安全复核尚未完成，不能由本 lane 自证 |
| 真实供应商费用 | **BLOCKED（未验收）** | 本轮所有网络开关为 0，未调用 Serper/SerpApi/TinyFish/Crawl4AI/Jev，未产生费用；单价与额度仍是外部假设，未由真实账单核对 |
| 客户授权 / 正式外发 | **BLOCKED（未验收）** | 无客户授权、无 SMTP、无真实客户邮箱外发；客户视图始终 `CANDIDATE_NOT_APPROVED`、`send_authorized=false`、`formal_sop_claimed=false` |
| 客户视图安全（合成范围内） | **PASS** | 4 合格 + 4 HOLD；HOLD/伪造/仅证据值均未进入客户视图；manifest 与磁盘哈希一致 |

**结论：本 lane 可以合并进 RC 分支作发行卫生修正，但不能据此宣布正式发行。**
版本区分、合成夹具标注、可复现构建/冒烟/客户视图证据已闭合；`main` 的 23 条
基线红、公开历史风险、F 出口加固、G 独立复核、真实费用与客户授权仍是
BLOCKED / REVIEW_REQUIRED，必须由发布负责人分别裁决。

## 5. 复现

```bash
cd $WORKSPACE_ROOT/worktrees/marketops/rc-release-hygiene-20261020
$TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python \
  scripts/rc-20261020/check_release_hygiene.py \
  --evidence-dir $ARTIFACTS_ROOT/skills/marketops-rc/20261020/hygiene \
  --python $TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python

cd plugins/research-tools/marketops-research-assistant
$TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python \
  -m unittest discover -s tests -v
```

## 6. 明确未做

- 未合并 `main`、未发布 GitHub Release、未创建正式 tag。
- 未覆盖/归档/删除既有 `0.5.5` ZIP 或任何工作树。
- 未调用真实供应商、未产生付费调用、未发 SMTP、未改 OS 全局配置/凭据/权限。
- 未 force-push、reset、clean、prune，未打断其他 Agent。
