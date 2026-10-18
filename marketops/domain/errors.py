"""MarketOps 异常层次。

设计意图：把「可以换条路继续」与「必须停下」的失败区分开，避免调用方用
一个宽泛的 ``except Exception`` 把不可绕过的访问拒绝当成瞬时故障重试。
"""

from __future__ import annotations

from .access import AccessDiagnosis, Observation, SuspectedCause


class MarketOpsError(Exception):
    """所有 MarketOps 异常的基类。"""


class ConfigError(MarketOpsError):
    """配置缺失或非法（通常来自环境变量）。"""


class SearchError(MarketOpsError):
    """SearchProvider 失败。**观测与根因是两个字段，都带在异常上。**

    设计 §3 那句「观测和根因分开」在一次检索失败上的落点就是这里：一个 403 与
    一次 Cloudflare 挑战在调用方眼里都只是「这次没拿到」，而它们的处置不同
    （挑战的正文不得进入业务证据）。压成一句人话之后，调用方只能去**解析那句
    话**，或者退回去猜 —— 两种都是错的。

    所以这个异常带两个对象，它们都是 ``domain/access.py`` **已有的**（检索面与
    抓取面因此共用同一套词汇、同一张表，而不是第二套）：

    * :attr:`observation` —— 这一次**量到了什么**：状态码、白名单内的响应头、
      有界正文、传输层错误的类名。读不出来就是 ``None``，**不折成一个数** ——
      一次没问到状态码的调用不是一次 200，也不是一次 0。
    * :attr:`diagnosis` —— 据此**认为发生了什么**：闭集里的根因、我们的处置、
      以及一句在 ``domain/access.py`` 里已经写好的处置说明。

    两个字段都是可选的，所以旧写法 ``SearchError("一句话")`` 继续可用 —— 仓里
    两个检索替身与它们的判据在用那个形状。手写一条消息时 :attr:`suspected_cause`
    是 ``UNKNOWN``：那个成员的 docstring 逐字写着它的含义是「有异常，但没有任何
    证据把它归到下面任何一类」，而一句手写的消息正是这个处境。填 ``NONE`` 会把
    一次失败说成「这一次没有任何异常」。

    **不是 pickle 安全的**：``Exception.__reduce__`` 走 ``args``，两个结构化字段
    会在反序列化时静默消失。今天仓里没有任何一处 pickle 异常（没有
    ``multiprocessing``、没有 ``pickle``），所以没有判据能钉住这件事 —— 等真的
    出现跨进程传异常的那一天再补，而不是现在先写一段没人走的代码。
    """

    def __init__(
        self,
        message: str,
        *,
        observation: Observation | None = None,
        diagnosis: AccessDiagnosis | None = None,
    ) -> None:
        super().__init__(message)
        self.observation = observation
        self.diagnosis = diagnosis

    @property
    def suspected_cause(self) -> SuspectedCause:
        """疑似根因。

        **刻意不叫 ``cause``。** ``Exception.__cause__`` 是 Python 自己的链式异常，
        而本仓每一处 :class:`SearchError` 都是 ``raise ... from exc`` 抛出来的 ——
        「同名两义」在这里的代价是把「被链上的那个异常」读成「疑似根因」，而两者
        就住在同一个对象上。多打几个字符，换掉这一处误读。
        """
        if self.diagnosis is None:
            return SuspectedCause.UNKNOWN
        return self.diagnosis.cause


class FetchError(MarketOpsError):
    """Fetcher 失败（网络、超时、协议层）。"""


class AccessBlockedError(MarketOpsError):
    """目标站点明确拒绝公开访问（WAF challenge / 403 / paywall / 需登录）。

    **这是终态，不是可重试错误。** 处置是改走其他公开来源，
    而不是换 UA、换代理、绕 challenge —— 后者在本项目中被明确禁止。
    """


class ParserUnavailableError(MarketOpsError):
    """所需的 DocumentParser 未安装或不可用（如未装 MinerU 重依赖）。"""


class ParseError(MarketOpsError):
    """DocumentParser 拿到了文档但无法解析出内容。"""


