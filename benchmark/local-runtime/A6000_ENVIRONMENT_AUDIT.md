# A6000 环境审计（Phase 0）

> 仓库是 **public**。本文只写规格、状态和判断，不写内网地址、主机名、公网 IP、凭据和
> 原始请求/响应。所有数值均为本机实测，观测时间见文首。

| 项 | 值 |
|---|---|
| 观测时间 | 2026-11-18T16:28–16:32 +08:00 |
| 运行用户 | `fjd`（uid 1000，组含 `sudo`、`llm-team`） |
| 项目根 | `/home/fjd/projects/marketops-local-runtime-lab` |
| 代码分支 | `feat/a6000-local-runtime-lab` |
| 本地 HEAD | `536b78b849e309c4d4685ecfe09ea50b06da68ff` |
| 远端同分支 | `536b78b849e309c4d4685ecfe09ea50b06da68ff`（一致） |

---

## 1. 结论摘要

| 能力 | 状态 | 对 Phase 1 的影响 |
|---|---|---|
| GPU | ✅ RTX A6000 48 GB，空闲 | 可部署 MinerU，但必须限并发 |
| CPU / RAM | ⚠️ 20 线程 / **30 GiB**（非 128 GiB） | 内存是本机最紧的资源 |
| `/data` 数据盘 | ✅ 1.6 TiB 可用 | 模型权重与中间产物放这里 |
| Docker / Compose | ❌ **未安装** | **Crawl4AI 官方 Docker 部署阻塞** |
| NVIDIA Container Toolkit | ❌ 未安装 | 即便装了 Docker，GPU 容器仍需它 |
| CUDA 驱动 | ✅ 595.99.02，CUDA 13.2 | 向下兼容 PyTorch cu12x |
| CUDA Toolkit | ✅ `/usr/local/cuda-13.1` | `nvcc` 未在 PATH，仅影响编译 |
| PyTorch | ❌ 系统 Python 无 torch | MinerU 需自建独立环境 |
| 系统 Python | 3.14.4，无 pip、无 venv 模块 | 必须用 `uv` 建环境 |
| `uv` | ✅ `~/.local/bin/uv`，自带 3.11/3.12 | Python 3.12 可用于 MinerU |
| 网络出口 | ✅ pypi / github / docker hub / huggingface 均可达 | 可拉镜像与权重 |

**一句话**：硬件和网络就绪，GPU 空闲；缺的是 **Docker 栈**，而这需要 `sudo`
（当前 `sudo` 需要交互密码，非免密）。

---

## 2. GPU

```
GPU 0: NVIDIA RTX A6000
  显存       49140 MiB（47,479 MiB free）
  驱动       595.99.02
  CUDA 版本  13.2（驱动报告）
  功耗上限   300 W，当前 9 W（P8 待机）
  温度       27 °C
  PCIe        Gen3 x16（max）
  ECC        关闭（消费级配置）
  利用率     0 %
```

当前占用显存的进程（合计约 **1035 MiB**）：

| 进程 | 显存 | 说明 |
|---|---|---|
| `gnome-shell` | 146 MiB | 桌面合成器（图形） |
| `gnome-remote-desktop-daemon` | 266 MiB | 远程桌面（图形） |
| `x-terminal-emulator` | 32 MiB | 终端（图形） |
| `selkies` ×2（`/tmp/.mount_*`、`/home/venus/...`） | 262 MiB ×2 | 另一位用户的远程桌面运行时 |

**判断**：

- 没有推理服务常驻，无模型权重加载。GPU 处于完全空闲状态。
- 这 1 GiB 是图形栈，**不可回收，也不应尝试回收**。
- 可用显存约 **47.5 GiB**，对 MinerU 2.x（单模型约 6–10 GiB）绰绰有余。
- 任务书要求「不得抢占现有 GPU 显存」。当前没有可抢占的推理进程；部署 MinerU 时
  仍应按 **并发 1** 起步，并设置显存上限，避免与后续出现的实验争抢。

---

## 3. CPU / 内存 / 磁盘

### 3.1 CPU

| 项 | 值 |
|---|---|
| 型号 | Intel Core i9-10900X @ 3.70 GHz |
| 物理核 / 线程 | 10 / **20** |
| 当前频率 | 约 1.2–1.4 GHz（节能态） |
| 负载（1/5/15 分钟） | 0.33 / 0.39 / 0.47 |

20 线程对 Crawl4AI 的浏览器池和 MinerU 的预处理足够；当前负载很低。

### 3.2 内存（关键约束）

```
总计    30 GiB
已用    8.5 GiB
可用    22 GiB
缓存    18 GiB
Swap    8 GiB（已用 4.4 GiB）
```

