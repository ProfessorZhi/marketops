# MarketOps Research Plugin — 客户端安装与 Skills 交付状态（W-G）

**日期：** 2026-10-17（北京时间）
**范围：** W-G CLIENTS/SKILLS —— 安装面、宿主清单、工具 schema 兼容、Skills 打包、
依赖锁定、离线安装冒烟。**不覆盖**五家 provider adapter 与账本（W-A…W-F）。
**基线：** `b2bcdcd24862a923f7b32eff75bfcc2756ae767b`（PR #73 HEAD）
**标签阶梯：** 引 `docs/wave1-coordination/INTERFACE-CONTRACT.md` §1。
本 Wave 无 `DECISION-04` 授权，**最高只能到 `OFFLINE_TESTED`**；本文件不上调任何标签。

> **本文件不是验收通过声明。** 它是一张"哪一档、凭什么"的表。凡没有可复现证据的行，
> 一律写 `BLOCKED` 并指名缺什么。

---

## 1. 宿主安装面

| 能力 | 标签 | 证据 | 未完成的部分 |
|---|---|---|---|
| Claude Code 加载插件、登记唯一 MCP | `OFFLINE_TESTED`（宿主侧已实测，见 §10） | `claude --plugin-dir <plugin> plugin details` → `MCP servers (1) marketops-research`，Skills (17) | 见 §10：宿主 `tools/list` 与一次 T0 调用已实测；ladder 要求的 **T1** 只读调用在本轮授权下不可达 |
| 宿主清单齐备（Claude / Codex / WorkBuddy） | `OFFLINE_TESTED` | `client_install_probe.audit_plugin_tree()` → `manifests = {claude-code: true, codex: true, workbuddy: true}`，两份 MCP 配置都只声明 `marketops-research` | 宿主清单的**真实加载**只在 Claude Code 上观察过 |
| Codex 安装路径 | `BLOCKED` | 生成器产出 `marketops-local` marketplace JSON + `mcp.json`（`command=venv python`，无 `sh`）；CLI 注册需显式 `--register-codex` | **无 Codex 设备授权**，未在真实 Codex 上导入 |
| WorkBuddy 安装路径 | `BLOCKED` | `build_client_bundles.py` 产出 connector 目录 + 确定性 ZIP（固定时间戳/顺序），含 1 个 MCP | **无 WorkBuddy 设备授权**，未导入 |
| Windows | `BLOCKED` | `client_install_probe --artifact installed` 断言产物中无 `sh`/`.sh`/非 stdio/cwd 钉死；本机 Windows 形状由安装器产物保证 | **无 Windows 设备授权**；`device_result = NOT_TESTED_NO_WINDOWS_DEVICE_AUTHORIZED`。这是显式 BLOCKED，不是通过 |

### 关于 `.claude-plugin/plugin.json` 的一条负读数

本 lane 新增了 `.claude-plugin/plugin.json`（此前缺失）。**实测结论与预期不符，如实记录：**

- 移走 `.claude-plugin/` 后再跑 `claude plugin details`，**仍然**报 `MCP servers (1) marketops-research`，
  只是描述退化为 `Plugin from marketops-research-assistant@inline`。
- 读过的路径：`claude --help` 的 `plugin`/`mcp` 子命令面、`claude plugin details` 的
  Component inventory 段（有无 `.claude-plugin/` 各一次）。
- **结论：** 在 Claude Code 2.1.272 上，使 MCP 出现的是插件根目录的 `.mcp.json`（本插件原本就有），
  **不是** `.claude-plugin/plugin.json`。新增清单的价值是身份/版本完整性（`preflight.py`
  现在强制三份清单版本一致），**不得**被写成"它修好了 MCP 消失"。

---

## 2. 工具面与 schema 兼容（IF-1 / IF-4）

| 能力 | 标签 | 证据 | 未完成的部分 |
|---|---|---|---|
| 唯一 MCP、无厂商直连工具（IF-1） | `OFFLINE_TESTED` | 真实 stdio `initialize`+`tools/list`：**23 个工具全部 `marketops_*`**，`vendor_named_tools = []`；工具名集合与原始 server 完全一致 | 宿主内 `tools/list` 未取 |
| 工具描述不含未审核上游文本（IF-2） | `OFFLINE_TESTED` | 既有 `test_research_mcp_tool_refusal.py` 等覆盖；本次未新增断言 | 无独立 QA 复核 |
| 动态入口不绕过政策（IF-3） | 本 lane 不覆盖 | 该工具在 `research_mcp.py`（本 Wave 冻结只读，归 INTEGRATOR / W-B） | — |
| 只用宿主兼容 schema 子集（IF-4） | `OFFLINE_TESTED` | 见下 | 未在 Codex/WorkBuddy 上验证 |

### IF-4 的实测缺陷与修法

原始 server 的 `tools/list` 里有 **6 处 `anyOf: [X, {"type":"null"}]`**（可空可选参数的派生物）：

```text
marketops_capabilities / host_observed_tools, task_kind, tinyfish_wallet_snapshot
marketops_start        / brief
marketops_propose      / artifact_ids
marketops_material_excerpt / queries
```

`research_mcp.py` 本 Wave 冻结，**未改**。新增宿主侧门面
`scripts/research_mcp_facade.py`：同一 server、同一 23 个工具，只把可空 `anyOf` 摊平成
普通 `type`/`items`。方向恒为「**收窄对外声明，不放宽实际接受**」——server 自身校验仍收
显式 `null`，因此不存在"以前能调、现在被拒"的调用。遇到无法摊平的组合子
（`oneOf`/`$ref`/三段 `anyOf`/兄弟键冲突）**fail-closed 抛异常**，不静默透传。

### 入口裁决（2026-10-17，独立 QA 闸门后）：入口统一到 `research_mcp.py`

三个宿主清单、源码两份 MCP 配置、安装器产物与连接器生成器**统一指向唯一的受管入口
`scripts/research_mcp.py`**。理由是独立 QA 闸门
（`tests/qa_gate/test_qa_packaging_clients.py`）把"默认受管产品必须暴露**唯一**一个
MarketOps MCP，且生成的每份宿主配置启动的是该受管入口"判在被启动的入口文件名上，
而门面 `research_mcp_facade.py` 是另一个模块。门面**仍随包发布**（`client_install_probe.py`
用它的 `audit_schema()` 做兼容性审计，`--check` 仍可单独运行），但**不再出现在任何客户端
启动参数里**。

**2026-10-17 第二轮更正：上面那段"IF-4 在发行路径上未满足"的结论已被实测推翻。**

`research_mcp.py` 的属主随后给它加了 `host_compatible_server()`：该函数 `import
research_mcp_facade` 并返回 `research_mcp_facade.build_host_compatible_server()`，
`__main__` 走的就是它。于是"被启动的入口文件名是 `research_mcp.py`"与"播发的是宿主兼容
schema"两件事**同时成立**，QA 判据与 IF-4 不再冲突。

实测（真实宿主会话，`tools/list` 的原始帧）：宿主收到的 **23 份 input schema 中
`anyOf` 计数 = 0**。所以 **IF-4 在发行路径上已满足**（离线 + 宿主线级双重证据）；
上一轮那句"未满足"是**当时**的事实，现在不成立，予以更正而不是保留。

---

## 3. Skills 与 provider 参考

| 能力 | 标签 | 证据 | 未完成的部分 |
|---|---|---|---|
| 17 个 Skill 结构完整 | `OFFLINE_TESTED` | `audit_skills()` → `ok=true, skill_count=17`；每个 `SKILL.md` 有 `name`+`description` frontmatter；无凭据字面量 | 各 Skill 的**业务正确性**不在本 lane |
| 按需 provider 参考（不进 warm context） | `OFFLINE_TESTED` | 五家参考文档在 `references/providers/{tinyfish,serpapi,serper,crawl4ai,jev}.md`；Claude Code 实测 always-on ≈794 tok，provider 细节只在 on-invoke 时读 | 未测更长会话下的实际 token |
| Skills 数量收敛（proposal 主张 5 个主 Skill） | **未做** | — | 收敛是产品决策，越 lane；本 Wave 不裁 Skill，只保证结构正确 |

