# Research Core 本地测试环境说明

本文说明 Research Core Local Benchmark Lab 跑在什么环境上、需要什么、不需要什么，
以及哪些边界是有意设成"不越线"的。所有数字均为本机实测，不是转述。

- 用户：`fjd`，`HOME=/home/fjd`
- 仓库：`/home/fjd/projects/marketops-local-runtime-lab`
- 分支：`feat/a6000-local-runtime-lab`
- 时间基准：2026-11-19（+08:00 本机时区）

---

## 1. 运行时依赖

| 项 | 实测 |
|---|---|
| 解释器 | `.venv/bin/python` 3.14.4 |
| 关键库 | `jsonschema`（Draft 2020-12）、`httpx`、`pypdf`、`pytest` |
| GPU | 不需要。全部 9 个阶段是 CPU 上的规则/正则参考实现 |
| 模型权重 | 不下载、不加载 |
| API Key | **不需要**，实验室里没有任何真实 Provider |

`pyproject.toml` 已设 `pythonpath = ["."]`、`testpaths = ["tests"]`，新增测试放进
`tests/` 即可，不需要安装成包。

## 2. 端口

| 端口 | 用途 | 归属 |
|---|---|---|
| `127.0.0.1:18080` | local-runtime 既有 fixture server | **不动**，与本次实验室无关 |
| `127.0.0.1:18090` | Synthetic Benchmark 的 fixture HTTP 服务 | 本实验室，按需自动拉起 |
| `127.0.0.1:18091` | Public Evidence 冻结快照的只读 HTTP 服务 | 本实验室，按需自动拉起 |

两个端口都只绑定 loopback。18090/18091 在适配器构造时按需启动：先探测端口，已经有人
在监听就直接复用，不会重复 bind。18080 从不被探测以外的任何方式触碰。

## 3. 网络边界

| 动作 | 是否需要网络 |
|---|---|
| `validate` / `list` / `contract` / `run` / `suite` / `report` | 否 |
| `pytest tests/test_research_*.py` | 否（只读磁盘 + 本地 18090/18091） |
| `make research-test-offline` | 否（显式清空代理环境变量后再跑一遍） |
| `make research-public-capture` | **是**，且是唯一会联网的目标 |

`research-public-capture` 会重新冻结 Public Evidence 快照。它遵守 `robots.txt`
（`urllib.robotparser`），每主机 ≥2 秒一次请求，UA 带联系方式，401/403/429 一律记为
缺口而不绕过。已经冻结的 URL 与已记录的缺口在重跑时都会跳过——重复请求一个没变化的
站不是礼貌，是负载。

## 4. 与现有服务的关系

| 服务 | 状态 | 本实验室的动作 |
|---|---|---|
| MinerU 4.2.3 (`:18000`) | 运行中 | 不启动、不停止、不修改 |
| Crawl4AI Docker (`:11235`) | 运行中 healthy | 不启动、不停止、不修改 |
| SearXNG (`:8888`) | 运行中 | 不启动、不停止、不修改 |
| local-runtime fixture (`:18080`) | 运行中 | 不修改 |

本实验室**不接入**上述任何服务：它测的是 Research Core 各阶段的契约与指标，不测
MinerU/Crawl4AI 的解析质量（那属于 local-runtime 主线）。

## 5. 一条命令能做什么

```bash
make research-dataset-validate   # 两个数据集全部 schema 校验
make research-test              # 数据集契约 + 适配器契约 + 参考跑分（70 条测试）
make research-test-offline      # 同上，显式断网
make research-benchmark         # 9 阶段 × 3 次，断言 gold 指标不动
make research-contract          # 7 Provider × 10 失败模式 = 70 项契约
make research-public-benchmark  # Public Evidence 的 fetch / evidence 两阶段
make research-report            # 由 manifest 渲染 Markdown 报告
make research-fixture-server    # 手工起 18090 / 18091
```

## 6. 明确的禁区

- 不接入真实付费 API，不要求任何 API Key，不产生外部费用。
- 不修改 MarketOps 生产插件、生产 Provider Routing、生产部署。
- 不触碰 `vince` / `zhengji` 的 HOME、凭据或会话；不进入 `/data/home/vince`。
- 不抓取 LinkedIn / Instagram，不绕过登录、付费墙、验证码或反爬。
- 不发送邮件，不接入 Odoo，不进行 SMTP 探测。
- 不停止已在运行的 A6000 模型或服务，不修改 NVIDIA 驱动 / CUDA 全局环境。
- 不把 `not_configured` 当空结果，不把失败请求当成功，不把 Mock 成绩混进真实 Provider。
