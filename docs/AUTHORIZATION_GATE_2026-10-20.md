# 外发授权闸门：契约与最后核验（2026-10-20）

状态：**设计 + 最小实现，Draft。未部署、未向任何人发送任何内容。**
对应 Issue：`ProfessorZhi/marketops#6`（P0）。

本文只描述公共源码里的授权边界，不含任何客户名称、真实联系人、会话原文、
内部主机或凭据。旧私有工作台账中的人工闸门切片以编号引用（private PR #44），
其内容未被复制。

## 1. 问题

Issue #6 要求：**在人类明确授权之前，对外发效果 fail-closed。** 判定不能来自
工具返回成功、模型声称有权限、或一条旧 PR 的存在。

public `main` 已经有一条完整的外向链（`application/outreach.py`）：
`plan → generate_drafts → prepare_wave → record_authorization → dispatch_wave →
reconcile`。这份 ADR 不重建它；它补的是三个在真实副作用入口仍然缺失的拒绝：

| 缺口 | 今天的行为 | 本切片 |
|---|---|---|
| 授权从哪条路径进入账本 | 应用层可以构造任意 `authorized_by` / `authority_ref` 并登记 | 授权带 `provenance`；默认 `unverified`，`dispatch_wave` 只放行 `bearer_principal` |
| 执行者与批准者 | `dispatch_wave` 不看执行主体 | `executor` 与 `authorized_by` 相同时整批拒绝；MCP `send_wave` 总是传传输层主体 |
| 批次执行中途失效（TOCTOU） | 授权在循环外取一次快照，循环内不再重读 | 每一封之前重读批次状态、清单摘要、授权记录与当前时刻 |
| 外部批准记录的来源 | `authority_ref` 只是字符串，没有验证器 | `AuthoritySource` 端口；默认 `NoAuthoritySource` 答 `BLOCKED_TRUST_SOURCE` |

## 2. 已经存在的模型（不重造）

`WaveAuthorization`（`marketops/domain/outreach.py`）已经绑定：

| 要求 | 字段 | 判据 |
|---|---|---|
| actor | `authorized_by` | MCP `authorize_wave` 取自 `guard.current_principal()`，不是载荷 |
| scope | `workspace` / `scene_id` | `covers()` 与清单逐项比对 |
| target / revision | `manifest_sha256` | 每次**重新计算**清单摘要再比 |
| expiration | `expires_at` | `is_live_at()` 与 `covers()` 两处都判 |
| revocation | `revoked_at` / `revoked_reason` | 撤回必须带理由 |
| approved range | `approved_count` | 只发清单前 N 行，不读成 `len(entries)` |

角色分离在 `security/catalog.py`：`authorize_wave` 是目录里唯一 `_AUTHORIZE`
的工具，只有 `authorizer` 角色可调；controller 不能。令牌铸造侧
（`security/identity.py`）另有「研究身份不得携带 authorizer」的检查。

`record_authorization` 的 docstring 已经写明它**不产生授权**：它登记一份已经
存在的记录，并核对它覆盖清单。本切片没有把这句话改松。

## 3. 本切片新增的契约

### 3.1 `AuthorizationProvenance`

```python
class AuthorizationProvenance(str, Enum):
    UNVERIFIED = "unverified"          # 默认：任意应用层构造
    BEARER_PRINCIPAL = "bearer_principal"  # 只由 MCP authorize_wave 写入
```

`dispatch_wave` 只放行 `BEARER_PRINCIPAL`；其余整批 `skipped`，理由带
`BLOCKED_TRUST_SOURCE`。这不是密码学签名：它证明「这条记录由受管入口写入」，
不证明「这个人真的有权批准」。后者是 §3.2 的问题。

默认值必须是 `UNVERIFIED`：一个默认 `BEARER_PRINCIPAL` 的字段会让「忘了传」
与「受管入口写入」在盘上同形。

### 3.2 `AuthoritySource`：外部批准记录

```python
class AuthorityVerdict(str, Enum):
    VERIFIED = "verified"
    NOT_FOUND = "not_found"
    UNTRUSTED = "untrusted"
    BLOCKED_TRUST_SOURCE = "blocked_trust_source"

class AuthoritySource(Protocol):
    def check(
        self, *,
        authority_ref: str, authorized_by: str,
        workspace: str, scene_id: str,
        action: str, target: str,
    ) -> AuthorityCheck: ...
```

`record_authorization(..., authority_source=...)` 在写入前调用它：