class EvidenceError(MarketOpsError):
    """证据写入 / 校验失败。"""


class StoreError(MarketOpsError):
    """状态库（SQLite）打开 / 建表 / 版本不匹配，或者一个 append-only 版号被改写。

    与 ``EvidenceError`` 分开：证据是**产出**（它错了要重做研究），状态是
    **账**（它错了要停下对账）。两者混在一个异常里，调用方只能一起重试，
    而重试一个读不出来的账本会把错误放大。

    ## 版号被改写为什么不另开一个类型

    ``SceneStore.put_version`` 与 ``ProductCatalog.put_version`` 的「同一个
    ``(id, version)`` 写了不同的内容」落在**这里**。它读起来不像存储事故，处置
    却是同一条：版号是账（每一处引用只写 ``(id, 2)``，索引里没有内容），重读还是
    两份内容，**只有人能把账重新对齐**；当事人也不能「换一个版号」—— 那正是改历史。

    不另开类型还有一条：这个形状到此有两个实现（场景、产品目录），而同一形状抛
    两个异常，调用方要为同一件事写两个 ``except``。真要为它开类型，得先有一个
    处置确实不同的第三例。
    """


class GradingError(MarketOpsError):
    """分级规则缺失或非法。"""


class BudgetError(MarketOpsError):
    """预算层的失败基类。

    单开一族而不是复用 ``EvidenceError``：预算失败与证据失败的处置不同 ——
    预算失败要停（不能重试出预算），证据失败常常可重试。
    """


class BudgetNotConfiguredError(BudgetError):
    """该 ``(scope, line)`` 没有配置上限。

    **未配置不等于无限。** 把缺省当无限，等价于「忘写配置」和「故意放开」在
    行为上不可区分 —— 而这正是 §8 要挡的那类静默扩权。
    """


class BudgetExhaustedError(BudgetError):
    """预留会越过上限。**终态**：重试同样会失败，处置是停下来报告，不是重试。

    它把**已预留但尚未结算**的额度也算作占用 —— 一次超时未对账的尝试会一直
    扣着它的额度，直到有人显式退款。这是刻意的：见 ``ReservationState``。
    """


class ReservationStateError(BudgetError):
    """预留的状态转移非法（如退款一条已经花掉的预留）。"""


class IdempotencyConflictError(MarketOpsError):
    """同一个幂等键配了不同的输入。

    幂等键的承诺是「重发同一个请求不会执行两次」，不是「同一个键可以改内容」。
    后者必须显性失败：静默按新输入执行，会让「重试」变成一条改预算的旁路。
    """


class JobError(MarketOpsError):
    """作业层的失败基类。

    单开一族而不是复用 ``StoreError``：``StoreError`` 是「这个库打不开 / 版本不
    匹配」—— 处置是停机。作业层的失败（状态转移非法、拿不到租约）是**一次调用
    的正常结果**，处置是把它返回给调用方并让作业停在原处。两者混在一个类型里，
    会让「有人 pause 了一个已经取消的作业」在日志里长得像一次存储事故。
    """


class JobStateError(JobError):
    """作业状态上的非法动作：转移不合法、CAS 不匹配、或终态之后还写。

    与 ``docs`` 里 Quay 的 ``--expect-status`` 是同一条纪律：**读到的状态与据此
    写下的状态必须在同一个写事务里**。``set_state`` 因此要求 ``expected``，
    不一致就整次拒绝 —— 而那正是 Quay 逐字回的那句
    ``CAS conflict ... refusing to overwrite (write() aborted, nothing written to disk)``。
    """


class LeaseError(JobError):
    """拿不到或交不出一个租约。

    三种原因共用这一个类型：这个 work item 已经有主、已经停止、或者**还没派发
    到 Quay**。第三种是 §6 那句「The controller cannot substitute an outer-shell
    worker launch for Quay dispatch」的可执行形式 —— 没有 ``ExecutionRef`` 就没有
    租约，于是「绕过 Quay 直接开一个 worker」在代码里写不出来。

    消息必须点名是哪一种：三者对调用方的处置不同（等、改派别的 work item、
    先去派发）。
    """


