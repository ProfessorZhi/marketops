# Document Parsing Benchmark — 第一轮（合成 15 份 + 真实公开 4 份）

状态：**Benchmark Completed（合成数据集 15 份 + 真实公开文档 4 份，含 492 页长文档）**。
最后更新：2026-11-18 18:26 +08:00

---

## 1. 这一轮回答什么

三个解析器在同一批文档上跑同一套判据：

| 解析器 | 覆盖格式 | 运行位置 |
|---|---|---|
| PyMuPDF 2.1 | PDF | 本机 CPU |
| MinerU 4.2.3（tier=standard） | PDF | 本机 GPU（RTX A6000） |
| 原生 Office 解析器（python-docx / python-pptx / openpyxl） | DOCX / PPTX / XLSX | 本机 CPU |

判据在跑之前就固定了，来自 `benchmark/datasets/local-runtime/document/cases.jsonl`，
并先由 `document/verify_gold.py` 对文档本身验证过 15/15：文本类文档必须包含全部
gold marker，扫描类文档的文本层必须为空（否则它就不是在测 OCR）。这一步在解析器
跑之前完成，所以命中率低只能是解析器的问题。

## 2. 数据集

15 份合成文档，内容由 `document/generate.py` 生成，公司与联系人全部虚构，
不含真实个人信息。

| 类型 | 数量 | 设计意图 |
|---|---|---|
| 普通 PDF | 3 | 单页 / 双页 / 含小表 |
| 扫描 PDF | 3 | 纯图片无文本层；一份带线框表格；一份倾斜 −2° + 低对比度 |
| 多栏 PDF | 3 | 两栏 / 三栏 / 两栏跨页带通栏标题 |
| 复杂表格 PDF | 3 | 12×5 表 / 合并单元格表 / 46 行跨页重复表头 |
| Office | 3 | DOCX（含表）/ PPTX（3 页）/ XLSX（2 个工作表） |

每份的 SHA256 记在 `cases.jsonl`，金标来源为生成脚本打印的字符串本身。
生成字节不可复现（Office 写入器带时间戳），可复现的是内容与金标。

## 3. 结果

### 3.1 文本完整性（gold marker 命中率）

| 解析器 | 普通 PDF | 扫描 PDF | 多栏 PDF | 表格 PDF | Office |
|---|---|---|---|---|---|
| PyMuPDF | 1.00 | **0.00** | 1.00 | 1.00 | — |
| MinerU Local GPU | 1.00 | **1.00** | 1.00 | 1.00 | — |
| 原生 Office | — | — | — | — | 1.00 |

扫描件上的 0.00 不是缺陷，是能力边界：那三份文档没有文本层，PyMuPDF 读到的
字符数是 0。它证明的是「扫描件必须走 OCR」，不是「PyMuPDF 差」。

### 3.2 阅读顺序

| 解析器 | 多栏 PDF |
|---|---|
| PyMuPDF | 3/3 |
| MinerU Local GPU | **1/2**（仅两份带顺序判据） |

这是本轮唯一一处 MinerU 低于基线，且是真实缺陷。两栏样本的输出按行交错拼接：

```
Column 1, line 1 ... Column 2, line 1 ...
Column 1, line 2 ... HEAD-1 Column 2, line 2 ... HEAD-2
```

三栏样本反而顺序正确。说明决定结果的是栏间距与布局模型的判定，而不是栏数本身。
对下游的影响是实体与联系方式的归属可能串栏，这在「从 About 页抽联系人」这类
场景里是实质错误。已作为待跟进项记录，未修改任何判据。

### 3.3 表格

三个解析器在表格类样本上的 gold cell 命中率均为 1.00。

这一项的说服力有限，需明说：判据是「单元格字符串是否出现」，不是结构还原。
结构只做了记录、没有计分，而且记录本身也不完整：PyMuPDF 的 `find_tables()` 在
4 份带表 PDF 上分别报出 1/1/1/2 个表（跨页样本正确报出 2 个），MinerU 走 markdown
表格语法，`<table>` 标签计数只在 1 份样本上非零。两边的结构计数口径不同，
不能直接比较。真正区分表格能力需要单元格级的对齐判据，本轮没做。

### 3.4 延迟

| 解析器 | 类型 | p50 | 最大 |
|---|---|---|---|
| PyMuPDF | 全部 PDF | 0.11–0.26 s | 3.23 s（首份，含导入） |
| MinerU | 普通 / 多栏 / 表格 PDF | 9.1–12.0 s | 16.4 s |
| MinerU | **扫描 PDF（OCR）** | **46.8 s** | **69.6 s** |

