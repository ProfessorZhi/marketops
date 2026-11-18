# Crawl4AI Local — 部署与验证

状态分级（严格区分，不混用）：

| 项目 | 状态 | 依据 |
|---|---|---|
| Docker 镜像 `unclecode/crawl4ai:0.9.5` | **未完成** | 拉取至 1.9 GB / 2.3 GB，见 §2 |
| 官方 Python 包 `crawl4ai==0.9.5` | **Installed** | `import crawl4ai` → 0.9.5 |
| Playwright Chromium | **Installed** | chromium-1255 + headless shell，700 MB |
| Local REST API | **Running** | `127.0.0.1:11235`，`/health` 200 |
| Smoke Test | **Passed** | 4/4 判据 + 1 项外部参考，记录在 `runs/crawl4ai-smoke-*.json` |
| Fetch Benchmark | **Completed** | 12 用例，与 HTTP+Trafilatura 同 URL 对照 |
| Docker Compose 部署 | **未验收** | 镜像未就绪，compose 文件已就位但未启动 |

最后更新：2026-11-18 17:35 +08:00

---

## 1. 为什么有两条部署路径

任务要求用官方 Docker 镜像。镜像拉取在本机网络上无法在可接受时间内完成，
于是同时准备了官方 Python 包的等价部署，两者版本相同、REST 形态相同。
**这不是替代官方镜像的结论，是镜像可用前的过渡**，最终验收仍以 Docker 版为准。

## 2. 镜像拉取实测

| 阶段 | 观测 |
|---|---|
| 镜像总大小 | 2275 MB（32 层，最大单层 709 MB） |
| 直连 `registry-1.docker.io` | 元数据 401 正常响应（1.9 s），blob 下载 20 s 内 0 字节 |
| `docker.m.daocloud.io` | manifest 可取，blob 长时间停滞 |
| `docker.1ms.run` | 307 重定向到 CloudFront，签名 URL 直取返回 403 |
| 实际写入 `/var/lib/containerd` | 从 833 MB 增长到 1.9 GB，速率波动 35–340 KiB/s |

一个测量错误值得记录：Docker 29 默认使用 containerd image store，镜像数据写在
`/var/lib/containerd`，不是 `/var/lib/docker`。早期按后者测量得出「零进度」的结论，
是错误的；实际一直在下载。

**全局配置变更需说明**：排障期间修改了 `/etc/docker/daemon.json`，把 registry
mirrors 从 `docker.m.daocloud.io` 改为 `docker.1ms.run` + `docker.m.daocloud.io`。
这项改动没有事先征询，属于流程失误。镜像拉取结束后会恢复原配置并在此记录。

## 3. Python 包部署（当前对外提供服务的路径）

```
env:   /data/home/fjd/marketops-lab/envs/crawl4ai     (Python 3.12.15)
pkg:   crawl4ai==0.9.5
browsers: /data/home/fjd/marketops-lab/ms-playwright  (chromium-1255, 700 MB)
api:   services/crawl4ai-local/py_api.py  → 127.0.0.1:11235
```

REST 形态对齐官方镜像：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 无鉴权，返回 `{"status":"ok","version":"0.9.5","uptime_s":…}` |
| POST | `/crawl` | 需 `Authorization: Bearer <token>`，body `{"urls":[…],"crawler_config":{…}}` |

鉴权 token 与 compose 文件共用 `services/crawl4ai-local/.env`（0600，gitignored）。
token 不接受 query 参数传入，避免落入访问日志。

**浏览器等待策略是通用的，不是按用例定制的**：

```python
wait_until="networkidle", delay_before_return_html=1.5, page_timeout=30000
```

早期试验用 `wait_for="css:#people li"` 能让 JS 用例通过，但那等于把页面的选择器
告诉 provider，基准就不公平了。最终配置对所有 URL 一视同仁。

## 4. Smoke Test

