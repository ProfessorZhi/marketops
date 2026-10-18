"""一个 worker 的身份从**环**里来（§15(a)）：选谁、凭什么、以及锁到什么时候。

## 这一组钉的是哪一句话

「worker 的启动器必须用格式 3 的令牌环，不再读那些被取代的旧令牌文件。」
（§15，逐字见 ``docs/DEPLOYMENT.md`` §2 与 ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md``
§G1：那两个 vault 文件是 9-22 铸的，而 9-23 的格式 3 环替换了它们 —— 服务一律 401，
委托方**完全没有** ``marketops`` 工具面。）

「不再读旧文件」有一个廉价的错解：把文件名换个模式、或者加一个时间戳比较。两者都不是
判据 —— 令牌作不作数**只写在环里**。所以这里每一条拒绝对应的都是「环说了什么」：

* 文件在、环里解不出 ⇒ 不可用（**不是**次选）；
* 环里解得出、角色不是恰好 researcher ⇒ 不可用；
* 环里解得出、角色对、但过期了 ⇒ 不可用（服务会在认证那一步拒它）；
* 都通过 ⇒ 拿一把 ``flock``，锁到本进程退出。

## 这里不测什么

不测环文件自己的形状（那是 ``tests/test_security_tokens.py``）、不测服务怎么判令牌
（``tests/test_mcp_auth_interop.py``）、也不测启动器怎么把它接起来
（``tests/test_research_worker.py`` 的「身份」那一节）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from marketops.domain.errors import ConfigError
from marketops.security.catalog import Role
from marketops.security.identity import (
    IDENTITY_FILE_GLOB,
    TOKEN_ENV,
    TOKEN_RING_ENV,
    claim_identity,
)
from marketops.security.tokens import mint
from tests.mcp_harness import ring_entry, ring_payload

VAULT_RING_NAME = "marketops-mcp-tokens.json"


def _file_name(index: int) -> str:
    """vault 里第 k 份研究员令牌的文件名（部署的命名约定）。"""
    return f"marketops-mcp-token-worker-{index}.txt"


def _vault(tmp_path: Path, *grants: tuple[str, tuple[Role, ...]], lifetime: int = 30 * 86400):
    """写一份 vault：一个格式 3 的环 + 每个主体一份令牌文件。

    返回 ``(环路径, {主体: 令牌}, {主体: 文件路径})``。文件名按**主体的顺序**编号
    —— 于是有一条用例可以故意让文件名与主体不一致（``_place`` 单独用）。
    """
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    entries = []
    tokens: dict[str, str] = {}
    files: dict[str, Path] = {}
    moment = time.time() + lifetime
    for index, (principal, roles) in enumerate(grants, start=1):
        token, digest = mint(principal, list(roles))
        tokens[principal] = token
        files[principal] = _place(vault, _file_name(index), token)
        entries.append(
            ring_entry(
                digest,
                principal,
                roles,
                expires_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment)),
            )
        )
    ring = vault / VAULT_RING_NAME
    ring.write_text(json.dumps(ring_payload(*entries), ensure_ascii=False), encoding="utf-8")
    return ring, tokens, files


def _place(vault: Path, name: str, token: str) -> Path:
    """把一份令牌放到 vault 里那个名字上。"""
    path = vault / name
    path.write_text(token + "\n", encoding="utf-8")
    return path


def _lock_root(tmp_path: Path) -> Path:
    """身份锁的落点：``<数据根>/state/identities``。"""
    return tmp_path / "runtime" / "state" / "identities"


def _claim(tmp_path: Path, ring: Path):
    return claim_identity(ring, lock_root=_lock_root(tmp_path))


def test_the_identity_comes_from_the_ring_not_from_the_file_name(tmp_path: Path) -> None:
    """文件叫 ``worker-9`` 而环里那个令牌属于 ``claude-worker-1`` ⇒ 身份是后者。

    文件名此前**就是**身份（启动器按它 echo 出主体名），而环里的主体名是另一件
    独立的事实。不一致时被记下来的名字必须是环里那一个 —— 否则记录里的身份是一个
    环里并不存在的名字，而它能对上的只有 vault 里的一个文件名。
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    token, digest = mint("claude-worker-1", [Role.RESEARCHER])
    _place(vault, "marketops-mcp-token-worker-9.txt", token)
    ring = vault / VAULT_RING_NAME
    ring.write_text(
        json.dumps(ring_payload(ring_entry(digest, "claude-worker-1", [Role.RESEARCHER]))),
        encoding="utf-8",
    )

    identity = _claim(tmp_path, ring)

    assert identity.principal == "claude-worker-1"
    assert identity.token == token
    assert identity.token_file.name == "marketops-mcp-token-worker-9.txt"
    assert identity.roles == frozenset({Role.RESEARCHER})


