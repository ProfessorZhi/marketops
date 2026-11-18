# Crawl4AI Local — 部署与验证

状态分级（严格区分，不混用）：

| 项目 | 状态 | 依据 |
|---|---|---|
| Docker 镜像 `unclecode/crawl4ai:0.9.5` | **Installed** | 32 层逐层校验，`docker images` 见 §2.3 |
| 官方 Python 包 `crawl4ai==0.9.5` | **Installed** | `import crawl4ai` → 0.9.5（备选路径） |
| Playwright Chromium | **Installed** | chromium-1255 + headless shell，700 MB |
| Docker Compose 部署 | **Running** | 容器 `mo-crawl4ai-local`，Up (healthy)，`127.0.0.1:11235` |
| Local REST API | **Running** | `/health` 200，无凭据 `/crawl` 401 |
| Smoke Test | **Passed** | Docker 版 4/4 + 外部参考项；Python 版同样 4/4 |
| Fetch Benchmark | **Completed** | 12 用例；Docker 版与 Python 版结果一致（§5） |

最后更新：2026-11-18 19:45 +08:00

---

## 1. 为什么有两条部署路径

任务要求用官方 Docker 镜像。镜像拉取一度卡住，期间用官方 Python 包做了等价部署
（同版本、同 REST 形态）。**镜像现已就位，Docker 版是当前对外服务的路径**；
Python 版保留为备选与对照，两者在同一套 12 用例上的判定完全一致（§5）。

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
这项改动没有事先征询，属于流程失误。**已恢复**：拉取停止后配置回到
`["https://docker.m.daocloud.io"]`，即最初创建该文件时的内容。

**第二次拉取（18:14–18:40）**：重试同一镜像，18:59 时 `docker pull` 进程仍在，
但 `/var/lib/containerd` 连续 25 分钟保持 **3.1 GB** 不变，最近 5 分钟没有任何
blob 写入，dockerd 自 17:54 起无新日志，`docker images` 仍为空。判定为同一处死锁，
已停止。3.1 GB 层缓存保留，未删除。

**第三次尝试（不走 dockerd）**：安装 `skopeo`，按 manifest 直接拉取层并组装为
`docker-archive`，避免 dockerd 的拉取路径。这是绕过而不是修改全局配置：
`/etc/docker/daemon.json` 保持原样（仅 `docker.m.daocloud.io` 一个 mirror）。
结果见本节末尾更新。

**拉取最终结论（截至 dockerd 两次尝试）**：镜像在约 2.0 GB / 2.3 GB 处死锁，
dockerd 无读取、无日志，多次等待无恢复。Docker 版部署在 `skopeo` 结果出来前判定为
**未完成**，本轮对外服务由官方 Python 包提供。

### 2.1 真正的瓶颈是「单连接」，不是总带宽

第三次尝试的 `skopeo` 已经能下载，但速率只有 **233 KB/s**（tar 30 分钟没有增长，
暂存 blob 每分钟约 14 MB）。当时看起来又像死锁，实测是**单连接限速**：

| 方式 | 实测吞吐 |
|---|---|
| 单连接（skopeo） | 0.23 MB/s |
| 单连接（curl，同一 blob 同一 token） | 0.33–1.16 MB/s |
| **4 路并发（curl，各取 10 MB）** | **6.7 MB/s 合计** |

2.3 GB 走单连接约 2.5 小时；并发下载不到 10 分钟。这是镜像仓库/CDN 的按连接限速，
不是网络不通。

### 2.2 并发拉层 + 本地 OCI 组装

`services/crawl4ai-local/pull_image_oci.py`：只做两件事——并发下载 blob、写 OCI
layout。**组装仍由 `skopeo copy oci:… docker-archive:…` 完成**，所以进 `docker load`
的 tar 是 skopeo 产出的，不是手搓格式。

每个 blob 下载时边下边算 SHA256，与 digest 不符就失败，不会把截断的层留到
`docker load` 才炸。

过程中一个值得记的坑：注册表前面的 CDN 对 `User-Agent: Python-urllib/3.x` 直接返回
Cloudflare **1010**，看起来像 token 失效（所有 blob 都 403）。换一个自报身份的 UA
就正常 —— **403 不一定是权限问题**。

| 阶段 | 实测 |
|---|---|
| 并发下载 32 层 + config | **2,275.5 MB / 399.7 s**（平均 5.7 MB/s） |
| `skopeo` 本地组装 docker-archive | 148 s，tar **5.4 GB**（未压缩层） |
| `docker load` + `docker tag` | 成功，`unclecode/crawl4ai:0.9.5` |

### 2.3 镜像已就位

```
unclecode/crawl4ai:0.9.5   e2a0d754de2a   镜像内容 4.62 GB
```

镜像总层数 32，压缩传输体积 2,275.4 MB。之前的 `crawl4ai-0.9.5.tar.partial-skopeo`
（1.75 GB，单连接半成品）保留未删，作为「串行路径停在半路」的物证。

## 3. Docker Compose 部署（当前对外提供服务的路径）

```
image:     unclecode/crawl4ai:0.9.5        （官方镜像）
container: mo-crawl4ai-local，network_mode: host
api:       127.0.0.1:11235（服务自身只绑 loopback）
token:     services/crawl4ai-local/.env   （0600，gitignored）
```

三处**必须显式配置**，默认值下本地基准跑不起来：

