"""跨测试文件共用的替身。

## 为什么这个替身要**记账**

``FakeDispatcher`` 把每一次调用记进 ``calls``。判据里几条最承重的断言是
``calls == []`` / ``"create" not in calls`` —— 它们问的是「**有没有碰过外部**」，
而这个问题只有记账才答得出来：一个「碰了但结果是幂等的」实现会让所有基于返回值的
断言照旧全绿，而它每一次重试都在外部留下一次真实的调用。

**一份实现，两个层次的调用方。** ``test_dispatch_ordering.py`` 用它测
``WorkDispatch`` 本身，``test_mcp_job_tools.py`` / ``test_mcp_control_job.py``
用它测服务面那条路径 —— 后者要回答的是「工具走完之后，外部被碰了几次」。两个文件
各写一份的话，两份会在「什么叫碰过」上漂移，而漂移的那一份恰好会让
「工具层静默重试」看起来是干净的。

外部 id 刻意用 ``EXT-…``，**不是**适配器那个 ``MOPS-…`` 形状：本层不知道也不该
知道实现怎么派生 id（那是执行器适配器自己的约定，本层不该问）。用同一个形状写替身，
会让「本层悄悄依赖了那个形状」这件事在测试里看不出来。

## 页面那一对替身也住在这里，理由与 ``FakeDispatcher`` 逐字相同

``FakeHttp`` / ``PageParser`` 是页面复核那一路的替身，而「抓了几次」是那一路最承重
的一个数：一轮 tick 里两个页面面共用同一批页面时，**每一页恰好抓一次**是 §B22 的
关闭条件之一，而它只在 ``FakeHttp.calls`` 上读得出来（``REUSED`` 与 ``UNCHANGED``
在观测那一格上长得一模一样）。

它们因此不能各文件一份。``application/refresh.py`` 那一层用它测编排，
``adapters/mcp_server/product_loop.py`` 那一层用它测「接线之后同一批页面还是一次」
—— 两个文件各写一份的话，两份会在「什么算一次抓取」上漂移，而漂移的那一份恰好会
让「工具层多抓了一次」看起来是干净的。「每一次取回都写盘」这个形状也是判据的一部分
（``body_path`` 必须指向一个真的文件，否则解析那一步测不到）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from marketops.domain.enums import FetchOutcome, FetchTier, SourceKind
from marketops.domain.errors import ExternalStateError
from marketops.domain.profile import SceneFile, SceneFiles
from marketops.domain.subject import SubjectDossier
from marketops.domain.util import sha256_text
from marketops.ports.acquisition import FetchRequest, FetchResult, HttpFetcher
from marketops.ports.dispatch import Dispatcher, ExecutionUnit, ExternalTask
from marketops.ports.documents import DocumentParser, ParsedDocument

#: 合成 Scene 快照的文件名。刻意用一个**任何 profile 目录里都不该有**的名字：
#: 它出现在一份真 Scene 里就是一次误接，而那时它得看得出来。
SYNTHETIC_SCENE_PATH = "synthetic-scene.yaml"

#: 合成 Scene 快照的内容。与文件名同理，是一段不像任何规则的东西。
#:
#: **ASCII 字面量**，不是 ``"…".encode()``：字节字面量里放不下中文，而这里要的是
#: 「一段不对应任何目录的字节」，不是「一句给人读的话」—— 那句话在 docstring 里。
_SYNTHETIC_SCENE_CONTENT = (
    b"# synthetic scene: these bytes belong to no directory under profiles/\n"
)


def synthetic_scene_files(*, tag: str = "") -> SceneFiles:
    """一个**不是从目录读来的** Scene 快照，给合成 bundle 用。

    ``ProfileBundle`` 要求 ``scene_version`` 是 ``scene_files`` 的版本，所以合成
    bundle 也需要一批字节。它们从前是一个哨兵字符串
    （``"synthetic-not-loaded-from-a-directory"``），现在这个哨兵性质由**字节**
    承载：这段内容不来自任何 profile 目录，所以它算出的摘要不等于任何真 profile
    的版本。

    差别不在「更好看」，在于**合成 bundle 从前是自相矛盾的**：它拿着一个版本号，
    却没有任何东西是那个版本。一个自相矛盾的夹具教不会任何人「版本与内容必须对上」
    —— 它恰好是那条规则的反例，而且每个读它的人都会学到「这一条不必当真」。

    ``tag`` 只为一件事存在：同一个文件里要能造出**两个不同**的合成快照。重新编译
    那几条判据（「换了一份规则就不能重钉」）要的是一条**真的分歧**，而拿同一个
    快照比两次证明不了任何事。tag 进字节，所以两份的版本必然不同 —— 而不是把
    ``SceneFiles.version`` 覆写成别的值（那个字段是算出来的，不是一个可以设的
    结论）。

    每次返回新对象（``SceneFiles`` 是 frozen 的，但共用一份没有好处，只有「谁改过
    它」的问题）。
    """
    content = _SYNTHETIC_SCENE_CONTENT
    if tag:
        content += f"# tag: {tag}\n".encode("ascii")
    return SceneFiles(entries=(SceneFile(path=SYNTHETIC_SCENE_PATH, content=content),))


def synthetic_dossiers(*account_ids: str) -> tuple[SubjectDossier, ...]:
    """一串内部 id → 一串**身份**，形状合理、而且**确定**。

    ``compile_work_items`` 收的是 ``SubjectDossier`` 而不是字符串（理由见
    ``ports/jobs.py``：一个只有 ``account_id`` 的任务包正是「worker 拿内部 slug
    去检索」那个缺口的形状）。所以存储层的判据要么各写一份身份，要么用这个。

    每份的字段全部由 id 派生 —— **确定性**是这里的要点：同一个 id 每次得到逐字节
    相同的身份，所以「同一份输入编译两次」在那几条幂等判据里测的真的是幂等，
    而不是「夹具每次都造一份新的，于是相等是碰巧的」。

    ``organisation`` 刻意不是 id 本身（``record-0001`` 这种名字不是身份），
    ``domain`` 刻意是 ``.example`` —— 保留域，不指向任何真站点。
    """
    return tuple(
        SubjectDossier(
            account_id=account_id,
            organisation=f"Synthetic Org {account_id}",
            domain=f"{account_id}.example",
            country="GB",
            source_ref="tests/fakes.py",
            input_sha256=sha256_text(f"synthetic-row:{account_id}"),
        )
        for account_id in account_ids
    )


class FakeDispatcher(Dispatcher):
    """记账的外部执行器。

    ``find`` 按 **AND** 语义过滤（端口逐字要求），``create`` 的 id 从 ``unit_id``
    派生 —— 派生方式**故意与真适配器不同**（见模块 docstring）。
    """

    def __init__(self) -> None:
        self.tasks: dict[str, ExternalTask] = {}
        self.calls: list[str] = []
        #: 逐次记录，用来断言 ``find`` 与 ``create`` 拿到的是**同一组** label。
        self.find_labels: list[tuple[str, ...]] = []
        self.create_labels: list[tuple[str, ...]] = []
        self.create_statuses: list[str] = []
        self.created_units: list[ExecutionUnit] = []
        #: 逐次记录 ``(external_id, expected, status)``。三个都要，因为一次 CAS
        #: 的**两端**都是判据：只记目标状态，会让「expected 写成了目标」这种
        #: 自说自话的迁移全绿。
        self.transitions: list[tuple[str, str, str]] = []

    # 摆状态用（不是被测对象的一部分）。
    def seed(
        self, external_id: str, labels: Sequence[str], *, status: str = "todo"
    ) -> ExternalTask:
        task = ExternalTask(external_id=external_id, status=status, labels=tuple(labels))
        self.tasks[external_id] = task
        return task

    def find(self, *, labels: Sequence[str]) -> tuple[ExternalTask, ...]:
        self.calls.append("find")
        self.find_labels.append(tuple(labels))
        wanted = set(labels)
        return tuple(task for task in self.tasks.values() if wanted <= set(task.labels))

    def create(
        self, unit: ExecutionUnit, *, labels: Sequence[str], status: str
    ) -> ExternalTask:
        self.calls.append("create")
        self.create_labels.append(tuple(labels))
        self.create_statuses.append(status)
        self.created_units.append(unit)
        return self.seed(f"EXT-{unit.unit_id[:6]}", labels, status=status)

    def transition(self, external_id: str, *, expected: str, status: str) -> ExternalTask:
        """一次 CAS 迁移，**照真适配器的行为**：读到的状态不等于 ``expected`` 就抛。

        ## 那句 ``raise AssertionError`` 去哪了

        这个替身原本在这里直接抛，理由是「派发这条路径不该调用 transition」。
        G5c 之后**结算**那条路径会用它，所以那句断言的前提没了。但它守的那件事
        还在，而且守得更准：``calls`` 逐次记账，于是派发路径的那几条判据
        （``calls == ["find", "create"]``）会在 transition 被**任何**阶段的派发
        顺手调用时红。断言换成记账，判据没有丢。

        抛 ``ExternalStateError`` 而不是 ``AssertionError``：CAS 冲突在真适配器
        那里就是这个类型（适配器在 CAS 冲突时抛它），替身换一种类型会
        让「冲突怎么处置」这条路径在测试里走出与生产不同的分支。
        """
        self.calls.append("transition")
        current = self.tasks.get(external_id)
        if current is None:
            raise ExternalStateError(f"no such task: {external_id}")
        if current.status != expected:
            raise ExternalStateError(
                f"CAS conflict on {external_id}: expected status {expected!r} "
                f'but actual current status is "{current.status}"'
            )
        self.transitions.append((external_id, expected, status))
        updated = ExternalTask(external_id=external_id, status=status, labels=current.labels)
        self.tasks[external_id] = updated
        return updated


class PageParser(DocumentParser):
    """把一份「文件」当成一页读出来：第一行是标题，其余是正文。

    **单行文件 = 空壳页**（正文逐字等于标题）。那不是为了造一个方便的边角情形 ——
    真实的 ``<html><head><title>X</title></head></html>`` 抽出来就是这样，而它是
    两条证据行 id 相撞的唯一常见形态（见 ``tests/test_application_page_refresh.py``
    最后那一条判据）。

    ``sha256`` 是**文件字节**的散列 —— 与真解析器同一个量（``ParsedDocument``
    的 docstring 逐字如此），也是 ``Evidence.content_sha256`` 与
    ``PageTarget.expected`` 比较的那一个。写成「正文的散列」会让每一页都读成变了。

    ``name`` 参与复用判定（``target.parser in router.parser_names``），所以换一个
    名字就是在测「解析器换过了」那一支。
    """

    extracts_links = False
    source_kind = SourceKind.FETCHED_HTML

    def __init__(self, name: str = "html") -> None:
        self.name = name

    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        return True

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        raw = path.read_bytes()
        lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
        if len(lines) <= 1:
            title = text = lines[0] if lines else ""
        else:
            title, text = lines[0], "\n".join(lines[1:])
        return ParsedDocument(
            source_path=path,
            sha256=hashlib.sha256(raw).hexdigest(),
            media_type="text/html",
            parser=self.name,
            title=title,
            text=text,
            links=(),
            extracts_links=self.extracts_links,
            source_kind=self.source_kind,
        )


class FakeHttp(HttpFetcher):
    """按 URL 回一份预置的字节；没有预置的那些 URL 回一个失败结果。

    ``calls`` 是**抓了几次**的账：这一层几乎所有判据最终都落在这个列表上 ——
    「复用」与「没复用」在记录里长得很像（都是「没变」），而它们花的钱不同。
    ``calls`` 与预置的条数**不是**一回事：同一页被取两次会出现两次，那正是要挡的。

    ``failures`` 让某几个 URL 回一个**说得出理由**的失败（够不着），用来测
    「整格不续期」那一支；它记进 ``calls``，因为失败的一次也是真的发出去了一次。
    """

    name = "fake-http"

    def __init__(
        self,
        bodies: dict[str, bytes],
        *,
        root: Path,
        failures: dict[str, FetchOutcome] | None = None,
    ) -> None:
        self.bodies = dict(bodies)
        self.failures = dict(failures or {})
        self.root = root
        self.calls: list[str] = []

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request.url)
        outcome = self.failures.get(request.url)
        if outcome is not None:
            return FetchResult(
                request=request,
                outcome=outcome,
                tier=FetchTier.HTTP,
                final_url=request.url,
                error="模拟的传输失败",
                elapsed_ms=3,
            )
        raw = self.bodies[request.url]
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{hashlib.sha256(request.url.encode()).hexdigest()[:16]}.html"
        path.write_bytes(raw)
        return FetchResult(
            request=request,
            outcome=FetchOutcome.OK,
            tier=FetchTier.HTTP,
            final_url=request.url,
            status_code=200,
            content_type="text/html",
            body_path=path,
            body_bytes=len(raw),
            elapsed_ms=5,
        )
