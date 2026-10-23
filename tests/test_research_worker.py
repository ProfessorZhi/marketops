"""派发的**入站那一端**：两个显式 id 怎么变成一轮研究 worker。

## 这一层是谁

一次显式调用是

    python -m marketops.interfaces.research_worker --work-item <id> --task <id>

它领一个研究员身份、建一个本轮唯一的隔离运行目录、把研究 prompt 交给部署指定的
delegate，然后把 delegate 的退出码原样交回去。参数**不是**一串要从外部驱动的 argv 里
反解的旗标：``--work-item`` / ``--task`` 就是两个 id，而 ``run_round`` 是同一段逻辑的
可复用入口（任务板那次循环也走它）。

## 为什么每一条拒绝都同时断言「对面没有被启动」

这一层没有中间态：配置接错了就**不能**跑，而它一旦跑起来就会真的花预算、真的写数据。
所以「拒绝」必须连着断言 delegate 没被启动 —— 只断言退出码非零的话，「先启动、然后
自己也报个错」的实现会绿，而它已经把 delegate 放进去了。

替身是一个**真的子进程**（测试自己写进 tmp 的脚本，解释器就是跑测试这个）：它把收到
的 argv、cwd 与令牌摘要记到盘上，退出码从 env 读。于是「转发的是部署拼的那串参数」
不是由一条断言说的，是由对面**照着自己收到了什么**说的；而「没被启动」就是那份记录
文件不存在 —— 不是一句本层的自述。

## 脚手架放在哪

替身、它的记录、vault、数据根与隔离根都在 ``tmp_path`` 下；``deploy/`` 是部署树的
替身 —— 本层**不该碰它**，所以有一条判据拿它逐字节比对。隔离根（真跑时是
``ARTIFACTS_ROOT`` 下面）与数据根分开放，正是为了让「本层读的根」与「本层写的根」
是同一条判据里的两格，而不是同一个字符串。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from marketops.adapters.build_stamp import LOADED_BUILD, BuildStamp, compare_builds
from marketops.adapters.server_build import publish_server_build, server_build_path
from marketops.interfaces.research_worker import (
    DATA_ROOT_ENV,
    DELEGATE_ARGS_ENV,
    DELEGATE_ENV,
    EXIT_REFUSED,
    RESEARCH_WORKER_PROMPT,
    WORKER_ROOT_ENV,
    build_receipt,
    main,
    research_prompt,
    run_round,
)
from marketops.security.catalog import Role
from marketops.security.identity import TOKEN_ENV, TOKEN_RING_ENV, claim_identity
from marketops.security.tokens import digest_of, mint
from tests.mcp_harness import ring_entry, ring_payload

WORK_ITEM_ID = "6d783321a9df9b2f2c6bf4ace0cd9b26"
TASK_ID = "MOPS-1b1b7f82-6d783321"
PRINCIPAL = "claude-worker-1"

#: 缺省的假令牌文件在 vault 里的名字（``identity.IDENTITY_FILE_GLOB`` 那一族）。
TOKEN_FILE_NAME = "marketops-mcp-token-worker-1.txt"


# ----------------------------------------------------------------------
# 脚手架
# ----------------------------------------------------------------------
def write_delegate(directory: Path) -> Path:
    """写一个真的子进程替身：记下 argv、cwd 与令牌摘要，退出码从 env 读。

    令牌**只记它的摘要**（``digest_of``，与环里落盘的那种形态同一个函数）：
    「delegate 拿到的是不是环里那一个」这条判据只要有摘要就成立，而把明文令牌写进
    一份记录 —— 哪怕是 tmp 里的假令牌 —— 是在给「令牌可以落在某处」立一个先例。
    """
    program = directory / "fake-delegate"
    program.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, json, os, sys\n"
        f"token = os.environ.get({TOKEN_ENV!r}, '')\n"
        "record = {'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "          'token_sha256': hashlib.sha256(token.encode()).hexdigest(),\n"
        "          'token_present': bool(token)}\n"
        "with open(os.environ['FAKE_DELEGATE_RECORD'], 'w', encoding='utf-8') as handle:\n"
        "    json.dump(record, handle, ensure_ascii=False)\n"
        "raise SystemExit(int(os.environ.get('FAKE_DELEGATE_EXIT', '0')))\n",
        encoding="utf-8",
    )
    program.chmod(0o755)
    return program


def write_mcp_config(
    directory: Path, servers: Iterable[str] = ("marketops",), *, name: str = "mcp.json"
) -> Path:
    """写一份 delegate 会看见的 MCP 配置。``servers`` 就是 ``mcpServers`` 的键集。"""
    path = directory / name
    path.write_text(
        json.dumps({"mcpServers": {server: {"type": "http"} for server in servers}}),
        encoding="utf-8",
    )
    return path


def default_delegate_args(config: Path) -> list[str]:
    """一个接对了的部署给 delegate 的那串参数（prompt 由本层追加，不在这里）。"""
    return ["--strict-mcp-config", "--mcp-config", str(config)]


def record_path(tmp_path: Path) -> Path:
    return tmp_path / "scaffold" / "delegate-record.json"


def recorded(tmp_path: Path) -> dict:
    """对面收到了什么。文件不在 ⇒ 它没有被启动到「写下这一行」那一步。"""
    path = record_path(tmp_path)
    assert path.is_file(), "delegate 没有留下记录 —— 它没有被启动"
    return json.loads(path.read_text(encoding="utf-8"))


def run_record(tmp_path: Path) -> dict:
    """那一轮写在自己隔离目录里的 ``run.json`` —— 本层唯一的产物。

    路径从 delegate 记下的 ``cwd`` 取，而不是重新算一遍：那正是「这一轮站在哪里」的
    读数，而本层写记录时用的也是同一个值。重算一遍会让这个助手在某天读到一个**别的**
    目录，而它照样能读出内容来。
    """
    body = Path(recorded(tmp_path)["cwd"]) / "run.json"
    return json.loads(body.read_text(encoding="utf-8"))


def run_records(tmp_path: Path) -> list[Path]:
    """``MARKETOPS_WORKER_ROOT`` 下所有 ``run.json`` —— 用来钉「只造了一轮」。"""
    root = tmp_path / "workers"
    return sorted(root.rglob("run.json")) if root.is_dir() else []


def runtime_root(tmp_path: Path) -> Path:
    """这个部署的运行态根 —— ``MARKETOPS_DATA_ROOT`` 指的就是它。

    与 ``tmp_path/workers``（隔离运行目录的根）**分开放**：真跑时后者是
    ``ARTIFACTS_ROOT`` 下面的一格，而测试把两者放在两个目录里，正是为了让「本层读的
    根」与「本层写的根」是同一条判据里的两格，而不是同一个字符串。

    身份锁落在它的 ``state/identities/`` 下 —— 被保护的资源（run 级状态库）就在同一个
    根里，所以锁跟着数据根走。
    """
    return tmp_path / "runtime"


def identity_lock(tmp_path: Path, principal: str = PRINCIPAL) -> Path:
    """那个身份在本部署上的锁。"""
    return runtime_root(tmp_path) / "state" / "identities" / f"{principal}.lock"


def vault_dir(tmp_path: Path) -> Path:
    """凭据库 —— 环与令牌文件都在这里（真跑时是部署方给的那个 vault 目录）。"""
    return tmp_path / "vault"


def vault_token(tmp_path: Path) -> str:
    """vault 里那份令牌文件的内容（判据只拿它的摘要去比）。"""
    return (vault_dir(tmp_path) / TOKEN_FILE_NAME).read_text(encoding="utf-8").strip()


def write_vault(
    tmp_path: Path,
    *,
    principal: str = PRINCIPAL,
    roles: tuple[Role, ...] = (Role.RESEARCHER,),
    in_ring: bool = True,
) -> Path:
    """写一份 vault（格式 3 的环 + 一份研究员令牌文件），返回环的路径。

    ``in_ring=False`` 写出一份**从来没进过环**的令牌：文件逐字节有效、形状与真令牌
    一样，而环里没有它的摘要。那正是「文件在」不等于「令牌作数」的形状 —— 旧启动器
    在这里照旧把死令牌交出去，本层在派发之前停下。
    """
    vault = vault_dir(tmp_path)
    vault.mkdir(parents=True, exist_ok=True)
    token, digest = mint(principal, list(roles))
    if not in_ring:
        digest = digest_of(mint(principal, list(roles))[0])
    (vault / TOKEN_FILE_NAME).write_text(token + "\n", encoding="utf-8")
    ring = vault / "marketops-mcp-tokens.json"
    ring.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, principal, roles)),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ring


def server_build_file(tmp_path: Path) -> Path:
    return server_build_path(runtime_root(tmp_path))


def write_server_build(tmp_path: Path, payload: dict) -> Path:
    """写一份**指定形状**的服务构建回执。

    默认那一份走生产路径（``publish_server_build``，见 ``Run``）；这个助手是给那些
    要写出**坏掉的**文件的测试用的 —— 而写坏正是 ``publish_server_build`` 拒绝做的
    事，所以它们不能借它。
    """
    path = server_build_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def a_stamp(**overrides: object) -> BuildStamp:
    """一份构建指纹，缺省与 ``LOADED_BUILD`` 只差 ``read_at``。

    ``read_at`` 刻意不同：服务那一份是**另一个进程**在另一个时刻读的，而一份抄了
    本层 ``read_at`` 的假回执会让「服务报的是什么」这个问题在测试里被抹平。
    """
    base: dict[str, object] = {
        "package_file": LOADED_BUILD.package_file,
        "source_tree": LOADED_BUILD.source_tree,
        "revision": LOADED_BUILD.revision,
        "dirty": LOADED_BUILD.dirty,
        "read_at": "2026-09-24T00:00:00+00:00",
        "errors": {},
    }
    base.update(overrides)
    return BuildStamp(**base)  # type: ignore[arg-type]


def snapshot(root: Path) -> dict[str, bytes]:
    """一棵树的逐字节快照（只比较文件；目录随文件出现）。"""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class Run:
    """一次调用的输入（delegate、参数、两个根、环），各有缺省。

    缺省值表示**一个接对了的部署**：delegate 参数里恰好开出 ``marketops``、隔离根是
    一个绝对路径、数据根上已经有一份与服务一致的构建回执、环里有一个研究员主体。
    要测拒绝的那几条，就把对应的那一格换掉 —— 换掉之后跑出来的拒绝，与一个真的
    接错了的部署跑出来的是同一句。
    """

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        delegate: Path | str | None = "__default__",
        delegate_args: list[str] | str | None = "__default__",
        worker_root_env: str | None = "__default__",
        data_root_env: str | None = "__default__",
        server_build: str | None = "__default__",
        ring_env: str | None = "__default__",
        ring_in_ring: bool = True,
        launcher_token: str | None = None,
        item_id: str = WORK_ITEM_ID,
        task_id: str = TASK_ID,
    ) -> None:
        self.tmp_path = tmp_path
        self.deploy = tmp_path / "deploy"
        self.scaffold = tmp_path / "scaffold"
        self.worker_root = tmp_path / "workers"
        self.runtime_root = runtime_root(tmp_path)
        self.item_id = item_id
        self.task_id = task_id
        self.deploy.mkdir(parents=True, exist_ok=True)
        self.scaffold.mkdir(parents=True, exist_ok=True)
        # 本层不读 cwd（隔离目录由绝对根拼出来），但仍然站进部署树：一条「相对根被拒绝」
        # 的判据要能看出「它本来会解析到这里」，而不是在一个空目录里恒真。
        monkeypatch.chdir(self.deploy)
        monkeypatch.setenv("FAKE_DELEGATE_RECORD", str(record_path(tmp_path)))
        monkeypatch.delenv("FAKE_DELEGATE_EXIT", raising=False)

        self.delegate_program: str | None = None
        if delegate == "__default__":
            self.delegate_program = str(write_delegate(self.scaffold))
            monkeypatch.setenv(DELEGATE_ENV, self.delegate_program)
        elif delegate is None:
            monkeypatch.delenv(DELEGATE_ENV, raising=False)
        else:
            self.delegate_program = str(delegate)
            monkeypatch.setenv(DELEGATE_ENV, self.delegate_program)

        self.mcp_config = write_mcp_config(self.scaffold)
        self.delegate_args: list[str] | str | None
        if delegate_args == "__default__":
            self.delegate_args = default_delegate_args(self.mcp_config)
            monkeypatch.setenv(DELEGATE_ARGS_ENV, json.dumps(self.delegate_args))
        elif delegate_args is None:
            self.delegate_args = None
            monkeypatch.delenv(DELEGATE_ARGS_ENV, raising=False)
        elif isinstance(delegate_args, str):
            self.delegate_args = delegate_args
            monkeypatch.setenv(DELEGATE_ARGS_ENV, delegate_args)
        else:
            self.delegate_args = list(delegate_args)
            monkeypatch.setenv(DELEGATE_ARGS_ENV, json.dumps(self.delegate_args))

        if worker_root_env == "__default__":
            monkeypatch.setenv(WORKER_ROOT_ENV, str(self.worker_root))
        elif worker_root_env is None:
            monkeypatch.delenv(WORKER_ROOT_ENV, raising=False)
        else:
            monkeypatch.setenv(WORKER_ROOT_ENV, worker_root_env)

        if server_build == "__default__":
            publish_server_build(self.runtime_root, stamp=LOADED_BUILD)

        if data_root_env == "__default__":
            monkeypatch.setenv(DATA_ROOT_ENV, str(self.runtime_root))
        elif data_root_env is None:
            monkeypatch.delenv(DATA_ROOT_ENV, raising=False)
        else:
            monkeypatch.setenv(DATA_ROOT_ENV, data_root_env)

        self.ring: Path | None = None
        if ring_env == "__default__":
            self.ring = write_vault(tmp_path, in_ring=ring_in_ring)
            monkeypatch.setenv(TOKEN_RING_ENV, str(self.ring))
        elif ring_env is None:
            monkeypatch.delenv(TOKEN_RING_ENV, raising=False)
        else:
            monkeypatch.setenv(TOKEN_RING_ENV, ring_env)

        # 启动器**已经不再** export 令牌了，但部署是分批上线的：缺省把它清掉，免得跑测试
        # 这个 shell 里恰好有一份把判据弄成一条恒真的真话。
        monkeypatch.delenv(TOKEN_ENV, raising=False)
        if launcher_token is not None:
            monkeypatch.setenv(TOKEN_ENV, launcher_token)

    def __call__(self) -> int:
        return main(["--work-item", self.item_id, "--task", self.task_id])


# ----------------------------------------------------------------------
# 通路：delegate 收到的是部署参数 + 本轮的研究 prompt
# ----------------------------------------------------------------------
def test_the_delegate_gets_the_deploy_args_then_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """部署拼的那串参数**原样原序**过去，后面追加 ``-p`` 与研究 prompt。

    两个方向都要断：**少了**任何一项（比如顺手重建一份参数表时漏掉 ``--mcp-config``）
    会让 delegate 拿不到市场侧的工具；**换了**值，等于绕开部署在配置里做的决定。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    got = recorded(tmp_path)["argv"]
    expected = [*run.delegate_args, "-p", research_prompt(WORK_ITEM_ID)]  # type: ignore[misc]
    assert got == expected


