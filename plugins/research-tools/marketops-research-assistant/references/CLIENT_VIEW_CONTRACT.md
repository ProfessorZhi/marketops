# 客户视图生成契约（client-view）

`scripts/research_client_view.py` 把**插件正式过滤后的业务 Sheet**投影成
`client-view/` 下的 CSV + XLSX + `MANIFEST.json`。它不是第二个准入引擎，
不改 `research_delivery.py` 的候选语义，也不做任何外部调用。

```python
write_client_view(destination: Path, sheets: Sequence[Sheet], *,
                  label: str, generated_at: object = None) -> dict
verify_client_view(destination: Path) -> dict
```

## 1. 输入契约

调用方传入的 `sheets` 必须**已经**由插件正式过滤器筛过。本 helper 只校验并投影：

| 输入条件 | 行为 |
| --- | --- |
| 任一行含 `HOLD` / `HOLD_*` 标记 | `HOLD_ROW_REJECTED`，不创建目标目录 |
| 任一行含 `CONFLICT` / `冲突` 标记 | `CONFLICT_MARKER` |
| 同一业务表首列 ID 重复 | `DUPLICATE_RECORD_KEY` |
| 同一 `资料ID` 在多张表重复 | `DUPLICATE_RECORD_KEY` |
| 行内所有 `*资料引用*` / `资料ID` / `来源URL` 均为空或 `NONE`/`UNKNOWN` | `PROVENANCE_MISSING` |
| 传入证据表（含 `资料ID`）时，`*资料引用*` 指向不存在的 ID | `PROVENANCE_UNKNOWN_REF` |
| 状态列出现 `APPROVED` / `READY` / `VERIFIED` 等精确断言 | `APPROVAL_CLAIM_REJECTED` |
| 单元格含私钥标记 | `PRIVATE_KEY_MATERIAL` |
| 单元格含 XML 不接受的控制字符、孤立代理码位或超长文本 | `UNSAFE_CELL_CHARACTER` / `CELL_TOO_LONG` |

**拒绝，而不是丢弃。** 若在这里静默过滤 HOLD，客户可见行数就取决于一个调用方
没有执行的过滤器，事后无法解释。调用方应先在自己的正式过滤层处理，再把剩余
Sheet 交给本函数。

每张表至少要有一个 provenance 列：列名含 `资料引用`，或精确为 `资料ID` /
`来源URL`。这是“无引文不出”的落点。

## 2. 输出布局

`destination` 就是 `client-view/`：

```text
client-view/
├── Client_View.xlsx          # 说明页 + 来源索引 + 全部业务表
├── 客户视图说明.csv
├── 来源索引.csv
├── <业务表名>.csv            # 每张输入表一份
└── MANIFEST.json
```

- 所有单元格按文本写出：ID 保留前导 0，数值不被表格软件改写。
- 公式注入前缀（`=` `+` `-` `@`、前置空白、`\t`、`\r`、`\n`）与
  `export_excel_safe_csv.literal_cell` 一致，写为 `'` 前缀的字面文本。
- XLSX 复用 `_xlsx.Sheet` / `render_workbook`，仅把 ZIP 时间戳规范化为固定值，
  同一输入得到逐字节相同的文件。
- `来源索引` 逐行列出 `业务表 / 行号 / 记录ID / 资料引用`，provenance 在客户视图中
  可见，不要求读者反查 manifest。

## 3. MANIFEST / receipt

`MANIFEST.json` 同时是返回值的持久化形态。关键字段：

```json
{
  "schema": "marketops.client-view/1",
  "artifact": "client-view",
  "label": "...",
  "generated_at": "...",
  "status": "CANDIDATE_NOT_APPROVED",
  "send_authorized": false,
  "formal_sop": false,
  "formal_sop_claimed": false,
  "business_verification": "NOT_PERFORMED",
  "input_source": "FILTERED_BUSINESS_SHEETS_ONLY",
  "counts": {"客户总表": 3},
  "provenance": {
    "provenance_columns": {"客户总表": ["原始资料引用"]},
    "referential_integrity": "CHECKED",
    "evidence_ids": 3,
    "checked_rows": 9
  },
  "files": {"客户总表.csv": {"bytes": 123, "sha256": "..."}}
}
```

对接最终 MANIFEST 时，把 `artifact`、`label`、`status`、`counts`、`files` 原样并入
父级 `artifacts["client-view"]` 即可；不要重算状态或覆盖 `files`。返回值额外带
`directory`、`xlsx_path`、`manifest_sha256`、`verified`、`replayed`，这些是运行时
定位信息，不写入父级 manifest。

`manifest_sha256` 是 `MANIFEST.json` 自身的哈希（不能写进它自己），由返回值给出。

## 4. 状态语义

- 视图与每一行都只能是 `CANDIDATE_NOT_APPROVED` 语义；输入行里的状态列原样投影，
  但本 helper 不会把任何行晋升为正式联系人或正式 SOP。
- `send_authorized` 恒为 `false`，`formal_sop_claimed` 恒为 `false`。
- `business_verification` 恒为 `NOT_PERFORMED`；引用可回读不等于业务事实已验证。
- 不宣称市场覆盖率，不生成发信批次。

## 5. 重放与写入原子性

1. 先在 `destination` 同级的临时目录生成、读回、校验。
2. `destination` 不存在时原子 rename 发布。
3. `destination` 已存在且内容逐字节相同：返回 `replayed: true`，不重写。
4. `destination` 已存在但内容不同：`DESTINATION_CONFLICT`，绝不覆盖他人的视图。
5. 任何拒绝都发生在创建目标目录之前；不会留下半份 client view。

相同 `sheets` + 相同 `label` + 相同 `generated_at` ⇒ 相同文件哈希；测试用固定
`generated_at` 验证逐字节重放。

## 6. 校验

```bash
python scripts/research_client_view.py <client-view 目录>
```

`verify_client_view` 只读磁盘：校验 `files` 的 size/SHA-256、文件集合、CSV 行数、
XLSX 工作表名与状态标志；篡改任一文件会报 `MANIFEST_HASH_MISMATCH` 或
`MANIFEST_SIZE_MISMATCH`。

## 7. 集成注意（交给 Lane A / 集成者）

- 本 Lane 只拥有 `scripts/research_client_view.py`、
  `tests/test_client_view_generation.py`、本文件。
- `scripts/build_client_bundles.py` 的脚本拷贝清单目前**没有**包含
  `research_client_view.py`；集成时必须由 Lane A/集成者在打包清单里补上，
  否则客户端包里没有这个 helper。本 Lane 不修改该文件。
- 正式调用顺序：`research_delivery.build_delivery` 产出候选 Sheet → 上游正式
  过滤 → `write_client_view(client_view_dir, filtered_sheets, label=run_id)`。
  不要跳过正式过滤直接把 `build_delivery` 的九张表全量传入（其中
  `排除与待确认` 会含 HOLD/冲突标记，本 helper 会拒绝）。
- 测试：
  `python tests/test_client_view_generation.py -v`
  （仅标准库 + `_xlsx`，离线，不依赖 MCP/Provider）。
