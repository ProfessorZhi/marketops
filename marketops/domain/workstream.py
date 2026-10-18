"""**Workstream 与 EffectiveScene** —— B01 / B02 的两个运行边界上的值对象。

``docs/PRODUCT_LINE_AND_SCENE_MODEL.md`` §2 逐字：「产品线可选，
**Workstream / Scene 才是运行边界**」。这一模块落下那句话里的两样东西：

* :class:`Workstream` —— 一次已经受理的启动，此后的暂停 / 恢复 / 改名都作用在
  它上面。它的业务键是 :func:`workstream_id` （``<scene_id>#<ordinal>``），
  而 ``ordinal`` 在**同一个 scene_id 内**单调递增（冻结表 §3.2）。
* :class:`LaunchRecord` —— 「谁、按哪一版、拿哪个幂等键批准了这次启动」。
  它的业务键是那条四元组，而**幂等不是这一层记得的**：它是 DDL 上那条 UNIQUE
  （``(workspace, scene_id, scene_version, idempotency_key)``）。这一层只负责
  把「同一次启动」这件事**描述得出来**，让存储那一半能照它判。

## 为什么这三样东西（Workstream / LaunchRecord / EffectiveScene）在一个文件里

它们说的是**同一次决定**的三面：批准（LaunchRecord）、受理（Workstream）、
以及那一刻生效的规则（EffectiveScene）。分成三个模块的代价不是多几行 import，
是「同一次启动的三个 id」在三个地方各推一遍 —— 而它们必须相互指得着：
``Workstream.launch_id`` 指向那条 LaunchRecord，``LaunchRecord.workstream_id``
指回来，``EffectiveScene`` 按 ``(scene_id, version)`` 被两者共同引用。

## 这一层不做什么

* **不读时钟**。``created_at`` / ``updated_at`` 由调用方给（照 ``SceneVersion``
  的先例：模型里出现 ``utc_now()`` 会让同一个输入在两个时刻构造出两个不同的对象，
  而「重发同一个请求」这件事在类型层就失去判据）。
* **不判幂等**。幂等的判据是盘上那条 UNIQUE 加一次读回，见
  ``application/workstream.py::start_workstream``。
* **不取冲突的赢家**。:class:`EffectiveScene.conflicts` 逐条点名，见那一格的 docstring。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator

from marketops.domain.base import StrictModel
from marketops.domain.util import (
    SEP,
    check_canonical_id,
    check_market_country,
    sha256_text,
    to_utc,
)

__all__ = [
    "EffectiveCell",
    "EffectiveScene",
    "GeoScope",
    "LaunchRecord",
    "SalesMotion",
    "SceneConflict",
    "Workstream",
    "WorkstreamAction",
    "WorkstreamQueue",
    "WorkstreamState",
    "WorkstreamStateError",
    "check_workstream_id",
    "effective_scene_digest",
    "launch_subject",
    "workstream_id",
]


class WorkstreamStateError(ValueError):
    """一次不被允许的 Workstream 状态迁移。

    单独一个类而不是 ``ValueError``：调用方要能把它与「入参形状不对」分开 ——
    前者是「这件事在这个状态上做不了」（处置是去看状态），后者是「这句话本身
    构造不出来」（处置是改请求）。两者在日志上都是 ``ValueError``，
    而处置相反。
    """


class WorkstreamState(str, Enum):
    """一个 Workstream 在**控制面**上到哪一步了。

    两个成员，不多不少。**没有 ``CLOSED``**：本阶段没有任何一条工具承诺过结束一个
    工作流，而一个今天不可达的状态会让「它是什么」这个问题永远没有实测的答案 ——
    本仓的纪律是未知不写成一个像样的值。
    """

    RUNNING = "running"
    PAUSED = "paused"


#: 允许的迁移。与 ``domain/scene.py::SCENE_TRANSITIONS`` 同形、同一条理由：
#: 一张表比散在几个 ``if`` 里的判断更容易读全，而「恢复」与「暂停」不是同一条边
#: 的两个方向 —— 后者要一个原因，前者不要。
WORKSTREAM_TRANSITIONS: dict[WorkstreamState, frozenset[WorkstreamState]] = {
    WorkstreamState.RUNNING: frozenset({WorkstreamState.PAUSED}),
    WorkstreamState.PAUSED: frozenset({WorkstreamState.RUNNING}),
}


class WorkstreamAction(str, Enum):
    """``control_workstream`` 收的那一档**带类型**的动作。

    冻结表把这条工具写成「带类型的暂停 / 恢复 / 改名」，而「带类型」的落点就是
    这里：一个字符串参数（``"pause"``）在拼错时是**静默的**（它落到 else 分支，
    而 else 分支总是可以做点什么），闭集枚举在构造期就红。三档各自的必填参数
    写在 :func:`WorkstreamAction.requires_reason` 上 —— 只有暂停要原因。
    """

    PAUSE = "pause"
    RESUME = "resume"
    RENAME = "rename"

    @property
    def requires_reason(self) -> bool:
        """这一档动不动得了一个**必填**的原因。

        只有 ``PAUSE``。逐字理由见 ``marketops/security/catalog.py`` 里
        ``control_workstream`` 那条：「一个没有原因的暂停，三天后没人说得清该不该
        恢复」。恢复不要原因 —— 恢复是把一个已经被人写下来的决定撤销，而那个决定
        本身就带着原因。
        """
        return self is WorkstreamAction.PAUSE

    @property
    def target_state(self) -> WorkstreamState | None:
        """这一档把状态推到哪一格。``RENAME`` 不推状态（``None``）。"""
        if self is WorkstreamAction.PAUSE:
            return WorkstreamState.PAUSED
        if self is WorkstreamAction.RESUME:
            return WorkstreamState.RUNNING
        return None


class WorkstreamQueue(str, Enum):
    """启动那一刻**排队状态**的一格。

    逐字来自 PSM §4.2 第 5 条：「返回 launch/Scene/job id、**实际排队状态**、
    原因及何时会再次醒来；不能只把一个表字段改 active」。所以这一格必须是**说
    出来的**，而不是「回执里有没有 run_id」推出来的 —— 一个没有 run_id 的回执
    在报告上既可能是「排队中」也可能是「受理失败」，而两者的处置相反。

    * :attr:`RUN_ADMITTED` —— 第一轮 Run 已经被**既有的准入路径**受理并落盘。
    * :attr:`IDLE` —— 工作流已建、这一轮没有被受理。它今天只在**重发**上出现：
      重发回的是同一次启动，而那一次的第一轮早就受理过了；读的人据这一格知道
      「没有新的一轮被开出来」。
    """

    IDLE = "idle"
    RUN_ADMITTED = "run_admitted"


def workstream_id(scene_id: str, ordinal: int) -> str:
    """Workstream 的业务键：``<scene_id>#<ordinal>``。**冻结表 §3.2 逐字。**

    ``ordinal`` 在**同一个 scene_id 内**单调递增 —— 不是全局的，也不是按
    workspace 的。用 ``#`` 而不是 ``SEP``：这一格是给人读的（冻结表把它写成
    业务键的形状就是这个字面形状），而它必须能从一个 id 反推出「哪个场景的第几次
    启动」。``scene_id`` 走 ``check_canonical_id``，而那个函数保证它不含 ``#``
    （见 ``util.py::check_canonical_id`` 的字符集），所以拼接是无歧义的。
    """
    check_canonical_id(scene_id, "scene_id")
    if ordinal < 1:
        raise ValueError(
            f"ordinal 从 1 开始，给的是 {ordinal}。\n"
            "0 与「还没有第几次启动」是同义词，而把一个不存在的序号写进业务键会让"
            "两个不同的东西（还没有，与第一次）读成同一个 id。"
        )
    return f"{scene_id}#{ordinal}"


def check_workstream_id(value: str, field: str = "workstream_id") -> str:
    """一个字符串**是不是**那个业务键 —— :func:`workstream_id` 的读法。

    它存在的理由是**这一格被抄到了别处**：``RunSpec.workstream`` 要能说「这一轮
    属于哪一次启动」，而它装的就是这个键。那一格上如果只判「非空」，一个
    ``"#"``、一个 ``"devon-2026"``（少了序号）、或者一个 ``ordinal`` 为 0 的键
    都会被收下 —— 而三种都在盘上长得像「这一轮属于某个工作流」，读的人要等
    照它去读那一行才发现读不回来。

    **一个定义、两处使用者**：生产者是 :func:`workstream_id`，这一条是它的逆。
    两处各写一份形状判断的话，漂移的表现是「造得出来的键读不回来」。

    判据逐条对齐生产者：前缀过 ``check_canonical_id``（那个函数保证它不含 ``#``，
    所以只有一个 ``#`` 是无歧义的）、后缀是 ``>= 1`` 的十进制整数（``int()`` 收
    得下的写法比这个宽 —— ``"-1"`` / ``" 3"`` / ``"0x2"`` 都不是这里要的形状）。
    """
    head, sep, tail = value.partition("#")
    if not sep or not tail or "#" in tail:
        raise ValueError(
            f"{field} 不是 ``<scene_id>#<ordinal>`` 这个形状：{value!r}。\n"
            "  少一个 ``#`` 与多一个 ``#`` 都读不出「哪个场景的第几次启动」—— 而"
            "两者在报告上都长得像一个正常的工作流 id。"
        )
    check_canonical_id(head, f"{field} 的 scene_id 那一段")
    if not tail.isdigit() or int(tail) < 1:
        raise ValueError(
            f"{field} 的序号那一段不是从 1 起的十进制整数：{value!r}（读到 "
            f"{tail!r}）。\n"
            "  0 与「还没有第几次启动」是同义词，而 ``int()`` 收得下的写法比这个"
            "形状宽（符号、空白、``0x`` 前缀）—— 一个能通过的写法会在盘上表示一个"
            "生产者造不出来的工作流。"
        )
    return value


def launch_subject(scene_id: str, version: int, idempotency_key: str) -> str:
    """一次启动的**主语**：``launch:<scene_id>@<version>/<key>``。

    与 ``application/launch_preview.py::launch_subject`` 同一个形状、同一条理由：
    回执上的主语要能自己说出「这一次是哪一版、拿哪个键提的」。三个都进主语是因为
    它们三个合起来才是那条 UNIQUE 的业务键 —— 少一个，两条不同的记录会在报告上
    同形。
    """
    check_canonical_id(scene_id, "scene_id")
    if version < 1:
        raise ValueError(f"版本号从 1 开始，给的是 {version}")
    if not idempotency_key.strip():
        raise ValueError("主语里不能带一个空幂等键 —— 空键的启动不止一次")
    return f"launch:{scene_id}@{version}/{idempotency_key}"


class LaunchRecord(StrictModel):
    """**已批准的一次启动**。冻结表 §3.2：单写、不可变。

    ## 它为什么是「记录」而不是「请求」

    一条 LaunchRecord 落盘之后**不再改**：改场景要新开一次启动，改预算要新开一次
    启动。它是 PSM §4.2 第 4 条那句「获授权者批准具体 Scene、目标、研究预算与
    持续维护周期/累计上限，**形成版本化 LaunchRecord**」的落点 —— 而「版本化」
    在这里的形态是**多条记录**，不是同一行的多次改写：一条被就地改过的记录会让
    「当时批准的是什么」这个问题在盘上无迹可寻。

    ## ``request_digest`` 是「同一次」的第二半

    业务键（``workspace`` / ``scene_id`` / ``scene_version`` / ``idempotency_key``）
    答的是「这是**哪一次意图**」；这一格答的是「这一次意图**内容是不是同一份**」。
    两半缺一不可，与 ``domain/run.py::spec_fingerprint`` 和
    ``domain/job.py::request_fingerprint`` 是同一条纪律：

    * 只有业务键 ⇒ 拿同一个键交一份**别的**请求会静默拿回第一次那一行，而调用方
      以为自己开的那一轮跑的就是他要的东西。
    * 只有摘要 ⇒ 幂等键失去作用（同一个键配同一份内容会被算成新的一次）。

    所以存储那一层的处置是：键撞上、摘要也相等 ⇒ **回同一行**；键撞上、摘要不等
    ⇒ 报错并点名（``product_catalog.py::_insert_and_compare`` 是同一条，那一份的
    注释逐字说了这件事）。
    """

    #: 这一条记录的 id，由 :func:`launch_id_of` 从业务键推出。
    launch_id: str
    workspace: str
    scene_id: str
    #: 绑的是这一版 Scene 的**整数编号**，不是 profile 目录的字节摘要 ——
    #: 与 ``OutreachDraft.scene_version`` 同一条分野，理由见那里的 docstring。
    scene_version: int = Field(ge=1)
    #: 调用方给的。服务端不生成 —— 与 ``RunSpec.idempotency_key`` 逐字同一条。
    idempotency_key: str
    #: 这次启动建出来的工作流。它是**下游**，但记在这里而不是反过来：读一条
    #: LaunchRecord 的人第一个要问的是「它启动了哪一个工作流」。
    workstream_id: str
    #: 这次启动受理的第一轮 Run。**空串 = 那一轮没有被受理** —— 那不是失败，
    #: 是一种要说得出来的结果（照 PSM §4.2 第 5 条：缺容量是 waiting、
    #: 缺资料是 blocked，而「配置完成」不是「已执行」）。
    run_id: str = ""
    #: 上面四元组之外**这次请求的全部内容**的摘要。见类 docstring。
    request_digest: str
    created_at: datetime
    #: 谁批准的。身份不在这里判（那属于安全层），但**必须记下来**：
    #: 一条说不出是谁批准的启动，在复盘时与一条没人批准过的长得一样。
    launched_by: str

    @field_validator("launch_id", "workspace", "scene_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("workstream_id")
    @classmethod
    def _the_business_key_is_a_reference(cls, value: str) -> str:
        """业务键走**它自己的**读法，**不**走 ``check_canonical_id``。

        2026-09-29 实测：把 ``workstream_id`` 交给 ``check_canonical_id`` 会让
        **每一个** Workstream 都构造不出来 —— 冻结表 §3.2 的业务键是
        ``<scene_id>#<ordinal>``，而 ``slugify`` 会把那个 ``#`` 换成 ``-``，于是
        校验报的是「它不是规范形式」（``scene-uk-natural-grass#1`` 会被规范成
        ``scene-uk-natural-grass-1``）。

        这正是 ``util.py::check_canonical_id`` 的 docstring 自己点名的那种误用：
        「用它去校验一个**引用**是错的：``account:acme`` / ``view:v1`` /
        ``workspace/run`` 都是本仓真实存在的引用形状，而它们一个都不是文件名」。
        这一格是**引用**（``RunSpec.workstream`` 装的就是它），不是文件名 ——
        它没有落盘面。

        换掉它**不是**放松：:func:`check_workstream_id` 是 :func:`workstream_id`
        的逆，判的形状比原来那一条更贴（少一个 ``#``、多一个 ``#``、``ordinal`` 为
        0 都被它挡下，而那三种在 ``check_canonical_id`` 底下只会因为别的原因被挡，
        或者根本不被挡）。判据仍然**只有一个定义处**。
        """
        return check_workstream_id(value, "workstream_id")

    @field_validator("idempotency_key")
    @classmethod
    def _key_is_not_blank(cls, value: str) -> str:
        """空键与带空白的键都不是键。与 ``RunSpec._key_identifies_this_submission``
        逐字同一条推理，而这里更贵一点：空键会让**每一次**不带键的启动都撞在
        UNIQUE 上，于是第二次启动拿回第一个场景的启动记录，而两边都自洽。
        """
        if not value.strip():
            raise ValueError(
                "幂等键不得为空或全是空白。\n"
                "空键会让这条 UNIQUE 把**每一次**不带键的启动都判成同一次 —— "
                "报告上它看起来像一次成功的重发。\n"
                "  这里只判、不改写（不 ``strip()`` 收下它）：改写会让两个不同的"
                "输入指向同一行，而「报的是哪个键」与「用的是哪个键」从此不是同一个"
                "字符串。"
            )
        return value

    @field_validator("launched_by")
    @classmethod
    def _launcher_is_named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "launched_by 不得为空 —— 没有人签字的启动在复盘时与一次没人批准的"
                "启动长得一样。"
            )
        return value

    @field_validator("request_digest")
    @classmethod
    def _digest_shape(cls, value: str) -> str:
        return _as_sha256(value, "request_digest")

    @field_validator("run_id")
    @classmethod
    def _run_ref_may_be_absent(cls, value: str) -> str:
        """空串 = 那一轮没有被受理。**允许**，而它必须与一个真 id 分得开。

        与 :meth:`Workstream._run_ref_may_be_absent` 逐字同一条 —— 两处各写一份的
        那一天，「这一轮没有被受理」与「记了一个读不回来的 run_id」会在两条记录上
        分成两种命运，而它们是同一条链的两面。
        """
        if not value:
            return ""
        return check_canonical_id(value, "run_id")

    @model_validator(mode="after")
    def _the_id_is_the_one_the_key_produces(self) -> "LaunchRecord":
        """``launch_id`` 必须**就是**那四格推出来的那一个。

        这一格是内容寻址的（:func:`launch_id_of` 是它唯一的生产者），而校验器是
        把那句话变成**构造**：一个手填的 id 会在重发时认不出是同一次启动 ——
        两次提交各自都「成功」，而盘上多了一条谁也说不清是谁的启动记录。

        与 :meth:`Workstream._the_id_says_which_ordinal` 是同一条纪律：id 里说的话
        与字段里说的话必须是同一句。
        """
        expected = launch_id_of(
            workspace=self.workspace,
            scene_id=self.scene_id,
            scene_version=self.scene_version,
            idempotency_key=self.idempotency_key,
        )
        if self.launch_id != expected:
            raise ValueError(
                f"launch_id 与那四格业务键对不上：记的是 {self.launch_id!r}，按四格算"
                f"出来是 {expected!r}。\n"
                "  「同一个请求回同一个 launch」这句话的载体就是这一格 —— 一个手填的"
                " id 会让同一次请求的第二次提交建出**第二个** launch，而两边都长得"
                "像成功。要换内容就换幂等键。"
            )
        return self

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @property
    def business_key(self) -> tuple[str, str, int, str]:
        """那条 UNIQUE 的四格。**写成属性而不是让每个读者自己拼一遍** ——
        两处拼错的形态是「读回时少了一格」，而它读起来像「这条记录不存在」。
        """
        return (self.workspace, self.scene_id, self.scene_version, self.idempotency_key)


def launch_id_of(*, workspace: str, scene_id: str, scene_version: int, idempotency_key: str) -> str:
    """``launch_id`` 的**唯一**生产者 —— 内容寻址，不含时钟。

    「同一个请求回同一个 launch」这句话在这一格上是**构造**而不是自觉：id 由那四格
    散出来，所以两次调用在拼 id 这一步就不可能得到两个不同的值。带上一个自增序号
    或一个时刻的实现，会让同一次请求的第二次提交拿到一个新 id，而**调用方看不出来**：
    两个 id 都合法，回执都长得像成功。

    用 ``SEP`` 而不是 ``|`` / ``:`` / ``-`` 拼：``workspace`` 与 ``scene_id`` 都是
    受控 id，但 ``idempotency_key`` 是调用方给的，它**可以**含冒号 —— 于是
    ``("a:b", "c")`` 与 ``("a", "b:c")`` 会拼成同一串，而那是两次不同的启动。

    ## 摘要取 32 位，而这一格是**构造**的

    ``LaunchRecord.launch_id`` 过 ``check_canonical_id``，而它的上限是
    ``FILENAME_ID_MAX_LENGTH = 60``：``"launch-" + 64 位摘要`` 是 71 个字符，
    ``slugify`` 会把它截到 60 —— 于是**这个生产者造出来的 id 被它自己的模型拒了**
    （2026-09-29 实测：契约测试第一条构造就报「不是规范形式」）。

    取 32 位（128 bit，与 ``SceneVersion.frozen_sha256`` 同一个量级）之后是 39 个
    字符，且全落在 ``[a-z0-9-]`` 里、首尾不是 ``-`` —— ``slugify`` 在上面是恒等。
    末尾那次 ``check_canonical_id`` 是**构造**而不是自觉：换成别的拼法若越过了 60，
    它会在这里报，而不是等到某一次 ``LaunchRecord(...)`` 上以「模型坏了」的形状出现。
    """
    check_canonical_id(workspace, "workspace")
    check_canonical_id(scene_id, "scene_id")
    if scene_version < 1:
        raise ValueError(f"版本号从 1 开始，给的是 {scene_version}")
    if not idempotency_key.strip():
        raise ValueError("幂等键不得为空 —— 见 LaunchRecord 的同名校验器")
    return check_canonical_id(
        "launch-"
        + sha256_text(
            SEP.join(("launch", workspace, scene_id, str(scene_version), idempotency_key))
        )[:32],
        "launch_id",
    )


class Workstream(StrictModel):
    """**一个已经被受理的经营任务**。冻结表 §3.2 的业务键是 ``<scene_id>#<ordinal>``。

    ## ``revision`` 是 CAS 的载体

    ``control_workstream`` 收 ``expected_revision``，而这一格就是它比的那一格。
    「过期被拒而不是覆盖」的落点是存储那一层的 ``WHERE revision = ?`` +
    ``rowcount``（``adapters/sqlite/scenes.py::_update_record`` 是同一条），
    这一层只保证 revision 是一个**单调递增、每次写都变**的整数 —— 没有它，
    CAS 没有可比的量。

    ## ``next_wake_at`` 说的是「什么时候会自己醒」而不是「排了什么事」

    它取自受理那一轮的**自己的地平线**（``RunSpec.cutoff``），不是一份时刻表：
    本构建里没有任何东西会按一张表去唤醒一个工作流（那是 B25 的
    ``schedule_definition`` / ``fire_schedule``）。所以：

    * ``execution_mode="manual"`` ⇒ ``None``，且这是**说出来的**「没有人会来叫它」
      —— 手动那一轮是人拉起来的，而这一格没有把它伪装成一个周期任务。
    * 暂停 ⇒ ``None``（一个被暂停的工作流不该出现在任何人的唤醒名单上）。
    * 恢复 ⇒ **不还原**上一次的值。恢复是一个新的决定，把它还原成「暂停前那一刻」
      会让一个三天前的时刻重新变成「下次醒来」，而没有任何人做过那个决定。
    """

    workstream_id: str
    workspace: str
    scene_id: str
    #: 建出它的那一次启动。与 ``LaunchRecord.workstream_id`` 是**同一条边的两面**，
    #: 而两格都存：只存一面的话，读任意一面都要扫另一张表，而「扫描」这件事在
    #: 读回面上是看不出来的。
    launch_id: str
    #: 这一版 Scene 的编号。
    scene_version: int = Field(ge=1)
    #: 在**同一个 scene_id** 内的第几次启动。1 起。
    ordinal: int = Field(ge=1)
    #: 人给的名字。可以为空串（「没起名字」是一个说得出来的状态）。
    name: str = ""
    state: WorkstreamState
    #: 与存储那一层的 CAS 比的那一格。**从 1 开始**，每次写 +1。
    revision: int = Field(ge=1)
    queue: WorkstreamQueue
    #: 这次启动受理了的那一轮。空串 = 没有被受理，见 :class:`LaunchRecord.run_id`。
    run_id: str = ""
    #: 「什么时候会自己醒」。见类 docstring。``None`` 是**说出来的**，不是省略。
    next_wake_at: datetime | None = None
    #: 暂停的原因。**只有 ``PAUSED`` 允许非空**，而 ``PAUSED`` 时必填 ——
    #: 见 :meth:`_paused_says_why`。
    pause_reason: str = ""
    created_at: datetime
    updated_at: datetime

    @field_validator("workspace", "scene_id", "launch_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("workstream_id")
    @classmethod
    def _the_business_key_is_a_reference(cls, value: str) -> str:
        """同 :meth:`LaunchRecord._the_business_key_is_a_reference` —— 这一格是
        ``<scene_id>#<ordinal>``（冻结表 §3.2 逐字），而 ``#`` 不是 ``slugify``
        的不动点。判据是 :func:`check_workstream_id`，与生产者成对。
        """
        return check_workstream_id(value, "workstream_id")

    @field_validator("run_id")
    @classmethod
    def _run_ref_may_be_absent(cls, value: str) -> str:
        """空串 = 这一轮没有被受理。**允许**，而它必须与一个真 id 分得开。"""
        if not value:
            return ""
        return check_canonical_id(value, "run_id")

    @field_validator("created_at", "updated_at", "next_wake_at")
    @classmethod
    def _aware(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @model_validator(mode="after")
    def _the_id_says_which_ordinal(self) -> "Workstream":
        """业务键与它那两格必须说的是同一件事。

        两格各写一遍就会有一个自洽的假答案：一个 ``ordinal=3`` 而 id 是
        ``...#1`` 的对象，读的人据 id 说「这是第一次启动」，据字段说「第三次」，
        而**两句话都由这一行支持**。id 是业务键（外面拿它寻址），所以 id 说了算。
        """
        expected = workstream_id(self.scene_id, self.ordinal)
        if self.workstream_id != expected:
            raise ValueError(
                f"workstream_id {self.workstream_id!r} 与 scene_id/ordinal 对不上："
                f"按 {self.scene_id!r} 的第 {self.ordinal} 次启动应当是 {expected!r}。\n"
                "  业务键是外面寻址用的那一格，而 ordinal 是它读得出来的那一格 —— "
                "两者不一致时这一行会支持两个互相矛盾的读法。"
            )
        return self

    @model_validator(mode="after")
    def _paused_says_why(self) -> "Workstream":
        """``PAUSED`` 必须带原因；``RUNNING`` 不得带。

        与 ``SceneRecord`` 的 ``pause_reason`` 逐字同一条纪律（那里写着：「缺理由的
        暂停与「有人点了一下」在报告上无法区分」）。这里多一半：**运行中带着一个
        原因**同样是错的 —— 恢复时不把 ``pause_reason`` 清掉的实现，会让一个正在
        跑的工作流在报告上带着一条「为什么停着」的说明。
        """
        if self.state is WorkstreamState.PAUSED and not self.pause_reason.strip():
            raise ValueError(
                f"工作流 {self.workstream_id!r} 是暂停的，却没有 pause_reason。\n"
                "  一条说不出为什么停着的记录，三天后没人说得清该不该恢复它 —— "
                "而那是 ``control_workstream`` 这条工具存在的理由。"
            )
        if self.state is WorkstreamState.RUNNING and self.pause_reason.strip():
            raise ValueError(
                f"工作流 {self.workstream_id!r} 在运行中，却带着 pause_reason "
                f"{self.pause_reason!r}。\n"
                "  恢复时不清掉原因的实现在报告上会说「它一边跑一边因为某个理由停着」"
                "—— 而那句话没有处置：读的人不知道要不要去处理那个理由。"
            )
        return self

    @model_validator(mode="after")
    def _updated_is_not_before_created(self) -> "Workstream":
        if self.updated_at < self.created_at:
            raise ValueError(
                f"updated_at（{self.updated_at.isoformat()}）早于 created_at"
                f"（{self.created_at.isoformat()}）—— 一次改动不可能发生在建它之前。"
            )
        return self

    @property
    def business_key(self) -> str:
        """一行就是一行：Workstream 的业务键**没有** workspace 那一格。

        与 ``launch_record`` 的四元组不同，冻结表把 Workstream 的键写成
        ``<scene_id>#<ordinal>``，而 ``scene_id`` 本身在 ``SceneVersion`` 上带
        workspace —— 「同一个场景名在两个工作区里各有一份」这件事由 Scene 那一层
        挡住，不在这里再挡一遍。
        """
        return self.workstream_id

    def accepts_new_work(self) -> bool:
        """这个工作流今天放不放新的活。**只有状态**这一半 —— 场景那一半在
        ``SceneRecord`` 上（见 ``application/workstream.py`` 的派发读数）。
        """
        return self.state is WorkstreamState.RUNNING


class GeoScope(StrictModel):
    """**有来源及版本的国家/行政区/地区集合，含排除区。** PSM §2 逐字。

    它**不是**一个自由文本搜索词（PSM 那一行同时写着「不等于：只有自由文本
    ``Devon`` 的搜索词」）。所以这里的形状是：

    * ``countries`` —— ISO 3166-1 alpha-2（大写）。它答「覆盖哪些国家」。
    * ``regions`` —— **有序**的行政区 / 地区集合。有序是有意的：``("GB-England-Devon",
      "GB-England-Cornwall")`` 与反过来是同一个集合、不同的**优先次序**，而
      「先做 Devon」这句话在报告上必须留得住。
    * ``exclusions`` —— 排除区。**不得为空**（与 ``SceneVersion.exclusions``
      逐字同一条理由：一个什么都不排除的范围不是一个更宽松的配置，是一个没有边界
      的采集）。

    ## 它没有自己的表

    任务书 §「你的五张表」逐字：「``GeoScope`` / ``SalesMotion`` 是**编译输入**，
    落在 ``effective_scene.payload`` 里」。所以这一层不产出存储行，只产出**值**。
    """

    countries: tuple[str, ...]
    regions: tuple[str, ...]
    exclusions: tuple[str, ...]
    #: 这个范围从哪来（哪一份 profile / 哪一条声明）。``source`` 与 ``provenance``
    #: 是 B02-b 逐字要的两格，两者分开：一个是「哪份材料」，一个是「这份材料凭什么
    #: 算数」。
    source: str
    provenance: str
    #: 这份范围在**哪一刻**被读出来。``GeoScope`` 会被下一次改版换掉，所以
    #: 「你读的是哪一刻的它」是它的一部分。
    at: datetime
    #: 这份范围的负责人。PSM §5 的冻结绑定里有 ``owner`` 这一格。
    owner: str

    @field_validator("countries")
    @classmethod
    def _countries_are_country_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "countries 不得为空。一个不覆盖任何国家的范围不是一个更宽松的范围，"
                "是一个**没有主语**的范围 —— 此后每一处问「这个对象在不在范围内」"
                "都会得到一个恒假的答案，而报告上它看起来只是「一无所获」。"
            )
        if len(set(value)) != len(value):
            raise ValueError("countries 不得有重复项 —— 重复会让「覆盖几个国家」这个数读错")
        return tuple(check_market_country(item, "countries") for item in value)

    @field_validator("regions")
    @classmethod
    def _regions_are_ordered_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("regions 不得含空串")
        if len(set(value)) != len(value):
            raise ValueError(
                "regions 不得有重复项。重复项会让「优先次序」这一格失去意义（同一个"
                "地区出现两次时，它在第几位没有答案），而次序正是这一格存在的理由。"
            )
        return value

    @field_validator("exclusions")
    @classmethod
    def _exclusions_are_declared(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "exclusions 不得为空。与 ``SceneVersion.exclusions`` 逐字同一条理由："
                "一个什么都不排除的范围在装载层就装不出来，而在这里放它过去会产出一个"
                "「范围无限大」的 GeoScope。"
            )
        if any(not item.strip() for item in value):
            raise ValueError("exclusions 不得含空串")
        return value

    @field_validator("source", "provenance", "owner")
    @classmethod
    def _non_empty(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(
                f"{info.field_name} 不得为空。B02-b 要的六格（source / provenance / "
                "units / locale / owner / hash）里，这一格答的是「凭什么算数」——"
                "空着的时候，一份范围和一句模型的猜测在盘上同形。"
            )
        return value

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    def covers(self, region: str) -> bool:
        """这个范围含不含某个行政区。

        **排除区先判**：一个既在 ``regions`` 里又在 ``exclusions`` 里的地区是
        **被排除**的。两个都含时取排除，是因为另一条读法（取包含）会让「把它从
        这个范围里拿掉」这个动作在盘上不留痕 —— 而它恰恰是最需要留痕的那一次。
        """
        if region in self.exclusions:
            return False
        return region in self.regions

    @property
    def digest(self) -> str:
        """这一份范围的内容摘要。进 ``EffectiveScene`` 的格哈希。"""
        return sha256_text(
            SEP.join(
                (
                    "geo-scope",
                    *self.countries,
                    *self.regions,
                    *self.exclusions,
                    self.source,
                    self.provenance,
                    self.owner,
                )
            )
        )


class SalesMotion(StrictModel):
    """**商业关系**：终端销售、维护承包商客户、渠道招募等（PSM §2 逐字）。

    ## 为什么这一格不是闭集枚举

    PSM 那一行的定义以「等」收尾，而同一张表的右列逐字写着它**不等于**
    「自动由组织名字推断的'客户/竞品'标签」。两句合起来的意思是这个类型要能装下
    **还没有被命名的**商业关系 —— 一个闭集枚举会把「等」里那几种挡在外面，
    而挡住的形态是「这个市场的关系类型注册不了」，不是「配置写错了」。

    所以 ``motion`` 是一个**可核对的 ref**（``check_canonical_id``），
    而 ``relation`` 说它是什么关系。两格分开与 ``ApprovedClaim`` 的
    ``claim_id`` / ``text`` 同一条：一个是对内的键，一个是给人读的那句话。

    ## 它也没有自己的表

    同 :class:`GeoScope` —— 编译输入，落在 ``effective_scene.payload`` 里。
    """

    #: 这一档商业关系的 ref。可核对（同一份 ref 在两次编译里指向同一件事）。
    motion: str
    #: 它是什么关系，一句给人读的话。
    relation: str
    source: str
    provenance: str
    at: datetime
    owner: str

    @field_validator("motion")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "motion")

    @field_validator("relation", "source", "provenance", "owner")
    @classmethod
    def _non_empty(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} 不得为空 —— 见 SalesMotion 的类 docstring")
        return value

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)


def _as_sha256(value: str, field: str) -> str:
    """一个 64 位十六进制摘要 —— 与 ``domain/outreach.py::_as_sha256`` 同形。

    这一份是本模块自己的（那个函数在 ``outreach`` 里是私有的，而 domain 层内部
    import 一个下划线名字会让「谁改它算破坏契约」说不清）。判据只有一条：
    形状对不对 —— 一个 ``""`` 或 ``"tbd"`` 混进去，读的人会拿它去比对，
    而**任何东西都不等于它**，于是那条比对恒假而报告上看不出来。
    """
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(
            f"{field} 不是一份 sha256 摘要：{value!r}。\n"
            "  它此后要拿去比对，而一个形状不对的值会让那次比对恒假 —— "
            "报告上它与「内容真的变了」长得一样。"
        )
    return value.lower()


class EffectiveCell(StrictModel):
    """``EffectiveScene`` 里**一格**编译出来的东西。

    B02-b 逐字要六格：source / provenance / units / locale / owner / hash。
    本类把它们逐格落成字段，而不是折成一个字典 —— 折成一个 ``{"source": ...}``
    的实现在**跨格校验**上就什么也判不了（一个缺 ``owner`` 的格子与一个
    ``owner=""`` 的格子在字典里同形）。

    ``precedence`` 与 ``overrides`` 是 B02-b 的另外半句：「**优先级逐条写出来**」。
    某一格被谁覆盖过、按什么次序覆盖，在这两格里；而 ``value`` 只是**算完之后**
    的那一个值。没有这两格的话，「为什么是这一个值」在盘上没有答案。
    """

    #: 这一格叫什么（``geo_scope`` / ``offer`` / ``sales_motion`` / 某条 policy）。
    field: str
    #: 算完之后的那一个值。**总是字符串** —— 它不是「任意 JSON」：一个能装任意
    #: 形状的值会让「这一格有没有」这个问题在类型上失去判据。
    value: str
    #: 这份值从哪来（哪份材料、哪一行）。
    source: str
    #: 这份材料凭什么算数（approved / declared / derived …）。
    provenance: str
    #: 单位。无单位时是一个**说出来的** ``"-"`` 而不是空串 —— 空串与「忘了填」
    #: 同形，而没有单位的数字在这一层是不合法的（见 ``_unit_is_stated``）。
    unit: str
    #: 语言（主语言子标签，``en`` 而不是 ``en-GB``）。同 ``unit``：无语言时写 ``"-"``。
    locale: str
    #: 这一格的负责人。
    owner: str
    #: 值的摘要。**内容寻址**：同一个值在两次编译里散出同一个串。
    value_sha256: str
    #: 这一格在冲突里的次序。**越小越优先**，而它**不决定胜负** —— 冲突逐条点名、
    #: 不取赢家（见 :class:`SceneConflict`）。这一格只回答「按声明的次序，谁先说话」。
    precedence: int = Field(ge=0)
    #: 被这一格覆盖掉的那些读法（各自的 ref）。逐条写出来，不折成一个数。
    overrides: tuple[str, ...] = ()

    @field_validator("field")
    @classmethod
    def _field_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "field")

    @field_validator("source", "provenance", "owner")
    @classmethod
    def _non_empty(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(
                f"{info.field_name} 不得为空 —— B02-b 的六格是这一层产出里最该被看见的"
                "那半句，而空着的那一格恰恰是「这一格是谁说的、凭什么」没有答案。"
            )
        return value

    @field_validator("unit", "locale")
    @classmethod
    def _unit_is_stated(cls, value: str, info) -> str:
        if not value:
            raise ValueError(
                f"{info.field_name} 不得为空。没有{info.field_name}时写 ``'-'`` —— "
                "它是**说出来的**「这一格没有单位/语言」，与「忘了填」不是同一件事，"
                "而两者在一个空串上是同形的。"
            )
        return value

    @field_validator("value")
    @classmethod
    def _value_may_be_empty_but_is_stated(cls, value: str) -> str:
        """值本身**可以**为空串（有些格子就是空的），但 ``value_sha256`` 会跟着变。

        这条校验器只做一件事：把「空值」与「没这一格」分开的责任交给
        ``value_sha256`` —— 空串的摘要是 ``e3b0c442...``，任何实现都不可能把它与
        「忘了散」混起来。
        """
        return value

    @field_validator("value_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _as_sha256(value, "value_sha256")

    @model_validator(mode="after")
    def _the_digest_is_the_value(self) -> "EffectiveCell":
        """摘要必须是**这个值**的摘要。

        两格各写一遍就会有一个自洽的假答案：一个 ``value`` 被就地改过而摘要没跟上的
        格子，读的人拿摘要去比对时会说「这个值没变」—— 而它变了。这与
        ``SceneVersion.frozen_sha256`` 是**同一条**：摘要要能当「这一格长什么样」的
        凭据用，前提是它真的是从这一格算出来的。
        """
        expected = sha256_text(self.value)
        if self.value_sha256 != expected:
            raise ValueError(
                f"格 {self.field!r} 的 value_sha256 与它的值对不上：记的是 "
                f"{self.value_sha256}，按值算出来是 {expected}。\n"
                "  B02-b 要这一格是因为「同一格在两次编译里是不是同一个东西」只能由"
                "摘要回答 —— 而一个不是从值算出来的摘要会对任何比对恒真。"
            )
        return self


class SceneConflict(StrictModel):
    """**一处冲突，逐条点名。** B02-b 逐字：「冲突逐条点名，**不取赢家**」。

    一个「取赢家」的实现在报告上抹掉的恰恰是这一层最该被看见的那一格：
    「这里有两份说法」。抹掉之后，读的人看到的是一份干净、自洽、无从质疑的
    EffectiveScene —— 而它底下压着一次没人做过的裁定。

    所以本类里**没有** ``winner`` 那一格，而这不是省略：加上它，任何一处
    ``max(readings)`` 都会有一个自然的落点，而那条落点不会有人去质疑。
    """

    #: 冲突发生在哪一格上（与 ``EffectiveCell.field`` 同一套名字）。
    field: str
    #: 两份（或更多）读法各自的出处。**逐条**，一条一个来源。
    readings: tuple[str, ...]
    #: 一句话说清它们为什么对不上。**必填** —— 一条只列出两个值的冲突，读的人
    #: 不知道它们是不是真的矛盾（有可能是同一个值的两种写法，那是一个要去修的
    #: 规范化问题，不是一次冲突）。
    detail: str

    @field_validator("field")
    @classmethod
    def _field_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "field")

    @field_validator("readings")
    @classmethod
    def _two_or_more(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) < 2:
            raise ValueError(
                f"一条冲突至少要两份读法，给的是 {len(value)} 份。\n"
                "  一份读法不构成冲突 —— 把它放进这张表会让「有几处冲突」这个数虚高，"
                "而那个数是这一层产出里最容易被拿去当判据的一格。"
            )
        if any(not item.strip() for item in value):
            raise ValueError("readings 不得含空串 —— 一条说不出出处的读法等于没有出处")
        if len(set(value)) != len(value):
            raise ValueError("readings 不得有重复项 —— 同一个出处说两遍不是两处冲突")
        return value

    @field_validator("detail")
    @classmethod
    def _detail_says_what_differs(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "detail 不得为空。一条只列出两份读法的冲突，读的人分不清它是「两份"
                "材料真的对不上」还是「同一个值的两种写法」—— 前者要人去裁定，"
                "后者要去修规范化，两者的处置相反。"
            )
        return value


class EffectiveScene(StrictModel):
    """**声明的那一版 Scene 编译出来的、不可变的那一份**。B02-b。

    ## 它是不可变的，而这不是一句自觉

    主键是 ``(scene_id, version)``（冻结的 DDL 逐字），而写路径在第二次写同一个
    主键时**报错**而不是覆盖（``adapters/sqlite/workstreams.py``）。加上本类自带的
    :meth:`digest`（由内容散出来、不是调用方给的），「同一 ``(scene, version)`` 在
    两次部署之间是两份不同的东西」这件事在盘上**做不到** ——
    这正是 ``get_effective_scene`` 那条 summary 逐字点名的承重点。

    ## 冲突与格子是两张表，不是一张

    :attr:`cells` 是**算完之后**的值，:attr:`conflicts` 是**没算出来**的那部分。
    合成一张表的实现会让一处冲突长得像一格普通的值（`"value": "A | B"`），
    而读的人会当它是个字符串。
    """

    scene_id: str
    version: int = Field(ge=1)
    #: 这份编译结果属于哪个工作区。**它必须在这一格上**：``get_effective_scene``
    #: 之外还有别处要按工作区判定可见性，而一个只按 ``(scene_id, version)`` 存的
    #: 实现在跨工作区读的那条判据上就没有可比的量。
    workspace: str
    #: 编译它的那一版 Scene 的内容摘要（``SceneVersion.frozen_sha256``）。
    #: 它与 ``version`` 是两件事：版本号是可被重新解释的整数，摘要不是。
    compiled_from: str
    #: 编译的那一刻。
    at: datetime
    #: 谁编译的。
    compiler: str
    cells: tuple[EffectiveCell, ...] = ()
    #: 那些**算不出来**的格子，逐条点名（照 ``launch_preview`` 的
    #: ``LAUNCH_NOT_REPORTED`` 那一族）。它与 ``conflicts`` 不同：
    #: 冲突是「有两份说法」，这里是「一份都没有」。
    not_reported: tuple[str, ...] = ()
    conflicts: tuple[SceneConflict, ...] = ()

    @field_validator("scene_id", "workspace")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("compiled_from")
    @classmethod
    def _compiled_from_is_the_scene_digest(cls, value: str) -> str:
        """``compiled_from`` 装的是 ``SceneVersion.frozen_sha256``，**原样**。

        它的形状因此不是 64 位十六进制：``frozen_sha256`` 是
        ``sha256_text(...)[:32]`` —— 一个**截断过的**摘要，而这是实测出来的既有
        事实（它同时是授权门与 wave manifest 绑的那一格，本片不改它）。

        所以这里收 32 位或 64 位十六进制，而这不是一条宽松的判据：一条只收 64 位
        的实现会让 ``compile_effective_scene`` 在**每一次**调用上抛
        （2026-09-29 实测：传 ``frozen_sha256`` 进来报的正是这一条），于是「这一份
        编译结果是从哪一版规则来的」这条链根本接不上。收两个长度是为了容纳
        ``frozen_sha256`` 与「以后某个真按 64 位算的摘要」这两者，而不是为了放过
        ``""`` / ``"tbd"`` —— 它们仍然被挡在外面，而挡它们的理由与
        :func:`_as_sha256` 那一条逐字相同：一个形状不对的值此后要拿去比对，
        而任何东西都不等于它。
        """
        if len(value) not in (32, 64) or any(
            ch not in "0123456789abcdef" for ch in value.lower()
        ):
            raise ValueError(
                f"compiled_from 不是一份摘要：{value!r}（长度 {len(value)}）。\n"
                "  它装的是 ``SceneVersion.frozen_sha256`` 那一个值 —— "
                "32 位（截断过的，今天的实际形状）或 64 位都收。\n"
                "  一个形状不对的值会让「这份编译结果是从哪一版规则来的」那次比对"
                "恒假，而报告上它与「内容真的变了」长得一样。"
            )
        return value.lower()

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("compiler")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "compiler 不得为空 —— 一份说不出是谁编译的有效场景，在复盘时与一份"
                "没人编译过的长得一样。"
            )
        return value

    @model_validator(mode="after")
    def _cells_and_conflicts_do_not_overlap(self) -> "EffectiveScene":
        """同一格不能既算出值、又列在冲突里。

        两者同时出现只有两种可能：要么冲突被「顺手取了个赢家」写进了 ``cells``
        （那正是这一层不许做的事），要么 ``conflicts`` 里那一条已经被解决了而没人
        把它删掉。两种都让「这张表上有几处冲突」这个数不可信，而那个数是读的人
        唯一能据它决定「要不要去找人裁定」的一格。
        """
        overlap = {c.field for c in self.conflicts} & {c.field for c in self.cells}
        if overlap:
            raise ValueError(
                f"这几格既算出了值、又列在冲突里：{sorted(overlap)}。\n"
                "  冲突逐条点名而**不取赢家**（B02-b 逐字）—— 一处冲突若同时出现在"
                "cells 里，读的人会拿那一格当结论，而它底下压着一次没人做过的裁定。"
            )
        return self

    @model_validator(mode="after")
    def _fields_are_unique(self) -> "EffectiveScene":
        seen = [cell.field for cell in self.cells]
        if len(set(seen)) != len(seen):
            duplicated = sorted({name for name in seen if seen.count(name) > 1})
            raise ValueError(
                f"cells 里这几格出现了不止一次：{duplicated}。\n"
                "  同一格两条读数而其中一条是「后来的」—— 那正是「取赢家」的形态，"
                "而它在这一层是**做不出来**的：后写的那一条会静默盖掉前一条。"
            )
        return self

    @property
    def digest(self) -> str:
        """这份编译结果的摘要。**由内容散出来**，不是构造时传进来的。

        它是 ``get_effective_scene`` 回执上那一格，也是「同一 ``(scene, version)``
        的两次读是不是同一份东西」的唯一判据。哈希由调用方给的话，一个改过内容的
        实现只要把哈希一起改掉就看不出来了。
        """
        return effective_scene_digest(self)


def effective_scene_digest(scene: EffectiveScene) -> str:
    """:attr:`EffectiveScene.digest` 的唯一生产者。

    写成模块级函数而不是只做属性，是因为**存储那一层**也要它：读回一行的时候，
    它要能拿盘上的负载重算一遍再与 ``digest`` 列比 —— 那是「这一行有没有被就地
    改过」在库层的判据。两处各算一份的话，漂移的那天两边都自洽而比对恒真。
    """
    blocks = [
        "effective-scene",
        scene.scene_id,
        str(scene.version),
        scene.workspace,
        scene.compiled_from,
    ]
    for cell in scene.cells:
        blocks.extend(
            (
                f"cell:{cell.field}",
                cell.value_sha256,
                sha256_text(cell.source),
                sha256_text(cell.provenance),
                sha256_text(cell.unit),
                sha256_text(cell.locale),
                sha256_text(cell.owner),
                str(cell.precedence),
                *(sha256_text(item) for item in cell.overrides),
            )
        )
    for gap in scene.not_reported:
        blocks.extend(("not-reported", sha256_text(gap)))
    for conflict in scene.conflicts:
        blocks.extend(
            (
                f"conflict:{conflict.field}",
                *(sha256_text(item) for item in conflict.readings),
                sha256_text(conflict.detail),
            )
        )
    return sha256_text(SEP.join(blocks))
