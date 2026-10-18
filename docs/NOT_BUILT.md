# 刻意未建的部分

这份文件记的是**今天没有实现、而且本阶段不打算实现**的东西，以及将来真要建时
**已经被固定住、不能推翻**的约束。

它曾经不是一份文档，而是 `services/api/` 与 `services/feishu_bot/` 两个目录 ——
各自只有一个 `__init__.py`（一行 docstring）和一份 README。那两个目录里没有一行
可运行的代码，删掉它们不损失任何能力；**搬过来的是它们唯一的理由**：
「为什么现在不建」本身就是一条会被重新追问的决定，删掉它，下一个人会把同一件事
再想一遍，而且很可能想出不一样的答案。

判定「该不该建」的判据从来不是「目录里空着不好看」，而是**有没有要服务的对象**。
下面两条各自说清了今天没有对象、以及什么情况下会出现对象。

---

## 1. HTTP API（曾计划落在 `services/api/`）

**没有实现，是刻意的。**

需求原文界定了本阶段的技术范围：只搭 Research Pipeline，不做邮件发送、
follow-up、回复监控、目标站点登录、CAPTCHA 绕过、SMTP 验证、邮箱猜测、
住宅代理绕 WAF、复杂多 Agent。

在这个范围里，一个 HTTP API 没有要服务的对象：唯一的消费者是本地操作者，
而 `marketops/interfaces/cli.py` 已经覆盖了它。先建 API 壳子只会带来
需要维护的接口和需要回答的鉴权问题，却不解决任何当前问题。

### 什么时候该填

出现下面任一情形时，再设计 API：

* 需要从非本机的系统触发运行（例如飞书机器人发指令）；
* 需要把结果对接到同事的界面，而不是导出 JSON / Bitable；
* 需要在多个操作者之间共享运行状态。

### 建的时候不能推翻的约束

这些约束已经由**现有代码**固定，不是建议：

* `research_only = true`、`send_authorized = false` 是模型层校验，
  API **不得**提供任何绕过它们的入参；
* 运行数据路径来自 `MARKETOPS_DATA_ROOT`，API 不得自行拼路径；
* `contracts/*.json` 是唯一对外契约，由 `marketops.schema` 从模型导出；
  API 的请求/响应体应当复用它们，而不是另写一套。

相关：`docs/ARCHITECTURE.md`（分层与依赖方向）、`docs/POC_PLAN.md`（本阶段范围与验收）。

---

## 2. 飞书机器人（曾计划落在 `services/feishu_bot/`）

**没有实现，是刻意的。**

需求原文：「飞书暂时做 adapter，不阻塞 Demo。先留接口和环境变量，
新飞书 Sandbox 应用申请完成后再接。」

因此本阶段的交付物是 `marketops/adapters/feishu/` 里的**适配层契约**，
而不是一个机器人。在 Sandbox 应用申请下来之前，写 bot 只能靠猜：

* 事件订阅用哪种模式（长连接 / Webhook）取决于应用配置；
* 机器人被允许在哪些群、能读哪些消息，取决于审批结果；
* 权限范围没有下来之前，任何鉴权代码都无法验证。

### 当前状态

| 项 | 状态 |
|---|---|
| 环境变量名 | 已确定（见 `.env.example`） |
| 字段映射契约 | 已确定（`marketops/adapters/feishu/bitable.py`） |
| 重试 / 限流策略 | 未定 —— 等真实 API 配额 |
| 事件订阅 | 未定 —— 等 Sandbox 应用配置 |

`marketops/adapters/feishu/client.py:89` 的 `ADAPTER_STATE` 是 `"partially_wired"`，
且**逐种记录分开**记录在 `RECORD_KIND_STATE` 里：`handoff` 是 `"wired"`（写入路径
实现了，含读回行 id/版本/条数与失败回执），`route` / `opportunity` 是
`"no_business_key_declared"`（**不是代码没写** —— 它们没有一条可判定的业务键，
那是一个业务决定）。推路线/机会会返回 `PushResult(reason="route_not_wired")`
并带上这个理由 —— 显式的未接线，而不是静默 no-op。静默 no-op 会让「以为推出去了」
这种事发生在几个月后。这一条有执行器：`tests/test_feishu_adapter.py::test_adapter_state_is_honest`
断言这个状态，所以「接线没做完但状态先改了」会当场红。

**真实飞书写入是 BLOCKED 的**（`client.py:103` 的 `LIVE_WRITE_STATE`，缺
`LIVE_WRITE_BLOCKERS` 里那三样：业务写入授权、凭据、这条路**从未对厂商执行过一次**）。
第三样不是「本机到不了」—— 2026-09-24 实测本机 DNS 能解出 ``open.feishu.cn``；
缺的不是网络。
因此 `tests/test_feishu_push.py` 里跑的是**隔离**测试（`httpx.MockTransport` 传输
替身，一次网络都不发），那些通过是 `sandbox_pass`，**不是**真实厂商的 PASS。

### 接线时的顺序

1. 推送实现已按本清单补上（`client.py` 的 `push_handoff`），
   执行器是 `tests/test_feishu_push.py`（mocked HTTP，不打真实 API）；
2. 让 `ADAPTER_STATE` 变成 `"wired"` 只剩一件事：给 `route` / `opportunity`
   定下业务键、并实现它们各自的写入路径。这两个取值由
   `tests/test_feishu_adapter.py::test_adapter_state_is_honest` 钉住 ——
   状态先改了而实现没跟上，会当场红；
3. 最后才做 bot 的事件订阅 —— 它依赖前两步已经稳定。

### 建的时候不能推翻的约束

飞书侧的任何数据流**同样**受本阶段的范围限制：
`research_only = true`、`send_authorized = false`。
机器人不得被用来发送任何接触邮件。

相关：`docs/PRODUCT_SPEC.md`（非阻塞清单）、`docs/DATA_MODEL.md`（字段映射）。