def test_a_file_that_is_not_in_the_ring_is_not_an_identity(tmp_path: Path) -> None:
    """解不出主体 ⇒ **不可用**，而且理由要点到「环里解不出」与出路（重铸）。

    这就是 §G1 的形状：文件是 9-22 铸的、环是 9-23 的，文件逐字节没变。旧启动器
    在这里照旧把死令牌交出去；本层的判据是「环里有没有它的摘要」。
    """
    ring, tokens, files = _vault(tmp_path, ("claude-worker-1", (Role.RESEARCHER,)))
    # 把文件换成一份**从来没进过环**的令牌（形状与真令牌一样）。
    dead, _ = mint("claude-worker-1", [Role.RESEARCHER])
    _place(files["claude-worker-1"].parent, files["claude-worker-1"].name, dead)

    with pytest.raises(ConfigError) as excinfo:
        _claim(tmp_path, ring)

    message = str(excinfo.value)
    assert "不在环里" in message
    assert files["claude-worker-1"].name in message, "要点名是哪一份文件"
    assert "claude-worker-1" in message, "要说环里有哪些主体"
    assert "mint-token" in message and "--rotate" in message, "要说清出路"
    assert tokens["claude-worker-1"] not in message, "拒绝里不得出现任何令牌"


def test_a_superseded_file_does_not_hide_a_valid_one(tmp_path: Path) -> None:
    """vault 里有一份死令牌、也有一份活的 ⇒ 用活的那一份（不以「有坏的」为拒绝理由）。

    这一条与上一条是一对：上一条钉「坏的不算数」，这一条钉「坏的不连坐」。一个
    「见到不认识的候选就停」的实现会让一次轮换把整个部署停掉，直到有人手工删文件。
    """
    ring, tokens, files = _vault(
        tmp_path, ("claude-worker-1", (Role.RESEARCHER,)), ("claude-worker-2", (Role.RESEARCHER,))
    )
    dead, _ = mint("claude-worker-2", [Role.RESEARCHER])
    _place(files["claude-worker-2"].parent, files["claude-worker-2"].name, dead)

    identity = _claim(tmp_path, ring)

    assert identity.principal == "claude-worker-1"
    assert identity.token == tokens["claude-worker-1"]


def test_an_empty_candidate_file_is_not_an_identity(tmp_path: Path) -> None:
    """空文件（有人把令牌挪走了、只留下那份文件）也归「环里解不出」。"""
    ring, _, files = _vault(tmp_path, ("claude-worker-1", (Role.RESEARCHER,)))
    path = files["claude-worker-1"]
    path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError, match="不在环里"):
        _claim(tmp_path, ring)


def test_no_candidate_file_at_all_says_which_glob_and_which_command(tmp_path: Path) -> None:
    """vault 里一份候选都没有 ⇒ 点名那个 glob 与铸令牌的命令。

    「找不到文件」与「文件在但作废」是两件事，处置也不同 —— 前者是部署还没放这一族
    文件，后者是轮换。合成一句会让运维先去查轮换，而那时一份文件都没有。
    """
    ring, _, _ = _vault(tmp_path, ("claude-worker-1", (Role.RESEARCHER,)))
    for path in ring.parent.glob(IDENTITY_FILE_GLOB):
        path.unlink()

    with pytest.raises(ConfigError) as excinfo:
        _claim(tmp_path, ring)

    assert IDENTITY_FILE_GLOB in str(excinfo.value)
    assert "mint-token" in str(excinfo.value)


