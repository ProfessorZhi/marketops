# MarketOps 0.5.6-rc2 最终集成记录（2026-10-20）

> **历史记录（RC2）。** 后续 P0 修复见 [`RC3-INTEGRATION.md`](RC3-INTEGRATION.md)，
> 写入时版本校验与来源选择见 [`RC4-INTEGRATION.md`](RC4-INTEGRATION.md)。
> 本文保留 rc2 当时的证据，不重写。

状态：**DRAFT / RC2 candidate — 不是正式发布**。未合并 `main`，未创建 GitHub Release，
未调用任何真实付费 Provider，未发邮件，未改全局配置或密钥。

## 1. 来源与基线

| 角色 | 分支 | 精确 SHA |
| --- | --- | --- |
| H 发行卫生（RC2 base） | `fix/marketops-rc-release-hygiene-20261020` | `38dcef853f526f64f669c475c96ad9c3bad3ad47` |
| F 金额门禁 | `fix/marketops-rc-money-gate-20261020` | `a3e45df134bc88305a7647d3f8f9f646be317450` |
| G 浏览器出口 | `fix/marketops-rc-browser-egress-20261020` | `f977c6a149235b68b1388d2bd793f71d86675615` |
| QA #15 | `qa/marketops-rc-customer-gate-20261020` | `cdb1d9a3f7c4b9d7c8d12923364bcd091fcd2a95` |
| RC2 候选 | `rc/marketops-0.5.6-rc2-20261020` | 由 H 精确提交创建；F/G 以 `cherry-pick -x` 引入 |

集成方式：

```text
38dcef85 (H, base)
  ├─ 57fd5036  cherry-pick -x a3e45df1  (F)
  └─ b6e66e96  cherry-pick -x f977c6a1  (G)
       └─ RC2 seam commit (I)
```

两次 cherry-pick 均无冲突；没有使用 ours/theirs、没有重置工作树、没有覆盖他人提交。
`docs/rc-20261020/lanes.json` 已登记 F/G/H/I 的来源 SHA 与集成后 SHA。

## 2. RC2 接缝修改（仅 Integrator 可改）

### 2.1 F：USD 帽与运行/项目次数在真实入口生效

`research_runtime_setup.state_ledger_caps` 在 F 中已经存在，但三个真实入口仍各自
用 `set_cap(ceiling_calls=...)` 重写整行：

- `research_wiring._GatewayAfterTheGate.invoke`（MCP managed call）；
- `dual_search_mcp._state_caps`（REST 搜索）；
- `research_provider_extensions._state_caps`（Provider 扩展入口）。

`set_cap` 是**整行替换**，因此这些入口会同时：

1. 把独立的 `run_max_calls` / `project_max_calls` 又改回同一个 `max_calls`；
2. 把 `ceiling_micros` / `usd_enforced` 清空 —— 操作员刚同意的美元帽在账本里消失，
   而 `runtime.status` 仍报告它存在。

RC2 的处理：

- 三个入口在本地同意记录存在时统一调用 `runtime.state_ledger_caps(...)`；
- 只声明次数帽的遗留路径改走 `runtime.set_call_cap_preserving_money(...)`，
  保留既有金额、`usd_enforced` 与秒帽；
- `research_runtime_status` 的 capability matrix 改为读取 `runtime.status` 的真实
  开关，而不是硬编码 `dollar_limit_enforced: False`。

行为边界（保持 F 的语义）：

- run / project 次数相互独立；
- 显式 USD 帽在无已审核价目表时 **fail-closed**（`PRICE_UNKNOWN`）；
- “执行成功但费用未知”记录为 `CHARGE_UNVERIFIED_AFTER_SUCCESS`，不再误报为
  Provider 失败；
- `UNKNOWN` 行保持 hold，不会被清零或释放。

### 2.2 G：WebRTC/代理旗标覆盖所有可达 Crawl4AI 入口

`research_provider_extensions._crawl4ai_local` 是 G 之外仍直接构造
`BrowserConfig(**egress.browser_config_kwargs())` 的可达入口。RC2 在它启动
浏览器**之前**增加同一套判据：

- `_managed_browser_url`：只允许 http(s)、必须有 hostname、禁止 userinfo；
- `browser_launch_violations`：代理必须是本机回环 http、`extra_args` 形状合法、
  不得用同名参数降级 WebRTC/DNS/proxy 护栏；
- `hardened_browser_extra_args`：补齐
  `--webrtc-ip-handling-policy=disable_non_proxied_udp`、
  `--host-resolver-rules=MAP * ~NOTFOUND,EXCLUDE localhost,EXCLUDE 127.0.0.1`
  与既有 loopback/QUIC 防御。

不合格时返回 `LOCAL_CRAWL_NOT_SAFE_TO_RUN`，**没有直连回退**，浏览器不会构造。
判据只看“写进了启动参数”，不声称 OS 级 UDP 已隔离。

