"""``WakeupRegistry`` 抽象：**这个工作区现在由谁唤醒**。

## 它为什么与 ``JobStore`` 分开

因为它们是两个接缝，判据是「有没有一条共用的写事务」：

* 续跑意图（``JobStore`` 上那一段）与**业务事件**共用写事务 —— 受理与它欠下的下一步
  必须一起落账，否则事件在盘上而意图不在（见 ``ports/jobs.py``）。
* 唤醒归属与**任何业务事件都没有**共用的写事务。控制器拿到归属时，盘上没有任何一个
  业务事实发生了变化：它只是说「这一轮由我来推」。把两件事塞进同一个接口，会让
  「谁在推」看起来是那批意图的一个字段 —— 而它与那些意图的生命周期完全无关
  （归还可以发生在两条意图之间，意图的推进也可以跨很多代）。

## 它守的是哪一条

``docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md`` §10 逐字：**「唯一唤醒 owner，不能并行双
触发器」**。失败只有一种，而且**两张脸都不报错**：

1. 两个控制器同时在跑 —— 各自领到**不同的**到期行（CAS 只挡住同一行），同一批工作
   被推两遍，两边都以为自己是对的，账上是两倍的活；
2. 一个控制器死了 —— 没有别人接手，盘上的意图一直在那里，报告上显示「还有未决工作」
   而没有任何东西在动。这与第 1 条是同一个旋钮的两端。

## ``acquire`` 与 ``heartbeat`` 分开，不是可以把两件事合成一件

``acquire`` 是**竞争**（有人可能赢在我前面），``heartbeat`` 是**续期**（只有持有者能
做）。合成一个 ``touch()`` 会让「我现在拿到了它」与「我还拿着它」在调用点不可区分 ——
而它们的返回不一样，一个要报告「我没拿到」，另一个在拿不到时意味着**我已经被顶掉了**，
那是一件要立刻停手的事（继续做下去就是第 1 条）。所以 ``heartbeat`` 回一个布尔，
且那个 ``False`` 是回滚信号，不是一个可以忽略的日志。

## 与 ``JobStore`` 的租约同一条纪律：**不自动收回**

见 ``domain/continuation.py::WakeupOwner``。这里只有 ``stale`` 的判据，没有超时定时器。

## 两个实现，同一份判据

``adapters/sqlite/wakeup.py``（跨进程正确，靠 ``BEGIN IMMEDIATE``）与
``adapters/memory.py``（单进程测试替身）。与 ``JobStore`` 同一条理由：端口在第二个
实现出现时才提取，两个都跑 ``tests/test_continuation_contract.py``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..domain.continuation import WakeupOwner


class WakeupRegistry(ABC):
    """一个工作区的唤醒归属。**每个工作区一行**，不是每个场景一行。

    粒度是工作区，因为「几个触发器」这件事是**部署**的性质：两个控制器各自驱动同一
    个工作区下的不同场景，仍然是两个触发器在推同一批共享资源（模型预算、执行器、
    Quay 任务）。按场景发放归属会让「四个场景各一个控制器」看起来是四个正当的持有者。
    """

    @abstractmethod
    def acquire(self, workspace: str, *, holder: str, now: datetime) -> WakeupOwner | None:
        """申请成为这个工作区的唤醒持有者。

        三种情形**在同一个写事务里**判定：

        1. 还没有持有者 → 发一个，``epoch=1``；
        2. 有持有者、但已经 ``stale`` → **顶掉**，``epoch`` 在他那一代上加一；
        3. 有持有者、还活着 → ``None``。

        ## ``holder`` 同名**不**算「是我」

        第二条里没有「持有者就是 ``holder`` 自己 → 续期」这一格，而它看起来像是一个
        漏掉的好意。写下来为什么不要它：**一个进程不需要第二次拿到自己已经拿着的东西**
        （拿到之后走 ``heartbeat``），而「同名即是我」正好让两个同名进程互相看不见 ——
        同一个部署在两台机器上、同一个容器名被重启到一个新进程，都会命中它。那时
        两个进程都认为自己持有，正是本接口开头第 1 条失败。``holder`` 是给人读的
        （报告里「谁在推」），不是身份证明；身份是那一行本身。

        ## ``None`` 之后调用方该做什么

        **退出，或者等一会儿再 acquire**（等是安全的：``acquire`` 本身是一次 CAS，
        重试它不会驱动任何东西）。**不能**在拿不到的情况下接着驱动 —— 那才是
        「并行双触发器」。重试要有间隔：一个空转的 acquire 循环会把这一层的开销变成
        一次持续运行的代价。

        Raises:
            ValueError: ``holder`` 为空。空持有者无法被追问，也无法在顶掉之后解释
                上一代是谁。
        """
        raise NotImplementedError

    @abstractmethod
    def heartbeat(self, workspace: str, *, holder: str, epoch: int, now: datetime) -> bool:
        """续期。``False`` 表示**我已经不是持有者了** —— 立刻停手。

        它必须能返回 ``False``：一次 ``acquire`` 顶掉上一代之后，上一代的 ``heartbeat``
        会走到这里，而它唯一诚实的答复是「你已经不是了」。写成一个必然成功的
        ``UPDATE``（例如不带 ``epoch`` 条件）会让上一代永远自我感觉良好 —— 那是本模块
        开头第 1 条失败最省事的产生方式。

        同一条判定里也要挡住「别人的 ``epoch``」：``(workspace, holder, epoch)``
        三格全对才算续期，只对前两格会让一次换代之后回来的旧持有者把心跳写回去
        （他的 ``holder`` 与新一代逐字相同 —— 见 ``Continuation.epoch`` 那条注释）。
        """
        raise NotImplementedError

    @abstractmethod
    def release(self, workspace: str, *, holder: str, epoch: int) -> bool:
        """主动让出。``False`` 表示这一行已经不是你的了（同样不必补救）。

        **它不收时刻**，而另外三个动词都收。这不是不一致，是这一格本来就没有时刻：
        让出是**删掉那一行**，而一次删除不需要知道「现在几点」—— 收一个 ``now`` 然后
        不用它，是给后来的人留一个「它大概在做一件与时间有关的事」的错误印象。

        为什么需要有这个动词，而不是让归属自然过期：**一次正常的退出**（收到信号、
        ``--once`` 跑完、运维停服）与**一次崩溃**是两件不同的事，而 ``stale`` 只区分
        「还活着」与「没在跳」。让正常退出也走 ``stale`` 那条路，意味着每次重启都要
        白等一个 ``OWNER_STALE_AFTER``；报告上那一段空白会被读成「系统卡了 90 秒」，
        而它其实是「上一个进程好好地说了一声再见」。

        让出是**删掉那一行**的语义（下一任从 ``epoch=1`` 开始），不是把 ``heartbeat_at``
        改旧 —— 后者会让下一任以为自己在顶掉一个崩溃的进程。``epoch`` 的用途是让
        **同一代之内**的陈旧可判，而一次干净的交接里根本没有上一代。
        """
        raise NotImplementedError

    @abstractmethod
    def current(self, workspace: str) -> WakeupOwner | None:
        """现在挂着的是谁；没有时返回 ``None``。

        **不回真假、也不回一个布尔「有没有人」**：报告里要写的是「谁、第几代、多久
        之前跳的」，而这三格只有记录本身有。一个 ``is_owned()`` 会把那三样东西留在
        调用点之外，于是「谁在推」只能靠猜。
        """
        raise NotImplementedError


__all__ = ["WakeupRegistry"]