---

## 4. 凭据与安全变量

| 能力 | 标签 | 证据 | 未完成的部分 |
|---|---|---|---|
| 配置只声明经审的变量名 | `OFFLINE_TESTED` | `SAFE_ENV_PASSTHROUGH` 白名单 16 个名字；`UNAPPROVED_ENV_NAME` 负向用例可复现 | 宿主是否真的只透传这些名字，未在宿主上验证 |
| 配置/清单不含凭据字面量 | `OFFLINE_TESTED` | `SECRET_PATTERNS`（`sk-…`、`Bearer …`、JWT 形状）扫全部清单与 Skills，负向用例可复现 | 仅覆盖本 lane 的文件 |
| 无真实厂商登录 | 卫生条件 | 本 lane 未做任何认证；探针在启动前**移除**全部 provider key 变量并把三个联网开关钉为 `0` | — |

---

## 5. 直连旁路（P1-02 的核心判据）

| 能力 | 标签 | 证据 | 未完成的部分 |
|---|---|---|---|
| 发行包只有一个 MCP，无启用的厂商直连 | `OFFLINE_TESTED` | 安装产物中不存在 `codex/`、`workbuddy/` 目录；`PACKAGED_VENDOR_MCP` 负向用例可复现 | 真实生成的**客户 ZIP** 未在客户端导入核对 |
| 三套厂商直连配置**不在默认安装面** | `OFFLINE_TESTED` | `setup_clients.py` 只 `copytree(SOURCE)`；`build_client_bundles.py` 只从本插件目录取文件；安装产物断言无这两条路径 | 同上 |
| 三套示例保留但标为开发者专用 | 已完成（本 lane 内） | 四个目录（含 `examples/codex/`）各含 `NOT-FOR-MANAGED-DISTRIBUTION.md`，正文写明 **not for managed distribution / 不为受管分发** | 标注只是文字声明，不构成技术隔离 |
| 直连示例用**正向显式白名单** | 已完成（本 lane 内） | 四份示例配置（codex starter `.mcp.json`、两个 WorkBuddy `mcp.json`、`examples/codex/config.toml.example`）都改用 `enabledTools` / `enabled_tools` 列出允许的上游工具；拒绝名单已移除 | `enabledTools`/`enabled_tools` 的真实过滤效果**未在真实客户端验收**；允许的工具名取自本仓文档（`search`/`fetch_content`、`scrape`/`extract`），未与上游 `tools/list` 逐项核对 —— 名字不符时应**拒绝**而非放行（fail-closed） |

---

## 6. 可复现性

| 能力 | 标签 | 证据 | 未完成的部分 |
|---|---|---|---|
| 依赖 hash 锁定 | `OFFLINE_TESTED` | `requirements-lock-lite.txt`（39 包，492 条 `--hash=sha256:`）、`requirements-lock-crawl-local.txt`（~130 包，3030 条 hash）；`uv pip compile --generate-hashes` 生成，头注释记录命令、平台、解释器 | 只有 linux/x86_64 + CPython 3.12 一次解析；**Windows/macOS 需按同一命令重解析** |
| 安装走锁定文件 | `OFFLINE_TESTED` | `setup_clients.py` 存在锁定时用 `pip install --require-hashes -r <lock>`，回执记录 `dependency_sources[].hash_pinned` | `--install-deps` 路径需联网，**未在本轮实跑**（离线授权） |
| 版本号 | `DOCUMENTED`（未动） | 三份清单 + 两个 Skill 仍写 `0.4.0` | 本次交付改变了发行内容却沿用 `0.4.0`。**是否升版本号由 INTEGRATOR/owner 决定**，本 lane 不自行改号以免与其他 worker 分支冲突 |

---

## 7. 负读数（写明搜过什么，避免"没找到"被读成"不存在"）

1. **宿主侧 `tools/list` 与一次只读调用：第二轮已做**（见 §10；用的是独立 HOME、按名
   `unset` 全部 provider 变量、联网开关全 0 的隔离环境，全程不读不打印任何凭据）。
   **仍然没做的是 ladder 要的 T1 那一档**——T1 需要本地浏览器或一次真实网络检索，
   本轮授权下都不可达；替代它的是 T0 `marketops_setup_status`。
   **仍然主动不做**的是 `claude mcp get`：它会回显 server 配置，在带真实变量的环境里
   等于把活凭据打进记录。本轮改用**自有隔离环境**规避，而不是放弃这条读数。
2. **Codex / WorkBuddy 客户端导入：未做。** 无设备授权。读到的是生成器的产物结构，
   不是客户端行为。
3. **Windows：未做。** 无设备授权。给出的是形状检查 + 负向用例，不是平台测试结果。
4. **`plugins/research-tools/` 之外**：`marketops-plugin/`（仓库根的业务插件）不在本 lane
   所有权内，**未改动**。
5. 读到但未改的冻结文件：`research_mcp.py`、`dual_search_mcp.py`、
   `research_provider_extensions.py`、`research_official_mcp.py`、`research_provider_policy.py`。

---

## 8. 明确不许上调的声明

- 五家 provider：本 Wave 内**没有任何一项**达到 `LIVE_VERIFIED` 或 `CLIENT_VERIFIED`。
- 本 lane 的每一项最高 `OFFLINE_TESTED`；任何 `CLIENT_VERIFIED` 需要**至少一个真实宿主的
  安装 + `initialize` + `tools/list` 一致 + 一次 T1 只读调用**，并附该 build 的日志。
- `BLOCKED` 不等于通过。缺的是**设备授权**（Codex/WorkBuddy/Windows）与**一次 T1 只读调用
  的授权**（本地浏览器下载，或一次真实网络检索）。宿主内取 `tools/list` 的通道本轮已打通
  （隔离 HOME + 按名 `unset` 凭据），不再列为缺口。

---

## 9. 复现命令

```bash
# 真实 Claude Code 宿主端到端（隔离 HOME；--session 才花一次模型轮次）
#   不加 --python 会看到"解释器缺 mcp"的静默失败形状；加上它才分离出插件本身的问题
sh scripts/cc_host_probe.sh --plugin-dir "$PWD" --python <有 mcp 的解释器> --session

# 全量离线套件（本文件写就时的基线：候选 tip 上 626 用例 OK / skipped=2）
python -m unittest discover -s tests

# 静态预检（含三份清单版本一致性、入口统一到受管入口 research_mcp.py）
python scripts/preflight.py

# 安装冒烟：源码树
python scripts/client_install_probe.py --plugin-root . --python <有 mcp 的解释器>
# 安装冒烟：安装产物（Windows 契约在此判）
python scripts/client_install_probe.py --plugin-root <安装目录>/plugins/marketops-research-assistant \
       --artifact installed
# schema 兼容自查
python scripts/research_mcp_facade.py --check

# Claude Code 宿主侧登记（只读，不含凭据）
claude --plugin-dir <plugin 绝对路径> plugin details marketops-research-assistant
```

---

## 10. 真实 Claude Code 宿主端到端（2026-10-17 第二轮实测）

**被测件：** 集成候选 `integration/wave1-candidate-20261017` 的插件树（本轮把 W-G 分支先
fast-forward 到候选 tip，再在其上加修复）。**宿主：** Claude Code 2.1.272 / Linux /
`device:tokyo-shared-dev-01`。**隔离：** 独立 tmp HOME 与 `XDG_*`/`TMPDIR`，按名 `unset`
全部 provider 凭据变量（脚本从不读取其值），三个联网开关与付费开关全部钉 0；未触碰任何
客户账户、设备、系统权限或正式客户端设置。

复现入口是本轮新增的 `scripts/cc_host_probe.sh`（`--session` 才花一次模型轮次）。

### 10.1 宿主实际在线上做了什么（本次实测，非推断）