def test_a_token_whose_role_is_not_exactly_researcher_is_refused(tmp_path: Path) -> None:
    """控制侧令牌放进 vault ⇒ 拒绝，并说明角色要**恰好** researcher。

    「多一个角色」在这个方向上是放宽：一个带着 ``authorizer`` 的研究员令牌，让
    **一个代理**能自己走完「批准」与「投递」两半，而那正是 §6 要分开的东西。
    """
    ring, _, _ = _vault(tmp_path, ("claude-control", (Role.CONTROLLER,)))

    with pytest.raises(ConfigError) as excinfo:
        _claim(tmp_path, ring)

    message = str(excinfo.value)
    assert "恰好 researcher" in message
    assert "claude-control" in message and "controller" in message


def test_a_researcher_token_that_also_carries_the_gate_is_refused(tmp_path: Path) -> None:
    """``researcher`` + ``authorizer`` 也拒 —— 角色是**相等**判据，不是包含判据。

    装载器只拒绝 ``controller`` + ``authorizer`` 同时出现的条目（那是两个方向叠在
    一个主体上），所以 ``researcher`` + ``authorizer`` 这一份是能装进环里的。它能
    装进环里，正是这一层要拦它的理由。
    """
    ring, _, _ = _vault(tmp_path, ("claude-worker-1", (Role.RESEARCHER, Role.AUTHORIZER)))

    with pytest.raises(ConfigError, match="恰好 researcher"):
        _claim(tmp_path, ring)


def test_an_expired_token_is_refused_here_not_left_to_the_service(tmp_path: Path) -> None:
    """到点的令牌 ⇒ 拒绝，且理由说的是**过期**（不是「不在环里」）。

    服务一定会在认证那一步拒它（``auth.check_token``）。所以放它过去的后果与 §G1
    逐字相同：委托方没有工具面、报告写着「server 未被挂载」。而有效期到点是**正常
    事件**（每条令牌都有），它不在这里判就一定会发生。
    """
    ring, _, _ = _vault(
        tmp_path, ("claude-worker-1", (Role.RESEARCHER,)), lifetime=-60
    )

    with pytest.raises(ConfigError) as excinfo:
        _claim(tmp_path, ring)

    message = str(excinfo.value)
    assert "已经过期" in message
    assert "不在环里" not in message, "另一句会把处置指到轮换上"


def test_the_lock_is_taken_under_the_data_root_named_after_the_principal(tmp_path: Path) -> None:
    """锁的路径 = ``<数据根>/state/identities/<主体名>.lock``，目录不存在时建出来。

    锁按**主体名**落盘而不是按文件序号：要独占的是那个身份，而序号是 vault 的排版。
    两个不同序号的文件若持有同一个主体的令牌，它们必须落进同一把锁 —— 按序号命名
    会让这一对看起来各占一把，而盘上没有任何地方看得出它们其实是同一个身份。
    """
    ring, _, _ = _vault(tmp_path, ("claude-worker-1", (Role.RESEARCHER,)))
    assert not _lock_root(tmp_path).exists(), "前提：锁目录还没有"

    identity = _claim(tmp_path, ring)

    assert identity.lock_path == _lock_root(tmp_path) / "claude-worker-1.lock"
    assert identity.lock_path.is_file()


def test_a_second_claim_takes_a_different_identity(tmp_path: Path) -> None:
    """两份身份、两次认领 ⇒ 两个不同的主体、两把不同的锁（§8 的并发上限）。"""
    ring, _, _ = _vault(
        tmp_path, ("claude-worker-1", (Role.RESEARCHER,)), ("claude-worker-2", (Role.RESEARCHER,))
    )

    first = _claim(tmp_path, ring)
    second = _claim(tmp_path, ring)

    assert {first.principal, second.principal} == {"claude-worker-1", "claude-worker-2"}
    assert first.lock_path != second.lock_path
    assert first.lock_fd != second.lock_fd


