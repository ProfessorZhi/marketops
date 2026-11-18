# 联合流水线 — Crawl4AI / MinerU / PyMuPDF 端到端对照

状态：**Completed（12 份合成 PDF + 4 份真实公开 PDF，含 492 页长文档）**
最后更新：2026-11-18 19:00 +08:00

---

## 1. 结论

| 流水线 | 成功率 | 文本完整性均值 | p50 延迟 | 总延迟（12 份） |
|---|---|---|---|---|
| P1 HTTP 下载 → PyMuPDF | **12 / 12** | 0.75 | 0.191 s | **3.54 s** |
| P2 HTTP 下载 → MinerU | **12 / 12** | **1.00** | 4.223 s | 52.20 s |
| P3 Crawl4AI 抓取 → MinerU | **0 / 12** | — | — | — |

三句话：

1. **Crawl4AI 不能直接取 PDF。** 12/12 在 fetch 阶段失败，不是解析失败。
2. **PyMuPDF 对扫描件完全无效**（3/3 完整性 0），这是 MinerU 唯一不可替代的
   价值点。
3. P2 的代价是 **14.7 倍延迟**（52.2 s vs 3.5 s）换 **+0.25 完整性**。

在 4 份真实公开 PDF（525 页，含 492 页 NIST 标准）上重复同一套流水线，结论不变：
P1 4/4（1.77 s）、P2 4/4（567 s，525/525 页）、P3 0/4（全部失败在 fetch），
见 §8。P3 的失败不是本地 fixture 的产物。

P3 失败不等于「浏览器没用」。P4 让浏览器只渲染 HTML 页面并读出 PDF 链接，
字节仍由 HTTP 取：**4/4，15.78 s**。浏览器负责 HTML/JS，HTTP 负责二进制，
这是本轮唯一被证明可用的组合方式（§8.1）。

**换成 Docker 版 Crawl4AI 后结论不变**：P3 仍 0/12（全部失败在 fetch），
P4 仍 4/4（10.77 s，官方镜像的服务端开销更小）。差别在部署形态，不在能力边界。

## 2. 流水线定义

```
P1  GET url ──────────────► bytes ─► PyMuPDF ─► text
P2  GET url ──────────────► bytes ─► MinerU  ─► markdown
P3  Crawl4AI /crawl url ──► text  ─► MinerU  ─► markdown
P4  Crawl4AI 渲染 HTML 页 ─► 读出 PDF 链接 ─► GET 字节 ─► PyMuPDF ─► text
```

三者的 URL 集合、字节、gold markers 完全相同。P3 没有在失败时偷偷回退到 HTTP：
如果浏览器拿不到文档，这条流水线就是失败的，回退会把一个失败伪装成成功。

文档来自 `benchmark/datasets/local-runtime/document/`，12 份 PDF：
plain ×3、scanned ×3、multicolumn ×3、table ×3。同一批文件通过
`127.0.0.1:18081` 以 HTTP 暴露，再由各流水线抓取，因此下载的是真实字节
（`blob_hash_matches_source` 12/12 与源文件 SHA256 一致）。

## 3. 结果

### 3.1 抓取阶段

| 方式 | 成功 | p50 | 总耗时 | 失败原因 |
|---|---|---|---|---|
| HTTP GET | **12/12** | 0.002 s | 0.029 s | — |
| Crawl4AI `/crawl` | **0/12** | 0.639 s | 8.30 s | `Page.goto: Download is starting` |

### 3.2 解析阶段

| 解析器 | 成功 | 完整性 | p50 | 总耗时 |
|---|---|---|---|---|
| PyMuPDF | 12/12 | 0.75 | 0.191 s | 3.54 s |
| MinerU 4.2.3 (standard) | 12/12 | **1.00** | 4.223 s | 52.20 s |

完整性 0.75 = 9 份 1.0 + 3 份扫描件 0.0。PyMuPDF 不是解析出错，是扫描 PDF
**没有文字层**，它拿不到任何字符。

### 3.3 逐例

| 文档 | PyMuPDF | MinerU |
|---|---|---|
| PLAIN-001/002/003 | 1.0 | 1.0 |
| **SCAN-001/002/003** | **0.0** | **1.0** |
| MULTICOL-001/002/003 | 1.0 | 1.0 |
| TABLE-001/002/003 | 1.0 | 1.0 |