class ContinuationError(JobError):
    """一次续跑意图上的非法动作：那一行不存在、不在可动的状态、或者不是你的。

    **与 ``JobStateError`` 分开**，因为主语不同：那个是「这个作业是什么状态」，
    这个是「**这一行欠下的下一步**在谁手里」。两者合成一个类型，会让「两个控制器
    抢同一行」在日志里长得像「有人 pause 了一个已取消的作业」—— 而它们的处置相反，
    前者什么都不用做（别人在做），后者要人去看一眼。

    三种原因共用这一个类型，消息必须点名是哪一种：

    * **不存在** —— 调用方手上那个 id 是错的（或者那一行被删了，而删除不在本层的
      动词里）；
    * **状态不对** —— 它不在可动的状态，或者对 ``finish`` 而言它不在 ``CLAIMED``；
    * **不是你的** —— ``(holder, epoch)`` 对不上。这一种**必须是错误而不是 ``False``**：
      一个换代之后才回来的旧控制器以为自己还在做那一件，它唯一正确的动作是**停手**，
      而 ``False`` 在调用点最容易被写成一个被忽略的日志。
    """


class DispatchError(MarketOpsError):
    """外部执行器**没能给出一份可读的答案**：起不来、超时、输出读不懂。

    **与 ``ExternalStateError`` 分开，因为处置相反**：这个是「那条路自己断了」，
    处置是停机看工具链；那个是「路是通的，对面给了一个不同的状态」，处置是重读。
    两者共用一个类型，会让「执行器没装」在日志里长得像一次 CAS 冲突 —— 而按冲突
    去处置（重读、重试）会在每一次重试上都得到同一个错误。

    它也**不是** ``JobError``：作业层那些失败（拿不到租约、转移非法）是「这次调用
    的结论」，而这个是「用来得出结论的那个东西不工作了」。
    """


class ExternalStateError(JobError):
    """外部执行器上的状态与预期不符。**三种原因共用一个类型**：

    * 外部当前状态不是我们 ``expected`` 的那一个（有别人先写了）；
    * ``external_id`` 在外部**不存在**；
    * 按一组 label 一查，带这组 label 的任务**不止一条**（``application/dispatch.py``
      第 6 步：同一件事可能有两条线，而本层拒绝挑一条）。

    与 ``LeaseError`` 同一个理由合并：三种对调用方的处置是同一条 —— **停下，重读
    那张映射**，而不是重试这次写。第三条尤其容易被人按「查到多条就挑一条」处置，
    而那不是收敛，是把「同一件事被派发了两遍」的证据扔掉。

    但**消息必须点名是哪一种**：第一条的处置是「按新状态重新决定」，第二条是
    「那条映射悬空了，去查是谁删的」，第三条是「两条都在，先看清哪一条是真的」。
    按第一条去处置第二条会让调用方永远在读一个不存在的任务；按任一条去处置
    第三条会把一个要报告的事实静默掉。
    """


class RunError(MarketOpsError):
    """§B17 的 Run 这一族失败的共同祖先。

    **为什么要有一个族头**：``RunSpecError`` 讲的是「这个规格今天走不通」（处置
    是改规格），而 ``RunStateError`` 讲的是「这一轮此刻不是那个状态」（处置是重读
    它现在到哪一步了）。两条处置相反，合成一个类型会让「有人先停了这一轮」在日志里
    长得像「参数写错了」—— 而按参数错去处置的那条路会一直重试同一个已经结束的 Run。

    与 ``JobError`` 一族并列而不并入：Run 圈的是「一次增量研究」的范围、信封与
    截止，作业圈的是「一件格子工作」的执行。同一轮 Run 下面可以有很多作业，
    而它们失败的原因不一定让这一轮停下。
    """