def test_the_prompt_names_the_work_item_and_carries_no_guidance_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prompt 是**过程**，不是内容：指引走 ``get_assignment``，不走任何外部正文。

    这一条挡的是「把任务板上的正文塞进 prompt」那种实现。它看起来更方便，实际让
    worker 按一份**没有版本、没有租约、也没有证据链**的文本干活，而板面与账本从此
    各说各话。所以这里先钉固定形状（等于常量 + 一个 id），再钉盘上那些像正文的东西
    一个字都没进 prompt。
    """
    run = Run(tmp_path, monkeypatch)
    sentinel = "GUIDANCE-BODY-THAT-MUST-NOT-REACH-THE-DELEGATE"
    (run.deploy / "assignment.md").write_text(sentinel, encoding="utf-8")
    (run.deploy / "board").mkdir(exist_ok=True)
    (run.deploy / "board" / f"{TASK_ID}.md").write_text(sentinel, encoding="utf-8")

    assert run() == 0

    prompt = recorded(tmp_path)["argv"][-1]
    assert prompt == RESEARCH_WORKER_PROMPT.format(work_item_id=WORK_ITEM_ID)
    assert prompt == research_prompt(WORK_ITEM_ID)
    assert WORK_ITEM_ID in prompt
    assert "get_assignment" in prompt
    assert sentinel not in prompt


def test_run_round_is_the_reusable_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_round`` 与 ``main`` 走的是同一段「核对 → 落位 → 递出去」。

    任务板那次循环直接调它。两处各写一遍的话，漂移的那一天会出现「循环起的那一轮
    不核对构建」，而这种形状在报告上完全看不出来。
    """
    Run(tmp_path, monkeypatch)

    assert run_round(WORK_ITEM_ID, task_id=TASK_ID) == 0

    assert Path(recorded(tmp_path)["cwd"]).is_dir()
    assert run_record(tmp_path)["work_item_id"] == WORK_ITEM_ID
    assert len(run_records(tmp_path)) == 1


