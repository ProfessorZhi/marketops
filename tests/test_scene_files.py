"""Scene 内容快照：``scene_version`` 与它指称的那批字节。

本文件守的是**收口之前与之后算出来是同一个版本**这件事，以及快照自身的判据。

## 为什么第一条测试要把旧算法**冻结地抄一遍**

本仓的常规纪律是「绝不保留两份真实实现」。这里是那条纪律唯一一处**故意的例外**，
而且理由不是「这次不一样」，是这条测试问的问题本身：

> 盘上（运行时 DB 的 ``work_item_guidance.scene_version``）钉着的版本，与新算法
> 算出来的版本，**还是不是同一个数**？

要回答它，就必须有一份「旧算法」站在对面。真实实现只有一份
（``SceneFiles.version``），旧算法在这里是**冻结的历史样本**，不是第二个实现 ——
它永远不会被改动，它存在的全部意义就是**不**跟着变。

**这条测试转红时，问题不是「更新一下期望值」。** 转红意味着算法变了，而算法一变，
库里所有钉住的 ``scene_version`` 就全部指向一个再也算不出来的值 ——
``pinned_is_effective`` 会对每一件在途工作说「规则变了」，而其实一个字节都没变。
那时要做的决定是「要不要作废所有钉住的版本」，不是把期望值改掉。

（对照：``_VERSION_SEP`` 的注释里写着「没有它时路径 ``("a", "bc")`` 与
``("ab", "c")`` 会给出同一个中间串」。那是这段算法的**语义**，所以它留在
``domain/profile.py`` 里；这里冻的是**逐字节的取值**，所以它留在这里。）
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from marketops.adapters.profile_loader import load_profile, read_scene_files
from marketops.config import Settings
from marketops.domain.profile import ProfileBundle, SceneFile, SceneFiles
from tests.fakes import synthetic_scene_files

REAL_PROFILE_ID = "uk-acme-natural-grass"

#: **冻结的历史实现**，逐字节抄自收口之前的 ``profile_loader._scene_version``。
#: 不要改它，不要「顺手对齐」它 —— 见文件头。
#:
#: 它是收口之前的 ``_scene_version``：目录里每个普通文件按相对 posix 路径排序，
#: 每文件算一条 ``sha256(路径 ‖ 0x00 ‖ 内容)``，再把这些定长块拼起来散一次。
_FROZEN_VERSION_SEP = b"\x00"


def _frozen_scene_version(directory: Path) -> str:
    paths = sorted(
        (path for path in directory.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    entries: list[bytes] = []
    for path in paths:
        relative = path.relative_to(directory).as_posix()
        digest = hashlib.sha256()
        digest.update(relative.encode("utf-8"))
        digest.update(_FROZEN_VERSION_SEP)
        digest.update(path.read_bytes())
        entries.append(digest.digest())
    return hashlib.sha256(b"".join(entries)).hexdigest()


def _real_profile_dir(settings: Settings) -> Path:
    return settings.profile_dir()


# --- 回归：收口不能换掉已经发出去的版本号 -------------------------------------


def test_the_version_algorithm_still_gives_the_version_already_pinned(
    settings: Settings,
) -> None:
    """新算法与旧算法在**真 profile 目录**上必须算出同一个摘要。

    这条是全文件的承重墙。运行时 DB 里 ``work_item_guidance.scene_version`` 存的
    是旧算法算出来的串；摘要一变，那些行全部变成「指向一个算不出来的版本」——
    而它们在盘上看起来仍然完全正常。
    """
    directory = _real_profile_dir(settings)
    assert directory.is_dir(), f"真 profile 目录不存在：{directory}"

    snapshot = read_scene_files(directory)

    assert snapshot.version == _frozen_scene_version(directory), (
        "Scene 版本算法变了。\n"
        "这不是「期望值过期」——盘上钉住的每一个 scene_version 都由此作废，"
        "而它们会表现为「规则变了」而不是任何一条报错。\n"
        "要么把算法改回去，要么先决定怎么作废那些钉住的版本。"
    )


def test_the_bundle_agrees_with_the_frozen_algorithm(settings: Settings) -> None:
    """装载出来的 bundle 报的版本**就是**那条算法算出来的那一个。

    上一条测的是 ``read_scene_files``；这一条测的是 ``load_profile`` 真的把它的
    结果接上了（而不是自己另算一遍、或者接错了对象）。
    """
    directory = _real_profile_dir(settings)
    assert load_profile(settings).scene_version == _frozen_scene_version(directory)


# --- 快照装的是什么 -----------------------------------------------------------


def test_the_snapshot_holds_the_bytes_not_just_their_digest(settings: Settings) -> None:
    """快照里真的有**内容**，路径是相对 posix 路径，且覆盖整个目录。

    「只留一个版本号」正是收口之前的状态：谁也没法回答「那次派发用的是哪一份
    指引」。所以这条判据问的不是版本，是**内容在不在**。
    """
    directory = _real_profile_dir(settings)
    snapshot = read_scene_files(directory)

    on_disk = sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    )
    assert [entry.path for entry in snapshot.entries] == on_disk
    assert len(on_disk) >= 3, "真 profile 至少有三个 YAML；更少说明装载器的输入变了"

    for entry in snapshot.entries:
        assert not entry.path.startswith("/"), "路径必须是目录内的相对路径"
        assert entry.content == (directory / entry.path).read_bytes(), (
            f"{entry.path} 的字节与盘上不符 —— 快照与目录已经不是同一份了"
        )


def test_the_snapshot_keeps_the_guidance_file_a_future_scene_would_add(
    tmp_path: Path,
) -> None:
    """目录里**今天还不存在**的那种文件（程序性指引）也进快照。

    ``docs/SCENE_EVOLUTION.md`` 的 ``skill/SKILL.md`` 现在还没有；写死一份文件清单
    的读法会让「指引发了、版本没动」，而那正是这套机制唯一要防的失败。所以这里放
    一个不在那三个 YAML 之列的文件，问它进没进版本。
    """
    directory = tmp_path / "scene"
    (directory / "nested").mkdir(parents=True)
    (directory / "profile.yaml").write_bytes(b"name: probe\n")
    (directory / "skill").mkdir()
    (directory / "skill" / "SKILL.md").write_bytes(b"# how to do it\n")

    snapshot = read_scene_files(directory)
    assert [entry.path for entry in snapshot.entries] == [
        "profile.yaml",
        "skill/SKILL.md",
    ]

    before = snapshot.version
    (directory / "skill" / "SKILL.md").write_bytes(b"# how to do it, differently\n")
    assert read_scene_files(directory).version != before, "指引改了而版本没动"


def test_one_byte_changes_the_version(tmp_path: Path) -> None:
    """改动一个字节，版本就变。这是「版本」这个词在这里的全部含义。"""
    directory = tmp_path / "scene"
    directory.mkdir()
    (directory / "profile.yaml").write_bytes(b"name: probe\n")

    before = read_scene_files(directory).version
    (directory / "profile.yaml").write_bytes(b"name: probe.\n")
    assert read_scene_files(directory).version != before


def test_as_mapping_is_a_copy(tmp_path: Path) -> None:
    """``as_mapping()`` 每次给一个新字典。

    两次调用拿到同一个可变对象，就是一处可以让「读的人」凭空改掉快照内容的入口 ——
    而快照的全部价值在于它不再变。
    """
    snapshot = synthetic_scene_files()
    first = snapshot.as_mapping()
    second = snapshot.as_mapping()

    assert first == second
    assert first is not second
    first["injected.yaml"] = b"x"
    assert "injected.yaml" not in snapshot.as_mapping()


# --- 快照自身的判据 -----------------------------------------------------------


def test_an_empty_snapshot_is_refused() -> None:
    """零文件的快照算不出任何版本，所以不许存在。

    放它过去，一个**什么都没读到**的目录会得到一个算得出来的版本号，于是空装载
    看起来像一次成功的装载。
    """
    with pytest.raises(ValueError, match="不能是空的"):
        SceneFiles(entries=())


def test_unsorted_entries_are_refused() -> None:
    """乱序的 entries 会让版本在没有任何改动时变来变去。"""
    with pytest.raises(ValueError, match="升序"):
        SceneFiles(
            entries=(
                SceneFile(path="b.yaml", content=b"b"),
                SceneFile(path="a.yaml", content=b"a"),
            )
        )


def test_a_duplicated_path_is_refused() -> None:
    """同一个路径算两次，版本仍然稳定、仍然唯一，只是它描述的不是这个目录。"""
    with pytest.raises(ValueError, match="重复 path"):
        SceneFiles(
            entries=(
                SceneFile(path="a.yaml", content=b"a"),
                SceneFile(path="a.yaml", content=b"a"),
            )
        )


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.yaml", "a/../../b.yaml"])
def test_paths_outside_the_directory_are_refused(path: str) -> None:
    """``path`` 是**目录内**的相对路径：绝对路径把「装在哪台机器上」写进规则，
    ``..`` 让版本描述到目录外面去。"""
    with pytest.raises(ValueError, match="目录内"):
        SceneFiles(entries=(SceneFile(path=path, content=b"x"),))


# --- bundle 不许自相矛盾 ------------------------------------------------------


def test_a_bundle_cannot_carry_a_version_that_is_not_its_bytes(
    settings: Settings,
) -> None:
    """``scene_version`` 与 ``scene_files`` 对不上时，bundle 构造不出来。

    这条判据证明的是**两者一致**，不是两者同源（那件事没有类型能表达，由
    ``read_scene_files`` 一次遍历同时产出两样来保证）。一致性也要守住：一个拿着
    别的版本号的 bundle，它钉到工作单元上的那个号码描述的是一份谁也没读过的规则。
    """
    real = load_profile(settings)
    with pytest.raises(ValueError, match="字节算出"):
        ProfileBundle(
            profile_id=real.profile_id,
            status=real.status,
            rules=real.rules,
            role_terms=real.role_terms,
            scene_version=synthetic_scene_files().version,
            scene_files=real.scene_files,
        )


def test_a_synthetic_bundle_is_internally_consistent(settings: Settings) -> None:
    """合成 bundle 的哨兵性质由**字节**承载，不再是一个没有指称对象的版本号。

    它从前是自相矛盾的：拿着一个版本号，却没有任何东西是那个版本。一个自相矛盾的
    夹具教不会任何人「版本与内容必须对上」—— 它恰好是那条规则的反例。

    哨兵性质仍然成立，而且是**可测的**：合成字节不来自任何 profile 目录，所以它的
    版本不等于真 profile 的版本。一个合成 bundle 因此不可能偶然表现成
    「钉住的版本 == 当前版本」。
    """
    snapshot = synthetic_scene_files()
    assert snapshot.version == synthetic_scene_files().version, "同一个快照算两次不一致"
    assert snapshot.version != load_profile(settings).scene_version, (
        "合成快照的版本撞上了真 profile 的版本 —— 它作为哨兵就不再成立了"
    )

    bundle = ProfileBundle(
        profile_id="probe",
        status=None,
        rules={},
        role_terms=(),
        scene_version=snapshot.version,
        scene_files=snapshot,
    )
    assert bundle.scene_version == bundle.scene_files.version


# --- 每文件的摘要：回执里那一格到底在证明什么 -----------------------------------


def test_the_file_digest_covers_the_content_alone() -> None:
    """``SceneFile.sha256`` 不含路径 —— 而 ``SceneFiles.version`` **含**。

    两层放在一起才对得上账：这个值答的是「你手里这个文件是不是我当时读到的
    那几个字节」，那是一个关于内容的问题；版本号答的是「这**整套**规则换过没有」，
    而搬一个文件就是换了一套规则（路径是查找的一部分）。

    所以这一条有两半，缺一半就退化成同义反复：

    * 同样的内容在**两个路径**上给出**同一个** file sha256 —— 少了这一半，一个
      「顺手把路径也散进去」的实现照旧全绿，而它会让「同一个文件被搬了个位置」
      在回执里长得像「内容变了」；
    * 而那两个快照的 **version 必须不同** —— 少了这一半，「版本与内容无关」这种
      把两层合成一层的实现会过，而那正是 ``SceneFiles`` 存在的理由被抹掉。

    （一格的路径 ``a/`` 与 ``b/`` 是刻意的：同层的两个名字会让「顺序」也掺进来，
    而这条判据问的不是顺序。）
    """
    here = SceneFile(path="a/manifest.txt", content=b"rules\n")
    moved = SceneFile(path="b/manifest.txt", content=b"rules\n")

    assert here.sha256 == moved.sha256
    assert SceneFiles(entries=(here,)).version != SceneFiles(entries=(moved,)).version


def test_the_same_path_under_two_scenes_gets_two_file_digests() -> None:
    """**同一条路径、两个版本** —— 规则被就地改过时，回执要分得开这两份字节。

    这是真发生过的形状：``profile.yaml`` 被改一格，路径一个都没动。一个按路径
    记账的实现（或者把 file sha256 实现成路径的散列）会在这条上给出**同一个**
    digest，而那时回执里的 ``files`` 列着一串查得出来、却指不到任何字节的值 ——
    比不列更坏，因为它看起来像是核过了。

    同时断正向：同一份快照构造两次，逐文件摘要相同（否则它不能被用来「核对你
    手里那一份」，因为连它自己都不可复现）。
    """
    first = synthetic_scene_files()
    second = synthetic_scene_files(tag="other")

    assert [entry.path for entry in first.entries] == [
        entry.path for entry in second.entries
    ], "这条判据要的是同一条路径的两种内容，不是两批不同的文件"
    differing = [
        (before, after)
        for before, after in zip(first.entries, second.entries, strict=True)
        if before.sha256 != after.sha256
    ]
    assert differing, "改了内容而逐文件摘要一模一样 —— 回执里那一格证明不了任何事"
    assert first.version != second.version

    again = synthetic_scene_files()
    assert [entry.sha256 for entry in again.entries] == [
        entry.sha256 for entry in first.entries
    ]
