---
name: marketops-doctor
description: Diagnose and repair this computer's MarketOps environment - the interpreter the MCP server actually runs on, the local page reader, the data root, and which provider keys are set. Use when setup looks fine but research does not start, when page reads fall through to paid routes, when the plugin was just installed or updated, or when moving to another computer.
---

# MarketOps Doctor：本机环境诊断与安全修复

先跑只读检查，再决定修什么。默认**不改任何东西**：

```bash
python scripts/research_doctor.py            # 人读
python scripts/research_doctor.py --json     # 机器读（agent 用这个）
```

退出码 `0` = 没有 blocker；`20` = 至少一项 BLOCKER 还在。
`scripts/` 指插件安装目录下的 `scripts/`（宿主里是 `${CLAUDE_PLUGIN_ROOT}/scripts`）。

## 它查的是哪几格

| check | 说什么 | 为什么单独列出来 |
|---|---|---|
| `interpreter.exists` | `MARKETOPS_RESEARCH_PYTHON` 指向的解释器在不在 | 服务跑在**那个**解释器里，不是跑 doctor 的这个 |
| `interpreter.mcp` | 那个解释器能不能 `import mcp` | 没有它，研究服务根本起不来（BLOCKER） |
| `interpreter.crawl4ai` / `.playwright` | 免费本地读页的两层在不在 | 缺了不致命，但读页会降级到付费路，甚至停在 HOLD |
| `keys.present` / `keys.absent` | 哪些 provider 的 Key 设了 | **只报名字，不打印值**。缺 Key 是警告，不是停机 |
| `config.data_root` | 数据根配了没、存不存在、可写不可写 | 没有它，一次运行没有地方写数据（BLOCKER） |
| `crawl4ai.state` | 本机缓存里那份 crawl4ai 检查结论 | 缓存可能是**另一个**解释器写下的，这一格会点出来 |

最后一条最容易骗人：检查结果存在数据根里，谁跑过就写谁的；换成另一个 venv 之后
"WORKING" 仍然在，而服务用的那个解释器里根本没有 crawl4ai。所以 doctor 会把它标成
"缓存里的结论来自另一个解释器"，而不是照抄 WORKING。

## 怎么修

```bash
python scripts/research_doctor.py --repair              # 只做安全的修复
python scripts/research_doctor.py --repair --install    # 顺带 pip 安装缺的包
```

`--repair` 做三件事，且只做这三件：

1. 建配置里已经写明的数据根（不替你编路径）；
2. 让 crawl4ai 的缓存/浏览器指针回到可用状态；
3. 缺包时**打印**该跑的命令。

加 `--install` 才会真的安装，并且装进**目标解释器**而不是 doctor 自己那个。
装包是一次外发与供应链动作，所以默认不做，必须显式同意。

用哪条安装命令由目标解释器决定：uv 建的 venv **没有 pip**，这时报的是
`uv pip install --python <那个解释器>`；两者都没有时，doctor 不承诺修复，只说明先换解释器。
装完 `playwright` 不等于浏览器就位——`playwright install chromium` 是另一次下载，doctor 会单独念出来。

`--repair` 不会：删文件、覆盖用户选的配置值、读写任何 Key 的值、改动凭据库。

## 症状对照

| 症状 | 先看这一格 | 处置 |
|---|---|---|
| 工具列表里没有 MarketOps | `interpreter.mcp` | 换解释器或补装 `mcp`，重启宿主 |
| 搜索能用，读页总是 HOLD | `interpreter.crawl4ai` + `crawl4ai.state` | 换到带 crawl4ai 的解释器，或 `--repair --install` |
| `marketops_setup_status` 说 `CONFIG_REQUIRED` | `config.data_root` | 走 `marketops-setup`，让用户给两个真实路径 |
| 某一路"配了却还是 unavailable" | `keys.absent` | Key 名对不对、是不是在启动宿主**之前**设的 |
| 换电脑后一切照旧坏 | 全部 | 连接器与解释器路径都是机器绑定的，在新机器上重跑安装与 doctor |

## 边界

- 诊断输出里**永远不出现 Key 的值**——只出现变量名与"设了没"。
- 不替用户决定数据目录；那是 `marketops-setup` 的两个问题。
- 不自动安装、不自动升级：装包要 `--install`，升级走 `marketops_update_check`。
- 修完要**重跑一次** `--json` 并把前后两份都念给用户：修了什么、还剩什么，不合并成一句"已修好"。
