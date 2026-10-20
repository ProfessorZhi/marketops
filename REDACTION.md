# 脱敏说明

本仓库是 MarketOps 的**脱敏公开快照**（2026-10-19）。原始仓库连同完整提交历史、Issue 与 PR
保持私有，不在本仓库中。

## 做了什么

| 对象 | 处理 |
|---|---|
| 同事姓名（飞书发言人、协作者） | 换成 `Member-A` … `Member-E` |
| 飞书 `open_id` / `chat_id` / `message_id` / `tenant_key` | 换成全零序列号，**保持唯一性与可关联性** |
| 飞书群与文档链接 | 域名换成 `applink.example.com` / `example.feishu.cn`，文档 token 换成 `TOKENxxxxxx` |
| 消息正文 | **原样保留**，仅替换上述标识符 |
| 客户/项目代号 | 换成 `Acme` / `AcmeCorp` |
| 公司品牌名与域名 | 换成 `EXD` / `example.com` |
| 内部主机名与路径 | 换成 `vm-example`、`host-example`、`/srv/marketops` 等 |
| 5 个二进制附件（2 个 docx 开发信模板、2 张会话截图、1 张报错截图） | **从仓库中移除**，`sources/manifest.yaml` 中对应条目光一并删除 |
| 以人名命名的目录、文档与常量 | 一律改为 `member-a-*` / `member-c-*` / `member-d-*` / `party-group` |

## 因此会看到的现象

- `sources/` 里的消息快照与上游 `upstream-records` 不再逐字节一致，`tests/test_business_sources.py`
  中依赖上游比对的断言在无上游环境时按设计跳过。
- 引用被移除附件的文档段落仍保留了文字说明。

本仓库不含任何可用的 API key、密码或令牌。测试夹具中的 `sk-…` 形态字符串均为占位符。

---

# Redaction and secret-scan allowlist

This port branch was passed through D1-synth v3 with variant-aware full-tree
replacement (`apply-synthetic.py` + private `synthetic-map.json`). The map is
private and is deliberately absent from this repository.

Full-tree check before replacement (public `main` baseline): **244 hits in 51 files**
(13 variant hits; 199 unique lines). After D1-synth v3 replacement: **0 hits in 0 files**;
a second check is also 0 (idempotent). `synthetic-map.json` and `apply-synthetic.py` are
private and are deliberately absent from this repository.

New-rule audit (all 280 map entries scanned independently, all variants, case-insensitive
bare substring; `_`, `.` and `-` are treated as token boundaries, so a word entry cannot
consume a wider substring entry):

- final tree: **724 files scanned; boundary hits 0**; T1 (`member_a`) 0.
- new history `main..HEAD`: **433 text blobs scanned**; blob boundary hits 0,
  commit-message boundary hits 0, path boundary hits 0; T1 0.
- raw substring-only hits (boundary 0): 139 in the final tree, 83 in
  new blobs, 1 in commit messages, 0 in paths. These are ordinary
  English-word interiors or pre-existing typo-fixture text, never a standalone real identifier.

Public `main`'s existing tree/history still contains the 244 baseline hits above; this branch
does not rewrite `main` history and cannot remove them. Only the new content introduced by
this branch is fully synthetic.

Residual classes fixed in v2: the literal institution email address is removed from every
historical blob; the live E2E test now extracts the address dynamically from the fetched
official page and fails if it is absent (no invented fallback), while the offline path is
unchanged. Private runner device id, private upstream archive path, Feishu chat/message ids,
private commit SHAs and the non-map fixture personal name are replaced with synthetic
placeholders (`example-runner-01`, `上游私有档案库`, `<chat-id-redacted>`,
`<message-id-redacted>`, `<sha-redacted>`, `Member-A`). Person identifiers use `member_a`
(underscore), not `member-a`. All other addresses/fixtures are synthetic.

The findings below are **not secrets**. They are either deterministic fixtures
already present on public `main`, or synthetic values created solely to test
redaction/QA behaviour. Allowlisting is narrow by file + matched text (+ rule),
never repo-wide or rule-wide.

| file:line | rule | reason |
|---|---|---|
| `plugins/research-tools/marketops-research-assistant/tests/test_research_experience_summary.py:213` | generic-api-key | explicit synthetic test fixture; inline `# gitleaks:allow` |
| `plugins/research-tools/marketops-research-assistant/tests/test_research_lite_integration.py:132` | generic-api-key | explicit synthetic test fixture; inline `# gitleaks:allow` |
| `plugins/research-tools/marketops-research-assistant/tests/test_client_view_generation.py:205` | private-key | explicit synthetic, deliberately invalid PEM header (`MIIBsynthetic`); used only to prove `PRIVATE_KEY_MATERIAL` refusal; inline `# gitleaks:allow` + path/match allowlist |
| `tests/test_application_workstream.py:88` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |
| `tests/test_domain_workstream.py:51` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |
| `tests/test_mcp_product_loop.py:1175` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |
| `tests/test_mcp_responsibility_relations.py:209` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |
| `tests/test_mcp_responsibility_relations.py:277` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |
| `tests/test_mcp_workspace.py:654` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |
| `tests/test_plugin_skill_has_no_authority.py:210` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |
| `tests/test_workstream_store_contract.py:67` | generic-api-key | public main baseline false positive; fixture/deterministic id, unchanged content |

---

## 2026-10-20：研究插件整棵并入（本分支）

这次收敛把私有分支上的研究插件整棵搬进公开树，脱敏口径与上面完全一致：

- 并入 `plugins/research-tools/**`（312 个文件，含 1911 条用例）与
  `docs/quality-scorecard/`（3 个文件）；其中 3 个以人名命名的夹具文件改名为
  `MEMBER_A_*`，与上表「以人名命名的目录、文档与常量」同一条规则。
- 并入前后各跑一次 `apply-synthetic.py --check`（280 个条目 / 5523 个变体形态）：
  **921 个文件扫描，0 命中**；替换是幂等的。
- 被并入的历史不跟随搬迁：本分支只带文件快照，不重写任何既有提交。
- 插件的测试树保留了它自己的写法（测试先 `sys.path.insert` 再 import，属于 E402；
  一行写多条断言，属于 E701/E702）。搬迁期不重排这些代码——重排会把这次 diff 变成
  几百行纯格式改动，也会让上游分支与本仓永久分叉。因此 `pyproject.toml` 里对
  `plugins/research-tools/**` 豁免了这四条**纯风格**规则；会藏 bug 的 F 类
  （未定义名、重复定义、未使用变量/导入）照旧全开，并且已在整棵树上清零。