## 4. Crawl4AI 取 PDF：两次尝试

第一次（默认配置）：

```
Error: Failed on navigating ACS-GOTO:
Page.goto: Download is starting
```

第二次：打开 Crawl4AI 自带的 `BrowserConfig(accept_downloads=True)` 后重试，
**仍然失败，错误完全相同**。

源码解释了为什么：`async_crawler_strategy.py:771` 只兜底
`net::ERR_ABORTED`，而 Playwright 在「导航被下载接管」时抛的是
`Page.goto: Download is starting`，不匹配这个分支，于是被重新抛出。

**结论**：Crawl4AI 0.9.5 的 fetch 层不承担 PDF。调用方必须按 content-type
或 URL 后缀分流，PDF 走 HTTP 下载。这不是配置问题，不能靠调参绕过。

代码里保留了这个开关（`CRAWL4AI_ACCEPT_DOWNLOADS`，默认 0）和注释，因为它
记录了一次否证；服务当前以默认值运行。

## 5. 耗时与成本

| 阶段 | 12 份总耗时 |
|---|---|
| HTTP 下载 | 0.029 s |
| PyMuPDF 解析 | 3.54 s |
| MinerU 解析 | 52.20 s |
| **P1 端到端** | **3.57 s** |
| **P2 端到端** | **52.23 s** |

- 成本：API 成本 **$0**（全部本地）。
- MinerU 的边际成本不是钱，是 GPU 时间：52 s 里 GPU 常驻约 2.5 GB。
- 这里的 4.2 s/份比首轮 document benchmark 的 9–47 s 快，因为模型已预热、
  同一批文件重复解析命中缓存。**不能把 4.2 s 当作冷启动延迟**。

## 6. 生产含义

按文档类型分流，而不是按「有哪个工具」分流：

| 文档类型 | 建议链路 | 理由 |
|---|---|---|
| 有文字层的 PDF | HTTP → PyMuPDF | 0.19 s vs 4.2 s，质量相同 |
| 扫描 PDF | HTTP → MinerU | PyMuPDF 无法读取，MinerU 1.0 |
| 复杂表格/多栏 | 先 PyMuPDF，质量不达标再升 MinerU | 本轮两者都 1.0，未分出差距 |
| 网页 | Crawl4AI（JS 页）或 HTTP+Trafilatura | 见 `FETCH_BENCHMARK_REPORT.md` |
| PDF URL | **不要**交给 Crawl4AI | 0/12，确定性失败 |
| 网页里的 PDF 链接 | Crawl4AI 取链接 → HTTP 下字节 → PyMuPDF | P4 4/4，15.78 s |

一个可用的判定顺序：

```
1. 拿到一个 URL
   ├─ 响应是 HTML → 静态页用 HTTP+Trafilatura；有 JS 用 Crawl4AI
   │                页面里有 PDF 链接 → 交给 HTTP 下载（不要让浏览器点）
   └─ 响应是 PDF  → HTTP 下载 → PyMuPDF
                    若文本 < 阈值（扫描件）→ MinerU
```

分流的依据必须是**内容类型或字节头**，不能靠「先试浏览器、失败再回退」：
Crawl4AI 对 PDF 抛的是导航异常，和网络错误无法区分，重试只会重复失败。

## 7. 本轮不覆盖

- 真实公开 PDF 中的**扫描件**。已加入 4 份真实文档（§10），但都是文本型。
- 大文件（>100 页）与超时行为。
- 并发：全部串行，并发 1。
- MinerU 冷启动延迟（本轮命中缓存）。
- Crawl4AI 通过 HTML 页面**发现** PDF 链接的路径（只测了直接 URL）。
- OCR 质量的语言维度（只有英文样本）。

## 8. 真实公开文档（4 份）

同一套流水线，换成下载来的公开 PDF。没有金标，成功定义为：
HTTP 下载字节与源文件一致、解析返回内容、MinerU 覆盖全部页。