**30 GiB 总量，不是任务书设想的 128 GiB 级配置。** Swap 已用 4.4 GiB，说明
历史上有过内存压力。这直接决定了两件事：

1. MinerU 模型常驻 + 页面预处理必须限并发（建议起始 `batch = 1`，worker = 1）；
2. 不要在同一时段并发跑 Crawl4AI 多浏览器实例 + MinerU 推理。

部署前应再观测一次，避免把「当前恰好空闲」当成「一直有空」。

### 3.3 磁盘

| 挂载 | 容量 | 已用 | 可用 | 用途 |
|---|---|---|---|---|
| `/`（NVMe 512 GB） | 468 G | 217 G | **227 G** | 系统、代码、venv |
| `/data`（LVM 1.9 T） | 1.9 T | 225 G | **1.6 T** | 模型权重、数据集、中间产物 |

`/data/home/fjd` 存在且 **fjd 可写**，下有 `isaac/`、`rt_isaac_benchmark/`
（另一位实验的产物，**本轮不触碰**）。

**放置约定**：

- 代码仓：`/home/fjd/projects/marketops-local-runtime-lab`（已在）
- 模型权重 / 缓存 / 中间产物：`/data/home/fjd/marketops-lab/`（新建，不与 `isaac` 混放）
- 不得把数 GB 权重写进 `$HOME`（宿主策略亦禁止）。

---

## 4. 容器与 CUDA 栈

### 4.1 Docker：缺失

```
docker                 MISSING
docker-compose         MISSING
podman / nerdctl       MISSING
containerd（服务）      未运行
nvidia-ctk             MISSING
nvidia-container-runtime MISSING
dpkg 中 docker/containerd/nvidia-container-* 包：无
```

`sudo -n true` 失败：**当前 `sudo` 需要交互密码**，fjd 虽在 `sudo` 组，但本会话
无法免密提权。

### 4.2 影响

任务书 §5.A 要求 Crawl4AI **官方 Docker、固定版本、独立 Compose**。没有 Docker
就不能按该形态部署。三条可选路径：

| 路径 | 前提 | 评价 |
|---|---|---|
| A. 装 Docker + NVIDIA Container Toolkit | 需一次 `sudo`（交互密码） | **推荐**。符合任务书；一次装好，后续 Phase 全部受益 |
| B. 不用 Docker，`uv` 装 `<crawl4ai>` 起独立进程 | 无需 sudo | 可行但偏离「官方 Docker/Compose」形态；健康检查与版本固定要自己做 |
| C. 暂缓 Crawl4AI，先做 MinerU | 无需 sudo | 会打乱执行顺序 |

**建议走 A**，并同时装 Compose 插件与 NVIDIA Container Toolkit（MinerU 之后若想
容器化也用得上）。MinerU 本身不强制 Docker，可用独立 `uv` 环境直接跑，因此
**Docker 不是 MinerU 的阻塞项，只是 Crawl4AI 的**。

### 4.3 CUDA / PyTorch

- 驱动：595.99.02，报告 CUDA 13.2；`/usr/local/cuda-13.1` 存在。
- `nvcc` 不在 PATH，仅影响需要编译 CUDA 扩展的场景，本阶段不依赖。
- 系统 Python 3.14.4 **没有 torch**；`python3 -m pip` 也不存在。
- `uv` 已缓存 Python 3.11.17 与 3.12.15 —— MinerU 用 3.12 建独立环境，避免
  Python 3.14 上 wheel 覆盖不全的风险。

---

## 5. 运行中的服务与端口

只列可归属的监听端口；无权限枚举其余进程属主。**不写具体内网 IP/主机名**。

| 端口 | 绑定 | 归属 | 判断 |
|---|---|---|---|
| 22 | 0.0.0.0 | sshd | 系统 |
| 631 | loopback | CUPS | 打印 |
| 1055 | loopback | tailscaled | 组网 |
| 3000 | loopback | 未识别（HTTP 301） | 非本项目 |
| 3230 / 3231 | loopback | node `cloudcli`、ssh 隧道 | ServerOS 链路 |
| 8081 | loopback + 内网 | `caddy` | 反代 |
| 8082 | loopback | selkies python | 远程桌面 |
| 8084 / 8085 | loopback | 未识别（401） | 非本项目 |
| 8123 | loopback | node（pid 2953915） | 非本项目 |
| 8200 | loopback | 未识别（404） | 非本项目 |
| 8765 / 8788 | loopback | 未识别（200） | 非本项目 |
| 8790 / 8891 / 8895 / 8897 / 8901 / 8903 | loopback | `python3 -m http.server` | 长期存在的静态服务 |
| 9000 / 9090 / 9101 | loopback | 未识别 | 监控类，非本项目 |
| 9222 | loopback | Chrome 调试端口 | **已被占用，Crawl4AI 不得复用** |
| 16101 / 26601 等 | loopback | ssh 隧道 | ServerOS |
| 3389 | 全部 | gnome-remote-desktop | 远程桌面 |

