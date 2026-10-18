"""**Workstream / LaunchRecord / EffectiveScene 的存储契约。**

三个存法，两份实现（``adapters/sqlite/workstreams.py`` 主存、
``adapters/memory.py`` 测试替身），跑同一份
``tests/test_workstream_store_contract.py`` —— 与 ``ports/scenes.py`` /
``ports/product_catalog.py`` 是同一条纪律。

## 幂等与不可变**在端口上就是两句话**

这两件事很容易被写成一句「存之前先查一下」，而那样写出来的判据是**自觉**的：

* **幂等**（:meth:`WorkstreamStore.put_launch`）的载体是 DDL 上那条 UNIQUE
  ``(workspace, scene_id, scene_version, idempotency_key)`` **加**一条读回路径。
  端口把两半都摆出来 —— 写是 :meth:`~WorkstreamStore.put_launch`，读是
  :meth:`~WorkstreamStore.get_launch`；而前者的返回值**就是读回来的那一行**，
  不是调用方交上去的那个对象。少了这后半句，「重发回同一个 launch」就只是
  「这一次没报错」。

* **不可变**（:meth:`EffectiveSceneStore.put_scene`）的载体是主键
  ``(scene_id, version)``：第二次写同一个主键配**同一份内容**是幂等的，
  配**另一份内容**必须报错。一个「已存在就跳过」的实现会让一次改错内容的部署
  在盘上不留痕，而此后每一处引用它的地方读到的都不是它编译时的那一份。

两条都是**写路径**的判据，所以它们各自要有一个负向用例 —— 见契约测试里
``test_a_second_launch_with_another_body_is_refused`` 与
``test_an_effective_scene_version_cannot_be_rewritten``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from marketops.domain.workstream import EffectiveScene, LaunchRecord, Workstream

__all__ = ["EffectiveSceneStore", "WorkstreamStore"]


class WorkstreamStore(ABC):
    """一次启动的三张面孔：那条记录、那个工作流、以及它们之间的边。"""

    # ------------------------------------------------------------------
    # LaunchRecord
    # ------------------------------------------------------------------
    @abstractmethod
    def put_launch(self, record: LaunchRecord) -> LaunchRecord:
        """落一条启动记录，**返回盘上那一行**。

        四格业务键撞上时：

        * 盘上那行的 ``request_digest`` 与这一次的**相等** ⇒ 回盘上那一行
          （这就是「重发同一个请求回同一次启动」）。
        * 不相等 ⇒ ``StoreError`` 并**点名**是哪一格不一样（键相同而内容不同是
          一次冲突，不是一次重发）。

        **返回值必须是读回来的那一行**，不是入参对象：一个直接回传入参的实现，
        在「另一台机器上已经写过了」这件事上什么也证明不了 —— 而它读起来与
        真正的幂等一模一样。
        """
        ...

    @abstractmethod
    def get_launch(
        self,
        *,
        workspace: str,
        scene_id: str,
        scene_version: int,
        idempotency_key: str,
    ) -> LaunchRecord | None:
        """按**四格业务键**读一条启动记录。读不到返回 ``None``。

        四格全在签名里，不给一个「按 launch_id 读」就完事：幂等的判据是业务键，
        而一个只按代理键读的实现在重发那条路上根本没有可比的量。
        """
        ...

    # ------------------------------------------------------------------
    # Workstream
    # ------------------------------------------------------------------
    @abstractmethod
    def next_ordinal(self, scene_id: str) -> int:
        """这个 ``scene_id`` 底下**下一个**可用的序号（1 起、单调递增）。

        它单独一个方法而不是让调用方去 ``max()`` 一遍列表：读法只有一处，
        而「第几次启动」是业务键的一半 —— 两处各数一遍的那天，两个工作流会拿到
        同一个 ``workstream_id``，而存储那一层只报一句主键冲突。
        """
        ...

    @abstractmethod
    def put_workstream(
        self, stream: Workstream, *, expected_revision: int | None
    ) -> None:
        """写一个工作流。``expected_revision=None`` = **必须不存在**（首次创建）。

        与 ``SceneStore.put_record`` 逐字同一条纪律。更新路径的载体是
        ``WHERE workstream_id = ? AND revision = ?`` 加 ``rowcount``：

        * 行不存在 ⇒ 拒绝（点名这个 id）。
        * 行在、revision 对不上 ⇒ 拒绝（**点名盘上那一版是哪一版**）。
          「过期被拒而不是覆盖」这句话的落点就是这一条。
        * 行在、revision 正好是 ``expected_revision`` ⇒ 写。

        三条各自的报文必须分得开：合成一句「写入失败」会让调用方分不清是
        「有人先改了」（去看最新状态再重试）还是「这个 id 根本不在」（去查是不是
        写错了场景名）—— 两者的处置相反。
        """
        ...

    @abstractmethod
    def get_workstream(self, workstream_id: str) -> Workstream | None:
        """按业务键读一个工作流。读不到返回 ``None``（**不是**一个空壳对象）。"""
        ...

    @abstractmethod
    def workstreams(self, *, workspace: str) -> tuple[Workstream, ...]:
        """本工作区的**全部**工作流，按 ``workstream_id`` 升序。

        ``workspace`` 是必填关键字：一个可以省略它的签名会让「本工作区的每一个」
        在调用点悄悄变成「盘上的每一个」，而两者在报告上同形 —— 那正是
        ``list_workstreams`` 那条 summary 要挡的读法（「读错了工作区」与「这一层
        没接上」是同一个形状）。
        """
        ...


class EffectiveSceneStore(ABC):
    """编译结果只增不改。**没有 update**，而这不是省略。"""

    @abstractmethod
    def put_scene(self, scene: EffectiveScene) -> None:
        """写入一份有效场景。``(scene_id, version)`` 已存在时：

        * 内容（含 ``digest``）**逐字相同** ⇒ 幂等，什么都不做。
        * 不同 ⇒ ``StoreError``，**点名**是哪个 ``(scene_id, version)``。

        「存在就跳过」与「存在就覆盖」都不行，理由相反但一样贵：前者让一次改错
        内容的部署在盘上不留痕；后者就是任务书 §5 那条变异要打中的东西 ——
        同一 ``(scene, version)`` 在两次部署之间成了两份不同的东西，而引用它的
        每一处都看不出来。
        """
        ...

    @abstractmethod
    def get_scene(self, scene_id: str, version: int) -> EffectiveScene | None:
        """按 ``(scene_id, version)`` 读。读不到返回 ``None``。"""
        ...

    @abstractmethod
    def versions(self, scene_id: str) -> tuple[int, ...]:
        """这个场景**盘上已经有**的版本号，升序。

        它单独一个方法，是因为 ``get_effective_scene`` 的拒绝必须把盘上有的版本
        **点名写出来**（「这一版不存在」与「这一版存在但什么都没编译出来」不是
        同一件事）。拒绝里报不出这一串的实现，读起来与「盘上什么都没有」同形。
        """
        ...

    @abstractmethod
    def scenes(self, *, workspace: str) -> tuple[EffectiveScene, ...]:
        """本工作区的全部编译结果，按 ``(scene_id, version)`` 升序。

        ``workspace`` 与 :meth:`WorkstreamStore.workstreams` 那一个同一条理由。
        它同时是「一个工作区读不到另一个工作区的 EffectiveScene」这条判据的落点：
        存储那一层按工作区过滤，而**不是**靠调用方记得自己过滤。
        """
        ...