class RunStateError(RunError):
    """这一轮 Run **此刻的状态**不允许这个动作。**两种原因共用一个类型**，
    与 ``ContinuationError`` / ``ExternalStateError`` 同一条纪律：

    * ``run_id`` 指的那一轮**不存在**（或者有人拿了一个别的什么 id 来当 run_id）；
    * 它**已经结束了**，而这一次带上来的结局（``RunStatus`` + ``RunDelta``）
      与先前落盘的那一套**不是同一套** —— 同一个 Run 只有一份结局：两个不同的
      产出对着同一个 Run，报告里读不出哪一份是真的。

    两种共用一个类型的理由：调用方的处置是同一条 —— **停下，重读这一轮现在的
    样子**。而**消息必须点名是哪一种**：第一种要去查是谁给错了 id，第二种意味着
    有人拿着两份对不上的产出在写同一行。

    第二条尤其不能被静默处理成「重复调用」。把「两份不同的 Delta 争同一个结局」
    读成重放，会让先落盘的那一份悄悄赢，而后来那一份的产出**没有任何一处记着
    它丢过**。

    **同内容重放不是错误**（``RunStore.stop`` 幂等，见那份 docstring）：判重放的
    依据是「结局与产出是不是同一套」，不是「这个方法被调过几次」。``stopped_at``
    不比 —— 重放记的是第一次停表的那个时刻，而不是最后一次尝试的时刻。

    **与 ``IdempotencyConflictError`` 分开**：那个比的是「同一个幂等键配了不同
    内容」，发生在**受理**那一步，处置是把键或内容改对。这个发生在**结束**那一步，
    处置是去看那一轮到底产出了什么。
    """


class RunSpecError(RunError):
    """这次受理的**规格**今天走不通：模式今天受理不了、截止时间已过、
    或者引用指不到任何东西。

    **这是受理拒绝，不是事故**（与 ``JobError`` 一族同一条纪律）：处置是把规格
    改对再提交，而不是重试这次调用。

    **与 ``AuthorizationError`` 分开**：那个是「你这个主体不允许调这个工具」，
    处置是换主体或换角色；这个是「主体没问题，这个规格跑不起来」，处置是改规格。
    合成一个类型，会让一次越权尝试在日志里长得像一次写错的参数。

    **与 ``ConfigError`` 分开**：那个是「部署写错了，应该起不来」；这个是服务
    正常、这次调用不允许。

    §B17 那句「mode 不是权限捷径」在受理面上的落点就是这个类型。**拒的不是形状**：
    切片 e1 之后窗口那一格收得下，形制合法的自动规格也构造得出来。今天拒的是
    **这一次提交缺的那一样前提**，而两个自动模式各缺各的：``bounded_auto`` 缺
    「没有带授权窗口」（``ports/runs.py::admission_refusal``），``watch`` 缺
    「这一批主体上没有到期/信号」（``application/run_admission.py`` 的
    ``_require_due`` —— 切片 f 之后它的来源有生产者了，所以那一句从「它的来源今天
    没有生产者」换成了这一句）。

    切片 e2 之后**带窗口的 ``bounded_auto`` 受理得下来** —— 那一侧的拒绝换到了
    ``ports/runs.py::window_refusal``（这条链走到任一条 stop 之后不开下一轮）。所以
    这个类型今天说的不只是「模式不对」，还有「这条链现在说不能再开了」（上一轮还在跑、
    累计走到了阈值、累计读数读不到），以及「这一次的来源今天不成立」（``watch`` 那一
    支要读盘，所以它不住在 ``ports/runs.py``，见 ``run_admission.py`` 的拒绝对照表）。
    拒绝要比假装受理诚实 —— 一个受理了、而没有任何一处问过「这条链走到哪了」的自动
    Run，会让那句话在盘上无处成立。

    ## 这个类型**不**说的是「你没有资格签这份窗口」

    e2c 之后 ``submit_run`` 收得下窗口，于是这件事变得看得见了：今天**任何能调到
    这条工具的主体都能签一份自动授权**。「谁有权签」是一条身份/授权层的裁定
    （B01/B02，排在 B17 之后），不是受理面判得了的 —— 受理面手里只有传输层给的
    角色，而「这个角色能不能签一份跨多轮、跨多对象的授权」不在那张表上。
    缺口写在 ``adapters/mcp_server/runs.py`` 的模块 docstring 里，不在这里造一个
    占位判据：占位判据的下场是它此后被读成「这里已经判过」。
    """