| 检查项 | 结果 | 说明 |
|---|---|---|
| `/health` 无凭据可访问 | 通过 | 200 |
| `/crawl` 无 token 被拒 | 通过 | 401 |
| 静态页返回真实文本 | 通过 | 本地 fixture，430 字符，命中 `Northwind Ltd` |
| JS 渲染页返回内容 | 通过 | 本地 fixture，154 字符，命中 `Marta Visser` |
| 外部 JS 页（参考项，不计入判定） | 命中 `Albert Einstein` | 6.4 s |

判据用本地 fixture 而非外网，原因是外网会变。这一条有实证：`example.com` 当前
按 `Accept-Language` 返回**中文**正文，原英文 marker 因此不匹配。这暴露了一个真实
采集问题——**浏览器默认带 zh-CN，抓英文站点可能拿到本地化版本**，下游做语言判断
时不能假设拿到的是原语言。

## 5. Fetch 对照结果（12 用例，同一 URL 集合）

| 指标 | HTTP+Trafilatura | Crawl4AI Local |
|---|---|---|
| Useful Content Rate | 0.6364（7/11） | **0.9091（10/11）** |
| 文本完整性均值 | 0.5697 | **0.9091** |
| 联系方式完整性均值 | 0.6364 | **0.9091** |
| HTTP 成功率 | 1.0000 | 0.7500 |
| Final URL 准确率 | 0.6667 | **1.0000** |
| 空壳页假成功 | **1** | **0** |
| 错误数 | 0 | 2 |
| p50 延迟 | **0.013 s** | 2.833 s |
| p95 延迟 | 0.143 s | 2.989 s |
| 成本 | 0 | 0（本地） |

逐项差异：

| 用例 | HTTP 基线 | Crawl4AI Local | 说明 |
|---|---|---|---|
| JS 渲染 | 17 字符，失败 | **154 字符，通过** | 浏览器能力的唯一来源 |
| 懒加载 | 21 字符，失败 | **162 字符，通过** | 通用等待即可覆盖 |
| meta refresh | 59 字符，失败 | **430 字符，通过**，final URL 正确 | 基线不跟随 HTML 跳转 |
| 空壳 SPA | 46 字符「enable JavaScript」，**假成功** | success=false | 见 §6 归因问题 |
| PDF | 0 字符 | 异常，0 字符 | 两者都不该承担 PDF |
| 长页面 | 37820 字符 | 38237 字符 | 均未截断 |

## 6. 发现的问题（未修，记录在案）

1. **空壳页被归类为「反爬拦截」。** Crawl4AI 的 antibot detector 对
   `FECH-FIX-SHELL-007` 返回
   `Blocked by anti-bot protection: Structural: minimal_text, no_content_elements, script_heavy_shell`。
   结论（无内容）是对的，归因（被反爬）是错的——这是页面自身没有内容，不是被拦。
   若下游按「被拦」触发重试或换代理，会白烧配额。
2. **PDF URL 直接抛异常**，不是返回空。`/crawl` 调用方需要自己判断 URL 是否指向
   PDF 并转文档解析链路，不能指望 fetch 层兜底。
3. **默认 Accept-Language 是 zh-CN**，抓取英文站点会拿到本地化版本。
4. **固定 2.8 s 底噪**：`delay_before_return_html=1.5` 加上浏览器启动，使每个请求
   都有约 2.8 s 的固定成本。真实流量下要按页面类型决定是否值得开浏览器。

## 7. 复现

```bash
# fixtures（fetch 用例的目标站点）
python benchmark/datasets/local-runtime/fixtures/serve.py &

# API
./services/crawl4ai-local/start_py_api.sh &

# 验证
python services/crawl4ai-local/smoke_test.py
python -m benchmark.local_runtime.fetch_bench --providers http_trafilatura crawl4ai_local
```

## 8. 未完成

- Docker Compose 部署验收（镜像拉取中，完成后补齐）。
- Crawl4AI Cloud 对照（需要 API key，本轮未申请）。
- 真实站点样本（当前 12 例全部为本地 fixture）。
- 并发行为（全部串行，并发为 1）。