def test_the_delegate_runs_in_a_per_round_isolated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd **不是**隔离根、也不是部署树，而是本轮新造的那个目录。

    让 worker 站在部署树上，它读得到部署配置、读得到兄弟任务的记录、也读得到上一轮
    留下的回执；而 ``Read``/``Grep`` 不在部署的 deny 名单上 —— 所以「站在哪里」就是
    那条边界本身。目录的形状也被钉住：两个真正持有的身份 + UTC 时刻 + pid。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    cwd = Path(recorded(tmp_path)["cwd"]).resolve()
    assert cwd.is_dir()
    assert cwd != run.worker_root.resolve()
    assert cwd != run.deploy.resolve()
    assert cwd != run.runtime_root.resolve()
    assert cwd.is_relative_to(run.worker_root.resolve()), cwd

    parts = cwd.relative_to(run.worker_root.resolve()).parts
    assert parts[0] == WORK_ITEM_ID
    assert parts[1] == TASK_ID
    assert re.fullmatch(r"\d{8}T\d{12}Z-\d+", parts[2]), parts[2]
    assert parts[2].endswith(f"-{os.getpid()}")


def test_two_rounds_do_not_share_a_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一件活派两次 ⇒ 两个目录。

    共用目录的话，第二轮会读到上一轮留下的 scratch 与记录 —— 而「这一轮在盘上留下了
    什么」正是隔离要能回答的那个问题。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0
    first = Path(recorded(tmp_path)["cwd"])
    second_record = tmp_path / "scaffold" / "delegate-record-2.json"
    monkeypatch.setenv("FAKE_DELEGATE_RECORD", str(second_record))

    assert run() == 0

    second = Path(
        json.loads(second_record.read_text(encoding="utf-8"))["cwd"]
    )
    assert first != second
    assert first.is_dir() and second.is_dir()
    assert (first / "run.json").is_file() and (second / "run.json").is_file()
    assert not second.is_relative_to(first)


