"""入站的启动器：Quay 的 driver 交给 MarketOps 的那一次派发。

## 这一层是谁

Quay 的 worker driver 每一轮把一件任务 spawn 成一个进程，argv 由 ``launchArgv`` 拼
（``[launcher, "--settings", …, "-n", <role>, "-p", <prompt>]``）。``launcher`` 是
``.quay/profiles.yml`` 里 role 的**单 token**（实测：``const argv =
[resolved.launcher, …]``，不切空格），所以 ``marketops-research-worker`` 就是那个
token 指到的程序，它的 argv **只有一种含义**：Quay 拼的那一串。

它要做的只有几件事：从 ``-p`` 里读出任务身份 → **核对这条 argv 会给子 claude 开出
多大的工具面** → **核对本层加载的 marketops 与服务加载的是不是同一份** → 顺着任务文件
读出工作单元 → **建本轮的隔离运行目录并 chdir 过去** → 把 Quay 的其余 argv 原样交给
delegate、只把 prompt 换成研究形状的 → 把 delegate 的退出码原样交回去。

## 为什么每一条拒绝都同时断言「对面没有被启动」

这一层没有中间态：读不出任务身份就**不能**跑（跑起来的会是「某件工作单元的
worker」，而不知道是哪一件），而它一旦跑起来就会真的花预算、真的写数据。所以
「拒绝」必须连着断言 delegate 没被启动 —— 只断言退出码非零的话，「先启动、然后
自己也报个错」的实现会绿，而它已经把 delegate 放进去了。

替身是一个**真的子进程**（测试自己写进 tmp 的脚本，解释器就是跑测试这个）：它把
收到的 argv 与 cwd 记到盘上，退出码从 env 读。于是「转发的是 Quay 那一份 argv」
不是由一条断言说的，是由对面**照着自己收到了什么**说的。

替身、它的记录、以及隔离根（``tmp_path/workers``，真跑时是 ``ARTIFACTS_ROOT`` 下面）
都在工作区**外面** —— 因为有一条判据钉的是「一次派发不在工作区里留下任何东西」，而
脚手架要是写在工作区里，那条判据就会被自己弄红。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from marketops.adapters.build_stamp import LOADED_BUILD, BuildStamp, compare_builds
from marketops.adapters.server_build import publish_server_build, server_build_path
from marketops.interfaces.research_worker import (
    DATA_ROOT_ENV,
    DELEGATE_ENV,
    EXIT_REFUSED,
    WORKER_ROOT_ENV,
    build_receipt,
    main,
    research_prompt,
)
from marketops.security.catalog import Role
from marketops.security.identity import TOKEN_ENV, TOKEN_RING_ENV, claim_identity
from marketops.security.tokens import digest_of, mint
from tests.mcp_harness import ring_entry, ring_payload

JOB_ID = "1b1b7f8209c19491e1a26c34ad2207a8"
WORK_ITEM_ID = "6d783321a9df9b2f2c6bf4ace0cd9b26"
TASK_ID = "MOPS-1b1b7f82-6d783321"

#: 任务文件的前言块，逐字照实测形状（``marketops-mvp-quay/tasks/`` 那五条）。
TASK_FILE = """\
---
id: {task_id}
title: "one-subject"
status: ready
labels:
  - marketops-job:{job_id}
  - marketops-item:{work_item_id}