| 观测 | 值 |
|---|---|
| 会话 init 事件里的 MCP 状态 | `{"name":"plugin:marketops-research-assistant:marketops-research","status":"connected"}` |
| 模型可见的受管工具数 | **23**，全部形如 `mcp__plugin_marketops-research-assistant_marketops-research__marketops_*` |
| 宿主→服务端方法序列 | `initialize` → `notifications/initialized` → `tools/list` → `prompts/list` → `resources/list` → **`tools/call`** |
| `initialize` 里宿主自报 | `clientInfo.name=claude-code`、`version=2.1.272`、协议 `2025-11-25` |
| 服务端 `serverInfo` | `MarketOps Lite Research` `0.4.0` |
| 一次 T0 调用 | `tools/call marketops_setup_status {}` → 成功，回执 `status=CONFIG_REQUIRED`、`mutates_nothing=true`、`network_or_provider_fee_incurred=false` |
| 该次调用在 wire 上的参数 | `{"name":"marketops_setup_status","arguments":{},"_meta":{"claudecode/toolUseId":"call_00_ET_…","progressToken":4}}` |
| 宿主收到的 23 份 input schema | **`anyOf` 计数 = 0**（`research_mcp.py` 现在经 `host_compatible_server()` 走门面归一化） |

### 10.2 本轮发现并修复的宿主级缺陷：`default_tools_approval_mode`

> **2026-10-17 第二轮更正（见 §11）：这一段原先写的"服务器根本不启动"在 Wave-2 的
> 基线上**复现不出来**，机理被推翻。保留原文以便对照，**结论以 §11 为准**。**

**症状（当时观测）：** 真实会话把受管 MCP 标为 `status: "failed"`，并且**一次都没有执行过
launcher**（用一个会记账的"解释器"证实：会话期间零调用）。模型侧可用工具 0 个。**而
`claude mcp list` 对同一份配置报 `✔ Connected`**，`claude plugin details` 也照常显示
`MCP servers (1)`。也就是说：两处绿灯，实际不可用。

**A/B（同一隔离环境、同一 commit、只差一个字段）：**

| 配置 | 会话 init 的 MCP 状态 | launcher 是否被 exec |
|---|---|---|
| `.mcp.json` 含 `default_tools_approval_mode`（原样） | `failed`（**未复现**） | 否（**未复现**） |
| 从 `.mcp.json` 去掉该字段，`mcp.json` 保留 | `connected` | 是（`23` 个工具） |

**修法（仍然成立，与机理无关）：** 从 `.mcp.json` 删除。**通用 `mcp.json` 保留**——它指向的
宿主本机无设备可测，删它同样没有依据；探针把该字段报成 `FIELD_ON_UNTESTED_HOST` 注记而
**不是**静默接受。

**回归护栏：** `client_install_probe.py` 新增 `CLAUDE_PLUGIN_SERVER_FIELDS` 白名单，
`.mcp.json` 出现名单外字段即 `HOST_FOREIGN_FIELD`（fatal）；生成器写的 config 里出现
名单外字段即 `UNEXPECTED_SERVER_FIELD`；`mcp.json` 的同类字段进 `notes`。
测试 `tests/test_wave1_client_install_smoke.py` 有正向 + 负向各一条。

### 10.3 另一条独立读数：源码头在隔离 HOME 下**默认起不来**

不设 `MARKETOPS_RESEARCH_PYTHON` 时，`launch_research_mcp.sh` 依次探测
`../.venv`、`python3`、`python`；隔离 HOME 里三者都不带 `mcp`，于是 launcher 以短码
`MARKETOPS_MCP_INTERPRETER_UNAVAILABLE` 退出 78（这是**设计内的显式拒绝**）——
但宿主只显示 **`-32000: MCP error -32000: Connection closed`**，那句短码不出现在宿主输出里。

这不是本插件的缺陷，而是"源码树按设计是 POSIX-only、需要一个带 `mcp` 的解释器"的
已知条件；安装器生成的配置绑绝对 venv 解释器，因此安装产物不受影响。**保留为 §5 负读数
之外的一条操作面提示**：宿主上的这类失败看起来像网络/协议问题，其实是解释器缺依赖。

### 10.4 本轮 `CLIENT_VERIFIED` 的准确程度

契约 §1 的 `CLIENT_VERIFIED` 要求四件：安装 + `initialize` + `tools/list` 一致 +
**一次 T1 只读调用**，并附该 build 的日志。

| 要件 | 本轮 |
|---|---|
| 真实宿主安装（`--plugin-dir` 加载，非 mock） | ✅ 实测 |
| `initialize` | ✅ 线上抓取 |
| `tools/list` 一致（23） | ✅ 实测 |
| 一次 **T1** 只读调用 | ❌ **不可达**：T1 需要本地浏览器（未授权下载 Chromium）或一次真实网络检索（本轮禁止联网/用 Key）。做的是 **T0** `marketops_setup_status` |
| 该 build 的日志 | ✅ `out/session.jsonl`（`stream-json`）+ `out/health.txt` |

**判定：`CLIENT_VERIFIED` 不上调，仍记 `BLOCKED`——缺的是 T1 那一步，不是证据链。**
已完成的部分比上一轮的"只有元数据层"强得多，但阶梯不跳档。

### 10.5 版本戳 / 锁定 / 浏览器依赖（本轮复核）

| 项 | 实测 |
|---|---|
| 版本戳 | 三份清单 `0.4.0`、`setup_clients.VERSION=0.4.0`、`MCPServer(version="0.4.0")`、`skill_version="0.4.0"`、宿主 `serverInfo 0.4.0` ——**六处一致**。同一版本号对应多个内容不同的提交仍是未决项（见 CR-G-4） |
| 依赖锁定 | `requirements-lock-lite.txt` 627 行 / 492 条 `--hash=sha256:`；crawl 3314 行 / 3030 条；**两者绝对路径计数 = 0**（有回归用例） |
| 浏览器依赖隔离 | 安装器在 `--install-crawl-browser` 时把 `PLAYWRIGHT_BROWSERS_PATH` 钉到**安装目录内**的 `browsers/` 并注入 server `env`；未显式设置时落到平台默认缓存（`$HOME/.cache/ms-playwright`），运行期以 `source: platform_default` 如实报出。**Chromium 未下载、未启动、未验证**（本轮禁止下载） |

### 10.6 仍然 BLOCKED

Codex、WorkBuddy、Windows 三项与 §4 一致：**缺设备授权**，本轮未做，也未冒称。

---

## 11. Wave-2：那条宿主级结论被自己的 A/B 推翻（2026-10-17 第二轮）

**基线：** `cf8ff6e36931b81bdd7d63df5c190ecae8593a6c`（PR #78 的 code tip）+ 本分支修复。
**宿主：** Claude Code 2.1.272 / Linux，仍走 `scripts/cc_host_probe.sh` 的隔离环境
（独立 HOME/XDG/TMPDIR、provider 变量按名 unset、联网与付费开关全 0）。

### 11.1 复现出来的 A/B（同树、同脚本、同参数，只差那一个字段）

| `.mcp.json` | 会话 init 的 `mcp_servers` | 模型可见受管工具 | 那次 `tools/call` |
|---|---|---|---|
| 含 `default_tools_approval_mode: "prompt"` | **`connected`** | **23** | **被拒**：`Claude requested permissions to use … but you haven't granted it yet` |
| 去掉该字段 | `connected` | 23 | **执行成功**，回执 `CONFIG_REQUIRED` / `mutates_nothing=true` |

### 11.2 更正

- **§10.2 的机理是错的。** 该字段**不会**让服务器"根本不启动"——在这个基线上服务器照常
  启动、照常 `connected`、照常把 23 个工具交给模型。上一轮观测到的 `status:"failed"` +
  launcher 零调用**本轮没有复现，我无法解释它**，因此不再把它当作该字段的后果。
- **该字段真正可复现的后果是审批**：它让每一次工具调用都需要人工批准，**而且
  `--allowedTools <确切工具名>` 压不过它**。这在交互会话里也许正是意图，但在无人值守的
  `-p` 运行里，受管 MCP 变成"连得上、一个工具也调不动"。
