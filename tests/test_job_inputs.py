"""``input_ref`` → 工作单元 subject（``marketops/application/inputs.py``）。

这一层决定三件事：**这个名字能不能当文件名**、**那份清单里有哪些账号**、
**「这一类输入本部署还没有生产者」这句话说不说得出来**。三件都是纯函数，
所以本文件不联网、不碰 Serper、不碰 Quay —— 全部在 ``tmp_path`` 里跑完。

## 每条判据都配一个正对照

一个只会说「这个输入不合法」的判据，在一个**恒拒绝**的实现上同样全绿。
所以下面每一条拒绝都跟着一个走通的例子：非法名字旁边是合法名字读出了账号，
空清单旁边是同一份清单写了一个账号就读出了那一个。

## 为什么「空清单」单占一条

``compile_work_items`` 逐字拒绝空清单（「一个没有任何 work item 的作业在报告里
长得像已经做完」）。如果 ``subjects_for`` 把「清单是空的」静默返回成一串空
subject，那条拒绝就会在更下游以两种形态出现：要么被读成一次成功受理，要么变成
一条**没有理由**的 ``BLOCKED``（``set_state`` 对 BLOCKED 要求理由非空）——
一种在报告里长得像完成，另一种是一次崩溃。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from marketops.application.inputs import (
    KNOWN_ACCOUNTS_SUFFIX,
    _NO_PRODUCER,
    SubjectResolution,
    subjects_for,
)
from marketops.application.motherpool import read_motherpool, write_receipt
from marketops.application.promotion import (
    ACCOUNT_ID_PREFIX,
    canonical_account_id_for_row,
)
from marketops.config import Settings
from marketops.domain.enums import DomainVerdict, JobInputKind
from tests.fakes import synthetic_dossiers

#: 「本仓载体」的形状：一个指向仓库内模块的路径，以 ``.py`` 收尾。
_MODULE_PATH = re.compile(r"marketops/[A-Za-z0-9_./-]+\.py")

_TWO_ACCOUNTS = """
accounts:
  - account_id: acct-0001
    organisation: Example Coatings Ltd
    cohort: known_success
  - account_id: acct-0002
    organisation: Second Example Ltd
    cohort: accessible_no_core_email
"""

_THREE_ACCOUNTS_NAMED = """
accounts:
  - account_id: acct-0001
    organisation: Example Coatings Ltd
    cohort: known_success
  - account_id: acct-0002
    organisation: Second Example Ltd
    cohort: known_success
  - account_id: acct-0003
    organisation: Third Example Ltd
    cohort: known_success
"""

_TWO_ACCOUNTS_REVERSED = """
accounts:
  - account_id: acct-0002
    organisation: Second Example Ltd
    cohort: accessible_no_core_email
  - account_id: acct-0001
    organisation: Example Coatings Ltd
    cohort: known_success
"""


def _write(inputs_dir: Path, name: str, body: str) -> Path:
    inputs_dir.mkdir(parents=True, exist_ok=True)
    path = inputs_dir / f"{name}{KNOWN_ACCOUNTS_SUFFIX}"
    path.write_text(body, encoding="utf-8")
    return path


def _ids(resolution: SubjectResolution) -> tuple[str, ...]:
    """解析结果里的 ``account_id`` 序列。

    ``subjects`` 住的是 ``SubjectDossier`` 而不是字符串（理由见 ``inputs.py``：
    一个只有 slug 的解析结果正是要修的那个形状），而「顺序」这类判据问的是 id
    那一列 —— 抽成一处，免得每个断言各挑一个字段。
    """
    return tuple(dossier.account_id for dossier in resolution.subjects)


# ----------------------------------------------------------------------
# 有生产者的那一类：名字 → 清单 → subject
# ----------------------------------------------------------------------
def test_a_list_becomes_subjects_in_the_order_the_file_has_them(tmp_path: Path) -> None:
    """文件顺序**有意义**：它决定 ``ordinal``，因而决定 ``work_item_id``。

    正对照就在同一处：同一批账号、反过来写一遍，得到的 subject 序列也反过来。
    少了这一半，一条把结果 ``sorted()`` 的实现照样过 —— 而它会把一次重排读成
    另一批工作（``compile_work_items`` 比对整个序列，不只是集合）。
    """
    inputs = tmp_path / "inputs"
    _write(inputs, "devon-2026", _TWO_ACCOUNTS)
    _write(inputs, "devon-2026-flipped", _TWO_ACCOUNTS_REVERSED)

    forward = subjects_for(JobInputKind.KNOWN_ACCOUNTS, "devon-2026", inputs_dir=inputs)
    backward = subjects_for(
        JobInputKind.KNOWN_ACCOUNTS, "devon-2026-flipped", inputs_dir=inputs
    )

    assert forward.ok and backward.ok
    assert _ids(forward) == ("acct-0001", "acct-0002")
    assert _ids(backward) == ("acct-0002", "acct-0001")
    assert forward.blocker == ""
    # 顺序对**还不够**：主体必须把身份一起带出来。只回一串 ``account_id`` 的实现
    # 在上面两条上全绿 —— 而它正是 2026-09-23 那次真实运行的形状：worker 拿到
    # ``kent-003``，检索九次，全是无关的同名物。
    assert forward.subjects[0].organisation == "Example Coatings Ltd"
    assert backward.subjects[0].organisation == "Second Example Ltd"


def test_a_malformed_list_is_a_blocker_not_a_crash(tmp_path: Path) -> None:
    """清单读得出来但读不成账号：阻塞，不是异常，也不静默退化成空。

    ``load_accounts`` 的 ``ConfigError`` 说的是「这份清单哪个位置坏了」——
    在报告里它必须活着走到调用点，而不是变成一次崩溃或一串空 subject。
    """
    inputs = tmp_path / "inputs"
    _write(
        inputs,
        "duplicated",
        """
