# MarketOps RC 测试矩阵（2026-10-20）

状态：**RC5 executed — DRAFT，不是正式发布**。结果与证据路径见下；证据根为
`$ARTIFACTS_ROOT/skills/marketops-rc5/20261020/`。

基线：`4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44`（PR9）。
RC3 base：`38dcef853f526f64f669c475c96ad9c3bad3ad47`（H）。
解释器：`$TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python`
（插件套件必须用 e2e venv；用 mvp venv 会多出 3 条 error，那是量具差异，不是回归）。

## 1. 需求 → 判据

| # | RC 判据 | Lane | 定向判据 | 状态 |
| --- | --- | --- | --- | --- |
| R1 | release manifest / hash 可复核，既有 0.5.5/rc1 zip 不被覆盖 | 集成 | G7：`build_source()` 到仓库外；逐文件哈希、`.sha256`、zip 哈希一致 | PASS（hygiene gate；version `0.5.6-rc5`；未覆盖任何旧 ZIP） |
| R2 | 真正自动生成客户视图 | A+B+I | `write_delivery` 内部自动调用 `write_client_view`；顶 MANIFEST 收录 `client-view/*` 哈希 | PASS（hygiene H8：4 admitted / 4 held，8 文件） |
| R3 | P0 伪造数据绝不进入业务表 | A+Q+I | A 的 admission 测试 + Q 的边界测试 + G6/P0 负例 | PASS（产品 P0 负例全绿；QA #15 冻结夹具 3 红，见 R15） |
| R4 | 合格样本能进入，而不是被门禁一刀切 | A+I | A 的正例测试 + G6 正例 + 长页 4 条合法正向 | PASS（`test_rc3_evidence_p0.PositiveControls` 4/4） |
| R5 | 安装明确 opt-in | C | G3：无 `--apply` 不落盘；非交互无 destination 被拒；全局 Codex 注册默认 no | PASS（Ran 21 OK） |
| R6 | SerpApi 不重复 reserve | D | G4：REST 与 embedded 同 signature；同一 operation 只一条 reserve | PASS（Ran 23 OK） |
| R7 | Jev evidence locator | E | G5：locator 2–60、未命中 `JEV_EVIDENCE_LOCATOR_NO_MATCH`、box 拒绝 | PASS（Ran 19 OK） |
| R8 | 所有相关定向测试 | 全部 | G1–G6 + F/G/RC2/RC3 | PASS（A 3 / B 11 / C 21 / D 23 / E 19 / G6 5 / F 16 / G 19 / RC2 12 / RC3 19 / RC3 QA 6 / gate-status 8 / RC4 17+15 / RC5 6+10） |
| R9 | 完整 offline plugin suite 一次 | 集成 | G8：`python -m unittest discover -s tests -v` | PASS（Ran 2271 tests, 0 failures, 0 errors, 30 skipped） |
| R10 | 不引入私有数据 / 密钥 | 集成 | G9：只扫本分支新增行；私钥/token 为 FAIL，个人邮箱/手机号为 REVIEW | PASS（0 secret-level，1 review-level：已 inline allowlist 的合成测试夹具）；**gitleaks CLI 缺失，不能宣称 gitleaks 通过** |
| R11 | 基线红如实区分 | 集成 | G8 + `--baseline-failures N`；只有“总数等于声明基线”才记 `BASELINE_RED_NOT_NEW` | PASS（见 §4；23 条与 `main@63a5fe48`、`4f87bc33` 逐条相同） |
| R12 | 不合并 main / 不发布 / 不付费 / 不外发 | 集成 | PR 保持 Draft；无 Provider key、无真实邮箱；gate 强制网络开关为 0 | 约束已生效（真实 Provider 调用 0） |
| R13 | F：run/project 次数独立，USD 帽 fail-closed | F+I | `test_runtime_dollar_cap_contract.py` + `test_rc2_integration_seams.py` | PASS（16 + 12 tests） |
| R14 | G：WebRTC/代理旗标覆盖所有可达 Crawl4AI 入口 | G+I | `test_crawl_browser_webrtc_egress.py` + RC2 接缝负例 | PASS（19 + 12 tests；`_crawl4ai_local` 已加固且无直连回退） |
| R15 | QA #15 在独立夹具升级后 9/9 绿 | Q | `tests/test_customer_delivery_boundary_qa.py`，`MARKETOPS_QA_PLUGIN_ROOT` 指向 RC 插件 | **BLOCKED**：冻结夹具的 venue/contact/person quote 不含组织名+值同跨度，3 个用例失败；未改 QA 工件、未放宽产品核验。需 QA 方升级夹具或提供具名关系 |
| R16 | 字段级身份支持 | I | 组织/场地 name、人员/岗位每个展示字段必须出现在其 linked quote | PASS（`test_rc3_evidence_p0.FieldSupportTests` 7 tests） |
| R17 | 实体/关系级归属 | I | 同页两组织、错绑、共享 artifact、具名 relation | PASS（`BindingTests` 3 tests；长页正向 4/4） |
| R18 | 自动 client-view + 完整 MANIFEST | I | `write_delivery` / `mcp.handoff` 产出 client-view 与哈希 | PASS（`AutomaticClientViewTests` 3 tests；hygiene H8/H10） |
| R19 | 独立 QA 门禁（两个报告层） | I | `qa_verified=false` → BLOCKED/NOT_RELEASE_READY；gate `ok=false` | PASS（`test_rc3_release_qa.py` 6 + `test_rc3_release_gate_status.py` 8；hygiene H12 BLOCKED；gate G15 BLOCKED） |
| R20 | live public E2E 归属 | I | 真实页面有 org+venue/email 局部跨度则写具名 relationship；否则诚实 HOLD | PASS（`_local_span` 合成单测 3；CI 以新 run 为准，不 skip/xfail） |
| R21 | 写入时 supersedes 链校验 | I | kind/stage/entity_key/run/目标一致、父版本存在、无 fork/cycle | PASS（`test_rc4_supersedes_write_guard.py` 17 tests；Essex 7 错 key 形状入库即拒） |
| R22 | 缺口驱动来源选择 | I | 离线 deterministic scorer；marketops_next 暴露；预算/blocked/共享目录 | PASS（`test_rc4_source_selection.py` 15 tests + workflow exposure） |
| R23 | review supersedes 类型一致 | I | note 不能顶替 target_disposition；单条/batch 原子；合法三段修订 | PASS（`test_rc5_review_type_guard.py` 6 tests；held venue 始终 0） |
| R24 | rate/phone/contact-binding 证据 | I | 需 linked field + quote 支持值 + 机构归属 | PASS（`test_rc5_source_evidence.py` 10 tests） |