| 文档 | 页 | HTTP | P1 PyMuPDF | P2 MinerU | P3 Crawl4AI |
|---|---|---|---|---|---|
| arXiv Attention | 15 | 0.015 s | 0.11 s / 39,498 字符 | 23.6 s / 44,843 | 失败 |
| arXiv BERT | 16 | 0.009 s | 0.06 s / 64,117 | 22.9 s / 69,711 | 失败 |
| IRS Form 1040 | 2 | 0.003 s | 0.06 s / 10,493 | 49.0 s / 18,695 | 失败 |
| NIST SP 800-53r5 | 492 | 0.085 s | 1.54 s / 1,651,613 | 471.8 s / 1,626,231 | 失败 |

| 汇总 | P1 | P2 | P3 |
|---|---|---|---|
| 成功 | **4 / 4** | **4 / 4** | **0 / 4** |
| 页覆盖 | 525 / 525 | **525 / 525** | — |
| 总延迟 | **1.77 s** | 567.28 s | — |
| 失败阶段 | — | — | fetch ×4 |

- P2 是 P1 的 **320 倍**延迟，换来的是扫描件能力（本组文档没有一份需要它）。
- P3 的 4 次失败与合成集完全同一错误：`Page.goto: Download is starting`。
  **这不是 fixture 的产物**，真实 PDF 一样失败。
- MinerU 长文档用了 9 次 content 调用（每次 ≤200k 字符），无覆盖缺口，
  分页协议在真实大文件上工作正常。
- PyMuPDF 的表格结构检测（`find_tables()`）不计入这里的延迟：它在 492 页文档上
  要 110 s，而文本提取只要 1.5 s，两者不是同一条生产路径。

### 8.1 P4：浏览器只负责找链接

P3 让 Crawl4AI 直接取 PDF，0/4。P4 换分工：浏览器渲染一个普通的 HTML 页面，
从页面里读出 PDF 链接，再由 HTTP 下载字节，交给 PyMuPDF。

| 指标 | P3（浏览器取 PDF） | P4（浏览器找链接） |
|---|---|---|
| 成功 | 0 / 4 | **4 / 4** |
| 页面渲染 | — | 4 / 4 |
| 字节与源文件一致 | — | 4 / 4 |
| 总延迟 | — | 15.78 s（Docker 版重跑 10.77 s） |
| 失败原因 | `Page.goto: Download is starting` | — |

这就是可用的生产拓扑：**浏览器负责 HTML 与 JS，HTTP 负责二进制**。
把 PDF 交给浏览器既拿不到字节，也拿不到错误（Crawl4AI 抛的是导航异常，
不是「这是 PDF」），所以由内容类型或链接后缀分流比任何重试都可靠。

复现：

```bash
python3 -m http.server 18082 --bind 127.0.0.1 \
  -d /data/home/fjd/marketops-lab/samples/public &
PYTHONPATH=$PWD /data/home/fjd/marketops-lab/envs/docbench/bin/python \
  -m benchmark.local_runtime.joint_pipeline --public
```

## 9. 复现

```bash
# 依赖服务
python benchmark/datasets/local-runtime/fixtures/serve.py &          # 18080
python -m http.server 18081 --bind 127.0.0.1 \
  -d benchmark/datasets/local-runtime/document/generated &           # 18081
./services/crawl4ai-local/start_py_api.sh &                          # 11235
# MinerU doclib 18000 需已启动

/data/home/fjd/marketops-lab/envs/docbench/bin/python \
  -m benchmark.local_runtime.joint_pipeline
```

必须用 `docbench` venv：系统 Python 没有 `pymupdf`/`httpx`，用错解释器会得到
12 条假的「解析失败」。

## 10. 原始数据

```
runs/joint-pipeline-20261118T175723.jsonl         合成集 12 行
runs/joint-pipeline-20261118T175723.summary.json  聚合
runs/joint-public-20261118T184345.jsonl           真实公开文档 4 行
runs/joint-public-20261118T184345.summary.json    聚合
runs/joint-public-20261118T182635.jsonl           首次运行，PyMuPDF 含表格检测（P1 延迟不可比）
runs/joint-public-20261118T190816.jsonl           P4：页面→链接→HTTP→PyMuPDF，4/4
runs/joint-pipeline-20261118T194305.jsonl         Docker 版重跑：P3 仍 0/12（fetch）
runs/joint-public-20261118T194412.jsonl           Docker 版 P4：4/4，10.77 s
```

（`175609` 那一轮是错误解释器产生的无效数据，已删除，不进入报告。）