class RunReconciliationError(RunError):
    """这一轮 Run 的**账对不上**：它的 Delta 与它折进池子的那些行说的是两件事。

    **与 ``RunSpecError`` 分开**：那个的处置是「把规格改对再提交」，而这里是
    「去看这一轮到底折进去了什么」—— 规格一个字都没错，错的是这一轮自己的记账。
    合成一个类型，会让一次「池子变大了而报告说没变」在日志里长得像一次写错的参数，
    而按参数错去处置的那条路会去改一份本来就对的规格。

    **与 ``RunStateError`` 分开**：那个读的是**这一个 Run 自己的状态**（不存在、
    已经结束了），处置是重读它现在到哪一步；这个读的是**两份记录之间的关系**，
    两份各自都读得出来，只是读出来的不是同一件事。

    **它挡住的形状**（§B17 逐字：「'池子变大'包括纠错和 currentness 改变，不只
    计算新增行」）：一次导入让池子里多出几行、或者改掉了几行的 currentness，
    而那几行在这份回执里一个字都没提 —— 报告上它读起来只是「这一批没怎么变」。
    按新增行数对账的实现读不出它：**纠错与 currentness 改变对计数是零**。

    消息里必须逐键点名是哪几条对不上（``application/run_reconciliation.py``
    那一处给出读数）：一个只说「对不上」的拒绝，会让人去从头读那一整轮。
    """


class RunFoldLedgerError(RunError):
    """这一轮 Run 的**折入账本**读不出来：它本该在受理那一刻就有，而盘上没有。

    **它不是「这一轮一行都没折」。** 那是一个真的读数，由一份**存在且为空**的账本
    给出（``RunStore.folds_of`` 返回空元组）。这里的形态是：``research_run`` 里有这一
    行，而它的账本行不在 —— 那是一个**按写路径造不出来**的状态（受理把两行写在同一个
    事务里），所以它要么是有人在库外动过手，要么是这个库来自一个还没有账本的版本。

    **为什么必须抛、且不能返回空元组**：``close_run`` 拿这个读数去判「这一轮折了什么」，
    而一个读不出来的账与一本空账，在「这一轮折了没有」这个问题的**两侧**。把前者读成
    后者，恰好就是 e3 要关掉的那条假账 —— 折了却没记，然后在结束时报一份空主张，
    账照旧平，池子却变大了。一个缺账本轮次在那条路上的表现与「老实的零折入」逐字相同。

    **与 ``RunStateError`` 分开**：那个说的是「这一轮此刻不是那个状态」（不存在、
    已经结束了），处置是重读它现在到哪一步。这个说的是**这一轮的行在、而它该有的另
    一行不在** —— 处置是去查这个库是怎么变成这样的，重读几次都一样。

    **与 ``RunReconciliationError`` 分开**：那个读的是两份都在的记录之间的关系
    （Delta 与折入），这个读的是**其中一份根本读不出来**。合成一个类型会让一次
    完整性事故在报告里长得像一次记错了的账，而按「记错了」去处置会把那条假账
    当成一次正常的对账失败 —— 也就是把它留在原地。
    """


class SceneConflictError(MarketOpsError):
    """写一个 Scene 的当前状态时，它已经不是读到的那个版本了。

    **与 ``StoreError`` 分开，因为处置相反。** ``StoreError`` 是「这个库打不开 /
    版本不匹配」—— 处置是停机。这个是「有人先写了」—— 处置是**重读、重新决定**，
    而且这次写**一个字节都没落盘**。

    与 ``IdempotencyConflictError`` 也分开：那个是「同一个键配了不同内容」，
    处置是把键或内容改对；这个是「状态变了」，处置是重新拿一次当前状态。

    消息里必须同时有期望与实际的 revision：只报「冲突了」的回执，读的人无法
    判断是自己太慢还是对面在打架。
    """