def test_an_identity_that_is_held_is_not_handed_out_twice(tmp_path: Path) -> None:
    """只剩一份身份、它被占着 ⇒ 拒绝（并说清身份数就是并发上限）。

    这一条同时钉住锁的**寿命**：关掉那个 fd 之后同一个身份又能被认领 ——
    也就是说这把锁没有「陈旧」这个状态，它随持有者进程而存在。
    """
    ring, _, _ = _vault(tmp_path, ("claude-worker-1", (Role.RESEARCHER,)))

    held = _claim(tmp_path, ring)
    with pytest.raises(ConfigError) as excinfo:
        _claim(tmp_path, ring)
    assert "被在飞的 worker 占着" in str(excinfo.value)
    assert str(_lock_root(tmp_path)) in str(excinfo.value)

    os.close(held.lock_fd)

    again = _claim(tmp_path, ring)
    assert again.principal == "claude-worker-1"


def test_a_principal_that_cannot_be_a_file_name_is_refused(tmp_path: Path) -> None:
    """环里的主体名要能当文件名用（锁按它命名）—— 不能就拒绝。

    ``mint-token`` 只校验主体名非空，所以带 ``/`` 的名字能进环。落盘成锁的时候
    它会变成一层路径：两把锁看起来是两个文件，而「一个身份一把锁」这句话要么
    不成立（父目录不存在时直接失败），要么指向一个谁的没听过的目录。
    """
    ring, _, _ = _vault(tmp_path, ("claude-worker/1", (Role.RESEARCHER,)))

    with pytest.raises(ValueError, match="名字") as excinfo:
        _claim(tmp_path, ring)

    assert "claude-worker-1" in str(excinfo.value), "那句拒绝要给得出规范形式"


def test_a_ring_that_is_not_there_names_the_variable(tmp_path: Path) -> None:
    """环文件不在 ⇒ ``TokenRing.from_file`` 的措辞（点名变量与出路），不再拼一遍。"""
    missing = tmp_path / "vault" / VAULT_RING_NAME

    with pytest.raises(ConfigError) as excinfo:
        _claim(tmp_path, missing)

    assert TOKEN_RING_ENV in str(excinfo.value)
    assert str(missing) in str(excinfo.value)


def test_the_payload_never_carries_the_token(tmp_path: Path) -> None:
    """进记录的那一格不含令牌 —— 令牌进的是**环境**，不是 ``run.json``。

    ``Identity`` 的 ``repr`` 也是为这一条重写的（与 ``TokenRing`` 同一个理由）：
    这个对象会出现在异常链与日志里，而默认的 dataclass repr 会把令牌打出来。
    """
    ring, tokens, _ = _vault(tmp_path, ("claude-worker-1", (Role.RESEARCHER,)))

    identity = _claim(tmp_path, ring)
    payload = identity.payload()
    body = json.dumps(payload, ensure_ascii=False)

    assert "token" not in {key.lower() for key in payload}
    assert tokens["claude-worker-1"] not in body
    assert tokens["claude-worker-1"] not in repr(identity)
    assert "claude-worker-1" in repr(identity), "但主体名要在（它才是那一格的用处）"


def test_the_token_environment_variable_is_named_once(tmp_path: Path) -> None:
    """交给 delegate 的那一格变量名只有一个定义处（``.mcp.json`` 与它同名）。

    这条判据的形状是「两处必须一致，而其中一处不在本仓」：工作区的 ``.mcp.json``
    写的是 ``${MARKETOPS_MCP_TOKEN}``，由子 claude 自己从环境展开。名字改了而
    ``.mcp.json`` 没改，症状是子 claude 拿到一个**未展开的字面量**当令牌 ——
    401，而两边的配置看起来都对。
    """
    assert TOKEN_ENV == "MARKETOPS_MCP_TOKEN"
    assert TOKEN_ENV != TOKEN_RING_ENV, "单数与复数是两个变量，别混"
