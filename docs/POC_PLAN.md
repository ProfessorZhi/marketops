# POC 计划 —— 研究阶段

```yaml
status:       工程初始化完成，首次真实运行**尚未执行**
hard_limits:  research_only=true · send_authorized=false
```

---

## 1. 为什么先把形态定下来

运行环境总会有未就绪的时候（机器不可用、环境待配置）。这时有两种选择：
等环境就绪，或者把**不依赖具体运行环境的部分**先做完。本阶段选后者。

真正依赖运行环境的只有「部署」；而 pipeline 的形状、契约、
不变量、可迁移性 —— 这些都可以先在任意一台开发机上定下来，且**定得越早越便宜**。
若等到部署时才开工，所有设计缺陷都会在部署时集中暴露。

> 这条判断最初是在本地 Demo 阶段做出的，但它与具体是哪台机器无关，现在仍然成立。

**因此本阶段的目标不是「跑出多少联系人」，而是「把机器人做对」。**

---

## 2. 本阶段做 / 不做

### 做

```text
Known Account
  → Serper 检索（URL discovery）
  → 候选 URL
  → HTTP fetch
  → 必要时 Crawl4AI / Playwright 浏览器 fetch
  → HTML / PDF
  → 文档解析（轻量优先，MinerU 按需）
  → 字面 email 抽取
  → Name / Role / Email 证据绑定
  → grade_route → A / B / C / D / X      （一条路线值多少）
  → grade_account → 账号状态              （一个组织值不值得研究）
  → 本地 Evidence Store
```

### 不做（明确排除，不是「暂未实现」）

| 排除项 | 原因 |
|---|---|
| 邮件发送 | `send_authorized=false`，且本阶段根本没有发信组件 |
| follow-up / 回复监控 | 依赖发信，不在研究阶段 |
| 目标站点登录 | 突破 access control |
| CAPTCHA 绕过 | 同上 |
| SMTP 验证 | 会对第三方服务器产生未授权探测流量 |
| 邮箱猜测 | 与「literal 抽取」原则直接冲突 |
| 住宅代理绕 WAF | 明确受阻即换来源，不绕 |
| 复杂多 Agent | 本阶段的价值在契约与不变量，不在编排复杂度 |

**代码库不提供上述能力的接口**。不是「没接线」，是**没这个函数**。
这保证了下阶段想加时必须显式地加，而不是不小心打开一个 flag。

---

## 3. 三个队列（各 10 个账号）

| 队列 | 定义 | 它检验什么 |
|---|---|---|
| `known_success` | 历史上成功拿到过核心邮箱 | 回归 —— 新 pipeline 不能比旧链更差 |
| `accessible_no_core_email` | 站点可访问，但拿不到核心邮箱 | 上限 —— 明确哪里是天花板，而非盲试 |
| `previously_blocked` | 上次被拦或失败的 | 拦截是终态还是可绕开（**答案必须是「换来源」，不是「绕过」**） |

`CohortPlan(10, 10, 10)`；`validate_plan` 会在 `--dry-run` 时校验队列构成。

> ⚠️ **真实账号清单尚未提供**（`tests/fixtures/accounts.yaml` 刻意留空）。
> 需要一个 30 行的 YAML —— 这是启动首次真实运行的唯一前置条件。

---

## 4. 验收指标

### 主指标

| 指标 | 目标 | 说明 |
|---|---|---|
| `net_new_ab` | > 0 | 本次新拿到的 A + B |
| `net_new_cd` | > 0 | 本次新拿到的 C + D |
| `blocked_recovery` | > 0 | previously_blocked 中跑通的账号数 |
| `false_attribution` | **= 0** | 归属冲突。这是硬门槛，不是「越低越好」 |

`false_attribution` 之所以是硬门槛：一条错误归属会污染下游所有判断，
而且**看起来和正确结果一模一样**。宁可少产出。

### 成本指标

| 指标 | 关注点 |
|---|---|
| `serp_queries` | 检索是否过度发散 |
| `browser_seconds` | 浏览器是否是成本黑洞 |
| `snippet_bindings_rejected` | 该不变量是否真被考验过（长期为 0 说明没测到） |

### 明确不作为主指标

```text
domains/min · 页面数 · 抓取总量
```

抓得多不代表抓得对。本阶段的产出是**可审计的证据**，不是吞吐。

---

## 5. 每个账号要记录的东西

按要求，每个账号需可回放出：检索 query、候选 URL、HTTP 结果、
是否升级浏览器、blocked 分类、字面邮箱、角色证据、路线等级、账号状态、
耗时、API 调用次数。

这些字段全部已在 `AccountMetrics` 中，由 pipeline 自动累计 ——
它们不是「跑完再补的统计」，是运行时就写入的。

注意 `net_new_ab` / `net_new_cd` 只统计**路线**（`AccountMetrics.grades`）。
一个账号产出的**路线机会**（有岗位、没有字面邮箱）单独计数
（`AccountMetrics.opportunities`），不并进 A/B —— 机会不是路线，
合并计数会让"拿到路线的比例"看起来比实际高。

---

## 6. 首次运行的执行步骤

```bash
# 0) 环境（用本仓库自己的虚拟环境，避免误用其他 venv）
python -m pytest -q
python -m marketops.interfaces.cli check-deps

# 1) 校验队列（不联网）
export MARKETOPS_DATA_ROOT=<运行目录>       # 注入方式按运行平台而定
python -m marketops.interfaces.cli run-demo --accounts tests/fixtures/accounts.yaml --dry-run

# 2) 真实运行（SERPER_API_KEY 由部署配置提供，不入库、不写进文档）
python -m marketops.interfaces.cli run-demo --accounts tests/fixtures/accounts.yaml

# 3) 只跑一个账号先看形状（推荐先做这个）
python -m marketops.interfaces.cli run-demo --accounts tests/fixtures/accounts.yaml --limit 1 --no-browser
```

上面的 `<运行目录>` 是**逻辑占位符**：实际路径由部署配置 / 环境变量决定，
本仓库不写死任何一台机器的路径。相对路径（`tests/fixtures/accounts.yaml` 等）
一律相对仓库根解析。

`--no-browser` 值得先跑一次：它把成本压到最低，同时暴露
「有多少内容靠纯 HTTP 就能拿到」。若这个数很低，说明浏览器是必需品而非优化项。

---

## 7. 成功判据

本阶段交付「完成」的定义：

- [x] 目录审计完成，落点符合约定的目录布局
- [x] 本仓库创建并绑定 origin
- [x] Crawl4AI / MinerU 作为独立 Git 仓库就位（**非 submodule，未复制**）
- [x] skeleton / contracts / tests 齐备
- [x] `tests/test_portability.py` 让可迁移性成为硬约束
- [x] pytest 全绿
- [x] secret 扫描与 `git status` 复核通过
- [x] 首次 commit 并 push `main`
- [x] 业务规则（SOP）编译进 `profiles/`，两条定级轴各自接线
- [x] CI 在**只有本仓库**的机器上跑完整套测试（`.github/workflows/ci.yml`）
- [ ] **首次真实运行**（阻塞：等待真实账号清单）
- [ ] POC 指标达标（阻塞：同上）

前 10 项是本轮范围；后 2 项属于下一轮，且**必须由使用者提供真实账号**才能开始。