def test_the_run_record_says_what_this_round_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run.json`` 是本层唯一的产物：两个 id、两个目录、身份、以及核对过的工具面。

    ``cwd`` 与「记录自己写在哪」是同一格；``mcp.servers`` 必须是**放行时的那一个
    读数** —— 从配置再读一次会在某一天写出「放行的是 A、记下来的是 B」。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    recorded_body = recorded(tmp_path)
    body = run_record(tmp_path)
    assert body["task_id"] == TASK_ID
    assert body["work_item_id"] == WORK_ITEM_ID
    assert Path(body["cwd"]).resolve() == Path(recorded_body["cwd"]).resolve()
    assert body["delegate"] == run.delegate_program
    assert body["started_at"].endswith("+00:00")
    assert body["mcp"]["servers"] == ["marketops"]
    assert body["mcp"]["strict"] is True
    assert body["loaded_marketops"]["source_tree"] == LOADED_BUILD.source_tree
    assert body["loaded_marketops"]["revision"] == LOADED_BUILD.revision
    assert body["why_no_attempt_id"], "要写清为什么目录不按 attempt_id 命名"
    # 隔离目录里只有这一份记录：本轮不该顺手往那里放别的东西。
    assert sorted(p.name for p in Path(recorded_body["cwd"]).iterdir()) == ["run.json"]


# ----------------------------------------------------------------------
# 退出码即裁决
# ----------------------------------------------------------------------
@pytest.mark.parametrize("code", [0, 7])
def test_the_delegate_exit_code_is_handed_back_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """0 出去 0 回来、7 出去 7 回来 —— 本层不解释、不替换、不吞。

    给它一个「非零就归 1」的规范化，外部账本里记的结局就与 delegate 真正的结局分家，
    而那条记录是**唯一**一份「这一轮为什么没成」。
    """
    run = Run(tmp_path, monkeypatch)
    # 退出码在**构造之后**设：``Run`` 会清掉上一轮可能留下的那一格，好让缺省真的是 0。
    monkeypatch.setenv("FAKE_DELEGATE_EXIT", str(code))

    assert run() == code


# ----------------------------------------------------------------------
# 拒绝矩阵，每一条都要「对面没有被启动」
# ----------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_deployment_that_does_not_name_a_delegate_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    capsys: pytest.CaptureFixture,
) -> None:
    """delegate 没有缺省值：没命名就拒绝，并且**说出来缺的是哪一个**。

    「跑了但谁也没干活」与「报错说不知道该跑谁」退出码都是非零，而运维读到的是完全
    不同的两句话。
    """
    run = Run(tmp_path, monkeypatch, delegate=value)

    code = run()

    assert code == EXIT_REFUSED
    assert DELEGATE_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()
    assert run_records(tmp_path) == []


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_deployment_that_does_not_name_a_worker_root_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    capsys: pytest.CaptureFixture,
) -> None:
    """隔离根没有缺省值：没命名就拒绝，并点名那个变量。

    一个有缺省值的根最可能的取值就是「部署树里面」，而那正是这一格要关掉的地方 ——
    且它会看起来完全成功。
    """
    run = Run(tmp_path, monkeypatch, worker_root_env=value)

    code = run()

    assert code == EXIT_REFUSED
    assert WORKER_ROOT_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()
    assert run_records(tmp_path) == []


def test_a_relative_worker_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """相对路径 ⇒ 拒绝。相对值会解析到调用方站的那棵树里 —— 部署树里面。

    那样的话目录建出来了、记录写进去了、delegate 也在跑，而隔离一点都没生效。
    （这条判据的第一版可能绿着，因为 ``Path("workers")`` 在 tmp 里也建得出来；绿
    的原因不是「相对路径安全」，是「判据没问它是不是绝对的」。）
    """
    run = Run(tmp_path, monkeypatch, worker_root_env="workers")

    code = run()

    assert code == EXIT_REFUSED
    assert WORKER_ROOT_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()
    assert not (run.deploy / "workers").exists(), "相对根不该在部署树里被建出来"


def test_a_deployment_without_a_data_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``MARKETOPS_DATA_ROOT`` 没配 ⇒ 拒绝，并点名缺的是哪个变量。

    没有这一格就没有第二个读数，也就无从核对构建与落身份锁 —— 而「无从核对」与
    「核对过、一致」在这一轮的记录里长得一样。所以它不是「跳过这一条检查」，是拒绝。
    """
    run = Run(tmp_path, monkeypatch, data_root_env=None)

    code = run()

    assert code == EXIT_REFUSED
    assert DATA_ROOT_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()
    assert run_records(tmp_path) == []


def test_a_deployment_without_a_token_ring_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``MARKETOPS_MCP_TOKENS`` 没配 ⇒ 拒绝，点名缺的变量，**delegate 不动**。

    「没有环」与「环里没有可用身份」是两件事（前者是部署还没接线、后者是轮换），
    所以这里除了拒绝还要判 delegate 没有被启动：启动一个没有身份的 worker，它会以
    一个空工具面跑完一整轮，而两边的报告都看不出这件事。
    """
    run = Run(tmp_path, monkeypatch, ring_env=None)

    code = run()

    assert code == EXIT_REFUSED
    assert TOKEN_RING_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()
    assert run_records(tmp_path) == []