## 2. 执行顺序

```bash
cd $WORKSPACE_ROOT/worktrees/marketops/rc-final-20261020

# 1) 来源核对（远端 SHA 与给定值逐字一致）
git ls-remote origin refs/heads/fix/marketops-rc-release-hygiene-20261020 \
  refs/heads/fix/marketops-rc-money-gate-20261020 \
  refs/heads/fix/marketops-rc-browser-egress-20261020

# 2) 定向与跨 Lane 门禁（不跑全量）
$TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python \
  scripts/rc-20261020/check_release_gate.py \
  --evidence-dir $ARTIFACTS_ROOT/skills/marketops-rc5/20261020/gate

# 3) 全量 offline plugin suite，只跑一次
$TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python -m unittest discover -s tests -v

# 4) 构建 + 解包 + 离线 stdio + 混合 client-view
$TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python \
  scripts/rc-20261020/check_release_hygiene.py \
  --evidence-dir $ARTIFACTS_ROOT/skills/marketops-rc5/20261020/hygiene \
  --work-dir $ARTIFACTS_ROOT/skills/marketops-rc5/20261020/hygiene-work \
  --python $TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python

# 5) QA #15（QA 工件只在 artifacts 中，绝不复制进产品分支）
cd $ARTIFACTS_ROOT/skills/marketops-rc5/20261020/qa
MARKETOPS_QA_PLUGIN_ROOT=<RC plugin root> \
  $TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python \
  -m unittest test_customer_delivery_boundary_qa -v
```