parent: null
children: []
extra: {{}}
---
"""


def quay_prompt(root: Path) -> str:
    """Quay ``buildWorkerPrompt`` 产出的那份 prompt。

    前两句是**逐字**实测（``worker-driver.ts``：``Task: ${task}. Repo root: ${root}.``），
    后半段是软件开发形状的正文 —— 它的存在就是下面那条判据要挡的东西。
    """
    return (
        "You are a per-task worker in the quay repo (SPEC-worker-driven-inner §5 阶段 2). "
        f"Task: {TASK_ID}. Repo root: {root}. "
        "Run the implementation chain: (1) create an isolated git worktree for "
        f"{TASK_ID}, then immediately provision it by running "
        "`bash kernel/scripts/dispatch-worktree-setup.sh <the worktree path>`"
    )


def quay_argv(root: Path, prompt: str) -> list[str]:
    """``launchArgv`` 在 role 上拼出来的那一串（去掉打头的 launcher token）。

    ``--mcp-config`` 的值是**实测形状的节略**（只留 ``type``）—— 逐字的那一份在
    ``tests/test_quay_cli_adapter.py`` 的 ``_launch_argv()`` 里，那边钉的是形状本身。
    这里要的是这条通路的后果，所以节略到「键集恰好是 marketops」这一件事为止。
    """
    return [
        "--settings",
        str(root / ".claude" / "launch.settings.json"),
        "--exclude-dynamic-system-prompt-sections",
        "--prompt-suggestions",
        "false",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{"marketops":{"type":"http"}}}',
        "-n",
        "marketops-mvp-task-worker",
        "-p",
        prompt,
        "--session-id",
        "3f1c0a52-9d4e-4a17-8b6c-2e5f7a9c1d30",
    ]


def argv_with_servers(root: Path, servers: dict[str, dict], *, strict: bool = True) -> list[str]:
    """实测 argv，只把 ``--mcp-config`` 的值换成 ``servers`` 那一份。

    ``strict=False`` 时**两个旗标一起拿走** —— 那是 driver 那条 fail-open 的**形状**
    （``mcpConfigArgvSuffix`` 在名单为空时把它们一起省掉），而不是「随手去掉一个」。
    """
    argv = quay_argv(root, quay_prompt(root))
    at = argv.index("--mcp-config")
    argv[at + 1] = json.dumps({"mcpServers": servers})
    if strict:
        return argv
    dropped = {"--strict-mcp-config", "--mcp-config", argv[at + 1]}
    return [a for a in argv if a not in dropped]


def work_root(tmp_path: Path) -> Path:
    """工作区就建在 ``tmp_path/ws``，脚手架（替身、记录）留在 ``tmp_path``。"""
    root = tmp_path / "ws"
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    return root


def write_task(root: Path, labels: tuple[str, ...]) -> Path:
    body = TASK_FILE.format(task_id=TASK_ID, job_id=JOB_ID, work_item_id=WORK_ITEM_ID)
    lines = body.splitlines()
    head = lines[: lines.index("labels:")]
    tail = lines[lines.index("parent: null") :]
    rendered = "\n".join(head + ["labels:"] + [f"  - {label}" for label in labels] + tail)
    path = root / "tasks" / f"{TASK_ID}.md"
    path.write_text(rendered + "\n", encoding="utf-8")
    return path


def default_labels() -> tuple[str, ...]:
    return (f"marketops-job:{JOB_ID}", f"marketops-item:{WORK_ITEM_ID}")


def write_delegate(tmp_path: Path) -> Path:
    """写一个真的子进程替身：记下 argv 与 cwd，退出码从 env 读。

    令牌**只记它的摘要**（``digest_of``，与环里落盘的那种形态同一个函数）：
    「delegate 拿到的是不是环里那一个」这条判据只要有摘要就成立，而把明文令牌写进
    一份记录 —— 哪怕是 tmp 里的假令牌 —— 是在给「令牌可以落在某处」立一个先例。
    """
    program = tmp_path / "fake-delegate"
    program.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, json, os, sys\n"
        "token = os.environ.get('MARKETOPS_MCP_TOKEN', '')\n"
        "with open(os.environ['FAKE_DELEGATE_RECORD'], 'w', encoding='utf-8') as handle:\n"
        "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "               'token_sha256': hashlib.sha256(token.encode()).hexdigest(),\n"
        "               'token_present': bool(token)}, handle)\n"
        "raise SystemExit(int(os.environ.get('FAKE_DELEGATE_EXIT', '0')))\n",
        encoding="utf-8",
    )
    program.chmod(0o755)
    return program


def record_path(tmp_path: Path) -> Path:
    return tmp_path / "delegate-record.json"


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


def runtime_root(tmp_path: Path) -> Path:
    """这个部署的运行态根 —— ``MARKETOPS_DATA_ROOT`` 指的就是它。

    与 ``tmp_path/workers``（隔离运行目录的根）**分开放**：真跑时后者是前者的
    ``workers/``，而测试把两者放在两个目录里，正是为了让「本层读的根」与「本层写的
    根」是同一条判据里的两格，而不是同一个字符串。

    §15(a) 之后它多了一个读者：身份锁落在它的 ``state/identities/`` 下 ——
    被保护的资源（run 级状态库）就在同一个根里，所以锁跟着数据根走。
    """
    return tmp_path / "runtime"


def identity_lock(tmp_path: Path, principal: str = "claude-worker-1") -> Path:
    """那个身份在本部署上的锁。"""
    return runtime_root(tmp_path) / "state" / "identities" / f"{principal}.lock"


def vault_dir(tmp_path: Path) -> Path:
    """凭据库 —— 环与令牌文件都在这里（真跑时是部署方给的那个 vault 目录）。

    路径由 ``MARKETOPS_MCP_TOKENS`` 指过来，所以本仓里不写死任何一个绝对的 vault
    路径 —— 那条判据在 ``tests/test_portability.py``（这条 docstring 上一版就撞在它上面）。
    """
    return tmp_path / "vault"


def write_vault(
    tmp_path: Path,
    *,
    principal: str = "claude-worker-1",
    roles: tuple[Role, ...] = (Role.RESEARCHER,),
    in_ring: bool = True,
    lifetime: int = 30 * 86400,
) -> Path:
    """写一份 vault（格式 3 的环 + 一份研究员令牌文件），返回环的路径。

    ``in_ring=False`` 写出一份**从来没进过环**的令牌：文件逐字节有效、形状与真令牌
    一样，而环里没有它的摘要 —— 那就是 §G1 的形状（文件是 9-22 铸的、环是 9-23 的）。
    两条路径共用同一份写盘逻辑，否则「测试里的环」与「部署里的环」会各长一样。
    """
    vault = vault_dir(tmp_path)
    vault.mkdir(parents=True, exist_ok=True)
    token, digest = mint(principal, list(roles))
    if not in_ring:
        digest = digest_of(mint(principal, list(roles))[0])
    (vault / "marketops-mcp-token-worker-1.txt").write_text(token + "\n", encoding="utf-8")
    ring = vault / "marketops-mcp-tokens.json"
    ring.write_text(
        json.dumps(
            ring_payload(
                ring_entry(
                    digest,
                    principal,
                    roles,
                    expires_at=time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + lifetime)
                    ),
                )
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ring


def vault_token(tmp_path: Path) -> str:
    """vault 里那份令牌文件的内容（判据只拿它的摘要去比）。"""
    return (vault_dir(tmp_path) / "marketops-mcp-token-worker-1.txt").read_text(
        encoding="utf-8"
    ).strip()


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


class Run:
    """一次调用的输入（delegate 程序、prompt、argv、以及这个部署的接线），各有缺省。

    缺省值表示**一个接对了的部署**：数据根指着 ``tmp_path/runtime``，而那个根上已经
    有一份与服务一致的构建回执（§15(b)）。要测拒绝的那几条，就把对应的那一格换掉
    —— 换掉之后跑出来的拒绝，与一个真的接错了的部署跑出来的是同一句。
    """

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        delegate: Path | None = None,
        delegate_env: str | None = "__default__",
        worker_root_env: str | None = "__default__",
        data_root_env: str | None = "__default__",
        server_build: str | None = "__default__",
        ring_env: str | None = "__default__",
        ring_in_ring: bool = True,
        launcher_token: str | None = None,
        prompt: str | None = None,
        argv: list[str] | None = None,
    ) -> None:
        self.root = tmp_path / "ws"
        self.worker_root = tmp_path / "workers"
        self.runtime_root = runtime_root(tmp_path)
        monkeypatch.chdir(self.root)
        monkeypatch.setenv("FAKE_DELEGATE_RECORD", str(record_path(tmp_path)))
        if delegate_env == "__default__":
            program = delegate if delegate is not None else write_delegate(tmp_path)
            monkeypatch.setenv(DELEGATE_ENV, str(program))
        elif delegate_env is None:
            monkeypatch.delenv(DELEGATE_ENV, raising=False)
        else:
            monkeypatch.setenv(DELEGATE_ENV, delegate_env)
        if worker_root_env == "__default__":
            monkeypatch.setenv(WORKER_ROOT_ENV, str(self.worker_root))
        elif worker_root_env is None:
            monkeypatch.delenv(WORKER_ROOT_ENV, raising=False)
        else:
            monkeypatch.setenv(WORKER_ROOT_ENV, worker_root_env)
        # 服务那一份回执：`"__default__"` = 服务报的就是本层加载的这一份（于是核对通过）。
        # `None` = **一个字都不写**，留给测试自己摆那一份（坏文件、别的树、不存在）。
        if server_build == "__default__":
            publish_server_build(self.runtime_root, stamp=LOADED_BUILD)
        if data_root_env == "__default__":
            monkeypatch.setenv(DATA_ROOT_ENV, str(self.runtime_root))
        elif data_root_env is None:
            monkeypatch.delenv(DATA_ROOT_ENV, raising=False)
        else:
            monkeypatch.setenv(DATA_ROOT_ENV, data_root_env)
        # §15(a)：身份从环里来。`"__default__"` = 这个凭据库里有一份格式 3 的环、
        # 环里有一个研究员主体、令牌也就写在环里（认领通过）。`None` = 没有这一格。
        self.ring: Path | None = None
        if ring_env == "__default__":
            self.ring = write_vault(tmp_path, in_ring=ring_in_ring)
            monkeypatch.setenv(TOKEN_RING_ENV, str(self.ring))
        elif ring_env is None:
            monkeypatch.delenv(TOKEN_RING_ENV, raising=False)
        else:
            monkeypatch.setenv(TOKEN_RING_ENV, ring_env)
        # 启动器**已经不再** export 令牌了（§15(a) 退役的是那一套）。缺省清掉它，
        # 免得跑测试这个 shell 里恰好有一份把判据弄成一条恒真的真话。
        if launcher_token is None:
            monkeypatch.delenv(TOKEN_ENV, raising=False)
        else:
            monkeypatch.setenv(TOKEN_ENV, launcher_token)
        self.argv = argv if argv is not None else quay_argv(
            self.root, prompt if prompt is not None else quay_prompt(self.root)
        )

    def __call__(self) -> int:
        return main(self.argv)


# ----------------------------------------------------------------------
# 通路：转发的是 Quay 那一份 argv，换掉的只有 prompt
# ----------------------------------------------------------------------
def test_the_delegate_gets_quays_argv_with_the_prompt_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quay 拼的那一串**原样原序**过去，只有 ``-p`` 的值换成了研究形状的。

    两个方向都要断：**少了**任何一项（比如顺手重建一份 argv 时漏掉 ``--mcp-config``）
    会让 delegate 拿不到工作区配好的工具；**换了**值，等于绕开工作区在
    ``profiles.yml`` 与 ``launch.settings.json`` 里做的部署决定。
    """
    write_task(work_root(tmp_path), default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    got = recorded(tmp_path)["argv"]
    sent = quay_argv(tmp_path / "ws", quay_prompt(tmp_path / "ws"))
    at = sent.index("-p")
    expected = sent[:at] + sent[at + 2 :]  # Quay 拼的，去掉 -p 与它的值

    assert got[: len(expected)] == expected
    assert got[len(expected) : len(expected) + 1] == ["-p"]
    assert len(got) == len(expected) + 2, "多了或少了参数"


def test_the_delegate_never_sees_quays_software_engineering_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quay 的 prompt 里是一整条软件开发链（worktree / 实现 / commit）。

    它原样到达 delegate 的表现是：研究 worker 收到一份「去 git 里改代码」的指令，
    然后照做 —— 而外部看板上那条任务会显示跑完了。
    """
    write_task(work_root(tmp_path), default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    got = recorded(tmp_path)["argv"]
    prompt = got[got.index("-p") + 1]
    assert "implementation chain" not in prompt
    assert "dispatch-worktree-setup" not in prompt
    assert WORK_ITEM_ID in prompt


def test_the_delegate_runs_in_an_isolated_directory_not_in_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd **不是**工作区根，而是本轮新造的那个隔离目录。

    工作区根里住着 ``.quay/``（看板控制）、``tasks/``（**别的任务**）、部署的权限与工具
    配置 —— 而 ``Read``/``Grep``/``Glob`` 在部署的 deny 名单上**不在**，所以「站在哪里」
    就是那条边界本身。上一版的判据钉的是相反的结论（cwd == 工作区根），它当时绿着，
    因为那时这层还没造这个目录。
    """
    root = work_root(tmp_path)
    write_task(root, default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    got = Path(recorded(tmp_path)["cwd"]).resolve()
    assert got != root.resolve(), "delegate 站在工作区根 —— .quay/、tasks/、部署配置都在那儿"
    assert got.is_relative_to((tmp_path / "workers").resolve()), got
    assert WORK_ITEM_ID in got.parts and TASK_ID in got.parts, got


def test_two_dispatches_do_not_share_an_isolated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一条任务派两次 ⇒ 两个目录。

    共用目录的话，第二轮会读到上一轮留下的 scratch 与记录 —— 而「这一轮在盘上留下了
    什么」正是隔离要能回答的那个问题。
    """
    write_task(work_root(tmp_path), default_labels())
    run = Run(tmp_path, monkeypatch)

    assert run() == 0
    first = Path(recorded(tmp_path)["cwd"])
    second_record = tmp_path / "delegate-record-2.json"
    monkeypatch.setenv("FAKE_DELEGATE_RECORD", str(second_record))

    assert run() == 0

    second = Path(json.loads(second_record.read_text(encoding="utf-8"))["cwd"])
    assert first != second


def test_the_isolated_directory_records_which_run_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """那一轮留在盘上的那份记录：两个身份、两个目录、以及**核对过的那个工具面**。

    ``cwd`` 与 ``workspace_root`` 是两格：它们不同是正常的（隔离生效），相同就是没生效
    ——合成一格之后这两件事读起来一模一样。``mcp.servers`` 必须是**放行时的那一个读数**：
    从配置再读一次会在某一天写出「放行的是 A、记下来的是 B」。
    """
    root = work_root(tmp_path)
    write_task(root, default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    got = Path(recorded(tmp_path)["cwd"]).resolve()
    body = json.loads((got / "run.json").read_text(encoding="utf-8"))

    assert body["quay_task_id"] == TASK_ID
    assert body["work_item_id"] == WORK_ITEM_ID
    assert Path(body["cwd"]).resolve() == got
    assert Path(body["workspace_root"]).resolve() == root.resolve()
    assert body["mcp"]["servers"] == ["marketops"]
    assert body["loaded_marketops"]["source_tree"] == LOADED_BUILD.source_tree
    assert body["loaded_marketops"]["revision"] == LOADED_BUILD.revision


def test_the_research_prompt_names_the_tools_that_carry_the_work() -> None:
    """prompt 是**过程**，不是内容：指引与业务正文走 ``get_assignment``。

    §2.14 H3a 逐字：「指引走 ``get_assignment``，**不**走 Quay 的 task 正文」。
    所以这里钉四件事：工作单元 id 在、四个工具名在、以及「拿不到证据就别交」这条
    （P0-2 那一课：worker 正确地 ``blocked`` 而不是伪造 ``evidence_ref``）。
    """
    prompt = research_prompt(WORK_ITEM_ID)

    assert WORK_ITEM_ID in prompt
    for tool in ("get_assignment", "fetch_document", "submit_findings", "get_evidence"):
        assert tool in prompt
    assert "blocked" in prompt


# ----------------------------------------------------------------------
# 退出码即裁决
# ----------------------------------------------------------------------
@pytest.mark.parametrize("code", [0, 7])
def test_the_delegate_exit_code_is_handed_back_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """0 出去 0 回来、7 出去 7 回来 —— 本层不解释、不替换、不吞。

    给它一个「非零就归 1」的规范化，driver 在 ``worker-outcome.jsonl`` 里记的结局
    就与 delegate 真正的结局分了家，而那条记录是**唯一**一份「这一轮为什么没成」。
    """
    write_task(work_root(tmp_path), default_labels())
    monkeypatch.setenv("FAKE_DELEGATE_EXIT", str(code))

    assert Run(tmp_path, monkeypatch)() == code


# ----------------------------------------------------------------------
# 拒绝，每一条都要「对面没有被启动」
# ----------------------------------------------------------------------
def test_an_argv_without_the_prompt_flag_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, argv=["--settings", "x", "-n", "w"])()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()


def test_a_prompt_pointing_at_another_workspace_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Repo root:`` 说的是**另一个**工作区 ⇒ 拒绝。

    同一个任务 id 在两棵树里都可能存在，而 ``tasks/<id>.md`` 是从 cwd 读的 ——
    不比对就会读到「碰巧站着的」那一棵里的同 id 任务，也就是把这次派发绑到错的
    工作单元上。误拒的代价是一次响亮的停摆，误放的代价是一次静默的错派。
    """
    write_task(work_root(tmp_path), default_labels())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    code = Run(tmp_path, monkeypatch, prompt=quay_prompt(elsewhere))()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()


def test_a_task_without_our_item_label_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任务文件在，但 label 里没有 ``marketops-item:`` ⇒ 拒绝。

    这条对应的是「driver 派了一件不是本仓写的任务」—— 它真实存在（同一个工作区里
    可以同时有软件开发任务），而放它过去就是让研究 worker 去跑一件代码任务。
    """
    write_task(work_root(tmp_path), (f"marketops-job:{JOB_ID}",))

    code = Run(tmp_path, monkeypatch)()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()


def test_a_missing_task_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务文件不在 ⇒ 拒绝，而不是「prompt 里有 task id，先跑起来再说」。"""
    work_root(tmp_path)

    code = Run(tmp_path, monkeypatch)()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()


def test_a_task_file_whose_shape_is_off_is_refused_before_the_delegate_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """任务文件的形状不对 ⇒ 拒绝，而且**在 delegate 起来之前**。

    这是那条读法（读工作区里的任务文件，理由见 ``adapters/quay_cli.py``）在这条
    通路上的后果。读成一组空 label 的话，下游会报一句听起来完全合理的话：「这条
    任务不是我们派的」—— 而真相是文件换了形状：同一件事、两个方向完全不同的排查。

    五种形状逐条钉在 ``tests/test_quay_cli_adapter.py``（那里直接断 ``ConfigError``，
    因为退出码分辨不出是哪一层拒的 —— 两条路都是 ``EXIT_REFUSED``）。这里钉的是
    它在**这条通路上**的后果：一个 2，加上对面**没有被启动**。
    """
    root = work_root(tmp_path)
    (root / "tasks" / f"{TASK_ID}.md").write_text(
        f"---\nid: {TASK_ID}\nlabels: marketops-item:{WORK_ITEM_ID}\n---\n",
        encoding="utf-8",
    )

    assert Run(tmp_path, monkeypatch)() == EXIT_REFUSED
    assert not record_path(tmp_path).exists()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_workspace_that_does_not_name_a_delegate_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    capsys: pytest.CaptureFixture,
) -> None:
    """delegate 没有缺省值：没命名就拒绝，并且**说出来缺的是哪一个**。

    「跑了但谁也没干活」与「报错说不知道该跑谁」退出码都是非零，而运维读到的是完全
    不同的两句话。所以这里连 stderr 一起断：变量名必须出现在里面。
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, delegate_env=value)()

    assert code == EXIT_REFUSED
    assert DELEGATE_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_workspace_that_does_not_name_an_isolated_root_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    capsys: pytest.CaptureFixture,
) -> None:
    """隔离根没有缺省值：没命名就拒绝，并且**说出来缺的是哪一个**。

    一个有缺省值的根最可能的取值就是「部署工作区里面」，而那正是这一格要关掉的地方
    —— 且它会看起来完全成功。与 ``DELEGATE_ENV`` 一样连 stderr 一起断。
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, worker_root_env=value)()

    assert code == EXIT_REFUSED
    assert WORKER_ROOT_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_a_relative_isolated_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """相对路径 ⇒ 拒绝。本层的 cwd 是**部署工作区根**，所以 ``workers`` 会解析到工作区
    里面 —— 目录建出来了、记录写进去了、delegate 也在跑，而隔离一点都没生效。

    （这条判据的第一版是绿的：``Path("workers")`` 在 tmp 里也建得出来。绿的原因不是
    「相对路径安全」，是「这条判据没问它是不是绝对的」。）
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, worker_root_env="workers")()

    assert code == EXIT_REFUSED
    assert WORKER_ROOT_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


@pytest.mark.parametrize("item", ["..", ".", "a/b", "a\x00b"])
def test_an_item_id_that_cannot_be_a_path_component_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, item: str
) -> None:
    """label 里的工作单元 id 会被当成**路径成分**用，所以四种取值必须拒绝。

    它从外面来（工作区里的任务文件），而 ``work_item_id_from_labels`` 对它们**全都**放行
    —— 它只要求非空、无首尾空白。所以这一格是这条通路上唯一一次检查，而四种各走一条：

    * ``..`` 能上跳，且不含 ``check_slug_safe`` 拦的任何字符（那是另一条检查的事）；
    * ``.`` 同上，只是它跳回原地、看起来无害 —— 它会与「同一秒里的下一次派发」撞名，
      而撞名在这里的表现是**第二次派发整个失败**，不是覆盖；
    * ``a/b`` 含路径分隔符，``check_slug_safe`` 拦；
    * 最后一个是**内嵌 NUL** 的 id，四条里唯一一个上面两条都不拦、却仍然必须被拒的：
      实测它到不了本层 —— label 里有 NUL 的任务文件在前言那一层就被拒了
      （``read_task_labels``：``unacceptable character #x0000``）。留在这里是为了钉住
      结果（拒绝 + 盘上没多出东西），不是为了钉住那一条路径。

    断三件事：退出码、delegate 没被启动、以及**盘上什么都没多出来**。
    """
    root = work_root(tmp_path)
    write_task(root, (f"marketops-job:{JOB_ID}", f"marketops-item:{item}"))
    # 快照取在**部署摆好之后、派发之前**：`Run(...)` 造出来的就是这一层启动时的盘上
    # 样子（工作区、delegate 那个程序、以及假服务报过构建的那个数据根），而被问的是
    # 「**这一次派发**有没有写东西」。钉「多了什么」而不是钉「有哪些」，才不会把
    # 「部署本来就长这样」读成「这一层写了东西」。
    run = Run(tmp_path, monkeypatch)
    before = sorted(p.name for p in tmp_path.iterdir())

    code = run()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == before, (
        "拒绝发生在写之前：盘上不该多出任何东西"
    )


def test_a_delegate_that_cannot_be_spawned_is_not_reported_as_a_failed_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delegate 指到一个不存在的程序 ⇒ 拒绝，**不**把它报成 delegate 自己失败。

    两者都让 driver 记下一次失败，但前者是本层的配置错、后者是研究真的没成 ——
    混在一起，看板上「这个工作单元反复失败」会指向错误的方向。
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, delegate=tmp_path / "not-a-program")()

    assert code == EXIT_REFUSED
    assert not record_path(tmp_path).exists()


# ----------------------------------------------------------------------
# 工具面：这条 argv 给子 claude 开出什么，本层说了算
# ----------------------------------------------------------------------
# 这一节钉的是「**在提示词之外**放宽的权限」。prompt 里写了「不要动看板」，而那句
# 话的执行器是一个语言模型；这几条钉的是让它**看不到**别的工具。上面第三条通路
# 测试（``test_the_delegate_gets_quays_argv_with_the_prompt_replaced``）跑的就是
# marketops-only 那份 argv，所以它是这一节的正对照：已知输入 → 派发成功。
#
# 三条各对应一种真实形状：多一个、少一个、以及 driver 那条 fail-open（两个旗标一起
# 消失）。三条都要「对面没有被启动」—— 一个先启动再报错的实现会让模型拿着多出来的
# 工具过一遍任务，而窗口里没有一行日志说这件事。


def test_an_argv_that_widens_the_tool_surface_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """多出一个 server ⇒ 拒绝，且 stderr 点名多出来的是哪一个。

    多出来的那个用实测里最真的一例：``plugin_quay_quay``（插件的合并名 —— 实测
    它正是躲过第一版 ``mcpBlacklist`` 的那一个）。它能改任务状态，而这一轮的 worker
    被明确要求不许动看板。
    """
    write_task(work_root(tmp_path), default_labels())
    argv = argv_with_servers(
        tmp_path / "ws",
        {"marketops": {"type": "http"}, "plugin_quay_quay": {"type": "http"}},
    )

    code = Run(tmp_path, monkeypatch, argv=argv)()

    assert code == EXIT_REFUSED
    assert "plugin_quay_quay" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_an_argv_whose_config_no_longer_lists_marketops_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """清单读得出来、但里面没有 ``marketops`` ⇒ 拒绝（缺的那一侧）。

    这一半不是「安全」而是「能不能干活」：没有市场工具，这一轮无论如何都完不成，
    而它会以「模型跑了、什么也没交」的样子花掉一次额度与一轮时间。
    """
    write_task(work_root(tmp_path), default_labels())
    argv = argv_with_servers(tmp_path / "ws", {})

    code = Run(tmp_path, monkeypatch, argv=argv)()

    assert code == EXIT_REFUSED
    assert "marketops" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_an_argv_with_the_two_flags_gone_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``--strict-mcp-config`` 与 ``--mcp-config`` **一起消失** ⇒ 拒绝。

    这是 driver 那条 fail-open 的形状（``mcpConfigArgvSuffix`` 在排除名单为空时返回
    空数组），也是本层这条守卫存在的首要理由：那一天子 claude 会去合并宿主上的一切
    来源，而这条 argv 上**看不出**它合并了什么。所以答复必须是拒绝，而不是「读出来
    是空的，于是放行」。
    """
    write_task(work_root(tmp_path), default_labels())
    argv = argv_with_servers(tmp_path / "ws", {"marketops": {"type": "http"}}, strict=False)

    code = Run(tmp_path, monkeypatch, argv=argv)()

    assert code == EXIT_REFUSED
    assert "--strict-mcp-config" in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