12 份 PDF 跑完 MinerU 共耗时 **229.5 s**（串行，含每次提交到取回内容的完整往返）。

MinerU 的延迟里含固定开销：每份都 `force=True` 强制重跑，不走缓存；文本类 PDF
仍需走完整版面分析流程。扫描件的 47–70 s 是 OCR 推理本身，属于预期量级。

### 3.5 资源

| 项 | 实测 |
|---|---|
| GPU 常驻显存（MinerU parse-server） | 2547 MiB（首轮）→ 3300 MiB（预热后） |
| 推理期 GPU 利用率采样 | 9 / 21 / 8 / 9 % |
| 模型体积 | torch bundle 854 MB + GGUF 1.2 GB |
| 文档 API 成本 | 0（本地） |

**解析期显存采样**（`services/mineru-local/vram_probe.py`，DOC-PDF-SCAN-001，
0.2 s 间隔，17 个样本，原始数据 `runs/mineru-vram-20261118T180153.json`）：

| 指标 | 基线 | 解析期峰值 | 均值 |
|---|---|---|---|
| 整卡显存 | 6811 MiB | **6979 MiB** | 6849.8 MiB |
| MinerU 进程显存 | 3300 MiB | **3468 MiB** | 3339.5 MiB |
| 整卡利用率 | 27 % | **63 %** | 31.8 % |

17/17 个采样点利用率 > 0，证明推理确实在 GPU 上跑，而不是 CPU 回退。
解析只额外占 +168 MiB；**容量规划应按常驻 3.3 GB 算，不是按增量算**。

注意这块卡是共享的：整卡数字里含其他用户的进程（isaac 2.3 GB 等），
`mineru_proc` 才是本部署可归因的部分。

### 3.6 真实公开文档（3 份）

合成文档证明能力边界，不能代表真实排版。这一组是直接下载的公开文件，
没有手写金标，因此记录的是「截断或串页就一定会露馅」的性质：页覆盖、
参考文献条数、章节标记。

| 文档 | 页 | 字节 | PyMuPDF | MinerU | 版式 |
|---|---|---|---|---|---|
| arXiv 1706.03762（Attention Is All You Need） | 15 | 2.2 MB | 39,498 字符 / 5.0 s | **44,843 / 21.6 s** | 双栏、公式、2 张表、96 条参考文献 |
| arXiv 1810.04805（BERT） | 16 | 775 KB | 64,117 / 5.6 s | **69,711 / 43.0 s** | 双栏、大对照表 |
| IRS Form 1040 | 2 | 220 KB | 10,493 / 3.2 s | **18,695 / 52.5 s** | 表单版式，字段与标签密集 |
| NIST SP 800-53r5 | **492** | 6.1 MB | 1,651,613 / **1.2 s** | 1,626,234 / **491.2 s** | 长文档、层级标题、大量表格；805 个 markdown 标题 |

- **页覆盖**：MinerU 3/3 全部页（有 `<!-- page n of N -->` marker 可逐页核对），
  PyMuPDF 按 `page_count` 计，同样 3/3。
- **参考文献**：Attention 论文两边都是 96 条；截断到 8 页时 MinerU 只有 1 条。
  这个指标能把「停在半路」和「读完了」分开。
- **字符差**主要来自 markdown 标记与 LaTeX 转换，不是内容多寡；1040 表单上
  MinerU 多出的部分是重复的标签/字段名。
- **长文档**：492 页的 NIST 标准，MinerU 492/492 页、9 次 content 调用
  （每次 ≤200k 字符，最后一次 `truncated=false`）、无覆盖缺口。单页约 1.0 s，
  与短文档同量级，说明延迟随页数线性增长，没有隐藏的规模断崖。
  PyMuPDF 在同一份文件上只用 1.2 s——**406 倍差距**，这是文本型 PDF 不该走
  MinerU 的最直接证据。
- MinerU 慢 4–406 倍，这个代价在文本型 PDF 上没有换来可测的质量收益。

> **修正记录（2026-11-18 18:10）**：首次跑 arXiv 时 MinerU 只返回 27,722 字符、
> 1–8 页，被误认为解析器截断。实际是两个 API 默认值叠加，见 §3.7。
> 修复后 15/15 页、44,843 字符、96 条参考文献。
> 首次的错误记录保留在 `runs/public-doc-arxiv-20261118T180500.json`，作否证。

### 3.7 MinerU doclib 的两个静默截断

这两个都不是解析质量问题，是 API 的默认值，但后果一样：**看起来成功，内容缺了一截**。