class WaveConflictError(MarketOpsError):
    """写一个 Wave 的当前状态时，它已经不是读到的那个版本了。

    **与 ``SceneConflictError`` 分开，而不是复用它。** 两者的处置完全一样
    （重读、重新决定，且这次写一个字节都没落盘），分开的唯一理由是**名字要说得出
    主语**：一次批次冲突的消息里出现 ``SceneConflictError``，读日志的人第一件事
    是去查场景，而那条记录里没有任何一个字段与场景的 revision 有关。
    两件处置相同、主语不同的事共用一个异常名，就是下一次误判的入口。

    与 ``StoreError`` 分开的理由同 ``SceneConflictError``：那个是「这个库打不开 /
    版本不匹配」—— 处置是停机；这个是「有人先写了」—— 处置是重读。

    消息里必须同时有期望与实际的 revision：只报「冲突了」的回执，读的人无法
    判断是自己太慢还是对面在打架。
    """


class AssetConflictError(MarketOpsError):
    """把一次新的观察折进池子里那一行时，它和已有的判定**说不到一块儿**。

    具体是这一种：两边都对「这块场地有没有官网」下了判定，而**判定不同**
    （``confirmed`` 配一个域名 vs ``absent`` 配「查过没有」，或者两个 ``confirmed``
    配了两个不同的域名）。处置是**停下来，由人显式改那一行** —— 本层没有
    「谁的判定更权威」这条规则，所以它不猜。

    ## 为什么不是 ``StoreError``

    ``StoreError`` 是「这个库打不开 / 版本不匹配」—— 处置是停机对账，库的问题。
    这个是**两行业务事实互相矛盾**——处置是回看那两条判定各自的证据，然后决定
    哪一条作废。两者的处置不同，共用一个类型会让一次真实的判定冲突在日志里长得
    像一次存储事故。

    ## 为什么不是 ``SceneConflictError``

    同一个理由的另一半，而且是 ``SceneConflictError`` / ``WaveConflictError``
    分开时写下的那条：**名字要说得出主语**。那个是「有人先写了，你手上的
    revision 过期了」——重读就能继续；这个是「两条判定互相矛盾」——重读还是矛盾，
    必须有人做决定。两件处置相反的事共用一个异常名，是下一次按错的那一条去处置
    的入口。

    ## 它与「重新导入不必抹掉已确认的官网」不是同一条

    那条规则管的是**没做判定**的来行（``domain_verdict=unknown``）：它不许把
    已经证得的结论抹掉，而这件事**不报错**，是正常的合并。这里管的是**做了判定、
    而且判得不一样**的来行。少写这一条，池子上就出现一条「后写的那次判定覆盖了
    前一次」的路，而它读起来像一次正常的更新。

    消息里必须同时有点名的两边（各自的判定、域名、证据 id）：只说「冲突了」，
    读的人无法判断要作废哪一条。
    """


class ReviewConflictError(MarketOpsError):
    """写一件待办的裁决时，它已经不是读到的那个版本了 —— 或者那条裁决与它的
    当前状态不相容。

    **与 ``SceneConflictError`` / ``WaveConflictError`` 分开，理由与它们各自分开的
    理由逐字相同**：处置一样（重读、重新决定，且这次写一个字节都没落盘），分开的
    唯一理由是**名字要说得出主语**。一次待办冲突的消息里出现 ``SceneConflictError``，
    读日志的人第一件事是去查场景的 revision，而那条记录里没有任何一格与场景的
    revision 有关。

    第二类（状态不相容）也在这一格里，而不是另开一个异常：它的处置**也是**重读当前
    状态再决定，而且它的成因与第一类常常是同一件事的两种样子 —— 两个人同时裁决同一
    件时，后到的那一个看到的可能是「version 变了」，也可能是「它已经不在未决态了」，
    取决于另一个人的决定落在哪一步。分成两个类型会让同一个成因在报告里读成两回事。

    消息里必须同时有期望与实际的 ``version``，外加案件的**当前状态**：只报「冲突了」
    的回执，读的人无法判断是自己太慢还是对面在打架。
    """