@pytest.mark.parametrize("item", ["..", ".", "a/b", "a\\b", ""])
def test_an_item_id_that_cannot_be_a_path_component_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, item: str
) -> None:
    """工作单元 id 会被当成**路径成分**用，所以这些取值必须拒绝。

    * ``..`` 能上跳，且不含 ``check_slug_safe`` 拦的任何字符；
    * ``.`` 同上，只是它跳回原地、看起来无害 —— 它会与「同一时刻里的下一次派发」撞名，
      而撞名在这里的表现是第二次派发整个失败；
    * ``a/b`` 与 ``a\\b`` 含路径分隔符，一条被拼成两级目录、另一条在别的平台上被当成
      分隔符；
    * 空串让路径少一级，而「少一级」正是隔离根本身。

    断三件事：退出码、delegate 没被启动、以及**隔离根根本没被建出来**。
    """
    run = Run(tmp_path, monkeypatch, item_id=item)

    code = run()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()
    assert not run.worker_root.exists(), "拒绝发生在落位之前：隔离根不该出现"


@pytest.mark.parametrize("task", ["..", ".", "a/b", "a\\b", ""])
def test_a_task_id_that_cannot_be_a_path_component_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    """任务 id 与工作单元 id 是**同一个**路径成分问题，不能只守一半。

    外部任务 id 由任务板给，形状不受本仓约束；只校验工作单元 id 的话，``--task ..``
    会让目录退到工作单元那一层，两轮之间就共用了。
    """
    run = Run(tmp_path, monkeypatch, task_id=task)

    code = run()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()
    assert not run.worker_root.exists()


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}', '["--mcp-config", 3]'])
def test_delegate_args_that_are_not_a_string_array_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """``MARKETOPS_RESEARCH_DELEGATE_ARGS`` 必须是一个**字符串**数组。

    靠空格切一串字符串在带空格的路径上有一整类静默错法；而 JSON 里混进非字符串
    元素时，如果放过去，它会变成一次 ``TypeError`` —— 那是本层的崩溃，不是一次
    部署层面的拒绝。
    """
    run = Run(tmp_path, monkeypatch, delegate_args=raw)

    code = run()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()


def test_a_delegate_that_cannot_be_spawned_is_not_reported_as_a_failed_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """delegate 指到一个不存在的程序 ⇒ 拒绝，**不**把它报成 delegate 自己失败。

    两者都让调用方记下一次失败，但前者是本层的配置错、后者是研究真的没成 —— 混在
    一起，「这个工作单元反复失败」会指向错误的方向。这一条与别处不同：落位已经
    发生（隔离目录与记录都在），所以断言的是「delegate 没起来」，不是「盘上没东西」。
    """
    run = Run(tmp_path, monkeypatch, delegate=tmp_path / "not-a-program")

    code = run()

    assert code == EXIT_REFUSED
    assert "起不来" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()
    records = run_records(tmp_path)
    assert len(records) == 1, "落位已经发生：本轮目录与记录在，但 delegate 没被启动"


# ----------------------------------------------------------------------
# 工具面：这串参数给子 agent 开出什么，本层说了算
# ----------------------------------------------------------------------
# 这一节钉的是「**在提示词之外**放宽的权限」。prompt 里写了「不要动任务板」，而那句话
# 的执行器是一个语言模型；这几条钉的是让它**看不到**别的工具。三条各对应一种真实
# 形状：多一个、少一个、以及那个旗标整个消失。三条都要「对面没有被启动」。
def test_extra_mcp_servers_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``mcpServers`` 里多一个 server ⇒ 拒绝，且 stderr 点名多出来的是哪一个。

    多出来的工具面是**在提示词之外**放宽的权限：worker 被要求不许动任务板，而一个
    顺手挂上的浏览器/文件系统 server 让它「能」动 —— 两件事不是同一层。
    """
    run = Run(tmp_path, monkeypatch)
    widened = write_mcp_config(run.scaffold, ("marketops", "playwright"), name="widened.json")
    run.delegate_args = default_delegate_args(widened)
    monkeypatch.setenv(DELEGATE_ARGS_ENV, json.dumps(run.delegate_args))

    code = run()

    assert code == EXIT_REFUSED
    assert "playwright" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()
    assert not run.worker_root.exists()


def test_a_config_without_marketops_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """清单读得出来、但里面没有 ``marketops`` ⇒ 拒绝（缺的那一侧）。

    这一半不是「安全」而是「能不能干活」：没有市场工具，这一轮无论如何都完不成，
    而它会以「模型跑了、什么也没交」的样子花掉一次额度与一轮时间。
    """
    run = Run(tmp_path, monkeypatch)
    empty = write_mcp_config(run.scaffold, (), name="empty.json")
    run.delegate_args = default_delegate_args(empty)
    monkeypatch.setenv(DELEGATE_ARGS_ENV, json.dumps(run.delegate_args))

    code = run()

    assert code == EXIT_REFUSED
    assert "marketops" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_delegate_args_without_mcp_config_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """参数里没有 ``--mcp-config`` ⇒ 拒绝，而不是「读出来是空的，于是放行」。

    一个不写明工具面的子 agent 会与宿主上一切可用来源合并，而这条参数上**看不出**
    它合并了什么。本层被授予的权力是「核对工具面」，不是「假设部署已经核对过」。
    """
    run = Run(tmp_path, monkeypatch, delegate_args=["--strict-mcp-config"])

    code = run()

    assert code == EXIT_REFUSED
    assert "--mcp-config" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_an_mcp_config_flag_without_a_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``--mcp-config`` 是最后一个元素 ⇒ 拒绝，而不是把这个旗标当成路径去读。"""
    run = Run(tmp_path, monkeypatch, delegate_args=["--mcp-config"])

    code = run()

    assert code == EXIT_REFUSED
    assert "没有路径" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_an_mcp_config_file_that_is_not_there_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``--mcp-config`` 指向一个不存在的文件 ⇒ 拒绝（读不出来就是拒绝）。

    文件不在与文件坏掉在处置上都指向部署，但在措辞上要带上路径 —— 否则运维不知道
    这一层读的是哪一个文件。
    """
    run = Run(tmp_path, monkeypatch)
    missing = run.scaffold / "missing.json"
    run.delegate_args = ["--mcp-config", str(missing)]
    monkeypatch.setenv(DELEGATE_ARGS_ENV, json.dumps(run.delegate_args))

    code = run()

    assert code == EXIT_REFUSED
    assert str(missing) in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_an_mcp_config_that_is_not_readable_json_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """配置文件不是 JSON ⇒ 拒绝。读不懂的配置不能被当成「没有多出来的 server」。"""
    run = Run(tmp_path, monkeypatch)
    broken = run.scaffold / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    run.delegate_args = ["--mcp-config", str(broken)]
    monkeypatch.setenv(DELEGATE_ARGS_ENV, json.dumps(run.delegate_args))

    code = run()

    assert code == EXIT_REFUSED
    assert str(broken) in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_a_config_without_an_mcp_servers_object_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``mcpServers`` 不是对象 ⇒ 拒绝。空对象是合法的「一个都没有」，缺键不是。"""
    run = Run(tmp_path, monkeypatch)
    shapeless = run.scaffold / "shapeless.json"
    shapeless.write_text(json.dumps({"mcpServers": ["marketops"]}), encoding="utf-8")
    run.delegate_args = ["--mcp-config", str(shapeless)]
    monkeypatch.setenv(DELEGATE_ARGS_ENV, json.dumps(run.delegate_args))

    code = run()

    assert code == EXIT_REFUSED
    assert "mcpServers" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