- **判据不变**：它仍然不是 Claude Code 插件 `.mcp.json` 的字段（`claude mcp add --help`
  旗面里没有对应项），**从 `.mcp.json` 删除仍然是对的**——删它恢复的是宿主自己的审批控制，
  而不是"修好了一个 500"。

### 11.3 教训（写给下一次）

一个"两处绿灯、实际不可用"的观测很容易被归因给当时唯一被改动的那一行。**单一 A/B 只能
证明"这一行有影响"，不能证明"影响是这个机理"**；机理要在**另一个基线上重跑**才算数。
本轮做的就是这次重跑，它把机理换掉了、把判据留下了。

### 11.4 本轮 Codex / WorkBuddy 的离线包校验

无设备授权，因此**只做产物形状**，不声称宿主行为。新增
`client_install_probe.py::audit_client_packages(dest)`，对一次 `--client all` 安装的产物
分别判三家：

| 宿主 | 判什么 |
|---|---|
| Claude Code | 复用 `audit_plugin_tree`（清单齐备、恰好一个受管 MCP、入口可达、`.mcp.json` 字段面） |
| Codex | `.agents/plugins/marketplace.json` 形状；`source.path` **必须解析在安装目录内**；其指向的插件带 `.codex-plugin/plugin.json` 且 `mcpServers` 指到一个存在的配置 |
| WorkBuddy | 连接器目录恰好一个；`connector-meta.json`/`icon.svg`/`mcp.json` 齐备；`type=="mcp"`；入口脚本随包；`token-schema.json` 的字段名 ⊆ 已审变量表且无凭据字面量 |

**修 validator 时发现的一个真 bug（值得单独记）：** Codex 的 `source.path` 逃逸检查原先写的是
`declared.lstrip("./")`。`lstrip` 是**按字符集**剥离，会把 `../../elsewhere` 变成
`elsewhere`，于是这条检查会**放行它本该拒绝的那个输入**。改成显式剥前缀后，负向对照才红。
`tests/test_wave1_client_packages.py` 的 6 条负向用例各自钉住一种失败。

---

## 12. Wave-3：真实宿主冒烟收据 + OS 中立启动器计划

**被测件：** 本分支 HEAD（`plugin_sha` 见下），工作树干净、未改集成分支。集成侧只读核对。
**宿主：** Claude Code 2.1.272 / Linux / `device:tokyo-shared-dev-01`。
**隔离：** 独立 tmp `HOME`/`XDG_*`/`TMPDIR`；provider 变量**按名 `unset` 且从不读取其值**；
联网与付费开关全 0；未动客户账户/设备/系统权限/正式客户端设置；**无任何厂商调用**。

### 12.1 冒烟收据（来自 `scripts/cc_host_probe.sh`，非 import Python）

| 步骤 | 退出码 | 观测 |
|---|---|---|
| `claude --plugin-dir <p> plugin details` | `0` | `Skills (17)`、`MCP servers (1) marketops-research` |
| `claude --plugin-dir <p> mcp list` | `0` | `✔ Connected` |
| `claude -p … --output-format stream-json --allowedTools <tool>` | `0` | `mcp_servers: [{… "status": "connected"}]` |
| 模型侧可见受管工具 | — | **23** |
| 宿主发出的 **T0** 调用 | — | `marketops_setup_status {}` → `status=CONFIG_REQUIRED`、`mutates_nothing=true`、`network_or_provider_fee_incurred=false` |
| 宿主发出的**被拒**调用 | — | `marketops_search {provider:serper, run_id:"absent-run", …}` → **`status: BLOCKED`**、`reason: "Unknown Run"`（断网 + 无有效 Run 下的 fail-closed） |

**收据里没有凭据**：对落盘的 `session.jsonl` 跑 `sk-…` / `Bearer …` / JWT 形状三种正则扫描，
**0 命中**。这也是"按名 unset 而不读值"这个设计的直接后果。

### 12.2 `.mcp.json` 与 `mcp.json` 的环境变量一致性（实测）

| | `.mcp.json`（Claude） | `mcp.json`（通用） |
|---|---|---|
| `command` | `sh`（POSIX 启动器） | `python` |
| `args` 个数 | 2（启动器 + 受管入口） | 1（受管入口） |
| `env_vars` | 16 个 | 15 个 |
| 差集 | **仅** `MARKETOPS_RESEARCH_PYTHON` | 无 |

差异**恰好等于**启动路径的差异：POSIX 启动器需要一个解释器覆盖来指定"哪个 Python 有 mcp"，
而通用配置直接把解释器写在 `command` 里。有专门用例钉住"多一个变量就红"。

### 12.3 OS 中立启动器与 Windows 计划

**先说结论：不存在一个能同时启动这个 server 的 `command` 字符串。** 一份宿主配置只能写一个
`command`；`sh` 在 Windows 上不存在，`python` 在 Linux 上不保证存在。所以"OS 中立"不能靠换一个
更聪明的命令串实现，只能靠**让安装器在安装时解析出绝对解释器路径**（它本来就是这么做的）。

本轮补的是**源码树/手工配置**那条路：

- 新增 `scripts/launch_research_mcp.py`：与 `.sh` 同样的探测顺序（`MARKETOPS_RESEARCH_PYTHON`
  （必须绝对）→ 插件内 venv（**POSIX `bin/` 与 Windows `Scripts/` 两种布局都探**）→
  `python3` / `python` / Windows 的 `py -3`），纯 stdlib，无下载、无凭据读取。
  若**当前解释器本身**就能 `import mcp`，就地 `runpy` 跑 server，不再另找解释器。
  `--list` 列出全部候选及各自是否可用，`--check` 打印会被选中的那个。
- 新增 `client_install_probe.audit_launcher_portability()`：把"哪份配置在哪族系统上能用"报成**数据**
  （`configs_needing_a_shell`、`os_neutral_launcher`、`windows_plan`），而不是让读者从文件里的
  一个 `sh` 去猜。
- 新增 `references/mcp.windows.example.json`：一份 Windows 用的样例（不含凭据、占位符是明文
  占位符），由既有的 `audit_windows_config` 校验（无 `sh`、无 `.sh` 参数、stdio、不钉 `cwd`）。

**Windows 实际状态：`NOT_TESTED`。** 没有 Windows 设备授权，上面全部是**形状**与**计划**，
不是平台测试结果。`windows_plan.verified_on_windows = False` 是探针输出里的一个字段，
不是文档里的一句免责声明。

### 12.4 三家宿主的实际测试状态（分开列，不合并）

| 宿主 | 本轮实际做到 | 标签 |
|---|---|---|
| **Claude Code**（Linux，2.1.272） | 真实进程加载 → `initialize` → `tools/list`(23) → T0 调用成功 + 被拒调用 BLOCKED；退出码与脱敏收据齐全 | **三件齐**（缺 ladder 的 T1，仍不上调 `CLIENT_VERIFIED`） |
| **Codex** | 只判**生成产物的形状**（marketplace/清单/入口/逃逸）；**未**在真实 Codex 上导入 | `BLOCKED`（无设备授权） |
| **WorkBuddy** | 只判**生成产物的形状**（连接器必需文件/type/入口随包/token 表单字段名）；**未**导入 | `BLOCKED`（无设备授权） |
| **Windows** | 只判**形状 + 计划**；`verified_on_windows = False` | `BLOCKED`（无设备授权） |

**不得**把上面三行读成"Codex/WorkBuddy/Windows 已验证"——它们判的是我们生成的文件自身是否自洽，
不是那些宿主的行为。

---

## 13. Wave-4：对集成候选 ba3dd28 / code 6f0219d 的复测与合并状态

**候选（只读检出，未修改其工作树）：** `ba3dd28b227cbfa6aa13e8cba8898d2b7f490e08`，
code tip `6f0219da7e4a6c92bd04e7eeda86114b9e2aca9f`（`ba3dd28` 是 docs-only）。

### 13.1 哪些 G2 修复已合并 / 未合并（`git merge-base --is-ancestor` 判定）