- 只有 `VERIFIED` 放行；
- `NOT_FOUND` / `UNTRUSTED` / `BLOCKED_TRUST_SOURCE` 一律拒绝；
- 验证器抛异常也按拒绝处理（fail-closed，不是 fail-open）。

`NoAuthoritySource` 是**今天唯一诚实的实现**：本仓没有接入任何受管批准记录
来源，所以它对每一次询问答 `BLOCKED_TRUST_SOURCE`。把它换成 `VERIFIED` 而
不接真的来源，等于从记录里删掉「没有验证」这件事。

### 3.3 最后核验的位置

```python
dispatch_wave(
    *, wave_id, store, scenes, transport, rules, at,
    executor: str | None = None,
    clock: Callable[[], datetime] | None = None,
)
```

`dispatch_wave` 是唯一会造成不可撤销外部效果的应用层入口。检查顺序：

1. Scene 在运行；
2. 批次状态是 `AUTHORIZED` / `SENDING`；
3. 清单存在且摘要未变；
4. 授权当前覆盖清单（重新计算摘要）；
5. **授权 provenance 是 `bearer_principal`**；
6. **执行者与批准者不是同一个主体**；
7. 发送箱一致、时间窗；
8. 每一封之前**重读**：批次状态、清单摘要、授权记录、当前时刻；
9. `begin_attempt` 的 `won`（存储的 `BEGIN IMMEDIATE` 背书）。

`clock` 默认返回批次的 `at`。一个可能跨过 `expires_at` 的调用方必须传
`utc_now`，否则「过期」只能在下一次调用时被发现。这是本切片明确留下的
调用方责任，不是隐含的乐观假设。

## 4. 负测（全部在传输层之前断言）

`tests/test_outreach_contract.py` 第九节，memory 与 sqlite 两种后端各跑一遍：

| 判据 | 断言 |
|---|---|
| 没有授权 | `transport.sent == []` |
| `provenance=unverified` | `transport.sent == []`，理由含 `BLOCKED_TRUST_SOURCE` |
| 执行者 == 批准者 | `transport.sent == []` |
| 批次中途退回待授权 | 只发出第一封，其余 `skipped` |
| 批次中途过期 | 只发出第一封，理由含「过期」 |
| `NoAuthoritySource` | 登记被拒，存储为空 |
| `NOT_FOUND` / `UNTRUSTED` | 登记被拒，存储为空 |
| 验证器崩溃 | 登记被拒，存储为空 |
| `VERIFIED` 正向对照 | 登记成功，且验证器收到绑定的全部事实 |

四条守卫各做一次变异负控（去掉判据后对应用例必须变红）：provenance、executor、
逐封重读、`NoAuthoritySource` 的判决。结果均 BITES，源码随后从备份恢复。

## 5. 与旧私有工作台账的关系

- private PR #44（`draft(fix): 人工闸的 reason 是枚举不是散文`）处理的是
  **调用方可见性**：`reason` / `change_kind` 的 `inputSchema` 与拒绝消息。
  它的 `human_gate` 模块在 public `main` 中不存在（关键词 0 命中），因此
  **不是**本切片的移植对象；本切片用的是 public 已有的 `WaveAuthorization`
  与 `dispatch_wave`，没有引入第二套流程引擎。
- private 栈中的 `request_human_gate` / `resolve_human_gate` 语义在本切片中
  由「授权记录 + 拒绝原因」承担；没有复制任何客户数据、会话原文或内部路径。
- 未吸收部分：human gate 的**交互式待答对象**（一张可被回答的闸门行）在
  public 仍缺失。它需要产品裁决（是否允许模型发起一次「请人批准」的请求），
  不在本 P0 切片内。

## 6. 明确不做

- 不发送任何邮件、不连接任何真实 SMTP、不调用任何付费 API。
- 不改生产服务、网关、凭据、定时任务。
- 不引入第二个身份源；actor 仍然来自 Bearer 令牌环。
- 不把 `BLOCKED_TRUST_SOURCE` 写成通过；不虚构一个已验证的签发者。
- 不合并 `main`、不删除分支、不归档仓库。

## 7. 部署前还需要什么

1. 一个真实的 `AuthoritySource` 实现（接入受管批准记录），并有独立的接入测试。
2. 生产调用方把 `clock=utc_now` 传进 `dispatch_wave`，让批次中途过期可见。
3. 对既有 `UNVERIFIED` 授权做一次迁移/重批：它们在新判据下不会发出，这是
   预期的 fail-closed，而不是缺陷。
4. 由 owner 明确批准后才允许任何真实外发。
