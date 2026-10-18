"""一个 worker 的身份从**令牌环**里来，不从文件名来（§15(a)）。

## 它替换的是什么

部署侧的启动器此前自己遍历 vault 里 ``marketops-mcp-token-worker-<k>.txt`` 这一族
文件，谁在就是谁的身份。那一套有两个各自致命的性质：

* **它答不出「这份令牌还作数吗」。** 令牌的有效性只写在环里（环里只有摘要），
  文件系统上看不见。所以 9-23 的格式 3 环替换了 9-22 铸的那两个令牌之后，
  启动器照旧把死令牌交给 worker —— 表现是**委托方完全没有 ``marketops`` 工具面**
  （服务一律 401），而两轮 worker 的报告逐字写着「marketops server 未被挂载或
  其进程未起来」。现场见 ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G1。
* **它把「身份是谁」交给了一个文件名。** 主体名在环里，文件名是部署的约定；
  两者不一致时，被记下来的身份会是一个环里并不存在的名字。

所以这里改成：**候选文件仍然从 vault 里取，但每一份都要在环里解出主体来**，
解不出来的不是「次选」，是**不可用**。身份名、角色、有效期全部来自环 ——
也就是服务那一侧真正会判的那一份。

## 为什么一个 worker 一个身份（§8）

环里的主体是**授权单位**：``_claim`` 对「有活租约、是本人」这一支原样交回那条租约，
而 ``_require_holder`` 只在 ``attempt.holder != principal.name`` 时拒绝。两个 worker
共用一个主体时，第二个 worker 拿第一个的 work item id 去 ``get_assignment`` 会命中
「是本人」那一支，**拿到别人的 assignment**，而报告上不留痕迹（调用方看到的
``holder`` 就是它自己的名字）。

所以这一层做两件事：**在环能解出的候选里选一个**，并**在选中的那一刻拿一把
内核持有的 ``flock``**（``<数据根>/state/identities/<主体名>.lock``）。锁由内核按
进程持有：进程在锁就在，进程没了（正常退出、被 kill、被 SIGKILL）锁自动放。
于是「陈旧锁」这个状态不存在，也就不需要超时、心跳或回收 —— 一个需要回收的锁有
它自己的失效模式，而那正是要避免的东西。

**锁落在数据根，不落在工作区。** 被保护的资源是那个根里的 run 级状态库：两个
worker 共用身份之所以危险，正是因为它们读写的 ``state/marketops.db`` 是同一个。
锁跟着数据根走，就不必新增一个部署事实，也不会往工作区里写东西。

## 过期的那一条也要在这里判

环里的 ``expires_at`` 是**服务真正会判的那一格**（``BearerAuthBackend`` 那一条链最后
落在 ``auth.check_token`` 里）。所以一个过期的令牌与一个被轮换掉的令牌在委托方面前
是同一件事：401，而报告上写着「marketops server 未被挂载」。本层因此把它当成**不可用**
而不是「交给服务去判」—— 少判这一格，§15(a) 那个缺口会在有效期到点那天原地复活，
而现场会像是「服务挂了」。

## 它不答什么

* 不答「这个主体准动哪个 Scene」。那是环里 ``workspaces`` 那一格的事，判定在服务侧。
* 不答「这条令牌发给谁」。受众同理。
* 不替部署轮换。过期与轮换的出路都是**重铸**，而重铸既有生产令牌是一次要有授权的
  动作 —— 本层把话说清楚就停下。
"""

from __future__ import annotations

import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..domain.errors import ConfigError
from ..domain.util import check_canonical_id
from .catalog import Role
from .tokens import TOKEN_RING_ENV, TokenGrant, TokenRing, format_timestamp

__all__ = [
    "IDENTITY_FILE_GLOB",
    "TOKEN_ENV",
    "TOKEN_RING_ENV",
    "Identity",
    "claim_identity",
]

#: 命令行的启动器写进环境、交给 delegate 的那一格令牌（单数）。
#:
#: 第二个读者不在本仓的 Python 里，在工作区的 ``.mcp.json``：那里写的是
#: ``${MARKETOPS_MCP_TOKEN}``，由**子 claude 自己**从环境展开（实测：
#: ``--mcp-config`` 的 JSON 里 ``${VAR}`` 会被展开）。两处必须同名，所以名字
#: 定义一次、放在这里。
TOKEN_ENV = "MARKETOPS_MCP_TOKEN"

#: 一份研究员令牌在 vault 里的命名约定。**这是部署的约定，不是环的内容**：
#: 环里只有摘要，而摘要无法反推出「该去哪个文件拿令牌」。所以文件名仍然是入口，
#: 只是它不再有权决定「你是谁」—— 那由环里的解析结果说了算。
IDENTITY_FILE_GLOB = "marketops-mcp-token-worker-*.txt"