class SavedViewConflictError(MarketOpsError):
    """写一份视图时，盘上已经是另一个样子了。**三个成因，一个类型。**

    * **名字**：``(workspace, owner, name)`` 上已经有**另一条**当前版。
      ``catalog.py`` 里 ``put_saved_view`` 的 summary 逐字：「已存在的 ``(owner, name)``
      回它**当前的那一版**而不是悄悄盖掉」。所以这条异常的两件事必须都做到：**拒**
      这一次写（一个字节都不落盘），并且消息里**点名那条当前版的 view_id 与 revision**
      —— 只说「重名了」的回执，读的人无法去做正确的那件事（用那条自己的 ``view_id``
      写下一版，或者换个名字），而他会做的下一件事是换一个名字再试一次，于是同一份
      查询在库里有了两条。
    * **基准版本不对**：``expected_revision`` 不是盘上当前那一版（有人先写了，
      或者调用方手上那份跳了一版）。
    * **声称是首次创建，而它已经在了**。

    ## 为什么三个合成一个，而 ``SceneConflictError`` 那几个是分开的

    分开的那几处，理由是**名字要说得出主语**（``WaveConflictError`` 的 docstring
    逐字）—— 一批工件与一个场景的冲突，读日志的人要能一眼分出该去看谁。这三个的
    主语是**同一个**（这一条视图），而且它们的**处置也是同一句话**：重读这一条视图
    的当前版，再决定用哪个名字 / 哪一版重写。分成三个类型会让同一个成因在两次报告里
    读成两回事（``ReviewConflictError`` 对同一条理由的处理逐字）。

    **与 ``StoreError`` 分开，理由与 ``SceneConflictError`` 逐字相同**：那个是
    「这个库打不开 / 版本不匹配」—— 处置是停机；这个说的是「盘上那一条不是你以为的
    那一条」—— 处置是重读，而这次写**一个字节都没落盘**。
    """


class ActionItemConflictError(MarketOpsError):
    """写一件待办时，它已经不是读到的那个版本了 —— 或者那条处置与它当前的状态不相容。

    与 ``ReviewConflictError`` 逐字同一条结构（两类合成一个异常），理由也逐字相同：
    两类的处置**都是**重读当前状态再决定，而且它们的成因常常是同一件事的两种样子 ——
    两个人同时处置同一件时，后到的那一个看到的可能是「版本变了」，也可能是「它已经
    结了」，取决于另一个人的决定落在哪一步。分成两个类型会让同一个成因在报告里读成
    两回事。

    **消息里必须同时有期望与实际的版本，外加那一条当前的状态**：只报「冲突了」的回执，
    读的人无法判断是自己太慢（重读再试）还是对面在打架（去看是谁做的）。

    **与 ``SavedViewConflictError`` 分开，而不是复用它**：处置相反。那个是「换个名字
    或换用对的 id」（与版本无关），这个是「重读当前版本」。
    """


class ScheduleConflictError(MarketOpsError):
    """写一版调度声明时，盘上当前那一版不是读到的那个。**这次写一个字节都没落盘。**

    ``db.py`` 的 ``schedule_definition`` 那一段逐字：「改范围、改预算、改通知目标
    这些写操作有版本和权限」—— 而这句话的载体就是 ``expected_revision`` 这个 CAS。
    两个写者各读第 3 版、各自算出第 4 版时，后到的那个必须**被拒**：一次拒会把
    「你手上这一版过期了」说出口，而一次安静的接受会让先写的那一版被人跳过 ——
    报告上不会出现任何异常，只出现一个版本号。

    ## 两个成因，一个类型

    * **基准版本不对**：``expected_revision`` 不是盘上当前那一版。
    * **声称是首次声明，而这条调度已经在**（``expected_revision=None`` 而
      ``schedule_id`` 已有行）。这一类的处置与上一类相同：重读当前那一版再决定
      是改它还是换一个 ``schedule_id``。

    与 ``SavedViewConflictError`` 的处理逐字同一条理由（两类的处置是同一句话：
    重读当前版再决定），它们**分开**只是因为名字要说得出主语。

    **与 ``StoreError`` 分开**：那个是「这个库打不开 / 版本不匹配」（停机），
    这个是「盘上那一条不是你以为的那一条」（重读再决定）。

    **与 ``IdempotencyConflictError`` 分开，因为处置相反。** 那一个是「同一个键配了
    两个不同的内容」（把键或内容改对，重试**不会**变好）；这一条是重试就会变好的
    —— 所以把一次调度冲突报成幂等冲突，会让人去改一个本来没错的东西。

    消息里必须同时点名**期望的与实际的那一版**：只报「冲突了」的回执，读的人无法
    判断是自己太慢还是对面在打架。
    """


