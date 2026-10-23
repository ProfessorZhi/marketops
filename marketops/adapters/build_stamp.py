"""运行中那份代码的构建指纹 —— 回执要能分辨「陈旧的进程」与「最新的进程」。

## 它为什么存在

``marketops.__version__`` 是常量 ``"0.1.0"``：它回答「这是哪一版产品」，不回答
「这个进程加载的是哪个 revision」。2026-09-23 的收口现场量过这个缺口 —— 一个
2026-09-22 23:29:53 起的服务进程比当轮的三个提交都早，而它的
``get_capabilities`` 回执与当轮新起的进程**逐字相同**。于是「这个服务加载的是
哪一版」在回执里无从问起，运维能做的只有「把它重起一遍，然后希望」。

``docs/MCP_SERVICE.md`` §2 要求的本来就是能力表报「配了没 / 就绪没 / 上次校验何时」，
并**带上版本与观测时间**：version 那一格给的是产品版本，这里补的是**这一次的部署**。

本模块答两问。第一问是「**这个进程**加载了哪份代码」（:func:`read_build_stamp`）。
第二问是「两份指纹是不是同一份代码」（:func:`compare_builds`，§15(b)：worker 派发前要
核对服务侧与 worker 侧加载的构建一致）—— 第二问的输入是**两份**指纹，其中一份可能来自
另一个进程写下的文件，所以 :meth:`BuildStamp.from_payload` 与 :meth:`BuildStamp.payload`
互为逆，形状只有一处。

## 它读的是运行中那棵树，不是 cwd

树根来自 ``marketops.config.REPO_ROOT``（本仓唯一一处「这份代码在盘上哪里」的
生产，由 ``__file__`` 反推）。刻意不读 cwd —— 记忆 `cwd 会遮住 .pth，让 venv
静默解析到错的 revision`：从某个检出目录里跑探针，测的是「碰巧站着的那个」
revision，而且不报错。回执要答的是「**这个进程**加载了哪份代码」。

## 「读不出来」与「没改过」是两个答案

``revision`` 与 ``dirty`` 都可能是 ``None``，而 ``None`` **不是**「干净」、
也**不是**「没有 commit」：它是「这一格没读出来」，逐格的理由写在 ``errors`` 里。
一个把读不出来降级成 ``False`` 的实现，会让一份**无人可复现**的构建在回执里看起来
是一次干净检出 —— 而这份回执的用途恰恰是判断「这个进程加载的是哪一版」。

## 三条边界（都是「本模块不答」，所以写在门口）

* **它不判断这个 revision 是不是「对的那一版」。** 没有清单可比对，它只报这棵树
  当时是什么样。谁是对的，由人来判。
* **它不重读。** :data:`LOADED_BUILD` 在 import 时读一次，此后不再落盘。所以
  ``read_at`` 是「这份代码被加载的时刻」，**不是「现在」** —— 一个每次调用都重读的
  实现，在一棵正在被改的树上会报出「现在 HEAD 是什么」，而那不是这个进程加载的东西。
* **``dirty`` 为真时，``revision`` 不足以复现加载的代码。** 要那个答案得逐文件比对
  内容，本模块没做。两格并排放在回执里，就是为了让这句话读得出来。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import PACKAGE_DIR
from ..domain.util import utc_now

__all__ = [
    "BuildComparison",
    "BuildStamp",
    "BuildVerdict",
    "LOADED_BUILD",
    "compare_builds",
    "read_build_stamp",
]

#: 一次 git 调用的结果：``(返回码, stdout, stderr)``。
#: 返回码为 ``None`` 表示**这个进程根本没起来**（没有 git、没权限、超时）——
#: 它与「起来了、退了个非零码」是两种故障，理由不同、处置也不同。
GitResult = tuple[int | None, str, str]

#: 跑一次 git 的那一处。留成参数是为了让三条故障分支都走得到（见 ``read_build_stamp``）。
GitRunner = Callable[[Path, Sequence[str]], GitResult]

#: 一次 git 调用最多等多久。构建指纹是**诊断**，不是关键路径 —— 一个卡住的读
#: 不该把服务面的启动一起拖住。
_GIT_TIMEOUT_S = 5.0

#: 一条理由最多回多少字符。回执会进调用方的模型上下文（``tools.py`` 的模块纪律
#: 第三条），而 git 的 stderr 是别人的文本。截断**自报**：末尾留一个省略号。
_MAX_REASON_CHARS = 200


def _run_git(root: Path, argv: Sequence[str]) -> GitResult:
    """跑一次 ``git -C <root> …``。**退出码只被返回，不被判断。**

    ## 为什么 ``-C`` 后面是**绝对**路径

    记忆 `git -C 会把相对文件路径拼两遍`：相对路径会被拼到 ``-C`` 的目录后面，
    本机实测得到的是一次静默的空值。这里传进来的 ``root`` 一律是绝对路径
    （``REPO_ROOT`` 由 ``__file__`` 反推），而 ``rev-parse`` / ``status`` 都不带
    文件参数 —— 两重保险，因为这条错误的表现形式是「读出来是空」。

    ## 为什么 ``--no-optional-locks``

    本模块**只读**。不带这个开关时 ``status`` 会顺手刷新索引并取索引锁：一个状态面
    不该写它正在报告的那棵树，也不该在别人（另一个 agent 的 git）拿着锁的时候去抢。

    ## 为什么是**最小环境**，而不是继承

    ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` 一旦在环境里，``-C`` 就
    不再是「哪棵树」的唯一定位来源（``GIT_DIR`` 优先于它）。继承环境会让「这份回执
    说的是哪棵树」取决于启动这个服务的那个人当时站在什么 shell 里 —— 而这一格要答
    的正是「哪棵树」。形状与 ``构建回执那一族的 _minimal_env`` 相同，只少一项（那边把 node
    所在目录放进 ``PATH``，因为对面内部会再起一个 node）。
    """
    full = ["git", "--no-optional-locks", "-C", str(root), *argv]
    try:
        done = subprocess.run(  # noqa: S603 -- argv 是列表，不经过 shell
            full,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_GIT_TIMEOUT_S,
            check=False,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(Path.home()),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            cwd=None,
        )
    except subprocess.TimeoutExpired:
        return None, "", f"TimeoutExpired：{_GIT_TIMEOUT_S:g} 秒没有返回（{' '.join(argv)}）"
    except UnicodeDecodeError as broken:
        return None, "", f"UnicodeDecodeError：输出不是 UTF-8：{broken}"
    except OSError as failed:
        return None, "", f"{type(failed).__name__}：{failed}"
    return done.returncode, done.stdout, done.stderr


def _first_line(text: str) -> str:
    """stderr 里第一个非空行，超长时截断并**自报**。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            if len(stripped) > _MAX_REASON_CHARS:
                return stripped[: _MAX_REASON_CHARS - 1] + "…"
            return stripped
    return ""


def _could_not_start(what: str, detail: str) -> str:
    return (
        f"{what}：git 没被起来（{_first_line(detail) or detail}）。"
        "这与「git 起来了、说这棵树不行」是两件事：处置是看这个进程能不能起它。"
    )


def _refused(what: str, code: int, err: str) -> str:
    detail = _first_line(err) or "（stderr 上没有可读的一行）"
    return f"{what}：git 退 {code}：{detail}"


def _revision(root: Path, run: GitRunner) -> tuple[str | None, str]:
    """这棵树现在的 ``HEAD`` → ``(commit id, 读不出来的理由)``。"""
    code, out, err = run(root, ("rev-parse", "HEAD"))
    if code is None:
        return None, _could_not_start("revision", err)
    if code != 0:
        return None, _refused("revision", code, err)
    sha = out.strip()
    if not sha:
        return None, (
            "revision：git 退 0 而 stdout 是空的。空的 commit id 与「没有 commit id」"
            "在回执里长得一样，所以这一格按读不出来算。"
        )
    return sha, ""


def _dirty(root: Path, run: GitRunner) -> tuple[bool | None, str]:
    """这棵树有没有未提交的改动 → ``(有没有, 读不出来的理由)``。

    ``False`` 是「读了、干净」，``None`` 是「没读出来」—— 两者都不是「大概没问题」。
    """
    code, out, err = run(root, ("status", "--porcelain"))
    if code is None:
        return None, _could_not_start("dirty", err)
    if code != 0:
        return None, _refused("dirty", code, err)
    return bool(out.strip()), ""


@dataclass(frozen=True)
class BuildStamp:
    """一份构建指纹：**这个进程**加载的代码在盘上的位置，以及那棵树当时的样子。

    字段（``payload()`` 恒为这六个键）：

    ``package_file``
        ``marketops`` 包本体在盘上的绝对路径。它是「加载了哪一份代码」的直接证据，
        也是另外两格的来源。

    ``source_tree``
        那棵树（包的上一级）。运维要核对的就是它。

    ``revision``
        ``git rev-parse HEAD``，读不出来时 ``None``（理由在 ``errors``）。

    ``dirty``
        有没有未提交的改动：``True`` / ``False`` / ``None``。三值而不是布尔值 ——
        「没读出来」降级成 ``False`` 会让无人可复现的构建看起来是干净检出。

    ``read_at``
        读的**那一刻**（UTC，带时区）。服务面在 import 时读，所以对它来说这就是
        「这个进程加载这份代码的时刻」；它不是「现在」，本模块不会重读。

    ``errors``
        哪一格没读出来、为什么。读得出来时是空对象，**键恒在** —— 一个会消失的键
        会让「读不出来」在解析方那里与「没有这一格」长得一样。
    """

    package_file: str
    source_tree: str
    revision: str | None
    dirty: bool | None
    read_at: str
    errors: Mapping[str, str]

    def payload(self) -> dict[str, Any]:
        """规范形状。**两个表面共用这一份**（理由同 ``findings_payload``）：
        ``get_capabilities`` 与 ``--check`` 各序列化一遍，漂移的那天同一份指纹
        在回执里会有两个形状。"""
        return {
            "package_file": self.package_file,
            "source_tree": self.source_tree,
            "revision": self.revision,
            "dirty": self.dirty,
            "read_at": self.read_at,
            "errors": dict(self.errors),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> BuildStamp:
        """从 :meth:`payload` 的形状读回一份指纹。**两者互为逆**。

        这一对是刻意做成一对的：一份指纹要穿过一个进程边界（服务把它写下来、
        worker 读回来，§15(b)），而「写出去」与「读回来」各写一遍的那一天，读的
        那一侧会按一个**旧的**形状解析一个新写的文件 —— 而它长得像「这个文件是
        坏的」。所以形状只有这一处，读与写共用它。

        ``dirty`` 与 ``revision`` 允许 ``None``（那是它们**有意义**的取值，见类
        docstring），但**不接受别的类型**：``0`` / ``"false"`` 这类落进来之后，
        比对会拿着一个既不是真也不是假的值继续往下走，而那个值在两份指纹里长得
        一样。**bool 是 int 的子类**，所以 ``dirty`` 那一格先判 ``bool`` 再判
        ``int``（本例里根本不许 int，写出来是因为反过来的判法会静默放行 ``0``）。

        Raises:
            ValueError: 缺键、或某一格不是这一格该有的形状。消息里点名是哪一个键。
        """
        missing = [key for key in _PAYLOAD_KEYS if key not in payload]
        if missing:
            raise ValueError(f"构建指纹缺这些键：{'、'.join(missing)}")

        def _text(key: str) -> str:
            value = payload[key]
            if not isinstance(value, str) or not value:
                raise ValueError(f"构建指纹的 {key} 必须是非空字符串，实际是 {value!r}")
            return value

        revision = payload["revision"]
        if revision is not None and not isinstance(revision, str):
            raise ValueError(f"构建指纹的 revision 必须是字符串或 null，实际是 {revision!r}")
        dirty = payload["dirty"]
        if dirty is not None and not isinstance(dirty, bool):
            raise ValueError(f"构建指纹的 dirty 必须是 true / false / null，实际是 {dirty!r}")
        errors = payload["errors"]
        if not isinstance(errors, Mapping):
            raise ValueError(f"构建指纹的 errors 必须是一个对象，实际是 {errors!r}")
        for key, value in errors.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(f"构建指纹的 errors 里有非字符串的一格：{key!r} → {value!r}")

        return cls(
            package_file=_text("package_file"),
            source_tree=_text("source_tree"),
            revision=revision,
            dirty=dirty,
            read_at=_text("read_at"),
            errors=dict(errors),
        )


#: ``payload()`` 的六个键。读回来的那一侧按它判缺键 —— 写在模块级而不是函数里，
#: 是因为它与 ``payload()`` 的返回字面量是**同一份清单**的两处写法，而它们之间
#: 的漂移只会在有人读一个旧文件时才被发现。
_PAYLOAD_KEYS = (
    "package_file",
    "source_tree",
    "revision",
    "dirty",
    "read_at",
    "errors",
)


class BuildComparison(str, Enum):
    """两份构建指纹比下来的三种结论。

    **没有一个「差不多」的中间格。** 这一格要么证明两边加载的是同一份代码，要么
    证明不是，要么证明不了 —— 而第三种与前两种一样是一个**结论**，不是一个留给
    人去判断的模糊状态。调用方按它决定放行还是停下，所以它必须是闭集。
    """

    AGREES = "agrees"
    DIFFERS = "differs"
    UNPROVABLE = "unprovable"


@dataclass(frozen=True)
class BuildVerdict:
    """两份指纹的比对结论，以及**要说给运维的那句话**。

    ``message`` 为空**当且仅当** :attr:`agrees` —— 一致本身没有什么要说的，另外
    两种都要点名是哪一格、在哪一侧、以及能做什么。措辞与判据都在这里（不在调用
    点上）的理由与 ``db.read_schema_state`` 是同一条：这句话有两个读者（worker
    的 stderr 回执与 ``run.json``），两处各拼一遍就会出现「日志说 A、记录说 B」。
    """

    verdict: BuildComparison
    message: str

    @property
    def agrees(self) -> bool:
        return self.verdict is BuildComparison.AGREES

    def payload(self) -> dict[str, str]:
        """规范形状（进 ``run.json``）。与 :attr:`message` 同源，不是第二处措辞。"""
        return {"verdict": self.verdict.value, "message": self.message}


def compare_builds(worker: BuildStamp, server: BuildStamp) -> BuildVerdict:
    """worker 加载的那一份，与服务加载的那一份，是不是同一份代码（§15(b)）。

    ## 判据

    1. **``source_tree`` 不同 ⇒ ``DIFFERS``。** 这是本判据要挡的那件事的**主要
       形状**：两个进程各自从一个检出里 import，而那两个检出是两个目录。
       ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G3 记的现场就是它 ——
       worker 的 ``PYTHONPATH`` 与服务器差 47 个提交，而两边都不报错。
    2. 同一棵树、两边的 ``revision`` 都读得出来而**不相等** ⇒ ``DIFFERS``。
       这是一条更细的形状：树是同一棵，但服务是**在某个提交之前**起的，此后
       那棵树被提交过。一个起了三小时的进程正属于这一格。
    3. 同一棵树、而某一侧的 ``revision`` 读不出来 ⇒ ``UNPROVABLE``。
    4. 其余 ⇒ ``AGREES``。

    ## ``dirty`` **参与报告，不参与判定**（这一条决定了这个判据能不能用）

    同一棵树上两侧都是「有未提交改动」时，两边读的是**同一批文件**；它们是否会
    各自 import 到不同的内容，取决于两次 import 之间那棵树有没有被改过，而
    「这一份代码是不是那一份」在本模块里**刻意的**答不出来（类 docstring 第三条
    边界：那要逐文件比对内容，本模块没做）。

    把 ``dirty`` 提成一条拒绝的理由会有一个具体的后果：**工作树里跑不出任何一轮
    研究**。而 §15 要求测试部署真的跑、§16 要求一轮有界真实队列 —— 也就是说，
    那条规则会把要验的东西本身挡掉。所以 ``dirty`` 只进回执（``run.json`` 与
    worker 那一行 stderr 上各有一份），由人判；这一层判的是它**证明得了**的那部分。

    ## 它不答什么

    它不答「服务还活着没有」，也不答「这个文件是新写的还是上一轮服务留下的」。
    那两个问题各有自己的产物（进程表、``read_at`` 与 ``pid``），把其中一个塞进
    这个判据里，会让「构建不一致」与「服务没起来」在报告里变成同一句话。
    """
    if worker.source_tree != server.source_tree:
        return BuildVerdict(
            BuildComparison.DIFFERS,
            f"两侧加载的不是同一棵树：\n"
            f"  worker：{worker.source_tree}\n"
            f"  服务：  {server.source_tree}\n"
            "这条链上两个进程各自 import 了一份 marketops，它们不是同一份代码。"
            "先让两侧指向同一棵树（部署侧的那一处是启动器里的 PYTHONPATH），再派发。",
        )
    if worker.revision is None or server.revision is None:
        side, stamp = (
            ("worker", worker) if worker.revision is None else ("服务", server)
        )
        why = stamp.errors.get("revision", "没有给出理由")
        return BuildVerdict(
            BuildComparison.UNPROVABLE,
            f"两侧是同一棵树（{worker.source_tree}），但 {side} 那一侧的 revision "
            f"读不出来：{why}\n"
            "同一棵树不足以判定两侧加载的是不是同一份代码 —— 它们可以是在两个不同的"
            "提交上分别起的。这一格读不出来就没有第二个依据了，所以这里停下。",
        )
    if worker.revision != server.revision:
        return BuildVerdict(
            BuildComparison.DIFFERS,
            f"两侧是同一棵树（{worker.source_tree}），但 revision 不同：\n"
            f"  worker：{worker.revision}\n"
            f"  服务：  {server.revision}\n"
            "服务是在另一个提交上起的 —— 一个起了很久的进程正属于这一格。"
            "重启服务让它加载当前这棵树，再派发。",
        )
    return BuildVerdict(BuildComparison.AGREES, "")


def read_build_stamp(*, run: GitRunner = _run_git) -> BuildStamp:
    """读一次运行中那棵树的构建指纹。

    Args:
        run: 跑 git 的那一处。留作注入口是为了让「git 起不来」「git 说这不是仓库」
            「stdout 是空的」三条分支**都走得到** —— 一个只能在真的坏掉时才知道
            自己坏在哪的诊断，正是本模块要修的东西。

    Returns:
        一份 :class:`BuildStamp`。读不出来的格子取 ``None``，理由进 ``errors``；
        **不抛**：构建指纹是诊断，一个抛出去的诊断会让调用方只能选择打堆栈或吞掉。
    """
    root = PACKAGE_DIR.parent
    errors: dict[str, str] = {}

    revision, revision_why = _revision(root, run)
    if revision is None:
        errors["revision"] = revision_why

    dirty, dirty_why = _dirty(root, run)
    if dirty is None:
        errors["dirty"] = dirty_why

    return BuildStamp(
        package_file=str(PACKAGE_DIR / "__init__.py"),
        source_tree=str(root),
        revision=revision,
        dirty=dirty,
        read_at=utc_now().isoformat(),
        errors=errors,
    )


#: **这个进程**加载这份代码时读到的那一份。建在 import 时刻，不是第一次被问到时。
#:
#: 两处读的是**同一个对象**（``get_capabilities`` 与 ``--check``），所以两个表面
#: 不会对同一个进程给出两份指纹 —— 而两份指纹在报告里长得一样，只有一处会是对的。
#:
#: 为什么不懒读：懒读报的是「第一次有人问到的那一刻那棵树是什么样」。一个起了三小时
#: 的进程在这三小时里被改过、又被提交过，懒读会给出一份**新的** revision，而它加载
#: 的是旧的 —— 那个方向的错正是本模块存在的理由。
LOADED_BUILD: BuildStamp = read_build_stamp()