@dataclass(frozen=True, repr=False)
class Identity:
    """一个**已经锁住**的研究员身份。

    ``repr`` 被关掉并重写，理由与 :class:`marketops.security.tokens.TokenRing`
    逐字相同：这个对象会出现在异常链与日志里，而它的 ``token`` 是一份可用的
    凭据。默认的 dataclass repr 会把它打出来。

    ``lock_fd`` 是**内核持有的那把锁**。它必须活到本轮结束 —— 掉进垃圾回收就等于
    提前放锁，而放早了的后果是另一个 worker 进来共用这个身份。持有者是调用方
    （``interfaces/research_worker.py`` 的 ``main`` 把它绑在一个局部变量上）。
    """

    principal: str
    roles: frozenset[Role]
    #: 明文令牌。只在这里、只在这一轮 —— 落盘的只有它的 sha256。
    token: str
    token_file: Path
    lock_path: Path
    lock_fd: int

    def __repr__(self) -> str:
        return f"Identity({self.principal!r}, {len(self.roles)} 个角色，令牌不外显)"

    def payload(self) -> dict[str, object]:
        """进 ``run.json`` 的那一格。**不含令牌** —— 它进的是环境，不是记录。"""
        return {
            "principal": self.principal,
            "roles": sorted(role.value for role in self.roles),
            "token_file": str(self.token_file),
            "lock": str(self.lock_path),
        }


def _refuse(*lines: str) -> ConfigError:
    """拒绝时的措辞。空行由调用方给，这里只负责把它们连起来。"""
    return ConfigError("\n".join(lines))