class SubscriptionConflictError(MarketOpsError):
    """写一版通知订阅时，盘上当前那一版不是读到的那个。**这次写一个字节都没落盘。**

    与 ``ScheduleConflictError`` 是逐字同一条结构（两个成因：基准版本不对 /
    声称首次而它已经在了），处置也是同一句话：重读这一条的当前版再决定。
    分开的唯一理由是名字要说得出主语 —— 一条订阅冲突的消息里出现「调度」，
    读日志的人第一件事是去查调度的 revision，而那条记录里没有一格与调度有关。

    **与 ``StoreError`` 分开**：那个是「这个库打不开 / 版本不匹配」（停机），
    这个是「盘上那一条不是你以为的那一条」（重读再决定）。

    消息里必须同时点名**期望的与实际的那一版**。
    """


class OpenActionItemExistsError(MarketOpsError):
    """同一主体上的同一类待办**已经有一条未结的**了 —— 这次创建是一次插入失败。

    ``db.py`` 的 ``action_item`` 那一段逐字：「没有它，每个 Run 都会新增一条「核实
    转介联系人」，而报告上看起来只是「待办多了一些」」。那条 partial unique index 是
    这句话**唯一**的执行者，所以这条异常的来路只有一条：**插入被约束拒绝**。

    它与 ``ActionItemConflictError`` 分开，因为**主语不同**：那个说的是「你手上这一版
    过期了」（重读再试），这个说的是「你要建的那一件已经在池子里了」（去处理它、或者
    改它，而不是再建一条）。名字里那个 ``Open`` 是有内容的：**已结的可以有任意多条**
    （那是历史），被拒的只有「第二条未结的」。

    消息里必须点名**盘上那一条的 action_id**。只说「已经存在」的回执把调用方留在原地
    （他手上没有那条的 id，也就没法去做它），而他会做的下一件事是换个 action_id 再试
    一次 —— 那正是这条约束要挡的事。
    """


class AuthorizationError(MarketOpsError):
    """调用方的主体不存在，或其角色不允许这个工具。

    **这不是 ``ConfigError``**：配置错误是「部署写错了，应该起不来」，
    而越权是「服务正常，这次调用不允许」—— 前者要打堆栈并停机，后者是一条
    要返回给调用方的结果。两者共用一个类型，会让越权尝试在日志里长得像
    一次配置事故，而真正需要被看见的那条记录就此淹没。

    注意这个类型**只在策略层**用。服务面对外返回的不是它，而是与「工具不存在」
    逐字相同的那条错误 —— 见 ``marketops/adapters/mcp_server/guard.py``。
    """


__all__ = [
    "MarketOpsError",
    "ConfigError",
    "SearchError",
    "FetchError",
    "AccessBlockedError",
    "ParserUnavailableError",
    "ParseError",
    "EvidenceError",
    "StoreError",
    "GradingError",
    "BudgetError",
    "BudgetNotConfiguredError",
    "BudgetExhaustedError",
    "ReservationStateError",
    "JobError",
    "JobStateError",
    "LeaseError",
    "DispatchError",
    "ExternalStateError",
    "RunError",
    "RunStateError",
    "RunSpecError",
    "RunReconciliationError",
    "RunFoldLedgerError",
    "IdempotencyConflictError",
    "SceneConflictError",
    "WaveConflictError",
    "AssetConflictError",
    "ReviewConflictError",
    "SavedViewConflictError",
    "ActionItemConflictError",
    "ScheduleConflictError",
    "SubscriptionConflictError",
    "OpenActionItemExistsError",
    "AuthorizationError",
]
