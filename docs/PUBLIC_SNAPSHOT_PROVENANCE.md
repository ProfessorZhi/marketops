# 公开快照的两套哈希

状态：**已实现**。接续 MarketOps 修复时按下述 additive 方案补齐公开哈希与
`publication` 声明。已有 `snapshot_sha256`、上游 pin 和快照文件均未改写。

## 事实

`REDACTION.md` 已经写明：本仓库是**脱敏公开快照**，`sources/` 里的消息快照与上游
`upstream-records` **不再逐字节一致**。`sources/README.md` 与 `docs/SOURCES.md`
描述的是脱敏前的模型：`snapshot_sha256` 必须等于上游那一版的 sha256。

两句话在公开仓里同时为真，结果就是：

- `sources/manifest.yaml` 的 `snapshot_sha256` 仍然是**脱敏前**那一版的哈希；
- 仓库里的文件是**脱敏后**的派生字节；
- `tests/test_sources_snapshot.py::test_snapshot_hashes_match_the_files` 要求两者
  相等，于是在 5 条已捕获条目里有 4 条必然不等。

在 `main@63a5fe486113a83afb063265e3458b46877789fa` 上实测（文件与 HEAD blob
逐字节相同，排除检出过滤）：

| 条目 | manifest `snapshot_sha256`（脱敏前） | 公开文件实际 sha256 |
|---|---|---|
| `member-a-private` | `206420c655ebc0e1…` | `1fabe8c9549a2bbf4d6f45ce44d7b8d564ea17a0b7efc1d29d52f2bb97e57a71` |
| `member-d-private` | `e3b0c44298fc1c14…` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（零字节，相等） |
| `member-c-private` | `a594f1b7dca225e0…` | `fef2643fb04e9b440604612143518d864801f3777f56718c202ee9cb1f84dd8d` |
| `party-group` | `6c7102da5ab5821f…` | `a5c7b3b3a1620f36e555dee76cc095bfb1946e6d1bb885e6f5a31ef36e610a29` |
| `sop-uk-natural-grass-acme-v1` | `7d9c602c6e8cca0e…` | `2e4b837b60ec8d5ed86007e669c6f3fcb30bc5c381efb902b2dbafdbbb8cc6c3` |

上表的完整公开哈希是**这次测量的结果**，不是已经生效的 pin；测量对象是上表
点名的那个 revision。文件变了，它们就应当重新计算。

## 为什么不能直接把 `snapshot_sha256` 改成公开文件的哈希

`manifest.yaml` 自己的 refresh 规则写着：只允许追加新条目，**不允许原地改写**
`upstream_blob_sha` / `snapshot_sha256` / `message_ids`。那个值是「当时读的是哪一版」
的历史事实，也是上游对照（`test_upstream_*`）的输入。覆盖它等于删掉那一段历史，
而且会让「公开文件是否被改过」与「上游那一版是什么」这两个问题重新混在一个数字里。

## 公开安全表示（additive，不改旧字段）

1. 每条已捕获条目增加 **`public_snapshot_sha256`**：本仓库中该文件字节的 sha256，
   64 位小写 hex。它与 `snapshot_sha256` 并列，语义分别是「公开后有没有被改过」与
   「脱敏前是哪一版」。
2. manifest 顶层增加一个 `publication` 块，声明这是
   `redacted-public-derivative`，指向 `REDACTION.md`，并写明两个哈希各自回答什么。
3. `test_snapshot_hashes_match_the_files` 改为对 `public_snapshot_sha256` 做
   文件比对；`snapshot_sha256` 保留格式与上游语义。再增加一条判据：每条已捕获
   条目**两个哈希都必须有**，`publication.snapshot_kind` 取封闭值 —— 这样
   「删掉一个字段」不会重新变成静默放行。
4. 迁移只**追加** `public_snapshot_sha256`（用当前公开树计算，确定且不含秘密），
   不触碰已有 `snapshot_sha256`。

## 校验边界

默认 CI 校验当前公开字节与 `public_snapshot_sha256`，并要求每条已捕获记录同时
保留两个合法哈希；删除任一个字段都会失败。未捕获记录不允许携带公开哈希。
上游检查仍使用原始 pin，不把脱敏后的公开文件当成原始字节的替代品。