def _read_token(path: Path) -> str | None:
    """读一份候选文件里的令牌。读不出来返回 ``None``（由调用方归入「不可用」）。

    ``strip()`` 是**对**的：``$(cat file)`` 与 ``echo`` 都会带一个换行，而
    ``mint-token`` 打给运维的那一行末尾也有。这不是「顺手清理」—— 令牌本身由
    ``token_urlsafe`` 产生，字符集里没有空白，所以去空白不可能改变一个真令牌。
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None


def _try_lock(path: Path) -> int | None:
    """拿一把非阻塞的 ``flock``。拿不到返回 ``None``（别人正在用这个身份）。

    打开失败**抛**而不是返回 ``None``：那不是「占着」，是这一层写不下去 ——
    目录不存在、权限不对。把这两件事合成一个 ``None`` 会让一次部署错误
    表现成「身份都被占着」，而那句话的处置是「等一会儿再试」。
    """
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(handle)
        return None
    return handle


def claim_identity(ring_path: Path, *, lock_root: Path) -> Identity:
    """从环里选一个还没被占的研究员身份并锁住它。

    候选来自 ``ring_path.parent`` 下 ``IDENTITY_FILE_GLOB`` 那一族文件，**但每一份
    都要在环里解出主体**。选择的顺序按主体名排序 —— 结果是确定的，所以「哪一轮用
    了哪个身份」是一个可复现的读数，而不是一次碰运气。

    角色判据是**恰好** ``researcher``：多一个角色就是多一份这个 worker 用不到的
    权力，而其中 ``authorizer`` 尤其不能在这里出现 —— §6 的「授权必须来自独立
    责任人」在那条路径上的执行器就是角色分离；一个同时带 researcher 与 authorizer
    的令牌，让**一个代理**能自己走完「批准并投递」两半。

    Raises:
        ConfigError: 环读不出来（``TokenRing.from_file`` 的措辞）、vault 里一份候选
            文件都没有、没有任何一份能在环里解出、解出来的都不是恰好 researcher、
            或者全部研究员身份都已被在飞的 worker 占着。**四种原因在消息里分开
            列**：它们的处置完全不同（轮换 / 改角色 / 加身份），合成一句
            「没有可用身份」会让运维去猜。
        ValueError: 环里那个主体名不是规范形式（``check_canonical_id``）——
            锁的文件名按它取，需要规范化的名字会让两个主体落到同一把锁上。
    """
    ring = TokenRing.from_file(ring_path)
    if not lock_root.is_dir():
        try:
            lock_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"身份锁的目录建不出来：{lock_root}（{type(exc).__name__}: {exc}）\n"
                "它跟着数据根走（`<数据根>/state/identities`）—— 本层停下来，"
                "而不是去别处找一把谁都不认的锁。"
            ) from exc

    candidates = sorted(ring_path.parent.glob(IDENTITY_FILE_GLOB))
    if not candidates:
        raise _refuse(
            f"{ring_path.parent} 下一份研究员令牌都没有（找的是 {IDENTITY_FILE_GLOB}）。",
            "",
            "候选文件是部署的约定：环里只有摘要，摘要反推不出「该去哪个文件拿令牌」。",
            "铸一个并把令牌写进那里：",
            "  python -m marketops.interfaces.cli mint-token --principal claude-worker-1 "
            "--role researcher --audience <服务的 mcp 资源> --workspace <Scene> --expires-in 30d",
            "",
            "没有身份时本层**不派发**（退 2，delegate 没被启动）：让一个没有身份的",
            "worker 跑起来，它会以一个空工具面跑完一整轮，而报告上看不出这件事。",
        )

    not_in_ring: list[Path] = []
    wrong_role: list[tuple[str, frozenset[Role]]] = []
    expired: list[tuple[str, int]] = []
    eligible: list[tuple[TokenGrant, str, Path]] = []
    for path in candidates:
        token = _read_token(path)
        grant = None if token is None else ring.grant_for(token)
        if grant is None or token is None:
            not_in_ring.append(path)
            continue
        # 主体名会被当作锁的文件名用：非规范的名字会让两个主体落到同一把锁上，
        # 而那时「一个 worker 一个身份」是假的，且盘上两把锁看起来是同一把。
        check_canonical_id(grant.principal, "令牌环里这个主体的名字")
        if grant.roles != frozenset({Role.RESEARCHER}):
            wrong_role.append((grant.principal, grant.roles))
            continue
        if grant.expires_at <= int(time.time()):
            # 判据与措辞都不在这里的原则的唯一例外：这一格是**时间**，服务的判法
            # 是 ``auth.check_token`` 里那半句 ``grant.expires_at <= int(time.time())``。
            # 抄这个比较而不是抄一句话 —— 两处各写一遍必然漂移，而漂移的方向是
            # 「本层放行、服务拒绝」，症状又是那个 401。
            expired.append((grant.principal, grant.expires_at))
            continue
        eligible.append((grant, token, path))

    if not eligible:
        names = "、".join(ring.principal_names) or "（空）"
        lines = [
            f"环里解不出一个可用的研究员身份（环 {ring_path}，候选 {len(candidates)} 份）。",
            f"环里的主体：{names}",
        ]
        if not_in_ring:
            lines.append(
                f"{len(not_in_ring)} 份候选不在环里（它们多半是被一次轮换取代的旧令牌）："
                + "、".join(str(path.name) for path in not_in_ring)
            )
        if wrong_role:
            lines.append(
                "解出来但角色不是恰好 researcher："
                + "、".join(f"{name} {sorted(r.value for r in roles)}" for name, roles in wrong_role)
            )
        if expired:
            lines.append(
                "解出来但**已经过期**（服务会在认证那一步拒它们）："
                + "、".join(format_timestamp(moment) for _, moment in expired)
            )
        lines += [
            "",
            "令牌的有效性只写在环里，文件系统上看不见 —— 所以「文件在」不等于「令牌作数」。",
            "出路是重铸：`python -m marketops.interfaces.cli mint-token --rotate …`，",
            "把打出来的那一行令牌写进 vault 里那份文件（**旧的移到一边，不要就地覆盖**）。",
            "轮换既有生产令牌是一次要有授权的动作 —— 本层停下来，不替部署做这件事。",
        ]
        if wrong_role and not not_in_ring:
            lines.append(
                "角色要**恰好** researcher：多带 authorizer 的令牌让一个代理能自己走完"
                "「批准」与「投递」两半（§6 要分开的正是这两件事）。"
            )
        raise _refuse(*lines)

    held = 0
    for grant, token, path in sorted(eligible, key=lambda item: item[0].principal):
        lock_path = lock_root / f"{grant.principal}.lock"
        handle = _try_lock(lock_path)
        if handle is None:
            held += 1
            continue
        return Identity(
            principal=grant.principal,
            roles=grant.roles,
            token=token,
            token_file=path,
            lock_path=lock_path,
            lock_fd=handle,
        )

    raise _refuse(
        f"{len(eligible)} 个研究员身份**全部**被在飞的 worker 占着（每个身份一把 flock，见 {lock_root}）。",
        "",
        "一个 worker 一个身份是并发隔离的前提：共用身份时第二个 worker 的",
        "`get_assignment` 会拿到第一个的 assignment（`_claim` 的「是本人」那一支），",
        "而两边的报告都显示它读到了自己的那份。所以身份用尽时这里停下，",
        "而不是让第 N+1 个 worker 和别人共用一个身份。",
        f"（上面这一轮试了 {held} 把锁，每一把都已经被别人拿着。）",
        "",
        "身份数 = 这台机器真实的并发上限：`plugin/scripts/drivers.yml` 的",
        "`kinds.worker.cap` 必须 ≤ 身份数。要么加一个身份（vault 里多一份令牌 +",
        "环里多一个 researcher 主体），要么把 cap 降到身份数以内。",
    )