| G2 commit | 内容 | 是否在 `6f0219d` 里 |
|---|---|---|
| `877ee89` | 从 `.mcp.json` 删除 `default_tools_approval_mode` + 字段白名单 | **已合并** |
| `3c0cca3` | 三家宿主的离线包校验（Codex marketplace / WorkBuddy 连接器） | **已合并** |
| `7cd7fa8` | OS 中立启动器 + Windows 样例 + 退出码收据 | **未合并** |
| `1a0def9` | Wave-3 状态文档 | **未合并** |

**候选 `6f0219d` 的实测：** `.mcp.json` 键 = `args/command/env_vars/type`（**无**该字段）；
`mcp.json` 仍保留 `default_tools_approval_mode`。两份 `env_vars` 差集**仅**
`MARKETOPS_RESEARCH_PYTHON`（= POSIX 启动器所需），其余 15 个完全一致。

### 13.2 候选上的真实宿主冒烟（隔离 HOME，无凭据、无厂商调用）

`probe_exit=0`；`Skills (17)`；`MCP servers (1) marketops-research`；`mcp list` → `✔ Connected`；
会话 init `status: connected`；模型可见受管工具 **23**；T0 `marketops_setup_status` →
`CONFIG_REQUIRED` / `mutates_nothing=true`。**候选本身的客户端契约成立**（Wave-3 的增量不是它的前置条件）。

### 13.3 Codex：从"无设备"更正为"包可装、会话不可验"

本机**有** `codex` CLI（0.160.0），所以 Wave-3 写的"无设备授权"不够准确。在**隔离 `CODEX_HOME`**
（`/data/scratch/...`，未触碰真实 `~/.codex`）里做了真安装：

| 步骤 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 加 marketplace | `codex plugin marketplace add <dest>` | 0 | `Added marketplace marketops-local` |
| 列 marketplace | `codex plugin marketplace list` | 0 | `marketops-local → <dest>` |
| 列插件 | `codex plugin list` | 0 | `marketops-research-assistant@marketops-local  not installed` |
| 装插件 | `codex plugin add marketops-research-assistant@marketops-local` | 0 | **`installed, enabled`，0.4.0** |
| MCP 面 | `codex mcp list` | 0 | `No MCP servers configured yet`（Codex 的该命令只覆盖 `config.toml`，不含插件贡献的 server） |

**能到哪一步：** 生成物**被真实 Codex 接受并启用**——这比"只判 JSON 形状"强一档。
**不能到哪一步：** Codex 是否启动该 MCP、`tools/list` 是否 23 个，**未验**。原因是凭据：
`codex doctor` 在隔离 home 里报 `✗ auth: no Codex credentials were found`；要用真实 home 就得
`marketplace add`/`plugin add` 写用户配置（**禁止**），拷贝 `auth.json` 进 scratch（**禁止**）。
⇒ **Codex MCP 启动 = BLOCKED（凭据隔离，不是设备缺失）**。

**一条工具边界（负读数，写明试过什么）：** `codex debug prompt-input` 在**无凭据**下可用，
并确实渲染出插件里的 17 个 Skill；但把绑定的解释器换成带 `mcp` 的那一个之后，
渲染结果里 `marketops_*` 工具名**仍为 0 个**。所以该命令**不渲染 MCP 工具面**，
不能拿它当 `tools/list` 的量具——这是量具的限制，不是产品的结论。

### 13.4 新发现：安装产物绑的解释器可能根本没有 `mcp`

`setup_clients.py` 不带 `--install-deps` 时会建一个**没有 `mcp`** 的 venv，并把所有宿主配置绑到它。
于是"安装成功、静态探针 ok"与"MCP 在任何宿主上都起不来"同时成立——这正是本 lane 最该显式说出来的状态。

**修法（本 lane）：** `client_install_probe.audit_installed_interpreter()`：
- 绑定路径**不存在** → `BOUND_INTERPRETER_MISSING`（fatal，运行时无解）；
- 路径存在但 `import mcp` 失败 → **注记** `INTERPRETER_LACKS_MCP`，并给出可执行修复
  （`setup_clients.py --install-deps` 或私有 venv），`ok` 仍为真但不再说"可以跑"。

### 13.5 OS 中立启动器 vs Windows 清单（静态 harness，本轮实跑）

| 检查 | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 候选解释器枚举 | `launch_research_mcp.py --list` | 0 | `/usr/bin/python3` → `no`（无 `mcp`） |
| 指定解释器选择 | `MARKETOPS_RESEARCH_PYTHON=<venv> launch_research_mcp.py --check` | 0 | 打印该解释器 |
| Windows 样例校验 | `audit_windows_config(references/mcp.windows.example.json)` | — | `ok=True`，0 findings（无 `sh`、无 `.sh` 参数、stdio、不钉 `cwd`） |
| 可移植性审计 | `audit_launcher_portability(root)` | — | `ok=True`；`configs_needing_a_shell=['.mcp.json']`（注记，不隐藏）；`verified_on_windows=False` |

**Windows 仍然 `NOT_TESTED`**：静态 harness 判的是"这份样例是否只用了 OS 中立构造"，
不是"Windows 上能跑"。

---

## 14. Wave-5：两个启动器都走真实宿主 + 归档实扫

**被测件：** 本分支 HEAD（`plugin_sha` 见状态文件）。隔离 tmp `HOME`/`XDG_*`/`TMPDIR`，
provider 变量按名 `unset` 且从不读值，联网与付费开关全 0，**零厂商调用**。

### 14.1 两个启动器都过真实宿主（同一宿主、同一插件树，只换入口）

| 入口 | `plugin details` | `mcp list` | 会话 init | 模型可见工具 | T0 `tools/call` |
|---|---|---|---|---|---|
| `launch_research_mcp.sh`（出厂默认，POSIX） | exit 0 | exit 0 `✔ Connected` | `connected` | **23** | 执行，`CONFIG_REQUIRED` |
| `launch_research_mcp.py`（OS 中立，受控变体） | exit 0 | exit 0 `✔ Connected` | `connected` | **23** | 执行，`CONFIG_REQUIRED` |

这是把"Windows 计划"从纸面推到"同一个 Python 入口在真实宿主里确实能起服务、能列 23 个工具、能完成一次调用"——
**但 Windows 本身仍未验**，验证的是入口形态。

### 14.2 新增确定性回归：两个启动器必须播发同一套工具

`LauncherParityTests` 用**真 MCP 客户端**分别对接两个启动器，断言
`23 个工具名 + 每份 input schema 完全相等`，且**两边都没有 schema 组合子**（`anyOf` = 0）。

写这条时先踩了两个自伤，都已修正并留了注释：

1. **不给 shell 启动器指定解释器** → 它按设计拒绝（exit 78）。比较的是工具面，不是解释器发现。
2. **手搓 `initialize`/`tools/list` 的 stdin 时序** → `tools/list` 有时不回答，
   三次连跑得到 1 红 / 绿 / 2 红。**一个三次里红一次的用例比没有更糟**；改用
   `mcp.client.stdio` + `ClientSession`（宿主用的同一套握手），三次连跑全绿。

### 14.3 新增：扫客户**归档**本身，而不只扫它来自的目录

`CustomerArchiveTests` 解开实际生成的 ZIP 逐成员检查：
- **已启用的厂商 MCP**：只在*声明了 `mcpServers`/`mcp_servers`* 的 `.json`/`.toml` 里禁 `VENDOR_ENDPOINTS`；
- **凭据字面量**：全成员扫 `sk-…` / `Bearer …` / JWT 形状；
- 归档内**恰好一个** `mcp.json`，且只声明 `marketops-research`。

**第一版断言写错了一次**：它把 README 里解释"embedded bridge 连的是 `agent.tinyfish.ai`"
当成直连旁路。**散文提及厂商端点不是旁路，声明的 server 才是** —— 判据与树内审计
（端点 **且** `mcpServers` 键）保持一致。

### 14.4 修掉一个探针自身的缺陷（会影响任何客户目录名）