accounts:
  - account_id: acct-0001
    organisation: Example Coatings Ltd
    cohort: known_success
  - account_id: acct-0001
    organisation: Same Id Again Ltd
    cohort: previously_blocked
""",
    )

    resolved = subjects_for(JobInputKind.KNOWN_ACCOUNTS, "duplicated", inputs_dir=inputs)

    assert not resolved.ok
    assert "acct-0001" in resolved.blocker
    assert "重复" in resolved.blocker


# ----------------------------------------------------------------------
# 四类输入**各自**都有一个生产者（E2 之后这一类也补上了）
# ----------------------------------------------------------------------
#: 真的去 ``inputs_dir`` 读一份文件的那些类。**手写的**，与
#: ``test_every_input_kind_is_either_served_or_has_a_sentence`` 里那个集合分开：
#: 两者今天只差 ``COHORT``，而它们问的本来就是两件事 —— 那个问「有没有生产者」，
#: 这个问「读的是不是盘上那一份」。
_DISK_READING = (
    JobInputKind.KNOWN_ACCOUNTS,
    JobInputKind.SOURCE_RECEIPT,
    JobInputKind.DISCOVERY_SCOPE,
)


@pytest.mark.parametrize("kind", _DISK_READING)
def test_every_kind_reaches_the_disk_instead_of_saying_it_has_no_producer(
    kind: JobInputKind, tmp_path: Path
) -> None:
    """这三类输入都**真的去读盘**了 —— 证据是它们给出的理由说的是「读不到」。

    这条判据的前身是它的反面：``discovery_scope`` 曾经在这里返回一句「这一类没有
    生产者，所以没有去读任何东西」。那一句在 E2 之后不成立了，而「不成立」必须被
    执行器看出来 —— 否则它会以另一种形式回来：生产者落地了，而受理那一侧仍然在说
    没有（一个从来没有机会被发现的缺口，因为两边都自称正常）。

    所以判据换成两句，缺一不可：

    * ``"生产者" not in blocker`` —— 理由**不是**那一句。这一半挡的是回归。
    * ``str(missing) in blocker`` —— 理由说得出**看的是哪个目录**。这一半挡的是
      「读不到」却不说去哪读的消息：运维照着修不了。同时它证明这件事真的发生了 ——
      一个先读盘、读不到再报「没有生产者」的实现也能过第一句，但过不了这一句。

    ``inputs_dir`` 是一个**不存在**的目录，所以每一类都必须走「读不到」那条路。

    ``cohort`` 不在这里，**不是**因为它没有生产者，而是因为它读的不是盘上一个
    文件 —— 把「到一个不存在的目录下读不到」当成它该给的答案，等于要求它去一个
    它根本不看的地方。它那一半在下面那一条上。
    """
    missing = tmp_path / "inputs-that-do-not-exist"

    resolved = subjects_for(kind, "devon-2026", inputs_dir=missing)

    assert not resolved.ok
    assert resolved.subjects == ()
    assert "生产者" not in resolved.blocker
    assert str(missing) in resolved.blocker


def test_the_cohort_kind_reaches_the_pool_instead_of_the_disk(tmp_path: Path) -> None:
    """``cohort`` 那一类**没有接线**时给的理由说的是缺哪两样存储，不是「读不到盘」。

    它与上面那一条是同一件事的两个半边：那里问「理由说不说得出去哪读」，这里问
    「它去读的到底是不是盘」。这两句在同一类上会互相矛盾 —— 一个把 ``cohort``
    也送去 ``inputs_dir`` 摸一遍的实现，在目录存在时会安静地返回「没有这一份回执」
    （一句**听起来对**的话：库里确实没有），而它真正的毛病是从来没有去读池子。

    所以判据钉住**缺的是哪一样**：理由点得出 ``accounts`` 与 ``cohorts`` 两个工厂。
    一个只说「名单读不出来」的实现过不了 —— 那正是把「尚未接线」读成「这里是空
    的」，而这两句话的处置相反（补接线 vs 去冻一份名单）。

    另半句同样要钉：``inputs_dir`` 在那句理由里**不该出现**。一个不存在的目录
    出现在这条阻塞上，说明它还是从盘那条路过来的 —— 而它本该走不到那里。
    """
    missing = tmp_path / "inputs-that-do-not-exist"

    resolved = subjects_for(JobInputKind.COHORT, "coh-0123456789abcdef", inputs_dir=missing)

    assert not resolved.ok
    assert resolved.subjects == ()
    assert "生产者" not in resolved.blocker
    assert "accounts" in resolved.blocker and "cohorts" in resolved.blocker, (
        "理由要说得出缺的是哪两样接线 —— 只报「名单读不出来」会把「这个部署没装"
        "这一段存储」读成「这份名单还不存在」，而两者的处置相反"
    )
    assert str(missing) not in resolved.blocker, (
        "这一类不读盘，一个不存在的 inputs_dir 出现在它的阻塞理由上，说明它还是"
        "从盘那条路过来的"
    )


def test_the_no_producer_channel_still_gives_a_reason_and_names_a_real_carrier() -> None:
    """``_NO_PRODUCER`` 空掉之后，「没有生产者」这条通道**仍然要给出一句话**。

    它今天没有对象（三类都有生产者了），所以这条判据问的是**兜底**那一句
    （:func:`~marketops.application.inputs._no_producer_reason`）：往枚举里加一个
    成员而忘了处置它时，受理那一侧不该是一次 ``KeyError``，而该是一句读得懂的
    阻塞理由 —— 那正是这个函数存在的理由，也是 ``_NO_PRODUCER`` 留着一张空表的
    理由。

    载体那一条纪律**跟着这句话一起留在这里**：理由里点名的 ``marketops/....py``
    路径必须真的在盘上。它挡的是那次具体的失真 —— ``discovery_scope`` 的理由曾经
    写着「发现管线不在本仓库里」，而展开器（``application/openweb.py``）与投影
    （``application/qualify.py``）落地之后那句话就不再准确了。一条指错地方的引用
    比没有引用坏：读者会去查，然后以为自己读懂了。

    **边界说清楚：** 它证明理由**点名了真实存在的载体**，不证明理由说得对。
    能被执行的是「指向哪里」，不是「说得准不准」；后者只能靠人读。
    """
    from marketops.application.inputs import _no_producer_reason

    repo_root = Path(__file__).resolve().parents[1]

    for kind in JobInputKind:
        reason = _no_producer_reason(kind)
        assert reason.strip(), f"{kind.value} 有一句空话，等于没有理由"
        assert kind.value in reason, (
            f"理由里没有点名 {kind.value} —— 读的人不知道要补的是哪一类"
        )
        assert "生产者" in reason
        # 用正则而不是切词：理由里紧跟着中文标点（``openweb.py、配对键``）时，
        # 按空白切出来的 token 会带着标点，于是**看起来**没点名任何载体 ——
        # 一条被排版细节骗过的判据比没有这条判据更坏。
        named = _MODULE_PATH.findall(reason)
        assert named, (
            f"{kind.value} 的理由没有点名任何本仓载体（`marketops/....py`）。"
            "「不在本仓库里」这句话无法核对，而它曾经就是错的。"
        )
        for relative in named:
            assert (repo_root / relative).is_file(), (
                f"{kind.value} 的理由点名了 {relative}，但盘上没有这个文件"
            )


def test_every_input_kind_is_either_served_or_has_a_sentence() -> None:
    """闭集上的一条覆盖判据：新增一个输入种类而不处置它，这条会红。

    它问的是 ``_NO_PRODUCER`` 与 ``JobInputKind`` 的那个**差集**：差集非空意味着
    有一类今天没有生产者，而它必须有一句话（``_no_producer_reason`` 不会兜底出
    一句准确的话 —— 兜底只保证不崩）。差集为空时这条判据是 green 的，而那正是
    今天的状态：**没有一句理由要写，因为三类都有生产者了**。
    """
    # **手写**的四类，刻意不读 ``inputs._NAME_ADDRESSED``：这句判据要问的正是
    # 「今天哪几类真的有生产者」，而让它去读被测模块自己的常量，
    # 就变成「那个集合等于它自己」—— 一条恒真的断言。
    #
    # ``COHORT`` **不在** ``_NAME_ADDRESSED`` 里，这是对的、不是遗漏：那个集合
    # 的成员资格问的是「读 ``inputs_dir`` 下的哪一个文件」，而这一类读的是状态库
    # 里的账号池与冻结名单，一个文件都不读。所以这个手写集合的成员资格从「读一份
    # 文件」改成了**「有生产者」** —— 这是一句更强的话：它现在同时盯住「有人往
    # 枚举里加成员却没处置」与「处置了却挂在错的那一处」。
    served = {
        JobInputKind.KNOWN_ACCOUNTS,
        JobInputKind.SOURCE_RECEIPT,
        JobInputKind.DISCOVERY_SCOPE,
        JobInputKind.COHORT,
    }

    assert set(_NO_PRODUCER) == set(JobInputKind) - served
    for kind, sentence in _NO_PRODUCER.items():
        assert sentence.strip(), f"{kind.value} 有一句空话，等于没有理由"


# ----------------------------------------------------------------------
# 名字：拒绝，不规范化
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("given", "would_become"),
    [
        ("Devon 2026", "devon-2026"),
        ("../secrets", "secrets"),
        ("devon-2026-", "devon-2026"),
    ],
)
def test_a_name_that_needs_normalizing_is_refused_not_rewritten(
    given: str, would_become: str, tmp_path: Path
) -> None:
    """名字会变成文件名，所以它必须是 ``slugify`` 的不动点。

    中间那一例是本条判据的**安全半边**：``../secrets`` 被规范化后是 ``secrets``，
    于是「读哪个文件」这件事就由调用方给的一串字符决定了 —— 这正是把
    ``input_ref`` 收成**名字**而不是路径要挡掉的东西。第三例走的是另一侧：
    名字合法、只是首尾的破折号会被抹掉，于是 ``devon-2026-`` 与 ``devon-2026``
    落到**同一个文件**上，而调用方给的是两个名字。

    ``ValueError`` 而不是阻塞：这是**调用方给错了**，不是部署的事实。给错了要改
    请求，部署缺生产者在调用方这一侧无解 —— 两件事的处置不同。
    """
    inputs = tmp_path / "inputs"
    _write(inputs, "devon-2026", _TWO_ACCOUNTS)

    with pytest.raises(ValueError) as caught:
        subjects_for(JobInputKind.KNOWN_ACCOUNTS, given, inputs_dir=inputs)

    assert would_become in str(caught.value)

    # 正对照：同一个函数、同一个目录，给一个已经规范的**名字**就读得出账号。
    assert _ids(
        subjects_for(JobInputKind.KNOWN_ACCOUNTS, "devon-2026", inputs_dir=inputs)
    ) == ("acct-0001", "acct-0002")


# ----------------------------------------------------------------------
# 解析结果：恰好一半
# ----------------------------------------------------------------------
def test_a_missing_list_names_the_file_it_looked_for(tmp_path: Path) -> None:
    """「清单不存在」这句话没有告诉运维把文件放哪儿 —— 所以消息里要有路径。"""
    inputs = tmp_path / "inputs"

    resolved = subjects_for(JobInputKind.KNOWN_ACCOUNTS, "devon-2026", inputs_dir=inputs)

    assert not resolved.ok
    assert str(inputs / f"devon-2026{KNOWN_ACCOUNTS_SUFFIX}") in resolved.blocker


def test_an_empty_list_is_a_blocker_not_a_silent_zero(tmp_path: Path) -> None:
    """空清单**不是**「受理成功、只是没有活干」。

    这里断的是 ``compile_work_items`` 拒绝空清单时逐字写的那个理由 ——
    两份实现说同一句话，是因为它们是同一件事的两侧。
    """
    inputs = tmp_path / "inputs"
    _write(inputs, "empty", "accounts: []\n")

    resolved = subjects_for(JobInputKind.KNOWN_ACCOUNTS, "empty", inputs_dir=inputs)

    assert not resolved.ok
    assert resolved.subjects == ()
    assert "已经做完" in resolved.blocker
    assert str(inputs / "empty.yaml") in resolved.blocker


def test_a_list_where_one_row_has_no_identity_is_refused_whole(tmp_path: Path) -> None:
    """**整份拒绝，不是跳过那一行。** 而这一步发生在编译之前。

    跳过那一行会让一份三行的清单编译出两件工作 —— 而报告里那个「2」与「清单里
    本来就只有 2 行」长得一样。更要紧的是位置：这个解析发生在任何 work item 被
    建出来之前，所以「拒绝」在这里就等于**零检索、零模型调用**。到了
    ``compile_work_items`` 就已经有工作单元了，而工作单元的失败形态是「worker
    领走它、然后把预算花光」。

    正对照：把那一行补上，同一份清单就编译得出三件。
    """
    inputs = tmp_path / "inputs"
    _write(
        inputs,
        "devon-2026",
        "accounts:\n"
        "  - account_id: acct-0001\n"
        "    organisation: Example Coatings Ltd\n"
        "    cohort: known_success\n"
        "  - account_id: acct-0002\n"
        "    organisation: acct-0002\n"
        "    cohort: known_success\n"
        "  - account_id: acct-0003\n"
        "    organisation: Third Example Ltd\n"
        "    cohort: known_success\n",
    )

    resolved = subjects_for(JobInputKind.KNOWN_ACCOUNTS, "devon-2026", inputs_dir=inputs)

    assert not resolved.ok
    assert resolved.subjects == ()
    assert "acct-0002" in resolved.blocker
    # 合规的那两行**不**出现在清单里：列出来会让读的人以为它们也有问题。
    assert "acct-0001：" not in resolved.blocker
    assert "没有被花掉" in resolved.blocker

    _write(inputs, "devon-2026", _THREE_ACCOUNTS_NAMED)
    fixed = subjects_for(JobInputKind.KNOWN_ACCOUNTS, "devon-2026", inputs_dir=inputs)
    assert _ids(fixed) == ("acct-0001", "acct-0002", "acct-0003")


def test_every_row_that_needs_fixing_is_named_at_once(tmp_path: Path) -> None:
    """一次把要补的行全报出来 —— 一行一轮会让人来回补好几趟。

    这条判据很薄，但它挡的是一个真实的读法：只报**第一行**的实现，在报告里长得
    与「只有一行有问题」一模一样。
    """
    inputs = tmp_path / "inputs"
    _write(
        inputs,
        "kent-2026",
        "accounts:\n"
        "  - account_id: acct-0001\n"
        "    organisation: ''\n"
        "    cohort: known_success\n"
        "  - account_id: acct-0002\n"
        "    organisation: Example Coatings Ltd\n"
        "    cohort: known_success\n"
        "  - account_id: acct-0003\n"
        "    organisation: acct-0003\n"
        "    cohort: known_success\n",
    )

    resolved = subjects_for(JobInputKind.KNOWN_ACCOUNTS, "kent-2026", inputs_dir=inputs)

    assert not resolved.ok
    assert "2 个主体" in resolved.blocker
    assert "acct-0001" in resolved.blocker and "acct-0003" in resolved.blocker


def test_a_resolution_must_give_exactly_one_half() -> None:
    """两样都没有的那种构造不出来 —— 它在调用点会变成一条没有理由的 BLOCKED。

    正对照是另外三种：只有 subject 的、只有理由的，各是合法的一半。
    """
    with pytest.raises(ValueError, match="恰好给出一半"):
        SubjectResolution()
    with pytest.raises(ValueError, match="恰好给出一半"):
        SubjectResolution(subjects=synthetic_dossiers("acct-0001"), blocker="因为")

    assert SubjectResolution(subjects=synthetic_dossiers("acct-0001")).ok
    assert not SubjectResolution(blocker="因为").ok


def test_the_inputs_directory_lives_under_the_data_root(settings: Settings) -> None:
    """清单放在 ``data_root`` 下、**不在** ``state/`` 下，而且默认就被建出来。

    这个目录装的是**调用方交进来的东西**，``state/`` 装的是我们自己写出来的
    状态 —— 混在一起之后，「这个文件是谁写的」就没有答案了。
    """
    assert settings.inputs_dir == settings.data_root / "inputs"
    assert not settings.inputs_dir.is_relative_to(settings.state_dir)
    assert settings.inputs_dir.is_dir()


# ----------------------------------------------------------------------
# 来源回执那一类输入：从一份母池工件，到一串**能开工的**主体
# ----------------------------------------------------------------------
#: 一份**诚实**的母池 fixture：按对象类型编号的导出，常态长相。
#:
#: 五种去向都在里面，因为这正是这一类输入的常态 —— 上游交进来的不是一份已经
#: 整理干净的账号清单，而是一份导出。要证明的也正在这里：**它不必先被整理成
#: 完整 Account 才能进系统**，而整理不了的那几行不会被静默丢掉。
_POOL = "".join(
    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    for row in [
        # 有名字、有域名 —— 这一行现在可以去研究
        {
            "type": "node",
            "id": "1001",
            "name": "Example Riverside Club",
            "town": "Exeter",
            "website": "https://www.riverside.example",
        },
        # 只有代号，没有名字 —— Member-C 说的那种行
        {"type": "node", "id": "1002"},
        # 同一个对象的第二次出现
        {"type": "node", "id": "1001", "name": "Example Riverside Club (again)"},
        # 同名、不同地点 —— **不**合并
        {"type": "node", "id": "1003", "name": "Example Riverside Club", "town": "Bristol"},
        # 缺域名，但有名字
        {"type": "node", "id": "1004", "name": "Example Hilltop Club"},
    ]
)


def _seed_receipt(inputs: Path, *, name: str = "pool-2026"):
    """把一份工件读成回执、落盘，返回那份回执。**走的是真正的生产者。**"""
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / f"{name}.jsonl").write_text(_POOL, encoding="utf-8", newline="\n")
    receipt = read_motherpool(
        inputs,
        name,
        source_method_id="import.register.v1",
        batch_id="batch-01",
        profile_id="devon-2026",
    )
    write_receipt(inputs, receipt)
    return receipt


def test_a_receipt_becomes_work_subjects_without_manual_account_ification(
    tmp_path: Path,
) -> None:
    """**母池不必先被人工整理成完整 Account。**

    E1 的那条要求在这里成为一条跑得动的判据：一份只有代号、重复、同名不同地点、
    缺域名的导出，读成回执之后，``resolved`` 的那些行**直接**变成主体 ——
    而另外三种去向留在回执里带着理由，不产生 work item、也不静默消失。

    同时钉住 ``account_id`` 是什么：它是这一行的 **canonical 账号 id**
    （``acct-<16 位十六进制>``），与 ``promote_receipt`` 折进 ``market_account``
    的**是同一个值** —— 而它**不是** ``row_id``。这一条改过一次，改的理由写在
    ``application/promotion.py`` 的模块 docstring 里：``row-…`` 曾经是这里的主体，
    而账号池那边是另一套 slug，两个空间的交集是空集 —— 检索真的发生了、证据真的
    落了盘，却没有一个账号读得出来。所以这里既钉形状，也钉「来源标识不进身份」。
    """
    inputs = tmp_path / "inputs"
    receipt = _seed_receipt(inputs)

    resolved = subjects_for(
        JobInputKind.SOURCE_RECEIPT, receipt.receipt_id, inputs_dir=inputs
    )

    assert resolved.ok
    assert [d.account_id for d in resolved.subjects] == [
        canonical_account_id_for_row(row) for row in receipt.resolved_rows
    ]
    assert all(d.account_id.startswith(ACCOUNT_ID_PREFIX) for d in resolved.subjects)
    assert not any(d.account_id == row.row_id for row in receipt.resolved_rows for d in resolved.subjects)
    assert "node" not in " ".join(d.account_id for d in resolved.subjects)
    # 出处那一栏仍然说得出来是哪一行 —— ``row_id`` 没有被丢掉，它换了位置。
    assert all(d.source_ref.endswith(f"{receipt.receipt_id}.receipt.json") for d in resolved.subjects)


def test_a_receipt_carries_the_identity_not_just_the_handle(tmp_path: Path) -> None:
    """带进任务包的是**身份**，不是主键。

    ``kent-003`` 那次事故的成因是这一层只留下一个 slug，``organisation`` 与
    ``domain`` 读出来即弃。这条判据问的是同一件事：一份回执里那两个字段到没到
    工作包上。域名那一格是**配对键**（走 ``pairing.canonical_host``），
    所以 ``https://www.riverside.example`` 到这里是 ``riverside.example``。

    **按位置数，不按名字索引。** 这份 fixture 里恰好有两个同名不同地点的单位
    （回执刻意不合并它们），所以 ``{d.organisation: d}`` 那种索引会把其中一条
    悄悄吃掉 —— 那正是「按名字认东西」这个动作本身的毛病，测试里也不该用它。
    """
    inputs = tmp_path / "inputs"
    receipt = _seed_receipt(inputs)

    resolved = subjects_for(
        JobInputKind.SOURCE_RECEIPT, receipt.receipt_id, inputs_dir=inputs
    )

    assert [d.organisation for d in resolved.subjects] == [
        "Example Riverside Club",
        "Example Riverside Club",
        "Example Hilltop Club",
    ]
    first, same_name_other_town, bare = resolved.subjects
    assert first.domain == "riverside.example"
    # 同名不同地点的那一条**没有**域名可继承 —— 它是另一个主体，不是这一条的副本。
    assert same_name_other_town.domain == ""
    # 缺域名那一行**照旧是一个主体** —— 域名不是开工的前提，它是配对用的那一格。
    assert bare.domain == ""
    assert all(d.source_ref.endswith(".receipt.json") for d in resolved.subjects)
    assert all(d.input_sha256 for d in resolved.subjects)


#: 一份**发现期**工件的常态长相：一行是「我们在某台主机上读到了这个组织」。
#: ``discovered_host`` 是这一行唯一的域名来源 —— 官网那一组格子**一个都没有**，
#: 因为「这台主机是它的官网」正是还没被证明的那件事。
_DISCOVERY_ARTIFACT = "".join(
    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    for row in [
        {
            "type": "node",
            "id": "2001",
            "name": "Example Riverside Club",
            "discovered_host": "directory.example",
            "discovered_url": "https://directory.example/org/2001",
        },
    ]
)


def test_the_host_we_fetched_is_not_the_account_domain_authority(tmp_path: Path) -> None:
    """**我们读到这一行的主机，不是它的官网。** SOP L121 的那条边界。

    ``discovered_host`` 与 ``claimed_official_domain`` / ``verified_official_domains``
    分成三格，理由就是这一条：一行是「我们在这台主机上看见了它」（可能是目录站、
    社媒、聚合页），一行是「它自己说官网是这个」（转述），一行是「有证据指着这个
    域名」（证明）。前两种往**两个方向**都错 —— 把目录站当官网会拒掉真实的跨域
    邮箱，把社媒页上那个真实域名当外来域会拒掉正确的那个。

    所以这条判据问的是**这一格有没有越界**：工件把主机名写得清清楚楚，
    它到这里仍然只是一个观察，身份那一层的域名权威是空的、判定是不判定。
    一个把 ``discovered_host`` 顺手当官网的实现（``_binding_domains`` 里多加一行
    ``row.discovered_host``）会在这里红，而它在报告上看起来只是「多认出了一些官网」。
    """
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "listing.jsonl").write_text(
        _DISCOVERY_ARTIFACT, encoding="utf-8", newline="\n"
    )
    receipt = read_motherpool(
        inputs, "listing", source_method_id="import.register.v1", batch_id="batch-01"
    )
    write_receipt(inputs, receipt)

    resolved = subjects_for(
        JobInputKind.SOURCE_RECEIPT, receipt.receipt_id, inputs_dir=inputs
    )

    assert resolved.ok
    row = receipt.resolved_rows[0]
    # 观察**在**，而且是规范形式 —— 它确实是我们读到过的一格，不是被丢掉了。
    assert row.discovered_host == "directory.example"
    assert row.discovered_url == "https://directory.example/org/2001"
    # 而官网那三格是空的：没有人说过，也没有东西证过。
    assert row.claimed_official_domain == ""
    assert row.verified_official_domains == []
    assert row.domain_verdict is DomainVerdict.UNKNOWN
    # 于是身份那一层拿不到任何域名权威。
    dossier = resolved.subjects[0]
    assert dossier.domain == ""
    assert dossier.domains == ()
    assert dossier.domain_verdict is DomainVerdict.UNKNOWN
    # 名字照旧带上了 —— 缺域名不是拒绝开工的理由。
    assert dossier.organisation == "Example Riverside Club"


def test_country_is_left_empty_on_purpose(tmp_path: Path) -> None:
    """回执这一层**不知道**国家，所以它不猜一个。

    ``country`` 是 ``SubjectDossier`` 上唯一允许为 None 的字段，而这就是它被留空
    的地方：工件里可能有一列叫 ``country``，但「这一列是国家」是一个判断，
    而一个按列名猜出来的国家在报告里与一个真读出来的国家长得一样。
    """
    inputs = tmp_path / "inputs"
    receipt = _seed_receipt(inputs)

    resolved = subjects_for(
        JobInputKind.SOURCE_RECEIPT, receipt.receipt_id, inputs_dir=inputs
    )

    assert all(d.country is None for d in resolved.subjects)


def test_the_same_receipt_read_twice_gives_the_same_subjects(tmp_path: Path) -> None:
    """**重放幂等**：同一份工件重读一次，主体序列逐位不变。

    这是「重放不会重复建 Account」这条要求在**这一层**的样子：``account_id`` 是
    内容派生的 ``row_id``，``input_sha256`` 也由内容派生，所以两次读出来的主体
    在 ``compile_work_items`` 眼里是同一批（它比对整个序列），不会被读成两批。
    """
    inputs = tmp_path / "inputs"
    receipt = _seed_receipt(inputs)
    first = subjects_for(JobInputKind.SOURCE_RECEIPT, receipt.receipt_id, inputs_dir=inputs)

    replayed = _seed_receipt(inputs)
    second = subjects_for(JobInputKind.SOURCE_RECEIPT, replayed.receipt_id, inputs_dir=inputs)

    assert replayed.receipt_id == receipt.receipt_id
    assert [d.account_id for d in first.subjects] == [d.account_id for d in second.subjects]
    assert [d.input_sha256 for d in first.subjects] == [
        d.input_sha256 for d in second.subjects
    ]
    # 落盘的重放**没有建出第二份回执** —— 路径是内容寻址的。
    assert len(list(inputs.glob("*.receipt.json"))) == 1


def test_a_pool_with_nothing_resolvable_blocks_and_says_why(tmp_path: Path) -> None:
    """整池只有代号 —— 回执**自己说**它阻塞在哪里。

    这不是「读入成功、只是没有活干」。一条空的主体序列会让
    ``compile_work_items`` 抛，而那个端口逐字写着「一个没有任何 work item 的
    作业在报告里长得像已经做完」。
    """
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "thin.jsonl").write_text(
        '{"type": "node", "id": "1"}\n{"type": "node", "id": "2"}\n',
        encoding="utf-8",
        newline="\n",
    )
    receipt = read_motherpool(
        inputs, "thin", source_method_id="import.register.v1", batch_id="batch-01"
    )
    write_receipt(inputs, receipt)

    resolved = subjects_for(JobInputKind.SOURCE_RECEIPT, receipt.receipt_id, inputs_dir=inputs)

    assert not resolved.ok
    assert resolved.subjects == ()
    assert "没有一行可以拿去研究" in resolved.blocker
    assert "身份补全" in resolved.blocker


def test_a_missing_receipt_names_the_file_it_looked_for(tmp_path: Path) -> None:
    """「回执不存在」这句话要说得出运维该把文件放哪儿。"""
    inputs = tmp_path / "inputs"

    resolved = subjects_for(
        JobInputKind.SOURCE_RECEIPT, "receipt-0000000000000000", inputs_dir=inputs
    )

    assert not resolved.ok
    assert "receipt-0000000000000000.receipt.json" in resolved.blocker


def test_a_receipt_whose_name_needs_normalizing_is_refused(tmp_path: Path) -> None:
    """名字会变成文件名，所以回执那一条也必须是 ``slugify`` 的不动点。

    与已知账号逐字同一条判据、同一个位置：给错名字是**调用方**的问题（异常），
    部署缺生产者才走阻塞。两条输入种类在这里不能有两套行为。
    """
    inputs = tmp_path / "inputs"

    with pytest.raises(ValueError):
        subjects_for(JobInputKind.SOURCE_RECEIPT, "Pool 2026", inputs_dir=inputs)