# ----------------------------------------------------------------------
# 构建核对：本层加载的那份代码，与服务加载的那份
# ----------------------------------------------------------------------
# 这一节钉的是「记录里那一格 ``loaded_marketops`` 说的是**本层**，而干活的是服务」。
# 两边加载了两份代码时，链上没有任何一处会报错：delegate 照常调工具，服务照常答，
# 而这一轮每一条发现都由一份与服务不同的代码产生 —— 记录里写着本层那一份，于是报告
# 主张了一件不成立的事。
def test_a_data_root_where_no_service_published_its_build_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """服务没报过自己的构建 ⇒ 拒绝，并说出那个文件在哪。

    文件不在有两种情形（服务没起来过、服务起的时候用的是另一个数据根），而两种都
    意味着本层手里没有可比的东西。说成「构建不一致」会把一种部署接线问题说成一个
    代码问题 —— 处置完全不同。
    """
    run = Run(tmp_path, monkeypatch, server_build=None)

    code = run()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert str(server_build_file(tmp_path)) in err
    assert "还没有服务报过" in err
    assert not record_path(tmp_path).exists()
    assert run_records(tmp_path) == []


def test_worker_and_service_loading_different_trees_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """两侧是两个检出 ⇒ 拒绝，而且**两棵树都报出来**。

    这是本判据要挡的那件事的主要形状：两个进程各自从一个检出里 import。只报出
    「不一致」而不报是哪两棵树，运维还得自己去问服务站在哪里 —— 而问的地方就是
    这里，因为除了这一行没有别的地方说。
    """
    elsewhere = "/somewhere/else/marketops"
    write_server_build(tmp_path, {**a_stamp(source_tree=elsewhere).payload(), "pid": 4242})
    run = Run(tmp_path, monkeypatch, server_build=None)

    code = run()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert elsewhere in err
    assert LOADED_BUILD.source_tree in err
    assert not record_path(tmp_path).exists()


def test_a_service_left_running_on_an_older_commit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """同一棵树、服务停在别的提交上 ⇒ 拒绝。

    这一格是「一个起了很久的进程」：树是当前这棵，而它 import 的时候树还在上一个
    提交上。``revision`` 那一格存在的全部意义就是能判出这一条。
    """
    assert LOADED_BUILD.revision is not None, (
        "这条判据的前提是这份代码从一个 git 检出里加载 —— revision 读不出来时它测的"
        "是另一条路（UNPROVABLE），而那条路有自己的测试"
    )
    older = "0" * 40
    write_server_build(tmp_path, {**a_stamp(revision=older).payload(), "pid": 4242})
    run = Run(tmp_path, monkeypatch, server_build=None)

    code = run()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert older in err
    assert LOADED_BUILD.revision in err
    assert not record_path(tmp_path).exists()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[1, 2, 3]", "不是一个对象"),
        ("{}", "不是一份构建指纹"),
        ("{not json", "不是一份 JSON"),
    ],
)
def test_a_build_receipt_that_is_not_a_build_stamp_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    raw: str,
    expected: str,
) -> None:
    """文件在、但不是一个对象 / 缺键 / 不是 JSON ⇒ 拒绝，并带上那几格的原话。

    「读不出来」与「比下来不一致」是两句不同的话：前者的处置是去看那个文件（谁写的、
    什么时候写的），后者是去改部署。合成一句之后，一次磁盘故障会在报告里变成一个
    代码版本问题。
    """
    run = Run(tmp_path, monkeypatch, server_build=None)
    path = server_build_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")

    code = run()

    assert code == EXIT_REFUSED
    assert expected in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_the_refusal_repeats_the_verdict_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """stderr 上那句话与 ``compare_builds`` 给出的是**同一句**。

    判据与措辞都只有一份（``adapters/build_stamp.py``）。两处各拼一遍的那一天，
    日志上说的与 ``run.json`` 里记的会是两句不同的话，而工单里贴的永远是先跑到的
    那一句。
    """
    elsewhere = "/somewhere/else/marketops"
    write_server_build(tmp_path, {**a_stamp(source_tree=elsewhere).payload(), "pid": 4242})
    verdict = compare_builds(LOADED_BUILD, a_stamp(source_tree=elsewhere))
    run = Run(tmp_path, monkeypatch, server_build=None)

    assert not verdict.agrees
    assert run() == EXIT_REFUSED
    assert verdict.message in capsys.readouterr().err