`cc_host_probe.sh` 原来用**目录名**当插件名去 `plugin details`。客户把 ZIP 解成
`marketops-research-assistant-v0.4.0/` 就会得到 `Plugin "…" not found`（exit 1），
而 MCP 其实是好的。现在从 `.claude-plugin/plugin.json` 读 `name`，目录名仅作兜底。
实测：改名目录下 `plugin details` 从 exit 1 → exit 0。

### 14.5 依赖 pin（本轮复核）

| 包 | 版本 | 来源 |
|---|---|---|
| `mcp` | **2.3.0** | `requirements-lock-lite.txt`（38 pin / 492 条 hash） |
| `typesafe-sdk` | 0.7.5 | 同上 |
| `keyring` | 25.7.0 | 同上 |
| `crawl4ai` | 0.9.5 | `requirements-lock-crawl-local.txt`（95 pin / 3030 条 hash） |

安装器在有锁定时走 `--require-hashes`（`dependency_sources[].hash_pinned` 进回执）。

### 14.6 Codex（隔离 `CODEX_HOME`，本轮在这棵树上复测）

`marketplace add` rc 0 → `plugin add` rc 0 → `plugin list` = `installed, enabled 0.4.0`；
`codex mcp list` 仍为 `No MCP servers configured yet`（该命令只覆盖 `config.toml`）。
**Codex MCP 启动仍 BLOCKED（凭据隔离）**：隔离 home 无凭据，真实 home 要写用户配置，
拷贝 `auth.json` 越界。

### 14.7 每宿主状态

| 宿主 | 状态 | 精确到什么程度 |
|---|---|---|
| **Claude Code**（Linux 2.1.272） | **真实** | 两个启动器各自完成 install→initialize→tools/list(23)→T0 call，全部 exit 0 |
| **Codex**（0.160.0） | **真实安装 / 模拟不可及** | 包装上并被 enable；MCP 启动未验（凭据隔离） |
| **WorkBuddy** | **未验收** | 无该客户端；只判生成物形状 |
| **Windows** | **未验收** | 只有静态形状 + 同一 Python 入口在 Linux 宿主上通过 |

`CLIENT_VERIFIED` **仍不上调**：ladder 的 T1 只读调用未授权，本轮做的是 T0。

---

## 15. Wave-6：对 PR78 code `d88b176` 的真实宿主冒烟

**候选（只读 detached 检出，未改其工作树）：** `d88b17653546b37232cf97a5201ddd11384f503b`
（集成分支 tip 是它之上的 docs-only 提交）。
**宿主：** Claude Code 2.1.272 / Linux；服务端 SDK `mcp==2.3.0`；协商协议 `2025-11-25`。
**隔离：** 独立 tmp `HOME`/`XDG_*`/`TMPDIR`；provider 变量**按名 `unset` 且从不读值**；
联网与付费开关全 0；**零厂商调用**；未碰客户账户/设备/系统权限/正式客户端设置。

### 15.1 冒烟结果（带退出码）

| 步骤 | 退出码 | 观测 |
|---|---|---|
| `claude --plugin-dir <p> plugin details` | **0** | `Skills (17)`、`MCP servers (1) marketops-research` |
| `claude --plugin-dir <p> mcp list` | **0** | `✔ Connected` |
| 会话（T0） | **0** | init `mcp_servers: [{… "status": "connected"}]`；模型可见工具 **23** |
| 会话（T2 拒绝） | **0** | `marketops_search` → **`BLOCKED` / `Unknown Run`** |

**宿主真实发过的方法（协议 tap 抓的原始帧，不是推测）：**
`initialize` ×2 → `notifications/initialized` ×2 → `tools/list` ×2 →
**`prompts/list` ×1（0 项）** → **`resources/list` ×1（0 项）** → `tools/call` ×1。
`tools/list` 回来的 **23 个工具全部以 `marketops_` 开头**。

**T0 回执：** `marketops_setup_status {}` → `status=CONFIG_REQUIRED`、`mutates_nothing=true`、
`network_or_provider_fee_incurred=false`。
**T2 回执：** `marketops_search{provider:serper, run_id:"absent-run", …}` → `BLOCKED`、`reason="Unknown Run"`。

**脱敏：** 两个 session 回执与 tap 日志跑三种凭据正则，**0 命中**。

### 15.2 清单与环境变量

| | `.mcp.json`（Claude） | `mcp.json`（通用） |
|---|---|---|
| keys | `args/command/env_vars/type` | `args/command/default_tools_approval_mode/env_vars/type` |
| command | `sh`（POSIX 启动器） | `python` |
| args | 2（启动器 + `research_mcp.py`） | 1（`research_mcp.py`） |
| env_vars | 16 | 15 |
| 差集 | **仅** `MARKETOPS_RESEARCH_PYTHON` | 无 |

### 15.3 CLI 兼容：**没有声明任何版本下限**

三份清单里没有任何 `min*Version` / `engines` / `compatibility` 字段。所以"能装就兼容"是
读者会做的推断，而不是仓库的承诺。新增探针字段 `host_version_floor` 把这件事**报出来**
（`NO_DECLARED_HOST_VERSION_FLOOR` 注记），而不是留白。**不去发明一个宿主并不认识的字段**——
那正是 `default_tools_approval_mode` 犯过的错。当前兼容性只在 **2.1.272 + mcp 2.3.0** 上被测过。

### 15.4 直连旁路：源码树与**发布归档**都查

用候选自己的 `build_client_bundles.py` 生成归档后逐成员扫描：
`marketops-research-assistant-v0.4.0.zip`，**17 Skills / 1 MCP**；含 `mcpServers` 的配置成员 1 个，
**无启用的厂商端点**，**无凭据字面量**；归档内唯一的 `mcp.json` 只声明 `marketops-research`。

### 15.5 尚未并入集成的 G2 工作（本轮在候选上如实体现）

候选缺 `scripts/launch_research_mcp.py`，因此可移植性审计在候选上返回
`ok=False`、`configs_needing_a_shell=['.mcp.json']`。这不是候选的缺陷，而是**我的 OS 中立
启动器尚未并入**；该入口在本分支上已通过真实宿主验证（§14.1）。

### 15.6 Windows / Codex / WorkBuddy

- **Windows**：`NOT_TESTED`。只有形状检查；`windows_plan.verified_on_windows = False`。
- **Codex**：上一轮在隔离 `CODEX_HOME` 实测到 `installed, enabled 0.4.0`；**MCP 启动仍 BLOCKED**
  （隔离 home 无凭据；真实 home 需写用户配置；拷 `auth.json` 越界）。
- **WorkBuddy**：无该客户端，只判产物形状。

**`CLIENT_VERIFIED` 仍不上调**：ladder 要的 T1 只读调用未授权，本轮做的是 T0 + 一次 T2 拒绝。

---

## 16. Wave-7：product `bd10203` 的宿主面与 S0 DENY（含一条 RED）

**被测件（只读检出，未改其工作树）：** `bd10203d0eb927ab7505abf332fc1ee6b84ae387`
（集成 tip `479f428` 是其之上的 docs 提交）。
**宿主：** Claude Code 2.1.272 / `mcp==2.3.0` / 协议 `2025-11-25`。
**隔离：** 独立 tmp HOME/XDG/TMPDIR；provider 变量按名 `unset` 且从不读值；联网与付费开关全 0；
**零厂商调用**。主机预检：load 13.45/128 核、可用内存 169 GB、PSI cpu 1.11 / mem 0.00 / io 0.08、磁盘 31%。

### 16.1 RED：7 个厂商能力工具与受管入口并列暴露

`tools/list` 回来 **24 个工具**，其中 `marketops_managed_call`（唯一受 S0 闸门的入口）与
**7 个 legacy 厂商工具**并列：

```text
marketops_search              -> dual_search_mcp.search        (Serper/SerpApi REST)
marketops_crawl               -> research_provider_extensions  (Crawl4AI)
marketops_tinyfish            -> 同上                           (TinyFish)
marketops_serpapi_mcp_search  -> 同上                           (SerpApi MCP)
marketops_tinyfish_agent      -> 同上                           (付费 Agent)
marketops_tinyfish_agent_wait -> 同上                           (Agent 状态)
marketops_jev_choice          -> 同上                           (Jev)
```