**端口规划建议**（避开上述）：

- Crawl4AI Local：`127.0.0.1:11235`（官方默认）
- MinerU Local API：`127.0.0.1:18000`（自定义，避开 8000/8080 系）
- 两者均只绑 loopback，不对外暴露。

### 5.1 运行中的 AI / 平台服务

| 服务 | 形态 | 与本项目关系 |
|---|---|---|
| `serverosd` + fleet 连接器 | 用户进程 / systemd | ServerOS 控制面，勿动 |
| `cloudcli` | user service | 云端 CLI 常驻 |
| `llmlab-agent` / `backend.bff` | user service / 进程 | LLM Lab 观测面 |
| `fjd-llm-forward` | user service | 到网关的 loopback 转发 |
| `gnome-remote-desktop-headless` | user service | 远程桌面 |
| selkies（另一用户） | 进程 | 远程桌面，勿动 |
| `isaac` 实验脚本 | `/data/home/fjd/isaac` | **正在进行的他人/历史实验，勿动** |

**没有** ollama / vLLM / SGLang / LiteLLM 本地推理进程在跑；GPU 上无模型。
未发现现成的 SearXNG 实例（所有 HTTP 端口均非搜索服务）。

---

## 6. 工作区与仓库状态

```
/home/fjd/projects/marketops-local-runtime-lab
  .git                → origin ProfessorZhi/marketops
  benchmark/          → runner / datasets / schemas / local-runtime
  marketops/          → 生产包（本轮不修改）
  tests/              → 既有测试
  .venv/              → uv 建的本地环境（已 gitignore）
```

`/data/home/vince` 在本机是**空目录**（`drwxr-x--- vince vince`）。`SYNC_MANIFEST.json`
里 `missing_artifacts.source_directory = /data/home/vince/artifacts/.../recorded-pilot/`
指向的是**另一台 A6000**，本机无法取得。因此这 9 个文件的状态维持
`pending_fetch_from_original_machine`，**不假装已同步**。

---

## 7. 基线验收（与任务书一致）

| 检查 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 远端分支 SHA | `536b78b8…da68ff` | 同 | ✅ |
| 本地 HEAD | `536b78b8…da68ff` | 同 | ✅ |
| `validate --dataset benchmark/datasets/pilot` | checksum `28157ca3b4a9ac31` | `24 search, 28 fetch, 10 e2e (checksum 28157ca3b4a9ac31)` | ✅ |
| `tests/test_benchmark_local_runtime_manifest.py` | 9 passed | 9 passed | ✅ |
| `tests/test_benchmark_*.py` | 120 passed | 120 passed | ✅ |

环境口径：`uv` + Python 3.14.4 + 项目 `[dev]` extra（pytest 9.2.0、jsonschema 4.26.0）。
**没有**修改任何数据集或测试来消除差异；三项数字与任务书完全一致。

> 交接文档 §10.6 记的是「111 passed」，实测为 **120 passed**。差异来自该文档写作
> 时点的统计口径，不是本次改动造成 —— 本地未改测试文件。以实测为准，并记录在此。

---

## 8. 阻塞项与下一步

### 阻塞项

1. **Docker 安装需要一次交互式 `sudo`。** 这是 Crawl4AI 官方部署的硬前置。
2. **内存 30 GiB**，MinerU + Crawl4AI 并发必须显式限流。
3. **9222 端口已被 Chrome 占用**，不能复用。

### 不阻塞

- MinerU：`uv` + Python 3.12 独立环境即可，不需要 Docker、不需要 sudo。
- 数据集扩充、离线测试、报告撰写：完全可做。
- 外部 API 测试：网络已通，凭据需另行确认（**本轮不读取其他用户凭据**）。

### 建议顺序

1. 取得一次 `sudo` 授权 → 安装 Docker Engine + Compose 插件 + NVIDIA Container Toolkit；
2. 建 `/data/home/fjd/marketops-lab/` 作为 DATA_ROOT（权重、缓存、中间产物）；
3. 部署 MinerU（独立 `uv` 环境，`batch=1`，显存上限）；
4. 部署 Crawl4AI（Docker Compose，固定版本，loopback + 独立 Token）；
5. 两者各自过 Smoke Test，再进入数据集与 Benchmark。

---

## 9. 观测命令（可复现）

```bash
nvidia-smi
nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version --format=csv
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
lscpu; free -h; df -h; uptime
ss -lntp
dpkg -l | grep -Ei 'docker|containerd|nvidia-container'
command -v docker nvidia-ctk uv
uv python list
```