def test_an_uncommitted_change_is_reported_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """服务报的那一份带未提交改动 ⇒ **放行**，但那一行要说出来。

    这条刻意钉的是一个**不拒绝**的形状：把 ``dirty`` 提成拒绝的理由会让工作树里跑不
    出任何一轮研究，而本仓的验收要求测试部署真的跑。所以它由两半组成：放行，**且**
    「未提交改动」这件事在回执上看得见。
    """
    write_server_build(tmp_path, {**a_stamp(dirty=True).payload(), "pid": os.getpid()})
    run = Run(tmp_path, monkeypatch, server_build=None)

    assert run() == 0
    assert recorded(tmp_path)["cwd"], "放行的那一轮 delegate 真的被启动了"
    assert "有未提交改动" in capsys.readouterr().err


def test_the_run_records_the_builds_it_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一次成功的派发留下的记录里，**写着它核对过**，以及核对的是哪一个进程。

    这一格是 ``loaded_marketops`` 的对照：那一格说的是本层加载的代码，两次（核对过、
    没核对过）都写着同一个值 —— 所以「核对过没有」这件事只能由这一格答。``pid`` 与
    ``read_at`` 是「核对的是哪一个服务实例」的唯一凭据。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    body = run_record(tmp_path)["build_consistency"]
    assert body["verdict"] == "agrees"
    assert body["message"] == ""
    assert body["server_build_path"] == str(server_build_file(tmp_path))
    assert body["server_pid"] == os.getpid(), (
        "这一份回执是测试进程自己 publish 的，所以 pid 就是它 —— 换成别的值说明记录里"
        "那一格不是写它的那个进程"
    )
    assert body["server_read_at"] == LOADED_BUILD.read_at


# ----------------------------------------------------------------------
# 身份：从环里来，不从文件名来
# ----------------------------------------------------------------------
# 选身份、解主体、拿锁那几条判据在 ``tests/test_security_identity.py``。这里要答的是
# 另外三个问题：身份有没有进产物、delegate 手里那一份令牌是不是环解出来的那一份、
# 以及接错的部署会不会**在没有身份的情况下照样把 delegate 启动起来**。
def test_the_run_records_which_identity_it_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """这一轮的产物里写着**是哪一个身份**：主体、角色、令牌文件、锁（不含令牌）。

    ``principal`` 与 ``token_file`` 必须**一起**记：环里的主体名与 vault 里的文件名
    是两件独立的事实，只记文件名会让一次「环里换了主体」在记录里看不出来。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    identity = run_record(tmp_path)["identity"]
    assert identity["principal"] == PRINCIPAL
    assert identity["roles"] == ["researcher"]
    assert identity["token_file"].endswith(TOKEN_FILE_NAME)
    assert identity["lock"] == str(identity_lock(tmp_path))
    assert identity["launcher_token_replaced"] is False
    assert vault_token(tmp_path) not in json.dumps(identity, ensure_ascii=False)
    assert identity_lock(tmp_path).is_file(), "锁真的落在数据根下"
    assert str(identity_lock(tmp_path)) in capsys.readouterr().err


def test_the_delegate_gets_the_token_the_ring_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delegate 环境里那一份 = vault 里那一份（按摘要比，明文不进任何记录）。

    令牌的有效性只写在环里；「文件在」不等于「令牌作数」。按摘要比而不是逐字节：
    写进记录的只有摘要，「令牌可以落在某处」这个先例连测试文件都不给。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    got = recorded(tmp_path)
    assert got["token_present"] is True, "delegate 拿不到令牌时工具面整个是空的"
    assert got["token_sha256"] == digest_of(vault_token(tmp_path))


def test_a_vault_whose_token_is_not_in_the_ring_refuses_before_the_delegate_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """vault 里那份文件被一次轮换取代了 ⇒ 拒绝，且拒绝里不含令牌。

    这是最贵的一格：旧启动器照旧把死令牌交出去，worker 以一个空工具面跑完一整轮，
    报告上写着「server 未被挂载」。本层在**派发之前**停下，所以那一轮的成本是零。
    """
    run = Run(tmp_path, monkeypatch, ring_in_ring=False)

    code = run()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "不在环里" in err
    assert TOKEN_FILE_NAME in err, "要点名是哪一份文件"
    assert vault_token(tmp_path) not in err, "拒绝里不得出现令牌"
    assert not record_path(tmp_path).exists()


def test_a_stale_launcher_token_is_replaced_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """环境里还留着旧令牌 ⇒ 覆盖它、并在记录里写明覆盖过。

    退役的是「按文件名选身份」那套，不是「启动器可能还留着它」这件事：启动器与 worker
    不在同一个提交里上线。覆盖而不是拒绝，是因为那份令牌可能是对的（同一个主体重铸），
    而拒绝会把一次部署顺序问题升级成一次停摆；写明而不只是覆盖，是因为「这一轮用的是
    哪一份」在两个读数不一致时必须留痕。
    """
    stale = mint(PRINCIPAL, [Role.RESEARCHER])[0]
    run = Run(tmp_path, monkeypatch, launcher_token=stale)

    assert run() == 0

    got = recorded(tmp_path)
    assert got["token_present"] is True
    assert got["token_sha256"] == digest_of(vault_token(tmp_path))
    assert got["token_sha256"] != digest_of(stale)
    assert run_record(tmp_path)["identity"]["launcher_token_replaced"] is True


def test_the_identity_is_released_when_the_round_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一轮结束时锁就放掉 —— **不等进程退出**，成功的与失败的两条出口都放。

    这一条钉的是 ``run_round`` 里那个 ``finally``：``lock_fd`` 是一个整数，``identity``
    出了作用域、被 GC 掉都不会关掉它，于是同一个进程里的下一次派发会撞上自己的上一轮
    （读数形状：「研究员身份全部被在飞的 worker 占着」）。在 CLI 里那个形状被进程退出
    掩盖了，测试进程里连派两轮正好把它照出来。
    """
    run = Run(tmp_path, monkeypatch)
    assert run.ring is not None

    assert run() == 0
    monkeypatch.setenv("FAKE_DELEGATE_EXIT", "7")
    assert run() == 7, "delegate 失败那一轮也放锁"

    lock_root = runtime_root(tmp_path) / "state" / "identities"
    again = claim_identity(run.ring, lock_root=lock_root)
    os.close(again.lock_fd)
    assert again.principal == PRINCIPAL