**源码里这是可核对的**：`marketops_search` 直接 `return search(...)` → `dual_search_mcp.search(...)`；
其余 6 个走 `invoke(...)` → `extra.invoke(...)`。两者都**不经过** `research_wiring.managed_invoke`
的 S0 闸门。**发行配置里也没有任何工具白名单**（`.mcp.json`/`mcp.json` 均无
`enabledTools`/`enabled_tools`/`disabledTools`），所以宿主把 24 个全部看到、可挑。

**判定：RED。** 这不是"多几个工具"，而是**网关旁路**：一个 Agent 选 legacy 工具即可绕开
S0 证据闸门。**本轮未执行其中任何一个**（`--allowedTools` 只放行两个安全工具；wire 上
`tools/call` 只有 1 次，见 16.2）。

**机检**：`client_install_probe.audit_tool_surface()` 输出
`severity=RED`、`legacy_vendor_tools=[7 个]`、`LEGACY_VENDOR_TOOL_EXPOSED`。
该清单是**当前 inventory**，provider lane 退役它们后必须同步把这个常量清空——留旧条目会
报一个已不存在的旁路，那也是假话。

### 16.2 安全调用与 S0 DENY（真实宿主，带退出码）

| 会话 | exit | 观测 |
|---|---|---|
| A：T0 `marketops_setup_status` | **0** | `connected`；模型可见 **24** 工具；回执 `CONFIG_REQUIRED` |
| B：`marketops_managed_call(serper.search, …)`，数据根无 S0 证据 | **0** | **`status=BLOCKED`**、`reason_code=CAPABILITY_NOT_AVAILABLE`、**`gateway_invoked=false`**、`s0_failed` 列出 6 条未过闸门（S0_01/02/03/05/06/07） |

**wire 上一共 1 次 `tools/call`，名字是 `marketops_managed_call`；7 个 legacy 工具执行次数 = 0。**
两个 session 回执与 tap 日志的三种凭据正则命中数均为 **0**。

`gateway_invoked=false` 是这条证据的要害：**闸门拒绝时没有预约、没有 adapter 运行**，
所以"安全地演示拒绝"不是靠约定，而是靠回执本身携带的事实。

### 16.3 静态面

| 项 | 值 |
|---|---|
| `.mcp.json` keys | `args/command/env_vars/type`，16 个 env_vars，`sh` |
| `mcp.json` keys | 多一个 `default_tools_approval_mode`，15 个 env_vars，`python` |
| env 差集 | 仅 `MARKETOPS_RESEARCH_PYTHON` |
| 工具白名单 | **无**（这正是 16.1 的 RED 能发生的原因） |
| 归档 | 17 Skills / 1 MCP；含 `mcpServers` 的成员 1 个；**无启用厂商端点、无凭据字面量** |

### 16.4 每宿主

Claude Code **真实**（A/B 两会话，exit 0）；Codex **真实安装过 / MCP 启动 BLOCKED**（凭据隔离）；
WorkBuddy **未验收**；Windows **未验收**。
**`CLIENT_VERIFIED` 仍不上调**：T1 未授权，且 16.1 的 RED 未清除前，客户端"能装能连"不等于可交付。

---

## 17. Wave-8：干净 ZIP → 干净解压 → 原生 CLI 列出 → 真实 stdio 调用

**产品（只读检出，未改其工作树）：** `be7f3e89fe015148b91d4066dca9c8f20a95be99`；
PR78 tip `d2366bb1fe81808326a830c011de4f3a1b0a0974`（其上是 docs）。
**宿主：** Claude Code 2.1.272 / `mcp==2.3.0`。**隔离：** 独立 tmp HOME/XDG/TMPDIR。
**本轮未发起任何模型调用**（无既有授权容量），全部证据来自原生 CLI 元数据与**真实 stdio 客户端**。
**主机预检：** load 13.38/128 核、可用内存 170 GB、PSI cpu 0.14 / mem 0.00 / io 0.25、磁盘 31% —— 非 RED。

### 17.1 归档

| 项 | 值 |
|---|---|
| 归档 | `marketops-research-assistant-v0.4.0.zip` |
| **SHA-256** | **`ba1a5829fe0f35ad35edf2e1436f48e6a28c3a71a9397b8ec38c0602bdb020ac`** |
| 成员数 / 字节 | 249 / 1,374,613 |
| 构建方式 | 固定时间戳 + 排序 + 压缩 → **两次构建字节相同**（有回归用例） |
| 解压 | `extract_rc=0`，249 个文件，17 个 `SKILL.md` |

### 17.2 原生 CLI 列出（两种方式）

| 输入 | 观测 | exit |
|---|---|---|
| `--plugin-dir <解压目录>` `plugin details` | `Skills (17)`、`MCP servers (1) marketops-research` | **0** |
| `--plugin-dir <ZIP 本身>` `plugin details` | 同上（CLI 直接吃 zip） | **0** |
| `--plugin-dir <解压目录>` `mcp list` | `✔ Connected` | **0** |

### 17.3 真实 stdio（从解压目录启动）

| 观测 | 值 |
|---|---|
| `initialize` | `MarketOps Lite Research 0.4.0` |
| `tools/list` | **24** 个，全部 `marketops_*`，**`anyOf` = 0** |
| `marketops_managed_call` 存在 | 是 |
| **调用 1** `marketops_setup_status {}` | `CONFIG_REQUIRED`、`mutates_nothing=true` |
| **调用 2** `marketops_managed_call(serper.search, …)`，数据根无 S0 证据 | **`BLOCKED`** / `CAPABILITY_NOT_AVAILABLE` / **`gateway_invoked=false`** / 6 条 S0 闸门未过 |

**只做了这两次调用**；7 个 legacy 厂商工具一次未执行。

### 17.4 供应商记录器归零

调用后数据根下只出现 `state/usage-ledger.sqlite3`；表 `caps` / `events` / `operations`
**计数全为 0**，且**没有** legacy 账本（`search_attempts` / `provider_reservations` 均不存在）。
即两次调用没有产生任何供应商尝试或预约。

### 17.5 泄漏与配置面

- 数据根下凭据形状正则命中 **0**；回执中无凭据。
- 归档内声明 `mcpServers` 的只有 `.mcp.json` / `mcp.json`，各自只声明 `marketops-research`；
  `.codex-plugin/plugin.json` 只是指向 `./mcp.json` 的指针。**无厂商端点**。
- 命令面：`.mcp.json` = `sh`（POSIX 启动器，设计如此），`mcp.json` = `python`；
  **参数里没有 `-c`**，也没有非预期的 shell。

### 17.6 平台状态（不overclaim）

| 宿主 | 状态 |
|---|---|
| **Claude Code（Linux）** | **真实**：CLI 列出 + stdio 握手 + 两次安全调用 |
| **Codex / WorkBuddy / Windows** | **仅静态清单检查**，无设备 ⇒ 不是客户端验证 |

**`CLIENT_VERIFIED` 仍不上调。** 本轮的证据链到"解压后能列出、能握手、能两次安全调用"，
但 ladder 要的 T1 只读调用仍未授权；且 §16.1 的 7 个 ungated 工具 RED 仍未清除。

---

## 18. Wave-14：最终发布候选的干净归档收据（product `5473724`）

**被测件：** exact-SHA 只读检出 `5473724089020c8f70bb01e46323b7d807ffc284`（PR78 HEAD `5e4462f6…` 是 docs）。
**宿主：** Claude Code 2.1.272 / `mcp==2.3.0`。**等价性：** direct / `launch_research_mcp.sh` / `launch_research_mcp.py` 三者一致。
**零模型调用、零厂商网络、零 socket。**

### 18.1 归档

| 项 | 值 |
|---|---|
| 文件 | `marketops-research-assistant-v0.4.0.zip` |
| **SHA-256** | **`551cbff247ba45f75d65e0849b3210862d691cb5bc3a2523a2309c8668c31471`** |
| 成员 / 字节 | **145** / 948,658 |
| 排除策略 | 与 `setup_clients.py` 一致：不含 `tests/`、缓存、客户数据、`.env` |
| 模式位 | **保留**（`launch_research_mcp.py` 755；`launch_research_mcp.sh` 上游即 644，经 `sh` 调用） |
| 两次构建 | 字节相同（有回归用例） |