# ----------------------------------------------------------------------
# 构建核对：本层加载的那份代码，与服务加载的那份（§15(b)）
# ----------------------------------------------------------------------
# 这一节钉的是「记录里那一格 ``loaded_marketops`` 说的是**本层**，而干活的是服务」。
# 两边加载了两份代码时，链上没有任何一处会报错：delegate 照常调工具，服务照常答，
# 而这一轮每一条发现都由一份与服务不同的代码产生 —— 记录里写着本层那一份，于是报告
# 主张了一件不成立的事。现场（worker 的 ``PYTHONPATH`` 与服务器差 47 个提交）见
# ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G3。
#
# 四条拒绝各对应一种真实形状：数据根没配、服务从没报过、报的是另一棵树、报的是同一个
# 提交之前的服务。第五条钉「措辞只有一处」（判据与那句话都在
# ``adapters/build_stamp.compare_builds``）；第六条钉这一轮的记录里**写着它核对过**；
# 第七条钉那条刻意**不**拒绝的形状 —— 两侧都在同一棵未提交的树上。
def test_a_deployment_without_a_data_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``MARKETOPS_DATA_ROOT`` 没配 ⇒ 拒绝，并点名缺的是哪个变量。

    没有这一格就没有第二个读数，也就无从核对 —— 而「无从核对」与「核对过、一致」
    在这一轮的记录里长得一样。所以它不是「跳过这一条检查」，是拒绝。
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, data_root_env=None)()

    assert code == EXIT_REFUSED
    assert DATA_ROOT_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_a_data_root_where_no_service_ever_published_its_build_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """服务没报过自己的构建 ⇒ 拒绝，并说出那个文件在哪。

    文件不在有两种情形（服务没起来过、服务起的时候用的是另一个数据根），而两种都
    意味着本层手里没有可比的东西。说成「构建不一致」会把一种部署接线问题说成一个
    代码问题 —— 处置完全不同。
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, server_build=None)()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert str(server_build_file(tmp_path)) in err
    assert "还没有服务报过" in err
    assert not record_path(tmp_path).exists()


def test_a_worker_and_a_service_loading_different_trees_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """两侧是两个检出 ⇒ 拒绝，而且**两棵树都报出来**（§G3 的形状）。

    这是本判据要挡的那件事的主要形状：两个进程各自从一个检出里 import。只报出
    「不一致」而不报是哪两棵树，运维还得自己去问服务站在哪里 —— 而问的地方就是
    这里，因为除了这一行没有别的地方说。
    """
    write_task(work_root(tmp_path), default_labels())
    elsewhere = "/somewhere/else/marketops"
    write_server_build(
        tmp_path, {**a_stamp(source_tree=elsewhere).payload(), "pid": 4242}
    )

    code = Run(tmp_path, monkeypatch, server_build=None)()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert elsewhere in err
    assert LOADED_BUILD.source_tree in err
    assert not record_path(tmp_path).exists()


def test_a_service_started_before_the_current_commit_is_refused(
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
    write_task(work_root(tmp_path), default_labels())
    older = "0" * 40
    write_server_build(tmp_path, {**a_stamp(revision=older).payload(), "pid": 4242})

    code = Run(tmp_path, monkeypatch, server_build=None)()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert older in err
    assert LOADED_BUILD.revision in err
    assert not record_path(tmp_path).exists()


def test_a_build_receipt_that_is_not_a_build_stamp_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """文件在、但不是一个对象 / 缺键 ⇒ 拒绝，并带上那几格的原话。

    「读不出来」与「比下来不一致」是两句不同的话：前者的处置是去看那个文件（谁写的、
    什么时候写的），后者是去改部署。合成一句之后，一次磁盘故障会在报告里变成一个
    代码版本问题。
    """
    write_task(work_root(tmp_path), default_labels())
    server_build_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    server_build_file(tmp_path).write_text("[1, 2, 3]", encoding="utf-8")

    code = Run(tmp_path, monkeypatch, server_build=None)()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "不是一个对象" in err
    assert not record_path(tmp_path).exists()


def test_the_refusal_repeats_the_verdict_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """stderr 上那句话与 ``compare_builds`` 给出的是**同一句**。

    判据与措辞都只有一份（``adapters/build_stamp.py``）。两处各拼一遍的那一天，
    日志上说的与 ``run.json`` 里记的会是两句不同的话，而工单里贴的永远是先跑到的
    那一句。这条测试是那句话的唯一执行器。
    """
    write_task(work_root(tmp_path), default_labels())
    elsewhere = "/somewhere/else/marketops"
    write_server_build(
        tmp_path, {**a_stamp(source_tree=elsewhere).payload(), "pid": 4242}
    )
    verdict = compare_builds(LOADED_BUILD, a_stamp(source_tree=elsewhere))

    assert not verdict.agrees
    assert Run(tmp_path, monkeypatch, server_build=None)() == EXIT_REFUSED
    assert verdict.message in capsys.readouterr().err


def test_the_run_records_the_builds_it_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一次成功的派发留下的记录里，**写着它核对过**，以及核对的是哪一个进程。

    这一格是 ``loaded_marketops`` 的对照：那一格说的是本层加载的代码，两次（核对过、
    没核对过）都写着同一个值 —— 所以「核对过没有」这件事只能由这一格答。``pid`` 与
    ``read_at`` 是「核对的是哪一个服务实例」的唯一凭据。
    """
    root = work_root(tmp_path)
    write_task(root, default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    got = Path(recorded(tmp_path)["cwd"]).resolve()
    body = json.loads((got / "run.json").read_text(encoding="utf-8"))["build_consistency"]

    assert body["verdict"] == "agrees"
    assert body["message"] == ""
    assert body["server_build_path"] == str(server_build_file(tmp_path))
    assert body["server_pid"] == os.getpid(), (
        "这一份回执是测试进程自己 publish 的，所以 pid 就是它 —— 换成别的值说明记录里"
        "那一格不是写它的那个进程"
    )
    assert body["server_read_at"] == LOADED_BUILD.read_at


def test_the_run_says_on_stderr_that_it_checked_the_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """那一行在 stderr 上：说的不是「我的构建是什么」，是「我核对过服务那一份」。"""
    write_task(work_root(tmp_path), default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    err = capsys.readouterr().err
    assert "构建核对" in err
    assert f"pid {os.getpid()}" in err


def test_an_uncommitted_change_on_either_side_is_reported_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """服务报的那一份带未提交改动 ⇒ **放行**，但那一行要说出来。

    这条刻意钉的是一个**不拒绝**的形状（判据在 ``compare_builds`` 的 docstring 里
    写了理由：把 ``dirty`` 提成拒绝的理由会让工作树里跑不出任何一轮研究，而 §15 要求
    测试部署真的跑、§16 要求一轮有界真实队列）。所以它由两半组成：放行，**且**
    「未提交改动」这件事在回执上看得见 —— 两侧各有一行说它（服务那一侧在
    ``构建核对`` 那一行上，本层那一侧在 ``build_receipt()`` 那一行上）。
    """
    write_task(work_root(tmp_path), default_labels())
    write_server_build(
        tmp_path, {**a_stamp(dirty=True).payload(), "pid": os.getpid()}
    )

    code = Run(tmp_path, monkeypatch, server_build=None)()

    assert code == 0
    assert recorded(tmp_path)["cwd"], "放行的那一轮 delegate 真的被启动了"
    assert "有未提交改动" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 两件**不**做的事
# ----------------------------------------------------------------------
def test_a_run_leaves_the_workspace_byte_for_byte_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一次成功的派发之后，工作区里一个字节都没变。

    Kent POC 的启动器自己翻 status、自己 commit、自己 push —— 那条路让外部看板与
    本地账各说各话，而两边都不报错。本仓的关闭在市场侧（``submit_findings`` →
    结算 → 回传），所以这里钉的是这条否定的事实：本层不写工作区。

    包含任务文件本身与「没有多出任何文件」两面 —— 凭据也不该从这条路进工作区。
    """
    root = work_root(tmp_path)
    task = write_task(root, default_labels())
    before = task.read_bytes()
    names_before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))

    assert Run(tmp_path, monkeypatch)() == 0

    assert task.read_bytes() == before
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == names_before


def test_the_run_says_which_work_item_it_took_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """一次派发留一行痕迹：任务 id → 工作单元 id。

    报告里唯一能把「外面那条任务」与「里面那件工作单元」对上的一句话就是它。
    """
    write_task(work_root(tmp_path), default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    err = capsys.readouterr().err
    assert TASK_ID in err
    assert WORK_ITEM_ID in err


def test_the_run_says_which_tree_and_revision_it_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """一次派发留下「从哪棵树、哪个 commit 起来的」与「在哪个目录里跑」。

    两件事**不是同一棵树**：工作目录是 Quay 的工作区（``tasks/`` 在那里），而
    marketops 的代码由 venv 解析，可能落在另一个检出里。两者不一致时，除了这一行
    没有别的地方会说 —— 而报告里「worker 跑的是哪一版」正是由它答的。
    """
    root = work_root(tmp_path)
    write_task(root, default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    err = capsys.readouterr().err
    assert LOADED_BUILD.source_tree in err
    assert LOADED_BUILD.revision in err
    assert str(root) in err


def test_the_receipt_does_not_render_an_unreadable_tree_as_a_clean_one() -> None:
    """``读不出来`` 与「干净」在那一行上**必须不同**。

    三值渲染的失败形态是本仓最贵的一类：一个把「没读出来」印成「无」的实现，
    会让一次无法复现的派发在日志里看起来是从干净检出起来的 —— 而那份日志正是
    事后唯一的凭据。
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
    # 会从另一格漏过来，两个取值就分不开了（这条判据第一次写出来时正是这么绿的）。
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


def test_the_module_is_runnable_as_a_program() -> None:
    """``python -m marketops.interfaces.research_worker`` 必须存在 —— 工作区的启动器
    就是 exec 它。一个挂空的 ``__main__`` 会让那条路径以 0 退出而什么都没做。"""
    import subprocess

    env = {key: value for key, value in os.environ.items() if key != DELEGATE_ENV}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    proc = subprocess.run(
        [sys.executable, "-m", "marketops.interfaces.research_worker"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == EXIT_REFUSED
    assert DELEGATE_ENV in proc.stderr


# ----------------------------------------------------------------------
# §15(a)：身份从环里来，不从文件名来 —— 这一侧**接起来没有**
# ----------------------------------------------------------------------
# 选身份、解主体、拿锁那几条判据在 ``tests/test_security_identity.py``。这里要答的是
# 另外三个问题：身份有没有进产物、delegate 手里那一份令牌是不是环解出来的那一份、
# 以及接错的部署会不会**在没有身份的情况下照样把 delegate 启动起来**（后者是这条
# 缺口的形状：报告上写着「server 未被挂载」，而没有任何东西红）。
def test_the_run_records_which_identity_it_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """这一轮的产物里写着**是哪一个身份**：主体、角色、令牌文件、锁（不含令牌）。

    身份以前只出现在启动器往 stderr 打的一行字里，而那行字不属于任何产物 —— 事后问
    「这一轮是哪个人在做事」只能靠翻终端滚动条。而 ``principal`` 与 ``token_file``
    必须**一起**记：环里的主体名与 vault 里的文件名是两件独立的事实，只记文件名会让
    一次「环里换了主体」在记录里看不出来。
    """
    write_task(work_root(tmp_path), default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    identity = run_record(tmp_path)["identity"]
    assert identity["principal"] == "claude-worker-1"
    assert identity["roles"] == ["researcher"]
    assert identity["token_file"].endswith("marketops-mcp-token-worker-1.txt")
    assert identity["lock"] == str(identity_lock(tmp_path))
    assert identity["launcher_token_replaced"] is False
    assert vault_token(tmp_path) not in json.dumps(identity, ensure_ascii=False)
    assert identity_lock(tmp_path).is_file(), "锁真的落在数据根下"
    assert str(identity_lock(tmp_path)) in capsys.readouterr().err


def test_the_delegate_gets_the_token_the_ring_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delegate 环境里那一份 = vault 里那一份（按摘要比，明文不进任何记录）。

    这一条是 §G1 的直接反面：那时 delegate 拿到的是**文件里**的令牌，而环里没有它的
    摘要 —— 于是服务一律 401。判据按摘要比而不是逐字节：写进记录的只有摘要，
    「令牌可以落在某处」这个先例连测试文件都不给。
    """
    write_task(work_root(tmp_path), default_labels())

    assert Run(tmp_path, monkeypatch)() == 0

    got = recorded(tmp_path)
    assert got["token_present"] is True, "delegate 拿不到令牌时工具面整个是空的"
    assert got["token_sha256"] == digest_of(vault_token(tmp_path))


def test_a_deployment_without_a_token_ring_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``MARKETOPS_MCP_TOKENS`` 没配 ⇒ 拒绝，点名缺的变量，**delegate 不动**。

    「没有环」与「环里没有可用身份」是两件事（前者是部署还没接线、后者是轮换），
    所以这里除了拒绝还要判 delegate 没有被启动：启动一个没有身份的 worker，它会以
    一个空工具面跑完一整轮，而两边的报告都看不出这件事。
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, ring_env=None)()

    assert code == EXIT_REFUSED
    assert TOKEN_RING_ENV in capsys.readouterr().err
    assert not record_path(tmp_path).exists()


def test_a_vault_whose_token_is_not_in_the_ring_refuses_before_the_delegate_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """vault 里那份文件被一次轮换取代了 ⇒ 拒绝，且拒绝里不含令牌。

    这是 §G1 的逐字形状：文件是 9-22 铸的、环是 9-23 的，文件逐字节没变。旧启动器
    在这里照旧把死令牌交出去；本层在**派发之前**停下，所以那一轮的成本是零。
    """
    write_task(work_root(tmp_path), default_labels())

    code = Run(tmp_path, monkeypatch, ring_in_ring=False)()

    assert code == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "不在环里" in err
    assert "marketops-mcp-token-worker-1.txt" in err, "要点名是哪一份文件"
    assert vault_token(tmp_path) not in err, "拒绝里不得出现令牌"
    assert not record_path(tmp_path).exists()


def test_a_launcher_token_from_the_retired_mechanism_is_replaced_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """启动器仍 export 着旧令牌 ⇒ 覆盖它、并**在记录里写明覆盖过**。

    退役的是「按文件名选身份」那套，不是「启动器可能还留着它」这件事：启动器与 worker
    不在同一个提交里上线。覆盖而不是拒绝，是因为那份令牌可能是对的（同一个主体重铸），
    而拒绝会把一次部署顺序问题升级成一次停摆；写明而不只是覆盖，是因为「这一轮用的是
    哪一份」在两个读数不一致时必须留痕。
    """
    write_task(work_root(tmp_path), default_labels())
    stale = mint("claude-worker-1", [Role.RESEARCHER])[0]

    assert Run(tmp_path, monkeypatch, launcher_token=stale)() == 0

    err = capsys.readouterr().err
    assert TOKEN_ENV in err and "覆盖" in err
    assert recorded(tmp_path)["token_sha256"] == digest_of(vault_token(tmp_path))
    assert recorded(tmp_path)["token_sha256"] != digest_of(stale)
    assert run_record(tmp_path)["identity"]["launcher_token_replaced"] is True


def test_the_identity_is_released_when_the_round_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """一轮结束时锁就放掉 —— **不等进程退出**，成功的与失败的两条出口都放。

    这一条钉的是 ``main`` 里那个 ``finally``，形状是本层实测出来的：``lock_fd`` 是一个
    整数，``identity`` 出了作用域、被 GC 掉都不会关掉它，于是同一个进程里的下一次派发
    会撞上自己的上一轮（读数形状：「1 个研究员身份全部被在飞的 worker 占着」）。在
    CLI 里那个形状被进程退出掩盖了，测试进程里连派两轮正好把它照出来。
    """
    write_task(work_root(tmp_path), default_labels())
    run = Run(tmp_path, monkeypatch)
    assert run.ring is not None

    assert run() == 0
    monkeypatch.setenv("FAKE_DELEGATE_EXIT", "7")
    assert run() == 7, "delegate 失败那一轮也放锁"
    capsys.readouterr()

    lock_root = runtime_root(tmp_path) / "state" / "identities"
    again = claim_identity(run.ring, lock_root=lock_root)
    os.close(again.lock_fd)
    assert again.principal == "claude-worker-1"
