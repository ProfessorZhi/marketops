"""``FetchAttemptStore`` —— 一次访问的实测记录**落在哪里**。

## 它为什么独立成一个端口模块，而不是并进 ``ports/store.py``

判据与本仓几次端口提取用的是同一条：**幂等边界不一样**（``ports/pool.py`` 的模块
docstring 把这条判据写全了）。

* ``EvidenceStore`` 的边界是「同一 ``evidence_id`` 同一内容」—— 证据**只增不改**，
  而且 ``evidence_id`` 是**内容寻址**的，所以「同 id 不同内容」只可能意味着 id 被
  手工指定过。
* 本模块的边界是「同一次**尝试**只记一行」—— id 由
  :func:`~marketops.domain.fetch_attempt.attempt_id` 从「谁、哪一跳、什么时候开始」
  算出，**不是**内容的散列。同 id 不同内容因此有另一个含义：两个生产者对同一次
  尝试说了两套话（一个说拿到了 200、另一个说超时），那是一次要报错的冲突，而不是
  一次幂等的重复提交。

两者的失败处置也不同：证据那一侧按 ``EvidenceError`` 分支，这里按 ``StoreError``
（与 ``ports/pool.py`` 逐字同一条理由 —— 见 ``db.py::bind_workspace`` 的 ``error``
参数那一节：**判词一份，类型归主语**）。

## 它为什么**不是** ``EvidenceStore`` 上的一张表

收录一条证据与收录一次访问是两件事，差别在**这一行回答的问题**：证据回答「凭什么
这么说」，本模块回答「**我们那天到底问过什么、得到了什么**」。一次被拒的抓取
（403、挑战页、地址判据拦下）**产不出一条证据**，而它恰恰是最需要被记下来的一类
尝试 —— 采集设计 §5 那条坏结局（同一个源挑战页上的 20 个账号）在证据表上**一个字
都留不下**，因为那 20 次一条证据也没产出。把尝试挂到证据上，等于让记录的有无
跟着业务结果走；而它的用处正是解释**为什么没有那个结果**。

## 两个实现，同一份判据

``adapters/sqlite/fetch_attempt.py``（主存、跨进程正确）与
``adapters/memory.py::InMemoryFetchAttemptStore``（单进程测试替身）。端口在第二个
实现出现时才提取，两个都跑 ``tests/test_fetch_attempt_store_contract.py`` —— 与
``WakeupRegistry`` / ``EvidenceStore`` / ``BudgetLedger`` 逐字同一条纪律。

## 读取侧不传 ``workspace``

与 ``Evidence`` / ``Observation`` 同一个写法：血统写在**行**上（``FetchAttempt.workspace``
是必填、无默认值），写路径当场说清；读路径只认这个库自己的绑定
（``db.py::bind_workspace``）。不属于本库的行**报错**，不是被跳过 —— 跳过会让
「这一行不属于你」与「没有这一行」在调用点长成同一个空结果（``get_attempt`` 的
``None`` 因此只有一个意思）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..domain.fetch_attempt import FetchAttempt


class FetchAttemptStore(ABC):
    """一个工作区留下的访问尝试账。**只增不改**（见模块 docstring）。

    它没有 ``delete`` / ``update`` / ``prune`` 这类入口，而且这不是「还没做」：
    这条记录是事后回答「这个 URL 那天到底怎么样了」的唯一凭据，一条能被改写的访问
    记录在最需要它的场合（有人问「为什么那天什么都没抓到」）恰好是最不可信的。
    要清账就换一个库 —— 与 ``db.py::bind_workspace`` 对「换工作区」的处置同一句话。
    """

    @abstractmethod
    def put_attempt(self, attempt: FetchAttempt) -> None:
        """记下一次尝试。**幂等**；同一个 id 配不同内容是错误。

        两条分支：

        * 这一行已经在账上、内容逐位相同 → 什么都不做（同一次尝试被提交两遍是正常的：
          一次重放、一次重试的收尾、一条消息被消费两次）。
        * 这一行已经在账上、内容不同 → **抛 ``StoreError``**。这不是覆盖的场合：
          ``attempt_id`` 算的是「这一次尝试」，两套内容只可能意味着两个生产者对
          同一次尝试说了两套话，而静默保留先写的那条会让事后那句「这个 URL 那天
          到底怎么样了」有一个**看不出来**的错误答案。

        工作区绑定由**第一行写入**建立（照 ``bind_workspace`` 的既有规矩）：落进
        另一个工作区的行被拒，而不是被接受之后再靠读的人分辨。

        Raises:
            StoreError: 同一个 ``attempt_id`` 配不同内容，或这一行属于别的工作区。
        """
        raise NotImplementedError

    @abstractmethod
    def get_attempt(self, attempt_id: str) -> FetchAttempt | None:
        """按 id 取一行。

        ``None`` 只有一个意思：**没有这个 id 的行**。这一行存在但不属于本库时抛
        ``StoreError``（与 ``ports/pool.py`` 的三个实现逐字同一条）—— 一个把二者
        合成 ``None`` 的实现会让越权读变成一次静默的成功。
        """
        raise NotImplementedError

    @abstractmethod
    def list_attempts(
        self,
        *,
        scope: str | None = None,
        url: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[FetchAttempt]:
        """**最新在前**地列出这个工作区的尝试，可以按三样筛。

        Args:
            scope: 只列记在这个主体名下的。**``None`` 是「不筛」，空串是「筛那个
                不属于任何主体的那一批」** —— 两者不是同一件事，而把它们合成一个
                值之后，「这次运行什么都没抓到」与「不属于任何主体的抓取非常多」
                在报告上同形。``FetchRequest.scope`` 与 ``FetchAttempt.scope``
                都允许空串（见那两处的 docstring）。
            url: 只列**请求的那个地址**等于它的（不是 ``final_url``）：排查问的是
                「我们问过这个 URL 吗」，而重定向之后的地址是另一个问题的答案。
            since: 只列 ``started_at >= since`` 的。用**开始**时刻而不是结束，
                与 ``attempt_id`` 同一条理由（结束时刻会随重试次数变）。
            limit: 截掉**最旧的**。一个 ``N`` 的读数通常是「最近 N 次怎么样了」，
                而截最新的那一头会把这句话答反。

        Returns:
            按 ``(started_at, attempt_id)`` **降序**。尾部那个 ``attempt_id`` 不是
            为了好看：同刻的一批行必须有**稳定**的顺序，否则两次读在上限截断之下
            会给出不同的那一批，而报告上只显示「这次列的是别的几行」。
        """
        raise NotImplementedError


__all__ = ["FetchAttemptStore"]