### 18.2 解压与工具面

| 步骤 | 结果 |
|---|---|
| `unzip` 到干净目录 | rc 0；145 文件；**17 Skills**；不含 `tests/` |
| 干净 temp HOME + 干净 venv | venv 建成但**无 `mcp`、无 pip**（离线装不了依赖）⇒ stdio 冒烟显式使用带 `mcp` 的解释器，已标注 |
| 原生 CLI `plugin details` | rc 0：`Skills (17)`、`MCP servers (1) marketops-research` |
| 原生 CLI `mcp list` | rc 0：`✔ Connected` |
| `tools/list` | **25**，全 `marketops_*`，**`anyOf`=0**，含 **`marketops_evidence_workbook`** |
| direct vs `launch.sh` vs `launch.py` | **25 / 25 / 25，三份完全一致** |

### 18.3 只做两次非计费调用

| 调用 | 结果 |
|---|---|
| `marketops_setup_status {}` | `CONFIG_REQUIRED`、`mutates_nothing=true` |
| `marketops_managed_call(serper.search, …)` 无 S0 证据 | `BLOCKED` / `CAPABILITY_NOT_AVAILABLE` / **`gateway_invoked=false`** / 6 条 S0 未过 |

**零派发的硬证据：** `strace -f -e trace=connect` 全程**没有任何 `connect()` 调用**（唯一一行是 SIGCHLD）；
且整轮结束后数据根**一个文件都没写**（连 ledger 都没创建）。

### 18.4 自上一版 G 归档的变化（不沿用旧计数）

`be7f3e89`(24 工具) → `5473724`(25 工具)：**新增恰好 `marketops_evidence_workbook`**。
可执行源码另有较大演进（`research_capability_registry.py` +927、`research_adapter_tinyfish.py` +950、
`research_egress.py` +178 等）；**`launch_research_mcp.py`（OS 中立启动器）首次随包发布**并已在本轮实测可用。

### 18.5 发布卫生项（文档，非可执行/配置）

归档内有 **2 份参考文档**含构建机绝对路径：`references/EGRESS_SMOKE_RECORD_2026-10-17.md`、
`references/PROVIDER_ADAPTER_INTERFACE_WD.md`（`/home/operator/...`）。它们不是我的内容，**未改**；
不含可执行代码或配置中的绝对路径，也不含凭据字面量（全归档扫描 0 命中）。建议由对应 lane 改成占位符。

配置面：只有 `marketops-research`；**无厂商端点**；参数**无 `-c`**；`.mcp.json` 用 `sh`、`mcp.json` 用 `python`。

### 18.6 平台状态

Claude Code（Linux）**真实**；Codex / WorkBuddy / Windows **仅静态清单**（`windows_shape` 在源码归档上是
POSIX 形态，属设计；`verified_on_windows=false`）。
**模型驱动的 Claude Code 研究 E2E：`BLOCKED_EXTERNAL`** —— 未授权，未发起。
**Stage 0 保持 DENY**：无官方 B0/价目、无完整 tools/list schema 核验、无 TinyFish key 轮换确认。

---

## 19. 更正 §17/§18，并修生产构建器（P0）

### 19.1 更正

**§17/§18 记录的 `145 members / 948,658 bytes / sha256 551cbff2…` 不是生产构建器的产物。**
它是 `tests/test_wave1_client_packages.py::CleanArchiveTests._build()` — 一个**测试助手** —
打出来的归档。两者不是同一个东西，把那个哈希当成发行哈希是错的。
真正决定顾客拿到什么的是 `scripts/build_client_bundles.py::make_zip()` + `build()`。

### 19.2 生产构建器的三个缺陷（本轮修复）

| 缺陷 | 实测 | 修法 |
|---|---|---|
| **模式位被压平** | `make_zip` 无条件 `external_attr = 0o644 << 16`，把 100755 的 `launch_research_mcp.py` 也记成 0644 | `(S_IFREG \| S_IMODE(mode)) << 16`，并先 `& ~0o022` 归一掉 umask 带来的 group/other-write（否则归档哈希随构建者变化） |
| **符号链接会被解引用** | `rglob` + `is_file()` 会把包外文件的内容当包内文件打包 | 改用 `os.walk(followlinks=False)`；遇到任何符号链接直接 `SYMLINK_IN_PACKAGE` 抛错，不解引用 |
| **复制清单漏了 17 个模块**（P0） | 连接器缺 `research_wiring`、`research_grants`、整个 `research_adapter_*` 层等；**连接器连自己的入口都 import 不了**（实测 `ImportError: attempted relative import with no known parent package`） | 手维护清单改为「全量复制 + 显式永不发布集合」（当前仅排除 `cc_host_probe.sh`）。新增模块自动随包 |

模式实测：生产 ZIP 记录 `launch_research_mcp.py` = **0755**、`.sh` = **0644**，`unzip` 还原一致。

### 19.3 机器绑定：改为**数据化标注**，并实测拷贝行为

生成的 `mcp.json` 绑定**构建机解释器绝对路径**与**构建机包绝对路径**，无法靠换一个 `command` 变成可移植。
本轮的做法：

- 生产构建器在**连接器目录之外**写 `workbuddy/MACHINE_BOUND_PREVIEW.json`
  （`marker / why / regenerate_on_target / portable:false`）——放在外面，宿主导入永远看不到多出来的键；
  构建回执同时带 `machine_bound: true` 与 marker 路径。
- **实测拷贝行为（不是推断）**：把归档解到另一个目录后，其自带的 `mcp.json` 的 `args[0]`
  仍指向**原始构建目录**，因此同机拷贝会静默运行旧树。**这是当前机制的已知限制**，
  缓解手段就是那条 marker 与「在目标机重新生成」的指令；没有假装可移植。

### 19.4 生产产物实测（精确产品源码 + 本分支修好的构建器）

因为连接器必须用**精确产品源码**才有 25 个工具，本轮在 scratch 副本上把产品树 `5473724` 与
本分支修好的 `build_client_bundles.py` / `launch_research_mcp.py` 合成后构建（只读检出未改）：

| 项 | 值 |
|---|---|
| **生产 ZIP sha256** | **`25a6fa63ed00db70dc7e22cd62d7bb44f5b31a53f49fe2c374af0c43caab475c`** |
| 字节 | 945,328 |
| 连接器脚本数 / Skills | **55 / 17**；`tests/` 按安装口径排除 |
| 模式 | `.py` 0755、`.sh` 0644（recorded 与 unzip 还原一致） |
| stdio（用生成的 `mcp.json` 原样） | **25 工具**、全 `marketops_*`、含 `marketops_evidence_workbook` |
| `setup_status` | `CONFIG_REQUIRED`、`mutates_nothing=true` |
| `managed_call(serper.search)` 无 S0 | `BLOCKED` / `CAPABILITY_NOT_AVAILABLE` / **`gateway_invoked=false`** |
| 零派发 | `strace -f -e trace=connect` **0 次 `connect()`**；数据根 **0 文件** |
| 原生 CLI | 连接器上 `plugin details` → `Skills (17)`、**`MCP servers (0)`**（它是 WorkBuddy 连接器，配置名是 `mcp.json` 不是 `.mcp.json`，Claude 不认——如实记录，不算缺陷） |

### 19.5 回归（G-only，防复发）

- `ProductionBundleTests`：直接调 `build()`，断言 ZIP 记录的模式 = 打包文件的模式（0755 py / 0644 sh）、
  同一目标路径两次构建字节相同、符号链接被拒、marker 在连接器之外、连接器不含 `tests/`。
- `ConnectorCompletenessTests`：连接器必须包含 `scripts/` 下**每一个** `.py`；并且
  `python -c "import research_mcp"` 在连接器目录里必须成功（这正是漏 17 个模块时失败的那一条）。