| # | 位置 | 默认值 | 现象 | 后果 |
|---|---|---|---|---|
| 1 | `POST /api/v1/parses` 不给 `page_range` | `default_parse_range()` → `1-10` | 15 页文档只解析前 10 页，`status: done`、`coverage: null` | 第 11 页起从未解析，读取时也拿不到 |
| 2 | `GET /docs/{ref}/content` 不给 `limit` | 30000 字符 | 第 8 页中途截断，返回 `truncated: true` + `next_request` | 只读第一页响应的客户端拿到 27,722 字符 |
| 3 | 文件入库（`GET /docs/by-path`、watch 扫描） | 自动排一个 `tier=flash`、`page_range=1-10` 的解析 | 492 页文档入库后立刻有一条 1-10 的 flash 解析任务 | 只想查 page_count 也会触发解析；它和手动提交的 1-N 解析并存 |

第 1 条是服务端行为，无法绕过，只能由调用方显式给范围；第 2 条是分页协议，
正确做法是跟随 `next_request` 直到 `truncated=false`。

`benchmark/local_runtime/document_bench.py::parse_mineru` 现在：

1. 先 `GET /docs/by-path` 取 `page_count`；
2. 提交时显式 `page_range: "1-N"`；
3. 读取时带 `page_range` 和大 `limit`，并在 `truncated` 时跟随 `next_request`；
4. 从 page marker 统计 `pages_covered` / `coverage_gaps` 写进结果，覆盖不全会留下痕迹。

合成数据集不受影响：最长 2 页、最大 6,092 字符，两个上限都没碰到，
因此 §3.1–§3.4 的结果不需要重跑。

## 4. 结论

1. **文本型 PDF 不需要 MinerU。** PyMuPDF 0.2 s 拿到 100% 内容，MinerU 12 s 拿到同样的
   100%。差 50 倍延迟，质量无差。
2. **扫描件只能靠 MinerU。** 基线在扫描件上是 0，MinerU 是 1.00。
3. **多栏是 MinerU 当前的弱项。** 两栏交错，三栏正常。若下游要按栏归属实体，
   这一项必须先修或加后处理。
4. **Office 文档用原生解析器。** 毫秒级、100% 命中，没有任何理由转成 PDF 再解析。
5. **成本结论（本地部分）：** 文档解析的边际 API 成本为 0，代价是 GPU 占用与
   47–70 s/页的 OCR 延迟。是否值得取决于扫描件在真实流量中的占比——这个占比
   目前还没有真实数据，不能拍板。

## 5. 本轮不覆盖

- **真实扫描件**：真实公开文档已加入 3 份，但都是文本型 PDF；扫描件仍只有合成样本。
- **真实年报/财报**：SEC EDGAR 从本机返回 403，未能取到 10-K 原件。
- **单元格级表格还原**：当前只测字符串命中。
- **MinerU Cloud 对照**：未执行，配额未确认。
- **字符级 CER / WER**：没有逐字符金标，只有 marker。
- **长篇文档**：已补 492 页 NIST 标准（§3.6），但只有一份；扫描类长文档仍缺。
- **并发与吞吐**：全部串行，并发为 1。

## 6. 原始数据

```
benchmark/local-runtime/runs/
  mineru-smoke-20261118T171211.json          Smoke Test 原始记录（7/7）
  mineru-smoke-20261118T171211-env.json      版本 / GPU / 模型 / PID 记录
  document-bench-20261118T171355.jsonl       PyMuPDF + Office 逐用例
  document-bench-20261118T171408.jsonl       MinerU 逐用例
  document-bench-all.jsonl                   合并后的 27 行
  *.summary.json                             按 parser × doc_type 聚合
  public-doc-arxiv-20261118T180500.json      arXiv 首次对照（客户端截断，作否证）
  public-doc-20261118T181008.json            arXiv 修复后：15/15 页、96 条参考文献
  public-doc-20261118T181304.json            BERT + IRS 1040
```

公开样本身份（SHA256）随每次运行写入记录；文件本身不提交仓库。

复现：

```bash
cd /home/fjd/projects/marketops-local-runtime-lab
PYTHONPATH=$PWD /data/home/fjd/marketops-lab/envs/docbench/bin/python \
  -m benchmark.local_runtime.public_doc \
  --pdf /data/home/fjd/marketops-lab/samples/public/arxiv-attention.pdf
```

`--tables` 会额外统计 PyMuPDF 的 `find_tables()`，在长文档上极慢，默认关闭。