# ----------------------------------------------------------------------
# 边界：写在哪、说了什么、怎么被起起来
# ----------------------------------------------------------------------
def test_a_round_touches_only_the_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一次成功的派发之后，部署树与凭据库一个字节都没变。

    本层的关闭在市场侧（``submit_findings``），不在这里；所以它不该往部署树里写任何
    东西 —— 任务文件本身、部署配置、以及凭据库两面都要逐字节相同。写下去的东西只能
    在隔离根（本轮 scratch）与数据根（身份锁）里。
    """
    run = Run(tmp_path, monkeypatch)
    board = run.deploy / "board"
    board.mkdir()
    (board / f"{TASK_ID}.md").write_text("---\nid: x\n---\n", encoding="utf-8")
    (run.deploy / "settings.json").write_text('{"flags": []}\n', encoding="utf-8")
    before_deploy = snapshot(run.deploy)
    before_vault = snapshot(vault_dir(tmp_path))

    assert run() == 0

    assert snapshot(run.deploy) == before_deploy
    assert snapshot(vault_dir(tmp_path)) == before_vault
    cwd = Path(recorded(tmp_path)["cwd"]).resolve()
    assert not cwd.is_relative_to(run.deploy.resolve())
    assert cwd.is_relative_to(run.worker_root.resolve())


def test_the_run_says_which_work_item_it_took_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """一次派发留一行痕迹：任务 id → 工作单元 id。

    报告里唯一能把「外面那条任务」与「里面那件工作单元」对上的一句话就是它。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    err = capsys.readouterr().err
    assert TASK_ID in err
    assert WORK_ITEM_ID in err
    assert f"任务 {TASK_ID} → 工作单元 {WORK_ITEM_ID}" in err


def test_the_run_says_on_stderr_that_it_checked_the_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """stderr 上说的不是「我的构建是什么」，是「我核对过服务那一份」。

    两句话在措辞上必须不同：一句是自述，一句是一次跨进程核对。合成一句之后，一次
    **没有核对**的派发在日志里与核对通过的完全一样。
    """
    run = Run(tmp_path, monkeypatch)

    assert run() == 0

    err = capsys.readouterr().err
    assert "构建核对" in err
    assert f"pid {os.getpid()}" in err
    assert LOADED_BUILD.source_tree in err
    if LOADED_BUILD.revision is not None:
        assert LOADED_BUILD.revision in err


def test_the_receipt_does_not_render_an_unreadable_tree_as_a_clean_one() -> None:
    """``读不出来`` 与「干净」在那一行上**必须不同**。

    三值渲染的失败形态是本仓最贵的一类：一个把「没读出来」印成「无」的实现，会让一次
    无法复现的派发在日志里看起来是从干净检出起来的 —— 而那份日志正是事后唯一的凭据。
    """

    def stamp(*, revision: str | None, dirty: bool | None, errors: dict) -> BuildStamp:
        return BuildStamp(
            package_file="/p/__init__.py",
            source_tree="/p",
            revision=revision,
            dirty=dirty,
            read_at="2026-09-23T00:00:00+00:00",
            errors=errors,
        )

    # 两半**各测各的**：只让一格读不出来，另一格必须是好的 —— 否则一条「读不出来」
    # 会从另一格漏过来，两个取值就分不开了。
    clean = build_receipt(stamp(revision="a" * 40, dirty=False, errors={}))
    touched = build_receipt(stamp(revision="a" * 40, dirty=True, errors={}))
    dirty_unreadable = build_receipt(
        stamp(revision="a" * 40, dirty=None, errors={"dirty": "git 没被起来（没有 git）"})
    )
    revision_unreadable = build_receipt(
        stamp(revision=None, dirty=False, errors={"revision": "git 没被起来（没有 git）"})
    )

    assert "未提交改动：无" in clean
    assert "未提交改动：有" in touched
    assert "未提交改动：读不出来" in dirty_unreadable and "没有 git" in dirty_unreadable
    assert "未提交改动：无" in revision_unreadable, "这半格读得出来，就不该跟着另一格一起糊"
    assert "读不出来" in revision_unreadable
    assert len({clean, touched, dirty_unreadable, revision_unreadable}) == 4


@pytest.mark.parametrize(
    "argv",
    [[], ["--work-item", WORK_ITEM_ID], ["--task", TASK_ID]],
)
def test_main_requires_both_ids(argv: list[str]) -> None:
    """两个 id 都是必填：缺一个就退 2，而不是「猜一个前缀」或「先跑起来再说」。

    这条钉的是参数面本身：显式两个 id 取代的是「从别人拼的 argv 里反解身份」，而反解
    之所以危险，是因为同一串参数可以有第二种读法。缺参数在这里必须是 argparse 的 2，
    不是一次部分成功的派发。
    """
    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == EXIT_REFUSED


def test_the_module_is_runnable_as_a_program() -> None:
    """``python -m marketops.interfaces.research_worker`` 必须真的是一条入口。

    两半：缺参数时 argparse 退 2 并点名 ``--work-item`` / ``--task``；参数齐了而
    部署没接 delegate 时，本层退 2 并点名缺的那个变量。一个挂空的 ``__main__`` 会让
    第一条以 0 退出而什么都没做。
    """
    env = {key: value for key, value in os.environ.items() if key != DELEGATE_ENV}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    bare = subprocess.run(
        [sys.executable, "-m", "marketops.interfaces.research_worker"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert bare.returncode == EXIT_REFUSED
    assert "--work-item" in bare.stderr and "--task" in bare.stderr

    configured = subprocess.run(
        [
            sys.executable,
            "-m",
            "marketops.interfaces.research_worker",
            "--work-item",
            WORK_ITEM_ID,
            "--task",
            TASK_ID,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert configured.returncode == EXIT_REFUSED
    assert DELEGATE_ENV in configured.stderr