1. **`network_mode: host`**。镜像默认 bridge；容器里的 `127.0.0.1` 是容器自己，
   而 fixture 在宿主机回环上。改成端口映射没用——映射只暴露 11235，不会把
   18080/18081 引进容器。host 网络下两边共享回环，且服务本身仍只绑 loopback。
2. **`CRAWL4AI_ALLOW_INTERNAL_URLS=true`**。官方镜像有 SSRF 防护
   （`/app/utils.py:353` + `egress_broker`/`egress_proxy`），默认拒绝
   127.0.0.0/8、10/8、169.254/16 等全部非全局地址，返回
   `400 URL blocked (SSRF protection)`。基准全部打本地 fixture，必须放开。
   **这是基准部署的开关，生产不能带**——注释已写在 compose 里。
3. **显式 `crawler_config`**。镜像默认不等待页面定时器，`setTimeout(300)` 的 JS
   页只读到 20 字符空壳；加 `wait_until=networkidle` +
   `delay_before_return_html=1.5` 后读到 154 字符。这套等待策略对**所有 URL
   相同**，不是按用例给选择器（`wait_for="css:#people li"` 会把答案位置告诉
   provider）。

## 3.1 Python 包部署（镜像可用前的过渡路径，保留）

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

| 指标 | HTTP+Trafilatura | Crawl4AI（Python 版） | Crawl4AI（Docker 版） |
|---|---|---|---|
| Useful Content Rate | 0.6364（7/11） | **0.9091（10/11）** | **0.9091（10/11）** |
| 文本完整性均值 | 0.5697 | **0.9091** | **0.9091** |
| 联系方式完整性均值 | 0.6364 | **0.9091** | **0.9091** |
| HTTP 成功率 | 1.0000 | 0.7500 | 0.7500 |
| Final URL 准确率 | 0.6667 | **1.0000** | **1.0000** |
| 空壳页假成功 | **1** | **0** | **0** |
| 错误数 | 0 | 2 | 2 |
| p50 延迟 | **0.013 s** | 2.833 s | 2.265 s |
| p95 延迟 | 0.143 s | 2.989 s | 2.327 s |
| 成本 | 0 | 0（本地） | 0（本地） |

两种部署逐项判定完全一致（12/12 同结论），说明决定结果的不是部署形态，而是
Crawl4AI 本身的能力边界。Docker 版 p50 快 0.57 s 来自服务端固定开销更小，
不是质量差异。

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
   已做二次验证：打开 Crawl4AI 自带的 `BrowserConfig(accept_downloads=True)` 后
   重试，**仍然失败**，错误同为 `Page.goto: Download is starting`。源码
   `async_crawler_strategy.py:771` 只兜底 `net::ERR_ABORTED`，不匹配 Playwright
   这个错误。**这是确定性的能力缺口，不是配置问题**，见
   `JOINT_PIPELINE_REPORT.md` §4（12/12 PDF 全部在 fetch 阶段失败）。
3. **默认 Accept-Language 是 zh-CN**，抓取英文站点会拿到本地化版本。
4. **固定 2.8 s 底噪**：`delay_before_return_html=1.5` 加上浏览器启动，使每个请求
   都有约 2.8 s 的固定成本。真实流量下要按页面类型决定是否值得开浏览器。
5. **重定向后报告的 `status_code` 是 302，不是最终 200**。Cookie 用例
   （`FECH-FIX-COOKIE-006`）内容 100% 命中、`final_url` 也正确，但按状态码判定的
   `http_success` 记为 false。下游若只用 `status_code == 200` 判成败，会把成功的
   重定向记成失败；应以 `success` + `final_url` + 内容三项判定。
6. **两个「同版本」部署的响应结构不一样**。Python 版 `markdown` 是字符串，
   官方镜像的 `markdown` 是对象（`raw_markdown` / `markdown_with_citations` /
   `fit_markdown`）；最终地址 Python 版叫 `final_url`，镜像叫 `redirected_url`。
   只读一种形状的客户端会把成功抓取读成 0 字符、把重定向记成没跳转 —— 本轮
   就踩到了，修在 `fetch_bench._markdown_text` / `_final_url`。**接入前先对齐
   字段，别假设版本号相同就能直接换。**
7. **官方镜像默认拒绝内网地址**（SSRF 防护），Python 版没有这层校验。这不是
   可以忽略的差异：镜像拦住的是 `169.254.169.254` 这类元数据端点。基准部署
   用 `CRAWL4AI_ALLOW_INTERNAL_URLS=true` 放开，生产不得照抄。

## 7. 复现

```bash
# fixtures（fetch 用例的目标站点）
python benchmark/datasets/local-runtime/fixtures/serve.py &

# API（二选一）
cd services/crawl4ai-local && sudo docker compose up -d      # 官方镜像，当前在用
./services/crawl4ai-local/start_py_api.sh &                  # Python 包，备选

# 验证
python services/crawl4ai-local/smoke_test.py
python -m benchmark.local_runtime.fetch_bench --providers http_trafilatura crawl4ai_local
```

## 8. 未完成

- Crawl4AI Cloud 对照（需要 API key，本轮未申请）。
- 真实站点样本（当前 12 例全部为本地 fixture）。
- 并发行为（全部串行，并发为 1）。
- 容器重启策略只在 compose 里声明（`unless-stopped`），未验证宿主机重启后的自动恢复。
- 镜像没有挂 volume，缓存只在进程内存里；重启后首请求是冷的。