## 3. 结果回填格式

每个判据记录：`run_id`、命令、`Ran N tests`、`failures/errors/skipped`、HEAD SHA、
证据 JSON 路径。红项必须写“基线同样红 / RC 新增红 / 无法判定”三种之一，禁止改测试或加 skip 冒充绿。

## 4. 根 CI 既有 23 条红的准确集合

来源：GitHub Actions `ci` 工作流。

- RC2 base H `38dcef85` run `39376391351`：23 failed。
- `main@63a5fe48` run `39194528248`：**同一 23 条**。
- PR9 baseline `4f87bc33` run `39364517578`：**同一 23 条**。

因此这 23 条是既有基线红，不是 RC2 新增。逐条：

```text
tests/test_evidence_store_contract.py::test_insertion_order_is_preserved[jsonl]
tests/test_evidence_store_contract.py::test_insertion_order_is_preserved[sqlite]
tests/test_evidence_store_contract.py::test_insertion_order_is_preserved[memory]
tests/test_evidence_store_contract.py::test_projection_order_is_by_key_not_by_arrival[jsonl]
tests/test_evidence_store_contract.py::test_projection_order_is_by_key_not_by_arrival[sqlite]
tests/test_evidence_store_contract.py::test_projection_order_is_by_key_not_by_arrival[memory]
tests/test_evidence_store_contract.py::test_the_fold_does_not_take_the_maximum[jsonl]
tests/test_evidence_store_contract.py::test_the_fold_does_not_take_the_maximum[sqlite]
tests/test_evidence_store_contract.py::test_the_fold_does_not_take_the_maximum[memory]
tests/test_portability.py::test_slugify_is_platform_independent
tests/test_security_egress.py::test_the_domain_part_is_normalised_and_the_local_part_is_not
tests/test_sources_snapshot.py::test_every_entry_resolves_to_a_full_sha_commit
tests/test_sources_snapshot.py::test_every_declared_snapshot_path_exists
tests/test_sources_snapshot.py::test_snapshot_hashes_match_the_files
tests/test_sources_snapshot.py::test_every_file_under_sources_is_declared
tests/test_sources_snapshot.py::test_manifest_channels_are_well_formed_and_covered
tests/test_sources_snapshot.py::test_every_snapshot_entry_lives_under_its_channels_root
tests/test_sources_snapshot.py::test_private_chat_lines_keep_every_original_field
tests/test_sources_snapshot.py::test_selected_message_ids_match_the_file_in_order
tests/test_sources_snapshot.py::test_selections_with_message_ids_declare_their_criterion
tests/test_sources_snapshot.py::test_complete_selections_declare_no_filter
tests/test_sources_snapshot.py::test_complete_chat_line_count_matches_upstream_count
tests/test_sources_snapshot.py::test_a_none_matched_selection_is_zero_bytes_and_explains_itself
```

未改测试、未加 skip、未把 23 条降级成“可接受”。

## 5. 已知量具注意

- 全量套件耗时受主机负载影响，**耗时不是回归信号**。
- `pytest --maxfail` 会截断失败集；本 gate 用 `unittest discover`，不做 `--maxfail`。
- 插件套件与核心 `tests/` 是两套；`pyproject.toml` 的 `testpaths=["tests"]` 管的是核心套件。
- Q 的 `tests/test_customer_delivery_boundary_qa.py` 在 QA 分支，是 QA 工件，
  不进入产品分支，也不由插件套件发现。
- 本机没有 `gitleaks` CLI；仓库内 G9 只是正则扫描，两者不能互相冒充。
- OS 级 UDP 未抓包；`BROWSER_SECURITY_BOUNDARY` 明确标注 `UNPROVEN` / `NOT_RUN`。