### 2.3 core fetcher 的非覆盖风险

`marketops/adapters/crawl4ai_fetcher.py::_browser_config` 只有
`--proxy-bypass-list=<-loopback>` 与 `--disable-quic`，**没有** WebRTC 与
DNS 护栏。它是仓库核心代码，不在本插件的发行 ZIP 中；`build_client_bundles`
只复制插件 `scripts/`，插件脚本也没有 `marketops.adapters` 导入边。

因此：

- **发行插件**不可调用该 core adapter，G 的护栏覆盖发行面；
- **核心产品路径**仍存在该缺口，属于“非覆盖风险”，本次不宣称已修；
- `tests/test_rc2_integration_seams.py::CoreFetcherReachabilityTests` 禁止插件
  新增到 core adapter 的导入边，避免未来悄悄把未加固路径带进发行包。

## 3. 版本与打包

- 版本统一为 `0.5.6-rc2`：三个 manifest、`build_client_bundles.META`、
  `setup_clients.VERSION`、`research_delivery.VERSION`、`research_mcp` server 与
  tool-visible 版本、README / START_HERE / CLIENT_SETUP / CUSTOMER_QUICKSTART /
  RELEASE_GATE。
- 新 ZIP 只写入独立 artifacts 目录；未覆盖、未改名、未删除任何 `0.5.6-rc1` 或
  `0.5.5` ZIP。
- 构建后重新读取 SOURCE_MANIFEST 的逐文件摘要、ZIP SHA256 与 `.sha256` sidecar；
  解包真实 stdio MCP，再做混合“合格/虚构/HOLD”client-view 演练。

## 4. 验证摘要

| 门 | 结果 |
| --- | --- |
| F 定向（`test_runtime_dollar_cap_contract.py`） | 16 tests OK |
| G 定向（`test_crawl_browser_webrtc_egress.py`） | 19 tests OK |
| RC2 接缝（`test_rc2_integration_seams.py`） | 12 tests OK |
| QA #15（独立 QA 工件，只读挂载 RC 插件） | 9/9 OK |
| 插件全量离线套件 | `Ran 2187 tests`，0 failures，0 errors，30 skipped |
| release hygiene（构建+解包+stdio+client-view） | PASS；server `0.5.6-rc2`，30 tools，provider calls 0 |
| release gate 定向 | 全部 PASS（含 A/B/C/D/E/F/G/RC/RC2 与基线 ancestry） |
| 根 CI 既有 23 条红 | 与 `main@63a5fe48`、`4f87bc33` **逐条相同**，非 RC2 新增 |

## 5. 阻塞与未证明事项

- **OS 级 UDP 隔离未抓包**：G 的 flag 只证明“写入了启动参数”，
  `BROWSER_SECURITY_BOUNDARY` 仍为 `os_level_udp_isolation=UNPROVEN`、
  `runtime_udp_measurement=NOT_RUN`。不得据此宣称网络隔离已证明。
- **收费定价未知**：没有已审核价目表时，显式 USD 帽 fail-closed；
  真实付费 Provider 调用本次为 0，费用正确性未经验证。
- **gitleaks CLI 缺失**：本机没有 `gitleaks` 可执行文件，因此**不能**宣称
  gitleaks 凭证扫描通过。仓库内 gate 的正则扫描为 PASS（0 secret-level、
  1 review-level synthetic fixture），那是另一件量具。
- **CI**：根 `ci` 工作流的 23 条红是既有基线，不是本候选引入；最终以 push 后的
  实际 run 为准。插件自身 `research-plugin-e2e` 工作流此前在 H/F/G 上均为 success。
- root CI 的 23 条红：`test_evidence_store_contract.py` 9 条（3 用例 × 3 backend）、
  `test_sources_snapshot.py` 12 条、`test_portability.py` 1 条、
  `test_security_egress.py` 1 条。

## 6. 回滚点

- 起点：H `38dcef853f526f64f669c475c96ad9c3bad3ad47`。
- F 集成点：`57fd5036`；G 集成点：`b6e66e96`；RC2 接缝提交紧随其后。
- 未改写历史、未 force-push、未动 `main`。回滚只需删除候选分支/关闭 Draft PR；
  不影响任何 Lane 分支或既有 ZIP。

## 7. 独立安全复核

Draft PR 保留独立安全 reviewer 位置。复核重点：

1. `_crawl4ai_local` 的“无直连回退”是否在异常路径仍成立；
2. `set_call_cap_preserving_money` 是否在所有 legacy 入口覆盖；
3. `state_ledger_caps` 与 `state_ledger_caps` 之外的 `set_cap` 是否仍可能清空
   `usd_enforced`；
4. 发行 ZIP 是否确实不含 core `marketops/` 路径。
